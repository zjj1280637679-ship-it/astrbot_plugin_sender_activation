# RC20 Attention Runtime Epistemic Model

## Purpose

rc19 完成了 Attention Runtime 的工程闭环，但事件到策略之间仍存在压缩层：

`Event → Policy`

rc20 不增加更多 Hook，而先补齐中间的认识层，避免效果能力替代机制。

## Core Pipeline

```
Observation
    ↓
Interpretation
    ↓
Belief
    ↓
Attention Decision
    ↓
Policy
    ↓
Action
```

## Layer Definitions

### Observation

客观可记录事实。

Examples:

- 用户发送了一条消息
- Cron 到达时间点
- 某个 Tool 返回成功/失败

Observation 不包含价值判断。

---

### Interpretation

对 Observation 的解释。

Examples:

- 高频消息可能代表刷屏
- 某次失败可能代表权限不足

Interpretation 可以错误，因此不能直接等同 Policy。

---

### Belief

系统当前相信的状态模型。

Examples:

- 用户可能正在持续刷屏
- 当前 Echo 没有必要继续

Belief 必须允许更新和撤销。

---

### Attention Decision

决定是否值得消耗 Agent 注意力。

不是回复决定。

Attention Decision 输出：

- activate candidate
- ignore candidate
- defer candidate
- no-op

---

### Policy

执行权限与规则。

例如 rc19：

- Ignore lease
- Echo hook
- Heartbeat gate

Policy 不负责解释世界，只负责执行授权。

---

### Action

实际副作用：

- Tool call
- Reply
- Yield
- Schedule

---

## External Analogies

### OS Scheduler

Observation 类似任务到达。

Attention Decision 类似调度选择。

Policy 类似资源限制。

---

### Kubernetes Controller

Observation → Desired State → Reconcile → Actual State。

Attention Runtime 应避免直接从事件跳到动作。

---

### Notification Pipeline

Message → Classification → Priority → Delivery。

AI 注意力系统需要类似分层。

---

## Failure Attribution

所有失败必须分类：

1. Interface failure

工具、API、宿主接口错误。

2. State failure

状态没有正确保存或恢复。

3. Cognitive failure

解释或判断错误。

4. Theory failure

模型本身无法解释现实。

## RC20 Constraint

不推翻 rc19 Execution Layer。

新增内容必须满足：

```
Configuration
    ↓
Implementation
    ↓
Test
    ↓
Attribution
```
