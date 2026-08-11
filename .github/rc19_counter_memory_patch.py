from pathlib import Path

p = Path('attention_service.py')
t = p.read_text(encoding='utf-8')

old = '''@dataclass(slots=True)
class _GuardSlot:
    events: deque[float]
    blocked_until: float = 0.0
'''
new = '''@dataclass(slots=True)
class _GuardSlot:
    events: deque[float]
    cumulative_count: int = 0
    blocked_until: float = 0.0
'''
assert old in t
t = t.replace(old, new, 1)

old = '''            slot.blocked_until = 0.0
            if policy.trigger_window_seconds > 0:
                threshold = monotonic_now - policy.trigger_window_seconds
                while slot.events and slot.events[0] <= threshold:
                    slot.events.popleft()
            slot.events.append(monotonic_now)
            observed = len(slot.events)
'''
new = '''            slot.blocked_until = 0.0
            if policy.trigger_window_seconds > 0:
                threshold = monotonic_now - policy.trigger_window_seconds
                while slot.events and slot.events[0] <= threshold:
                    slot.events.popleft()
                slot.events.append(monotonic_now)
                observed = len(slot.events)
            else:
                # A pure lifetime counter needs no per-event timestamps. Keeping a deque
                # here would let a high configured threshold become a memory amplifier.
                slot.cumulative_count += 1
                observed = slot.cumulative_count
'''
assert old in t
t = t.replace(old, new, 1)

old = '''            slot.blocked_until = monotonic_now + max(0.0, ignore_for)
            slot.events.clear()
            self._metrics["ignore_triggered"] += 1
'''
new = '''            slot.blocked_until = monotonic_now + max(0.0, ignore_for)
            slot.events.clear()
            slot.cumulative_count = 0
            self._metrics["ignore_triggered"] += 1
'''
assert old in t
t = t.replace(old, new, 1)

old = '''            active_blocks = sum(slot.blocked_until > now for slot in self._slots.values())
            return {
                "ignore_guard_slots": len(self._slots),
                "ignore_guard_active_blocks": active_blocks,
                "ignore_guard_metrics": dict(self._metrics),
            }
'''
new = '''            active_blocks = sum(slot.blocked_until > now for slot in self._slots.values())
            event_samples = sum(len(slot.events) for slot in self._slots.values())
            cumulative_slots = sum(slot.cumulative_count > 0 for slot in self._slots.values())
            return {
                "ignore_guard_slots": len(self._slots),
                "ignore_guard_active_blocks": active_blocks,
                "ignore_guard_event_samples": event_samples,
                "ignore_guard_cumulative_slots": cumulative_slots,
                "ignore_guard_metrics": dict(self._metrics),
            }
'''
assert old in t
t = t.replace(old, new, 1)

p.write_text(t, encoding='utf-8')

# Add a resource-amplification regression to the existing counterexample suite.
p = Path('.github/rc19_core_tests.py')
t = p.read_text(encoding='utf-8')
marker = '''    # Sliding-window counterexample: an old event outside the window must not contribute.
'''
addition = '''    # Counterexample: window=0 is pure lifetime counting and must not retain one
    # timestamp per event. Large configured counts stay O(1) in event-sample memory.
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
    await set_policy(service, trigger_count=50, window=0, ignore_seconds=20, policy_seconds=100)
    for expected in range(1, 26):
        decision = service.evaluate_activation_attempt(SCOPE, A)
        assert not decision.ignored and decision.observed_count == expected
    guard_health = service._guard.health()
    assert guard_health["ignore_guard_event_samples"] == 0
    assert guard_health["ignore_guard_cumulative_slots"] == 1

'''
assert marker in t
t = t.replace(marker, addition + marker, 1)
p.write_text(t, encoding='utf-8')
