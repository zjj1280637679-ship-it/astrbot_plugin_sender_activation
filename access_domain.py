


from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .domain import (
    MAX_CLOCK_SKEW_SECONDS,
    MAX_TARGETS_PER_OPERATION,
    DomainError,
    normalize_scope,
    normalize_target_ids,
)

ACCESS_SCHEMA_VERSION = 1
MAX_ACCESS_SECONDS = 365 * 24 * 60 * 60
MAX_ACCESS_ROWS = 5_000
AccessKey = tuple[str, str]


@dataclass(frozen=True)
class AccessLimits:
    default_duration_seconds: int = 30 * 24 * 60 * 60
    max_duration_seconds: int = MAX_ACCESS_SECONDS
    max_per_scope: int = 50
    max_total: int = 1_000


@dataclass(frozen=True)
class OperatorGrant:
    scope: str
    operator_id: str
    created_at: float
    expires_at: float
    created_by: str

    @property
    def key(self) -> AccessKey:
        return (self.scope, self.operator_id)

    def as_record(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "operator_id": self.operator_id,
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
class AccessState:
    grants: tuple[OperatorGrant, ...] = ()

    @classmethod
    def empty(cls) -> AccessState:
        return cls()


@dataclass(frozen=True)
class AccessQuarantine:
    index: int
    code: str

    def as_dict(self) -> dict[str, Any]:
        return {"kind": "operator_access", "index": self.index, "code": self.code}


@dataclass(frozen=True)
class AccessLoadResult:
    state: AccessState
    quarantined: tuple[AccessQuarantine, ...] = ()
    expired_count: int = 0


@dataclass(frozen=True)
class AccessMutation:
    state: AccessState
    changed: tuple[dict[str, Any], ...]
    expired_before_ack: frozenset[AccessKey] = frozenset()


def _timestamp(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DomainError("invalid_access_record", f"{field} 必须是有限数字。") from exc
    if not math.isfinite(result) or result < 0:
        raise DomainError("invalid_access_record", f"{field} 必须是有限正数。")
    return result


def _duration(value: Any, limits: AccessLimits) -> int:
    try:
        duration = int(value)
    except (TypeError, ValueError, OverflowError):
        duration = 0
    if duration == 0:
        duration = limits.default_duration_seconds
    if duration <= 0:
        raise DomainError("invalid_access_duration", "授权有效期必须是正整数秒。")
    if duration > limits.max_duration_seconds:
        raise DomainError(
            "access_duration_exceeded",
            f"授权有效期不能超过 {limits.max_duration_seconds} 秒。",
        )
    return duration


def _actor(value: Any) -> str:
    actor = str(value or "").strip()
    if not actor or len(actor) > 128:
        raise DomainError("invalid_actor", "授权创建者引用无效。")
    return actor


def prune_access_state(state: AccessState, now: float) -> AccessState:
    instant = _timestamp(now, "now")
    return AccessState(
        tuple(
            sorted(
                (grant for grant in state.grants if grant.expires_at > instant),
                key=lambda row: row.key,
            )
        )
    )


def access_state_to_document(state: AccessState, now: float) -> dict[str, Any]:
    clean = prune_access_state(state, now)
    return {
        "schema_version": ACCESS_SCHEMA_VERSION,
        "operator_grants": [grant.as_record() for grant in clean.grants],
    }


def _load_grant(raw: Any, now: float) -> OperatorGrant | None:
    if not isinstance(raw, Mapping):
        raise DomainError("invalid_access_record", "授权记录必须是对象。")
    scope = normalize_scope(raw.get("scope"))
    operator_id = normalize_target_ids(
        [raw.get("operator_id")],
        1,
    )[0]
    created_at = _timestamp(raw.get("created_at"), "created_at")
    expires_at = _timestamp(raw.get("expires_at"), "expires_at")
    created_by = _actor(raw.get("created_by"))
    if expires_at <= created_at:
        raise DomainError("invalid_access_record", "授权到期时间无效。")
    if expires_at - created_at > MAX_ACCESS_SECONDS + 1:
        raise DomainError("invalid_access_record", "授权超过绝对期限上限。")
    if (
        created_at > now + MAX_CLOCK_SKEW_SECONDS
        or expires_at > now + MAX_ACCESS_SECONDS + MAX_CLOCK_SKEW_SECONDS
    ):
        raise DomainError("future_access_record", "授权时间戳位于未来。")
    if expires_at <= now:
        return None
    return OperatorGrant(scope, operator_id, created_at, expires_at, created_by)


def load_access_document(document: Mapping[str, Any], now: float) -> AccessLoadResult:
    if not isinstance(document, Mapping):
        raise DomainError("invalid_access_document", "授权状态必须是对象。")
    if document.get("schema_version") != ACCESS_SCHEMA_VERSION:
        raise DomainError("unsupported_access_schema", "授权状态版本不受支持。")
    rows = document.get("operator_grants")
    if not isinstance(rows, list):
        raise DomainError("invalid_access_document", "operator_grants 必须是数组。")
    if len(rows) > MAX_ACCESS_ROWS:
        raise DomainError("access_state_too_large", "授权记录超过安全上限。")

    instant = _timestamp(now, "now")
    grants: dict[AccessKey, OperatorGrant] = {}
    quarantined: list[AccessQuarantine] = []
    expired = 0
    for index, raw in enumerate(rows):
        try:
            grant = _load_grant(raw, instant)
            if grant is None:
                expired += 1
                continue
            if grant.key in grants:
                raise DomainError("duplicate_access_record", "存在重复授权记录。")
            grants[grant.key] = grant
        except DomainError as exc:
            quarantined.append(AccessQuarantine(index, exc.code))
    return AccessLoadResult(
        AccessState(tuple(sorted(grants.values(), key=lambda row: row.key))),
        tuple(quarantined),
        expired,
    )


def grant_operator_access(
    state: AccessState,
    limits: AccessLimits,
    scope: Any,
    operator_ids: Any,
    duration_seconds: Any,
    created_by: str,
    *,
    renew_only: bool,
    now: float,
) -> AccessMutation:
    instant = _timestamp(now, "now")
    clean = prune_access_state(state, instant)
    normalized_scope = normalize_scope(scope)
    operators = normalize_target_ids(operator_ids, MAX_TARGETS_PER_OPERATION)
    duration = _duration(duration_seconds, limits)
    actor = _actor(created_by)
    grants = {grant.key: grant for grant in clean.grants}

    if renew_only:
        missing = [
            operator
            for operator in operators
            if (normalized_scope, operator) not in grants
        ]
        if missing:
            raise DomainError(
                "operator_access_absent",
                f"无法续期不存在的操作员授权: {', '.join(missing)}。",
            )

    new_keys = {
        (normalized_scope, operator)
        for operator in operators
        if (normalized_scope, operator) not in grants
    }
    scope_operators = {
        operator for grant_scope, operator in grants if grant_scope == normalized_scope
    }
    scope_operators.update(operator for _, operator in new_keys)
    if new_keys and len(scope_operators) > limits.max_per_scope:
        raise DomainError(
            "access_scope_capacity_exceeded",
            f"每个作用域最多授权 {limits.max_per_scope} 个操作员。",
        )
    if new_keys and len(set(grants) | new_keys) > limits.max_total:
        raise DomainError(
            "access_total_capacity_exceeded",
            f"插件最多同时保存 {limits.max_total} 个操作员授权。",
        )

    changed: list[dict[str, Any]] = []
    for operator in operators:
        grant = OperatorGrant(
            normalized_scope,
            operator,
            instant,
            instant + duration,
            actor,
        )
        grants[grant.key] = grant
        changed.append(grant.as_public(instant))
    return AccessMutation(
        AccessState(tuple(sorted(grants.values(), key=lambda row: row.key))),
        tuple(changed),
    )


def revoke_operator_access(
    state: AccessState,
    scope: Any,
    operator_ids: Any,
    *,
    now: float,
) -> AccessMutation:
    clean = prune_access_state(state, now)
    normalized_scope = normalize_scope(scope)
    operators = normalize_target_ids(operator_ids, MAX_TARGETS_PER_OPERATION)
    grants = {grant.key: grant for grant in clean.grants}
    changed: list[dict[str, Any]] = []
    for operator in operators:
        if grants.pop((normalized_scope, operator), None) is not None:
            changed.append({"operator_id": operator, "access_removed": True})
    return AccessMutation(
        AccessState(tuple(sorted(grants.values(), key=lambda row: row.key))),
        tuple(changed),
    )


def has_operator_access(
    state: AccessState,
    scope: Any,
    operator_id: Any,
    *,
    now: float,
) -> bool:
    normalized_scope = normalize_scope(scope)
    operator = normalize_target_ids([operator_id], 1)[0]
    return any(
        grant.scope == normalized_scope
        and grant.operator_id == operator
        and grant.expires_at > now
        for grant in state.grants
    )


def public_access_snapshot(
    state: AccessState,
    *,
    now: float,
    scope: Any | None = None,
) -> dict[str, Any]:
    clean = prune_access_state(state, now)
    normalized_scope = normalize_scope(scope) if scope is not None else None
    grants = [
        grant.as_public(now)
        for grant in clean.grants
        if normalized_scope is None or grant.scope == normalized_scope
    ]
    return {
        "schema_version": ACCESS_SCHEMA_VERSION,
        "operator_grants": grants,
        "known_scopes": sorted({grant.scope for grant in clean.grants}),
    }
