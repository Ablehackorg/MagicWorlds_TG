# entity_resolver.py
import asyncio
import time
import logging
from typing import Optional, Tuple

from telethon.errors import FloodWaitError
from telethon.tl.types import InputPeerUser, InputPeerChannel, InputPeerChat

log = logging.getLogger(__name__)

# Кэш: (bot_id, key) -> (entity, ts)
_CACHE: dict[Tuple[int, str], Tuple[object, float]] = {}
RESOLVE_TTL_SEC = 3600  # 1 час

async def ensure_peer(client, *, telegram_id: Optional[int] = None, link: Optional[str] = None):
    """
    Упрощенная и улучшенная версия резолвера
    """
    if not telegram_id and not link:
        raise ValueError("ensure_peer: нужен telegram_id или link")
    
    # Определяем bot_id для кэша
    try:
        me = await client.get_me()
        bot_id = me.id
    except:
        bot_id = -1

    now = time.time()
    cache_key = f"id:{telegram_id}" if telegram_id else f"link:{link}"

    # Проверяем кэш
    cached = _CACHE.get((bot_id, cache_key))
    if cached:
        entity, ts = cached
        if now - ts < RESOLVE_TTL_SEC:
            return entity
        else:
            del _CACHE[(bot_id, cache_key)]

    try:
        # Пробуем быстрые пути
        if telegram_id:
            try:
                entity = await client.get_input_entity(telegram_id)
                _CACHE[(bot_id, cache_key)] = (entity, now)
                return entity
            except (ValueError, FloodWaitError) as e:
                if isinstance(e, FloodWaitError):
                    await asyncio.sleep(e.seconds)
                    entity = await client.get_input_entity(telegram_id)
                    _CACHE[(bot_id, cache_key)] = (entity, now)
                    return entity
                # Продолжаем если ValueError

        if link:
            try:
                entity = await client.get_input_entity(link)
                _CACHE[(bot_id, cache_key)] = (entity, now)
                return entity
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds)
                entity = await client.get_input_entity(link)
                _CACHE[(bot_id, cache_key)] = (entity, now)
                return entity

        # Fallback: ищем в диалогах
        if telegram_id:
            async for dialog in client.iter_dialogs():
                entity_obj = dialog.entity
                if hasattr(entity_obj, 'id') and entity_obj.id == telegram_id:
                    _CACHE[(bot_id, cache_key)] = (entity_obj, now)
                    return entity_obj

        raise ValueError(f"Не удалось найти entity (id={telegram_id}, link={link})")

    except Exception as e:
        log.error(f"Ошибка в ensure_peer: {e}")
        raise