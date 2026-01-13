# reaction_booster.py

import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import random
import pytz
import re

from telethon import TelegramClient
from telethon.tl.types import Message, Channel, ChatAdminRights
from telethon.tl.functions.messages import SendReactionRequest, ImportChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest, GetFullChannelRequest
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.types import ReactionEmoji, ChannelParticipantAdmin, ChannelParticipantCreator
from telethon.errors import (
    ChannelPrivateError, InviteHashEmptyError, InviteHashExpiredError, 
    InviteHashInvalidError, UsernameNotOccupiedError, UsernameInvalidError,
    MsgIdInvalidError, UserNotParticipantError
)

from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload

from utils.db_utils import get_session
from telegram_client import init_user_client
from entity_resolver import ensure_peer
from models import ReactionBoostTask, ReactionRecord, MainEntity, BotSession, Country

# Настройка логирования
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                   format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("reaction_booster")

# Константы
CHECK_INTERVAL = 60
MAX_REACTIONS_PER_BOT = 3
MAX_RETRY_ATTEMPTS = 2
REQUEST_DELAY = 2
FLOOD_WAIT_SAFETY_MARGIN = 5
MAX_TASK_ATTEMPTS = 10
TASK_RETRY_DELAY = 300
MAX_JOIN_ATTEMPTS = 2

REACTION_TYPES = {
    "positive": ["👍", "🙏", "🔥", "❤️"],
    "negative": ["😢", "👎", "💙", "🚫", "❌"]
}

class ReactionBoostManager:
    """Менеджер для управления задачами накрутки реакций"""
    
    def __init__(self):
        self.tasks: Dict[int, ReactionBoostTask] = {}
        self.clients: Dict[int, TelegramClient] = {}
        self.running_tasks: Dict[int, asyncio.Task] = {}
        self.bot_premium_status: Dict[int, bool] = {}
        self.last_request_time: Dict[int, datetime] = {}
        self.task_attempts: Dict[int, int] = {}
        self.joined_channels: Dict[int, set] = {}
        self.channel_entities: Dict[int, Dict[int, object]] = {}
        self.invalid_posts: Dict[int, set] = {}
        self.admin_checked_bots: Dict[int, Dict[int, bool]] = {}  # bot_id -> {target_id -> is_admin}
        
    async def initialize(self):
        """Инициализация менеджера"""
        log.info("🔄 Инициализация менеджера реакций...")
        await self._load_tasks()
        await self._initialize_clients()
        
    async def _load_tasks(self):
        """Загружает активные задачи из БД"""
        with get_session() as session:
            tasks = session.execute(
                select(ReactionBoostTask)
                .options(
                    joinedload(ReactionBoostTask.target),
                    joinedload(ReactionBoostTask.bot)
                )
                .where(ReactionBoostTask.is_active == True)
            ).unique().scalars().all()
            
            self.tasks = {task.id: task for task in tasks}
            self.task_attempts = {task.id: 0 for task in tasks}
            log.info(f"🔍 Загружено {len(self.tasks)} активных задач реакций")
    
    async def _initialize_clients(self):
        """Инициализирует клиентов для ботов"""
        with get_session() as session:
            bots = {b.id: b for b in session.execute(
                select(BotSession).where(BotSession.is_active == True)
            ).scalars().all()}
        
        for bot_id, bot in bots.items():
            if bot_id not in self.clients:
                try:
                    client = init_user_client(bot)
                    await client.start()
                    if not await client.is_user_authorized():
                        raise RuntimeError(f"Бот #{bot_id} не авторизован")
                    
                    is_premium = await self._check_premium_status(client)
                    self.bot_premium_status[bot_id] = is_premium
                    
                    self.clients[bot_id] = client
                    self.joined_channels[bot_id] = set()
                    self.channel_entities[bot_id] = {}
                    self.invalid_posts[bot_id] = set()
                    self.admin_checked_bots[bot_id] = {}
                    self.last_request_time[bot_id] = datetime.utcnow() - timedelta(minutes=5)
                    log.info(f"✅ Бот #{bot_id} авторизован для реакций (Премиум: {'Да' if is_premium else 'Нет'})")
                except Exception as e:
                    log.error(f"❌ Ошибка инициализации бота #{bot_id}: {e}")
    
    async def _check_premium_status(self, client: TelegramClient) -> bool:
        """Проверяет премиум статус"""
        try:
            me = await client.get_me()
            return getattr(me, 'premium', False)
        except Exception as e:
            log.warning(f"⚠️ Не удалось проверить премиум статус: {e}")
            return False

    def extract_invite_hash(self, link: str) -> Optional[str]:
        """Извлекает хэш инвайта из ссылки"""
        if not link:
            return None
            
        patterns = [
            r't\.me/joinchat/([a-zA-Z0-9_-]+)',
            r'tg://join\?invite=([a-zA-Z0-9_-]+)',
            r't\.me/\+([a-zA-Z0-9_-]+)',
            r'joinchat/([a-zA-Z0-9_-]+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, link)
            if match:
                return match.group(1)
        
        return None

    def extract_username(self, link: str) -> Optional[str]:
        """Извлекает username из ссылки"""
        if not link:
            return None
            
        patterns = [
            r't\.me/([a-zA-Z0-9_]+)(?!\/joinchat)',
            r'@([a-zA-Z0-9_]+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, link)
            if match:
                username = match.group(1)
                if not username.startswith('+') and 'joinchat' not in link:
                    return username
        
        return None

    async def check_admin_status(self, client: TelegramClient, target: MainEntity, bot_id: int) -> bool:
        """Проверяет, является ли бот администратором в канале/группе"""
        try:
            # Проверяем кэш
            if target.id in self.admin_checked_bots.get(bot_id, {}):
                return self.admin_checked_bots[bot_id][target.id]
            
            log.info(f"🔍 Проверка прав администратора для бота #{bot_id} в {target.name}")
            
            entity = await self.get_channel_entity(client, target, bot_id)
            if not entity:
                log.warning(f"⚠️ Не удалось получить entity для проверки админ-прав бота #{bot_id}")
                return False
            
            # Получаем информацию о себе как участнике
            me = await client.get_me()
            try:
                participant = await client(GetParticipantRequest(
                    channel=entity,
                    participant=me.id
                ))
                
                # Проверяем, является ли бот администратором или создателем
                is_admin = isinstance(participant.participant, (ChannelParticipantAdmin, ChannelParticipantCreator))
                
                if is_admin:
                    log.warning(f"⚠️ Бот #{bot_id} является администратором в {target.name} - пропускаем для реакций")
                
                # Кэшируем результат
                self.admin_checked_bots[bot_id][target.id] = is_admin
                return is_admin
                
            except UserNotParticipantError:
                log.info(f"ℹ️ Бот #{bot_id} не является участником {target.name}, не может быть администратором")
                self.admin_checked_bots[bot_id][target.id] = False
                return False
            except Exception as e:
                log.warning(f"⚠️ Ошибка проверки админ-прав для бота #{bot_id} в {target.name}: {e}")
                # В случае ошибки считаем, что не админ, чтобы не блокировать работу
                self.admin_checked_bots[bot_id][target.id] = False
                return False
                
        except Exception as e:
            log.error(f"❌ Неожиданная ошибка при проверке админ-прав бота #{bot_id}: {e}")
            self.admin_checked_bots[bot_id][target.id] = False
            return False

    async def join_channel(self, client: TelegramClient, target: MainEntity, bot_id: int) -> bool:
        """Пытается вступить в канал/группу без использования ensure_peer"""
        try:
            log.info(f"🔗 Попытка вступления бота #{bot_id} в канал {target.name}")
            
            if not target.link:
                log.error(f"❌ Нет ссылки для канала {target.name}")
                return False

            # Пытаемся вступить по инвайт-ссылке
            invite_hash = self.extract_invite_hash(target.link)
            if invite_hash:
                for attempt in range(MAX_JOIN_ATTEMPTS):
                    try:
                        await self.safe_request_delay(bot_id)
                        await client(ImportChatInviteRequest(invite_hash))
                        log.info(f"✅ Бот #{bot_id} успешно вступил в приватный канал {target.name} по инвайт-ссылке")
                        self.joined_channels[bot_id].add(target.id)
                        # Очищаем кэш entity после вступления
                        if target.id in self.channel_entities.get(bot_id, {}):
                            del self.channel_entities[bot_id][target.id]
                        # Очищаем кэш админ-прав
                        if target.id in self.admin_checked_bots.get(bot_id, {}):
                            del self.admin_checked_bots[bot_id][target.id]
                        return True
                    except (InviteHashEmptyError, InviteHashExpiredError, InviteHashInvalidError):
                        log.warning(f"❌ Неверная или устаревшая инвайт-ссылка для канала {target.name}")
                        return False
                    except Exception as e:
                        if "already" in str(e).lower() or "Уже" in str(e):
                            log.info(f"ℹ️ Бот #{bot_id} уже является участником канала {target.name}")
                            self.joined_channels[bot_id].add(target.id)
                            return True
                        if attempt < MAX_JOIN_ATTEMPTS - 1:
                            wait_time = (attempt + 1) * 10
                            log.warning(f"⚠️ Попытка {attempt + 1} вступления в канал {target.name} не удалась, повтор через {wait_time} сек: {e}")
                            await asyncio.sleep(wait_time)
                        else:
                            log.error(f"❌ Не удалось вступить в канал {target.name} после {MAX_JOIN_ATTEMPTS} попыток: {e}")
                            return False

            # Пытаемся вступить по username
            username = self.extract_username(target.link)
            if username:
                for attempt in range(MAX_JOIN_ATTEMPTS):
                    try:
                        await self.safe_request_delay(bot_id)
                        await client(JoinChannelRequest(username))
                        log.info(f"✅ Бот #{bot_id} успешно вступил в публичный канал {target.name}")
                        self.joined_channels[bot_id].add(target.id)
                        # Очищаем кэш entity после вступления
                        if target.id in self.channel_entities.get(bot_id, {}):
                            del self.channel_entities[bot_id][target.id]
                        # Очищаем кэш админ-прав
                        if target.id in self.admin_checked_bots.get(bot_id, {}):
                            del self.admin_checked_bots[bot_id][target.id]
                        return True
                    except (UsernameNotOccupiedError, UsernameInvalidError):
                        log.warning(f"❌ Username {username} не существует или неверный для канала {target.name}")
                        return False
                    except Exception as e:
                        if "already" in str(e).lower() or "Уже" in str(e):
                            log.info(f"ℹ️ Бот #{bot_id} уже является участником канала {target.name}")
                            self.joined_channels[bot_id].add(target.id)
                            return True
                        if attempt < MAX_JOIN_ATTEMPTS - 1:
                            wait_time = (attempt + 1) * 10
                            log.warning(f"⚠️ Попытка {attempt + 1} вступления в канал {target.name} не удалась, повтор через {wait_time} сек: {e}")
                            await asyncio.sleep(wait_time)
                        else:
                            log.error(f"❌ Не удалось вступить в канал {target.name} после {MAX_JOIN_ATTEMPTS} попыток: {e}")
                            return False

            log.error(f"❌ Не удалось определить тип ссылки для канала {target.name}: {target.link}")
            return False
            
        except Exception as e:
            log.error(f"❌ Неожиданная ошибка при вступлении в канал {target.name}: {e}")
            return False

    async def get_channel_entity(self, client: TelegramClient, target: MainEntity, bot_id: int) -> Optional[object]:
        """Получает entity канала с кэшированием"""
        try:
            # Проверяем кэш
            if target.id in self.channel_entities.get(bot_id, {}):
                return self.channel_entities[bot_id][target.id]
            
            # Пытаемся получить entity через ensure_peer
            entity = await ensure_peer(client, telegram_id=target.telegram_id, link=target.link)
            if entity:
                self.channel_entities[bot_id][target.id] = entity
                return entity
            return None
        except Exception as e:
            log.warning(f"⚠️ Не удалось получить entity канала {target.name}: {e}")
            return None
    
    def get_reactions_for_type(self, reaction_type: str) -> List[str]:
        """Возвращает список реакций для указанного типа"""
        return REACTION_TYPES.get(reaction_type, REACTION_TYPES["positive"])
    
    async def get_random_channel_posts(self, client: TelegramClient, target: MainEntity, bot_id: int, needed_count: int) -> List[Message]:
        """Получает случайные посты из канала за всё время"""
        try:
            # Сначала пытаемся вступить в канал
            if not await self.ensure_channel_membership(client, target, bot_id):
                return []
            
            # Проверяем права администратора
            if await self.check_admin_status(client, target, bot_id):
                log.warning(f"⚠️ Бот #{bot_id} является администратором в {target.name}, пропускаем для получения постов")
                return []
            
            # Получаем entity канала
            channel_entity = await self.get_channel_entity(client, target, bot_id)
            if not channel_entity:
                log.error(f"❌ Не удалось получить entity канала {target.name}")
                return []
            
            # Определяем максимальное количество постов для проверки
            # Пытаемся получить как можно больше постов для лучшей рандомизации
            try:
                # Получаем первые 1000 постов для выборки
                all_messages = await client.get_messages(channel_entity, limit=1000)
                
                # Фильтруем сообщения и проверяем их валидность
                valid_messages = []
                grouped_messages = {}
                
                for msg in all_messages:
                    if not self.is_valid_message(msg, target.id, bot_id):
                        continue
                        
                    if hasattr(msg, 'id') and msg.id and not getattr(msg, 'action', None):
                        if hasattr(msg, 'grouped_id') and msg.grouped_id:
                            group_id = msg.grouped_id
                            if group_id not in grouped_messages:
                                grouped_messages[group_id] = msg
                            continue
                        
                        valid_messages.append(msg)
                
                valid_messages.extend(grouped_messages.values())
                
                log.info(f"📄 Получено {len(valid_messages)} валидных постов из канала {target.name} для бота #{bot_id}")
                
                # Выбираем случайные посты
                if len(valid_messages) <= needed_count:
                    selected_posts = valid_messages
                else:
                    selected_posts = random.sample(valid_messages, needed_count)
                
                post_ids = [msg.id for msg in selected_posts]
                log.info(f"🎲 Выбрано {len(selected_posts)} случайных постов: {post_ids}")
                return selected_posts
                
            except Exception as e:
                log.warning(f"⚠️ Не удалось получить много постов из канала {target.name}: {e}")
                # Пробуем получить меньше постов
                messages = await client.get_messages(channel_entity, limit=needed_count * 3)
                
                valid_messages = [msg for msg in messages if self.is_valid_message(msg, target.id, bot_id)]
                
                if len(valid_messages) <= needed_count:
                    return valid_messages
                else:
                    return random.sample(valid_messages, needed_count)
                
        except ChannelPrivateError:
            log.error(f"🔒 Бот #{bot_id} не имеет доступа к приватному каналу {target.name}")
            return []
        except Exception as e:
            log.error(f"❌ Ошибка получения постов из канала {target.name}: {e}")
            return []
    
    def is_valid_message(self, message: Message, target_id: int, bot_id: int) -> bool:
        """Проверяет, является ли сообщение валидным для установки реакций"""
        if not hasattr(message, 'id') or not message.id:
            return False
        
        # Проверяем, не является ли сообщение служебным
        if getattr(message, 'action', None):
            return False
        
        # Проверяем, не находится ли сообщение в кэше невалидных
        post_key = (target_id, message.id)
        if post_key in self.invalid_posts.get(bot_id, set()):
            return False
        
        return True
    
    def mark_post_as_invalid(self, target_id: int, post_id: int, bot_id: int):
        """Помечает пост как невалидный для конкретного бота"""
        post_key = (target_id, post_id)
        self.invalid_posts[bot_id].add(post_key)
        log.debug(f"🚫 Пост {post_id} помечен как невалидный для бота #{bot_id}")
    
    async def ensure_channel_membership(self, client: TelegramClient, target: MainEntity, bot_id: int) -> bool:
        """Убеждается, что бот является участником канала"""
        try:
            # Проверяем кэш
            if target.id in self.joined_channels.get(bot_id, set()):
                log.debug(f"✅ Бот #{bot_id} уже в канале {target.name} (из кэша)")
                return True
            
            log.info(f"🔗 Обязательное вступление бота #{bot_id} в {target.name}")
            
            # Пытаемся вступить в канал
            success = await self.join_channel(client, target, bot_id)
            
            if not success:
                log.error(f"❌ Бот #{bot_id} не смог вступить в канал {target.name}")
                # Важно: помечаем канал как недоступный для этого бота
                if bot_id not in self.invalid_posts:
                    self.invalid_posts[bot_id] = set()
                self.invalid_posts[bot_id].add(target.id)
                return False
            
            # После успешного вступления проверяем доступ
            try:
                entity = await self.get_channel_entity(client, target, bot_id)
                if entity:
                    # Проверяем, можем ли мы получить сообщения
                    await client.get_messages(entity, limit=1)
                    log.info(f"✅ Бот #{bot_id} успешно вступил в {target.name} и имеет доступ")
                    self.joined_channels[bot_id].add(target.id)
                    return True
                else:
                    log.error(f"❌ Бот #{bot_id} вступил, но не может получить entity для {target.name}")
                    return False
            except Exception as e:
                log.error(f"❌ Бот #{bot_id} вступил, но нет доступа к {target.name}: {e}")
                return False
                
        except Exception as e:
            log.error(f"❌ Ошибка проверки членства в канале {target.name}: {e}")
            return FLOOD_WAIT_SAFETY_MARGIN
    
    async def safe_request_delay(self, bot_id: int):
        """Обеспечивает безопасную задержку между запросами"""
        now = datetime.utcnow()
        last_request = self.last_request_time.get(bot_id, now - timedelta(minutes=5))
        
        time_since_last = (now - last_request).total_seconds()
        if time_since_last < REQUEST_DELAY:
            wait_time = REQUEST_DELAY - time_since_last
            log.debug(f"⏳ Задержка {wait_time:.1f}с для бота #{bot_id}")
            await asyncio.sleep(wait_time)
        
        self.last_request_time[bot_id] = datetime.utcnow()

    async def set_reaction(self, client: TelegramClient, target: MainEntity, post_id: int, reaction: str, bot_id: int):
        """Отправляет реакцию на пост от имени аккаунта-бота"""
        try:
            # Убедиться, что бот в канале
            if not await self.ensure_channel_membership(client, target, bot_id):
                log.error(f"❌ Бот #{bot_id} не состоит в канале {target.name}")
                return False

            # Проверяем права администратора
            if await self.check_admin_status(client, target, bot_id):
                log.warning(f"⚠️ Бот #{bot_id} является администратором в {target.name}, пропускаем для реакции")
                return False

            # Получаем entity канала
            entity = await self.get_channel_entity(client, target, bot_id)
            if not entity:
                log.error(f"❌ Не удалось получить entity канала {target.name}")
                return False

            # ⏳ Безопасная задержка
            await self.safe_request_delay(bot_id)

            # Проверяем доступ к посту перед отправкой реакции
            try:
                # Пробуем получить пост для проверки доступа
                messages = await client.get_messages(entity, ids=post_id)
                if not messages:
                    log.warning(f"⚠️ Пост {post_id} недоступен для бота #{bot_id}")
                    self.mark_post_as_invalid(target.id, post_id, bot_id)
                    return False
            except Exception as e:
                log.warning(f"⚠️ Бот #{bot_id} не может получить доступ к посту {post_id}: {e}")
                return False

            #    КОРРЕКТНЫЙ ВЫЗОВ ДЛЯ ЮЗЕР-БОТОВ
            await client(SendReactionRequest(
                peer=entity,
                msg_id=post_id,
                reaction=[ReactionEmoji(emoticon=reaction)]
            ))

            log.info(f"👍 Бот #{bot_id} поставил реакцию {reaction} на пост {post_id} в {target.name}")
            return True

        except MsgIdInvalidError:
            log.warning(f"⚠️ Пост {post_id} не найден. Помечаем как невалидный.")
            self.mark_post_as_invalid(target.id, post_id, bot_id)
            return False
        except Exception as e:
            if "can't write" in str(e).lower() or "You can't write" in str(e):
                log.error(f"❌ Бот #{bot_id} не имеет прав на запись в {target.name}")
                # Помечаем канал как недоступный для этого бота
                if bot_id not in self.invalid_posts:
                    self.invalid_posts[bot_id] = set()
                self.invalid_posts[bot_id].add(target.id)
            else:
                log.error(f"❌ Ошибка при установке реакции ({reaction}) ботом #{bot_id} в канале {target.name}: {e}")
            return False

    
    async def get_all_available_bots(self) -> List[Tuple[int, int]]:
        """Возвращает список всех доступных ботов с указанием их лимита реакций"""
        available_bots = []
        
        with get_session() as session:
            all_bots = session.execute(
                select(BotSession.id)
                .where(BotSession.is_active == True)
            ).scalars().all()
        
        for bot_id in all_bots:
            if bot_id in self.clients:
                # Каждый бот может поставить только одну реакцию на пост
                # (даже премиум боты могут поставить только одну реакцию от своего имени)
                available_bots.append((bot_id, 1))
        
        log.info(f"🤖 Всего доступно {len(available_bots)} ботов: {[bot[0] for bot in available_bots]}")
        return available_bots
    
    async def distribute_reactions_among_bots(self, total_reactions_needed: int, target: MainEntity) -> List[Tuple[int, int]]:
        """Распределяет нужное количество реакций между всеми доступными ботами с учетом премиум статуса"""
        available_bots = []
        
        with get_session() as session:
            all_bots = session.execute(
                select(BotSession.id)
                .where(BotSession.is_active == True)
            ).scalars().all()
        
        for bot_id in all_bots:
            if bot_id in self.clients:
                # Проверяем премиум статус бота
                is_premium = self.bot_premium_status.get(bot_id, False)
                # Премиум боты могут поставить до 3 реакций на пост
                bot_limit = 3 if is_premium else 1
                available_bots.append((bot_id, bot_limit))
        
        log.info(f"🤖 Всего доступно {len(available_bots)} ботов с лимитами: {[(bot[0], bot[1]) for bot in available_bots]}")
        
        # Случайно перемешиваем ботов для равномерного распределения
        random.shuffle(available_bots)
        
        distributed = []
        reactions_remaining = total_reactions_needed
        
        for bot_id, bot_limit in available_bots:
            if reactions_remaining <= 0:
                break
            
            client = self.clients.get(bot_id)
            if not client:
                continue
            
            # Пытаемся проверить возможность участия бота
            try:
                log.info(f"🔍 Проверка бота #{bot_id} для канала {target.name}")
                
                # 1. Проверяем права администратора
                is_admin = await self.check_admin_status(client, target, bot_id)
                if is_admin:
                    log.warning(f"⚠️ Бот #{bot_id} является администратором в {target.name}, пропускаем")
                    continue
                
                # 2. ОБЯЗАТЕЛЬНО вступаем в канал
                if not await self.ensure_channel_membership(client, target, bot_id):
                    log.warning(f"❌ Бот #{bot_id} не может вступить в канал {target.name}, пропускаем")
                    continue
                
                # 3. Дополнительная проверка доступа к реакции
                try:
                    # Проверяем, может ли бот получать сообщения
                    entity = await self.get_channel_entity(client, target, bot_id)
                    if not entity:
                        log.warning(f"⚠️ Бот #{bot_id} не может получить entity для {target.name}")
                        continue
                    
                    # Пробуем получить одно сообщение для проверки доступа
                    messages = await client.get_messages(entity, limit=1)
                    if not messages:
                        log.warning(f"⚠️ Бот #{bot_id} не может получить сообщения из {target.name}")
                        continue
                    
                    log.info(f"✅ Бот #{bot_id} успешно проверен для {target.name}")
                    
                except Exception as e:
                    log.warning(f"⚠️ Бот #{bot_id} не имеет доступа к {target.name}: {e}")
                    continue
                
                # Бот подходит, добавляем его
                bot_reactions = min(bot_limit, reactions_remaining)
                distributed.append((bot_id, bot_reactions))
                reactions_remaining -= bot_reactions
                log.info(f"✅ Бот #{bot_id} выбран для {bot_reactions} реакций (осталось {reactions_remaining})")
                
            except Exception as e:
                log.warning(f"⚠️ Ошибка проверки бота #{bot_id} для {target.name}: {e}")
                continue
        
        if reactions_remaining > 0 and len(distributed) == 0:
            log.error(f"❌ Нет подходящих ботов для задачи в канале {target.name}")
        
        log.info(f"📊 Распределение {total_reactions_needed} реакций по ботам: {distributed}")
        return distributed
    
    async def check_existing_reactions(self, task: ReactionBoostTask, post_id: int) -> int:
        """Проверяет, сколько реакций уже поставлено на пост для этой задачи"""
        with get_session() as session:
            existing_count = session.execute(
                select(ReactionRecord)
                .where(ReactionRecord.task_id == task.id)
                .where(ReactionRecord.post_message_id == post_id)
            ).scalars().all()
            
            return len(existing_count)
    
    async def execute_task_with_retry(self, task_id: int):
        """Выполняет задачу с повторными попытками до достижения цели"""
        task_attempt = self.task_attempts.get(task_id, 0) + 1
        self.task_attempts[task_id] = task_attempt
        
        log.info(f"🔄 Попытка {task_attempt}/{MAX_TASK_ATTEMPTS} выполнения задачи #{task_id}")
        
        success = await self.execute_task(task_id)
        
        if not success and task_attempt < MAX_TASK_ATTEMPTS:
            log.info(f"⏳ Задача #{task_id} не выполнена полностью. Повтор через {TASK_RETRY_DELAY} секунд")
            await asyncio.sleep(TASK_RETRY_DELAY)
            self.running_tasks[task_id] = asyncio.create_task(self.execute_task_with_retry(task_id))
        elif not success:
            log.error(f"❌ Задача #{task_id} не выполнена после {MAX_TASK_ATTEMPTS} попыток")
        else:
            log.info(f"✅ Задача #{task_id} успешно выполнена")
            self.task_attempts[task_id] = 0
    
    async def execute_task(self, task_id: int) -> bool:
        """Выполняет задачу накрутки реакций"""
        with get_session() as session:
            task = session.execute(
                select(ReactionBoostTask)
                .options(joinedload(ReactionBoostTask.target), joinedload(ReactionBoostTask.bot))
                .where(ReactionBoostTask.id == task_id)
            ).unique().scalar_one_or_none()
            
            if not task:
                log.error(f"❌ Задача #{task_id} не найдена в БД")
                return False
                
            target_name = task.target.name
            log.info(f"🚀 Запуск задачи реакций #{task.id} для канала {target_name}")
            
            try:
                # ВАЖНОЕ ИЗМЕНЕНИЕ: выбираем случайного бота из доступных
                available_bots = await self.distribute_reactions_among_bots(1, task.target)
                if not available_bots:
                    log.error(f"❌ Нет доступных ботов для канала {target_name}")
                    return False
                
                # Выбираем случайного бота из доступных
                random_bot = random.choice(available_bots)
                main_bot_id = random_bot[0]
                log.info(f"🎲 Выбран случайный бот #{main_bot_id} для основной проверки")
                
                main_client = self.clients.get(main_bot_id)
                if not main_client:
                    log.error(f"❌ Клиент для случайного бота #{main_bot_id} не найден")
                    return False
                
                # Убеждаемся, что случайный бот является участником канала
                if not await self.ensure_channel_membership(main_client, task.target, main_bot_id):
                    log.error(f"❌ Случайный бот #{main_bot_id} не смог вступить в канал {target_name}. Задача завершается.")
                    return False
                
                # Проверяем, не является ли случайный бот администратором
                if await self.check_admin_status(main_client, task.target, main_bot_id):
                    log.error(f"❌ Случайный бот #{main_bot_id} является администратором в {target_name}. Задача не может быть выполнена.")
                    return False
                
                # Получаем случайные посты из всех доступных
                posts = await self.get_random_channel_posts(main_client, task.target, main_bot_id, task.posts_count)
                if not posts:
                    log.warning(f"⚠️ В канале {target_name} нет доступных постов для бота #{main_bot_id}")
                    return False
                
                selected_count = min(task.posts_count, len(posts))
                if selected_count == 0:
                    log.warning(f"⚠️ В канале {target_name} нет подходящих постов")
                    return False
                    
                selected_posts = posts  # Уже случайные из get_random_channel_posts
                total_reactions_needed = selected_count * task.reactions_per_post
                
                log.info(f"📝 Выбрано {len(selected_posts)} случайных постов для обработки, нужно {total_reactions_needed} реакций")
                
                reactions = [r for r in self.get_reactions_for_type(task.reaction_type) if r != "⭐️"]
                if not reactions:
                    log.error(f"❌ Нет доступных реакций для типа {task.reaction_type}")
                    return False
                
                log.info(f"🎭 Доступные реакции: {reactions}")
                
                total_reactions_set = 0
                posts_processed = 0
                
                for post_idx, post in enumerate(selected_posts):
                    log.info(f"📄 Обработка поста {post_idx + 1}/{len(selected_posts)} (ID: {post.id})")
                    
                    # Проверяем, не помечен ли пост как невалидный
                    post_key = (task.target.id, post.id)
                    if any(post_key in self.invalid_posts.get(bot_id, set()) for bot_id in self.clients.keys()):
                        log.warning(f"🚫 Пост {post.id} помечен как невалидный, пропускаем")
                        continue
                    
                    existing_reactions = await self.check_existing_reactions(task, post.id)
                    reactions_needed_for_post = max(0, task.reactions_per_post - existing_reactions)
                    
                    if reactions_needed_for_post == 0:
                        log.info(f"✅ На пост {post.id} уже установлены все нужные реакции")
                        posts_processed += 1
                        total_reactions_set += task.reactions_per_post
                        continue
                    
                    log.info(f"🔄 Нужно установить {reactions_needed_for_post} реакций на пост {post.id}")
                    
                    bots_for_post = await self.distribute_reactions_among_bots(reactions_needed_for_post, task.target)
                    
                    if not bots_for_post:
                        log.error(f"❌ Нет доступных ботов для поста {post.id}")
                        continue
                    
                    log.info(f"🤖 Для поста {post.id} используется {len(bots_for_post)} ботов: {[(bot[0], bot[1]) for bot in bots_for_post]}")
                    
                    post_reactions_set = 0
                    
                    for bot_idx, (bot_id, reactions_count) in enumerate(bots_for_post):
                        if post_reactions_set >= reactions_needed_for_post:
                            break
                            
                        client = self.clients.get(bot_id)
                        if not client:
                            continue
                            
                        # Убеждаемся, что бот является участником канала
                        if not await self.ensure_channel_membership(client, task.target, bot_id):
                            log.warning(f"⚠️ Бот #{bot_id} не может вступить в канал {target_name}, пробуем другого бота")
                            # ПРОПУСКАЕМ этого бота и продолжаем с другими
                            continue
                        
                        # Проверяем, не является ли бот администратором
                        if await self.check_admin_status(client, task.target, bot_id):
                            log.warning(f"⚠️ Бот #{bot_id} является администратором в {target.name}, пропускаем")
                            continue
                        
                        # Для премиум ботов ставим несколько реакций сразу
                        for reaction_idx in range(reactions_count):
                            if post_reactions_set >= reactions_needed_for_post:
                                break
                                
                            # Выбираем случайную реакцию для каждого вызова
                            reaction = random.choice(reactions)
                            success = await self.set_reaction(client, task.target, post.id, reaction, bot_id)
                            
                            if success:
                                # Проверяем, не поставил ли уже этот бот реакцию на этот пост
                                existing_bot_reactions = session.execute(
                                    select(ReactionRecord)
                                    .where(and_(
                                        ReactionRecord.task_id == task.id,
                                        ReactionRecord.post_message_id == post.id,
                                        ReactionRecord.bot_id == bot_id
                                    ))
                                ).scalars().all()
                                
                                if len(existing_bot_reactions) > 0:
                                    # Если бот уже поставил реакцию, но у него премиум и может больше
                                    # Проверяем, сколько уже поставил
                                    if len(existing_bot_reactions) >= reactions_count:
                                        log.warning(f"⚠️ Бот #{bot_id} уже поставил максимальное количество реакций на пост {post.id}, пропускаем")
                                        continue
                                
                                record = ReactionRecord(
                                    task_id=task.id,
                                    post_message_id=post.id,
                                    bot_id=bot_id,
                                    reaction=reaction
                                )
                                session.add(record)
                                session.flush()  # Сохраняем, но не коммитим
                                
                                post_reactions_set += 1
                                total_reactions_set += 1
                                
                                log.info(f"✅ Бот #{bot_id} поставил реакцию {reaction} на пост {post.id} ({post_reactions_set}/{reactions_needed_for_post})")
                                
                                # Короткая задержка между реакциями от одного бота
                                if reaction_idx < reactions_count - 1:
                                    await asyncio.sleep(1)
                            else:
                                log.warning(f"⚠️ Бот #{bot_id} не смог поставить реакцию на пост {post.id}")
                        
                        # Задержка между ботами для одного поста
                        if bot_idx < len(bots_for_post) - 1:
                            await asyncio.sleep(2)
                    
                    if post_reactions_set >= reactions_needed_for_post:
                        posts_processed += 1
                        log.info(f"✅ На пост {post.id} установлено {post_reactions_set}/{reactions_needed_for_post} реакций")
                    else:
                        log.warning(f"⚠️ На пост {post.id} установлено только {post_reactions_set}/{reactions_needed_for_post} реакций")
                    
                    # Задержка между постами
                    if post_idx < len(selected_posts) - 1:
                        post_delay = 5 + random.randint(0, 5)
                        log.debug(f"⏳ Задержка {post_delay}с перед следующим постом")
                        await asyncio.sleep(post_delay)
                
                # Обновляем время последнего запуска
                task.last_launch = datetime.utcnow().replace(tzinfo=None)
                
                # Проверяем, достигнута ли цель
                all_reactions_set = (total_reactions_set >= total_reactions_needed)
                all_posts_processed = (posts_processed >= len(selected_posts))
                
                success = all_reactions_set and all_posts_processed
                
                if success:
                    log.info(f"🎉 Задача #{task.id} ВЫПОЛНЕНА: установлено {total_reactions_set}/{total_reactions_needed} реакций на {posts_processed}/{len(selected_posts)} постов")
                    
                    if task.run_once_now:
                        task.run_once_now = False
                        task.is_active = False
                        log.info(f"🛑 Задача #{task.id} деактивирована после успешного выполнения")
                        if task.id in self.tasks:
                            del self.tasks[task.id]
                else:
                    log.warning(f"⚠️ Задача #{task.id} НЕ ЗАВЕРШЕНА: установлено {total_reactions_set}/{total_reactions_needed} реакций на {posts_processed}/{len(selected_posts)} постов")
                
                session.commit()
                return success
                
            except Exception as e:
                log.error(f"❌ Ошибка выполнения задачи #{task.id}: {e}")
                session.rollback()
                return False

    # Остальные методы остаются без изменений...
    def should_run_task(self, task: ReactionBoostTask) -> bool:
        """Проверяет, нужно ли запускать задачу"""
        if task.run_once_now:
            return True
        
        if not task.is_active:
            return False
        
        now = datetime.utcnow()
        
        if not task.last_launch:
            return True
        
        last_launch = task.last_launch
        if last_launch.tzinfo is not None:
            last_launch = last_launch.astimezone(pytz.UTC).replace(tzinfo=None)
        
        days_passed = (now - last_launch).days
        if days_passed < task.frequency_days:
            return False
        
        try:
            with get_session() as session:
                country = session.execute(
                    select(Country).join(MainEntity).where(MainEntity.id == task.target_id)
                ).scalar_one_or_none()
                
                if country and country.time_zone_delta is not None:
                    moscow_tz = pytz.timezone('Europe/Moscow')
                    country_tz = pytz.FixedOffset(int(country.time_zone_delta * 60))
                    
                    now_moscow = datetime.now(moscow_tz)
                    now_country = now_moscow.astimezone(country_tz)
                    
                    current_time = now_country.time()
                else:
                    current_time = datetime.now().time()
        except Exception as e:
            log.warning(f"⚠️ Ошибка получения временной зоны для задачи #{task.id}: {e}")
            current_time = datetime.now().time()
        
        launch_time = task.launch_time
        time_diff = abs((current_time.hour * 60 + current_time.minute) - 
                       (launch_time.hour * 60 + launch_time.minute))
        
        return time_diff <= 5
    
    async def check_and_run_tasks(self):
        """Проверяет и запускает задачи по расписанию"""
        tasks_to_run = []
        
        for task_id, task in self.tasks.items():
            if task_id in self.running_tasks:
                running_task = self.running_tasks[task_id]
                if not running_task.done():
                    continue
                else:
                    try:
                        running_task.result()
                    except Exception as e:
                        log.error(f"❌ Ошибка в задаче #{task_id}: {e}")
                    del self.running_tasks[task_id]
                
            if self.should_run_task(task):
                tasks_to_run.append(task_id)
        
        for task_id in tasks_to_run:
            log.info(f"⏰ Запуск задачи реакций #{task_id}")
            self.running_tasks[task_id] = asyncio.create_task(self.execute_task_with_retry(task_id))
    
    async def check_for_updates(self):
        """Проверяет обновления в БД и обновляет задачи"""
        with get_session() as session:
            current_tasks_result = session.execute(
                select(ReactionBoostTask)
                .options(joinedload(ReactionBoostTask.target), joinedload(ReactionBoostTask.bot))
                .where(ReactionBoostTask.is_active == True)
            ).unique().scalars().all()
            
            current_tasks = {task.id: task for task in current_tasks_result}
            
            for task_id, task in current_tasks.items():
                if task_id not in self.tasks:
                    self.tasks[task_id] = task
                    self.task_attempts[task_id] = 0
                    log.info(f"✅ Добавлена новая задача реакций #{task_id}")
            
            for task_id in list(self.tasks.keys()):
                if task_id not in current_tasks:
                    if task_id in self.running_tasks:
                        self.running_tasks[task_id].cancel()
                        try:
                            await self.running_tasks[task_id]
                        except asyncio.CancelledError:
                            pass
                        del self.running_tasks[task_id]
                    
                    if task_id in self.task_attempts:
                        del self.task_attempts[task_id]
                    del self.tasks[task_id]
                    log.info(f"🗑️ Удалена неактивная задача реакций #{task_id}")
            
            for task_id, current_task in current_tasks.items():
                if task_id in self.tasks:
                    self.tasks[task_id].posts_count = current_task.posts_count
                    self.tasks[task_id].reactions_per_post = current_task.reactions_per_post
                    self.tasks[task_id].reaction_type = current_task.reaction_type
                    self.tasks[task_id].frequency_days = current_task.frequency_days
                    self.tasks[task_id].launch_time = current_task.launch_time
                    self.tasks[task_id].run_once_now = current_task.run_once_now
                    self.tasks[task_id].last_launch = current_task.last_launch
    
    async def run(self):
        """Основной цикл работы менеджера"""
        await self.initialize()
        log.info("✅ Менеджер реакций запущен")
        
        check_counter = 0
        
        while True:
            try:
                await self.check_and_run_tasks()
                await asyncio.sleep(CHECK_INTERVAL)
                
                check_counter += 1
                if check_counter >= 5:
                    await self.check_for_updates()
                    check_counter = 0
                    
            except Exception as e:
                log.error(f"❌ Ошибка в основном цикле реакций: {e}")
                await asyncio.sleep(60)
    
    async def cleanup(self):
        """Очистка ресурсов"""
        for task_id, task in self.running_tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        for client in self.clients.values():
            try:
                await client.disconnect()
            except Exception:
                pass

# Глобальный менеджер
manager = ReactionBoostManager()

async def run_reaction_booster():
    """Запуск основного цикла реакций"""
    log.info("🚀 Модуль накрутки реакций запускается...")
    
    try:
        await manager.run()
    except KeyboardInterrupt:
        log.info("🛑 Остановка по Ctrl+C")
    except Exception as e:
        log.error(f"💥 Критическая ошибка в модуле реакций: {e}")
    finally:
        await manager.cleanup()
        log.info("🛑 Модуль накрутки реакций остановлен")