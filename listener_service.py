from __future__ import annotations

import asyncio
import hashlib
import inspect
import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .domain import DomainError, normalize_scope, normalize_target_id
from .storage import AstrBotKVStateStore, CommitIndeterminateError

LISTENER_STATE_KEY = "sender_activation_listener_state_v1"
LISTENER_BACKUP_KEY = "sender_activation_listener_state_v1_lkg"
LISTENER_SCHEMA_VERSION = 1

LISTENER_TAG = "_sender_activation_listener"
LISTENER_KIND = "normalized_attention_listener_activation"
LISTENER_EFFECT_CONTRACT = "finite_normalized_attention_listener"

MAX_LISTENER_GOAL_CHARS = 2000
MAX_KEYWORDS = 20
MAX_KEYWORD_CHARS = 80
MAX_FREQUENCY_COUNT = 10_000
MAX_SETTLE_SECONDS = 30.0
MAX_WATCHDOG_SECONDS = 24 * 60 * 60
MAX_LIFETIME_SECONDS = 7 * 24 * 60 * 60
DISPATCH_EPSILON_SECONDS = 0.05

FREQUENCY_PRESETS: dict[str, int] = {
    "each": 1,
    "every_3": 3,
    "every_10": 10,
}
RESPONSE_SPEED_PRESETS: dict[str, float] = {
    "immediate_0s": 0.0,
    "normal_1s": 1.0,
    "settle_3s": 3.0,
}
WATCHDOG_PRESETS: dict[str, float | None] = {
    "default_3m": 180.0,
    "ten_min": 600.0,
    "thirty_min": 1800.0,
    "off": None,
}
LIFETIME_PRESETS: dict[str, int] = {
    "10m": 10 * 60,
    "30m": 30 * 60,
    "2h": 2 * 60 * 60,
    "24h": 24 * 60 * 60,
}


@dataclass(frozen=True)
class ListenerLimits:
    max_per_scope: int = 30
    max_total: int = 500
    max_frequency_count: int = MAX_FREQUENCY_COUNT
    max_settle_seconds: float = MAX_SETTLE_SECONDS
    max_watchdog_seconds: float = MAX_WATCHDOG_SECONDS
    max_lifetime_seconds: int = MAX_LIFETIME_SECONDS


@dataclass(frozen=True)
class ListenerContract:
    listener_id: str
    scope: str
    owner_sender_id: str
    condition_kind: str
    condition_values: tuple[str, ...]
    frequency_count: int
    settle_delay_seconds: float
    watchdog_seconds: float | None
    goal: str
    created_at: float
    expires_at: float
    created_by: str

    @property
    def dispatch_key(self) -> tuple[str, str]:
        return (self.scope, self.owner_sender_id)

    def matches(self, sender_id: str, message: str) -> bool:
        if self.condition_kind == "sender":
            return sender_id in self.condition_values
        if self.condition_kind == "keyword":
            haystack = message.casefold()
            return any(value.casefold() in haystack for value in self.condition_values)
        if self.condition_kind == "any_message":
            return True
        if self.condition_kind == "time_only":
            return False
        return False

    def as_record(self) -> dict[str, Any]:
        return {
            "listener_id": self.listener_id,
            "scope": self.scope,
            "owner_sender_id": self.owner_sender_id,
            "condition_kind": self.condition_kind,
            "condition_values": list(self.condition_values),
            "frequency_count": self.frequency_count,
            "settle_delay_seconds": self.settle_delay_seconds,
            "watchdog_seconds": self.watchdog_seconds,
            "goal": self.goal,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "created_by": self.created_by,
        }

    def as_public(
        self,
        now: float,
        *,
        runtime: "_RuntimeState | None" = None,
        watchdog_due_at: float | None = None,
    ) -> dict[str, Any]:
        return {
            **self.as_record(),
            "remaining_seconds": max(0, math.ceil(self.expires_at - now)),
            "runtime": {
                "observed_since_reset": int(runtime.observed_since_reset) if runtime else 0,
                "pending": bool(runtime.pending) if runtime else False,
                "pending_due_at": runtime.due_at if runtime and runtime.pending else None,
                "pending_match_count": int(runtime.pending_match_count) if runtime else 0,
                "latest_sender_id": runtime.latest_sender_id if runtime else None,
                "watchdog_due_at": watchdog_due_at,
                "watchdog_remaining_seconds": (
                    max(0, math.ceil(watchdog_due_at - now))
                    if watchdog_due_at is not None
                    else None
                ),
            },
        }


@dataclass
class _RuntimeState:
    observed_since_reset: int = 0
    pending: bool = False
    due_at: float = 0.0
    first_signal_at: float = 0.0
    last_signal_at: float = 0.0
    pending_match_count: int = 0
    latest_sender_id: str | None = None
    pending_reasons: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class _ActivationItem:
    listener_id: str
    goal: str
    reasons: tuple[str, ...]
    condition_kind: str
    pending_match_count: int
    latest_sender_id: str | None


def _number(value: Any, field_name: str) -> float:
    if isinstance(value, bool):
        raise DomainError(f"invalid_{field_name}", f"{field_name} 必须是有限数值。")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DomainError(
            f"invalid_{field_name}",
            f"{field_name} 必须是有限数值。",
        ) from exc
    if not math.isfinite(number):
        raise DomainError(f"invalid_{field_name}", f"{field_name} 必须是有限数值。")
    return number


def _positive_int(value: Any, field_name: str, maximum: int) -> int:
    number = _number(value, field_name)
    if not number.is_integer() or number <= 0 or number > maximum:
        raise DomainError(
            f"invalid_{field_name}",
            f"{field_name} 必须是 1 到 {maximum} 的整数。",
        )
    return int(number)


def _bounded_nonnegative(value: Any, field_name: str, maximum: float) -> float:
    number = _number(value, field_name)
    if number < 0 or number > maximum:
        raise DomainError(
            f"invalid_{field_name}",
            f"{field_name} 必须在 0 到 {maximum:g} 之间。",
        )
    return number


def _actor(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 128:
        raise DomainError("invalid_actor", "监听创建者标识无效。")
    return text


def _goal(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise DomainError(
            "missing_listener_goal",
            "必须说明未来激活后需要主 Agent 重新判断的开放目标。",
        )
    if len(text) > MAX_LISTENER_GOAL_CHARS or "\x00" in text:
        raise DomainError(
            "invalid_listener_goal",
            f"goal 不能超过 {MAX_LISTENER_GOAL_CHARS} 字且不能包含空字符。",
        )
    return text


def _condition_values(kind: str, raw_values: Any) -> tuple[str, ...]:
    values = raw_values if isinstance(raw_values, (list, tuple, set)) else []
    if kind == "sender":
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = normalize_target_id(raw)
            if value not in seen:
                seen.add(value)
                normalized.append(value)
        if not normalized:
            raise DomainError(
                "empty_listener_condition",
                "sender 条件至少需要一个真实数字 QQ ID。",
            )
        if len(normalized) > 100:
            raise DomainError("too_many_listener_values", "一次最多监听 100 个 QQ ID。")
        return tuple(normalized)

    if kind == "keyword":
        normalized = []
        seen = set()
        for raw in values:
            value = str(raw or "").strip()
            if not value:
                continue
            if len(value) > MAX_KEYWORD_CHARS or "\x00" in value:
                raise DomainError(
                    "invalid_listener_keyword",
                    f"单个关键词不能超过 {MAX_KEYWORD_CHARS} 字且不能包含空字符。",
                )
            folded = value.casefold()
            if folded not in seen:
                seen.add(folded)
                normalized.append(value)
        if not normalized:
            raise DomainError("empty_listener_condition", "keyword 条件至少需要一个关键词。")
        if len(normalized) > MAX_KEYWORDS:
            raise DomainError(
                "too_many_listener_values",
                f"一次最多监听 {MAX_KEYWORDS} 个关键词。",
            )
        return tuple(normalized)

    if kind in {"any_message", "time_only"}:
        return ()

    raise DomainError(
        "invalid_listener_condition_kind",
        "condition_kind 必须是 sender、keyword、any_message 或 time_only。",
    )


def _preset_frequency(
    preset: Any,
    custom: Any,
    limits: ListenerLimits,
) -> int:
    name = str(preset or "each").strip().lower()
    if name == "custom":
        return _positive_int(custom, "frequency_count", limits.max_frequency_count)
    if name not in FREQUENCY_PRESETS:
        raise DomainError(
            "invalid_listener_frequency",
            "frequency 必须是 each、every_3、every_10 或 custom。",
        )
    return FREQUENCY_PRESETS[name]


def _preset_settle(
    preset: Any,
    custom: Any,
    limits: ListenerLimits,
) -> float:
    name = str(preset or "normal_1s").strip().lower()
    if name == "custom":
        return _bounded_nonnegative(custom, "settle_delay_seconds", limits.max_settle_seconds)
    if name not in RESPONSE_SPEED_PRESETS:
        raise DomainError(
            "invalid_listener_response_speed",
            "response_speed 必须是 immediate_0s、normal_1s、settle_3s 或 custom。",
        )
    return RESPONSE_SPEED_PRESETS[name]


def _preset_watchdog(
    preset: Any,
    custom: Any,
    limits: ListenerLimits,
) -> float | None:
    name = str(preset or "default_3m").strip().lower()
    if name == "custom":
        value = _number(custom, "watchdog_seconds")
        if value <= 0 or value > limits.max_watchdog_seconds:
            raise DomainError(
                "invalid_watchdog_seconds",
                f"watchdog_seconds 必须大于 0 且不超过 {limits.max_watchdog_seconds:g}。",
            )
        return value
    if name not in WATCHDOG_PRESETS:
        raise DomainError(
            "invalid_listener_watchdog",
            "watchdog 必须是 default_3m、ten_min、thirty_min、off 或 custom。",
        )
    return WATCHDOG_PRESETS[name]


def _preset_lifetime(
    preset: Any,
    custom: Any,
    limits: ListenerLimits,
) -> int:
    name = str(preset or "2h").strip().lower()
    if name == "custom":
        return _positive_int(custom, "lifetime_seconds", limits.max_lifetime_seconds)
    if name not in LIFETIME_PRESETS:
        raise DomainError(
            "invalid_listener_lifetime",
            "lifetime 必须是 10m、30m、2h、24h 或 custom。",
        )
    return min(LIFETIME_PRESETS[name], limits.max_lifetime_seconds)


def _load_contract(raw: Any, *, now: float, limits: ListenerLimits) -> ListenerContract | None:
    if not isinstance(raw, Mapping):
        raise DomainError("invalid_listener_record", "监听记录必须是对象。")
    listener_id = str(raw.get("listener_id") or "").strip()
    if not listener_id or len(listener_id) > 64:
        raise DomainError("invalid_listener_record", "监听 ID 无效。")
    scope = normalize_scope(raw.get("scope"))
    owner_sender_id = normalize_target_id(raw.get("owner_sender_id"))
    kind = str(raw.get("condition_kind") or "").strip().lower()
    values = _condition_values(kind, raw.get("condition_values"))
    frequency_count = _positive_int(
        raw.get("frequency_count"),
        "frequency_count",
        limits.max_frequency_count,
    )
    settle = _bounded_nonnegative(
        raw.get("settle_delay_seconds"),
        "settle_delay_seconds",
        limits.max_settle_seconds,
    )
    watchdog_raw = raw.get("watchdog_seconds")
    watchdog = None
    if watchdog_raw is not None:
        watchdog = _number(watchdog_raw, "watchdog_seconds")
        if watchdog <= 0 or watchdog > limits.max_watchdog_seconds:
            raise DomainError("invalid_listener_record", "监听兜底时间无效。")
    goal = _goal(raw.get("goal"))
    created_at = _number(raw.get("created_at"), "created_at")
    expires_at = _number(raw.get("expires_at"), "expires_at")
    if expires_at <= created_at or expires_at - created_at > limits.max_lifetime_seconds + 1:
        raise DomainError("invalid_listener_record", "监听有效期无效。")
    if expires_at <= now:
        return None
    created_by = _actor(raw.get("created_by"))
    if kind == "time_only" and watchdog is None:
        raise DomainError(
            "invalid_listener_record",
            "time_only 监听必须配置定时激活。",
        )
    return ListenerContract(
        listener_id=listener_id,
        scope=scope,
        owner_sender_id=owner_sender_id,
        condition_kind=kind,
        condition_values=values,
        frequency_count=frequency_count,
        settle_delay_seconds=settle,
        watchdog_seconds=watchdog,
        goal=goal,
        created_at=created_at,
        expires_at=expires_at,
        created_by=created_by,
    )


def is_owned_listener_payload(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    tag = payload.get(LISTENER_TAG)
    return isinstance(tag, Mapping) and tag.get("kind") == LISTENER_KIND


class ListenerService:
    def __init__(
        self,
        limits: ListenerLimits,
        store: AstrBotKVStateStore,
        cron_manager: Any,
        *,
        wall_clock: Callable[[], float] = time.time,
        preflight: Callable[[str], bool | Awaitable[bool]] | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.limits = limits
        self._store = store
        self._manager = cron_manager
        self._wall_clock = wall_clock
        self._preflight = preflight
        self._sleeper = sleeper

        self._state_lock = asyncio.Lock()
        self._runtime_lock = asyncio.Lock()
        self._listeners: dict[str, ListenerContract] = {}
        self._by_scope: dict[str, tuple[ListenerContract, ...]] = {}
        self._runtime: dict[str, _RuntimeState] = {}
        self._watchdog_due: dict[str, float] = {}
        self._dispatch_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._dispatch_generation: dict[tuple[str, str], int] = {}
        self._running_keys: set[tuple[str, str]] = set()
        self._active_job_ids: set[str] = set()
        self._created_serial = 0

        self.active = False
        self.storage_ready = False
        self.storage_write_healthy = False
        self.loaded_from = "none"
        self.last_error_code: str | None = None
        self.last_write_error_code: str | None = None
        self.quarantined: list[dict[str, Any]] = []

        self._signals_seen = 0
        self._candidates_created = 0
        self._candidates_debounced = 0
        self._activations = 0
        self._watchdog_activations = 0
        self._preflight_suppressed = 0

    def _now(self) -> float:
        return float(self._wall_clock())

    @staticmethod
    def scope_ref(scope: str) -> str:
        return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:12]

    def _require_manager(self) -> Any:
        manager = self._manager
        required = ("add_active_job", "delete_job", "list_jobs", "run_job_now")
        if manager is None or any(not callable(getattr(manager, name, None)) for name in required):
            raise DomainError(
                "listener_scheduler_unavailable",
                "当前 AstrBot 未提供兼容的一次性主动 Agent 调度接口。",
            )
        return manager

    def _rebuild_index(self) -> None:
        by_scope: dict[str, list[ListenerContract]] = {}
        for contract in self._listeners.values():
            by_scope.setdefault(contract.scope, []).append(contract)
        self._by_scope = {
            scope: tuple(sorted(rows, key=lambda row: row.listener_id))
            for scope, rows in by_scope.items()
        }

    async def _raw_owned_jobs(self) -> list[Any]:
        jobs = await self._require_manager().list_jobs("active_agent")
        return [
            job
            for job in jobs
            if is_owned_listener_payload(getattr(job, "payload", None))
        ]

    async def _preflight_allowed(self, scope: str) -> bool:
        if not self.active or not self.storage_ready:
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

    async def _load_state(self) -> None:
        try:
            stored = await self._store.load()
        except Exception as exc:
            self.last_error_code = f"listener_storage_load_failed:{type(exc).__name__}"
            return

        now = self._now()
        candidates: list[tuple[str, Mapping[str, Any] | None]] = [
            ("primary", stored.primary),
            ("backup", stored.backup),
        ]
        for source, document in candidates:
            if document is None:
                continue
            try:
                if not isinstance(document, Mapping):
                    raise DomainError("invalid_listener_state_document", "监听状态文档必须是对象。")
                if document.get("schema_version") != LISTENER_SCHEMA_VERSION:
                    raise DomainError(
                        "unsupported_listener_state_schema",
                        "监听状态 Schema 版本不受支持。",
                    )
                rows = document.get("listeners")
                if not isinstance(rows, list):
                    raise DomainError("invalid_listener_state_document", "listeners 必须是数组。")
                loaded: dict[str, ListenerContract] = {}
                quarantined: list[dict[str, Any]] = []
                for index, raw in enumerate(rows):
                    try:
                        contract = _load_contract(raw, now=now, limits=self.limits)
                    except DomainError as exc:
                        quarantined.append({"index": index, "error_code": exc.code})
                        continue
                    if contract is None:
                        continue
                    if contract.listener_id in loaded:
                        quarantined.append(
                            {"index": index, "error_code": "duplicate_listener_id"}
                        )
                        continue
                    loaded[contract.listener_id] = contract
                self._listeners = loaded
                self._rebuild_index()
                self.quarantined = quarantined
                self.storage_ready = True
                self.storage_write_healthy = True
                self.loaded_from = source
                self.last_error_code = None
                return
            except DomainError as exc:
                self.last_error_code = exc.code
                continue

        if stored.primary is None and stored.backup is None:
            self._listeners = {}
            self._rebuild_index()
            self.storage_ready = True
            self.storage_write_healthy = True
            self.loaded_from = "empty"
            self.last_error_code = None

    def _document(self, listeners: Mapping[str, ListenerContract] | None = None) -> dict[str, Any]:
        source = listeners if listeners is not None else self._listeners
        now = self._now()
        active = [
            contract
            for contract in source.values()
            if contract.expires_at > now
        ]
        active.sort(key=lambda row: row.listener_id)
        return {
            "schema_version": LISTENER_SCHEMA_VERSION,
            "listeners": [contract.as_record() for contract in active],
        }

    async def _commit_listeners(self, candidate: dict[str, ListenerContract]) -> None:
        if not self.storage_ready:
            raise DomainError("listener_storage_unavailable", "监听状态存储当前不可用。")
        previous_document = self._document()
        candidate_document = self._document(candidate)
        try:
            await self._store.commit(previous_document, candidate_document)
        except CommitIndeterminateError as exc:
            self.storage_ready = False
            self.storage_write_healthy = False
            self.last_write_error_code = "listener_commit_indeterminate"
            self._listeners = {}
            self._rebuild_index()
            await self._cancel_all_runtime()
            raise DomainError(
                "listener_commit_indeterminate",
                "监听状态写入结果无法确认；运行时已安全停用监听以避免陈旧激活。",
            ) from exc
        except Exception as exc:
            self.storage_write_healthy = False
            self.last_write_error_code = "listener_storage_write_failed"
            raise DomainError(
                "listener_storage_write_failed",
                f"监听状态写入失败: {type(exc).__name__}",
            ) from exc
        self._listeners = candidate
        self._rebuild_index()
        self.storage_write_healthy = True
        self.last_write_error_code = None

    async def initialize(self) -> None:
        self.active = False
        await self._cancel_all_runtime()
        try:
            for job in await self._raw_owned_jobs():
                try:
                    await self._require_manager().delete_job(
                        str(getattr(job, "job_id", "") or "")
                    )
                except Exception:
                    pass
        except DomainError:
            raise
        await self._load_state()
        if not self.storage_ready:
            return
        self._require_manager()
        self.active = True
        now = self._now()
        async with self._runtime_lock:
            for contract in self._listeners.values():
                self._runtime.setdefault(contract.listener_id, _RuntimeState())
                if contract.watchdog_seconds is not None:
                    self._watchdog_due[contract.listener_id] = (
                        now + contract.watchdog_seconds
                    )
            for key in {contract.dispatch_key for contract in self._listeners.values()}:
                self._reschedule_key_locked(key, now=now)

    async def terminate(self) -> list[str]:
        self.active = False
        await self._cancel_all_runtime()
        failures: list[str] = []
        try:
            jobs = await self._raw_owned_jobs()
        except Exception:
            return ["inventory"]
        for job in jobs:
            job_id = str(getattr(job, "job_id", "") or "")
            try:
                await self._require_manager().delete_job(job_id)
            except Exception:
                failures.append(job_id)
        return failures

    async def _cancel_all_runtime(self) -> None:
        tasks = list(self._dispatch_tasks.values())
        self._dispatch_tasks.clear()
        self._dispatch_generation.clear()
        self._running_keys.clear()
        self._runtime.clear()
        self._watchdog_due.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _active_contracts_for_key(
        self,
        key: tuple[str, str],
        *,
        now: float,
    ) -> list[ListenerContract]:
        scope, owner = key
        return [
            contract
            for contract in self._by_scope.get(scope, ())
            if contract.owner_sender_id == owner and contract.expires_at > now
        ]

    def _next_due_locked(
        self,
        key: tuple[str, str],
        *,
        now: float,
    ) -> float | None:
        due_values: list[float] = []
        for contract in self._active_contracts_for_key(key, now=now):
            runtime = self._runtime.get(contract.listener_id)
            if runtime is not None and runtime.pending:
                due_values.append(runtime.due_at)
            watchdog_due = self._watchdog_due.get(contract.listener_id)
            if watchdog_due is not None:
                due_values.append(watchdog_due)
        return min(due_values) if due_values else None

    def _reschedule_key_locked(
        self,
        key: tuple[str, str],
        *,
        now: float | None = None,
    ) -> None:
        if not self.active or key in self._running_keys:
            return
        current = asyncio.current_task()
        previous = self._dispatch_tasks.pop(key, None)
        if previous is not None and previous is not current and not previous.done():
            previous.cancel()
        instant = self._now() if now is None else now
        due = self._next_due_locked(key, now=instant)
        generation = self._dispatch_generation.get(key, 0) + 1
        self._dispatch_generation[key] = generation
        if due is None:
            return
        task = asyncio.create_task(
            self._wait_dispatch(key, generation, due),
            name=f"listener-dispatch-{self.scope_ref(key[0])}-{generation}",
        )
        self._dispatch_tasks[key] = task

    async def _wait_dispatch(
        self,
        key: tuple[str, str],
        generation: int,
        due_at: float,
    ) -> None:
        try:
            await self._sleeper(max(0.0, due_at - self._now()))
            await self._dispatch_key(key, generation)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.quarantined.append(
                {
                    "scope_ref": self.scope_ref(key[0]),
                    "error_code": f"listener_dispatch_{type(exc).__name__}",
                }
            )
            async with self._runtime_lock:
                if self._dispatch_generation.get(key) == generation:
                    self._reschedule_key_locked(key)

    async def _dispatch_key(
        self,
        key: tuple[str, str],
        generation: int,
    ) -> None:
        scope, owner_sender_id = key
        async with self._runtime_lock:
            if not self.active or self._dispatch_generation.get(key) != generation:
                return
            self._dispatch_tasks.pop(key, None)
            now = self._now()
            items: list[_ActivationItem] = []
            activated_contracts: list[ListenerContract] = []
            for contract in self._active_contracts_for_key(key, now=now):
                runtime = self._runtime.setdefault(contract.listener_id, _RuntimeState())

                # A watchdog is also only an activation signal. It must pass through
                # this listener's own settle/debounce window instead of waking the
                # Agent directly.
                watchdog_due = self._watchdog_due.get(contract.listener_id)
                if (
                    watchdog_due is not None
                    and watchdog_due <= now + DISPATCH_EPSILON_SECONDS
                ):
                    self._watchdog_due.pop(contract.listener_id, None)
                    self._signals_seen += 1
                    if not runtime.pending:
                        runtime.pending = True
                        runtime.first_signal_at = now
                        runtime.pending_match_count = 0
                    runtime.last_signal_at = now
                    runtime.due_at = now + contract.settle_delay_seconds
                    runtime.pending_reasons.add("watchdog")

                if not (
                    runtime.pending
                    and runtime.due_at <= now + DISPATCH_EPSILON_SECONDS
                ):
                    continue

                reasons: list[str] = []
                if "condition" in runtime.pending_reasons:
                    reasons.append("condition_ready")
                if "watchdog" in runtime.pending_reasons:
                    reasons.append("watchdog_due")
                if not reasons:
                    continue

                items.append(
                    _ActivationItem(
                        listener_id=contract.listener_id,
                        goal=contract.goal,
                        reasons=tuple(reasons),
                        condition_kind=contract.condition_kind,
                        pending_match_count=runtime.pending_match_count,
                        latest_sender_id=runtime.latest_sender_id,
                    )
                )
                activated_contracts.append(contract)
                runtime.observed_since_reset = 0
                runtime.pending = False
                runtime.due_at = 0.0
                runtime.first_signal_at = 0.0
                runtime.last_signal_at = 0.0
                runtime.pending_match_count = 0
                runtime.latest_sender_id = None
                runtime.pending_reasons.clear()

            if not items:
                self._reschedule_key_locked(key, now=now)
                return
            self._running_keys.add(key)

        try:
            await self._execute_activation(
                scope=scope,
                owner_sender_id=owner_sender_id,
                items=items,
            )
        finally:
            completion = self._now()
            async with self._runtime_lock:
                self._running_keys.discard(key)
                current_ids = set(self._listeners)
                for contract in activated_contracts:
                    if (
                        contract.listener_id in current_ids
                        and contract.expires_at > completion
                        and contract.watchdog_seconds is not None
                    ):
                        self._watchdog_due[contract.listener_id] = (
                            completion + contract.watchdog_seconds
                        )
                self._reschedule_key_locked(key, now=completion)

    async def _execute_activation(
        self,
        *,
        scope: str,
        owner_sender_id: str,
        items: list[_ActivationItem],
    ) -> None:
        if not await self._preflight_allowed(scope):
            self._preflight_suppressed += 1
            return

        now = self._now()
        rows: list[str] = []
        watchdog_hit = False
        for item in items:
            reasons: list[str] = []
            if "condition_ready" in item.reasons:
                reasons.append(
                    f"监听条件已归一化就绪，累计命中 {item.pending_match_count or 1} 次"
                )
            if "watchdog_due" in item.reasons:
                reasons.append("无条件命中时的兜底检查时间已到")
                watchdog_hit = True
            latest = (
                f"，最后关联发送者 {item.latest_sender_id}"
                if item.latest_sender_id
                else ""
            )
            rows.append(
                f"- {item.listener_id} [{item.condition_kind}]："
                f"{'；'.join(reasons)}{latest}\n  开放目标：{item.goal}"
            )

        note = (
            "[统一监听激活]\n"
            "这是管理员的真理捍卫器根据既有监听契约产生的主动 Agent 回合，"
            "不是新的用户命令。多个 READY 信号已经在时间尺度上归一化为本次"
            "单一激活。请读取当前 AstrBot 会话上下文，重新判断这些开放目标"
            "现在是否需要行动。没有独立公开价值时，调用 yield_current_turn "
            "保持无可见回复；目标已完成或监听已无意义时，可调用 "
            "manage_active_listener(cancel) 净化对应 listener_id。即使忘记净化，"
            "后续兜底激活也必须重新判断，而不是机械发言。\n"
            + "\n".join(rows)
        )
        payload = {
            "session": scope,
            "sender_id": owner_sender_id,
            "origin": "astrbot_plugin_sender_activation",
            "note": note,
            LISTENER_TAG: {
                "kind": LISTENER_KIND,
                "schema_version": LISTENER_SCHEMA_VERSION,
                "listener_ids": [item.listener_id for item in items],
                "reasons": [list(item.reasons) for item in items],
                "created_at": now,
                "normalized": True,
            },
        }

        manager = self._require_manager()
        job = None
        try:
            job = await manager.add_active_job(
                name=f"[统一监听激活] {self.scope_ref(scope)}",
                cron_expression=None,
                payload=payload,
                description="监听条件/兜底信号归一化后的单次 AstrBot 主 Agent 激活。",
                timezone="UTC",
                enabled=False,
                persistent=False,
                run_once=True,
                run_at=datetime.fromtimestamp(now, tz=timezone.utc),
            )
            job_id = str(getattr(job, "job_id", "") or "")
            if not job_id:
                raise RuntimeError("missing listener active job id")
            self._active_job_ids.add(job_id)
            await manager.run_job_now(job_id)
            self._activations += 1
            if watchdog_hit:
                self._watchdog_activations += 1
        except Exception as exc:
            self.quarantined.append(
                {
                    "scope_ref": self.scope_ref(scope),
                    "error_code": f"listener_execute_{type(exc).__name__}",
                }
            )
        finally:
            if job is not None:
                job_id = str(getattr(job, "job_id", "") or "")
                self._active_job_ids.discard(job_id)
                try:
                    await manager.delete_job(job_id)
                except Exception:
                    pass

    def has_match(self, scope: Any, sender_id: Any, message: Any) -> bool:
        if not self.active or not self.storage_ready:
            return False
        try:
            normalized_scope = normalize_scope(scope)
            sender = str(sender_id or "").strip()
            if not sender:
                return False
            text = str(message or "")
            now = self._now()
            return any(
                contract.expires_at > now and contract.matches(sender, text)
                for contract in self._by_scope.get(normalized_scope, ())
            )
        except Exception:
            return False

    async def observe_event(
        self,
        *,
        scope: Any,
        sender_id: Any,
        message: Any,
    ) -> dict[str, Any]:
        if not self.active or not self.storage_ready:
            return {"matched": [], "candidates": []}
        normalized_scope = normalize_scope(scope)
        sender = str(sender_id or "").strip()
        if not sender:
            raise DomainError("missing_sender", "监听事件缺少发送者 ID。")
        text = str(message or "")
        now = self._now()
        matched: list[str] = []
        candidates: list[str] = []
        affected_keys: set[tuple[str, str]] = set()

        async with self._runtime_lock:
            for contract in self._by_scope.get(normalized_scope, ()):
                if contract.expires_at <= now or not contract.matches(sender, text):
                    continue
                matched.append(contract.listener_id)
                self._signals_seen += 1
                runtime = self._runtime.setdefault(
                    contract.listener_id,
                    _RuntimeState(),
                )
                if runtime.pending:
                    runtime.due_at = now + contract.settle_delay_seconds
                    runtime.last_signal_at = now
                    runtime.pending_match_count += 1
                    runtime.latest_sender_id = sender
                    runtime.pending_reasons.add("condition")
                    self._candidates_debounced += 1
                    candidates.append(contract.listener_id)
                    affected_keys.add(contract.dispatch_key)
                    continue

                runtime.observed_since_reset += 1
                if runtime.observed_since_reset < contract.frequency_count:
                    continue
                runtime.pending = True
                runtime.due_at = now + contract.settle_delay_seconds
                runtime.first_signal_at = now
                runtime.last_signal_at = now
                runtime.pending_match_count = runtime.observed_since_reset
                runtime.latest_sender_id = sender
                runtime.pending_reasons.add("condition")
                self._candidates_created += 1
                candidates.append(contract.listener_id)
                affected_keys.add(contract.dispatch_key)

            for key in affected_keys:
                self._reschedule_key_locked(key, now=now)

        return {"matched": matched, "candidates": candidates}

    async def manage(
        self,
        *,
        scope: Any,
        action: Any,
        owner_sender_id: Any,
        actor_ref: Any,
        condition_kind: Any = "",
        condition_values: Any = None,
        frequency: Any = "each",
        frequency_count: Any = 0,
        response_speed: Any = "normal_1s",
        settle_delay_seconds: Any = 0,
        watchdog: Any = "default_3m",
        watchdog_seconds: Any = 0,
        lifetime: Any = "2h",
        lifetime_seconds: Any = 0,
        goal: Any = "",
        listener_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_scope = normalize_scope(scope)
        normalized_action = str(action or "").strip().lower()

        if normalized_action == "list":
            return {
                "status": "ok",
                "error_code": None,
                "action": "list",
                "scope_ref": self.scope_ref(normalized_scope),
                "changed": False,
                "effect_contract": LISTENER_EFFECT_CONTRACT,
                "effect_applied": False,
                "effect_state": "not_applied",
                "reply_guaranteed": False,
                "state": await self.snapshot(scope=normalized_scope),
            }

        if normalized_action == "cancel":
            ids = {
                str(value or "").strip()
                for value in (listener_ids or [])
                if str(value or "").strip()
            }
            if not ids:
                raise DomainError(
                    "missing_listener_ids",
                    "cancel 必须提供要净化的 listener_ids。",
                )
            async with self._state_lock:
                selected = [
                    contract
                    for listener_id, contract in self._listeners.items()
                    if listener_id in ids and contract.scope == normalized_scope
                ]
                if len(selected) != len(ids):
                    raise DomainError(
                        "listener_absent",
                        "至少一个 listener_id 不存在或不属于当前群。",
                    )
                candidate = dict(self._listeners)
                for contract in selected:
                    candidate.pop(contract.listener_id, None)
                await self._commit_listeners(candidate)

            async with self._runtime_lock:
                affected_keys = {contract.dispatch_key for contract in selected}
                for contract in selected:
                    self._runtime.pop(contract.listener_id, None)
                    self._watchdog_due.pop(contract.listener_id, None)
                for key in affected_keys:
                    self._reschedule_key_locked(key)

            return {
                "status": "ok",
                "error_code": None,
                "action": "cancel",
                "scope_ref": self.scope_ref(normalized_scope),
                "listener_ids": sorted(ids),
                "changed": bool(selected),
                "outcome": "listeners_cancelled",
                "effect_contract": LISTENER_EFFECT_CONTRACT,
                "effect_applied": bool(selected),
                "effect_state": "applied" if selected else "not_applied",
                "reply_guaranteed": False,
            }

        if normalized_action != "start":
            raise DomainError(
                "invalid_action",
                "action 必须是 start、cancel 或 list。",
            )
        owner = normalize_target_id(owner_sender_id)
        if not self.active:
            raise DomainError("listener_service_inactive", "统一监听服务当前未激活。")
        if not self.storage_ready:
            raise DomainError("listener_storage_unavailable", "监听状态存储当前不可用。")

        kind = str(condition_kind or "").strip().lower()
        values = _condition_values(kind, condition_values)
        frequency_value = _preset_frequency(frequency, frequency_count, self.limits)
        settle = _preset_settle(
            response_speed,
            settle_delay_seconds,
            self.limits,
        )
        watchdog_value = _preset_watchdog(
            watchdog,
            watchdog_seconds,
            self.limits,
        )
        lifetime_value = _preset_lifetime(
            lifetime,
            lifetime_seconds,
            self.limits,
        )
        normalized_goal = _goal(goal)
        creator = _actor(actor_ref)
        if kind == "time_only" and watchdog_value is None:
            raise DomainError(
                "time_listener_requires_watchdog",
                "time_only 监听必须选择一个定时激活选项。",
            )

        now = self._now()
        self._created_serial += 1
        seed = (
            f"{normalized_scope}|{owner}|{creator}|{kind}|{now:.9f}|"
            f"{self._created_serial}|{normalized_goal}"
        )
        listener_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        contract = ListenerContract(
            listener_id=listener_id,
            scope=normalized_scope,
            owner_sender_id=owner,
            condition_kind=kind,
            condition_values=values,
            frequency_count=frequency_value,
            settle_delay_seconds=settle,
            watchdog_seconds=watchdog_value,
            goal=normalized_goal,
            created_at=now,
            expires_at=now + lifetime_value,
            created_by=creator,
        )

        async with self._state_lock:
            active = [
                row for row in self._listeners.values() if row.expires_at > now
            ]
            scope_count = sum(1 for row in active if row.scope == normalized_scope)
            if scope_count >= self.limits.max_per_scope:
                raise DomainError(
                    "listener_scope_capacity_exceeded",
                    "当前群监听数量达到配置上限。",
                )
            if len(active) >= self.limits.max_total:
                raise DomainError(
                    "listener_total_capacity_exceeded",
                    "统一监听总数达到配置上限。",
                )
            candidate = {
                row.listener_id: row
                for row in active
            }
            candidate[listener_id] = contract
            await self._commit_listeners(candidate)

        async with self._runtime_lock:
            self._runtime[listener_id] = _RuntimeState()
            if watchdog_value is not None:
                self._watchdog_due[listener_id] = now + watchdog_value
            self._reschedule_key_locked(contract.dispatch_key, now=now)

        return {
            "status": "ok",
            "error_code": None,
            "action": "start",
            "scope_ref": self.scope_ref(normalized_scope),
            "listener_id": listener_id,
            "changed": True,
            "outcome": "listener_started",
            "effect_contract": LISTENER_EFFECT_CONTRACT,
            "effect_applied": True,
            "effect_state": "applied",
            "reply_guaranteed": False,
            "listener": contract.as_public(
                now,
                runtime=self._runtime.get(listener_id),
                watchdog_due_at=self._watchdog_due.get(listener_id),
            ),
        }

    def owns_all(
        self,
        *,
        scope: Any,
        owner_sender_id: Any,
        listener_ids: list[str] | None,
    ) -> bool:
        try:
            normalized_scope = normalize_scope(scope)
            owner = normalize_target_id(owner_sender_id)
        except DomainError:
            return False
        ids = {
            str(value or "").strip()
            for value in (listener_ids or [])
            if str(value or "").strip()
        }
        if not ids:
            return False
        selected = [
            self._listeners.get(listener_id)
            for listener_id in ids
        ]
        return all(
            contract is not None
            and contract.scope == normalized_scope
            and contract.owner_sender_id == owner
            for contract in selected
        )

    async def snapshot(
        self,
        *,
        scope: Any | None = None,
    ) -> dict[str, Any]:
        now = self._now()
        normalized_scope = normalize_scope(scope) if scope is not None else None
        async with self._runtime_lock:
            rows = [
                contract.as_public(
                    now,
                    runtime=self._runtime.get(contract.listener_id),
                    watchdog_due_at=self._watchdog_due.get(contract.listener_id),
                )
                for contract in self._listeners.values()
                if contract.expires_at > now
                and (normalized_scope is None or contract.scope == normalized_scope)
            ]
        rows.sort(key=lambda row: (row["scope"], row["listener_id"]))
        return {
            "listeners": rows,
            "known_scopes": sorted({self.scope_ref(row["scope"]) for row in rows}),
            "raw_scopes": sorted({row["scope"] for row in rows}),
            "quarantined": list(self.quarantined),
        }

    async def health(self) -> dict[str, Any]:
        try:
            snapshot = await self.snapshot()
            count = len(snapshot["listeners"])
            quarantined = len(snapshot["quarantined"])
        except Exception:
            count = 0
            quarantined = len(self.quarantined)
        return {
            "listener_active": self.active,
            "listener_storage_ready": self.storage_ready,
            "listener_storage_write_healthy": self.storage_write_healthy,
            "listener_loaded_from": self.loaded_from,
            "listener_last_error_code": self.last_error_code,
            "listener_last_write_error_code": self.last_write_error_code,
            "listener_count": count,
            "listener_dispatch_tasks": len(self._dispatch_tasks),
            "listener_running_keys": len(self._running_keys),
            "listener_signals_seen_total": self._signals_seen,
            "listener_candidates_created_total": self._candidates_created,
            "listener_candidates_debounced_total": self._candidates_debounced,
            "listener_activations_total": self._activations,
            "listener_watchdog_activations_total": self._watchdog_activations,
            "listener_preflight_suppressed_total": self._preflight_suppressed,
            "listener_quarantined_count": quarantined,
        }
