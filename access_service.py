


from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable
from typing import Any

from .access_domain import (
    AccessLoadResult,
    AccessState,
    access_state_to_document,
    grant_operator_access,
    has_operator_access,
    load_access_document,
    prune_access_state,
    public_access_snapshot,
    revoke_operator_access,
)
from .domain import (
    MAX_TARGETS_PER_OPERATION,
    DomainError,
    normalize_optional_target_ids,
    normalize_scope,
)
from .service import ActorContext
from .settings import PluginSettings
from .storage import AstrBotKVStateStore, CommitIndeterminateError

ACCESS_STATE_KEY = "sender_activation_operator_access_v1"
ACCESS_BACKUP_KEY = "sender_activation_operator_access_v1_lkg"
ACCESS_EFFECT_CONTRACT = "scoped_plugin_operator_capability"

_AGENT_RESTRICTIVE_ACTIONS = {
    "activation": frozenset({"disable"}),
    "rate": frozenset({"set"}),
    "heartbeat": frozenset({"disable"}),
}


class AccessService:


    def __init__(
        self,
        settings: PluginSettings,
        store: AstrBotKVStateStore,
        *,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self._store = store
        self._wall_clock = wall_clock
        self._state = AccessState.empty()
        self._lock = asyncio.Lock()
        self.storage_ready = False
        self.storage_write_healthy = False
        self.loaded_from = "none"
        self.last_error_code: str | None = None
        self.last_write_error_code: str | None = None
        self.quarantined: list[dict[str, Any]] = []
        self.expired_on_load = 0

    def _now(self) -> float:
        return float(self._wall_clock())

    @staticmethod
    def scope_ref(scope: str) -> str:
        return hashlib.sha256(scope.encode("utf-8")).hexdigest()[:12]

    async def initialize(self) -> None:
        self.storage_ready = False
        self.storage_write_healthy = False
        self.loaded_from = "none"
        self.last_error_code = None
        try:
            stored = await self._store.load()
        except Exception as exc:
            self.last_error_code = f"access_storage_load_failed:{type(exc).__name__}"
            return

        selected: AccessLoadResult | None = None
        selected_name = "empty"
        for name, document, read_error in (
            ("primary", stored.primary, stored.primary_error),
            ("backup", stored.backup, stored.backup_error),
        ):
            if selected is not None or read_error is not None or document is None:
                continue
            try:
                selected = load_access_document(document, self._now())
                selected_name = name
            except DomainError:
                continue
        if selected is None:
            if (
                (stored.primary_error is not None or stored.backup_error is not None)
                and stored.primary is None
                and stored.backup is None
            ):
                self.last_error_code = "access_storage_unreadable"
                return
            if stored.primary is not None or stored.backup is not None:
                self.last_error_code = "access_state_unrecoverable"
                return
            selected = AccessLoadResult(AccessState.empty())

        self._state = selected.state
        self.quarantined = [item.as_dict() for item in selected.quarantined]
        self.expired_on_load = selected.expired_count
        self.loaded_from = selected_name
        self.storage_ready = True
        self.storage_write_healthy = True

    async def terminate(self) -> None:
        self.storage_ready = False

    def _require_storage(self) -> None:
        if not self.storage_ready:
            raise DomainError(
                "access_storage_unavailable",
                "操作员授权存储不可用；管理员仍可操作插件，但不能新增授权。",
            )

    async def _commit(self, mutation_factory) -> Any:
        self._require_storage()
        async with self._lock:
            now = self._now()
            previous = prune_access_state(self._state, now)
            mutation = mutation_factory(previous, now)
            previous_doc = access_state_to_document(previous, now)
            candidate_doc = access_state_to_document(mutation.state, now)
            try:
                await self._store.commit(previous_doc, candidate_doc)
            except CommitIndeterminateError as exc:
                self.storage_write_healthy = False
                self.last_write_error_code = "access_commit_indeterminate"
                raise DomainError(
                    "access_commit_indeterminate",
                    "授权写入结果无法确认；请重载插件后查询。",
                ) from exc
            except Exception as exc:
                self.storage_write_healthy = False
                self.last_write_error_code = "access_storage_write_failed"
                raise DomainError(
                    "access_storage_write_failed",
                    "授权写入失败，运行快照未改变。",
                ) from exc
            self._state = mutation.state
            self.storage_write_healthy = True
            self.last_write_error_code = None
            return mutation

    @staticmethod
    def _is_admin(actor: ActorContext) -> bool:
        return actor.channel == "dashboard" or actor.is_admin

    def has_operator(self, scope: Any, operator_id: Any) -> bool:
        if not self.storage_ready:
            return False
        return has_operator_access(
            self._state,
            scope,
            operator_id,
            now=self._now(),
        )

    def authorize(
        self,
        *,
        actor: ActorContext,
        scope: Any,
        capability: str,
        action: Any,
    ) -> str:
        normalized_scope = normalize_scope(scope)
        normalized_action = str(action or "").strip().lower()
        if self._is_admin(actor):
            return "administrator"
        if self.has_operator(normalized_scope, actor.sender_id):
            return "delegated_operator"
        if normalized_action in _AGENT_RESTRICTIVE_ACTIONS.get(
            capability,
            frozenset(),
        ):
            return "agent_restrictive_veto"
        if (
            actor.proactive_source == "recovery_report"
            and capability == "activation"
            and normalized_action in {"enable", "renew"}
        ):
            return "bounded_recovery_report"
        raise DomainError(
            "operator_access_required",
            "当前发送者没有本群插件操作员授权；请让 AstrBot 管理员授权该 QQ ID。",
        )

    async def manage(
        self,
        *,
        actor: ActorContext,
        scope: Any,
        action: Any,
        operator_ids: Any,
        duration_seconds: Any,
    ) -> dict[str, Any]:
        if not self._is_admin(actor):
            raise DomainError(
                "admin_required",
                "只有 AstrBot 管理员能授予、续期、撤销或查看插件操作员权限。",
            )
        self._require_storage()
        normalized_scope = normalize_scope(scope)
        normalized_action = str(action or "").strip().lower()
        operators = normalize_optional_target_ids(
            operator_ids,
            MAX_TARGETS_PER_OPERATION,
        )
        if normalized_action == "list":
            return {
                "status": "ok",
                "error_code": None,
                "action": "list",
                "scope_ref": self.scope_ref(normalized_scope),
                "changed": False,
                "outcome": "operator_access_listed",
                "effect_contract": ACCESS_EFFECT_CONTRACT,
                "effect_applied": False,
                "effect_state": "not_applied",
                "reply_guaranteed": False,
                "state": self.snapshot(scope=normalized_scope),
            }
        if normalized_action not in {"grant", "renew", "revoke"}:
            raise DomainError(
                "invalid_action",
                "action 必须是 grant、renew、revoke 或 list。",
            )
        if not operators:
            raise DomainError("empty_target_ids", "operator_ids 至少包含一个 QQ ID。")

        if normalized_action == "revoke":
            mutation = await self._commit(
                lambda state, now: revoke_operator_access(
                    state,
                    normalized_scope,
                    operators,
                    now=now,
                )
            )
        else:
            mutation = await self._commit(
                lambda state, now: grant_operator_access(
                    state,
                    self.settings.access_limits,
                    normalized_scope,
                    operators,
                    duration_seconds,
                    actor.actor_ref,
                    renew_only=normalized_action == "renew",
                    now=now,
                )
            )
        changed = bool(mutation.changed)
        outcomes = {
            "grant": "operator_access_granted",
            "renew": "operator_access_renewed",
            "revoke": (
                "operator_access_revoked"
                if changed
                else "operator_access_already_absent"
            ),
        }
        return {
            "status": "ok",
            "error_code": None,
            "action": normalized_action,
            "scope_ref": self.scope_ref(normalized_scope),
            "operator_ids": operators,
            "changed": changed,
            "outcome": outcomes[normalized_action],
            "effect_contract": ACCESS_EFFECT_CONTRACT,
            "effect_applied": changed,
            "effect_state": "applied" if changed else "not_applied",
            "reply_guaranteed": False,
            "changes": list(mutation.changed),
        }

    def snapshot(self, *, scope: Any | None = None) -> dict[str, Any]:
        return public_access_snapshot(
            self._state,
            now=self._now(),
            scope=scope,
        )

    def health(self) -> dict[str, Any]:
        state = self.snapshot()
        return {
            "access_storage_ready": self.storage_ready,
            "access_storage_write_healthy": self.storage_write_healthy,
            "access_loaded_from": self.loaded_from,
            "access_last_error_code": self.last_error_code,
            "access_last_write_error_code": self.last_write_error_code,
            "access_grant_count": len(state["operator_grants"]),
            "access_quarantined_count": len(self.quarantined),
            "access_quarantined": list(self.quarantined),
            "access_expired_on_load": self.expired_on_load,
        }
