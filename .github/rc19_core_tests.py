from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_sender_activation.attention_domain import IgnoreLimits  # noqa: E402
from astrbot_plugin_sender_activation.attention_service import (  # noqa: E402
    AttentionIgnoreService,
)
from astrbot_plugin_sender_activation.echo_service import (  # noqa: E402
    ECHO_KIND,
    ECHO_TAG,
    EchoLimits,
    EchoService,
)
from astrbot_plugin_sender_activation.domain import DomainError  # noqa: E402
from astrbot_plugin_sender_activation.storage import (  # noqa: E402
    CommitIndeterminateError,
    StoredDocuments,
)

SCOPE = "aiocqhttp:GroupMessage:10001"
A = "123456789"
B = "987654321"


class Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class MemoryStore:
    def __init__(self) -> None:
        self.primary: dict[str, Any] | None = None
        self.backup: dict[str, Any] | None = None
        self.fail_mode: str | None = None
        self.commits = 0

    async def load(self) -> StoredDocuments:
        return StoredDocuments(self.primary, self.backup)

    async def commit(self, previous_document, candidate_document) -> None:
        self.commits += 1
        if self.fail_mode == "indeterminate":
            raise CommitIndeterminateError("simulated")
        if self.fail_mode == "write":
            raise OSError("simulated")
        self.backup = dict(previous_document)
        self.primary = dict(candidate_document)


async def set_policy(
    service: AttentionIgnoreService,
    *,
    target: str = A,
    trigger_count: int = 3,
    window: float = 10.0,
    ignore_seconds: int = 20,
    policy_seconds: int = 100,
):
    return await service.manage(
        scope=SCOPE,
        action="set",
        target_ids=[target],
        trigger_count=trigger_count,
        trigger_window_seconds=window,
        ignore_duration_seconds=ignore_seconds,
        policy_seconds=policy_seconds,
        created_by="agent:test",
    )


async def test_ignore_core() -> None:
    wall = Clock()
    mono = Clock()
    store = MemoryStore()
    limits = IgnoreLimits(
        default_ignore_seconds=10,
        max_ignore_seconds=1000,
        default_policy_seconds=100,
        max_policy_seconds=1000,
        default_trigger_count=1,
        max_trigger_count=100,
        default_trigger_window_seconds=0.0,
        max_trigger_window_seconds=1000.0,
        max_per_scope=10,
        max_total=20,
    )
    service = AttentionIgnoreService(limits, store, wall_clock=wall, monotonic_clock=mono)
    await service.initialize()
    assert service.storage_ready

    # Counterexample: A's policy must not make B pay a global policy check/penalty.
    await set_policy(service, trigger_count=3, window=10, ignore_seconds=20, policy_seconds=100)
    assert service.has_policy(SCOPE, A)
    assert not service.has_policy(SCOPE, B)
    assert (SCOPE, A) in service._policy_map  # exact-key hot index
    assert len(service._policy_map) == 1
    b = service.evaluate_activation_attempt(SCOPE, B)
    assert not b.matched and not b.ignored

    # Nth activation attempt itself is blocked; N+1 stays blocked without an LLM turn.
    d1 = service.evaluate_activation_attempt(SCOPE, A)
    d2 = service.evaluate_activation_attempt(SCOPE, A)
    d3 = service.evaluate_activation_attempt(SCOPE, A)
    d4 = service.evaluate_activation_attempt(SCOPE, A)
    assert (d1.ignored, d1.observed_count) == (False, 1)
    assert (d2.ignored, d2.observed_count) == (False, 2)
    assert d3.ignored and d3.triggered and d3.observed_count == 3
    assert d4.ignored and not d4.triggered and d4.reason == "ignore_active"

    # Ignore expiration restarts counting; it must not become a permanent object block.
    wall.advance(21)
    mono.advance(21)
    d5 = service.evaluate_activation_attempt(SCOPE, A)
    assert not d5.ignored and d5.observed_count == 1

    # Sliding-window counterexample: an old event outside the window must not contribute.
    await service.manage(
        scope=SCOPE,
        action="clear",
        target_ids=[A],
        trigger_count=0,
        trigger_window_seconds=0,
        ignore_duration_seconds=0,
        policy_seconds=0,
        created_by="agent:test",
    )
    await set_policy(service, trigger_count=3, window=5, ignore_seconds=20, policy_seconds=100)
    assert service.evaluate_activation_attempt(SCOPE, A).observed_count == 1
    wall.advance(6)
    mono.advance(6)
    after_gap = service.evaluate_activation_attempt(SCOPE, A)
    assert not after_gap.ignored and after_gap.observed_count == 1
    assert not service.evaluate_activation_attempt(SCOPE, A).ignored
    assert service.evaluate_activation_attempt(SCOPE, A).ignored

    # Policy lifetime caps the runtime block even when ignore_duration is much larger.
    await service.manage(
        scope=SCOPE,
        action="clear",
        target_ids=[A],
        trigger_count=0,
        trigger_window_seconds=0,
        ignore_duration_seconds=0,
        policy_seconds=0,
        created_by="agent:test",
    )
    await set_policy(service, trigger_count=1, window=0, ignore_seconds=100, policy_seconds=3)
    short = service.evaluate_activation_attempt(SCOPE, A)
    assert short.ignored and 0 < short.retry_after_seconds <= 3
    wall.advance(4)
    mono.advance(4)
    assert not service.has_policy(SCOPE, A)
    assert not service.evaluate_activation_attempt(SCOPE, A).ignored

    # Runtime counters are deliberately not persistent. Restart is fail-open, never a stale block.
    await service.manage(
        scope=SCOPE,
        action="clear",
        target_ids=[A],
        trigger_count=0,
        trigger_window_seconds=0,
        ignore_duration_seconds=0,
        policy_seconds=0,
        created_by="agent:test",
    )
    await set_policy(service, trigger_count=2, window=60, ignore_seconds=20, policy_seconds=100)
    first = service.evaluate_activation_attempt(SCOPE, A)
    assert first.observed_count == 1 and not first.ignored
    restarted = AttentionIgnoreService(limits, store, wall_clock=wall, monotonic_clock=mono)
    await restarted.initialize()
    post_restart = restarted.evaluate_activation_attempt(SCOPE, A)
    assert post_restart.observed_count == 1 and not post_restart.ignored

    # Indeterminate storage mutation must disable filtering (fail-open) rather than risk stale ignore.
    store.fail_mode = "indeterminate"
    try:
        await restarted.manage(
            scope=SCOPE,
            action="clear",
            target_ids=[A],
            trigger_count=0,
            trigger_window_seconds=0,
            ignore_duration_seconds=0,
            policy_seconds=0,
            created_by="agent:test",
        )
    except DomainError as exc:
        assert exc.code == "attention_commit_indeterminate"
    else:
        raise AssertionError("commit-indeterminate mutation unexpectedly succeeded")
    assert not restarted.storage_ready
    assert not restarted.has_policy(SCOPE, A)
    assert not restarted.evaluate_activation_attempt(SCOPE, A).ignored


@dataclass
class FakeJob:
    job_id: str
    payload: dict[str, Any]
    enabled: bool = True
    status: str = "pending"
    cron_expression: str | None = None


class FakeCronManager:
    def __init__(self) -> None:
        self.jobs: dict[str, FakeJob] = {}
        self.calls: list[dict[str, Any]] = []
        self.run_calls: list[str] = []
        self.next_id = 1
        self.fail_on_add_number: int | None = None
        self.fail_delete_ids: set[str] = set()

    async def list_jobs(self, job_type: str | None = None):
        return list(self.jobs.values())

    async def add_active_job(self, **kwargs):
        call_number = len(self.calls) + 1
        self.calls.append(dict(kwargs))
        if self.fail_on_add_number == call_number:
            raise RuntimeError("simulated add failure")
        job_id = f"echo-{self.next_id}"
        self.next_id += 1
        job = FakeJob(
            job_id=job_id,
            payload=dict(kwargs["payload"]),
            enabled=bool(kwargs.get("enabled", True)),
            cron_expression=kwargs.get("cron_expression"),
        )
        self.jobs[job_id] = job
        return job

    async def delete_job(self, job_id: str):
        if job_id in self.fail_delete_ids:
            raise OSError("simulated delete failure")
        self.jobs.pop(job_id, None)

    async def run_job_now(self, job_id: str):
        if job_id in self.jobs:
            self.run_calls.append(job_id)


async def test_echo_core() -> None:
    wall = Clock(2000.0)
    manager = FakeCronManager()
    limits = EchoLimits(
        default_delay_seconds=2,
        max_delay_seconds=300,
        default_count=1,
        max_count=3,
        default_interval_seconds=4,
        max_interval_seconds=300,
        max_pending_per_scope=5,
        max_pending_total=10,
    )
    service = EchoService(limits, manager, wall_clock=wall)
    await service.initialize()

    # Count>1 is a finite batch scheduled up front, never recursion.
    result = await service.create(
        scope=SCOPE,
        sender_id=A,
        actor_ref="agent:test",
        source="native_wake",
        delay_seconds=2,
        count=3,
        interval_seconds=4,
        instruction="检查刚才的话题是否出现真正相关的新对话。",
    )
    assert result["changed"] and len(result["hooks"]) == 3
    assert len(manager.calls) == 3
    for index, call in enumerate(manager.calls):
        assert call["run_once"] is True
        assert call["persistent"] is False
        assert call["enabled"] is False
        assert call["cron_expression"] is None
        tag = call["payload"][ECHO_TAG]
        assert tag["kind"] == ECHO_KIND
        assert tag["batch_count"] == 3
        assert tag["batch_index"] == index + 1
        assert tag["source"] == "native_wake"
    run_times = [call["run_at"].timestamp() for call in manager.calls]
    assert run_times == [2002.0, 2006.0, 2010.0]

    # Structural rule: an Echo turn can never grant itself another Echo, regardless of config.
    try:
        await service.create(
            scope=SCOPE,
            sender_id=A,
            actor_ref="agent:test",
            source="echo",
            delay_seconds=2,
            count=1,
            interval_seconds=4,
            instruction="recursive",
        )
    except DomainError as exc:
        assert exc.code == "echo_recursion_forbidden"
    else:
        raise AssertionError("recursive echo unexpectedly accepted")

    # Scope cancellation removes pending hooks without touching unrelated scope jobs.
    other_scope = "aiocqhttp:GroupMessage:20002"
    other = await service.create(
        scope=other_scope,
        sender_id=B,
        actor_ref="agent:test",
        source="sender_activation",
        delay_seconds=3,
        count=1,
        interval_seconds=4,
        instruction="other scope",
    )
    assert other["hooks"]
    removed = await service.cancel_scope(SCOPE)
    assert len(removed) == 3
    snapshot_other = await service.snapshot(scope=other_scope)
    assert len(snapshot_other["echo_hooks"]) == 1

    # Partial native scheduling failure rolls back everything created by that request.
    manager2 = FakeCronManager()
    manager2.fail_on_add_number = 2
    rollback_service = EchoService(limits, manager2, wall_clock=wall)
    await rollback_service.initialize()
    try:
        await rollback_service.create(
            scope=SCOPE,
            sender_id=A,
            actor_ref="agent:test",
            source="heartbeat",
            delay_seconds=2,
            count=3,
            interval_seconds=4,
            instruction="rollback test",
        )
    except DomainError as exc:
        assert exc.code == "echo_create_failed"
    else:
        raise AssertionError("partial scheduling failure unexpectedly succeeded")
    assert manager2.jobs == {}

    # Permission/session preflight runs before any main-Agent execution.
    gate = {"allowed": False}
    gated_manager = FakeCronManager()
    gated = EchoService(
        limits,
        gated_manager,
        wall_clock=wall,
        preflight=lambda _scope: gate["allowed"],
    )
    await gated.initialize()
    blocked = await gated.create(
        scope=SCOPE,
        sender_id=A,
        actor_ref="agent:test",
        source="native_wake",
        delay_seconds=2,
        count=1,
        interval_seconds=4,
        instruction="preflight block",
    )
    blocked_id = blocked["hooks"][0]["hook_id"]
    timer = gated._tasks.pop(blocked_id)
    timer.cancel()
    await asyncio.gather(timer, return_exceptions=True)
    await gated._execute_armed_hook(blocked_id, SCOPE)
    assert gated_manager.run_calls == []
    assert blocked_id not in gated_manager.jobs
    assert (await gated.health())["echo_preflight_suppressed_total"] == 1

    gate["allowed"] = True
    allowed = await gated.create(
        scope=SCOPE,
        sender_id=A,
        actor_ref="agent:test",
        source="native_wake",
        delay_seconds=2,
        count=1,
        interval_seconds=4,
        instruction="preflight allow",
    )
    allowed_id = allowed["hooks"][0]["hook_id"]
    timer = gated._tasks.pop(allowed_id)
    timer.cancel()
    await asyncio.gather(timer, return_exceptions=True)
    await gated._execute_armed_hook(allowed_id, SCOPE)
    assert gated_manager.run_calls == [allowed_id]
    assert allowed_id not in gated_manager.jobs
    await gated.terminate()

    # Counterexample: native delete failure must never be reported as successful cancel.
    delete_manager = FakeCronManager()
    delete_service = EchoService(limits, delete_manager, wall_clock=wall)
    await delete_service.initialize()
    doomed = await delete_service.create(
        scope=SCOPE,
        sender_id=A,
        actor_ref="agent:test",
        source="native_wake",
        delay_seconds=20,
        count=1,
        interval_seconds=4,
        instruction="delete failure",
    )
    doomed_id = doomed["hooks"][0]["hook_id"]
    delete_manager.fail_delete_ids.add(doomed_id)
    cancelled = await delete_service.cancel(scope=SCOPE, hook_ids=[doomed_id])
    assert cancelled["status"] == "error"
    assert cancelled["error_code"] == "echo_delete_failed"
    assert cancelled["cancelled_hook_ids"] == []
    assert cancelled["failed_hook_ids"] == [doomed_id]
    assert cancelled["effect_applied"] is False
    assert doomed_id in delete_manager.jobs
    # Timer was still stopped, so the surviving disabled row cannot later self-activate.
    assert doomed_id not in delete_service._tasks
    delete_manager.fail_delete_ids.clear()
    await delete_service.terminate()

    # Restart/reload cannot resurrect pending Echo hooks: initialization deletes stale owned rows.
    stale_manager = FakeCronManager()
    stale_payload = {
        "session": SCOPE,
        "sender_id": A,
        ECHO_TAG: {
            "kind": ECHO_KIND,
            "schema_version": 1,
            "group_id": "stale-group",
            "source": "native_wake",
            "instruction": "stale",
            "created_at": 1900.0,
            "run_at": 2100.0,
            "created_by": "agent:test",
            "batch_index": 1,
            "batch_count": 1,
        },
    }
    stale_manager.jobs["stale-1"] = FakeJob("stale-1", stale_payload)
    cleanup_service = EchoService(limits, stale_manager, wall_clock=wall)
    await cleanup_service.initialize()
    assert stale_manager.jobs == {}
    await service.terminate()
    await cleanup_service.terminate()


async def main() -> None:
    await test_ignore_core()
    await test_echo_core()
    print("rc19 counterexample core tests: PASS")


if __name__ == "__main__":
    asyncio.run(main())
