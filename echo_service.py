from __future__ import annotations

import asyncio
import hashlib
import inspect
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable
from typing import Any

from .domain import DomainError, normalize_scope

ECHO_TAG = "_sender_activation_echo"
ECHO_KIND = "finite_nonrecursive_attention_echo"
ECHO_SCHEMA_VERSION = 1
ECHO_EFFECT_CONTRACT = "finite_nonrecursive_self_activation_hook"


@dataclass(frozen=True)
class EchoLimits:
    default_delay_seconds: float = 30.0
    max_delay_seconds: float = 30 * 60.0
    default_count: int = 1
    max_count: int = 1
    default_interval_seconds: float = 30.0
    max_interval_seconds: float = 30 * 60.0
    max_pending_per_scope: int = 10
    max_pending_total: int = 100


@dataclass(frozen=True)
class EchoHook:
    hook_id: str
    group_id: str
    scope: str
    source: str
    instruction: str
    run_at: float
    created_at: float
    created_by: str
    batch_index: int
    batch_count: int
    enabled: bool
    status: str

    def as_public(self, now: float, scope_ref: str) -> dict[str, Any]:
        return {
            "hook_id": self.hook_id,
            "group_id": self.group_id,
            "scope_ref": scope_ref,
            "source": self.source,
            "instruction": self.instruction,
            "run_at": self.run_at,
            "remaining_seconds": max(0, math.ceil(self.run_at - now)),
            "created_at": self.created_at,
            "created_by": self.created_by,
            "batch_index": self.batch_index,
            "batch_count": self.batch_count,
            "enabled": self.enabled,
            "status": self.status,
        }


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise DomainError(f"invalid_{field}", f"{field} 必须是有限数值。")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DomainError(f"invalid_{field}", f"{field} 必须是有限数值。") from exc
    if not math.isfinite(number):
        raise DomainError(f"invalid_{field}", f"{field} 必须是有限数值。")
    return number


def _positive_float(value: Any, field: str, maximum: float) -> float:
    number = _number(value, field)
    if number <= 0 or number > maximum:
        raise DomainError(
            f"invalid_{field}",
            f"{field} 必须大于 0 且不超过 {maximum:g}。",
        )
    return number


def _positive_int(value: Any, field: str, maximum: int) -> int:
    number = _number(value, field)
    if not number.is_integer() or number <= 0 or number > maximum:
        raise DomainError(
            f"invalid_{field}",
            f"{field} 必须是 1 到 {maximum} 的整数。",
        )
    return int(number)


def _instruction(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise DomainError("missing_echo_instruction", "回响必须说明稍后重新检查的语境目标。")
    if len(text) > 2000 or "\x00" in text:
        raise DomainError("invalid_echo_instruction", "回响目标不能超过 2000 字且不能包含空字符。")
    return text


def _actor(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 128:
        raise DomainError("invalid_actor", "回响创建者标识无效。")
    return text


def is_owned_echo_payload(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    tag = payload.get(ECHO_TAG)
    return isinstance(tag, dict) and tag.get("kind") == ECHO_KIND


def echo_from_job(job: Any) -> EchoHook:
    payload = getattr(job, "payload", None)
    if not is_owned_echo_payload(payload):
        raise DomainError("not_owned_echo", "该任务不属于本插件回响。")
    tag = payload[ECHO_TAG]
    if tag.get("schema_version") != ECHO_SCHEMA_VERSION:
        raise DomainError("unsupported_echo_schema", "回响 Schema 版本不受支持。")
    hook_id = str(getattr(job, "job_id", "") or "").strip()
    if not hook_id:
        raise DomainError("invalid_echo_job_id", "原生回响任务 ID 无效。")
    scope = normalize_scope(payload.get("session"))
    group_id = str(tag.get("group_id") or "").strip()
    if not group_id:
        raise DomainError("invalid_echo_group", "回响组 ID 无效。")
    source = str(tag.get("source") or "").strip()
    if not source or source == "echo":
        raise DomainError("invalid_echo_source", "回响不能来源于另一次回响。")
    instruction = _instruction(tag.get("instruction"))
    run_at = _number(tag.get("run_at"), "run_at")
    created_at = _number(tag.get("created_at"), "created_at")
    created_by = _actor(tag.get("created_by"))
    batch_index = _positive_int(tag.get("batch_index"), "batch_index", 10**6)
    batch_count = _positive_int(tag.get("batch_count"), "batch_count", 10**6)
    if batch_index > batch_count:
        raise DomainError("invalid_echo_batch", "回响批次序号无效。")
    return EchoHook(
        hook_id=hook_id,
        group_id=group_id,
        scope=scope,
        source=source,
        instruction=instruction,
        run_at=run_at,
        created_at=created_at,
        created_by=created_by,
        batch_index=batch_index,
        batch_count=batch_count,
        enabled=bool(tag.get("armed", True)),
        status=str(getattr(job, "status", "unknown") or "unknown"),
    )


class EchoService:
    def __init__(
        self,
        limits: EchoLimits,
        cron_manager: Any,
        *,
        wall_clock=time.time,
        preflight: Callable[[str], bool | Awaitable[bool]] | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.limits = limits
        self._manager = cron_manager
        self._wall_clock = wall_clock
        self._preflight = preflight
        self._sleeper = sleeper
        self._active = False
        self._created = 0
        self._cancelled = 0
        self._preflight_suppressed = 0
        self._executed = 0
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._quarantined: list[dict[str, str]] = []

    def _require_manager(self) -> Any:
        manager = self._manager
        required = ("add_active_job", "delete_job", "list_jobs", "run_job_now")
        if manager is None or any(not callable(getattr(manager, name, None)) for name in required):
            raise DomainError("echo_native_scheduler_unavailable", "当前 AstrBot 未提供兼容的原生一次性主动任务接口。")
        return manager

    @staticmethod
    def _scope_ref(scope: str) -> str:
        digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:10]
        prefix = ":".join(scope.split(":")[:2]) or "scope"
        return f"{prefix}:{digest}"

    async def _raw_owned_jobs(self) -> list[Any]:
        manager = self._require_manager()
        jobs = await manager.list_jobs("active_agent")
        return [job for job in jobs if is_owned_echo_payload(getattr(job, "payload", None))]

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

    async def _delete_owned_job(self, hook_id: str, *, strict: bool = False) -> bool:
        task = self._tasks.pop(hook_id, None)
        current = asyncio.current_task()
        if task is not None and task is not current and not task.done():
            task.cancel()
        try:
            await self._require_manager().delete_job(hook_id)
            return True
        except Exception as exc:
            # delete_job has no "already absent" return contract. Re-inventory once so
            # an idempotent race is success, but a surviving disabled native row is not
            # falsely reported as cancelled.
            try:
                still_exists = any(
                    str(getattr(job, "job_id", "") or "") == hook_id
                    for job in await self._raw_owned_jobs()
                )
            except Exception:
                still_exists = True
            if not still_exists:
                return True
            self._quarantined.append(
                {
                    "hook_id": hook_id,
                    "error_code": f"echo_delete_{type(exc).__name__}",
                }
            )
            if strict:
                raise DomainError(
                    "echo_delete_failed",
                    "回响本地计时器已停止，但原生禁用任务删除失败；不能声称已完全取消。",
                ) from exc
            return False

    async def _execute_armed_hook(self, hook_id: str, scope: str) -> None:
        if not await self._preflight_allowed(scope):
            self._preflight_suppressed += 1
            await self._delete_owned_job(hook_id, strict=False)
            return
        manager = self._require_manager()
        try:
            # The native job is deliberately stored disabled, so AstrBot cannot bypass
            # this preflight. run_job_now(ignore_enabled=True) is the only execution path.
            await manager.run_job_now(hook_id)
            self._executed += 1
        finally:
            await self._delete_owned_job(hook_id, strict=False)

    async def _wait_and_execute(self, hook_id: str, scope: str, delay: float) -> None:
        try:
            await self._sleeper(max(0.0, delay))
            if self._active:
                await self._execute_armed_hook(hook_id, scope)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._quarantined.append(
                {"hook_id": hook_id, "error_code": f"echo_execute_{type(exc).__name__}"}
            )
            await self._delete_owned_job(hook_id, strict=False)
        finally:
            self._tasks.pop(hook_id, None)

    def _arm_task(self, hook_id: str, scope: str, run_at: float) -> None:
        delay = max(0.0, run_at - float(self._wall_clock()))
        task = asyncio.create_task(
            self._wait_and_execute(hook_id, scope, delay),
            name=f"sender-echo-{hook_id}",
        )
        self._tasks[hook_id] = task

    async def initialize(self) -> None:
        self._active = False
        for task in list(self._tasks.values()):
            task.cancel()
        self._tasks.clear()
        self._quarantined.clear()
        try:
            # Echo hooks are intentionally non-persistent. Any rows surviving a plugin or
            # host restart are stale promises and are deleted instead of being resurrected.
            for job in await self._raw_owned_jobs():
                try:
                    await self._require_manager().delete_job(str(getattr(job, "job_id", "")))
                except Exception as exc:
                    self._quarantined.append(
                        {
                            "hook_id": str(getattr(job, "job_id", "") or "unknown"),
                            "error_code": f"startup_cleanup_{type(exc).__name__}",
                        }
                    )
            self._active = True
        except DomainError:
            raise
        except Exception as exc:
            raise DomainError(
                "echo_native_scheduler_unavailable",
                f"初始化回响调度器失败: {type(exc).__name__}",
            ) from exc

    async def terminate(self) -> list[str]:
        self._active = False
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        failures: list[str] = []
        try:
            jobs = await self._raw_owned_jobs()
        except Exception:
            return ["inventory"]
        for job in jobs:
            hook_id = str(getattr(job, "job_id", "") or "")
            try:
                await self._require_manager().delete_job(hook_id)
            except Exception:
                failures.append(hook_id)
        return failures

    def _normalize_create(
        self,
        *,
        source: Any,
        delay_seconds: Any,
        count: Any,
        interval_seconds: Any,
        instruction: Any,
    ) -> tuple[str, float, int, float, str]:
        normalized_source = str(source or "").strip()
        if not normalized_source:
            raise DomainError("missing_echo_source", "当前回合缺少可验证的激活来源。")
        if normalized_source == "echo":
            raise DomainError("echo_recursion_forbidden", "回响触发的回合不能创建新的回响。")
        delay = self.limits.default_delay_seconds if delay_seconds in (None, 0, 0.0, "") else delay_seconds
        delay = _positive_float(delay, "delay_seconds", self.limits.max_delay_seconds)
        batch_count = self.limits.default_count if count in (None, 0, 0.0, "") else count
        batch_count = _positive_int(batch_count, "count", self.limits.max_count)
        interval = self.limits.default_interval_seconds if interval_seconds in (None, 0, 0.0, "") else interval_seconds
        interval = _positive_float(interval, "interval_seconds", self.limits.max_interval_seconds)
        return normalized_source, delay, batch_count, interval, _instruction(instruction)

    async def snapshot(self, *, scope: str | None = None) -> dict[str, Any]:
        now = float(self._wall_clock())
        hooks: list[dict[str, Any]] = []
        quarantined = list(self._quarantined)
        for job in await self._raw_owned_jobs():
            try:
                hook = echo_from_job(job)
            except DomainError as exc:
                quarantined.append(
                    {
                        "hook_id": str(getattr(job, "job_id", "") or "unknown"),
                        "error_code": exc.code,
                    }
                )
                continue
            if scope is not None and hook.scope != scope:
                continue
            hooks.append(hook.as_public(now, self._scope_ref(hook.scope)))
        hooks.sort(key=lambda row: (row["run_at"], row["hook_id"]))
        return {
            "echo_hooks": hooks,
            "quarantined": quarantined,
            "raw_scopes": sorted(
                {
                    echo_from_job(job).scope
                    for job in await self._raw_owned_jobs()
                    if is_owned_echo_payload(getattr(job, "payload", None))
                    and _safe_echo(job)
                }
            ),
        }

    async def create(
        self,
        *,
        scope: Any,
        sender_id: Any,
        actor_ref: Any,
        source: Any,
        delay_seconds: Any,
        count: Any,
        interval_seconds: Any,
        instruction: Any,
    ) -> dict[str, Any]:
        if not self._active:
            raise DomainError("echo_service_inactive", "回响服务当前未激活。")
        normalized_scope = normalize_scope(scope)
        normalized_source, delay, batch_count, interval, normalized_instruction = self._normalize_create(
            source=source,
            delay_seconds=delay_seconds,
            count=count,
            interval_seconds=interval_seconds,
            instruction=instruction,
        )
        sender = str(sender_id or "").strip()
        if not sender:
            raise DomainError("missing_sender", "当前回合缺少发送者 ID。")
        creator = _actor(actor_ref)
        current = await self.snapshot()
        pending = current["echo_hooks"]
        scope_pending = sum(1 for row in pending if row["scope_ref"] == self._scope_ref(normalized_scope))
        if scope_pending + batch_count > self.limits.max_pending_per_scope:
            raise DomainError("echo_scope_capacity_exceeded", "当前作用域待执行回响数量达到配置上限。")
        if len(pending) + batch_count > self.limits.max_pending_total:
            raise DomainError("echo_total_capacity_exceeded", "全局待执行回响数量达到配置上限。")

        now = float(self._wall_clock())
        group_seed = f"{normalized_scope}|{sender}|{creator}|{normalized_source}|{now:.9f}|{self._created}"
        group_id = hashlib.sha256(group_seed.encode("utf-8")).hexdigest()[:16]
        created_jobs: list[Any] = []
        manager = self._require_manager()
        try:
            for index in range(batch_count):
                run_at = now + delay + index * interval
                note = (
                    "[有限回响] 这是一次由此前真实激活显式挂载的有限主动再检查，不是新的用户命令。"
                    "请只检查原会话在此前激活之后是否出现与自身/原任务相关的新对话；若没有相关新事实，"
                    "调用 yield_current_turn 保持无可见回复。该回合不得创建新的回响。\n"
                    f"原目标：{normalized_instruction}"
                )
                payload = {
                    "session": normalized_scope,
                    "sender_id": sender,
                    "origin": "astrbot_plugin_sender_activation",
                    "note": note,
                    ECHO_TAG: {
                        "kind": ECHO_KIND,
                        "schema_version": ECHO_SCHEMA_VERSION,
                        "group_id": group_id,
                        "source": normalized_source,
                        "instruction": normalized_instruction,
                        "created_at": now,
                        "run_at": run_at,
                        "created_by": creator,
                        "batch_index": index + 1,
                        "batch_count": batch_count,
                        "armed": True,
                    },
                }
                job = await manager.add_active_job(
                    name=f"[有限回响] {group_id}-{index + 1}",
                    cron_expression=None,
                    payload=payload,
                    description="本插件有限、不可递归的一次性回响钩子。",
                    timezone="UTC",
                    enabled=False,
                    persistent=False,
                    run_once=True,
                    run_at=datetime.fromtimestamp(run_at, tz=timezone.utc),
                )
                created_jobs.append(job)
                self._arm_task(str(getattr(job, "job_id", "")), normalized_scope, run_at)
        except Exception as exc:
            for job in created_jobs:
                try:
                    await self._delete_owned_job(str(getattr(job, "job_id", "")), strict=False)
                except Exception:
                    pass
            if isinstance(exc, DomainError):
                raise
            raise DomainError(
                "echo_create_failed",
                f"原生一次性回响创建失败: {type(exc).__name__}",
            ) from exc

        self._created += len(created_jobs)
        hooks = [echo_from_job(job).as_public(now, self._scope_ref(normalized_scope)) for job in created_jobs]
        return {
            "status": "ok",
            "error_code": None,
            "action": "create",
            "scope_ref": self._scope_ref(normalized_scope),
            "group_id": group_id,
            "changed": bool(hooks),
            "outcome": "echo_hooks_created",
            "effect_contract": ECHO_EFFECT_CONTRACT,
            "effect_applied": bool(hooks),
            "effect_state": "applied" if hooks else "not_applied",
            "reply_guaranteed": False,
            "hooks": hooks,
        }

    async def cancel(
        self,
        *,
        scope: Any,
        hook_ids: list[str] | None = None,
        group_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_scope = normalize_scope(scope)
        hook_set = {str(value).strip() for value in (hook_ids or []) if str(value).strip()}
        group_set = {str(value).strip() for value in (group_ids or []) if str(value).strip()}
        manager = self._require_manager()
        removed: list[str] = []
        failed: list[str] = []
        for job in await self._raw_owned_jobs():
            try:
                hook = echo_from_job(job)
            except DomainError:
                continue
            if hook.scope != normalized_scope:
                continue
            if hook_set and hook.hook_id not in hook_set:
                continue
            if group_set and hook.group_id not in group_set:
                continue
            if not hook_set and not group_set:
                continue
            if await self._delete_owned_job(hook.hook_id, strict=False):
                removed.append(hook.hook_id)
            else:
                failed.append(hook.hook_id)
        self._cancelled += len(removed)
        if failed:
            status = "error"
            error_code = "echo_delete_partial" if removed else "echo_delete_failed"
            outcome = "echo_hooks_partially_cancelled" if removed else "echo_hooks_cancel_failed"
        else:
            status = "ok"
            error_code = None
            outcome = "echo_hooks_cancelled" if removed else "echo_hooks_already_absent"
        return {
            "status": status,
            "error_code": error_code,
            "action": "cancel",
            "scope_ref": self._scope_ref(normalized_scope),
            "changed": bool(removed),
            "outcome": outcome,
            "effect_contract": ECHO_EFFECT_CONTRACT,
            "effect_applied": bool(removed),
            "effect_state": "applied" if removed else "not_applied",
            "reply_guaranteed": False,
            "cancelled_hook_ids": sorted(removed),
            "failed_hook_ids": sorted(failed),
            "recovery_action": "retry_cancel_or_inspect_native_cron" if failed else None,
            "recovery_action_executed": False,
            "automatic_retry_scheduled": False,
        }

    async def cancel_scope(self, scope: Any) -> list[str]:
        normalized_scope = normalize_scope(scope)
        manager = self._require_manager()
        removed: list[str] = []
        for job in await self._raw_owned_jobs():
            try:
                hook = echo_from_job(job)
            except DomainError:
                continue
            if hook.scope != normalized_scope:
                continue
            if await self._delete_owned_job(hook.hook_id, strict=False):
                removed.append(hook.hook_id)
        self._cancelled += len(removed)
        return removed

    async def list_scope(self, scope: Any) -> dict[str, Any]:
        normalized_scope = normalize_scope(scope)
        snapshot = await self.snapshot(scope=normalized_scope)
        return {
            "status": "ok",
            "error_code": None,
            "action": "list",
            "scope_ref": self._scope_ref(normalized_scope),
            "changed": False,
            "effect_contract": ECHO_EFFECT_CONTRACT,
            "effect_applied": False,
            "effect_state": "not_applied",
            "reply_guaranteed": False,
            "state": snapshot,
        }

    async def health(self) -> dict[str, Any]:
        try:
            snapshot = await self.snapshot()
            pending = len(snapshot["echo_hooks"])
            quarantined = len(snapshot["quarantined"])
            error = None
        except Exception as exc:
            pending = 0
            quarantined = len(self._quarantined)
            error = type(exc).__name__
        return {
            "echo_active": self._active,
            "echo_pending": pending,
            "echo_created_total": self._created,
            "echo_cancelled_total": self._cancelled,
            "echo_executed_total": self._executed,
            "echo_preflight_suppressed_total": self._preflight_suppressed,
            "echo_timer_tasks": len(self._tasks),
            "echo_quarantined_count": quarantined,
            "echo_last_error": error,
        }


def _safe_echo(job: Any) -> bool:
    try:
        echo_from_job(job)
        return True
    except DomainError:
        return False
