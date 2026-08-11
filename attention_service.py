from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .attention_domain import (
    IgnoreLimits,
    IgnoreMutation,
    IgnorePolicy,
    IgnoreState,
    clear_ignore_policies,
    ignore_state_to_document,
    load_ignore_document,
    prune_ignore_state,
    public_ignore_snapshot,
    set_ignore_policies,
)
from .domain import DomainError, normalize_optional_target_ids, normalize_scope, normalize_target_ids
from .storage import AstrBotKVStateStore, CommitIndeterminateError

ATTENTION_STATE_KEY = "sender_attention_ignore_state_v1"
ATTENTION_BACKUP_KEY = "sender_attention_ignore_state_v1_lkg"
IGNORE_EFFECT_CONTRACT = "finite_object_scoped_pre_llm_attention_suppression"

IgnoreKey = tuple[str, str]


@dataclass(frozen=True)
class IgnoreDecision:
    matched: bool
    ignored: bool
    triggered: bool
    reason: str
    scope: str
    target_id: str
    observed_count: int = 0
    trigger_count: int = 0
    trigger_window_seconds: float = 0.0
    retry_after_seconds: float = 0.0
    policy_remaining_seconds: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "ignored": self.ignored,
            "triggered": self.triggered,
            "reason": self.reason,
            "scope": self.scope,
            "target_id": self.target_id,
            "observed_count": self.observed_count,
            "trigger_count": self.trigger_count,
            "trigger_window_seconds": self.trigger_window_seconds,
            "retry_after_seconds": round(max(0.0, self.retry_after_seconds), 3),
            "policy_remaining_seconds": max(0, self.policy_remaining_seconds),
        }


@dataclass(slots=True)
class _GuardSlot:
    events: deque[float]
    cumulative_count: int = 0
    blocked_until: float = 0.0


class ObjectAttentionGuard:
    """Fast exact-object guard.

    Runtime counters intentionally stay in memory: a restart is fail-open rather than
    accidentally preserving a stale ignore. Persistent policy intent is stored by
    AttentionIgnoreService, while event-rate bookkeeping never writes storage per event.
    """

    def __init__(
        self,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = monotonic_clock
        self._lock = threading.RLock()
        self._slots: dict[IgnoreKey, _GuardSlot] = {}
        self._metrics: Counter[str] = Counter()

    def clear(self, keys: set[IgnoreKey] | frozenset[IgnoreKey]) -> None:
        with self._lock:
            for key in keys:
                self._slots.pop(key, None)

    def clear_all(self) -> None:
        with self._lock:
            self._slots.clear()

    def evaluate(
        self,
        policy: IgnorePolicy,
        *,
        wall_now: float,
    ) -> IgnoreDecision:
        key = policy.key
        monotonic_now = float(self._clock())
        policy_remaining = max(0.0, float(policy.expires_at) - float(wall_now))
        if policy_remaining <= 0:
            self.clear({key})
            return IgnoreDecision(
                matched=False,
                ignored=False,
                triggered=False,
                reason="policy_expired",
                scope=policy.scope,
                target_id=policy.target_id,
            )

        with self._lock:
            slot = self._slots.get(key)
            if slot is None:
                slot = _GuardSlot(deque())
                self._slots[key] = slot

            if slot.blocked_until > monotonic_now:
                retry_after = min(
                    slot.blocked_until - monotonic_now,
                    policy_remaining,
                )
                self._metrics["ignored_active"] += 1
                return IgnoreDecision(
                    matched=True,
                    ignored=True,
                    triggered=False,
                    reason="ignore_active",
                    scope=policy.scope,
                    target_id=policy.target_id,
                    observed_count=0,
                    trigger_count=policy.trigger_count,
                    trigger_window_seconds=policy.trigger_window_seconds,
                    retry_after_seconds=retry_after,
                    policy_remaining_seconds=math_ceil(policy_remaining),
                )

            slot.blocked_until = 0.0
            if policy.trigger_window_seconds > 0:
                threshold = monotonic_now - policy.trigger_window_seconds
                while slot.events and slot.events[0] <= threshold:
                    slot.events.popleft()
                slot.events.append(monotonic_now)
                observed = len(slot.events)
            else:
                # A pure lifetime counter needs no per-event timestamps. Keeping a deque
                # here would let a high configured threshold become a memory amplifier.
                slot.cumulative_count += 1
                observed = slot.cumulative_count
            if observed < policy.trigger_count:
                self._metrics["observed_below_threshold"] += 1
                return IgnoreDecision(
                    matched=True,
                    ignored=False,
                    triggered=False,
                    reason="below_threshold",
                    scope=policy.scope,
                    target_id=policy.target_id,
                    observed_count=observed,
                    trigger_count=policy.trigger_count,
                    trigger_window_seconds=policy.trigger_window_seconds,
                    policy_remaining_seconds=math_ceil(policy_remaining),
                )

            # The threshold-crossing event itself is suppressed. This is important for
            # fast @ floods: waiting for the following event would waste one more LLM turn.
            ignore_for = min(float(policy.ignore_duration_seconds), policy_remaining)
            slot.blocked_until = monotonic_now + max(0.0, ignore_for)
            slot.events.clear()
            slot.cumulative_count = 0
            self._metrics["ignore_triggered"] += 1
            return IgnoreDecision(
                matched=True,
                ignored=True,
                triggered=True,
                reason="threshold_triggered",
                scope=policy.scope,
                target_id=policy.target_id,
                observed_count=observed,
                trigger_count=policy.trigger_count,
                trigger_window_seconds=policy.trigger_window_seconds,
                retry_after_seconds=ignore_for,
                policy_remaining_seconds=math_ceil(policy_remaining),
            )

    def health(self) -> dict[str, Any]:
        with self._lock:
            now = float(self._clock())
            active_blocks = sum(slot.blocked_until > now for slot in self._slots.values())
            event_samples = sum(len(slot.events) for slot in self._slots.values())
            cumulative_slots = sum(slot.cumulative_count > 0 for slot in self._slots.values())
            return {
                "ignore_guard_slots": len(self._slots),
                "ignore_guard_active_blocks": active_blocks,
                "ignore_guard_event_samples": event_samples,
                "ignore_guard_cumulative_slots": cumulative_slots,
                "ignore_guard_metrics": dict(self._metrics),
            }


def math_ceil(value: float) -> int:
    # Kept local to avoid a hot-path import through the domain module.
    integer = int(value)
    return integer if value <= integer else integer + 1


class AttentionIgnoreService:
    def __init__(
        self,
        limits: IgnoreLimits,
        store: AstrBotKVStateStore,
        *,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits
        self._store = store
        self._wall_clock = wall_clock
        self._state = IgnoreState.empty()
        self._policy_map: dict[IgnoreKey, IgnorePolicy] = {}
        self._mutation_lock = asyncio.Lock()
        self._guard = ObjectAttentionGuard(monotonic_clock=monotonic_clock)
        self._metrics_lock = threading.Lock()
        self._metrics: Counter[str] = Counter()
        self._lifecycle_active = False
        self.storage_ready = False
        self.storage_write_healthy = False
        self.loaded_from = "none"
        self.last_error_code: str | None = None
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

    def _rebuild_policy_index(self) -> None:
        self._policy_map = {policy.key: policy for policy in self._state.policies}

    async def initialize(self) -> None:
        self._lifecycle_active = False
        self.storage_ready = False
        self.storage_write_healthy = False
        self._guard.clear_all()
        self._policy_map.clear()
        try:
            stored = await self._store.load()
        except Exception as exc:
            self.last_error_code = f"attention_storage_load_failed:{type(exc).__name__}"
            return

        selected = None
        selected_name = "empty"
        primary_error: DomainError | None = None
        backup_error: DomainError | None = None
        now = float(self._wall_clock())
        if stored.primary_error is None and stored.primary is not None:
            try:
                selected = load_ignore_document(stored.primary, now)
                selected_name = "primary"
            except DomainError as exc:
                primary_error = exc
        if selected is None and stored.backup_error is None and stored.backup is not None:
            try:
                selected = load_ignore_document(stored.backup, now)
                selected_name = "backup"
            except DomainError as exc:
                backup_error = exc
        both_empty = (
            stored.primary_error is None
            and stored.backup_error is None
            and stored.primary is None
            and stored.backup is None
        )
        if selected is None and not both_empty:
            if stored.primary_error:
                self.last_error_code = f"attention_primary_read_failed:{stored.primary_error}"
            elif stored.backup_error:
                self.last_error_code = f"attention_backup_read_failed:{stored.backup_error}"
            elif primary_error is not None:
                self.last_error_code = primary_error.code
            elif backup_error is not None:
                self.last_error_code = backup_error.code
            else:
                self.last_error_code = "invalid_attention_state"
            return
        if selected is None:
            selected = load_ignore_document(None, now)

        document = ignore_state_to_document(selected.state, now)
        try:
            await self._store.commit(document, document)
        except Exception as exc:
            self.last_error_code = f"attention_storage_initialize_failed:{type(exc).__name__}"
            return

        self._state = selected.state
        self._rebuild_policy_index()
        self.loaded_from = selected_name
        self.quarantined = selected.quarantined
        self.expired_on_load = selected.expired_count
        self.last_commit_at = now
        self.last_error_code = None
        self.storage_ready = True
        self.storage_write_healthy = True
        self._lifecycle_active = True

    async def terminate(self) -> None:
        self._lifecycle_active = False
        self.storage_ready = False
        async with self._mutation_lock:
            self.storage_ready = False
            self.storage_write_healthy = False
            self._guard.clear_all()
            self._policy_map.clear()

    def _require_storage(self) -> None:
        if not self._lifecycle_active or not self.storage_ready:
            raise DomainError(
                "attention_storage_unavailable",
                "忽略策略存储当前不可用；为避免误伤，前置忽略保持关闭。",
            )

    async def _commit_mutation(self, operation: Callable[[IgnoreState, float], IgnoreMutation]) -> IgnoreMutation:
        self._require_storage()
        async with self._mutation_lock:
            self._require_storage()
            before = self._state
            now = float(self._wall_clock())
            result = operation(before, now)
            previous_document = ignore_state_to_document(before, now)
            candidate_document = ignore_state_to_document(result.state, now)
            try:
                await self._store.commit(previous_document, candidate_document)
            except CommitIndeterminateError as exc:
                self.storage_ready = False
                self.storage_write_healthy = False
                self.last_error_code = "attention_commit_indeterminate"
                self._guard.clear_all()
                raise DomainError(
                    "attention_commit_indeterminate",
                    "忽略策略写入结果无法确认；前置忽略已转为 fail-open，需重载核对。",
                ) from exc
            except Exception as exc:
                self.storage_write_healthy = False
                self.last_error_code = "attention_storage_write_failed"
                raise DomainError(
                    "attention_storage_write_failed",
                    "忽略策略写入失败；运行策略未改变。",
                ) from exc
            self._state = prune_ignore_state(result.state, now)
            self._rebuild_policy_index()
            changed_keys = {
                (str(row.get("scope") or ""), str(row.get("target_id") or ""))
                for row in result.changed
                if row.get("scope") and row.get("target_id")
            }
            self._guard.clear(changed_keys)
            self.last_commit_at = now
            self.last_error_code = None
            self.storage_write_healthy = True
            self._metric("mutation_committed")
            return result

    def _policy(self, scope: str, target_id: str) -> IgnorePolicy | None:
        if not self.storage_ready:
            return None
        policy = self._policy_map.get((scope, target_id))
        if policy is None:
            return None
        if policy.expires_at <= float(self._wall_clock()):
            # Expiry is fail-open on the hot path. Persistent pruning happens on the
            # next mutation/reload; we never perform storage I/O per event.
            self._guard.clear({(scope, target_id)})
            return None
        return policy

    def has_policy(self, scope: Any, target_id: Any) -> bool:
        try:
            normalized_scope = normalize_scope(scope)
            target = normalize_target_ids([target_id], 1)[0]
        except DomainError:
            return False
        return self._policy(normalized_scope, target) is not None

    def evaluate_activation_attempt(self, scope: Any, target_id: Any) -> IgnoreDecision:
        normalized_scope = normalize_scope(scope)
        target = normalize_target_ids([target_id], 1)[0]
        policy = self._policy(normalized_scope, target)
        if policy is None:
            return IgnoreDecision(
                matched=False,
                ignored=False,
                triggered=False,
                reason="policy_absent",
                scope=normalized_scope,
                target_id=target,
            )
        decision = self._guard.evaluate(policy, wall_now=float(self._wall_clock()))
        self._metric(decision.reason)
        return decision

    def snapshot(
        self,
        *,
        scope: str | None = None,
        target_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return public_ignore_snapshot(
            self._state,
            now=float(self._wall_clock()),
            scope=scope,
            target_ids=target_ids,
        )

    async def manage(
        self,
        *,
        scope: Any,
        action: Any,
        target_ids: Any,
        trigger_count: Any,
        trigger_window_seconds: Any,
        ignore_duration_seconds: Any,
        policy_seconds: Any,
        created_by: Any,
    ) -> dict[str, Any]:
        self._require_storage()
        normalized_scope = normalize_scope(scope)
        normalized_action = str(action or "").strip().lower()
        targets = normalize_optional_target_ids(target_ids, self.limits.max_per_scope)
        if normalized_action == "list":
            return {
                "status": "ok",
                "error_code": None,
                "action": "list",
                "scope_ref": self.scope_ref(normalized_scope),
                "changed": False,
                "effect_applied": False,
                "effect_state": "not_applied",
                "effect_contract": IGNORE_EFFECT_CONTRACT,
                "state": self.snapshot(
                    scope=normalized_scope,
                    target_ids=targets or None,
                ),
            }
        if normalized_action not in {"set", "clear"}:
            raise DomainError("invalid_action", "action 必须是 set、clear 或 list。")
        if not targets:
            raise DomainError("empty_target_ids", "target_ids 至少包含一个 QQ ID。")
        if normalized_action == "clear":
            result = await self._commit_mutation(
                lambda state, now: clear_ignore_policies(
                    state,
                    normalized_scope,
                    targets,
                    now=now,
                )
            )
            outcome = "ignore_policy_cleared" if result.changed else "ignore_policy_already_absent"
        else:
            result = await self._commit_mutation(
                lambda state, now: set_ignore_policies(
                    state,
                    self.limits,
                    normalized_scope,
                    targets,
                    trigger_count=trigger_count,
                    trigger_window_seconds=trigger_window_seconds,
                    ignore_duration_seconds=ignore_duration_seconds,
                    policy_seconds=policy_seconds,
                    created_by=created_by,
                    now=now,
                )
            )
            outcome = "ignore_policy_set" if result.changed else "ignore_policy_unchanged"
        changed = bool(result.changed)
        return {
            "status": "ok",
            "error_code": None,
            "action": normalized_action,
            "scope_ref": self.scope_ref(normalized_scope),
            "targets": targets,
            "changed": changed,
            "outcome": outcome,
            "effect_applied": changed,
            "effect_state": "applied" if changed else "not_applied",
            "effect_contract": IGNORE_EFFECT_CONTRACT,
            "reply_guaranteed": False,
            "changes": [
                {key: value for key, value in row.items() if key != "scope"}
                for row in result.changed
            ],
        }

    def health(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        return {
            "ignore_storage_ready": self.storage_ready,
            "ignore_storage_write_healthy": self.storage_write_healthy,
            "ignore_loaded_from": self.loaded_from,
            "ignore_policy_count": len(snapshot["ignore_policies"]),
            "ignore_quarantined_count": len(self.quarantined),
            "ignore_expired_on_load": self.expired_on_load,
            "ignore_last_error_code": self.last_error_code,
            "ignore_last_commit_at": self.last_commit_at,
            **self._guard.health(),
            "ignore_metrics": dict(self._metrics),
        }
