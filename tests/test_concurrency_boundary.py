from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_sender_activation.echo_service import (  # noqa: E402
    ECHO_KIND,
    ECHO_TAG,
    EchoLimits,
    EchoService,
)
from astrbot_plugin_sender_activation.heartbeat_domain import heartbeat_payload  # noqa: E402
from astrbot_plugin_sender_activation.heartbeat_gate import (  # noqa: E402
    DRIVER_TAG,
    HeartbeatWakeGate,
)

SCOPE = "aiocqhttp:GroupMessage:10001"
A = "123456789"


@dataclass
class Job:
    job_id: str
    payload: dict[str, Any]
    enabled: bool = True
    status: str = "pending"
    next_run_time: float | None = None


class BlockingManager:
    def __init__(self) -> None:
        self.active: dict[str, Job] = {}
        self.basic: dict[str, Job] = {}
        self.handlers: dict[str, Any] = {}
        self.next_id = 1
        self.run_started = asyncio.Event()
        self.run_release = asyncio.Event()
        self.run_count = 0

    async def add_active_job(self, **kwargs):
        jid = f"active-{self.next_id}"
        self.next_id += 1
        job = Job(jid, dict(kwargs.get("payload") or {}), bool(kwargs.get("enabled", True)))
        self.active[jid] = job
        return job

    async def add_basic_job(self, *, handler, **kwargs):
        jid = f"basic-{self.next_id}"
        self.next_id += 1
        job = Job(jid, dict(kwargs.get("payload") or {}), bool(kwargs.get("enabled", True)), next_run_time=1234.0)
        self.basic[jid] = job
        self.handlers[jid] = handler
        return job

    async def list_jobs(self, job_type=None):
        if job_type == "active_agent":
            return list(self.active.values())
        if job_type == "basic":
            return list(self.basic.values())
        return [*self.active.values(), *self.basic.values()]

    async def delete_job(self, job_id: str):
        self.active.pop(job_id, None)
        self.basic.pop(job_id, None)
        self.handlers.pop(job_id, None)

    async def run_job_now(self, job_id: str):
        # Mirrors the important native semantic boundary: once run_job_now has found
        # and started executing a job, deleting the registry row cannot reclaim that
        # already-started Agent execution.
        if job_id not in self.active:
            return
        self.run_started.set()
        await self.run_release.wait()
        self.run_count += 1


def echo_payload(run_at: float) -> dict[str, Any]:
    return {
        "session": SCOPE,
        "sender_id": A,
        ECHO_TAG: {
            "kind": ECHO_KIND,
            "schema_version": 1,
            "group_id": "race-group",
            "source": "native_wake",
            "instruction": "race test",
            "created_at": 1000.0,
            "run_at": run_at,
            "created_by": "agent:test",
            "batch_index": 1,
            "batch_count": 1,
            "armed": True,
        },
    }


async def test_echo_before_boundary() -> None:
    manager = BlockingManager()
    service = EchoService(
        EchoLimits(default_delay_seconds=30, max_delay_seconds=300),
        manager,
        wall_clock=lambda: 1000.0,
        preflight=lambda _scope: True,
    )
    await service.initialize()
    created = await service.create(
        scope=SCOPE,
        sender_id=A,
        actor_ref="agent:test",
        source="native_wake",
        delay_seconds=30,
        count=1,
        interval_seconds=30,
        instruction="race test",
    )
    hook_id = created["hooks"][0]["hook_id"]
    result = await service.cancel(scope=SCOPE, hook_ids=[hook_id])
    assert result["status"] == "ok" and result["effect_applied"] is True
    assert hook_id not in manager.active
    assert hook_id not in service._tasks
    assert manager.run_count == 0
    assert not manager.run_started.is_set()
    await service.terminate()


async def test_echo_after_boundary() -> None:
    manager = BlockingManager()
    service = EchoService(
        EchoLimits(default_delay_seconds=30, max_delay_seconds=300),
        manager,
        wall_clock=lambda: 1000.0,
        preflight=lambda _scope: True,
    )
    await service.initialize()
    created = await service.create(
        scope=SCOPE,
        sender_id=A,
        actor_ref="agent:test",
        source="native_wake",
        delay_seconds=30,
        count=1,
        interval_seconds=30,
        instruction="race test",
    )
    hook_id = created["hooks"][0]["hook_id"]
    timer = service._tasks.pop(hook_id)
    timer.cancel()
    await asyncio.gather(timer, return_exceptions=True)

    running = asyncio.create_task(service._execute_armed_hook(hook_id, SCOPE))
    await asyncio.wait_for(manager.run_started.wait(), timeout=1)

    # Cancellation after the native run boundary can remove all future handles, but
    # cannot reclaim the one Agent turn whose execution already started.
    result = await service.cancel(scope=SCOPE, hook_ids=[hook_id])
    assert result["status"] == "ok"
    assert hook_id not in manager.active
    assert hook_id not in service._tasks

    manager.run_release.set()
    await running
    assert manager.run_count == 1
    assert hook_id not in manager.active
    assert hook_id not in service._tasks
    await service.terminate()


async def test_heartbeat_boundary() -> None:
    manager = BlockingManager()
    parent = "hb-1"
    manager.active[parent] = Job(
        parent,
        heartbeat_payload(
            scope=SCOPE,
            sender_id=A,
            instruction="race heartbeat",
            name="race",
            created_at=1000.0,
            expires_at=5000.0,
            created_by="agent:test",
            source="tool",
        ),
        False,
    )
    gate = HeartbeatWakeGate(manager, preflight=lambda _scope: True, wall_clock=lambda: 1100.0)
    await gate.initialize()

    from astrbot_plugin_sender_activation.heartbeat_domain import HeartbeatLease

    lease = HeartbeatLease(
        lease_id=parent,
        scope=SCOPE,
        name="race",
        cron_expression="*/5 * * * *",
        instruction="race heartbeat",
        created_at=1000.0,
        expires_at=5000.0,
        created_by="agent:test",
        source="tool",
        enabled=False,
        plugin_suspended=False,
        status="pending",
    )
    await gate.arm(lease)
    driver = next(job for job in manager.basic.values() if DRIVER_TAG in job.payload)
    handler = manager.handlers[driver.job_id]

    # Before execution boundary: disarm removes recurrence; no Agent turn starts.
    await gate.disarm(parent)
    assert driver.job_id not in manager.basic
    assert not manager.run_started.is_set()

    # Re-arm and cross the native execution boundary.
    await gate.arm(lease)
    driver = next(job for job in manager.basic.values() if DRIVER_TAG in job.payload)
    handler = manager.handlers[driver.job_id]
    running = asyncio.create_task(handler(**driver.payload))
    await asyncio.wait_for(manager.run_started.wait(), timeout=1)

    await gate.disarm(parent)
    assert not any(DRIVER_TAG in job.payload for job in manager.basic.values())
    manager.run_release.set()
    await running
    assert manager.run_count == 1
    assert not any(DRIVER_TAG in job.payload for job in manager.basic.values())
    await gate.terminate()


async def main() -> None:
    await test_echo_before_boundary()
    await test_echo_after_boundary()
    await test_heartbeat_boundary()
    print("rc19 concurrency execution-boundary counterexamples: PASS")


if __name__ == "__main__":
    asyncio.run(main())
