


from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

STATE_KEY = "sender_activation_state_v1"
BACKUP_KEY = "sender_activation_state_v1_lkg"


class KVPlugin(Protocol):
    async def get_kv_data(self, key: str, default: Any = None) -> Any: ...

    async def put_kv_data(self, key: str, value: Any) -> None: ...


@dataclass(frozen=True)
class StoredDocuments:
    primary: Mapping[str, Any] | None
    backup: Mapping[str, Any] | None
    primary_error: str | None = None
    backup_error: str | None = None


class CommitIndeterminateError(RuntimeError):
    pass


class AstrBotKVStateStore:


    def __init__(self, plugin: KVPlugin) -> None:
        self._plugin = plugin

    async def load(self) -> StoredDocuments:
        async def read_slot(
            key: str,
        ) -> tuple[Mapping[str, Any] | None, str | None]:
            try:
                value = await self._plugin.get_kv_data(key, None)
            except Exception as exc:
                return None, type(exc).__name__
            return value, None

        primary, primary_error = await read_slot(STATE_KEY)
        backup, backup_error = await read_slot(BACKUP_KEY)
        return StoredDocuments(
            primary=primary,
            backup=backup,
            primary_error=primary_error,
            backup_error=backup_error,
        )

    async def _put_verified(
        self,
        key: str,
        value: Mapping[str, Any],
    ) -> None:
        candidate = dict(value)
        try:
            await self._plugin.put_kv_data(key, candidate)
            return
        except Exception as write_error:
            try:
                observed = await self._plugin.get_kv_data(key, None)
            except Exception as read_error:
                raise CommitIndeterminateError(
                    f"{key}:write_{type(write_error).__name__}:"
                    f"readback_{type(read_error).__name__}"
                ) from write_error
            if observed == candidate:
                return
            raise write_error

    async def commit(
        self,
        previous_document: Mapping[str, Any],
        candidate_document: Mapping[str, Any],
    ) -> None:

        await self._put_verified(BACKUP_KEY, previous_document)
        await self._put_verified(STATE_KEY, candidate_document)
