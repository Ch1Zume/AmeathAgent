import json

from ameath.config import Config, Settings
from ameath.llm import Completion, ModelClient, tune_payload
from ameath.agent import Agent
from ameath.game import GameSession, DemoBridge, choices_for


def test_deepseek_is_the_default_core():
    settings = Settings()
    assert settings.endpoint == "https://api.deepseek.com"
    assert settings.model == "deepseek-flash"
    assert settings.protocol == "chat"
    assert settings.game_thinking is True


def test_deepseek_uses_its_own_output_limit_and_thinking_toggle():
    original = {"max_completion_tokens": 8192, "model": "deepseek-flash"}
    fast = tune_payload(original, Settings())
    thoughtful = tune_payload(original, Settings(), thinking=True)
    assert fast["thinking"] == {"type": "disabled"}
    assert thoughtful["thinking"] == {"type": "enabled"}
    assert thoughtful["reasoning_effort"] == "high"
    assert thoughtful["max_tokens"] == 8192
    assert "max_completion_tokens" not in thoughtful
    assert original == {"max_completion_tokens": 8192, "model": "deepseek-flash"}


def test_deepseek_environment_key_is_used_only_for_deepseek_endpoint(tmp_path, monkeypatch):
    monkeypatch.delenv("AMEATH_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-token")
    config = Config(tmp_path)
    assert config.key() == "test-deepseek-token"
    config.settings.endpoint = "https://api.example.com/v1"
    assert config.key() == ""


def test_reading_and_games_use_the_same_core_and_final_json_only(tmp_path):
    config = Config(tmp_path)
    assert isinstance(Agent(config).client, ModelClient)
    config.settings.mode = "live"

    class Client:
        def complete_json(self, system, material, cancelled, **kwargs):
            assert "json" in system.lower()
            assert "provided_choices" in material
            assert "reasoning_content" not in json.dumps(material)
            assert kwargs.get("detail") == "sts2:" + material["task"]
            first = material["provided_choices"][0]
            step = {"kind": first["kind"], "name": first["name"], "target": first["target"]}
            return Completion(json.dumps({"steps": [step], "reason": "Test", "math": "6 ≥ 6", "lethal": True,
                                          "speech": [], "emotion": "thoughtful", "combat_strategy": ""}),
                              {"total_tokens": 123}, config.settings.model)

    session = GameSession(config, DemoBridge(), client=Client())
    decision = session.propose()
    assert decision.usage["total_tokens"] == 123
    assert session.budget.requests == 1
    assert session.budget.tokens == 123
    assert not decision.demo


def test_old_live_configuration_is_preserved_but_unconfigured_demo_migrates(tmp_path):
    config = Config(tmp_path)
    config.settings.endpoint = "https://api.openai.com/v1"
    config.settings.model = "gpt-4.1-mini"
    config.settings.protocol = "responses"
    config.save()
    assert Config(tmp_path).settings.endpoint == "https://api.deepseek.com"
    config.settings.mode = "live"
    config.save()
    assert Config(tmp_path).settings.endpoint == "https://api.openai.com/v1"
