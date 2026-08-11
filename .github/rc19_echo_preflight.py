from pathlib import Path

path = Path("echo_service.py")
text = path.read_text(encoding="utf-8")

text = text.replace(
    'import hashlib\nimport math\nimport time\n',
    'import asyncio\nimport hashlib\nimport inspect\nimport math\nimport time\n',
    1,
)
text = text.replace(
    'from typing import Any\n',
    'from collections.abc import Awaitable, Callable\nfrom typing import Any\n',
    1,
)

text = text.replace(
    '        enabled=bool(getattr(job, "enabled", False)),\n',
    '        enabled=bool(tag.get("armed", True)),\n',
    1,
)

old_init = '''    def __init__(\n        self,\n        limits: EchoLimits,\n        cron_manager: Any,\n        *,\n        wall_clock=time.time,\n    ) -> None:\n        self.limits = limits\n        self._manager = cron_manager\n        self._wall_clock = wall_clock\n        self._active = False\n        self._created = 0\n        self._cancelled = 0\n        self._quarantined: list[dict[str, str]] = []\n'''
new_init = '''    def __init__(\n        self,\n        limits: EchoLimits,\n        cron_manager: Any,\n        *,\n        wall_clock=time.time,\n        preflight: Callable[[str], bool | Awaitable[bool]] | None = None,\n        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,\n    ) -> None:\n        self.limits = limits\n        self._manager = cron_manager\n        self._wall_clock = wall_clock\n        self._preflight = preflight\n        self._sleeper = sleeper\n        self._active = False\n        self._created = 0\n        self._cancelled = 0\n        self._preflight_suppressed = 0\n        self._executed = 0\n        self._tasks: dict[str, asyncio.Task[None]] = {}\n        self._quarantined: list[dict[str, str]] = []\n'''
assert old_init in text
text = text.replace(old_init, new_init, 1)

old_require = '''        required = ("add_active_job", "delete_job", "list_jobs")\n'''
new_require = '''        required = ("add_active_job", "delete_job", "list_jobs", "run_job_now")\n'''
assert old_require in text
text = text.replace(old_require, new_require, 1)

# Add helpers before initialize.
anchor = '''    async def initialize(self) -> None:\n'''
helpers = '''    async def _preflight_allowed(self, scope: str) -> bool:\n        if not self._active:\n            return False\n        callback = self._preflight\n        if callback is None:\n            return True\n        try:\n            result = callback(scope)\n            if inspect.isawaitable(result):\n                result = await result\n            return bool(result)\n        except Exception:\n            return False\n\n    async def _delete_owned_job(self, hook_id: str) -> None:\n        task = self._tasks.pop(hook_id, None)\n        current = asyncio.current_task()\n        if task is not None and task is not current and not task.done():\n            task.cancel()\n        try:\n            await self._require_manager().delete_job(hook_id)\n        except Exception:\n            # Cancellation is idempotent; an already-consumed/deleted native row is fine.\n            pass\n\n    async def _execute_armed_hook(self, hook_id: str, scope: str) -> None:\n        if not await self._preflight_allowed(scope):\n            self._preflight_suppressed += 1\n            await self._delete_owned_job(hook_id)\n            return\n        manager = self._require_manager()\n        try:\n            # The native job is deliberately stored disabled, so AstrBot cannot bypass\n            # this preflight. run_job_now(ignore_enabled=True) is the only execution path.\n            await manager.run_job_now(hook_id)\n            self._executed += 1\n        finally:\n            await self._delete_owned_job(hook_id)\n\n    async def _wait_and_execute(self, hook_id: str, scope: str, delay: float) -> None:\n        try:\n            await self._sleeper(max(0.0, delay))\n            if self._active:\n                await self._execute_armed_hook(hook_id, scope)\n        except asyncio.CancelledError:\n            raise\n        except Exception as exc:\n            self._quarantined.append(\n                {"hook_id": hook_id, "error_code": f"echo_execute_{type(exc).__name__}"}\n            )\n            await self._delete_owned_job(hook_id)\n        finally:\n            self._tasks.pop(hook_id, None)\n\n    def _arm_task(self, hook_id: str, scope: str, run_at: float) -> None:\n        delay = max(0.0, run_at - float(self._wall_clock()))\n        task = asyncio.create_task(\n            self._wait_and_execute(hook_id, scope, delay),\n            name=f"sender-echo-{hook_id}",\n        )\n        self._tasks[hook_id] = task\n\n    async def initialize(self) -> None:\n'''
assert anchor in text
text = text.replace(anchor, helpers, 1)

# Initialize/terminate task cleanup.
text = text.replace(
    '        self._active = False\n        self._quarantined.clear()\n',
    '        self._active = False\n        for task in list(self._tasks.values()):\n            task.cancel()\n        self._tasks.clear()\n        self._quarantined.clear()\n',
    1,
)
old_term = '''    async def terminate(self) -> list[str]:\n        self._active = False\n        failures: list[str] = []\n        try:\n            jobs = await self._raw_owned_jobs()\n        except Exception:\n            return ["inventory"]\n        for job in jobs:\n            hook_id = str(getattr(job, "job_id", "") or "")\n            try:\n                await self._require_manager().delete_job(hook_id)\n            except Exception:\n                failures.append(hook_id)\n        return failures\n'''
new_term = '''    async def terminate(self) -> list[str]:\n        self._active = False\n        tasks = list(self._tasks.values())\n        self._tasks.clear()\n        for task in tasks:\n            task.cancel()\n        if tasks:\n            await asyncio.gather(*tasks, return_exceptions=True)\n        failures: list[str] = []\n        try:\n            jobs = await self._raw_owned_jobs()\n        except Exception:\n            return ["inventory"]\n        for job in jobs:\n            hook_id = str(getattr(job, "job_id", "") or "")\n            try:\n                await self._require_manager().delete_job(hook_id)\n            except Exception:\n                failures.append(hook_id)\n        return failures\n'''
assert old_term in text
text = text.replace(old_term, new_term, 1)

# Payload armed flag and disabled native scheduling, then arm in memory.
text = text.replace(
    '                        "batch_count": batch_count,\n                    },\n',
    '                        "batch_count": batch_count,\n                        "armed": True,\n                    },\n',
    1,
)
text = text.replace(
    '                    enabled=True,\n                    persistent=False,\n                    run_once=True,\n',
    '                    enabled=False,\n                    persistent=False,\n                    run_once=True,\n',
    1,
)
text = text.replace(
    '                created_jobs.append(job)\n',
    '                created_jobs.append(job)\n                self._arm_task(str(getattr(job, "job_id", "")), normalized_scope, run_at)\n',
    1,
)

# Rollback must cancel local tasks too.
text = text.replace(
    '                    await manager.delete_job(str(getattr(job, "job_id", "")))\n',
    '                    await self._delete_owned_job(str(getattr(job, "job_id", "")))\n',
    1,
)

# cancel uses helper so the timer cannot resurrect a deleted hook.
text = text.replace(
    '            await manager.delete_job(hook.hook_id)\n            removed.append(hook.hook_id)\n',
    '            await self._delete_owned_job(hook.hook_id)\n            removed.append(hook.hook_id)\n',
    1,
)
text = text.replace(
    '            await manager.delete_job(hook.hook_id)\n            removed.append(hook.hook_id)\n',
    '            await self._delete_owned_job(hook.hook_id)\n            removed.append(hook.hook_id)\n',
    1,
)

# Health.
text = text.replace(
    '            "echo_cancelled_total": self._cancelled,\n',
    '            "echo_cancelled_total": self._cancelled,\n            "echo_executed_total": self._executed,\n            "echo_preflight_suppressed_total": self._preflight_suppressed,\n            "echo_timer_tasks": len(self._tasks),\n',
    1,
)

path.write_text(text, encoding="utf-8")

# Wire plugin-side SessionGate preflight and construct session gate before EchoService.
main = Path("main.py")
text = main.read_text(encoding="utf-8")
old = '''        self.echo_service = EchoService(\n            self.settings.echo_limits,\n            getattr(context, "cron_manager", None),\n        )\n        self.access_service = AccessService(\n'''
new = '''        self.session_gate = AstrBotSessionGate(PLUGIN_NAME, sp)\n        self.echo_service = EchoService(\n            self.settings.echo_limits,\n            getattr(context, "cron_manager", None),\n            preflight=self._echo_preflight,\n        )\n        self.access_service = AccessService(\n'''
assert old in text
text = text.replace(old, new, 1)
text = text.replace(
    '        self.session_gate = AstrBotSessionGate(PLUGIN_NAME, sp)\n        self._terminated = False\n',
    '        self._terminated = False\n',
    1,
)
anchor = '''    async def _session_status(self, scope: str) -> SessionGateStatus:\n        return await self.session_gate.read(scope)\n\n'''
insert = '''    async def _session_status(self, scope: str) -> SessionGateStatus:\n        return await self.session_gate.read(scope)\n\n    async def _echo_preflight(self, scope: str) -> bool:\n        if self._terminated or not self.settings.echo_enabled:\n            return False\n        status = await self._session_status(scope)\n        return status.enabled is not False\n\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)
main.write_text(text, encoding="utf-8")
