


from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

SCHEMA_VERSION = 1
QQ_ID_PATTERN = re.compile(r"^[0-9]{5,20}$")
MAX_SCOPE_LENGTH = 512
MAX_ACTIVATION_SECONDS = 365 * 24 * 60 * 60
MAX_RATE_SECONDS = 24 * 60 * 60
MAX_RATE_WINDOW_SECONDS = 24 * 60 * 60
MAX_RATE_ACTIVATIONS = 1000
MAX_RECOVERY_REPORT_SECONDS = 7 * 24 * 60 * 60
MAX_STATE_ROWS = 10_000
MAX_TARGETS_PER_OPERATION = 100
MAX_CLOCK_SKEW_SECONDS = 5 * 60

LeaseKey = tuple[str, str]
RateSource = Literal["admin", "agent"]
RecoveryReason = Literal["agent_error"]


class DomainError(ValueError):


    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class Limits:
    activation_default_seconds: int = 24 * 60 * 60
    activation_max_seconds: int = MAX_ACTIVATION_SECONDS
    max_targets_per_scope: int = 20
    max_targets_total: int = 500
    rate_max_duration_seconds: int = MAX_RATE_SECONDS
    rate_max_activations: int = MAX_RATE_ACTIVATIONS
    rate_max_window_seconds: float = MAX_RATE_WINDOW_SECONDS
    recovery_report_seconds: int = 24 * 60 * 60


@dataclass(frozen=True)
class ActivationLease:
    scope: str
    target_id: str
    created_at: float
    expires_at: float
    created_by: str

    @property
    def key(self) -> LeaseKey:
        return (self.scope, self.target_id)

    def as_record(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "target_id": self.target_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "created_by": self.created_by,
        }

    def as_public(self, now: float) -> dict[str, Any]:
        return {
            **self.as_record(),
            "remaining_seconds": max(0, math.ceil(self.expires_at - now)),
        }


@dataclass(frozen=True)
class RateLease:
    scope: str
    target_id: str
    max_activations: int
    window_seconds: float
    created_at: float
    expires_at: float
    created_by: str
    source: RateSource

    @property
    def key(self) -> LeaseKey:
        return (self.scope, self.target_id)

    def as_record(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "target_id": self.target_id,
            "max_activations": self.max_activations,
            "window_seconds": self.window_seconds,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "created_by": self.created_by,
            "source": self.source,
        }

    def as_public(self, now: float, recent_count: int = 0) -> dict[str, Any]:
        return {
            **self.as_record(),
            "remaining_seconds": max(0, math.ceil(self.expires_at - now)),
            "recent_count": recent_count,
        }


@dataclass(frozen=True)
class RecoveryTarget:
    target_id: str
    activation_remaining_seconds: int
    rate_max_activations: int | None = None
    rate_window_seconds: float | None = None
    rate_remaining_seconds: int | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "activation_remaining_seconds": self.activation_remaining_seconds,
            "rate_max_activations": self.rate_max_activations,
            "rate_window_seconds": self.rate_window_seconds,
            "rate_remaining_seconds": self.rate_remaining_seconds,
        }


@dataclass(frozen=True)
class RecoveryReport:
    scope: str
    report_id: str
    reason: RecoveryReason
    created_at: float
    expires_at: float
    targets: tuple[RecoveryTarget, ...]

    @property
    def key(self) -> str:
        return self.scope

    def as_record(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "report_id": self.report_id,
            "reason": self.reason,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "targets": [target.as_record() for target in self.targets],
        }

    def as_public(self, now: float) -> dict[str, Any]:
        return {
            **self.as_record(),
            "remaining_seconds": max(0, math.ceil(self.expires_at - now)),
        }


@dataclass(frozen=True)
class RegistryState:


    activations: tuple[ActivationLease, ...] = ()
    rates: tuple[RateLease, ...] = ()
    recovery_reports: tuple[RecoveryReport, ...] = ()

    @classmethod
    def empty(cls) -> RegistryState:
        return cls()


@dataclass(frozen=True)
class QuarantinedRecord:
    kind: str
    index: int
    code: str

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "index": self.index, "code": self.code}


@dataclass(frozen=True)
class LoadResult:
    state: RegistryState
    quarantined: tuple[QuarantinedRecord, ...] = ()
    expired_count: int = 0


@dataclass(frozen=True)
class MutationResult:
    state: RegistryState
    changed: tuple[dict[str, Any], ...]
    reset_counter_keys: frozenset[LeaseKey] = frozenset()
    cascaded_rate_clears: frozenset[LeaseKey] = frozenset()
    expired_before_ack: frozenset[LeaseKey] = frozenset()


@dataclass(frozen=True)
class ActivationDecision:
    admitted: bool
    reason: str
    scope: str
    target_id: str
    rate_limited: bool = False
    retry_after_seconds: float = 0.0
    remaining_in_window: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "reason": self.reason,
            "scope": self.scope,
            "target_id": self.target_id,
            "rate_limited": self.rate_limited,
            "retry_after_seconds": round(self.retry_after_seconds, 3),
            "remaining_in_window": self.remaining_in_window,
        }


def normalize_scope(scope: Any) -> str:
    value = str(scope or "").strip()
    if not value:
        raise DomainError("invalid_scope", "scope 不能为空。")
    if len(value) > MAX_SCOPE_LENGTH:
        raise DomainError("invalid_scope", "scope 过长。")
    if any(ord(char) < 32 or char.isspace() for char in value):
        raise DomainError("invalid_scope", "scope 不能包含空白或控制字符。")
    return value


def normalize_target_id(target_id: Any) -> str:
    value = str(target_id or "").strip()
    if not QQ_ID_PATTERN.fullmatch(value):
        raise DomainError("invalid_target_id", f"无效 QQ ID: {value!r}。")
    return value


def normalize_target_ids(target_ids: Any, maximum: int) -> list[str]:
    if not isinstance(target_ids, (list, tuple, set)):
        raise DomainError("invalid_target_ids", "target_ids 必须是 QQ ID 数组。")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in target_ids:
        value = normalize_target_id(raw)
        if value not in seen:
            seen.add(value)
            normalized.append(value)
    if not normalized:
        raise DomainError("empty_target_ids", "target_ids 至少包含一个 QQ ID。")
    if len(normalized) > maximum:
        raise DomainError(
            "too_many_targets",
            f"一次最多管理 {maximum} 个 QQ ID。",
        )
    return normalized


def normalize_optional_target_ids(target_ids: Any, maximum: int) -> list[str]:
    if target_ids in (None, []):
        return []
    return normalize_target_ids(target_ids, maximum)


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise DomainError(f"invalid_{field}", f"{field} 必须是有限数值。")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DomainError(
            f"invalid_{field}",
            f"{field} 必须是有限数值。",
        ) from exc
    if not math.isfinite(number):
        raise DomainError(f"invalid_{field}", f"{field} 必须是有限数值。")
    return number


def _positive_int(value: Any, field: str, maximum: int) -> int:
    number = _finite_number(value, field)
    if not number.is_integer() or number <= 0 or number > maximum:
        raise DomainError(
            f"invalid_{field}",
            f"{field} 必须是 1 到 {maximum} 的整数。",
        )
    return int(number)


def _positive_float(value: Any, field: str, maximum: float) -> float:
    number = _finite_number(value, field)
    if number <= 0 or number > maximum:
        raise DomainError(
            f"invalid_{field}",
            f"{field} 必须大于 0 且不超过 {maximum:g}。",
        )
    return number


def _timestamp(value: Any, field: str) -> float:
    return _finite_number(value, field)


def _actor(value: Any) -> str:
    actor = str(value or "").strip()
    if not actor or len(actor) > 128:
        raise DomainError("invalid_actor", "租约创建者标识无效。")
    return actor


def _duration(
    value: Any,
    *,
    default: int | None,
    maximum: int,
    field: str = "duration_seconds",
) -> int:
    if value in (None, 0, 0.0, ""):
        if default is None:
            raise DomainError(f"invalid_{field}", f"{field} 必须显式提供。")
        value = default
    return _positive_int(value, field, maximum)


def _activation_map(state: RegistryState) -> dict[LeaseKey, ActivationLease]:
    return {lease.key: lease for lease in state.activations}


def _rate_map(state: RegistryState) -> dict[LeaseKey, RateLease]:
    return {lease.key: lease for lease in state.rates}


def _recovery_map(state: RegistryState) -> dict[str, RecoveryReport]:
    return {report.key: report for report in state.recovery_reports}


def prune_state(state: RegistryState, now: float) -> RegistryState:
    instant = _timestamp(now, "now")
    activations = tuple(
        lease for lease in state.activations if lease.expires_at > instant
    )
    active_keys = {lease.key for lease in activations}
    activation_by_key = {lease.key: lease for lease in activations}
    rates = tuple(
        lease
        for lease in state.rates
        if lease.expires_at > instant
        and lease.key in active_keys
        and lease.expires_at <= activation_by_key[lease.key].expires_at
    )
    reports = tuple(
        report for report in state.recovery_reports if report.expires_at > instant
    )
    return RegistryState(
        tuple(sorted(activations, key=lambda row: row.key)),
        tuple(sorted(rates, key=lambda row: row.key)),
        tuple(sorted(reports, key=lambda row: row.key)),
    )


def state_to_document(state: RegistryState, now: float) -> dict[str, Any]:
    clean = prune_state(state, now)
    return {
        "schema_version": SCHEMA_VERSION,
        "activation_leases": [lease.as_record() for lease in clean.activations],
        "rate_leases": [lease.as_record() for lease in clean.rates],
        "recovery_reports": [report.as_record() for report in clean.recovery_reports],
    }


def _load_activation(raw: Any, now: float) -> ActivationLease | None:
    if not isinstance(raw, Mapping):
        raise DomainError("invalid_activation_record", "激活租约必须是对象。")
    scope = normalize_scope(raw.get("scope"))
    target = normalize_target_id(raw.get("target_id"))
    created_at = _timestamp(raw.get("created_at"), "created_at")
    expires_at = _timestamp(raw.get("expires_at"), "expires_at")
    created_by = _actor(raw.get("created_by"))
    if expires_at <= created_at:
        raise DomainError("invalid_activation_record", "激活租约到期时间无效。")
    if expires_at - created_at > MAX_ACTIVATION_SECONDS + 1:
        raise DomainError("invalid_activation_record", "激活租约超过绝对上限。")
    if (
        created_at > now + MAX_CLOCK_SKEW_SECONDS
        or expires_at > now + MAX_ACTIVATION_SECONDS + MAX_CLOCK_SKEW_SECONDS
    ):
        raise DomainError("future_activation_record", "激活租约时间戳位于未来。")
    if expires_at <= now:
        return None
    return ActivationLease(scope, target, created_at, expires_at, created_by)


def _load_rate(raw: Any, now: float) -> RateLease | None:
    if not isinstance(raw, Mapping):
        raise DomainError("invalid_rate_record", "限频租约必须是对象。")
    scope = normalize_scope(raw.get("scope"))
    target = normalize_target_id(raw.get("target_id"))
    maximum = _positive_int(
        raw.get("max_activations"),
        "max_activations",
        MAX_RATE_ACTIVATIONS,
    )
    window = _positive_float(
        raw.get("window_seconds"),
        "window_seconds",
        MAX_RATE_WINDOW_SECONDS,
    )
    created_at = _timestamp(raw.get("created_at"), "created_at")
    expires_at = _timestamp(raw.get("expires_at"), "expires_at")
    created_by = _actor(raw.get("created_by"))
    source = str(raw.get("source") or "").strip()
    if source not in {"admin", "agent"}:
        raise DomainError("invalid_rate_record", "限频租约 source 无效。")
    if expires_at <= created_at or expires_at - created_at > MAX_RATE_SECONDS + 1:
        raise DomainError("invalid_rate_record", "限频租约到期时间无效。")
    if (
        created_at > now + MAX_CLOCK_SKEW_SECONDS
        or expires_at > now + MAX_RATE_SECONDS + MAX_CLOCK_SKEW_SECONDS
    ):
        raise DomainError("future_rate_record", "限频租约时间戳位于未来。")
    if expires_at <= now:
        return None
    return RateLease(
        scope,
        target,
        maximum,
        window,
        created_at,
        expires_at,
        created_by,
        source,
    )


def _load_recovery_target(raw: Any) -> RecoveryTarget:
    if not isinstance(raw, Mapping):
        raise DomainError("invalid_recovery_target", "恢复目标必须是对象。")
    target_id = normalize_target_id(raw.get("target_id"))
    activation_remaining = _positive_int(
        raw.get("activation_remaining_seconds"),
        "activation_remaining_seconds",
        MAX_ACTIVATION_SECONDS,
    )
    rate_values = (
        raw.get("rate_max_activations"),
        raw.get("rate_window_seconds"),
        raw.get("rate_remaining_seconds"),
    )
    if all(value is None for value in rate_values):
        return RecoveryTarget(target_id, activation_remaining)
    if any(value is None for value in rate_values):
        raise DomainError(
            "invalid_recovery_target",
            "恢复目标的限频字段必须同时存在或同时为空。",
        )
    return RecoveryTarget(
        target_id,
        activation_remaining,
        _positive_int(
            rate_values[0],
            "rate_max_activations",
            MAX_RATE_ACTIVATIONS,
        ),
        _positive_float(
            rate_values[1],
            "rate_window_seconds",
            MAX_RATE_WINDOW_SECONDS,
        ),
        _positive_int(
            rate_values[2],
            "rate_remaining_seconds",
            MAX_RATE_SECONDS,
        ),
    )


def _load_recovery(raw: Any, now: float) -> RecoveryReport | None:
    if not isinstance(raw, Mapping):
        raise DomainError("invalid_recovery_report", "恢复报告必须是对象。")
    scope = normalize_scope(raw.get("scope"))
    report_id = str(raw.get("report_id") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{16}", report_id):
        raise DomainError("invalid_recovery_report", "恢复报告 ID 无效。")
    reason = str(raw.get("reason") or "").strip()
    if reason != "agent_error":
        raise DomainError("invalid_recovery_report", "恢复报告原因无效。")
    created_at = _timestamp(raw.get("created_at"), "created_at")
    expires_at = _timestamp(raw.get("expires_at"), "expires_at")
    if (
        expires_at <= created_at
        or expires_at - created_at > MAX_RECOVERY_REPORT_SECONDS + 1
    ):
        raise DomainError("invalid_recovery_report", "恢复报告到期时间无效。")
    if (
        created_at > now + MAX_CLOCK_SKEW_SECONDS
        or expires_at > now + MAX_RECOVERY_REPORT_SECONDS + MAX_CLOCK_SKEW_SECONDS
    ):
        raise DomainError("future_recovery_report", "恢复报告时间戳位于未来。")
    if expires_at <= now:
        return None
    raw_targets = raw.get("targets")
    if not isinstance(raw_targets, list):
        raise DomainError("invalid_recovery_report", "恢复报告目标必须是数组。")
    targets = tuple(_load_recovery_target(item) for item in raw_targets)
    if not targets or len(targets) > MAX_TARGETS_PER_OPERATION:
        raise DomainError("invalid_recovery_report", "恢复报告目标数量无效。")
    if len({target.target_id for target in targets}) != len(targets):
        raise DomainError("invalid_recovery_report", "恢复报告目标重复。")
    return RecoveryReport(
        scope,
        report_id,
        "agent_error",
        created_at,
        expires_at,
        tuple(sorted(targets, key=lambda row: row.target_id)),
    )


def load_document(document: Mapping[str, Any], now: float) -> LoadResult:


    if not isinstance(document, Mapping):
        raise DomainError("invalid_state_document", "状态文档必须是对象。")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise DomainError("unsupported_state_schema", "状态文档版本不受支持。")
    activation_rows = document.get("activation_leases")
    rate_rows = document.get("rate_leases")
    recovery_rows = document.get("recovery_reports", [])
    if (
        not isinstance(activation_rows, list)
        or not isinstance(rate_rows, list)
        or not isinstance(recovery_rows, list)
    ):
        raise DomainError("invalid_state_document", "状态租约字段必须是数组。")
    if len(activation_rows) + len(rate_rows) + len(recovery_rows) > MAX_STATE_ROWS:
        raise DomainError("state_too_large", "状态文档记录数超过安全上限。")

    instant = _timestamp(now, "now")
    activations: dict[LeaseKey, ActivationLease] = {}
    rates: dict[LeaseKey, RateLease] = {}
    recoveries: dict[str, RecoveryReport] = {}
    quarantined: list[QuarantinedRecord] = []
    expired = 0

    for index, row in enumerate(activation_rows):
        try:
            lease = _load_activation(row, instant)
            if lease is None:
                expired += 1
                continue
            if lease.key in activations:
                raise DomainError(
                    "duplicate_activation_record",
                    "状态中存在重复激活租约。",
                )
            activations[lease.key] = lease
        except DomainError as exc:
            quarantined.append(QuarantinedRecord("activation", index, exc.code))

    for index, row in enumerate(rate_rows):
        try:
            lease = _load_rate(row, instant)
            if lease is None:
                expired += 1
                continue
            activation = activations.get(lease.key)
            if activation is None:
                raise DomainError(
                    "orphan_rate_record",
                    "限频租约没有对应的激活租约。",
                )
            if lease.expires_at > activation.expires_at:
                raise DomainError(
                    "rate_outlives_activation",
                    "限频租约不能晚于激活租约到期。",
                )
            if lease.key in rates:
                raise DomainError(
                    "duplicate_rate_record",
                    "状态中存在重复限频租约。",
                )
            rates[lease.key] = lease
        except DomainError as exc:
            quarantined.append(QuarantinedRecord("rate", index, exc.code))

    for index, row in enumerate(recovery_rows):
        try:
            report = _load_recovery(row, instant)
            if report is None:
                expired += 1
                continue
            if report.key in recoveries:
                raise DomainError(
                    "duplicate_recovery_report",
                    "状态中存在重复恢复报告。",
                )
            recoveries[report.key] = report
        except DomainError as exc:
            quarantined.append(QuarantinedRecord("recovery", index, exc.code))

    return LoadResult(
        RegistryState(
            tuple(sorted(activations.values(), key=lambda row: row.key)),
            tuple(sorted(rates.values(), key=lambda row: row.key)),
            tuple(sorted(recoveries.values(), key=lambda row: row.key)),
        ),
        tuple(quarantined),
        expired,
    )


def enable_activation(
    state: RegistryState,
    limits: Limits,
    scope: Any,
    target_ids: Any,
    duration_seconds: Any,
    created_by: str,
    *,
    renew_only: bool,
    now: float,
) -> MutationResult:
    instant = _timestamp(now, "now")
    clean = prune_state(state, instant)
    normalized_scope = normalize_scope(scope)
    targets = normalize_target_ids(target_ids, MAX_TARGETS_PER_OPERATION)
    duration = _duration(
        duration_seconds,
        default=limits.activation_default_seconds,
        maximum=limits.activation_max_seconds,
    )
    actor = _actor(created_by)
    activations = _activation_map(clean)
    rates = _rate_map(clean)

    if renew_only:
        missing = [
            target
            for target in targets
            if (normalized_scope, target) not in activations
        ]
        if missing:
            raise DomainError(
                "activation_absent",
                f"无法续期不存在的激活租约: {', '.join(missing)}。",
            )

    new_keys = {
        (normalized_scope, target)
        for target in targets
        if (normalized_scope, target) not in activations
    }
    projected_scope = {
        target
        for existing_scope, target in activations
        if existing_scope == normalized_scope
    }
    projected_scope.update(target for _, target in new_keys)
    if new_keys and len(projected_scope) > limits.max_targets_per_scope:
        raise DomainError(
            "scope_capacity_exceeded",
            f"每个 scope 最多激活 {limits.max_targets_per_scope} 个 ID。",
        )
    projected_total = set(activations)
    projected_total.update(new_keys)
    if new_keys and len(projected_total) > limits.max_targets_total:
        raise DomainError(
            "total_capacity_exceeded",
            f"整个插件最多同时激活 {limits.max_targets_total} 个目标。",
        )

    changed: list[dict[str, Any]] = []
    reset_keys: set[LeaseKey] = set()
    cascaded_rate_clears: set[LeaseKey] = set()
    for target in targets:
        key = (normalized_scope, target)
        lease = ActivationLease(
            normalized_scope,
            target,
            instant,
            instant + duration,
            actor,
        )
        activations[key] = lease
        rate = rates.get(key)
        rate_removed = False
        if rate and rate.expires_at > lease.expires_at:
            rates.pop(key, None)
            cascaded_rate_clears.add(key)
            rate_removed = True
        change = lease.as_public(instant)
        if rate_removed:
            change["rate_removed"] = True
        changed.append(change)
        reset_keys.add(key)

    return MutationResult(
        RegistryState(
            tuple(sorted(activations.values(), key=lambda row: row.key)),
            tuple(sorted(rates.values(), key=lambda row: row.key)),
            clean.recovery_reports,
        ),
        tuple(changed),
        frozenset(reset_keys),
        frozenset(cascaded_rate_clears),
    )


def disable_activation(
    state: RegistryState,
    limits: Limits,
    scope: Any,
    target_ids: Any,
    *,
    now: float,
) -> MutationResult:
    instant = _timestamp(now, "now")
    clean = prune_state(state, instant)
    normalized_scope = normalize_scope(scope)
    targets = normalize_target_ids(target_ids, MAX_TARGETS_PER_OPERATION)
    activations = _activation_map(clean)
    rates = _rate_map(clean)
    changed: list[dict[str, Any]] = []
    reset_keys: set[LeaseKey] = set()
    cascaded_rate_clears: set[LeaseKey] = set()

    for target in targets:
        key = (normalized_scope, target)
        removed_activation = activations.pop(key, None)
        removed_rate = rates.pop(key, None)
        if removed_rate is not None:
            cascaded_rate_clears.add(key)
        if removed_activation or removed_rate:
            changed.append(
                {
                    "target_id": target,
                    "activation_removed": removed_activation is not None,
                    "rate_removed": removed_rate is not None,
                }
            )
        reset_keys.add(key)

    return MutationResult(
        RegistryState(
            tuple(sorted(activations.values(), key=lambda row: row.key)),
            tuple(sorted(rates.values(), key=lambda row: row.key)),
            clean.recovery_reports,
        ),
        tuple(changed),
        frozenset(reset_keys),
        frozenset(cascaded_rate_clears),
    )


def set_rate(
    state: RegistryState,
    limits: Limits,
    scope: Any,
    target_ids: Any,
    max_activations: Any,
    window_seconds: Any,
    duration_seconds: Any,
    created_by: str,
    source: RateSource,
    *,
    now: float,
) -> MutationResult:
    instant = _timestamp(now, "now")
    clean = prune_state(state, instant)
    normalized_scope = normalize_scope(scope)
    targets = normalize_target_ids(target_ids, MAX_TARGETS_PER_OPERATION)
    maximum = _positive_int(
        max_activations,
        "max_activations",
        limits.rate_max_activations,
    )
    window = _positive_float(
        window_seconds,
        "window_seconds",
        limits.rate_max_window_seconds,
    )
    if source not in {"admin", "agent"}:
        raise DomainError("invalid_source", "source 必须是 admin 或 agent。")
    duration = _duration(
        duration_seconds,
        default=None,
        maximum=limits.rate_max_duration_seconds,
    )
    actor = _actor(created_by)
    activations = _activation_map(clean)
    rates = _rate_map(clean)

    absent = [
        target for target in targets if (normalized_scope, target) not in activations
    ]
    if absent:
        raise DomainError(
            "activation_required",
            f"设置限频前必须先建立激活租约: {', '.join(absent)}。",
        )
    changed: list[dict[str, Any]] = []
    reset_keys: set[LeaseKey] = set()
    for target in targets:
        key = (normalized_scope, target)
        activation = activations[key]
        requested_expires_at = instant + duration
        expires_at = min(requested_expires_at, activation.expires_at)
        lease = RateLease(
            normalized_scope,
            target,
            maximum,
            window,
            instant,
            expires_at,
            actor,
            source,
        )
        rates[key] = lease
        public = lease.as_public(instant)
        public.update(
            {
                "requested_duration_seconds": duration,
                "effective_duration_seconds": max(
                    0,
                    math.ceil(expires_at - instant),
                ),
                "clamped_to_activation": expires_at < requested_expires_at,
            }
        )
        changed.append(public)
        reset_keys.add(key)

    return MutationResult(
        RegistryState(
            tuple(sorted(activations.values(), key=lambda row: row.key)),
            tuple(sorted(rates.values(), key=lambda row: row.key)),
            clean.recovery_reports,
        ),
        tuple(changed),
        frozenset(reset_keys),
    )


def clear_rate(
    state: RegistryState,
    limits: Limits,
    scope: Any,
    target_ids: Any,
    *,
    source: RateSource,
    now: float,
) -> MutationResult:
    instant = _timestamp(now, "now")
    clean = prune_state(state, instant)
    normalized_scope = normalize_scope(scope)
    targets = normalize_target_ids(target_ids, MAX_TARGETS_PER_OPERATION)
    rates = _rate_map(clean)
    if source not in {"admin", "agent"}:
        raise DomainError("invalid_source", "source 必须是 admin 或 agent。")
    changed: list[dict[str, Any]] = []
    reset_keys: set[LeaseKey] = set()
    for target in targets:
        key = (normalized_scope, target)
        if rates.pop(key, None) is not None:
            changed.append({"target_id": target, "rate_removed": True})
        reset_keys.add(key)

    return MutationResult(
        RegistryState(
            clean.activations,
            tuple(sorted(rates.values(), key=lambda row: row.key)),
            clean.recovery_reports,
        ),
        tuple(changed),
        frozenset(reset_keys),
    )


def restore_scope_to_native(
    state: RegistryState,
    limits: Limits,
    scope: Any,
    *,
    reason: RecoveryReason,
    now: float,
) -> MutationResult:


    instant = _timestamp(now, "now")
    clean = prune_state(state, instant)
    normalized_scope = normalize_scope(scope)
    if reason != "agent_error":
        raise DomainError("invalid_recovery_reason", "恢复原因无效。")
    activations = _activation_map(clean)
    rates = _rate_map(clean)
    affected = [lease for lease in clean.activations if lease.scope == normalized_scope]
    if not affected:
        return MutationResult(clean, ())

    targets: list[RecoveryTarget] = []
    reset_keys: set[LeaseKey] = set()
    changes: list[dict[str, Any]] = []
    for activation in affected:
        key = activation.key
        rate = rates.get(key)
        activation_remaining = max(
            1,
            min(
                MAX_ACTIVATION_SECONDS,
                math.ceil(activation.expires_at - instant),
            ),
        )
        if rate is None:
            recovery_target = RecoveryTarget(
                activation.target_id,
                activation_remaining,
            )
        else:
            recovery_target = RecoveryTarget(
                activation.target_id,
                activation_remaining,
                rate.max_activations,
                rate.window_seconds,
                max(
                    1,
                    min(
                        MAX_RATE_SECONDS,
                        math.ceil(rate.expires_at - instant),
                    ),
                ),
            )
        targets.append(recovery_target)
        activations.pop(key, None)
        rates.pop(key, None)
        reset_keys.add(key)
        changes.append(
            {
                "target_id": activation.target_id,
                "activation_removed": True,
                "rate_removed": rate is not None,
            }
        )

    report_material = "|".join(
        [
            normalized_scope,
            f"{instant:.6f}",
            reason,
            *sorted(target.target_id for target in targets),
        ]
    )
    report_id = hashlib.sha256(report_material.encode("utf-8")).hexdigest()[:16]
    report = RecoveryReport(
        normalized_scope,
        report_id,
        reason,
        instant,
        instant + limits.recovery_report_seconds,
        tuple(sorted(targets, key=lambda row: row.target_id)),
    )
    recoveries = _recovery_map(clean)
    recoveries[normalized_scope] = report
    return MutationResult(
        RegistryState(
            tuple(sorted(activations.values(), key=lambda row: row.key)),
            tuple(sorted(rates.values(), key=lambda row: row.key)),
            tuple(sorted(recoveries.values(), key=lambda row: row.key)),
        ),
        tuple(changes),
        frozenset(reset_keys),
        frozenset(reset_keys),
    )


def acknowledge_recovery(
    state: RegistryState,
    scope: Any,
    report_id: Any,
    *,
    now: float,
) -> MutationResult:
    instant = _timestamp(now, "now")
    clean = prune_state(state, instant)
    normalized_scope = normalize_scope(scope)
    expected_id = str(report_id or "").strip()
    recoveries = _recovery_map(clean)
    report = recoveries.get(normalized_scope)
    if report is None or report.report_id != expected_id:
        return MutationResult(clean, ())
    recoveries.pop(normalized_scope, None)
    return MutationResult(
        RegistryState(
            clean.activations,
            clean.rates,
            tuple(sorted(recoveries.values(), key=lambda row: row.key)),
        ),
        (
            {
                "scope": normalized_scope,
                "report_id": expected_id,
                "recovery_acknowledged": True,
            },
        ),
    )


def active_leases(
    state: RegistryState,
    scope: Any,
    target_id: Any,
    now: float,
) -> tuple[ActivationLease | None, RateLease | None]:
    normalized_scope = normalize_scope(scope)
    target = normalize_target_id(target_id)
    key = (normalized_scope, target)
    activation = _activation_map(state).get(key)
    if activation is None or activation.expires_at <= now:
        return None, None
    rate = _rate_map(state).get(key)
    if rate is None or rate.expires_at <= now:
        return activation, None
    return activation, rate


def public_snapshot(
    state: RegistryState,
    *,
    now: float,
    scope: Any | None = None,
    target_ids: Iterable[Any] | None = None,
    recent_counts: Mapping[LeaseKey, int] | None = None,
) -> dict[str, Any]:
    clean = prune_state(state, now)
    normalized_scope = normalize_scope(scope) if scope is not None else None
    target_filter: set[str] | None = None
    if target_ids is not None:
        values = list(target_ids)
        if values:
            target_filter = set(normalize_target_ids(values, max(1, len(values))))
    counts = recent_counts or {}
    activations = [
        lease.as_public(now)
        for lease in clean.activations
        if (normalized_scope is None or lease.scope == normalized_scope)
        and (target_filter is None or lease.target_id in target_filter)
    ]
    rates = [
        lease.as_public(now, counts.get(lease.key, 0))
        for lease in clean.rates
        if (normalized_scope is None or lease.scope == normalized_scope)
        and (target_filter is None or lease.target_id in target_filter)
    ]
    recovery_reports: list[dict[str, Any]] = []
    for report in clean.recovery_reports:
        if normalized_scope is not None and report.scope != normalized_scope:
            continue
        public = report.as_public(now)
        if target_filter is not None:
            public["targets"] = [
                target
                for target in public["targets"]
                if target["target_id"] in target_filter
            ]
            if not public["targets"]:
                continue
        recovery_reports.append(public)
    known_scopes = {lease.scope for lease in clean.activations} | {
        report.scope for report in clean.recovery_reports
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "activation_leases": activations,
        "rate_leases": rates,
        "recovery_reports": recovery_reports,
        "known_scopes": sorted(known_scopes),
    }
