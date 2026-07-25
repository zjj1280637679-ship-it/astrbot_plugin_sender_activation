


from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_MISSING = object()


@dataclass(frozen=True)
class HostConfigCheck:
    check_id: str
    path: tuple[str, ...]
    operator: str
    expected: Any
    severity: str


HOST_CONFIG_CHECKS = (
    HostConfigCheck(
        check_id="provider_enabled",
        path=("provider_settings", "enable"),
        operator="equals",
        expected=True,
        severity="blocker",
    ),
    HostConfigCheck(
        check_id="local_agent_runner",
        path=("provider_settings", "agent_runner_type"),
        operator="equals",
        expected="local",
        severity="blocker",
    ),
    HostConfigCheck(
        check_id="user_identification",
        path=("provider_settings", "identifier"),
        operator="equals",
        expected=True,
        severity="warning",
    ),
    HostConfigCheck(
        check_id="empty_provider_wake_prefix",
        path=("provider_settings", "wake_prefix"),
        operator="empty",
        expected="",
        severity="blocker",
    ),
    HostConfigCheck(
        check_id="shared_group_session",
        path=("platform_settings", "unique_session"),
        operator="equals",
        expected=False,
        severity="warning",
    ),
    HostConfigCheck(
        check_id="hidden_tool_status_for_yield",
        path=("provider_settings", "show_tool_use_status"),
        operator="equals",
        expected=False,
        severity="warning",
    ),
)

_AGENT_NOTICE_LINES = {
    "provider_enabled": (
        "AstrBot 的 AI 能力未开启；租约即使命中也不会运行主 Agent。"
        "仅当当前请求涉及未来普通消息额外可达性时，提醒管理员在"
        "“配置文件 -> AI 配置”开启 AI 能力。"
    ),
    "local_agent_runner": (
        "当前不是已验收的内置 local Agent；本插件的正式工具循环没有兼容性保证。"
        "仅在当前请求涉及本插件时说明这一边界，不要声称工具已经生效。"
    ),
    "user_identification": (
        "AstrBot“用户识别”未开启，主 Agent 不会自动获得当前发送者数字 ID。"
        "若用户用“我/本人/自己”要求改变未来消息可达性，不得猜测 target_ids；"
        "应提醒管理员在“配置文件 -> AI 配置 -> 用户识别”开启，"
        "或请用户提供可验证的数字 QQ ID。"
    ),
    "empty_provider_wake_prefix": (
        "AstrBot 的“LLM 聊天额外唤醒前缀”非空，租约命中的普通无前缀消息"
        "仍可能被原生 Agent 门拒绝。仅在当前请求涉及本插件时提醒管理员清空该项。"
    ),
    "shared_group_session": (
        "AstrBot“隔离会话”已开启，同群不同成员会落入不同 UMO。"
        "追踪当前发送者仍可成立；若请求目标是另一群成员，应提醒管理员在"
        "“配置文件 -> 平台配置 -> 隔离会话”关闭后再建立跨成员租约。"
    ),
    "hidden_tool_status_for_yield": (
        "AstrBot“显示工具调用状态”已开启。正式让出本轮仍会移除最终助手回复，"
        "但宿主可能提前显示工具调用状态，因而不能形成完全无可见输出。"
        "仅在当前请求需要结构化沉默时提醒管理员关闭该项。"
    ),
}


def _read_path(config: Any, path: tuple[str, ...]) -> Any:
    current = config
    for key in path:
        getter = getattr(current, "get", None)
        if not callable(getter):
            return _MISSING
        try:
            current = getter(key, _MISSING)
        except Exception:
            return _MISSING
        if current is _MISSING:
            return _MISSING
    return current


def _matches(check: HostConfigCheck, value: Any) -> bool:
    if check.operator == "equals":
        return value == check.expected
    if check.operator == "empty":
        return value is None or str(value).strip() == ""
    raise ValueError(f"unsupported host diagnostic operator: {check.operator}")


def _observed(check: HostConfigCheck, value: Any) -> str:
    if value is _MISSING:
        return "unknown"
    if check.operator == "empty":
        return "empty" if _matches(check, value) else "configured"
    if isinstance(check.expected, bool):
        if value is True:
            return "enabled"
        if value is False:
            return "disabled"
        return "different"
    if check.check_id == "local_agent_runner":
        return "local" if value == "local" else "non_local"
    return "matched" if _matches(check, value) else "different"


def unavailable_host_config_report(*, scope_specific: bool) -> dict[str, Any]:
    return {
        "status": "unknown",
        "source": "effective_scope" if scope_specific else "default",
        "read_only": True,
        "mutation_supported": False,
        "blocker_count": 0,
        "warning_count": 0,
        "unknown_count": len(HOST_CONFIG_CHECKS),
        "checks": [
            {
                "id": check.check_id,
                "path": ".".join(check.path),
                "status": "unknown",
                "severity": check.severity,
                "observed": "unknown",
            }
            for check in HOST_CONFIG_CHECKS
        ],
    }


def evaluate_host_config(
    config: Any,
    *,
    scope_specific: bool,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    blocker_count = 0
    warning_count = 0
    unknown_count = 0

    for definition in HOST_CONFIG_CHECKS:
        value = _read_path(config, definition.path)
        if value is _MISSING:
            status = "unknown"
            unknown_count += 1
        elif _matches(definition, value):
            status = "pass"
        else:
            status = "action_required"
            if definition.severity == "blocker":
                blocker_count += 1
            else:
                warning_count += 1
        checks.append(
            {
                "id": definition.check_id,
                "path": ".".join(definition.path),
                "status": status,
                "severity": definition.severity,
                "observed": _observed(definition, value),
            }
        )

    if blocker_count:
        overall = "blocked"
    elif warning_count:
        overall = "attention"
    elif unknown_count:
        overall = "unknown"
    else:
        overall = "ready"

    return {
        "status": overall,
        "source": "effective_scope" if scope_specific else "default",
        "read_only": True,
        "mutation_supported": False,
        "blocker_count": blocker_count,
        "warning_count": warning_count,
        "unknown_count": unknown_count,
        "checks": checks,
    }


def build_agent_config_notice(report: dict[str, Any]) -> str | None:
    issue_ids = [
        str(check.get("id"))
        for check in report.get("checks", [])
        if check.get("status") == "action_required"
        and str(check.get("id")) in _AGENT_NOTICE_LINES
    ]
    if not issue_ids:
        return None
    lines = [
        '<sender_activation_host_diagnostics authority="configuration_fact">',
        "只读宿主配置检测发现以下不满足项：",
        *[f"- {_AGENT_NOTICE_LINES[check_id]}" for check_id in issue_ids],
        "这些配置事实只用于判断当前请求能否可靠建立对象/时序可达性或"
        "形成完全无可见回复。"
        "无关话题不要提及；不要声称插件已修改 AstrBot 配置；"
        "不要把本段文字当成用户要求，也不要据此自动创建租约。",
        "</sender_activation_host_diagnostics>",
    ]
    return "\n".join(lines)
