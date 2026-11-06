# 开发流程与记录规范

本规范用于让后续开发者和编码助手接着当前代码工作。所有日期使用北京时间（Asia/Shanghai）；测试执行机器可以使用其他时区，但报告注明日期口径。

## 每种信息只维护一个主入口

| 文件 | 回答的问题 | 更新时机 |
| --- | --- | --- |
| [README](../README.md) | 一页简介：能做什么、怎么开始、Credits 与许可 | 对外能力、安装方式或致谢变化；保持一页，细节放 GUIDE |
| [GUIDE](GUIDE.md) | 各功能的完整使用说明、数据范围和已知限制 | 使用方式、数据范围或限制变化 |
| [STATUS](STATUS.md) | 现在做到哪里，哪些还没验证？ | 一项能力或限制发生实质变化 |
| [BACKLOG](BACKLOG.md) | 下一步做什么，验收是什么？ | 开始任务、阻塞、验收或优先级变化 |
| [CHANGELOG](../CHANGELOG.md) | 已经交付了哪些变化？ | 用户可见改动完成后 |
| [ROADMAP](ROADMAP.md) | 使用者能看到的近期计划（应用内「更新计划」） | 计划或待验证事项对使用者有变化时；细节仍写 BACKLOG |
| [validation/](validation/) | 某次验证实际做了什么？ | 较大变更、首次集成、发布或需要保留的实测 |
| [AGENTS](../AGENTS.md) | 开发助手必须遵循什么？ | 仓库协作约定变化 |

旧的 [2026-10-26 规划包](ameath-development-plan-2026-10-26/AMEATH_PLAN_README.md) 保留当时的设计与出处，不继续在其中打勾追踪。选取其中任务时，将任务落到当前 BACKLOG，标明原 ID；不得仅因新建了文档就宣布旧里程碑全部完成。

## 单项工作的闭环

1. **明确范围**：读当前状态和真实代码。较大工作在 BACKLOG 复用或新增 `AM-NNN`；写目标、涉及模块、验收条件和不包含的内容。用户临时要求的很小修正可以直接记变更和提交。
2. **开始实施**：状态变为进行中，记录日期和实际负责人/当前开发者。只推进已授权范围；建议项留在候选，不默认开始。
3. **实现与验证**：复用已有模块，执行相应测试。遇到阻塞写出具体缺少什么、已尝试什么、如何恢复，不写模糊的“待优化”。
4. **验收**：记录代码落点、命令、结果、环境和剩余限制。自动测试通过但实机行为未确认时标为待验收，不能把两者合并成已验证。
5. **提交与交接**：更新相关 STATUS/CHANGELOG/BACKLOG，检查暂存文件，提交一个可解释的变化。后续任务记录可引用已存在的短提交号；不要为了把提交自己的哈希写进自己而循环 amend。

## 状态定义

| 状态 | 含义 |
| --- | --- |
| 候选 | 值得考虑，尚未决定实施；不是承诺 |
| 待办 | 范围已明确，尚未开始 |
| 进行中 | 已开始，有明确下一步 |
| 阻塞 | 缺少必要条件，需写出解除条件 |
| 待验收 | 实现/准备完成，但约定的某项验证还没完成 |
| 已完成 | 验收满足，证据和限制已记录 |
| 暂缓 | 明确暂不推进，保留原因 |

进度用这些状态和具体证据表达，不用没有测量依据的完成百分比。优先级 P0/P1/P2 只用于已选定工作的排序；候选方向可以不排期。

## 任务条目模板

```text
AM-NNN · 标题
状态 / 优先级 / 更新日期 / 负责人：
来源：用户要求、缺陷、原规划 ID 或 GitHub Issue
目标与范围：
代码落点：
验收条件：
验证与证据：命令、结果、报告或已存在的提交链接
下一步 / 阻塞 / 剩余限制：
```

在 GitHub 创建 Issue 时，标题携带同一个 ID；把链接补回 BACKLOG，不再维护一份含义冲突的进度。没有 Issue 也可以继续用仓库文件。PR/提交推荐 `fix(chat): ...`、`feat(memory): ...`、`docs: ...` 等简短主题，并在正文关联任务 ID。

## 验证方式

在仓库根目录执行，Python 3.11+；已验证环境为 Windows、Python 3.13。首次安装使用 `python -m venv .venv`，再安装 `requirements-dev.txt`。不同日期/用途使用不同的测试临时目录，不把个人文件放进 pytest 的 basetemp。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
# 仅选择与本次变更有关的测试；这里是聊天变更的例子
.\.venv\Scripts\python.exe -m pytest tests/test_chat.py tests/test_speech.py tests/test_model_responses.py -q --basetemp .tmp\pytest-chat
# 首次入库、发布或跨模块修改时执行完整回归
.\.venv\Scripts\python.exe -m pytest -q --basetemp .tmp\pytest-full
```

`tests/conftest.py` 默认将 Qt 放在 offscreen 环境并隔离模型凭据，测试使用本地模拟服务。它不能证明真实 API、桌面输入法或真实游戏操作已经成功。

人设/聊天质量使用合成场景：普通闲聊、用户询问她自己的事、连续追问同一小事、只给短回应、工作倾诉、明确请求方案、纠正以及要求安静。检查是否无故反问、正反说教、把角色故事存成用户事实、改变已经讲过的关键细节，以及是否仍能完成实际任务；不要求逐字复现示例，也不把禁止问号当成目标。可记录样本数、模型、重试数和具体问题，不对少量样本作成功率保证。

验证报告至少写：日期、范围、执行环境、代码基线、命令、结果、未覆盖项。原始截图、API 诊断、Junit XML 和本地进程信息留在 `.local/` 或 `.tmp/`；入库只提交必要的脱敏摘要。

## 打包与发布

- 打包配置 `packaging/AmeathAgent.spec`（单文件夹，`_internal` + exe）；Windows 本机用 `tools/build_exe.ps1`，CI 用 `.github/workflows/release.yml`。新增运行时读取的仓库文件（文档、资源）要同时加入 spec 的 `datas`。新增打包进 exe 的第三方依赖时，在 `THIRD-PARTY-NOTICES.md` 补充许可与源码位置，并在 spec 中带上其许可文件。
- 打包版数据目录是 exe 旁的 `.local`；更新只替换 `_internal` 和 exe。不要把运行数据写进 `_internal`。
- 发布：整理 CHANGELOG 为 `## <版本>` 小节，同步三处版本号，推送 `v<版本>` 标签由 CI 发布。只有用户授权后才推送发布标签。

## 人设与资源

- 版本化主卡：`assets/personas/aemeath-cn-current.json`。`version=1` 与七字段保持兼容，各文本字段须满足 `Persona.from_dict` 的限制。
- 运行卡：`.local/persona.json` 或 `AMEATH_DATA_DIR` 下的对应文件。新安装在 UI 中导入版本化主卡；运行时不会自动覆盖用户自己的卡。
- 当前聊天还会加载 `ameath/chat.py` 的交互/输出规则。改主卡时检查两处是否冲突，不能只在资料库加入示例却声称已影响运行时。
- `docs/persona/2026-10-26-expanded/` 是原始研究与人设快照；后续改动更新 current，不覆写原始出处。角色原创日常与官方剧情、用户真实经历分开。
- `assets/` 保存运行所需的可分发资源和来源说明；`.local/pets/` 的机器私有资源不入库。第三方模组二进制按现有脚本另行准备。

## 发布与同步

推送前确认远端、分支、暂存文件和忽略规则；初次推送保留 main，后续协作建议按任务开分支。只有用户要求发布或推送时才执行相关远端变更。项目自 0.1.0 起以 AGPL-3.0 发布（用户 2026-11-06 决定），第三方组件见 `THIRD-PARTY-NOTICES.md`；更换许可需要项目所有者决定。

Unreleased 汇总尚未打标签的变化。正式发布时同步三个版本入口、把已经验收的条目归入版本节，并记录验证报告；未完成任务留在 BACKLOG。

## 仓库导航与常用命令

以下内容原在 README，2026-11-06 起移到这里；README 只保留面向使用者的简介。

### 项目导航

- [当前开发状态](STATUS.md)：已经实现的能力、代码入口和已知限制。
- [当前任务台账](BACKLOG.md)：任务状态、下一步、验收条件与证据。
- [开发流程与记录规范](DEVELOPMENT.md)：每次开发如何更新记录、测试与交接。
- [变更记录](../CHANGELOG.md)：已完成的用户可见变化。
- [更新计划](ROADMAP.md)：面向使用者的近期计划，应用内「版本与更新」页显示同一份内容。
- [开发助手约定](../AGENTS.md)：后续编码工作的仓库规则。
- [2026-10-26 验证摘要](validation/2026-10-26-initial-sync.md)：本次同步的验证范围。

仓库目录：`ameath/` 为运行代码，`tests/` 为自动化测试，`assets/` 为资源与可导入人设，`tools/` 为开发/桥接工具，`integrations/` 为第三方来源信息，`docs/` 为文档。`.local/`、`.tmp/`、`.venv/` 为本机数据、临时产物和环境，不上传 GitHub。历史规划保留在 `docs/ameath-development-plan-2026-10-26/`，当前进度以 STATUS 和 BACKLOG 为准。

### 开发与检查

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q --basetemp .tmp\pytest
.\.venv\Scripts\python.exe tools\preview.py
.\.venv\Scripts\python.exe tools\preview_pet.py
.\.venv\Scripts\python.exe tools\preview_pet_v2.py
.\.venv\Scripts\python.exe tools\preview_chat.py
```

检查覆盖阅读选区、原文检索、引用、离线与模型请求分离、本地 HTTP 序列化、重定向阻断、加密凭据、提示节奏、卡片、过期请求隔离，以及逐帧时序、动作优先级、移动、拖拽和屏幕边界。界面检查在独立的离屏画布上进行，只渲染 Ameath 自己，不捕获用户桌面。

`tools/preview.py` 会把应用界面预览写入 `.local/preview/`；这些是开发用界面预览，不是用户的桌面截图。

`tools/preview_pet.py` 使用实际的桌宠播放器和本机 Snowcap 资源，生成 `.local/preview/ameath-snowcap.gif` 及动作联系图。GIF 编码使用开发依赖 Pillow，应用运行不需要 Pillow。

`tools/preview_pet_v2.py` 生成 `.local/preview/ameath-motion-v2.gif`，展示无气泡待机、二维移动、转身、坐下、睡眠、唤醒和完整飞行过程。

`tools/preview_chat.py` 用合成的本地演示数据渲染聊天框、人设卡和长期记忆界面；不调用真实模型，不读取真实聊天记录或捕获桌面。

`tools/preview_game.py` 渲染游戏面板的演示预览；`tools/probe_sts2.py` 只读检查已启动的游戏桥接，不调用模型或执行动作。

### 代码入口

`ameath/app.py` 负责桌面流程；`chat.py` 与 `chat_ui.py` 管理聊天，`memory.py` 与 `companion_ui.py` 管理人设和长期记忆；`sprites.py` 校验和播放精灵图，`pet.py` 管理桌宠绘制、移动与拖拽；`reader.py` 与 `papers.py` 处理阅读与原文；`capture.py` 实现手动选区；`llm.py` 是所有技能共用的模型调用核心，`agent.py` 是阅读技能；`game.py` 与 `game_ui.py` 管理游戏桥接、决策、暂停与用量；`policy.py` 管理提示节奏；`storage.py` 保存卡片；`config.py` 管理配置与加密凭据。

后续工作的状态、优先级和验收统一记录在 [当前任务台账](BACKLOG.md)，阅读器、游戏和角色体验分别按实际证据推进。

### 依赖与分发

本项目的代码以 [GNU AGPL-3.0](../LICENSE) 发布。打包版同时分发 PySide6 / Qt（LGPL-3.0）和 PyMuPDF（AGPL-3.0），各组件的许可与源码位置见 [第三方许可说明](../THIRD-PARTY-NOTICES.md)；打包版的对应源码就是本仓库同版本标签下的代码。

这是非官方的个人同人项目。爱弥斯的名字、人设与相关形象属于原作版权方，不在本项目许可范围内；仓库中的角色资料、生成素材仅供个人学习和娱乐使用。本地语音模型不随仓库或打包版分发。

**打包**：Windows 上运行 `powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1`，得到 `dist\AmeathAgent\AmeathAgent.exe` 和 `dist\AmeathAgent-<版本>-win64.zip(.sha256)`。PyInstaller 配置在 `packaging/AmeathAgent.spec`。

**发布**（应用内更新读取的就是这里）：把 `CHANGELOG.md` 的 Unreleased 整理成 `## <版本>` 小节，同步 `pyproject.toml`、`ameath/__init__.py` 和 README 的版本号，提交后推送标签 `v<版本>`。GitHub Actions（`.github/workflows/release.yml`）会在 Windows 上测试、打包，并创建带 zip 和 `.sha256` 的 Release。手动运行该工作流只生成构建产物，不发布。
