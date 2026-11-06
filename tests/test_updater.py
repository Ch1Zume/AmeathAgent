import hashlib
import io
import threading
import zipfile

import pytest

from ameath import __version__, updater
from ameath.config import Config
from ameath.updater import UpdateError, UpdateInfo, extract, parse_release, version_tuple
from ameath.updates_ui import UpdatesPanel, changelog_for_users
from test_ui import app  # noqa: F401


def release(tag="v9.0.0", with_checksum=True):
    name = f"AmeathAgent-{tag.lstrip('v')}-win64.zip"
    assets = [{"name": name, "browser_download_url": f"https://github.com/o/r/releases/download/{tag}/{name}", "size": 10}]
    if with_checksum:
        assets.append({"name": name + ".sha256", "browser_download_url": f"https://github.com/o/r/releases/download/{tag}/{name}.sha256"})
    return {"tag_name": tag, "body": "- 新功能", "html_url": "https://github.com/o/r/releases/tag/" + tag, "assets": assets}


def test_release_parsing_and_version_comparison():
    info = parse_release(release())
    assert info.version == "9.0.0" and info.newer and info.installable and info.notes == "- 新功能"
    assert not parse_release(release(with_checksum=False)).installable
    assert not parse_release(release("v" + __version__)).newer
    assert version_tuple("v0.10.0") > version_tuple("0.9.9")
    with pytest.raises(UpdateError):
        parse_release({"assets": []})


def test_only_https_github_hosts_are_contacted():
    with pytest.raises(UpdateError):
        updater._checked("http://github.com/x")
    with pytest.raises(UpdateError):
        updater._checked("https://example.com/AmeathAgent.zip")
    assert updater._checked("https://objects.githubusercontent.com/x")


def bundle(files: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class FakeResponse(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_download_verifies_sha256_and_extracts_the_app_folder(tmp_path, monkeypatch):
    data = bundle({"AmeathAgent/AmeathAgent.exe": b"exe", "AmeathAgent/_internal/base.dll": b"dll"})
    info = UpdateInfo("9.0.0", "", "", "https://github.com/a.zip", "AmeathAgent-9.0.0-win64.zip", len(data), "https://github.com/a.sha")
    good = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(updater, "_open", lambda url, timeout=20: FakeResponse(
        (good + "  AmeathAgent-9.0.0-win64.zip").encode() if url.endswith(".sha") else data))
    progress = []
    archive = updater.download(info, tmp_path, threading.Event(), lambda r, t: progress.append((r, t)))
    assert archive.read_bytes() == data and progress[-1] == (len(data), len(data))
    staged = extract(archive, tmp_path)
    assert (staged / "AmeathAgent.exe").read_bytes() == b"exe" and (staged / "_internal").is_dir()

    monkeypatch.setattr(updater, "_open", lambda url, timeout=20: FakeResponse(
        ("0" * 64).encode() if url.endswith(".sha") else data))
    with pytest.raises(UpdateError, match="校验失败"):
        updater.download(info, tmp_path / "bad", threading.Event())
    assert not list((tmp_path / "bad").glob("*.zip")) and not list((tmp_path / "bad").glob("*.part"))


def test_extract_rejects_unsafe_paths_and_wrong_layout(tmp_path):
    unsafe = tmp_path / "unsafe.zip"
    unsafe.write_bytes(bundle({"../evil.txt": b"x", "AmeathAgent.exe": b"x"}))
    with pytest.raises(UpdateError, match="不安全"):
        extract(unsafe, tmp_path)
    wrong = tmp_path / "wrong.zip"
    wrong.write_bytes(bundle({"readme.txt": b"x"}))
    with pytest.raises(UpdateError, match="结构不正确"):
        extract(wrong, tmp_path)


def test_source_runs_cannot_apply_in_place(tmp_path):
    with pytest.raises(UpdateError, match="git pull"):
        updater.launch_apply(tmp_path, tmp_path)


def test_updates_panel_shows_plan_changelog_and_reports_new_versions(app, tmp_path):
    panel = UpdatesPanel(Config(tmp_path))
    assert panel.tabs.count() == 3
    assert "更新计划" in panel.plan.toPlainText()
    assert panel.log.toPlainText().startswith("Unreleased")
    found = []
    panel.update_available.connect(found.append)
    panel.generation = 1
    panel.on_checked(1, parse_release(release()))
    assert found and "9.0.0" in panel.status.text()
    assert not panel.install_button.isEnabled()  # source run: no in-place install
    panel.on_checked(0, parse_release(release("v10.0.0")))  # stale result is ignored
    assert panel.info.version == "9.0.0"
    assert changelog_for_users("# 标题\n前言\n## Unreleased\n- a").startswith("## Unreleased")
