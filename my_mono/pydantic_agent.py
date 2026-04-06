# ============================================================
# my_mono/agent.py — pydantic-ai Version
# ============================================================

from __future__ import annotations
import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent as PaiAgent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    UserPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    TextPartDelta,
)
from openai.types.chat import ChatCompletionMessageParam

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

ThinkingLevel = Literal["low", "medium", "high"]


class AgentState(BaseModel):
    messages: list[AgentMessage] = Field(default_factory=list)
    tools: list[AgentTool] = Field(default_factory=list)
    model: str = "llama3.1:8b"
    thinking_level: ThinkingLevel = "low"
    is_streaming: bool = False
    custom: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}


class AgentOptions(BaseModel):
    model: str
    tools: list[AgentTool] = Field(default_factory=list)
    system_prompt: str = ""
    thinking_level: ThinkingLevel = "low"
    ollama_base_url: str = "http://localhost:11434/v1"
    convert_to_llm: Callable[[list[AgentMessage]], list[AgentMessage]] = Field(
        default=lambda msgs: msgs,
        exclude=True,
    )

    model_config = {"arbitrary_types_allowed": True}


class AgentEvent(BaseModel):
    type: str
    payload: Any = None


# ─── AGENT CLASS ────────────────────────────────────────────

class Agent:

    def __init__(self, options: AgentOptions):
        self._options = options
        self._state = AgentState(
            model=options.model,
            tools=options.tools,
            thinking_level=options.thinking_level,
        )
        self._subscribers: list[Callable[[AgentEvent], None]] = []
        self._pai_model = OpenAIChatModel(
            model_name=options.model,
            provider=OpenAIProvider(base_url=options.ollama_base_url),
        )
        logger.info("Agent initialized | model=%s thinking=%s tools=%s",
                    options.model,
                    options.thinking_level,
                    [t.name for t in options.tools])

    # ── Public API ──────────────────────────────────────────

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

    # ── Agent Loop ──────────────────────────────────────────

    async def _run_loop(self) -> None:
        self._state.is_streaming = True
        self._emit("agent_start")
        turn = 0

        try:
            # convert_to_llm hook — AgentSession intervenes here
            transformed = self._options.convert_to_llm(self._state.messages)

            if not transformed:
                return

            # Last message = current user prompt
            # Everything before = message_history for pydantic-ai
            last = transformed[-1]
            history = transformed[:-1]

            user_prompt = last.content if isinstance(last, UserMessage) else str(last)
            pai_history = self._to_pai_messages(history)

            # Build FunctionToolset from AgentTool list
            toolset = self._build_toolset()

            pai_agent = PaiAgent(
                self._pai_model,
                system_prompt=self._options.system_prompt,
            )

            logger.info("Turn %d start | model=%s messages=%d",
                        turn, self._state.model, len(self._state.messages))
            self._emit("turn_start")

            async with pai_agent.iter(
                user_prompt,
                message_history=pai_history,
                toolsets=[toolset],
            ) as agent_run:

                async for node in agent_run:

                    # ── LLM responds ───────────────────────
                    if PaiAgent.is_model_request_node(node):
                        async with node.stream(agent_run.ctx) as stream:
                            async for event in stream:
                                if isinstance(event, PartDeltaEvent):
                                    if isinstance(event.delta, TextPartDelta):
                                        self._emit("message_update",
                                                   event.delta.content_delta)

                    # ── Tools are executed ────────────────
                    elif PaiAgent.is_call_tools_node(node):
                        tool_calls_this_node: list[ToolCallRequest] = []

                        async with node.stream(agent_run.ctx) as stream:
                            async for event in stream:

                                if isinstance(event, FunctionToolCallEvent):
                                    tc = ToolCallRequest(
                                        id=event.part.tool_call_id or "",
                                        name=event.part.tool_name,
                                        arguments=event.part.args
                                            if isinstance(event.part.args, dict)
                                            else json.loads(event.part.args),
                                    )
                                    tool_calls_this_node.append(tc)
                                    self._emit("tool_start", tc)
                                    logger.info("Tool start | name=%s args=%s",
                                                tc.name, tc.arguments)

                                elif isinstance(event, FunctionToolResultEvent):
                                    tc_id = event.result.tool_call_id
                                    content = str(event.result.content)

                                    matching_tc = next(
                                        (t for t in tool_calls_this_node
                                         if t.id == tc_id), None
                                    )
                                    if matching_tc:
                                        logger.info("Tool end | name=%s result_len=%d",
                                                    matching_tc.name, len(content))
                                        self._emit("tool_end", matching_tc)

                                    self._state.messages.append(ToolResultMessage(
                                        tool_call_id=tc_id,
                                        content=content,
                                    ))

                        self._emit("turn_end")
                        turn += 1
                        logger.info("Turn %d start | model=%s messages=%d",
                                    turn, self._state.model,
                                    len(self._state.messages))
                        self._emit("turn_start")

            # Final response
            final_text = str(agent_run.result.output) if agent_run.result else ""
            assistant_msg = AssistantMessage(content=final_text or None)
            self._state.messages.append(assistant_msg)
            self._emit("message_end", assistant_msg)

            logger.info("Turn %d end | text_len=%d", turn, len(final_text or ""))
            self._emit("turn_end")

        except Exception as e:
            logger.exception("Agent loop failed: %s", e)
            raise

        finally:
            self._state.is_streaming = False
            self._emit("agent_end")
            logger.info("Agent end | total_turns=%d total_messages=%d",
                        turn + 1, len(self._state.messages))

    # ── Build FunctionToolset ────────────────────────────────

    def _build_toolset(self):
        from pydantic_ai import FunctionToolset

        toolset = FunctionToolset()

        for agent_tool in self._state.tools:
            def make_wrapper(tool: AgentTool) -> Callable:
                # Correct parameter names from our schema
                param_names = list(tool.parameters.properties.keys())

                async def wrapper(**kwargs: Any) -> str:
                    # LLM may send different kwarg names than in the schema —
                    # map values by order to the correct names
                    values = list(kwargs.values())
                    remapped = dict(zip(param_names, values))

                    if asyncio.iscoroutinefunction(tool.execute):
                        result = await tool.execute(**remapped)
                    else:
                        result = await asyncio.to_thread(tool.execute, **remapped)
                    return str(result)

                wrapper.__name__ = tool.name
                wrapper.__doc__ = tool.description
                return wrapper

            toolset.add_function(
                make_wrapper(agent_tool),
                name=agent_tool.name,
                description=agent_tool.description,
            )

        return toolset

    # ── Conversion ──────────────────────────────────────────

    def _to_pai_messages(
        self, messages: list[AgentMessage]
    ) -> list[ModelMessage]:
        """Internal AgentMessages → pydantic-ai ModelMessage format."""
        result: list[ModelMessage] = []

        for msg in messages:
            if isinstance(msg, UserMessage):
                result.append(ModelRequest(parts=[
                    UserPromptPart(content=msg.content)
                ]))
            elif isinstance(msg, AssistantMessage):
                parts = []
                if msg.content:
                    parts.append(TextPart(content=msg.content))
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        parts.append(ToolCallPart(
                            tool_name=tc.name,
                            args=tc.arguments,
                            tool_call_id=tc.id,
                        ))
                if parts:
                    result.append(ModelResponse(parts=parts))
            elif isinstance(msg, ToolResultMessage):
                result.append(ModelRequest(parts=[
                    ToolReturnPart(
                        tool_name="",
                        content=msg.content,
                        tool_call_id=msg.tool_call_id,
                    )
                ]))

        return result

    def _to_openai_messages(
        self, messages: list[AgentMessage]
    ) -> list[ChatCompletionMessageParam]:
        """
        For AgentSession.compact() — kept so that
        agent_session.py works without modification.
        """
        result: list[ChatCompletionMessageParam] = []

        if self._options.system_prompt:
            result.append({"role": "system",
                           "content": self._options.system_prompt})

        for msg in messages:
            if isinstance(msg, UserMessage):
                result.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AssistantMessage):
                entry: dict = {"role": "assistant",
                               "content": msg.content or ""}
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
                result.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content,
                })

        return result

    def _emit(self, event_type: str, payload: Any = None) -> None:
        event = AgentEvent(type=event_type, payload=payload)
        logger.debug("Event | type=%s payload=%s", event.type, event.payload)
        for listener in self._subscribers:
            try:
                listener(event)
            except Exception as e:
                logger.exception("Subscriber error on event '%s': %s",
                                 event_type, e)