import os
import pytest

# GUI checks render only the application on an isolated virtual surface.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolate_model_credentials(monkeypatch):
    for name in ("AMEATH_API_KEY", "VIOLET_API_KEY", "DEEPSEEK_API_KEY", "AMEATH_VISION_API_KEY"):
        monkeypatch.delenv(name, raising=False)
