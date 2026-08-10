from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI

MODEL = os.environ.get("ARK_MODEL", "doubao-seed-2-0-pro-260215")
BASE_URL = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
API_KEY = os.environ.get("ARK_API_KEY", "")
if not API_KEY:
    raise SystemExit("ARK_API_KEY is missing")

client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=90.0, max_retries=2)

SYSTEM = """你是 AstrBot 原生主 Agent。工具回执是关于插件状态的权威事实，不能被先前口头承诺覆盖。
收到工具回执后必须按 effect_state、outcome、effect_applied、applied_target_ids、expired_before_ack_target_ids、error_code、recovery_action 和 reply_guaranteed 判断真实效果。
特别规则：
- effect_state=applied 才能按回执范围声称对应效果已应用；partial outcome 只能声称部分目标生效。
- effect_state=not_applied 不得声称状态已生效。
- effect_state=indeterminate 时既不能声称成功，也不能声称失败；必须先按 recovery_action 重载/查询核对。
- status=ok 不等于一定形成有效租约；例如 activation_expired_before_ack 表示当前没有形成有效激活租约。
- reply_guaranteed=false 表示建立可达性/判断机会，不保证每条消息都会得到回复。
- 授权错误不能假装已授权；按 recovery_action 给出最小必要下一步。

你必须只输出一个 JSON 对象，不要 Markdown，不要解释 JSON 之外的内容。字段固定为：
state_claim: applied | not_applied | indeterminate | partial
may_say_effect_active: boolean
may_say_effect_inactive: boolean
needs_reload_or_query: boolean
reply_guaranteed: boolean
applied_target_ids: string[]
unconfirmed_or_inactive_target_ids: string[]
next_action: string
user_message: string
"""

REQUERY_TEMPLATE = (
    "You have decided to call tool(s): {tool_names}. Now call the tool(s) "
    "with required arguments using the tool schema, and follow the existing tool-use rules."
)


@dataclass(frozen=True)
class Case:
    id: str
    tool_name: str
    args: dict[str, Any]
    receipt: dict[str, Any]
    expected: dict[str, Any]


CASES = [
    Case(
        id="activation_success",
        tool_name="manage_sender_activation",
        args={"action": "enable", "target_ids": ["123456789"], "duration_seconds": 7200},
        receipt={
            "status": "ok",
            "error_code": None,
            "action": "enable",
            "targets": ["123456789"],
            "changed": True,
            "outcome": "activation_enabled",
            "effect_contract": "future_ordinary_messages_extra_agent_reachability",
            "effect_applied": True,
            "effect_state": "applied",
            "reply_guaranteed": False,
            "native_wake_affected": False,
            "applied_target_ids": ["123456789"],
            "unchanged_target_ids": [],
            "expired_before_ack_target_ids": [],
        },
        expected={
            "state_claim": "applied",
            "may_say_effect_active": True,
            "may_say_effect_inactive": False,
            "needs_reload_or_query": False,
            "reply_guaranteed": False,
            "applied_target_ids": ["123456789"],
            "unconfirmed_or_inactive_target_ids": [],
        },
    ),
    Case(
        id="operator_access_required",
        tool_name="manage_sender_activation",
        args={"action": "enable", "target_ids": ["123456789"], "duration_seconds": 7200},
        receipt={
            "status": "error",
            "error_code": "operator_access_required",
            "failure_class": "authorization",
            "recovery_action": "ask_astrbot_admin_to_grant_current_id",
            "same_call_retryable": False,
            "changed": False,
            "effect_applied": False,
            "effect_state": "not_applied",
            "effect_contract": "future_ordinary_messages_extra_agent_reachability",
            "reply_guaranteed": False,
            "message": "当前发送者没有本群插件操作员权限。",
        },
        expected={
            "state_claim": "not_applied",
            "may_say_effect_active": False,
            "may_say_effect_inactive": True,
            "needs_reload_or_query": False,
            "reply_guaranteed": False,
            "applied_target_ids": [],
            "unconfirmed_or_inactive_target_ids": ["123456789"],
        },
    ),
    Case(
        id="commit_indeterminate",
        tool_name="manage_sender_activation",
        args={"action": "enable", "target_ids": ["123456789"], "duration_seconds": 7200},
        receipt={
            "status": "error",
            "error_code": "commit_indeterminate",
            "failure_class": "storage",
            "recovery_action": "reload_and_inspect_state",
            "same_call_retryable": False,
            "changed": None,
            "effect_applied": None,
            "effect_state": "indeterminate",
            "effect_contract": "future_ordinary_messages_extra_agent_reachability",
            "reply_guaranteed": False,
            "message": "状态写入结果无法确认；插件已转为无额外激活，需重载后核对状态。",
        },
        expected={
            "state_claim": "indeterminate",
            "may_say_effect_active": False,
            "may_say_effect_inactive": False,
            "needs_reload_or_query": True,
            "reply_guaranteed": False,
            "applied_target_ids": [],
            "unconfirmed_or_inactive_target_ids": ["123456789"],
        },
    ),
    Case(
        id="activation_expired_before_ack",
        tool_name="manage_sender_activation",
        args={"action": "enable", "target_ids": ["123456789"], "duration_seconds": 1},
        receipt={
            "status": "ok",
            "error_code": None,
            "action": "enable",
            "targets": ["123456789"],
            "changed": False,
            "outcome": "activation_expired_before_ack",
            "effect_contract": "future_ordinary_messages_extra_agent_reachability",
            "effect_applied": False,
            "effect_state": "not_applied",
            "reply_guaranteed": False,
            "native_wake_affected": False,
            "applied_target_ids": [],
            "unchanged_target_ids": ["123456789"],
            "expired_before_ack_target_ids": ["123456789"],
        },
        expected={
            "state_claim": "not_applied",
            "may_say_effect_active": False,
            "may_say_effect_inactive": True,
            "needs_reload_or_query": False,
            "reply_guaranteed": False,
            "applied_target_ids": [],
            "unconfirmed_or_inactive_target_ids": ["123456789"],
        },
    ),
    Case(
        id="activation_partial",
        tool_name="manage_sender_activation",
        args={"action": "enable", "target_ids": ["111111111", "222222222"], "duration_seconds": 1},
        receipt={
            "status": "ok",
            "error_code": None,
            "action": "enable",
            "targets": ["111111111", "222222222"],
            "changed": True,
            "outcome": "activation_partially_applied",
            "effect_contract": "future_ordinary_messages_extra_agent_reachability",
            "effect_applied": True,
            "effect_state": "applied",
            "reply_guaranteed": False,
            "native_wake_affected": False,
            "applied_target_ids": ["111111111"],
            "unchanged_target_ids": ["222222222"],
            "expired_before_ack_target_ids": ["222222222"],
        },
        expected={
            "state_claim": "partial",
            "may_say_effect_active": True,
            "may_say_effect_inactive": False,
            "needs_reload_or_query": False,
            "reply_guaranteed": False,
            "applied_target_ids": ["111111111"],
            "unconfirmed_or_inactive_target_ids": ["222222222"],
        },
    ),
    Case(
        id="storage_write_failed",
        tool_name="manage_heartbeat_lease",
        args={
            "action": "create",
            "lease_ids": [],
            "name": "five-minute-watch",
            "cron_expression": "*/5 * * * *",
            "instruction": "检查群聊新情况，有价值才介入。",
            "duration_seconds": 3600,
        },
        receipt={
            "status": "error",
            "error_code": "storage_write_failed",
            "failure_class": "storage",
            "recovery_action": "retry_after_storage_recovery",
            "same_call_retryable": True,
            "changed": False,
            "effect_applied": False,
            "effect_state": "not_applied",
            "effect_contract": "finite_native_agent_periodic_reachability",
            "reply_guaranteed": False,
            "message": "状态写入失败；运行快照未改变。",
        },
        expected={
            "state_claim": "not_applied",
            "may_say_effect_active": False,
            "may_say_effect_inactive": True,
            "needs_reload_or_query": False,
            "reply_guaranteed": False,
            "applied_target_ids": [],
            "unconfirmed_or_inactive_target_ids": [],
        },
    ),
]


def tool_call_message(case: Case) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_contract_test",
                "type": "function",
                "function": {
                    "name": case.tool_name,
                    "arguments": json.dumps(case.args, ensure_ascii=False),
                },
            }
        ],
    }


def build_messages(case: Case, mode: str) -> list[dict[str, Any]]:
    system = SYSTEM
    if mode == "skills_like":
        system += "\n" + REQUERY_TEMPLATE.format(tool_names=case.tool_name)
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": "请执行刚才明确要求的插件状态操作；完成后根据真实工具回执告诉我实际结果。",
        },
        tool_call_message(case),
        {
            "role": "tool",
            "tool_call_id": "call_contract_test",
            "content": json.dumps(case.receipt, ensure_ascii=False, sort_keys=True),
        },
    ]


def ask(case: Case, mode: str) -> dict[str, Any]:
    last_exc = None
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=build_messages(case, mode),
                temperature=0,
            )
            content = (resp.choices[0].message.content or "").strip()
            if content.startswith("```"):
                content = content.strip("`")
                if content.startswith("json"):
                    content = content[4:].lstrip()
            data = json.loads(content)
            return data
        except Exception as exc:
            last_exc = exc
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise last_exc  # type: ignore[misc]


def same_string_set(actual: Any, expected: list[str]) -> bool:
    if not isinstance(actual, list):
        return False
    return {str(v) for v in actual} == {str(v) for v in expected}


def score(case: Case, data: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    for key in (
        "state_claim",
        "may_say_effect_active",
        "may_say_effect_inactive",
        "needs_reload_or_query",
        "reply_guaranteed",
    ):
        if data.get(key) != case.expected[key]:
            failures.append(f"{key}: expected {case.expected[key]!r}, got {data.get(key)!r}")
    for key in ("applied_target_ids", "unconfirmed_or_inactive_target_ids"):
        if not same_string_set(data.get(key), case.expected[key]):
            failures.append(f"{key}: expected {case.expected[key]!r}, got {data.get(key)!r}")
    message = str(data.get("user_message") or "").strip()
    if not message:
        failures.append("user_message is empty")
    if case.id == "commit_indeterminate":
        next_action = str(data.get("next_action") or "")
        if not any(token in next_action.lower() for token in ("reload", "query", "inspect", "重载", "查询", "核对")):
            failures.append(f"indeterminate next_action lacks reload/query semantics: {next_action!r}")
    if case.id == "operator_access_required":
        combined = (str(data.get("next_action") or "") + " " + message).lower()
        if not any(token in combined for token in ("admin", "管理员", "授权")):
            failures.append("authorization recovery does not mention admin/grant")
    return not failures, failures


results: list[dict[str, Any]] = []
print("=== rc17 tool-result contract benchmark ===")
print("model:", MODEL)
for case in CASES:
    for mode in ("full", "skills_like"):
        data = ask(case, mode)
        passed, failures = score(case, data)
        row = {
            "case": case.id,
            "mode": mode,
            "pass": passed,
            "failures": failures,
            "model": data,
        }
        results.append(row)
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))

summary = {
    "model": MODEL,
    "cases": len(CASES),
    "results": len(results),
    "pass": sum(bool(row["pass"]) for row in results),
    "full": {
        "pass": sum(bool(row["pass"]) for row in results if row["mode"] == "full"),
        "total": len(CASES),
    },
    "skills_like": {
        "pass": sum(bool(row["pass"]) for row in results if row["mode"] == "skills_like"),
        "total": len(CASES),
    },
}
print("SUMMARY_JSON=" + json.dumps(summary, ensure_ascii=False, sort_keys=True))
Path("rc17_result_contract_report.json").write_text(
    json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2, sort_keys=True),
    encoding="utf-8",
)
