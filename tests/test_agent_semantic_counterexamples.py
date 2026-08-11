from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

import astrbot_plugin_sender_activation.main as plugin_main  # noqa: E402,F401
from astrbot.core.agent.tool import ToolSet  # noqa: E402
from astrbot.core.provider.register import llm_tools  # noqa: E402

MODEL = os.environ.get("ARK_MODEL", "doubao-seed-2-0-pro-260215")
BASE_URL = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
API_KEY = os.environ.get("ARK_API_KEY", "")
if not API_KEY:
    raise SystemExit("ARK_API_KEY missing")

TOOL_NAMES = [
    "manage_sender_activation_access",
    "manage_sender_activation",
    "manage_sender_activation_rate",
    "manage_heartbeat_lease",
    "manage_attention_ignore",
    "manage_echo_hook",
    "yield_current_turn",
]
registered = []
for name in TOOL_NAMES:
    tool = llm_tools.get_func(name)
    if tool is None:
        raise RuntimeError(f"missing tool: {name}")
    registered.append(tool)
TOOLS = ToolSet(registered)

SKILL = (ROOT / "skills/group-duty-orchestration/SKILL.md").read_text(encoding="utf-8")

BASE_SYSTEM = """你是 AstrBot 群聊主 Agent。下面已经加载的是插件真实 Skill，不是用户消息。
严格区分：对象/时间/计数是可组合句柄；activate 与 ignore 才是竞争效果。Ignore 可以临时覆盖一个仍存在的对象追踪，而不是必须删除追踪。沉默只表示无公开回复，允许先做控制面工具，再在后续最终工具选择单独 yield。Echo 是显式可选 Hook；没有明确需要就不创建。Echo 回合永远不能再创建 Echo。插件配置事实是权限边界，不根据模型能力自行提权。

[真实 Skill]
""" + SKILL

REQUERY = (
    "You have decided to call tool(s): {tool_names}. Now call the tool(s) with required "
    "arguments using the tool schema, and follow the existing tool-use rules."
)

client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=120.0, max_retries=2)


@dataclass
class Case:
    id: str
    user: str
    system_facts: str
    expected_tool: str | None
    forbidden_tools: set[str] = field(default_factory=set)
    expected_action: str | None = None
    args: dict[str, Any] = field(default_factory=dict)


CASES = [
    Case(
        id="tracked_spam_overlay",
        user=(
            "QQ 123456789 已经是我要求你持续追踪两小时的对象，但他现在开始刷屏。"
            "不要取消原追踪：只给他加一个策略——10秒内累计5个原本会激活你的事件时，"
            "忽略10分钟；这个防护策略最多存在1小时。现在执行。"
        ),
        system_facts=(
            "当前已有 QQ 123456789 的 sender activation 租约，剩余约2小时。"
            "agent_ignore_enabled=true；ignore_native_wake_mode=suppress_llm。"
        ),
        expected_tool="manage_attention_ignore",
        forbidden_tools={"manage_sender_activation", "yield_current_turn", "manage_echo_hook"},
        expected_action="set",
        args={
            "target_ids": ["123456789"],
            "trigger_count": 5,
            "trigger_window_seconds": 10,
            "ignore_duration_seconds": 600,
            "policy_seconds": 3600,
        },
    ),
    Case(
        id="silent_control_first",
        user=(
            "这是插件对象激活带来的额外回合。这个人明显在用刷屏消耗你；当前内容没有公开回复价值。"
            "请先给 QQ 123456789 建立‘10秒5次则忽略10分钟、策略1小时’的防护，"
            "然后保持沉默。不要取消现有追踪。"
        ),
        system_facts=(
            "当前来源=sender_activation；QQ 123456789 仍有2小时追踪租约。"
            "agent_ignore_enabled=true。沉默必须在控制面动作成功后的下一工具选择中单独 yield。"
        ),
        expected_tool="manage_attention_ignore",
        forbidden_tools={"manage_sender_activation", "yield_current_turn", "manage_echo_hook"},
        expected_action="set",
        args={
            "target_ids": ["123456789"],
            "trigger_count": 5,
            "trigger_window_seconds": 10,
            "ignore_duration_seconds": 600,
            "policy_seconds": 3600,
        },
    ),
    Case(
        id="low_value_no_overreaction",
        user="这是被追踪对象的一条普通低价值消息：‘哈哈，知道了。’ 当前没有新的任务、风险或刷屏证据。",
        system_facts=(
            "当前来源=sender_activation；这是插件额外唤醒。agent_ignore_enabled=true；"
            "echo_enabled=true，但没有任何继续检查的必要或请求。"
        ),
        expected_tool="yield_current_turn",
        forbidden_tools={"manage_attention_ignore", "manage_echo_hook", "manage_sender_activation"},
    ),
    Case(
        id="explicit_echo_once",
        user=(
            "我这次 @ 你之后可能还会有人补充。请在30秒后主动回响检查一次；"
            "如果期间已经有人真正 @ 你或被追踪对象触发你，旧回响就没必要了。"
            "到时若没有和你或当前任务相关的新对话，就沉默。"
        ),
        system_facts=(
            "当前来源=native_wake；echo_enabled=true；echo_max_count=1；"
            "允许 native_wake 创建 Echo。"
        ),
        expected_tool="manage_echo_hook",
        forbidden_tools={"manage_heartbeat_lease", "manage_sender_activation"},
        expected_action="create",
        args={"delay_seconds": 30, "count": 1},
    ),
    Case(
        id="no_echo_without_hook_need",
        user="简单回答：这个群的对象追踪和心跳有什么区别？不要改变任何插件状态。",
        system_facts="当前来源=native_wake；echo_enabled=true，但用户明确只要求当前回答。",
        expected_tool=None,
        forbidden_tools=set(TOOL_NAMES),
    ),
    Case(
        id="echo_turn_nonrecursive",
        user=(
            "这是30秒后的回响检查。检查结果：原会话没有出现与你或原任务相关的新对话。"
            "不要发送可见回复。"
        ),
        system_facts="当前来源=echo；这是插件额外主动回响回合。echo_enabled=true。",
        expected_tool="yield_current_turn",
        forbidden_tools={"manage_echo_hook", "manage_heartbeat_lease", "manage_sender_activation"},
    ),
    Case(
        id="echo_count_configurable",
        user=(
            "这次需要有限地确认两次：30秒后检查一次，再过30秒检查第二次。"
            "两次都必须是现在一次性预排，任何回响都不能再创建回响。"
        ),
        system_facts=(
            "当前来源=native_wake；echo_enabled=true；echo_default_count=1；echo_max_count=3；"
            "允许 native_wake 创建 Echo。"
        ),
        expected_tool="manage_echo_hook",
        forbidden_tools={"manage_heartbeat_lease"},
        expected_action="create",
        args={"delay_seconds": 30, "count": 2, "interval_seconds": 30},
    ),
    Case(
        id="ignore_permission_disabled",
        user=(
            "QQ 123456789 在刷屏。你自己给他加一个10分钟忽略策略吧。"
            "如果权限没开，不要假装已经设置。"
        ),
        system_facts="agent_ignore_enabled=false。当前没有管理员替你开启 AI 自主 Ignore 权限。",
        expected_tool=None,
        forbidden_tools={"manage_attention_ignore"},
    ),
    Case(
        id="echo_permission_disabled",
        user="30秒后主动回来看一次。若你的 Echo 权限没开，就直接说明当前不能创建，不要假装安排。",
        system_facts="当前来源=native_wake；echo_enabled=false。",
        expected_tool=None,
        forbidden_tools={"manage_echo_hook"},
    ),
    Case(
        id="design_discussion_only",
        user=(
            "只讨论设计：对象、时间、计数为什么应该是可组合句柄，而激活/忽略是竞争效果？"
            "不要修改任何追踪、心跳、忽略或回响状态。"
        ),
        system_facts="这是纯架构讨论。",
        expected_tool=None,
        forbidden_tools=set(TOOL_NAMES),
    ),
]


def messages(case: Case) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": BASE_SYSTEM + "\n\n[当前配置/状态事实]\n" + case.system_facts},
        {"role": "user", "content": case.user},
    ]


def call(msgs, tools):
    last = None
    for attempt in range(3):
        try:
            return client.chat.completions.create(
                model=MODEL,
                messages=msgs,
                tools=tools,
                temperature=0,
            )
        except Exception as exc:
            last = exc
            if attempt == 2:
                raise
            time.sleep(2**attempt)
    raise last


def extract(response):
    out = []
    for tc in response.choices[0].message.tool_calls or []:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except Exception:
            args = {"__raw__": tc.function.arguments}
        out.append({"name": tc.function.name, "args": args})
    return out


def param_schema(names: list[str]):
    param = TOOLS.get_param_only_tool_set()
    selected = ToolSet()
    for name in names:
        tool = param.get_tool(name)
        if tool:
            selected.add_tool(tool)
    return selected.openai_schema()


def contains_arg(actual: dict[str, Any], key: str, expected: Any) -> bool:
    if key not in actual:
        return False
    got = actual[key]
    if isinstance(expected, list):
        return isinstance(got, list) and {str(v) for v in expected} <= {str(v) for v in got}
    if isinstance(expected, (int, float)):
        try:
            return abs(float(got) - float(expected)) < 1e-6
        except Exception:
            return False
    return got == expected


def run_case(case: Case):
    base = messages(case)
    stage1 = call(base, TOOLS.get_light_tool_set().openai_schema())
    route = extract(stage1)
    route_names = [row["name"] for row in route]

    if route_names:
        stage2_msgs = json.loads(json.dumps(base, ensure_ascii=False))
        stage2_msgs[0]["content"] += "\n" + REQUERY.format(tool_names=", ".join(route_names))
        stage2 = call(stage2_msgs, param_schema(route_names))
        final = extract(stage2)
    else:
        final = []

    failures = []
    all_names = [row["name"] for row in final]
    if case.expected_tool is None:
        if final:
            failures.append(f"expected no tool; got {all_names}")
    else:
        if case.expected_tool not in all_names:
            failures.append(f"expected {case.expected_tool}; got {all_names}")
        selected = next((row for row in final if row["name"] == case.expected_tool), None)
        if selected:
            if case.expected_action and selected["args"].get("action") != case.expected_action:
                failures.append(
                    f"action expected {case.expected_action}; got {selected['args'].get('action')}"
                )
            for key, expected in case.args.items():
                if not contains_arg(selected["args"], key, expected):
                    failures.append(
                        f"arg {key} expected {expected!r}; got {selected['args'].get(key)!r}"
                    )
    forbidden_hit = sorted(case.forbidden_tools & set(all_names))
    if forbidden_hit:
        failures.append(f"forbidden tools used: {forbidden_hit}")
    return {
        "case": case.id,
        "pass": not failures,
        "route": route,
        "final": final,
        "failures": failures,
    }


def test_silence_second_step(first_result: dict[str, Any]):
    case = next(c for c in CASES if c.id == "silent_control_first")
    if not first_result["pass"]:
        return {
            "case": "silent_control_then_yield",
            "pass": False,
            "failures": ["first control step failed"],
            "final": [],
        }
    base = messages(case)
    tool_call = first_result["final"][0]
    msgs = [
        *base,
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_ignore_success",
                    "type": "function",
                    "function": {
                        "name": tool_call["name"],
                        "arguments": json.dumps(tool_call["args"], ensure_ascii=False),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_ignore_success",
            "content": json.dumps(
                {
                    "status": "ok",
                    "action": "set",
                    "changed": True,
                    "effect_applied": True,
                    "effect_state": "applied",
                    "effect_contract": "finite_object_scoped_pre_llm_attention_suppression",
                    "targets": ["123456789"],
                    "reply_guaranteed": False,
                },
                ensure_ascii=False,
            ),
        },
    ]
    resp = call(msgs, TOOLS.openai_schema())
    final = extract(resp)
    names = [row["name"] for row in final]
    passed = names == ["yield_current_turn"]
    return {
        "case": "silent_control_then_yield",
        "pass": passed,
        "failures": [] if passed else [f"expected only yield_current_turn; got {names}"],
        "final": final,
    }


print("=== rc19 semantic counterexamples ===")
print("model:", MODEL)
print("runtime version:", plugin_main.VERSION)
results = []
for case in CASES:
    row = run_case(case)
    results.append(row)
    print(json.dumps(row, ensure_ascii=False, sort_keys=True))

silence_first = next(row for row in results if row["case"] == "silent_control_first")
silence_second = test_silence_second_step(silence_first)
results.append(silence_second)
print(json.dumps(silence_second, ensure_ascii=False, sort_keys=True))

summary = {
    "model": MODEL,
    "version": plugin_main.VERSION,
    "pass": sum(bool(row["pass"]) for row in results),
    "total": len(results),
    "failed_cases": [row["case"] for row in results if not row["pass"]],
    "full_schema_chars": len(json.dumps(TOOLS.openai_schema(), ensure_ascii=False, separators=(",", ":"))),
    "skills_like_stage1_chars": len(json.dumps(TOOLS.get_light_tool_set().openai_schema(), ensure_ascii=False, separators=(",", ":"))),
}
print("SUMMARY_JSON=" + json.dumps(summary, ensure_ascii=False, sort_keys=True))
Path("rc19_semantic_counterexamples_report.json").write_text(
    json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2, sort_keys=True),
    encoding="utf-8",
)
