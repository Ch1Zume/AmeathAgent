# 第三方许可说明

AmeathAgent 自身的代码以 [GNU AGPL-3.0](LICENSE) 发布。Windows 打包版（`AmeathAgent-<版本>-win64.zip`）还包含下列第三方组件；它们各自的许可证随打包版放在 `_internal/` 中（`LICENSE`、`licenses/`、各组件的 `*.dist-info/`）。

| 组件 | 用途 | 许可 | 源码 |
| --- | --- | --- | --- |
| Qt for Python（PySide6、Shiboken6）与 Qt 6 | 界面 | LGPL-3.0（见 `licenses/LGPL-3.0.txt` 与 `licenses/GPL-3.0.txt`） | https://code.qt.io/cgit/pyside/pyside-setup.git 、https://download.qt.io/official_releases/qt/ |
| PyMuPDF / MuPDF | PDF 阅读 | AGPL-3.0（或 Artifex 商业许可；本项目按 AGPL-3.0 使用） | https://github.com/pymupdf/PyMuPDF |
| CPython | 运行时 | Python Software Foundation License | https://github.com/python/cpython |
| PyInstaller 引导程序 | 打包 | GPL-2.0，附允许分发打包程序的例外条款 | https://github.com/pyinstaller/pyinstaller |

LGPL 组件以独立的动态库形式提供，可以用兼容版本替换 `_internal/` 中的对应文件。

**对应源码**：打包版的源码就是本仓库同版本标签（例如 `v0.1.0`）下的代码，构建方式见 README 的「依赖与分发」和 `packaging/AmeathAgent.spec`。

**不随本项目分发的内容**：《杀戮尖塔 2》桥接模组（STS2 AI Agent，AGPL-3.0）需要另外安装，来源与许可见 `integrations/sts2/`；本地语音模型（GPT-SoVITS 及音色）不随仓库或打包版分发。

**角色相关内容**：本项目是非官方的个人同人作品。爱弥斯的名字、人设与相关形象属于原作版权方，不在 AGPL-3.0 许可范围内。
