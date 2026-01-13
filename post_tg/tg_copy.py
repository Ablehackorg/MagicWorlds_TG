import asyncio
import random
import copy
from dataclasses import dataclass
from typing import List, Optional, Tuple
from datetime import datetime, timedelta
from telethon import types, utils
from telethon.tl.custom.message import Message
from telethon.tl.types import MessageMediaEmpty
import pytz
import re


# ==== Параметры/лимиты Telegram ====
MAX_TEXT_LEN = 4096
MAX_CAPTION_LEN = 2048

LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:c/)?([^/]+)/(\d+)")


# -----------------------------------------------------------------------------
#   Модель «собранного» поста (одиночный пост или альбом)
# -----------------------------------------------------------------------------
@dataclass
class BuiltPost:
    """Единица публикации: одиночное сообщение или альбом (группа сообщений)."""
    messages: List[Message]

    @property
    def is_album(self) -> bool:
        return len(self.messages) > 1

    @property
    def first(self) -> Message:
        return self.messages[0]
    


# -----------------------------------------------------------------------------
#   Вспомогательные функции
# -----------------------------------------------------------------------------

def _len16(s: str) -> int:
    return len(utils.add_surrogate(s or ""))

def _slice16(s: str, n16: int) -> str:
    """Обрезать строку по длине в UTF-16 код-юнитах, не ломая эмодзи."""
    s16 = utils.add_surrogate(s or "")
    cut16 = s16[:max(0, n16)]
    return utils.del_surrogate(cut16)

def _trim_entities_to_len16(entities, max_len16: int):
    """Обрезает entities, чтобы не выходили за границы текста (в UTF-16)."""
    if not entities:
        return None
    out = []
    for e in entities:
        start = int(getattr(e, "offset", 0))
        length = int(getattr(e, "length", 0))
        if start >= max_len16 or length <= 0:
            continue
        if start + length > max_len16:
            length = max_len16 - start
            if length <= 0:
                continue

        # --- создаём новый entity корректно ---
        if isinstance(e, types.MessageEntityCustomEmoji):
            ne = types.MessageEntityCustomEmoji(start, length, getattr(e, "document_id"))
        else:
            try:
                ne = e.__class__(start, length)
            except Exception:
                # fallback для редких типов
                ne = copy.copy(e)
                ne.offset = start
                ne.length = length

        # переносим все дополнительные поля (url, language, user_id, document_id и т.п.)
        for attr in ("url", "language", "user_id", "document_id", "custom_emoji_id"):
            if hasattr(e, attr) and not hasattr(ne, attr):
                setattr(ne, attr, getattr(e, attr))

        out.append(ne)
    return out or None


def _group_messages_for_posts(msgs: List[Message]) -> List[BuiltPost]:
    """Группирует сообщения в посты/альбомы."""
    albums = {}
    singles = []
    for m in msgs:
        gid = getattr(m, "grouped_id", None)
        if gid:
            albums.setdefault(gid, []).append(m)
        else:
            singles.append(m)

    posts: List[BuiltPost] = []
    for _, lst in albums.items():
        lst_sorted = sorted(lst, key=lambda x: (x.date, x.id))
        posts.append(BuiltPost(messages=lst_sorted))
    for s in singles:
        posts.append(BuiltPost(messages=[s]))
    posts.sort(key=lambda p: (p.first.date, p.first.id))
    return posts


def _pick_posts(posts: List[BuiltPost], choice_mode: str, limit: int) -> List[BuiltPost]:
    """Выбирает случайные или последовательные посты."""
    if not posts:
        return []
    if (choice_mode or "random") == "random":
        tmp = posts[:]
        random.shuffle(tmp)
        return tmp[: max(1, min(limit, len(tmp)))]
    return posts[: max(1, min(limit, len(posts)))]


def _parse_formatted_text(text: str, formatting_mode: str = "markdown"):
    """
    Конвертирует markdown-подобный текст (**жирный**, *курсив*, __подчеркнутый__, ~~зачеркнутый~~, `моно`)
    в текст + entities для Telegram.
    """
    if not text:
        return "", None

    if formatting_mode == "markdown":
        # Используем парсер Telethon (если есть)
        try:
            clean_text, entities = utils.parse_markdown(text)
            return clean_text, entities
        except Exception:
            pass

        # Фоллбэк: простая ручная обработка
        entities = []
        clean_text = ""
        i = 0
        while i < len(text):
            if text.startswith("**", i):
                end = text.find("**", i + 2)
                if end != -1:
                    start_offset = len(clean_text)
                    inner = text[i + 2:end]
                    clean_text += inner
                    entities.append(types.MessageEntityBold(start_offset, len(inner)))
                    i = end + 2
                    continue
            elif text.startswith("__", i):
                end = text.find("__", i + 2)
                if end != -1:
                    start_offset = len(clean_text)
                    inner = text[i + 2:end]
                    clean_text += inner
                    entities.append(types.MessageEntityUnderline(start_offset, len(inner)))
                    i = end + 2
                    continue
            elif text.startswith("*", i):
                end = text.find("*", i + 1)
                if end != -1:
                    start_offset = len(clean_text)
                    inner = text[i + 1:end]
                    clean_text += inner
                    entities.append(types.MessageEntityItalic(start_offset, len(inner)))
                    i = end + 1
                    continue
            elif text.startswith("~~", i):
                end = text.find("~~", i + 2)
                if end != -1:
                    start_offset = len(clean_text)
                    inner = text[i + 2:end]
                    clean_text += inner
                    entities.append(types.MessageEntityStrike(start_offset, len(inner)))
                    i = end + 2
                    continue
            elif text.startswith("`", i):
                end = text.find("`", i + 1)
                if end != -1:
                    start_offset = len(clean_text)
                    inner = text[i + 1:end]
                    clean_text += inner
                    entities.append(types.MessageEntityCode(start_offset, len(inner)))
                    i = end + 1
                    continue

            # по умолчанию
            clean_text += text[i]
            i += 1

        return clean_text, entities or None

    # fallback для plain-текста
    return text, None


def _append_formatted_suffix(
    text: Optional[str],
    entities: Optional[list],
    suffix: str,
    is_add_suffix: bool,
    *,
    is_caption: bool
):
    base = text or ""
    if not is_add_suffix or not suffix:
        return base, (entities or None)

    # Парсим суффикс с форматированием
    suffix_text, suffix_entities = _parse_formatted_text(suffix)

    sep = "\n" if base else ""
    limit = MAX_CAPTION_LEN if is_caption else MAX_TEXT_LEN

    # Формируем итоговый текст
    out_text = f"{base}{sep}{suffix_text}" if base else suffix_text

    # Рассчитываем длины в UTF-16
    base_utf16 = utils.add_surrogate(base)
    sep_utf16 = utils.add_surrogate(sep)
    suffix_utf16 = utils.add_surrogate(suffix_text)
    out_utf16 = utils.add_surrogate(out_text)

    base_len16 = len(base_utf16)
    sep_len16 = len(sep_utf16)
    suffix_len16 = len(suffix_utf16)
    out_len16 = len(out_utf16)

    # Проверяем длину и обрезаем если нужно
    if out_len16 > limit:
        # Вычисляем доступное место для суффикса в UTF-16
        base_with_sep_utf16 = base_utf16 + sep_utf16
        base_with_sep_len16 = len(base_with_sep_utf16)
        available_len16 = limit - base_with_sep_len16
        
        if available_len16 > 0:
            # Обрезаем суффикс в UTF-16
            suffix_utf16 = suffix_utf16[:available_len16]
            suffix_text = utils.del_surrogate(suffix_utf16)
            
            # Обрезаем entities суффикса
            if suffix_entities:
                suffix_entities = [e for e in suffix_entities if e.offset < available_len16]
                for e in suffix_entities:
                    if e.offset + e.length > available_len16:
                        e.length = available_len16 - e.offset
        else:
            suffix_text = ""
            suffix_entities = None
        
        # Обновляем итоговый текст
        out_text = utils.del_surrogate(base_with_sep_utf16) + suffix_text if base else suffix_text
        out_utf16 = utils.add_surrogate(out_text)

    # Формируем итоговые entities
    new_entities = []
    
    # Добавляем исходные entities без изменений (они уже в UTF-16)
    if entities:
        for entity in entities:
            new_entities.append(entity)

    # Добавляем entities суффикса со смещением в UTF-16
    if suffix_entities:
        base_offset_utf16 = base_len16 + sep_len16
        for entity in suffix_entities:
            # Создаем копию entity со смещенным offset в UTF-16
            new_entity = copy.copy(entity)
            new_entity.offset += base_offset_utf16
            new_entities.append(new_entity)

    return out_text, (new_entities or None)


def _reply_to_for_topic(topic_id: Optional[int]):
    if not topic_id:
        return None
    return types.InputReplyToForumTopic(topic_id)


def _is_text_only(msg: Message) -> bool:
    return not msg.media and (msg.text or "")


def _extract_caption_and_entities(msg: Message) -> Tuple[str, Optional[List[types.TypeMessageEntity]]]:
    return msg.message or "", msg.entities or None


# -----------------------------------------------------------------------------
#   Подготовка медиа
# -----------------------------------------------------------------------------
async def _prepare_media_for_send(client, msg: Message):
    """Возвращает параметры для send_message/send_file."""
    if getattr(msg, "photo", None):
        return {"mode": "photo", "file": msg.photo, "force_document": False}

    doc = getattr(msg, "document", None)
    if doc:
        mime = getattr(doc, "mime_type", "")
        is_image_doc = bool(mime.startswith("image/"))

        if is_image_doc:
            data = await client.download_media(msg, bytes)
            return {"mode": "photo", "file": data, "force_document": False}

        file_name = getattr(msg.file, "name", None)
        if not file_name:
            ext = mime.split("/")[-1] if "/" in mime else "bin"
            file_name = f"file_{msg.id}.{ext}"
            data = await client.download_media(msg, bytes)
            attrs = list(getattr(doc, "attributes", []) or [])
            attrs = [a for a in attrs if not isinstance(a, types.DocumentAttributeFilename)]
            attrs.append(types.DocumentAttributeFilename(file_name))
            return {"mode": "document", "file": data, "attributes": attrs, "force_document": True}

        return {"mode": "passthrough", "file": msg, "force_document": True}

    if getattr(msg, "media", None):
        return {"mode": "passthrough", "file": msg}
    return {"mode": "text"}

import logging
log = logging.getLogger("sync")
log.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
log.addHandler(handler)
# -----------------------------------------------------------------------------
#   Отправка
# -----------------------------------------------------------------------------
async def _send_single(
    client,
    target_entity,
    msg: Message,
    *,
    topic_id: Optional[int],
    text_suffix: str,
    is_add_suffix: bool,
):
    """Отправляет одиночное сообщение (сразу, без schedule)."""
    reply_to = _reply_to_for_topic(topic_id)
    if isinstance(getattr(msg, "media", None), MessageMediaEmpty):
        msg.media = None

    # добавление форматированного суффикса
    if text_suffix and is_add_suffix:
        base_text = msg.message or ""
        base_entities = msg.entities or []
        
        new_text, new_entities = _append_formatted_suffix(
            base_text, base_entities, text_suffix, True, is_caption=bool(msg.media)
        )
    else:
        new_text = msg.message or ""
        new_entities = msg.entities

    log.info(f"Превью2: {msg.web_preview != None}")
    # Обработка разных типов медиа
    if msg.media:
        # Проверяем тип медиа - MessageMediaWebPage нельзя использовать как файл
        if isinstance(msg.media, types.MessageMediaWebPage):
            # Для веб-превью отправляем как текстовое сообщение с ссылкой
            return await client.send_message(
                target_entity,
                message=new_text,
                formatting_entities=new_entities,
                reply_to=reply_to,
                link_preview=msg.web_preview != None,  # Включаем превью для веб-страниц
            )
        else:
            # Для других типов медиа (фото, видео, документы и т.д.)
            return await client.send_message(
                target_entity,
                message=new_text,
                formatting_entities=new_entities,
                reply_to=reply_to,
                link_preview=msg.web_preview != None,  # Отключаем превью, т.к. есть медиа
                file=msg.media
            )
    else:
        # Текстовое сообщение без медиа
        return await client.send_message(
            target_entity,
            message=new_text,
            formatting_entities=new_entities,
            reply_to=reply_to,
            link_preview=msg.web_preview != None,
        )

async def _send_album(
    client,
    target_entity,
    msgs: List[Message],
    *,
    topic_id: Optional[int],
    text_suffix: str,
    is_add_suffix: bool,
):
    reply_to = _reply_to_for_topic(topic_id)

    files = []
    captions = []
    entities_list = []

    for i, m in enumerate(msgs):
        if not m.media or isinstance(m.media, types.MessageMediaWebPage):
            continue

        caption, entities = _extract_caption_and_entities(m)
        if i == 0 and is_add_suffix and text_suffix:
            caption, entities = _append_formatted_suffix(
                caption, entities, text_suffix, True, is_caption=True
            )

        files.append(m.media)
        captions.append(caption or "")
        entities_list.append(entities or [])

    if not files:
        # fallback на текст
        caption, entities = _extract_caption_and_entities(msgs[0])
        caption, entities = _append_formatted_suffix(
            caption, entities, text_suffix, is_add_suffix, is_caption=False
        )
        return [await client.send_message(
            target_entity,
            caption or None,
            formatting_entities=entities,
            reply_to=reply_to,
            link_preview=True,
        )]

    try:
        sent = await client.send_file(
            target_entity,
            files,
            caption=captions,
            formatting_entities=entities_list,
            reply_to=reply_to,
            nosound_video=True,
        )
        return sent
    except Exception as e:
        print(f"[WARN] Ошибка при отправке альбома — fallback: {e}")
        # fallback на одиночные сообщения
        sent = []
        for i, (m, caption, entities) in enumerate(zip(msgs, captions, entities_list)):
            try:
                msg = await client.send_file(
                    target_entity,
                    m.media,
                    caption=caption,
                    formatting_entities=entities,
                    reply_to=reply_to,
                )
                sent.append(msg)
                await asyncio.sleep(0.3)
            except Exception as e2:
                print(f"[ERR] Ошибка при отправке {i}-го файла: {e2}")
        return sent


        
async def _prepare_media_for_send(client, msg: Message):
    """Возвращает параметры для send_message/send_file."""
    if getattr(msg, "photo", None):
        return {"mode": "photo", "file": msg.media, "force_document": False}

    doc = getattr(msg, "document", None)
    if doc:
        mime = getattr(doc, "mime_type", "")
        is_image_doc = bool(mime.startswith("image/"))

        if is_image_doc:
            # Для изображений в документах - используем как фото
            data = await client.download_media(msg, bytes)
            return {"mode": "photo", "file": data, "force_document": False}

        # Для остальных документов
        return {"mode": "document", "file": msg.media, "force_document": True}

    if getattr(msg, "media", None):
        return {"mode": "passthrough", "file": msg.media}
    return {"mode": "text"}


# -----------------------------------------------------------------------------
#   Публичные функции
# -----------------------------------------------------------------------------
async def build_post(client, source_id: int, limit: int = 5000) -> List[BuiltPost]:
    """Возвращает список собранных постов из источника."""
    peer = await client.get_entity(source_id)
    msgs: List[Message] = []
    async for m in client.iter_messages(peer, limit=limit):
        if getattr(m, "action", None) or getattr(m, "service", False):
            continue
        if not (m.message or m.media):
            continue
        msgs.append(m)

    if not msgs:
        return []
    
    posts = _group_messages_for_posts(msgs)
    return posts


async def send_post(
    client,
    post: BuiltPost,
    target_id: int,
    *,
    topic_id: Optional[int] = None,
    text_suffix: str = "",
    is_add_suffix: bool = False,
) -> List[int]:
    """Публикует собранный пост (одиночный или альбом)."""
    target_entity = await client.get_entity(target_id)
    if post.is_album:
        res = await _send_album(
            client,
            target_entity,
            post.messages,
            topic_id=topic_id,
            text_suffix=text_suffix,
            is_add_suffix=is_add_suffix,
        )
        return [getattr(m, "id", None) for m in res] if isinstance(res, list) else [res.id]
    else:
        m = await _send_single(
            client,
            target_entity,
            post.first,
            topic_id=topic_id,
            text_suffix=text_suffix,
            is_add_suffix=is_add_suffix,
        )
        return [m.id]
