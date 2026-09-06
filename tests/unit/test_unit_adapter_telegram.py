# ============================================================
# test_unit_adapter_telegram.py
#
# Unit tests für selma/adapter_telegram.py — komplett mit Fakes,
# kein echter Telegram-Server.
#
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

import asyncio
import datetime
import types
from typing import Any, cast
from unittest import mock

import pytest
from telegram import Update

from selma.adapter_telegram import TelegramChannel
from selma.config import SelmaConfig
from selma.runtime import DeliveryContext

# ── Minimal-Fakes aus dem telegram-Package ──────────────


class FakeMessage:
    def __init__(self, text: str | None = None, caption: str | None = None, message_id: int = 7) -> None:
        self.text = text
        self.caption = caption
        self.message_id = message_id
        self.date = datetime.datetime.now(datetime.UTC)

    async def reply_text(self, text: str) -> None:
        pass


class FakeChat:
    def __init__(self, id: int = -100123, type: str = "supergroup") -> None:
        self.id = id
        self.type = type


class FakeUser:
    def __init__(self, first_name: str = "Rolf") -> None:
        self.first_name = first_name


class FakeBot:
    username = "SelmaBot"


class FakeUpdate:
    def __init__(self, text: str | None = None, chat_id: int = -100123, chat_type: str = "supergroup") -> None:
        self.effective_message: FakeMessage | None = FakeMessage(text=text)
        self.message = self.effective_message
        self.effective_chat = FakeChat(id=chat_id, type=chat_type)
        self.effective_user = FakeUser()

    def get_bot(self) -> FakeBot:
        return FakeBot()


def _as_update(fake: FakeUpdate) -> Update:
    # FakeUpdate ist ein Duck-Type-Double für Update, kein echtes telegram-Objekt
    return cast(Update, fake)


def _as_config(fake: object) -> SelmaConfig:
    # Duck-Type-Double für SelmaConfig, kein echtes Pydantic-Modell
    return cast(SelmaConfig, fake)


# ── normalize ────────────────────────────────────────────


def test_normalize_group_message() -> None:
    upd = FakeUpdate(text="hallo @SelmaBot")
    nti = TelegramChannel.normalize(_as_update(upd))
    assert nti.id == "7"
    assert nti.body == "hallo @SelmaBot"
    assert nti.body_for_agent == "[Rolf]: hallo @SelmaBot"
    assert nti.body_for_commands == "hallo"  # Bot-Handle entfernt
    assert nti.session_key == "telegram:group:123"  # -100 prefixed, id gereinigt
    assert nti.timestamp is not None
    assert nti.timestamp > 0
    assert nti.raw is upd


def test_normalize_private_chat() -> None:
    upd = FakeUpdate(text="hi", chat_id=42, chat_type="private")
    nti = TelegramChannel.normalize(_as_update(upd))
    assert nti.session_key == "telegram:42"  # kein group-Prefix
    assert nti.body_for_agent == "[Rolf]: hi"


def test_normalize_caption_fallback() -> None:
    upd = FakeUpdate(text=None, chat_id=42, chat_type="private")
    assert upd.effective_message is not None
    upd.effective_message.caption = "Bildunterschrift"
    nti = TelegramChannel.normalize(_as_update(upd))
    assert nti.body == "Bildunterschrift"


def test_normalize_raises_without_message() -> None:
    upd = FakeUpdate()
    upd.effective_message = None
    with pytest.raises(ValueError, match="No message"):
        TelegramChannel.normalize(_as_update(upd))


# ── deliver ──────────────────────────────────────────────


def test_deliver_accumulates_and_splits_at_max_chars() -> None:
    upd = FakeUpdate(text="ping")
    ctx = TelegramChannel.deliver(_as_update(upd))

    assert isinstance(ctx, DeliveryContext)
    assert ctx.on_partial_reply is not None
    assert ctx.on_block_reply_flush is not None
    ctx.on_partial_reply("ABCDEFGHIJ")
    ctx.on_partial_reply("KLMN")

    # _MAX_CHARS muss im Flush-Moment gepatcht sein (Loop liest cls._MAX_CHARS)
    with (
        mock.patch.object(TelegramChannel, "_MAX_CHARS", 10),
        mock.patch("selma.adapter_telegram.spawn_background_task") as spawn,
    ):
        ctx.on_block_reply_flush()
        assert spawn.call_count == 2

        def _text_of(coroutine: types.CoroutineType[Any, Any, Any]) -> str:
            # reply_text(text) ist async → Argument aus dem Coroutine-Frame holen
            _ = coroutine.cr_await
            frame = coroutine.cr_frame
            text = frame.f_locals["text"] if frame is not None and "text" in frame.f_locals else coroutine.__name__
            return text

        a0 = spawn.call_args_list[0][0][0]
        a1 = spawn.call_args_list[1][0][0]
        assert _text_of(a0) == "ABCDEFGHIJ"
        assert _text_of(a1) == "KLMN"
        a0.close()
        a1.close()


def test_deliver_empty_reply_sends_nothing() -> None:
    upd = FakeUpdate(text="ping")
    ctx = TelegramChannel.deliver(_as_update(upd))
    assert ctx.on_partial_reply is not None
    assert ctx.on_block_reply_flush is not None
    ctx.on_partial_reply("   ")
    with mock.patch("selma.adapter_telegram.spawn_background_task") as spawn:
        ctx.on_block_reply_flush()
        spawn.assert_not_called()


# ── is_enabled / name ────────────────────────────────────


def test_name_and_is_enabled() -> None:
    assert TelegramChannel.name == "telegram"

    class On:
        def is_channel_enabled(self, name: str) -> bool:
            return name == "telegram"

    class Off:
        def is_channel_enabled(self, name: str) -> bool:
            return False

    assert TelegramChannel().is_enabled(_as_config(On())) is True
    assert TelegramChannel().is_enabled(_as_config(Off())) is False


# ── start ────────────────────────────────────────────────


def test_start_skips_without_token() -> None:
    class NoTokenConfig:
        def get_telegram_token(self) -> str | None:
            return None

    async def run() -> None:
        with mock.patch.dict("sys.modules", {"selma.gateway": types.SimpleNamespace(handle_telegram=object())}):
            await TelegramChannel().start(_as_config(NoTokenConfig()))

    asyncio.new_event_loop().run_until_complete(run())  # darf nicht werfen


def test_start_boots_polling_with_token() -> None:
    """Mit Token: initialize + start + polling werden aufgerufen, 2 Handler."""

    inits = {"init": False, "start": False, "poll": False}
    app = types.SimpleNamespace(
        add_handler=mock.Mock(),
        initialize=mock.AsyncMock(side_effect=lambda: (inits.__setitem__("init", True), True)[1]),
        start=mock.AsyncMock(side_effect=lambda: (inits.__setitem__("start", True), True)[1]),
        updater=types.SimpleNamespace(
            start_polling=mock.AsyncMock(side_effect=lambda: inits.__setitem__("poll", True))
        ),
    )

    class TokenConfig:
        def get_telegram_token(self) -> str:
            return "TOKEN-123"

    class FakeApplicationBuilder:
        def token(self, tok: str) -> "FakeApplicationBuilder":
            assert tok == "TOKEN-123"
            return self

        def build(self) -> types.SimpleNamespace:
            return app

    import selma.adapter_telegram as tg_mod

    with mock.patch.object(tg_mod, "ApplicationBuilder", FakeApplicationBuilder):
        with mock.patch.dict("sys.modules", {"selma.gateway": types.SimpleNamespace(handle_telegram=object())}):
            asyncio.new_event_loop().run_until_complete(TelegramChannel().start(_as_config(TokenConfig())))

    assert inits["init"] is True
    assert inits["start"] is True
    assert inits["poll"] is True
    assert app.add_handler.call_count == 2  # TEXT-Handler + COMMAND-Handler
