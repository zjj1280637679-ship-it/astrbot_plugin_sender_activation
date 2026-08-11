from __future__ import annotations

import hashlib
import inspect
import math
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from .domain import DomainError
from .heartbeat_domain import HeartbeatLease, heartbeat_from_job, is_owned_heartbeat_payload

DRIVER_TAG = "_sender_activation_heartbeat_driver"
DRIVER_KIND = "finite_heartbeat_preflight_driver"
DRIVER_CLEANUP_TAG = "_sender_activation_heartbeat_driver_cleanup"
DRIVER_CLEANUP_KIND = "finite_heartbeat_preflight_driver_cleanup"


class HeartbeatWakeGate:
    """Recurring low-cost preflight in front of the native active-agent heartbeat.

    The active-agent Cron row is deliberately disabled and serves only as the native
    Agent execution template. A basic Cron driver owns the recurrence, checks the
    current session permission, then calls run_job_now() only when activation is still
    allowed. This prevents a disabled session from paying an LLM turn merely to yield.
    """

    def __init__(
        self,
        cron_manager: Any,
        *,
        preflight: Callable[[str], bool | Awaitable[bool]] | None = None,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._manager = cron_manager
        self._preflight = preflight
        self._wall_clock = wall_clock
        self._active = False
        self._executed = 0
        self._suppressed = 0
        self._stale_deleted = 0
        self._failures: list[dict[str, str]] = []

    def _require_manager(self) -> Any:
        manager = self._manager
        required = (
            "add_basic_job",
            "delete_job",
            "list_jobs",
            "run_job_now",
        )
        if manager is None or any(not callable(getattr(manager, name, None)) for name in required):
            raise DomainError(
                "heartbeat_preflight_unavailable",
                "当前 AstrBot 未提供兼容的心跳前置调度接口。",
            )
        return manager

    @staticmethod
    def _scope_ref(scope: str) -> str:
        digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:10]
        return f"heartbeat:{digest}"

    @staticmethod
    def _expiry_cron(expires_at: float) -> str:
        instant = datetime.fromtimestamp(expires_at, tz=timezone.utc)
        return f"{instant.minute} {instant.hour} {instant.day} {instant.month} *"

    async def _raw_basic_jobs(self) -> list[Any]:
        return await self._require_manager().list_jobs("basic")

    async def _driver_jobs(self) -> list[Any]:
        rows = []
        for job in await self._raw_basic_jobs():
            payload = getattr(job, "payload", None)
            if not isinstance(payload, dict):
                continue
            tag = payload.get(DRIVER_TAG)
            if isinstance(tag, dict) and tag.get("kind") == DRIVER_KIND:
                rows.append(job)
        return rows

    async def _cleanup_jobs(self) -> list[Any]:
        rows = []
        for job in await self._raw_basic_jobs():
            payload = getattr(job, "payload", None)
            if not isinstance(payload, dict):
                continue
            tag = payload.get(DRIVER_CLEANUP_TAG)
            if isinstance(tag, dict) and tag.get("kind") == DRIVER_CLEANUP_KIND:
                rows.append(job)
        return rows

    @staticmethod
    def _parent(job: Any, tag_name: str) -> str:
        payload = getattr(job, "payload", {}) or {}
        tag = payload.get(tag_name, {}) or {}
        return str(tag.get("parent_lease_id") or "").strip()

    async def _delete_job_truthfully(self, job_id: str) -> bool:
        try:
            await self._require_manager().delete_job(job_id)
            return True
        except Exception as exc:
            try:
                still_exists = any(
                    str(getattr(job, "job_id", "") or "") == job_id
                    for job in await self._raw_basic_jobs()
                )
            except Exception:
                still_exists = True
            if still_exists:
                self._failures.append(
                    {"job_id": job_id, "error_code": f"delete_{type(exc).__name__}"}
                )
                return False
            return True

    async def _preflight_allowed(self, scope: str) -> bool:
        if not self._active:
            return False
        callback = self._preflight
        if callback is None:
            return True
        try:
            result = callback(scope)
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
        except Exception:
            return False

    async def _active_template_exists(self, lease_id: str) -> bool:
        manager = self._require_manager()
        for job in await manager.list_jobs("active_agent"):
            if str(getattr(job, "job_id", "") or "") != lease_id:
                continue
            return is_owned_heartbeat_payload(getattr(job, "payload", None))
        return False

    async def _driver_handler(
        self,
        lease_id: str = "",
        scope: str = "",
        expires_at: float = 0,
        **_: Any,
    ) -> None:
        parent = str(lease_id or "").strip()
        if not parent or not scope:
            return
        now = float(self._wall_clock())
        if float(expires_at or 0) <= now or not await self._active_template_exists(parent):
            self._stale_deleted += 1
            await self.disarm(parent)
            return
        if not await self._preflight_allowed(scope):
            self._suppressed += 1
            return
        try:
            await self._require_manager().run_job_now(parent)
            self._executed += 1
        except Exception as exc:
            self._failures.append(
                {"job_id": parent, "error_code": f"run_{type(exc).__name__}"}
            )

    async def _cleanup_handler(
        self,
        lease_id: str = "",
        expires_at: float = 0,
        **_: Any,
    ) -> None:
        if float(expires_at or 0) > float(self._wall_clock()) + 1:
            return
        await self.disarm(str(lease_id or ""))

    async def initialize(self) -> None:
        self._active = False
        self._failures.clear()
        # Drivers are non-persistent runtime mechanics. Rebuild them from heartbeat
        # lease templates so a hot reload cannot duplicate recurrence.
        for job in [*await self._driver_jobs(), *await self._cleanup_jobs()]:
            job_id = str(getattr(job, "job_id", "") or "")
            if job_id:
                await self._delete_job_truthfully(job_id)
        self._active = True

    async def terminate(self) -> list[str]:
        self._active = False
        failed: list[str] = []
        for job in [*await self._driver_jobs(), *await self._cleanup_jobs()]:
            job_id = str(getattr(job, "job_id", "") or "")
            if job_id and not await self._delete_job_truthfully(job_id):
                failed.append(job_id)
        return failed

    async def arm(self, lease: HeartbeatLease) -> HeartbeatLease:
        if not self._active:
            raise DomainError("heartbeat_preflight_inactive", "心跳前置门当前未激活。")
        await self.disarm(lease.lease_id)
        manager = self._require_manager()
        driver = None
        cleanup = None
        payload = {
            "lease_id": lease.lease_id,
            "scope": lease.scope,
            "expires_at": lease.expires_at,
            DRIVER_TAG: {
                "kind": DRIVER_KIND,
                "parent_lease_id": lease.lease_id,
                "scope_ref": self._scope_ref(lease.scope),
            },
        }
        try:
            driver = await manager.add_basic_job(
                name=f"[心跳前置门] {lease.name}",
                cron_expression=lease.cron_expression,
                handler=self._driver_handler,
                description="在主 Agent 心跳激活前检查当前会话权限。",
                timezone="UTC",
                payload=payload,
                enabled=True,
                persistent=False,
            )
            cleanup = await manager.add_basic_job(
                name=f"[心跳前置门到期] {lease.name}",
                cron_expression=self._expiry_cron(lease.expires_at),
                handler=self._cleanup_handler,
                description="到期后清理对应心跳前置门。",
                timezone="UTC",
                payload={
                    "lease_id": lease.lease_id,
                    "expires_at": lease.expires_at,
                    DRIVER_CLEANUP_TAG: {
                        "kind": DRIVER_CLEANUP_KIND,
                        "parent_lease_id": lease.lease_id,
                    },
                },
                enabled=True,
                persistent=False,
            )
        except Exception as exc:
            for job in (driver, cleanup):
                if job is None:
                    continue
                await self._delete_job_truthfully(str(getattr(job, "job_id", "") or ""))
            if isinstance(exc, DomainError):
                raise
            raise DomainError(
                "heartbeat_preflight_create_failed",
                f"心跳前置门创建失败: {type(exc).__name__}",
            ) from exc
        return self.with_driver_state(lease, driver)

    async def disarm(self, lease_id: str) -> list[str]:
        parent = str(lease_id or "").strip()
        removed: list[str] = []
        if not parent:
            return removed
        for job, tag_name in [
            *((job, DRIVER_TAG) for job in await self._driver_jobs()),
            *((job, DRIVER_CLEANUP_TAG) for job in await self._cleanup_jobs()),
        ]:
            if self._parent(job, tag_name) != parent:
                continue
            job_id = str(getattr(job, "job_id", "") or "")
            if job_id and await self._delete_job_truthfully(job_id):
                removed.append(job_id)
        return removed

    async def driver_for(self, lease_id: str) -> Any | None:
        parent = str(lease_id or "").strip()
        for job in await self._driver_jobs():
            if self._parent(job, DRIVER_TAG) == parent:
                return job
        return None

    @staticmethod
    def _timestamp(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            current = value
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            return current.timestamp()
        try:
            return float(value)
        except (TypeError, ValueError, OverflowError):
            return None

    def with_driver_state(self, lease: HeartbeatLease, driver: Any | None) -> HeartbeatLease:
        if driver is None:
            return HeartbeatLease(
                **{**lease.__dict__, "enabled": False, "next_run_at": None}
            )
        return HeartbeatLease(
            **{
                **lease.__dict__,
                "enabled": bool(getattr(driver, "enabled", False)),
                "next_run_at": self._timestamp(getattr(driver, "next_run_time", None)),
            }
        )

    async def decorate(self, leases: list[HeartbeatLease]) -> list[HeartbeatLease]:
        drivers = {
            self._parent(job, DRIVER_TAG): job
            for job in await self._driver_jobs()
        }
        return [self.with_driver_state(lease, drivers.get(lease.lease_id)) for lease in leases]

    async def health(self) -> dict[str, Any]:
        drivers = await self._driver_jobs() if self._active else []
        return {
            "heartbeat_preflight_active": self._active,
            "heartbeat_preflight_driver_count": len(drivers),
            "heartbeat_preflight_executed_total": self._executed,
            "heartbeat_preflight_suppressed_total": self._suppressed,
            "heartbeat_preflight_stale_deleted_total": self._stale_deleted,
            "heartbeat_preflight_failure_count": len(self._failures),
            "heartbeat_preflight_failures": list(self._failures[-20:]),
        }
