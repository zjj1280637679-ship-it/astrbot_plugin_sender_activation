


from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .domain import (
    MAX_TARGETS_PER_OPERATION,
    ActivationDecision,
    DomainError,
    LeaseKey,
    LoadResult,
    MutationResult,
    RegistryState,
    acknowledge_recovery,
    active_leases,
    clear_rate,
    disable_activation,
    enable_activation,
    load_document,
    normalize_optional_target_ids,
    normalize_scope,
    normalize_target_ids,
    prune_state,
    public_snapshot,
    restore_scope_to_native,
    set_rate,
    state_to_document,
)
from .settings import PluginSettings
from .storage import AstrBotKVStateStore, CommitIndeterminateError

ACTIVATION_EFFECT_CONTRACT = "future_ordinary_messages_extra_agent_reachability"
RATE_EFFECT_CONTRACT = "extra_activation_rate_only"
HEARTBEAT_EFFECT_CONTRACT = "finite_native_agent_periodic_reachability"


@dataclass(frozen=True)
class ActorContext:
    sender_id: str
    actor_ref: str
    channel: str
    is_admin: bool = False
    proactive_source: str | None = None


class SlidingWindowCounters:


    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._usage: dict[LeaseKey, deque[float]] = {}

    def admit(
        self,
        key: LeaseKey,
        *,
        maximum: int,
        window_seconds: float,
        now: float,
    ) -> tuple[bool, float, int]:
        with self._lock:
            history = self._usage.setdefault(key, deque())
            threshold = now - window_seconds
            while history and history[0] <= threshold:
                history.popleft()
            if len(history) >= maximum:
                retry_after = max(0.0, history[0] + window_seconds - now)
                return False, retry_after, 0
            history.append(now)
            return True, 0.0, max(0, maximum - len(history))

    def preview(
        self,
        key: LeaseKey,
        *,
        maximum: int,
        window_seconds: float,
        now: float,
    ) -> tuple[bool, float, int]:
        with self._lock:
            history = self._usage.get(key)
            if history is None:
                return True, 0.0, maximum
            threshold = now - window_seconds
            recent = [timestamp for timestamp in history if timestamp > threshold]
            if len(recent) >= maximum:
                retry_after = max(0.0, recent[0] + window_seconds - now)
                return False, retry_after, 0
            return True, 0.0, max(0, maximum - len(recent))

    def clear(self, keys: set[LeaseKey] | frozenset[LeaseKey]) -> None:
        with self._lock:
            for key in keys:
                self._usage.pop(key, None)

    def clear_all(self) -> None:
        with self._lock:
            self._usage.clear()

    def counts(
        self,
        active_windows: Mapping[LeaseKey, float],
        *,
        now: float,
    ) -> dict[LeaseKey, int]:
        with self._lock:
            counts: dict[LeaseKey, int] = {}
            for key in list(self._usage):
                window_seconds = active_windows.get(key)
                if window_seconds is None:
                    self._usage.pop(key, None)
                    continue
                history = self._usage[key]
                threshold = now - window_seconds
                while history and history[0] <= threshold:
                    history.popleft()
                if history:
                    counts[key] = len(history)
                else:
                    self._usage.pop(key, None)
            return counts


class ActivationService:
    def __init__(
        self,
        settings: PluginSettings,
        store: AstrBotKVStateStore,
        *,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._store = store
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._state = RegistryState.empty()
        self._mutation_lock = asyncio.Lock()
        self._counters = SlidingWindowCounters()
        self._wall_time_lock = threading.Lock()
        self._last_wall_time: float | None = None
        self._metrics_lock = threading.Lock()
        self._metrics: Counter[str] = Counter()
        self._lifecycle_active = False
        self.storage_ready = False
        self.storage_write_healthy = False
        self.loaded_from = "none"
        self.last_error_code: str | None = None
        self.last_write_error_code: str | None = None
        self.recovery_warning_code: str | None = None
        self.last_commit_at: float | None = None
        self.quarantined: tuple[dict[str, Any], ...] = ()
        self.expired_on_load = 0

    @staticmethod
    def scope_ref(scope: str) -> str:
        prefix = ":".join(scope.split(":")[:2]) or "scope"
        digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:10]
        return f"{prefix}:{digest}"

    def _metric(self, name: str) -> None:
        with self._metrics_lock:
            self._metrics[name] += 1

    def _metrics_snapshot(self) -> dict[str, int]:
        with self._metrics_lock:
            return dict(self._metrics)

    def _effective_wall_time(self) -> float:
        current = float(self._wall_clock())
        with self._wall_time_lock:
            if self._last_wall_time is None or current > self._last_wall_time:
                self._last_wall_time = current
            return self._last_wall_time

    def _scoped_receipt_state(
        self,
        scope: str,
        target_ids: list[str] | None,
    ) -> dict[str, Any]:
        snapshot = self.snapshot(scope=scope, target_ids=target_ids)

        def project(row: dict[str, Any]) -> dict[str, Any]:
            result = dict(row)
            result.pop("scope", None)
            return result

        return {
            **snapshot,
            "activation_leases": [
                project(row) for row in snapshot["activation_leases"]
            ],
            "rate_leases": [project(row) for row in snapshot["rate_leases"]],
            "recovery_reports": [project(row) for row in snapshot["recovery_reports"]],
            "known_scopes": [self.scope_ref(scope)],
        }

    @staticmethod
    def _receipt_changes(
        changes: tuple[dict[str, Any], ...],
    ) -> list[dict[str, Any]]:
        projected: list[dict[str, Any]] = []
        for change in changes:
            row = dict(change)
            row.pop("scope", None)
            projected.append(row)
        return projected

    @staticmethod
    def _success_contract(
        *,
        outcome: str,
        effect_contract: str,
        changed: bool,
    ) -> dict[str, Any]:
        return {
            "outcome": outcome,
            "effect_contract": effect_contract,
            "effect_applied": changed,
            "effect_state": "applied" if changed else "not_applied",
            "reply_guaranteed": False,
            "native_wake_affected": False,
        }

    @staticmethod
    def _target_partition(
        targets: list[str],
        changes: tuple[dict[str, Any], ...],
    ) -> dict[str, list[str]]:
        applied = {
            str(change.get("target_id") or "")
            for change in changes
            if change.get("target_id")
        }
        return {
            "applied_target_ids": [
                target_id for target_id in targets if target_id in applied
            ],
            "unchanged_target_ids": [
                target_id for target_id in targets if target_id not in applied
            ],
        }

    async def initialize(self) -> None:
        self._lifecycle_active = False
        self.storage_ready = False
        self.storage_write_healthy = False
        self.loaded_from = "none"
        self.last_error_code = None
        self.last_write_error_code = None
        self.recovery_warning_code = None
        try:
            stored = await self._store.load()
        except Exception as exc:
            self.storage_ready = False
            self.storage_write_healthy = False
            self.last_error_code = f"storage_load_failed:{type(exc).__name__}"
            return

        selected: LoadResult | None = None
        selected_name = "empty"
        primary_error: DomainError | None = None
        backup_error: DomainError | None = None
        if stored.primary_error is None and stored.primary is not None:
            try:
                selected = load_document(stored.primary, self._effective_wall_time())
                selected_name = "primary"
            except DomainError as exc:
                primary_error = exc
        if (
            selected is None
            and stored.backup_error is None
            and stored.backup is not None
        ):
            try:
                selected = load_document(stored.backup, self._effective_wall_time())
                selected_name = "backup"
            except DomainError as exc:
                backup_error = exc
                selected = None
        both_slots_empty = (
            stored.primary_error is None
            and stored.backup_error is None
            and stored.primary is None
            and stored.backup is None
        )
        if selected is None and not both_slots_empty:
            self.storage_ready = False
            self.storage_write_healthy = False
            if stored.primary_error and stored.backup_error:
                self.last_error_code = (
                    f"storage_slots_read_failed:{stored.primary_error}:"
                    f"{stored.backup_error}"
                )
            elif stored.primary_error:
                self.last_error_code = (
                    f"storage_primary_read_failed:{stored.primary_error}"
                )
            elif stored.backup_error:
                self.last_error_code = (
                    f"storage_backup_read_failed:{stored.backup_error}"
                )
            elif primary_error is not None:
                self.last_error_code = primary_error.code
            elif backup_error is not None:
                self.last_error_code = backup_error.code
            else:
                self.last_error_code = "invalid_state_document"
            return
        if selected is None:
            selected = LoadResult(RegistryState.empty())

        now = self._effective_wall_time()
        candidate_document = state_to_document(selected.state, now)
        previous_document = candidate_document
        try:
            await self._store.commit(previous_document, candidate_document)
        except Exception as exc:
            self.storage_ready = False
            self.storage_write_healthy = False
            self.last_error_code = f"storage_initialize_failed:{type(exc).__name__}"
            self.last_write_error_code = self.last_error_code
            return

        self._state = selected.state
        self.loaded_from = selected_name
        self.quarantined = tuple(item.as_dict() for item in selected.quarantined)
        self.expired_on_load = selected.expired_count
        self.last_commit_at = now
        self.last_error_code = None
        self.last_write_error_code = None
        warnings: list[str] = []
        if stored.primary_error:
            warnings.append(f"primary_read_failed:{stored.primary_error}")
        if stored.backup_error:
            warnings.append(f"backup_read_failed:{stored.backup_error}")
        if selected_name == "backup" and primary_error is not None:
            warnings.append(f"primary_recovered_from_backup:{primary_error.code}")
        self.recovery_warning_code = ";".join(warnings) or None
        self.storage_ready = True
        self.storage_write_healthy = True
        self._lifecycle_active = True

    async def terminate(self) -> None:
        self._lifecycle_active = False
        self.storage_ready = False
        async with self._mutation_lock:
            self.storage_ready = False
            self.storage_write_healthy = False
            self._counters.clear_all()

    def _require_storage(self) -> None:
        if not self._lifecycle_active or not self.storage_ready:
            raise DomainError(
                "storage_unavailable",
                "状态存储当前不可用；插件保持无额外激活状态。",
            )

    async def _commit_mutation(
        self,
        operation: Callable[[RegistryState, float], MutationResult],
        *,
        fail_inert: bool = False,
    ) -> MutationResult:
        self._require_storage()
        async with self._mutation_lock:
            self._require_storage()
            before = self._state
            now = self._effective_wall_time()
            result = operation(before, now)
            previous_document = state_to_document(before, now)
            candidate_document = state_to_document(result.state, now)
            try:
                await self._store.commit(previous_document, candidate_document)
            except CommitIndeterminateError as exc:
                if fail_inert:
                    self._state = result.state
                    self._counters.clear(result.reset_counter_keys)
                self._metric("commit_indeterminate")
                self.last_error_code = "commit_indeterminate"
                self.last_write_error_code = "commit_indeterminate"
                self.storage_ready = False
                self.storage_write_healthy = False
                raise DomainError(
                    "commit_indeterminate",
                    "状态写入结果无法确认；插件已转为无额外激活，需重载后核对状态。",
                ) from exc
            except Exception as exc:
                if fail_inert:
                    self._state = result.state
                    self._counters.clear(result.reset_counter_keys)
                    self.storage_ready = False
                self._metric("storage_write_failed")
                self.last_error_code = "storage_write_failed"
                self.last_write_error_code = "storage_write_failed"
                self.storage_write_healthy = False
                raise DomainError(
                    "storage_write_failed",
                    "状态写入失败；运行快照未改变。",
                ) from exc
            acknowledged_at = self._effective_wall_time()
            effective_changes: list[dict[str, Any]] = []
            expired_before_ack: set[LeaseKey] = set()
            for change in result.changed:
                expires_at = change.get("expires_at")
                scope = str(change.get("scope") or "")
                target = str(change.get("target_id") or "")
                if (
                    isinstance(expires_at, (int, float))
                    and expires_at <= acknowledged_at
                    and scope
                    and target
                ):
                    expired_before_ack.add((scope, target))
                    continue
                effective_changes.append(change)
            result = MutationResult(
                prune_state(result.state, acknowledged_at),
                tuple(effective_changes),
                result.reset_counter_keys,
                result.cascaded_rate_clears,
                frozenset(expired_before_ack),
            )
            self._state = result.state
            self._counters.clear(result.reset_counter_keys)
            self.last_commit_at = acknowledged_at
            self.last_error_code = None
            self.last_write_error_code = None
            self.storage_write_healthy = True
            self._metric("mutation_committed")
            return result

    async def restore_native_after_agent_error(self, scope: Any) -> dict[str, Any]:


        normalized_scope = normalize_scope(scope)
        result = await self._commit_mutation(
            lambda state, now: restore_scope_to_native(
                state,
                self.settings.limits,
                normalized_scope,
                reason="agent_error",
                now=now,
            ),
            fail_inert=True,
        )
        self._metric("native_restore_committed")
        report = self.pending_recovery(normalized_scope)
        return {
            "changed": bool(result.changed),
            "scope_ref": self.scope_ref(normalized_scope),
            "report": report,
        }

    def pending_recovery(self, scope: Any) -> dict[str, Any] | None:
        normalized_scope = normalize_scope(scope)
        snapshot = public_snapshot(
            self._state,
            now=self._effective_wall_time(),
            scope=normalized_scope,
        )
        reports = snapshot["recovery_reports"]
        return dict(reports[0]) if reports else None

    async def acknowledge_recovery_report(
        self,
        scope: Any,
        report_id: Any,
    ) -> bool:
        normalized_scope = normalize_scope(scope)
        result = await self._commit_mutation(
            lambda state, now: acknowledge_recovery(
                state,
                normalized_scope,
                report_id,
                now=now,
            )
        )
        if result.changed:
            self._metric("recovery_report_acknowledged")
        return bool(result.changed)

    def _evaluate(
        self,
        scope: Any,
        target_id: Any,
        *,
        consume: bool,
    ) -> ActivationDecision:
        normalized_scope = normalize_scope(scope)
        target = normalize_target_ids(
            [target_id],
            self.settings.limits.max_targets_per_scope,
        )[0]
        if not self.storage_ready:
            if consume:
                self._metric("storage_unavailable")
            return ActivationDecision(
                False,
                "storage_unavailable",
                normalized_scope,
                target,
            )

        activation, rate = active_leases(
            self._state,
            normalized_scope,
            target,
            self._effective_wall_time(),
        )
        if activation is None:
            self._counters.clear({(normalized_scope, target)})
            if consume:
                self._metric("activation_absent")
            return ActivationDecision(
                False,
                "activation_absent",
                normalized_scope,
                target,
            )
        if rate is None:
            self._counters.clear({activation.key})
            if consume:
                self._metric("admitted")
            return ActivationDecision(
                True,
                "activation_lease",
                normalized_scope,
                target,
            )

        counter_operation = self._counters.admit if consume else self._counters.preview
        admitted, retry_after, remaining = counter_operation(
            activation.key,
            maximum=rate.max_activations,
            window_seconds=rate.window_seconds,
            now=self._monotonic_clock(),
        )
        if not admitted:
            if consume:
                self._metric("rate_limited")
            return ActivationDecision(
                False,
                "rate_limited",
                normalized_scope,
                target,
                rate_limited=True,
                retry_after_seconds=retry_after,
                remaining_in_window=0,
            )
        if consume:
            self._metric("admitted")
        return ActivationDecision(
            True,
            "activation_lease",
            normalized_scope,
            target,
            remaining_in_window=remaining,
        )

    def preview(self, scope: Any, target_id: Any) -> ActivationDecision:


        return self._evaluate(scope, target_id, consume=False)

    def decide(self, scope: Any, target_id: Any) -> ActivationDecision:


        return self._evaluate(scope, target_id, consume=True)

    async def manage_activation(
        self,
        *,
        actor: ActorContext,
        scope: Any,
        action: Any,
        target_ids: Any,
        duration_seconds: Any,
    ) -> dict[str, Any]:
        self._require_storage()
        normalized_scope = normalize_scope(scope)
        normalized_action = str(action or "").strip().lower()
        targets = normalize_optional_target_ids(
            target_ids,
            MAX_TARGETS_PER_OPERATION,
        )
        if normalized_action == "list":
            return {
                "status": "ok",
                "error_code": None,
                "action": "list",
                "scope_ref": self.scope_ref(normalized_scope),
                "changed": False,
                **self._success_contract(
                    outcome="activation_state_listed",
                    effect_contract=ACTIVATION_EFFECT_CONTRACT,
                    changed=False,
                ),
                "state": self._scoped_receipt_state(
                    normalized_scope,
                    targets or None,
                ),
            }
        if normalized_action not in {"enable", "renew", "disable"}:
            raise DomainError(
                "invalid_action",
                "action 必须是 enable、renew、disable 或 list。",
            )
        if not targets:
            raise DomainError("empty_target_ids", "target_ids 至少包含一个 QQ ID。")

        if normalized_action == "disable":
            result = await self._commit_mutation(
                lambda state, now: disable_activation(
                    state,
                    self.settings.limits,
                    normalized_scope,
                    targets,
                    now=now,
                )
            )
        else:
            result = await self._commit_mutation(
                lambda state, now: enable_activation(
                    state,
                    self.settings.limits,
                    normalized_scope,
                    targets,
                    duration_seconds,
                    actor.actor_ref,
                    renew_only=normalized_action == "renew",
                    now=now,
                )
            )
        changed = bool(result.changed)
        expired_before_ack = sorted(
            target_id
            for scope_name, target_id in result.expired_before_ack
            if scope_name == normalized_scope
        )
        if expired_before_ack:
            outcome = (
                "activation_partially_applied"
                if changed
                else "activation_expired_before_ack"
            )
        else:
            outcome = {
                "enable": "activation_enabled",
                "renew": "activation_renewed",
                "disable": (
                    "activation_disabled" if changed else "activation_already_absent"
                ),
            }[normalized_action]
        return {
            "status": "ok",
            "error_code": None,
            "action": normalized_action,
            "scope_ref": self.scope_ref(normalized_scope),
            "targets": targets,
            "changed": changed,
            **self._success_contract(
                outcome=outcome,
                effect_contract=ACTIVATION_EFFECT_CONTRACT,
                changed=changed,
            ),
            **self._target_partition(targets, result.changed),
            "changes": self._receipt_changes(result.changed),
            "expired_before_ack_target_ids": expired_before_ack,
            "cascaded_rate_clears": sorted(
                target_id
                for scope_name, target_id in result.cascaded_rate_clears
                if scope_name == normalized_scope
            ),
        }

    async def manage_rate(
        self,
        *,
        actor: ActorContext,
        scope: Any,
        action: Any,
        target_ids: Any,
        max_activations: Any,
        window_seconds: Any,
        duration_seconds: Any,
    ) -> dict[str, Any]:
        self._require_storage()
        normalized_scope = normalize_scope(scope)
        normalized_action = str(action or "").strip().lower()
        targets = normalize_optional_target_ids(
            target_ids,
            MAX_TARGETS_PER_OPERATION,
        )
        if normalized_action == "list":
            return {
                "status": "ok",
                "error_code": None,
                "action": "list",
                "scope_ref": self.scope_ref(normalized_scope),
                "changed": False,
                **self._success_contract(
                    outcome="rate_state_listed",
                    effect_contract=RATE_EFFECT_CONTRACT,
                    changed=False,
                ),
                "state": self._scoped_receipt_state(
                    normalized_scope,
                    targets or None,
                ),
            }
        if normalized_action not in {"set", "clear"}:
            raise DomainError(
                "invalid_action",
                "action 必须是 set、clear 或 list。",
            )
        if not targets:
            raise DomainError("empty_target_ids", "target_ids 至少包含一个 QQ ID。")
        source = "admin" if actor.channel == "dashboard" else "agent"

        if normalized_action == "clear":
            result = await self._commit_mutation(
                lambda state, now: clear_rate(
                    state,
                    self.settings.limits,
                    normalized_scope,
                    targets,
                    source=source,
                    now=now,
                )
            )
        else:
            result = await self._commit_mutation(
                lambda state, now: set_rate(
                    state,
                    self.settings.limits,
                    normalized_scope,
                    targets,
                    max_activations,
                    window_seconds,
                    duration_seconds,
                    actor.actor_ref,
                    source,
                    now=now,
                )
            )
        changed = bool(result.changed)
        expired_before_ack = sorted(
            target_id
            for scope_name, target_id in result.expired_before_ack
            if scope_name == normalized_scope
        )
        if expired_before_ack:
            outcome = (
                "rate_limit_partially_applied"
                if changed
                else "rate_limit_expired_before_ack"
            )
        else:
            outcome = {
                "set": "rate_limit_set",
                "clear": (
                    "rate_limit_cleared" if changed else "rate_limit_already_absent"
                ),
            }[normalized_action]
        return {
            "status": "ok",
            "error_code": None,
            "action": normalized_action,
            "scope_ref": self.scope_ref(normalized_scope),
            "targets": targets,
            "changed": changed,
            **self._success_contract(
                outcome=outcome,
                effect_contract=RATE_EFFECT_CONTRACT,
                changed=changed,
            ),
            **self._target_partition(targets, result.changed),
            "changes": self._receipt_changes(result.changed),
            "expired_before_ack_target_ids": expired_before_ack,
        }

    def snapshot(
        self,
        *,
        scope: Any | None = None,
        target_ids: list[Any] | None = None,
    ) -> dict[str, Any]:
        wall_now = self._effective_wall_time()
        clean = prune_state(self._state, wall_now)
        active_windows = {lease.key: lease.window_seconds for lease in clean.rates}
        return public_snapshot(
            clean,
            now=wall_now,
            scope=scope,
            target_ids=target_ids,
            recent_counts=self._counters.counts(
                active_windows,
                now=self._monotonic_clock(),
            ),
        )

    def health(self) -> dict[str, Any]:
        state = self.snapshot()
        return {
            "lifecycle_active": self._lifecycle_active,
            "storage_ready": self.storage_ready,
            "runtime_snapshot_ready": self.storage_ready,
            "storage_write_healthy": self.storage_write_healthy,
            "loaded_from": self.loaded_from,
            "last_error_code": self.last_error_code,
            "last_write_error_code": self.last_write_error_code,
            "recovery_warning_code": self.recovery_warning_code,
            "last_commit_at": self.last_commit_at,
            "quarantined_count": len(self.quarantined),
            "quarantined": list(self.quarantined),
            "expired_on_load": self.expired_on_load,
            "counter_persistence": "memory_only_reset_on_restart",
            "activation_count": len(state["activation_leases"]),
            "rate_count": len(state["rate_leases"]),
            "recovery_report_count": len(state["recovery_reports"]),
            "metrics": self._metrics_snapshot(),
        }
