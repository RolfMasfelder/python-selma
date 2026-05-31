from typing import Any, Protocol, runtime_checkable

from data import NormalizedTurnInput
from runtime import DeliveryContext


@runtime_checkable
class ChannelAdapter(Protocol):
    """
    Combined interface for every channel.

    normalize  — classmethod: converts raw channel input into a NormalizedTurnInput
    deliver    — classmethod: builds a DeliveryContext for this channel's output
    is_enabled — returns True if this channel is active in config
    start      — long-running coroutine that boots and runs the channel
    """

    name: str

    @classmethod
    def normalize(cls, raw: Any) -> NormalizedTurnInput: ...

    @classmethod
    def deliver(cls, context: Any) -> DeliveryContext: ...

    def is_enabled(self, config: Any) -> bool: ...
    async def start(self, config: Any) -> None: ...
