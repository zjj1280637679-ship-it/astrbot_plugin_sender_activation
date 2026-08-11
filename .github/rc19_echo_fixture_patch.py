from pathlib import Path

p = Path('.github/rc19_core_tests.py')
t = p.read_text(encoding='utf-8')

old = '''    def __init__(self) -> None:
        self.jobs: dict[str, FakeJob] = {}
        self.calls: list[dict[str, Any]] = []
        self.next_id = 1
        self.fail_on_add_number: int | None = None
'''
new = '''    def __init__(self) -> None:
        self.jobs: dict[str, FakeJob] = {}
        self.calls: list[dict[str, Any]] = []
        self.run_calls: list[str] = []
        self.next_id = 1
        self.fail_on_add_number: int | None = None
'''
assert old in t
t = t.replace(old, new, 1)

anchor = '''    async def delete_job(self, job_id: str):
        self.jobs.pop(job_id, None)


async def test_echo_core() -> None:
'''
replacement = '''    async def delete_job(self, job_id: str):
        self.jobs.pop(job_id, None)

    async def run_job_now(self, job_id: str):
        if job_id in self.jobs:
            self.run_calls.append(job_id)


async def test_echo_core() -> None:
'''
assert anchor in t
t = t.replace(anchor, replacement, 1)

t = t.replace(
    '        assert call["run_once"] is True\n        assert call["persistent"] is False\n',
    '        assert call["run_once"] is True\n        assert call["persistent"] is False\n        assert call["enabled"] is False\n',
    1,
)

marker = '''    # Restart/reload cannot resurrect pending Echo hooks: initialization deletes stale owned rows.
'''
addition = '''    # Permission/session preflight runs before any main-Agent execution.
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

'''
assert marker in t
t = t.replace(marker, addition + marker, 1)

marker2 = '''    cleanup_service = EchoService(limits, stale_manager, wall_clock=wall)
    await cleanup_service.initialize()
    assert stale_manager.jobs == {}
'''
replacement2 = marker2 + '''    await service.terminate()
    await cleanup_service.terminate()
'''
assert marker2 in t
t = t.replace(marker2, replacement2, 1)

p.write_text(t, encoding='utf-8')
