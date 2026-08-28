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
from unittest import mock

import pytest

from selma.adapter_telegram import TelegramChannel
from selma.runtime import DeliveryContext

# ── Minimal-Fakes aus dem telegram-Package ──────────────


class FakeMessage:
    def __init__(self, text=None, caption=None, message_id=7):
        self.text = text
        self.caption = caption
        self.message_id = message_id
        self.date = datetime.datetime.now(datetime.UTC)

    async def reply_text(self, text):
        pass


class FakeChat:
    def __init__(self, id=-100123, type="supergroup"):
        self.id = id
        self.type = type


class FakeUser:
    def __init__(self, first_name="Rolf"):
        self.first_name = first_name


class FakeBot:
    username = "SelmaBot"


class FakeUpdate:
    def __init__(self, text=None, chat_id=-100123, chat_type="supergroup"):
        self.effective_message = FakeMessage(text=text)
        self.message = self.effective_message
        self.effective_chat = FakeChat(id=chat_id, type=chat_type)
        self.effective_user = FakeUser()

    def get_bot(self):
        return FakeBot()


# ── normalize ────────────────────────────────────────────


def test_normalize_group_message():
    upd = FakeUpdate(text="hallo @SelmaBot")
    nti = TelegramChannel.normalize(upd)
    assert nti.id == "7"
    assert nti.body == "hallo @SelmaBot"
    assert nti.body_for_agent == "[Rolf]: hallo @SelmaBot"
    assert nti.body_for_commands == "hallo"  # Bot-Handle entfernt
    assert nti.session_key == "telegram:group:123"  # -100 prefixed, id gereinigt
    assert nti.timestamp > 0
    assert nti.raw is upd


def test_normalize_private_chat():
    upd = FakeUpdate(text="hi", chat_id=42, chat_type="private")
    nti = TelegramChannel.normalize(upd)
    assert nti.session_key == "telegram:42"  # kein group-Prefix
    assert nti.body_for_agent == "[Rolf]: hi"


def test_normalize_caption_fallback():
    upd = FakeUpdate(text=None, chat_id=42, chat_type="private")
    upd.effective_message.caption = "Bildunterschrift"
    nti = TelegramChannel.normalize(upd)
    assert nti.body == "Bildunterschrift"


def test_normalize_raises_without_message():
    upd = FakeUpdate()
    upd.effective_message = None
    with pytest.raises(ValueError, match="No message"):
        TelegramChannel.normalize(upd)


# ── deliver ──────────────────────────────────────────────


def test_deliver_accumulates_and_splits_at_max_chars():
    upd = FakeUpdate(text="ping")
    ctx = TelegramChannel.deliver(upd)

    assert isinstance(ctx, DeliveryContext)
    ctx.on_partial_reply("ABCDEFGHIJ")
    ctx.on_partial_reply("KLMN")

    # _MAX_CHARS muss im Flush-Moment gepatcht sein (Loop liest cls._MAX_CHARS)
    with (
        mock.patch.object(TelegramChannel, "_MAX_CHARS", 10),
        mock.patch("selma.adapter_telegram.spawn_background_task") as spawn,
    ):
        ctx.on_block_reply_flush()
        assert spawn.call_count == 2

        def _text_of(coroutine) -> str:
            # reply_text(text) ist async → Argument aus dem Coroutine-Frame holen
            _ = coroutine.cr_await
            frame = coroutine.cr_frame
            text = frame.f_locals["text"] if "text" in frame.f_locals else coroutine.__name__
            return text

        a0 = spawn.call_args_list[0][0][0]
        a1 = spawn.call_args_list[1][0][0]
        assert _text_of(a0) == "ABCDEFGHIJ"
        assert _text_of(a1) == "KLMN"
        a0.close()
        a1.close()


def test_deliver_empty_reply_sends_nothing():
    upd = FakeUpdate(text="ping")
    ctx = TelegramChannel.deliver(upd)
    ctx.on_partial_reply("   ")
    with mock.patch("selma.adapter_telegram.spawn_background_task") as spawn:
        ctx.on_block_reply_flush()
        spawn.assert_not_called()


# ── is_enabled / name ────────────────────────────────────


def test_name_and_is_enabled():
    assert TelegramChannel.name == "telegram"

    class On:
        def is_channel_enabled(self, name):
            return name == "telegram"

    class Off:
        def is_channel_enabled(self, name):
            return False

    assert TelegramChannel().is_enabled(On()) is True
    assert TelegramChannel().is_enabled(Off()) is False


# ── start ────────────────────────────────────────────────


def test_start_skips_without_token():
    class NoTokenConfig:
        def get_telegram_token(self):
            return None

    async def run():
        with mock.patch.dict("sys.modules", {"selma.gateway": types.SimpleNamespace(handle_telegram=object())}):
            await TelegramChannel().start(NoTokenConfig())

    asyncio.new_event_loop().run_until_complete(run())  # darf nicht werfen


def test_start_boots_polling_with_token():
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
        def get_telegram_token(self):
            return "TOKEN-123"

    class FakeApplicationBuilder:
        def token(self, tok):
            assert tok == "TOKEN-123"
            return self

        def build(self):
            return app

    import selma.adapter_telegram as tg_mod

    with mock.patch.object(tg_mod, "ApplicationBuilder", FakeApplicationBuilder):
        with mock.patch.dict("sys.modules", {"selma.gateway": types.SimpleNamespace(handle_telegram=object())}):
            asyncio.new_event_loop().run_until_complete(TelegramChannel().start(TokenConfig()))

    assert inits["init"] is True
    assert inits["start"] is True
    assert inits["poll"] is True
    assert app.add_handler.call_count == 2  # TEXT-Handler + COMMAND-Handler
