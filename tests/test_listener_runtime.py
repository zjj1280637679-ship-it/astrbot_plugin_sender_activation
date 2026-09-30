from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_sender_activation.listener_service import (  # noqa: E402
    LISTENER_TAG,
    ListenerLimits,
    ListenerService,
)
from astrbot_plugin_sender_activation.storage import StoredDocuments  # noqa: E402

SCOPE = "aiocqhttp:GroupMessage:10001"
OWNER = "123456789"
TARGET = "987654321"


class MemoryStore:
    def __init__(self) -> None:
        self.primary: dict[str, Any] | None = None
        self.backup: dict[str, Any] | None = None

    async def load(self) -> StoredDocuments:
        return StoredDocuments(self.primary, self.backup)

    async def commit(self, previous_document, candidate_document) -> None:
        self.backup = dict(previous_document)
        self.primary = dict(candidate_document)


@dataclass
class FakeJob:
    job_id: str
    payload: dict[str, Any]
    enabled: bool = False
    status: str = "pending"


class FakeManager:
    def __init__(self) -> None:
        self.jobs: dict[str, FakeJob] = {}
        self.next_id = 1
        self.run_payloads: list[dict[str, Any]] = []

    async def list_jobs(self, job_type: str | None = None):
        return list(self.jobs.values())

    async def add_active_job(self, **kwargs):
        job_id = f"listener-{self.next_id}"
        self.next_id += 1
        job = FakeJob(
            job_id=job_id,
            payload=dict(kwargs.get("payload") or {}),
            enabled=bool(kwargs.get("enabled", False)),
        )
        self.jobs[job_id] = job
        return job

    async def delete_job(self, job_id: str):
        self.jobs.pop(job_id, None)

    async def run_job_now(self, job_id: str):
        job = self.jobs.get(job_id)
        if job is not None:
            self.run_payloads.append(dict(job.payload))
        await asyncio.sleep(0)


async def new_service(
    *,
    store: MemoryStore | None = None,
    manager: FakeManager | None = None,
) -> tuple[ListenerService, MemoryStore, FakeManager]:
    store = store or MemoryStore()
    manager = manager or FakeManager()
    service = ListenerService(
        ListenerLimits(
            max_per_scope=20,
            max_total=100,
            max_frequency_count=100,
            max_settle_seconds=1.0,
            max_watchdog_seconds=10.0,
            max_lifetime_seconds=1000,
        ),
        store,
        manager,
        wall_clock=time.monotonic,
    )
    await service.initialize()
    return service, store, manager


async def start(
    service: ListenerService,
    *,
    condition_kind: str,
    condition_values: list[str] | None = None,
    frequency: str = "each",
    response_speed: str = "immediate_0s",
    settle_delay_seconds: float = 0,
    watchdog: str = "off",
    watchdog_seconds: float = 0,
    goal: str = "test goal",
) -> str:
    result = await service.manage(
        scope=SCOPE,
        action="start",
        owner_sender_id=OWNER,
        actor_ref="agent:test",
        condition_kind=condition_kind,
        condition_values=condition_values or [],
        frequency=frequency,
        frequency_count=0,
        response_speed=response_speed,
        settle_delay_seconds=settle_delay_seconds,
        watchdog=watchdog,
        watchdog_seconds=watchdog_seconds,
        lifetime="custom",
        lifetime_seconds=30,
        goal=goal,
        listener_ids=[],
    )
    return result["listener_id"]


async def test_debounce_keeps_one_activation() -> None:
    service, _, manager = await new_service()
    listener_id = await start(
        service,
        condition_kind="sender",
        condition_values=[TARGET],
        response_speed="custom",
        settle_delay_seconds=0.05,
    )

    first = await service.observe_event(
        scope=SCOPE,
        sender_id=TARGET,
        message="first",
    )
    assert first["candidates"] == [listener_id]
    await asyncio.sleep(0.025)
    second = await service.observe_event(
        scope=SCOPE,
        sender_id=TARGET,
        message="second",
    )
    assert second["candidates"] == [listener_id]

    await asyncio.sleep(0.035)
    assert manager.run_payloads == []
    await asyncio.sleep(0.04)
    assert len(manager.run_payloads) == 1
    payload = manager.run_payloads[0]
    assert payload[LISTENER_TAG]["listener_ids"] == [listener_id]
    assert "累计命中 2 次" in payload["note"]
    await service.terminate()


async def test_frequency_threshold() -> None:
    service, _, manager = await new_service()
    listener_id = await start(
        service,
        condition_kind="any_message",
        frequency="every_3",
    )
    for index in range(2):
        await service.observe_event(
            scope=SCOPE,
            sender_id=TARGET,
            message=f"m{index}",
        )
        await asyncio.sleep(0.01)
    assert manager.run_payloads == []

    await service.observe_event(scope=SCOPE, sender_id=TARGET, message="m3")
    await asyncio.sleep(0.03)
    assert len(manager.run_payloads) == 1
    assert manager.run_payloads[0][LISTENER_TAG]["listener_ids"] == [listener_id]
    await service.terminate()


async def test_same_scope_ready_signals_are_normalized() -> None:
    service, _, manager = await new_service()
    first = await start(
        service,
        condition_kind="keyword",
        condition_values=["瑜伽"],
        goal="汇总瑜伽练习",
    )
    second = await start(
        service,
        condition_kind="keyword",
        condition_values=["完成"],
        goal="检查完成情况",
    )

    await service.observe_event(
        scope=SCOPE,
        sender_id=TARGET,
        message="瑜伽完成",
    )
    await asyncio.sleep(0.03)

    assert len(manager.run_payloads) == 1
    ids = set(manager.run_payloads[0][LISTENER_TAG]["listener_ids"])
    assert ids == {first, second}
    await service.terminate()


async def test_watchdog_resets_after_real_activation() -> None:
    service, _, manager = await new_service()
    listener_id = await start(
        service,
        condition_kind="sender",
        condition_values=[TARGET],
        watchdog="custom",
        watchdog_seconds=0.08,
    )

    await asyncio.sleep(0.04)
    await service.observe_event(
        scope=SCOPE,
        sender_id=TARGET,
        message="activity",
    )
    await asyncio.sleep(0.025)
    assert len(manager.run_payloads) == 1

    # The original watchdog would have fired around now if it had not been reset.
    await asyncio.sleep(0.035)
    assert len(manager.run_payloads) == 1

    # It is re-armed from completion of the real activation.
    await asyncio.sleep(0.06)
    assert len(manager.run_payloads) == 2
    second = manager.run_payloads[1]
    assert second[LISTENER_TAG]["listener_ids"] == [listener_id]
    assert "兜底检查时间已到" in second["note"]
    await service.terminate()


async def test_watchdog_signal_uses_same_settle_delay() -> None:
    service, _, manager = await new_service()
    listener_id = await start(
        service,
        condition_kind="time_only",
        response_speed="custom",
        settle_delay_seconds=0.05,
        watchdog="custom",
        watchdog_seconds=0.05,
        goal="定时信号也必须先归一化",
    )

    # Watchdog becomes a signal around 50ms, but the Agent must not run until
    # the listener's own 50ms settle window also completes.
    await asyncio.sleep(0.075)
    assert manager.run_payloads == []
    await asyncio.sleep(0.055)
    assert len(manager.run_payloads) == 1
    assert manager.run_payloads[0][LISTENER_TAG]["listener_ids"] == [listener_id]
    assert "兜底检查时间已到" in manager.run_payloads[0]["note"]
    await service.terminate()


async def test_time_only_watchdog_can_wake_without_messages() -> None:
    service, _, manager = await new_service()
    listener_id = await start(
        service,
        condition_kind="time_only",
        watchdog="custom",
        watchdog_seconds=0.04,
        goal="没人叫我也重新检查开放目标",
    )
    await asyncio.sleep(0.07)
    assert len(manager.run_payloads) == 1
    assert manager.run_payloads[0][LISTENER_TAG]["listener_ids"] == [listener_id]
    await service.terminate()


async def test_cancel_and_restart_contract() -> None:
    service, store, manager = await new_service()
    listener_id = await start(
        service,
        condition_kind="sender",
        condition_values=[TARGET],
        watchdog="custom",
        watchdog_seconds=0.5,
    )
    assert service.owns_all(
        scope=SCOPE,
        owner_sender_id=OWNER,
        listener_ids=[listener_id],
    )
    await service.terminate()

    restarted, _, _ = await new_service(store=store, manager=manager)
    snapshot = await restarted.snapshot(scope=SCOPE)
    assert [row["listener_id"] for row in snapshot["listeners"]] == [listener_id]

    cancelled = await restarted.manage(
        scope=SCOPE,
        action="cancel",
        owner_sender_id=None,
        actor_ref="agent:test",
        listener_ids=[listener_id],
    )
    assert cancelled["effect_applied"] is True
    assert (await restarted.snapshot(scope=SCOPE))["listeners"] == []
    await restarted.terminate()


async def main() -> None:
    await test_debounce_keeps_one_activation()
    await test_frequency_threshold()
    await test_same_scope_ready_signals_are_normalized()
    await test_watchdog_resets_after_real_activation()
    await test_watchdog_signal_uses_same_settle_delay()
    await test_time_only_watchdog_can_wake_without_messages()
    await test_cancel_and_restart_contract()
    print("v1.2 listener runtime counterexamples: PASS")


if __name__ == "__main__":
    asyncio.run(main())
