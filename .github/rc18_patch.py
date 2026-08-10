from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
METADATA = ROOT / "metadata.yaml"
README = ROOT / "README.md"
SKILL = ROOT / "skills/group-duty-orchestration/SKILL.md"

# 1) Runtime version + backward-compatible error receipt facts.
main = MAIN.read_text(encoding="utf-8")
old_version = 'VERSION = "1.1.0-rc.17"'
new_version = 'VERSION = "1.1.0-rc.18"'
assert main.count(old_version) == 1, main.count(old_version)
main = main.replace(old_version, new_version, 1)

old_receipt = '''            "recovery_action": recovery_action,
            "same_call_retryable": same_call_retryable,'''
new_receipt = '''            "recovery_action": recovery_action,
            "recovery_action_executed": False,
            "automatic_retry_scheduled": False,
            "same_call_retryable": same_call_retryable,'''
assert main.count(old_receipt) == 1, main.count(old_receipt)
main = main.replace(old_receipt, new_receipt, 1)
ast.parse(main)
MAIN.write_text(main, encoding="utf-8")

# 2) Metadata version only; compatibility floor remains open-ended.
metadata = METADATA.read_text(encoding="utf-8")
assert metadata.count('version: "1.1.0-rc.17"') == 1
metadata = metadata.replace('version: "1.1.0-rc.17"', 'version: "1.1.0-rc.18"', 1)
assert 'astrbot_version: ">=4.26.1"' in metadata
METADATA.write_text(metadata, encoding="utf-8")

# 3) Skill body: recovery_action is advice, not an already-executed action.
skill = SKILL.read_text(encoding="utf-8")
anchor = '- 收到 `status=ok` 前，不得口头声称已经上岗或已经生效。\n'
addition = (
    '- 错误回执中的 `recovery_action` 只是下一步建议，不表示恢复动作已经执行。'
    '当 `recovery_action_executed=false` 时不得说“正在重载/已经修复”；当 '
    '`automatic_retry_scheduled=false` 时不得说“系统会自动重试”。只有后续真的'
    '执行了相应动作并取得新事实后，才能报告它已经发生。\n'
)
assert anchor in skill
if addition not in skill:
    skill = skill.replace(anchor, anchor + addition, 1)
SKILL.write_text(skill, encoding="utf-8")

# 4) README: keep rc17 two-stage history, document rc18 as a narrow reliability iteration.
readme = README.read_text(encoding="utf-8")
old_note = '''> **候选版说明：** `1.1.0-rc.17` 在解除 AstrBot 版本上限的基础上，加入
> 原有 AstrBot 原生插件 Skill，并针对 `Skills-like（两阶段）` 模式压缩五个工具的
> 第一阶段描述。租约、权限、状态存储、Web API 与工具参数接口保持不变。'''
new_note = '''> **候选版说明：** `1.1.0-rc.18` 基于已通过真实模型两阶段验收的 `rc.17`，
> 只补充错误回执的恢复事实：`recovery_action_executed=false` 与
> `automatic_retry_scheduled=false`，避免主 Agent 把“建议下一步恢复”误报成
> “已经在恢复/系统会自动重试”。租约、权限、状态存储、Web API、工具名称与
> 参数接口均保持不变。'''
assert old_note in readme
readme = readme.replace(old_note, new_note, 1)
README.write_text(readme, encoding="utf-8")

# Final invariants.
assert 'VERSION = "1.1.0-rc.18"' in MAIN.read_text(encoding="utf-8")
assert 'version: "1.1.0-rc.18"' in METADATA.read_text(encoding="utf-8")
assert 'astrbot_version: ">=4.26.1"' in METADATA.read_text(encoding="utf-8")
assert 'automatic_retry_scheduled' in MAIN.read_text(encoding="utf-8")
print("rc18 patch invariants: OK")
