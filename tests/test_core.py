import json
import threading

import pymupdf
import pytest

from ameath.agent import Agent, AgentError, build_payload, demo_answer, parse_response
from ameath.config import Config, Settings, validate_endpoint
from ameath.models import Question, Source
from ameath.papers import Paper, make_sample
from ameath.policy import AttentionPolicy
from ameath.storage import Notebook


def test_context_keeps_current_page_then_retrieves_relevant_page_and_bounds_size(tmp_path):
    path = tmp_path / "paper.pdf"
    doc = pymupdf.open()
    for text in ["Opening context", "Softmax weights and attention", "Unrelated astronomy"]:
        page = doc.new_page()
        page.insert_text((40, 50), text)
    doc.save(path)
    doc.close()
    paper = Paper(path)
    try:
        sources = paper.context("softmax attention", 0)
        assert [s.page for s in sources] == [1, 2]
        assert all(s.paper_id == paper.id for s in sources)
        assert sum(len(s.text) for s in paper.context("attention", 0, budget=12)) <= 12
        assert "Opening" in paper.text_in_rect(0, (0, 0, 500, 100))
    finally:
        paper.close()


def test_sample_is_original_readable_and_has_no_missing_sections(tmp_path):
    paper = Paper(make_sample(tmp_path / "sample.pdf"))
    try:
        assert paper.count == 3
        assert "not a published research paper" in paper.pages[0]
        assert "sqrt(d_k)" in paper.pages[1]
        assert "Keep a concept card" in paper.pages[2]
        assert paper.image(0, 0.3).width > 0
    finally:
        paper.close()


def test_attention_requires_enabled_active_dwell_and_respects_pause_and_feedback():
    policy = AttentionPolicy(enabled=False, dwell=30, cooldown=120)
    policy.visit("paper:1", now=0)
    assert not policy.candidate(True, False, now=100)
    policy.resume(now=100)
    assert not policy.candidate(True, False, now=129)
    assert not policy.candidate(False, False, now=131)
    assert not policy.candidate(True, True, now=131)
    assert policy.candidate(True, False, now=131)
    policy.prompted(now=131)
    assert not policy.candidate(True, False, now=1000)
    policy.visit("paper:2", now=132)
    policy.feedback(False, now=132)
    assert not policy.candidate(True, False, now=300)
    assert policy.candidate(True, False, now=313)
    policy.pause()
    assert not policy.candidate(True, False, now=1000)


def test_demo_text_selection_with_image_does_not_claim_vision():
    source = Source("a", "Paper", 3, "Attention builds a summary.")
    answer = demo_answer(Question("Explain", selection="attention", sources=[source], image_png=b"image"))
    assert answer.demo and "固定演示卡片" in answer.text
    assert "source:1" in answer.text
    assert "第 3 页" in answer.text
    screenshot = demo_answer(Question("explain", image_png=b"image", external=True))
    assert "没有识别" in screenshot.text
    assert not screenshot.sources


def test_payload_protocols_images_and_source_mapping():
    q = Question("Explain it", selection="softmax", sources=[Source("id", "Paper", 7, "definition")],
                 image_png=b"png", history=[("user", "before"), ("assistant", "answer")])
    responses = build_payload(q, Settings(protocol="responses"))
    assert responses["store"] is False
    assert responses["input"][-1]["content"][1]["type"] == "input_image"
    material = json.loads(responses["input"][-1]["content"][0]["text"])
    assert material["sources"][0]["page"] == 7
    chat = build_payload(q, Settings(protocol="chat"))
    assert chat["messages"][0]["role"] == "system"
    assert chat["messages"][-1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_response_parser_rejects_empty_and_accepts_both_shapes():
    assert parse_response({"output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]}, "responses") == "OK"
    assert parse_response({"choices": [{"message": {"content": "Hi"}}]}, "chat") == "Hi"
    with pytest.raises(AgentError):
        parse_response({}, "responses")


@pytest.mark.parametrize("endpoint", ["http://evil.example/v1", "https://user:secret@example.com/v1", "https://api.example/v1?key=secret", "ftp://localhost/v1"])
def test_endpoint_rejects_credentials_and_insecure_remote_transport(endpoint):
    with pytest.raises(ValueError):
        validate_endpoint(endpoint)


def test_settings_do_not_persist_plaintext_credentials_and_corrupt_settings_recover(tmp_path, monkeypatch):
    monkeypatch.delenv("AMEATH_API_KEY", raising=False)
    config = Config(tmp_path)
    config.set_key("session-secret", False)
    config.save()
    assert "session-secret" not in config.path.read_text()
    assert config.key() == "session-secret"
    assert Config(tmp_path).key() == ""
    config.path.write_text('{"cooldown_seconds":"broken"}')
    assert Config(tmp_path).settings == Settings()


def test_demo_never_opens_network(tmp_path, monkeypatch):
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: pytest.fail("demo attempted network"))
    answer = Agent(Config(tmp_path)).answer(Question("attention"), threading.Event())
    assert answer.demo


def test_cards_are_explicit_and_deleteable(tmp_path):
    book = Notebook(tmp_path / "cards.sqlite3")
    assert not book.cards()
    book.save("id", "Title", 4, "query", "explanation")
    assert book.cards()[0]["page"] == 4
    book.delete(book.cards()[0]["id"])
    assert not book.cards()
    book.close()


def test_single_instance_lock_releases_cleanly(tmp_path):
    from ameath.instance import SingleInstance
    first = SingleInstance(tmp_path, activate_existing=False)
    second = SingleInstance(tmp_path, activate_existing=False)
    assert first.primary and not second.primary
    first.close()
    third = SingleInstance(tmp_path, activate_existing=False)
    assert third.primary
    third.close()


def test_renamed_app_reads_legacy_environment_and_prefers_new_names(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    current = tmp_path / "current"
    monkeypatch.setenv("VIOLET_DATA_DIR", str(legacy))
    monkeypatch.setenv("VIOLET_API_KEY", "legacy-test-token")
    config = Config()
    assert config.directory == legacy
    assert config.key() == "legacy-test-token"
    monkeypatch.setenv("AMEATH_DATA_DIR", str(current))
    monkeypatch.setenv("AMEATH_API_KEY", "new-test-token")
    config = Config()
    assert config.directory == current
    assert config.key() == "new-test-token"
