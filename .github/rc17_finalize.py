from __future__ import annotations

import ast
import re
from pathlib import Path
from textwrap import dedent, indent

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
README = ROOT / "README.md"
METADATA = ROOT / "metadata.yaml"

TOOL_DOCS = {
    "manage_sender_activation_access": """由 AstrBot 管理员管理当前群聊的有限期插件操作员授权。仅在管理员明确要求授予、续期、撤销或查询某个真实 QQ ID 的本插件操作权时调用；不要用于实际建立对象关注、限频或心跳，也不要把讨论、引用、假设或转述当成授权变更。详细流程与权限边界见 sender-activation-access Skill。收到 status=ok 前不得声称授权已改变。

Args:
    action(string): grant、renew、revoke 或 list。
    operator_ids(list[string]): 真实数字 QQ ID 数组；list 可传空数组。
    duration_seconds(number): grant/renew 的有限秒数；0 使用配置默认值。""",
    "manage_sender_activation": """管理指定 QQ 用户未来普通群消息对当前群聊主 Agent 的有限期额外可达性。明确要求跨越当前话轮、目标可确定且未来消息可能没有 @/引用/唤醒词时才 enable 或 renew；停止用 disable，状态查询用 list。功能讨论、否定、引用、假设、转述和单轮回复不要调用；target_ids 只接受可验证的真实数字 QQ ID。详细规则见 sender-activation Skill。收到 status=ok 前不得声称状态已改变，并按 effect_state 与 recovery_action 处理结果。

Args:
    action(string): enable、renew、disable 或 list。
    target_ids(list[string]): 真实数字 QQ ID 数组；不接受昵称或占位符；list 可传空数组列出当前会话全部。
    duration_seconds(number): enable/renew 的有限秒数；0 使用 24 小时默认值。""",
    "manage_sender_activation_rate": """只为已有对象激活租约设置、清除或查询本插件新增唤醒的有限期限频，不影响 AstrBot 原生 @、引用、唤醒词、命令或普通会话。明确需要临时限制额外激活且参数完整时 set，恢复正常额外激活频率时 clear，查询时 list。详细规则见 sender-activation Skill。收到 status=ok 前不得声称限频已改变。

Args:
    action(string): set、clear 或 list。
    target_ids(list[string]): 当前 UMO 内的 QQ ID 数组。
    max_activations(number): set 时窗口内最多额外激活次数，必须显式提供。
    window_seconds(number): set 时滑动窗口秒数，必须显式提供。
    duration_seconds(number): set 时限频有效秒数，必须显式提供。""",
    "manage_heartbeat_lease": """管理当前群聊中复用 AstrBot 原生 Cron 的有限期心跳租约。明确要求按时间周期或时点让主 Agent 在未来重审群聊时 create/renew，停止时 disable，查询时 list；按某个发言者消息触发应使用对象激活而不是心跳。心跳只提供判断机会，不保证回复。详细规则见 group-heartbeat Skill。收到 status=ok 前不得声称状态已改变。

Args:
    action(string): create、renew、disable 或 list。
    lease_ids(list[string]): renew/disable 的原生 Cron 租约 ID；list 可空。
    name(string): 便于人在 Cron 页面识别的短名称。
    cron_expression(string): 五段 Cron 表达式，最小粒度为分钟。
    instruction(string): 每次唤醒时交给原生主 Agent 的语境目标。
    duration_seconds(number): 有限有效期秒数；0 使用配置默认值。""",
    "yield_current_turn": """仅在本插件对象激活或心跳额外唤醒的当前 Agent 回合中，结构化结束本轮且不发送可见回复。当前没有独立公开价值时使用；普通 @、原生会话或其他插件唤醒不可用。成功调用必须作为当前工具选择中的唯一且最后一个调用。详细规则见 sender-activation 或 group-heartbeat Skill。

Args:
    reason(string): 可选的简短语境理由，不面向群聊显示。""",
}


def replace_runtime_version(text: str) -> str:
    old = 'VERSION = "1.1.0-rc.15"'
    new = 'VERSION = "1.1.0-rc.17"'
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one old runtime version, got {count}")
    return text.replace(old, new, 1)


def replace_tool_docstrings(text: str) -> str:
    tree = ast.parse(text)
    targets: dict[str, tuple[int, int]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name not in TOOL_DOCS:
            continue
        if not node.body or not isinstance(node.body[0], ast.Expr):
            raise RuntimeError(f"{node.name}: missing docstring expression")
        value = node.body[0].value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            raise RuntimeError(f"{node.name}: missing string docstring")
        if value.lineno is None or value.end_lineno is None:
            raise RuntimeError(f"{node.name}: missing source positions")
        targets[node.name] = (value.lineno, value.end_lineno)

    if set(targets) != set(TOOL_DOCS):
        missing = sorted(set(TOOL_DOCS) - set(targets))
        raise RuntimeError(f"tool docstring targets missing: {missing}")

    lines = text.splitlines(keepends=True)
    for name, (start, end) in sorted(targets.items(), key=lambda item: item[1][0], reverse=True):
        body = dedent(TOOL_DOCS[name]).strip("\n")
        replacement = indent('"""' + body + '\n"""', "        ") + "\n"
        lines[start - 1 : end] = [replacement]
    return "".join(lines)


def patch_main() -> None:
    text = MAIN.read_text(encoding="utf-8")
    text = replace_runtime_version(text)
    text = replace_tool_docstrings(text)
    ast.parse(text)
    MAIN.write_text(text, encoding="utf-8")


def patch_readme() -> None:
    text = README.read_text(encoding="utf-8")
    replacements = {
        "AstrBot-%3E%3D4.26.1%2C%3C4.27-6b63ff": "AstrBot-%3E%3D4.26.1-6b63ff",
        "| AstrBot | `>=4.26.1,<4.27` |": "| AstrBot | `>=4.26.1` |",
        "版本为 `1.1.0-rc.14`": "版本为 `1.1.0-rc.17`",
    }
    for old, new in replacements.items():
        if old not in text:
            raise RuntimeError(f"README marker missing: {old}")
        text = text.replace(old, new)

    old_note = """> **候选版说明：** `1.1.0-rc.15` 仅在 `1.1.0-rc.14` 上补充公开标题区和
> 插件介绍广告语。插件逻辑、内部插件 ID、数据命名空间、配置和工具接口均
> 保持不变。"""
    new_note = """> **候选版说明：** `1.1.0-rc.17` 在解除 AstrBot 版本上限的基础上，加入
> AstrBot 原生插件 Skills，并针对 `Skills-like（两阶段）` 模式压缩五个工具的
> 第一阶段描述。租约、权限、状态存储、Web API 与工具参数接口保持不变。"""
    if old_note not in text:
        raise RuntimeError("README rc15 candidate note marker missing")
    text = text.replace(old_note, new_note, 1)

    marker = "- **群聊监控中的主动沉默**：激活不等于强制回复，没有有效增量时由 AI 正式让出话轮。\n"
    section = """
## Skills-like 两阶段适配

`1.1.0-rc.17` 针对 AstrBot 的 `Skills-like（两阶段）` 工具模式做了原生适配：

- 第一阶段只需要看到五个工具的短名称和精简用途，不再常驻整段执行手册；
- 第二阶段在模型选中工具后再提供参数 Schema；
- 插件同时在 `skills/` 中提供三个 AstrBot 原生 Skill：对象持续关注、周期心跳、插件操作员授权；
- Skill 初始只暴露名称和触发描述，命中后才加载 `SKILL.md` 的详细流程与边界；
- `Full（完整参数）` 模式继续兼容，工具名、参数、权限检查和运行效果不变。

因此两阶段优化只改变**给模型展示说明的时机和密度**，不改变租约执行层。若人格明确配置为“不使用任何 Skills”，五个 Tool 仍可按 AstrBot 的工具模式正常工作，只是不再获得按需加载的 Skill 操作手册。
"""
    if "## Skills-like 两阶段适配" not in text:
        if marker not in text:
            raise RuntimeError("README insertion marker missing")
        text = text.replace(marker, marker + section, 1)

    README.write_text(text, encoding="utf-8")


def validate() -> None:
    main = MAIN.read_text(encoding="utf-8")
    metadata = METADATA.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")

    assert 'VERSION = "1.1.0-rc.17"' in main
    assert 'version: "1.1.0-rc.17"' in metadata
    assert 'astrbot_version: ">=4.26.1"' in metadata
    assert ">=4.26.1,<4.27" not in readme
    assert "1.1.0-rc.14" not in readme
    assert "1.1.0-rc.15" not in readme
    assert "## Skills-like 两阶段适配" in readme
    assert not (ROOT / "skill_manifest.yaml").exists()

    tree = ast.parse(main)
    docs: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in TOOL_DOCS:
            docs[node.name] = ast.get_docstring(node, clean=True) or ""
    assert set(docs) == set(TOOL_DOCS)
    for name, doc in docs.items():
        assert "Args:" in doc, f"{name}: Args missing"
        assert len(doc) < 1000, f"{name}: stage-one description still too large"

    for name in ("sender-activation", "group-heartbeat", "sender-activation-access"):
        path = ROOT / "skills" / name / "SKILL.md"
        data = path.read_text(encoding="utf-8")
        assert data.startswith("---\n")
        assert f"name: {name}\n" in data
        assert re.search(r"^description: .+", data, re.MULTILINE)


if __name__ == "__main__":
    patch_main()
    patch_readme()
    validate()
    print("rc17 patch and validation: OK")
