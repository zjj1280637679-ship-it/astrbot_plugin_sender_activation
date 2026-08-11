from pathlib import Path

p = Path('heartbeat_gate.py')
t = p.read_text(encoding='utf-8')
old = '''    async def initialize(self) -> None:
        self._active = False
        self._failures.clear()
        # Drivers are non-persistent runtime mechanics. Rebuild them from heartbeat
        # lease templates so a hot reload cannot duplicate recurrence.
        for job in [*await self._driver_jobs(), *await self._cleanup_jobs()]:
            job_id = str(getattr(job, "job_id", "") or "")
            if job_id:
                await self._delete_job_truthfully(job_id)
        self._active = True
'''
new = '''    async def initialize(self) -> None:
        self._active = False
        self._failures.clear()
        # Drivers are non-persistent runtime mechanics. Rebuild them from heartbeat
        # lease templates so a hot reload cannot duplicate recurrence. A surviving
        # stale driver is more dangerous than temporarily losing heartbeats, so
        # reconciliation is deliberately fail-closed.
        failed: list[str] = []
        for job in [*await self._driver_jobs(), *await self._cleanup_jobs()]:
            job_id = str(getattr(job, "job_id", "") or "")
            if job_id and not await self._delete_job_truthfully(job_id):
                failed.append(job_id)
        if failed:
            raise DomainError(
                "heartbeat_preflight_reconcile_failed",
                "旧心跳前置门无法确认清理；为避免重复主动唤醒，本次不重建新前置门。",
            )
        self._active = True
'''
assert old in t
t = t.replace(old, new, 1)
p.write_text(t, encoding='utf-8')

p = Path('main.py')
t = p.read_text(encoding='utf-8')
anchor = '''    "heartbeat_preflight_create_failed": (
        "host_runtime",
        "inspect_native_cron",
        True,
    ),
'''
extra = '''    "heartbeat_preflight_reconcile_failed": (
        "host_runtime",
        "inspect_and_remove_stale_preflight_jobs",
        False,
    ),
'''
assert anchor in t
if '"heartbeat_preflight_reconcile_failed": (' not in t:
    t = t.replace(anchor, anchor + extra, 1)
p.write_text(t, encoding='utf-8')

p = Path('.github/rc19_heartbeat_gate_tests.py')
t = p.read_text(encoding='utf-8')
old = '''        self.run_calls: list[str] = []
'''
new = '''        self.run_calls: list[str] = []
        self.fail_delete_ids: set[str] = set()
'''
assert old in t
t = t.replace(old, new, 1)
old = '''    async def delete_job(self, job_id):
        self.basic.pop(job_id, None)
        self.active.pop(job_id, None)
        self.handlers.pop(job_id, None)
'''
new = '''    async def delete_job(self, job_id):
        if job_id in self.fail_delete_ids:
            raise OSError("simulated delete failure")
        self.basic.pop(job_id, None)
        self.active.pop(job_id, None)
        self.handlers.pop(job_id, None)
'''
assert old in t
t = t.replace(old, new, 1)
marker = '''    # Explicit disarm is scoped to one parent and removes both driver and cleanup.
'''
addition = '''    # Counterexample: if a stale driver cannot be deleted during hot reload,
    # initialization must fail closed instead of arming a duplicate driver.
    stale_failure_manager = Manager()
    stale_failure_manager.active["hb-stale"] = Job(
        job_id="hb-stale",
        payload=active_payload(expires=2100.0),
        enabled=False,
        cron_expression="*/5 * * * *",
    )
    old_gate = HeartbeatWakeGate(stale_failure_manager, preflight=lambda _scope: True, wall_clock=lambda: 1100.0)
    await old_gate.initialize()
    await old_gate.arm(lease("hb-stale", expires=2100.0))
    stale_ids = {j.job_id for j in stale_failure_manager.basic.values() if DRIVER_TAG in j.payload}
    assert len(stale_ids) == 1
    stale_failure_manager.fail_delete_ids |= stale_ids
    new_gate = HeartbeatWakeGate(stale_failure_manager, preflight=lambda _scope: True, wall_clock=lambda: 1100.0)
    try:
        await new_gate.initialize()
    except DomainError as exc:
        assert exc.code == "heartbeat_preflight_reconcile_failed"
    else:
        raise AssertionError("duplicate-driver reconcile unexpectedly succeeded")
    assert new_gate._active is False
    assert {j.job_id for j in stale_failure_manager.basic.values() if DRIVER_TAG in j.payload} == stale_ids
    # The old gate can be made inert even if its native row is temporarily undeletable.
    old_gate._active = False
    stale_failure_manager.fail_delete_ids.clear()
    await old_gate.terminate()

'''
assert marker in t
t = t.replace(marker, addition + marker, 1)
p.write_text(t, encoding='utf-8')
