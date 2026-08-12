# Thin Core 对照实现计划

## 目标

不立即重构 rc19，而建立最小机制模型，与现有实现比较。

核心假设：

> 如果更薄的机制能够覆盖同样的核心效果，则复杂功能不应进入产品主干。

## Thin Core

只保留：

```
Object(ID) -> finite activation lease

AI Tool -> manage_sender_activation

CustomFilter -> AstrBot native wake bridge

Time activation -> AstrBot future_task

Skill -> semantic mapping
```

## 对照指标

### 效果

- 是否能让指定 ID 普通消息激活主 Agent
- 是否能让未来时间激活主 Agent
- 是否保持原生 @ 行为

### 工程复杂度

- 文件数量
- 状态模型数量
- Tool 数量
- 配置数量
- 测试数量

### 风险

- 与 AstrBot 重复程度
- 状态同步点
- 失败路径数量
- 权限边界数量

## 实验规则

不比较功能数量，而比较：

```
同一核心效果
+
更少机制
+
更少副作用
```

## 成功标准

如果 Thin Core 达到 rc19 核心效果：

- P0 保留；
- P2 边缘能力重新评估；
- 重复 AstrBot 能力退出核心。

如果失败：

必须说明缺失的是：

- 机制缺口；
- 宿主接口限制；
- 可靠性要求；
- 其他真实原因。
