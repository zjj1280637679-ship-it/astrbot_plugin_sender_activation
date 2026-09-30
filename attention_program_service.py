from __future__ import annotations

import asyncio
import hashlib
import inspect
import math
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .domain import DomainError, normalize_scope, normalize_target_id
from .listener_service import (
    LISTENER_BACKUP_KEY,
    LISTENER_STATE_KEY,
    LISTENER_SCHEMA_VERSION,
)
from .storage import AstrBotKVStateStore, CommitIndeterminateError

PROGRAM_STATE_KEY = "sender_activation_attention_program_state_v2"
PROGRAM_BACKUP_KEY = "sender_activation_attention_program_state_v2_lkg"
PROGRAM_SCHEMA_VERSION = 2

PROGRAM_TAG = "_sender_activation_attention_program"
PROGRAM_KIND = "attention_program_reconcile_activation"
PROGRAM_EFFECT_CONTRACT = "finite_attention_program_reconcile"

MAX_GOAL_CHARS = 2000
MAX_KEYWORDS = 20
MAX_KEYWORD_CHARS = 80
MAX_QUANTIFIER_COUNT = 10_000
MAX_SETTLE_SECONDS = 30.0
MAX_RECHECK_SECONDS = 24 * 60 * 60
MAX_LEASE_SECONDS = 7 * 24 * 60 * 60
MAX_WATCHES_PER_PROGRAM = 8
MAX_DEDUPE_KEYS = 4096
DISPATCH_EPSILON_SECONDS = 0.001

QUANTIFIER_PRESETS: dict[str, int] = {
    "each": 1,
    "every_3": 3,
    "every_10": 10,
}
SETTLE_PRESETS: dict[str, float] = {
    "immediate_0s": 0.0,
    "normal_1s": 1.0,
    "settle_3s": 3.0,
}
RECHECK_PRESETS: dict[str, float | None] = {
    "default_3m": 180.0,
    "ten_min": 600.0,
    "thirty_min": 1800.0,
    "off": None,
}
LEASE_PRESETS: dict[str, int] = {
    "10m": 10 * 60,
    "30m": 30 * 60,
    "2h": 2 * 60 * 60,
    "24h": 24 * 60 * 60,
}


@dataclass(frozen=True)
class ProgramLimits:
    max_per_scope: int = 30
    max_total: int = 500
    max_watches_per_program: int = MAX_WATCHES_PER_PROGRAM
    max_quantifier_count: int = MAX_QUANTIFIER_COUNT
    max_settle_seconds: float = MAX_SETTLE_SECONDS
    max_recheck_seconds: float = MAX_RECHECK_SECONDS
    max_lease_seconds: int = MAX_LEASE_SECONDS
    max_dedupe_keys: int = MAX_DEDUPE_KEYS


@dataclass(frozen=True)
class EventEnvelope:
    """CloudEvents-inspired transient event envelope.

    source + id is used for in-process duplicate suppression. data is transient
    matching material and is never written into Program storage.
    """

    id: str
    source: str
    type: str
    subject: str | None
    occurred_at: float | None
    observed_at: float
    payload_ref: str | None
    data: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def dedupe_key(self) -> tuple[str, str]:
        return (self.source, self.id)

    def ref(self) -> str:
        return self.payload_ref or f"{self.source}#{self.id}"


@dataclass(frozen=True)
class WatchContract:
    watch_id: str
    match_kind: str
    match_values: tuple[str, ...]
    quantifier_count: int
    settle_seconds: float

    def matches(self, envelope: EventEnvelope) -> bool:
        if envelope.type != "com.astrbot.qq.group.message":
            return False
        sender_id = str(envelope.subject or "").strip()
        message = str(envelope.data.get("message") or "")
        if self.match_kind == "sender":
            return sender_id in self.match_values
        if self.match_kind == "keyword":
            haystack = message.casefold()
            return any(value.casefold() in haystack for value in self.match_values)
        if self.match_kind == "any_message":
            return True
        return False

    def as_record(self) -> dict[str, Any]:
        return {
            "watch_id": self.watch_id,
            "match_kind": self.match_kind,
            "match_values": list(self.match_values),
            "quantifier_count": self.quantifier_count,
            "settle_seconds": self.settle_seconds,
        }


@dataclass(frozen=True)
class AttentionProgram:
    program_id: str
    scope: str
    controller_sender_id: str
    goal: str
    watches: tuple[WatchContract, ...]
    recheck_seconds: float | None
    created_at: float
    expires_at: float
    created_by: str

    @property
    def attention_key(self) -> tuple[str, str]:
        return (self.scope, self.controller_sender_id)

    def as_record(self) -> dict[str, Any]:
        return {
            "program_id": self.program_id,
            "scope": self.scope,
            "controller_sender_id": self.controller_sender_id,
            "goal": self.goal,
            "watches": [watch.as_record() for watch in self.watches],
            "recheck_seconds": self.recheck_seconds,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "created_by": self.created_by,
        }


@dataclass
class _WatchRuntime:
    observed_since_reset: int = 0
    pending: bool = False
    ready_at: float = 0.0
    first_signal_at: float = 0.0
    last_signal_at: float = 0.0
    pending_match_count: int = 0
    latest_event_ref: str | None = None


@dataclass
class _ProgramRuntime:
    dirty_generation: int = 0
    reconciled_generation: int = 0
    pending_reasons: set[str] = field(default_factory=set)
    event_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _ReconcileItem:
    program_id: str
    goal: str
    generation: int
    reasons: tuple[str, ...]
    event_refs: tuple[str, ...]


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
        raise DomainError("invalid_actor", "注意力程序创建者标识无效。")
    return text


def _goal(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise DomainError(
            "missing_program_goal",
            "必须说明每次重新醒来后要基于当前世界重新判断的开放目标。",
        )
    if len(text) > MAX_GOAL_CHARS or "\x00" in text:
        raise DomainError(
            "invalid_program_goal",
            f"goal 不能超过 {MAX_GOAL_CHARS} 字且不能包含空字符。",
        )
    return text


def _match_values(kind: str, raw_values: Any) -> tuple[str, ...]:
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
            raise DomainError("empty_watch_match", "sender Watch 至少需要一个真实数字 QQ ID。")
        if len(normalized) > 100:
            raise DomainError("too_many_watch_values", "单个 Watch 最多监听 100 个 QQ ID。")
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
                    "invalid_watch_keyword",
                    f"单个关键词不能超过 {MAX_KEYWORD_CHARS} 字且不能包含空字符。",
                )
            folded = value.casefold()
            if folded not in seen:
                seen.add(folded)
                normalized.append(value)
        if not normalized:
            raise DomainError("empty_watch_match", "keyword Watch 至少需要一个关键词。")
        if len(normalized) > MAX_KEYWORDS:
            raise DomainError(
                "too_many_watch_values",
                f"单个 Watch 最多监听 {MAX_KEYWORDS} 个关键词。",
            )
        return tuple(normalized)

    if kind == "any_message":
        return ()

    raise DomainError(
        "invalid_watch_match_kind",
        "Watch match.type 必须是 sender、keyword 或 any_message。",
    )


def _quantifier(preset: Any, custom: Any, limits: ProgramLimits) -> int:
    name = str(preset or "each").strip().lower()
    if name == "custom":
        return _positive_int(custom, "quantifier_count", limits.max_quantifier_count)
    if name not in QUANTIFIER_PRESETS:
        raise DomainError(
            "invalid_watch_quantifier",
            "quantifier 必须是 each、every_3、every_10 或 custom。",
        )
    return QUANTIFIER_PRESETS[name]


def _settle(preset: Any, custom: Any, limits: ProgramLimits) -> float:
    name = str(preset or "normal_1s").strip().lower()
    if name == "custom":
        return _bounded_nonnegative(custom, "settle_seconds", limits.max_settle_seconds)
    if name not in SETTLE_PRESETS:
        raise DomainError(
            "invalid_watch_settle",
            "settle 必须是 immediate_0s、normal_1s、settle_3s 或 custom。",
        )
    return SETTLE_PRESETS[name]


def _recheck(preset: Any, custom: Any, limits: ProgramLimits) -> float | None:
    name = str(preset or "default_3m").strip().lower()
    if name == "custom":
        value = _number(custom, "recheck_seconds")
        if value <= 0 or value > limits.max_recheck_seconds:
            raise DomainError(
                "invalid_recheck_seconds",
                f"recheck_seconds 必须大于 0 且不超过 {limits.max_recheck_seconds:g}。",
            )
        return value
    if name not in RECHECK_PRESETS:
        raise DomainError(
            "invalid_program_recheck",
            "recheck 必须是 default_3m、ten_min、thirty_min、off 或 custom。",
        )
    return RECHECK_PRESETS[name]


def _lease(preset: Any, custom: Any, limits: ProgramLimits) -> int:
    name = str(preset or "2h").strip().lower()
    if name == "custom":
        return _positive_int(custom, "lease_seconds", limits.max_lease_seconds)
    if name not in LEASE_PRESETS:
        raise DomainError(
            "invalid_program_lease",
            "lease 必须是 10m、30m、2h、24h 或 custom。",
        )
    return min(LEASE_PRESETS[name], limits.max_lease_seconds)


def _watch_from_input(
    raw: Any,
    *,
    limits: ProgramLimits,
    fallback_id: str,
) -> WatchContract:
    if not isinstance(raw, Mapping):
        raise DomainError("invalid_watch", "每个 watches 项必须是对象。")

    match = raw.get("match")
    if isinstance(match, Mapping):
        kind = str(match.get("type") or "").strip().lower()
        values = match.get("values")
    else:
        kind = str(raw.get("match_kind") or raw.get("condition_kind") or "").strip().lower()
        values = raw.get("match_values")
        if values is None:
            values = raw.get("condition_values")

    watch_id = str(raw.get("watch_id") or fallback_id).strip()
    if not watch_id or len(watch_id) > 64:
        raise DomainError("invalid_watch_id", "watch_id 无效。")

    return WatchContract(
        watch_id=watch_id,
        match_kind=kind,
        match_values=_match_values(kind, values),
        quantifier_count=_quantifier(
            raw.get("quantifier", raw.get("frequency", "each")),
            raw.get("quantifier_count", raw.get("frequency_count", 0)),
            limits,
        ),
        settle_seconds=_settle(
            raw.get("settle", raw.get("response_speed", "normal_1s")),
            raw.get("settle_seconds", raw.get("settle_delay_seconds", 0)),
            limits,
        ),
    )


def _watches_from_input(
    raw_watches: Any,
    *,
    limits: ProgramLimits,
    program_seed: str,
) -> tuple[WatchContract, ...]:
    if raw_watches is None:
        rows: Sequence[Any] = ()
    elif isinstance(raw_watches, (list, tuple)):
        rows = raw_watches
    else:
        raise DomainError("invalid_watches", "watches 必须是对象数组。")

    if len(rows) > limits.max_watches_per_program:
        raise DomainError(
            "too_many_watches",
            f"一个 Program 最多包含 {limits.max_watches_per_program} 个 Watch。",
        )

    result: list[WatchContract] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        fallback = hashlib.sha256(
            f"{program_seed}|watch|{index}".encode("utf-8")
        ).hexdigest()[:12]
        watch = _watch_from_input(raw, limits=limits, fallback_id=fallback)
        if watch.watch_id in seen:
            raise DomainError("duplicate_watch_id", "同一 Program 内 watch_id 必须唯一。")
        seen.add(watch.watch_id)
        result.append(watch)
    return tuple(result)


def _watch_from_record(raw: Any, *, limits: ProgramLimits) -> WatchContract:
    if not isinstance(raw, Mapping):
        raise DomainError("invalid_watch_record", "Watch 记录必须是对象。")
    watch_id = str(raw.get("watch_id") or "").strip()
    if not watch_id:
        raise DomainError("invalid_watch_record", "Watch 记录缺少 watch_id。")
    kind = str(raw.get("match_kind") or "").strip().lower()
    return WatchContract(
        watch_id=watch_id,
        match_kind=kind,
        match_values=_match_values(kind, raw.get("match_values")),
        quantifier_count=_positive_int(
            raw.get("quantifier_count"),
            "quantifier_count",
            limits.max_quantifier_count,
        ),
        settle_seconds=_bounded_nonnegative(
            raw.get("settle_seconds"),
            "settle_seconds",
            limits.max_settle_seconds,
        ),
    )


def _program_from_record(raw: Any, *, now: float, limits: ProgramLimits) -> AttentionProgram | None:
    if not isinstance(raw, Mapping):
        raise DomainError("invalid_program_record", "Program 记录必须是对象。")
    program_id = str(raw.get("program_id") or "").strip()
    if not program_id or len(program_id) > 64:
        raise DomainError("invalid_program_record", "Program ID 无效。")
    scope = normalize_scope(raw.get("scope"))
    controller = normalize_target_id(raw.get("controller_sender_id"))
    goal = _goal(raw.get("goal"))
    rows = raw.get("watches")
    if not isinstance(rows, list):
        raise DomainError("invalid_program_record", "watches 必须是数组。")
    if len(rows) > limits.max_watches_per_program:
        raise DomainError("invalid_program_record", "Program Watch 数量超过上限。")
    watches = tuple(_watch_from_record(row, limits=limits) for row in rows)
    if len({watch.watch_id for watch in watches}) != len(watches):
        raise DomainError("invalid_program_record", "Program 中存在重复 watch_id。")
    recheck_raw = raw.get("recheck_seconds")
    recheck_seconds = None
    if recheck_raw is not None:
        recheck_seconds = _number(recheck_raw, "recheck_seconds")
        if recheck_seconds <= 0 or recheck_seconds > limits.max_recheck_seconds:
            raise DomainError("invalid_program_record", "Program recheck_seconds 无效。")
    if not watches and recheck_seconds is None:
        raise DomainError("invalid_program_record", "Program 必须至少有一个 Watch 或启用 Recheck。")
    created_at = _number(raw.get("created_at"), "created_at")
    expires_at = _number(raw.get("expires_at"), "expires_at")
    if expires_at <= created_at or expires_at - created_at > limits.max_lease_seconds + 1:
        raise DomainError("invalid_program_record", "Program Lease 无效。")
    if expires_at <= now:
        return None
    return AttentionProgram(
        program_id=program_id,
        scope=scope,
        controller_sender_id=controller,
        goal=goal,
        watches=watches,
        recheck_seconds=recheck_seconds,
        created_at=created_at,
        expires_at=expires_at,
        created_by=_actor(raw.get("created_by")),
    )


def _legacy_listener_to_program(
    raw: Any,
    *,
    now: float,
    limits: ProgramLimits,
) -> AttentionProgram | None:
    if not isinstance(raw, Mapping):
        raise DomainError("invalid_legacy_listener", "旧 Listener 记录必须是对象。")
    listener_id = str(raw.get("listener_id") or "").strip()
    if not listener_id:
        raise DomainError("invalid_legacy_listener", "旧 Listener 缺少 listener_id。")
    scope = normalize_scope(raw.get("scope"))
    controller = normalize_target_id(raw.get("owner_sender_id"))
    created_at = _number(raw.get("created_at"), "created_at")
    expires_at = _number(raw.get("expires_at"), "expires_at")
    if expires_at <= now:
        return None
    if expires_at <= created_at or expires_at - created_at > limits.max_lease_seconds + 1:
        raise DomainError("invalid_legacy_listener", "旧 Listener Lease 无效。")
    kind = str(raw.get("condition_kind") or "").strip().lower()
    if kind == "time_only":
        watches: tuple[WatchContract, ...] = ()
    else:
        watches = (
            WatchContract(
                watch_id=f"migrated-{listener_id[:32]}",
                match_kind=kind,
                match_values=_match_values(kind, raw.get("condition_values")),
                quantifier_count=_positive_int(
                    raw.get("frequency_count"),
                    "quantifier_count",
                    limits.max_quantifier_count,
                ),
                settle_seconds=_bounded_nonnegative(
                    raw.get("settle_delay_seconds"),
                    "settle_seconds",
                    limits.max_settle_seconds,
                ),
            ),
        )
    recheck_raw = raw.get("watchdog_seconds")
    recheck_seconds = None
    if recheck_raw is not None:
        recheck_seconds = _number(recheck_raw, "recheck_seconds")
        if recheck_seconds <= 0 or recheck_seconds > limits.max_recheck_seconds:
            raise DomainError("invalid_legacy_listener", "旧 Listener watchdog 无效。")
    if not watches and recheck_seconds is None:
        raise DomainError("invalid_legacy_listener", "旧 time_only Listener 缺少 watchdog，无法迁移。")
    return AttentionProgram(
        program_id=listener_id,
        scope=scope,
        controller_sender_id=controller,
        goal=_goal(raw.get("goal")),
        watches=watches,
        recheck_seconds=recheck_seconds,
        created_at=created_at,
        expires_at=expires_at,
        created_by=_actor(raw.get("created_by")),
    )


def is_owned_program_payload(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    tag = payload.get(PROGRAM_TAG)
    return isinstance(tag, Mapping) and tag.get("kind") == PROGRAM_KIND


class AttentionProgramService:
    def __init__(
        self,
        limits: ProgramLimits,
        store: AstrBotKVStateStore,
        cron_manager: Any,
        *,
        legacy_store: AstrBotKVStateStore | None = None,
        wall_clock: Callable[[], float] = time.time,
        preflight: Callable[[str], bool | Awaitable[bool]] | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.limits = limits
        self._store = store
        self._legacy_store = legacy_store
        self._manager = cron_manager
        self._wall_clock = wall_clock
        self._preflight = preflight
        self._sleeper = sleeper

        self._state_lock = asyncio.Lock()
        self._runtime_lock = asyncio.Lock()
        self._programs: dict[str, AttentionProgram] = {}
        self._by_scope: dict[str, tuple[AttentionProgram, ...]] = {}
        self._program_runtime: dict[str, _ProgramRuntime] = {}
        self._watch_runtime: dict[tuple[str, str], _WatchRuntime] = {}
        self._recheck_due: dict[str, float] = {}
        self._dispatch_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._dispatch_generation: dict[tuple[str, str], int] = {}
        self._running_keys: set[tuple[str, str]] = set()
        self._active_job_ids: set[str] = set()
        self._seen_events: OrderedDict[tuple[str, str], None] = OrderedDict()
        self._created_serial = 0

        self.active = False
        self.storage_ready = False
        self.storage_write_healthy = False
        self.loaded_from = "none"
        self.migrated_from_listener_v1 = False
        self.last_error_code: str | None = None
        self.last_write_error_code: str | None = None
        self.quarantined: list[dict[str, Any]] = []

        self._signals_seen = 0
        self._duplicate_events = 0
        self._watch_candidates = 0
        self._watch_debounces = 0
        self._dirty_marks = 0
        self._reconciliations = 0
        self._reconcile_requeues = 0
        self._recheck_signals = 0
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
            raise DomainError("program_scheduler_unavailable", "当前 AstrBot 未提供兼容的一次性主动 Agent 调度接口。")
        return manager

    def _rebuild_index(self) -> None:
        grouped: dict[str, list[AttentionProgram]] = {}
        for program in self._programs.values():
            grouped.setdefault(program.scope, []).append(program)
        self._by_scope = {
            scope: tuple(sorted(rows, key=lambda row: row.program_id))
            for scope, rows in grouped.items()
        }

    async def _raw_owned_jobs(self) -> list[Any]:
        jobs = await self._require_manager().list_jobs("active_agent")
        return [
            job for job in jobs
            if is_owned_program_payload(getattr(job, "payload", None))
        ]

    async def _preflight_allowed(self, scope: str) -> bool:
        if not self.active or not self.storage_ready:
            return False
        if self._preflight is None:
            return True
        try:
            result = self._preflight(scope)
            if inspect.isawaitable(result):
                result = await result
            return bool(result)
        except Exception:
            return False

    def _document(self, programs: Mapping[str, AttentionProgram] | None = None) -> dict[str, Any]:
        source = programs if programs is not None else self._programs
        now = self._now()
        rows = [program for program in source.values() if program.expires_at > now]
        rows.sort(key=lambda row: row.program_id)
        return {
            "schema_version": PROGRAM_SCHEMA_VERSION,
            "programs": [program.as_record() for program in rows],
        }

    async def _load_v2_state(self) -> str:
        """Return loaded, absent, or invalid.

        Legacy v1 migration is allowed only when both v2 slots are truly absent.
        If any v2 document exists but cannot be validated, fail inert rather than
        resurrecting potentially stale v1 desired state.
        """
        try:
            stored = await self._store.load()
        except Exception as exc:
            self.last_error_code = f"program_storage_load_failed:{type(exc).__name__}"
            return "invalid"

        if stored.primary_error or stored.backup_error:
            errors = ",".join(
                value
                for value in (stored.primary_error, stored.backup_error)
                if value
            )
            self.last_error_code = f"program_storage_slot_read_failed:{errors}"
            self.storage_ready = False
            self.storage_write_healthy = False
            self.loaded_from = "read_error"
            return "invalid"

        if stored.primary is None and stored.backup is None:
            return "absent"

        now = self._now()
        saw_document = False
        for source, document in (("primary", stored.primary), ("backup", stored.backup)):
            if document is None:
                continue
            saw_document = True
            try:
                if not isinstance(document, Mapping):
                    raise DomainError("invalid_program_state_document", "Program 状态文档必须是对象。")
                if document.get("schema_version") != PROGRAM_SCHEMA_VERSION:
                    raise DomainError("unsupported_program_state_schema", "Program 状态 Schema 版本不受支持。")
                rows = document.get("programs")
                if not isinstance(rows, list):
                    raise DomainError("invalid_program_state_document", "programs 必须是数组。")
                loaded: dict[str, AttentionProgram] = {}
                quarantined: list[dict[str, Any]] = []
                for index, raw in enumerate(rows):
                    try:
                        program = _program_from_record(raw, now=now, limits=self.limits)
                    except DomainError as exc:
                        quarantined.append({"index": index, "error_code": exc.code})
                        continue
                    if program is None:
                        continue
                    if program.program_id in loaded:
                        quarantined.append({"index": index, "error_code": "duplicate_program_id"})
                        continue
                    loaded[program.program_id] = program
                self._programs = loaded
                self._rebuild_index()
                self.quarantined = quarantined
                self.storage_ready = True
                self.storage_write_healthy = True
                self.loaded_from = source
                self.last_error_code = None
                return "loaded"
            except DomainError as exc:
                self.last_error_code = exc.code
        if saw_document:
            self.storage_ready = False
            self.storage_write_healthy = False
            self.loaded_from = "invalid_v2"
            return "invalid"
        return "absent"

    async def _migrate_listener_v1(self) -> bool:
        if self._legacy_store is None:
            return False
        try:
            stored = await self._legacy_store.load()
        except Exception:
            return False
        now = self._now()
        for source, document in (("legacy_primary", stored.primary), ("legacy_backup", stored.backup)):
            if document is None or not isinstance(document, Mapping):
                continue
            if document.get("schema_version") != LISTENER_SCHEMA_VERSION:
                continue
            rows = document.get("listeners")
            if not isinstance(rows, list):
                continue
            migrated: dict[str, AttentionProgram] = {}
            quarantined: list[dict[str, Any]] = []
            for index, raw in enumerate(rows):
                try:
                    program = _legacy_listener_to_program(raw, now=now, limits=self.limits)
                except DomainError as exc:
                    quarantined.append({"index": index, "error_code": exc.code})
                    continue
                if program is not None:
                    migrated[program.program_id] = program
            try:
                await self._store.commit(
                    {"schema_version": PROGRAM_SCHEMA_VERSION, "programs": []},
                    self._document(migrated),
                )
            except Exception as exc:
                self.last_error_code = f"program_migration_write_failed:{type(exc).__name__}"
                return False
            self._programs = migrated
            self._rebuild_index()
            self.quarantined = quarantined
            self.storage_ready = True
            self.storage_write_healthy = True
            self.loaded_from = source
            self.migrated_from_listener_v1 = True
            self.last_error_code = None
            return True
        return False

    async def _commit_programs(self, candidate: dict[str, AttentionProgram]) -> None:
        if not self.storage_ready:
            raise DomainError("program_storage_unavailable", "注意力程序状态存储当前不可用。")
        previous_document = self._document()
        candidate_document = self._document(candidate)
        try:
            await self._store.commit(previous_document, candidate_document)
        except CommitIndeterminateError as exc:
            self.storage_ready = False
            self.storage_write_healthy = False
            self.last_write_error_code = "program_commit_indeterminate"
            self._programs = {}
            self._rebuild_index()
            await self._cancel_all_runtime()
            raise DomainError(
                "program_commit_indeterminate",
                "注意力程序写入结果无法确认；Runtime 已安全停用以避免陈旧激活。",
            ) from exc
        except Exception as exc:
            self.storage_write_healthy = False
            self.last_write_error_code = "program_storage_write_failed"
            raise DomainError(
                "program_storage_write_failed",
                f"注意力程序状态写入失败: {type(exc).__name__}",
            ) from exc
        self._programs = candidate
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

        load_status = await self._load_v2_state()
        if load_status == "absent":
            migrated = await self._migrate_listener_v1()
            if not migrated:
                self._programs = {}
                self._rebuild_index()
                self.storage_ready = True
                self.storage_write_healthy = True
                self.loaded_from = "empty"
                self.last_error_code = None
        elif load_status == "invalid":
            return

        if not self.storage_ready:
            return

        self._require_manager()
        self.active = True
        now = self._now()
        async with self._runtime_lock:
            for program in self._programs.values():
                runtime = self._program_runtime.setdefault(program.program_id, _ProgramRuntime())
                runtime.dirty_generation = max(runtime.dirty_generation, 1)
                runtime.pending_reasons.add("restart_dirty")
                self._dirty_marks += 1
                for watch in program.watches:
                    self._watch_runtime.setdefault(
                        (program.program_id, watch.watch_id),
                        _WatchRuntime(),
                    )
                if program.recheck_seconds is not None:
                    self._recheck_due[program.program_id] = now + program.recheck_seconds
            for key in {program.attention_key for program in self._programs.values()}:
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
        self._program_runtime.clear()
        self._watch_runtime.clear()
        self._recheck_due.clear()
        self._seen_events.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _active_programs_for_key(
        self,
        key: tuple[str, str],
        *,
        now: float,
    ) -> list[AttentionProgram]:
        scope, controller = key
        return [
            program for program in self._by_scope.get(scope, ())
            if program.controller_sender_id == controller and program.expires_at > now
        ]

    def _remember_event_locked(self, envelope: EventEnvelope) -> bool:
        key = envelope.dedupe_key
        if key in self._seen_events:
            self._seen_events.move_to_end(key)
            self._duplicate_events += 1
            return False
        self._seen_events[key] = None
        while len(self._seen_events) > self.limits.max_dedupe_keys:
            self._seen_events.popitem(last=False)
        return True

    @staticmethod
    def _reset_watch_runtime(runtime: _WatchRuntime) -> None:
        runtime.observed_since_reset = 0
        runtime.pending = False
        runtime.ready_at = 0.0
        runtime.first_signal_at = 0.0
        runtime.last_signal_at = 0.0
        runtime.pending_match_count = 0
        runtime.latest_event_ref = None

    def _mark_dirty_locked(
        self,
        program: AttentionProgram,
        reasons: Sequence[str],
        event_refs: Sequence[str] = (),
    ) -> None:
        runtime = self._program_runtime.setdefault(program.program_id, _ProgramRuntime())
        runtime.dirty_generation += 1
        runtime.pending_reasons.update(reason for reason in reasons if reason)
        for ref in event_refs:
            if ref and ref not in runtime.event_refs:
                runtime.event_refs.append(ref)
        if len(runtime.event_refs) > 16:
            runtime.event_refs[:] = runtime.event_refs[-16:]
        self._dirty_marks += 1

    def _promote_due_locked(self, program: AttentionProgram, *, now: float) -> bool:
        reasons: list[str] = []
        refs: list[str] = []
        for watch in program.watches:
            runtime = self._watch_runtime.setdefault(
                (program.program_id, watch.watch_id),
                _WatchRuntime(),
            )
            if not (
                runtime.pending
                and runtime.ready_at <= now + DISPATCH_EPSILON_SECONDS
            ):
                continue
            reasons.append(f"watch:{watch.watch_id}")
            if runtime.latest_event_ref:
                refs.append(runtime.latest_event_ref)
            self._reset_watch_runtime(runtime)

        recheck_due = self._recheck_due.get(program.program_id)
        if recheck_due is not None and recheck_due <= now + DISPATCH_EPSILON_SECONDS:
            self._recheck_due.pop(program.program_id, None)
            reasons.append("recheck")
            self._recheck_signals += 1

        if not reasons:
            return False
        self._mark_dirty_locked(program, reasons, refs)
        return True

    def _next_due_locked(
        self,
        key: tuple[str, str],
        *,
        now: float,
    ) -> float | None:
        due_values: list[float] = []
        for program in self._active_programs_for_key(key, now=now):
            runtime = self._program_runtime.setdefault(program.program_id, _ProgramRuntime())
            if runtime.dirty_generation > runtime.reconciled_generation:
                return now
            for watch in program.watches:
                wr = self._watch_runtime.get((program.program_id, watch.watch_id))
                if wr is not None and wr.pending:
                    due_values.append(wr.ready_at)
            recheck_due = self._recheck_due.get(program.program_id)
            if recheck_due is not None:
                due_values.append(recheck_due)
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
        self._dispatch_tasks[key] = asyncio.create_task(
            self._wait_dispatch(key, generation, due),
            name=f"attention-program-{self.scope_ref(key[0])}-{generation}",
        )

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
                    "error_code": f"program_dispatch_{type(exc).__name__}",
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
        scope, controller_sender_id = key
        async with self._runtime_lock:
            if not self.active or self._dispatch_generation.get(key) != generation:
                return
            self._dispatch_tasks.pop(key, None)
            now = self._now()
            programs = self._active_programs_for_key(key, now=now)
            for program in programs:
                self._promote_due_locked(program, now=now)

            items: list[_ReconcileItem] = []
            active_ids: list[str] = []
            for program in programs:
                runtime = self._program_runtime.setdefault(program.program_id, _ProgramRuntime())
                if runtime.dirty_generation <= runtime.reconciled_generation:
                    continue
                target_generation = runtime.dirty_generation
                reasons = tuple(sorted(runtime.pending_reasons)) or ("dirty",)
                refs = tuple(runtime.event_refs)
                runtime.reconciled_generation = target_generation
                runtime.pending_reasons.clear()
                runtime.event_refs.clear()
                items.append(
                    _ReconcileItem(
                        program_id=program.program_id,
                        goal=program.goal,
                        generation=target_generation,
                        reasons=reasons,
                        event_refs=refs,
                    )
                )
                active_ids.append(program.program_id)

            if not items:
                self._reschedule_key_locked(key, now=now)
                return
            self._running_keys.add(key)

        try:
            await self._execute_reconcile(
                scope=scope,
                controller_sender_id=controller_sender_id,
                items=items,
            )
        finally:
            completion = self._now()
            async with self._runtime_lock:
                self._running_keys.discard(key)
                current_ids = set(self._programs)
                for program_id in active_ids:
                    program = self._programs.get(program_id)
                    if (
                        program is not None
                        and program.expires_at > completion
                        and program.recheck_seconds is not None
                    ):
                        self._recheck_due[program_id] = completion + program.recheck_seconds
                if any(
                    self._program_runtime.get(program_id, _ProgramRuntime()).dirty_generation
                    > self._program_runtime.get(program_id, _ProgramRuntime()).reconciled_generation
                    for program_id in current_ids
                    if self._programs[program_id].attention_key == key
                ):
                    self._reconcile_requeues += 1
                self._reschedule_key_locked(key, now=completion)

    async def _execute_reconcile(
        self,
        *,
        scope: str,
        controller_sender_id: str,
        items: list[_ReconcileItem],
    ) -> None:
        if not await self._preflight_allowed(scope):
            self._preflight_suppressed += 1
            return

        rows: list[str] = []
        for item in items:
            labels: list[str] = []
            for reason in item.reasons:
                if reason == "recheck":
                    labels.append("无外界事件时的定期 Recheck 到点")
                elif reason == "restart_dirty":
                    labels.append("Runtime 重启后要求按当前世界重新对齐")
                elif reason.startswith("watch:"):
                    labels.append(f"Watch {reason.split(':', 1)[1]} 已满足并稳定")
                else:
                    labels.append(reason)
            refs = (
                f"\n  最近事件引用：{', '.join(item.event_refs)}"
                if item.event_refs
                else ""
            )
            rows.append(
                f"- Program {item.program_id} / generation {item.generation}\n"
                f"  激活原因：{'；'.join(labels)}\n"
                f"  开放目标：{item.goal}{refs}"
            )

        note = (
            "[AttentionProgram Reconcile]\n"
            "这是一个 level-triggered 主 Agent 回合：Watch/Recheck 只表示相关 Program "
            "可能需要重新计算，不是要求机械执行某个旧动作。请读取当前 AstrBot 会话"
            "上下文和可用正式工具，基于当前世界重新判断 Goal。没有独立公开价值时"
            "调用 yield_current_turn；目标已完成时调用 manage_attention_program(cancel) "
            "净化 Program。运行期间如果又有新事件，Runtime 会把 Program 再次标 dirty，"
            "并在本轮完成后按 single-flight 语义重新 reconcile，而不会并发启动第二个"
            "同主体 Agent。\n"
            + "\n".join(rows)
        )
        now = self._now()
        payload = {
            "session": scope,
            "sender_id": controller_sender_id,
            "origin": "astrbot_plugin_sender_activation",
            "note": note,
            PROGRAM_TAG: {
                "kind": PROGRAM_KIND,
                "schema_version": PROGRAM_SCHEMA_VERSION,
                "program_ids": [item.program_id for item in items],
                "generations": [item.generation for item in items],
                "created_at": now,
                "level_triggered": True,
                "single_flight": True,
            },
        }

        manager = self._require_manager()
        job = None
        try:
            job = await manager.add_active_job(
                name=f"[Attention Reconcile] {self.scope_ref(scope)}",
                cron_expression=None,
                payload=payload,
                description="AttentionProgram dirty/recheck 归一化后的单次主 Agent reconcile。",
                timezone="UTC",
                enabled=False,
                persistent=False,
                run_once=True,
                run_at=datetime.fromtimestamp(now, tz=timezone.utc),
            )
            job_id = str(getattr(job, "job_id", "") or "")
            if not job_id:
                raise RuntimeError("missing attention program active job id")
            self._active_job_ids.add(job_id)
            await manager.run_job_now(job_id)
            self._reconciliations += 1
        except Exception as exc:
            self.quarantined.append(
                {
                    "scope_ref": self.scope_ref(scope),
                    "error_code": f"program_execute_{type(exc).__name__}",
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
            envelope = EventEnvelope(
                id="preview",
                source="astrbot://preview",
                type="com.astrbot.qq.group.message",
                subject=str(sender_id or "").strip(),
                occurred_at=None,
                observed_at=self._now(),
                payload_ref=None,
                data={"message": str(message or "")},
            )
            now = self._now()
            return any(
                program.expires_at > now
                and any(watch.matches(envelope) for watch in program.watches)
                for program in self._by_scope.get(normalized_scope, ())
            )
        except Exception:
            return False

    async def observe_event(self, *, scope: Any, envelope: EventEnvelope) -> dict[str, Any]:
        if not self.active or not self.storage_ready:
            return {"duplicate": False, "matched_watches": [], "dirty_programs": []}
        normalized_scope = normalize_scope(scope)
        now = self._now()
        matched: list[str] = []
        affected_keys: set[tuple[str, str]] = set()
        dirty_programs: set[str] = set()

        async with self._runtime_lock:
            if not self._remember_event_locked(envelope):
                return {"duplicate": True, "matched_watches": [], "dirty_programs": []}

            for program in self._by_scope.get(normalized_scope, ()):
                if program.expires_at <= now:
                    continue
                program_had_match = False
                for watch in program.watches:
                    if not watch.matches(envelope):
                        continue
                    program_had_match = True
                    self._signals_seen += 1
                    matched.append(f"{program.program_id}:{watch.watch_id}")
                    runtime = self._watch_runtime.setdefault(
                        (program.program_id, watch.watch_id),
                        _WatchRuntime(),
                    )
                    if runtime.pending:
                        runtime.ready_at = now + watch.settle_seconds
                        runtime.last_signal_at = now
                        runtime.pending_match_count += 1
                        runtime.latest_event_ref = envelope.ref()
                        self._watch_debounces += 1
                    else:
                        runtime.observed_since_reset += 1
                        if runtime.observed_since_reset < watch.quantifier_count:
                            continue
                        runtime.pending = True
                        runtime.ready_at = now + watch.settle_seconds
                        runtime.first_signal_at = now
                        runtime.last_signal_at = now
                        runtime.pending_match_count = runtime.observed_since_reset
                        runtime.latest_event_ref = envelope.ref()
                        self._watch_candidates += 1

                if program_had_match:
                    before = self._program_runtime.setdefault(
                        program.program_id,
                        _ProgramRuntime(),
                    ).dirty_generation
                    self._promote_due_locked(program, now=now)
                    after = self._program_runtime[program.program_id].dirty_generation
                    if after > before:
                        dirty_programs.add(program.program_id)
                    affected_keys.add(program.attention_key)

            for key in affected_keys:
                self._reschedule_key_locked(key, now=now)

        return {
            "duplicate": False,
            "matched_watches": matched,
            "dirty_programs": sorted(dirty_programs),
        }

    def _program_public(self, program: AttentionProgram, *, now: float) -> dict[str, Any]:
        runtime = self._program_runtime.get(program.program_id, _ProgramRuntime())
        watches: list[dict[str, Any]] = []
        for watch in program.watches:
            wr = self._watch_runtime.get((program.program_id, watch.watch_id), _WatchRuntime())
            watches.append(
                {
                    **watch.as_record(),
                    "runtime": {
                        "observed_since_reset": wr.observed_since_reset,
                        "pending": wr.pending,
                        "ready_at": wr.ready_at if wr.pending else None,
                        "pending_match_count": wr.pending_match_count,
                        "latest_event_ref": wr.latest_event_ref,
                    },
                }
            )
        recheck_due = self._recheck_due.get(program.program_id)
        return {
            **program.as_record(),
            "scope_ref": self.scope_ref(program.scope),
            "remaining_seconds": max(0, math.ceil(program.expires_at - now)),
            "watches": watches,
            "runtime": {
                "dirty_generation": runtime.dirty_generation,
                "reconciled_generation": runtime.reconciled_generation,
                "dirty": runtime.dirty_generation > runtime.reconciled_generation,
                "attention_key": self.scope_ref(
                    f"{program.scope}\0{program.controller_sender_id}"
                ),
                "recheck_due_at": recheck_due,
                "recheck_remaining_seconds": (
                    max(0, math.ceil(recheck_due - now))
                    if recheck_due is not None
                    else None
                ),
            },
        }

    async def snapshot(self, *, scope: Any | None = None) -> dict[str, Any]:
        now = self._now()
        normalized_scope = normalize_scope(scope) if scope is not None else None
        async with self._runtime_lock:
            rows = [
                self._program_public(program, now=now)
                for program in self._programs.values()
                if program.expires_at > now
                and (normalized_scope is None or program.scope == normalized_scope)
            ]
        rows.sort(key=lambda row: (row["scope"], row["program_id"]))
        return {
            "programs": rows,
            "known_scopes": sorted({self.scope_ref(row["scope"]) for row in rows}),
            "raw_scopes": sorted({row["scope"] for row in rows}),
            "quarantined": list(self.quarantined),
        }

    async def legacy_listener_snapshot(self, *, scope: Any | None = None) -> dict[str, Any]:
        snapshot = await self.snapshot(scope=scope)
        listeners: list[dict[str, Any]] = []
        for program in snapshot["programs"]:
            watches = program.get("watches") or []
            watch = watches[0] if len(watches) == 1 else None
            listeners.append(
                {
                    "listener_id": program["program_id"],
                    "scope": program["scope"],
                    "scope_ref": program["scope_ref"],
                    "owner_sender_id": program["controller_sender_id"],
                    "condition_kind": (
                        watch["match_kind"]
                        if watch is not None
                        else ("time_only" if not watches else "multi_watch")
                    ),
                    "condition_values": watch["match_values"] if watch is not None else [],
                    "frequency_count": watch["quantifier_count"] if watch is not None else 1,
                    "settle_delay_seconds": watch["settle_seconds"] if watch is not None else 0,
                    "watchdog_seconds": program["recheck_seconds"],
                    "goal": program["goal"],
                    "created_at": program["created_at"],
                    "expires_at": program["expires_at"],
                    "created_by": program["created_by"],
                    "remaining_seconds": program["remaining_seconds"],
                    "runtime": program["runtime"],
                    "compatibility": "program_view",
                }
            )
        return {
            "listeners": listeners,
            "known_scopes": snapshot["known_scopes"],
            "raw_scopes": snapshot["raw_scopes"],
            "quarantined": snapshot["quarantined"],
        }

    def owns_all(
        self,
        *,
        scope: Any,
        controller_sender_id: Any,
        program_ids: list[str] | None,
    ) -> bool:
        try:
            normalized_scope = normalize_scope(scope)
            controller = normalize_target_id(controller_sender_id)
        except DomainError:
            return False
        ids = {
            str(value or "").strip()
            for value in (program_ids or [])
            if str(value or "").strip()
        }
        if not ids:
            return False
        return all(
            (program := self._programs.get(program_id)) is not None
            and program.scope == normalized_scope
            and program.controller_sender_id == controller
            for program_id in ids
        )

    def _new_program_id(
        self,
        *,
        scope: str,
        controller: str,
        actor_ref: str,
        goal: str,
        now: float,
    ) -> str:
        self._created_serial += 1
        seed = f"{scope}|{controller}|{actor_ref}|{goal}|{now:.9f}|{self._created_serial}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]

    async def manage(
        self,
        *,
        scope: Any,
        action: Any,
        controller_sender_id: Any,
        actor_ref: Any,
        program_ids: list[str] | None = None,
        goal: Any = "",
        watches: Any = None,
        recheck: Any = "default_3m",
        recheck_seconds: Any = 0,
        lease: Any = "2h",
        lease_seconds: Any = 0,
    ) -> dict[str, Any]:
        normalized_scope = normalize_scope(scope)
        normalized_action = str(action or "").strip().lower()

        if normalized_action == "list":
            return {
                "status": "ok",
                "error_code": None,
                "action": "list",
                "changed": False,
                "effect_contract": PROGRAM_EFFECT_CONTRACT,
                "effect_applied": False,
                "effect_state": "not_applied",
                "reply_guaranteed": False,
                "state": await self.snapshot(scope=normalized_scope),
            }

        ids = [
            str(value or "").strip()
            for value in (program_ids or [])
            if str(value or "").strip()
        ]

        if normalized_action == "cancel":
            if not ids:
                raise DomainError("missing_program_ids", "cancel 必须提供 program_ids。")
            unique_ids = set(ids)
            async with self._state_lock:
                selected = [
                    program
                    for program_id, program in self._programs.items()
                    if program_id in unique_ids and program.scope == normalized_scope
                ]
                if len(selected) != len(unique_ids):
                    raise DomainError("program_absent", "至少一个 program_id 不存在或不属于当前群。")
                candidate = dict(self._programs)
                for program in selected:
                    candidate.pop(program.program_id, None)

                # Publish persisted desired state and volatile runtime teardown
                # under one observation boundary so no event can race between
                # "Program disappeared" and scheduler cleanup.
                async with self._runtime_lock:
                    await self._commit_programs(candidate)
                    affected_keys = {program.attention_key for program in selected}
                    for program in selected:
                        self._program_runtime.pop(program.program_id, None)
                        self._recheck_due.pop(program.program_id, None)
                        for watch in program.watches:
                            self._watch_runtime.pop((program.program_id, watch.watch_id), None)
                    for key in affected_keys:
                        self._reschedule_key_locked(key)

            return {
                "status": "ok",
                "error_code": None,
                "action": "cancel",
                "program_ids": sorted(unique_ids),
                "changed": bool(selected),
                "outcome": "attention_programs_cancelled",
                "effect_contract": PROGRAM_EFFECT_CONTRACT,
                "effect_applied": bool(selected),
                "effect_state": "applied" if selected else "not_applied",
                "reply_guaranteed": False,
            }

        if normalized_action not in {"create", "update"}:
            raise DomainError("invalid_action", "action 必须是 create、update、cancel 或 list。")
        if not self.active:
            raise DomainError("program_service_inactive", "AttentionProgram Runtime 当前未激活。")
        if not self.storage_ready:
            raise DomainError("program_storage_unavailable", "注意力程序状态存储当前不可用。")

        creator = _actor(actor_ref)
        normalized_goal = _goal(goal)
        recheck_value = _recheck(recheck, recheck_seconds, self.limits)
        lease_value = _lease(lease, lease_seconds, self.limits)
        now = self._now()

        existing: AttentionProgram | None = None
        if normalized_action == "update":
            if len(ids) != 1:
                raise DomainError("update_requires_one_program", "update 必须且只能提供一个 program_id。")
            existing = self._programs.get(ids[0])
            if existing is None or existing.scope != normalized_scope:
                raise DomainError("program_absent", "program_id 不存在或不属于当前群。")
            program_id = existing.program_id
            controller = existing.controller_sender_id
            created_at = existing.created_at
            created_by = existing.created_by
        else:
            controller = normalize_target_id(controller_sender_id)
            program_id = self._new_program_id(
                scope=normalized_scope,
                controller=controller,
                actor_ref=creator,
                goal=normalized_goal,
                now=now,
            )
            created_at = now
            created_by = creator

        normalized_watches = _watches_from_input(
            watches,
            limits=self.limits,
            program_seed=program_id,
        )
        if not normalized_watches and recheck_value is None:
            raise DomainError("program_has_no_wake_source", "Program 至少需要一个 Watch 或启用 Recheck。")

        program = AttentionProgram(
            program_id=program_id,
            scope=normalized_scope,
            controller_sender_id=controller,
            goal=normalized_goal,
            watches=normalized_watches,
            recheck_seconds=recheck_value,
            created_at=created_at,
            expires_at=now + lease_value,
            created_by=created_by,
        )

        async with self._state_lock:
            active = {
                row.program_id: row
                for row in self._programs.values()
                if row.expires_at > now
            }
            if normalized_action == "create":
                scope_count = sum(
                    1 for row in active.values() if row.scope == normalized_scope
                )
                if scope_count >= self.limits.max_per_scope:
                    raise DomainError("program_scope_capacity_exceeded", "当前群 Program 数量达到上限。")
                if len(active) >= self.limits.max_total:
                    raise DomainError("program_total_capacity_exceeded", "AttentionProgram 总数达到上限。")
            active[program_id] = program

            # Hold the runtime observation boundary across persistence + publish.
            # This deliberately trades a short event wait for "no signal can be
            # observed under the new spec and then erased by runtime reset".
            async with self._runtime_lock:
                await self._commit_programs(active)
                self._program_runtime[program_id] = _ProgramRuntime()
                for key in [key for key in self._watch_runtime if key[0] == program_id]:
                    self._watch_runtime.pop(key, None)
                for watch in program.watches:
                    self._watch_runtime[(program_id, watch.watch_id)] = _WatchRuntime()
                if recheck_value is not None:
                    self._recheck_due[program_id] = now + recheck_value
                else:
                    self._recheck_due.pop(program_id, None)
                affected = {program.attention_key}
                if existing is not None:
                    affected.add(existing.attention_key)
                for key in affected:
                    self._reschedule_key_locked(key, now=now)

        return {
            "status": "ok",
            "error_code": None,
            "action": normalized_action,
            "program_id": program_id,
            "changed": True,
            "outcome": (
                "attention_program_created"
                if normalized_action == "create"
                else "attention_program_updated"
            ),
            "effect_contract": PROGRAM_EFFECT_CONTRACT,
            "effect_applied": True,
            "effect_state": "applied",
            "reply_guaranteed": False,
            "program": self._program_public(program, now=now),
        }

    async def manage_legacy_listener(
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
        normalized_action = str(action or "").strip().lower()
        if normalized_action == "list":
            return {
                "status": "ok",
                "error_code": None,
                "action": "list",
                "changed": False,
                "effect_contract": PROGRAM_EFFECT_CONTRACT,
                "effect_applied": False,
                "effect_state": "not_applied",
                "reply_guaranteed": False,
                "state": await self.legacy_listener_snapshot(scope=scope),
                "compatibility": "listener_v1_view",
            }
        if normalized_action == "cancel":
            result = await self.manage(
                scope=scope,
                action="cancel",
                controller_sender_id=owner_sender_id,
                actor_ref=actor_ref,
                program_ids=listener_ids,
            )
            result["action"] = "cancel"
            result["listener_ids"] = result.pop("program_ids", [])
            result["outcome"] = "listeners_cancelled"
            result["compatibility"] = "listener_v1_adapter"
            return result
        if normalized_action != "start":
            raise DomainError("invalid_action", "旧 Listener action 必须是 start、cancel 或 list。")

        kind = str(condition_kind or "").strip().lower()
        if kind == "time_only":
            watches: list[dict[str, Any]] = []
        else:
            watches = [
                {
                    "match": {
                        "type": kind,
                        "values": list(condition_values or []),
                    },
                    "quantifier": str(frequency or "each"),
                    "quantifier_count": frequency_count,
                    "settle": str(response_speed or "normal_1s"),
                    "settle_seconds": settle_delay_seconds,
                }
            ]
        result = await self.manage(
            scope=scope,
            action="create",
            controller_sender_id=owner_sender_id,
            actor_ref=actor_ref,
            goal=goal,
            watches=watches,
            recheck=watchdog,
            recheck_seconds=watchdog_seconds,
            lease=lifetime,
            lease_seconds=lifetime_seconds,
        )
        result["action"] = "start"
        result["listener_id"] = result["program_id"]
        result["outcome"] = "listener_started"
        result["compatibility"] = "listener_v1_adapter"
        return result

    async def health(self) -> dict[str, Any]:
        try:
            snapshot = await self.snapshot()
            count = len(snapshot["programs"])
            quarantined = len(snapshot["quarantined"])
        except Exception:
            count = 0
            quarantined = len(self.quarantined)
        return {
            "program_active": self.active,
            "program_storage_ready": self.storage_ready,
            "program_storage_write_healthy": self.storage_write_healthy,
            "program_loaded_from": self.loaded_from,
            "program_migrated_from_listener_v1": self.migrated_from_listener_v1,
            "program_last_error_code": self.last_error_code,
            "program_last_write_error_code": self.last_write_error_code,
            "program_count": count,
            "program_running_attention_keys": len(self._running_keys),
            "program_dispatch_tasks": len(self._dispatch_tasks),
            "program_signals_seen_total": self._signals_seen,
            "program_duplicate_events_total": self._duplicate_events,
            "program_watch_candidates_total": self._watch_candidates,
            "program_watch_debounces_total": self._watch_debounces,
            "program_dirty_marks_total": self._dirty_marks,
            "program_reconciliations_total": self._reconciliations,
            "program_reconcile_requeues_total": self._reconcile_requeues,
            "program_recheck_signals_total": self._recheck_signals,
            "program_preflight_suppressed_total": self._preflight_suppressed,
            "program_quarantined_count": quarantined,
        }
