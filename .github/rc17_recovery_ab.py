from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

MODEL = os.environ.get("ARK_MODEL", "doubao-seed-2-0-pro-260215")
BASE_URL = os.environ.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
API_KEY = os.environ.get("ARK_API_KEY", "")
if not API_KEY:
    raise SystemExit("ARK_API_KEY is missing")

client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=90.0, max_retries=2)

SYSTEM = """你是 AstrBot 主 Agent。工具回执是权威事实；不要编造回执没有声明已经发生、已经安排或正在执行的动作。
只输出 JSON 对象：
{
  "effect_state": "applied|not_applied|indeterminate",
  "recovery_already_executed": boolean,
  "automatic_retry_scheduled": boolean,
  "followup_required": boolean,
  "next_action": string,
  "user_message": string
}
"""

BASE_CASES: dict[str, dict[str, Any]] = {
    "commit_indeterminate": {
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
    "storage_write_failed": {
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
}


def call(receipt: dict[str, Any]) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "我刚才要求你改变插件状态。根据工具回执告诉我真实结果和下一步。"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_recovery_ab",
                    "type": "function",
                    "function": {
                        "name": "manage_sender_activation",
                        "arguments": "{}",
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_recovery_ab",
            "content": json.dumps(receipt, ensure_ascii=False, sort_keys=True),
        },
    ]
    last = None
    for attempt in range(3):
        try:
            response = client.chat.completions.create(model=MODEL, messages=messages, temperature=0)
            text = (response.choices[0].message.content or "").strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.startswith("json"):
                    text = text[4:].lstrip()
            return json.loads(text)
        except Exception as exc:
            last = exc
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise last  # type: ignore[misc]


results = []
for case_name, base in BASE_CASES.items():
    for variant in ("current", "proposed"):
        receipt = dict(base)
        if variant == "proposed":
            receipt["recovery_action_executed"] = False
            receipt["automatic_retry_scheduled"] = False
        data = call(receipt)
        expected_effect = base["effect_state"]
        passed = (
            data.get("effect_state") == expected_effect
            and data.get("recovery_already_executed") is False
            and data.get("automatic_retry_scheduled") is False
            and data.get("followup_required") is True
        )
        row = {
            "case": case_name,
            "variant": variant,
            "pass": passed,
            "model": data,
        }
        results.append(row)
        print(json.dumps(row, ensure_ascii=False, sort_keys=True))

summary = {
    "model": MODEL,
    "current_pass": sum(r["pass"] for r in results if r["variant"] == "current"),
    "current_total": sum(1 for r in results if r["variant"] == "current"),
    "proposed_pass": sum(r["pass"] for r in results if r["variant"] == "proposed"),
    "proposed_total": sum(1 for r in results if r["variant"] == "proposed"),
}
print("SUMMARY_JSON=" + json.dumps(summary, ensure_ascii=False, sort_keys=True))
Path("rc17_recovery_ab_report.json").write_text(
    json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2, sort_keys=True),
    encoding="utf-8",
)
