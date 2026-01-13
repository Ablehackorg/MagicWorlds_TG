# post_tg/ads_sync.py
import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytz
from telethon import events
from telethon.errors import RPCError, FloodWaitError
from telethon.tl.types import InputPeerUser
from sqlalchemy import select

from utils.db_utils import get_session
from telegram_client import init_user_client
from entity_resolver import ensure_peer
from tg_copy import BuiltPost, send_post
from models import AdsOrder, MainEntity, BotSession

from utils.tg_links import parse_post_link

# Настройка логирования
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ads")

# Уменьшаем логирование telethon
logging.getLogger('telethon').setLevel(logging.WARNING)

CHECK_INTERVAL = int(os.getenv("ADS_CHECK_INTERVAL", "30"))
ADMIN_CHAT_ID = int(os.getenv("ADS_ADMIN_CHAT_ID", "0"))
TZ = pytz.timezone(os.getenv("TZ", "Europe/Moscow"))

UNPIN_AFTER = timedelta(hours=1)
DELETE_AFTER = timedelta(hours=24)

# --- helpers ---

def _utcnow():
    return datetime.now(timezone.utc)

def _ensure_utc(dt: datetime) -> datetime:
    """Конвертирует datetime в UTC"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return TZ.localize(dt).astimezone(timezone.utc)
    else:
        return dt.astimezone(timezone.utc)

def _format_datetime_moscow(dt: datetime) -> tuple:
    """Форматирует datetime в московское время для уведомлений"""
    moscow_dt = dt.astimezone(TZ)
    date_str = moscow_dt.strftime("%d.%m.%Y")
    time_str = moscow_dt.strftime("%H:%M")
    return date_str, time_str

def _need_publish(task: AdsOrder) -> bool:
    if task.published_at is not None:
        return False
    now_utc = _utcnow()
    publish_at_utc = _ensure_utc(task.publish_at)
    return now_utc >= publish_at_utc

def _need_unpin(task: AdsOrder) -> bool:
    if task.pinned_at is None or task.unpinned_at is not None:
        return False
    now_utc = _utcnow()
    pinned_at_utc = _ensure_utc(task.pinned_at)
    return now_utc >= pinned_at_utc + UNPIN_AFTER

def _need_delete(task: AdsOrder) -> bool:
    if task.published_at is None or task.deleted_at is not None:
        return False
    now_utc = _utcnow()
    published_at_utc = _ensure_utc(task.published_at)
    return now_utc >= published_at_utc + DELETE_AFTER

async def _notify(client, user_id: int, text: str):
    """Улучшенная функция уведомлений с обработкой FloodWait"""
    if not user_id:
        return
    
    try:
        # Пробуем получить entity пользователя
        try:
            entity = await client.get_entity(user_id)
        except ValueError:
            # Если не нашли по ID, пробуем как InputPeerUser
            try:
                entity = InputPeerUser(user_id=user_id, access_hash=0)
            except Exception:
                log.warning(f"⚠️ Не удалось найти пользователя {user_id} для уведомления")
                return
        
        # Отправляем сообщение как есть - Telegram сам создаст превью для распознанных ссылок
        await client.send_message(entity, text)
        log.debug(f"✅ Уведомление отправлено пользователю {user_id}")
        
    except FloodWaitError as e:
        log.warning(f"⏳ FloodWait при отправке уведомления {user_id}: {e.seconds} сек")
        await asyncio.sleep(e.seconds)
        # Повторяем попытку после ожидания
        await _notify(client, user_id, text)
    except Exception as e:
        log.warning(f"⚠️ Ошибка отправки уведомления пользователю {user_id}: {e}")

async def _build_post_from_link(client, link: str) -> BuiltPost:
    """Упрощенная версия построения поста"""
    try:
        chat_id, username, msg_id = parse_post_link(link)
        
        # Получаем peer - используем ensure_peer который сам обработает все случаи
        peer = await ensure_peer(client, telegram_id=chat_id, link=f"@{username}" if username else None)
        
        # Получаем сообщение
        msg = await client.get_messages(peer, ids=msg_id)
        
        if not msg:
            raise ValueError(f"Сообщение {msg_id} не найдено")

        # Проверяем альбом
        gid = getattr(msg, "grouped_id", None)
        if gid:
            msgs = []
            async for m in client.iter_messages(peer, limit=100):
                if getattr(m, "grouped_id", None) == gid:
                    msgs.append(m)
            
            if not msgs:
                msgs = [msg]
            
            msgs.sort(key=lambda x: (x.date, x.id))
            return BuiltPost(messages=msgs)

        return BuiltPost(messages=[msg])

    except Exception as e:
        log.error(f"❌ Ошибка построения поста из {link}: {e}")
        raise

def _target_link_for(task: AdsOrder) -> str:
    """Генерирует ссылку на сообщение"""
    try:
        abs_id = abs(int(task.target.telegram_id or 0))
        mid = int(task.target_message_id or 0)
        if abs_id and mid:
            return f"https://t.me/c/{abs_id}/{mid}"
    except Exception:
        log.error(f"Ошибка создания ссылка: {e}")
        pass
    return ""

# --- основной цикл ---

async def process_once():
    """Один проход с улучшенным логированием"""
    with get_session() as s:
        tasks = (
            s.execute(
                select(AdsOrder).where(
                    AdsOrder.is_active == True,
                    AdsOrder.is_paid == True,
                ).order_by(AdsOrder.publish_at.asc())
            ).scalars().all()
        )

        tasks = [t for t in tasks if not (
            t.published_at and t.pinned_at and t.unpinned_at and t.deleted_at
        )]

        bot_ids = sorted(set(t.bot_id for t in tasks))
        bots = {b.id: b for b in s.execute(select(BotSession).where(BotSession.id.in_(bot_ids))).scalars().all()}

    if not tasks:
        log.debug("🔍 Нет активных платных рекламных задач")
        return

    log.info(f"🔍 Проверяем {len(tasks)} задач")

    # Поднимаем клиентов
    clients = {}
    for bid in bots:
        try:
            client = init_user_client(bots[bid])
            await client.start()
            if not await client.is_user_authorized():
                raise RuntimeError(f"Бот #{bid} не авторизован")
            clients[bid] = client
        except Exception as e:
            log.warning(f"⚠️ Ошибка инициализации бота #{bid}: {e}")

    # Обработка задач
    try:
        for task in tasks:
            client = clients.get(task.bot_id)
            if not client:
                continue

            # Обновляем target из БД
            with get_session() as s:
                target = s.get(MainEntity, task.target_id)

            # Публикация
            if _need_publish(task):
                try:
                    log.info(f"🚀 Публикую задачу #{task.id}: {task.name}")
                    
                    post = await _build_post_from_link(client, task.post_link)
                    suffix = getattr(target, "text_suffix", "") or ""
                    is_add_suffix = bool(getattr(target, "is_add_suffix", True))

                    target_entity = await ensure_peer(client, telegram_id=target.telegram_id, link=target.link)
                    sent_ids = await send_post(
                        client, post, target_entity,
                        topic_id=None,
                        text_suffix=suffix,
                        is_add_suffix=is_add_suffix
                    )
                    sent_id = sent_ids[-1] if sent_ids else None

                    # Закрепляем
                    if sent_id:
                        try:
                            await client.pin_message(target_entity, sent_id, notify=False)
                        except Exception as e:
                            log.warning(f"⚠️ Ошибка закрепления: {e}")

                    # Сохраняем в БД
                    current_utc = _utcnow()
                    with get_session() as s:
                        db_task = s.get(AdsOrder, task.id)
                        db_task.published_at = current_utc
                        db_task.pinned_at = current_utc
                        db_task.target_message_id = sent_id
                        s.commit()

                    # Уведомления для заказчика (новый текст)
                    if task.notify_customer and task.customer_telegram:
                        published_date, published_time = _format_datetime_moscow(current_utc)
                        try:
                            # Получаем информацию о канале
                            channel_entity = await client.get_entity(target_entity)
                            
                            # Пробуем получить username для красивой ссылки
                            username = getattr(channel_entity, 'username', None)
                            
                            if username:
                                link_to_target = f"https://t.me/{username}/{sent_id}"
                            else:
                                # Если username нет, формируем ссылку через ID
                                channel_id = getattr(channel_entity, 'id', None)
                                if channel_id:
                                    # Преобразуем ID канала в правильный формат
                                    raw_id = str(abs(channel_id))
                                    if raw_id.startswith('100'):
                                        clean_id = raw_id[3:]
                                    else:
                                        clean_id = raw_id
                                    link_to_target = f"https://t.me/c/{clean_id}/{sent_id}"
                                else:
                                    link_to_target = _target_link_for(task)
                            
                            log.info(f"🔗 Сформирована прямая ссылка: {link_to_target}")
                        except Exception as e:
                            log.warning(f"⚠️ Не удалось сформировать прямую ссылку: {e}")
                            link_to_target = _target_link_for(task)  # fallback   

                        notification_text = f"""Уважаемый рекламодатель!
Информируем Вас, что в сообществе {target.name} опубликована Ваша реклама. Заказан рекламный пакет 1/24 :

__Время старта__ публикации: {published_time}, дата {published_date}
__Ссылка__ на рекламный пост: {link_to_target}

*указано московское время

По всем вопросам рекламы просим обращаться: @magic_worlds_ads"""
    
                        await _notify(client, task.customer_telegram, notification_text)

                    # Уведомление для админа (оставляем старое)
                    if task.notify_admin and ADMIN_CHAT_ID:
                        link_to_target = _target_link_for(task)
                        await _notify(client, ADMIN_CHAT_ID,
                                    f"📣 Опубликована реклама #{task.id} '{task.name}' → {getattr(target,'name','')}\n{link_to_target}")

                    log.info(f"✅ Задача #{task.id} опубликована (сообщение {sent_id})")

                except Exception as e:
                    log.error(f"❌ Ошибка публикации задачи #{task.id}: {e}")

            # Открепление - убираем все уведомления
            if _need_unpin(task):
                try:
                    log.info(f"🔓 Открепляю задачу #{task.id}")
                    
                    target_entity = await ensure_peer(client, telegram_id=task.target.telegram_id, link=task.target.link)
                    if task.target_message_id:
                        try:
                            await client.unpin_message(target_entity, task.target_message_id)
                        except RPCError as e:
                            log.debug(f"ℹ️ Сообщение уже откреплено: {e}")

                    with get_session() as s:
                        db_task = s.get(AdsOrder, task.id)
                        db_task.unpinned_at = _utcnow()
                        s.commit()

                    log.info(f"✅ Задача #{task.id} откреплена")

                except Exception as e:
                    log.warning(f"⚠️ Ошибка открепления задачи #{task.id}: {e}")

            # Удаление
            if _need_delete(task):
                try:
                    log.info(f"🗑️ Удаляю задачу #{task.id}")
                    
                    target_entity = await ensure_peer(client, telegram_id=task.target.telegram_id, link=task.target.link)
                    if task.target_message_id:
                        try:
                            await client.delete_messages(target_entity, [task.target_message_id], revoke=True)
                        except RPCError as e:
                            log.debug(f"ℹ️ Сообщение уже удалено: {e}")

                    with get_session() as s:
                        db_task = s.get(AdsOrder, task.id)
                        db_task.deleted_at = _utcnow()
                        s.commit()

                    # Уведомления для заказчика (новый текст)
                    if task.notify_customer and task.customer_telegram:
                        # Получаем времена из БД
                        published_at_utc = _ensure_utc(task.published_at)
                        pinned_at_utc = _ensure_utc(task.pinned_at)
                        unpinned_at_utc = _ensure_utc(task.unpinned_at) if task.unpinned_at else published_at_utc + UNPIN_AFTER
                        
                        # Форматируем даты и время
                        pin_start_date, pin_start_time = _format_datetime_moscow(pinned_at_utc)
                        pin_end_date, pin_end_time = _format_datetime_moscow(unpinned_at_utc)
                        feed_start_date, feed_start_time = _format_datetime_moscow(published_at_utc)
                        feed_end_date, feed_end_time = _format_datetime_moscow(_utcnow())
                        
                        notification_text = f"""Уважаемый рекламодатель!
Информируем Вас, что в сообществе {target.name} завершена публикация Вашей рекламы:

▫️__В закрепе__ сообщества:
с {pin_start_time} до {pin_end_time} , дата {pin_start_date}

▫️__В ленте__ сообщества:
с {feed_start_time}, дата {feed_start_date} по {feed_end_time}, дата {feed_end_date}

*указано московское время

Спасибо, что воспользовались нашими услугами 🙏
По всем вопросам рекламы просим обращаться: @magic_worlds_ads"""
                        
                        await _notify(client, task.customer_telegram, notification_text)

                    # Уведомление для админа (оставляем старое)
                    if task.notify_admin and ADMIN_CHAT_ID:
                        await _notify(client, ADMIN_CHAT_ID,
                                    f"🗑️ Удалена реклама #{task.id} '{task.name}'")

                    log.info(f"✅ Задача #{task.id} удалена")

                except Exception as e:
                    log.warning(f"⚠️ Ошибка удаления задачи #{task.id}: {e}")

    finally:
        # Закрываем клиентов
        for c in clients.values():
            try:
                await c.disconnect()
            except Exception:
                pass

async def run_ads_sync():
    log.info("🚀 Синхронизация рекламы запущена")
    while True:
        try:
            await process_once()
        except Exception as e:
            log.error(f"❌ Ошибка в цикле синхронизации: {e}")
        await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(run_ads_sync())