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

from astrbot_plugin_sender_activation.attention_program_service import (  # noqa: E402
    PROGRAM_KIND,
    PROGRAM_SCHEMA_VERSION,
    PROGRAM_TAG,
    AttentionProgramService,
    EventEnvelope,
    ProgramLimits,
)
from astrbot_plugin_sender_activation.storage import StoredDocuments  # noqa: E402

SCOPE = "aiocqhttp:GroupMessage:10001"
OWNER = "123456789"
TARGET = "987654321"


class MemoryStore:
    def __init__(
        self,
        *,
        primary: dict[str, Any] | None = None,
        backup: dict[str, Any] | None = None,
    ) -> None:
        self.primary = primary
        self.backup = backup

    async def load(self) -> StoredDocuments:
        return StoredDocuments(self.primary, self.backup)

    async def commit(self, previous_document, candidate_document) -> None:
        self.backup = dict(previous_document)
        self.primary = dict(candidate_document)


class ReadErrorStore(MemoryStore):
    async def load(self) -> StoredDocuments:
        return StoredDocuments(
            None,
            None,
            primary_error="SimulatedReadError",
            backup_error=None,
        )


@dataclass
class FakeJob:
    job_id: str
    payload: dict[str, Any]
    enabled: bool = False


class FakeManager:
    def __init__(self, *, block_runs: bool = False) -> None:
        self.jobs: dict[str, FakeJob] = {}
        self.next_id = 1
        self.run_payloads: list[dict[str, Any]] = []
        self.running = 0
        self.max_running = 0
        self.block_runs = block_runs
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        if not block_runs:
            self.release.set()

    async def list_jobs(self, job_type: str | None = None):
        return list(self.jobs.values())

    async def add_active_job(self, **kwargs):
        job_id = f"program-{self.next_id}"
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
        if job is None:
            return
        self.run_payloads.append(dict(job.payload))
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        self.entered.set()
        try:
            await self.release.wait()
        finally:
            self.running -= 1


def envelope(
    event_id: str,
    *,
    sender: str = TARGET,
    message: str = "hello",
) -> EventEnvelope:
    now = time.monotonic()
    return EventEnvelope(
        id=event_id,
        source="astrbot://test/group",
        type="com.astrbot.qq.group.message",
        subject=sender,
        occurred_at=now,
        observed_at=now,
        payload_ref=f"message:{event_id}",
        data={"message": message},
    )


async def new_service(
    *,
    store: MemoryStore | None = None,
    legacy_store: MemoryStore | None = None,
    manager: FakeManager | None = None,
) -> tuple[AttentionProgramService, MemoryStore, FakeManager]:
    store = store or MemoryStore()
    manager = manager or FakeManager()
    service = AttentionProgramService(
        ProgramLimits(
            max_per_scope=20,
            max_total=100,
            max_watches_per_program=8,
            max_quantifier_count=100,
            max_settle_seconds=1.0,
            max_recheck_seconds=10.0,
            max_lease_seconds=1000,
            max_dedupe_keys=64,
        ),
        store,
        manager,
        legacy_store=legacy_store,
        wall_clock=time.monotonic,
    )
    await service.initialize()
    return service, store, manager


async def create_program(
    service: AttentionProgramService,
    *,
    watches: list[dict[str, Any]],
    goal: str = "reconcile current world",
    recheck: str = "off",
    recheck_seconds: float = 0,
    controller: str = OWNER,
) -> str:
    result = await service.manage(
        scope=SCOPE,
        action="create",
        controller_sender_id=controller,
        actor_ref="agent:test",
        goal=goal,
        watches=watches,
        recheck=recheck,
        recheck_seconds=recheck_seconds,
        lease="custom",
        lease_seconds=30,
        program_ids=[],
    )
    return result["program_id"]


def sender_watch(*, settle: str = "immediate_0s", quantifier: str = "each") -> dict[str, Any]:
    return {
        "match": {"type": "sender", "values": [TARGET]},
        "quantifier": quantifier,
        "settle": settle,
    }


async def test_multi_watch_one_program_one_dirty_generation() -> None:
    service, _, manager = await new_service()
    program_id = await create_program(
        service,
        watches=[
            sender_watch(),
            {
                "match": {"type": "keyword", "values": ["完成"]},
                "quantifier": "each",
                "settle": "immediate_0s",
            },
        ],
        goal="多 Watch 是同一个开放目标",
    )

    result = await service.observe_event(
        scope=SCOPE,
        envelope=envelope("e1", message="今天完成了"),
    )
    assert len(result["matched_watches"]) == 2
    assert result["dirty_programs"] == [program_id]
    await asyncio.sleep(0.03)

    assert len(manager.run_payloads) == 1
    payload = manager.run_payloads[0]
    tag = payload[PROGRAM_TAG]
    assert tag["kind"] == PROGRAM_KIND
    assert tag["program_ids"] == [program_id]
    assert tag["generations"] == [1]
    assert payload["note"].count("Watch ") >= 2

    snap = await service.snapshot(scope=SCOPE)
    runtime = snap["programs"][0]["runtime"]
    assert runtime["dirty_generation"] == 1
    assert runtime["reconciled_generation"] == 1
    await service.terminate()


async def test_two_programs_same_attention_key_single_activation() -> None:
    service, _, manager = await new_service()
    p1 = await create_program(
        service,
        watches=[sender_watch()],
        goal="goal one",
    )
    p2 = await create_program(
        service,
        watches=[
            {
                "match": {"type": "keyword", "values": ["完成"]},
                "quantifier": "each",
                "settle": "immediate_0s",
            }
        ],
        goal="goal two",
    )

    await service.observe_event(
        scope=SCOPE,
        envelope=envelope("e2", message="完成"),
    )
    await asyncio.sleep(0.03)

    assert len(manager.run_payloads) == 1
    assert set(manager.run_payloads[0][PROGRAM_TAG]["program_ids"]) == {p1, p2}
    await service.terminate()


async def test_event_envelope_deduplicates_source_plus_id() -> None:
    service, _, manager = await new_service()
    program_id = await create_program(service, watches=[sender_watch()])
    event = envelope("same-id")

    first = await service.observe_event(scope=SCOPE, envelope=event)
    second = await service.observe_event(scope=SCOPE, envelope=event)
    assert first["duplicate"] is False
    assert second["duplicate"] is True

    await asyncio.sleep(0.03)
    assert len(manager.run_payloads) == 1
    assert manager.run_payloads[0][PROGRAM_TAG]["program_ids"] == [program_id]
    health = await service.health()
    assert health["program_duplicate_events_total"] == 1
    await service.terminate()


async def test_quantifier_and_quiet_period() -> None:
    service, _, manager = await new_service()
    program_id = await create_program(
        service,
        watches=[
            {
                "match": {"type": "any_message", "values": []},
                "quantifier": "every_3",
                "settle": "custom",
                "settle_seconds": 0.05,
            }
        ],
    )

    await service.observe_event(scope=SCOPE, envelope=envelope("q1"))
    await service.observe_event(scope=SCOPE, envelope=envelope("q2"))
    assert manager.run_payloads == []

    await service.observe_event(scope=SCOPE, envelope=envelope("q3"))
    await asyncio.sleep(0.025)
    await service.observe_event(scope=SCOPE, envelope=envelope("q4"))
    await asyncio.sleep(0.035)
    assert manager.run_payloads == []
    await asyncio.sleep(0.04)
    assert len(manager.run_payloads) == 1
    assert manager.run_payloads[0][PROGRAM_TAG]["program_ids"] == [program_id]
    await service.terminate()


async def test_dirty_while_running_requeues_once_without_concurrency() -> None:
    manager = FakeManager(block_runs=True)
    service, _, _ = await new_service(manager=manager)
    program_id = await create_program(service, watches=[sender_watch()])

    await service.observe_event(scope=SCOPE, envelope=envelope("r1"))
    await asyncio.wait_for(manager.entered.wait(), timeout=1)
    assert manager.running == 1
    assert len(manager.run_payloads) == 1

    # The same AttentionKey is processing. A new signal must only mark dirty.
    await service.observe_event(scope=SCOPE, envelope=envelope("r2"))
    await asyncio.sleep(0.02)
    assert manager.running == 1
    assert manager.max_running == 1
    assert len(manager.run_payloads) == 1

    # Let the first reconcile finish. Runtime must then run exactly one more.
    manager.release.set()
    await asyncio.sleep(0.06)
    assert len(manager.run_payloads) == 2
    assert manager.max_running == 1
    assert all(
        payload[PROGRAM_TAG]["program_ids"] == [program_id]
        for payload in manager.run_payloads
    )
    assert manager.run_payloads[0][PROGRAM_TAG]["generations"] == [1]
    assert manager.run_payloads[1][PROGRAM_TAG]["generations"] == [2]
    health = await service.health()
    assert health["program_reconcile_requeues_total"] >= 1
    await service.terminate()


async def test_recheck_counts_from_reconcile_completion() -> None:
    manager = FakeManager(block_runs=True)
    service, _, _ = await new_service(manager=manager)
    program_id = await create_program(
        service,
        watches=[sender_watch()],
        recheck="custom",
        recheck_seconds=0.08,
    )

    await service.observe_event(scope=SCOPE, envelope=envelope("w1"))
    await asyncio.wait_for(manager.entered.wait(), timeout=1)
    await asyncio.sleep(0.06)
    assert len(manager.run_payloads) == 1

    # Completion happens now; recheck must start from here, not create/dispatch time.
    manager.release.set()
    await asyncio.sleep(0.045)
    assert len(manager.run_payloads) == 1
    await asyncio.sleep(0.06)
    assert len(manager.run_payloads) == 2
    assert manager.run_payloads[1][PROGRAM_TAG]["program_ids"] == [program_id]
    assert "Recheck" in manager.run_payloads[1]["note"]
    await service.terminate()


async def test_program_without_watch_uses_recheck_only() -> None:
    service, _, manager = await new_service()
    program_id = await create_program(
        service,
        watches=[],
        recheck="custom",
        recheck_seconds=0.04,
        goal="纯时间复查无需 time_only 特殊 Watch",
    )
    await asyncio.sleep(0.07)
    assert len(manager.run_payloads) == 1
    assert manager.run_payloads[0][PROGRAM_TAG]["program_ids"] == [program_id]
    await service.terminate()


async def test_restart_marks_current_state_dirty_once() -> None:
    store = MemoryStore(
        primary={
            "schema_version": PROGRAM_SCHEMA_VERSION,
            "programs": [
                {
                    "program_id": "persisted-program",
                    "scope": SCOPE,
                    "controller_sender_id": OWNER,
                    "goal": "重启后重新读取当前世界",
                    "watches": [
                        {
                            "watch_id": "watch-1",
                            "match_kind": "sender",
                            "match_values": [TARGET],
                            "quantifier_count": 1,
                            "settle_seconds": 0,
                        }
                    ],
                    "recheck_seconds": None,
                    "created_at": time.monotonic() - 1,
                    "expires_at": time.monotonic() + 20,
                    "created_by": "agent:test",
                }
            ],
        }
    )
    service, _, manager = await new_service(store=store)
    await asyncio.sleep(0.04)

    assert len(manager.run_payloads) == 1
    payload = manager.run_payloads[0]
    assert payload[PROGRAM_TAG]["program_ids"] == ["persisted-program"]
    assert "重启后" in payload["note"]
    await service.terminate()


async def test_listener_v1_migration_preserves_ids() -> None:
    now = time.monotonic()
    legacy = MemoryStore(
        primary={
            "schema_version": 1,
            "listeners": [
                {
                    "listener_id": "legacy-user",
                    "scope": SCOPE,
                    "owner_sender_id": OWNER,
                    "condition_kind": "sender",
                    "condition_values": [TARGET],
                    "frequency_count": 3,
                    "settle_delay_seconds": 0.2,
                    "watchdog_seconds": 0.5,
                    "goal": "旧目标",
                    "created_at": now - 1,
                    "expires_at": now + 20,
                    "created_by": "agent:test",
                },
                {
                    "listener_id": "legacy-time",
                    "scope": SCOPE,
                    "owner_sender_id": OWNER,
                    "condition_kind": "time_only",
                    "condition_values": [],
                    "frequency_count": 1,
                    "settle_delay_seconds": 0,
                    "watchdog_seconds": 0.5,
                    "goal": "旧纯时间目标",
                    "created_at": now - 1,
                    "expires_at": now + 20,
                    "created_by": "agent:test",
                },
            ],
        }
    )
    manager = FakeManager(block_runs=True)
    service, store, _ = await new_service(
        store=MemoryStore(),
        legacy_store=legacy,
        manager=manager,
    )
    snapshot = await service.snapshot(scope=SCOPE)
    by_id = {row["program_id"]: row for row in snapshot["programs"]}
    assert set(by_id) == {"legacy-user", "legacy-time"}
    assert len(by_id["legacy-user"]["watches"]) == 1
    assert by_id["legacy-user"]["watches"][0]["quantifier_count"] == 3
    assert by_id["legacy-user"]["watches"][0]["settle_seconds"] == 0.2
    assert by_id["legacy-time"]["watches"] == []
    assert by_id["legacy-time"]["recheck_seconds"] == 0.5
    assert store.primary["schema_version"] == PROGRAM_SCHEMA_VERSION
    health = await service.health()
    assert health["program_migrated_from_listener_v1"] is True
    await service.terminate()


async def test_corrupt_v2_never_resurrects_stale_v1() -> None:
    now = time.monotonic()
    corrupt_v2 = MemoryStore(
        primary={"schema_version": PROGRAM_SCHEMA_VERSION, "programs": "broken"},
    )
    legacy = MemoryStore(
        primary={
            "schema_version": 1,
            "listeners": [
                {
                    "listener_id": "stale",
                    "scope": SCOPE,
                    "owner_sender_id": OWNER,
                    "condition_kind": "sender",
                    "condition_values": [TARGET],
                    "frequency_count": 1,
                    "settle_delay_seconds": 0,
                    "watchdog_seconds": None,
                    "goal": "不应复活",
                    "created_at": now - 1,
                    "expires_at": now + 20,
                    "created_by": "agent:test",
                }
            ],
        }
    )
    service, _, manager = await new_service(
        store=corrupt_v2,
        legacy_store=legacy,
    )
    assert service.storage_ready is False
    assert service.active is False
    assert (await service.snapshot(scope=SCOPE))["programs"] == []
    assert manager.run_payloads == []


async def test_legacy_read_error_blocks_empty_startup() -> None:
    service, _, manager = await new_service(
        store=MemoryStore(),
        legacy_store=ReadErrorStore(),
    )
    assert service.storage_ready is False
    assert service.active is False
    assert service.loaded_from == "legacy_read_error"
    assert service.last_error_code.startswith("legacy_listener_slot_read_failed:")
    assert manager.run_payloads == []


async def test_unrecognized_legacy_state_blocks_empty_startup() -> None:
    legacy = MemoryStore(
        primary={
            "schema_version": 999,
            "listeners": [],
        }
    )
    service, _, manager = await new_service(
        store=MemoryStore(),
        legacy_store=legacy,
    )
    assert service.storage_ready is False
    assert service.active is False
    assert service.loaded_from == "legacy_invalid"
    assert service.last_error_code == "legacy_listener_state_unrecognized"
    assert manager.run_payloads == []


async def test_v2_read_error_never_looks_like_absent_state() -> None:
    now = time.monotonic()
    legacy = MemoryStore(
        primary={
            "schema_version": 1,
            "listeners": [
                {
                    "listener_id": "must-not-migrate",
                    "scope": SCOPE,
                    "owner_sender_id": OWNER,
                    "condition_kind": "sender",
                    "condition_values": [TARGET],
                    "frequency_count": 1,
                    "settle_delay_seconds": 0,
                    "watchdog_seconds": None,
                    "goal": "读取失败时不能复活",
                    "created_at": now - 1,
                    "expires_at": now + 20,
                    "created_by": "agent:test",
                }
            ],
        }
    )
    service, _, manager = await new_service(
        store=ReadErrorStore(),
        legacy_store=legacy,
    )
    assert service.storage_ready is False
    assert service.active is False
    assert service.loaded_from == "read_error"
    assert service.last_error_code.startswith("program_storage_slot_read_failed:")
    assert (await service.snapshot(scope=SCOPE))["programs"] == []
    assert manager.run_payloads == []


async def test_update_preserves_controller_and_replaces_desired_watches() -> None:
    service, _, _ = await new_service()
    program_id = await create_program(service, watches=[sender_watch()])
    result = await service.manage(
        scope=SCOPE,
        action="update",
        controller_sender_id="111111111",
        actor_ref="agent:other",
        program_ids=[program_id],
        goal="updated goal",
        watches=[
            {
                "match": {"type": "keyword", "values": ["更新"]},
                "quantifier": "each",
                "settle": "normal_1s",
            }
        ],
        recheck="off",
        lease="custom",
        lease_seconds=30,
    )
    program = result["program"]
    assert program["controller_sender_id"] == OWNER
    assert program["goal"] == "updated goal"
    assert len(program["watches"]) == 1
    assert program["watches"][0]["match_kind"] == "keyword"
    await service.terminate()


async def main() -> None:
    await test_multi_watch_one_program_one_dirty_generation()
    await test_two_programs_same_attention_key_single_activation()
    await test_event_envelope_deduplicates_source_plus_id()
    await test_quantifier_and_quiet_period()
    await test_dirty_while_running_requeues_once_without_concurrency()
    await test_recheck_counts_from_reconcile_completion()
    await test_program_without_watch_uses_recheck_only()
    await test_restart_marks_current_state_dirty_once()
    await test_listener_v1_migration_preserves_ids()
    await test_corrupt_v2_never_resurrects_stale_v1()
    await test_legacy_read_error_blocks_empty_startup()
    await test_unrecognized_legacy_state_blocks_empty_startup()
    await test_v2_read_error_never_looks_like_absent_state()
    await test_update_preserves_controller_and_replaces_desired_watches()
    print("v1.2 rc2 attention program counterexamples: PASS")


if __name__ == "__main__":
    asyncio.run(main())
