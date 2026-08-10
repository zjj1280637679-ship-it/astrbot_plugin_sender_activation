from __future__ import annotations

import ast
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
README = ROOT / "README.md"
UMBRELLA = ROOT / "skills" / "group-duty-orchestration" / "SKILL.md"


def patch_main() -> None:
    text = MAIN.read_text(encoding="utf-8")
    replacements = {
        "sender-activation-access Skill": "group-duty-orchestration Skill",
        "sender-activation Skill": "group-duty-orchestration Skill",
        "group-heartbeat Skill": "group-duty-orchestration Skill",
        "sender-activation 或 group-heartbeat Skill": "group-duty-orchestration Skill",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    ast.parse(text)
    MAIN.write_text(text, encoding="utf-8")


def remove_split_skills() -> None:
    for name in ("sender-activation", "group-heartbeat", "sender-activation-access"):
        path = ROOT / "skills" / name
        if path.exists():
            shutil.rmtree(path)


def patch_umbrella_skill() -> None:
    text = UMBRELLA.read_text(encoding="utf-8")
    old = (
        "description: 当用户希望 AI 在群聊里限时承担持续性职责时，读取本 Skill，把自然语言目标组合为发言者激活、原生 Cron 心跳、结构化沉默与租约终止；职业和应用场景是开放的，不是代码枚举。"
    )
    new = (
        "description: 当用户明确要求 AI 在群聊中建立跨话轮的有限期职责时使用：持续关注指定 QQ 成员后续消息、按 Cron 周期检查群聊、管理本插件操作员授权或限频，并在插件额外唤醒但无公开价值时结构化让出；功能讨论、假设和单次回复不触发。"
    )
    if old not in text:
        raise RuntimeError("umbrella Skill description marker not found")
    UMBRELLA.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_readme() -> None:
    text = README.read_text(encoding="utf-8")
    text = text.replace(
        "- 插件同时在 `skills/` 中提供三个 AstrBot 原生 Skill：对象持续关注、周期心跳、插件操作员授权；",
        "- 插件复用并强化原有 `group-duty-orchestration` 原生 Skill，把对象关注、周期心跳、操作员授权、限频与结构化沉默作为一个按需加载的职责编排手册；",
    )
    text = text.replace(
        "- Skill 初始只暴露名称和触发描述，命中后才加载 `SKILL.md` 的详细流程与边界；",
        "- 该 Skill 初始只暴露名称和触发描述，命中后才加载 `SKILL.md` 的详细流程与边界；",
    )
    text = text.replace(
        "> AstrBot 原生插件 Skills，并针对 `Skills-like（两阶段）` 模式压缩五个工具的",
        "> 原有 AstrBot 原生插件 Skill，并针对 `Skills-like（两阶段）` 模式压缩五个工具的",
    )
    README.write_text(text, encoding="utf-8")


def validate() -> None:
    main = MAIN.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    skill = UMBRELLA.read_text(encoding="utf-8")

    ast.parse(main)
    assert 'VERSION = "1.1.0-rc.17"' in main
    assert "group-duty-orchestration Skill" in main
    for stale in ("sender-activation Skill", "group-heartbeat Skill", "sender-activation-access Skill"):
        assert stale not in main
    assert "group-duty-orchestration` 原生 Skill" in readme
    assert "三个 AstrBot 原生 Skill" not in readme
    assert "指定 QQ 成员" in skill.split("---", 2)[1]
    assert "Cron" in skill.split("---", 2)[1]
    assert "操作员授权" in skill.split("---", 2)[1]
    for name in ("sender-activation", "group-heartbeat", "sender-activation-access"):
        assert not (ROOT / "skills" / name).exists()
    assert not (ROOT / "skill_manifest.yaml").exists()


if __name__ == "__main__":
    patch_main()
    remove_split_skills()
    patch_umbrella_skill()
    patch_readme()
    validate()
    print("rc17 convergence: OK")
