# channel_sync.py

import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Set, Tuple
import pytz

from telethon import TelegramClient, functions
from telethon.tl.types import Message, MessageService, Channel, MessageMediaPhoto, MessageMediaDocument
from telethon.errors import FloodWaitError, ChatAdminRequiredError
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from utils.db_utils import get_session
from telegram_client import init_user_client
from entity_resolver import ensure_peer
from tg_copy import build_post, send_post, BuiltPost
from models import ChannelSyncTask, ChannelSyncHistory, ChannelSyncProgress, MainEntity, BotSession, BotProfile


# Настройка логирования
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                   format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("channel_sync")

# Константы
DEFAULT_CHECK_INTERVAL = int(os.getenv("CHANNEL_SYNC_CHECK_INTERVAL", "300"))  # 5 минут
TZ = pytz.timezone(os.getenv("TZ", "Europe/Moscow"))
UTC_TZ = pytz.UTC

def make_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """Преобразует наивный datetime в UTC-aware datetime"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return UTC_TZ.localize(dt)
    return dt

def make_naive(dt: Optional[datetime]) -> Optional[datetime]:
    """Преобразует aware datetime в наивный datetime (для совместимости с БД)"""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt

class ChannelSyncTracker:
    """Трекер для синхронизации каналов"""
    
    def __init__(self, task_id: int, client: TelegramClient):
        self.task_id = task_id
        self.client = client
        self.is_running = True
        self.current_task_data: Optional[ChannelSyncTask] = None
        self.source_entity = None
        self.target_entity = None
        self.is_syncing = False
       
    async def ensure_bot_in_channel(self, entity, entity_data):
        """Убеждается, что бот находится в канале, при необходимости добавляется или подаёт заявку"""
        try:
            # Получаем информацию о канале
            channel = await self.client.get_entity(entity)
            me = await self.client.get_me()
            
            # Пытаемся получить информацию о канале - если получится, значит бот имеет доступ
            try:
                # Простая проверка - пытаемся получить информацию о канале
                channel_full = await self.client(functions.channels.GetFullChannelRequest(channel))
                
                # Если дошли до этого места без ошибок - бот имеет доступ к каналу
                log.info(f"✅ Бот имеет доступ к каналу {entity_data.name}")
                return True
                
            except (ValueError, TypeError, ChatAdminRequiredError):
                # Бот не имеет доступа к каналу или произошла ошибка проверки
                log.warning(f"⚠️ Бот не имеет доступа к каналу {entity_data.name}, пытаемся присоединиться...")
                
                try:
                    # Пытаемся присоединиться к каналу
                    if hasattr(channel, 'username') and channel.username:
                        # Публичный канал - просто присоединяемся
                        await self.client(functions.channels.JoinChannelRequest(channel))
                        log.info(f"✅ Бот успешно присоединился к публичному каналу {entity_data.name}")
                    else:
                        # Приватный канал - пытаемся подать заявку
                        await self.client(functions.channels.JoinChannelRequest(channel))
                        log.info(f"✅ Заявка на вступление в приватный канал {entity_data.name} отправлена")
                        
                    # Даем время для обработки присоединения
                    await asyncio.sleep(3)
                    
                    # Проверяем, что бот теперь имеет доступ
                    try:
                        await self.client(functions.channels.GetFullChannelRequest(channel))
                        log.info(f"✅ Бот успешно получил доступ к каналу {entity_data.name}")
                        return True
                    except Exception:
                        log.warning(f"⚠️ Бот все еще не имеет доступа к каналу {entity_data.name} после попытки присоединения")
                        return False
                        
                except FloodWaitError as e:
                    log.warning(f"⏳ Ожидание {e.seconds} секунд перед присоединением к каналу {entity_data.name}")
                    await asyncio.sleep(e.seconds)
                    return await self.ensure_bot_in_channel(entity, entity_data)
                except Exception as join_error:
                    log.error(f"❌ Не удалось присоединиться к каналу {entity_data.name}: {join_error}")
                    return False
                    
        except Exception as e:
            log.error(f"❌ Ошибка проверки/присоединения к каналу {entity_data.name}: {e}")
            return False

    def _load_task_data_from_db(self) -> Optional[ChannelSyncTask]:
        """Загружает актуальные данные задачи из БД"""
        try:
            with get_session() as session:
                task = session.execute(
                    select(ChannelSyncTask)
                    .options(
                        joinedload(ChannelSyncTask.source),
                        joinedload(ChannelSyncTask.target),
                        joinedload(ChannelSyncTask.progress)
                    )
                    .where(ChannelSyncTask.id == self.task_id)
                ).unique().scalar_one_or_none()
                
                if task:
                    log.info(f"✅ Загружена задача синхронизации #{self.task_id}: "
                            f"{task.source.name} → {task.target.name}")
                    return task
                else:
                    log.error(f"❌ Задача синхронизации #{self.task_id} не найдена в БД")
                    return None
                    
        except Exception as e:
            log.error(f"❌ Ошибка загрузки задачи синхронизации #{self.task_id}: {e}")
            return None

    async def load_task_data(self):
        """Загружает данные задачи и инициализирует entities"""
        self.current_task_data = self._load_task_data_from_db()
        
        if not self.current_task_data:
            return False
            
        try:
            # Инициализируем source entity
            self.source_entity = await ensure_peer(
                self.client,
                telegram_id=self.current_task_data.source.telegram_id,
                link=self.current_task_data.source.link
            )
            
            # Инициализируем target entity
            self.target_entity = await ensure_peer(
                self.client,
                telegram_id=self.current_task_data.target.telegram_id,
                link=self.current_task_data.target.link
            )
            
            # Убеждаемся, что бот находится в исходном канале
            await self.ensure_bot_in_channel(self.source_entity, self.current_task_data.source)
            
            # Убеждаемся, что бот находится в целевом канале
            await self.ensure_bot_in_channel(self.target_entity, self.current_task_data.target)
            
            log.info(f"✅ Инициализированы entities для задачи #{self.task_id}")
            return True
            
        except Exception as e:
            log.error(f"❌ Ошибка инициализации entities для задачи #{self.task_id}: {e}")
            return False

    async def get_channel_subscribers_count(self, entity) -> int:
        """Получает количество подписчиков канала"""
        try:
            channel = await self.client.get_entity(entity)
            subscribers_count = 0

            try:
                full = await self.client(functions.channels.GetFullChannelRequest(channel))
                if full.full_chat.participants_count:
                    subscribers_count = full.full_chat.participants_count
            except Exception:
                try:
                    full = await self.client(functions.messages.GetFullChatRequest(channel.id))
                    if full.full_chat.participants_count:
                        subscribers_count = full.full_chat.participants_count
                except Exception as inner_e:
                    log.warning(f"⚠️ Не удалось получить количество подписчиков: {inner_e}")

            return int(subscribers_count) if subscribers_count else 0
                
        except Exception as e:
            log.error(f"❌ Ошибка получения количества подписчиков: {e}")
            return 0

    def _is_media_message(self, message: Message) -> bool:
        """Проверяет, является ли сообщение медиа-сообщением"""
        return bool(message.media and not isinstance(message.media, MessageService))

    def _get_media_group_id(self, message: Message) -> Optional[str]:
        """Получает ID медиа-группы, если сообщение является частью альбома"""
        if hasattr(message, 'grouped_id') and message.grouped_id:
            return str(message.grouped_id)
        return None

    async def get_channel_posts_count(self, entity) -> Tuple[int, int]:
        """
        Получает количество постов в канале.
        Возвращает кортеж: (количество постов, количество сообщений)
        """
        try:
            messages = []
            log.info(f"🔍 Подсчет постов в канале {entity}")
            
            # Получаем ВСЕ сообщения
            async for message in self.client.iter_messages(
                entity,
                limit=None,  # Все сообщения
                # Без reverse - получаем от новых к старым, потом отсортируем
            ):
                # Пропускаем служебные сообщения
                if isinstance(message, MessageService):
                    continue
                    
                # Пропускаем сообщения без контента
                if not message.message and not message.media:
                    continue
                    
                # Пропускаем удаленные сообщения
                if getattr(message, 'action', None):
                    continue
                    
                messages.append(message)
            
            if not messages:
                log.info(f"📭 Канал {entity} пуст")
                return 0, 0
            
            # Сортируем по возрастанию ID (от старых к новым)
            messages.sort(key=lambda x: x.id)
            
            # Группируем сообщения по постам (альбомам)
            posts_count = 0
            processed_groups = set()
            
            for message in messages:
                group_id = self._get_media_group_id(message)
                
                if group_id:
                    if group_id not in processed_groups:
                        posts_count += 1
                        processed_groups.add(group_id)
                else:
                    posts_count += 1
            
            messages_count = len(messages)
            log.info(f"📊 Канал {entity}: {posts_count} постов, {messages_count} сообщений")
            return posts_count, messages_count
            
        except Exception as e:
            log.error(f"❌ Ошибка подсчета постов в канале: {e}")
            return 0, 0

    async def get_channel_messages_grouped(self, entity, limit: int = None, offset_id: int = 0) -> List[List[Message]]:
        """Получает сообщения из канала, группируя их по альбомам"""
        try:
            messages = []
            log.info(f"🔍 Начинаем получение всех сообщений из канала {entity}")
            
            # Получаем ВСЕ сообщения от новых к старым
            async for message in self.client.iter_messages(
                entity,
                limit=limit,  # None = все сообщения
                # Не используем offset_id для получения всех
            ):
                # Пропускаем служебные сообщения
                if isinstance(message, MessageService):
                    continue
                    
                # Пропускаем сообщения без контента
                if not message.message and not message.media:
                    continue
                    
                # Пропускаем удаленные сообщения
                if getattr(message, 'action', None):
                    continue
                    
                messages.append(message)
                
                # Логируем прогресс каждые 50 сообщений
                if len(messages) % 50 == 0:
                    log.info(f"📥 Получено {len(messages)} сообщений...")
            
            if not messages:
                log.warning(f"⚠️ В канале {entity} не найдено сообщений для синхронизации")
                return []
            
            log.info(f"📨 Всего получено {len(messages)} сообщений из канала {entity}")
            
            # ВАЖНО: Сортируем по возрастанию ID (от старых к новым)
            # Это нужно для правильного порядка копирования
            messages.sort(key=lambda x: x.id)
            log.info(f"📊 ID первого сообщения: {messages[0].id}, ID последнего: {messages[-1].id}")
            
            # Группируем сообщения по альбомам
            grouped_messages = []
            media_groups: Dict[str, List[Message]] = {}
            standalone_messages = []
            
            for message in messages:
                group_id = self._get_media_group_id(message)
                
                if group_id:
                    # Это часть медиа-группы
                    if group_id not in media_groups:
                        media_groups[group_id] = []
                    media_groups[group_id].append(message)
                else:
                    # Одиночное сообщение
                    standalone_messages.append(message)
            
            # Добавляем медиа-группы (сортируем сообщения внутри группы по ID)
            for group_id, group_messages in media_groups.items():
                group_messages.sort(key=lambda x: x.id)  # Сортируем по возрастанию ID внутри группы
                grouped_messages.append(group_messages)
                log.debug(f"📦 Медиа-группа {group_id}: {len(group_messages)} сообщений")
            
            # Добавляем одиночные сообщения
            for message in standalone_messages:
                grouped_messages.append([message])
            
            # Сортируем все группы по ID первого сообщения (от старых к новым)
            grouped_messages.sort(key=lambda x: x[0].id)
            
            log.info(f"📦 Сгруппировано {len(grouped_messages)} постов "
                    f"(включая {len(media_groups)} альбомов и {len(standalone_messages)} одиночных сообщений)")
            
            return grouped_messages
            
        except Exception as e:
            log.error(f"❌ Ошибка получения сообщений из канала {entity}: {e}")
            import traceback
            log.error(f"❌ Детали ошибки: {traceback.format_exc()}")
            return []

    async def sync_full_channel(self):
        """Полная синхронизация канала с правильным порядком и группировкой"""
        try:
            log.info(f"🔄 Задача #{self.task_id}: полная синхронизация канала")
            
            # Получаем количество постов в целевом канале ДО синхронизации
            posts_before, _ = await self.get_channel_posts_count(self.target_entity)
            
            # Получаем все сообщения из источника (с группировкой)
            log.info(f"🔍 Получаем ВСЕ сообщения из источника...")
            source_message_groups = await self.get_channel_messages_grouped(self.source_entity, limit=None)
            
            if not source_message_groups:
                log.warning(f"⚠️ В источнике нет сообщений для синхронизации")
                return
            
            log.info(f"📨 Найдено {len(source_message_groups)} постов в источнике")
            
            # Получаем текущие сообщения в цели
            target_message_ids = await self.get_target_message_ids()
            
            # Получаем количество подписчиков источника
            source_subscribers = await self.get_channel_subscribers_count(self.source_entity)
            
            # Создаем множество ID сообщений источника для быстрого поиска
            source_message_ids = set()
            for group in source_message_groups:
                for message in group:
                    source_message_ids.add(message.id)
            
            # Удаляем сообщения из цели, которых нет в источнике
            messages_to_delete = target_message_ids - source_message_ids
            if messages_to_delete:
                log.info(f"🗑️ Удаление {len(messages_to_delete)} сообщений из целевого канала")
                
                for message_id in messages_to_delete:
                    if not self.is_running:
                        break
                    await self.delete_message_from_target(message_id)
                    await asyncio.sleep(0.5)  # Задержка между удалениями
            
            # Копируем посты, которых нет в цели
            posts_to_copy = []
            for message_group in source_message_groups:
                # Проверяем, есть ли уже хотя бы одно сообщение из этой группы в цели
                group_exists = any(msg.id in target_message_ids for msg in message_group)
                if not group_exists:
                    posts_to_copy.append(message_group)
            
            if not posts_to_copy:
                log.info(f"✅ Все посты уже синхронизированы")
                # Сохраняем историю с текущими данными
                await self.save_history(posts_before, posts_before, source_subscribers, new_posts_count=0)
                return
            
            log.info(f"📨 Копирование {len(posts_to_copy)} отсутствующих постов")
            
            total = len(posts_to_copy)
            copied = 0
            last_copied_id = None
            last_post_url = None
            
            # Копируем посты в правильном порядке (от старых к новым)
            for i, message_group in enumerate(posts_to_copy, 1):
                if not self.is_running:
                    break
                    
                log.info(f"📤 Копирование поста {i}/{total} (ID: {message_group[0].id})")
                success, post_url = await self.copy_message_group_to_target(message_group)
                if success:
                    copied += 1
                    last_copied_id = message_group[0].id
                    last_post_url = post_url
                    
                    # Обновляем прогресс каждые 5 постов или в конце
                    if i % 5 == 0 or i == total:
                        await self.update_progress(total, copied, last_copied_id)
                    
                    # Небольшая задержка между постами
                    await asyncio.sleep(2)
                else:
                    log.error(f"❌ Ошибка копирования поста {i}/{total} (ID: {message_group[0].id})")
            
            # Получаем количество постов в целевом канале ПОСЛЕ синхронизации
            posts_after, _ = await self.get_channel_posts_count(self.target_entity)
            
            # Сохраняем историю с ОБЩИМ количеством постов и количеством новых
            await self.save_history(posts_before, posts_after, source_subscribers, new_posts_count=copied, last_post_url=last_post_url)
            
            # Обновляем прогресс как завершенный
            await self.update_progress(total, copied, last_copied_id, is_completed=True)
            
            log.info(f"✅ Полная синхронизация завершена: скопировано {copied}/{total} постов, "
                    f"удалено {len(messages_to_delete)} сообщений")
            
        except Exception as e:
            log.error(f"❌ Ошибка полной синхронизации канала: {e}")
            import traceback
            log.error(f"❌ Детали ошибки: {traceback.format_exc()}")

    async def get_target_message_ids(self) -> Set[int]:
        """Получает ID всех сообщений в целевом канале"""
        try:
            message_ids = set()
            log.info(f"🔍 Получение ID всех сообщений из целевого канала...")
            
            async for message in self.client.iter_messages(self.target_entity, limit=None):
                # Только реальные сообщения (не служебные)
                if not isinstance(message, MessageService):
                    message_ids.add(message.id)
            
            log.info(f"📊 Найдено {len(message_ids)} сообщений в целевом канале")
            return message_ids
            
        except Exception as e:
            log.error(f"❌ Ошибка получения сообщений из целевого канала: {e}")
            import traceback
            log.error(f"❌ Детали ошибки: {traceback.format_exc()}")
            return set()

    async def copy_message_group_to_target(self, message_group: List[Message]) -> Tuple[bool, Optional[str]]:
        """Копирует группу сообщений (альбом) в целевой канал и возвращает ссылку на последний пост"""
        max_attempts = 3
        base_delay = 2  # базовая задержка в секундах
        
        for attempt in range(1, max_attempts + 1):
            try:
                # Создаем BuiltPost из группы сообщений
                built_post = BuiltPost(messages=message_group)
                
                # Отправляем пост в целевой канал
                sent_ids = await send_post(
                    self.client,
                    built_post,
                    self.target_entity,
                    text_suffix="",  # Без суффикса
                    is_add_suffix=False
                )
                
                if sent_ids:
                    last_message_id = sent_ids[-1]
                    last_post_url = await self.get_message_link(last_message_id)
                    
                    if len(message_group) > 1:
                        log.debug(f"✅ Скопирован альбом {message_group[0].id} ({len(message_group)} медиа) → {len(sent_ids)} сообщений")
                    else:
                        log.debug(f"✅ Скопировано сообщение {message_group[0].id} → {sent_ids[0]}")
                    return True, last_post_url
                else:
                    if len(message_group) > 1:
                        log.warning(f"⚠️ Попытка {attempt}/{max_attempts}: не удалось скопировать альбом {message_group[0].id}")
                    else:
                        log.warning(f"⚠️ Попытка {attempt}/{max_attempts}: не удалось скопировать сообщение {message_group[0].id}")
                    
                    # Если это не последняя попытка, ждем перед повторной попыткой
                    if attempt < max_attempts:
                        delay = base_delay * attempt  # Увеличиваем задержку с каждой попыткой
                        log.info(f"⏳ Повторная попытка через {delay} секунд...")
                        await asyncio.sleep(delay)
                    else:
                        if len(message_group) > 1:
                            log.error(f"❌ Все {max_attempts} попыток скопировать альбом {message_group[0].id} завершились неудачей")
                        else:
                            log.error(f"❌ Все {max_attempts} попыток скопировать сообщение {message_group[0].id} завершились неудачей")
                        return False, None
                    
            except FloodWaitError as e:
                log.warning(f"⏳ Flood wait {e.seconds} секунд для сообщения {message_group[0].id}")
                await asyncio.sleep(e.seconds)
                # Flood wait не считается за попытку - продолжаем с той же попытки
                continue
            except Exception as e:
                if len(message_group) > 1:
                    log.error(f"❌ Попытка {attempt}/{max_attempts}: ошибка копирования альбома {message_group[0].id}: {e}")
                else:
                    log.error(f"❌ Попытка {attempt}/{max_attempts}: ошибка копирования сообщения {message_group[0].id}: {e}")
                
                # Если это не последняя попытка, ждем перед повторной попыткой
                if attempt < max_attempts:
                    delay = base_delay * attempt  # Увеличиваем задержку с каждой попыткой
                    log.info(f"⏳ Повторная попытка через {delay} секунд...")
                    await asyncio.sleep(delay)
                else:
                    if len(message_group) > 1:
                        log.error(f"❌ Все {max_attempts} попыток скопировать альбом {message_group[0].id} завершились неудачей")
                    else:
                        log.error(f"❌ Все {max_attempts} попыток скопировать сообщение {message_group[0].id} завершились неудачей")
                    return False, None
        
        return False, None

    async def get_message_link(self, message_id: int) -> str:
        """Генерирует ссылку на сообщение"""
        try:
            # Получаем сущность канала для получения username
            channel = await self.client.get_entity(self.target_entity)
            channel_username = getattr(channel, 'username', None)
            
            if channel_username:
                return f"https://t.me/{channel_username}/{message_id}"
            else:
                # Для каналов без username используем ID
                channel_id = getattr(channel, 'id', None)
                if channel_id:
                    return f"https://t.me/c/{abs(channel_id)}/{message_id}"
                else:
                    return f"https://t.me/c/unknown/{message_id}"
                    
        except Exception as e:
            log.error(f"❌ Ошибка генерации ссылки на сообщение {message_id}: {e}")
            return ""

    async def delete_message_from_target(self, message_id: int) -> bool:
        """Удаляет сообщение из целевого канала с повторными попытками"""
        max_attempts = 3
        base_delay = 1  # базовая задержка в секундах
        
        for attempt in range(1, max_attempts + 1):
            try:
                await self.client.delete_messages(self.target_entity, [message_id])
                log.debug(f"🗑️ Удалено сообщение {message_id} из целевого канала")
                return True
            except FloodWaitError as e:
                log.warning(f"⏳ Flood wait {e.seconds} секунд для удаления сообщения {message_id}")
                await asyncio.sleep(e.seconds)
                # Flood wait не считается за попытку - продолжаем с той же попытки
                continue
            except ChatAdminRequiredError:
                log.error(f"🚫 Нет прав на удаление сообщений в целевом канале")
                return False
            except Exception as e:
                log.warning(f"⚠️ Попытка {attempt}/{max_attempts}: ошибка удаления сообщения {message_id}: {e}")
                
                # Если это не последняя попытка, ждем перед повторной попыткой
                if attempt < max_attempts:
                    delay = base_delay * attempt
                    log.info(f"⏳ Повторная попытка удаления через {delay} секунд...")
                    await asyncio.sleep(delay)
                else:
                    log.error(f"❌ Все {max_attempts} попыток удалить сообщение {message_id} завершились неудачей")
                    return False
        
        return False

    async def update_progress(self, total: int, copied: int, last_message_id: int = None, is_completed: bool = False):
        """Обновляет прогресс синхронизации"""
        try:
            with get_session() as session:
                progress = session.execute(
                    select(ChannelSyncProgress)
                    .where(ChannelSyncProgress.task_id == self.task_id)
                ).scalar_one_or_none()
                
                if not progress:
                    progress = ChannelSyncProgress(task_id=self.task_id)
                    session.add(progress)
                
                progress.total_posts_to_copy = total
                progress.copied_posts = copied
                if last_message_id:
                    progress.last_copied_message_id = last_message_id
                
                if is_completed and not progress.is_completed:
                    progress.is_completed = True
                    progress.completed_at = datetime.utcnow()
                elif not is_completed and progress.is_completed:
                    progress.is_completed = False
                    progress.completed_at = None
                    progress.started_at = datetime.utcnow()
                
                session.commit()
                log.debug(f"📊 Обновлен прогресс: {copied}/{total} постов")
                
        except Exception as e:
            log.error(f"❌ Ошибка обновления прогресса: {e}")

    async def save_history(self, posts_before: int, posts_after: int, source_subscribers: int, new_posts_count: int = 0, last_post_url: str = None):
        """Сохраняет запись в историю синхронизации"""
        try:
            with get_session() as session:
                sync_date = datetime.now()

                history = ChannelSyncHistory(
                    task_id=self.task_id,
                    posts_before=posts_before,
                    posts_after=posts_after,
                    source_subscribers_count=source_subscribers,
                    sync_date=sync_date,
                    last_post_url=last_post_url
                )
                session.add(history)
                
                # Обновляем подписчиков в основной задаче
                task = session.get(ChannelSyncTask, self.task_id)
                if task:
                    task.source_subscribers_count = source_subscribers
                    task.last_sync_date = datetime.utcnow()
                
                session.commit()
                log.info(f"💾 Сохранена история синхронизации: {posts_before} → {posts_after} постов "
                        f"(новых: {new_posts_count}), {source_subscribers} подписчиков, последний пост: {last_post_url}")
                
        except Exception as e:
            log.error(f"❌ Ошибка сохранения истории: {e}")

    async def sync_new_posts_only(self):
        """Синхронизирует только новые посты с правильным порядком и группировкой"""
        try:
            log.info(f"🔄 Задача #{self.task_id}: синхронизация только новых постов")
            
            # Получаем прогресс из БД
            with get_session() as session:
                progress = session.execute(
                    select(ChannelSyncProgress)
                    .where(ChannelSyncProgress.task_id == self.task_id)
                ).scalar_one_or_none()
            
            last_message_id = progress.last_copied_message_id if progress else None
            
            # Получаем количество постов в целевом канале ДО синхронизации
            posts_before, _ = await self.get_channel_posts_count(self.target_entity)
            
            # Получаем ВСЕ сообщения из источника (с группировкой)
            source_message_groups = await self.get_channel_messages_grouped(
                self.source_entity, 
                limit=None
            )
            
            if not source_message_groups:
                log.info(f"✅ Нет сообщений в источнике для синхронизации")
                return
            
            # Фильтруем группы сообщений, которые новее последнего скопированного
            if last_message_id:
                # Находим индекс последнего скопированного сообщения
                last_group_index = None
                for i, group in enumerate(source_message_groups):
                    if any(msg.id == last_message_id for msg in group):
                        last_group_index = i
                        break
                
                if last_group_index is not None:
                    # Берем только посты после последнего скопированного
                    source_message_groups = source_message_groups[last_group_index + 1:]
                else:
                    # Если последний скопированный пост не найден, копируем все
                    log.warning(f"⚠️ Последний скопированный пост {last_message_id} не найден, копируем все посты")
            
            if not source_message_groups:
                log.info(f"✅ Нет новых постов для синхронизации")
                return
            
            log.info(f"📨 Найдено {len(source_message_groups)} новых постов для синхронизации")
            
            # Получаем количество подписчиков источника
            source_subscribers = await self.get_channel_subscribers_count(self.source_entity)
            
            # Копируем новые группы сообщений
            total = len(source_message_groups)
            copied = 0
            last_copied_id = last_message_id
            last_post_url = None
            
            for i, message_group in enumerate(source_message_groups, 1):
                if not self.is_running:
                    break
                    
                success, post_url = await self.copy_message_group_to_target(message_group)
                if success:
                    copied += 1
                    last_copied_id = message_group[0].id  # ID первого сообщения в группе
                    last_post_url = post_url
                    
                    # Обновляем прогресс каждые 5 постов или в конце
                    if i % 5 == 0 or i == total:
                        await self.update_progress(total, copied, last_copied_id)
                    
                    # Небольшая задержка между постами
                    await asyncio.sleep(2)
            
            # Получаем количество постов в целевом канале ПОСЛЕ синхронизации
            posts_after, _ = await self.get_channel_posts_count(self.target_entity)
            
            # Сохраняем историю с ОБЩИМ количеством постов и количеством новых
            await self.save_history(posts_before, posts_after, source_subscribers, new_posts_count=copied, last_post_url=last_post_url)
            
            # Обновляем прогресс как завершенный
            await self.update_progress(total, copied, last_copied_id, is_completed=True)
            
            log.info(f"✅ Синхронизация новых постов завершена: скопировано {copied}/{total} постов")
            
        except Exception as e:
            log.error(f"❌ Ошибка синхронизации новых постов: {e}")

    async def sync_full_channel(self):
        """Полная синхронизация канала с правильным порядком и группировкой"""
        try:
            log.info(f"🔄 Задача #{self.task_id}: полная синхронизация канала")
            
            # Получаем количество постов в целевом канале ДО синхронизации
            posts_before, _ = await self.get_channel_posts_count(self.target_entity)
            
            # Получаем все сообщения из источника (с группировкой)
            source_message_groups = await self.get_channel_messages_grouped(self.source_entity, limit=None)
            
            if not source_message_groups:
                log.warning(f"⚠️ В источнике нет сообщений для синхронизации")
                return
            
            log.info(f"📨 Найдено {len(source_message_groups)} постов в источнике")
            
            # Получаем текущие сообщения в цели
            target_message_ids = await self.get_target_message_ids()
            
            # Получаем количество подписчиков источника
            source_subscribers = await self.get_channel_subscribers_count(self.source_entity)
            
            # Создаем множество ID сообщений источника для быстрого поиска
            source_message_ids = set()
            for group in source_message_groups:
                for message in group:
                    source_message_ids.add(message.id)
            
            # Удаляем сообщения из цели, которых нет в источнике
            messages_to_delete = target_message_ids - source_message_ids
            if messages_to_delete:
                log.info(f"🗑️ Удаление {len(messages_to_delete)} сообщений из целевого канала")
                
                for message_id in messages_to_delete:
                    if not self.is_running:
                        break
                    await self.delete_message_from_target(message_id)
                    await asyncio.sleep(0.5)  # Задержка между удалениями
            
            # Копируем посты, которых нет в цели
            posts_to_copy = []
            for message_group in source_message_groups:
                # Проверяем, есть ли уже хотя бы одно сообщение из этой группы в цели
                group_exists = any(msg.id in target_message_ids for msg in message_group)
                if not group_exists:
                    posts_to_copy.append(message_group)
            
            if not posts_to_copy:
                log.info(f"✅ Все посты уже синхронизированы")
                # Сохраняем историю с текущими данными
                await self.save_history(posts_before, posts_before, source_subscribers, new_posts_count=0)
                return
            
            log.info(f"📨 Копирование {len(posts_to_copy)} отсутствующих постов")
            
            total = len(posts_to_copy)
            copied = 0
            last_copied_id = None
            last_post_url = None
            
            for i, message_group in enumerate(posts_to_copy, 1):
                if not self.is_running:
                    break
                    
                success, post_url = await self.copy_message_group_to_target(message_group)
                if success:
                    copied += 1
                    last_copied_id = message_group[0].id
                    last_post_url = post_url
                    
                    # Обновляем прогресс каждые 5 постов или в конце
                    if i % 5 == 0 or i == total:
                        await self.update_progress(total, copied, last_copied_id)
                    
                    # Небольшая задержка между постами
                    await asyncio.sleep(2)
            
            # Получаем количество постов в целевом канале ПОСЛЕ синхронизации
            posts_after, _ = await self.get_channel_posts_count(self.target_entity)
            
            # Сохраняем историю с ОБЩИМ количеством постов и количеством новых
            await self.save_history(posts_before, posts_after, source_subscribers, new_posts_count=copied, last_post_url=last_post_url)
            
            # Обновляем прогресс как завершенный
            await self.update_progress(total, copied, last_copied_id, is_completed=True)
            
            log.info(f"✅ Полная синхронизация завершена: скопировано {copied}/{total} постов, "
                    f"удалено {len(messages_to_delete)} сообщений")
            
        except Exception as e:
            log.error(f"❌ Ошибка полной синхронизации канала: {e}")

    def _is_first_day_of_period(self, current_date: datetime, period_days: int) -> bool:
        """Проверяет, является ли текущая дата первым днем периода"""
        if period_days == 7:  # Неделя
            return current_date.weekday() == 0  # Понедельник
        elif period_days == 14:  # Две недели
            # Первый понедельник периода (проверяем, что это понедельник и номер недели четный)
            return current_date.weekday() == 0 and (current_date.isocalendar()[1] % 2 == 1)
        elif period_days == 30:  # Месяц
            return current_date.day == 1
        return False

    async def check_and_sync(self):
        """Проверяет необходимость синхронизации и выполняет её"""
        if not self.is_running or self.is_syncing:
            return
        
        try:
            self.is_syncing = True
            
            # ВСЕГДА загружаем свежие данные из БД перед проверкой
            current_data = self._load_task_data_from_db()
            if not current_data:
                return
            
            self.current_task_data = current_data
            
            # Проверяем активность задачи
            if not await self.check_task_active():
                log.info(f"🛑 Задача #{self.task_id} деактивирована")
                self.stop()
                return
            
            # Проверяем флаг немедленной синхронизации
            if self.current_task_data.run_once_task:
                log.info(f"🚀 Немедленная синхронизация для задачи #{self.task_id}")
                await self.sync_new_posts_only()
                
                # Сбрасываем флаг
                with get_session() as session:
                    task = session.get(ChannelSyncTask, self.task_id)
                    if task and task.run_once_task:
                        task.run_once_task = False
                        session.commit()
                        log.info(f"✅ Сброшен флаг run_once_task для задачи #{self.task_id}")
                return
            
            # Проверяем периодическую синхронизацию
            if not self.current_task_data.update_period_days:
                log.debug(f"⏭️ Периодическая синхронизация отключена для задачи #{self.task_id}")
                return
            
            # Проверяем, является ли сегодня первый день периода и подходит ли время
            current_time = datetime.now()
            scheduled_time = self.current_task_data.scheduled_time
            
            if not self._is_first_day_of_period(current_time, self.current_task_data.update_period_days):
                log.debug(f"⏭️ Сегодня не первый день периода для задачи #{self.task_id}")
                return
            
            # Проверяем время запуска
            current_time_only = current_time.time()
            if scheduled_time and current_time_only < scheduled_time:
                log.debug(f"⏭️ Время запуска для задачи #{self.task_id} еще не наступило: {scheduled_time}")
                return
            
            # Проверяем, не запускалась ли уже сегодня синхронизация
            last_sync = self.current_task_data.last_sync_date
            if last_sync:
                last_sync_date = last_sync.date()
                if last_sync_date == current_time.date():
                    log.debug(f"⏭️ Синхронизация для задачи #{self.task_id} уже выполнялась сегодня")
                    return
            
            log.info(f"🔄 Запуск периодической синхронизации для задачи #{self.task_id} "
                    f"(период: {self.current_task_data.update_period_days} дней, время: {scheduled_time})")
            
            # Выбираем тип синхронизации
            if self.current_task_data.update_range == "new_only":
                await self.sync_new_posts_only()
            else:
                await self.sync_full_channel()
                
        except Exception as e:
            log.error(f"❌ Ошибка проверки синхронизации для задачи #{self.task_id}: {e}")
        finally:
            self.is_syncing = False

    async def check_task_active(self) -> bool:
        """Быстрая проверка активности задачи в БД"""
        try:
            with get_session() as session:
                task_active = session.execute(
                    select(ChannelSyncTask.is_active)
                    .where(ChannelSyncTask.id == self.task_id)
                ).scalar_one_or_none()
                
                return task_active if task_active is not None else False
                
        except Exception as e:
            log.error(f"❌ Ошибка проверки активности задачи #{self.task_id}: {e}")
            return True  # Продолжаем работу при ошибке

    def stop(self):
        """Останавливает трекер"""
        self.is_running = False


class ChannelSyncManager:
    """Менеджер для управления всеми задачами синхронизации каналов"""
    
    def __init__(self):
        self.trackers: Dict[int, ChannelSyncTracker] = {}
        self.clients: Dict[int, TelegramClient] = {}
        self.periodic_tasks: Dict[int, asyncio.Task] = {}
        
    async def _load_tasks(self):
        """Загружает активные задачи из БД и настраивает трекеры"""
        with get_session() as session:
            tasks_result = session.execute(
                select(ChannelSyncTask)
                .options(
                    joinedload(ChannelSyncTask.source),
                    joinedload(ChannelSyncTask.target),
                    joinedload(ChannelSyncTask.bot)
                )
                .where(ChannelSyncTask.is_active == True)
            ).unique().scalars().all()
        
        if not tasks_result:
            log.info("🔍 Нет активных задач синхронизации каналов")
            return
            
        log.info(f"🔍 Загружено {len(tasks_result)} активных задач синхронизации")
        
        bot_ids = sorted(set(t.bot_id for t in tasks_result))
        
        with get_session() as session:
            bots = {
                b.id: b
                for b in session.execute(
                    select(BotSession)
                    .join(BotProfile, BotProfile.bot_id == BotSession.id)
                    .where(
                        BotSession.id.in_(bot_ids),
                        BotProfile.telegram_status == "premium"
                    )
                ).scalars().all()
            }
        
        # Инициализация клиентов
        for bot_id in bot_ids:
            if bot_id not in bots:
                log.warning(
                    f"⛔ Бот #{bot_id} пропущен — аккаунт не Premium"
                )
                continue
            if bot_id not in self.clients:
                try:
                    client = init_user_client(bots[bot_id])
                    await client.start()
                    if not await client.is_user_authorized():
                        raise RuntimeError(f"Бот #{bot_id} не авторизован")
                    self.clients[bot_id] = client
                    log.info(f"✅ Бот #{bot_id} авторизован для синхронизации каналов")
                except Exception as e:
                    log.error(f"❌ Ошибка инициализации бота #{bot_id}: {e}")
        
        # Создание и настройка трекеров
        for task in tasks_result:
            client = self.clients.get(task.bot_id)
            if client and task.id not in self.trackers:
                tracker = ChannelSyncTracker(task.id, client)
                if await tracker.load_task_data():
                    self.trackers[task.id] = tracker
                    self._start_periodic_check(task.id, tracker)
                    log.info(f"✅ Трекер синхронизации создан для задачи #{task.id}")
                else:
                    log.error(f"❌ Не удалось загрузить данные для задачи #{task.id}")

    def _start_periodic_check(self, task_id: int, tracker: ChannelSyncTracker):
        """Запускает периодическую проверку для трекера"""
        async def periodic_check():
            while tracker.is_running:
                try:
                    await tracker.check_and_sync()
                    # Проверяем каждые 10 секунд для быстрой реакции
                    await asyncio.sleep(10)
                except Exception as e:
                    log.error(f"❌ Ошибка в периодической проверке задачи #{task_id}: {e}")
                    await asyncio.sleep(30)
        
        task = asyncio.create_task(periodic_check())
        self.periodic_tasks[task_id] = task

    async def check_for_updates(self):
        """Проверяет обновления в БД и обновляет трекеры"""
        try:
            with get_session() as session:
                active_tasks = session.execute(
                    select(ChannelSyncTask)
                    .where(ChannelSyncTask.is_active == True)
                ).scalars().all()
                
                active_task_ids = {t.id for t in active_tasks}
                current_tracker_ids = set(self.trackers.keys())
                
                # Удаляем неактивные трекеры
                for task_id in current_tracker_ids - active_task_ids:
                    if task_id in self.trackers:
                        self.trackers[task_id].stop()
                        if task_id in self.periodic_tasks:
                            self.periodic_tasks[task_id].cancel()
                            del self.periodic_tasks[task_id]
                        del self.trackers[task_id]
                        log.info(f"🗑️ Удален трекер синхронизации для задачи #{task_id}")
                
                # Добавляем новые трекеры
                for task in active_tasks:
                    if task.id not in self.trackers:
                        client = self.clients.get(task.bot_id)
                        if client:
                            tracker = ChannelSyncTracker(task.id, client)
                            if await tracker.load_task_data():
                                self.trackers[task.id] = tracker
                                self._start_periodic_check(task.id, tracker)
                                log.info(f"✅ Добавлен трекер синхронизации для задачи #{task.id}")
                
                # Принудительно обновляем данные во всех трекерах для задач с run_once_task
                for task in active_tasks:
                    tracker = self.trackers.get(task.id)
                    if tracker and task.run_once_task:
                        log.info(f"🎯 Обнаружена задача #{task.id} с run_once_task - принудительное обновление данных")
                        # Принудительно обновляем данные в трекере
                        tracker.current_task_data = task
                        
        except Exception as e:
            log.error(f"❌ Ошибка при проверке обновлений БД: {e}")

    async def cleanup(self):
        """Очистка ресурсов"""
        for tracker in self.trackers.values():
            tracker.stop()
        for task in self.periodic_tasks.values():
            task.cancel()
        for client in self.clients.values():
            try:
                await client.disconnect()
            except Exception:
                pass
        self.trackers.clear()
        self.clients.clear()
        self.periodic_tasks.clear()

# Глобальный менеджер
manager = ChannelSyncManager()

async def run_channel_sync():
    """Запуск основного цикла синхронизации каналов"""
    log.info("🚀 Модуль синхронизации каналов запускается...")
    
    try:
        await manager._load_tasks()
        log.info("✅ Модуль синхронизации каналов успешно запущен")
        
        # Основной цикл для проверки обновлений БД
        while True:
            try:
                # Проверяем обновления БД каждые 10 минут
                await asyncio.sleep(600)
                await manager.check_for_updates()
                
            except Exception as e:
                log.error(f"❌ Ошибка при проверке обновлений БД: {e}")
                await asyncio.sleep(30)
            
    except Exception as e:
        log.error(f"💥 Критическая ошибка в модуле синхронизации каналов: {e}")
    finally:
        await manager.cleanup()
        log.info("🛑 Модуль синхронизации каналов остановлен")

if __name__ == "__main__":
    asyncio.run(run_channel_sync())