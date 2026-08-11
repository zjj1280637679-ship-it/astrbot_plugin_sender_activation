from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

import astrbot_plugin_sender_activation.main as plugin_main  # noqa: E402
from astrbot_plugin_sender_activation.attention_service import IgnoreDecision  # noqa: E402
from astrbot_plugin_sender_activation.domain import DomainError  # noqa: E402
from astrbot_plugin_sender_activation.echo_service import (  # noqa: E402
    ECHO_KIND,
    ECHO_TAG,
)
from astrbot_plugin_sender_activation.heartbeat_domain import heartbeat_payload  # noqa: E402
from astrbot_plugin_sender_activation.main import SenderActivationPlugin  # noqa: E402
from astrbot_plugin_sender_activation.settings import PluginSettings  # noqa: E402
from astrbot.core.agent.tool import ToolSet  # noqa: E402
from astrbot.core.cron.events import CronMessageEvent  # noqa: E402
from astrbot.core.platform.message_session import MessageSession  # noqa: E402
from astrbot.core.provider.register import llm_tools  # noqa: E402
from astrbot.core.star.star_handler import star_handlers_registry  # noqa: E402

SCOPE = "aiocqhttp:GroupMessage:10001"
A = "123456789"


class DummyContext:
    async def send_message(self, *args, **kwargs):
        return None


class FakeEvent:
    def __init__(self, *, native_wake: bool) -> None:
        self.unified_msg_origin = SCOPE
        self.is_at_or_wake_command = native_wake
        self._extras: dict[str, Any] = {}
        self._stopped = False

    def get_platform_name(self):
        return "aiocqhttp"

    def is_private_chat(self):
        return False

    def get_sender_id(self):
        return A

    def get_self_id(self):
        return "999999999"

    def get_extra(self, key: str):
        return self._extras.get(key)

    def set_extra(self, key: str, value: Any):
        self._extras[key] = value

    def stop_event(self):
        self._stopped = True

    def is_stopped(self):
        return self._stopped


class FakeAttention:
    def evaluate_activation_attempt(self, scope: str, sender: str):
        assert scope == SCOPE
        assert sender == A
        return IgnoreDecision(
            matched=True,
            ignored=True,
            triggered=True,
            reason="threshold_triggered",
            scope=scope,
            target_id=sender,
            observed_count=3,
            trigger_count=3,
            trigger_window_seconds=10.0,
            retry_after_seconds=60.0,
            policy_remaining_seconds=600,
        )


class FakeEcho:
    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def cancel_scope(self, scope: str):
        self.cancelled.append(scope)
        return ["fake"]


class FailReservation:
    async def reserve(self, *args, **kwargs):
        raise AssertionError("ignored event reached activation reservation")


class FakeGate:
    def __init__(self, enabled: bool | None) -> None:
        self.enabled = enabled

    async def read(self, scope: str):
        assert scope == SCOPE
        return SimpleNamespace(enabled=self.enabled, reason="test")


def echo_payload() -> dict[str, Any]:
    return {
        "session": SCOPE,
        "sender_id": A,
        "origin": "astrbot_plugin_sender_activation",
        "note": "echo test",
        ECHO_TAG: {
            "kind": ECHO_KIND,
            "schema_version": 1,
            "group_id": "runtime-test",
            "source": "native_wake",
            "instruction": "check follow-up",
            "created_at": 1000.0,
            "run_at": 1030.0,
            "created_by": "agent:test",
            "batch_index": 1,
            "batch_count": 1,
            "armed": True,
        },
    }


def make_cron(payload: dict[str, Any]) -> CronMessageEvent:
    session = MessageSession.from_str(SCOPE)
    return CronMessageEvent(
        context=DummyContext(),
        session=session,
        message="runtime test",
        extras={"cron_payload": payload},
        message_type=session.message_type,
    )


def test_registration() -> None:
    assert plugin_main.VERSION == "1.1.0-rc.19"
    expected = [
        "manage_sender_activation_access",
        "manage_sender_activation",
        "manage_sender_activation_rate",
        "manage_heartbeat_lease",
        "yield_current_turn",
        "manage_attention_ignore",
        "manage_echo_hook",
    ]
    tools = []
    for name in expected:
        tool = llm_tools.get_func(name)
        assert tool is not None, f"missing tool: {name}"
        tools.append(tool)
    tool_set = ToolSet(tools)
    full = tool_set.openai_schema()
    light = tool_set.get_light_tool_set().openai_schema()
    assert {row["function"]["name"] for row in full} == set(expected)
    assert {row["function"]["name"] for row in light} == set(expected)
    ignore_schema = next(row for row in full if row["function"]["name"] == "manage_attention_ignore")
    echo_schema = next(row for row in full if row["function"]["name"] == "manage_echo_hook")
    ignore_props = ignore_schema["function"]["parameters"]["properties"]
    echo_props = echo_schema["function"]["parameters"]["properties"]
    assert {"target_ids", "trigger_count", "trigger_window_seconds", "ignore_duration_seconds", "policy_seconds"} <= set(ignore_props)
    assert {"delay_seconds", "count", "interval_seconds", "instruction"} <= set(echo_props)
    print(
        "tool_schema_chars",
        json.dumps(
            {
                "full": len(json.dumps(full, ensure_ascii=False, separators=(",", ":"))),
                "skills_like_stage1": len(json.dumps(light, ensure_ascii=False, separators=(",", ":"))),
            },
            ensure_ascii=False,
        ),
    )


def test_handler_priority() -> None:
    handlers = [
        handler
        for handler in star_handlers_registry
        if handler.handler_module_path == "astrbot_plugin_sender_activation.main"
    ]
    by_name = {handler.handler_name: handler for handler in handlers}
    guard = by_name["apply_attention_guard"]
    activation = by_name["activate_native_agent"]
    assert guard.extras_configs["priority"] == 3000
    assert activation.extras_configs["priority"] == 2000
    ordered_names = [handler.handler_name for handler in handlers]
    assert ordered_names.index("apply_attention_guard") < ordered_names.index("activate_native_agent")


def test_owned_cron_context() -> None:
    echo_event = make_cron(echo_payload())
    assert echo_event.get_platform_name() == "cron"
    assert SenderActivationPlugin._scope(echo_event) == SCOPE
    assert SenderActivationPlugin._sender(echo_event) == A
    plugin = object.__new__(SenderActivationPlugin)
    assert plugin._activation_source(echo_event) == "echo"
    assert plugin._plugin_proactive_source(echo_event) == "echo"

    heartbeat = heartbeat_payload(
        scope=SCOPE,
        instruction="heartbeat test",
        name="test",
        created_at=1000.0,
        expires_at=2000.0,
        created_by="agent:test",
        source="tool",
        sender_id=A,
    )
    heartbeat_event = make_cron(heartbeat)
    assert SenderActivationPlugin._scope(heartbeat_event) == SCOPE
    assert SenderActivationPlugin._sender(heartbeat_event) == A
    assert plugin._activation_source(heartbeat_event) == "heartbeat"
    assert plugin._plugin_proactive_source(heartbeat_event) == "heartbeat"

    foreign_event = make_cron({"session": SCOPE, "sender_id": A, "note": "foreign"})
    try:
        SenderActivationPlugin._scope(foreign_event)
    except DomainError as exc:
        assert exc.code == "unsupported_platform"
    else:
        raise AssertionError("foreign Cron event was incorrectly trusted as owned QQ context")


def test_error_contract() -> None:
    data = json.loads(
        plugin_main._tool_error(
            DomainError(
                "attention_commit_indeterminate",
                "simulated uncertain write",
            ),
            "manage_attention_ignore",
        )
    )
    assert data["effect_state"] == "indeterminate"
    assert data["effect_applied"] is None
    assert data["recovery_action_executed"] is False
    assert data["automatic_retry_scheduled"] is False


def test_config_authority() -> None:
    default = PluginSettings.from_config({})
    assert default.agent_ignore_enabled is False
    assert default.echo_enabled is False
    assert default.echo_limits.default_count == 1
    assert default.echo_limits.max_count == 1
    assert default.ignore_native_wake_mode == "extra_only"

    elevated = PluginSettings.from_config(
        {
            "model": "gpt-5.6-do-not-special-case",
            "agent_ignore_enabled": True,
            "echo_enabled": True,
            "echo_default_count": 2,
            "echo_max_count": 3,
            "ignore_native_wake_mode": "suppress_llm",
        }
    )
    assert elevated.agent_ignore_enabled is True
    assert elevated.echo_enabled is True
    assert elevated.echo_limits.default_count == 2
    assert elevated.echo_limits.max_count == 3
    assert elevated.ignore_native_wake_mode == "suppress_llm"

    same_without_model = PluginSettings.from_config(
        {
            "agent_ignore_enabled": True,
            "echo_enabled": True,
            "echo_default_count": 2,
            "echo_max_count": 3,
            "ignore_native_wake_mode": "suppress_llm",
        }
    )
    assert elevated == same_without_model


async def test_guard_modes() -> None:
    for mode in ("extra_only", "suppress_llm", "stop_event"):
        plugin = object.__new__(SenderActivationPlugin)
        plugin.settings = SimpleNamespace(
            agent_ignore_enabled=True,
            ignore_native_wake_mode=mode,
            echo_enabled=True,
            echo_cancel_on_native_wake=True,
            echo_cancel_on_sender_activation=True,
        )
        plugin.attention_service = FakeAttention()
        plugin.echo_service = FakeEcho()
        event = FakeEvent(native_wake=True)
        await plugin.apply_attention_guard(event)
        assert event.get_extra(plugin_main.ATTENTION_IGNORED_EXTRA)["mode"] == mode
        if mode == "extra_only":
            assert event.is_at_or_wake_command is True
            assert event.is_stopped() is False
            assert plugin.echo_service.cancelled == [SCOPE]
        elif mode == "suppress_llm":
            assert event.is_at_or_wake_command is False
            assert event.is_stopped() is False
            # An actually suppressed hostile wake must not cancel a useful pending Echo.
            assert plugin.echo_service.cancelled == []
        else:
            assert event.is_at_or_wake_command is True
            assert event.is_stopped() is True
            assert plugin.echo_service.cancelled == []

    plugin = object.__new__(SenderActivationPlugin)
    plugin.settings = SimpleNamespace(
        echo_enabled=True,
        echo_cancel_on_native_wake=True,
        echo_cancel_on_sender_activation=True,
    )
    plugin.attention_service = FakeAttention()
    plugin.echo_service = FakeEcho()
    plugin.activation_reservations = FailReservation()
    tracked = FakeEvent(native_wake=False)
    await plugin.apply_attention_guard(tracked)
    assert tracked.get_extra(plugin_main.ATTENTION_IGNORED_EXTRA)
    await plugin.activate_native_agent(tracked)
    assert plugin.echo_service.cancelled == []


async def test_echo_preflight_gate() -> None:
    plugin = object.__new__(SenderActivationPlugin)
    plugin._terminated = False
    plugin.settings = SimpleNamespace(echo_enabled=True)
    plugin.session_gate = FakeGate(False)
    assert await plugin._echo_preflight(SCOPE) is False
    plugin.session_gate = FakeGate(True)
    assert await plugin._echo_preflight(SCOPE) is True
    plugin._terminated = True
    assert await plugin._echo_preflight(SCOPE) is False


async def main() -> None:
    test_registration()
    test_handler_priority()
    test_owned_cron_context()
    test_error_contract()
    test_config_authority()
    await test_guard_modes()
    await test_echo_preflight_gate()
    print("rc19 AstrBot runtime integration counterexamples: PASS")


if __name__ == "__main__":
    asyncio.run(main())
