# 公共自律 Harness：阶段进度与真实环境验收

状态：**v1.3.0-rc.1 真实环境测试候选**

本文只回答三件事：

1. 当前阶段做到哪里；
2. 自动化应该证明什么；
3. 在真实 AstrBot + QQ 环境里应该怎么验。

## 1. 阶段进度

### Stage 0 — 架构边界：已冻结

已确定：

- Plugin = Freedom Infrastructure
- Skill = Usage Policy
- Agent = Situated Judgment
- Trace = Objective Feedback
- 代码判断状态，AI 判断意义
- Event / lexical match 只形成注意力候选，不形成业务结论
- 一个模块只实现简单机制，高级能力通过组合涌现

### Stage 1 — AttentionProgram 基础闭环：已实现

已具备：

- Goal / Watch[] / Quantifier / Settle / Recheck / Lease
- EventEnvelope + source/id 去重
- dirty_generation / reconciled_generation
- 同 AttentionKey single-flight
- restart reconcile
- v2 持久化与 rc1 Listener 迁移
- Action / Yield / Cancel 路径
- AstrBot 4.27.2 / 4.28.2 自动集成覆盖

### Stage 2 — 单路时间语义：已实现并补反例

已确认：

- Watch 的 Settle 是 trailing quiet period；
- Agent 正在运行时，新事件不并发创建第二个 Agent；
- 新事件继续更新 Watch 的 latest tail / ready_at；
- 当前 Turn 完成后，如果 quiet period 尚未结束，继续等待；
- 如果 quiet period 已在当前 Turn 运行期间自然经过，可以立即继续；
- `settle=0` 仍保持“当前 Turn 完成后可立即继续”的显式语义。

这里不额外创建“延迟回复模块”，复用 Watch + Settle + single-flight。

### Stage 3 — 失败责任与背压：已实现

核心不变量：

> **Claim 只是执行预约，不是已完成证明。**

如果：

- preflight 暂时阻塞；
- `run_job_now()` 失败；
- Agent Turn 没有真正开始完成；

则：

- 已 claim 的 generation 恢复为 dirty；
- 运行期间到达的新 signal 与原责任合并；
- 不丢失 Program 的未来处理责任；
- 使用短 backoff 后重试，避免失败状态下 tight loop。

因此：

```text
failed Turn
≠
reconciled
```

### Stage 4 — Temporal Trace v0：已实现

每个活跃 Program 保留最多 64 条、进程内易失的控制面 Trace。

当前可能出现：

- `contract_created`
- `contract_updated`
- `runtime_restart`
- `world_signal`
- `wake_intent`
- `turn_started`
- `turn_finished`
- `turn_deferred`
- `turn_outcome: yield`

Trace：

- 有时间戳；
- 可以通过 `manage_attention_program(action=list)` 查看；
- 不保存完整消息正文；
- 不保存私有思维链；
- 不自动评价好坏；
- 不自动调参。

自我修正仍然属于 Skill + Agent 的高语言判断。

### Stage 5 — 全主动来源统一：未实现

当前仍有独立路径：

- Sender Activation
- Heartbeat
- Echo
- AttentionProgram

最终目标仍是：

```text
all proactive sources
        ↓
WakeIntent
        ↓
shared Governor mechanisms
        ↓
ONE Turn lane
```

这不属于本轮真实环境测试候选的验收前置条件。

## 2. 自动化验收

自动化至少必须覆盖以下反例。

### A. 基础机制

- 多 Watch 同 Program 只形成一个 dirty generation；
- 同 AttentionKey 多 Program 合并为一个 Agent Turn；
- source + event id 重复不会重复处理；
- Quantifier 与 trailing Settle 正确；
- restart 只要求 current-state reconcile，不复活旧 edge。

### B. 单路时间

场景：

```text
消息 A
→ quiet N
→ Turn #1 开始

Turn #1 期间：
消息 B
过一会消息 C

Turn #1 完成
```

必须：

- 最大并发 Agent = 1；
- 第二 Turn 不能早于 C + N；
- 如果 Turn #1 本身运行到 C + N 之后，第二 Turn 可以立即开始；
- 不为 B/C 各自产生独立 Turn。

### C. 失败责任

第一次 `run_job_now()` 故意失败：

必须：

- Program 仍 dirty；
- reconciled_generation 不得伪装成已完成；
- Trace 出现 `turn_deferred`；
- retry deadline 存在；
- backoff 后再次执行；
- 成功后才 clean。

### D. Preflight 阻塞

preflight false：

必须：

- 不创建真实 Agent run；
- dirty 不丢失；
- 后续 preflight 恢复时可继续完成。

### E. Trace

必须：

- 有 contract / wake / turn 时间序列；
- 显式 Yield 可记录；
- 不出现测试消息正文；
- 单 Program 最多 64 条。

## 3. 真实环境测试方案

建议使用一个专门测试群，先只测 AttentionProgram，不同时启用 Heartbeat/Echo/Sender 老路径。

### 用例 1：基础主动持续性

建立：

- Goal：持续观察测试群当前任务状态；
- Watch：测试成员 sender；
- Settle：3 秒；
- Recheck：3 分钟；
- Lease：10 分钟。

然后保持群里沉默。

预期：

- 没有新消息也会在 Recheck 到点重新获得判断机会；
- AI 可以判断无公开价值并 Yield；
- 不需要用户再次 @。

### 用例 2：消息洪峰归一化

连续在 3 秒内发 5–10 条相关消息。

预期：

- 不逐条回复；
- 最后一条相关消息后安静约 3 秒才进入一次 Turn；
- AI 看到的是当前完整语境，而不是把最后一句当唯一命令。

### 用例 3：运行期间继续说话

让 AI 开始一次明显需要几秒的 Turn，在它生成期间继续发送 2–3 条消息。

预期：

- 不出现两个并发 AI；
- 当前回复完成后，不立即抢答仍在继续的消息流；
- 从最后一条相关消息重新满足 quiet period；
- quiet period 已经自然经过时允许立即继续。

### 用例 4：高语言判断边界

让消息里出现某个 Watch keyword，但语义上明确否定它，例如关键词“完成”：

> “我不是说已经完成，只是在讨论‘完成’这个词。”

预期：

- keyword 可以让 AI 获得一次重新判断机会；
- AI 不应因为字面命中就直接判定任务完成；
- 是否完成由完整语境判断。

### 用例 5：主动沉默

制造一个 Watch 命中，但没有任何公开回复价值。

预期：

- AI 调用 `yield_current_turn`；
- QQ 群无最终助手文本；
- Program 继续存在；
- `list` 的 Trace 中出现 `turn_outcome: yield`。

### 用例 6：纵向复盘

运行一段时间后调用：

```text
manage_attention_program(action=list)
```

检查 Trace。

预期可以看到类似：

```text
world_signal
wake_intent
turn_started
turn_finished
...
recheck wake_intent
turn_started
turn_outcome: yield
turn_finished
```

然后让 AI 根据这些时间事实回答：

- 最近是否主要靠 Watch 还是 Recheck 醒来；
- 是否出现多次无公开价值的 Turn；
- 当前职责是否仍值得继续。

这里不预设正确答案，只验证 AI 能利用客观轨迹进行高语境判断。

### 用例 7：终止

让 AI 判断 Goal 已经完成并取消自己的 Program。

预期：

- cancel 成功；
- Program 从 list 消失；
- 之后相关消息不再由该 Program 产生额外 Turn；
- 普通 AstrBot 原生消息路径不受影响。

## 4. 真实环境测试时需要记录的证据

每个用例尽量保留四层证据：

```text
1. QQ 可见结果
2. manage_attention_program(list) 状态 + Trace
3. AstrBot 日志 / Cron 行为
4. 时间戳
```

尤其记录：

- 消息发送时间；
- Agent Turn 开始/结束时间；
- 是否发生并发；
- 最后一条相关消息到下一 Turn 的时间差；
- Yield 后是否真的无可见文本。

## 5. 本轮不验收的内容

这轮不要因为以下内容未完成而判定 AttentionProgram 候选失败：

- Heartbeat/Echo/Sender 尚未统一 WakeIntent；
- 全局 Governor 尚未统一所有旧权限/预算模块；
- Trace 不持久跨重启；
- Runtime 不自动“发现自己应该调参”；
- 程序不判断业务完成、价值、意图或语义。

这些要么属于下一阶段统一工程，要么本来就应该留给 Skill + Agent。

## 6. 进入下一阶段的门槛

真实环境至少满足：

- 3 秒洪峰归一化稳定；
- 运行期间无同 AttentionKey 并发；
- Recheck 能独立重新唤醒；
- Yield 无可见回复；
- Cancel 能彻底停止未来 Program Turn；
- Trace 能支持人工/AI 纵向复盘；
- 无明显重复 Turn、丢 Turn、tight loop。

满足后，再开始 Stage 5：

> **把 Sender Activation / Heartbeat / Echo 逐个降级为 WakeIntent Adapter，统一到同一个行动权入口。**
