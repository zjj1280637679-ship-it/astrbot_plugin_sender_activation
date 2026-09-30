# 统一监听—激活 Runtime：v1.2 工程契约

状态：`1.2.0-rc.1` 首版实现。本文定义统一监听 Runtime 的产品语义；旧对象激活、Heartbeat、Ignore、Echo 继续作为兼容/高级机制。

## 1. 核心立意

AstrBot 主 AI 被视为当前群聊沙盒中的连续实体。一个 Agent 回合结束，不代表开放目标结束。

插件不维持一个永不退出的 Agent 进程，而是允许当前回合给未来的自己留下有限注意力：

```text
当前 Agent 回合
  → 建立有限监听
  → 当前回合结束
  → 世界继续变化
  → 监听器只产生信号
  → 时间归一化
  → 单一主动 Agent 回合
  → 重新观察、行动 / 沉默 / 净化
```

正确性来自**每次醒来重新判断当前世界**，不是来自机械执行旧动作。

## 2. 分层

```text
AstrBot Event Stream
        ↓
Listener Condition
        ↓
Frequency
        ↓
Per-listener Debounce
        ↓
READY Signal
        ↓
Entity-level Normalizer
        ↓
AstrBot Main Agent
        ↓
Action / Yield / Cancel / Keep
```

各层只做自己的工作：

- **Condition**：什么事件与当前开放目标有关。
- **Frequency**：多少个相关事件才形成候选。
- **Delay**：候选形成后，环境连续安静多久才 READY。
- **Normalizer**：多个 READY 在同一控制主体内归一化成一次 Agent 激活。
- **Watchdog**：长期没有条件事件时，保证 AI 仍有一次重新检查机会。
- **Agent**：解释事实并决定行动，不由 Listener 写死回复。

## 3. Listener Contract

首版 Listener Contract：

| 维度 | 首版选项 | 语义 |
| --- | --- | --- |
| Condition | `sender` | 指定 QQ ID 发言 |
| Condition | `keyword` | 任意普通消息包含关键词 |
| Condition | `any_message` | 当前群任意普通消息 |
| Condition | `time_only` | 不依赖消息，仅靠 watchdog |
| Frequency | `each` | 每次命中 |
| Frequency | `every_3` | 累计 3 次 |
| Frequency | `every_10` | 累计 10 次 |
| Delay | `immediate_0s` | 不等待 |
| Delay | `normal_1s` | 默认等 1 秒 |
| Delay | `settle_3s` | 复杂连续场景等 3 秒 |
| Watchdog | `default_3m` | 默认 3 分钟无事件也醒一次 |
| Watchdog | `ten_min` / `thirty_min` | 更低频兜底 |
| Watchdog | `off` | 无事件时不主动醒 |
| Lifetime | `10m` / `30m` / `2h` / `24h` | 有限生命周期 |

各维度正交。需要特殊参数时才使用 `custom`；一般场景优先预设。

`goal` 是唯一主要自由文本：描述“醒来后重新判断什么开放目标”，不要写固定台词。

## 4. 对象不是用户 ID

“监听对象”容易被误解为 `target_user_id`。Runtime 实际监听的是**条件**。

例如：

- `sender=[123456]`：关注一个成员。
- `keyword=["报错","失败"]`：关注内容。
- `any_message + every_10`：再出现 10 条消息后重新检查。
- `time_only + default_3m`：不依赖新消息，每 3 分钟获得一次检查机会。

未来可新增新的 Condition Adapter，而不改变后面的 Frequency / Delay / Normalizer / Agent。

## 5. 消抖与“最后一条”

Delay 是 debounce，不是固定延时执行。

设 `settle=3s`：

```text
10:00:00  命中 A → 候选，等 3s
10:00:01  命中 B → 更新最新状态，重新等 3s
10:00:02  命中 C → 更新最新状态，重新等 3s
10:00:05  连续 3s 无新命中 → READY
```

A/B/C 不产生三个 Agent。Runtime 只产生一次激活，并把“累计命中次数 + 最新关联事件”作为激活事实；主 Agent 再读取当前 AstrBot 上下文理解完整现场。

## 6. 两级归一化

### 订单内归一化

每个 Listener 独立计算 Frequency 与 Delay。

### 实体级归一化

同一群、同一控制主体的多个 Listener 可能同时 READY。它们不各自启动 Agent，而是合并：

```text
READY A ─┐
READY B ─┼→ one normalized activation → Main Agent
READY C ─┘
```

主 Agent 一次看到本轮所有激活原因。

当 Agent 已经运行时，新信号继续进入 Runtime；不并发启动同一控制主体的第二个主动 Agent。当前回合完成后重新调度。

## 7. Watchdog：条件监听的活性兜底

条件监听是主路径，Watchdog 是兜底。

```text
条件命中 → 快速激活 → Agent 重审目标
                    ↓
                 回合完成
                    ↓
              watchdog 重新计时

一直没有条件命中
        ↓
watchdog 到点
        ↓
Agent 重新检查目标
```

默认 `default_3m`。

目标完成时 AI 应取消对应 Listener；但“正确取消”不是正确性的前提。如果忘记取消，下一次 watchdog 只多产生一次重新判断，AI 可发现目标已完成并 `yield_current_turn` 或净化。

因此：

- 条件监听负责敏捷性。
- Watchdog 负责活性。
- Agent 每轮重判负责正确性。
- 净化负责效率。

## 8. 复位语义

一次 Listener 激活结束后，首版默认重新布防：

- 清空本轮计数与 pending 候选；
- 保留有限 Listener Contract；
- 有 watchdog 时从**本轮 Agent 完成时刻**重新计时；
- 生命周期到期后不再产生新激活；
- Agent 可主动 cancel 当前 Listener。

这避免“刚因条件醒来，旧的 3 分钟 watchdog 紧接着又醒一次”。

## 9. 瑜伽群验收场景

目标：

> 学员实时汇报练习；人报齐后统一反馈。如果一直有人没报，AI不能永久沉默，要主动检查并决定是否提醒。

可以建立一个或多个 Listener：

```text
Condition: 学员相关 sender / 相关关键词
Frequency: each
Delay: settle_3s
Watchdog: default_3m
Goal:
“持续关注今日练习汇报；条件足够时统一反馈，
若长时间仍有人未汇报则检查当前签到情况并决定是否提醒。”
```

当多人连续汇报时，3 秒 debounce 吸收连续事件；当无人继续汇报时，3 分钟 watchdog 保证 AI重新获得检查机会。

“谁已经签到 / 谁应该签到”的持久业务状态不由 Listener Runtime 自行伪造。主 Agent 应结合 AstrBot 当前上下文、QQ 历史检索或其他正式状态工具判断。未来若引入专门 Projection/业务状态层，应保持与 Listener Runtime 解耦。

## 10. 工具面板原则

给 AI 选择题，不给大量填空题。

主工具 `manage_active_listener` 的常用字段只有：

```text
监听什么       condition_kind / condition_values
多久形成候选   frequency
等多久再醒     response_speed
多久没事也醒   watchdog
持续多久       lifetime
为什么醒       goal
```

默认推荐：

```text
frequency      = each
response_speed = normal_1s
watchdog       = default_3m
lifetime       = 2h
```

自定义数值只在预设明显不能表达需求时使用。Schema 负责“怎么操作”，Skill 只负责“什么时候值得建立监听”。

## 11. 首版边界

`1.2.0-rc.1` 已实现：

- sender / keyword / any_message / time_only 条件；
- 计数频率；
- 0/1/3 秒及自定义 debounce；
- 默认 3 分钟及可调/可关闭 watchdog；
- 同一控制主体的 READY 合并；
- Agent 运行期间不并发重复主动激活；
- Listener 持久化、到期、查询、精确取消；
- Listener 主动回合可净化自己拥有的 Listener；
- Listener 回合可结构化沉默。

尚未把以下内容伪装成已完成：

- 群昵称 → QQ ID 自动解析；
- 任意 AND/OR 条件 DSL；
- “所有学员已经报齐”这类业务集合 Barrier；
- 专门的业务 Projection/签到数据库；
- 精确绝对时点条件（现有 Heartbeat/Cron 仍可承担）；
- 持久化 pending 消息计数；重启后 Listener Contract 恢复，但未结算的内存候选不复活。

这些能力应作为 Condition / Projection / Scheduler 的独立扩展，不修改“信号 → 归一化 → 单一 Agent 激活”的核心契约。
