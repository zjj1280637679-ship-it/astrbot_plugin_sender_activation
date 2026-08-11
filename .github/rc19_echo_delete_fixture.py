from pathlib import Path

p = Path('.github/rc19_core_tests.py')
t = p.read_text(encoding='utf-8')

old = '''        self.next_id = 1
        self.fail_on_add_number: int | None = None
'''
new = '''        self.next_id = 1
        self.fail_on_add_number: int | None = None
        self.fail_delete_ids: set[str] = set()
'''
assert old in t
t = t.replace(old, new, 1)

old = '''    async def delete_job(self, job_id: str):
        self.jobs.pop(job_id, None)
'''
new = '''    async def delete_job(self, job_id: str):
        if job_id in self.fail_delete_ids:
            raise OSError("simulated delete failure")
        self.jobs.pop(job_id, None)
'''
assert old in t
t = t.replace(old, new, 1)

marker = '''    # Restart/reload cannot resurrect pending Echo hooks: initialization deletes stale owned rows.
'''
addition = '''    # Counterexample: native delete failure must never be reported as successful cancel.
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

'''
assert marker in t
t = t.replace(marker, addition + marker, 1)

p.write_text(t, encoding='utf-8')
