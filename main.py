


from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from astrbot.api import logger, sp
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.event.filter import CustomFilter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request
from astrbot.core.agent.message import TextPart

from .access_service import (
    ACCESS_BACKUP_KEY,
    ACCESS_EFFECT_CONTRACT,
    ACCESS_STATE_KEY,
    AccessService,
)
from .activation_reservation import ActivationReservationCoordinator
from .cron_adapter import AstrBotCronAdapter
from .domain import DomainError, normalize_scope
from .heartbeat_domain import HEARTBEAT_TAG, is_owned_heartbeat_payload
from .heartbeat_service import HeartbeatService
from .host_diagnostics import (
    build_agent_config_notice,
    evaluate_host_config,
    unavailable_host_config_report,
)
from .service import (
    ACTIVATION_EFFECT_CONTRACT,
    HEARTBEAT_EFFECT_CONTRACT,
    RATE_EFFECT_CONTRACT,
    ActivationService,
    ActorContext,
)
from .session_gate import AstrBotSessionGate, SessionGateStatus
from .settings import PluginSettings
from .storage import AstrBotKVStateStore

PLUGIN_NAME = "astrbot_plugin_sender_activation"
VERSION = "1.1.0-rc.17"
DECISION_EXTRA = "sender_activation_decision"
RECOVERY_REPORT_EXTRA = "sender_activation_recovery_report"
TURN_YIELD_EXTRA = "sender_activation_turn_yield"
API_PREFIX = f"/{PLUGIN_NAME}"

_ACTIVE_PLUGIN: SenderActivationPlugin | None = None

_TOOL_EFFECT_CONTRACTS = {
    "manage_sender_activation_access": ACCESS_EFFECT_CONTRACT,
    "manage_sender_activation": ACTIVATION_EFFECT_CONTRACT,
    "manage_sender_activation_rate": RATE_EFFECT_CONTRACT,
    "manage_heartbeat_lease": HEARTBEAT_EFFECT_CONTRACT,
    "yield_current_turn": "contextual_no_visible_reply_for_plugin_proactive_turn",
}
_TOOL_ERROR_POLICIES: dict[str, tuple[str, str, bool]] = {
    "session_plugin_inactive": ("session", "enable_plugin_for_session", False),
    "unsupported_platform": ("platform", "use_supported_aiocqhttp_group", False),
    "unsupported_chat_type": ("platform", "use_supported_aiocqhttp_group", False),
    "missing_sender": ("platform", "retry_from_valid_group_event", False),
    "invalid_scope": ("platform", "retry_from_valid_group_event", False),
    "activation_absent": ("state_precondition", "enable_activation_instead", False),
    "activation_required": (
        "state_precondition",
        "establish_activation_first",
        False,
    ),
    "scope_capacity_exceeded": (
        "capacity",
        "free_scope_capacity_or_raise_limit",
        False,
    ),
    "total_capacity_exceeded": (
        "capacity",
        "free_total_capacity_or_raise_limit",
        False,
    ),
    "state_too_large": ("capacity", "repair_or_reduce_stored_state", False),
    "storage_unavailable": ("storage", "inspect_storage_health", False),
    "storage_write_failed": ("storage", "retry_after_storage_recovery", True),
    "commit_indeterminate": ("storage", "reload_and_inspect_state", False),
    "invalid_actor": ("internal", "report_failure", False),
    "invalid_source": ("internal", "report_failure", False),
    "invalid_state_document": ("storage", "repair_or_restore_state", False),
    "unsupported_state_schema": ("storage", "upgrade_or_restore_state", False),
    "admin_required": ("authorization", "ask_astrbot_admin_to_grant_access", False),
    "operator_access_required": (
        "authorization",
        "ask_astrbot_admin_to_grant_current_id",
        False,
    ),
    "access_storage_unavailable": (
        "storage",
        "inspect_operator_access_storage",
        False,
    ),
    "access_storage_write_failed": (
        "storage",
        "retry_after_access_storage_recovery",
        True,
    ),
    "access_commit_indeterminate": (
        "storage",
        "reload_and_inspect_operator_access",
        False,
    ),
    "operator_access_absent": (
        "state_precondition",
        "grant_operator_access_instead",
        False,
    ),
    "access_scope_capacity_exceeded": (
        "capacity",
        "revoke_unused_scope_operators",
        False,
    ),
    "access_total_capacity_exceeded": (
        "capacity",
        "revoke_unused_operators",
        False,
    ),
    "internal_error": ("internal", "report_failure", False),
    "native_cron_unavailable": ("host_capability", "enable_native_cron", False),
    "native_cron_create_failed": ("host_runtime", "inspect_native_cron", True),
    "native_cron_update_failed": ("host_runtime", "inspect_native_cron", True),
    "native_cron_update_indeterminate": (
        "host_runtime",
        "reload_and_inspect_native_cron",
        False,
    ),
    "native_cron_delete_failed": ("host_runtime", "inspect_native_cron", True),
    "heartbeat_service_inactive": (
        "host_capability",
        "inspect_heartbeat_health",
        False,
    ),
    "yield_not_available": (
        "state_precondition",
        "reply_normally_or_wait_for_plugin_proactive_turn",
        False,
    ),
}


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _temporary_context_part(text: str) -> TextPart:
    return TextPart(text=text).mark_as_temp()


def _mask_identifier(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) <= 4:
        return "*" * len(text)
    return f"{text[:2]}***{text[-2:]}"


def _actor_reference(kind: str, value: Any) -> str:
    text = str(value or "").strip() or "unknown"
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{kind}:{digest}"


def _recovery_context(report: dict[str, Any]) -> str:
    target_rows: list[str] = []
    for target in report.get("targets", []):
        target_id = str(target.get("target_id") or "")
        activation_seconds = int(target.get("activation_remaining_seconds") or 0)
        rate_maximum = target.get("rate_max_activations")
        if rate_maximum is None:
            rate_text = "无从属限频"
        else:
            rate_text = (
                f"原限频 {int(rate_maximum)} 次/"
                f"{float(target.get('rate_window_seconds') or 0):g} 秒，"
                f"原剩余 {int(target.get('rate_remaining_seconds') or 0)} 秒"
            )
        target_rows.append(
            f"- QQ {target_id}: 原激活剩余约 {activation_seconds} 秒；{rate_text}"
        )
    targets = "\n".join(target_rows) or "- 无可重建目标"
    return (
        "[发言者激活结构恢复报告]\n"
        "上一次由本插件额外唤醒的 Agent 回合以结构错误结束，程序已一次性"
        "撤销当前作用域全部额外激活与从属限频，AstrBot 原生唤醒路径未被"
        "阻断。\n"
        f"报告 ID: {report.get('report_id')}\n"
        f"{targets}\n"
        "这是事实报告，不是建立租约的用户命令。请结合现有对话判断：只有"
        "存在尚未撤销的既有租约需求时，才可用正式工具帧重建合适租期；"
        "若原租约异常或没有既有需求，保持原生状态。不得凭此报告为无人要求"
        "的对象自动创建租约。是否重建属于控制面判断，不得把“我在考虑是否"
        "重建”之类的内部维护过程作为群聊回复；没有独立的公开价值时，在"
        "本插件主动回合应正式调用 yield_current_turn。"
    )


def _tool_error_policy(code: str) -> tuple[str, str, bool]:
    if policy := _TOOL_ERROR_POLICIES.get(code):
        return policy
    if code.startswith(("invalid_", "missing_", "empty_", "too_many_")):
        return ("input", "correct_arguments", False)
    if code.startswith(("future_", "duplicate_")):
        return ("storage", "repair_or_restore_state", False)
    return ("internal", "report_failure", False)


def _tool_error(error: DomainError, tool: str) -> str:
    failure_class, recovery_action, same_call_retryable = _tool_error_policy(error.code)
    indeterminate = error.code == "commit_indeterminate"
    return _json(
        {
            "status": "error",
            "error_code": error.code,
            "failure_class": failure_class,
            "recovery_action": recovery_action,
            "same_call_retryable": same_call_retryable,
            "tool": tool,
            "message": error.message,
            "changed": None if indeterminate else False,
            "effect_applied": None if indeterminate else False,
            "effect_state": "indeterminate" if indeterminate else "not_applied",
            "effect_contract": _TOOL_EFFECT_CONTRACTS.get(tool),
            "reply_guaranteed": False,
        }
    )


class SenderActivationFilter(CustomFilter):


    def filter(self, event: AstrMessageEvent, cfg: Any) -> bool:
        try:
            plugin = _ACTIVE_PLUGIN
            if plugin is None:
                return False
            if event.get_platform_name() != "aiocqhttp":
                return False
            if event.is_private_chat():
                return False
            sender_id = str(event.get_sender_id() or "").strip()
            self_id = str(event.get_self_id() or "").strip()
            if not sender_id or (self_id and sender_id == self_id):
                return False
            native_wake = bool(event.is_at_or_wake_command)
            if not native_wake:
                if not plugin.service.storage_ready:
                    return False
                decision = plugin.service.preview(
                    event.unified_msg_origin,
                    sender_id,
                )
                if not decision.admitted:
                    return False
            session_status = plugin.session_gate.read_sync(
                event.unified_msg_origin,
            )
            if session_status.enabled is False:
                logger.debug(
                    "[sender_activation] inert explicit_session_disable=%s sender=%s",
                    session_status.code,
                    _mask_identifier(sender_id),
                )
                return False
            if session_status.enabled is None:
                logger.debug(
                    "[sender_activation] open session_status=%s sender=%s",
                    session_status.code,
                    _mask_identifier(sender_id),
                )
            return True
        except DomainError as exc:
            sender_id = locals().get("sender_id", "")
            logger.debug(
                "[sender_activation] inert decision_error=%s sender=%s",
                exc.code,
                _mask_identifier(sender_id),
            )
            return False
        except Exception:
            sender_id = locals().get("sender_id", "")
            logger.exception(
                "[sender_activation] inert unexpected_filter_error sender=%s",
                _mask_identifier(sender_id),
            )
            return False


class SenderActivationPlugin(Star):


    def __init__(self, context: Context, config: Any = None) -> None:
        super().__init__(context, config)
        self.config = config or {}
        self.settings = PluginSettings.from_config(self.config)
        self.service = ActivationService(
            self.settings,
            AstrBotKVStateStore(self),
        )
        self.access_service = AccessService(
            self.settings,
            AstrBotKVStateStore(
                self,
                state_key=ACCESS_STATE_KEY,
                backup_key=ACCESS_BACKUP_KEY,
            ),
        )
        self.heartbeat_service = HeartbeatService(
            self.settings,
            AstrBotCronAdapter(context),
        )
        self.activation_reservations = ActivationReservationCoordinator(
            self.settings.activation_min_interval_seconds,
            max_slots=self.settings.limits.max_targets_total,
        )
        self.session_gate = AstrBotSessionGate(PLUGIN_NAME, sp)
        self._terminated = False
        self._yield_count = 0

    async def initialize(self) -> None:
        global _ACTIVE_PLUGIN

        self._terminated = False
        await self.access_service.initialize()
        await self.service.initialize()
        try:
            await self.heartbeat_service.initialize()
        except DomainError as exc:
            logger.warning(
                "[sender_activation] heartbeat_inert error=%s",
                exc.code,
            )
        self._register_web_apis()
        _ACTIVE_PLUGIN = self
        health = self.service.health()
        logger.info(
            "[sender_activation] ready version=%s storage_ready=%s "
            "activations=%d rates=%d quarantined=%d",
            VERSION,
            health["storage_ready"],
            health["activation_count"],
            health["rate_count"],
            health["quarantined_count"],
        )

    async def terminate(self) -> None:
        global _ACTIVE_PLUGIN

        self._terminated = True
        self.activation_reservations.close()
        heartbeat_failures = await self.heartbeat_service.terminate()
        if heartbeat_failures:
            logger.warning(
                "[sender_activation] heartbeat_shutdown_failures count=%d",
                len(heartbeat_failures),
            )
        await self.service.terminate()
        await self.access_service.terminate()
        if _ACTIVE_PLUGIN is self:
            _ACTIVE_PLUGIN = None

    def _register_web_apis(self) -> None:
        self.context.register_web_api(
            f"{API_PREFIX}/state",
            self._web_state,
            ["GET"],
            "查看发言者激活租约、限频租约、存储健康和只读宿主配置检测",
        )
        self.context.register_web_api(
            f"{API_PREFIX}/activation",
            self._web_activation,
            ["POST"],
            "管理发言者激活租约",
        )
        self.context.register_web_api(
            f"{API_PREFIX}/rate",
            self._web_rate,
            ["POST"],
            "管理发言者额外激活的限频租约",
        )
        self.context.register_web_api(
            f"{API_PREFIX}/heartbeat",
            self._web_heartbeat,
            ["POST"],
            "管理复用 AstrBot 原生 Cron 的有限期心跳租约",
        )
        self.context.register_web_api(
            f"{API_PREFIX}/access",
            self._web_access,
            ["POST"],
            "管理当前作用域的插件操作员授权",
        )

    async def _page_scope_map(self) -> dict[str, str]:
        state = self.service.snapshot()
        heartbeat = await self.heartbeat_service.snapshot(
            scope_ref=self.service.scope_ref,
        )
        access = self.access_service.snapshot()
        scope_map: dict[str, str] = {}
        for scope in [
            *state["known_scopes"],
            *heartbeat["raw_scopes"],
            *access["known_scopes"],
        ]:
            reference = self.service.scope_ref(scope)
            if reference in scope_map and scope_map[reference] != scope:
                raise DomainError(
                    "scope_ref_collision",
                    "作用域引用发生碰撞；页面管理已安全停止。",
                )
            scope_map[reference] = scope
        return scope_map

    def _require_active_web(self) -> None:
        if self._terminated or not self.service.storage_ready:
            raise DomainError(
                "storage_unavailable",
                "插件当前未激活或状态存储不可用。",
            )

    async def _resolve_page_scope(self, scope_ref: Any) -> str:
        reference = str(scope_ref or "").strip()
        if not reference:
            raise DomainError("missing_scope_ref", "必须选择已有作用域。")
        scope = (await self._page_scope_map()).get(reference)
        if scope is None:
            raise DomainError(
                "unknown_scope_ref",
                "作用域引用不存在或已经失效。",
            )
        return scope

    async def _session_status(self, scope: str) -> SessionGateStatus:
        return await self.session_gate.read(scope)

    def _host_config_report(self, scope: str | None) -> dict[str, Any]:
        scope_specific = scope is not None
        try:
            config = self.context.get_config(umo=scope)
            return evaluate_host_config(
                config,
                scope_specific=scope_specific,
            )
        except Exception as exc:
            logger.warning(
                "[sender_activation] host_config_diagnostics_unavailable "
                "scope=%s error=%s",
                self.service.scope_ref(scope) if scope is not None else "default",
                type(exc).__name__,
            )
            return unavailable_host_config_report(
                scope_specific=scope_specific,
            )

    async def _require_new_state_allowed(self, scope: str) -> None:
        status = await self._session_status(scope)
        if status.enabled is not False:
            return
        raise DomainError(
            "session_plugin_inactive",
            "当前会话已明确禁用本插件；不能新增额外激活状态。",
        )

    async def _page_snapshot(
        self,
        *,
        scope: str | None,
    ) -> dict[str, Any]:
        source = self.service.snapshot(scope=scope)
        access = self.access_service.snapshot(scope=scope)
        heartbeat = await self.heartbeat_service.snapshot(
            scope=scope,
            scope_ref=self.service.scope_ref,
        )
        if scope is None:
            source = {
                **source,
                "activation_leases": [],
                "rate_leases": [],
                "recovery_reports": [],
            }
            access["operator_grants"] = []
            heartbeat["heartbeat_leases"] = []

        def project(row: dict[str, Any]) -> dict[str, Any]:
            result = dict(row)
            raw_scope = str(result.pop("scope"))
            result["scope_ref"] = self.service.scope_ref(raw_scope)
            return result

        return {
            **source,
            "activation_leases": [project(row) for row in source["activation_leases"]],
            "rate_leases": [project(row) for row in source["rate_leases"]],
            "recovery_reports": [project(row) for row in source["recovery_reports"]],
            "operator_grants": [project(row) for row in access["operator_grants"]],
            "heartbeat_leases": heartbeat["heartbeat_leases"],
            "heartbeat_quarantined": heartbeat["quarantined"],
            "known_scopes": sorted(await self._page_scope_map()),
        }

    @staticmethod
    def _scope(event: AstrMessageEvent) -> str:
        if event.get_platform_name() != "aiocqhttp":
            raise DomainError(
                "unsupported_platform",
                "本插件首版只支持 aiocqhttp。",
            )
        if event.is_private_chat():
            raise DomainError(
                "unsupported_chat_type",
                "本插件首版只作用于群聊。",
            )
        return normalize_scope(event.unified_msg_origin)

    @staticmethod
    def _sender(event: AstrMessageEvent) -> str:
        sender = str(event.get_sender_id() or "").strip()
        if not sender:
            raise DomainError("missing_sender", "当前事件缺少发送者 QQ ID。")
        return sender

    def _actor(self, event: AstrMessageEvent) -> ActorContext:
        sender = self._sender(event)
        return ActorContext(
            sender_id=sender,
            actor_ref=_actor_reference("qq", sender),
            channel="agent_tool",
            is_admin=bool(event.is_admin()),
            proactive_source=self._plugin_proactive_source(event),
        )

    @staticmethod
    def _dashboard_actor() -> ActorContext:
        return ActorContext(
            sender_id="dashboard",
            actor_ref=_actor_reference(
                "dashboard",
                request.username or "admin",
            ),
            channel="dashboard",
            is_admin=True,
        )

    @staticmethod
    def _tool_permission_report() -> dict[str, Any]:
        operation_tools = (
            "manage_sender_activation",
            "manage_sender_activation_rate",
            "manage_heartbeat_lease",
        )
        informational_tools = (
            "manage_sender_activation_access",
            "yield_current_turn",
        )
        try:
            raw = sp.get(
                "tool_permissions",
                {},
                scope="global",
                scope_id="global",
            )
            defaults = raw.get("_default", {}) if isinstance(raw, dict) else {}
            if not isinstance(defaults, dict):
                defaults = {}
        except Exception as exc:
            return {
                "status": "unknown",
                "source": "shared_preferences_unavailable",
                "error_code": type(exc).__name__,
                "checks": [],
                "blocked_operation_tools": [],
            }

        checks: list[dict[str, Any]] = []
        blocked: list[str] = []
        for name in (*operation_tools, *informational_tools):
            effective = str(defaults.get(name) or "member")
            blocks_delegate = name in operation_tools and effective == "admin"
            if blocks_delegate:
                blocked.append(name)
            checks.append(
                {
                    "tool": name,
                    "effective_permission": effective,
                    "configured": name in defaults,
                    "delegate_reachable": not blocks_delegate,
                    "required_for_delegates": (
                        "member" if name in operation_tools else "guarded_in_plugin"
                    ),
                }
            )
        return {
            "status": "blocked" if blocked else "ready",
            "source": "astrbot_shared_preferences",
            "checks": checks,
            "blocked_operation_tools": blocked,
        }

    def _operator_context_notice(
        self,
        event: AstrMessageEvent,
        scope: str,
    ) -> str | None:
        sender = self._sender(event)
        if not self.access_service.has_operator(scope, sender):
            return None
        report = self._tool_permission_report()
        blocked = report["blocked_operation_tools"]
        text = (
            "[插件授权事实] 当前发送者已由 AstrBot 管理员授予本群的插件操作员"
            "权限。若其自然语言明确要求改变对象激活、限频或心跳状态，请结合"
            "完整语境调用相应正式工具；不需要其拥有 AstrBot 超级管理员权限。"
            "授权不等于强制调用，否定、引用、假设和纯讨论仍有否决权。"
        )
        if blocked:
            text += (
                " 但 AstrBot 原生工具权限仍将这些操作工具设为 admin，授权成员"
                "会在进入插件前被阻断；请管理员在 WebUI 的扩展组件中把以下"
                f"工具改为 member：{', '.join(blocked)}。"
            )
        return text

    @filter.custom_filter(SenderActivationFilter, priority=2000)
    async def activate_native_agent(self, event: AstrMessageEvent) -> None:


        try:
            scope = self._scope(event)
            sender = self._sender(event)
            if event.is_at_or_wake_command:
                return
            reservation = await self.activation_reservations.reserve(scope, sender)
            if not reservation.released or reservation.permit is None:
                logger.debug(
                    "[sender_activation] reservation=%s scope=%s sender=%s",
                    reservation.reason,
                    self.service.scope_ref(scope),
                    _mask_identifier(sender),
                )
                return
            permit = reservation.permit
            committed = False
            decision = self.service.decide(
                scope,
                sender,
            )
            committed = self.activation_reservations.commit(
                permit,
                admitted=decision.admitted,
            )
            if not committed:
                logger.warning(
                    "[sender_activation] reservation_commit_stale scope=%s sender=%s",
                    self.service.scope_ref(scope),
                    _mask_identifier(sender),
                )
                return
            if not decision.admitted:
                return
            event.set_extra(DECISION_EXTRA, decision.as_dict())
            event.set_extra("enable_streaming", False)
            event.is_wake = True
            event.is_at_or_wake_command = True
            logger.debug(
                "[sender_activation] admitted scope=%s sender=%s",
                self.service.scope_ref(decision.scope),
                _mask_identifier(decision.target_id),
            )
        except DomainError as exc:
            permit = locals().get("permit")
            if permit is not None and not locals().get("committed", False):
                self.activation_reservations.commit(permit, admitted=False)
            logger.debug(
                "[sender_activation] inert activation_error=%s",
                exc.code,
            )
        except Exception:
            permit = locals().get("permit")
            if permit is not None and not locals().get("committed", False):
                self.activation_reservations.commit(permit, admitted=False)
            logger.exception("[sender_activation] inert unexpected_activation_error")

    @filter.on_llm_request()
    async def provide_recovery_context(
        self,
        event: AstrMessageEvent,
        request: ProviderRequest,
    ) -> None:


        try:
            try:
                recovery_scope = self._scope(event)
            except DomainError:
                recovery_scope = None
            if recovery_scope is not None:
                report = self.service.pending_recovery(recovery_scope)
                if report is not None:
                    request.extra_user_content_parts.append(
                        _temporary_context_part(_recovery_context(report))
                    )
                    event.set_extra(RECOVERY_REPORT_EXTRA, report["report_id"])
                operator_notice = self._operator_context_notice(
                    event,
                    recovery_scope,
                )
                if operator_notice is not None:
                    request.extra_user_content_parts.append(
                        _temporary_context_part(operator_notice)
                    )
            if self.settings.host_config_notice_mode == "page_and_agent":
                diagnostic_scope = normalize_scope(event.unified_msg_origin)
                notice = build_agent_config_notice(
                    self._host_config_report(diagnostic_scope)
                )
                if notice is not None:
                    request.extra_user_content_parts.append(
                        _temporary_context_part(notice)
                    )
        except DomainError:
            return
        except Exception:
            logger.exception("[sender_activation] agent_context_injection_failed")

    @filter.on_llm_response()
    async def reconcile_after_agent(
        self,
        event: AstrMessageEvent,
        response: LLMResponse,
    ) -> None:


        try:
            if (
                event.get_extra(TURN_YIELD_EXTRA)
                and response is not None
                and response.role == "assistant"
            ):
                response.result_chain = None
                response.completion_text = ""
                response.reasoning_content = None
                response.reasoning_signature = None
                event.set_extra("_llm_reasoning_content", None)
                event.clear_result()
            decision = event.get_extra(DECISION_EXTRA)
            report_id = event.get_extra(RECOVERY_REPORT_EXTRA)
            if decision is None and not report_id:
                return
            scope = self._scope(event)
            if response is not None and response.role == "err" and decision is not None:
                result = await self.service.restore_native_after_agent_error(scope)
                logger.warning(
                    "[sender_activation] native_restore scope=%s changed=%s",
                    result["scope_ref"],
                    result["changed"],
                )
                return
            if report_id and response is not None and response.role != "err":
                await self.service.acknowledge_recovery_report(scope, report_id)
        except DomainError as exc:
            logger.warning(
                "[sender_activation] recovery_reconciliation_failed code=%s",
                exc.code,
            )
        except Exception:
            logger.exception("[sender_activation] recovery_reconciliation_failed")

    @filter.on_agent_done()
    async def remove_yielded_turn_record(
        self,
        event: AstrMessageEvent,
        run_context: Any,
        response: LLMResponse,
    ) -> None:


        if not event.get_extra(TURN_YIELD_EXTRA):
            return
        if response is None or response.role != "assistant":
            return
        messages = getattr(run_context, "messages", None)
        if (
            isinstance(messages, list)
            and messages
            and getattr(messages[-1], "role", None) == "assistant"
        ):
            messages.pop()
        event.clear_result()

    def _plugin_proactive_source(self, event: AstrMessageEvent) -> str | None:
        if event.get_extra(DECISION_EXTRA):
            return "sender_activation"
        payload = event.get_extra("cron_payload")
        if (
            isinstance(payload, dict)
            and HEARTBEAT_TAG in payload
            and is_owned_heartbeat_payload(payload)
        ):
            return "heartbeat"
        if event.get_extra(RECOVERY_REPORT_EXTRA):
            return "recovery_report"
        return None

    @filter.llm_tool(name="manage_sender_activation_access")
    async def manage_sender_activation_access(
        self,
        event: AstrMessageEvent,
        action: str = "",
        operator_ids: list[str] | None = None,
        duration_seconds: int = 0,
    ) -> str:
        """由 AstrBot 管理员管理当前群聊的有限期插件操作员授权。仅在管理员明确要求授予、续期、撤销或查询某个真实 QQ ID 的本插件操作权时调用；不要用于实际建立对象关注、限频或心跳，也不要把讨论、引用、假设或转述当成授权变更。详细流程与权限边界见 sender-activation-access Skill。收到 status=ok 前不得声称授权已改变。

        Args:
            action(string): grant、renew、revoke 或 list。
            operator_ids(list[string]): 真实数字 QQ ID 数组；list 可传空数组。
            duration_seconds(number): grant/renew 的有限秒数；0 使用配置默认值。
        """

        tool = "manage_sender_activation_access"
        try:
            result = await self.access_service.manage(
                actor=self._actor(event),
                scope=self._scope(event),
                action=action,
                operator_ids=operator_ids,
                duration_seconds=duration_seconds,
            )
            result["tool"] = tool
            return _json(result)
        except DomainError as exc:
            return _tool_error(exc, tool)
        except Exception as exc:
            logger.exception("[sender_activation] access_tool_failed")
            return _tool_error(
                DomainError("internal_error", f"工具执行失败: {type(exc).__name__}"),
                tool,
            )

    @filter.llm_tool(name="manage_sender_activation")
    async def manage_sender_activation(
        self,
        event: AstrMessageEvent,
        action: str = "",
        target_ids: list[str] | None = None,
        duration_seconds: int = 0,
    ) -> str:
        """管理指定 QQ 用户未来普通群消息对当前群聊主 Agent 的有限期额外可达性。明确要求跨越当前话轮、目标可确定且未来消息可能没有 @/引用/唤醒词时才 enable 或 renew；停止用 disable，状态查询用 list。功能讨论、否定、引用、假设、转述和单轮回复不要调用；target_ids 只接受可验证的真实数字 QQ ID。详细规则见 sender-activation Skill。收到 status=ok 前不得声称状态已改变，并按 effect_state 与 recovery_action 处理结果。

        Args:
            action(string): enable、renew、disable 或 list。
            target_ids(list[string]): 真实数字 QQ ID 数组；不接受昵称或占位符；list 可传空数组列出当前会话全部。
            duration_seconds(number): enable/renew 的有限秒数；0 使用 24 小时默认值。
        """

        tool = "manage_sender_activation"
        try:
            scope = self._scope(event)
            actor = self._actor(event)
            authorization_basis = self.access_service.authorize(
                actor=actor,
                scope=scope,
                capability="activation",
                action=action,
            )
            if str(action or "").strip().lower() in {"enable", "renew"}:
                await self._require_new_state_allowed(scope)
            result = await self.service.manage_activation(
                actor=actor,
                scope=scope,
                action=action,
                target_ids=target_ids,
                duration_seconds=duration_seconds,
            )
            result["tool"] = tool
            result["authorization_basis"] = authorization_basis
            return _json(result)
        except DomainError as exc:
            return _tool_error(exc, tool)
        except Exception as exc:
            logger.exception("[sender_activation] activation_tool_failed")
            return _tool_error(
                DomainError("internal_error", f"工具执行失败: {type(exc).__name__}"),
                tool,
            )

    @filter.llm_tool(name="manage_sender_activation_rate")
    async def manage_sender_activation_rate(
        self,
        event: AstrMessageEvent,
        action: str = "",
        target_ids: list[str] | None = None,
        max_activations: int = 0,
        window_seconds: float = 0,
        duration_seconds: int = 0,
    ) -> str:
        """只为已有对象激活租约设置、清除或查询本插件新增唤醒的有限期限频，不影响 AstrBot 原生 @、引用、唤醒词、命令或普通会话。明确需要临时限制额外激活且参数完整时 set，恢复正常额外激活频率时 clear，查询时 list。详细规则见 sender-activation Skill。收到 status=ok 前不得声称限频已改变。

        Args:
            action(string): set、clear 或 list。
            target_ids(list[string]): 当前 UMO 内的 QQ ID 数组。
            max_activations(number): set 时窗口内最多额外激活次数，必须显式提供。
            window_seconds(number): set 时滑动窗口秒数，必须显式提供。
            duration_seconds(number): set 时限频有效秒数，必须显式提供。
        """

        tool = "manage_sender_activation_rate"
        try:
            scope = self._scope(event)
            actor = self._actor(event)
            authorization_basis = self.access_service.authorize(
                actor=actor,
                scope=scope,
                capability="rate",
                action=action,
            )
            if str(action or "").strip().lower() == "set":
                await self._require_new_state_allowed(scope)
            result = await self.service.manage_rate(
                actor=actor,
                scope=scope,
                action=action,
                target_ids=target_ids,
                max_activations=max_activations,
                window_seconds=window_seconds,
                duration_seconds=duration_seconds,
            )
            result["tool"] = tool
            result["authorization_basis"] = authorization_basis
            return _json(result)
        except DomainError as exc:
            return _tool_error(exc, tool)
        except Exception as exc:
            logger.exception("[sender_activation] rate_tool_failed")
            return _tool_error(
                DomainError("internal_error", f"工具执行失败: {type(exc).__name__}"),
                tool,
            )

    @filter.llm_tool(name="manage_heartbeat_lease")
    async def manage_heartbeat_lease(
        self,
        event: AstrMessageEvent,
        action: str = "",
        lease_ids: list[str] | None = None,
        name: str = "",
        cron_expression: str = "",
        instruction: str = "",
        duration_seconds: int = 0,
    ) -> str:
        """管理当前群聊中复用 AstrBot 原生 Cron 的有限期心跳租约。明确要求按时间周期或时点让主 Agent 在未来重审群聊时 create/renew，停止时 disable，查询时 list；按某个发言者消息触发应使用对象激活而不是心跳。心跳只提供判断机会，不保证回复。详细规则见 group-heartbeat Skill。收到 status=ok 前不得声称状态已改变。

        Args:
            action(string): create、renew、disable 或 list。
            lease_ids(list[string]): renew/disable 的原生 Cron 租约 ID；list 可空。
            name(string): 便于人在 Cron 页面识别的短名称。
            cron_expression(string): 五段 Cron 表达式，最小粒度为分钟。
            instruction(string): 每次唤醒时交给原生主 Agent 的语境目标。
            duration_seconds(number): 有限有效期秒数；0 使用配置默认值。
        """

        tool = "manage_heartbeat_lease"
        try:
            scope = self._scope(event)
            actor = self._actor(event)
            authorization_basis = self.access_service.authorize(
                actor=actor,
                scope=scope,
                capability="heartbeat",
                action=action,
            )
            if str(action or "").strip().lower() in {"create", "renew"}:
                await self._require_new_state_allowed(scope)
            result = await self.heartbeat_service.manage(
                actor=actor,
                scope=scope,
                scope_ref=self.service.scope_ref(scope),
                action=action,
                lease_ids=lease_ids,
                name=name,
                cron_expression=cron_expression,
                instruction=instruction,
                duration_seconds=duration_seconds,
            )
            result["tool"] = tool
            result["authorization_basis"] = authorization_basis
            return _json(result)
        except DomainError as exc:
            return _tool_error(exc, tool)
        except Exception as exc:
            logger.exception("[sender_activation] heartbeat_tool_failed")
            return _tool_error(
                DomainError("internal_error", f"工具执行失败: {type(exc).__name__}"),
                tool,
            )

    @filter.llm_tool(name="yield_current_turn")
    async def yield_current_turn(
        self,
        event: AstrMessageEvent,
        reason: str = "",
    ) -> str | None:
        """仅在本插件对象激活或心跳额外唤醒的当前 Agent 回合中，结构化结束本轮且不发送可见回复。当前没有独立公开价值时使用；普通 @、原生会话或其他插件唤醒不可用。成功调用必须作为当前工具选择中的唯一且最后一个调用。详细规则见 sender-activation 或 group-heartbeat Skill。

        Args:
            reason(string): 可选的简短语境理由，不面向群聊显示。
        """

        tool = "yield_current_turn"
        try:
            source = self._plugin_proactive_source(event)
            if source is None:
                raise DomainError(
                    "yield_not_available",
                    "当前回合不是本插件额外激活回合，不能结构化让出。",
                )
            normalized_reason = str(reason or "").strip()
            if len(normalized_reason) > 240:
                raise DomainError("invalid_yield_reason", "reason 不能超过 240 字。")
            event.set_extra(
                TURN_YIELD_EXTRA,
                {
                    "source": source,
                    "accepted_at": time.time(),
                    "reason": normalized_reason,
                },
            )
            event.set_extra("enable_streaming", False)
            self._yield_count += 1
            logger.info(
                "[sender_activation] turn_yield outcome=turn_yield_accepted source=%s",
                source,
            )


            return None
        except DomainError as exc:
            return _tool_error(exc, tool)
        except Exception as exc:
            logger.exception("[sender_activation] yield_tool_failed")
            return _tool_error(
                DomainError("internal_error", f"工具执行失败: {type(exc).__name__}"),
                tool,
            )

    async def _web_state(self):
        try:
            self._require_active_web()
            unknown_query = set(request.query.keys()) - {"scope_ref"}
            if unknown_query:
                raise DomainError(
                    "invalid_query_parameter",
                    "状态接口收到不支持的查询参数。",
                )
            scope_ref = request.query.get("scope_ref")
            scope = await self._resolve_page_scope(scope_ref) if scope_ref else None
            session_status = (
                await self._session_status(scope)
                if scope is not None
                else SessionGateStatus(None, "scope_not_selected")
            )
            health = {
                **self.service.health(),
                **self.access_service.health(),
                **await self.heartbeat_service.health(),
                **self.activation_reservations.health(),
                "turn_yield_count": self._yield_count,
            }
            return json_response(
                {
                    "version": VERSION,
                    "health": health,
                    "host_config": self._host_config_report(scope),
                    "tool_permissions": self._tool_permission_report(),
                    "session_status": session_status.as_dict(),
                    "view_mode": "scope" if scope is not None else "scope_index",
                    "state": await self._page_snapshot(
                        scope=scope,
                    ),
                }
            )
        except DomainError as exc:
            status = 503 if exc.code == "storage_unavailable" else 400
            return error_response(
                exc.message,
                status_code=status,
                data=exc.as_dict(),
            )

    async def _web_activation(self):
        body = await request.json(default={})
        if not isinstance(body, dict):
            return error_response("请求体必须是 JSON 对象。", status_code=400)
        try:
            self._require_active_web()
            scope = await self._resolve_page_scope(body.get("scope_ref"))
            action = str(body.get("action") or "").strip().lower()
            actor = self._dashboard_actor()
            self.access_service.authorize(
                actor=actor,
                scope=scope,
                capability="activation",
                action=action,
            )
            if action in {"enable", "renew"}:
                await self._require_new_state_allowed(scope)
            result = await self.service.manage_activation(
                actor=actor,
                scope=scope,
                action=action,
                target_ids=body.get("target_ids", []),
                duration_seconds=body.get("duration_seconds", 0),
            )
            return json_response(result)
        except DomainError as exc:
            status = 503 if exc.code == "storage_unavailable" else 400
            return error_response(exc.message, status_code=status, data=exc.as_dict())
        except Exception as exc:
            logger.exception("[sender_activation] activation_web_failed")
            return error_response(
                f"内部错误: {type(exc).__name__}",
                status_code=500,
            )

    async def _web_rate(self):
        body = await request.json(default={})
        if not isinstance(body, dict):
            return error_response("请求体必须是 JSON 对象。", status_code=400)
        try:
            self._require_active_web()
            scope = await self._resolve_page_scope(body.get("scope_ref"))
            action = str(body.get("action") or "").strip().lower()
            actor = self._dashboard_actor()
            self.access_service.authorize(
                actor=actor,
                scope=scope,
                capability="rate",
                action=action,
            )
            if action == "set":
                await self._require_new_state_allowed(scope)
            result = await self.service.manage_rate(
                actor=actor,
                scope=scope,
                action=action,
                target_ids=body.get("target_ids", []),
                max_activations=body.get("max_activations", 0),
                window_seconds=body.get("window_seconds", 0),
                duration_seconds=body.get("duration_seconds", 0),
            )
            return json_response(result)
        except DomainError as exc:
            status = 503 if exc.code == "storage_unavailable" else 400
            return error_response(exc.message, status_code=status, data=exc.as_dict())
        except Exception as exc:
            logger.exception("[sender_activation] rate_web_failed")
            return error_response(
                f"内部错误: {type(exc).__name__}",
                status_code=500,
            )

    async def _web_heartbeat(self):
        body = await request.json(default={})
        if not isinstance(body, dict):
            return error_response("请求体必须是 JSON 对象。", status_code=400)
        try:
            self._require_active_web()
            scope = await self._resolve_page_scope(body.get("scope_ref"))
            action = str(body.get("action") or "").strip().lower()
            actor = self._dashboard_actor()
            self.access_service.authorize(
                actor=actor,
                scope=scope,
                capability="heartbeat",
                action=action,
            )
            if action in {"create", "renew"}:
                await self._require_new_state_allowed(scope)
            result = await self.heartbeat_service.manage(
                actor=actor,
                scope=scope,
                scope_ref=self.service.scope_ref(scope),
                action=action,
                lease_ids=body.get("lease_ids", []),
                name=body.get("name", ""),
                cron_expression=body.get("cron_expression", ""),
                instruction=body.get("instruction", ""),
                duration_seconds=body.get("duration_seconds", 0),
            )
            return json_response(result)
        except DomainError as exc:
            status = 503 if exc.code.endswith("unavailable") else 400
            return error_response(exc.message, status_code=status, data=exc.as_dict())
        except Exception as exc:
            logger.exception("[sender_activation] heartbeat_web_failed")
            return error_response(
                f"内部错误: {type(exc).__name__}",
                status_code=500,
            )

    async def _web_access(self):
        body = await request.json(default={})
        if not isinstance(body, dict):
            return error_response("请求体必须是 JSON 对象。", status_code=400)
        try:
            self._require_active_web()
            scope = await self._resolve_page_scope(body.get("scope_ref"))
            result = await self.access_service.manage(
                actor=self._dashboard_actor(),
                scope=scope,
                action=body.get("action", ""),
                operator_ids=body.get("operator_ids", []),
                duration_seconds=body.get("duration_seconds", 0),
            )
            return json_response(result)
        except DomainError as exc:
            status = 503 if exc.code.endswith("unavailable") else 400
            return error_response(exc.message, status_code=status, data=exc.as_dict())
        except Exception as exc:
            logger.exception("[sender_activation] access_web_failed")
            return error_response(
                f"内部错误: {type(exc).__name__}",
                status_code=500,
            )
