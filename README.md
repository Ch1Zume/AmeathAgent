# AmeathAgent

住在 Windows 桌面上的爱弥斯：会聊天、陪你读论文、提醒日程，还能替你打《杀戮尖塔 2》。当前版本 v0.1.0。

> 非官方的个人同人项目，与《鸣潮》官方及库洛游戏无关。

## 她能做什么

- **聊天**：单击桌宠就能聊；回复按句分成小气泡，可以用本地爱弥斯音色或 MiniMax 朗读。她记得你明确说过的偏好，也会延续以前聊过的事。
- **陪读论文**：打开 PDF，框选段落、公式或图表让她解释；外部阅读器里也能用 `Ctrl+Alt+E`（复制的文字）和 `Ctrl+Alt+S`（框选屏幕）。
- **日程与提醒**：在日程表里添加，或在聊天里说“明天下午三点提醒我开组会”，到时间她会提醒你。
- **日常陪伴**：天气问候、番茄钟、可选的主动搭话和定时屏幕观察；有夜间静默和每日上限，也可以让她安静两小时。
- **杀戮尖塔 2**：通过桥接模组读取局面，按增减益算伤害，按流派选牌、规划路线，一局结束后复盘；`Ctrl+Alt+P` 随时接管。
- **用量与更新**：按日/周/月查看模型 Token 用量；打包版可以在应用内一键更新。

## 开始使用

1. 从 [Releases](https://github.com/Ch1Zume/AmeathAgent/releases) 下载 `AmeathAgent-<版本>-win64.zip`，解压后运行 `AmeathAgent\AmeathAgent.exe`。
2. 在「模型设置」里选择 DeepSeek 预设并填入 API 密钥；不填也可以用本地演示体验。
3. 右键桌宠可以打开主界面、设置、日程、天气和游戏入口。

设置、记忆和日程都保存在 exe 旁边的 `.local\` 文件夹，更新不会覆盖。也可以从源码运行：安装 Python 3.11+ 后双击 `启动AmeathAgent.cmd`。

更多说明：[使用说明](docs/GUIDE.md) · [杀戮尖塔 2 接入](docs/sts2.md) · [更新计划](docs/ROADMAP.md) · [更新记录](CHANGELOG.md) · [开发文档](docs/DEVELOPMENT.md)

## Credits

- **角色**：爱弥斯出自库洛游戏《鸣潮》，名字、人设与形象归原作版权方所有。本项目的人设卡依据官方公开的 PV、短片和角色资料整理改编；人设里的日常小事是原创扮演内容。
- **桌宠形象**：基础形象是本机的 Snowcap 桌宠资源，不随项目分发。补充的移动、动作、飞行和入场动画由图像生成模型参考它绘制，生成记录见 `assets/*/generation.json`。
- **本地语音**（可选，不随项目分发）：[AemeathVoice](https://github.com/1983879947-ctrl/AemeathVoice) 角色音色模型，基于 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)，以及 G2PW 等组件。音色的使用限制以上游项目为准。
- **游戏**：《杀戮尖塔 2》（Mega Crit）；桥接模组 [STS2 AI Agent](https://github.com/CharTyr/STS2-Agent)，作者 CharTyr，许可 AGPL-3.0。
- **服务**：[DeepSeek](https://www.deepseek.com/) 提供对话与决策模型；可选 [MiniMax](https://www.minimaxi.com/) 语音合成；天气数据来自 [Open-Meteo](https://open-meteo.com/)（CC BY 4.0）。
- **开源组件**：[Qt for Python / PySide6](https://doc.qt.io/qtforpython-6/)（LGPL-3.0）、[PyMuPDF](https://github.com/pymupdf/PyMuPDF)（AGPL-3.0）、[PyInstaller](https://pyinstaller.org/)。详见 [第三方许可说明](THIRD-PARTY-NOTICES.md)。
- **开发**：[Ch1Zume](https://github.com/Ch1Zume)，部分代码、文档和素材在 AI 编程助手协助下完成。

## 许可

代码以 [GNU AGPL-3.0](LICENSE) 发布。角色相关内容不在许可范围内，仅供个人学习和娱乐。
