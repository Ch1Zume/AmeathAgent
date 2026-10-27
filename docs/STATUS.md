# 当前开发状态

更新日期：2026-10-27（北京时间）。当前代码版本：0.6.1 原型；首次 GitHub 同步基线，尚未创建发布标签。这里只记录已找到的实现与验证边界，不把路线图当成功能清单。

## 能力现状

| 能力 | 当前实现 | 证据 / 限制 |
| --- | --- | --- |
| 文字聊天与模型 | DeepSeek 共享客户端、独立聊天上下文、有限自动重试；聊天避免强制 JSON 模式，历史助手消息统一输出格式 | `chat.py`、`llm.py`、模型/聊天测试；已有真实服务样本，不能保证服务永不空答或重试 |
| 分段表达 | 一轮回复多个气泡，文字淡入、段间停顿、显示全部、滚动回看、打断和过期回复隔离；Chat 接口下每生成完一个气泡就开始显示 | `bubbles.py`、`chat_ui.py`、`llm.py`、`test_speech.py`、`test_streaming.py`；流式只在 Chat Completions 接口且模型输出 `reply` 数组时逐泡显示，尚未在真实服务上测首泡延迟 |
| 人设与生活素材 | current 主卡包含主动关心、减少反问、明确偏好和五类个人日常；角色故事在当前上下文内延续 | `assets/personas/aemeath-cn-current.json`、`CHAT_DELIVERY`；没有独立日程引擎；跨会话延续依赖下行的往事与日常摘要 |
| 往事与角色日常 | 离开上下文的聊天由模型整理成真实往事摘要和按话题更新的角色原创日常，跨会话作为背景；可查看、删除、关闭 | `memory.py`、`chat.py`、`test_episodes.py`；自动回归通过，尚无真实模型样本；不保存聊天全文 |
| 用户长期记忆 | SQLite 保存可编辑事实/偏好；有开关、相关性选择与敏感值过滤 | `memory.py`、`companion_ui.py`；不是完整对话持久化、事件回溯或向量检索 |
| 阅读与截图解释 | 本地 PDF、选区、手动剪贴板/截图入口、引用和概念卡片 | `reader.py`、`papers.py`、`capture.py`、`agent.py`；外部阅读器自动接入仍有限 |
| 桌宠与日常工具 | 精灵动作、移动、飞行、休眠、天气问候、番茄钟 | `pet.py`、`sprites.py`、`weather.py`、`focus.py`；机器私有基础宠物资源需本机提供 |
| 卡牌游戏 | STS2 AI Agent v0.16.2 的本机 HTTP 桥接、动作校验、暂停和用量限制 | `game.py`、`game_ui.py`、[接入说明](sts2.md)；真实桥接/完整一局验收未完成，不是 Neuro WebSocket 接入 |
| 主动陪伴 | 默认关闭；开启后久别问候与闲时分享，带安静时段、每日上限、冷却、任务中不打扰和“安静两小时” | `companion.py`、`test_companion.py`；自动回归通过，尚无真实模型样本或长时间实机体验 |
| 语音朗读 | 有可插拔接口、朗读队列和 Windows WAV 播放器，默认关闭；**尚未接入任何真实 TTS 服务** | `tts.py`、`test_tts.py`；需在 `CustomSpeechProvider` 中实现 API；没有语音输入 |
| 持续桌面观察 | 尚无 PPT 自动建议、读屏观察或通用电脑控制入口 | 作为后续候选；不得由角色台词推定这些能力存在 |

## 本轮已落实

- 首轮请求兼容修复、完整历史格式、分段气泡与淡入；保留取消和回看行为。
- 工作倾诉与任务委托区分；增加俏皮护短，减少连续反问与正反总结。
- 补充音乐、游戏、收藏整理、动手试验和发呆灵感的日常素材；current 主卡已导出供新安装使用。
- 建立开发约定、任务台账、变更记录和验证入口；历史规划仍保留为资料。

## 验证边界

自动回归与真实聊天样本的具体范围见 [首次同步验证记录](validation/2026-10-26-initial-sync.md)。2026-10-27 新增的往事记忆、主动陪伴、流式输出与语音接口只有自动回归，见 [2026-10-27 验证记录](validation/2026-10-27-companion-features.md)。2026-10-25 的旧记录保留在 [历史验证报告](validation-2026-10-25.md)，其中的“未验证”是当日结论，不能覆盖后来的聊天服务实测；游戏实机验收仍未补齐。

目前最需要继续观察的是：长对话里角色是否仍反问/复读、真实桌面播放和输入法体验、游戏完整闭环。见 [当前待办](BACKLOG.md)。不把几轮模型样本视为长期稳定性评估。

## 实际代码入口

| 职责 | 文件 |
| --- | --- |
| 应用组装、启动、单实例 | `main.py`、`ameath/app.py`、`ameath/instance.py` |
| 模型、配置、凭据 | `ameath/llm.py`、`ameath/config.py`、`ameath/dialogs.py` |
| 对话、人设、记忆 | `ameath/chat.py`、`ameath/memory.py`、`ameath/companion_ui.py` |
| 对话界面、文本输入、气泡 | `ameath/chat_ui.py`、`ameath/input.py`、`ameath/bubbles.py` |
| 桌宠与生命周期 | `ameath/pet.py`、`ameath/sprites.py`、`ameath/lifecycle.py` |
| 论文、截图与笔记 | `ameath/reader.py`、`ameath/papers.py`、`ameath/capture.py`、`ameath/agent.py`、`ameath/storage.py` |
| 天气、专注、提示规则 | `ameath/weather.py`、`ameath/focus.py`、`ameath/policy.py` |
| 游戏与安装工具 | `ameath/game.py`、`ameath/game_ui.py`、`integrations/sts2/`、`tools/*sts2*` |

这份映射是当前代码导航，不代表旧规划 BASE-01 中所有权限、语音和真实运行审计已完成。
