# 定时屏幕观察验证

日期：2026-10-27（北京时间）。范围：AM-011 首次实现，基于本工作区未提交修改；保留已有语音及文档改动。

## 自动检查

Windows，Python 3.12.14、PySide6 6.12.0，Qt offscreen。原 `.venv` 所指 Python 3.13 路径当前不可用，使用 Codex 随附 Python 与安装在 `.tmp/screen-test-deps` 的临时依赖执行：

```powershell
$env:PYTHONPATH='D:\AmeathAgent\.tmp\screen-test-deps'
$env:QT_QPA_PLATFORM='offscreen'
$env:TEMP='D:\AmeathAgent\.tmp'
$env:TMP='D:\AmeathAgent\.tmp'
& 'C:\Users\zrq\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m pytest tests/test_screen_observation.py tests/test_companion.py tests/test_capture.py tests/test_chat.py tests/test_ui.py tests/test_core.py tests/test_transport.py tests/test_pet.py -q --basetemp .tmp\pytest-screen-final-3
```

结果：129 项通过。新增测试使用内存中的模拟帧和假视觉客户端，检查默认关闭、设置显示、独立密钥及载荷、空回复、动作约束、定时、安静、任务占用、用户消息取消、关闭、共享冷却与睡眠时触发。现有测试覆盖聊天、模型传输和桌宠回归；并非真实截图或视觉模型效果。`git diff --check` 通过。

## 未验证

- 未调用真实视觉服务；未验证论文、视频、聊天等画面的识别质量，或多轮求助、纠正和安静的语言表现。模型可能忽略要求或错误推测画面，不能保证输出稳定。
- 未在可见 Windows 桌面人工检查截图、锁屏、不同显示器和缩放、自动唤醒、气泡或语音。offscreen 不证明这些行为。
- 截图不写入本地文件，但会发送给配置的服务；无法撤回已发送请求。观察回复进入当前聊天历史，原始画面不会自动附给后续普通聊天；请求详细解释时仍需手动框选或提供内容。
