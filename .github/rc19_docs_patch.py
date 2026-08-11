from pathlib import Path

# README: document only proven rc19 candidate behavior and known execution boundaries.
p = Path('README.md')
t = p.read_text(encoding='utf-8')

anchor = '- **群聊监控中的主动沉默**：激活不等于强制回复，没有有效增量时由 AI 正式让出话轮。\n'
addition = (
    '- **有限对象忽略（rc19 候选）**：部署者显式授权后，主 Agent 可把对象 ID、计数阈值和滑动时间窗口叠加成有限前置 Ignore；已有对象追踪可以继续存在，短期 Ignore 只临时覆盖相应激活。\n'
    '- **有限回响（rc19 候选）**：主 Agent 只有显式挂载 Hook 才获得稍后有限主动再检查；默认一次但可配置上限，所有次数从原激活回合一次性预排，Echo 回合不能递归创建 Echo。\n'
)
assert anchor in t
if addition not in t:
    t = t.replace(anchor, anchor + addition, 1)

t = t.replace('第一阶段只需要看到五个工具的短名称和精简用途', '第一阶段只需要看到七个工具的短名称和精简用途', 1)
t = t.replace(
    '把对象关注、周期心跳、操作员授权、限频与结构化沉默作为一个按需加载的职责编排手册',
    '把对象关注、周期心跳、有限对象忽略、有限回响、操作员授权、限频与结构化沉默作为一个按需加载的职责编排手册',
    1,
)

old_note = '''> **候选版说明：** `1.1.0-rc.18` 基于已通过真实模型两阶段验收的 `rc.17`，
> 只补充错误回执的恢复事实：`recovery_action_executed=false` 与
> `automatic_retry_scheduled=false`，避免主 Agent 把“建议下一步恢复”误报成
> “已经在恢复/系统会自动重试”。租约、权限、状态存储、Web API、工具名称与
> 参数接口均保持不变。
'''
new_note = '''> **rc19 候选说明：** `1.1.0-rc.19` 在 rc18 的真实回执契约上增加第一批注意力运行时能力：
> 对象级有限 Ignore、对象计数/时间窗口触发 Ignore、可选有限 Echo，以及 Heartbeat/Echo 在主 Agent
> 激活前的 SessionGate 预检。Ignore 与 Echo 默认都关闭，是否授予主 Agent 由部署者配置决定；
> 程序不根据模型名称自动提权。当前仍不是“对象/时间/计数”的通用规则 DSL，也没有实现全量监听、
> 时间-only Ignore 或通用 Count Activate。
'''
assert old_note in t
t = t.replace(old_note, new_note, 1)

boundary_anchor = '''> **运行边界：** 首版运行域严格等于 `aiocqhttp` 群聊。其他平台和私聊事件
> 保持原样；若主 Agent 在这些语境调用管理工具，插件返回结构化不支持错误，
> 不建立租约、不写入状态。
'''
boundary_addition = '''
> **执行边界：** Ignore、Echo 取消、Heartbeat 禁用都能阻止**尚未开始**的后续 Agent 激活；但一旦
> AstrBot 原生 `run_job_now()` 已经跨过执行边界并开始构建/运行主 Agent，随后到达的取消不能追回
> 那个已经启动的 LLM 调用，最多允许当前在途一次完成，并阻止之后的新激活。同理，首次未知攻击者
> 在主 Agent 建立对象 Ignore 策略之前已经启动的并发 LLM 请求无法追溯取消。rc19 的目标是削减未来
> 可避免成本，不宣称绝对零竞态或零首次识别成本。
'''
assert boundary_anchor in t
if boundary_addition not in t:
    t = t.replace(boundary_anchor, boundary_anchor + boundary_addition, 1)

old_formula = '''M = A_native ∪ {I, H, L_i, Y}'''
new_formula = '''M = A_native ∪ {I, H, L_i, N, E, Y}'''
assert old_formula in t
t = t.replace(old_formula, new_formula, 1)
anchor = '- `L_i`：只约束 `I` 所产生额外激活的可选、有限期限频租约。\n'
addition = (
    '- `N`：部署者授权后，由主 Agent 管理的对象级有限 Ignore 策略；对象、计数与时间窗口可叠加。\n'
    '- `E`：显式可选、有限、不可递归的 Echo 主动再激活 Hook。\n'
)
assert anchor in t
if addition not in t:
    t = t.replace(anchor, anchor + addition, 1)
t = t.replace(
    '- `Y`：仅在 `I/H` 新增的主动回合中，由主 Agent 正式选择无可见回复。',
    '- `Y`：仅在 `I/H/E` 新增的主动回合中，由主 Agent 正式选择无可见回复。',
    1,
)

p.write_text(t, encoding='utf-8')

# Skill: teach truthful cancellation boundary without changing tool-selection semantics.
p = Path('skills/group-duty-orchestration/SKILL.md')
t = p.read_text(encoding='utf-8')
anchor = '- 收到 `status=ok` 前，不得口头声称已经上岗或已经生效。\n'
addition = (
    '- 撤销/禁用只保证阻止尚未开始的未来激活。若原生 `run_job_now()` 已经开始执行主 Agent，当前在途一次可能完成；不得把随后成功取消说成“追回了已经开始的这一轮”。\n'
    '- 对第一次出现、尚未建立 Ignore 策略的对抗对象，已经并发启动的 LLM 回合不能事后追回；有限 Ignore 保护的是策略生效后的后续可激活事件。\n'
)
assert anchor in t
if addition not in t:
    t = t.replace(anchor, anchor + addition, 1)
p.write_text(t, encoding='utf-8')
