import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import pytz
from telethon import TelegramClient
from telethon.tl.types import (
    MessageActionPinMessage, 
    MessageService, 
    Message,
    PeerChannel,
    Channel
)
from telethon.tl.functions.messages import GetHistoryRequest
from telethon.tl.types import InputPeerChannel
from sqlalchemy import select

from utils.db_utils import get_session
from telegram_client import init_user_client
from entity_resolver import ensure_peer
from models import DailyPinningTask, BotSession, MainEntity

# Настройка логирования
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("notification_debug")

TZ = pytz.timezone(os.getenv("TZ", "Europe/Moscow"))

async def debug_notification_search(task_id: int):
    """Диагностический скрипт для поиска сервисных уведомлений"""
    
    log.info(f"🔍 Запускаем диагностику для задачи #{task_id}")
    
    with get_session() as s:
        task = s.get(DailyPinningTask, task_id)
        if not task:
            log.error(f"❌ Задача #{task_id} не найдена")
            return
            
        channel = s.get(MainEntity, task.channel_id)
        bot = s.get(BotSession, task.bot_id)
        
        if not channel or not bot:
            log.error(f"❌ Канал или бот не найдены для задачи #{task_id}")
            return

    # Подключаем клиент
    try:
        client = init_user_client(bot)
        await client.start()
        if not await client.is_user_authorized():
            raise RuntimeError(f"Бот #{bot.id} не авторизован")
        log.info(f"✅ Бот #{bot.id} авторизован")
    except Exception as e:
        log.error(f"❌ Ошибка инициализации бота: {e}")
        return

    try:
        # Получаем entity канала
        channel_entity = await ensure_peer(client, telegram_id=channel.telegram_id, link=channel.link)
        log.info(f"✅ Получен entity канала: {channel_entity}")
        
        # Получаем информацию о канале
        channel_full = await client.get_entity(channel_entity)
        log.info(f"📊 Информация о канале: {channel_full}")
        
        # Проверяем разные способы получения сообщений
        await _check_different_methods(client, channel_entity, task.pinned_message_id)
        
    except Exception as e:
        log.error(f"❌ Ошибка диагностики: {e}")
    finally:
        await client.disconnect()

async def _check_different_methods(client, channel_entity, pinned_message_id: int):
    """Проверяет разные способы поиска сервисных сообщений"""
    
    log.info("=" * 60)
    log.info("🔍 МЕТОД 1: iter_messages с limit=100")
    await _method_iter_messages_limit(client, channel_entity, pinned_message_id, limit=100)
    
    log.info("=" * 60)
    log.info("🔍 МЕТОД 2: iter_messages с limit=200")
    await _method_iter_messages_limit(client, channel_entity, pinned_message_id, limit=200)
    
    log.info("=" * 60)
    log.info("🔍 МЕТОД 3: GetHistoryRequest")
    await _method_get_history(client, channel_entity, pinned_message_id)
    
    log.info("=" * 60)
    log.info("🔍 МЕТОД 4: Поиск по ID сообщения")
    await _method_search_by_id(client, channel_entity, pinned_message_id)
    
    log.info("=" * 60)
    log.info("🔍 МЕТОД 5: Получение последних системных сообщений")
    await _method_recent_service_messages(client, channel_entity)
    
    log.info("=" * 60)
    log.info("🔍 МЕТОД 6: Получение всех типов сообщений")
    await _method_all_message_types(client, channel_entity)

async def _method_iter_messages_limit(client, channel_entity, pinned_message_id: int, limit: int):
    """Метод 1: iter_messages с указанным лимитом"""
    log.info(f"📝 Проверяем последние {limit} сообщений...")
    
    service_count = 0
    regular_count = 0
    pin_notifications = []
    
    try:
        async for message in client.iter_messages(channel_entity, limit=limit):
            # Анализируем тип сообщения
            message_type = "UNKNOWN"
            
            if isinstance(message, MessageService):
                message_type = "SERVICE"
                service_count += 1
                
                # Проверяем разные способы идентификации уведомления о закреплении
                checks = await _check_pin_notification_all_methods(message, pinned_message_id)
                
                if any(checks.values()):
                    pin_notifications.append({
                        'id': message.id,
                        'date': message.date,
                        'checks': checks,
                        'action': str(getattr(message, 'action', 'None')),
                        'text': getattr(message, 'text', '')[:100] if getattr(message, 'text', '') else ''
                    })
                    
            elif isinstance(message, Message):
                message_type = "REGULAR"
                regular_count += 1
                
            # Логируем информацию о сообщении
            log.info(f"  📨 ID: {message.id}, Type: {message_type}, Date: {message.date}")
            
            if hasattr(message, 'action'):
                log.info(f"     Action: {message.action}")
            if hasattr(message, 'text') and message.text:
                log.info(f"     Text: {message.text[:100]}...")
                
    except Exception as e:
        log.error(f"❌ Ошибка в методе 1: {e}")
    
    log.info(f"📊 Итоги метода 1: {service_count} сервисных, {regular_count} обычных сообщений")
    log.info(f"📌 Найдено уведомлений о закреплении: {len(pin_notifications)}")
    
    for notif in pin_notifications:
        log.info(f"  🔔 Уведомление ID {notif['id']}:")
        for check_name, check_result in notif['checks'].items():
            log.info(f"     {check_name}: {check_result}")

async def _method_get_history(client, channel_entity, pinned_message_id: int):
    """Метод 2: Использование GetHistoryRequest"""
    log.info("📝 Используем GetHistoryRequest...")
    
    try:
        # Получаем историю сообщений
        result = await client(GetHistoryRequest(
            peer=channel_entity,
            limit=100,
            offset_date=None,
            offset_id=0,
            max_id=0,
            min_id=0,
            add_offset=0,
            hash=0
        ))
        
        service_count = 0
        pin_notifications = []
        
        for message in result.messages:
            if isinstance(message, MessageService):
                service_count += 1
                
                checks = await _check_pin_notification_all_methods(message, pinned_message_id)
                if any(checks.values()):
                    pin_notifications.append({
                        'id': message.id,
                        'date': message.date,
                        'checks': checks
                    })
        
        log.info(f"📊 GetHistoryRequest: {service_count} сервисных сообщений")
        log.info(f"📌 Найдено уведомлений: {len(pin_notifications)}")
        
    except Exception as e:
        log.error(f"❌ Ошибка в методе 2: {e}")

async def _method_search_by_id(client, channel_entity, pinned_message_id: int):
    """Метод 3: Поиск по ID закрепленного сообщения"""
    log.info(f"📝 Ищем информацию о закрепленном сообщении {pinned_message_id}...")
    
    try:
        # Пытаемся получить закрепленное сообщение
        pinned_msg = await client.get_messages(channel_entity, ids=pinned_message_id)
        if pinned_msg:
            log.info(f"✅ Закрепленное сообщение найдено: ID {pinned_msg.id}, дата: {pinned_msg.date}")
            log.info(f"   Текст: {getattr(pinned_msg, 'text', '')[:100] if getattr(pinned_msg, 'text', '') else 'Нет текста'}")
        else:
            log.info("❌ Закрепленное сообщение не найдено")
            
    except Exception as e:
        log.error(f"❌ Ошибка поиска закрепленного сообщения: {e}")

async def _method_recent_service_messages(client, channel_entity):
    """Метод 4: Поиск только сервисных сообщений"""
    log.info("📝 Ищем только сервисные сообщения...")
    
    try:
        service_messages = []
        async for message in client.iter_messages(channel_entity, limit=50):
            if isinstance(message, MessageService):
                service_messages.append(message)
                
        log.info(f"📊 Найдено сервисных сообщений: {len(service_messages)}")
        
        for msg in service_messages[:10]:  # Показываем первые 10
            log.info(f"  🔔 Сервисное сообщение ID {msg.id}:")
            log.info(f"     Дата: {msg.date}")
            if hasattr(msg, 'action'):
                log.info(f"     Action тип: {type(msg.action).__name__}")
                log.info(f"     Action: {msg.action}")
            if hasattr(msg, 'text') and msg.text:
                log.info(f"     Текст: {msg.text[:100]}...")
                
    except Exception as e:
        log.error(f"❌ Ошибка в методе 4: {e}")

async def _method_all_message_types(client, channel_entity):
    """Метод 5: Анализ всех типов сообщений"""
    log.info("📝 Анализируем все типы сообщений...")
    
    try:
        type_count = {}
        
        async for message in client.iter_messages(channel_entity, limit=100):
            msg_type = type(message).__name__
            type_count[msg_type] = type_count.get(msg_type, 0) + 1
            
            # Детальный анализ сервисных сообщений
            if isinstance(message, MessageService):
                log.info(f"  🔍 Детальный анализ сервисного сообщения ID {message.id}:")
                
                # Все атрибуты сообщения
                for attr in dir(message):
                    if not attr.startswith('_'):
                        try:
                            value = getattr(message, attr)
                            if value and attr not in ['_client', 'client']:
                                log.info(f"     {attr}: {str(value)[:100]}")
                        except:
                            pass
                
                log.info("     ---")
                
    except Exception as e:
        log.error(f"❌ Ошибка в методе 5: {e}")
    
    log.info("📊 Статистика по типам сообщений:")
    for msg_type, count in type_count.items():
        log.info(f"  {msg_type}: {count}")

async def _check_pin_notification_all_methods(message, pinned_message_id: int) -> dict:
    """Проверяет все возможные способы идентификации уведомления о закреплении"""
    checks = {}
    
    try:
        # Проверка 1: Тип действия
        action = getattr(message, 'action', None)
        checks['is_message_action_pin'] = isinstance(action, MessageActionPinMessage)
        
        # Проверка 2: Название класса действия
        if action:
            checks['action_class_name'] = type(action).__name__
            checks['action_has_pin_in_name'] = 'Pin' in type(action).__name__
        else:
            checks['action_class_name'] = 'None'
            checks['action_has_pin_in_name'] = False
            
        # Проверка 3: Текст сообщения
        message_text = (getattr(message, 'text', '') or '').lower()
        checks['has_text'] = bool(message_text)
        
        # Ключевые слова в тексте
        pin_keywords = [
            'закрепил', 'pinned', 'закріпив', 'pin', 
            'закрепила', 'закріпила', 'зафиксировал', 'закрепило',
            'fixed', 'pinned a message', 'закріплено', 'закріпило',
            'pinned message', 'закрепил сообщение'
        ]
        
        found_keywords = []
        for keyword in pin_keywords:
            if keyword in message_text:
                found_keywords.append(keyword)
                
        checks['found_keywords'] = found_keywords
        checks['has_pin_keywords'] = len(found_keywords) > 0
        
        # Проверка 4: ID сообщения в действии
        if action and hasattr(action, 'message_id'):
            checks['action_message_id'] = action.message_id
            checks['action_message_id_matches'] = action.message_id == pinned_message_id
        else:
            checks['action_message_id'] = None
            checks['action_message_id_matches'] = False
            
        # Проверка 5: Другие атрибуты
        checks['is_service_message'] = isinstance(message, MessageService)
        checks['has_action'] = action is not None
        
    except Exception as e:
        checks['error'] = str(e)
        
    return checks

async def main():
    """Основная функция"""
    if len(os.sys.argv) != 2:
        print("Использование: python debug_notifications.py <task_id>")
        return
        
    try:
        task_id = int(os.sys.argv[1])
        await debug_notification_search(task_id)
    except ValueError:
        print("Ошибка: task_id должен быть числом")
    except Exception as e:
        log.error(f"❌ Ошибка выполнения: {e}")

if __name__ == "__main__":
    asyncio.run(main())
