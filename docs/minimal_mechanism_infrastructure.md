# 最小机制基础设施审计

> 目标：不是继续发明“注意力功能”，而是找出为了兑现插件核心效果，**最少必须由插件自己提供的机制**。凡 AstrBot 已经成熟提供的能力，原则上复用；凡可由现有机制组合得到的效果，原则上放回 Skill 语义层而不是新增 Runtime。

## 0. 产品核重新定义

插件的核心不是“Attention Runtime 功能集合”，而是：

> **让 AstrBot 主对话 AI 能够管理额外的唤醒条件。**

核心只包含两类条件：

1. **对象条件：某个 ID 的普通消息可以临时唤醒主 Agent。**
2. **时间条件：某个未来时点或周期可以唤醒主 Agent。**

再加一条共同要求：

3. **上述条件应成为主对话 AI 可调用的 Tool，而不是只能由人手工设置。**

这三件事之外的追踪、心跳、防刷屏、Echo、沉默等首先按“效果”处理，不自动升级为独立机制。

---

## 1. 上游事实：Time → Activate 已经由 AstrBot 完成

AstrBot 4.27.2 已内置 `future_task` Tool，并且默认配置：

```text
provider_settings.proactive_capability.add_cron_tools = true
```

`future_task` 已支持：

- `create`
- `edit`
- `delete`
- `list`
- recurring cron
- one-shot `run_at`

其 `create` 直接调用 AstrBot 原生 `CronJobManager.add_active_job()`；原生 Cron 到时后直接构建主 Agent、加载当前会话历史，并运行 Agent。

因此：

```text
Time
  ↓
future_task Tool
  ↓
CronJobManager.add_active_job
  ↓
AstrBot 主 Agent
```

**已经是一条完整的上游主机制。**

结论：插件不应把 `HeartbeatService + HeartbeatWakeGate + CronAdapter + manage_heartbeat_lease` 当成第二套核心时间机制。它们最多是“有限期/权限/兼容性包装”，必须单独证明包装价值，否则应退出核心层。

---

## 2. 上游事实：ID → Activate 只缺一个很薄的桥

AstrBot 4.27.2 的 `WakingCheckStage` 已经支持：

- 原生 @ / 引用 / wake prefix；
- **任意插件 handler filter 通过也会令事件进入 wake 状态。**

其后 `ProcessStage` 会先执行被激活的插件 handler；如果 handler 把：

```python
event.is_at_or_wake_command = True
```

置为真，AstrBot 随后的默认 Agent 路径就会直接运行主 Agent。

因此对象激活最小链路是：

```text
普通群消息
   ↓
读取 sender_id
   ↓
(scope, sender_id) 是否有有效激活租约？
   ↓ yes
CustomFilter 通过
   ↓
极薄 handler：event.is_at_or_wake_command = True
   ↓
AstrBot ProcessStage
   ↓
原生主 Agent
```

插件不需要：

- 自建 Agent；
- 自建对话上下文；
- 自建消息检索；
- 自建路由器；
- 自建语义分类器。

真正新增的核心机制只是：

> **一个“ID → AstrBot 原生唤醒条件”的有限状态映射。**

---

## 3. 最小核心实现

### 3.1 必要状态

核心状态可以抽象到：

```text
(scope, sender_id) -> expires_at
```

可选附加元数据：

```text
created_by
created_at
```

但这些不是决定是否唤醒的必要变量。

热路径只需要：

```text
exact key lookup
+
expiry check
```

不需要全局扫描，不需要语义判断。

### 3.2 必要对象 Tool

核心自研 Tool 只需要：

```text
manage_sender_activation
```

最小动作：

```text
enable / renew / disable / list
```

最小参数：

```text
target_ids
duration_seconds
```

主 AI 负责根据 Skill 和完整语境判断“为什么要关注这个 ID”；Runtime 只判断：

- ID 是否合法；
- 调用是否有权限；
- 租期是否在允许范围；
- 状态是否成功写入。

### 3.3 时间 Tool

核心不新造时间 Tool。

直接使用 AstrBot：

```text
future_task
```

Skill 负责让 AI 知道：

- 对象触发 → `manage_sender_activation`
- 时间触发 → `future_task`

这比再给 AI 一个语义重复的 `manage_heartbeat_lease` 更薄，也减少 Tool 选择歧义。

---

## 4. 主干图

```text
                     主对话 AI
                         │
          Skill：解释两类“额外唤醒条件”
                         │
             ┌───────────┴───────────┐
             │                       │
      manage_sender_activation    future_task
             │                       │
             ▼                       ▼
      ID Lease Registry        AstrBot Cron
             │                       │
             ▼                       │
      CustomFilter / wake bridge     │
             │                       │
             └───────────┬───────────┘
                         ▼
                 AstrBot 主 Agent
```

这是**机制图**，不是 AI 判断流程图。

AI 为什么选择左边或右边，由完整上下文 + Skill 词关系决定，不写成插件 if/else。

---

## 5. 当前 rc19 模块按“必要性”重新分类

### A. 核心机制：保留概念

| 当前内容 | 最小机制中的身份 | 判断 |
|---|---|---|
| `SenderActivationFilter` | ID 精确匹配 → 唤醒桥 | 核心 |
| `manage_sender_activation` | AI 管理 ID 激活条件 | 核心 |
| 对象激活有限租约 | `(scope,id)->expires_at` | 核心 |
| 状态持久化 | 重启后仍知道有效租约 | 生产核心/可靠性 |
| 真实工具回执 | AI 不得把未执行状态当事实 | 生产核心/认识论 |

### B. 核心安全/可靠性，但不是产品功能

| 当前内容 | 身份 |
|---|---|
| 状态写锁 / 原子 mutation | 并发可靠性 |
| session/plugin enable 检查 | 宿主生命周期兼容 |
| 参数边界和容量 | 防失控 |
| fail-inert / 明确 indeterminate | 真值保护 |

这些可以保留，但不能反过来决定产品架构。

### C. AstrBot 已有机制：优先退出自研核心

| 当前内容 | AstrBot 已有替代 | 初步结论 |
|---|---|---|
| `manage_heartbeat_lease` | `future_task` | 降级为包装候选 |
| `HeartbeatService` | `CronJobManager` | 非核心 |
| `HeartbeatWakeGate` | 原生 ActiveAgent Cron | 仅在包装确有额外安全语义时保留 |
| `CronAdapter` | 原生 Cron API | 非核心 |

### D. 表面效果 / 边缘策略

| 当前内容 | 更准确的身份 |
|---|---|
| `manage_sender_activation_rate` | ID 激活的资源保护策略 |
| `manage_attention_ignore` | 反向抑制效果 |
| `AttentionGuardFilter` | Ignore 的执行旁路 |
| `manage_echo_hook` / `EchoService` | 有限再检查效果；与 one-shot time activation 高度重叠 |
| `yield_current_turn` | 对象额外唤醒后的输出控制辅助语义 |
| `manage_sender_activation_access` | 自定义委托权限策略；AstrBot 原生 Tool 权限之上的扩展 |
| Web 状态页 / diagnostics | 运维层 |
| recovery report | 故障恢复硬化 |

这些不能与 ID/Time 主机制等权。

---

## 6. 删除/降级判定法

以后任何模块进入 Runtime 前，必须通过四问：

1. **删除它后，ID→Activate 或 Time→Activate 是否仍成立？**
2. **AstrBot 是否已经提供同一机制？**
3. **它解决的是机制缺口，还是某个效果/安全偏好？**
4. **它能否只靠 Skill + 现有 Tool 组合得到？**

判定：

```text
删除后主机制不成立
→ 核心机制

主机制仍成立，但安全/真值无法保证
→ 核心可靠性

AstrBot 已有
→ 复用上游

只改善特殊语境
→ 边缘能力

Skill + 现有机制可组合
→ 不新增 Runtime
```

---

## 7. 对现有功能的重新解释

### 7.1 “追踪某人”

不是独立机制：

```text
ID + duration → Activate
```

### 7.2 “心跳”

不是插件核心机制：

```text
future_task(recurring) → Activate
```

### 7.3 “某时再检查一次”

机制上首先看作：

```text
future_task(run_once) → Activate
```

只有在“自动取消旧检查、特殊归属、非递归”等语义被证明无法由上游机制 + Skill 表达时，才证明需要 Echo 包装；否则 Echo 不升级为独立核心机制。

### 7.4 “防刷屏 / Ignore”

不是激活机制，而是资源保护旁路。即使完全移除，插件仍然能兑现核心产品承诺。

### 7.5 “沉默”

不是新的注意力机制，而是 Agent 已获得判断机会后的输出结果。`yield_current_turn` 是否需要自研，要单独检查 AstrBot 是否存在稳定的原生无可见输出接口；在未找到原生等价物前，可保留为很薄的辅助能力，但不得与两条主机制并列。

---

## 8. 最小 Skill 结构

Skill 不再介绍“七个并列功能”，而首先只教两个词关系：

```text
某个对象未来普通消息需要成为唤醒条件
→ manage_sender_activation

某个未来时间/周期需要成为唤醒条件
→ AstrBot future_task
```

然后再补一个总原则：

```text
唤醒 ≠ 回复
```

边缘能力只在对应语境出现时按需说明，不进入 Skill 第一层主干。

---

## 9. 最小验证集

### Object 主机制

必须证明：

1. 无租约普通消息不唤醒；
2. 有租约的精确 ID 普通消息唤醒；
3. 其他 ID 不受影响；
4. 到期立即恢复原生行为；
5. disable 后立即恢复原生行为；
6. 重启后未到期租约仍正确；
7. Tool 回执与真实状态一致。

### Time 主机制

不重复测试 AstrBot Cron 内部实现，只做上游契约验证：

1. `future_task` 在目标 AstrBot 版本存在；
2. 默认配置启用 cron tools；
3. 支持 recurring 和 run_once；
4. 能进入原生主 Agent；
5. 当前 Skill 能正确把时间需求路由到 `future_task` 而不是对象 Tool。

### 语义测试

只验证 AI Tool 选择：

```text
“接下来两小时关注 QQ 123 的消息”
→ manage_sender_activation

“30 分钟后提醒/检查一次”
→ future_task

“每 10 分钟检查一次”
→ future_task

“只回答现在这句话”
→ 不建立任何未来唤醒条件
```

---

## 10. 当前最重要结论

如果以 AstrBot 4.27.2 为宿主，插件的最小主体很可能不是“七 Tool Attention Runtime”，而是：

```text
一个自研机制：
ID → 有限原生唤醒

一个自研 Tool：
AI 管理 ID 激活租约

一个上游机制 + 上游 Tool：
Time → future_task → 原生主动 Agent

一个 Skill：
把高语境需求正确映射到对象条件或时间条件
```

下一步不应立刻删除 rc19 已有代码，而应建立一个 **thin-core 对照实现/测试**，与 rc19 在相同核心任务上比较：

- 可达性；
- 工作步数；
- Tool 选择准确率；
- 代码/配置/状态复杂度；
- 故障面；
- 与 AstrBot 上游重复率。

只有 thin-core 通过核心等价验证后，才决定哪些 rc19 边缘模块真正删除、降级或外置。
