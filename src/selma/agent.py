# ============================================================
# agent.py
#
# Agent loop using the OpenAI SDK directly.
# Works with Ollama's OpenAI-compatible endpoint.
# ============================================================

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any, Literal

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, Field

from selma.tracing import add_span_infos, trace_and_log, tracer

logger = logging.getLogger(__name__)


# ─── DATA STRUCTURES ────────────────────────────────────────


class ToolSchema(BaseModel):
    type: Literal["object"] = "object"
    properties: dict[str, Any]
    required: list[str] = Field(default_factory=list)


class AgentTool(BaseModel):
    name: str
    description: str
    parameters: ToolSchema
    execute: Callable[..., Any] = Field(exclude=True)

    model_config = {"arbitrary_types_allowed": True}


class ToolCallRequest(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class UserMessage(BaseModel):
    role: Literal["user"] = "user"
    content: str


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str | None = None
    tool_calls: list[ToolCallRequest] | None = None


class ToolResultMessage(BaseModel):
    role: Literal["tool"] = "tool"
    tool_call_id: str
    content: str


AgentMessage = UserMessage | AssistantMessage | ToolResultMessage

ThinkingLevel = Literal["low", "medium", "high"] | None


class AgentState(BaseModel):
    messages: list[AgentMessage] = Field(default_factory=list)
    tools: list[AgentTool] = Field(default_factory=list)
    model: str = "llama3.1:8b"
    thinking_level: ThinkingLevel = None
    is_streaming: bool = False
    custom: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}


class AgentOptions(BaseModel):
    model: str
    tools: list[AgentTool] = Field(default_factory=list)
    system_prompt: str = ""
    thinking_level: ThinkingLevel = None
    ollama_base_url: str = "http://localhost:11434/v1"
    # HTTP client read timeout and retry count for the underlying AsyncOpenAI client.
    client_timeout_seconds: float = 3600.0
    client_max_retries: int = 0
    logging_event_filter: list[str] = Field(default_factory=lambda: ["message_update"])
    convert_to_llm: Callable[[list[AgentMessage]], list[AgentMessage]] = Field(
        default=lambda msgs: msgs,
        exclude=True,
    )

    model_config = {"arbitrary_types_allowed": True}


class AgentEvent(BaseModel):
    type: str
    payload: Any = None


# ─── AGENT ──────────────────────────────────────────────────


class Agent:
    def __init__(self, options: AgentOptions):
        self._options = options
        self._state = AgentState(
            model=options.model,
            tools=options.tools,
            thinking_level=options.thinking_level,
        )
        self._subscribers: list[Callable[[AgentEvent], None]] = []
        self._client = AsyncOpenAI(
            base_url=options.ollama_base_url,
            api_key="ollama",  # Ollama ignores this, but the SDK requires a value
            # Default read=600s * max_retries=2 silently turns into a 30min hang on slow
            # (e.g. CPU-only) Ollama inference. The outer asyncio.wait_for in
            # execute_prompt() (runtime.py) already enforces config.model.timeout_seconds,
            # so these are configurable via ModelConfig instead of hardcoded.
            timeout=options.client_timeout_seconds,
            max_retries=options.client_max_retries,
        )
        logger.info("Agent initialized | model=%s tools=%s", options.model, [t.name for t in options.tools])

    # ── Public API ───────────────────────────────────────────

    def prompt(self, message: UserMessage) -> asyncio.Task:
        if self._state.is_streaming:
            raise RuntimeError("Agent is already running")
        self._state.messages.append(message)
        self._emit("prompt", message)
        return asyncio.create_task(self._run_loop())

    def subscribe(self, listener: Callable[[AgentEvent], None]) -> Callable:
        self._subscribers.append(listener)
        return lambda: self._subscribers.remove(listener)

    @property
    def state(self) -> AgentState:
        return self._state

    # ── Agent Loop ───────────────────────────────────────────

    @tracer.chain(name="Agent._run_loop : My Agent Loop")
    async def _run_loop(self) -> None:
        """
        Agentic loop:
          1. Convert messages → OpenAI format (via convert_to_llm hook)
          2. Stream LLM response
          3. If tool calls → execute all → append results → repeat from 1
          4. If plain text → emit message_end, done
        """
        self._state.is_streaming = True
        self._emit("agent_start")
        turn = 0

        # Tool schema is static for the entire run
        openai_tools = self._to_openai_tools()

        try:
            while True:
                # convert_to_llm hook — AgentSession injects context here
                messages = self._options.convert_to_llm(self._state.messages)
                openai_messages = self._to_openai_messages(messages)

                logger.info("Turn %d | model=%s messages=%d", turn, self._state.model, len(openai_messages))
                self._emit("turn_start")

                # ── Stream one LLM response ───────────────
                text_parts: list[str] = []
                # Accumulate tool call deltas by index
                # {index: {"id": str, "name": str, "arguments": str}}
                tool_call_accumulators: dict[int, dict[str, str]] = {}

                extra = {}
                if self._state.thinking_level is not None:
                    extra["reasoning_effort"] = self._state.thinking_level
                stream = await self._client.chat.completions.create(
                    model=self._state.model,
                    messages=openai_messages,
                    tools=openai_tools or None,
                    stream=True,
                    **extra,
                )

                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta

                    # Text delta → stream to subscribers
                    if delta.content:
                        text_parts.append(delta.content)
                        self._emit("message_update", delta.content)

                    # Tool call fragments → accumulate
                    if delta.tool_calls:
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_call_accumulators:
                                tool_call_accumulators[idx] = {"id": "", "name": "", "arguments": ""}
                            acc = tool_call_accumulators[idx]
                            if tc_delta.id:
                                acc["id"] += tc_delta.id
                            if tc_delta.function:
                                if tc_delta.function.name:
                                    acc["name"] += tc_delta.function.name
                                if tc_delta.function.arguments:
                                    acc["arguments"] += tc_delta.function.arguments

                self._emit("turn_end")

                # ── No tool calls → response complete ─────
                if not tool_call_accumulators:
                    final_text = "".join(text_parts)
                    assistant_msg = AssistantMessage(content=final_text or None)
                    self._state.messages.append(assistant_msg)
                    self._emit("message_end", assistant_msg)
                    logger.info("Turn %d done | text_len=%d", turn, len(final_text))
                    break

                # ── Tool calls → parse, persist, execute ──
                tool_calls: list[ToolCallRequest] = []
                for acc in tool_call_accumulators.values():
                    try:
                        arguments = json.loads(acc["arguments"] or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    tool_calls.append(
                        ToolCallRequest(
                            id=acc["id"],
                            name=acc["name"],
                            arguments=arguments,
                        )
                    )

                # Persist the assistant turn with tool_calls attached
                assistant_with_calls = AssistantMessage(
                    content="".join(text_parts) or None,
                    tool_calls=tool_calls,
                )
                self._state.messages.append(assistant_with_calls)
                self._emit("message_end", assistant_with_calls)

                # Execute each tool and append its result
                for tc in tool_calls:
                    result_content = await self._execute_tool(tc)

                    self._state.messages.append(
                        ToolResultMessage(
                            tool_call_id=tc.id,
                            content=result_content,
                        )
                    )

                turn += 1

        except Exception as e:
            logger.exception("Agent loop failed: %s", e)
            raise

        finally:
            self._state.is_streaming = False
            self._emit("agent_end")
            logger.info("Agent end | turns=%d messages=%d", turn + 1, len(self._state.messages))

    # ── Tool Execution ───────────────────────────────────────

    @tracer.tool
    async def _execute_tool(self, tc: ToolCallRequest) -> str:
        """Look up the tool by name and call its execute function."""

        add_span_infos(tool_name=tc.name, tool_arguments=json.dumps(tc.arguments))
        self._emit("tool_start", tc)

        tool = next((t for t in self._state.tools if t.name == tc.name), None)

        if tool is None:
            return f"Error: unknown tool '{tc.name}'"
        try:
            if asyncio.iscoroutinefunction(tool.execute):
                result = await tool.execute(**tc.arguments)
            else:
                result = await asyncio.to_thread(tool.execute, **tc.arguments)
            result_str = str(result)
            add_span_infos(tool_result_len=len(result_str))
            return result_str
        except Exception as e:
            add_span_infos(tool_error=str(e))
            return f"Error: {e}"
        finally:
            self._emit("tool_end", tc)

    # ── OpenAI Format Helpers ────────────────────────────────

    def _to_openai_tools(self) -> list[dict]:
        """Convert AgentTool list to the OpenAI function-calling schema."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters.model_dump(),
                },
            }
            for tool in self._state.tools
        ]

    def _to_openai_messages(self, messages: list[AgentMessage]) -> list[ChatCompletionMessageParam]:
        """Convert internal AgentMessages to the OpenAI messages format."""
        result: list[ChatCompletionMessageParam] = []

        if self._options.system_prompt:
            result.append({"role": "system", "content": self._options.system_prompt})

        for msg in messages:
            if isinstance(msg, UserMessage):
                result.append({"role": "user", "content": msg.content})

            elif isinstance(msg, AssistantMessage):
                entry: dict = {"role": "assistant", "content": msg.content or ""}
                if msg.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                result.append(entry)

            elif isinstance(msg, ToolResultMessage):
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": msg.tool_call_id,
                        "content": msg.content,
                    }
                )

        return result

    # ── Events ───────────────────────────────────────────────

    def _log_allowed(self, event_type: str) -> bool:
        return event_type not in self._options.logging_event_filter

    def _emit(self, event_type: str, payload: Any = None) -> None:
        event = AgentEvent(type=event_type, payload=payload)
        if self._log_allowed(event_type):
            trace_and_log(logger, f"Event | type={event_type} payload={payload}")
        for listener in self._subscribers:
            try:
                listener(event)
            except Exception as e:
                logger.exception("Subscriber error on event '%s': %s", event_type, e)
