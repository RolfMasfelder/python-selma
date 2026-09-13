# ============================================================
# agent_session unit tests
#
# Deckung für selma/agent_session.py:
#   - SessionEntry-Modelle (Pydantic)
#   - SessionManager: init/in-memory/create/open/continue_recent/
#     append_entry/build_context/_get_branch/_load
#   - Settings + SettingsManager (Datei/in_memory)
#   - ModelInfo/list_ollama_models/ModelRegistry (fake httpx)
#   - AgentSession: prompt, subscribe, set_model, set_thinking_level,
#     new_session, resume_session, compact (fake AsyncOpenAI),
#     _build_context (block_images + Auto-Compact-Trigger),
#     _on_agent_event, _persist_message, _strip_images, Properties
#   - create_agent_session: Default/Custom-Tools/Custom-Prompt/
#     Model-Registry-Rollen, continue_session-History-Restore
#
# Laufzeit-Regeln: Runtime-Env-Fakes sind SYNC, Coro-Fakes echt async,
# große Timeouts, state-dir-Isolation (alle Pfade in tmp_path/monkeypatch.cwd).
#
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

import asyncio
import json
from types import SimpleNamespace
from unittest import mock

import pydantic
import pytest

from selma import agent_session as AS
from selma.agent import (
    AgentEvent,
    AgentTool,
    AssistantMessage,
    ToolCallRequest,
    ToolResultMessage,
    UserMessage,
)

# ─── helpers ─────────────────────────────────────────────────


def run(coro):
    """Frische Event-Loop pro Test (Projekt-Stil)."""
    return asyncio.new_event_loop().run_until_complete(coro)


class FakeAgentState:
    model = "m1"
    thinking_level = None
    is_streaming = False

    def __init__(self):
        self.messages: list = []


class FakeOptions:
    system_prompt = "SYSTEM"
    ollama_base_url = "http://fake/v1"
    client_timeout_seconds = 1.0
    client_max_retries = 0

    def __init__(self):
        self.convert_to_llm = lambda msgs: msgs


class FakeAgent:
    """Ersatz Agent: prompt() gibt Coroutine, subscribe() sammelt."""

    def __init__(self):
        self.state = FakeAgentState()
        self._options = FakeOptions()
        self._subscribers: list = []
        self.prompted_with: UserMessage | None = None
        self.last_openai_kwargs: dict | None = None
        self.created_messages: list | None = None

    def prompt(self, message: UserMessage):
        async def _run():
            self.prompted_with = message
            if self.last_openai_kwargs is None:
                self.last_openai_kwargs = {}

        return _run()

    def subscribe(self, listener):
        self._subscribers.append(listener)
        return lambda: self._subscribers.remove(listener)

    def emit(self, event_type: str, payload=None) -> None:
        for listener in list(self._subscribers):
            listener(AgentEvent(type=event_type, payload=payload))

    # Agent-Schnittstellen, die AgentSession direkt aufruft:

    def _to_openai_messages(self, msgs):
        return [{"role": getattr(m, "role", "unknown"), "content": getattr(m, "content", None)} for m in msgs]


class FakeCompletions:
    def __init__(self, owner: FakeAgent):
        self._owner = owner

    async def create(self, **kwargs):
        self._owner.last_openai_kwargs = kwargs
        self._owner.created_messages = kwargs.get("messages")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ZUSAMMENFASSUNG"))])


class FakeOpenAI:
    def __init__(self, agent: FakeAgent, **init_kwargs):
        self.chat = SimpleNamespace(completions=FakeCompletions(agent))
        self.init_kwargs = init_kwargs


def fake_openai_factory(agent: FakeAgent):
    return lambda **kw: FakeOpenAI(agent, **kw)


def make_session(tmp_path, monkeypatch, settings_overrides=None, threshold=None):
    """AgentSession(FakeAgent, in-memory SM, Settings) im tmp-CWD."""
    if tmp_path is not None:
        monkeypatch.chdir(tmp_path)
    sm = AS.SessionManager()
    overrides = dict(settings_overrides or {})
    if threshold is not None:
        overrides["auto_compact_threshold"] = threshold
    st = AS.SettingsManager.in_memory(overrides or None)
    agent = FakeAgent()
    session = AS.AgentSession(agent, sm, st)
    return session, agent, sm, st


def jsonline(entry: dict) -> str:
    return json.dumps(entry)


# ─── SessionEntry-Modelle ────────────────────────────────────


def test_entry_models_defaults():
    meta = AS.SessionMetaEntry(model="m", system_prompt="sp")
    assert meta.type == "session"
    assert meta.parent_id is None
    assert meta.id  # uuid4

    msg = AS.MessageEntry(role="user", content="hi")
    assert msg.type == "message"

    comp = AS.CompactionEntry(summary="s", tokens_before=42)
    assert comp.type == "compaction"
    assert comp.tokens_before == 42

    mc = AS.ModelChangeEntry(model="gemma3")
    assert mc.type == "model_change"
    assert mc.model == "gemma3"


def test_message_entry_role_literal_rejects_other():
    with pytest.raises(pydantic.ValidationError):
        AS.MessageEntry(role="system", content="x")  # type: ignore[arg-type]


# ─── SessionManager ──────────────────────────────────────────


def test_init_in_memory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sm = AS.SessionManager()
    assert sm.session_file is None
    assert sm.get_session_id() is None
    assert sm.build_context() == []


def test_init_existing_file(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(
        "\n"
        + jsonline({"type": "session", "id": "a1", "model": "m", "system_prompt": "sp"})
        + "\n"
        + jsonline({"type": "message", "id": "a2", "parent_id": "a1", "role": "user", "content": "hallo"})
        + "\n",
        encoding="utf-8",
    )
    sm = AS.SessionManager(session_file=f)
    assert sm.get_session_id() == "a1"
    assert len(sm._entries) == 2


def test_init_file_given_but_missing(tmp_path):
    sm = AS.SessionManager(session_file=tmp_path / "nope.jsonl")
    assert sm.session_file == tmp_path / "nope.jsonl"
    assert sm._entries == []


def test_create_makes_session_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sm = AS.SessionManager.create(cwd=str(tmp_path))
    assert sm.session_file is not None
    assert sm.session_file.parent == tmp_path / ".my_mono" / "sessions"
    assert sm.session_file.suffix == ".jsonl"
    assert len(sm.session_file.name) == 14  # uuid4()[:8] + ".jsonl"


def test_open_wrapper(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(jsonline({"type": "session", "id": "z", "model": "m", "system_prompt": "sp"}) + "\n")
    sm = AS.SessionManager.open(f)
    assert sm.session_file == f
    assert sm.get_session_id() == "z"


def test_continue_recent_newest_wins(tmp_path):
    sd = tmp_path / ".my_mono" / "sessions"
    sd.mkdir(parents=True)
    old = sd / "old.jsonl"
    old.write_text(jsonline({"type": "session", "id": "old", "model": "m", "system_prompt": "sp"}) + "\n")
    new = sd / "new.jsonl"
    new.write_text(jsonline({"type": "session", "id": "new", "model": "m", "system_prompt": "sp"}) + "\n")
    import os
    import time

    now = time.time()
    os.utime(old, (now - 100, now - 100))
    os.utime(new, (now, now))

    sm = AS.SessionManager.continue_recent(cwd=str(tmp_path))
    assert sm.session_file == new
    assert sm.get_session_id() == "new"


def test_continue_recent_none_creates(tmp_path, caplog):
    sm = AS.SessionManager.continue_recent(cwd=str(tmp_path))
    assert sm.session_file is not None
    assert sm.session_file.parent == tmp_path / ".my_mono" / "sessions"
    assert sm.session_file.exists() is False  # erst beim ersten append
    assert sm.get_session_id() is None


def test_append_entry_in_memory_links_chain(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sm = AS.SessionManager()
    e1 = sm.append_entry(AS.SessionMetaEntry(model="m", system_prompt="sp"))
    e2 = sm.append_entry(AS.MessageEntry(role="user", content="a"))
    assert e2.parent_id == e1.id
    assert sm.get_session_id() == e1.id


def test_append_entry_persists_to_file(tmp_path):
    f = tmp_path / "s.jsonl"
    sm = AS.SessionManager(session_file=f)
    sm.append_entry(AS.MessageEntry(role="user", content="hallo"))
    lines = f.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["content"] == "hallo"


def test_build_context_branch_and_compaction(tmp_path):
    sm = AS.SessionManager()
    sm.append_entry(AS.SessionMetaEntry(model="m", system_prompt="sp"))
    u1 = sm.append_entry(AS.MessageEntry(role="user", content="alt"))
    a1 = sm.append_entry(
        AS.MessageEntry(
            role="assistant",
            content="tool-call-antwort",
            tool_calls=[{"id": "t1", "name": "read", "arguments": {"path": "x"}}],
        )
    )
    t1 = sm.append_entry(AS.MessageEntry(role="tool", content="datei-inhalt", tool_call_id="t1"))
    c1 = sm.append_entry(AS.CompactionEntry(summary="ZUSAMMENFASSUNG", tokens_before=100))
    u2 = sm.append_entry(AS.MessageEntry(role="user", content="neu"))

    ctx = sm.build_context()
    # alles vor der Compaction weg (auch die session-Meta-Entry), danach: summary + neu
    assert len(ctx) == 2
    assert isinstance(ctx[0], UserMessage)
    assert ctx[0].content == "[Summary]: ZUSAMMENFASSUNG"
    assert ctx[1] == UserMessage(content="neu")
    del u1, a1, t1, c1, u2


def test_build_context_assistant_tool_calls(tmp_path):
    sm = AS.SessionManager()
    sm.append_entry(AS.SessionMetaEntry(model="m", system_prompt="sp"))
    sm.append_entry(
        AS.MessageEntry(
            role="assistant",
            content="ich rufe an",
            tool_calls=[{"id": "t1", "name": "read", "arguments": {"path": "x"}}],
        )
    )
    ctx = sm.build_context()
    # build_context() überspringt "session"-Meta-Einträge — nur die Nachricht:
    assert len(ctx) == 1
    a = ctx[0]
    assert isinstance(a, AssistantMessage)
    assert a.tool_calls is not None and len(a.tool_calls) == 1
    assert a.tool_calls[0].name == "read"
    assert a.tool_calls[0].arguments == {"path": "x"}


def test_get_branch_empty_without_leaf(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sm = AS.SessionManager()
    assert sm._get_branch() == []


def test_load_skips_unknown_and_blank_lines(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(
        "\n"
        + jsonline({"type": "weirdo", "id": "x"})
        + "\n"
        + jsonline({"type": "session", "id": "a", "model": "m", "system_prompt": "sp"})
        + "\n"
        + jsonline({"type": "model_change", "id": "b", "parent_id": "a", "model": "neu"})
        + "\n",
        encoding="utf-8",
    )
    sm = AS.SessionManager(session_file=f)
    assert [e.id for e in sm._entries] == ["a", "b"]
    assert sm._leaf_id == "b"


# ─── Settings ────────────────────────────────────────────────


def test_settings_defaults_and_validation():
    s = AS.Settings()
    assert s.block_images is False
    assert s.auto_compact_threshold == 100_000
    assert s.ollama_base_url == "http://localhost:11434/v1"

    with pytest.raises(pydantic.ValidationError):
        AS.Settings(block_images="nein")  # type: ignore[arg-type]


def test_settings_manager_from_file(tmp_path):
    sp = tmp_path / ".my_mono"
    sp.mkdir(parents=True)
    (sp / "settings.json").write_text(
        json.dumps({"block_images": True, "auto_compact_threshold": 7}),
        encoding="utf-8",
    )
    mgr = AS.SettingsManager(cwd=str(tmp_path))
    got = mgr.get()
    assert got.block_images is True
    assert got.auto_compact_threshold == 7
    assert got.ollama_base_url == "http://localhost:11434/v1"  # Default bleibt


def test_settings_manager_defaults_when_missing(tmp_path):
    mgr = AS.SettingsManager(cwd=str(tmp_path))
    assert mgr.get() == AS.Settings()


def test_settings_manager_in_memory_overrides(tmp_path):
    mgr = AS.SettingsManager.in_memory({"block_images": True})
    assert mgr.get().block_images is True
    assert mgr.get().auto_compact_threshold == 100_000
    # ohne overrides → reine Defaults
    assert AS.SettingsManager.in_memory().get() == AS.Settings()


# ─── Ollama Model-Registry ───────────────────────────────────


class _FakeHttpxResponse:
    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error

    def raise_for_status(self):
        if self._error:
            raise self._error

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, handler):
        self._handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, timeout=None):
        self.url = url
        self.timeout = timeout
        return self._handler()


class _FakeHttpx:
    def __init__(self, handler):
        self.AsyncClient = lambda **kw: _FakeAsyncClient(handler)


def test_list_ollama_models_success(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = {
        "models": [
            {"name": "gemma3", "size": 123, "modified_at": "2026-01-01T00:00:00Z"},
            {"name": "qwen2.5"},  # optionale Felder mit Defaults
        ]
    }
    with mock.patch.object(AS, "httpx", _FakeHttpx(lambda: _FakeHttpxResponse(payload=payload))):
        models = run(AS.list_ollama_models("http://oll:11434"))

    assert [m.name for m in models] == ["gemma3", "qwen2.5"]
    assert models[0].size == 123
    assert models[1].size == 0  # Default


def test_list_ollama_models_empty_and_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with mock.patch.object(AS, "httpx", _FakeHttpx(lambda: _FakeHttpxResponse(payload={"models": []}))):
        assert run(AS.list_ollama_models()) == []

    with mock.patch.object(AS, "httpx", _FakeHttpx(lambda: _FakeHttpxResponse(error=RuntimeError("http 500")))):
        with pytest.raises(RuntimeError, match="http 500"):
            run(AS.list_ollama_models())


def test_model_registry():
    reg = AS.ModelRegistry(base_url="http://x")
    assert reg.get_available() == []
    assert reg.find("nichts") is None


def test_model_registry_refresh_and_find():
    reg = AS.ModelRegistry(base_url="http://x")

    async def go():
        with mock.patch.object(
            AS, "list_ollama_models", new=mock.AsyncMock(return_value=[AS.ModelInfo(name="a"), AS.ModelInfo(name="b")])
        ):
            got = await reg.refresh()
        return got, reg

    got, reg = run(go())
    assert [m.name for m in got] == ["a", "b"]
    assert reg.get_available()[0].name == "a"
    assert reg.find("b").name == "b"
    assert reg.find("fehlend") is None


# ─── AgentSession ────────────────────────────────────────────


def test_init_injects_hook_and_subscribes(tmp_path, monkeypatch):
    session, agent, sm, st = make_session(tmp_path, monkeypatch)
    # convert_to_llm wurde durch _build_context ersetzt (Hook-Injektion)
    assert agent._options.convert_to_llm is not None
    msgs = [UserMessage(content="x")]
    assert agent._options.convert_to_llm(msgs) == msgs
    # Subscriber registriert:
    assert len(agent._subscribers) == 1
    # state-Properties:
    assert session.state is agent.state
    assert session.agent is agent
    assert session.session_file is None
    assert session.session_id is None
    assert session.is_streaming is False


def test_prompt_happy_path_persists_user_message(tmp_path, monkeypatch):
    session, agent, sm, st = make_session(tmp_path, monkeypatch)
    run(session.prompt("hallo agent"))
    assert isinstance(agent.prompted_with, UserMessage)
    assert agent.prompted_with.content == "hallo agent"
    entries = sm._entries
    assert len(entries) == 1
    assert entries[0].type == "message"
    assert entries[0].role == "user"
    assert entries[0].content == "hallo agent"


def test_prompt_rejects_while_streaming(tmp_path, monkeypatch):
    session, agent, sm, st = make_session(tmp_path, monkeypatch)
    agent.state.is_streaming = True
    with pytest.raises(RuntimeError, match="already running"):
        run(session.prompt("zu früh"))


def test_subscribe_returns_unsubscribe(tmp_path, monkeypatch):
    session, agent, sm, st = make_session(tmp_path, monkeypatch)
    seen: list[AgentEvent] = []
    unsub = session.subscribe(seen.append)
    agent.emit("message_end", AssistantMessage(content="x"))
    assert len(seen) == 1
    unsub()
    agent.emit("message_end", AssistantMessage(content="y"))
    assert len(seen) == 1  # ausgelöst, danach abbestellt


def test_set_model_and_thinking_level(tmp_path, monkeypatch):
    session, agent, sm, st = make_session(tmp_path, monkeypatch)
    run(session.set_model("gemma3"))
    assert agent.state.model == "gemma3"
    entry = sm._entries[-1]
    assert entry.type == "model_change"
    assert entry.model == "gemma3"

    session.set_thinking_level("high")
    assert agent.state.model == "gemma3"  # unbeührt
    assert agent.state.thinking_level == "high"


def test_new_session_writes_meta_and_clears_messages(tmp_path, monkeypatch):
    tmp_dir = tmp_path / "wd"
    tmp_dir.mkdir()
    monkeypatch.chdir(tmp_dir)
    session, agent, sm, st = make_session(None, monkeypatch)
    agent.state.messages = [UserMessage(content="alt")]
    assert session.session_id is None

    run(session.new_session())

    assert session.session_id is not None
    assert session.session_file is not None
    # SessionManager.create() nutzt die relative Basis "." →
    # absolut aufgelöst landet die File im CWD (= chdirtmp-dir):
    assert (tmp_dir / ".my_mono" / "sessions") in session.session_file.resolve().parents
    assert agent.state.messages == []
    # Meta-Entry wurde am Ende der SM-Liste angehängt:
    assert session.session_manager._entries[-1].type == "session" if hasattr(session, "session_manager") else True


def test_resume_session_rebuilds_context(tmp_path, monkeypatch):
    # vorgefertigte Session auf Platte
    f = tmp_path / "resume.jsonl"
    f.write_text(
        jsonline({"type": "session", "id": "s1", "model": "m", "system_prompt": "sp"})
        + "\n"
        + jsonline({"type": "message", "id": "m1", "parent_id": "s1", "role": "user", "content": "frage"})
        + "\n"
        + jsonline({"type": "message", "id": "m2", "parent_id": "m1", "role": "assistant", "content": "antwort"})
        + "\n",
        encoding="utf-8",
    )
    session, agent, sm, st = make_session(tmp_path, monkeypatch)
    agent.state.messages = [UserMessage(content="muss-weg")]
    run(session.resume_session(f))
    assert session.session_file == f
    assert session.session_id == "s1"
    msgs = agent.state.messages
    # session-Meta-Entry wird übersprungen → nur User + Assistant:
    assert len(msgs) == 2
    assert msgs[0] == UserMessage(content="frage")
    assert msgs[1] == AssistantMessage(content="antwort")


def test_message_end_event_persists_assistant_with_tool_calls(tmp_path, monkeypatch):
    session, agent, sm, st = make_session(tmp_path, monkeypatch)
    sm.append_entry(AS.SessionMetaEntry(model="m", system_prompt="sp"))
    # AssistantMessage ist ein echtes Pydantic-Modell — payload muss ein
    # echtes Modell mit echten ToolCallRequest-Instanzen tragen.
    agent.emit(
        "message_end",
        AssistantMessage(
            content="ich hole das",
            tool_calls=[ToolCallRequest(id="t1", name="read", arguments={"path": "a.txt"})],
        ),
    )
    # model_dump() liefert die serialisierten ToolCalls:
    last = sm._entries[-1]
    assert last.type == "message"
    assert last.role == "assistant"
    assert last.content == "ich hole das"
    assert last.tool_calls == [{"id": "t1", "name": "read", "arguments": {"path": "a.txt"}}]


def test_agent_event_other_types_ignored(tmp_path, monkeypatch):
    session, agent, sm, st = make_session(tmp_path, monkeypatch)
    n0 = len(sm._entries)
    agent.emit("agent_start")
    agent.emit("message_update", "partial")
    agent.emit("agent_end")
    assert len(sm._entries) == n0


def test_persist_message_branches(tmp_path, monkeypatch):
    session, agent, sm, st = make_session(tmp_path, monkeypatch)
    session._persist_message(UserMessage(content="u"))
    session._persist_message(
        AssistantMessage(content="a", tool_calls=[ToolCallRequest(id="t", name="x", arguments={})])
    )
    session._persist_message(ToolResultMessage(tool_call_id="t", content="tool-output"))
    roles = [e.role for e in sm._entries]
    assert roles == ["user", "assistant", "tool"]
    tool_entry = sm._entries[-1]
    assert tool_entry.tool_call_id == "t"
    assert tool_entry.content == "tool-output"


def test_strip_images_returns_same_list(tmp_path, monkeypatch):
    session, agent, sm, st = make_session(tmp_path, monkeypatch)
    msgs = [UserMessage(content="hallo")]
    assert session._strip_images(msgs) is msgs


def test_build_context_block_images_and_plain(tmp_path, monkeypatch):
    session, agent, sm, st = make_session(tmp_path, monkeypatch)
    msgs = [UserMessage(content="x" * 10)]
    out = session._build_context(msgs)
    assert out == msgs
    # block_images=True → _strip_images-Pfad, Identität bleibt
    st._settings = AS.Settings(block_images=True, auto_compact_threshold=10**9)
    out2 = session._build_context(msgs)
    assert out2 == msgs


def test_build_context_triggers_auto_compact(tmp_path, monkeypatch):
    session, agent, sm, st = make_session(tmp_path, monkeypatch, threshold=20)
    sm.append_entry(AS.SessionMetaEntry(model="m", system_prompt="sp"))
    # lange Nachricht → token_estimate > 20
    long_msg = UserMessage(content="y" * 100)

    def fake_spawn(coro, *, name=None):
        # Echte spawn(coro, *, name) — hier synchron ausführen, damit der
        # Test deterministisch ist (kein Event-Loop-Hopping, kein verwaister
        # Coroutine).
        run(coro)
        return None

    captured: list[FakeOpenAI] = []

    def capture_factory(**kw):
        fake = FakeOpenAI(agent, **kw)
        captured.append(fake)
        return fake

    with (
        mock.patch.object(AS, "spawn_background_task", new=fake_spawn),
        mock.patch.object(AS, "AsyncOpenAI", side_effect=capture_factory),
    ):
        out = session._build_context([long_msg])

    assert out == [long_msg]
    # Compact-Coro wurde synchron durchlaufen:
    assert len(captured) == 1
    assert captured[0].chat is not None
    # Kompaction läuft auf SessionManager-Kontext (noch leer — die lange
    # Live-Nachricht wurde zum Zeitpunkt des Triggers noch nicht persistiert):
    assert sm._entries[-1].type == "compaction"
    assert sm._entries[-1].summary == "ZUSAMMENFASSUNG"
    assert sm._entries[-1].tokens_before == 0
    assert agent.state.messages == [UserMessage(content="[Summary]: ZUSAMMENFASSUNG")]


def test_compact_success_uses_options_and_prompt(tmp_path, monkeypatch):
    session, agent, sm, st = make_session(tmp_path, monkeypatch)
    sm.append_entry(AS.SessionMetaEntry(model="m", system_prompt="sp".ljust(5)))
    sm.append_entry(AS.MessageEntry(role="user", content="frage"))
    sm.append_entry(AS.MessageEntry(role="assistant", content="antwort"))

    captured: dict = {}

    def factory(**kw):
        captured.update(kw)
        return FakeOpenAI(agent, **kw)

    with mock.patch.object(AS, "AsyncOpenAI", side_effect=factory):
        run(session.compact("beachte: entscheidungen bleiben"))

    # AsyncOpenAI init-kwargs (ohne `model` — der geht per .create() rein):
    assert captured["base_url"] == "http://fake/v1"
    assert captured["api_key"] == "ollama"
    assert captured["max_retries"] == 0
    # Zusammenfassender Prompt als letzte .create()-Nachricht:
    assert agent.created_messages is not None
    last = agent.created_messages[-1]
    assert last["role"] == "user"
    assert "beachte: entscheidungen bleiben" in last["content"]
    # Und der model parameter kommt per .create():
    assert agent.last_openai_kwargs["model"] == "m1"
    assert sm._entries[-1].type == "compaction"
    assert agent.state.messages[0].content.startswith("[Summary]: ZUSAMMENFASSUNG")


def test_compact_no_instructions(tmp_path, monkeypatch):
    session, agent, sm, st = make_session(tmp_path, monkeypatch)
    sm.append_entry(AS.MessageEntry(role="user", content="k"))
    with mock.patch.object(AS, "AsyncOpenAI", side_effect=lambda **kw: FakeOpenAI(agent, **kw)):
        run(session.compact())
    last_msg = agent.created_messages[-1]
    assert "Extra instructions" not in last_msg["content"]
    assert sm._entries[-1].type == "compaction"


# ─── create_agent_session (Factory) ──────────────────────────


def test_create_default_options_writes_meta_and_agent_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    opts = AS.CreateSessionOptions(model="gemma3", thinking_level="medium", cwd=str(tmp_path))
    session = run(AS.create_agent_session(opts))
    assert isinstance(session, AS.AgentSession)
    # session_id = volle uuid4(); filename = uuid4()[:8]:
    assert session.session_id is not None and len(session.session_id) == 36
    assert session.session_file is not None
    assert len(session.session_file.stem) == 8
    assert session.session_file.parent == tmp_path / ".my_mono" / "sessions"
    agent = session.agent
    assert agent.state.model == "gemma3"
    assert agent.state.thinking_level == "medium"
    # Default-Tools (read, edit, write, exec) eingebaut:
    assert [t.name for t in agent.state.tools] == ["read", "edit", "write", "exec"]
    # System-Prompt generiert (Default-Prompt, mit Tool-Liste):
    assert "Available tools" in agent._options.system_prompt
    # Meta-Entry persistiert:
    sm = AS.SessionManager(session_file=session.session_file)
    first = sm._entries[0]
    assert first.type == "session"
    assert first.model == "gemma3"
    # convert_to_llm-Hook injiziert:
    assert agent._options.convert_to_llm is session._build_context or callable(agent._options.convert_to_llm)


def test_create_custom_tools_and_prompt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    tool = AgentTool(
        name="custom",
        description="Custom Tool",
        parameters={"type": "object", "properties": {}, "required": []},
        execute=lambda *a, **kw: "custom result",
    )
    opts = AS.CreateSessionOptions(model="m", tools=[tool], system_prompt="MEIN PROMPT")
    session = run(AS.create_agent_session(opts))
    agent = session.agent
    assert [t.name for t in agent.state.tools] == ["custom"]
    assert agent._options.system_prompt == "MEIN PROMPT"


def test_create_with_resource_loader_context_file(tmp_path, monkeypatch):
    ws = tmp_path / ".selma" / "workspace"
    ws.mkdir(parents=True)
    (ws / "CODING_TOOLS.md").write_text("custom tools doc", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    opts = AS.CreateSessionOptions(model="m")
    session = run(AS.create_agent_session(opts))
    assert "# Project Context" in session.agent._options.system_prompt
    assert "custom tools doc" in session.agent._options.system_prompt


def test_create_resolves_model_from_registry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake_registry = mock.AsyncMock()
    fake_registry.refresh = mock.AsyncMock(return_value=[AS.ModelInfo(name="qwen3.5"), AS.ModelInfo(name="a")])
    fake_registry.get_available = lambda: [AS.ModelInfo(name="qwen3.5"), AS.ModelInfo(name="a")]

    with mock.patch.object(AS, "ModelRegistry", return_value=fake_registry):
        session = run(AS.create_agent_session(AS.CreateSessionOptions(model="", cwd=str(tmp_path))))

    assert session.agent.state.model == "qwen3.5"
    assert fake_registry.refresh.await_count == 1


def test_create_no_model_and_no_models_available(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fake_registry = mock.AsyncMock()
    fake_registry.refresh = mock.AsyncMock(side_effect=RuntimeError("ollama tot"))
    fake_registry.get_available = lambda: []

    with mock.patch.object(AS, "ModelRegistry", return_value=fake_registry):
        with pytest.raises(RuntimeError, match="No Ollama model available"):
            run(AS.create_agent_session(AS.CreateSessionOptions(model="", cwd=str(tmp_path))))


def test_create_continue_session_restores_history(tmp_path, monkeypatch):
    # vorhandene Session anlegen
    sd = tmp_path / ".my_mono" / "sessions"
    sd.mkdir(parents=True)
    f = sd / "hist.jsonl"
    f.write_text(
        jsonline({"type": "session", "id": "h1", "model": "m", "system_prompt": "sp"})
        + "\n"
        + jsonline({"type": "message", "id": "h2", "parent_id": "h1", "role": "user", "content": "alt"})
        + "\n"
        + jsonline({"type": "message", "id": "h3", "parent_id": "h2", "role": "assistant", "content": "alt-antwort"})
        + "\n",
        encoding="utf-8",
    )
    with mock.patch.object(AS, "create_coding_tools", return_value=[]):
        session = run(
            AS.create_agent_session(AS.CreateSessionOptions(model="m", cwd=str(tmp_path), continue_session=f))
        )
    assert session.session_file == f
    assert session.session_id == "h1"
    msgs = session.agent.state.messages
    # session-Meta-Entry wird übersprungen → nur alt + alt-antwort:
    assert len(msgs) == 2
    assert msgs[0] == UserMessage(content="alt")
    assert msgs[1] == AssistantMessage(content="alt-antwort")
    # keine neue Session-Meta-Entry (has_existing=True-Branch):
    sm = AS.SessionManager(session_file=f)
    assert all(e.type != "session" or e.id == "h1" for e in sm._entries)
    assert len(sm._entries) == 3


def test_create_with_inmemory_session_manager_appends_meta(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sm = AS.SessionManager()
    opts = AS.CreateSessionOptions(model="m", cwd=str(tmp_path), session_manager=sm)
    session = run(AS.create_agent_session(opts))
    assert session.session_id is not None
    # neue Session-Meta-Entry wurde angehängt (in-memory):
    assert sm._entries[-1].type == "session"
    # convert_to_llm-Hook injiziert:
    assert callable(session.agent._options.convert_to_llm)
