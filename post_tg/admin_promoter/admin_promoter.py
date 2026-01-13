# admin_promoter/admin_promoter.py

import os
import asyncio
import logging
import json
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Tuple, Any
from pathlib import Path
from collections import defaultdict

from telethon import TelegramClient, functions, types
from telethon.errors import (
    FloodWaitError, ChatAdminRequiredError, UserAdminInvalidError,
    ChannelPrivateError, ChatWriteForbiddenError, UserNotParticipantError,
    InviteHashInvalidError, InviteHashExpiredError, InviteHashEmptyError,
    UsernameInvalidError, UsernameNotOccupiedError
)
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import joinedload

from utils.db_utils import get_session
from telegram_client import init_user_client
from entity_resolver import ensure_peer
from models import BotSession, MainEntity, DailyPinningTask, ViewBoostTask, OldViewsTask, SubscribersBoostTask, ReactionBoostTask, ChannelSyncTask, BlondinkaTask

# Настройка логирования
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("admin_promoter")

# Константы
CHECK_INTERVAL = 60  # Проверка каждую минуту
MAX_ADMINS_PER_CHAT = 50  # Лимит Telegram на администраторов в чате
DAILY_ADMIN_ADD_LIMIT = 20  # Лимит на добавление администраторов в день с одного аккаунта
COMMAND_FILE = Path("/app/data/admin_commands.json")  # Файл для управления командами
PROMOTER_BOT_ID = int(os.getenv("PROMOTER_BOT_ID", "10"))  # ID главного бота-промоутера
OWNER_FILTER = os.getenv("OWNER_FILTER", "Свой")  # Фильтр по полю owner

# Права администратора (без права назначения новых админов)
ADMIN_RIGHTS = types.ChatAdminRights(
    change_info=True,
    post_messages=True,
    edit_messages=True,
    delete_messages=True,
    ban_users=True,
    invite_users=True,
    pin_messages=True,
    add_admins=False,  # КРИТИЧНО: без права добавлять новых админов!
    anonymous=False,
    manage_call=True,
    other=True
)


class AdminPromoter:
    """Основной класс для назначения администраторов с одним главным ботом-промоутером"""
    
    def __init__(self):
        self.promoter_bot_id = PROMOTER_BOT_ID
        self.promoter_client: Optional[TelegramClient] = None
        self.joined_entities: Set[int] = set()  # ID сущностей, где промоутер состоит
        self.admin_entities: Set[int] = set()  # ID сущностей, где промоутер является админом
        self.daily_admin_additions: Dict[str, int] = {}  # Добавления админов по дням
        self.bots_cache: Dict[int, Dict[str, Any]] = {}  # Кэш ботов: {bot_id: {"telegram_id": int, "phone": str}}
        self.command_handler = CommandHandler()
        self.running = False
        
    async def initialize(self):
        """Инициализация промоутера с улучшенным логированием"""
        log.info(f"🔄 Инициализация AdminPromoter с главным ботом #{self.promoter_bot_id}...")
        log.info(f"🔍 Фильтр по принадлежности: owner='{OWNER_FILTER}'")
        
        if self.promoter_bot_id == 0:
            log.error("❌ PROMOTER_BOT_ID не указан!")
            raise ValueError("PROMOTER_BOT_ID не указан")
        
        try:
            # 1. Инициализируем промоутера
            await self._initialize_promoter()
            log.info("✅ Промоутер инициализирован")
            
            # 2. Обновляем кэш ботов
            log.info("🔄 Обновление кэша ботов...")
            await self._update_bots_cache()
            log.info(f"✅ Кэш ботов: {len(self.bots_cache)} ботов")
            
            # 3. Загружаем сущности
            log.info("🔄 Загрузка сущностей промоутера...")
            await self._load_promoter_entities()
            log.info(f"✅ Сущности загружены")
            
        except Exception as e:
            log.error(f"❌ Критическая ошибка инициализации: {e}")
            raise
        
    async def _initialize_promoter(self):
        """Инициализирует главного бота-промоутера"""
        with get_session() as session:
            promoter_bot = session.get(BotSession, self.promoter_bot_id)
            
            if not promoter_bot:
                log.error(f"❌ Бот-промоутер #{self.promoter_bot_id} не найден в БД")
                raise ValueError(f"Bot #{self.promoter_bot_id} not found")
            
            if not promoter_bot.is_active:
                log.error(f"❌ Бот-промоутер #{self.promoter_bot_id} не активен")
                raise ValueError(f"Bot #{self.promoter_bot_id} is not active")
            
            log.info(f"🔍 Найден бот-промоутер #{self.promoter_bot_id}: телефон {promoter_bot.phone}")
            
            # Проверяем и обновляем telegram_info при необходимости
            if not promoter_bot.telegram_info or 'id' not in promoter_bot.telegram_info:
                await self._update_bot_info(promoter_bot)
            
            # Инициализируем клиент
            try:
                self.promoter_client = init_user_client(promoter_bot)
                await self.promoter_client.start()
                
                if not await self.promoter_client.is_user_authorized():
                    raise RuntimeError(f"Бот-промоутер #{self.promoter_bot_id} не авторизован")
                
                # Получаем информацию о боте
                me = await self.promoter_client.get_me()
                log.info(f"✅ Бот-промоутер #{self.promoter_bot_id} авторизован: @{me.username if me.username else me.id}")
                
            except Exception as e:
                log.error(f"❌ Ошибка инициализации бота-промоутера #{self.promoter_bot_id}: {e}")
                raise
    
    async def _update_bot_info(self, bot: BotSession):
        """Обновляет telegram_info для бота с таймаутом"""
        log.info(f"🔧 Обновление telegram_info для бота #{bot.id}...")
        
        temp_client = None
        try:
            temp_client = init_user_client(bot)
            
            # Устанавливаем таймаут на подключение
            try:
                await asyncio.wait_for(temp_client.start(), timeout=30)
            except asyncio.TimeoutError:
                log.warning(f"⚠️ Таймаут подключения для бота #{bot.id}")
                if temp_client:
                    try:
                        await temp_client.disconnect()
                    except:
                        pass
                return
            
            # Проверяем авторизацию с таймаутом
            try:
                is_authorized = await asyncio.wait_for(
                    temp_client.is_user_authorized(), 
                    timeout=10
                )
            except asyncio.TimeoutError:
                log.warning(f"⚠️ Таймаут проверки авторизации для бота #{bot.id}")
                await temp_client.disconnect()
                return
            
            if not is_authorized:
                log.warning(f"⚠️ Бот #{bot.id} не авторизован")
                await temp_client.disconnect()
                return
            
            # Получаем информацию о боте с таймаутом
            try:
                me = await asyncio.wait_for(temp_client.get_me(), timeout=10)
            except asyncio.TimeoutError:
                log.warning(f"⚠️ Таймаут получения информации для бота #{bot.id}")
                await temp_client.disconnect()
                return
            
            # Сохраняем информацию в БД
            with get_session() as session:
                db_bot = session.get(BotSession, bot.id)
                if not db_bot.telegram_info:
                    db_bot.telegram_info = {}
                db_bot.telegram_info.update({
                    'id': me.id,
                    'username': me.username,
                    'first_name': me.first_name,
                    'last_name': me.last_name
                })
                session.commit()
            
            log.info(f"✅ Обновлен telegram_info для бота #{bot.id}: ID={me.id}")
            
        except Exception as e:
            log.warning(f"⚠️ Не удалось обновить telegram_info для бота #{bot.id}: {e}")
        finally:
            if temp_client:
                try:
                    await temp_client.disconnect()
                except Exception:
                    pass
    
    async def _update_bots_cache(self):
        """Обновляет кэш ботов из БД - УПРОЩЕННАЯ ВЕРСИЯ"""
        try:
            with get_session() as session:
                bots = session.execute(
                    select(BotSession)
                    .where(BotSession.is_active == True)
                    .where(BotSession.id != self.promoter_bot_id)
                ).scalars().all()
            
            self.bots_cache.clear()
            updated_count = 0
            total_bots = len(bots)
            
            log.info(f"🔄 Обновление информации для {total_bots} ботов...")
            
            for i, bot in enumerate(bots, 1):
                try:
                    telegram_id = await self._update_bot_info_simple(bot)
                    if telegram_id:
                        self.bots_cache[bot.id] = {
                            'telegram_id': telegram_id,
                            'phone': bot.phone,
                            'username': bot.telegram_info.get('username') if bot.telegram_info else None
                        }
                        updated_count += 1
                    
                    log.info(f"📊 Прогресс: {i}/{total_bots} ботов обработано")
                    
                    # Пауза между ботами
                    if i < total_bots:
                        await asyncio.sleep(2)
                        
                except Exception as e:
                    log.error(f"❌ Ошибка обработки бота #{bot.id}: {e}")
                    continue
            
            log.info(f"✅ Кэш ботов обновлен: {updated_count}/{total_bots} успешно")
            
        except Exception as e:
            log.error(f"❌ Ошибка обновления кэша ботов: {e}")

    async def _update_bot_info_simple(self, bot: BotSession) -> Optional[int]:
        """Простая версия обновления информации о боте"""
        log.info(f"🔧 Обновление telegram_info для бота #{bot.id}...")
        
        temp_client = None
        try:
            temp_client = init_user_client(bot)
            
            # Таймаут на всю операцию
            try:
                # Подключение
                await asyncio.wait_for(temp_client.start(), timeout=15)
                
                # Проверка авторизации
                is_authorized = await asyncio.wait_for(
                    temp_client.is_user_authorized(), 
                    timeout=10
                )
                
                if not is_authorized:
                    log.warning(f"⚠️ Бот #{bot.id} не авторизован")
                    return None
                
                # Получение информации
                me = await asyncio.wait_for(temp_client.get_me(), timeout=10)
                
                # Сохранение в БД
                with get_session() as session:
                    db_bot = session.get(BotSession, bot.id)
                    if not db_bot.telegram_info:
                        db_bot.telegram_info = {}
                    db_bot.telegram_info.update({
                        'id': me.id,
                        'username': me.username,
                        'first_name': me.first_name,
                        'last_name': me.last_name
                    })
                    session.commit()
                
                log.info(f"✅ Обновлен telegram_info для бота #{bot.id}: ID={me.id}")
                return me.id
                
            except asyncio.TimeoutError:
                log.warning(f"⚠️ Таймаут для бота #{bot.id}")
                return None
                
        except Exception as e:
            log.warning(f"⚠️ Ошибка обновления бота #{bot.id}: {e}")
            return None
        finally:
            if temp_client:
                try:
                    await temp_client.disconnect()
                except Exception:
                    pass
        
    async def _load_promoter_entities(self):
        """Загружает сущности, где промоутер уже состоит и является админом"""
        try:
            # Получаем все диалоги промоутера
            if not self.promoter_client:
                return
            
            dialogs = await self.promoter_client.get_dialogs(limit=100)
            
            # Сначала загружаем все свои сущности из БД
            with get_session() as session:
                stmt = select(MainEntity).where(
                    or_(
                        MainEntity.owner == OWNER_FILTER,
                        MainEntity.owner == None,
                        MainEntity.owner == ""
                    )
                )
                entities = session.execute(stmt).scalars().all()
            
            entity_by_username = {}
            entity_by_id = {}
            for entity in entities:
                self.joined_entities.add(entity.id)
                entity_by_id[entity.id] = entity
                
                # Извлекаем username для быстрого поиска
                username = self._extract_username(entity.link) if entity.link else None
                if username:
                    entity_by_username[username] = entity
            
            own_entities_count = len(entities)
            admin_entities_count = 0
            
            # Проверяем права в каждом диалоге
            for dialog in dialogs:
                try:
                    if not dialog.entity:
                        continue
                    
                    entity = None
                    
                    # Пробуем найти по username
                    if hasattr(dialog.entity, 'username') and dialog.entity.username:
                        username = dialog.entity.username.lower()
                        if username in entity_by_username:
                            entity = entity_by_username[username]
                    
                    # Если не нашли по username, ищем по названию или ID
                    if not entity:
                        chat_id = getattr(dialog.entity, 'id', None)
                        if chat_id:
                            chat_id = abs(chat_id)
                            for e in entities:
                                if (e.telegram_id and abs(e.telegram_id) == chat_id) or \
                                   (e.name and dialog.name and e.name.lower() == dialog.name.lower()):
                                    entity = e
                                    break
                    
                    if entity:
                        # Проверяем права администратора
                        try:
                            input_entity = await self.promoter_client.get_input_entity(dialog.entity)
                            me_entity = await self.promoter_client.get_input_entity('me')
                            
                            participant = await self.promoter_client(
                                functions.channels.GetParticipantRequest(
                                    channel=input_entity,
                                    participant=me_entity
                                )
                            )
                            
                            if isinstance(participant.participant,
                                        (types.ChannelParticipantAdmin, types.ChannelParticipantCreator)):
                                self.admin_entities.add(entity.id)
                                admin_entities_count += 1
                                log.debug(f"✅ Промоутер админ в {entity.name}")
                            else:
                                log.debug(f"⚠️ Промоутер не админ в {entity.name}")
                            
                        except (ChatAdminRequiredError, ChannelPrivateError, UserNotParticipantError) as e:
                            log.debug(f"⚠️ Нет прав для проверки статуса в {entity.name}: {e}")
                        except Exception as e:
                            log.debug(f"⚠️ Ошибка проверки статуса в {entity.name}: {e}")
                            
                except Exception as e:
                    log.debug(f"⚠️ Ошибка обработки диалога {getattr(dialog, 'name', 'Unknown')}: {e}")
                    continue
            
            log.info(f"✅ Загружено {own_entities_count} сущностей с owner='{OWNER_FILTER}'")
            log.info(f"✅ Промоутер является админом в {admin_entities_count} из них")
            
        except Exception as e:
            log.error(f"❌ Ошибка загрузки сущностей промоутера: {e}")
    
    def _extract_invite_hash(self, link: str) -> Optional[str]:
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
    
    def _extract_username(self, link: str) -> Optional[str]:
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
                    return username.lower()
        
        return None
    
    async def _join_entity(self, entity: MainEntity) -> bool:
        """Пытается присоединиться к сущности"""
        try:
            if not entity.link:
                log.warning(f"⚠️ Нет ссылки для сущности {entity.name} (owner: {entity.owner})")
                return False
            
            log.info(f"🔗 Пытаемся присоединиться к {entity.name} (owner: {entity.owner}) по ссылке: {entity.link}")
            
            # Пробуем по инвайт-ссылке
            invite_hash = self._extract_invite_hash(entity.link)
            if invite_hash:
                try:
                    await self.promoter_client(functions.messages.ImportChatInviteRequest(invite_hash))
                    log.info(f"✅ Успешно присоединились к {entity.name} (owner: {entity.owner}) по инвайт-ссылке")
                    return True
                except (InviteHashInvalidError, InviteHashExpiredError, InviteHashEmptyError) as e:
                    log.warning(f"⚠️ Неверная или устаревшая инвайт-ссылка для {entity.name} (owner: {entity.owner}): {e}")
                except Exception as e:
                    error_str = str(e).lower()
                    if "already" in error_str or "уже" in error_str:
                        log.info(f"ℹ️ Уже состоим в {entity.name} (owner: {entity.owner})")
                        return True
                    log.warning(f"⚠️ Ошибка присоединения по инвайт-ссылке к {entity.name} (owner: {entity.owner}): {e}")
            
            # Пробуем по username
            username = self._extract_username(entity.link)
            if username:
                try:
                    await self.promoter_client(functions.channels.JoinChannelRequest(f"@{username}"))
                    log.info(f"✅ Успешно присоединились к {entity.name} (owner: {entity.owner}) по username")
                    return True
                except (UsernameInvalidError, UsernameNotOccupiedError) as e:
                    log.warning(f"⚠️ Неверный username для {entity.name} (owner: {entity.owner}): {e}")
                except Exception as e:
                    error_str = str(e).lower()
                    if "already" in error_str or "уже" in error_str or "already a participant" in error_str:
                        log.info(f"ℹ️ Уже состоим в {entity.name} (owner: {entity.owner})")
                        return True
                    log.warning(f"⚠️ Ошибка присоединения по username к {entity.name} (owner: {entity.owner}): {e}")
            
            return False
                
        except Exception as e:
            log.error(f"❌ Ошибка присоединения к {entity.name} (owner: {entity.owner}): {e}")
            return False
    
    async def _get_all_participants(self, peer) -> List[types.TypeUser]:
        """Получает всех участников сущности (исправленная версия)"""
        participants = []
        
        try:
            # Используем итератор вместо offset
            async for participant in self.promoter_client.iter_participants(
                peer,
                limit=200,  # Лимит за один запрос
                aggressive=False
            ):
                participants.append(participant)
                
                # Логируем прогресс каждые 100 участников
                if len(participants) % 100 == 0:
                    log.debug(f"Получено {len(participants)} участников...")
                
                # Ограничиваем общее количество (на случай огромных чатов)
                if len(participants) >= 1000:
                    log.info(f"Достигнут лимит 1000 участников для проверки")
                    break
                    
        except (ChatAdminRequiredError, ChannelPrivateError, UserNotParticipantError) as e:
            log.warning(f"⚠️ Не удалось получить участников: {e}")
            return []
        except Exception as e:
            log.error(f"❌ Ошибка получения участников: {e}")
            return []
        
        log.debug(f"Всего получено {len(participants)} участников")
        return participants
    
    async def _get_all_admins(self, peer) -> Set[int]:
        """Получает всех администраторов сущности"""
        admins = set()
        
        try:
            # Получаем администраторов
            result = await self.promoter_client(
                functions.channels.GetParticipantsRequest(
                    channel=peer,
                    filter=types.ChannelParticipantsAdmins(),
                    offset=0,
                    limit=100,
                    hash=0
                )
            )
            
            for user in result.users:
                admins.add(user.id)
                
            log.debug(f"Найдено {len(admins)} администраторов")
                
        except Exception as e:
            log.warning(f"⚠️ Не удалось получить администраторов: {e}")
        
        return admins
    
    async def process_entity(self, entity: MainEntity):
        """Обрабатывает одну сущность"""
        try:
            # Проверяем, состоит ли промоутер в сущности и является ли админом
            if entity.id not in self.admin_entities:
                if entity.id in self.joined_entities:
                    log.info(f"⚠️ Промоутер в {entity.name}, но не админ")
                else:
                    log.info(f"🔗 Промоутер не в {entity.name}, пытаемся присоединиться...")
                    if await self._join_entity(entity):
                        self.joined_entities.add(entity.id)
                        log.info(f"✅ Присоединились к {entity.name}")
                    else:
                        log.warning(f"❌ Не удалось присоединиться к {entity.name}")
                        return
                
                # Проверяем права после присоединения
                await self._update_entity_admin_status(entity)
                if entity.id not in self.admin_entities:
                    log.warning(f"⚠️ Промоутер не админ в {entity.name}, пропускаем")
                    return
            
            # Промоутер админ - обрабатываем сущность
            log.info(f"🔍 Проверяем ботов в {entity.name}...")
            
            peer = await ensure_peer(
                self.promoter_client,
                telegram_id=entity.telegram_id,
                link=entity.link
            )
            
            if not peer:
                log.error(f"❌ Не удалось получить peer для {entity.name}")
                return
            
            # Получаем всех администраторов
            admin_ids = await self._get_all_admins(peer)
            log.debug(f"📊 В {entity.name} найдено {len(admin_ids)} администраторов")
            
            # Получаем всех участников (только ботов из нашего кэша)
            participants = await self._get_all_participants(peer)
            log.debug(f"📊 В {entity.name} найдено {len(participants)} участников")
            
            # Находим ботов, которые не админы
            bots_to_promote = []
            telegram_to_bot_id = {bot_info['telegram_id']: bot_id 
                                 for bot_id, bot_info in self.bots_cache.items()}
            
            for participant in participants:
                if participant.id in telegram_to_bot_id and participant.id not in admin_ids:
                    bot_id = telegram_to_bot_id[participant.id]
                    
                    # Проверяем лимит на сегодня
                    if not self._can_add_admin_today(entity.id):
                        log.warning(f"⚠️ Достигнут дневной лимит для {entity.name}")
                        break
                    
                    bots_to_promote.append((bot_id, participant.id))
            
            # Назначаем админами
            if bots_to_promote:
                log.info(f"🚀 Найдено {len(bots_to_promote)} ботов для назначения в {entity.name}")
                
                for bot_id, telegram_id in bots_to_promote:
                    success = await self._promote_to_admin(entity, telegram_id, bot_id)
                    if success:
                        self._record_admin_addition(entity.id)
                        log.info(f"✅ Бот #{bot_id} назначен админом в {entity.name}")
                        await asyncio.sleep(2)  # Задержка между назначениями
                    else:
                        log.warning(f"⚠️ Не удалось назначить бота #{bot_id} в {entity.name}")
            else:
                log.info(f"ℹ️ В {entity.name} все боты уже админы")
                    
        except Exception as e:
            log.error(f"❌ Ошибка обработки сущности {entity.name}: {e}")
    
    async def _update_entity_admin_status(self, entity: MainEntity):
        """Обновляет статус админа для сущности"""
        try:
            peer = await ensure_peer(
                self.promoter_client,
                telegram_id=entity.telegram_id,
                link=entity.link
            )
            
            if not peer:
                return
            
            # Получаем статус участника
            me = await self.promoter_client.get_me()
            
            try:
                participant = await self.promoter_client(
                    functions.channels.GetParticipantRequest(
                        channel=peer,
                        participant=me.id
                    )
                )
                
                if isinstance(participant.participant,
                             (types.ChannelParticipantAdmin, types.ChannelParticipantCreator)):
                    self.admin_entities.add(entity.id)
                    log.debug(f"✅ Промоутер админ в {entity.name}")
                else:
                    if entity.id in self.admin_entities:
                        self.admin_entities.remove(entity.id)
                    
            except (ChatAdminRequiredError, ChannelPrivateError, UserNotParticipantError):
                if entity.id in self.admin_entities:
                    self.admin_entities.remove(entity.id)
                
        except Exception as e:
            log.debug(f"⚠️ Ошибка проверки статуса админа в {entity.name}: {e}")
    
    def _can_add_admin_today(self, entity_id: int) -> bool:
        """Проверяет, можно ли добавлять админов сегодня с учетом лимитов"""
        today = datetime.now().strftime("%Y-%m-%d")
        key = f"{entity_id}_{today}"
        
        if key not in self.daily_admin_additions:
            self.daily_admin_additions[key] = 0
        
        return self.daily_admin_additions[key] < DAILY_ADMIN_ADD_LIMIT
    
    def _record_admin_addition(self, entity_id: int):
        """Записывает добавление администратора"""
        today = datetime.now().strftime("%Y-%m-%d")
        key = f"{entity_id}_{today}"
        
        if key not in self.daily_admin_additions:
            self.daily_admin_additions[key] = 0
        
        self.daily_admin_additions[key] += 1
    
    from telethon import types, functions
    from telethon.errors import ChatAdminRequiredError, UserAdminInvalidError, FloodWaitError
    import asyncio


    async def _promote_to_admin(self, entity: MainEntity, telegram_id: int, bot_id: int) -> bool:
        """Назначает бота администратором с диагностикой прав и типа сообщества"""
        try:
            peer = await ensure_peer(
                self.promoter_client,
                telegram_id=entity.telegram_id,
                link=entity.link
            )

            if not peer:
                log.error(f"❌ Не удалось получить peer для {entity.name}")
                return False

            # ───────────────────────────────
            # 1. Получаем сущность чата
            # ───────────────────────────────
            chat = await self.promoter_client.get_entity(peer)

            # Определяем тип
            if isinstance(chat, types.Channel):
                if chat.megagroup:
                    chat_type = "Супергруппа"
                else:
                    chat_type = "Канал"
            elif isinstance(chat, types.Chat):
                chat_type = "Обычная группа"
            else:
                chat_type = f"Неизвестный тип ({type(chat)})"

            log.info(f"📌 Сообщество: {entity.name}")
            log.info(f"📌 Тип сообщества: {chat_type}")

            # ───────────────────────────────
            # 2. Получаем права promoter_client
            # ───────────────────────────────
            my_perms = None

            if isinstance(chat, types.Channel):
                full = await self.promoter_client(
                    functions.channels.GetParticipantRequest(
                        channel=chat,
                        participant='me'
                    )
                )

                participant = full.participant

                if isinstance(participant, types.ChannelParticipantCreator):
                    log.info("👑 Текущий аккаунт — СОЗДАТЕЛЬ")
                    my_perms = None  # создатель может всё
                elif isinstance(participant, types.ChannelParticipantAdmin):
                    my_perms = participant.admin_rights
                    log.info(f"🛂 Права текущего аккаунта: {my_perms}")
                else:
                    log.error("❌ Текущий аккаунт НЕ администратор")
                    return False

            elif isinstance(chat, types.Chat):
                # В обычных группах нет тонких прав
                log.info("ℹ В обычной группе права админов бинарные (is_admin)")
                my_perms = None

            # ───────────────────────────────
            # 3. Формируем допустимые права
            # ───────────────────────────────
            def can(flag: str) -> bool:
                """Можно ли выдать право (если мы не создатель)"""
                if my_perms is None:
                    return True
                return getattr(my_perms, flag, False)

            admin_rights = types.ChatAdminRights(
                change_info=can("change_info"),
                delete_messages=can("delete_messages"),
                ban_users=can("ban_users"),
                invite_users=can("invite_users"),
                pin_messages=can("pin_messages"),

                # Только для каналов
                post_messages=can("post_messages") if isinstance(chat, types.Channel) and not chat.megagroup else False,
                edit_messages=can("edit_messages") if isinstance(chat, types.Channel) and not chat.megagroup else False,

                # Только для супергрупп
                anonymous=can("anonymous") if isinstance(chat, types.Channel) and chat.megagroup else False,
                manage_call=can("manage_call") if isinstance(chat, types.Channel) and chat.megagroup else False,

                # Никогда не даём
                add_admins=False
            )

            log.info(f"🧩 Назначаемые права: {admin_rights}")

            # ───────────────────────────────
            # 4. Назначаем админа
            # ───────────────────────────────
            if isinstance(chat, types.Chat):
                # обычная группа
                await self.promoter_client(
                    functions.messages.EditChatAdmin(
                        chat_id=chat.id,
                        user_id=telegram_id,
                        is_admin=True
                    )
                )
            else:
                # канал / супергруппа
                await self.promoter_client(
                    functions.channels.EditAdminRequest(
                        channel=chat,
                        user_id=telegram_id,
                        admin_rights=admin_rights,
                        rank=f"Bot_{bot_id}"
                    )
                )

            log.info(f"✅ Бот #{bot_id} назначен админом в {entity.name}")
            return True

        except FloodWaitError as e:
            log.warning(f"⏳ Flood wait {e.seconds} секунд для {entity.name}")
            await asyncio.sleep(e.seconds)
            return False

        except (ChatAdminRequiredError, UserAdminInvalidError) as e:
            log.error(f"❌ Нет прав для назначения админа в {entity.name}: {e}")
            return False

        except Exception as e:
            log.exception(f"🔥 Критическая ошибка назначения бота #{bot_id} в {entity.name}")
            return False

    
    async def process_all_entities(self):
        """Обрабатывает все сущности с фильтрацией по owner"""
        # Загружаем только сущности с owner='own' или без owner
        with get_session() as session:
            stmt = select(MainEntity).where(
                or_(
                    MainEntity.owner == OWNER_FILTER,
                    MainEntity.owner == None,
                    MainEntity.owner == ""
                )
            )
            entities = session.execute(stmt).scalars().all()
        
        if not entities:
            log.warning(f"⚠️ Нет сущностей с owner='{OWNER_FILTER}' для обработки")
            return
        
        log.info(f"🔍 Обработка {len(entities)} сущностей с owner='{OWNER_FILTER}'...")
        
        # Сначала обрабатываем сущности, где промоутер уже админ
        admin_entities = []
        non_admin_entities = []
        
        for entity in entities:
            if entity.id in self.admin_entities:
                admin_entities.append(entity)
            else:
                non_admin_entities.append(entity)
        
        log.info(f"📊 Статистика: админ в {len(admin_entities)}, не админ в {len(non_admin_entities)}")
        
        # Параллельная обработка сущностей с админскими правами
        if admin_entities:
            log.info(f"🚀 Параллельная обработка {len(admin_entities)} сущностей где промоутер админ...")
            tasks = []
            for entity in admin_entities:
                tasks.append(self.process_entity(entity))
            
            # Ограничиваем параллелизм 5 задачами
            for i in range(0, len(tasks), 5):
                batch = tasks[i:i+5]
                results = await asyncio.gather(*batch, return_exceptions=True)
                
                # Логируем ошибки
                for j, result in enumerate(results):
                    if isinstance(result, Exception):
                        entity = admin_entities[i + j]
                        log.error(f"❌ Ошибка обработки {entity.name}: {result}")
                
                await asyncio.sleep(5)  # Пауза между батчами
        
        # Обрабатываем сущности без админских прав (с задержкой)
        if non_admin_entities:
            log.info(f"🔗 Обрабатываем {len(non_admin_entities)} сущностей без прав админа...")
            # Ограничиваем 5 попытками за цикл
            attempts = min(5, len(non_admin_entities))
            for i in range(attempts):
                if self.running and i < len(non_admin_entities):
                    entity = non_admin_entities[i]
                    await self.process_entity(entity)
                    await asyncio.sleep(5)  # Большая задержка для присоединения
    
    async def process_commands(self):
        """Обрабатывает команды из файла управления"""
        commands = self.command_handler.get_pending_commands()
        
        for command in commands:
            try:
                result = await self.execute_command(command)
                self.command_handler.mark_command_completed(command["id"], result)
                log.info(f"✅ Команда #{command['id']} выполнена: {result}")
            except Exception as e:
                log.error(f"❌ Ошибка выполнения команды #{command['id']}: {e}")
                self.command_handler.mark_command_completed(command["id"], f"error: {str(e)}")
    
    async def execute_command(self, command: dict) -> str:
        """Выполняет одну команду"""
        command_type = command["type"]
        data = command["data"]
        
        if command_type == "promote":
            return await self._execute_promote(
                data["entity_id"],
                data["bot_id"]
            )
        elif command_type == "demote":
            return await self._execute_demote(
                data["entity_id"],
                data["bot_id"]
            )
        elif command_type == "leave":
            return await self._execute_leave(
                data["entity_id"],
                data["bot_id"]
            )
        else:
            return f"unknown command type: {command_type}"
    
    async def _execute_promote(self, entity_id: int, bot_id: int) -> str:
        """Выполняет команду назначения администратором"""
        with get_session() as session:
            entity = session.get(MainEntity, entity_id)
            bot = session.get(BotSession, bot_id)
            
            if not entity:
                return f"entity {entity_id} not found"
            if not bot:
                return f"bot {bot_id} not found"
        
        # Проверяем, принадлежит ли сущность "своим" (owner='own')
        if entity.owner != OWNER_FILTER and entity.owner not in [None, ""]:
            return f"entity {entity.name} is not owned by '{OWNER_FILTER}' (owner: {entity.owner})"
        
        # Проверяем telegram_id бота
        bot_telegram_id = None
        if bot.telegram_info and 'id' in bot.telegram_info:
            bot_telegram_id = bot.telegram_info['id']
        else:
            # Пытаемся обновить информацию
            await self._update_bot_info(bot)
            if bot.telegram_info and 'id' in bot.telegram_info:
                bot_telegram_id = bot.telegram_info['id']
        
        if not bot_telegram_id:
            return f"bot {bot_id} has no telegram id"
        
        # Проверяем, состоит ли промоутер в сущности и является ли админом
        if entity.id not in self.admin_entities:
            if entity.id in self.joined_entities:
                return f"promoter in {entity.name} but not admin"
            else:
                # Пытаемся присоединиться
                if await self._join_entity(entity):
                    self.joined_entities.add(entity.id)
                    # Проверяем права после присоединения
                    await self._update_entity_admin_status(entity)
                    if entity.id not in self.admin_entities:
                        return f"promoter joined {entity.name} but not admin"
                else:
                    return f"promoter not in entity {entity.name} and cannot join"
        
        # Назначаем администратором
        success = await self._promote_to_admin(entity, bot_telegram_id, bot_id)
        
        if success:
            # Обновляем кэш
            if bot_id in self.bots_cache:
                self.bots_cache[bot_id]['telegram_id'] = bot_telegram_id
            return f"bot {bot_id} promoted in {entity.name} (owner: {entity.owner})"
        else:
            return f"failed to promote bot {bot_id} in {entity.name} (owner: {entity.owner})"
    
    async def _execute_demote(self, entity_id: int, bot_id: int) -> str:
        """Выполняет команду снятия с администратора"""
        with get_session() as session:
            entity = session.get(MainEntity, entity_id)
            bot = session.get(BotSession, bot_id)
            
            if not entity:
                return f"entity {entity_id} not found"
            if not bot:
                return f"bot {bot_id} not found"
        
        # Проверяем принадлежность
        if entity.owner != OWNER_FILTER and entity.owner not in [None, ""]:
            return f"entity {entity.name} is not owned by '{OWNER_FILTER}' (owner: {entity.owner})"
        
        # Получаем Telegram ID бота
        bot_telegram_id = None
        if bot.telegram_info and 'id' in bot.telegram_info:
            bot_telegram_id = bot.telegram_info['id']
        
        if not bot_telegram_id:
            return f"bot {bot_id} has no telegram id"
        
        # Проверяем, является ли промоутер админом в сущности
        if entity.id not in self.admin_entities:
            return f"promoter not admin in {entity.name}"
        
        try:
            peer = await ensure_peer(
                self.promoter_client,
                telegram_id=entity.telegram_id,
                link=entity.link
            )
            
            if not peer:
                return f"cannot resolve entity {entity.name}"
            
            # Снимаем права администратора
            await self.promoter_client(
                functions.channels.EditAdminRequest(
                    channel=peer,
                    user_id=bot_telegram_id,
                    admin_rights=types.ChatAdminRights(),  # Пустые права
                    rank=""
                )
            )
            
            return f"bot {bot_id} demoted in {entity.name} (owner: {entity.owner})"
            
        except Exception as e:
            return f"error demoting bot {bot_id}: {str(e)}"
    
    async def _execute_leave(self, entity_id: int, bot_id: int) -> str:
        """Выполняет команду выхода из сущности"""
        with get_session() as session:
            entity = session.get(MainEntity, entity_id)
            bot = session.get(BotSession, bot_id)
            
            if not entity:
                return f"entity {entity_id} not found"
            if not bot:
                return f"bot {bot_id} not found"
        
        # Проверяем принадлежность
        if entity.owner != OWNER_FILTER and entity.owner not in [None, ""]:
            return f"entity {entity.name} is not owned by '{OWNER_FILTER}' (owner: {entity.owner})"
        
        # Создаем временный клиент для этого бота
        temp_client = None
        try:
            temp_client = init_user_client(bot)
            await temp_client.start()
            
            if not await temp_client.is_user_authorized():
                await temp_client.disconnect()
                return f"bot {bot_id} not authorized"
            
            peer = await ensure_peer(
                temp_client,
                telegram_id=entity.telegram_id,
                link=entity.link
            )
            
            if not peer:
                await temp_client.disconnect()
                return f"cannot resolve entity {entity.name}"
            
            # Выходим из сущности
            await temp_client(functions.channels.LeaveChannelRequest(peer))
            
            await temp_client.disconnect()
            
            return f"bot {bot_id} left {entity.name} (owner: {entity.owner})"
            
        except Exception as e:
            if temp_client:
                try:
                    await temp_client.disconnect()
                except:
                    pass
            return f"error leaving entity {entity.name}: {str(e)}"
    
    async def periodic_cache_update(self):
        """Периодическое обновление кэшей"""
        while self.running:
            try:
                await asyncio.sleep(3600)  # Каждый час
                
                log.info("🔄 Периодическое обновление кэшей...")
                
                # Обновляем кэш ботов
                await self._update_bots_cache()
                
                # Обновляем информацию о сущностях
                await self._load_promoter_entities()
                
                log.info("✅ Кэши обновлены")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"❌ Ошибка обновления кэшей: {e}")
    
    async def check_and_reconnect(self):
        """Проверяет и переподключает клиента при необходимости"""
        try:
            if not await self.promoter_client.is_user_authorized():
                log.warning("⚠️ Промоутер не авторизован, переподключаем...")
                
                with get_session() as session:
                    bot = session.get(BotSession, self.promoter_bot_id)
                    if bot:
                        try:
                            await self.promoter_client.disconnect()
                        except:
                            pass
                        
                        self.promoter_client = init_user_client(bot)
                        await self.promoter_client.start()
                        
                        if await self.promoter_client.is_user_authorized():
                            log.info("✅ Промоутер переподключен")
                        else:
                            log.error("❌ Не удалось переподключить промоутера")
        except Exception as e:
            log.error(f"❌ Ошибка проверки соединения промоутера: {e}")
    
    async def cleanup(self):
        """Очистка ресурсов"""
        self.running = False
        
        if self.promoter_client:
            try:
                await self.promoter_client.disconnect()
            except Exception:
                pass
    
    async def run(self):
        """Основной цикл работы"""
        await self.initialize()
        self.running = True
        
        log.info(f"✅ AdminPromoter запущен с главным ботом #{self.promoter_bot_id}")
        log.info(f"🔍 Обрабатываются сущности с owner='{OWNER_FILTER}'")
        
        # Запускаем фоновую задачу обновления кэшей
        cache_task = asyncio.create_task(self.periodic_cache_update())
        
        cycle_count = 0
        while self.running:
            try:
                cycle_count += 1
                
                log.info(f"🔄 Цикл #{cycle_count}")
                
                # Основная обработка
                await self.process_all_entities()
                
                # Обработка команд управления
                await self.process_commands()
                
                # Проверка соединения каждые 10 циклов
                if cycle_count % 10 == 0:
                    await self.check_and_reconnect()
                
                # Пауза между циклами
                log.info(f"⏳ Ожидание {CHECK_INTERVAL} секунд до следующего цикла...")
                await asyncio.sleep(CHECK_INTERVAL)
                
            except KeyboardInterrupt:
                log.info("🛑 Остановка по Ctrl+C")
                break
            except Exception as e:
                log.error(f"❌ Ошибка в основном цикле: {e}")
                await asyncio.sleep(60)
        
        # Останавливаем фоновую задачу
        cache_task.cancel()
        try:
            await cache_task
        except asyncio.CancelledError:
            pass


class CommandHandler:
    """Обработчик команд управления"""
    
    def __init__(self, command_file: Path = COMMAND_FILE):
        self.command_file = command_file
        self.command_file.parent.mkdir(parents=True, exist_ok=True)
        
    def load_commands(self) -> List[dict]:
        """Загружает команды из файла"""
        try:
            if not self.command_file.exists():
                return []
            
            with open(self.command_file, 'r', encoding='utf-8') as f:
                commands = json.load(f)
            
            return commands
        except Exception as e:
            log.error(f"❌ Ошибка загрузки команд: {e}")
            return []
    
    def save_commands(self, commands: List[dict]):
        """Сохраняет команды в файл"""
        try:
            with open(self.command_file, 'w', encoding='utf-8') as f:
                json.dump(commands, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log.error(f"❌ Ошибка сохранения команд: {e}")
    
    def add_command(self, command_type: str, **kwargs):
        """Добавляет новую команду"""
        commands = self.load_commands()
        
        command = {
            "id": len(commands) + 1,
            "type": command_type,
            "data": kwargs,
            "created_at": datetime.now().isoformat(),
            "status": "pending"
        }
        
        commands.append(command)
        self.save_commands(commands)
        log.info(f"📝 Добавлена команда {command_type}: {kwargs}")
    
    def get_pending_commands(self) -> List[dict]:
        """Возвращает ожидающие выполнения команды"""
        commands = self.load_commands()
        return [cmd for cmd in commands if cmd["status"] == "pending"]
    
    def mark_command_completed(self, command_id: int, result: str = "completed"):
        """Помечает команду как выполненную"""
        commands = self.load_commands()
        
        for cmd in commands:
            if cmd["id"] == command_id:
                cmd["status"] = "completed"
                cmd["completed_at"] = datetime.now().isoformat()
                cmd["result"] = result
                break
        
        self.save_commands(commands)


# Глобальный экземпляр
promoter = AdminPromoter()


async def run_admin_promoter():
    """Запуск основного цикла"""
    log.info("🚀 Модуль AdminPromoter запускается...")
    
    try:
        await promoter.run()
    except Exception as e:
        log.error(f"💥 Критическая ошибка в AdminPromoter: {e}")
    finally:
        await promoter.cleanup()
        log.info("🛑 AdminPromoter остановлен")