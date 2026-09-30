# 公共自律 Harness：工程总纲 vNext

状态：**目标架构已冻结，v1.3.0-rc.1 仅实现其中第一阶段。**

## 0. 一句话定义

> **Agent 不由事件驱动存在，而由职责驱动持续存在；事件只改变它所处的世界。**

本插件是建立在 AstrBot 主 Agent 之上的 **公共自律 Harness**。它不替 Agent 完成业务目标，而是为 Agent 提供可持续、可约束、可撤销的积极自由与消极自由，使一个有限职责能够跨回合继续存在，并按照自己的内部节奏重新获得判断机会。

第二条总原则：

> **公共自律 AI 既不会因为世界沉默而永久消失，也不会因为世界喧闹而被每一个事件牵着走。**

AstrBot 继续负责真正的 Agent、消息、历史、工具、Provider 与执行环境；Harness 只负责未来行动权的治理。

---

## 1. 与普通 AI 的范式差异

普通聊天 AI 的基本循环是：

    外界调用
       ↓
    Agent 激活
       ↓
    回复
       ↓
    回合结束

其隐含语义是：

> 没有新的外界调用，就没有下一次“我”。

公共自律 AI 改成：

    有限 Contract 仍然存活
           ↓
      内部时间继续前进
           │
       ┌───┴───────────────┐
       │                   │
    内部节奏             外界变化
       │                   │
    Recheck              Adapter
       │                   │
       │                 Watch
       │                   │
       └─────────┬─────────┘
                 ↓
              Readiness
                 ↓
              Governor
                 ↓
              ONE Turn
                 ↓
        Action / Yield / Done
                 ↓
          下一次内部节奏

因此外界事件不是“电源开关”，而只是世界事实。

只要一个 Contract 仍然有效、未完成、未过期且未被更高层规则终止，系统必须保证：

    Live Contract
    ∧ not Done
    ∧ not Expired
            ↓
      Eventually Reconcile

这就是职责驱动的持续性。

---

## 2. Plugin、Skill、Agent、Trace 四层分工

整个项目的责任边界冻结为：

    Plugin = Freedom Infrastructure
    Skill  = Usage Policy
    Agent  = Situated Judgment
    Trace  = Objective Feedback

中文即：

> **插件提供自由，Skill 提供方法，Agent 根据现实做判断，Trace 提供纵向客观反馈。**

### 2.1 Plugin：自由基础设施

插件负责“能不能”和“机制是否可靠”：

- 能否保留一个有限的未来职责；
- 能否在没有新消息时再次获得判断机会；
- 能否等待世界安静后再处理；
- 能否在运行期间吸收新变化而不产生第二个并发 Agent；
- 能否主动沉默；
- 能否修改或结束自己的职责；
- 能否在预算、权限、生命周期边界内行动。

插件保证的是机制正确性，不保证具体业务目标成功。

### 2.2 Skill：自由的使用策略

Skill 负责“该不该”和“如何组合”：

- 什么任务值得建立持续职责；
- Watch 应该关注什么；
- Settle 应该多长；
- Recheck 是否必要；
- Lease 应该多长；
- 什么情况下应该 Yield；
- 什么情况下应该 Done；
- 是否应收缩、扩展或结束当前 Contract；
- 如何读取纵向 Trace 修正自己的行为。

Skill 不能绕过 Runtime，也不能拥有额外执行权。

### 2.3 Agent：情境判断

Agent 每次真正获得 Turn 后，重新读取当前世界：

    CurrentState
    + Goal
    + Wake Reasons
    + Available Tools
            ↓
          Agent
            ↓
    Action / Yield / Keep / Done

Trigger 永远不等于 Decision。

### 2.4 Trace：客观纵向反馈

Trace 不给 AI 打分，也不保存私有思维链。

它只记录：

    什么时候
    为什么醒
    做了什么类型的结果
    下一次相关变化何时发生

例如：

    10:00 Recheck → Yield
    10:03 Recheck → Yield
    10:06 Recheck → Yield
    10:09 Recheck → Yield

是否意味着 Recheck 太频繁，由 Skill + Agent 判断，而不是 Runtime 硬编码。

原则：

> **自我修正不是让模型评价自己的思维，而是让模型重新观察过去行为留下的客观时间序列。**

---

## 3. 自由模型

### 3.1 积极自由

Agent 可以：

    建立未来职责
    关注未来变化
    在世界沉默时主动回来
    等待合适时机再判断
    继续维持未完成 Goal
    修改自己的有限 Contract
    主动结束职责

当前主要由：

    AttentionProgram
    Watch
    Recheck
    WakeIntent
    Turn

承载。

### 3.2 消极自由

Agent 可以：

    Wake 后不回复
    看到事件后不立即行动
    世界仍在快速变化时继续等待
    信息不足时保留判断
    不机械执行上一次计划
    Goal 完成时退出

核心不变量：

    Event ≠ Command
    Watch ≠ Wake
    Wake ≠ Reply
    Observe ≠ Act
    Think ≠ Speak

### 3.3 Constitution：自由的外部边界

自由不能无限扩张。

宿主提供不可由 Agent 自行突破的硬边界：

    Authority
    Scope
    Max Lease
    Budget
    Concurrency
    Capacity
    Audit

因此：

    Host Constitution
            ↓
       Self Contract
            ↓
     Runtime Mechanisms

Agent 可以让自己的 Contract 更严格，但不能比 Constitution 更宽松。

---

## 4. 内生节奏与外生敏感性

公共自律 AI 有两条正交通道。

### 4.1 内生持续性

回答：

> 即使世界没有任何新变化，我什么时候仍应重新看一次？

    Contract
       ↓
    Recheck / Internal Cadence
       ↓
    Internal Ready

它提供 liveness。

### 4.2 外生敏感性

回答：

> 世界发生了什么值得进入我的注意力状态？

    World Event
       ↓
    Adapter
       ↓
    Match
       ↓
    Watch
       ↓
    Contract state changes

它提供 agility。

两者汇合：

    Internal Cadence ─────┐
                          │
    Relevant World Change ├─→ Readiness → Governor → Turn
                          │
    Turn Completion ──────┘

所以：

> **Watch 负责筛选现实，Recheck 负责持续存在，Settle 负责选择处理时机，Governor 负责实际授予 Turn。**

---

## 5. 时间主权：Settle 的高阶形式

Settle 不是简单 delay。

它表达：

> **世界值得注意，不代表现在就是适合形成判断的时刻。**

同一 AttentionKey 必须遵循单路尾缘归一化：

    Event*
      ↓
    trailing Settle
      ↓
    ONE Turn
      ↓
    Event* while running
      ↓
    trailing Settle
      ↓
    ONE Turn
      ↓
    ...

运行期间的新事件不能创建第二个 Agent，只更新尾部状态。

状态机：

    IDLE
      │ event
      ▼
    SETTLING
      │ new event → restart quiet deadline
      │ quiet N
      ▼
    RUNNING
      │ new event → mark dirty + update tail
      │ turn complete
      ├─ no dirty → IDLE / cadence
      └─ dirty    → SETTLING

下一 Turn 的最早时间遵循：

    next_due =
    max(
        turn_finished_at,
        latest_relevant_event_at + settle_seconds
    )

如果 Turn 完成时世界已经安静超过 N 秒，则可立即进入下一次 reconcile；无需机械再等一遍 N 秒。

这统一了：

    延迟回复
    消息洪峰压缩
    trailing debounce
    single-flight
    running dirty
    reconcile re-arm
    最新事件锚点

高阶行为由简单机制组合产生，不新增“延迟回复模式”。

---

## 6. 单路归一化与“一个我”

核心原则：

> **世界可以同时以很多方式提醒我，但在任意一个时间尺度里，真正醒来的始终只有一个“我”。**

同一 AttentionKey：

    多个 Program
    多个 Watch
    Recheck
    Restart
    Future adapters
         │
         ▼
      WakeIntent*
         │
         ▼
      Coalescing
         │
         ▼
       ONE Turn

运行中的新变化：

    RUNNING
       │
       ├─ event A
       ├─ event B
       └─ event C
              ↓
        dirty + tail update
              ↓
       当前 Turn 完成
              ↓
       重新走 Settle
              ↓
          ONE Turn

禁止同一主体通过不同主动来源复制多个并发“自己”。

---

## 7. Contract

当前第一种 Contract 继续使用：

    AttentionProgram
    ├─ Goal
    ├─ Watch[]
    ├─ Recheck
    └─ Lease

它表示：

> 一个尚未完成、值得未来继续重新判断的有限职责。

它不是：

    Workflow
    Reminder script
    Fixed future reply
    Business state machine

不要因为业务不同新增：

    YogaProgram
    TrackingProgram
    SupervisorProgram
    GitHubProgram
    AttendanceProgram

只要现有简单机制的组合足以表达，复杂行为就应该由组合涌现。

---

## 8. Mechanism 与 Coordinator

代码核心只允许两类角色。

### 8.1 Mechanism

一个模块只实现一种简单规律：

    Input + Local State
        ↓
    New Local State + Local Result

典型机制：

    Match
    Quantifier
    Settle
    Recheck
    Lease
    Admission
    Budget
    Suppression
    Coalescing
    SingleFlight
    TraceAppend

机制应尽量：

    无 AstrBot
    无数据库
    无网络
    无业务语义

### 8.2 Coordinator

Coordinator 只接线，不创造新的业务规则。

理想形态：

    Event / Time / TurnFact
          ↓
        Adapter
          ↓
       Mechanisms
          ↓
       Readiness
          ↓
       Governor
          ↓
       Turn Claim
          ↓
       AstrBot Agent

Coordinator 越“笨”越好。

---

## 9. Adapter

Adapter 负责把具体世界转成公共事实。

当前 QQ Adapter 可以理解：

    sender
    keyword
    any_message

未来可以增加：

    GitHub
    File
    Webhook
    Tool status
    Robot sensor

但 Adapter 没有权直接启动 Agent。

它只能产生：

    EventEnvelope
    MatchResult
    WakeIntent

最终主动权必须进入同一公共治理路径。

---

## 10. WakeIntent

WakeIntent 只表达：

> 某个 Contract 现在可能值得重新获得一次 Turn。

它不是命令。

未来所有主动来源最终统一为：

    Watch
    Recheck
    Restart
    Legacy Heartbeat
    Legacy Echo
    Legacy Sender Activation
    Future Adapter
            ↓
         WakeIntent
            ↓
         Governor
            ↓
           Turn

从此禁止新增绕过 Governor 直接启动主 Agent 的主动路径。

---

## 11. Governor

Governor 是概念层，不应写成一个万能巨型类。

它由简单机制共同构成：

    Governor
    =
    Admission
    + Authority
    + Budget
    + Suppression
    + Coalescing
    + Concurrency
    + Expiry

当前 v1.3.0-rc.1 实际只实现初步 wake normalization：

    ALLOW_NOW
    WAIT
    COALESCE

DENY 已冻结为结果类型，但 Constitution / Budget / Admission 尚未统一迁入。

---

## 12. Temporal Trace 与自我修正

当前已经实现一个 **Trace v0**：每个活跃 Program 最多保留 64 条、进程内易失的控制面事实，可由 `manage_attention_program(action=list)` 读取。

当前记录：

    timestamp
    world_signal
    wake_intent
    governor_decision
    generation
    turn_started
    turn_finished / turn_deferred
    explicit yield

不保存：

    私有思维链
    完整消息正文
    主观“好/坏”评分

Trace 只负责让过去的行为可重新观察。Skill 可以提示 Agent 纵向比较时间戳、Wake 来源、世界变化与显式 Yield，但**程序不根据固定阈值自动评价或调参**。

例如：

    连续多次 Recheck + Yield

只能作为“值得重新判断当前职责是否合理”的证据，不能在 Runtime 中写成：

    if yield_count >= N:
        auto_change_recheck()

自我修正是以下组合的涌现结果：

    主动追踪自由
        +
    主动终止自由
        +
    客观 Trace
        +
    Skill 中的案例与观察维度
        +
    主 Agent 高语言判断
        ↓
    继续 / 等待 / 行动 / Yield / 结束职责

因此不新增 SelfOptimizationEngine、AdaptiveFrequency 或自动 Contract 调参模块。

---

## 13. 高级能力必须涌现

工程原则：

> **复杂能力 ≠ 复杂模块。**

例如“长期监督群聊”不应该有 GroupSupervisor Runtime。

它应由：

    Goal
    + Watch
    + Quantifier
    + Settle
    + Recheck
    + Lease
    + Budget
    + SingleFlight
    + Trace
    + Skill
    + Agent Judgment

共同产生。

因此：

> **代码只实现简单规律，不直接实现复杂行为；复杂行为由简单规律、环境、Skill 与 Agent 共同涌现。**

---

## 14. Plugin / Skill 判定规则

任何新需求先问：

### 是新的自由基础设施吗？

例如：

    “没有新消息也能重新回来”
    → Recheck
    → Plugin

### 是已有自由的使用方法吗？

例如：

    “监督签到时 10 分钟没人报告就回来看看”
    → Skill

### 是现实数据的来源吗？

例如：

    “GitHub PR 状态变化”
    → Adapter

### 是 Agent 的语义判断吗？

例如：

    “这个 PR 变化是否值得提醒”
    → Agent

### 是客观历史事实吗？

例如：

    “过去一小时已经主动醒了 8 次”
    → Trace

只要现有机制组合能表达，就禁止新增专门 Runtime Mode。

---

## 14.1 认识论边界：代码判断状态，AI 判断意义

程序只裁定可机械验证的结构事实：

    身份
    时间
    数量
    字面 Match
    权限
    生命周期
    去重
    并发
    资源状态

任何依赖语义、价值、意图、完成度或情境的判断，都留给主 Agent：

    是否真正回答问题
    是否出现新信息
    是否值得介入
    任务是否完成
    提醒是否过度
    当前职责是否仍有意义

不变量：

    Lexical Match ≠ Meaning
    Count ≠ Importance
    Event ≠ Intent
    Trigger ≠ Decision
    Silence ≠ Completion

Match 的最高权限只是“形成注意力候选”，不能形成业务结论。

---

## 15. 验收分层

以后不能只用“场景有没有成功”判断插件正确性。

必须拆成：

    Mechanism Test
        ↓
    单一规律是否正确？

    Composition Test
        ↓
    多机制组合是否仍保持不变量？

    Host Integration Test
        ↓
    AstrBot 消息 / 历史 / Cron / Agent 是否可达？

    Skill Evaluation
        ↓
    模型是否合理使用这些自由？

    Task Evaluation
        ↓
    具体目标最终是否达成？

例如：

    三条消息启动三个并发 Agent
    → Harness bug

而：

    AI 错误判断所有人已经签到
    → Agent / Skill / World Visibility 问题

两类失败不得混为一谈。

---

## 16. 当前代码映射

v1.3.0-rc.1 已经完成的第一阶段：

### harness_core.py

纯 Python，不依赖 AstrBot / asyncio / DB / Cron。

当前承载：

    EventEnvelope
    WatchContract
    AttentionProgram
    WatchRuntime
    ProgramRuntime
    WakeIntent
    GovernorDecision
    GovernorTransition
    ReconcileClaim
    observe_watch
    due_watch_intent
    govern_wake_intents
    claim_reconcile
    restore_reconcile_claim
    needs_reconcile

### attention_program_service.py

当前仍承担：

    v2 Program KV
    rc1 Listener migration
    Event dedupe
    timer
    AttentionKey scheduler
    AstrBot Cron active_agent
    Web/Tool state
    Host preflight

它已经通过 harness_core 处理 Watch 状态、WakeIntent、dirty generation 与 reconcile claim；失败的 Turn 会恢复 claim 的 dirty 责任并经过短 backoff 重试。Watch 的 trailing Settle 在运行期间继续更新尾部 deadline，避免同一 AttentionKey 并发。

当前还提供 bounded volatile Temporal Trace，供 Skill / Agent 纵向观察控制面事实。尚未完成的是所有旧主动来源的统一入口与完整 Governor 收敛。

### 旧模块

暂时仍独立存在：

    sender activation
    heartbeat
    echo
    ignore
    access
    activation reservation

它们是迁移来源，不代表最终架构。

---

## 17. 下一阶段实施顺序

### Phase A：真实环境验证当前单路时间语义

当前 Watch reducer 已自然满足 trailing Settle：

    RUNNING 期间相关 Event
      ↓
    更新 pending tail / ready_at
      ↓
    Turn 完成
      ↓
    quiet deadline 未到 → 继续等待
    quiet deadline 已过 → 可以立即继续

本阶段不再增加第二套“延迟回复 Runtime”，而是用自动反例和真实 QQ 时间戳验证这一组合语义。

同时已经修正：

    failed/preflight-blocked Turn
      ≠
    reconciled

失败 claim 会恢复 dirty，并经短 backoff 再争取 Turn。

### Phase B：统一 WakeIntent

依次把：

    Heartbeat
    Echo
    Sender Activation

降为 WakeIntent producer / compatibility adapter。

### Phase C：统一 Governor

把现有：

    Access
    Session Gate
    Rate
    Ignore
    Activation Reservation

逐步归入公共：

    Authority
    Scope
    Budget
    Suppression
    Concurrency

不要求物理上都进入同一个文件。

### Phase D：Temporal Trace（v0 已完成，后续只按真实需求扩展）

当前已记录 bounded volatile 控制面事实，并通过 Program list 暴露。后续只有在真实环境证明缺少某类客观事实时才扩展；禁止把主观评分或业务结论塞进 Trace。

### Phase E：Skill 重构（第一轮已完成）

当前 Skill 已加入：

    代码判断状态 / AI 判断意义
    trailing Settle 的真实语义
    Trace 纵向观察案例
    自我修正属于涌现行为

真实测试后再根据误用案例补 Skill，不把案例反写成 Runtime 规则。

### Phase F：删除重复 Runtime

只有在旧主动来源全部经过同一公共路径后，才删除 Heartbeat/Echo/Listener 等重复执行逻辑。

---

## 18. 最终工程不变量

所有未来 PR 都必须服从：

> **Agent 不由事件驱动存在，而由职责驱动持续存在。**

> **外界事件只改变世界，不直接命令 Agent。**

> **Live Contract 必须保有未来重新判断的可达性。**

> **Agent 有自己的内部节奏，外界变化只能调整节奏，不能牵引出无限并发。**

> **同一 AttentionKey 在任何时刻只允许一个真正运行的“我”。**

> **运行期间的新变化必须回到同一 trailing Settle 规则，而不是直接生成第二个 Turn。**

> **插件提供自由，不替 Agent 使用自由。**

> **Skill 提供策略，不拥有 Runtime 权力。**

> **Trace 提供事实，不替 Agent 给自己评分。**

> **Agent 做语义判断，程序只执行确定性规律。**

> **复杂能力必须由简单机制组合涌现。**

> **能由现有机制组合表达的高级行为，禁止新增专门 Mode。**

最终模型：

    Freedom Infrastructure
            ×
       Usage Skills
            ×
     Situated Judgment
            ×
      World Observability
            ×
     Longitudinal Feedback
            ↓
      Emergent Capability
