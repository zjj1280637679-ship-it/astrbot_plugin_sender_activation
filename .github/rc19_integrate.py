from pathlib import Path

main_path = Path("main.py")
text = main_path.read_text(encoding="utf-8")

# Imports.
anchor = '''from .activation_reservation import ActivationReservationCoordinator\n'''
insert = '''from .activation_reservation import ActivationReservationCoordinator\nfrom .attention_service import (\n    ATTENTION_BACKUP_KEY,\n    ATTENTION_STATE_KEY,\n    IGNORE_EFFECT_CONTRACT,\n    AttentionIgnoreService,\n)\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)

anchor = '''from .heartbeat_service import HeartbeatService\n'''
insert = '''from .heartbeat_service import HeartbeatService\nfrom .echo_service import (\n    ECHO_EFFECT_CONTRACT,\n    ECHO_TAG,\n    EchoService,\n    is_owned_echo_payload,\n)\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)

# Version / extras.
text = text.replace('VERSION = "1.1.0-rc.18"', 'VERSION = "1.1.0-rc.19"', 1)
anchor = '''TURN_YIELD_EXTRA = "sender_activation_turn_yield"\n'''
insert = '''TURN_YIELD_EXTRA = "sender_activation_turn_yield"\nATTENTION_IGNORED_EXTRA = "sender_attention_ignored"\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)

# Tool effect contracts.
anchor = '''    "yield_current_turn": "contextual_no_visible_reply_for_plugin_proactive_turn",\n}'''
insert = '''    "yield_current_turn": "contextual_no_visible_reply_for_plugin_proactive_turn",\n    "manage_attention_ignore": IGNORE_EFFECT_CONTRACT,\n    "manage_echo_hook": ECHO_EFFECT_CONTRACT,\n}'''
assert anchor in text
text = text.replace(anchor, insert, 1)

# Error classifications.
anchor = '''    "yield_not_available": (\n        "state_precondition",\n        "reply_normally_or_wait_for_plugin_proactive_turn",\n        False,\n    ),\n}'''
insert = '''    "yield_not_available": (\n        "state_precondition",\n        "reply_normally_or_wait_for_plugin_proactive_turn",\n        False,\n    ),\n    "attention_ignore_disabled": (\n        "configuration",\n        "enable_agent_ignore_in_plugin_config",\n        False,\n    ),\n    "attention_storage_unavailable": ("storage", "inspect_attention_storage", False),\n    "attention_storage_write_failed": ("storage", "retry_after_storage_recovery", True),\n    "attention_commit_indeterminate": ("storage", "reload_and_inspect_attention_state", False),\n    "echo_disabled": ("configuration", "enable_echo_in_plugin_config", False),\n    "echo_source_not_allowed": ("authorization", "adjust_echo_source_permissions", False),\n    "echo_source_unavailable": ("state_precondition", "use_from_a_real_activation_turn", False),\n    "echo_recursion_forbidden": ("structural", "do_not_create_echo_from_echo", False),\n    "echo_service_inactive": ("host_capability", "inspect_echo_health", False),\n    "echo_native_scheduler_unavailable": ("host_capability", "enable_native_cron", False),\n    "echo_scope_capacity_exceeded": ("capacity", "cancel_pending_echoes_or_raise_limit", False),\n    "echo_total_capacity_exceeded": ("capacity", "cancel_pending_echoes_or_raise_limit", False),\n    "echo_create_failed": ("host_runtime", "inspect_native_cron", True),\n}'''
assert anchor in text
text = text.replace(anchor, insert, 1)

# Add a fast object-only filter ahead of SenderActivationFilter.
anchor = '''class SenderActivationFilter(CustomFilter):\n'''
insert = '''class AttentionGuardFilter(CustomFilter):\n    """Exact-object pre-LLM guard. No policy for this ID means immediate inert return."""\n\n    def filter(self, event: AstrMessageEvent, cfg: Any) -> bool:\n        try:\n            plugin = _ACTIVE_PLUGIN\n            if plugin is None or not plugin.settings.agent_ignore_enabled:\n                return False\n            if event.get_platform_name() != "aiocqhttp" or event.is_private_chat():\n                return False\n            sender_id = str(event.get_sender_id() or "").strip()\n            self_id = str(event.get_self_id() or "").strip()\n            if not sender_id or (self_id and sender_id == self_id):\n                return False\n            scope = event.unified_msg_origin\n            if not plugin.attention_service.has_policy(scope, sender_id):\n                return False\n            session_status = plugin.session_gate.read_sync(scope)\n            if session_status.enabled is False:\n                return False\n\n            # Count only events that could otherwise reach the main Agent. Ordinary\n            # messages from an untracked object must not accumulate merely because an\n            # ignore policy exists.\n            if event.is_at_or_wake_command:\n                return True\n            if not plugin.service.storage_ready:\n                return False\n            return bool(plugin.service.preview(scope, sender_id).admitted)\n        except Exception:\n            return False\n\n\nclass SenderActivationFilter(CustomFilter):\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)

# Services in __init__.
anchor = '''        self.service = ActivationService(\n            self.settings,\n            AstrBotKVStateStore(self),\n        )\n'''
insert = '''        self.service = ActivationService(\n            self.settings,\n            AstrBotKVStateStore(self),\n        )\n        self.attention_service = AttentionIgnoreService(\n            self.settings.ignore_limits,\n            AstrBotKVStateStore(\n                self,\n                state_key=ATTENTION_STATE_KEY,\n                backup_key=ATTENTION_BACKUP_KEY,\n            ),\n        )\n        self.echo_service = EchoService(\n            self.settings.echo_limits,\n            getattr(context, "cron_manager", None),\n        )\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)

# Initialize.
anchor = '''        await self.access_service.initialize()\n        await self.service.initialize()\n        try:\n            await self.heartbeat_service.initialize()\n'''
insert = '''        await self.access_service.initialize()\n        await self.service.initialize()\n        await self.attention_service.initialize()\n        try:\n            await self.echo_service.initialize()\n        except DomainError as exc:\n            logger.warning(\n                "[sender_activation] echo_inert error=%s",\n                exc.code,\n            )\n        try:\n            await self.heartbeat_service.initialize()\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)

# Terminate.
anchor = '''        self.activation_reservations.close()\n        heartbeat_failures = await self.heartbeat_service.terminate()\n'''
insert = '''        self.activation_reservations.close()\n        echo_failures = await self.echo_service.terminate()\n        if echo_failures:\n            logger.warning(\n                "[sender_activation] echo_shutdown_failures count=%d",\n                len(echo_failures),\n            )\n        heartbeat_failures = await self.heartbeat_service.terminate()\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)
anchor = '''        await self.service.terminate()\n        await self.access_service.terminate()\n'''
insert = '''        await self.attention_service.terminate()\n        await self.service.terminate()\n        await self.access_service.terminate()\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)

# Safe echo snapshot helper inserted before page scope map.
anchor = '''    async def _page_scope_map(self) -> dict[str, str]:\n'''
insert = '''    async def _safe_echo_snapshot(self, scope: str | None = None) -> dict[str, Any]:\n        try:\n            return await self.echo_service.snapshot(scope=scope)\n        except Exception:\n            return {"echo_hooks": [], "quarantined": [], "raw_scopes": []}\n\n    async def _page_scope_map(self) -> dict[str, str]:\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)

# Page scope sources.
anchor = '''        heartbeat = await self.heartbeat_service.snapshot(\n            scope_ref=self.service.scope_ref,\n        )\n        access = self.access_service.snapshot()\n        scope_map: dict[str, str] = {}\n        for scope in [\n            *state["known_scopes"],\n            *heartbeat["raw_scopes"],\n            *access["known_scopes"],\n        ]:\n'''
insert = '''        heartbeat = await self.heartbeat_service.snapshot(\n            scope_ref=self.service.scope_ref,\n        )\n        access = self.access_service.snapshot()\n        attention = self.attention_service.snapshot()\n        echo = await self._safe_echo_snapshot()\n        scope_map: dict[str, str] = {}\n        for scope in [\n            *state["known_scopes"],\n            *heartbeat["raw_scopes"],\n            *access["known_scopes"],\n            *attention["known_scopes"],\n            *echo["raw_scopes"],\n        ]:\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)

# Page snapshot data.
anchor = '''        heartbeat = await self.heartbeat_service.snapshot(\n            scope=scope,\n            scope_ref=self.service.scope_ref,\n        )\n        if scope is None:\n'''
insert = '''        heartbeat = await self.heartbeat_service.snapshot(\n            scope=scope,\n            scope_ref=self.service.scope_ref,\n        )\n        attention = self.attention_service.snapshot(scope=scope)\n        echo = await self._safe_echo_snapshot(scope=scope)\n        if scope is None:\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)
anchor = '''            access["operator_grants"] = []\n            heartbeat["heartbeat_leases"] = []\n'''
insert = '''            access["operator_grants"] = []\n            heartbeat["heartbeat_leases"] = []\n            attention["ignore_policies"] = []\n            echo["echo_hooks"] = []\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)
anchor = '''            "operator_grants": [project(row) for row in access["operator_grants"]],\n            "heartbeat_leases": heartbeat["heartbeat_leases"],\n            "heartbeat_quarantined": heartbeat["quarantined"],\n            "known_scopes": sorted(await self._page_scope_map()),\n'''
insert = '''            "operator_grants": [project(row) for row in access["operator_grants"]],\n            "ignore_policies": [project(row) for row in attention["ignore_policies"]],\n            "heartbeat_leases": heartbeat["heartbeat_leases"],\n            "heartbeat_quarantined": heartbeat["quarantined"],\n            "echo_hooks": echo["echo_hooks"],\n            "echo_quarantined": echo["quarantined"],\n            "known_scopes": sorted(await self._page_scope_map()),\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)

# Tool permission diagnostics.
anchor = '''        operation_tools = (\n            "manage_sender_activation",\n            "manage_sender_activation_rate",\n            "manage_heartbeat_lease",\n        )\n'''
insert = '''        operation_tools = (\n            "manage_sender_activation",\n            "manage_sender_activation_rate",\n            "manage_heartbeat_lease",\n            "manage_attention_ignore",\n            "manage_echo_hook",\n        )\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)

# Echo source helpers before operator context notice.
anchor = '''    def _operator_context_notice(\n'''
insert = '''    def _activation_source(self, event: AstrMessageEvent) -> str | None:\n        payload = event.get_extra("cron_payload")\n        if isinstance(payload, dict) and is_owned_echo_payload(payload):\n            return "echo"\n        if event.get_extra(DECISION_EXTRA):\n            return "sender_activation"\n        if (\n            isinstance(payload, dict)\n            and HEARTBEAT_TAG in payload\n            and is_owned_heartbeat_payload(payload)\n        ):\n            return "heartbeat"\n        if event.is_at_or_wake_command:\n            return "native_wake"\n        if event.get_extra(RECOVERY_REPORT_EXTRA):\n            return "recovery_report"\n        return None\n\n    def _echo_source_allowed(self, source: str) -> bool:\n        if source == "native_wake":\n            return self.settings.echo_allow_native_wake_source\n        if source == "sender_activation":\n            return self.settings.echo_allow_sender_activation_source\n        if source == "heartbeat":\n            return self.settings.echo_allow_heartbeat_source\n        return False\n\n    def _operator_context_notice(\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)

# Insert guard handler before activation handler.
anchor = '''    @filter.custom_filter(SenderActivationFilter, priority=2000)\n    async def activate_native_agent(self, event: AstrMessageEvent) -> None:\n'''
insert = '''    @filter.custom_filter(AttentionGuardFilter, priority=3000)\n    async def apply_attention_guard(self, event: AstrMessageEvent) -> None:\n        try:\n            scope = self._scope(event)\n            sender = self._sender(event)\n            decision = self.attention_service.evaluate_activation_attempt(scope, sender)\n            if not decision.ignored:\n                return\n            native_wake = bool(event.is_at_or_wake_command)\n            mode = self.settings.ignore_native_wake_mode if native_wake else "extra_only"\n            payload = {**decision.as_dict(), "native_wake": native_wake, "mode": mode}\n            event.set_extra(ATTENTION_IGNORED_EXTRA, payload)\n\n            if not native_wake:\n                # The later sender-activation handler sees the extra marker and stays inert.\n                return\n            if mode == "extra_only":\n                # Native wake remains real engagement in this conservative mode.\n                if self.settings.echo_enabled and self.settings.echo_cancel_on_native_wake:\n                    await self.echo_service.cancel_scope(scope)\n                return\n            if mode == "suppress_llm":\n                # Keep other plugin handlers available but prevent AstrBot's default Agent path.\n                event.is_at_or_wake_command = False\n                return\n            if mode == "stop_event":\n                event.stop_event()\n                return\n        except DomainError as exc:\n            logger.debug("[sender_activation] attention_guard_inert code=%s", exc.code)\n        except Exception:\n            logger.exception("[sender_activation] attention_guard_failed")\n\n    @filter.custom_filter(SenderActivationFilter, priority=2000)\n    async def activate_native_agent(self, event: AstrMessageEvent) -> None:\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)

# Activation handler: ignored marker first, then cancellation on real native/tracked activation.
anchor = '''            scope = self._scope(event)\n            sender = self._sender(event)\n            if event.is_at_or_wake_command:\n                return\n            reservation = await self.activation_reservations.reserve(scope, sender)\n'''
insert = '''            scope = self._scope(event)\n            sender = self._sender(event)\n            if event.get_extra(ATTENTION_IGNORED_EXTRA):\n                return\n            if event.is_at_or_wake_command:\n                if self.settings.echo_enabled and self.settings.echo_cancel_on_native_wake:\n                    await self.echo_service.cancel_scope(scope)\n                return\n            reservation = await self.activation_reservations.reserve(scope, sender)\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)
anchor = '''            event.set_extra(DECISION_EXTRA, decision.as_dict())\n            event.set_extra("enable_streaming", False)\n'''
insert = '''            if self.settings.echo_enabled and self.settings.echo_cancel_on_sender_activation:\n                await self.echo_service.cancel_scope(scope)\n            event.set_extra(DECISION_EXTRA, decision.as_dict())\n            event.set_extra("enable_streaming", False)\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)

# Echo proactive source for yield.
anchor = '''    def _plugin_proactive_source(self, event: AstrMessageEvent) -> str | None:\n        if event.get_extra(DECISION_EXTRA):\n            return "sender_activation"\n        payload = event.get_extra("cron_payload")\n'''
insert = '''    def _plugin_proactive_source(self, event: AstrMessageEvent) -> str | None:\n        payload = event.get_extra("cron_payload")\n        if isinstance(payload, dict) and is_owned_echo_payload(payload):\n            return "echo"\n        if event.get_extra(DECISION_EXTRA):\n            return "sender_activation"\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)

# Insert new tools before access tool.
anchor = '''    @filter.llm_tool(name="manage_sender_activation_access")\n'''
insert = '''    @filter.llm_tool(name="manage_attention_ignore")\n    async def manage_attention_ignore(\n        self,\n        event: AstrMessageEvent,\n        action: str = "",\n        target_ids: list[str] | None = None,\n        trigger_count: int = 0,\n        trigger_window_seconds: float = 0,\n        ignore_duration_seconds: int = 0,\n        policy_seconds: int = 0,\n    ) -> str:\n        """管理主 Agent 自主使用的有限期对象忽略策略。用于减少已识别对象未来可激活事件的 LLM 成本；set 可组合对象、计数与时间窗口，clear 撤销，list 查询。沉默并不禁止先调用此控制工具；需要无可见回复时可在后续最终工具选择再单独调用 yield_current_turn。详细边界见 group-duty-orchestration Skill。\n\n        Args:\n            action(string): set、clear 或 list。\n            target_ids(list[string]): 当前群真实数字 QQ ID 数组；list 可空。\n            trigger_count(number): set 时累计多少个“原本可激活 AI”的事件后触发忽略；0 使用配置默认值。\n            trigger_window_seconds(number): set 时计数滑动窗口秒数；0 表示当前策略运行生命周期累计。\n            ignore_duration_seconds(number): 阈值命中后实际忽略多久；0 使用配置默认值。\n            policy_seconds(number): 该对象计数/忽略策略本身最多存在多久；0 使用配置默认值。\n        """\n        tool = "manage_attention_ignore"\n        try:\n            action_name = str(action or "").strip().lower()\n            if action_name == "set" and not self.settings.agent_ignore_enabled:\n                raise DomainError(\n                    "attention_ignore_disabled",\n                    "插件配置未授予主 Agent 新建对象忽略策略的权限。",\n                )\n            scope = self._scope(event)\n            if action_name == "set":\n                await self._require_new_state_allowed(scope)\n            actor = self._actor(event)\n            result = await self.attention_service.manage(\n                scope=scope,\n                action=action_name,\n                target_ids=target_ids,\n                trigger_count=trigger_count,\n                trigger_window_seconds=trigger_window_seconds,\n                ignore_duration_seconds=ignore_duration_seconds,\n                policy_seconds=policy_seconds,\n                created_by=actor.actor_ref,\n            )\n            result["tool"] = tool\n            result["authority"] = "agent_configured_ignore_permission"\n            return _json(result)\n        except DomainError as exc:\n            return _tool_error(exc, tool)\n        except Exception as exc:\n            logger.exception("[sender_activation] attention_ignore_tool_failed")\n            return _tool_error(\n                DomainError("internal_error", f"工具执行失败: {type(exc).__name__}"),\n                tool,\n            )\n\n    @filter.llm_tool(name="manage_echo_hook")\n    async def manage_echo_hook(\n        self,\n        event: AstrMessageEvent,\n        action: str = "",\n        hook_ids: list[str] | None = None,\n        group_ids: list[str] | None = None,\n        delay_seconds: float = 0,\n        count: int = 0,\n        interval_seconds: float = 0,\n        instruction: str = "",\n    ) -> str:\n        """管理可选的有限回响钩子。create 仅在配置允许的真实激活回合中，为当前会话预排有限次数的一次性主动再检查；没有显式调用就没有回响。回响来源的回合结构上禁止再 create。cancel/list 可清理或查询。\n\n        Args:\n            action(string): create、cancel 或 list。\n            hook_ids(list[string]): cancel 时可指定一次性回响任务 ID。\n            group_ids(list[string]): cancel 时可指定同一次创建形成的回响组 ID。\n            delay_seconds(number): 首次回响延迟秒数；0 使用配置默认值。\n            count(number): 本次一次性预排的回响次数；0 使用配置默认值。\n            interval_seconds(number): count>1 时各回响之间的秒数；0 使用配置默认值。\n            instruction(string): 回响时重新检查原会话的语境目标。\n        """\n        tool = "manage_echo_hook"\n        try:\n            action_name = str(action or "").strip().lower()\n            scope = self._scope(event)\n            if action_name == "list":\n                result = await self.echo_service.list_scope(scope)\n            elif action_name == "cancel":\n                result = await self.echo_service.cancel(\n                    scope=scope,\n                    hook_ids=hook_ids,\n                    group_ids=group_ids,\n                )\n            elif action_name == "create":\n                if not self.settings.echo_enabled:\n                    raise DomainError(\n                        "echo_disabled",\n                        "插件配置未授予主 Agent 创建回响的权限。",\n                    )\n                await self._require_new_state_allowed(scope)\n                source = self._activation_source(event)\n                if source is None:\n                    raise DomainError(\n                        "echo_source_unavailable",\n                        "当前回合没有可验证的真实激活来源，不能创建回响。",\n                    )\n                if source == "echo":\n                    raise DomainError(\n                        "echo_recursion_forbidden",\n                        "回响触发的回合不能创建新的回响。",\n                    )\n                if not self._echo_source_allowed(source):\n                    raise DomainError(\n                        "echo_source_not_allowed",\n                        f"插件配置未允许 {source} 来源创建回响。",\n                    )\n                actor = self._actor(event)\n                result = await self.echo_service.create(\n                    scope=scope,\n                    sender_id=actor.sender_id,\n                    actor_ref=actor.actor_ref,\n                    source=source,\n                    delay_seconds=delay_seconds,\n                    count=count,\n                    interval_seconds=interval_seconds,\n                    instruction=instruction,\n                )\n            else:\n                raise DomainError("invalid_action", "action 必须是 create、cancel 或 list。")\n            result["tool"] = tool\n            return _json(result)\n        except DomainError as exc:\n            return _tool_error(exc, tool)\n        except Exception as exc:\n            logger.exception("[sender_activation] echo_tool_failed")\n            return _tool_error(\n                DomainError("internal_error", f"工具执行失败: {type(exc).__name__}"),\n                tool,\n            )\n\n    @filter.llm_tool(name="manage_sender_activation_access")\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)

# Yield documentation: control tools may precede a silent final yield in another selection.
old = '''        """仅在本插件对象激活或心跳额外唤醒的当前 Agent 回合中，结构化结束本轮且不发送可见回复。当前没有独立公开价值时使用；普通 @、原生会话或其他插件唤醒不可用。成功调用必须作为当前工具选择中的唯一且最后一个调用。详细规则见 sender-activation 或 group-duty-orchestration Skill。'''
new = '''        """仅在本插件对象激活、心跳或回响额外唤醒的当前 Agent 回合中，结构化结束本轮且不发送可见回复。沉默只约束公开输出，不等于禁止控制面工具：可先在前一个工具选择中设置有限忽略/清理状态，再在后续最终工具选择中把本工具作为唯一调用结束本轮。普通 @、原生会话或其他插件唤醒不可用。详细规则见 group-duty-orchestration Skill。'''
assert old in text
text = text.replace(old, new, 1)

# Health data.
anchor = '''                **self.service.health(),\n                **self.access_service.health(),\n                **await self.heartbeat_service.health(),\n                **self.activation_reservations.health(),\n                "turn_yield_count": self._yield_count,\n'''
insert = '''                **self.service.health(),\n                **self.access_service.health(),\n                **self.attention_service.health(),\n                **await self.heartbeat_service.health(),\n                **await self.echo_service.health(),\n                **self.activation_reservations.health(),\n                "turn_yield_count": self._yield_count,\n'''
assert anchor in text
text = text.replace(anchor, insert, 1)

main_path.write_text(text, encoding="utf-8")

# Version metadata only on candidate branch.
metadata = Path("metadata.yaml")
meta = metadata.read_text(encoding="utf-8")
assert 'version: "1.1.0-rc.18"' in meta
meta = meta.replace('version: "1.1.0-rc.18"', 'version: "1.1.0-rc.19"', 1)
metadata.write_text(meta, encoding="utf-8")
