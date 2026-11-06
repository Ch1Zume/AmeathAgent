# 2026-11-06 验证记录：游戏算账/流派/路线/复盘、日程表、版本页与打包更新

- 日期：2026-11-06（北京时间）
- 范围：AM-015、AM-016、AM-017
- 环境：Linux（RHEL 8 内核 4.18），Python 3.12 `.venv`，PySide6 offscreen；PyInstaller 6.22.3
- 代码基线：`67a5165` 之上的本轮未提交改动

## 自动测试

```text
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q --basetemp .tmp/pytest-final -p no:cacheprovider
1 failed, 304 passed, 2 skipped
```

失败项为改动前已存在的 `tests/test_pet.py::test_right_click_commands_expose_sitting_sleeping_waking_and_flight`（offscreen 插件不支持 `raise()`）；本轮改动前的基线为 278 通过 / 2 跳过 / 同一项失败。

新增测试：

- `tests/test_game_analysis.py`（12 项）：完整/紧凑能力解析；`dynamic_values` 优先于规则文本；力量、虚弱、易伤、无实体、多段、敏捷、脆弱的计算与能量内最大伤害/击杀判断；`combat_summary` 跳过已死亡敌人；牌组汇总；完整与紧凑地图的路线枚举；规划一次后按路线前进且不再调用模型；低血量遇精英重规划；非战斗决策不出声但保留 `build_fit`；复盘只写一次、不保存用量、经验进入下一局回顾；演示不复盘；游戏页流派/路线显示和复盘历史。
- `tests/test_schedule.py`（8 项）：增删改、完成、敏感词拒绝；提醒只触发一次、工作日重复顺延到周一；错过的提醒 2 小时内补发；时间解析与重复规则；聊天 `schedule_updates` 校验；聊天背景注入；迟到回复不写日程；日程页操作。
- `tests/test_updater.py`（6 项）：Release 解析与版本比较；只允许 GitHub HTTPS 主机；下载 SHA256 校验通过/失败（失败时删除文件）；zip 路径穿越与结构检查；源码运行拒绝原地安装；版本页三个标签、过期检查结果被忽略。

## 打包检查（Linux，仅验证 spec）

```text
.venv/bin/python -m PyInstaller packaging/AmeathAgent.spec --noconfirm --clean --distpath .tmp/dist --workpath .tmp/pyinstaller
# 构建成功；_internal 下有 assets、ameath（语音服务源码）、docs、CHANGELOG.md、integrations
AMEATH_DATA_DIR=.tmp/smoke-data QT_QPA_PLATFORM=offscreen timeout 12 ./AmeathAgent --no-tray
# 运行到超时被结束（exit 124），无导入错误；数据目录生成 schedule.sqlite3、companion.sqlite3、示例 PDF 等
.venv/bin/python tools/package_release.py .tmp/dist/AmeathAgent --out .tmp/release --platform linux64
# 生成 zip 与 .sha256，结构为 AmeathAgent/AmeathAgent + AmeathAgent/_internal/...
```

这只说明 spec 和数据文件配置可用。Windows exe 没有在本次环境中构建或运行。

## 未覆盖

- 未调用真实模型：伤害算账是否与游戏显示一致、路线选择质量、复盘内容、聊天中添加日程的准确度都没有真实样本。
- 未在真实 STS2 中运行；桥接文档没有说明 `current_value` 与意图伤害是否已含增减益，需实机核对。
- 未在 Windows 桌面检查日程提醒的朗读、托盘通知和页面布局。
- 未在 Windows 构建 exe，未运行 `apply_update.cmd`，也没有真实 Release 可供升级测试；GitHub Actions 工作流尚未运行过。
