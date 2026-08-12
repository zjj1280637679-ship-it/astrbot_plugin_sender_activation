# 模块消元矩阵：从效果回到底层机制

## 目标

不按现有模块名称判断架构，而按：

```
需求效果
 ↓
最小机制
 ↓
是否已有上游能力
 ↓
是否需要 Runtime
```

判断模块是否必要。

## 核心保留

|模块|最小身份|结论|
|-|-|-|
|对象激活|ID + Lease + Wake Bridge|核心|
|manage_sender_activation|AI 管理对象激活条件|核心 Tool|
|对象状态存储|保存有效租约|生产可靠性|
|真实回执|防止 AI 把未执行当事实|核心可靠性|

## 上游复用

|模块|替代|结论|
|-|-|-|
|Heartbeat|AstrBot future_task|非核心|
|CronAdapter|AstrBot CronJobManager|非核心|
|周期唤醒|future_task cron|Skill 映射即可|
|一次延迟检查|future_task run_once|Skill 映射即可|

## 边缘治理

|模块|本质|结论|
|-|-|-|
|Rate|Activate 的频率限制|修饰符|
|Ignore|反向注意力控制|修饰符|
|AccessService|委托治理|高级扩展|
|Yield|获得回合后的输出控制|辅助语义|
|Echo|时间激活的特殊治理包装|需要单独证明|

## 删除判定

如果删除模块后：

- ID → Activate 仍成立；
- Time → Activate 仍成立；
- AI 仍可通过 Tool 配置；

则该模块不是产品主体。

## 下一步

建立 thin-core 实验版本，与 rc19 对比：

- 功能可达性；
- Tool 数量；
- 配置数量；
- 状态数量；
- 测试成本；
- 故障面。