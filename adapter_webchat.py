import asyncio
import logging
from datetime import datetime

from data import NormalizedTurnInput
from runtime import DeliveryContext


class WebChatChannel:
    name = "webchat"

    @classmethod
    def normalize(cls, raw_data: dict) -> NormalizedTurnInput:
        user_id = raw_data.get("user_id", "anonymous")
        text = raw_data.get("text", "")
        user_name = raw_data.get("user_name", "Web User")

        return NormalizedTurnInput(
            id=user_id,
            timestamp=int(datetime.now().timestamp()),
            body=text,
            body_for_agent=f"[Web:{user_name}]: {text}",
            body_for_commands=text.strip(),
            raw=raw_data,
            session_key=f"webchat:{user_id}",
        )

    @classmethod
    def deliver(cls, queue: asyncio.Queue) -> DeliveryContext:
        """
        Text chunks and tool events are pushed into the queue for the SSE generator.
        """
        def on_partial_reply(text: str) -> None:
            queue.put_nowait(text)

        def on_tool_call(tool_name: str, args: dict) -> None:
            queue.put_nowait({"type": "tool", "name": tool_name})

        return DeliveryContext(on_partial_reply=on_partial_reply, on_tool_call=on_tool_call)

    def is_enabled(self, config) -> bool:
        return config.is_channel_enabled("webchat")

    async def start(self, config) -> None:
        import uvicorn
        from gateway import api
        wc = config.channels.webchat
        uv_config = uvicorn.Config(app=api, host=wc.host, port=wc.port, log_level=wc.log_level)
        server = uvicorn.Server(uv_config)
        logging.info("🚀 WebChat API ready on port 8000.")
        await server.serve()
