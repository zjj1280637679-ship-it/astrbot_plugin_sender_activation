from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .domain import DomainError, normalize_scope, normalize_target_ids

ATTENTION_SCHEMA_VERSION = 1
MAX_IGNORE_TARGETS_PER_OPERATION = 100


@dataclass(frozen=True)
class IgnoreLimits:
    default_ignore_seconds: int = 10 * 60
    max_ignore_seconds: int = 24 * 60 * 60
    default_policy_seconds: int = 60 * 60
    max_policy_seconds: int = 7 * 24 * 60 * 60
    default_trigger_count: int = 1
    max_trigger_count: int = 10_000
    default_trigger_window_seconds: float = 0.0
    max_trigger_window_seconds: float = 24 * 60 * 60
    max_per_scope: int = 50
    max_total: int = 1000


@dataclass(frozen=True)
class IgnorePolicy:
    scope: str
    target_id: str
    trigger_count: int
    trigger_window_seconds: float
    ignore_duration_seconds: int
    created_at: float
    expires_at: float
    created_by: str

    @property
    def key(self) -> tuple[str, str]:
        return self.scope, self.target_id

    def as_record(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "target_id": self.target_id,
            "trigger_count": self.trigger_count,
            "trigger_window_seconds": self.trigger_window_seconds,
            "ignore_duration_seconds": self.ignore_duration_seconds,
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
class IgnoreState:
    policies: tuple[IgnorePolicy, ...] = ()

    @classmethod
    def empty(cls) -> "IgnoreState":
        return cls()


@dataclass(frozen=True)
class IgnoreLoadResult:
    state: IgnoreState
    quarantined: tuple[dict[str, Any], ...] = ()
    expired_count: int = 0


@dataclass(frozen=True)
class IgnoreMutation:
    state: IgnoreState
    changed: tuple[dict[str, Any], ...]


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise DomainError(f"invalid_{field}", f"{field} 必须是有限数值。")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DomainError(f"invalid_{field}", f"{field} 必须是有限数值。") from exc
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


def _nonnegative_float(value: Any, field: str, maximum: float) -> float:
    number = _finite_number(value, field)
    if number < 0 or number > maximum:
        raise DomainError(
            f"invalid_{field}",
            f"{field} 必须在 0 到 {maximum:g} 之间。",
        )
    return number


def _actor(value: Any) -> str:
    actor = str(value or "").strip()
    if not actor or len(actor) > 128:
        raise DomainError("invalid_actor", "策略创建者标识无效。")
    return actor


def _duration(
    value: Any,
    *,
    default: int,
    maximum: int,
    field: str,
) -> int:
    if value in (None, 0, 0.0, ""):
        value = default
    return _positive_int(value, field, maximum)


def normalize_ignore_parameters(
    *,
    limits: IgnoreLimits,
    trigger_count: Any,
    trigger_window_seconds: Any,
    ignore_duration_seconds: Any,
    policy_seconds: Any,
) -> tuple[int, float, int, int]:
    count = _positive_int(
        limits.default_trigger_count if trigger_count in (None, 0, 0.0, "") else trigger_count,
        "trigger_count",
        limits.max_trigger_count,
    )
    if trigger_window_seconds in (None, ""):
        trigger_window_seconds = limits.default_trigger_window_seconds
    window = _nonnegative_float(
        trigger_window_seconds,
        "trigger_window_seconds",
        limits.max_trigger_window_seconds,
    )
    ignore_seconds = _duration(
        ignore_duration_seconds,
        default=limits.default_ignore_seconds,
        maximum=limits.max_ignore_seconds,
        field="ignore_duration_seconds",
    )
    policy_duration = _duration(
        policy_seconds,
        default=limits.default_policy_seconds,
        maximum=limits.max_policy_seconds,
        field="policy_seconds",
    )
    return count, window, ignore_seconds, policy_duration


def prune_ignore_state(state: IgnoreState, now: float) -> IgnoreState:
    current = _finite_number(now, "now")
    return IgnoreState(
        tuple(
            sorted(
                (policy for policy in state.policies if policy.expires_at > current),
                key=lambda policy: policy.key,
            )
        )
    )


def ignore_state_to_document(state: IgnoreState, now: float) -> dict[str, Any]:
    clean = prune_ignore_state(state, now)
    return {
        "schema_version": ATTENTION_SCHEMA_VERSION,
        "ignore_policies": [policy.as_record() for policy in clean.policies],
    }


def _load_policy(raw: Any, now: float) -> IgnorePolicy | None:
    if not isinstance(raw, Mapping):
        raise DomainError("invalid_ignore_policy", "忽略策略必须是对象。")
    scope = normalize_scope(raw.get("scope"))
    target_id = normalize_target_ids([raw.get("target_id")], 1)[0]
    trigger_count = _positive_int(raw.get("trigger_count"), "trigger_count", 10**9)
    trigger_window_seconds = _nonnegative_float(
        raw.get("trigger_window_seconds"),
        "trigger_window_seconds",
        10**12,
    )
    ignore_duration_seconds = _positive_int(
        raw.get("ignore_duration_seconds"),
        "ignore_duration_seconds",
        10**9,
    )
    created_at = _finite_number(raw.get("created_at"), "created_at")
    expires_at = _finite_number(raw.get("expires_at"), "expires_at")
    created_by = _actor(raw.get("created_by"))
    if expires_at <= created_at:
        raise DomainError("invalid_ignore_policy", "忽略策略到期时间无效。")
    if expires_at <= now:
        return None
    return IgnorePolicy(
        scope=scope,
        target_id=target_id,
        trigger_count=trigger_count,
        trigger_window_seconds=trigger_window_seconds,
        ignore_duration_seconds=ignore_duration_seconds,
        created_at=created_at,
        expires_at=expires_at,
        created_by=created_by,
    )


def load_ignore_document(document: Any, now: float) -> IgnoreLoadResult:
    if document is None:
        return IgnoreLoadResult(IgnoreState.empty())
    if not isinstance(document, Mapping):
        raise DomainError("invalid_attention_state", "注意力策略状态必须是对象。")
    if document.get("schema_version") != ATTENTION_SCHEMA_VERSION:
        raise DomainError("unsupported_attention_schema", "注意力策略状态版本不受支持。")
    rows = document.get("ignore_policies", [])
    if not isinstance(rows, list):
        raise DomainError("invalid_attention_state", "ignore_policies 必须是数组。")
    policies: dict[tuple[str, str], IgnorePolicy] = {}
    quarantined: list[dict[str, Any]] = []
    expired = 0
    for index, raw in enumerate(rows):
        try:
            policy = _load_policy(raw, now)
            if policy is None:
                expired += 1
                continue
            if policy.key in policies:
                raise DomainError("duplicate_ignore_policy", "同一对象存在重复忽略策略。")
            policies[policy.key] = policy
        except DomainError as exc:
            quarantined.append(
                {"kind": "ignore_policy", "index": index, "code": exc.code}
            )
    return IgnoreLoadResult(
        IgnoreState(tuple(sorted(policies.values(), key=lambda policy: policy.key))),
        tuple(quarantined),
        expired,
    )


def set_ignore_policies(
    state: IgnoreState,
    limits: IgnoreLimits,
    scope: Any,
    target_ids: Any,
    *,
    trigger_count: Any,
    trigger_window_seconds: Any,
    ignore_duration_seconds: Any,
    policy_seconds: Any,
    created_by: Any,
    now: float,
) -> IgnoreMutation:
    normalized_scope = normalize_scope(scope)
    targets = normalize_target_ids(target_ids, MAX_IGNORE_TARGETS_PER_OPERATION)
    count, window, ignore_seconds, policy_duration = normalize_ignore_parameters(
        limits=limits,
        trigger_count=trigger_count,
        trigger_window_seconds=trigger_window_seconds,
        ignore_duration_seconds=ignore_duration_seconds,
        policy_seconds=policy_seconds,
    )
    clean = prune_ignore_state(state, now)
    policies = {policy.key: policy for policy in clean.policies}
    new_keys = [(normalized_scope, target) for target in targets if (normalized_scope, target) not in policies]
    scope_count = sum(1 for key in policies if key[0] == normalized_scope)
    if scope_count + len(new_keys) > limits.max_per_scope:
        raise DomainError("ignore_scope_capacity_exceeded", "当前作用域忽略策略数量达到配置上限。")
    if len(policies) + len(new_keys) > limits.max_total:
        raise DomainError("ignore_total_capacity_exceeded", "全局忽略策略数量达到配置上限。")
    creator = _actor(created_by)
    changes: list[dict[str, Any]] = []
    expires_at = float(now) + policy_duration
    for target in targets:
        policy = IgnorePolicy(
            scope=normalized_scope,
            target_id=target,
            trigger_count=count,
            trigger_window_seconds=window,
            ignore_duration_seconds=ignore_seconds,
            created_at=float(now),
            expires_at=expires_at,
            created_by=creator,
        )
        previous = policies.get(policy.key)
        policies[policy.key] = policy
        if previous != policy:
            changes.append(policy.as_record())
    return IgnoreMutation(
        IgnoreState(tuple(sorted(policies.values(), key=lambda policy: policy.key))),
        tuple(changes),
    )


def clear_ignore_policies(
    state: IgnoreState,
    scope: Any,
    target_ids: Any,
    *,
    now: float,
) -> IgnoreMutation:
    normalized_scope = normalize_scope(scope)
    targets = normalize_target_ids(target_ids, MAX_IGNORE_TARGETS_PER_OPERATION)
    clean = prune_ignore_state(state, now)
    policies = {policy.key: policy for policy in clean.policies}
    changes: list[dict[str, Any]] = []
    for target in targets:
        key = normalized_scope, target
        previous = policies.pop(key, None)
        if previous is not None:
            changes.append(previous.as_record())
    return IgnoreMutation(
        IgnoreState(tuple(sorted(policies.values(), key=lambda policy: policy.key))),
        tuple(changes),
    )


def public_ignore_snapshot(
    state: IgnoreState,
    *,
    now: float,
    scope: str | None = None,
    target_ids: list[str] | None = None,
) -> dict[str, Any]:
    clean = prune_ignore_state(state, now)
    targets = set(target_ids or [])
    rows = [
        policy.as_public(now)
        for policy in clean.policies
        if (scope is None or policy.scope == scope)
        and (not targets or policy.target_id in targets)
    ]
    return {
        "ignore_policies": rows,
        "known_scopes": sorted({policy.scope for policy in clean.policies}),
    }
