import os
import asyncio
import logging
from datetime import datetime, timedelta, time, date, timezone
from typing import Optional

import pytz
from telethon import TelegramClient, types
from telethon.tl.types import MessageActionPinMessage, MessageService
from telethon.errors import RPCError
from sqlalchemy import select

from utils.db_utils import get_session
from telegram_client import init_user_client
from entity_resolver import ensure_peer
from models import DailyPinningTask, BotSession, MainEntity
from utils.tg_links import parse_post_link

# Настройка логирования
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("daily_pinner")

# Уменьшаем логирование telethon
logging.getLogger('telethon').setLevel(logging.WARNING)

CHECK_INTERVAL = int(os.getenv("DAILY_PIN_CHECK_INTERVAL", "1400"))
TZ = pytz.timezone(os.getenv("TZ", "Europe/Moscow"))

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

def _is_in_time_interval(task: DailyPinningTask) -> bool:
    """Проверяет, находимся ли мы в рабочем интервале задачи"""
    now = datetime.now(TZ)
    current_time = (now - timedelta(hours=1)).time()
    result = task.start_time <= current_time <= task.end_time
    log.info(f"🕐 Временной интервал задачи #{task.id}: {task.start_time} <= {now.time()} <= {task.end_time}(+1 час) = {result}")
    return result

def _should_reset_daily_counters(task: DailyPinningTask) -> bool:
    """Проверяет, нужно ли сбросить дневные счетчики (новый день)"""
    today = date.today()
    result = task.last_cycle_date != today
    if result:
        log.info(f"🔄 Задача #{task.id}: сброс счетчиков (новый день) - {task.last_cycle_date} -> {today}")
    return result

def _is_two_hour_activation_time() -> bool:
    """Проверяет, находимся ли мы в момент активации (каждые 2 часа)"""
    now = datetime.now(TZ)
    current_hour = now.hour
    
    # Активируем в четные часы: 8, 10, 12, 14, 16, 18 и т.д.
    # Но только если прошло хотя бы 2 часа с начала интервала
    if current_hour % 2 != 0:
        return False
    
    # Проверяем, что мы в первой половине часа (первые 30 минут)
    # чтобы избежать многократных срабатываний в течение часа
    if now.minute > 30:
        return False
        
    log.info(f"⏰ Текущее время подходит для двухчасовой активации: {now.strftime('%H:%M')}")
    return True

def _should_activate_task(task: DailyPinningTask) -> bool:
    """Проверяет, нужно ли активировать задачу в текущий момент"""
    now = datetime.now(TZ)
    current_hour = now.hour
    
    # Проверяем, что находимся в интервале задачи
    if not _is_in_time_interval(task):
        return False
    
    # Проверяем двухчасовую активацию
    if not _is_two_hour_activation_time():
        return False
    
    # Вычисляем, прошло ли достаточно времени с начала интервала
    start_hour = task.start_time.hour
    hours_since_start = current_hour - start_hour
    
    # Активируем только если прошло хотя бы 2 часа от начала интервала
    # и текущий час кратен 2
    if hours_since_start >= 2 and current_hour % 2 == 0:
        log.info(f"🎯 Задача #{task.id}: активируем (с начала интервала прошло {hours_since_start} часов)")
        return True
    
    log.info(f"⏹️ Задача #{task.id}: не время для активации (с начала интервала прошло {hours_since_start} часов)")
    return False

def _need_pin(task: DailyPinningTask, recent_posts_count: int) -> bool:
    """Нужно ли закреплять пост - проверяем лимит постов за последние 2 часа"""
    if task.pinned_at is not None:
        log.info(f"⏹️ Задача #{task.id}: уже закреплено в этом цикле")
        return False
    
    # Проверяем, находимся ли в интервале и в двухчасовом окне активации
    if not _should_activate_task(task):
        return False
        
    # Если сегодняшние счетчики устарели - сбрасываем
    if _should_reset_daily_counters(task):
        return True
        
    # Проверяем лимит: постов за последние 2 часа должно быть 0
    result = recent_posts_count == 0
    log.info(f"📊 Задача #{task.id}: постов за последние 2 часа: {recent_posts_count}, нужно закреплять: {result}")
    return result

def _need_unpin(task: DailyPinningTask) -> bool:
    """Нужно ли откреплять пост"""
    if task.pinned_at is None or task.unpinned_at is not None:
        return False
        
    now_utc = _utcnow()
    pinned_at_utc = _ensure_utc(task.pinned_at)
    unpin_delta = timedelta(minutes=task.unpin_after_minutes)
    
    result = now_utc >= pinned_at_utc + unpin_delta
    if result:
        log.info(f"🔓 Задача #{task.id}: время открепления наступило")
    return result

def _need_delete_notification(task: DailyPinningTask) -> bool:
    """Нужно ли удалять уведомление о закреплении"""
    if task.pinned_at is None or task.notification_deleted_at is not None:
        return False
        
    now_utc = _utcnow()
    pinned_at_utc = _ensure_utc(task.pinned_at)
    delete_delta = timedelta(minutes=task.delete_notification_after_minutes)
    
    result = now_utc >= pinned_at_utc + delete_delta
    if result:
        log.info(f"🗑️ Задача #{task.id}: время удаления уведомления наступило")
    return result

def _cycle_completed(task: DailyPinningTask) -> bool:
    """Завершен ли текущий цикл (откреплено + удалено уведомление)"""
    result = task.unpinned_at is not None and task.notification_deleted_at is not None
    if result:
        log.info(f"🔄 Задача #{task.id}: цикл завершен")
    return result

async def _get_recent_posts_count(client, channel_entity, hours: int = 2) -> int:
    """Считает количество постов за последние N часов в канале"""
    try:
        now = datetime.now(TZ)
        start_time = now - timedelta(hours=hours, minutes=30)
        
        count = 0
        seen_grouped_ids = set()  # Для отслеживания уже учтенных медиа-групп
        
        async for message in client.iter_messages(
            channel_entity, 
            offset_date=start_time,
            reverse=True
        ):
            # Игнорируем служебные сообщения и закрепленные сообщения
            if (getattr(message, 'action', None) or 
                getattr(message, 'service', False) or
                getattr(message, 'pinned', False)):
                continue
                
            # Проверяем, что сообщение в нужном временном интервале
            message_date = message.date.astimezone(TZ)
            if message_date < start_time:
                break
                
            if start_time <= message_date <= now:
                # Проверяем, является ли сообщение частью медиа-группы
                grouped_id = getattr(message, 'grouped_id', None)
                if grouped_id:
                    # Если это новая медиа-группа - считаем как один пост
                    if grouped_id not in seen_grouped_ids:
                        seen_grouped_ids.add(grouped_id)
                        count += 1
                        log.debug(f"📦 Учтена медиа-группа {grouped_id} как один пост")
                else:
                    # Одиночное сообщение - считаем как один пост
                    count += 1
            else:
                break
            
        log.info(f"📈 Задача: постов за последние {hours} часов: {count} (медиа-групп: {len(seen_grouped_ids)})")
        return count
    except Exception as e:
        log.error(f"❌ Ошибка подсчета постов: {e}")
        return 0

async def _get_message_id_from_link(client, link: str, channel_entity) -> Optional[int]:
    """Получает ID сообщения из ссылки для закрепления"""
    if not link or not link.strip():
        raise ValueError("Ссылка на пост не может быть пустой")
    
    try:
        chat_id, username, msg_id = parse_post_link(link)
        if not msg_id:
            raise ValueError("Не удалось извлечь ID сообщения из ссылки")
        
        # Проверяем, что сообщение существует в целевом канале
        try:
            message = await client.get_messages(channel_entity, ids=msg_id)
            if not message:
                raise ValueError(f"Сообщение {msg_id} не найдено в канале")
            log.info(f"✅ Сообщение {msg_id} найдено в канале")
            return msg_id
        except Exception as e:
            raise ValueError(f"Не удалось получить сообщение {msg_id} из канала: {e}")
            
    except Exception as e:
        log.error(f"❌ Ошибка получения ID сообщения из '{link}': {e}")
        raise

async def _pin_existing_message(client, channel_entity, message_id: int) -> bool:
    """Закрепляет существующее сообщение в канале"""
    try:
        await client.pin_message(channel_entity, message_id, notify=True)
        log.info(f"📌 Сообщение {message_id} закреплено")
        return True
    except Exception as e:
        log.error(f"❌ Ошибка закрепления сообщения {message_id}: {e}")
        return False

async def _unpin_message(client, channel_entity, message_id: int):
    """Открепляет сообщение"""
    try:
        await client.unpin_message(channel_entity, message_id)
        log.info(f"🔓 Сообщение {message_id} откреплено")
    except RPCError as e:
        log.debug(f"ℹ️ Сообщение уже откреплено: {e}")
    except Exception as e:
        log.warning(f"⚠️ Ошибка открепления: {e}")

async def _delete_notification(client, channel_entity, pinned_message_id: int):
    """Удаляет последнее сервисное сообщение в канале"""
    try:
        log.info(f"🗑️ Удаляем последнее сервисное сообщение в канале")
        
        found_count = 0
        
        # Простой подход: берем последние 50 сообщений и ищем первое сервисное
        async for msg in client.iter_messages(channel_entity, limit=50):
            # Проверяем, является ли сообщение сервисным ЛЮБЫМ способом
            is_service = (
                isinstance(msg, MessageService) or 
                getattr(msg, 'service', False) or 
                getattr(msg, 'action', None) is not None
            )
            
            if is_service:
                try:
                    msg_id = getattr(msg, 'id', 'unknown')
                    msg_date = getattr(msg, 'date', 'unknown')
                    action_type = type(getattr(msg, 'action', None)).__name__ if getattr(msg, 'action', None) else 'None'
                    reply_to = getattr(msg, 'reply_to_msg_id', 'unknown')
                    
                    log.info(f"🔍 Найдено сервисное сообщение: ID {msg_id}, Дата: {msg_date}, "
                            f"Действие: {action_type}, Reply_to: {reply_to}")
                    
                    # Удаляем это сервисное сообщение
                    await client.delete_messages(channel_entity, [msg_id])
                    log.info(f"🗑️ УДАЛЕНО сервисное сообщение {msg_id}")
                    found_count = 1
                    break  # Удаляем только первое найденное
                    
                except Exception as e:
                    log.warning(f"⚠️ Не удалось удалить сервисное сообщение {msg_id}: {e}")
        
        if found_count > 0:
            log.info(f"✅ Удалено {found_count} сервисных сообщений")
        else:
            log.warning("⚠️ Сервисные сообщения не найдены в последних 50 сообщениях")
            
    except Exception as e:
        log.warning(f"⚠️ Ошибка удаления сервисного сообщения: {e}")

async def _force_delete_all_service_messages(client, channel_entity):
    """Принудительно удаляет ВСЕ сервисные сообщения в канале"""
    try:
        log.info("🔄 ПРИНУДИТЕЛЬНОЕ УДАЛЕНИЕ ВСЕХ СЕРВИСНЫХ СООБЩЕНИЙ")
        
        deleted_count = 0
        
        async for msg in client.iter_messages(channel_entity, limit=100):
            # Проверяем ВСЕМИ способами, является ли сообщение сервисным
            is_service = (
                isinstance(msg, MessageService) or 
                getattr(msg, 'service', False) or 
                getattr(msg, 'action', None) is not None
            )
            
            if is_service:
                try:
                    msg_id = getattr(msg, 'id', 'unknown')
                    await client.delete_messages(channel_entity, [msg_id])
                    log.info(f"🗑️ ПРИНУДИТЕЛЬНО УДАЛЕНО: {msg_id}")
                    deleted_count += 1
                except Exception as e:
                    log.warning(f"⚠️ Не удалось удалить {msg_id}: {e}")
        
        log.info(f"📊 Принудительно удалено {deleted_count} сервисных сообщений")
        
    except Exception as e:
        log.warning(f"⚠️ Ошибка принудительного удаления: {e}")

async def _is_pin_notification(msg) -> bool:
    """Проверяет, является ли сообщение уведомлением о закреплении"""
    try:
        # Проверка типа действия (самый надежный способ)
        action = getattr(msg, 'action', None)
        if action and isinstance(action, MessageActionPinMessage):
            return True
            
        # Дополнительная проверка по тексту (на разных языках)
        message_text = (msg.text or '').lower()
        pin_keywords = [
            'закрепил', 'pinned', 'закріпив', 'pin', 
            'закрепила', 'закріпила', 'зафиксировал', 'закрепило',
            'fixed', 'pinned a message', 'закріплено', 'закріпило'
        ]
        
        if any(keyword in message_text for keyword in pin_keywords):
            return True
            
        # Проверка для русской локали Telegram
        if 'закрепил' in message_text or 'pinned' in message_text:
            return True
            
        return False
        
    except Exception as e:
        log.warning(f"⚠️ Ошибка проверки уведомления: {e}")
        return False

# --- основной цикл ---

async def process_once():
    """Один проход обработки задач ежедневного закрепления"""
    with get_session() as s:
        tasks = s.execute(
            select(DailyPinningTask).where(
                DailyPinningTask.is_active == True
            )
        ).scalars().all()

    if not tasks:
        log.info("🔍 Нет активных задач ежедневного закрепления")
        return

    log.info(f"🔍 Проверяем {len(tasks)} задач закрепления")

    # Группируем задачи по ботам
    bot_ids = sorted(set(t.bot_id for t in tasks))
    
    with get_session() as s:
        bots = {b.id: b for b in s.execute(
            select(BotSession).where(BotSession.id.in_(bot_ids))
        ).scalars().all()}

    # Поднимаем клиентов
    clients = {}
    for bid in bot_ids:
        try:
            client = init_user_client(bots[bid])
            await client.start()
            if not await client.is_user_authorized():
                raise RuntimeError(f"Бот #{bid} не авторизован")
            clients[bid] = client
            log.info(f"✅ Бот #{bid} авторизован")
        except Exception as e:
            log.warning(f"⚠️ Ошибка инициализации бота #{bid}: {e}")

    # Обработка задач
    try:
        for task in tasks:
            log.info(f"🔍 Обрабатываем задачу #{task.id}")
            client = clients.get(task.bot_id)
            if not client:
                log.warning(f"⚠️ Для задачи #{task.id} нет клиента (bot_id: {task.bot_id})")
                continue

            # Получаем актуальные данные из БД
            with get_session() as s:
                db_task = s.get(DailyPinningTask, task.id)
                channel = s.get(MainEntity, task.channel_id)
                
                if not channel:
                    log.warning(f"⚠️ Задача #{task.id}: канал не найден")
                    continue

                log.info(f"📋 Задача #{task.id}: канал '{channel.name}', пост: {db_task.post_link}, интервал: {db_task.start_time}-{db_task.end_time}")

            # Получаем entity канала
            try:
                channel_entity = await ensure_peer(client, telegram_id=channel.telegram_id, link=channel.link)
                log.info(f"✅ Получен entity канала для задачи #{task.id}")
            except Exception as e:
                log.warning(f"⚠️ Задача #{task.id}: не удалось получить канал: {e}")
                continue

            # Сброс счетчиков при новом дне
            if _should_reset_daily_counters(db_task):
                log.info(f"🔄 Задача #{task.id}: новый день, сбрасываем счетчики")
                with get_session() as s:
                    db_task = s.get(DailyPinningTask, task.id)
                    db_task.total_yesterday = db_task.total_today
                    db_task.dummy_yesterday = db_task.dummy_today
                    db_task.total_today = 0
                    db_task.dummy_today = 0
                    db_task.last_cycle_date = date.today()
                    # Сбрасываем состояние для нового дня
                    db_task.pinned_at = None
                    db_task.unpinned_at = None
                    db_task.notification_deleted_at = None
                    db_task.pinned_message_id = None
                    s.commit()
                    log.info(f"✅ Счетчики сброшены для задачи #{task.id}")

            # Если цикл завершен - сбрасываем состояние для возможного нового закрепления
            if _cycle_completed(db_task):
                log.info(f"🔄 Задача #{task.id}: цикл завершен, сбрасываем состояние")
                with get_session() as s:
                    db_task = s.get(DailyPinningTask, task.id)
                    db_task.pinned_at = None
                    db_task.unpinned_at = None
                    db_task.notification_deleted_at = None
                    db_task.pinned_message_id = None
                    s.commit()
                    log.info(f"✅ Состояние сброшено для задачи #{task.id}")

            # Получаем текущее количество постов в канале за последние 2 часа
            recent_posts = await _get_recent_posts_count(client, channel_entity, hours=2)
            
            # Обновляем счетчик постов в БД (общее количество за сегодня)
            today_posts = await _get_recent_posts_count(client, channel_entity, hours=24)  # Посты за последние 24 часа
            with get_session() as s:
                db_task = s.get(DailyPinningTask, task.id)
                db_task.total_today = today_posts
                s.commit()
            
            # Закрепление - проверяем посты за последние 2 часа
            if _need_pin(db_task, recent_posts):
                try:
                    # Проверяем, что ссылка на пост не пустая
                    if not db_task.post_link or not db_task.post_link.strip():
                        log.error(f"❌ Задача #{task.id}: отсутствует ссылка на пост")
                        continue

                    log.info(f"🎯 Задача #{task.id}: ЗАКРЕПЛЯЕМ (постов за 2 часа: {recent_posts})")
                    
                    # Получаем ID сообщения из ссылки и закрепляем его
                    message_id = await _get_message_id_from_link(client, db_task.post_link, channel_entity)
                    success = await _pin_existing_message(client, channel_entity, message_id)
                    
                    if success:
                        current_utc = _utcnow()
                        with get_session() as s:
                            db_task = s.get(DailyPinningTask, task.id)
                            db_task.pinned_at = current_utc
                            db_task.pinned_message_id = message_id
                            db_task.dummy_today += 1  # Увеличиваем счетчик пустышек
                            s.commit()
                        log.info(f"✅ Задача #{task.id}: пост закреплен (msg_id: {message_id}), пустышек сегодня: {db_task.dummy_today}")
                    else:
                        log.error(f"❌ Задача #{task.id}: не удалось закрепить пост")

                except Exception as e:
                    log.error(f"❌ Ошибка закрепления задачи #{task.id}: {e}")
            else:
                # Логируем, почему не закрепляем
                if recent_posts > 0:
                    log.info(f"⏹️ Задача #{task.id}: есть посты за последние 2 часа ({recent_posts}), пропускаем")
                elif db_task.pinned_at is not None:
                    log.info(f"⏹️ Задача #{task.id}: уже закреплено в этом цикле")
                elif not _is_in_time_interval(db_task):
                    log.info(f"⏹️ Задача #{task.id}: вне временного интервала {db_task.start_time}-{db_task.end_time}")
                elif not _should_activate_task(db_task):
                    log.info(f"⏹️ Задача #{task.id}: не время для двухчасовой активации")
                else:
                    log.info(f"⏹️ Задача #{task.id}: неизвестная причина")

            # Открепление
            if _need_unpin(db_task):
                try:
                    log.info(f"🔓 Задача #{task.id}: открепляем сообщение")
                    if db_task.pinned_message_id:
                        await _unpin_message(client, channel_entity, db_task.pinned_message_id)
                    
                    with get_session() as s:
                        db_task = s.get(DailyPinningTask, task.id)
                        db_task.unpinned_at = _utcnow()
                        s.commit()
                    log.info(f"✅ Задача #{task.id}: сообщение откреплено")

                except Exception as e:
                    log.warning(f"⚠️ Ошибка открепления задачи #{task.id}: {e}")

            # Удаление уведомления
            if _need_delete_notification(db_task):
                try:
                    log.info(f"🗑️ Задача #{task.id}: удаляем уведомление")
                    await _delete_notification(client, channel_entity, db_task.pinned_message_id)
                    
                    with get_session() as s:
                        db_task = s.get(DailyPinningTask, task.id)
                        db_task.notification_deleted_at = _utcnow()
                        s.commit()
                    log.info(f"✅ Задача #{task.id}: уведомление удалено")

                except Exception as e:
                    log.warning(f"⚠️ Ошибка удаления уведомления задачи #{task.id}: {e}")

    finally:
        # Закрываем клиентов
        for c in clients.values():
            try:
                await c.disconnect()
            except Exception:
                pass

async def run_daily_pinner():
    """Запуск цикла ежедневного закрепления"""
    log.info("🚀 Модуль ежедневного закрепления запущен")
    while True:
        try:
            await process_once()
        except Exception as e:
            log.error(f"❌ Ошибка в цикле закрепления: {e}")
        await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    asyncio.run(run_daily_pinner())