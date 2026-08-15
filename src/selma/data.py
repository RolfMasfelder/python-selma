from typing import Any

from pydantic import BaseModel


class WebChatIn(BaseModel):
    """Minimal data model for incoming messages from the dashboard."""

    user_id: str
    text: str
    user_name: str = "Web User"


class NormalizedTurnInput(BaseModel):
    # OpenClaw equivalent: NormalizedTurnInput in src/channels/turn/types.ts
    id: str | None = None
    timestamp: int | None = None
    body: str | None = None  # rawText
    body_for_agent: str | None = None  # textForAgent
    body_for_commands: str | None = None  # textForCommands
    raw: Any | None = None

    # Selma-specific
    session_key: str | None = None

    model_config = {"arbitrary_types_allowed": True}

    @classmethod
    def pretty_print(cls, ctx: "NormalizedTurnInput"):
        print("\n" + "=" * 50)
        print("📥 NEW MESSAGE RECEIVED (NormalizedTurnInput)")
        print("=" * 50)
        print(ctx.model_dump_json(indent=4, exclude_none=True, exclude={"raw"}))
        print("=" * 50 + "\n")
