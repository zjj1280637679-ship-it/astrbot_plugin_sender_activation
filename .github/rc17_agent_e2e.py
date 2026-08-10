from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import OpenAI

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent
if str(PARENT) not in sys.path:
    sys.path.insert(0, str(PARENT))

import astrbot_plugin_sender_activation.main as plugin_main  # noqa: E402,F401
from astrbot.core.agent.tool import ToolSet  # noqa: E402
from astrbot.core.provider.register import llm_tools  # noqa: E402

MODEL = os.environ.get("ARK_MODEL", "doubao-seed-2-0-pro-260215")
BASE_URL = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
API_KEY = os.environ.get("ARK_API_KEY", "")
if not API_KEY:
    raise SystemExit("ARK_API_KEY is missing")

TOOL_NAMES = [
    "manage_sender_activation_access",
    "manage_sender_activation",
    "manage_sender_activation_rate",
    "manage_heartbeat_lease",
    "yield_current_turn",
]

registered = []
for name in TOOL_NAMES:
    tool = llm_tools.get_func(name)
    if tool is None:
        raise RuntimeError(f"AstrBot did not register tool: {name}")
    registered.append(tool)
RAW_TOOL_SET = ToolSet(registered)

SYSTEM_BASE = """你是 AstrBot 原生主 Agent，当前位于 aiocqhttp 群聊。你能看到真实数字 QQ ID。
只在用户明确要求改变跨话轮的未来插件状态时调用状态工具；功能讨论、引用、假设、转述、否定执行或只处理当前一条消息时不要调用状态工具。
对象消息触发使用 manage_sender_activation；时间/Cron 触发使用 manage_heartbeat_lease；已有对象激活的额外唤醒限频使用 manage_sender_activation_rate；只有 AstrBot 管理员管理本群插件操作员授权时使用 manage_sender_activation_access；仅插件额外唤醒且本轮无公开价值时使用 yield_current_turn。
不要在工具成功回执到达前声称状态已经改变。"""

REQUERY_TEMPLATE = (
    "You have decided to call tool(s): {tool_names}. Now call the tool(s) "
    "with required arguments using the tool schema, and follow the existing tool-use rules."
)


@dataclass
class Case:
    id: str
    user: str
    expected_tool: str | None
    expected_action: str | None = None
    contains_args: dict[str, Any] = field(default_factory=dict)
    system_extra: str = ""


CASES = [
    Case(
        id="activation_enable",
        user="接下来两小时持续关注 QQ 123456789 的后续普通群消息；他以后即使不 @ 你，也要让主 Agent 获得一次判断机会。现在就建立这个状态。",
        expected_tool="manage_sender_activation",
        expected_action="enable",
        contains_args={"target_ids": ["123456789"], "duration_seconds": 7200},
    ),
    Case(
        id="activation_disable",
        user="停止关注 QQ 123456789 的后续普通消息，恢复原生行为。",
        expected_tool="manage_sender_activation",
        expected_action="disable",
        contains_args={"target_ids": ["123456789"]},
        system_extra="当前群已经存在 QQ 123456789 的对象激活租约。",
    ),
    Case(
        id="heartbeat_create",
        user="未来一小时每五分钟重新检查一次本群现场，有值得介入的新情况再说，没有价值就不要占话轮。现在建立这个周期检查。",
        expected_tool="manage_heartbeat_lease",
        expected_action="create",
        contains_args={"duration_seconds": 3600},
    ),
    Case(
        id="rate_set",
        user="QQ 123456789 刷得太快了。接下来十分钟，把他的插件额外激活限制为每 60 秒最多 2 次；原生 @ 和命令不要受影响。",
        expected_tool="manage_sender_activation_rate",
        expected_action="set",
        contains_args={
            "target_ids": ["123456789"],
            "max_activations": 2,
            "window_seconds": 60,
            "duration_seconds": 600,
        },
        system_extra="当前群已经存在 QQ 123456789 的对象激活租约。",
    ),
    Case(
        id="access_grant",
        user="允许 QQ 987654321 在这个群使用本插件 30 天，但不要给他 AstrBot 超级管理员权限。现在授权。",
        expected_tool="manage_sender_activation_access",
        expected_action="grant",
        contains_args={"operator_ids": ["987654321"], "duration_seconds": 2592000},
        system_extra="当前消息发送者是 AstrBot 原生管理员。",
    ),
    Case(
        id="yield_proactive_turn",
        user="当前这条消息没有带来任何新事实，不需要公开回复；继续原有职责即可。",
        expected_tool="yield_current_turn",
        contains_args={},
        system_extra="当前回合由本插件已有对象激活租约额外唤醒；现有租约仍应继续。",
    ),
    Case(
        id="negative_discussion",
        user="解释一下这个插件为什么能做到免 @ 持续关注，讨论原理就行，不要修改任何状态。",
        expected_tool=None,
    ),
    Case(
        id="negative_hypothetical",
        user="假如我以后让你每五分钟检查一次群聊，会发生什么？只是假设讨论，现在不要创建任何任务。",
        expected_tool=None,
    ),
    Case(
        id="negative_current_only",
        user="只回答我这一条消息就行，不要建立后续关注、心跳、限频或授权。你觉得‘持续关注’这个概念有什么优缺点？",
        expected_tool=None,
    ),
    Case(
        id="boundary_sender_not_cron",
        user="以后只要 QQ 246813579 发普通群消息，你就获得一次判断机会；不是按时间巡查，也不要建 Cron。持续 30 分钟。",
        expected_tool="manage_sender_activation",
        expected_action="enable",
        contains_args={"target_ids": ["246813579"], "duration_seconds": 1800},
    ),
]

client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=90.0, max_retries=2)


def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]]):
    last_error = None
    for attempt in range(3):
        try:
            return client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=tools,
                temperature=0,
            )
        except Exception as exc:  # pragma: no cover - CI network path
            last_error = exc
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise last_error  # type: ignore[misc]


def extract_calls(resp) -> list[dict[str, Any]]:
    msg = resp.choices[0].message
    result = []
    for call in msg.tool_calls or []:
        raw_args = call.function.arguments or "{}"
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            args = {"__raw__": raw_args}
        result.append({"name": call.function.name, "args": args})
    return result


def selected_param_schema(names: list[str]) -> list[dict[str, Any]]:
    param_set = RAW_TOOL_SET.get_param_only_tool_set()
    subset = ToolSet()
    for name in names:
        tool = param_set.get_tool(name)
        if tool:
            subset.add_tool(tool)
    return subset.openai_schema()


def base_messages(case: Case) -> list[dict[str, Any]]:
    system = SYSTEM_BASE
    if case.system_extra:
        system += "\n\n[当前已知事实]\n" + case.system_extra
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": case.user},
    ]


def arg_contains(actual: dict[str, Any], key: str, expected: Any) -> bool:
    if key not in actual:
        return False
    value = actual[key]
    if isinstance(expected, list):
        if not isinstance(value, list):
            return False
        return all(str(item) in {str(v) for v in value} for item in expected)
    if isinstance(expected, (int, float)):
        try:
            return abs(float(value) - float(expected)) < 1e-9
        except (TypeError, ValueError):
            return False
    return value == expected


def score_final(case: Case, calls: list[dict[str, Any]]) -> tuple[bool, str]:
    if case.expected_tool is None:
        if calls:
            return False, f"unexpected tool(s): {[c['name'] for c in calls]}"
        return True, "no tool as expected"
    matching = [c for c in calls if c["name"] == case.expected_tool]
    if not matching:
        return False, f"expected {case.expected_tool}, got {[c['name'] for c in calls]}"
    call = matching[0]
    args = call["args"]
    if case.expected_action is not None and args.get("action") != case.expected_action:
        return False, f"action expected {case.expected_action}, got {args.get('action')}"
    for key, expected in case.contains_args.items():
        if not arg_contains(args, key, expected):
            return False, f"arg {key} expected {expected!r}, got {args.get(key)!r}"
    return True, "tool and required args matched"


def run_full(case: Case) -> dict[str, Any]:
    resp = chat(base_messages(case), RAW_TOOL_SET.openai_schema())
    calls = extract_calls(resp)
    passed, note = score_final(case, calls)
    return {
        "mode": "full",
        "case": case.id,
        "route_calls": calls,
        "final_calls": calls,
        "route_pass": passed,
        "final_pass": passed,
        "note": note,
    }


def run_skills_like(case: Case) -> dict[str, Any]:
    messages = base_messages(case)
    stage1 = chat(messages, RAW_TOOL_SET.get_light_tool_set().openai_schema())
    route_calls = extract_calls(stage1)
    route_names = [c["name"] for c in route_calls]

    if case.expected_tool is None:
        route_pass = len(route_names) == 0
    else:
        route_pass = case.expected_tool in route_names

    if not route_names:
        final_calls: list[dict[str, Any]] = []
        final_pass, note = score_final(case, final_calls)
        return {
            "mode": "skills_like",
            "case": case.id,
            "route_calls": route_calls,
            "final_calls": final_calls,
            "route_pass": route_pass,
            "final_pass": final_pass,
            "note": note,
        }

    requery_messages = json.loads(json.dumps(messages, ensure_ascii=False))
    instruction = REQUERY_TEMPLATE.format(tool_names=", ".join(route_names))
    requery_messages[0]["content"] = requery_messages[0]["content"] + "\n" + instruction
    stage2 = chat(requery_messages, selected_param_schema(route_names))
    final_calls = extract_calls(stage2)
    final_pass, note = score_final(case, final_calls)
    return {
        "mode": "skills_like",
        "case": case.id,
        "route_calls": route_calls,
        "final_calls": final_calls,
        "route_pass": route_pass,
        "final_pass": final_pass,
        "note": note,
    }


def description_chars(tool_set: ToolSet) -> int:
    return sum(len(tool.description or "") for tool in tool_set.tools)


def schema_chars(schema: list[dict[str, Any]]) -> int:
    return len(json.dumps(schema, ensure_ascii=False, separators=(",", ":")))


print("=== rc17 real Agent routing E2E ===")
print("model:", MODEL)
print("AstrBot plugin runtime version:", plugin_main.VERSION)
print("tool names:", RAW_TOOL_SET.names())
print(
    "schema footprint chars:",
    json.dumps(
        {
            "full_schema": schema_chars(RAW_TOOL_SET.openai_schema()),
            "skills_like_stage1_schema": schema_chars(
                RAW_TOOL_SET.get_light_tool_set().openai_schema()
            ),
            "full_descriptions": description_chars(RAW_TOOL_SET),
            "stage1_descriptions": description_chars(RAW_TOOL_SET.get_light_tool_set()),
        },
        ensure_ascii=False,
    ),
)

results: list[dict[str, Any]] = []
for case in CASES:
    for runner in (run_full, run_skills_like):
        result = runner(case)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))

summary: dict[str, Any] = {
    "model": MODEL,
    "cases": len(CASES),
    "results": len(results),
    "full": {},
    "skills_like": {},
}
for mode in ("full", "skills_like"):
    rows = [r for r in results if r["mode"] == mode]
    positives = [
        r for r in rows if next(c for c in CASES if c.id == r["case"]).expected_tool
    ]
    negatives = [
        r for r in rows if next(c for c in CASES if c.id == r["case"]).expected_tool is None
    ]
    summary[mode] = {
        "route_pass": sum(bool(r["route_pass"]) for r in rows),
        "final_pass": sum(bool(r["final_pass"]) for r in rows),
        "total": len(rows),
        "positive_final_pass": sum(bool(r["final_pass"]) for r in positives),
        "positive_total": len(positives),
        "negative_final_pass": sum(bool(r["final_pass"]) for r in negatives),
        "negative_total": len(negatives),
    }

print("SUMMARY_JSON=" + json.dumps(summary, ensure_ascii=False, sort_keys=True))

report = {
    "summary": summary,
    "footprint": {
        "full_schema_chars": schema_chars(RAW_TOOL_SET.openai_schema()),
        "skills_like_stage1_schema_chars": schema_chars(
            RAW_TOOL_SET.get_light_tool_set().openai_schema()
        ),
    },
    "results": results,
}
Path("rc17_agent_e2e_report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
    encoding="utf-8",
)

# We deliberately do not fail CI on routing misses: the report is an empirical model benchmark.
# Infrastructure or schema failures still raise above and fail the job.
