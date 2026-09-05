# ============================================================
# config.py — Unit-Tests (Pydantic-Modelle, Resolver, Caching, Fehlpfade)
# ============================================================

import json
import time
from typing import Any

import pytest
from pydantic import ValidationError

import selma.config as config_mod
from selma.config import (
    THINKING_LEVEL_ORDER,
    THINKING_LEVELS,
    AgentInfo,
    HeartbeatConfig,
    MemoryConfig,
    ModelConfig,
    SelmaConfig,
    get_default_model,
    load_config,
    resolve_thinking_default,
    resolve_timeout,
    resolve_tools_allow,
)

# ---------- Model-Defaults & Parsing =====================


class TestModels:
    def test_defaults(self):
        cfg = SelmaConfig()
        assert cfg.channels.telegram.enabled is False
        assert cfg.channels.webchat.host == "0.0.0.0"
        assert cfg.channels.webchat.port == 8000
        assert cfg.model.model == ""
        assert cfg.model.ollama_base_url == "http://localhost:11434/v1"
        assert cfg.agent.id == "main"
        assert cfg.heartbeat.every == "0m"
        assert cfg.memory.vector_search is False
        assert cfg.memory.embed_model == "nomic-embed-text"

    def test_heartbeat_and_memory_custom(self):
        hb = HeartbeatConfig(every="30m", target="last", ack_max_chars=100)
        assert hb.every == "30m"
        mc = MemoryConfig(vector_search=True, temporal_decay=True, temporal_decay_rate=0.1)
        assert mc.vector_search and mc.temporal_decay_rate == 0.1

    def test_agent_extra_fields_allowed(self):
        a = AgentInfo(id="x", extra_custom="yes")  # extra='allow'
        assert a.extra_custom == "yes"

    def test_agent_tools_allow_accepts_all_and_list(self):
        assert AgentInfo().toolsAllow == "all"
        a = AgentInfo(toolsAllow=["bash", "read"])
        assert a.toolsAllow == ["bash", "read"]

    def test_agent_tools_allow_rejects_bad_input(self):
        with pytest.raises(ValidationError):
            AgentInfo(toolsAllow=42)  # type: ignore[arg-type]

    def test_thinking_constants_consistency(self):
        assert THINKING_LEVELS == frozenset({"low", "medium", "high"})
        assert set(THINKING_LEVEL_ORDER) == THINKING_LEVELS


# ---------- SelmaConfig-Methode ============================


class TestSelmaConfigMethods:
    def test_is_channel_enabled_known_and_unknown(self):
        cfg = SelmaConfig()
        assert cfg.is_channel_enabled("telegram") is False
        cfg.channels.webchat.enabled = True
        assert cfg.is_channel_enabled("webchat") is True
        assert cfg.is_channel_enabled("doesnt_exist") is False

    def test_get_telegram_token(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
        assert SelmaConfig().get_telegram_token() is None
        monkeypatch.setenv("TELEGRAM_TOKEN", "sekret")
        assert SelmaConfig().get_telegram_token() == "sekret"


# ---------- load_config: Datei, Cache, Fehlpfaden ===========


@pytest.fixture()
def config_dir(tmp_path, monkeypatch):
    """Isolierte State-Dir pro Test via SELMA_STATE_DIR."""
    monkeypatch.setenv("SELMA_STATE_DIR", str(tmp_path / ".selma"))
    (tmp_path / ".selma").mkdir()
    yield tmp_path / ".selma"
    config_mod._config_cache.clear()


def test_load_config_parses_full_file(config_dir):
    config_dir.joinpath("selma.json").write_text(
        json.dumps(
            {
                "channels": {"telegram": {"enabled": True}, "webchat": {"port": 9999}},
                "model": {"model": "ollama/llama3.1", "thinking": "High", "timeout_seconds": 5},
                "agent": {"id": "a1", "toolsAllow": ["bash"]},
                "custom_extra": 1,
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config(cache=False)
    assert cfg.channels.telegram.enabled is True
    assert cfg.channels.webchat.port == 9999
    assert cfg.model.thinking == "High"
    assert cfg.model.timeout_seconds == 5
    assert cfg.agent.toolsAllow == ["bash"]
    assert cfg.custom_extra == 1  # extra='allow'


def test_load_config_missing_file_raises(config_dir):
    with pytest.raises(FileNotFoundError, match="Config file not found"):
        load_config(cache=False)


def test_load_config_invalid_json_raises_valueerror(config_dir):
    config_dir.joinpath("selma.json").write_text("{invalid json", encoding="utf-8")
    with pytest.raises(ValueError, match="Failed to parse JSON"):
        load_config(cache=False)


def test_load_config_unexpected_error_raises_runtimeerror(config_dir, monkeypatch):
    config_dir.joinpath("selma.json").write_text("{}", encoding="utf-8")

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("disk weg")

    monkeypatch.setattr("builtins.open", boom)
    with pytest.raises(RuntimeError, match="unexpected error"):
        load_config(cache=False)


def test_load_config_cache_hits_within_validity(config_dir):
    config_dir.joinpath("selma.json").write_text(json.dumps({"model": {"model": "a/b"}}), encoding="utf-8")
    load_config()  # cache=True
    # Datei nachträglich ändern → Cache-Treffer liefert noch den alten Wert
    config_dir.joinpath("selma.json").write_text(json.dumps({"model": {"model": "c/d"}}), encoding="utf-8")
    second = load_config()
    assert second.model.model == "a/b"
    # cache=False ignoriert den Cache
    assert load_config(cache=False).model.model == "c/d"


def test_load_config_cache_expires_after_validity(config_dir, monkeypatch):
    config_dir.joinpath("selma.json").write_text(json.dumps({"model": {"model": "a/b"}}), encoding="utf-8")
    load_config()
    # Cache-Eintrag künstlich altern → Reload
    _ = next(iter(config_mod._config_cache))
    real_time = time.time()
    monkeypatch.setattr(config_mod.time, "time", lambda: real_time + config_mod.CACHE_VALIDITY_SECONDS + 1)
    config_dir.joinpath("selma.json").write_text(json.dumps({"model": {"model": "c/d"}}), encoding="utf-8")
    assert load_config().model.model == "c/d"


# ---------- Resolver-Funktionen ============================


class TestGetDefaultModel:
    def test_empty_returns_default(self):
        assert get_default_model(SelmaConfig()) == ("ollama", "llama3.2")
        assert get_default_model(SelmaConfig(model=ModelConfig(model="   "))) == (
            "ollama",
            "llama3.2",
        )

    def test_provider_and_model_split(self):
        cfg = SelmaConfig(model=ModelConfig(model=" ollama / llama3.1 "))
        assert get_default_model(cfg) == ("ollama", "llama3.1")

    def test_no_slash_assumes_ollama(self):
        assert get_default_model(SelmaConfig(model=ModelConfig(model="mxbai-embed"))) == (
            "ollama",
            "mxbai-embed",
        )


class TestResolvers:
    def test_resolve_timeout_reads_config(self):
        assert resolve_timeout(SelmaConfig(model=ModelConfig(timeout_seconds=99))) == 99
        assert resolve_timeout(SelmaConfig()) == 60

    def test_resolve_tools_allow_variants(self):
        assert resolve_tools_allow(SelmaConfig()) is None  # "all"
        cfg = SelmaConfig(agent=AgentInfo(toolsAllow=["bash", "read"]))
        assert resolve_tools_allow(cfg) == ["bash", "read"]

    def test_resolve_thinking_default_ollama_returns_none(self):
        cfg = SelmaConfig(model=ModelConfig(thinking="high"))
        assert resolve_thinking_default(cfg, "ollama", "x") is None
        assert resolve_thinking_default(cfg, "OLLAMA", "x") is None

    def test_resolve_thinking_default_known_level_passthrough(self):
        for level in sorted(THINKING_LEVELS):
            cfg = SelmaConfig(model=ModelConfig(thinking=f" {level} "))
            assert resolve_thinking_default(cfg, "openai", "gpt") == level

    def test_resolve_thinking_default_unknown_falls_back_to_low(self):
        assert resolve_thinking_default(SelmaConfig(model=ModelConfig(thinking="turbo")), "openai", "gpt") == "low"
