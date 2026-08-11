


from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from .domain import DomainError
from .heartbeat_domain import (
    HEARTBEAT_TAG,
    HeartbeatLease,
    heartbeat_from_job,
    heartbeat_payload,
    is_owned_heartbeat_payload,
)

CLEANUP_TAG = "_sender_activation_heartbeat_cleanup"
CLEANUP_KIND = "finite_heartbeat_native_cleanup"


class AstrBotCronAdapter:


    def __init__(self, context: Any, *, active_execution_enabled: bool = True) -> None:
        self._manager = getattr(context, "cron_manager", None)
        self._active_execution_enabled = bool(active_execution_enabled)

    def _require_manager(self) -> Any:
        manager = self._manager
        required = (
            "add_active_job",
            "add_basic_job",
            "update_job",
            "delete_job",
            "list_jobs",
        )
        if manager is None or any(
            not callable(getattr(manager, name, None)) for name in required
        ):
            raise DomainError(
                "native_cron_unavailable",
                "当前 AstrBot 未提供兼容的原生 Cron 管理接口。",
            )
        return manager

    async def raw_owned_jobs(self) -> list[Any]:
        manager = self._require_manager()
        jobs = await manager.list_jobs("active_agent")
        return [
            job
            for job in jobs
            if is_owned_heartbeat_payload(getattr(job, "payload", None))
        ]

    async def raw_cleanup_jobs(self) -> list[Any]:
        manager = self._require_manager()
        jobs = await manager.list_jobs("basic")
        return [
            job
            for job in jobs
            if isinstance(getattr(job, "payload", None), dict)
            and isinstance(getattr(job, "payload", {}).get(CLEANUP_TAG), dict)
            and getattr(job, "payload", {})[CLEANUP_TAG].get("kind") == CLEANUP_KIND
        ]

    @staticmethod
    def _cleanup_cron(expires_at: float) -> str:
        instant = datetime.fromtimestamp(expires_at, tz=timezone.utc)
        return f"{instant.minute} {instant.hour} {instant.day} {instant.month} *"

    async def _create_cleanup(self, lease: HeartbeatLease) -> None:
        manager = self._require_manager()
        try:
            await manager.add_basic_job(
                name=f"[临时心跳到期] {lease.name}",
                cron_expression=self._cleanup_cron(lease.expires_at),
                handler=self._expire_handler,
                description="到期后删除对应心跳与本清理任务。",
                timezone="UTC",
                payload={
                    "lease_id": lease.lease_id,
                    "expires_at": lease.expires_at,
                    CLEANUP_TAG: {
                        "kind": CLEANUP_KIND,
                        "parent_lease_id": lease.lease_id,
                    },
                },
                enabled=True,
                persistent=False,
            )
        except Exception as exc:
            raise DomainError(
                "native_cron_cleanup_create_failed",
                f"AstrBot 原生到期清理任务创建失败: {type(exc).__name__}",
            ) from exc

    async def _delete_cleanup(self, lease_id: str) -> None:
        for job in await self.raw_cleanup_jobs():
            payload = getattr(job, "payload", {}) or {}
            tag = payload.get(CLEANUP_TAG, {}) or {}
            if str(tag.get("parent_lease_id") or "") != lease_id:
                continue
            await self._delete_quietly(getattr(job, "job_id", ""))

    async def _expire_handler(
        self,
        lease_id: str = "",
        expires_at: float = 0,
        **_: Any,
    ) -> None:
        try:
            if float(expires_at) > time.time() + 1:
                return
            await self._delete_quietly(lease_id)
            await self._delete_cleanup(lease_id)
        except Exception:
            return

    async def inventory(
        self,
    ) -> tuple[list[HeartbeatLease], list[dict[str, str]]]:
        leases: list[HeartbeatLease] = []
        quarantined: list[dict[str, str]] = []
        for job in await self.raw_owned_jobs():
            try:
                leases.append(heartbeat_from_job(job))
            except DomainError as exc:
                job_id = str(getattr(job, "job_id", "") or "unknown")
                quarantined.append({"lease_id": job_id, "error_code": exc.code})
                try:
                    await self._require_manager().update_job(job_id, enabled=False)
                except Exception:
                    pass
        return leases, quarantined

    async def create(
        self,
        *,
        scope: str,
        sender_id: str,
        actor_ref: str,
        source: str,
        name: str,
        cron_expression: str,
        instruction: str,
        created_at: float,
        expires_at: float,
    ) -> HeartbeatLease:
        manager = self._require_manager()
        payload = heartbeat_payload(
            scope=scope,
            sender_id=sender_id,
            instruction=instruction,
            name=name,
            created_at=created_at,
            expires_at=expires_at,
            created_by=actor_ref,
            source=source,
        )
        job = None
        try:
            job = await manager.add_active_job(
                name=f"[临时心跳] {name}",
                cron_expression=cron_expression,
                payload=payload,
                description="本插件有限期心跳租约；由 AstrBot 原生主 Agent 执行。",
                enabled=self._active_execution_enabled,
                persistent=False,
                run_once=False,
            )
            lease = heartbeat_from_job(job)
            await self._create_cleanup(lease)
            return lease
        except DomainError:
            if job is not None:
                await self._delete_quietly(getattr(job, "job_id", ""))
                await self._delete_cleanup(str(getattr(job, "job_id", "") or ""))
            raise
        except Exception as exc:
            if job is not None:
                await self._delete_quietly(getattr(job, "job_id", ""))
                await self._delete_cleanup(str(getattr(job, "job_id", "") or ""))
            raise DomainError(
                "native_cron_create_failed",
                f"AstrBot 原生 Cron 创建失败: {type(exc).__name__}",
            ) from exc

    async def renew(
        self,
        lease: HeartbeatLease,
        *,
        sender_id: str,
        actor_ref: str,
        source: str,
        name: str,
        cron_expression: str,
        instruction: str,
        now: float,
        expires_at: float,
    ) -> HeartbeatLease:
        manager = self._require_manager()
        raw = await self.find_raw(lease.lease_id, lease.scope)
        if raw is None:
            raise DomainError("heartbeat_absent", "心跳租约不存在或不属于当前作用域。")
        old_payload = dict(getattr(raw, "payload", {}) or {})
        old_fields = {
            "name": getattr(raw, "name", None),
            "cron_expression": getattr(raw, "cron_expression", None),
            "description": getattr(raw, "description", None),
            "payload": old_payload,
            "enabled": bool(getattr(raw, "enabled", False)),
        }
        payload = heartbeat_payload(
            scope=lease.scope,
            sender_id=sender_id,
            instruction=instruction,
            name=name,
            created_at=now,
            expires_at=expires_at,
            created_by=actor_ref,
            source=source,
            plugin_suspended=False,
        )
        enabled = self._active_execution_enabled
        try:
            updated = await manager.update_job(
                lease.lease_id,
                name=f"[临时心跳] {name}",
                cron_expression=cron_expression,
                description="本插件有限期心跳租约；由 AstrBot 原生主 Agent 执行。",
                payload=payload,
                enabled=enabled,
                persistent=False,
                run_once=False,
            )
            if updated is None:
                raise DomainError("heartbeat_absent", "心跳租约已不存在。")
            renewed = heartbeat_from_job(updated)
            await self._delete_cleanup(lease.lease_id)
            await self._create_cleanup(renewed)
            return renewed
        except DomainError:
            if await self.find_raw(lease.lease_id, lease.scope) is not None:
                try:
                    restored = await manager.update_job(lease.lease_id, **old_fields)
                    if restored is not None:
                        await self._delete_cleanup(lease.lease_id)
                        await self._create_cleanup(heartbeat_from_job(restored))
                except Exception:
                    pass
            raise
        except Exception as exc:
            try:
                await manager.update_job(lease.lease_id, **old_fields)
            except Exception as rollback_error:
                raise DomainError(
                    "native_cron_update_indeterminate",
                    "原生 Cron 更新失败且旧状态无法确认。",
                ) from rollback_error
            raise DomainError(
                "native_cron_update_failed",
                f"AstrBot 原生 Cron 更新失败: {type(exc).__name__}",
            ) from exc

    async def find_raw(self, lease_id: str, scope: str) -> Any | None:
        for job in await self.raw_owned_jobs():
            if str(getattr(job, "job_id", "")) != lease_id:
                continue
            try:
                lease = heartbeat_from_job(job)
            except DomainError:
                return None
            return job if lease.scope == scope else None
        return None

    async def delete(self, lease_id: str, scope: str) -> bool:
        raw = await self.find_raw(lease_id, scope)
        if raw is None:
            return False
        try:
            await self._require_manager().delete_job(lease_id)
            await self._delete_cleanup(lease_id)
        except Exception as exc:
            raise DomainError(
                "native_cron_delete_failed",
                f"AstrBot 原生 Cron 删除失败: {type(exc).__name__}",
            ) from exc
        return True

    async def delete_if_expired(
        self, lease_id: str, expires_at: float, now: float
    ) -> bool:
        for job in await self.raw_owned_jobs():
            if str(getattr(job, "job_id", "")) != lease_id:
                continue
            try:
                lease = heartbeat_from_job(job)
            except DomainError:
                return False
            if lease.expires_at != expires_at or lease.expires_at > now:
                return False
            await self._require_manager().delete_job(lease_id)
            await self._delete_cleanup(lease_id)
            return True
        return False

    async def reconcile_start(
        self, now: float
    ) -> tuple[list[HeartbeatLease], list[dict[str, str]]]:
        manager = self._require_manager()
        for cleanup in await self.raw_cleanup_jobs():
            await self._delete_quietly(getattr(cleanup, "job_id", ""))
        leases, quarantined = await self.inventory()
        active: list[HeartbeatLease] = []
        for lease in leases:
            if lease.expires_at <= now:
                await self._delete_quietly(lease.lease_id)
                continue
            raw = await self.find_raw(lease.lease_id, lease.scope)
            if raw is None:
                continue
            payload = dict(getattr(raw, "payload", {}) or {})
            tag = dict(payload.get(HEARTBEAT_TAG, {}) or {})
            should_enable = (
                (lease.enabled or lease.plugin_suspended)
                if self._active_execution_enabled
                else False
            )
            if lease.plugin_suspended:
                tag["plugin_suspended"] = False
                payload[HEARTBEAT_TAG] = tag
            try:
                updated = await manager.update_job(
                    lease.lease_id,
                    payload=payload,
                    enabled=should_enable,
                    persistent=False,
                )
                if updated is not None:
                    reconciled = heartbeat_from_job(updated)
                    await self._create_cleanup(reconciled)
                    active.append(reconciled)
            except Exception as exc:
                quarantined.append(
                    {
                        "lease_id": lease.lease_id,
                        "error_code": f"reconcile_{type(exc).__name__}",
                    }
                )
                try:
                    await manager.update_job(lease.lease_id, enabled=False)
                except Exception:
                    pass
        return active, quarantined

    async def suspend_for_shutdown(self) -> list[str]:
        manager = self._require_manager()
        failures: list[str] = []
        for cleanup in await self.raw_cleanup_jobs():
            await self._delete_quietly(getattr(cleanup, "job_id", ""))
        for job in await self.raw_owned_jobs():
            if not bool(getattr(job, "enabled", False)):
                continue
            job_id = str(getattr(job, "job_id", "") or "")
            payload = dict(getattr(job, "payload", {}) or {})
            tag = dict(payload.get(HEARTBEAT_TAG, {}) or {})
            tag["plugin_suspended"] = True
            payload[HEARTBEAT_TAG] = tag
            try:
                await manager.update_job(
                    job_id,
                    payload=payload,
                    enabled=False,
                    persistent=False,
                )
            except Exception:
                failures.append(job_id)
        return failures

    async def _delete_quietly(self, lease_id: Any) -> None:
        value = str(lease_id or "").strip()
        if not value:
            return
        try:
            await self._require_manager().delete_job(value)
        except Exception:
            pass
