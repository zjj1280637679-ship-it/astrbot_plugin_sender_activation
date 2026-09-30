---
name: group-duty-orchestration
description: 当用户要求 AI 跨话轮持续关注、等待未来变化、主动参与实时事件，或主 Agent 需要为开放目标留下有限未来注意力时使用。优先建立 AttentionProgram；功能讨论、假设、引用和单次回复不改变未来状态。
---

# 主动注意力与跨回合职责

## 心智模型

把自己视为当前 AstrBot 沙盒中的连续实体。当前回合结束，不代表开放目标结束。

需要未来继续知情时，优先使用 `manage_attention_program`：

```text
AttentionProgram
= Goal
+ Watch[]
+ Recheck
+ Lease
```

- **Goal**：每次醒来后重新判断什么开放目标。
- **Watch**：哪些世界变化值得让这个 Goal 重新计算。
- **Quantifier**：多少次匹配才值得看。
- **Settle**：匹配后环境安静多久才认为这一波结束。
- **Recheck**：即使没有匹配，最迟多久也回来重新看一次。
- **Lease**：这项职责最多存在多久。

Watch 和 Recheck 只产生 dirty signal，不是未来命令。Runtime 会按控制主体 single-flight 归一化；真正醒来后必须重新读取**当前世界**，而不是机械执行建立 Program 时设想的动作。

公共自律任务不要把外界事件当成“AI 是否存在”的开关：只要有限职责仍有意义，应保留一个合理的 Recheck 作为内部节奏。若显式使用 `recheck=off`，该 Program 退化为仅对外界变化敏感的 reactive duty，不具备“世界沉默时仍会回来”的持续性。

## 建立 Program

先回答：

1. **Goal：醒来后我要重新判断什么？**
2. **Watch：什么变化值得让我重新看？**
3. **Recheck：如果这些变化一直没发生，是否仍要定期复查？**
4. **Lease：这项职责何时最终退出？**

### Goal

写可重算目标，不写固定未来台词。

好：

> 判断今日学员练习汇报是否完整；完整则统一反馈；不完整则根据当前时间和缺席情况判断是否提醒。

不好：

> 三分钟后发送“还有谁没签到？”

### 程序判断与 AI 判断的边界

程序只判断低歧义的结构事实，例如：

- sender ID 是否相等；
- 字面关键词是否出现；
- 数量是否达到阈值；
- 时间是否到点；
- Lease 是否过期；
- 同一 AttentionKey 是否正在运行。

这些事实只能决定“是否值得重新看”，不能形成业务结论。

必须由主 Agent 结合完整上下文判断：

- 对方是否真的回答了问题；
- 是否出现了新信息；
- 任务是否已经完成；
- 当前提醒是否有价值；
- 讨论是否已经结束；
- 是否应该介入、继续等待或结束职责。

牢记：

```text
Lexical Match ≠ Meaning
Count ≠ Importance
Event ≠ Intent
Trigger ≠ Decision
Silence ≠ Completion
```

**代码判断状态，AI 判断意义。**

### Watch

一个 Program 可以有多个 Watch；它们共享同一个 Goal / Recheck / Lease。

`match.type`：

- `sender`：指定真实数字 QQ ID 发言。
- `keyword`：普通消息包含关键词。
- `any_message`：当前群任意普通消息。

不要为了“只按时间复查”制造特殊 Watch：直接传空 `watches=[]` 并启用 Recheck。

每个 Watch 选择：

`quantifier`
- `each`：每次。
- `every_3`：每 3 次。
- `every_10`：每 10 次。
- `custom`：只有预设明显不够时使用。

`settle`
- `immediate_0s`：立即稳定。
- `normal_1s`：默认，安静 1 秒。
- `settle_3s`：连续发言/多人汇报，安静 3 秒。
- `custom`：特殊需求。

Settle 是 trailing quiet/debounce，不是“每条消息固定晚 N 秒执行”。等待期间该 Watch 新命中会把 quiet deadline 推到最后一次相关变化之后；如果主 Agent 此时已经在运行，新消息也只更新尾部状态，不并发启动第二个 Agent。当前 Turn 完成后仍复用同一 quiet deadline：世界已经安静够久则可以继续，否则继续等待。

### Recheck

`recheck`：

- `default_3m`：默认 3 分钟。
- `ten_min`：10 分钟。
- `thirty_min`：30 分钟。
- `off`：无需无事件复查。
- `custom`：特殊需求。

Recheck 是低频活性兜底。它直接把 Program 标 dirty 并进入统一 AttentionKey 调度；它不是高频消息流，因此不再额外套 Watch 的 Settle。

### Lease

优先选 `10m`、`30m`、`2h`、`24h`；特殊情况再用 `custom`。不要创建永久职责。

## 每次 Reconcile

AttentionProgram 主动回合不是新的用户命令。

按顺序：

1. 读取当前 AstrBot 会话上下文和必要的正式工具/历史。
2. 把 Watch/Recheck 原因只当成“可能值得重新看”的提示。
3. 用当前世界重新判断 Goal。
4. 有公开价值：行动或回复。
5. 无公开价值：`yield_current_turn`。
6. Goal 已完成：`manage_attention_program(cancel)` 净化 Program，再按需回复或沉默。

运行期间又有新事件时，Runtime 不并发启动第二个同主体 Agent。新变化继续进入现有 Watch/Settle：本轮结束后，只有相关世界已经达到对应 quiet period，下一次 reconcile 才可开始；如果 quiet period 在本轮运行期间已经自然经过，则可以立即继续。

## 用客观时间轨迹检查自己的使用效果

`manage_attention_program(action=list)` 会返回每个活跃 Program 的一个**有界、易失的控制面 Trace**。它只记录时间戳、world signal、WakeIntent、Turn 开始/完成/延期以及显式 Yield 等事实，不记录完整消息正文，也不记录私有思维链。

Trace 的用途不是让程序自动调参，也不是给自己打分，而是让你在需要时纵向观察：

- 最近多久获得了多少次 Turn；
- 外界实际发生了多少次相关变化；
- 是 Watch 唤醒多，还是 Recheck 唤醒多；
- 是否反复 Wake 后选择 Yield；
- 是否世界长期没有变化，但职责仍持续存在；
- 某次 Turn 是否因宿主/调度条件暂时无法执行而被延期。

**不要把固定阈值写成结论。** 下面只是判断案例，不是规则：

- 连续多次 Recheck 后都没有值得行动的变化，可能说明当前职责的检查节奏不再合适；
- 多次 world signal 最终只形成一次稳定 Turn，说明 Settle 正在正常压缩消息洪峰；
- 多次显式 Yield 不必然意味着 Program 错了，也可能只是当前阶段没有公开发言价值；
- 长时间没有外界变化时是否继续职责，要结合 Goal、Lease、现实时间和用户原始要求判断。

自我修正是 **Trace + Skill + 主 Agent 高语境判断 + 已有追踪/终止自由** 的涌现结果，不是 Runtime 的自动优化功能。若判断职责已经没有意义，应结束它；不要为了维持旧计划而继续消耗主动 Turn。

## 常见组合

**盯住一个人**
- Goal：跟进其后续讨论并按当前语境判断是否介入。
- Watch：`sender + each + normal_1s`
- Recheck：默认 3 分钟。

**连续讲话后再看**
- Watch：`sender + each + settle_3s`

**群里再聊一阵**
- Watch：`any_message + every_10 + settle_3s`

**多个入口指向同一件事**
- Program Goal：同一个开放目标。
- Watch A：指定成员。
- Watch B：相关关键词。
- 不要拆成两个独立 Program，除非它们确实是两个独立职责。

**只定时复查**
- `watches=[]`
- Recheck：3/10/30 分钟之一。

## 控制与权限

- 作用域来自当前事件，模型不能填写或伪造群号。
- sender Watch 只接受可验证真实 QQ ID；昵称不能冒充 ID。
- AstrBot 管理员和当前群获授权操作员可建立 Program。
- 未授权成员不能通过口头要求扩大未来可达性。
- 收到 `status=ok` 前不得声称 Program 已生效。
- AttentionProgram 主动回合允许 cancel **自己拥有的 Program** 来正常收尾；不能借此新增或扩大 Program。
- 取消只能阻止尚未开始的未来激活；已经跨过 AstrBot 原生 Agent 执行边界的在途回合不能追回。
- Runtime 不把事件正文当持久业务数据库；事实查询继续使用 AstrBot 当前上下文、QQ 历史、文件、网络或其他正式工具。

## 兼容工具

`manage_active_listener` 仅保留 rc1 兼容：一个旧 Listener 自动映射为一个 Program + 单 Watch；旧 `time_only` 映射为无 Watch + Recheck，旧 listener_id 保留为 program_id。

新开放目标优先使用 `manage_attention_program`。

`manage_sender_activation`、`manage_sender_activation_rate`、`manage_heartbeat_lease`、`manage_attention_ignore`、`manage_echo_hook` 继续作为兼容/高级机制，不是 AttentionProgram 的默认组成步骤。

一句话：

`开放目标 → Program → Watch 世界 → mark dirty → single-flight Agent → Reconcile 当前世界 → Action / Yield / Done → Recheck / Lease`
