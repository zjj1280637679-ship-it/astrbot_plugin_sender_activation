from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .access_domain import AccessLimits
from .attention_domain import IgnoreLimits
from .domain import Limits
from .echo_service import EchoLimits
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


def _float(
    config: Any,
    key: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(_value(config, key, default))
    except (TypeError, ValueError, OverflowError):
        value = default
    return max(minimum, min(value, maximum))


def _bool(config: Any, key: str, default: bool) -> bool:
    value = _value(config, key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "enable", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disable", "disabled"}:
        return False
    return default


@dataclass(frozen=True)
class PluginSettings:
    limits: Limits
    heartbeat_limits: HeartbeatLimits = field(default_factory=HeartbeatLimits)
    access_limits: AccessLimits = field(default_factory=AccessLimits)
    ignore_limits: IgnoreLimits = field(default_factory=IgnoreLimits)
    echo_limits: EchoLimits = field(default_factory=EchoLimits)
    host_config_notice_mode: str = "page_and_agent"
    activation_min_interval_seconds: float = 120.0
    agent_ignore_enabled: bool = False
    ignore_native_wake_mode: str = "extra_only"
    echo_enabled: bool = False
    echo_cancel_on_native_wake: bool = True
    echo_cancel_on_sender_activation: bool = True
    echo_allow_native_wake_source: bool = True
    echo_allow_sender_activation_source: bool = True
    echo_allow_heartbeat_source: bool = False

    @classmethod
    def from_config(cls, config: Any) -> "PluginSettings":
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

        absolute_attention_seconds = 365 * 24 * 60 * 60
        ignore_max_seconds = _int(
            config,
            "ignore_max_seconds",
            24 * 60 * 60,
            1,
            absolute_attention_seconds,
        )
        ignore_default_seconds = _int(
            config,
            "ignore_default_seconds",
            10 * 60,
            1,
            ignore_max_seconds,
        )
        ignore_policy_max_seconds = _int(
            config,
            "ignore_policy_max_seconds",
            7 * 24 * 60 * 60,
            1,
            absolute_attention_seconds,
        )
        ignore_policy_default_seconds = _int(
            config,
            "ignore_policy_default_seconds",
            60 * 60,
            1,
            ignore_policy_max_seconds,
        )
        ignore_trigger_max_count = _int(
            config,
            "ignore_trigger_max_count",
            10_000,
            1,
            1_000_000,
        )
        ignore_trigger_default_count = _int(
            config,
            "ignore_trigger_default_count",
            1,
            1,
            ignore_trigger_max_count,
        )
        ignore_trigger_max_window = _float(
            config,
            "ignore_trigger_max_window_seconds",
            24 * 60 * 60,
            0.0,
            float(absolute_attention_seconds),
        )
        ignore_trigger_default_window = _float(
            config,
            "ignore_trigger_default_window_seconds",
            0.0,
            0.0,
            ignore_trigger_max_window,
        )

        echo_max_delay = _float(
            config,
            "echo_max_delay_seconds",
            30 * 60.0,
            0.1,
            float(absolute_attention_seconds),
        )
        echo_default_delay = _float(
            config,
            "echo_default_delay_seconds",
            30.0,
            0.1,
            echo_max_delay,
        )
        echo_max_count = _int(
            config,
            "echo_max_count",
            1,
            1,
            1000,
        )
        echo_default_count = _int(
            config,
            "echo_default_count",
            1,
            1,
            echo_max_count,
        )
        echo_max_interval = _float(
            config,
            "echo_max_interval_seconds",
            30 * 60.0,
            0.1,
            float(absolute_attention_seconds),
        )
        echo_default_interval = _float(
            config,
            "echo_default_interval_seconds",
            30.0,
            0.1,
            echo_max_interval,
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
            ignore_limits=IgnoreLimits(
                default_ignore_seconds=ignore_default_seconds,
                max_ignore_seconds=ignore_max_seconds,
                default_policy_seconds=ignore_policy_default_seconds,
                max_policy_seconds=ignore_policy_max_seconds,
                default_trigger_count=ignore_trigger_default_count,
                max_trigger_count=ignore_trigger_max_count,
                default_trigger_window_seconds=ignore_trigger_default_window,
                max_trigger_window_seconds=ignore_trigger_max_window,
                max_per_scope=_int(
                    config,
                    "max_ignore_policies_per_scope",
                    50,
                    1,
                    1000,
                ),
                max_total=_int(
                    config,
                    "max_ignore_policies_total",
                    1000,
                    1,
                    100_000,
                ),
            ),
            echo_limits=EchoLimits(
                default_delay_seconds=echo_default_delay,
                max_delay_seconds=echo_max_delay,
                default_count=echo_default_count,
                max_count=echo_max_count,
                default_interval_seconds=echo_default_interval,
                max_interval_seconds=echo_max_interval,
                max_pending_per_scope=_int(
                    config,
                    "echo_max_pending_per_scope",
                    10,
                    1,
                    1000,
                ),
                max_pending_total=_int(
                    config,
                    "echo_max_pending_total",
                    100,
                    1,
                    100_000,
                ),
            ),
            host_config_notice_mode=_choice(
                config,
                "host_config_notice_mode",
                "page_and_agent",
                frozenset({"page_only", "page_and_agent"}),
            ),
            activation_min_interval_seconds=_float(
                config,
                "activation_min_interval_seconds",
                120.0,
                0.0,
                1800.0,
            ),
            agent_ignore_enabled=_bool(config, "agent_ignore_enabled", False),
            ignore_native_wake_mode=_choice(
                config,
                "ignore_native_wake_mode",
                "extra_only",
                frozenset({"extra_only", "suppress_llm", "stop_event"}),
            ),
            echo_enabled=_bool(config, "echo_enabled", False),
            echo_cancel_on_native_wake=_bool(
                config,
                "echo_cancel_on_native_wake",
                True,
            ),
            echo_cancel_on_sender_activation=_bool(
                config,
                "echo_cancel_on_sender_activation",
                True,
            ),
            echo_allow_native_wake_source=_bool(
                config,
                "echo_allow_native_wake_source",
                True,
            ),
            echo_allow_sender_activation_source=_bool(
                config,
                "echo_allow_sender_activation_source",
                True,
            ),
            echo_allow_heartbeat_source=_bool(
                config,
                "echo_allow_heartbeat_source",
                False,
            ),
        )
