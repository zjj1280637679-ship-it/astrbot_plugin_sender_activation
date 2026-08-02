


from __future__ import annotations

import asyncio
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

ReservationKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class ActivationPermit:


    key: ReservationKey
    generation: int
    previous_next_allowed_at: float
    released_at: float
    trailing: bool


@dataclass(frozen=True, slots=True)
class ReservationResult:


    permit: ActivationPermit | None
    reason: str

    @property
    def released(self) -> bool:
        return self.permit is not None


@dataclass(slots=True)
class _Pending:
    generation: int
    signal: asyncio.Event


@dataclass(slots=True)
class _Slot:
    next_allowed_at: float = 0.0
    generation: int = 0
    claimed_generation: int | None = None
    pending: _Pending | None = None


class ActivationReservationCoordinator:







    def __init__(
        self,
        minimum_interval_seconds: float,
        *,
        max_slots: int,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.minimum_interval_seconds = max(
            0.0,
            min(float(minimum_interval_seconds), 1800.0),
        )
        self.max_slots = max(1, int(max_slots))
        self._clock = monotonic_clock
        self._lock = threading.RLock()
        self._slots: dict[ReservationKey, _Slot] = {}
        self._closed = False
        self._metrics: Counter[str] = Counter()

    @staticmethod
    def _key(scope: str, target_id: str) -> ReservationKey:
        return str(scope), str(target_id)

    def _prune_locked(self, now: float) -> None:
        removable = [
            key
            for key, slot in self._slots.items()
            if slot.pending is None
            and slot.claimed_generation is None
            and slot.next_allowed_at <= now
        ]
        for key in removable:
            self._slots.pop(key, None)

    def _claim_locked(
        self,
        key: ReservationKey,
        slot: _Slot,
        generation: int,
        *,
        now: float,
        trailing: bool,
    ) -> ActivationPermit:
        previous = slot.next_allowed_at
        slot.pending = None
        slot.claimed_generation = generation
        slot.next_allowed_at = now + self.minimum_interval_seconds
        self._metrics["released_trailing" if trailing else "released_immediate"] += 1
        return ActivationPermit(
            key=key,
            generation=generation,
            previous_next_allowed_at=previous,
            released_at=now,
            trailing=trailing,
        )

    def _offer_locked(
        self,
        key: ReservationKey,
        now: float,
    ) -> tuple[ActivationPermit | None, _Pending | None, float]:
        self._prune_locked(now)
        slot = self._slots.get(key)
        if slot is None:
            if len(self._slots) >= self.max_slots:
                self._metrics["capacity_inert"] += 1
                return None, None, now
            slot = _Slot()
            self._slots[key] = slot

        slot.generation += 1
        generation = slot.generation
        if (
            slot.claimed_generation is None
            and slot.pending is None
            and now >= slot.next_allowed_at
        ):
            return (
                self._claim_locked(
                    key,
                    slot,
                    generation,
                    now=now,
                    trailing=False,
                ),
                None,
                now,
            )

        previous = slot.pending
        if previous is not None:
            previous.signal.set()
            self._metrics["superseded"] += 1
        pending = _Pending(generation, asyncio.Event())
        slot.pending = pending
        due = max(now, slot.next_allowed_at)
        self._metrics["waiting"] += 1
        return None, pending, due

    async def reserve(self, scope: str, target_id: str) -> ReservationResult:


        key = self._key(scope, target_id)
        with self._lock:
            if self._closed:
                return ReservationResult(None, "shutdown")
            now = float(self._clock())
            permit, pending, due = self._offer_locked(key, now)
        if permit is not None:
            return ReservationResult(permit, "immediate")
        if pending is None:
            return ReservationResult(None, "capacity_inert")

        while True:
            remaining = max(0.0, due - float(self._clock()))
            signalled = False
            try:
                await asyncio.wait_for(pending.signal.wait(), timeout=remaining)
                signalled = True
            except asyncio.TimeoutError:
                pass

            with self._lock:
                if self._closed:
                    return ReservationResult(None, "shutdown")
                slot = self._slots.get(key)
                if (
                    slot is None
                    or slot.pending is None
                    or slot.pending.generation != pending.generation
                ):
                    return ReservationResult(None, "superseded")
                now = float(self._clock())
                if now >= due:
                    permit = self._claim_locked(
                        key,
                        slot,
                        pending.generation,
                        now=now,
                        trailing=True,
                    )
                    return ReservationResult(permit, "trailing")
                if signalled:
                    return ReservationResult(None, "superseded")

    def commit(self, permit: ActivationPermit, *, admitted: bool) -> bool:


        with self._lock:
            slot = self._slots.get(permit.key)
            if slot is None or slot.claimed_generation != permit.generation:
                self._metrics["stale_commit"] += 1
                return False
            slot.claimed_generation = None
            if admitted:
                self._metrics["committed"] += 1
            else:
                slot.next_allowed_at = permit.previous_next_allowed_at
                self._metrics["rolled_back"] += 1
            self._prune_locked(float(self._clock()))
            return True

    def close(self) -> None:


        with self._lock:
            if self._closed:
                return
            self._closed = True
            for slot in self._slots.values():
                if slot.pending is not None:
                    slot.pending.signal.set()
            self._slots.clear()
            self._metrics["shutdown"] += 1

    def health(self) -> dict[str, object]:
        with self._lock:
            pending = sum(slot.pending is not None for slot in self._slots.values())
            claimed = sum(
                slot.claimed_generation is not None for slot in self._slots.values()
            )
            return {
                "activation_min_interval_seconds": self.minimum_interval_seconds,
                "activation_reservation_slots": len(self._slots),
                "activation_reservation_pending": pending,
                "activation_reservation_claimed": claimed,
                "activation_reservation_closed": self._closed,
                "activation_reservation_metrics": dict(self._metrics),
            }
