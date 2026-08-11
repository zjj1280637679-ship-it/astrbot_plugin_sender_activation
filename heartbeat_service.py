


from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from typing import Any

from .cron_adapter import AstrBotCronAdapter
from .domain import DomainError, normalize_scope
from .heartbeat_gate import HeartbeatWakeGate
from .heartbeat_domain import (
    HeartbeatLease,
    normalize_cron_expression,
    normalize_heartbeat_duration,
    normalize_heartbeat_name,
    normalize_instruction,
    normalize_lease_ids,
)
from .service import HEARTBEAT_EFFECT_CONTRACT, ActorContext
from .settings import PluginSettings


class HeartbeatService:


    def __init__(
        self,
        settings: PluginSettings,
        adapter: AstrBotCronAdapter,
        wake_gate: HeartbeatWakeGate | None = None,
        *,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self._adapter = adapter
        self._wake_gate = wake_gate
        self._wall_clock = wall_clock
        self._lock = asyncio.Lock()
        self._active = False
        self._quarantined: list[dict[str, str]] = []
        self.last_error_code: str | None = None

    async def initialize(self) -> None:
        async with self._lock:
            self._active = True
            try:
                leases, quarantined = await self._adapter.reconcile_start(
                    float(self._wall_clock())
                )
                if self._wake_gate is not None:
                    await self._wake_gate.initialize()
                    for lease in leases:
                        try:
                            await self._wake_gate.arm(lease)
                        except DomainError as exc:
                            quarantined.append(
                                {"lease_id": lease.lease_id, "error_code": exc.code}
                            )
                self._quarantined = quarantined
                self.last_error_code = None
            except DomainError as exc:
                self.last_error_code = exc.code
                self._active = False
                raise

    async def terminate(self) -> list[str]:
        async with self._lock:
            self._active = False
            gate_failures: list[str] = []
            if self._wake_gate is not None:
                gate_failures = await self._wake_gate.terminate()
            try:
                return [*gate_failures, *await self._adapter.suspend_for_shutdown()]
            except DomainError as exc:
                self.last_error_code = exc.code
                return ["native_cron_unavailable"]

    def _require_active(self) -> None:
        if not self._active:
            raise DomainError(
                "heartbeat_service_inactive",
                "心跳租约服务当前未激活。",
            )

    async def _inventory(self) -> list[HeartbeatLease]:
        leases, quarantined = await self._adapter.inventory()
        self._quarantined = quarantined
        now = float(self._wall_clock())
        active: list[HeartbeatLease] = []
        for lease in leases:
            if lease.expires_at <= now:
                await self._adapter.delete_if_expired(
                    lease.lease_id,
                    lease.expires_at,
                    now,
                )
            else:
                active.append(lease)
        if self._wake_gate is not None:
            active = await self._wake_gate.decorate(active)
        return active

    @staticmethod
    def _native_minute_expiry(now: float, duration: int) -> float:
        return math.ceil((now + duration) / 60.0) * 60.0

    @staticmethod
    def _success(
        *,
        outcome: str,
        changed: bool,
        scope_ref: str,
        leases: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "status": "ok",
            "outcome": outcome,
            "effect_contract": HEARTBEAT_EFFECT_CONTRACT,
            "effect_applied": changed,
            "effect_state": "applied" if changed else "not_applied",
            "changed": changed,
            "reply_guaranteed": False,
            "scope_ref": scope_ref,
            "heartbeat_leases": leases,
        }

    async def manage(
        self,
        *,
        actor: ActorContext,
        scope: Any,
        scope_ref: str,
        action: Any,
        lease_ids: Any = None,
        name: Any = "",
        cron_expression: Any = "",
        instruction: Any = "",
        duration_seconds: Any = 0,
    ) -> dict[str, Any]:
        normalized_scope = normalize_scope(scope)
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"create", "renew", "disable", "list"}:
            raise DomainError(
                "invalid_heartbeat_action",
                "action 必须是 create、renew、disable 或 list。",
            )
        ids = normalize_lease_ids(lease_ids)
        async with self._lock:
            self._require_active()
            leases = await self._inventory()
            scoped = [lease for lease in leases if lease.scope == normalized_scope]
            by_id = {lease.lease_id: lease for lease in scoped}
            now = float(self._wall_clock())

            if normalized_action == "list":
                selected = (
                    scoped
                    if not ids
                    else [by_id[item] for item in ids if item in by_id]
                )
                return self._success(
                    outcome="heartbeat_listed",
                    changed=False,
                    scope_ref=scope_ref,
                    leases=[lease.public(now, scope_ref) for lease in selected],
                )

            if normalized_action == "create":
                if ids:
                    raise DomainError(
                        "unexpected_lease_ids",
                        "create 不接受 lease_ids；任务 ID 由 AstrBot 原生 Cron 生成。",
                    )
                limits = self.settings.heartbeat_limits
                if len(scoped) >= limits.max_per_scope:
                    raise DomainError(
                        "heartbeat_scope_capacity_exceeded",
                        "当前作用域心跳租约数量已达上限。",
                    )
                if len(leases) >= limits.max_total:
                    raise DomainError(
                        "heartbeat_total_capacity_exceeded",
                        "插件心跳租约总数已达上限。",
                    )
                duration = normalize_heartbeat_duration(duration_seconds, limits)
                lease = await self._adapter.create(
                    scope=normalized_scope,
                    sender_id=actor.sender_id,
                    actor_ref=actor.actor_ref,
                    source=actor.channel,
                    name=normalize_heartbeat_name(name),
                    cron_expression=normalize_cron_expression(cron_expression),
                    instruction=normalize_instruction(instruction),
                    created_at=now,
                    expires_at=self._native_minute_expiry(now, duration),
                )
                if self._wake_gate is not None:
                    try:
                        lease = await self._wake_gate.arm(lease)
                    except Exception:
                        await self._adapter.delete(lease.lease_id, normalized_scope)
                        raise
                return self._success(
                    outcome="heartbeat_created",
                    changed=True,
                    scope_ref=scope_ref,
                    leases=[lease.public(now, scope_ref)],
                )

            if not ids:
                raise DomainError(
                    "missing_lease_ids",
                    f"{normalized_action} 必须提供 lease_ids。",
                )

            if normalized_action == "disable":
                changed = False
                for lease_id in ids:
                    if self._wake_gate is not None:
                        await self._wake_gate.disarm(lease_id)
                    if await self._adapter.delete(lease_id, normalized_scope):
                        changed = True
                return self._success(
                    outcome=(
                        "heartbeat_disabled" if changed else "heartbeat_already_absent"
                    ),
                    changed=changed,
                    scope_ref=scope_ref,
                    leases=[],
                )

            duration = normalize_heartbeat_duration(
                duration_seconds,
                self.settings.heartbeat_limits,
            )
            selected = [by_id[item] for item in ids if item in by_id]
            if len(selected) != len(ids):
                raise DomainError(
                    "heartbeat_absent",
                    "至少一个心跳租约不存在或不属于当前作用域。",
                )
            renewed: list[HeartbeatLease] = []
            for lease in selected:
                updated = await self._adapter.renew(
                    lease,
                    sender_id=actor.sender_id,
                    actor_ref=actor.actor_ref,
                    source=actor.channel,
                    name=(
                        normalize_heartbeat_name(name)
                        if str(name or "").strip()
                        else lease.name
                    ),
                    cron_expression=(
                        normalize_cron_expression(cron_expression)
                        if str(cron_expression or "").strip()
                        else lease.cron_expression
                    ),
                    instruction=(
                        normalize_instruction(instruction)
                        if str(instruction or "").strip()
                        else lease.instruction
                    ),
                    now=now,
                    expires_at=self._native_minute_expiry(now, duration),
                )
                if self._wake_gate is not None:
                    try:
                        updated = await self._wake_gate.arm(updated)
                    except Exception as exc:
                        self.last_error_code = "heartbeat_preflight_update_indeterminate"
                        raise DomainError(
                            "heartbeat_preflight_update_indeterminate",
                            "心跳模板已更新但前置门重建失败；模板保持禁用，当前不会主动唤醒，需查询后重试。",
                        ) from exc
                renewed.append(updated)
            return self._success(
                outcome="heartbeat_renewed",
                changed=bool(renewed),
                scope_ref=scope_ref,
                leases=[lease.public(now, scope_ref) for lease in renewed],
            )

    async def snapshot(
        self,
        *,
        scope: str | None = None,
        scope_ref: Callable[[str], str],
    ) -> dict[str, Any]:
        async with self._lock:
            if not self._active:
                return {
                    "heartbeat_leases": [],
                    "known_scopes": [],
                    "raw_scopes": [],
                    "quarantined": list(self._quarantined),
                }
            leases = await self._inventory()
            if scope is not None:
                leases = [lease for lease in leases if lease.scope == scope]
            now = float(self._wall_clock())
            return {
                "heartbeat_leases": [
                    lease.public(now, scope_ref(lease.scope)) for lease in leases
                ],
                "known_scopes": sorted({scope_ref(lease.scope) for lease in leases}),
                "raw_scopes": sorted({lease.scope for lease in leases}),
                "quarantined": list(self._quarantined),
            }

    async def health(self) -> dict[str, Any]:
        snapshot = await self.snapshot(scope_ref=lambda value: value)
        gate_health = (
            await self._wake_gate.health()
            if self._wake_gate is not None
            else {"heartbeat_preflight_active": False}
        )
        return {
            "heartbeat_service_active": self._active,
            "heartbeat_count": len(snapshot["heartbeat_leases"]),
            "heartbeat_quarantined_count": len(snapshot["quarantined"]),
            "heartbeat_last_error_code": self.last_error_code,
            **gate_health,
        }
