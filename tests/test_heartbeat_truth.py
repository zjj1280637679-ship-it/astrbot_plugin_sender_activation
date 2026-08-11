from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

import astrbot_plugin_sender_activation.main as plugin_main  # noqa: E402
from astrbot_plugin_sender_activation.domain import DomainError  # noqa: E402
from astrbot_plugin_sender_activation.heartbeat_domain import HeartbeatLease  # noqa: E402
from astrbot_plugin_sender_activation.heartbeat_service import HeartbeatService  # noqa: E402
from astrbot_plugin_sender_activation.service import ActorContext  # noqa: E402
from astrbot_plugin_sender_activation.settings import PluginSettings  # noqa: E402

SCOPE = "aiocqhttp:GroupMessage:10001"


def lease() -> HeartbeatLease:
    return HeartbeatLease(
        lease_id="hb-1",
        scope=SCOPE,
        name="old",
        cron_expression="*/5 * * * *",
        instruction="old instruction",
        created_at=1000.0,
        expires_at=5000.0,
        created_by="agent:test",
        source="tool",
        enabled=True,
        plugin_suspended=False,
        status="pending",
    )


class Adapter:
    def __init__(self) -> None:
        self.current = lease()
        self.suspend_called = False

    async def inventory(self):
        return [self.current], []

    async def delete_if_expired(self, *args, **kwargs):
        return False

    async def renew(self, old, **kwargs):
        self.current = HeartbeatLease(
            lease_id=old.lease_id,
            scope=old.scope,
            name=kwargs["name"],
            cron_expression=kwargs["cron_expression"],
            instruction=kwargs["instruction"],
            created_at=kwargs["now"],
            expires_at=kwargs["expires_at"],
            created_by=kwargs["actor_ref"],
            source=kwargs["source"],
            enabled=False,
            plugin_suspended=False,
            status="pending",
        )
        return self.current

    async def suspend_for_shutdown(self):
        self.suspend_called = True
        return []


class FailingGate:
    async def decorate(self, leases):
        return leases

    async def arm(self, _lease):
        raise DomainError("heartbeat_preflight_create_failed", "simulated")

    async def terminate(self):
        return []

    async def health(self):
        return {"heartbeat_preflight_active": True}


async def test_renew_truth() -> None:
    settings = PluginSettings.from_config({})
    adapter = Adapter()
    service = HeartbeatService(
        settings,
        adapter,  # type: ignore[arg-type]
        FailingGate(),  # type: ignore[arg-type]
        wall_clock=lambda: 2000.0,
    )
    service._active = True
    actor = ActorContext("123456789", "agent:test", "tool")
    try:
        await service.manage(
            actor=actor,
            scope=SCOPE,
            scope_ref="test-scope",
            action="renew",
            lease_ids=["hb-1"],
            name="new",
            cron_expression="*/10 * * * *",
            instruction="new instruction",
            duration_seconds=3600,
        )
    except DomainError as exc:
        assert exc.code == "heartbeat_preflight_update_indeterminate"
    else:
        raise AssertionError("renew gate failure was incorrectly reported as success")

    # The active template may have changed, but it is fail-closed (disabled).
    assert adapter.current.name == "new"
    assert adapter.current.enabled is False
    assert service.last_error_code == "heartbeat_preflight_update_indeterminate"

    receipt = json.loads(
        plugin_main._tool_error(
            DomainError(
                "heartbeat_preflight_update_indeterminate",
                "simulated",
            ),
            "manage_heartbeat_lease",
        )
    )
    assert receipt["effect_state"] == "indeterminate"
    assert receipt["effect_applied"] is None
    assert receipt["recovery_action_executed"] is False
    assert receipt["automatic_retry_scheduled"] is False


async def main() -> None:
    await test_renew_truth()
    print("rc19 heartbeat lifecycle truth counterexamples: PASS")


if __name__ == "__main__":
    asyncio.run(main())
