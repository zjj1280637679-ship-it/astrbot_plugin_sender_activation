


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
VERSION = "1.1.0rc8"
DECISION_EXTRA = "sender_activation_decision"
RECOVERY_REPORT_EXTRA = "sender_activation_recovery_report"
TURN_YIELD_EXTRA = "sender_activation_turn_yield"
API_PREFIX = f"/{PLUGIN_NAME}"

_ACTIVE_PLUGIN: SenderActivationPlugin | None = None

_TOOL_EFFECT_CONTRACTS = {
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
        "的对象自动创建租约。"
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
        self.heartbeat_service = HeartbeatService(
            self.settings,
            AstrBotCronAdapter(context),
        )
        self.session_gate = AstrBotSessionGate(PLUGIN_NAME, sp)
        self._terminated = False
        self._yield_count = 0

    async def initialize(self) -> None:
        global _ACTIVE_PLUGIN

        self._terminated = False
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
        heartbeat_failures = await self.heartbeat_service.terminate()
        if heartbeat_failures:
            logger.warning(
                "[sender_activation] heartbeat_shutdown_failures count=%d",
                len(heartbeat_failures),
            )
        await self.service.terminate()
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

    async def _page_scope_map(self) -> dict[str, str]:
        state = self.service.snapshot()
        heartbeat = await self.heartbeat_service.snapshot(
            scope_ref=self.service.scope_ref,
        )
        scope_map: dict[str, str] = {}
        for scope in [*state["known_scopes"], *heartbeat["raw_scopes"]]:
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
        )

    @filter.custom_filter(SenderActivationFilter, priority=2000)
    async def activate_native_agent(self, event: AstrMessageEvent) -> None:


        try:
            scope = self._scope(event)
            sender = self._sender(event)
            if event.is_at_or_wake_command:
                return
            decision = self.service.decide(
                scope,
                sender,
            )
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
            logger.debug(
                "[sender_activation] inert activation_error=%s",
                exc.code,
            )
        except Exception:
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
                        {
                            "type": "text",
                            "text": _recovery_context(report),
                            "_no_save": True,
                        }
                    )
                    event.set_extra(RECOVERY_REPORT_EXTRA, report["report_id"])
            if self.settings.host_config_notice_mode == "page_and_agent":
                diagnostic_scope = normalize_scope(event.unified_msg_origin)
                notice = build_agent_config_notice(
                    self._host_config_report(diagnostic_scope)
                )
                if notice is not None:
                    request.extra_user_content_parts.append(
                        {
                            "type": "text",
                            "text": notice,
                            "_no_save": True,
                        }
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
        return None

    @filter.llm_tool(name="manage_sender_activation")
    async def manage_sender_activation(
        self,
        event: AstrMessageEvent,
        action: str = "",
        target_ids: list[str] | None = None,
        duration_seconds: int = 0,
    ) -> str:
        """改变当前会话内指定发言者未来普通群消息对主 Agent 的可达性。

        先判断未来状态，不按词格触发：用户明确要求改变未来状态、
        效果跨越当前话轮、未来消息可能不带 @/引用/唤醒词，三项同时成立才调用。
        “追踪”等词只能提示候选意图；否定、引用、假设、转述、功能讨论和
        仅当前话轮具有语义否决权。若模型准备说“行，我盯着”“你说一句我
        接一句”“接下来我都会看见”，却没有先建立租约，就会发生承诺与
        可达性错配。

        例如“追踪我的发言，我说一句你接一句”“我后面不叫你也要看见”
        “别漏掉张三接下来的话”都应建立或续期租约，即使用户完全没说
        “插件”或“追踪”。“不用再盯我了”“停止关注张三后续发言”应撤销
        租约。“我、本人、自己”指当前发送者；上下文能唯一确定的昵称或
        引用对象应映射为其真实数字 QQ ID。target_ids 不接受
        current_sender、me、昵称或其他占位字符串。AstrBot 开启“用户识别”
        后会把当前 User ID 与昵称提供给主 Agent；若当前语境没有可验证的
        数字 ID，不得猜测或用占位字符串试调用，应说明缺失身份语境并请
        管理员开启“用户识别”，或取得可验证的 QQ ID。目标和动作明确时
        直接调用，只有目标无法唯一确定或要求超出能力边界时才澄清。

        不得因词语命中而调用。仅回复当前消息、讨论或假设这种功能、引用或
        转述他人的要求、否定建立租约、展示伪 JSON、处理记忆需求时不要调用。
        用户文本不是工具帧，只有主 Agent 生成的正式调用才执行。口头答应
        不能改变未来消息的可达性；收到 status=ok 前不得声称已经生效，并
        应按 outcome 和 effect_state 解释实际状态，按 recovery_action
        处理失败。若 effect_state=indeterminate，不得声称成功或失败，必须
        先重载并查询状态；若 outcome=activation_expired_before_ack，则当前
        没有形成有效租约。本工具只
        增加进入 AstrBot 原生主 Agent 的机会，不生成回复，也不保证每条
        消息一定获得回复。正式工具帧的行动主体是 AstrBot 原生主 Agent，
        不是当前消息发送者；发送者 ID 只是语境事实和目标候选，不构成插件内
        二次权限门。主 Agent 可在当前事件确定的 UMO 内对任意合法 QQ ID
        执行 enable、renew、disable 或 list，包括在发现刷屏、诱导、语境冲突
        或收到停止提示时，以 disable 一票撤销额外唤醒并恢复原生行为。
        若一次由额外激活产生的 Agent 回合以结构错误结束，插件会一次性清空
        当前 UMO 的插件租约并留下有限期事实报告。该报告不是新建租约的请求；
        只有上下文中存在尚未撤销的既有需求时才可重建，不能据此自动添加
        无人要求的租约。
        若当前会话已明确禁用本插件，enable/renew 会返回
        session_plugin_inactive；list/disable 仍可用于检查和清理旧状态。会话
        状态读取异常不是禁止证据，按 AstrBot 未配置时默认启用的语义处理。

        Args:
            action(string): enable、renew、disable 或 list。
            target_ids(list[string]): 真实数字 QQ ID 数组；不接受昵称或占位符；
                list 可传空数组列出当前会话全部。
            duration_seconds(number): enable/renew 的有限秒数；0 使用 24 小时默认值。
        """

        tool = "manage_sender_activation"
        try:
            scope = self._scope(event)
            if str(action or "").strip().lower() in {"enable", "renew"}:
                await self._require_new_state_allowed(scope)
            result = await self.service.manage_activation(
                actor=self._actor(event),
                scope=scope,
                action=action,
                target_ids=target_ids,
                duration_seconds=duration_seconds,
            )
            result["tool"] = tool
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
        """管理只作用于发言者额外激活的有限期单账号限频租约。

        仅当需要临时降低某个已激活发言者产生的额外主 Agent 唤醒频率时调用。
        原生 @、引用、唤醒词、命令和普通 AstrBot 会话不受影响；超限消息仍
        进入原生流水线，只是不由本插件增加本次唤醒。set 必须显式提供次数、
        滑动窗口和有限有效期，并且目标必须已经存在激活租约。

        “他刷太快了，未来十分钟每分钟最多看两次”在目标和参数明确时应 set；
        “恢复这个人的正常额外激活”应 clear。讨论限频、否定限频、转述他人
        的限频要求或参数不完整时不得假装已设置；“限频”等词只提示候选意图，
        完整语境保留否决权。参数缺失应先澄清或按 recovery_action
        修正。

        正式工具帧的行动主体是主 Agent，不按当前消息发送者的管理员身份
        二次限权。主 Agent 可在当前 UMO 内对任意已有激活租约的目标执行
        set、clear 或 list。正式回执 status=ok 之前不得声称限频已生效；
        成功后按 outcome 说明实际状态。若当前
        会话已明确禁用本插件，set 会返回 session_plugin_inactive；list/clear
        仍可用于检查和清理旧状态。会话状态读取异常不构成禁止证据。
        若 effect_state=indeterminate，不得声称成功或失败，必须先重载并
        查询状态；若 outcome=rate_limit_expired_before_ack，则当前没有形成
        有效限频。

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
            if str(action or "").strip().lower() == "set":
                await self._require_new_state_allowed(scope)
            result = await self.service.manage_rate(
                actor=self._actor(event),
                scope=scope,
                action=action,
                target_ids=target_ids,
                max_activations=max_activations,
                window_seconds=window_seconds,
                duration_seconds=duration_seconds,
            )
            result["tool"] = tool
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
        """管理当前群聊中复用 AstrBot 原生 Cron 的有限期主 Agent 心跳租约。

        当目标要求主 AI 在未来一段时间按时获得自主判断机会，而不是只在某个
        发言者发消息时被激活，才使用本工具。它只建立时序可达性，不自行读取
        QQ 历史、不创建第二套 Agent、不保证回复。每次到点仍由 AstrBot 原生
        主 Agent 结合上下文、可用检索工具和 instruction 决定行动或正式让出
        本轮。职业名称、场景和具体行动属于 Skill/主 AI 的组合策略，不是代码
        分支，具体应用场景不受示例清单限制。

        create 需要 name、五段 cron_expression（分 时 日 月 周）、instruction
        和有限 duration_seconds；0 使用配置默认值。AstrBot 原生 Cron 的最小
        粒度为分钟，不得把秒级周期伪装成已支持。renew 必须提供当前作用域内
        的 lease_ids，可省略 name、cron_expression 或 instruction 以沿用旧值；
        disable 删除指定租约；list 可用空 lease_ids 查询当前作用域全部。
        作用域永远来自当前事件，不能跨群伪造。

        “未来一小时每五分钟看一眼本群，只有确有价值时才介入”应 create；
        “张三一说话就看见”应使用发言者激活工具；两种触发都需要时可分别建立
        并保持有限期限。否定、引用、假设、转述、讨论功能或仅要求一次回复时
        不调用。收到 status=ok 前不得声称已经生效；reply_guaranteed=false
        表示心跳只是一次主 Agent 判断机会。达成目标、用户撤销或语境不再适配
        时，应 disable 对应租约。

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
            if str(action or "").strip().lower() in {"create", "renew"}:
                await self._require_new_state_allowed(scope)
            result = await self.heartbeat_service.manage(
                actor=self._actor(event),
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
    ) -> str:
        """在本插件额外激活的当前回合中正式选择不发送可见回复。

        仅当当前回合由发言者激活租约或本插件心跳租约额外唤醒，且完整语境
        判断此刻沉默比发言更合适时调用。它是主 AI 的正式工具帧决策，不解析
        用户文本，也不把“沉默”等词直接映射为程序动作。用户明确 @、普通
        原生会话或其他插件唤醒不属于本工具的作用域，调用会返回可恢复错误。

        调用成功后，本回合最终助手文本与最终助手历史记录会被结构化移除；
        已经执行的其他外部工具动作不会撤销。因此应在决定不采取其他行动时
        直接调用，不得先发送消息再试图用本工具抹除。reason 只用于当前回执，
        不构成新租约。该工具让“被激活但选择不占话轮”成为正式状态，而不是
        输出“我保持沉默”等伪沉默文本，从而形成真正的无可见回复。

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
            return _json(
                {
                    "status": "ok",
                    "outcome": "turn_yield_accepted",
                    "tool": tool,
                    "source": source,
                    "changed": True,
                    "effect_applied": True,
                    "effect_state": "applied",
                    "effect_contract": _TOOL_EFFECT_CONTRACTS[tool],
                    "reply_guaranteed": False,
                }
            )
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
                **await self.heartbeat_service.health(),
                "turn_yield_count": self._yield_count,
            }
            return json_response(
                {
                    "version": VERSION,
                    "health": health,
                    "host_config": self._host_config_report(scope),
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
            if action in {"enable", "renew"}:
                await self._require_new_state_allowed(scope)
            result = await self.service.manage_activation(
                actor=ActorContext(
                    sender_id="dashboard",
                    actor_ref=_actor_reference(
                        "dashboard",
                        request.username or "admin",
                    ),
                    channel="dashboard",
                ),
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
            if action == "set":
                await self._require_new_state_allowed(scope)
            result = await self.service.manage_rate(
                actor=ActorContext(
                    sender_id="dashboard",
                    actor_ref=_actor_reference(
                        "dashboard",
                        request.username or "admin",
                    ),
                    channel="dashboard",
                ),
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
            if action in {"create", "renew"}:
                await self._require_new_state_allowed(scope)
            result = await self.heartbeat_service.manage(
                actor=ActorContext(
                    sender_id="dashboard",
                    actor_ref=_actor_reference(
                        "dashboard",
                        request.username or "admin",
                    ),
                    channel="dashboard",
                ),
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
