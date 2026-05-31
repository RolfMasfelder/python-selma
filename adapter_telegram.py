import asyncio
import logging

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters

from data import NormalizedTurnInput
from runtime import DeliveryContext


class TelegramChannel:
    name = "telegram"
    _MAX_CHARS = 4096

    @classmethod
    def normalize(cls, update: Update) -> NormalizedTurnInput:
        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user

        if not msg:
            raise ValueError("No message found in update")

        is_group = chat.type in ["group", "supergroup", "channel"]
        clean_id = str(chat.id).replace("-100", "")
        prefix = "group:" if is_group else ""
        s_key = f"telegram:{prefix}{clean_id}"

        raw_text = msg.text or msg.caption or ""
        bot_username = update.get_bot().username

        return NormalizedTurnInput(
            id=str(msg.message_id),
            timestamp=int(msg.date.timestamp()),
            body=raw_text,
            body_for_agent=f"[{user.first_name}]: {raw_text}",
            body_for_commands=raw_text.replace(f"@{bot_username}", "").strip(),
            raw=update,
            session_key=s_key,
        )

    @classmethod
    def deliver(cls, update: Update) -> DeliveryContext:
        """
        Accumulates tokens via on_partial_reply; sends the complete reply
        via asyncio.create_task when the agent finishes (on_block_reply_flush).
        Splits at Telegram's 4096-char limit if needed.
        """
        chunks: list[str] = []

        def on_partial_reply(text: str) -> None:
            chunks.append(text)

        def on_block_reply_flush() -> None:
            text = "".join(chunks).strip()
            if not text:
                return
            for i in range(0, len(text), cls._MAX_CHARS):
                asyncio.create_task(
                    update.message.reply_text(text[i:i + cls._MAX_CHARS])
                )

        return DeliveryContext(
            on_partial_reply=on_partial_reply,
            on_block_reply_flush=on_block_reply_flush,
        )

    def is_enabled(self, config) -> bool:
        return config.is_channel_enabled("telegram")

    async def start(self, config) -> None:
        from gateway import handle_telegram
        token = config.get_telegram_token()
        if not token:
            logging.warning("Telegram enabled but no token found — skipping.")
            return
        tg_app = ApplicationBuilder().token(token).build()
        tg_app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_telegram))
        tg_app.add_handler(MessageHandler(filters.COMMAND, handle_telegram))
        await tg_app.initialize()
        await tg_app.start()
        logging.info("🚀 Telegram channel active.")
        await tg_app.updater.start_polling()
