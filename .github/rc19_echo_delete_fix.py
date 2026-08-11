from pathlib import Path

p = Path('echo_service.py')
t = p.read_text(encoding='utf-8')

old = '''    async def _delete_owned_job(self, hook_id: str) -> None:
        task = self._tasks.pop(hook_id, None)
        current = asyncio.current_task()
        if task is not None and task is not current and not task.done():
            task.cancel()
        try:
            await self._require_manager().delete_job(hook_id)
        except Exception:
            # Cancellation is idempotent; an already-consumed/deleted native row is fine.
            pass
'''
new = '''    async def _delete_owned_job(self, hook_id: str, *, strict: bool = False) -> bool:
        task = self._tasks.pop(hook_id, None)
        current = asyncio.current_task()
        if task is not None and task is not current and not task.done():
            task.cancel()
        try:
            await self._require_manager().delete_job(hook_id)
            return True
        except Exception as exc:
            # delete_job has no "already absent" return contract. Re-inventory once so
            # an idempotent race is success, but a surviving disabled native row is not
            # falsely reported as cancelled.
            try:
                still_exists = any(
                    str(getattr(job, "job_id", "") or "") == hook_id
                    for job in await self._raw_owned_jobs()
                )
            except Exception:
                still_exists = True
            if not still_exists:
                return True
            self._quarantined.append(
                {
                    "hook_id": hook_id,
                    "error_code": f"echo_delete_{type(exc).__name__}",
                }
            )
            if strict:
                raise DomainError(
                    "echo_delete_failed",
                    "回响本地计时器已停止，但原生禁用任务删除失败；不能声称已完全取消。",
                ) from exc
            return False
'''
assert old in t
t = t.replace(old, new, 1)

# Execution/preflight cleanup remains best-effort but truthfully quarantines failures.
t = t.replace('            await self._delete_owned_job(hook_id)\n            return\n', '            await self._delete_owned_job(hook_id, strict=False)\n            return\n', 1)
t = t.replace('            await self._delete_owned_job(hook_id)\n\n    async def _wait_and_execute', '            await self._delete_owned_job(hook_id, strict=False)\n\n    async def _wait_and_execute', 1)
t = t.replace('            await self._delete_owned_job(hook_id)\n        finally:', '            await self._delete_owned_job(hook_id, strict=False)\n        finally:', 1)

# Rollback should attempt hard deletion, but create failure remains the primary contract.
t = t.replace('                    await self._delete_owned_job(str(getattr(job, "job_id", "")))\n', '                    await self._delete_owned_job(str(getattr(job, "job_id", "")), strict=False)\n', 1)

old_cancel = '''        removed: list[str] = []
        for job in await self._raw_owned_jobs():
            try:
                hook = echo_from_job(job)
            except DomainError:
                continue
            if hook.scope != normalized_scope:
                continue
            if hook_set and hook.hook_id not in hook_set:
                continue
            if group_set and hook.group_id not in group_set:
                continue
            if not hook_set and not group_set:
                continue
            await self._delete_owned_job(hook.hook_id)
            removed.append(hook.hook_id)
        self._cancelled += len(removed)
        return {
            "status": "ok",
            "error_code": None,
            "action": "cancel",
            "scope_ref": self._scope_ref(normalized_scope),
            "changed": bool(removed),
            "outcome": "echo_hooks_cancelled" if removed else "echo_hooks_already_absent",
            "effect_contract": ECHO_EFFECT_CONTRACT,
            "effect_applied": bool(removed),
            "effect_state": "applied" if removed else "not_applied",
            "reply_guaranteed": False,
            "cancelled_hook_ids": sorted(removed),
        }
'''
new_cancel = '''        removed: list[str] = []
        failed: list[str] = []
        for job in await self._raw_owned_jobs():
            try:
                hook = echo_from_job(job)
            except DomainError:
                continue
            if hook.scope != normalized_scope:
                continue
            if hook_set and hook.hook_id not in hook_set:
                continue
            if group_set and hook.group_id not in group_set:
                continue
            if not hook_set and not group_set:
                continue
            if await self._delete_owned_job(hook.hook_id, strict=False):
                removed.append(hook.hook_id)
            else:
                failed.append(hook.hook_id)
        self._cancelled += len(removed)
        if failed:
            status = "error"
            error_code = "echo_delete_partial" if removed else "echo_delete_failed"
            outcome = "echo_hooks_partially_cancelled" if removed else "echo_hooks_cancel_failed"
        else:
            status = "ok"
            error_code = None
            outcome = "echo_hooks_cancelled" if removed else "echo_hooks_already_absent"
        return {
            "status": status,
            "error_code": error_code,
            "action": "cancel",
            "scope_ref": self._scope_ref(normalized_scope),
            "changed": bool(removed),
            "outcome": outcome,
            "effect_contract": ECHO_EFFECT_CONTRACT,
            "effect_applied": bool(removed),
            "effect_state": "applied" if removed else "not_applied",
            "reply_guaranteed": False,
            "cancelled_hook_ids": sorted(removed),
            "failed_hook_ids": sorted(failed),
            "recovery_action": "retry_cancel_or_inspect_native_cron" if failed else None,
            "recovery_action_executed": False,
            "automatic_retry_scheduled": False,
        }
'''
assert old_cancel in t
t = t.replace(old_cancel, new_cancel, 1)

old_scope = '''        removed: list[str] = []
        for job in await self._raw_owned_jobs():
            try:
                hook = echo_from_job(job)
            except DomainError:
                continue
            if hook.scope != normalized_scope:
                continue
            await self._delete_owned_job(hook.hook_id)
            removed.append(hook.hook_id)
        self._cancelled += len(removed)
        return removed
'''
new_scope = '''        removed: list[str] = []
        for job in await self._raw_owned_jobs():
            try:
                hook = echo_from_job(job)
            except DomainError:
                continue
            if hook.scope != normalized_scope:
                continue
            if await self._delete_owned_job(hook.hook_id, strict=False):
                removed.append(hook.hook_id)
        self._cancelled += len(removed)
        return removed
'''
assert old_scope in t
t = t.replace(old_scope, new_scope, 1)

p.write_text(t, encoding='utf-8')

# Register error classification in main for any strict/user-facing future path.
p = Path('main.py')
t = p.read_text(encoding='utf-8')
anchor = '    "echo_create_failed": ("host_runtime", "inspect_native_cron", True),\n'
addition = '    "echo_delete_failed": ("host_runtime", "retry_cancel_or_inspect_native_cron", True),\n'
assert anchor in t
if addition not in t:
    t = t.replace(anchor, anchor + addition, 1)
p.write_text(t, encoding='utf-8')
