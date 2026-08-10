from __future__ import annotations

import ast
import shutil
import sys
from pathlib import Path

import docstring_parser

EXPECTED_PARAMS = {
    "manage_sender_activation_access": ["action", "operator_ids", "duration_seconds"],
    "manage_sender_activation": ["action", "target_ids", "duration_seconds"],
    "manage_sender_activation_rate": [
        "action",
        "target_ids",
        "max_activations",
        "window_seconds",
        "duration_seconds",
    ],
    "manage_heartbeat_lease": [
        "action",
        "lease_ids",
        "name",
        "cron_expression",
        "instruction",
        "duration_seconds",
    ],
    "yield_current_turn": ["reason"],
}

repo = Path.cwd()
main_path = repo / "main.py"
readme_path = repo / "README.md"
umbrella_skill = repo / "skills" / "group-duty-orchestration" / "SKILL.md"

# Final architecture: one existing umbrella Skill + five concise first-stage tool descriptions.
main = main_path.read_text(encoding="utf-8")
for old in (
    "sender-activation-access Skill",
    "sender-activation Skill",
    "group-heartbeat Skill",
    "sender-activation 或 group-heartbeat Skill",
):
    main = main.replace(old, "group-duty-orchestration Skill")
main_path.write_text(main, encoding="utf-8")

for extra in (
    repo / "skills" / "sender-activation",
    repo / "skills" / "group-heartbeat",
    repo / "skills" / "sender-activation-access",
):
    if extra.exists():
        shutil.rmtree(extra)

skill_text = umbrella_skill.read_text(encoding="utf-8")
old_desc = "description: 当用户希望 AI 在群聊里限时承担持续性职责时，读取本 Skill，把自然语言目标组合为发言者激活、原生 Cron 心跳、结构化沉默与租约终止；职业和应用场景是开放的，不是代码枚举。"
new_desc = "description: 当用户明确要求 AI 在群聊中建立跨话轮的有限期职责时使用：持续关注指定 QQ 成员后续消息、按 Cron 周期检查群聊、管理本插件操作员授权或限频，并在插件额外唤醒但无公开价值时结构化让出；功能讨论、假设和单次回复不触发。"
if old_desc not in skill_text:
    raise RuntimeError("umbrella skill description marker missing")
skill_text = skill_text.replace(old_desc, new_desc, 1)
umbrella_skill.write_text(skill_text, encoding="utf-8")

readme = readme_path.read_text(encoding="utf-8")
readme = readme.replace(
    "- 插件同时在 `skills/` 中提供三个 AstrBot 原生 Skill：对象持续关注、周期心跳、插件操作员授权；",
    "- 插件复用并强化原有 `group-duty-orchestration` 原生 Skill，把对象关注、周期心跳、操作员授权、限频与结构化沉默作为一个按需加载的职责编排手册；",
)
readme = readme.replace(
    "- Skill 初始只暴露名称和触发描述，命中后才加载 `SKILL.md` 的详细流程与边界；",
    "- 该 Skill 初始只暴露名称和触发描述，命中后才加载 `SKILL.md` 的详细流程与边界；",
)
readme = readme.replace(
    "> AstrBot 原生插件 Skills，并针对 `Skills-like（两阶段）` 模式压缩五个工具的",
    "> 原有 AstrBot 原生插件 Skill，并针对 `Skills-like（两阶段）` 模式压缩五个工具的",
)
readme_path.write_text(readme, encoding="utf-8")

# Put the checkout at the path shape AstrBot's plugin Skill manager expects.
plugin_parent = Path("/tmp/plugin_parent")
plugin_parent.mkdir(parents=True, exist_ok=True)
link = plugin_parent / "astrbot_plugin_sender_activation"
if link.exists() or link.is_symlink():
    link.unlink()
link.symlink_to(repo, target_is_directory=True)
sys.path.insert(0, str(plugin_parent))

import astrbot_plugin_sender_activation.main as plugin_main  # noqa: E402
from astrbot.core.skills.skill_manager import (  # noqa: E402
    SkillManager,
    _parse_frontmatter_description,
)

assert plugin_main.VERSION == "1.1.0-rc.17"

stage_one_total = 0
for tool_name, expected_params in EXPECTED_PARAMS.items():
    fn = getattr(plugin_main.SenderActivationPlugin, tool_name)
    parsed = docstring_parser.parse(fn.__doc__ or "")
    actual_params = [p.arg_name for p in parsed.params]
    assert actual_params == expected_params, (tool_name, actual_params, expected_params)
    description = (parsed.description or "").strip()
    assert description, f"{tool_name}: empty stage-one description"
    assert "Args:" not in description
    assert len(description) < 500, f"{tool_name}: stage-one description too long: {len(description)}"
    assert "group-duty-orchestration Skill" in description, tool_name
    stage_one_total += len(description)

assert stage_one_total < 1600, stage_one_total

manager = SkillManager(skills_root="/tmp/empty-local-skills", plugins_root=str(plugin_parent))
discovered = {
    skill_name: (plugin_name, skill_dir)
    for skill_name, plugin_name, skill_dir, _preset in manager._iter_plugin_skill_dirs()
    if plugin_name == "astrbot_plugin_sender_activation"
}
assert set(discovered) == {"group-duty-orchestration"}, discovered

skill_name, (_plugin_name, skill_dir) = next(iter(discovered.items()))
skill_md = skill_dir / "SKILL.md"
parsed_skill_desc = _parse_frontmatter_description(skill_md.read_text(encoding="utf-8"))
assert parsed_skill_desc
assert "指定 QQ 成员" in parsed_skill_desc
assert "Cron" in parsed_skill_desc
assert "操作员授权" in parsed_skill_desc

assert not (repo / "skill_manifest.yaml").exists()
ast.parse(main_path.read_text(encoding="utf-8"))

print("plugin import: OK")
print(f"five tool schemas: OK; stage-one description chars={stage_one_total}")
print("native skill discovery: OK; group-duty-orchestration")
