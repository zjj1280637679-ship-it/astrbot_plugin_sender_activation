


from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

SESSION_PLUGIN_CONFIG_KEY = "session_plugin_config"


class SessionPreferences(Protocol):
    def get(
        self,
        key: str,
        default: Any = None,
        scope: str | None = None,
        scope_id: str | None = None,
    ) -> Any: ...

    async def get_async(
        self,
        scope: str,
        scope_id: str,
        key: str,
        default: Any = None,
    ) -> Any: ...


@dataclass(frozen=True)
class SessionGateStatus:
    enabled: bool | None
    code: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "code": self.code,
        }


class AstrBotSessionGate:


    def __init__(
        self,
        plugin_name: str,
        preferences: SessionPreferences,
    ) -> None:
        self._plugin_name = plugin_name
        self._preferences = preferences

    def _status_from_document(
        self,
        document: Any,
        scope: str,
    ) -> SessionGateStatus:
        if not isinstance(document, Mapping):
            return SessionGateStatus(None, "session_config_malformed")
        session_config = document.get(scope, {})
        if not isinstance(session_config, Mapping):
            return SessionGateStatus(None, "session_config_malformed")
        disabled_plugins = session_config.get("disabled_plugins", [])
        if not isinstance(disabled_plugins, (list, tuple, set, frozenset)):
            return SessionGateStatus(None, "session_config_malformed")
        disabled = self._plugin_name in {str(item).strip() for item in disabled_plugins}
        return SessionGateStatus(
            not disabled,
            "session_enabled" if not disabled else "session_disabled",
        )

    def read_sync(self, scope: str) -> SessionGateStatus:
        try:
            document = self._preferences.get(
                SESSION_PLUGIN_CONFIG_KEY,
                {},
                scope="umo",
                scope_id=scope,
            )
        except Exception:
            return SessionGateStatus(None, "session_config_read_failed")
        return self._status_from_document(document, scope)

    async def read(self, scope: str) -> SessionGateStatus:
        try:
            document = await self._preferences.get_async(
                "umo",
                scope,
                SESSION_PLUGIN_CONFIG_KEY,
                {},
            )
        except Exception:
            return SessionGateStatus(None, "session_config_read_failed")
        return self._status_from_document(document, scope)
