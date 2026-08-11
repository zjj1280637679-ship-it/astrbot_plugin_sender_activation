# Agent Attention Runtime：能力可达性与副作用基线

本文件冻结 `1.1.0-rc.19` 候选的**实际可达能力、权限来源、运行效果与已知副作用**。后续版本新增能力时可以扩展此表，但不得把尚未实现的概念写成已实现能力，也不得在未更新对应测试的情况下改变这些效果。

## 1. 总效果图

```text
外部群事件 / 本插件主动调度
            |
            v
      Session / Scope Gate
            |
            v
   Object Attention Guard
      |             |
   Ignore          Pass
      |             |
  LLM 前抑制        v
              Activation Sources
          native @ / object / heartbeat / echo
                    |
                    v
                 Agent
          +---------+---------+
          |         |         |
        Reply      Tool      Yield
                    |
          +---------+---------+
          |                   |
    Policy Control         Optional Echo Hook
```

核心语义不变量：

- **激活 ≠ 回复**：激活只增加一次主 Agent 可达机会。
- **忽略 ≠ 删除追踪**：短期 Ignore 可以覆盖已有对象激活，但不销毁原激活租约。
- **沉默 ≠ 无控制行为**：Agent 可以先执行控制面工具，再在后续最终工具选择中 `yield_current_turn`。
- **Echo ≠ 无限主动 Agent**：Echo 必须显式创建、次数有限、由原始激活一次性预排，Echo 回合结构上禁止再创建 Echo。
- **模型能力 ≠ 权限**：是否开放 Ignore / Echo 等能力由插件配置决定，程序不按模型名称自动提权。
- **取消 ≠ 追回已启动 LLM**：一旦 AstrBot 原生执行已经跨过 `run_job_now()` 边界，当前在途一次可能完成；取消只阻止未来尚未开始的激活。

## 2. 已实现能力可达性矩阵

| 能力 | 默认权限 / 配置入口 | Agent / 事件入口 | 运行路径 | 回归证据 | 主要副作用边界 |
|---|---|---|---|---|---|
| 对象有限激活 | 既有管理员/操作员权限与租约限制 | `manage_sender_activation` | sender event → activation reservation → service decision → native Agent wake | 既有 rc17/rc18 回归 + `tests/test_astrbot_runtime.py` | 不保证回复；只扩展指定对象在指定作用域的有限可达性 |
| 对象激活限频 | 既有配置与从属租约 | `manage_sender_activation_rate` | tracked-object activation → rate lease | 既有回归 + 宿主集成 | 只约束本插件新增的对象激活，不等于原生 @ 限频 |
| 时间有限激活 / Heartbeat | 既有管理员/操作员权限 | `manage_heartbeat_lease` | disabled active template → basic preflight driver → SessionGate → `run_job_now()` | `tests/test_heartbeat_gate.py`, `tests/test_heartbeat_truth.py`, `tests/test_astrbot_runtime.py` | Cron 只是执行器，不是主动权限来源；Session 禁用后在 LLM 前抑制 |
| 对象有限 Ignore | `agent_ignore_enabled=false` 默认关闭 | `manage_attention_ignore(action=set)` | persistent policy → exact `(scope,target)` hot index → pre-LLM guard | `tests/test_attention_runtime_core.py`, `tests/test_astrbot_runtime.py` | 存储异常 fail-open；不提供永久对象封禁 |
| 对象 + Count + Ignore | 同上；`ignore_trigger_*` 控制阈值 | `manage_attention_ignore` | exact object policy → in-memory counter → threshold event itself suppressed | `tests/test_attention_runtime_core.py` | `window=0` 使用 O(1) 累计整数，避免按事件存时间戳造成内存放大 |
| 对象 + Time-window + Count + Ignore | 同上 | `manage_attention_ignore` | exact object policy → sliding timestamp deque → threshold → finite Ignore lease | `tests/test_attention_runtime_core.py` | 只统计本来可能激活 Agent 的事件；窗口到期不继续累计旧事件 |
| Ignore 命中原生唤醒 | `ignore_native_wake_mode=extra_only` 默认 | `AttentionGuardFilter` | `extra_only` / `suppress_llm` / `stop_event` | `tests/test_astrbot_runtime.py` | `extra_only` 最保守；`stop_event` 会阻断后续插件，副作用最大 |
| 有限 Echo | `echo_enabled=false` 默认关闭；`echo_*` 控制来源/次数/容量 | `manage_echo_hook(action=create)` | real activation → explicit hook → disabled one-shot active job → local timer → preflight → `run_job_now()` | `tests/test_attention_runtime_core.py`, `tests/test_concurrency_boundary.py`, `tests/test_astrbot_runtime.py` | 无显式 Hook 就无 Echo；重启不复活旧 Echo；删除失败不谎报成功 |
| 多次有限 Echo | `echo_default_count=1`，受 `echo_max_count` 限制 | 同上 | 原始激活回合一次性预排 N 个 one-shot jobs | `tests/test_attention_runtime_core.py` | N 次不是 N 层递归权限；任何 Echo 回合都不能创建下一层 Echo |
| 结构化沉默 | 插件主动回合可用 | `yield_current_turn` | sender activation / heartbeat / echo Agent turn → terminate return | 语义反例 + `tests/test_astrbot_runtime.py` | 只抑制公开输出，不撤销此前已执行的外部动作 |
| 操作员授权 | AstrBot admin 控制 | `manage_sender_activation_access` | current group access state | 既有 rc17/rc18 回归 | 不授予 AstrBot 管理员身份，不跨群 |

## 3. Echo 来源权限

当前配置把“来源是否允许创建 Echo”和“是否自动创建 Echo”严格分开：

| 来源 | 默认 | 含义 |
|---|---:|---|
| `native_wake` | 允许 | 原生 @/引用/唤醒回合**可以选择**调用 Echo 工具 |
| `sender_activation` | 允许 | 被追踪对象触发的额外回合**可以选择**调用 Echo 工具 |
| `heartbeat` | 禁止 | 默认不允许周期主动激活进一步放大成 Echo；部署者可显式开放 |
| `echo` | 结构禁止 | 无论配置如何，Echo 回合都不能创建新的 Echo |

“允许来源”只表示工具可达，不表示系统自动挂 Echo。

## 4. 失败方向与副作用原则

### Ignore

- 热路径无策略时应尽快 inert return，不扫描其他对象规则。
- 状态写入 `indeterminate` 时前置过滤转为 **fail-open**，避免旧状态误伤正常群聊。
- 运行计数只在内存中；插件重启后计数清零，宁可少挡一次也不保留陈旧封锁。

### Echo / Heartbeat

- SessionGate 在主 Agent 激活之前检查。
- 旧 driver 无法确认删除时 Heartbeat **fail-closed**：宁可暂时没有心跳，也不创建第二个 driver 导致重复 LLM 唤醒。
- Echo 原生 job 删除失败时，本地 timer 先停止；残留 disabled row 不应继续主动执行，同时回执必须报告 partial / failure。
- 跨过原生 `run_job_now()` 执行边界之后，当前在途 Agent turn 无法追回。

### 原生 @ Ignore 强度

- `extra_only`：最低副作用，只阻断本插件额外激活；原生 @ 仍可进入 Agent。
- `suppress_llm`：取消 AstrBot 默认 LLM 唤醒，但继续允许其他插件处理当前事件。
- `stop_event`：停止整个后续事件传播，最省资源，同时对其他插件副作用最大。

部署者选择成本/公平性边界，程序不替用户自动升级强度。

## 5. 当前未实现能力

以下概念属于后续 Attention Runtime 方向，**rc19 不宣称已经实现**：

- 全消息监听 / Perception 层；
- 通用 `Object / Time / Count` 条件 DSL；
- time-only Ignore；
- 通用 Count Activate；
- 任意事件源的自主追踪；
- 无限或递归 Echo；
- 按模型名称自动授予管理员级主动权限。

## 6. 测试与 CI 基线

长期测试资产位于 `tests/`，不再按 rc 版本复制命名：

- `tests/test_attention_runtime_core.py`：Ignore / Echo 核心不变量；
- `tests/test_heartbeat_gate.py`：Heartbeat preflight 与 stale driver；
- `tests/test_heartbeat_truth.py`：Heartbeat 状态真值与部分提交；
- `tests/test_concurrency_boundary.py`：`run_job_now()` 前后取消的确定性边界；
- `tests/test_astrbot_runtime.py`：AstrBot 宿主级注册、Schema、Cron 与事件语义；
- `tests/test_agent_semantic_counterexamples.py`：可选真实模型语义反例，需要显式 `ARK_API_KEY`，不属于默认 CI。

CI 分层：

- `quick-check.yml`：低成本静态与纯运行时不变量；
- `integration-check.yml`：需要 AstrBot 宿主的集成测试；
- `release-audit.yml`：PR 从 draft 进入 ready 后或 ready 状态继续变更时执行完整 release gate。

AstrBot 宿主基线钉在 `v4.27.2`，并同时校验提交 `ad4fbfa90ca0c4ac2b30b3250e34dbf8fe7babbf`，避免上游 `master` 漂移导致候选证据失真。

## 7. 后续变更规则

任何新增公开能力，至少同时更新：

1. 配置或管理员授权入口；
2. 实际运行代码路径；
3. 对应 invariant / integration 回归；
4. 本能力矩阵的效果、副作用与未实现边界。

如果其中任一项缺失，该能力不应进入 README / Skill 的公开“已实现功能”列表。
