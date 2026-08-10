from __future__ import annotations

import ast
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
    stage_one_total += len(description)

assert stage_one_total < 1600, stage_one_total

manager = SkillManager(skills_root="/tmp/empty-local-skills", plugins_root=str(plugin_parent))
discovered = {
    skill_name: (plugin_name, skill_dir)
    for skill_name, plugin_name, skill_dir, _preset in manager._iter_plugin_skill_dirs()
    if plugin_name == "astrbot_plugin_sender_activation"
}
expected_skills = {
    "sender-activation",
    "group-heartbeat",
    "sender-activation-access",
}
assert set(discovered) == expected_skills, discovered

for skill_name, (_plugin_name, skill_dir) in discovered.items():
    skill_md = skill_dir / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    desc = _parse_frontmatter_description(text)
    assert desc, f"{skill_name}: frontmatter description not parsed"
    assert skill_name in text.split("---", 2)[1], f"{skill_name}: frontmatter name missing"

assert not (repo / "skill_manifest.yaml").exists()
ast.parse((repo / "main.py").read_text(encoding="utf-8"))

print(f"plugin import: OK")
print(f"five tool schemas: OK; stage-one description chars={stage_one_total}")
print(f"plugin native skills: OK; {sorted(discovered)}")
