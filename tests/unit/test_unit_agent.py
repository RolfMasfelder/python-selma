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
