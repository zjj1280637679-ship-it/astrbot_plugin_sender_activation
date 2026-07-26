


from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .access_domain import AccessLimits
from .domain import Limits
from .heartbeat_domain import HeartbeatLimits


def _value(config: Any, key: str, default: Any) -> Any:
    getter = getattr(config, "get", None)
    if not callable(getter):
        return default
    try:
        return getter(key, default)
    except Exception:
        return default


def _int(
    config: Any,
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(_value(config, key, default))
    except (TypeError, ValueError, OverflowError):
        value = default
    return max(minimum, min(value, maximum))


def _choice(
    config: Any,
    key: str,
    default: str,
    choices: frozenset[str],
) -> str:
    value = str(_value(config, key, default) or "").strip()
    return value if value in choices else default


@dataclass(frozen=True)
class PluginSettings:
    limits: Limits
    heartbeat_limits: HeartbeatLimits = field(default_factory=HeartbeatLimits)
    access_limits: AccessLimits = field(default_factory=AccessLimits)
    host_config_notice_mode: str = "page_and_agent"

    @classmethod
    def from_config(cls, config: Any) -> PluginSettings:
        activation_max = _int(
            config,
            "activation_max_seconds",
            365 * 24 * 60 * 60,
            1,
            365 * 24 * 60 * 60,
        )
        activation_default = _int(
            config,
            "activation_default_seconds",
            24 * 60 * 60,
            1,
            activation_max,
        )
        rate_max = _int(
            config,
            "rate_max_duration_seconds",
            24 * 60 * 60,
            1,
            24 * 60 * 60,
        )
        heartbeat_max = _int(
            config,
            "heartbeat_max_seconds",
            24 * 60 * 60,
            60,
            7 * 24 * 60 * 60,
        )
        access_max = _int(
            config,
            "operator_access_max_seconds",
            365 * 24 * 60 * 60,
            60,
            365 * 24 * 60 * 60,
        )
        return cls(
            limits=Limits(
                activation_default_seconds=activation_default,
                activation_max_seconds=activation_max,
                max_targets_per_scope=_int(
                    config,
                    "max_targets_per_scope",
                    20,
                    1,
                    100,
                ),
                max_targets_total=_int(
                    config,
                    "max_targets_total",
                    500,
                    1,
                    5000,
                ),
                rate_max_duration_seconds=rate_max,
                rate_max_activations=_int(
                    config,
                    "rate_max_activations",
                    1000,
                    1,
                    1000,
                ),
                rate_max_window_seconds=_int(
                    config,
                    "rate_max_window_seconds",
                    24 * 60 * 60,
                    1,
                    24 * 60 * 60,
                ),
                recovery_report_seconds=_int(
                    config,
                    "recovery_report_seconds",
                    24 * 60 * 60,
                    60,
                    7 * 24 * 60 * 60,
                ),
            ),
            heartbeat_limits=HeartbeatLimits(
                default_duration_seconds=_int(
                    config,
                    "heartbeat_default_seconds",
                    60 * 60,
                    60,
                    heartbeat_max,
                ),
                max_duration_seconds=heartbeat_max,
                max_per_scope=_int(
                    config,
                    "max_heartbeats_per_scope",
                    10,
                    1,
                    50,
                ),
                max_total=_int(
                    config,
                    "max_heartbeats_total",
                    100,
                    1,
                    1000,
                ),
            ),
            access_limits=AccessLimits(
                default_duration_seconds=_int(
                    config,
                    "operator_access_default_seconds",
                    30 * 24 * 60 * 60,
                    60,
                    access_max,
                ),
                max_duration_seconds=access_max,
                max_per_scope=_int(
                    config,
                    "max_operators_per_scope",
                    50,
                    1,
                    100,
                ),
                max_total=_int(
                    config,
                    "max_operators_total",
                    1000,
                    1,
                    5000,
                ),
            ),
            host_config_notice_mode=_choice(
                config,
                "host_config_notice_mode",
                "page_and_agent",
                frozenset({"page_only", "page_and_agent"}),
            ),
        )
