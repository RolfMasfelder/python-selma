from typing import Any, Optional
from pydantic import BaseModel


class WebChatIn(BaseModel):
    """Minimal data model for incoming messages from the dashboard."""
    user_id: str
    text: str
    user_name: str = "Web User"


class NormalizedTurnInput(BaseModel):
    # OpenClaw equivalent: NormalizedTurnInput in src/channels/turn/types.ts
    id: Optional[str] = None
    timestamp: Optional[int] = None
    body: Optional[str] = None            # rawText
    body_for_agent: Optional[str] = None  # textForAgent
    body_for_commands: Optional[str] = None  # textForCommands
    raw: Optional[Any] = None

    # Selma-specific
    session_key: Optional[str] = None

    model_config = {"arbitrary_types_allowed": True}

    @classmethod
    def pretty_print(cls, ctx: "NormalizedTurnInput"):
        print("\n" + "="*50)
        print("📥 NEW MESSAGE RECEIVED (NormalizedTurnInput)")
        print("="*50)
        print(ctx.model_dump_json(indent=4, exclude_none=True, exclude={"raw"}))
        print("="*50 + "\n")
