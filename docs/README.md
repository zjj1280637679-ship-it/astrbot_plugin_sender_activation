# 文档入口

当前开发线只认三份文档，各自职责固定：

1. [self_discipline_harness.md](self_discipline_harness.md) — **工程总纲 / 目标架构**。决定项目为什么存在、模块边界和未来 PR 必须遵守的不变量。
2. [engineering_proposal.md](engineering_proposal.md) — **AttentionProgram 当前实现契约**。记录 v2 数据结构、迁移、dirty/reconcile 与现有 Runtime 语义。
3. [capability_matrix.md](capability_matrix.md) — **兼容能力与副作用基线**。记录仍然实际可达的旧 Sender/Heartbeat/Echo/Ignore 等路径，供回归和迁移审计。
4. [real_environment_candidate.md](real_environment_candidate.md) — **阶段进度 / 自动反例 / 真实环境验收单**。用于当前候选上机测试。

阅读优先级：

```text
工程总纲
   ↓
当前实现契约
   ↓
真实环境验收
   ↓
兼容能力矩阵
```

如果旧文档中的历史实现描述与工程总纲冲突，以工程总纲作为**目标语义**；但在代码尚未迁移前，测试仍必须如实覆盖当前实现，禁止把目标设计写成已经完成的能力。

仓库当前只保留一条主开发候选：`self-discipline-harness-v1.3.0-rc1` / PR #6。旧 rc 分支仅作为历史取证来源，不应继续承载新修改。
