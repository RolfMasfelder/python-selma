# ============================================================
# agent.py unit tests
#
# Deckung für selma/agent.py (Agent):
#   - Pydantic-Defaults (AgentOptions/AgentState/ToolSchema/AgentEvent)
#   - prompt()/subscribe()/state (inkl. RuntimeError bei doppeltem Start)
#   - Run-Loop: Plain-Text-Stream (Multi-Chunk, leere Chunk)
#   - Run-Loop: Tool-Calls (sync + async Tool, unvollständig geloggte
#     Argumente-Akkumulation, unknown tool, Tool-Exception,
#     invalides JSON, Multi-Turn)
#   - thinking_level → reasoning_effort-Kwarg
#   - convert_to_llm-Hook
#   - LLM-Fehler → propagate + agent_end wird trotzdem emittiert
#   - Subscriber-Exception wird verschluckt
#
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

import asyncio
from types import SimpleNamespace

import pytest

from selma.agent import (
    Agent,
    AgentEvent,
    AgentOptions,
    AgentState,
    AgentTool,
    AssistantMessage,
    ToolCallRequest,
    ToolResultMessage,
    ToolSchema,
    UserMessage,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _chunk(content=None, tool_calls=None):
    """Ein Stream-Chunk: choices[0].delta mit optionalen content/tool_calls."""
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


_CHUNK_EMPTY = SimpleNamespace(choices=[])  # hält der Loop per `continue` aus


def _tc(index=0, tc_id=None, name=None, arguments=None):
    """Ein ToolCall-Delta (fragmente werden pro index akkumuliert)."""
    return SimpleNamespace(index=index, id=tc_id, function=SimpleNamespace(name=name, arguments=arguments))


def _tool(name, fn, description="d"):
    return AgentTool(name=name, description=description, parameters=ToolSchema(properties={}), execute=fn)


def _make_agent(turns, tools=(), system_prompt="", thinking_level=None, convert_to_llm=None, crash=False):
    """
    Erzeugt einen Agent, dessen OpenAI-Client statt network ein
    vorberechneten Stream-Turns abliefert (je create()-Aufruf ein Turn).
    Returns: (agent, create_calls)
    """
    kwargs = {"model": "test-model", "tools": list(tools), "system_prompt": system_prompt}
    if thinking_level is not None:
        kwargs["thinking_level"] = thinking_level
    if convert_to_llm is not None:
        kwargs["convert_to_llm"] = convert_to_llm
    agent = Agent(AgentOptions(**kwargs))

    calls: list[dict] = []
    it = iter(turns)

    async def fake_create(**kw):
        calls.append(kw)
        if crash:
            raise RuntimeError("boom-llm")
        turn = next(it)

        async def gen():
            for c in turn:
                yield c

        return gen()

    agent._client.chat.completions.create = fake_create
    return agent, calls


async def _run_prompt(agent, content="hi"):
    task = agent.prompt(UserMessage(content=content))
    await asyncio.wait_for(task, timeout=60)


class TestDataClasses:
    def test_defaults(self):
        opts = AgentOptions(model="m")
        assert opts.model == "m"
        assert opts.tools == []
        assert opts.system_prompt == ""
        assert opts.thinking_level is None
        assert opts.ollama_base_url == "http://localhost:11434/v1"
        assert opts.client_timeout_seconds == 3600.0
        assert opts.client_max_retries == 0
        assert opts.logging_event_filter == ["message_update"]
        ids = [UserMessage(content="x")]
        assert opts.convert_to_llm(ids) == ids

        state = AgentState()
        assert state.messages == []
        assert state.tools == []
        assert state.model == "llama3.1:8b"
        assert state.thinking_level is None
        assert state.is_streaming is False
        assert state.custom == {}

    def test_tool_schema_dump(self):
        ts = ToolSchema(properties={"a": {"type": "string"}}, required=["a"])
        assert ts.type == "object"
        dumped = ts.model_dump()
        assert dumped["properties"] == {"a": {"type": "string"}}
        assert dumped["required"] == ["a"]

    def test_agent_event(self):
        e = AgentEvent(type="prompt", payload="x")
        assert (e.type, e.payload) == ("prompt", "x")
        assert AgentEvent(type="x").payload is None

    def test_agent_init_state(self):
        tool = _tool("t", lambda: "ok")
        agent = Agent(AgentOptions(model="m", tools=[tool], thinking_level="low"))
        assert agent.state.model == "m"
        assert agent.state.tools == [tool]
        assert agent.state.thinking_level == "low"


class TestPromptBasics:
    def test_prompt_rejects_double_run(self):
        agent, _ = _make_agent([[_chunk(content="x")]])
        agent._state.is_streaming = True
        with pytest.raises(RuntimeError, match="already running"):
            agent.prompt(UserMessage(content="hi"))

    def test_subscribe_and_unsubscribe(self):
        agent, _ = _make_agent([])
        events = []
        unsub = agent.subscribe(events.append)
        agent._emit("prompt", "x")
        unsub()
        agent._emit("prompt", "y")
        assert [e.type for e in events] == ["prompt"]


class TestRunLoop:
    def test_plain_text_stream_with_events(self):
        turns = [[_CHUNK_EMPTY, _chunk(content="Hel"), _chunk(content="lo!")]]
        agent, calls = _make_agent(turns, system_prompt="sys")
        events = []
        agent.subscribe(events.append)

        _run(_run_prompt(agent))

        assert [e.type for e in events] == [
            "prompt",
            "agent_start",
            "turn_start",
            "message_update",
            "message_update",
            "turn_end",
            "message_end",
            "agent_end",
        ]
        assert agent.state.is_streaming is False
        assert [m.role for m in agent.state.messages] == ["user", "assistant"]
        assert agent.state.messages[1].content == "Hello!"

        # create()-Aufruf shape
        kw = calls[0]
        assert kw["model"] == "test-model"
        assert kw["stream"] is True
        assert kw["messages"][0] == {"role": "system", "content": "sys"}
        assert kw["messages"][1] == {"role": "user", "content": "hi"}
        assert "reasoning_effort" not in kw

    def test_tool_call_roundtrip_sync_and_async_tool(self):
        def sync_tool(value):
            return f"val={value}"

        async def async_tool(value):
            return f"aval={value}"

        tools = [_tool("echo", sync_tool), _tool("echo_async", async_tool)]
        # Turn 1: 2 Tool-Calls, Argumente von call_1 über 2 Chunks akkumuliert,
        # Name über 2 Chunks akkumuliert.
        turn1 = [
            _chunk(
                tool_calls=[
                    _tc(0, tc_id="call_1", name="ec", arguments='{"val'),
                    _tc(1, tc_id="call_2", name="echo_async", arguments='{"value": "y"}'),
                ]
            ),
            _chunk(tool_calls=[_tc(0, name="ho", arguments='ue": "x"}')]),
        ]
        turn2 = [_chunk(content="Ergebnis")]
        agent, _ = _make_agent([turn1, turn2], tools=tools)
        events = []
        agent.subscribe(events.append)

        _run(_run_prompt(agent))

        assert [e.type for e in events] == [
            "prompt",
            "agent_start",
            "turn_start",
            "turn_end",
            "message_end",
            "tool_start",
            "tool_end",
            "tool_start",
            "tool_end",
            "turn_start",
            "message_update",
            "turn_end",
            "message_end",
            "agent_end",
        ]
        assert [m.role for m in agent.state.messages] == ["user", "assistant", "tool", "tool", "assistant"]

        # Akkumulierte Tool-Calls auf der Assistant-Message
        assistant = agent.state.messages[1]
        assert len(assistant.tool_calls) == 2
        assert assistant.tool_calls[0].id == "call_1"
        assert assistant.tool_calls[0].name == "echo"
        assert assistant.tool_calls[0].arguments == {"value": "x"}

        # Tool-Ergebnisse
        assert agent.state.messages[2].tool_call_id == "call_1"
        assert agent.state.messages[2].content == "val=x"
        assert agent.state.messages[3].tool_call_id == "call_2"
        assert agent.state.messages[3].content == "aval=y"

    def test_unknown_tool_returns_error_content(self):
        turn1 = [_chunk(tool_calls=[_tc(0, tc_id="c1", name="ghost", arguments="{}")])]
        agent, _ = _make_agent([turn1, [_chunk(content="fertig")]])

        _run(_run_prompt(agent))

        tool_msg = agent.state.messages[2]
        assert tool_msg.role == "tool"
        assert tool_msg.content == "Error: unknown tool 'ghost'"

    def test_tool_exception_is_reported_as_content(self):
        def bad_tool():
            raise ValueError("kaputt")

        agent, _ = _make_agent(
            [[_chunk(tool_calls=[_tc(0, tc_id="c1", name="bad", arguments="{}")])], [_chunk(content="ok")]],
            tools=[_tool("bad", bad_tool)],
        )

        _run(_run_prompt(agent))

        assert agent.state.messages[2].content == "Error: kaputt"

    # ── Safety-Net: Tool-Turn-Event-Reihenfolge + Fehler-Pfade ──
    # Fixiert die Event-Reihenfolge (tool_start VOR tool_end, tool_end VOR
    # nächstem tool_start, Turn-2 turn_start VOR message_update) und das
    # gleichzeitige Text+Tool-Mixing — gilt weiterhin nach der P2#7-Refactor
    # von _run_loop (Stein #7: message_end bleibt WÄHREND des Streams, nicht
    # nach prompt-Return).

    def test_tool_error_events_and_loop_continues(self):
        """Tool-Exception: Loop bricht NICHT ab, tool_end wird trotzdem
        emittiert (finally-Pfad), die fehlerhafte Message trägt den Namen."""

        def bad_tool():
            raise ValueError("kaputt")

        agent, _ = _make_agent(
            [[_chunk(tool_calls=[_tc(0, tc_id="c1", name="bad", arguments="{}")])], [_chunk(content="ok")]],
            tools=[_tool("bad", bad_tool)],
        )
        events = []
        agent.subscribe(events.append)

        _run(_run_prompt(agent))

        # Exakte Reihenfolge — insbesondere tool_start < tool_end < turn_start(2)
        assert [e.type for e in events] == [
            "prompt",
            "agent_start",
            "turn_start",
            "turn_end",
            "message_end",
            "tool_start",
            "tool_end",
            "turn_start",
            "message_update",
            "turn_end",
            "message_end",
            "agent_end",
        ]
        start, end = events[5], events[6]
        assert start.payload.name == "bad"
        assert end.payload.name == "bad"
        assert start.payload is end.payload  # gleicher ToolCallRequest-Request
        # Loop läuft bis zum Abschluss
        assert agent.state.is_streaming is False
        assert agent.state.messages[-1].content == "ok"

    def test_text_and_tool_calls_in_same_turn(self):
        """Gleichzeitiges Text- + Tool-Handling: Text-Fragmente UND Tool-Call
        im selben Assistant-Block; der Text landet als content der
        Tool-Message, event-Reihenfolge bleibt Message-basiert."""

        def echo(value):
            return f"val={value}"

        turn1 = [
            _chunk(content="Kurz "),
            _chunk(content="gedacht…", tool_calls=[_tc(0, tc_id="c1", name="echo", arguments='{"value": "x"}')]),
        ]
        agent, _ = _make_agent([turn1, [_chunk(content="Ergebnis")]], tools=[_tool("echo", echo)])
        events = []
        agent.subscribe(events.append)

        _run(_run_prompt(agent))

        assert [e.type for e in events] == [
            "prompt",
            "agent_start",
            "turn_start",
            "message_update",
            "message_update",
            "turn_end",
            "message_end",
            "tool_start",
            "tool_end",
            "turn_start",
            "message_update",
            "turn_end",
            "message_end",
            "agent_end",
        ]
        # Assistant-Message: Text UND Tool-Call in einem Block
        assistant = agent.state.messages[1]
        assert assistant.role == "assistant"
        assert assistant.content == "Kurz gedacht…"
        assert len(assistant.tool_calls) == 1
        assert assistant.tool_calls[0].name == "echo"
        # Tool-Ergebnis folgt der Message
        assert agent.state.messages[2].role == "tool"
        assert agent.state.messages[2].content == "val=x"

    def test_invalid_json_arguments_fall_back_to_empty(self):
        calls = []

        def noargs(**kw):
            calls.append(kw)
            return "keine-args"

        agent, _ = _make_agent(
            [[_chunk(tool_calls=[_tc(0, tc_id="c1", name="n", arguments="not-json{")])], [_chunk(content="ok")]],
            tools=[_tool("n", noargs)],
        )

        _run(_run_prompt(agent))

        assert calls == [{}]
        assert agent.state.messages[2].content == "keine-args"

    def test_thinking_level_passed_as_reasoning_effort(self):
        agent, calls = _make_agent([[_chunk(content="x")]], thinking_level="high")

        _run(_run_prompt(agent))

        assert calls[0]["reasoning_effort"] == "high"

    def test_convert_to_llm_hook_called(self):
        seen = []

        def hook(msgs):
            seen.append([m.role for m in msgs])
            return msgs

        agent, _ = _make_agent([[_chunk(content="x")]], convert_to_llm=hook)

        _run(_run_prompt(agent))

        assert seen == [["user"]]

    def test_message_end_fires_while_prompt_task_still_running(self):
        """Stolperstein #7 als expliziter Regressionstest (P2#7):
        message_end muss WÄHREND prompt() gesendet werden — d. h. der
        prompt-Task darf zum Zeitpunkt des message_end-Events NOCH nicht
        abgeschlossen sein. Ein Refactor, der das Event nach prompt()-Return
        verschieben würde, bricht diesen Test."""
        agent, _ = _make_agent([[_chunk(content="fertig")]])
        firing: dict[str, bool] = {}
        task: asyncio.Task[None]

        def listener(event):
            if event.type == "message_end":
                firing["task_done"] = task.done()

        async def kick():
            nonlocal task
            task = agent.prompt(UserMessage(content="hi"))
            await asyncio.wait_for(task, timeout=60)

        agent.subscribe(listener)
        _run(kick())

        assert "task_done" in firing, "message_end wurde nie emittiert"
        assert firing["task_done"] is False, "message_end kam NACH prompt()-Return"


class TestFailurePaths:
    def test_llm_error_propagates_but_agent_end_emitted(self):
        agent, _ = _make_agent([], crash=True)
        events = []
        agent.subscribe(events.append)

        task = None

        async def kick():
            nonlocal task
            task = agent.prompt(UserMessage(content="hi"))

        _run(kick())
        with pytest.raises(RuntimeError, match="boom-llm"):
            _run(asyncio.wait_for(task, timeout=60))

        assert agent.state.is_streaming is False
        # agent_start kam an, agent_end muss trotz Fehler kommen
        assert [e.type for e in events][:2] == ["prompt", "agent_start"]
        assert events[-1].type == "agent_end"

    def test_subscriber_exception_does_not_break_loop(self):
        def bad_listener(event):
            raise RuntimeError("observer kaputt")

        agent, _ = _make_agent([[_chunk(content="x")]])
        agent.subscribe(bad_listener)
        events = []
        agent.subscribe(events.append)

        _run(_run_prompt(agent))

        assert [e.type for e in events] == [
            "prompt",
            "agent_start",
            "turn_start",
            "message_update",
            "turn_end",
            "message_end",
            "agent_end",
        ]


class TestEnsureUserQuery:
    """Regression: Qwen-Template-Guard (2026-11-14).

    Ollama (echtes HF-Template + Live-Repro 2026-11-14) wirft
    "No user query found in messages." (HTTP 500), wenn die Sendungsliste
    keinen einzigen user-Eintrag hat — z. B. [system, assistant(tool_calls),
    tool] nach Compaction. _ensure_user_query läuft seitdem vor JEDER
    completions.create und garantiert: mindestens 1 user-Eintrag mit
    nicht-leerem Text, sonst synthetischer Placeholder (vor erstem
    assistant, sonst ans Ende — NICHT blindes Tail-Append).
    """

    PLACEHOLDER = "(continue from the tool results above)"

    def test_passthrough_when_user_present(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hallo"},
            {"role": "assistant", "content": ""},
        ]
        out = Agent._ensure_user_query(msgs, [1])
        assert out is msgs  # unverändert (Identität), kein Copy

    def test_list_content_user_counts_as_present(self):
        msgs = [
            {"role": "assistant", "content": ""},
            {"role": "user", "content": [{"type": "text", "text": "hey"}]},
        ]
        assert Agent._ensure_user_query(msgs, [1]) is msgs

    def test_blank_user_does_not_count_and_placeholder_inserted(self):
        msgs = [
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "   "},
            {"role": "tool", "tool_call_id": "x", "content": "y"},
        ]
        out = Agent._ensure_user_query(msgs, [1])
        assert out is not msgs
        # vor dem ERSTEN assistant
        assert out[0] == {"role": "user", "content": self.PLACEHOLDER}
        assert [m["role"] for m in out] == ["user", "assistant", "user", "tool"]

    def test_case_h_no_user_at_all(self):
        # Exakte Fehlschlag-Form 2026-11-14: system + assistant(tool_calls) + tool
        msgs = [
            {"role": "system", "content": "Sys"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "web_fetch", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "Error: no content retrieved"},
        ]
        out = Agent._ensure_user_query(msgs, [1, 2, 3])
        assert len(out) == len(msgs) + 1
        assert out[0] == {"role": "system", "content": "Sys"}
        assert out[1] == {"role": "user", "content": self.PLACEHOLDER}
        assert out[2] is msgs[1]  # Original-Assistant-Entry unverändert
        assert [m["role"] for m in out] == ["system", "user", "assistant", "tool"]

    def test_no_assistant_appends_at_end(self):
        msgs = [{"role": "tool", "tool_call_id": "x", "content": "y"}]
        out = Agent._ensure_user_query(msgs, [1])
        assert [m["role"] for m in out] == ["tool", "user"]
        assert out[-1] == {"role": "user", "content": self.PLACEHOLDER}

    @pytest.fixture(autouse=True)
    def _isolated_state_dir(self, tmp_path, monkeypatch):
        """Alle selma-State-Isolation (siehe MEMORY.md Stolpersteine)."""
        state = tmp_path / "state"
        (tmp_path / ".selma").mkdir(exist_ok=True)
        monkeypatch.setenv("SELMA_STATE_DIR", str(state))
        yield state

    def test_create_call_never_sends_without_user(self):
        """End-to-End über den Loop: convert_to_llm filtert den user-Turn weg
        (Case-H-Zustand aus production) → completions.create bekommt TROTZDEM
        einen user-Eintrag, und der Loop läuft zu Ende (kein 500/Agent-Abbruch)."""
        agent, calls = _make_agent([[_chunk(content="fertig")]])

        def dropping_user_hook(msgs):
            # Simuliert Compaction/convert_to_llm: initialer user-Turn weg,
            # übrig bleibt assistant(tool_calls) + tool — exakt die
            # production-Fehlschlagform ohne user-Eintrag.
            out = [
                AssistantMessage(
                    content=None,
                    tool_calls=[ToolCallRequest(id="c1", name="web_fetch", arguments={})],
                ),
                ToolResultMessage(tool_call_id="c1", content="Error: no content retrieved"),
            ]
            assert [m.role for m in msgs] == ["user"]
            return out

        # Hook nachträglich injizieren (Options-Modell ist frozen-ish? → direkt Field)
        agent._options.convert_to_llm = dropping_user_hook

        _run(_run_prompt(agent))

        assert len(calls) == 1
        sent = calls[0]["messages"]
        assert any(m.get("role") == "user" for m in sent), "create() without user-Eintrag!"
        placeholder = [m for m in sent if m.get("role") == "user"]
        assert placeholder, "user-Eintrag gefunden aber leer?"
        assert any(str(m.get("content") or "").strip() for m in placeholder)
        assert agent.state.is_streaming is False
