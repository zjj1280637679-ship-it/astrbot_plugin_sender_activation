# 效果推导矩阵：用最小机制判断是否真的需要新 Runtime

## 0. 目的

本文件不按“功能名称”扩展架构，而是先定义最小机制原语，再检查各种表面效果能否由原语组合得到。

原则：

> **能由已有原语组合得到的效果，不新增底层机制。只有无法组合、且需求真实存在时，才认定为机制缺口。**

---

## 1. 当前最小原语

### P1. Object / ID 条件

```text
Object(scope, sender_id)
```

表示“某个确定对象的事件”。

插件真正需要自研的是：把这个条件临时接入 AstrBot 原生 wake。

### P2. Time 条件

```text
Time(run_at | cron_expression)
```

AstrBot 4.27.2 已由 `future_task` + `CronJobManager` 原生提供。

### P3. Activate 效果

```text
Activate -> AstrBot native main Agent gets a turn
```

不是 Reply。

### P4. Lease / 有限有效期

```text
Lease(condition/effect, expires_at)
```

用于把对象条件等状态限制在有限期。

对象激活需要插件自己的有限租约；AstrBot one-shot Time 天然有限，recurring Time 是否需要额外自动过期属于安全包装而非 Time 机制本身。

### P5. Tool 配置入口

```text
AI -> Tool -> deterministic state mutation
```

主 AI 负责高语境判断；程序只执行确定状态变更。

---

## 2. 非核心但可复用的修饰符

这些不是当前产品主干成立所必需，但可以修饰核心机制：

### M1. Count

```text
Count(N, window)
```

描述次数条件或资源限制。

### M2. Ignore

```text
Ignore
```

与 Activate 方向相反：原本可达的事件暂时不进入 Agent。

### M3. Rate

```text
Rate(max, window)
```

控制 Activate 的频率，不产生新的激活来源。

### M4. Yield

```text
Yield
```

Agent 已获得回合后，不产生可见输出。

它发生在 Activate 之后，不是唤醒机制。

---

## 3. 表面效果推导

| 表面效果 | 最小推导 | 是否需要新机制 |
|---|---|---|
| 关注/追踪某 QQ | `Object(ID) + Lease + Activate` | 否，正是对象核心 |
| 某时提醒/再检查 | `Time(run_at) + Activate` | 否，AstrBot `future_task` 已有 |
| 周期心跳 | `Time(cron) + Activate` | 否，AstrBot `future_task` 已有 |
| 延迟处理当前任务 | `Time(run_at) + note/context + Activate` | 否 |
| Echo 一次 | `Time(run_at) + Activate` | 核心效果不需要；特殊取消/归属语义另审 |
| Echo 多次有限检查 | 多个 `Time(run_at_i) + Activate` 或 recurring Time | 核心效果不需要；禁止递归是策略约束 |
| 防刷屏 | `Object + Count(window) + Ignore` | 需要 Ignore 修饰符，但不是核心激活机制 |
| 限制追踪对象唤醒频率 | `Object + Rate + Activate` | Rate 修饰符，非新激活机制 |
| 暂时压制仍被追踪对象 | `Object+Lease+Activate` 与较短 `Object+Lease+Ignore` 叠加 | Ignore 扩展，不是新主干 |
| 激活后不说话 | `Activate -> Yield` | 输出控制，不是激活机制 |
| 取消对象追踪 | 删除/到期 `Object Lease` | 否 |
| 取消定时任务 | `future_task(delete)` | 否 |
| 续期对象追踪 | 更新 `Object Lease.expires_at` | 否 |
| 修改时间任务 | `future_task(edit)` | 否 |
| 查看时间任务 | `future_task(list)` | 否 |
| 查看对象追踪 | `manage_sender_activation(list)` | 否 |
| 有限操作员授权 | AstrBot Tool Permission + 可选 Lease wrapper | 治理扩展，不是核心 |
| 全量监听所有消息并理解 | 全事件 Observation/Perception | **是潜在新机制**，当前不实现 |
| 任意消息语义分类后决定是否唤醒 | Perception + semantic classifier | **是新机制，但与当前分工冲突，暂不实现** |
| 长期自主目标循环 | autonomous planner/lifecycle | **是新机制，且不属于当前插件产品核** |

---

## 4. Echo 的重新判定

“Echo”这个名称容易把一个效果误认为机制。

### 核心 Echo 效果

```text
现在被真实激活
+
未来某时再让 Agent 获得一次判断机会
```

等价于：

```text
future_task(run_once)
```

所以**再检查一次本身不证明需要 `EchoService`**。

### 可能真正额外的 Echo 语义

当前自研 Echo 还附带：

- 只能从特定真实激活来源创建；
- Echo 回合不能创建 Echo；
- 新的真实激活可自动取消旧 Echo；
- scope/pending capacity；
- 特殊归属和清理。

这些都属于“对一次性 Time Activate 的治理策略”。

必须逐项问：

1. 这是用户真正需要的产品语义吗？
2. Skill 能否通过 `future_task create/delete` 表达？
3. 若不能，最小缺口是一个小 wrapper，还是完整 `EchoService`？

在此验证完成前，Echo 不作为核心机制。

---

## 5. Heartbeat 的重新判定

“Heartbeat”同样是效果名。

核心语义：

```text
周期时间条件成立 -> 主 Agent 获得判断机会
```

即：

```text
future_task(cron)
```

当前 `HeartbeatService` 相比原生 `future_task` 新增的主要价值来自：

- 有限总租期；
- plugin/session gate；
- 自定义 operator 授权；
- 自定义状态页；
- 失败真值包装。

这些要被拆开分别归因，不能整体作为“Heartbeat 机制”保留。

如果需求只是“每 10 分钟看一次”，原生机制已经闭包。

---

## 6. Access 的重新判定

AstrBot 4.27.2 对非内置插件 Tool 已有统一权限模型：

```text
member (default)
admin (WebUI 可配置)
```

因此基础问题：

```text
谁可以让 AI 调用对象激活 Tool？
```

已经有上游机制。

当前 `AccessService` 提供的是：

```text
在 AstrBot member/admin 二元权限之上
增加“某群某 QQ 的有限期委托”
```

这是治理产品，不是 ID→Activate 的成立条件。

结论：

- 原生 Tool permission：核心复用；
- finite delegate access：边缘治理扩展。

---

## 7. 机制缺口判定协议

面对一个新需求 `E`：

### Step 1：效果化

先把产品语言翻译成：

```text
谁/什么时候
+
什么效果
+
持续多久
```

### Step 2：原语分解

尝试用：

```text
Object
Time
Activate
Lease
Tool
(+ optional modifiers)
```

表达。

### Step 3：上游消元

检查 AstrBot 是否已经提供该原语或组合。

### Step 4：反例验证

如果声称“现有机制不够”，必须给一个具体反例：

```text
需求成立
AND
所有现有原语组合都无法实现
```

没有这个反例，不进入 Runtime 开发。

### Step 5：最小缺口实现

即使证明有缺口，也只实现缺失的最小操作符，不实现整个效果名称对应的独立子系统。

---

## 8. 当前核心闭包

在 AstrBot 4.27.2 上，最小主干已经可以闭包绝大多数产品需求：

```text
                 AI + Skill
                    |
          +---------+---------+
          |                   |
 Object(ID)+Lease          Time
          |                   |
 manage_sender_activation   future_task
          |                   |
 wake bridge             native Cron
          +---------+---------+
                    |
             AstrBot Agent
```

目前唯一明确必须由插件新增的主体能力是：

> **Object(ID) + finite lease → AstrBot native wake**

Time 侧已经是上游能力。

因此下一轮工程验证应围绕一个问题：

> 如果只保留对象租约桥 + 一个对象 Tool + Skill 对 `future_task` 的正确使用，是否已经覆盖插件 80%~90% 的真实价值？

如果答案为是，rc20 的主要工作应是**减法和层级重构**，而不是功能扩展。
