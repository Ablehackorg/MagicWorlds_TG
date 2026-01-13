# tg_parser/views.py
from django.http import JsonResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
import json
import re
import logging
import os
from urllib.parse import urlparse

from telethon import types
from telethon.errors import (
    UsernameInvalidError,
    UsernameNotOccupiedError,
    ChannelInvalidError,
    ChannelPrivateError,
    FloodWaitError,
)
from telethon.errors.rpcerrorlist import (
    InviteHashExpiredError,
    InviteHashInvalidError,
)
from telethon.tl.functions.messages import (
    ImportChatInviteRequest,
    CheckChatInviteRequest,
)
# Импортируем конкретные функции вместо импорта всего модуля functions
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest

from .client import get_client, run_in_client

logger = logging.getLogger(__name__)

# Валидация username: начинается с буквы, длина 5–32 символа
USERNAME_RE = re.compile(r"^[a-zA-Z][\w\d]{3,30}[a-zA-Z\d]$")


# ==================== Вспомогательные функции ====================

async def _download_entity_photo_any(client, base, full, tg_id: int) -> str | None:
    """
    Пытается скачать фото для сущности (канал/группа).
    Логика:
    1) пробуем скачать напрямую из base
    2) если не получилось, ищем кандидата в full.chats
    Возвращает URL вида '/avatars/<tg_id>.jpg' или None.
    """
    target_path = os.path.join("avatars", f"{tg_id}.jpg")

    # --- пробуем скачать фото напрямую из base ---
    try:
        path = await client.download_profile_photo(base, file=target_path)
        if path and os.path.exists(target_path):
            return f"/avatars/{tg_id}.jpg"
    except Exception as e:
        logger.debug(
            "download_profile_photo(base) failed for %s: %s", tg_id, e)

    # --- пробуем среди full.chats ---
    try:
        chats = getattr(full, "chats", None)
        if chats:
            for ch in chats:
                try:
                    if getattr(ch, "id", None) not in (None, getattr(base, "id", None)):
                        continue
                    path = await client.download_profile_photo(ch, file=target_path)
                    if path and os.path.exists(target_path):
                        return f"/avatars/{tg_id}.jpg"
                except Exception as e:
                    logger.debug(
                        "download_profile_photo(ch) failed for %s: %s", tg_id, e)
    except Exception:
        pass

    return None


def _classify_input(raw: str):
    """
    Определяет тип входа (kind, value).
    Возможные kind:
    - "username"   → @name / t.me/name
    - "invite"     → +HASH / t.me/+HASH / t.me/joinchat/HASH
    - "private_c"  → t.me/c/... (приватный пост/чат)
    - "id"         → числовой id (-100123456...)
    - "unknown"    → если не распознано
    """
    s = (raw or "").strip()
    if not s:
        return ("unknown", "")

    # числовой id
    if re.fullmatch(r"-?\d{5,20}", s):
        return ("id", s)

    # username с @
    if s.startswith("@"):
        return ("username", s[1:])

    # голый инвайт вида +HASH
    if s.startswith("+") and len(s) > 1:
        return ("invite", s[1:])

    # t.me/... → парсим как URL
    if s.lower().startswith(("t.me/", "telegram.me/", "www.t.me/")):
        s = "https://" + s

    if s.startswith(("http://", "https://")):
        u = urlparse(s)
        host = (u.netloc or "").lower()
        if host in {"t.me", "telegram.me", "www.t.me"}:
            parts = [p for p in (u.path or "").split("/") if p]
            if not parts:
                return ("unknown", s)

            first = parts[0]

            if first.startswith("+"):
                return ("invite", first.lstrip("+"))
            if first.lower() == "joinchat":
                return ("invite", parts[1] if len(parts) >= 2 else "")
            if first.lower() == "c":
                return ("private_c", s)
            return ("username", first)

        return ("unknown", s)

    # по умолчанию пробуем как username
    return ("username", s)


async def ensure_join_and_get_entity(client, raw: str):
    """
    Получение entity по username/ID.
    Если строка — числовой ID (включая -100...), пробуем как int и как str.
    """
    if re.fullmatch(r"-?\d{5,20}", raw):
        try:
            return await client.get_entity(int(raw))
        except Exception:
            return await client.get_entity(raw)
    return await client.get_entity(raw)


def to_pyrogram_id(entity) -> int:
    """
    Преобразует Telethon id в формат Pyrogram:
    - Для каналов/супергрупп добавляем -100 перед id
    - Для чатов возвращаем как есть
    """
    if getattr(entity, "megagroup", False) or getattr(entity, "broadcast", False):
        return int(f"-100{entity.id}")
    return entity.id


# ==================== Основная логика ====================

async def _fetch_channel_info(raw: str) -> dict:
    """
    Получение информации о канале/группе.
    Поддерживает:
    - invite:<hash>
    - username
    - numeric id
    Возвращает словарь с полями:
    tg_id, name, username, members_count, type, photo, posts_count, last_message_date, description
    """
    client = get_client()

    async def _full_for_channel(entity):
        """
        Получение полной информации для объекта Telethon entity.
        Определяет тип (channel, supergroup, chat).
        """
        if isinstance(entity, types.Channel):
            # Используем GetFullChannelRequest вместо functions.channels.GetFullChannelRequest
            full = await client(GetFullChannelRequest(entity))
            participants = getattr(full.full_chat, "participants_count", None)
            base = entity
            tg_id = to_pyrogram_id(base)
        else:
            base = await client.get_entity(entity)
            tg_id = to_pyrogram_id(base)
            if isinstance(base, types.Channel):
                full = await client(GetFullChannelRequest(base))
                participants = getattr(
                    full.full_chat, "participants_count", None)
            else:
                full = await client(GetFullChatRequest(tg_id))
                participants = getattr(
                    full.full_chat, "participants_count", None)

        kind = (
            "supergroup"
            if isinstance(base, types.Channel) and base.megagroup
            else ("channel" if isinstance(base, types.Channel) else "chat")
        )
        username = getattr(base, "username", None)
        title = getattr(base, "title", None) or username or ""

        # описание
        description = None
        try:
            if hasattr(full, "full_chat") and getattr(full.full_chat, "about", None):
                description = full.full_chat.about
        except Exception:
            pass

        # фото
        photo_url = None
        try:
            if getattr(base, "photo", None):
                tmp_path = await client.download_profile_photo(base)
                if tmp_path and os.path.exists(tmp_path):
                    ext = os.path.splitext(tmp_path)[1] or ".jpg"
                    out_dir = "avatars"
                    os.makedirs(out_dir, exist_ok=True)
                    out_path = os.path.join(out_dir, f"{tg_id}{ext}")
                    with open(tmp_path, "rb") as src, open(out_path, "wb") as dst:
                        dst.write(src.read())
                    photo_url = f"/avatars/{tg_id}{ext}"
        except Exception as e:
            logger.warning(f"Не удалось получить фото для {tg_id}: {e}")

        # количество постов (limit = 100)
        posts_count = 0
        last_message_date = None
        async for msg in client.iter_messages(base, limit=100):
            if getattr(msg, "action", None):
                continue
            posts_count += 1
            if last_message_date is None:
                last_message_date = msg.date.isoformat()

        return {
            "tg_id": tg_id,
            "name": title,
            "username": username,
            "members_count": participants,
            "type": kind,
            "photo": photo_url,
            "posts_count": posts_count,
            "last_message_date": last_message_date,
            "description": description,
        }

    # --- инвайт ---
    if raw.startswith("invite:"):
        invite_hash = raw.split(":", 1)[1]
        info = await client(CheckChatInviteRequest(invite_hash))
        if isinstance(info, types.ChatInviteAlready):
            return await _full_for_channel(info.chat)
        if isinstance(info, types.ChatInvite):
            joined = await client(ImportChatInviteRequest(invite_hash))
            if joined.chats:
                return await _full_for_channel(joined.chats[0])
            raise RuntimeError("Не удалось определить канал после вступления")

    # --- username / id ---
    entity = await ensure_join_and_get_entity(client, raw)
    return await _full_for_channel(entity)


# ==================== Django view ====================

@login_required
@csrf_exempt
def parse_channel(request):
    """
    POST /api/parse_channel
    Body: {"link": "<@username | https://t.me/... | -100...>"}

    Определяет тип ссылки и возвращает JSON с данными канала.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Only POST allowed"}, status=405)

    try:
        body = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"error": "Некорректный JSON"}, status=400)

    link = (body.get("link") or body.get("username") or "").strip()
    if not link:
        return JsonResponse({"error": "Передайте ссылку на канал или @username"}, status=400)

    kind, value = _classify_input(link)

    # --- базовая валидация ---
    if kind == "username":
        if not USERNAME_RE.match(value):
            return JsonResponse(
                {"error": "Некорректный username. Пример: @somechannel или https://t.me/somechannel"},
                status=400,
            )
        raw = value
    elif kind == "invite":
        raw = f"invite:{value}"
    elif kind == "id":
        raw = value
    elif kind == "private_c":
        return JsonResponse(
            {"error": "Ссылка вида t.me/c/... относится к приватному чату/посту. Нужен доступ или инвайт-ссылка."},
            status=400,
        )
    else:
        return JsonResponse(
            {"error": "Ссылка не распознана. Укажите @username, https://t.me/<username> или валидную пригласительную ссылку."},
            status=400,
        )

    # --- выполняем асинхронную логику в клиенте ---
    try:
        data = run_in_client(_fetch_channel_info(raw))
        return JsonResponse(data, status=200)

    # --- обработка ошибок Telethon ---
    except InviteHashInvalidError:
        return JsonResponse({"error": "Некорректная пригласительная ссылка."}, status=400)
    except InviteHashExpiredError:
        return JsonResponse({"error": "Срок действия пригласительной ссылки истёк."}, status=410)
    except ChannelPrivateError:
        return JsonResponse({"error": "Канал приватный или нет доступа. Пришлите валидную инвайт-ссылку."}, status=403)
    except (UsernameNotOccupiedError, ChannelInvalidError):
        return JsonResponse({"error": "Канал не найден. Проверьте правильность ссылки/username."}, status=404)
    except UsernameInvalidError:
        return JsonResponse({"error": "Некорректный username/ссылка."}, status=400)
    except FloodWaitError as e:
        return JsonResponse({"error": f"Слишком много попыток. Подождите {e.seconds} сек."}, status=429)
    except Exception:
        logger.exception("parse_channel error")
        return JsonResponse({"error": "Внутренняя ошибка парсера"}, status=500)
