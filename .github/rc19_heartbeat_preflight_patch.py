from pathlib import Path

# cron_adapter: keep active-agent heartbeat rows disabled when a preflight driver owns recurrence.
p = Path('cron_adapter.py')
t = p.read_text(encoding='utf-8')
old = '''    def __init__(self, context: Any) -> None:
        self._manager = getattr(context, "cron_manager", None)
'''
new = '''    def __init__(self, context: Any, *, active_execution_enabled: bool = True) -> None:
        self._manager = getattr(context, "cron_manager", None)
        self._active_execution_enabled = bool(active_execution_enabled)
'''
assert old in t
t = t.replace(old, new, 1)
t = t.replace('                enabled=True,\n                persistent=False,\n                run_once=False,\n', '                enabled=self._active_execution_enabled,\n                persistent=False,\n                run_once=False,\n', 1)
# renew: active template is never scheduled directly in preflight mode.
old = '''        enabled = bool(getattr(raw, "enabled", False))
        try:
            updated = await manager.update_job(
'''
new = '''        enabled = self._active_execution_enabled
        try:
            updated = await manager.update_job(
'''
assert old in t
t = t.replace(old, new, 1)
# reconcile: migrate existing enabled rc18 heartbeats into disabled templates.
old = '''            should_enable = lease.enabled or lease.plugin_suspended
            if lease.plugin_suspended:
'''
new = '''            should_enable = (
                (lease.enabled or lease.plugin_suspended)
                if self._active_execution_enabled
                else False
            )
            if lease.plugin_suspended:
'''
assert old in t
t = t.replace(old, new, 1)
p.write_text(t, encoding='utf-8')

# heartbeat_service: arm/decorate/disarm through the low-cost preflight driver.
p = Path('heartbeat_service.py')
t = p.read_text(encoding='utf-8')
t = t.replace('from .heartbeat_domain import (\n', 'from .heartbeat_domain import (\n', 1)
if 'from .heartbeat_gate import HeartbeatWakeGate\n' not in t:
    t = t.replace('from .heartbeat_domain import (\n', 'from .heartbeat_gate import HeartbeatWakeGate\nfrom .heartbeat_domain import (\n', 1)
old = '''        adapter: AstrBotCronAdapter,
        *,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self._adapter = adapter
'''
new = '''        adapter: AstrBotCronAdapter,
        wake_gate: HeartbeatWakeGate | None = None,
        *,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self._adapter = adapter
        self._wake_gate = wake_gate
'''
assert old in t
t = t.replace(old, new, 1)
old = '''            try:
                _leases, quarantined = await self._adapter.reconcile_start(
                    float(self._wall_clock())
                )
                self._quarantined = quarantined
                self.last_error_code = None
'''
new = '''            try:
                leases, quarantined = await self._adapter.reconcile_start(
                    float(self._wall_clock())
                )
                if self._wake_gate is not None:
                    await self._wake_gate.initialize()
                    for lease in leases:
                        try:
                            await self._wake_gate.arm(lease)
                        except DomainError as exc:
                            quarantined.append(
                                {"lease_id": lease.lease_id, "error_code": exc.code}
                            )
                self._quarantined = quarantined
                self.last_error_code = None
'''
assert old in t
t = t.replace(old, new, 1)
old = '''            self._active = False
            try:
                return await self._adapter.suspend_for_shutdown()
            except DomainError as exc:
'''
new = '''            self._active = False
            gate_failures: list[str] = []
            if self._wake_gate is not None:
                gate_failures = await self._wake_gate.terminate()
            try:
                return [*gate_failures, *await self._adapter.suspend_for_shutdown()]
            except DomainError as exc:
'''
assert old in t
t = t.replace(old, new, 1)
old = '''        return active

    @staticmethod
'''
new = '''        if self._wake_gate is not None:
            active = await self._wake_gate.decorate(active)
        return active

    @staticmethod
'''
assert old in t
t = t.replace(old, new, 1)
# create: arm after template create; rollback if preflight driver cannot be created.
old = '''                lease = await self._adapter.create(
                    scope=normalized_scope,
                    sender_id=actor.sender_id,
                    actor_ref=actor.actor_ref,
                    source=actor.channel,
                    name=normalize_heartbeat_name(name),
                    cron_expression=normalize_cron_expression(cron_expression),
                    instruction=normalize_instruction(instruction),
                    created_at=now,
                    expires_at=self._native_minute_expiry(now, duration),
                )
                return self._success(
'''
new = '''                lease = await self._adapter.create(
                    scope=normalized_scope,
                    sender_id=actor.sender_id,
                    actor_ref=actor.actor_ref,
                    source=actor.channel,
                    name=normalize_heartbeat_name(name),
                    cron_expression=normalize_cron_expression(cron_expression),
                    instruction=normalize_instruction(instruction),
                    created_at=now,
                    expires_at=self._native_minute_expiry(now, duration),
                )
                if self._wake_gate is not None:
                    try:
                        lease = await self._wake_gate.arm(lease)
                    except Exception:
                        await self._adapter.delete(lease.lease_id, normalized_scope)
                        raise
                return self._success(
'''
assert old in t
t = t.replace(old, new, 1)
# disable: disarm first; active template deletion means any racing driver run_job_now becomes no-op.
old = '''                changed = False
                for lease_id in ids:
                    if await self._adapter.delete(lease_id, normalized_scope):
                        changed = True
'''
new = '''                changed = False
                for lease_id in ids:
                    if self._wake_gate is not None:
                        await self._wake_gate.disarm(lease_id)
                    if await self._adapter.delete(lease_id, normalized_scope):
                        changed = True
'''
assert old in t
t = t.replace(old, new, 1)
# renew: re-arm driver with new cron/expiry.
old = '''                renewed.append(updated)
            return self._success(
'''
new = '''                if self._wake_gate is not None:
                    updated = await self._wake_gate.arm(updated)
                renewed.append(updated)
            return self._success(
'''
assert old in t
t = t.replace(old, new, 1)
# health includes preflight driver.
old = '''        return {
            "heartbeat_service_active": self._active,
            "heartbeat_count": len(snapshot["heartbeat_leases"]),
            "heartbeat_quarantined_count": len(snapshot["quarantined"]),
            "heartbeat_last_error_code": self.last_error_code,
        }
'''
new = '''        gate_health = (
            await self._wake_gate.health()
            if self._wake_gate is not None
            else {"heartbeat_preflight_active": False}
        )
        return {
            "heartbeat_service_active": self._active,
            "heartbeat_count": len(snapshot["heartbeat_leases"]),
            "heartbeat_quarantined_count": len(snapshot["quarantined"]),
            "heartbeat_last_error_code": self.last_error_code,
            **gate_health,
        }
'''
assert old in t
t = t.replace(old, new, 1)
p.write_text(t, encoding='utf-8')

# main: create one shared session preflight for heartbeat and wire disabled active templates.
p = Path('main.py')
t = p.read_text(encoding='utf-8')
if 'from .heartbeat_gate import HeartbeatWakeGate\n' not in t:
    t = t.replace('from .heartbeat_service import HeartbeatService\n', 'from .heartbeat_gate import HeartbeatWakeGate\nfrom .heartbeat_service import HeartbeatService\n', 1)
old = '''        self.heartbeat_service = HeartbeatService(
            self.settings,
            AstrBotCronAdapter(context),
        )
'''
new = '''        self.heartbeat_wake_gate = HeartbeatWakeGate(
            getattr(context, "cron_manager", None),
            preflight=self._heartbeat_preflight,
        )
        self.heartbeat_service = HeartbeatService(
            self.settings,
            AstrBotCronAdapter(context, active_execution_enabled=False),
            self.heartbeat_wake_gate,
        )
'''
assert old in t
t = t.replace(old, new, 1)
anchor = '''    async def _echo_preflight(self, scope: str) -> bool:
        if self._terminated or not self.settings.echo_enabled:
            return False
        status = await self._session_status(scope)
        return status.enabled is not False

'''
addition = '''    async def _heartbeat_preflight(self, scope: str) -> bool:
        if self._terminated:
            return False
        status = await self._session_status(scope)
        return status.enabled is not False

'''
assert anchor in t
if addition not in t:
    t = t.replace(anchor, anchor + addition, 1)
# error contracts
anchor = '    "heartbeat_service_inactive": ("host_capability", "inspect_native_cron", False),\n'
if anchor in t:
    extra = '    "heartbeat_preflight_unavailable": ("host_capability", "inspect_native_cron", False),\n    "heartbeat_preflight_inactive": ("host_capability", "inspect_native_cron", False),\n    "heartbeat_preflight_create_failed": ("host_runtime", "inspect_native_cron", True),\n'
    if '"heartbeat_preflight_unavailable"' not in t:
        t = t.replace(anchor, anchor + extra, 1)
p.write_text(t, encoding='utf-8')
