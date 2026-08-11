from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_sender_activation.heartbeat_domain import HeartbeatLease  # noqa: E402
from astrbot_plugin_sender_activation.heartbeat_gate import (  # noqa: E402
    DRIVER_CLEANUP_TAG,
    DRIVER_TAG,
    HeartbeatWakeGate,
)

SCOPE = "aiocqhttp:GroupMessage:10001"


@dataclass
class Job:
    job_id: str
    payload: dict[str, Any]
    enabled: bool = True
    cron_expression: str | None = None
    next_run_time: float | None = None


class Manager:
    def __init__(self) -> None:
        self.basic: dict[str, Job] = {}
        self.active: dict[str, Job] = {}
        self.handlers: dict[str, Any] = {}
        self.next_id = 1
        self.run_calls: list[str] = []

    async def add_basic_job(self, *, handler, **kwargs):
        jid = f"basic-{self.next_id}"
        self.next_id += 1
        job = Job(
            jid,
            dict(kwargs.get("payload") or {}),
            bool(kwargs.get("enabled", True)),
            kwargs.get("cron_expression"),
            1234.0,
        )
        self.basic[jid] = job
        self.handlers[jid] = handler
        return job

    async def list_jobs(self, job_type=None):
        if job_type == "basic":
            return list(self.basic.values())
        if job_type == "active_agent":
            return list(self.active.values())
        return [*self.basic.values(), *self.active.values()]

    async def delete_job(self, job_id):
        self.basic.pop(job_id, None)
        self.active.pop(job_id, None)
        self.handlers.pop(job_id, None)

    async def run_job_now(self, job_id):
        if job_id in self.active:
            self.run_calls.append(job_id)


def lease(lease_id="hb-1", *, expires=2000.0):
    return HeartbeatLease(
        lease_id=lease_id,
        scope=SCOPE,
        name="watch",
        cron_expression="*/5 * * * *",
        instruction="check group",
        created_at=1000.0,
        expires_at=expires,
        created_by="agent:test",
        source="tool",
        enabled=False,
        plugin_suspended=False,
        status="pending",
    )


async def main() -> None:
    manager = Manager()
    manager.active["hb-1"] = Job("hb-1", {"dummy": True}, False)
    gate_flag = {"allowed": False}
    gate = HeartbeatWakeGate(manager, preflight=lambda _scope: gate_flag["allowed"], wall_clock=lambda: 1100.0)
    await gate.initialize()
    armed = await gate.arm(lease())
    assert armed.enabled is True
    drivers = [j for j in manager.basic.values() if DRIVER_TAG in j.payload]
    cleanups = [j for j in manager.basic.values() if DRIVER_CLEANUP_TAG in j.payload]
    assert len(drivers) == 1 and len(cleanups) == 1
    driver = drivers[0]

    # Disabled session: driver fires, but no native Agent activation is consumed.
    await manager.handlers[driver.job_id](**driver.payload)
    assert manager.run_calls == []
    assert (await gate.health())["heartbeat_preflight_suppressed_total"] == 1

    # Re-enabled session: the same heartbeat becomes reachable without recreating lease.
    gate_flag["allowed"] = True
    await manager.handlers[driver.job_id](**driver.payload)
    assert manager.run_calls == ["hb-1"]

    # Parent disappears: stale driver self-cleans instead of becoming a free-running trigger.
    manager.active.pop("hb-1")
    await manager.handlers[driver.job_id](**driver.payload)
    assert driver.job_id not in manager.basic

    # Hot reload: stale runtime drivers are removed and rebuilt only from live leases.
    manager.active["hb-2"] = Job("hb-2", {"dummy": True}, False)
    first = await gate.arm(lease("hb-2", expires=2100.0))
    assert first.enabled
    stale_driver_ids = {j.job_id for j in manager.basic.values() if DRIVER_TAG in j.payload}
    assert stale_driver_ids
    gate2 = HeartbeatWakeGate(manager, preflight=lambda _scope: True, wall_clock=lambda: 1100.0)
    await gate2.initialize()
    assert not any(j.job_id in stale_driver_ids for j in manager.basic.values())

    # Explicit disarm is scoped to one parent and removes both driver and cleanup.
    await gate2.arm(lease("hb-2", expires=2100.0))
    await gate2.disarm("hb-2")
    assert not any(
        (j.payload.get(DRIVER_TAG, {}).get("parent_lease_id") == "hb-2")
        or (j.payload.get(DRIVER_CLEANUP_TAG, {}).get("parent_lease_id") == "hb-2")
        for j in manager.basic.values()
    )

    await gate.terminate()
    await gate2.terminate()
    print("rc19 heartbeat preflight counterexamples: PASS")


if __name__ == "__main__":
    asyncio.run(main())
