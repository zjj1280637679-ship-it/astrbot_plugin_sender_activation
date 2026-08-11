from pathlib import Path

p = Path('heartbeat_gate.py')
t = p.read_text(encoding='utf-8')
old = '''                description="在主 Agent 心跳激活前检查当前会话权限。",
                timezone="UTC",
                payload=payload,
'''
new = '''                description="在主 Agent 心跳激活前检查当前会话权限。",
                # Preserve rc18/AstrBot semantics: heartbeat cron expressions use the
                # host scheduler's default timezone unless the original template had an
                # explicit timezone. The old active template did not force UTC.
                timezone=None,
                payload=payload,
'''
assert old in t
t = t.replace(old, new, 1)
p.write_text(t, encoding='utf-8')

p = Path('.github/rc19_heartbeat_gate_tests.py')
t = p.read_text(encoding='utf-8')
# Preserve kwargs for semantic assertions.
old = '''class Job:
    job_id: str
    payload: dict[str, Any]
    enabled: bool = True
    cron_expression: str | None = None
    next_run_time: float | None = None
'''
new = '''class Job:
    job_id: str
    payload: dict[str, Any]
    enabled: bool = True
    cron_expression: str | None = None
    next_run_time: float | None = None
    timezone: str | None = None
'''
assert old in t
t = t.replace(old, new, 1)
old = '''            kwargs.get("cron_expression"),
            1234.0,
        )
'''
new = '''            kwargs.get("cron_expression"),
            1234.0,
            kwargs.get("timezone"),
        )
'''
assert old in t
t = t.replace(old, new, 1)
# Positional active fixtures now need explicit named arguments to avoid new field drift.
t = t.replace(
    'Job("hb-1", active_payload(), False, "*/5 * * * *")',
    'Job(job_id="hb-1", payload=active_payload(), enabled=False, cron_expression="*/5 * * * *")',
)
t = t.replace(
    'Job("hb-2", active_payload(expires=2100.0), False, "*/5 * * * *")',
    'Job(job_id="hb-2", payload=active_payload(expires=2100.0), enabled=False, cron_expression="*/5 * * * *")',
)
marker = '''    driver = drivers[0]

    # Disabled session: driver fires, but no native Agent activation is consumed.
'''
addition = '''    # Recurring heartbeat keeps the old host-default timezone. Expiry cleanup is
    # absolute wall-clock time and remains UTC.
    assert driver.timezone is None
    assert cleanups[0].timezone == "UTC"

'''
assert marker in t
t = t.replace(marker, '    driver = drivers[0]\n\n' + addition + '    # Disabled session: driver fires, but no native Agent activation is consumed.\n', 1)
p.write_text(t, encoding='utf-8')
