from pathlib import Path

path = Path("skills/group-duty-orchestration/SKILL.md")
text = path.read_text(encoding="utf-8")

old = "description: 当用户明确要求 AI 在群聊中建立跨话轮的有限期职责时使用：持续关注指定 QQ 成员后续消息、按 Cron 周期检查群聊、管理本插件操作员授权或限频，并在插件额外唤醒但无公开价值时结构化让出；功能讨论、假设和单次回复不触发。"
new = "description: 当用户明确要求 AI 在群聊中建立跨话轮的有限期职责或主 Agent 需要治理自身注意力时使用：持续关注指定 QQ 成员、按 Cron 周期检查、有限期对象忽略、可选有限回响、操作员授权或限频；插件额外唤醒但无公开价值时可结构化让出。功能讨论、假设和单次回复不触发状态变更。"
assert old in text
text = text.replace(old, new, 1)

anchor = "4. `yield_current_turn`：在本插件额外激活的回合里，正式选择不占用可见话轮。\n"
addition = (
    "5. `manage_attention_ignore`：给主 Agent 一个由部署者配置授权的有限期对象忽略控制面。"
    "对象 ID、计数阈值和时间窗口可以叠加；它只统计原本可能激活主 Agent 的事件，"
    "命中后在 LLM 前尽量抑制后续成本。\n"
    "6. `manage_echo_hook`：显式挂载有限回响。回响等于有限主动自激活权限；没有调用就没有回响。"
    "一次调用可以按配置预排有限次数，但所有次数都来自原激活回合，回响回合绝不能再创建回响。\n"
)
assert anchor in text
text = text.replace(anchor, anchor + addition, 1)

anchor = "- 需要降频：仅对已有对象激活租约添加从属限频；未明确需要时不默认限频。\n"
addition = (
    "- 已知某对象会造成无价值或对抗性激活成本：若部署者授予 Ignore 权限，可建立有限对象忽略。"
    "忽略与对象激活不是互相删除的状态；同一对象可以同时存在激活租约和更短的忽略策略，"
    "命中忽略时由忽略效果临时覆盖该次激活，忽略到期后原有限激活租约仍按自身期限继续。\n"
    "- 需要在一次真实激活后保留短暂的主动再检查机会：只有确有必要时显式创建 Echo。"
    "Echo 不是每轮默认步骤，也不是长期追踪；没有钩子就不发生。\n"
)
assert anchor in text
text = text.replace(anchor, anchor + addition, 1)

anchor = "### 3. 用正式工具帧建立状态\n"
addition = '''### 2.1 区分三个句柄与两个竞争效果\n\n对象、时间、计数是可组合的条件句柄，不是三个互斥模式。当前 rc19 首先把这种组合用于对象忽略：\n\n- `对象 + 忽略`：`trigger_count=1`，忽略持续时间与策略期限按需要设置。\n- `对象 + 计数 + 忽略`：对象累计到 N 个可激活事件后再忽略。\n- `对象 + 时间窗口 + 计数 + 忽略`：只统计滑动时间窗口中的可激活事件。\n\n激活与忽略才是竞争效果。已有 `对象 + 有限时间 + 激活` 的追踪对象，在刷屏时可以叠加更短的 `对象 + 时间/计数 + 忽略`，而不是销毁原追踪意图。当前版本不把尚未实现的全局时间忽略、通用计数激活或全量监听伪装成已有能力。\n\n'''
assert anchor in text
text = text.replace(anchor, addition + anchor, 1)

anchor = "- 收到 `status=ok` 前，不得口头声称已经上岗或已经生效。\n"
addition = (
    "- 主动激活权限与主动忽略权限不对称：对象激活、心跳等扩大可达性的权限仍按现有管理员/操作员边界；"
    "对象忽略与 Echo 是否开放由插件配置决定，程序不根据模型名称自动提权。\n"
    "- `ignore_native_wake_mode` 由部署者选择：`extra_only` 只压本插件额外激活；`suppress_llm`"
    "阻断 AstrBot 默认 LLM 但保留其他插件；`stop_event` 最省成本但会停止整个事件传播。不要把三者效果说成一样。\n"
)
assert anchor in text
text = text.replace(anchor, addition + anchor, 1)

anchor = "有价值时正常发言或调用其他正式工具。没有价值时把\n`yield_current_turn` 作为该次工具选择中唯一且最后的调用。成功调用会使用\n"
replacement = (
    "有价值时正常发言或调用其他正式工具。没有公开价值不等于控制面什么都不能做："
    "例如一次对抗性刷屏已经进入 Agent 后，可以先在一个工具选择中调用 `manage_attention_ignore`"
    "建立未来的有限前置忽略；如果最终仍不应公开回复，再在后续最后一次工具选择中把\n"
    "`yield_current_turn` 作为唯一调用。成功调用会使用\n"
)
assert anchor in text
text = text.replace(anchor, replacement, 1)

anchor = "### 5. 分离控制面与对话面\n"
addition = '''### 4.1 Echo 是可选钩子，不是默认续命\n\nEcho 只在主 Agent 明确调用 `manage_echo_hook(action=create)` 时存在。默认次数目前可配置为 1，但不是代码常量；`echo_max_count` 允许部署者决定一次原始激活最多预排多少个回响。若 count > 1，各次回响在原始激活回合一次性排入 AstrBot 原生 `run_once` 主动任务，不靠前一次回响创建后一次，因此仍然不是递归。\n\n真实原生 @ 或被追踪对象的有效激活到来时，可以按配置取消当前作用域尚未执行的 Echo；如果一个恶意 @ 已被 Ignore 真正抑制，则它不应通过“取消 Echo”反过来形成拒绝服务。Echo 触发后应重新读取原会话语境；没有与自身或原目标相关的新对话时使用 `yield_current_turn`，而不是为了证明自己被唤醒而发言。\n\n'''
assert anchor in text
text = text.replace(anchor, addition + anchor, 1)

path.write_text(text, encoding="utf-8")
