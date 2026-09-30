from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from astrbot_plugin_sender_activation.harness_core import (  # noqa: E402
    EventEnvelope,
    GovernorDecision,
    ProgramRuntime,
    WatchContract,
    WatchRuntime,
    WakeIntent,
    claim_reconcile,
    due_watch_intent,
    govern_wake_intents,
    needs_reconcile,
    observe_watch,
    recheck_intent,
    reset_watch_runtime,
    restart_intent,
)


def event(event_id: str, *, sender: str = "10001", message: str = "hello") -> EventEnvelope:
    return EventEnvelope(
        id=event_id,
        source="astrbot://test/group",
        type="com.astrbot.qq.group.message",
        subject=sender,
        occurred_at=1.0,
        observed_at=1.0,
        payload_ref=f"message:{event_id}",
        data={"message": message},
    )


def test_watch_quantifier_and_settle_are_pure_reducers() -> None:
    watch = WatchContract(
        watch_id="w1",
        match_kind="any_message",
        match_values=(),
        quantifier_count=3,
        settle_seconds=3.0,
    )
    state = WatchRuntime()

    state1, first = observe_watch(watch, state, matched=True, event_ref="message:e1", now=10.0)
    assert first.matched is True
    assert first.candidate_created is False
    assert state.observed_since_reset == 0
    assert state1.observed_since_reset == 1

    state2, second = observe_watch(watch, state1, matched=True, event_ref="message:e2", now=11.0)
    assert second.candidate_created is False
    assert state2.observed_since_reset == 2

    state3, third = observe_watch(watch, state2, matched=True, event_ref="message:e3", now=12.0)
    assert third.candidate_created is True
    assert state3.pending is True
    assert state3.ready_at == 15.0

    state4, fourth = observe_watch(watch, state3, matched=True, event_ref="message:e4", now=13.0)
    assert fourth.debounced is True
    assert state4.ready_at == 16.0
    assert state4.pending_match_count == 4

    assert due_watch_intent(
        contract_id="p1",
        watch=watch,
        runtime=state4,
        now=15.9,
    ) is None
    intent = due_watch_intent(
        contract_id="p1",
        watch=watch,
        runtime=state4,
        now=16.0,
    )
    assert intent is not None
    assert intent.reason == "watch:w1"
    assert intent.event_refs == ("message:e4",)
    unchanged, ignored = observe_watch(
        watch,
        state4,
        matched=False,
        event_ref="message:ignored",
        now=17.0,
    )
    assert ignored.matched is False
    assert unchanged is state4
    assert reset_watch_runtime() == WatchRuntime()


def test_many_wake_intents_become_one_dirty_generation() -> None:
    runtime = ProgramRuntime()
    intents = [
        WakeIntent(
            contract_id="p1",
            reason="watch:a",
            source="watch",
            observed_at=20.0,
            event_refs=("m1",),
        ),
        WakeIntent(
            contract_id="p1",
            reason="watch:b",
            source="watch",
            observed_at=20.0,
            event_refs=("m2",),
        ),
        recheck_intent(contract_id="p1", now=20.0),
    ]

    transition = govern_wake_intents(
        runtime,
        intents,
        attention_key_running=False,
    )
    assert transition.decision == GovernorDecision.ALLOW_NOW
    assert transition.accepted_intents == 3
    assert transition.generation == 1
    assert transition.runtime.dirty_generation == 1
    assert transition.runtime.reconciled_generation == 0
    assert transition.runtime.pending_reasons == (
        "watch:a",
        "watch:b",
        "recheck",
    )
    assert transition.runtime.event_refs == ("m1", "m2")
    assert runtime == ProgramRuntime()


def test_already_dirty_or_running_is_coalesced() -> None:
    dirty = ProgramRuntime(
        dirty_generation=2,
        reconciled_generation=1,
        pending_reasons=("watch:a",),
    )
    transition = govern_wake_intents(
        dirty,
        [recheck_intent(contract_id="p1", now=30.0)],
        attention_key_running=False,
    )
    assert transition.decision == GovernorDecision.COALESCE
    assert transition.runtime.dirty_generation == 3

    clean = ProgramRuntime(dirty_generation=5, reconciled_generation=5)
    running = govern_wake_intents(
        clean,
        [restart_intent(contract_id="p1", now=30.0)],
        attention_key_running=True,
    )
    assert running.decision == GovernorDecision.COALESCE
    assert running.runtime.dirty_generation == 6


def test_claim_then_new_dirty_requires_one_more_reconcile() -> None:
    first = govern_wake_intents(
        ProgramRuntime(),
        [recheck_intent(contract_id="p1", now=40.0)],
        attention_key_running=False,
    ).runtime

    processing, claim = claim_reconcile(first)
    assert claim is not None
    assert claim.generation == 1
    assert claim.reasons == ("recheck",)
    assert needs_reconcile(processing) is False

    while_running = govern_wake_intents(
        processing,
        [
            WakeIntent(
                contract_id="p1",
                reason="watch:new",
                source="watch",
                observed_at=41.0,
                event_refs=("m3",),
            )
        ],
        attention_key_running=True,
    )
    assert while_running.decision == GovernorDecision.COALESCE
    assert while_running.runtime.dirty_generation == 2
    assert while_running.runtime.reconciled_generation == 1
    assert needs_reconcile(while_running.runtime) is True

    second_processing, second_claim = claim_reconcile(while_running.runtime)
    assert second_claim is not None
    assert second_claim.generation == 2
    assert second_claim.reasons == ("watch:new",)
    assert second_claim.event_refs == ("m3",)
    assert needs_reconcile(second_processing) is False


def test_event_refs_are_deduplicated_and_bounded() -> None:
    intents = [
        WakeIntent(
            contract_id="p1",
            reason=f"watch:{index}",
            source="watch",
            observed_at=float(index),
            event_refs=(f"m{index}", "shared"),
        )
        for index in range(5)
    ]
    transition = govern_wake_intents(
        ProgramRuntime(),
        intents,
        attention_key_running=False,
        max_event_refs=3,
    )
    assert transition.runtime.event_refs == ("m3", "m4", "shared")


def test_governor_rejects_cross_contract_batch() -> None:
    try:
        govern_wake_intents(
            ProgramRuntime(),
            [
                recheck_intent(contract_id="p1", now=1.0),
                recheck_intent(contract_id="p2", now=1.0),
            ],
            attention_key_running=False,
        )
    except ValueError as exc:
        assert "one Contract" in str(exc)
    else:
        raise AssertionError("cross-contract WakeIntent batch was accepted")


def test_zero_event_ref_budget_keeps_no_refs() -> None:
    transition = govern_wake_intents(
        ProgramRuntime(),
        [
            WakeIntent(
                contract_id="p1",
                reason="watch:a",
                source="watch",
                observed_at=1.0,
                event_refs=("m1", "m2"),
            )
        ],
        attention_key_running=False,
        max_event_refs=0,
    )
    assert transition.runtime.event_refs == ()


def test_empty_intent_batch_waits_without_mutation() -> None:
    runtime = ProgramRuntime(dirty_generation=7, reconciled_generation=7)
    transition = govern_wake_intents(
        runtime,
        [],
        attention_key_running=False,
    )
    assert transition.decision == GovernorDecision.WAIT
    assert transition.accepted_intents == 0
    assert transition.generation is None
    assert transition.runtime is runtime


def main() -> None:
    test_watch_quantifier_and_settle_are_pure_reducers()
    test_many_wake_intents_become_one_dirty_generation()
    test_already_dirty_or_running_is_coalesced()
    test_claim_then_new_dirty_requires_one_more_reconcile()
    test_event_refs_are_deduplicated_and_bounded()
    test_governor_rejects_cross_contract_batch()
    test_zero_event_ref_budget_keeps_no_refs()
    test_empty_intent_batch_waits_without_mutation()
    print("v1.3 harness core counterexamples: PASS")


if __name__ == "__main__":
    main()
