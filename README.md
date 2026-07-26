<h1 align="center">管理员的真理捍卫器</h1>
<p align="center"><strong>用这款 AstrBot 插件，让你的 AI 获得限时免@的主动回复能力：该反驳就反驳，该沉默就沉默。</strong></p>

[![License: AGPL-3.0-or-later](https://img.shields.io/badge/License-AGPL--3.0--or--later-blue.svg)](LICENSE)
[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.26.1%2C%3C4.27-6b63ff)](https://github.com/AstrBotDevs/AstrBot)
[![Platform](https://img.shields.io/badge/platform-aiocqhttp-2f855a)](https://docs.astrbot.app/en/dev/star/plugin-new.html)

> **让该被追问的发言得到反驳，让没有增量的接话归于沉默。**

你只需任命一次，就能让 AstrBot 在限定时间内继续留在争论现场：持续关注或追踪群成员的后续发言，也可以用有限期心跳定时唤醒主 Agent，巡查当时可见的群聊上下文。发现错误信息、概念偷换、回避问题或其他需要澄清的不良发言时，由你的 AI 基于完整上下文主动反驳或接话；没有证据变化、逻辑缺口或其他有效增量时，让它主动沉默。等争论结束，撤销任务或让租期到点，一切自动恢复原状。

## 你可能在找

- **免@主动回复**：任命后，目标成员的普通群消息也能获得一次主 Agent 判断机会。
- **持续关注或追踪群成员**：让 AI 在有限时间内跟住同一对象，不必每轮重新点名。
- **定时巡查群聊**：用原生 Cron 心跳定时唤醒主 Agent，检查当时可见的群聊上下文。
- **群聊监控中的主动沉默**：激活不等于强制回复，没有有效增量时由 AI 正式让出话轮。

> **候选版说明：** `1.1.0rc13` 使用项目作者提供的复古机器人海报，并将公开
> GitHub 仓库收束为可直接安装的运行态投影。内部插件 ID、数据命名空间、
> 工具接口和运行机制保持不变。

> **运行边界：** 首版运行域严格等于 `aiocqhttp` 群聊。其他平台和私聊事件
> 保持原样；若主 Agent 在这些语境调用管理工具，插件返回结构化不支持错误，
> 不建立租约、不写入状态。

## 让你的 AI 主动守住这场争论

你不是在命令代码裁定谁对谁错，也不是让关键词替你向某个人开火。你是在给
AstrBot 一段有限职责，让它守住一场讨论中最容易被消息流冲散的东西：

- 盯住原问题，别让话题漂移把它悄悄替换；
- 追问观点、证据与结论之间还没有闭合的关系；
- 遇到新证据就修正判断，不替旧答案护航；
- 没有矛盾或有效增量时，主动让出话轮。

你仍把具体判断交给 AstrBot 原生主 Agent 和完整上下文。需要外部事实核验
时，让主 Agent 组合已经安装的正式检索工具；别把本插件当成事实库，也别把
模型的一次回答当成永远正确的答案。

## 现在，把这场讨论交给它

例如：

> 接下来两小时，替我跟住张三关于这件事的论证。原问题被回避、概念被偷换或证据发生变化时再澄清；每十分钟也重新审视一次全群的新发言。没有值得补充的内容就保持沉默。

这句话对应一条有限生命周期：

1. **接任**：主 Agent 建立张三在当前群聊中的限时对象激活租约。
2. **跟进**：张三后续发言不必再次 `@` 机器人，也能获得一次原生 Agent
   判断机会。
3. **判断**：主 Agent 根据完整语境选择澄清、调用事实工具，或正式让出本轮。
4. **退场**：争论结束、人工撤销或租期到点后，插件不再新增唤醒。

激活从来不等于强制回复。它只让论证的下一环不会因为缺少一次 `@` 而从 AI
视野中消失。

## 为什么需要这层机制

AstrBot 原生的 `@`、引用、唤醒词和命令可以可靠地唤醒主 Agent，但“接下来
继续跟住这个人的论证”属于跨话轮状态。模型若只口头答应，却没有建立系统
状态，就会产生“承诺与可达性错配”；模型被主动唤醒后若只能硬发一条文字，
又会产生“看见却不能自然让出话轮”的错配。

管理员的真理捍卫器只补充这些基础机制，不复制原生 Agent：

```text
M = A_native ∪ {I, H, L_i, Y}
```

- `A_native`：AstrBot 原生 Agent、上下文、`@`、命令、主动回复与 Cron。
- `I`：当前 UMO 与指定 QQ ID 组成的有限期额外激活租约。
- `H`：当前 UMO 上复用 AstrBot 原生 Cron 的有限期时序激活租约。
- `L_i`：只约束 `I` 所产生额外激活的可选、有限期限频租约。
- `Y`：仅在 `I/H` 新增的主动回合中，由主 Agent 正式选择无可见回复。

生命周期闭环为：

```text
I/H 激活 -> 原生主 Agent 看现场 -> 行动或 Y 让出
        -> 达成目标、人工撤销或有限期到期 -> 回到 A_native
```

职责严格分离：

```text
Decision = AstrBot 原生主 Agent 对完整语境的理解
Execute  = 插件按正式工具帧维护 I、H、L_i 与当前回合 Y
Fallback = 插件异常、状态损坏、明确停用或租约失效时回到 A_native
```

插件不会把 AstrBot 超级管理员权限扩散给普通成员。管理员可以按当前群和
QQ ID 授予有限期插件操作员权限；获授权成员只能控制本插件的对象激活、
限频和心跳，不能配置 AstrBot、操作其他插件或跨群转授权。插件不判断谁
掌握真理、何时必须反驳；主 Agent 可以根据新证据修正或结束租约。若插件自身
崩溃或被宿主阻塞，失败语义只能是“不再新增唤醒”，不能阻断或改写原生路径。

## 求真边界

| 管理员的真理捍卫器不做的事 | 原因 |
| --- | --- |
| 在代码中判定真理或给用户贴标签 | 判断必须来自完整语境、证据和主 Agent |
| 内置事实库或替代网络检索 | 事实核验应复用独立、正式的信息工具 |
| 因出现某个词就自动反驳 | 用户文本只能由主 Agent 高语境理解后转成正式工具帧 |
| 保证每条发言都得到回复 | 激活只增加判断机会，主 Agent 仍可沉默 |
| 自建或选择大语言模型 | 主 Agent 与 Provider 生命周期由 AstrBot 负责 |
| 限制原生 `@`、引用、命令或普通会话 | 限频只消费本插件新增的激活额度 |
| 阻断、改写或合并消息 | 超限时消息仍进入原生流水线和群上下文 |
| 自建调度器、Cron 数据库或第二套 Agent | 时序激活只适配 AstrBot 原生 Cron |
| 自动读取 QQ 新消息或历史记录 | 心跳只提供判断机会；信息能力需由主 Agent 组合现有工具 |
| 对普通原生回合强制沉默 | `yield_current_turn` 只接受本插件新增的主动回合 |
| 提供安全级 DDoS 防御 | 内存计数器是运营保护，重启后清零 |
| 跨平台泛化 | 首版仅正式支持 `aiocqhttp` |

## 兼容性

| 项目 | 支持范围 |
| --- | --- |
| AstrBot | `>=4.26.1,<4.27` |
| Python | 跟随目标 AstrBot 版本，CI 使用 Python 3.12 |
| 平台适配器 | 仅 `aiocqhttp` |
| 私聊 | 不建立租约、不额外唤醒；可只读检测当前会话生效的宿主配置 |
| AstrBot `master` | 仅预警测试，失败不自动扩大支持范围 |

上表是当前公开候选的完整支持边界；未列平台不视为已兼容。

### AstrBot 宿主配置

插件不接管 AstrBot 的 Agent、会话或事件流水线，因此下列宿主配置会继续影响
实际效果：

| AstrBot 配置 | 对插件的影响 |
| --- | --- |
| `provider_settings.enable` | 必须开启；关闭后租约最多只能产生 wake 机会，主 Agent 不会运行 |
| `provider_settings.agent_runner_type` | 首版只验收内置 `local` Agent；第三方 Agent 执行器不在正式支持范围 |
| 当前对话模型的 `tool_use` 能力 | 必须具备，否则主 Agent 不能产生五个正式工具帧 |
| `provider_settings.show_tool_use_status=false` | 要实现完全无可见回复时应关闭；否则宿主可能先发送工具调用状态 |
| `provider_settings.identifier`（用户识别） | 使用“我、本人、刚才那个人”等表达时应开启，使主 Agent 获得真实 User ID；插件事件层仍会读取 ID，但模型看不到就无法可靠填写 `target_ids` |
| `provider_settings.wake_prefix`（LLM 额外唤醒前缀） | 要让租约命中的普通非前缀消息进入 Agent，必须留空；它不同于顶层普通唤醒词 |
| `platform_settings.unique_session`（隔离会话） | 跨成员追踪必须关闭；开启后同一群不同成员使用不同 UMO，发令者建立的租约无法命中另一成员的消息 |
| 白名单、原生 `platform_settings.rate_limit` | 仍在 AstrBot 原生流水线生效；插件默认不限频不等于关闭 AstrBot 原生限流 |
| `plugin_set`、会话插件开关、独立工具开关 | 可分别让事件处理器或工具不可达，修改后必须核对五个工具仍可见 |
| 操作工具的原生权限 | 获授权普通成员使用时，`manage_sender_activation`、`manage_sender_activation_rate`、`manage_heartbeat_lease` 必须为 `member`；插件再按当前群的授权表做指定 ID 校验 |

`target_ids` 只接受真实数字 QQ ID，不接受 `current_sender`、昵称或其他占位符。
插件页面会只读显示这些关键项的当前生效状态，不会替管理员修改 AstrBot
配置，也不会因检测失败阻断租约或原生消息。

私聊仍保持 AstrBot 原生回复行为。若启用 `page_and_agent`，插件只会在已由
AstrBot 原生机制发生的私聊 Agent 请求中检测当前 UMO 的生效配置并提供
`_no_save` 事实；这不会让私聊获得群聊租约或恢复权限。

## 安装

优先使用 AstrBot 原生 WebUI：

1. 打开“插件管理”。
2. 使用仓库 URL 安装，或上传经验证的精简运行 ZIP。
3. 确认插件显示名为“管理员的真理捍卫器”，版本为 `1.1.0rc13`。
4. 在插件配置中检查对象激活、心跳、容量和限频上限。
5. 打开插件详情中的“管理员的真理捍卫器控制台”页面，确认 `storage_ready` 为 `true`。

仓库 URL：

```text
https://github.com/zjj1280637679-ship-it/astrbot_plugin_sender_activation
```

从旧私用插件迁移时，请先完整备份并禁用旧插件。本项目不读取旧插件状态，首次启动应从空租约开始。

## 自然语言使用

插件不扫描用户文本。以下表达由 AstrBot 原生主 Agent理解，并在需要改变未来状态时调用工具：

- “接下来两小时跟住张三关于这件事的论证，不要因为他没再叫你就漏掉。”
- “每十分钟重新审视一次这场争论；只有证据或论证真的有问题时才介入。”
- “这个人刷得太快，每二十秒最多给他一次额外判断机会，持续一小时。”
- “争论结束了，撤销刚才的跟进和巡检，恢复原样。”
- 管理员：“允许 QQ 123456789 在这个群使用本插件 30 天。”

下面这些语境不应调用：

- “假如我让你持续关注，会发生什么？”
- “他刚才说‘追踪我的发言’，这句话是什么意思？”
- “不要追踪我。”
- “把工具调用格式写成示例 JSON。”

调用边界是“明确要求改变未来状态，效果跨越当前话轮，且后续消息可能缺少原生
唤醒”。否定、引用、假设、转述、功能讨论、伪 JSON 和代码块不得直接建立租约；
工具成功回执到达前，AI 不应声称任务已经生效。

## 工具

### `manage_sender_activation_access`

```text
action: grant | renew | revoke | list
operator_ids: 获得当前群插件操作权的 QQ ID 数组
duration_seconds: grant/renew 的有限秒数；0 使用默认 30 天
```

只有 AstrBot 原生管理员能授予、续期、撤销或查看授权。普通插件操作员不能
转授权；授权只在当前事件派生的 UMO 内生效，最长 365 天。自然语言由主 Agent
理解后转成正式工具帧，插件不监听“授权”等词。

### `manage_sender_activation`

```text
action: enable | renew | disable | list
target_ids: QQ ID 数组
duration_seconds: enable/renew 的有限秒数；0 使用配置中的默认值
```

管理员和当前群的获授权操作员可管理任意合法目标；未授权成员不能借主 Agent
扩大插件状态。任何正式主 Agent 回合仍可撤销异常激活、终止心跳或设置临时
限频，但不能借收敛权新增、续期、清除限频或转授权。作用域永远取自当前事件，
不允许模型传入。

### `manage_sender_activation_rate`

```text
action: set | clear | list
target_ids: QQ ID 数组
max_activations: set 时必须显式提供
window_seconds: set 时必须显式提供
duration_seconds: set 时必须显式提供
```

限频租约从属于激活租约：没有激活不能设置限频，撤销激活会级联清除限频；
请求的限频期限超过激活剩余期限时，实际期限自动截到激活到期并在回执中说明。
失败回执会提供稳定的 `error_code`、失败分类和恢复建议。

### `manage_heartbeat_lease`

```text
action: create | renew | disable | list
lease_ids: renew/disable 的原生 Cron 任务 ID；list 可为空
name: Cron 页面可辨识的短名称
cron_expression: 五段 Cron 表达式，最小粒度一分钟
instruction: 每次唤醒时交给原生主 Agent 的语境目标
duration_seconds: 有限有效期；0 使用配置默认值
```

一个心跳租约对应一个原生主动 Agent 任务和一个原生到期清理任务。插件正常
停用时暂停自己的心跳，重新启用时只恢复未到期且由插件暂停的任务；人工在
Cron 页面停用的任务不会被强行开启。

### `yield_current_turn`

```text
reason: 可选的当前语境理由，不向群聊显示
```

只允许由本插件对象激活或心跳产生的当前主动回合调用。成功后使用 AstrBot
本地工具的终结返回直接结束当前原生 Agent 工具循环，并移除本轮最终助手
文本；不会再进入下一次工具选择。已经执行的外部工具动作不会撤销。普通
`@`、普通原生会话和其他插件唤醒不能用它抹除回复。

租约维护属于控制面，不应直接变成群聊台词。继续租约但没有有效增量时直接
让出；单条消息没有增量不等于应撤租。自主终止租约时先调用对应 `disable`
并取得成功回执，下一次工具选择再把 `yield_current_turn` 作为唯一且最后的
调用。不要发送“我在判断是否停止”“先继续观察”一类内部维护句。

## 配置

| 配置项 | 默认值 | 含义 |
| --- | ---: | --- |
| `activation_default_seconds` | `86400` | 省略有效期时使用 24 小时 |
| `activation_max_seconds` | `31536000` | 激活租约硬上限 365 天 |
| `max_targets_per_scope` | `20` | 每个 UMO 同时激活的目标上限 |
| `max_targets_total` | `500` | 插件全局激活租约容量 |
| `operator_access_default_seconds` | `2592000` | 省略授权期限时使用 30 天 |
| `operator_access_max_seconds` | `31536000` | 操作员授权硬上限 365 天 |
| `max_operators_per_scope` | `50` | 每个 UMO 同时授权的操作员上限 |
| `max_operators_total` | `1000` | 全部 UMO 的操作员授权容量 |
| `rate_max_duration_seconds` | `86400` | 限频租约最长 24 小时 |
| `rate_max_activations` | `1000` | 单个窗口允许的额外激活次数上限 |
| `rate_max_window_seconds` | `86400` | 单个滑动窗口最长 24 小时 |
| `heartbeat_default_seconds` | `3600` | 省略心跳期限时使用 1 小时 |
| `heartbeat_max_seconds` | `86400` | 单个心跳租约最长 24 小时 |
| `max_heartbeats_per_scope` | `10` | 每个 UMO 同时存在的心跳上限 |
| `max_heartbeats_total` | `100` | 插件全部 UMO 的心跳容量 |
| `recovery_report_seconds` | `86400` | 结构恢复事实报告的有限保留期 |
| `host_config_notice_mode` | `page_and_agent` | `page_only` 仅页面显示；默认还向主 Agent 提供 `_no_save` 配置事实，由其结合语境决定是否提醒 |

默认不存在插件限频。配置上限只约束新操作；缩小配置不会让已有健康状态整体加载失败。

## 运行语义

- 无租约时插件是恒等变换：不唤醒、不阻断、不改写。
- 存储不可用或状态初始化失败时，插件退化为“无额外激活”。
- 每次变更先构造不可变候选快照，持久化成功后再原子替换运行快照。
- 损坏记录逐条隔离；两个 KV 槽独立读取，单槽异常时健康槽仍可恢复。
- KV 报错后会读回核对实际结果；无法核对时进入惰性状态，不会把未知结果谎报为失败。
- 停用先停止接收新修改并等待在途提交，返回后旧实例不能继续写状态。
- 限频使用单调时钟和内存短锁；重启后计数清零，但租约不会因此延长。
- 同一进程内租约墙钟不回退，已观察到期的租约不会因校时回拨复活。
- 原生 `@`、引用、唤醒词与命令不经过插件限频。
- 心跳与到期清理只由 AstrBot 原生 Cron 调度；插件不创建异步循环或私有队列。
- 心跳到期按原生 Cron 分钟粒度向上对齐；撤销或到期后不再新增时序激活。
- 正常停用插件会暂停其未到期心跳；再次启用时只恢复插件暂停的任务。
- “无可见回复”必须由主 Agent 调用正式让出工具；用户文本不能直接触发，
  普通原生回合也不能使用。
- 事件过滤分为两阶段：`CustomFilter` 只执行无副作用 `preview`；会话处理器实际获准运行后才由 `decide` 消费一次额外激活额度并设置原生 wake。
- AstrBot 会话中明确禁用插件时，不新增激活或限频；读取不到会话状态不等于禁用，按 AstrBot 的未配置默认启用语义继续运行。
- 查询、撤销激活和清除限频是恢复动作，不因会话状态未知而被封锁。
- “过滤预判无副作用”指插件不消费限频、不增加指标、不写状态、不修改事件。AstrBot 4.26.x 内核可能在过滤器返回 `true` 后先标记候选 wake，再应用会话处理器开关；插件不复制或改写该原生顺序。
- 过滤、状态或存储异常都惰性退化为“本次无额外激活”，不得阻断原生消息。

## 管理页面

Plugin Page 使用 AstrBot 注入的 `window.AstrBotPluginPage`：

- 前端只调用 `bridge.apiGet("state")`、`bridge.apiPost("access")`、
  `bridge.apiPost("activation")`、`bridge.apiPost("rate")` 和
  `bridge.apiPost("heartbeat")`。
- 页面与 LLM 工具复用同一个应用服务，不复制业务逻辑。
- 页面关闭后插件保持无头运行。
- 页面只使用现有租约派生的 `scope_ref`，不接收或展示原始 UMO。
- 页面通过现有 `state` API 只读显示默认配置或所选作用域的生效配置；前缀
  只显示“留空/已填写”，不回显其内容。
- 页面只读检测三个操作工具的 AstrBot 原生权限；若仍为 `admin`，会明确提示
  该设置在插件授权检查之前阻断普通操作员。
- 首个作用域租约必须由当前会话中的正式 Agent 工具帧建立。
- 首版页面一次只显示和管理一个作用域；写操作使用页面内影响预览确认，不依赖受限 iframe 中不可用的浏览器原生确认框；批量变更还必须勾选二次确认。

实现遵循 AstrBot 官方 [Plugin Pages 指南](https://docs.astrbot.app/en/dev/star/guides/plugin-pages.html)。

## 验证与发布

`1.0.0` 必须同时满足：

1. 无租约行为与未安装插件一致。
2. 不存在私有模型调用、Provider 选择、私有调度器或任意输出改写；唯一输出
   抑制是主 Agent 正式调用 `yield_current_turn` 后对当前主动回合的局部让出。
3. 写入失败时未提交状态不可见，无法确认的结果明确标为未知并要求重载核对。
4. 五条 Page API 在手机和桌面、明暗主题下可用，关闭页面后仍可运行。
5. 干净克隆可复现测试与确定性 ZIP。
6. 不说“插件”的自然语言仍能稳定产生正确工具帧。
7. 取得 AstrBot Trace、结构化日志、WebUI 和 QQ 可见结果四层证据。

发布包不得超过 16 MiB，并附文件清单与 SHA-256。公开 GitHub 仓库只保留
可安装运行态；测试、知识图、CI、内部报告和发布证据留在非公开开发仓库。
正式版通过全部门槛后，才提交至 [AstrBot 插件市场](https://plugins.astrbot.app/)。

## 反馈

问题和功能建议请提交到
[GitHub Issues](https://github.com/zjj1280637679-ship-it/astrbot_plugin_sender_activation/issues)。
报告安全问题时不要在 Issue 中附带密钥、Token、完整聊天记录或真实身份数据。

## 许可证

本项目以 [GNU Affero General Public License v3.0 or later](LICENSE) 发布。
