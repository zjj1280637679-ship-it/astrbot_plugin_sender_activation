# 公共自律 Harness：v1.3.0-rc.1 初版

状态：初版实现候选。

## 1. 主工程立意

本插件的最高层定义不再是“主动监听插件”，而是：

> 建立在 AstrBot 主 Agent 之上的公共自律 Harness。

AstrBot 继续负责消息、历史、工具、Provider、Cron 与真正的 Agent 执行。Harness 不替 Agent 理解业务，也不建立第二套 Agent；它只治理：

- Agent 为未来自己留下什么有限职责；
- 什么变化值得重新获得一个 Turn；
- 多个唤醒请求如何合并；
- 同一控制主体如何避免并发出多个“自己”；
- 什么时候允许沉默、继续或结束职责；
- 后续怎样插入 Budget、Audit 和更统一的 Authority。

一句话边界：

> Harness 管未来行动权的治理；Agent 管醒来以后如何理解世界和做决定。

## 2. 三层模型

    Contract
    “未来的我承担什么有限职责”
            │
      世界变化 / 时间
            │
            ▼
    Governor
    “这些未来行动请求如何被约束和合并”
            │
            ▼
    Turn
    “AstrBot 主 Agent 获得一次重新判断机会”
            │
            ├─ Action
            ├─ Yield
            ├─ Keep
            └─ Done

当前第一种 Contract 继续使用 rc2 已有的 AttentionProgram：

    AttentionProgram
    ├─ Goal
    ├─ Watch[]
    ├─ Recheck
    └─ Lease

不新增 SelfDisciplineProgram 等同义实体。

## 3. v1.3 初版代码边界

### 3.1 harness_core.py

纯 Python 核心，不导入 AstrBot、不使用 asyncio、不读写数据库、不创建任务。

当前包含：

- EventEnvelope
- WatchContract
- AttentionProgram
- WatchRuntime
- ProgramRuntime
- WakeIntent
- GovernorDecision
- GovernorTransition
- ReconcileClaim
- observe_watch()
- due_watch_intent()
- recheck_intent()
- restart_intent()
- govern_wake_intents()
- claim_reconcile()
- needs_reconcile()

纯状态流：

    Event
      ↓
    observe_watch
      ↓
    WatchRuntime
      ↓
    WakeIntent[]
      ↓
    govern_wake_intents
      ↓
    ProgramRuntime DIRTY
      ↓
    claim_reconcile
      ↓
    ReconcileClaim

### 3.2 attention_program_service.py

仍是 AstrBot 适配 / Runtime 层：

- v2 Program KV；
- rc1 Listener 迁移；
- Event 去重缓存；
- asyncio settle / recheck timer；
- AttentionKey 调度；
- AstrBot Cron active_agent；
- Program Web/Tool 状态；
- Host preflight。

它现在不再自己手写 Watch/dirty 状态突变，而是调用 harness_core.py reducer。

### 3.3 旧模块

初版暂不一次性重写：

- sender activation
- heartbeat
- echo
- ignore
- access
- activation reservation

这些仍按原路径工作。

后续目标是逐个变成：

    Legacy source
          ↓
    WakeIntent Adapter
          ↓
    Governor
          ↓
    Turn

只有完成统一入口以后，才删除它们内部重复的主动调度逻辑。

## 4. 初版 Governor 的真实能力

当前 Governor 只承担最小公共规则：

1. 一批 WakeIntent 必须属于同一个 Contract。
2. 多个 Watch/Recheck 原因合并成一个 dirty generation。
3. event refs 去重并只保留最近的有限集合。
4. Contract 已经 dirty 时，新 Intent 只做 coalesce。
5. AttentionKey 正在运行时，新 Intent 只做 coalesce，不要求并发 Turn。
6. claim_reconcile() 将当前 dirty generation 移入 processing。
7. processing 期间再次 dirty，完成后 needs_reconcile() 仍为真，因此只再运行一次。

当前决策枚举已经冻结：

    ALLOW_NOW
    WAIT
    COALESCE
    DENY

v1.3 初版真正使用：

- ALLOW_NOW
- WAIT
- COALESCE

DENY 预留给后续 Budget / Constitution / Admission；初版不伪装成已经实现。

## 5. 仍由现有层负责的自律能力

当前代码中已经存在、但尚未统一进入新 Governor 的能力：

    Authority       → AccessService
    Scope           → SessionGate
    Suppression     → AttentionIgnore
    Rate            → legacy activation rate
    Concurrency     → ActivationReservation + Program AttentionKey
    Yield           → yield_current_turn
    Expiry          → Program Lease / old lease systems

因此 v1.3 的目标不是“今天把一切重写成 Governor”，而是先建立唯一可复用内核，再逐步迁移这些能力。

## 6. 初版不变量

自动测试应保护：

    Event ≠ Command
    Wake ≠ Reply
    多 WakeIntent ≠ 多 Agent
    同一个 reduction 不能跨 Contract
    一个 AttentionKey 不并发两个 Program Turn
    running 期间 dirty 不丢失
    一个 Program 多 Watch 可以合并为一代 dirty
    纯 core 不依赖 AstrBot
    rc2 Program 存储格式不变
    rc1 Listener 迁移仍有效

## 7. 下一阶段顺序

### Phase A：统一 WakeIntent

把旧 Heartbeat、Echo、Sender Activation 逐个变成 WakeIntent producer，先不删兼容 Tool。

### Phase B：Governor 强化

按顺序加入：

    Constitution
    ├─ Authority ceiling
    ├─ Scope ceiling
    ├─ Max Lease
    ├─ Global concurrency
    └─ Budget

AI 可以建立更严格 Contract，但不能突破 Constitution。

### Phase C：Turn Receipt / Audit

只记录控制面事实：

    who
    contract_id
    wake_reason
    governor_decision
    generation
    turn_started
    turn_finished
    outcome

不记录私有思维链，不复制完整群消息正文。

### Phase D：删除重复 Runtime

当旧主动来源都进入 Governor 后，再删除：

- rc1 Listener Runtime；
- 独立 Heartbeat/Echo 主动调度重复部分；
- 可被 Governor 吸收的重复 reservation / rate glue。

## 8. 当前判断

v1.3 初版有意只做一件事：

> 把“公共自律 Harness”从项目口号变成一个可以脱离 AstrBot 单独测试的状态内核，并让真实 AttentionProgram 路径开始使用它。

这使后续每次迁移都有统一目标，而不需要再增加新的平级主动机制。
