from pathlib import Path

# Adapter shutdown must process disabled active templates in preflight mode.
p = Path('cron_adapter.py')
t = p.read_text(encoding='utf-8')
old = '''        for job in await self.raw_owned_jobs():
            if not bool(getattr(job, "enabled", False)):
                continue
            job_id = str(getattr(job, "job_id", "") or "")
'''
new = '''        for job in await self.raw_owned_jobs():
            if self._active_execution_enabled and not bool(getattr(job, "enabled", False)):
                continue
            job_id = str(getattr(job, "job_id", "") or "")
'''
assert old in t
t = t.replace(old, new, 1)
p.write_text(t, encoding='utf-8')

# Heartbeat renew: after template update, driver failure is indeterminate/fail-closed.
p = Path('heartbeat_service.py')
t = p.read_text(encoding='utf-8')
old = '''                if self._wake_gate is not None:
                    updated = await self._wake_gate.arm(updated)
                renewed.append(updated)
'''
new = '''                if self._wake_gate is not None:
                    try:
                        updated = await self._wake_gate.arm(updated)
                    except Exception as exc:
                        self.last_error_code = "heartbeat_preflight_update_indeterminate"
                        raise DomainError(
                            "heartbeat_preflight_update_indeterminate",
                            "心跳模板已更新但前置门重建失败；模板保持禁用，当前不会主动唤醒，需查询后重试。",
                        ) from exc
                renewed.append(updated)
'''
assert old in t
t = t.replace(old, new, 1)
p.write_text(t, encoding='utf-8')

# Error contract must report uncertainty, never 'not applied'. Also register all
# preflight errors that the integration patch introduced.
p = Path('main.py')
t = p.read_text(encoding='utf-8')
old = '''        "attention_commit_indeterminate",
        "native_cron_update_indeterminate",
    }
'''
new = '''        "attention_commit_indeterminate",
        "native_cron_update_indeterminate",
        "heartbeat_preflight_update_indeterminate",
    }
'''
assert old in t
t = t.replace(old, new, 1)

anchor = '''    "heartbeat_service_inactive": (
        "host_capability",
        "inspect_heartbeat_health",
        False,
    ),
'''
extra = '''    "heartbeat_preflight_unavailable": (
        "host_capability",
        "inspect_native_cron",
        False,
    ),
    "heartbeat_preflight_inactive": (
        "host_capability",
        "inspect_native_cron",
        False,
    ),
    "heartbeat_preflight_create_failed": (
        "host_runtime",
        "inspect_native_cron",
        True,
    ),
    "heartbeat_preflight_update_indeterminate": (
        "host_runtime",
        "query_heartbeat_then_retry",
        False,
    ),
'''
assert anchor in t
if '"heartbeat_preflight_update_indeterminate": (' not in t:
    t = t.replace(anchor, anchor + extra, 1)

p.write_text(t, encoding='utf-8')
