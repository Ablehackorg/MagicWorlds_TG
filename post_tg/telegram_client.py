# telegram_client.py
from telethon import TelegramClient
from telethon.sessions import StringSession
from models import BotSession
import os

def init_user_client(bot: BotSession) -> TelegramClient:
    if not bot.session_string:
        raise ValueError(f"Bot {bot.id} has no session_string")
    if not bot.api_id or not bot.api_hash:
        raise ValueError(f"Bot {bot.id} has no api_id/api_hash")

    flood_threshold = int(os.getenv("FLOOD_SLEEP_THRESHOLD", "300"))
    conn_retries    = int(os.getenv("CONNECTION_RETRIES", "5"))
    req_retries     = int(os.getenv("REQUEST_RETRIES", "5"))

    return TelegramClient(
        StringSession(bot.session_string),
        bot.api_id,
        bot.api_hash,
        flood_sleep_threshold=flood_threshold,
        connection_retries=None,
        request_retries=req_retries,
    )
