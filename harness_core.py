from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class EventEnvelope:
    """Transient, transport-neutral event facts.

    Inspired by CloudEvents core attributes. data is matching material only;
    callers decide whether any payload is persisted.
    """

    id: str
    source: str
    type: str
    subject: str | None
    occurred_at: float | None
    observed_at: float
    payload_ref: str | None
    data: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def dedupe_key(self) -> tuple[str, str]:
        return (self.source, self.id)

    def ref(self) -> str:
        return self.payload_ref or f"{self.source}#{self.id}"


@dataclass(frozen=True)
class WatchContract:
    watch_id: str
    match_kind: str
    match_values: tuple[str, ...]
    quantifier_count: int
    settle_seconds: float

    def matches(self, envelope: EventEnvelope) -> bool:
        if envelope.type != "com.astrbot.qq.group.message":
            return False
        sender_id = str(envelope.subject or "").strip()
        message = str(envelope.data.get("message") or "")
        if self.match_kind == "sender":
            return sender_id in self.match_values
        if self.match_kind == "keyword":
            haystack = message.casefold()
            return any(value.casefold() in haystack for value in self.match_values)
        if self.match_kind == "any_message":
            return True
        return False

    def as_record(self) -> dict[str, Any]:
        return {
            "watch_id": self.watch_id,
            "match_kind": self.match_kind,
            "match_values": list(self.match_values),
            "quantifier_count": self.quantifier_count,
            "settle_seconds": self.settle_seconds,
        }


@dataclass(frozen=True)
class AttentionProgram:
    program_id: str
    scope: str
    controller_sender_id: str
    goal: str
    watches: tuple[WatchContract, ...]
    recheck_seconds: float | None
    created_at: float
    expires_at: float
    created_by: str

    @property
    def attention_key(self) -> tuple[str, str]:
        return (self.scope, self.controller_sender_id)

    def as_record(self) -> dict[str, Any]:
        return {
            "program_id": self.program_id,
            "scope": self.scope,
            "controller_sender_id": self.controller_sender_id,
            "goal": self.goal,
            "watches": [watch.as_record() for watch in self.watches],
            "recheck_seconds": self.recheck_seconds,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "created_by": self.created_by,
        }


@dataclass(frozen=True)
class WatchRuntime:
    observed_since_reset: int = 0
    pending: bool = False
    ready_at: float = 0.0
    first_signal_at: float = 0.0
    last_signal_at: float = 0.0
    pending_match_count: int = 0
    latest_event_ref: str | None = None


@dataclass(frozen=True)
class ProgramRuntime:
    dirty_generation: int = 0
    reconciled_generation: int = 0
    pending_reasons: tuple[str, ...] = ()
    event_refs: tuple[str, ...] = ()

    @property
    def dirty(self) -> bool:
        return self.dirty_generation > self.reconciled_generation


@dataclass(frozen=True)
class WatchObservation:
    matched: bool
    candidate_created: bool = False
    debounced: bool = False


@dataclass(frozen=True)
class WakeIntent:
    """A fact that a Contract may deserve another Turn.

    A WakeIntent is not an Agent command. It contains only enough information
    for the Governor to mark the Contract dirty and explain why.
    """

    contract_id: str
    reason: str
    source: str
    observed_at: float
    event_refs: tuple[str, ...] = ()


class GovernorDecision(str, Enum):
    ALLOW_NOW = "allow_now"
    WAIT = "wait"
    COALESCE = "coalesce"
    DENY = "deny"


@dataclass(frozen=True)
class GovernorTransition:
    decision: GovernorDecision
    runtime: ProgramRuntime
    accepted_intents: int
    generation: int | None


@dataclass(frozen=True)
class ReconcileClaim:
    generation: int
    reasons: tuple[str, ...]
    event_refs: tuple[str, ...]


def reset_watch_runtime() -> WatchRuntime:
    return WatchRuntime()


def observe_watch(
    watch: WatchContract,
    runtime: WatchRuntime,
    envelope: EventEnvelope,
    *,
    now: float,
) -> tuple[WatchRuntime, WatchObservation]:
    """Reduce one event into one Watch runtime state."""

    if not watch.matches(envelope):
        return runtime, WatchObservation(matched=False)

    if runtime.pending:
        return (
            replace(
                runtime,
                ready_at=now + watch.settle_seconds,
                last_signal_at=now,
                pending_match_count=runtime.pending_match_count + 1,
                latest_event_ref=envelope.ref(),
            ),
            WatchObservation(matched=True, debounced=True),
        )

    observed = runtime.observed_since_reset + 1
    if observed < watch.quantifier_count:
        return (
            replace(runtime, observed_since_reset=observed),
            WatchObservation(matched=True),
        )

    return (
        WatchRuntime(
            observed_since_reset=observed,
            pending=True,
            ready_at=now + watch.settle_seconds,
            first_signal_at=now,
            last_signal_at=now,
            pending_match_count=observed,
            latest_event_ref=envelope.ref(),
        ),
        WatchObservation(matched=True, candidate_created=True),
    )


def due_watch_intent(
    *,
    contract_id: str,
    watch: WatchContract,
    runtime: WatchRuntime,
    now: float,
    epsilon_seconds: float = 0.0,
) -> WakeIntent | None:
    if not runtime.pending:
        return None
    if runtime.ready_at > now + epsilon_seconds:
        return None
    refs = (runtime.latest_event_ref,) if runtime.latest_event_ref else ()
    return WakeIntent(
        contract_id=contract_id,
        reason=f"watch:{watch.watch_id}",
        source="watch",
        observed_at=now,
        event_refs=refs,
    )


def recheck_intent(*, contract_id: str, now: float) -> WakeIntent:
    return WakeIntent(
        contract_id=contract_id,
        reason="recheck",
        source="recheck",
        observed_at=now,
    )


def restart_intent(*, contract_id: str, now: float) -> WakeIntent:
    return WakeIntent(
        contract_id=contract_id,
        reason="restart_dirty",
        source="restart",
        observed_at=now,
    )


def govern_wake_intents(
    runtime: ProgramRuntime,
    intents: Sequence[WakeIntent],
    *,
    attention_key_running: bool,
    max_event_refs: int = 16,
) -> GovernorTransition:
    """Initial public self-discipline Governor reducer.

    This first Governor stage owns common wake normalization only:
    many intents for one Contract become one dirty generation, reasons and
    references are coalesced, and a running AttentionKey never requires a
    concurrent Turn. Authority, budgets and global concurrency remain external
    policies for later stages.
    """

    accepted = [intent for intent in intents if intent.contract_id]
    if not accepted:
        return GovernorTransition(
            decision=GovernorDecision.WAIT,
            runtime=runtime,
            accepted_intents=0,
            generation=None,
        )

    contract_ids = {intent.contract_id for intent in accepted}
    if len(contract_ids) != 1:
        raise ValueError("one Governor reduction may only contain one Contract")

    reasons = list(runtime.pending_reasons)
    refs = list(runtime.event_refs)
    for intent in accepted:
        if intent.reason and intent.reason not in reasons:
            reasons.append(intent.reason)
        for ref in intent.event_refs:
            if not ref:
                continue
            if ref in refs:
                refs.remove(ref)
            refs.append(ref)

    if max_event_refs == 0:
        refs = []
    elif max_event_refs > 0 and len(refs) > max_event_refs:
        refs = refs[-max_event_refs:]

    was_dirty = runtime.dirty
    next_runtime = ProgramRuntime(
        dirty_generation=runtime.dirty_generation + 1,
        reconciled_generation=runtime.reconciled_generation,
        pending_reasons=tuple(reasons),
        event_refs=tuple(refs),
    )
    decision = (
        GovernorDecision.COALESCE
        if attention_key_running or was_dirty
        else GovernorDecision.ALLOW_NOW
    )
    return GovernorTransition(
        decision=decision,
        runtime=next_runtime,
        accepted_intents=len(accepted),
        generation=next_runtime.dirty_generation,
    )


def claim_reconcile(runtime: ProgramRuntime) -> tuple[ProgramRuntime, ReconcileClaim | None]:
    """Move the current dirty generation into processing."""

    if not runtime.dirty:
        return runtime, None

    generation = runtime.dirty_generation
    claim = ReconcileClaim(
        generation=generation,
        reasons=runtime.pending_reasons or ("dirty",),
        event_refs=runtime.event_refs,
    )
    return (
        ProgramRuntime(
            dirty_generation=runtime.dirty_generation,
            reconciled_generation=generation,
            pending_reasons=(),
            event_refs=(),
        ),
        claim,
    )


def needs_reconcile(runtime: ProgramRuntime) -> bool:
    return runtime.dirty_generation > runtime.reconciled_generation
