# Attention Program / Reconcile Runtime：v1.2.0-rc.2 工程契约

> v1.3.0-rc.1 起，本 Runtime 被定位为公共自律 Harness 的第一种 Contract 实现。
> Harness 的 Contract / Governor / Turn 总纲与纯 core 边界见 [self_discipline_harness.md](self_discipline_harness.md)。
> 本文继续保留 AttentionProgram 的具体工程契约与 v2 存储语义。

状态：**rc2 已实现候选。** 当前运行时已经从“一 Listener 一职责”重构为 **AttentionProgram → Watch → Dirty/Reconcile**；旧 rc1 Listener 只保留迁移与兼容适配，不再运行第二套 Listener Runtime。

## 1. 已实现目标

AstrBot 主 AI 被视为群聊沙盒中的连续实体。当前回合可以为开放目标保留有限的未来注意力，但运行时不保存“未来要机械执行的动作”。

新的一级实体不是 Listener，而是：

```text
AttentionProgram
= 一个尚未完成的开放目标
+ 一组告诉 Runtime “什么时候值得重新看”的 Watch
+ 无事件时的 Recheck
+ 有限 Lease
```

一句话：

> **Watch 负责把世界变化变成 dirty；Program 负责表示“哪件事还值得我继续关心”；Agent 每次被唤醒都重新 reconcile 当前世界与 Goal。**

## 1.1 借鉴而不是重造

rc2 的核心句柄直接对齐成熟实现：

- Kubernetes client-go workqueue：同 key single-flight，处理中再次 dirty，完成后再处理一次；
- controller-runtime Reconcile：触发只说明“可能需要重新计算”，真正处理重新读取当前状态；
- CloudEvents：事件信封采用 `id/source/type/subject/time` 一类通用句柄，`source + id` 用于去重；
- AstrBot 原生 Cron/Agent：真正的主 Agent 执行仍由 `run_job_now()` 承载，插件不建立第二套 Agent。

参考：
- https://pkg.go.dev/k8s.io/client-go/util/workqueue
- https://pkg.go.dev/sigs.k8s.io/controller-runtime/pkg/reconcile
- https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md

## 2. 最小逻辑树

```text
AstrBot / 外部世界
        │
        ├─ QQ 消息
        ├─ 关键词
        ├─ 消息数量
        ├─ 时间
        └─ 未来其他事件源
                │
                ▼
        EventEnvelope
        id / source / type / subject / time
                │
                ▼
      AttentionProgram
        ├─ Goal
        ├─ Lease
        ├─ Recheck
        └─ Watch[]
             ├─ Match
             ├─ Quantifier
             └─ Settle
                │
             命中后
                ▼
        mark Program DIRTY
                │
                ▼
       AttentionKey Normalizer
        scope + controller
                │
        ┌───────┴────────┐
        │                │
   当前未运行         Agent 正在运行
        │                │
按 settle 到期调度      只增加 dirty generation
        │                │
        └───────┬────────┘
                ▼
            single-flight
                ▼
          Main Agent Turn
                ▼
      RECONCILE(CurrentState, Goal)
        ├─ Action
        ├─ Yield
        ├─ Keep
        └─ Done / Cancel
                │
                ▼
     若运行期间再次 dirty
          再 reconcile 一次
                │
                ▼
     Recheck 从完成时刻重新计时
                │
                ▼
        Lease 到期最终退出
```

## 3. 六个给 AI 的高层句柄

### 3.1 Goal：开放目标

回答：

> 醒来以后，我要重新判断什么？

Goal 是 Reconcile Goal，不是未来固定动作。

好：

```text
判断今日练习汇报是否已经完整；
完整则统一反馈；
不完整则根据当前时间和缺席情况判断是否提醒。
```

不好：

```text
三分钟后发送“还有谁没签到？”
```

### 3.2 Watch：关注什么变化

回答：

> 什么变化值得把这个 Program 标脏？

Watch 只是传感器，不直接激活 Agent。

首批 Match Adapter 可以继续来自 rc1：

- sender
- keyword
- any_message
- time / future external adapters

未来 GitHub、文件、Webhook、工具状态等也只需要适配成 Watch Event，不改 Reconcile Core。

### 3.3 Quantifier：多少变化才值得重新看

替换内部模糊的 `frequency` 语义。

首版目标只保留简单离散值：

- every event
- every 3
- every 10
- custom N

它只回答计数，不承担 AND/OR、时间窗口、集合齐备或顺序模式。

### 3.4 Settle：世界安静多久才认为这一波结束

替换“response speed / delay”的歧义。

```text
immediate  = 0s
normal     = quiet 1s
settled    = quiet 3s
```

它是 debounce / quiet period，不是“每条事件固定延后 N 秒执行”。

### 3.5 Recheck：即使没有事件，最迟多久也重新看一次

替换 `watchdog` 的产品语义。

```text
default = 3m
10m
30m
off
custom
```

Condition/Watch 负责敏捷性，Recheck 负责活性。Recheck 也只是 signal，仍必须经过 Program 的归一化路径。

### 3.6 Lease：这件职责最多存在多久

回答：

> 这项 AttentionProgram 何时最终退出？

Lease 不承担 debounce、retry、completion、count window 等其他时间语义。

## 4. Runtime 内部句柄

这些不应暴露给普通 AI 面板。

### 4.1 Program ID

精确标识一个开放职责，供查询、更新、取消。

### 4.2 AttentionKey

```text
(scope, controller)
```

控制“真正醒来的始终只有一个我”。同一 AttentionKey 的多个 READY Program 合并成一次主 Agent Turn。

### 4.3 Dirty Generation

不要保存一个无限事件队列，只保存“自上次 reconcile 之后是否又变脏”。

```text
ProgramRuntime
{
  dirty_generation
  reconciled_generation
  running
  ready_at
  reasons
}
```

事件命中：

```text
dirty_generation += 1
```

若 Agent 正在运行：

```text
不并发启动第二个 Agent
只继续 mark dirty
```

Agent 完成：

```text
dirty_generation > reconciled_generation
    → 再 reconcile 一次
否则
    → idle
```

这是 Runtime 的背压与 single-flight 核心。

## 5. EventEnvelope：给更广泛外界数据的薄接口

不同事件源先归一成：

```text
EventEnvelope
{
  id
  source
  type
  subject
  occurred_at
  observed_at
  payload_ref
}
```

重要原则：

- `source + id` 可用于去重；
- `occurred_at` 与 `observed_at` 分开；
- payload 只保留引用或必要元数据，Runtime 不默认持久保存完整消息正文；
- 事件只是“可能需要重新计算”的事实，不是 Agent 命令。

## 6. 一个 Program 可以拥有多个 Watch

这是相对 rc1 最关键的结构变化。

例如：

```text
Program: 今日瑜伽练习监督

Goal:
  判断今日练习成员是否已经完成汇报；
  完整则统一反馈；
  未完整则根据当前时间与缺席情况判断是否提醒。

Lease:
  今天结束前

Recheck:
  3m

Watch A:
  Match = 学员消息
  Quantifier = every event
  Settle = quiet 3s

Watch B:
  Match = keyword("完成", "练完")
  Quantifier = every event
  Settle = quiet 3s
```

A/B 都只把同一个 Program 标脏，不各自持有 Goal，也不各自启动 Agent。

## 7. 不把业务 Barrier 塞进 Core

“所有学员是否报齐”暂时不做成 Runtime 的一级 Condition。

Runtime 只负责：

```text
有人汇报了
→ Program dirty
→ Agent 醒来
→ 读取当前世界
→ 自己判断 reported / missing
```

如果三分钟没有新事件：

```text
Recheck signal
→ Program dirty
→ Agent 醒来
→ 再次读取当前世界
→ 判断是否需要提醒
```

以后若完整扫描过贵，可以单独增加 Projection：

```text
Event → Projection update → mark dirty
                     ↓
              Agent reconcile
```

Projection 是优化层，不属于 Attention Core。

## 8. 重启恢复

坚持 level-triggered reconcile，不追求精确复活所有旧 edge。

重启后：

```text
恢复未过期 AttentionProgram
        ↓
丢弃旧 volatile debounce / pending edge
        ↓
每个活跃 Program 标记 restart_dirty
        ↓
按 AttentionKey 合并
        ↓
做一次 current-state reconcile
```

因此重启恢复的正确性来自“重新读取当前世界”，不是持久化崩溃前最后 1.37 秒的 debounce 状态。

## 9. 明确不进核心的概念

rc2 明确不把 Runtime 做成 CEP / Workflow DSL。

暂不进入 Attention Core：

- 任意 AND / OR / NOT 条件树；
- A 后 B、严格顺序等 Sequence DSL；
- all_of(users) 这类业务集合 Barrier；
- hold_for / hysteresis；
- 完整事件历史持久化；
- 固定未来回复脚本；
- 第二套 Agent 或第二套业务数据库。

真实需求出现时，再分别评估为 Watch Adapter、Projection、Scheduler 或独立业务插件。

## 10. AI 面板（rc2 已实现）

AI 工具与控制台只暴露真正需要理解的六件事：

```text
开放目标       Goal
关注什么       Watch
多少变化再看   Quantifier
安静多久再看   Settle
最迟多久再看   Recheck
持续多久       Lease
```

其余：

```text
Program ID
AttentionKey
Event ID
dirty_generation
single-flight
dedupe
reset
restart_dirty
```

全部沉到底层。

## 11. rc1 → rc2 已实现迁移

rc2 启动时，只有 v2 primary/backup 都真正不存在，才允许把 rc1 Listener 映射为：

```text
rc1 Listener.goal              → Program.goal
rc1 Listener.lifetime          → Program.lease
rc1 Listener.watchdog          → Program.recheck
rc1 Listener.condition         → Program.watch[0].match
rc1 Listener.frequency         → Program.watch[0].quantifier
rc1 Listener.settle_delay      → Program.watch[0].settle
```

rc2 没有推翻 rc1，而是把“一个 Listener = 一个完整职责”拆成：

```text
一个 Program
  + 多个 Watch
```

旧 listener_id 原值保留为 program_id；time_only 映射为 `watches=[] + Recheck`。如果 v2 已存在但损坏，Runtime fail inert，禁止用可能陈旧的 v1 覆盖 v2。

## 12. rc2 已自动化验收

当前候选至少验证：

1. 一个 Program 可以拥有多个 Watch，但只有一个 Goal / Recheck / Lease。
2. 多个 Watch 同时命中只增加同一 Program 的 dirty generation。
3. 多个 Program 同属一个 AttentionKey 时仍只有一个主 Agent single-flight。
4. Agent 运行期间的新事件不会并发启动第二个 Agent；完成后若仍 dirty，再 reconcile。
5. Recheck 与任何外部 Watch 一样，只产生 dirty signal。
6. 重启后不复活旧 edge，而是对未过期 Program 做一次 restart reconcile。
7. rc1 Listener 可以无损迁移成 Program + single Watch。
8. AI 常规调用不需要接触 Program ID 之外的任何内部并发句柄。
9. `source + id` 重复 EventEnvelope 不重复处理。
10. v2 损坏时不会复活陈旧 v1。
11. Recheck 从实际 Agent 完成时刻重新计时。
12. 固定 AstrBot 4.27.2 可生成 `watches: array[object]` Tool Schema。
