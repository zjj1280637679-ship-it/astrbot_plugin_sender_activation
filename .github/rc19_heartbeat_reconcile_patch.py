from pathlib import Path

# Business fix only: the counterexample fixture is already present in the current branch.
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
