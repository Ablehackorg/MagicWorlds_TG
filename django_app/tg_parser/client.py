import os

import asyncio
import threading
from dotenv import load_dotenv
from typing import Optional

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.photos import GetUserPhotosRequest
from telethon.tl.types import InputPeerChannel, Message, MessageService
from telethon.tl.functions.messages import GetHistoryRequest

load_dotenv()
# ==== ENV ====
API_ID = int(os.getenv("TG_API_ID", "123456"))
API_HASH = os.getenv("TG_API_HASH", "your_api_hash")

# Можно использовать либо строковую сессию (рекомендуется),
# либо файл сессии (если переменной нет — создаст файл "tg_userbot.session"):
TL_STRING_SESSION = os.getenv(
    "TG_SESSION_STRING") or os.getenv("TL_STRING_SESSION")

SESSION: StringSession | str = StringSession(
    TL_STRING_SESSION) if TL_STRING_SESSION else "tg_userbot"


AVATAR_DIR = "avatars"
os.makedirs(AVATAR_DIR, exist_ok=True)


def download_channel_photo(telegram_id: int) -> str | None:
    """
    Скачивает фото канала по telegram_id.
    Файл сохраняется в avatars/{telegram_id}.jpg
    Возвращает относительный путь (/avatars/{telegram_id}.jpg) или None.
    """
    client = get_client()
    filename = f"{telegram_id}.jpg"
    path = os.path.join(AVATAR_DIR, filename)

    async def _download():
        entity = await ensure_join_and_get_entity(client, link_or_id)
        if not getattr(entity, "photo", None):
            return None
        return await client.download_profile_photo(entity, file=path)

    result = run_in_client(_download())
    if result:
        return f"/avatars/{filename}"
    return None


# ==== BACKGROUND LOOP ====
_loop: asyncio.AbstractEventLoop | None = None
_client: Optional[TelegramClient] = None
_ready = threading.Event()


async def get_channel_stats(client, entity) -> tuple[int, str | None]:
    """
    Возвращает (кол-во обычных сообщений, дата последнего обычного сообщения)
    """
    posts_count = 0
    last_date = None

    # async for msg in client.iter_messages(entity, limit=100):
    #     # отбрасываем системные (MessageService)
    #     if isinstance(msg, MessageService):
    #         continue

    #     posts_count += 1
    #     if last_date is None:  # первое = самое свежее
    #         last_date = msg.date.isoformat()

    return posts_count, last_date


def parse_channel(link_or_id: str) -> dict:
    client = get_client()

    async def _work():
        entity = await client.ensure_join_and_get_entity(link_or_id)

        tg_id = entity.id
        name = getattr(entity, "title", getattr(
            entity, "username", None)) or str(tg_id)

        return {
            "tg_id": tg_id,
            "name": name,
            "photo": None,
            "posts_count": None,
            "last_message_date": None,
        }

        # аватар
        photo_path = None
        if getattr(entity, "photo", None):
            filename = os.path.join(AVATAR_DIR, f"{tg_id}.jpg")
            await client.download_profile_photo(entity, file=filename)
            photo_path = f"/avatars/{tg_id}.jpg"

        # считаем посты и берём дату последнего
        posts_count, last_date = await get_channel_stats(client, entity)

        return {
            "tg_id": tg_id,
            "name": name,
            "photo": photo_path,
            "posts_count": posts_count,
            "last_message_date": last_date,
        }

    return run_in_client(_work())


def _parse_proxy(url: str):
    """Возвращает telethon-формат прокси или None."""
    if not url:
        return None
    # Поддержим socks5://user:pass@host:port и http://host:port
    # Telethon ждёт: (proxy_type, host, port, username, password)
    import urllib.parse
    import socks
    u = urllib.parse.urlparse(url)
    host = u.hostname
    port = u.port
    user = u.username
    pwd = u.password
    if u.scheme.lower().startswith("socks"):
        return (socks.SOCKS5, host, port, True, user, pwd)
    if u.scheme.lower().startswith("http"):
        return (socks.HTTP, host, port, True, user, pwd)
    return None


def _loop_worker():
    """Фоновый поток: создаёт loop, стартует Telethon-клиент и держит loop живым."""
    global _loop, _client
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)

    async def _init():
        global _client

        _client = TelegramClient(
            session=SESSION,
            api_id=API_ID,
            api_hash=API_HASH,
            # device/system strings можно задать при желании
        )
        await _client.connect()
        # если сессии нет — попытка авторизации (но мы рассчитываем на готовую session string/файл)
        if not await _client.is_user_authorized():
            # Без номера/кода мы не сможем авторизоваться — сообщим явно
            raise RuntimeError(
                "Telethon session not authorized. Provide TG_SESSION_STRING or valid session file.")
        _ready.set()

    _loop.run_until_complete(_init())
    _loop.run_forever()


# Стартуем фонового работника при импорте
_thread = threading.Thread(
    target=_loop_worker, name="telethon-loop", daemon=True)
_thread.start()
_ready.wait(timeout=30)  # дождаться старта клиента (до 30с)


def run_in_client(coro):
    """Выполнить корутину в telethon-loop и вернуть результат (blocking)."""
    if _loop is None:
        raise RuntimeError("Telethon loop is not initialized")
    if not asyncio.iscoroutine(coro):
        raise TypeError("run_in_client expects coroutine")
    fut = asyncio.run_coroutine_threadsafe(coro, _loop)
    return fut.result()


def get_client() -> TelegramClient:
    if _client is None:
        raise RuntimeError("Telethon client is not ready")
    return _client


async def ensure_join_and_get_entity(client, link: str):
    """
    Возвращает entity канала/чата. Если это пригласительная ссылка
    и юзербот ещё не внутри, сначала присоединяется.
    """
    # 1. @username или https://t.me/channel
    if re.match(r"^(https?://)?t\.me/[\w\d_]+$", link):
        return await client.get_entity(link)

    # 2. joinchat/+invitehash
    m = re.search(r"(?:joinchat/|\+)([A-Za-z0-9_-]+)", link)
    if m:
        invite_hash = m.group(1)
        try:
            return await client(ImportChatInviteRequest(invite_hash))
        except UserAlreadyParticipantError:
            return await client.get_entity(link)
        except Exception as e:
            raise RuntimeError(f"Не удалось вступить по приглашению: {e}")

    # 3. fallback — числовой id
    return await client.get_entity(link)
