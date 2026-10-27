# 2026-10-27 往事记忆、主动陪伴、流式输出与语音接口验证

日期：2026-10-27（北京时间）。范围：AM-005、AM-008、AM-009、AM-010 首次集成；精确标识以包含本报告的 Git 提交为准。用户决定先补功能、之后统一验收，本报告只记录自动化回归，不代表上述任务已验收。

## 自动化回归

- 环境：Linux（非 Windows），Python 3.12.11，PySide6-Essentials 6.9.3，pytest 9.1.1；Qt 使用 offscreen，模型服务、语音接口和播放器均为本地模拟或假实现，未使用真实凭据。
- 执行：仓库根目录 `.venv` Python，`QT_QPA_PLATFORM=offscreen python -B -m pytest -q -p no:cacheprovider`。
- 结果：**235 passed，1 skipped，1 failed**，约 29 秒。
- 失败项：`tests/test_pet.py::test_right_click_commands_expose_sitting_sleeping_waking_and_flight`（`Internal C++ object (QMenu) already deleted`）。在本轮任何改动前的同一环境中已失败，判断为该 Linux offscreen / PySide6 版本差异，未修改；需在 Windows 环境复核。
- 新增用例：`test_episodes.py` 11 项、`test_companion.py` 16 项、`test_streaming.py` 7 项（含本地 SSE HTTP 服务）、`test_tts.py` 7 项；新增文件连续运行 3 次均通过。`test_chat.py` 中的假模型补充了 `stream` 方法。

## 覆盖要点

- AM-005：待整理轮次上限与敏感过滤、日常按话题更新与上限、重启后保留、相关往事选取、摘要解析容错、整理成功只删除已整理原文、失败/取消/演示保留原文、聊天上下文中区分真实往事与角色创作、关闭开关、编辑页删除与清空、启动时整理遗留轮次。
- AM-008：明确“安静”说法的正反例、夜间/上限/冷却/在场判断组合、久别问候与闲时分享触发、空回复不说、送达前用户已输入或已发消息时作废、右键开关与安静两小时。
- AM-009：增量解析只返回已闭合气泡且单调、SSE 读取并忽略思考内容、截断与中途取消、首泡早于完成且最终不重播、取消后无迟到气泡与历史。
- AM-010：可朗读文本清理、占位接口提示、按序朗读且流式不重复、关闭时不朗读、新回复打断、失败只提示一次、无播放器提示、聊天中取消和新消息停止朗读。

## 未覆盖与待验收

- **真实模型**：未调用。摘要是否只写真实发生的内容、主动发言的频率与内容、DeepSeek 实际流式分块与首泡延迟均未测。
- **Windows 实机**：未运行。`WinsoundBackend` 播放、停止和时长估计，主动陪伴长时间挂机体验，右键菜单实际显示均未检查。
- **语音 API**：按用户要求只提供接口，`CustomSpeechProvider.synthesize` 由用户接入，接入前开启语音只会提示未接入。
- 已知限制：整理进行中点击「全部清空」，正在生成的那一批仍可能在清空后写入。

当前后续工作见 [任务台账](../BACKLOG.md)。
