from pathlib import Path

p = Path('README.md')
t = p.read_text(encoding='utf-8')
old = '- `Full（完整参数）` 模式继续兼容，工具名、参数、权限检查和运行效果不变。\n'
new = '- `Full（完整参数）` 模式继续兼容；rc17 已有五个工具的名称和参数保持兼容，rc19 另新增 `manage_attention_ignore` 与 `manage_echo_hook`。\n'
assert old in t
t = t.replace(old, new, 1)
old = '若人格明确配置为“不使用任何 Skills”，五个 Tool 仍可按 AstrBot 的工具模式正常工作'
new = '若人格明确配置为“不使用任何 Skills”，七个 Tool 仍可按 AstrBot 的工具模式正常工作'
assert old in t
t = t.replace(old, new, 1)
p.write_text(t, encoding='utf-8')
