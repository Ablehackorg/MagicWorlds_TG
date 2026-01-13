# blondinka_manager.py

import os
import asyncio
import logging
import random
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple
import pytz
from sqlalchemy import select, and_
from sqlalchemy.orm import joinedload
from telethon import TelegramClient
from telethon.tl.types import Message

from utils.db_utils import get_session
from telegram_client import init_user_client
from entity_resolver import ensure_peer
from models import (
    BlondinkaTask, BlondinkaSchedule, BlondinkaDialog, GroupTheme, 
    BlondinkaLog, MainEntity, BotSession, Country, EntityCategory,
    BlondinkaTaskDialog
)

# Настройка логирования
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                   format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("blondinka")

# Константы
CHECK_INTERVAL = int(os.getenv("BLONDINKA_CHECK_INTERVAL", "60"))  # проверка каждую минуту
TZ = pytz.timezone(os.getenv("TZ", "Europe/Moscow"))
UTC_TZ = pytz.UTC

def get_entity_timezone(entity: MainEntity):
    """Возвращает часовой пояс для сущности"""
    try:
        if entity and hasattr(entity, "country") and entity.country and entity.country.time_zone_delta is not None:
            delta = entity.country.time_zone_delta
            return pytz.FixedOffset(int(delta * 60))
        return TZ
    except Exception as e:
        log.warning(f"⚠️ Ошибка получения часового пояса для сущности {entity.name if entity else 'unknown'}: {e}")
        return TZ

class PostPublisher:
    """Класс для публикации постов"""
    
    def __init__(self, task: BlondinkaTask, client: TelegramClient):
        self.task = task
        self.client = client
        self.group_entity = None
        self.group_timezone = TZ
        self.theme_url = None
        
    async def initialize(self):
        """Инициализация подключения к группе"""
        try:
            self.group_entity = await ensure_peer(
                self.client, 
                telegram_id=self.task.group.telegram_id,
                link=self.task.group.link
            )
            self.group_timezone = get_entity_timezone(self.task.group)
            
            # Получаем URL темы из связи Entity-Category
            self.theme_url = self._get_theme_url()
            
            log.info(f"✅ Инициализирован публикатор для задачи #{self.task.id} (группа: {self.task.group.name})")
            return True
        except Exception as e:
            log.error(f"❌ Ошибка инициализации публикатора для задачи #{self.task.id}: {e}")
            return False
    
    def _get_theme_url(self) -> Optional[str]:
        """Получает URL темы из связи Entity-Category"""
        try:
            # Проверяем, есть ли у темы связанная категория
            if not self.task.group_theme or not self.task.group_theme.category_id:
                log.warning(f"⚠️ У темы #{self.task.group_theme_id} нет связанной категории")
                return None
            
            # Ищем связь между группой и категорией темы
            with get_session() as session:
                stmt = select(EntityCategory).where(
                    and_(
                        EntityCategory.entity_id == self.task.group_id,
                        EntityCategory.category_id == self.task.group_theme.category_id
                    )
                )
                entity_category_link = session.execute(stmt).scalar_one_or_none()
                
                if entity_category_link and entity_category_link.theme_url:
                    log.info(f"🔗 Найден URL темы для группы {self.task.group.name}: {entity_category_link.theme_url}")
                    return entity_category_link.theme_url
                else:
                    log.warning(f"⚠️ Не найден URL темы для связи группа #{self.task.group_id} - категория #{self.task.group_theme.category_id}")
                    return None
                    
        except Exception as e:
            log.error(f"❌ Ошибка получения URL темы для задачи #{self.task.id}: {e}")
            return None
        
    def get_random_message(self) -> Optional[str]:
        """Получает случайное активное сообщение из темы через промежуточную таблицу"""
        try:
            with get_session() as session:
                # Получаем все активные связи задачи с диалогами
                stmt = select(BlondinkaTaskDialog).where(
                    and_(
                        BlondinkaTaskDialog.task_id == self.task.id,
                        BlondinkaTaskDialog.is_active == True
                    )
                ).options(
                    joinedload(BlondinkaTaskDialog.dialog)
                )
                task_dialogs = session.execute(stmt).scalars().all()
                
                if not task_dialogs:
                    log.warning(f"⚠️ Нет активных диалогов для задачи #{self.task.id}")
                    return None
                
                # Фильтруем только активные диалоги
                active_dialogs = []
                for task_dialog in task_dialogs:
                    if (task_dialog.dialog and 
                        task_dialog.dialog.is_active and 
                        task_dialog.dialog.theme_id == self.task.group_theme_id):
                        active_dialogs.append(task_dialog.dialog)
                
                if not active_dialogs:
                    log.warning(f"⚠️ Нет активных диалогов для темы #{self.task.group_theme_id} в задаче #{self.task.id}")
                    return None
                
                # Случайный выбор из активных диалогов
                dialog = random.choice(active_dialogs)
                log.info(f"📝 Выбрано сообщение из темы '{self.task.group_theme.name}' для задачи #{self.task.id}")
                return dialog.message
                
        except Exception as e:
            log.error(f"❌ Ошибка выбора сообщения для задачи #{self.task.id}: {e}")
            return None
    
    async def publish_post(self) -> Tuple[bool, Optional[Message], str]:
        """Публикует пост в группе"""
        message_text = self.get_random_message()
        if not message_text:
            return False, None, "Не удалось выбрать сообщение для публикации"
        
        try:
            # Получаем информацию о группе для правильного определения типа
            group_info = await self.client.get_entity(self.group_entity)
            
            # Определяем, является ли группа супергруппой с темами
            is_supergroup = hasattr(group_info, 'megagroup') and group_info.megagroup
            
            # Определяем, куда публиковать - в тему супергруппы или обычный чат
            if self.theme_url and is_supergroup:
                # Публикуем в тему супергруппы
                topic_id = self._get_topic_id()
                if topic_id:
                    try:
                        message = await self.client.send_message(
                            self.group_entity,
                            message_text,
                            reply_to=topic_id
                        )
                        log.info(f"📤 Опубликован пост в теме группы {self.task.group.name}")
                        return True, message, "Успешно опубликовано в теме"
                    except Exception as topic_error:
                        log.warning(f"⚠️ Не удалось опубликовать в тему, пробуем обычную публикацию: {topic_error}")
                        # Пробуем обычную публикацию как fallback
                        message = await self.client.send_message(
                            self.group_entity,
                            message_text
                        )
                        log.info(f"📤 Опубликован пост в группе {self.task.group.name} (обычный чат)")
                        return True, message, "Успешно опубликовано в обычный чат"
                else:
                    # Если не удалось получить topic_id, публикуем в обычный чат
                    message = await self.client.send_message(
                        self.group_entity,
                        message_text
                    )
                    log.info(f"📤 Опубликован пост в группе {self.task.group.name} (не удалось определить тему)")
                    return True, message, "Успешно опубликовано (тема не определена)"
            else:
                # Публикуем в обычную группу/канал
                message = await self.client.send_message(
                    self.group_entity,
                    message_text
                )
                log.info(f"📤 Опубликован пост в группе {self.task.group.name}")
                return True, message, "Успешно опубликовано"
            
        except Exception as e:
            error_msg = f"Ошибка публикации: {str(e)}"
            log.error(f"❌ {error_msg} в группе {self.task.group.name}")
            return False, None, error_msg
    
    def _get_topic_id(self) -> Optional[int]:
        """Получает ID темы из URL (если применимо)"""
        if not self.theme_url:
            return None
        
        try:
            # Парсим ID темы из различных форматов URL
            if 't.me/c/' in self.theme_url:
                # Формат: https://t.me/c/chat_id/topic_id
                parts = self.theme_url.split('/')
                if len(parts) >= 6:
                    topic_id_str = parts[5]
                    if topic_id_str.isdigit():
                        topic_id = int(topic_id_str)
                        log.info(f"🔗 Извлечен ID темы из URL: {topic_id}")
                        return topic_id
            
            elif 't.me/' in self.theme_url and '?topic=' in self.theme_url:
                # Формат: https://t.me/username?topic=123
                import urllib.parse
                parsed_url = urllib.parse.urlparse(self.theme_url)
                query_params = urllib.parse.parse_qs(parsed_url.query)
                if 'topic' in query_params:
                    topic_id_str = query_params['topic'][0]
                    if topic_id_str.isdigit():
                        topic_id = int(topic_id_str)
                        log.info(f"🔗 Извлечен ID темы из query параметра: {topic_id}")
                        return topic_id
            
            # Пробуем извлечь из конца URL
            parts = self.theme_url.split('/')
            last_part = parts[-1]
            if last_part.isdigit():
                topic_id = int(last_part)
                log.info(f"🔗 Извлечен ID темы из последней части URL: {topic_id}")
                return topic_id
                
        except Exception as e:
            log.warning(f"⚠️ Не удалось извлечь ID темы из URL: {self.theme_url}, ошибка: {e}")
        
        log.warning(f"⚠️ Не удалось определить ID темы из URL: {self.theme_url}")
        return None
    
    def get_post_url(self, message: Message) -> str:
        """Формирует ссылку на пост"""
        if self.theme_url:
            # Для постов в теме используем URL темы
            return self.theme_url
        else:
            # Для обычных постов формируем стандартную ссылку
            chat_id = abs(self.task.group.telegram_id)
            return f"https://t.me/c/{chat_id}/{message.id}"

class ScheduledPost:
    """Класс для отслеживания запланированных постов"""
    
    def __init__(self, message: Message, publisher: PostPublisher, delete_after_hours: Optional[int]):
        self.message = message
        self.publisher = publisher
        self.delete_after_hours = delete_after_hours
        self.published_at = datetime.now(publisher.group_timezone)
        self.should_delete_at = None
        
        if delete_after_hours:
            self.should_delete_at = self.published_at + timedelta(hours=delete_after_hours)
            log.info(f"⏰ Пост запланирован к удалению через {delete_after_hours} часов (в {self.should_delete_at})")
    
    async def check_and_delete(self) -> bool:
        """Проверяет и удаляет пост если пришло время"""
        if not self.delete_after_hours or not self.should_delete_at:
            return False
            
        current_time = datetime.now(self.publisher.group_timezone)
        if current_time >= self.should_delete_at:
            try:
                await self.publisher.client.delete_messages(
                    self.publisher.group_entity,
                    [self.message.id]
                )
                log.info(f"🗑️ Пост удален (прошло >= {self.delete_after_hours} часов)")
                return True
            except Exception as e:
                log.error(f"❌ Ошибка удаления поста: {e}")
                return False
        
        return False

class BlondinkaTaskTracker:
    """Трекер для управления одной задачей блондинки"""
    
    def __init__(self, task: BlondinkaTask, client: TelegramClient):
        self.task = task
        self.client = client
        self.publisher = PostPublisher(task, client)
        self.scheduled_posts: List[ScheduledPost] = []
        self.group_timezone = TZ
        self.last_processed_day = None
        self.last_run_now_time = None  # Время последнего запуска по run_now
        
    async def initialize(self):
        """Инициализация трекера"""
        if not await self.publisher.initialize():
            return False
            
        self.group_timezone = self.publisher.group_timezone
        log.info(f"✅ Трекер инициализирован для задачи #{self.task.id}")
        return True
    
    def is_active_day(self) -> bool:
        """Проверяет, активен ли сегодняшний день для публикации"""
        current_time = datetime.now(self.group_timezone)
        current_weekday = current_time.weekday()  # 0-понедельник, 6-воскресенье
        
        # Проверяем рабочие дни задачи
        working_days = self.task.working_days or []
        if current_weekday not in working_days:
            return False
            
        return True
    
    def should_publish_now(self) -> bool:
        """Проверяет, нужно ли публиковать пост сейчас по расписанию"""
        current_time = datetime.now(self.group_timezone)
        
        # ПЕРВЫЙ ПРИОРИТЕТ: если установлен флаг run_now
        try:
            # Получаем актуальное значение из базы
            with get_session() as session:
                db_task = session.query(BlondinkaTask).get(self.task.id)
                if db_task and db_task.run_now:
                    log.info(f"🚀 Флаг 'run_now' активирован для задачи #{self.task.id}")
                    
                    # Проверяем, не запускали ли мы уже run_now в течение последних 30 секунд
                    if self.last_run_now_time:
                        time_diff = (current_time - self.last_run_now_time).total_seconds()
                        if time_diff < 30:  # менее 30 секунд
                            log.info(f"⏸️ Run_now уже был запущен {time_diff:.0f} секунд назад, пропускаем")
                            return False
                    
                    return True
        except Exception as e:
            log.warning(f"⚠️ Ошибка проверки флага 'run_now': {e}")
            # В случае ошибки используем локальное значение
            if self.task.run_now:
                log.info(f"🚀 Флаг 'run_now' (локальный) активирован для задачи #{self.task.id}")
                
                # Та же проверка для локального значения
                if self.last_run_now_time:
                    time_diff = (current_time - self.last_run_now_time).total_seconds()
                    if time_diff < 30:
                        log.info(f"⏸️ Run_now (локальный) уже был запущен {time_diff:.0f} секунд назад, пропускаем")
                        return False
                
                return True
        
        # Далее обычная логика
        if not self.is_active_day():
            return False
            
        current_day = current_time.date()
        
        # Проверяем, не обрабатывали ли мы уже сегодня публикации
        if self.last_processed_day == current_day:
            return False
            
        current_time_only = current_time.time()
        
        # Проверяем все активные расписания на сегодня
        with get_session() as session:
            stmt = select(BlondinkaSchedule).where(
                and_(
                    BlondinkaSchedule.task_id == self.task.id,
                    BlondinkaSchedule.day_of_week == current_time.weekday(),
                    BlondinkaSchedule.is_active == True
                )
            )
            schedules = session.execute(stmt).scalars().all()
            
            for schedule in schedules:
                schedule_time = schedule.publish_time
                # Проверяем совпадение времени с допуском +/- 1 минута
                time_diff = abs((current_time_only.hour * 60 + current_time_only.minute) - 
                               (schedule_time.hour * 60 + schedule_time.minute))
                if time_diff <= 1:
                    log.info(f"⏰ Найдено подходящее расписание: {schedule_time} для задачи #{self.task.id}")
                    return True
        
        return False
    
    async def process_publication(self):
        """Обрабатывает публикацию поста по расписанию"""
        if not self.should_publish_now():
            return
            
        current_time = datetime.now(self.group_timezone)
        current_day = current_time.date()
        
        try:
            # Определяем, был ли это запуск по run_now
            is_run_now = False
            try:
                with get_session() as session:
                    db_task = session.query(BlondinkaTask).get(self.task.id)
                    if db_task and db_task.run_now:
                        is_run_now = True
            except:
                if self.task.run_now:
                    is_run_now = True
            
            # Если это был запуск по флагу run_now - сбрасываем его в БД
            if is_run_now:
                try:
                    with get_session() as session:
                        db_task = session.query(BlondinkaTask).get(self.task.id)
                        if db_task and db_task.run_now:
                            db_task.run_now = False
                            session.commit()
                            log.info(f"🔄 Сброшен флаг 'run_now' для задачи #{self.task.id}")
                            # Обновляем локальный объект
                            self.task.run_now = False
                except Exception as e:
                    log.error(f"❌ Ошибка сброса флага 'run_now': {e}")
                    # Продолжаем выполнение даже если не удалось сбросить флаг
                
                # Запоминаем время запуска run_now
                self.last_run_now_time = current_time
            
            # Публикуем пост
            success, message, result_message = await self.publisher.publish_post()
            
            # Логируем результат
            await self._log_publication(success, result_message, message)
            
            if success and message:
                # Создаем отслеживаемый пост для возможного удаления
                scheduled_post = ScheduledPost(
                    message=message,
                    publisher=self.publisher,
                    delete_after_hours=self.task.delete_post_after
                )
                self.scheduled_posts.append(scheduled_post)
                
                # Ограничиваем количество отслеживаемых постов (оставляем последние 100)
                if len(self.scheduled_posts) > 100:
                    self.scheduled_posts = self.scheduled_posts[-100:]
            
            # Помечаем день как обработанный (если это не run_now)
            if not is_run_now:
                self.last_processed_day = current_day
            
        except Exception as e:
            error_msg = f"Ошибка в процессе публикации: {str(e)}"
            log.error(f"❌ {error_msg}")
            await self._log_publication(False, error_msg, None)
    
    async def process_deletions(self):
        """Обрабатывает удаление постов, у которых истекло время"""
        posts_to_remove = []
        
        for i, scheduled_post in enumerate(self.scheduled_posts):
            try:
                deleted = await scheduled_post.check_and_delete()
                if deleted:
                    posts_to_remove.append(i)
                    await self._log_deletion(scheduled_post.message, True, "Успешно удален по расписанию")
            except Exception as e:
                log.error(f"❌ Ошибка при проверке удаления поста: {e}")
                # Если ошибка постоянная, удаляем пост из отслеживания
                posts_to_remove.append(i)
                await self._log_deletion(scheduled_post.message, False, f"Ошибка удаления: {str(e)}")
        
        # Удаляем обработанные посты из списка
        for i in sorted(posts_to_remove, reverse=True):
            if i < len(self.scheduled_posts):
                self.scheduled_posts.pop(i)
    
    async def _log_publication(self, success: bool, result_message: str, message: Optional[Message]):
        """Логирует результат публикации"""
        try:
            with get_session() as session:
                post_content = ""
                post_url = ""
                
                if message and hasattr(message, 'text'):
                    post_content = message.text
                    post_url = self.publisher.get_post_url(message)
                
                log_entry = BlondinkaLog(
                    task_id=self.task.id,
                    post_content=post_content,
                    post_url=post_url if success else None,
                    is_success=success,
                    error_message=result_message if not success else None
                )
                session.add(log_entry)
                session.commit()
                
                log_level = "INFO" if success else "ERROR"
                log.log(getattr(logging, log_level), 
                       f"{'✅' if success else '❌'} Лог публикации для задачи #{self.task.id}: {result_message}")
                       
        except Exception as e:
            log.error(f"❌ Ошибка записи лога публикации для задачи #{self.task.id}: {e}")
    
    async def _log_deletion(self, message: Message, success: bool, result_message: str):
        """Логирует результат удаления"""
        try:
            with get_session() as session:
                log_entry = BlondinkaLog(
                    task_id=self.task.id,
                    post_content=message.text if hasattr(message, 'text') else "",
                    post_url=self.publisher.get_post_url(message),
                    is_success=success,
                    error_message=result_message if not success else None
                )
                session.add(log_entry)
                session.commit()
                
                log_level = "INFO" if success else "ERROR"
                log.log(getattr(logging, log_level),
                       f"{'✅' if success else '❌'} Лог удаления для задачи #{self.task.id}: {result_message}")
                       
        except Exception as e:
            log.error(f"❌ Ошибка записи лога удаления для задачи #{self.task.id}: {e}")
    
    async def cleanup(self):
        """Очистка ресурсов"""
        self.scheduled_posts.clear()

class BlondinkaManager:
    """Менеджер для управления всеми задачами блондинки"""
    
    def __init__(self):
        self.trackers: Dict[int, BlondinkaTaskTracker] = {}
        self.clients: Dict[int, TelegramClient] = {}
        self.running = False
    
    async def initialize(self):
        """Инициализация менеджера"""
        log.info("🔄 Инициализация менеджера блондинки...")
        await self._load_tasks()
    
    async def _load_tasks(self):
        """Загружает активные задачи из БД - исправленная версия"""
        try:
            with get_session() as session:
                # Загружаем задачи со всеми необходимыми связями
                stmt = select(BlondinkaTask).where(
                    BlondinkaTask.is_active == True
                ).options(
                    joinedload(BlondinkaTask.bot),
                    joinedload(BlondinkaTask.group).joinedload(MainEntity.country),  # Загружаем country для группы
                    joinedload(BlondinkaTask.group_theme).joinedload(GroupTheme.category),  # Загружаем category для темы
                    joinedload(BlondinkaTask.task_dialogs).joinedload(BlondinkaTaskDialog.dialog)  # Загружаем диалоги
                )
                tasks = session.execute(stmt).unique().scalars().all()
                
        except Exception as e:
            log.error(f"❌ Ошибка загрузки задач блондинки: {e}")
            tasks = []
        
        if not tasks:
            log.info("🔍 Нет активных задач блондинки")
            return
        
        log.info(f"🔍 Загружено {len(tasks)} активных задач блондинки")
        
        bot_ids = sorted(set(t.bot_id for t in tasks))
        
        # Инициализация клиентов
        for bot_id in bot_ids:
            if bot_id not in self.clients:
                try:
                    with get_session() as session:
                        stmt = select(BotSession).where(BotSession.id == bot_id)
                        bot = session.execute(stmt).scalar_one_or_none()
                        if not bot:
                            log.error(f"❌ Бот #{bot_id} не найден в базе данных")
                            continue
                        
                        client = init_user_client(bot)
                        await client.start()
                        if not await client.is_user_authorized():
                            raise RuntimeError(f"Бот #{bot_id} не авторизован")
                        
                        self.clients[bot_id] = client
                        log.info(f"✅ Бот #{bot_id} авторизован для блондинки")
                except Exception as e:
                    log.error(f"❌ Ошибка инициализации бота #{bot_id} для блондинки: {e}")
        
        # Создание трекеров
        for task in tasks:
            client = self.clients.get(task.bot_id)
            if client and task.id not in self.trackers:
                tracker = BlondinkaTaskTracker(task, client)
                if await tracker.initialize():
                    self.trackers[task.id] = tracker
                    log.info(f"✅ Трекер создан для задачи #{task.id} (группа: {task.group.name})")
        
    async def check_for_updates(self):
        """Проверяет обновления в БД и обновляет трекеры"""
        try:
            with get_session() as session:
                stmt = select(BlondinkaTask).where(
                    BlondinkaTask.is_active == True
                ).options(
                    joinedload(BlondinkaTask.bot),
                    joinedload(BlondinkaTask.group).joinedload(MainEntity.country),
                    joinedload(BlondinkaTask.group_theme).joinedload(GroupTheme.category),
                    joinedload(BlondinkaTask.task_dialogs).joinedload(BlondinkaTaskDialog.dialog)
                )
                active_tasks = session.execute(stmt).unique().scalars().all()
                
                active_task_ids = {t.id for t in active_tasks}
                current_tracker_ids = set(self.trackers.keys())
                
                # Удаляем неактивные трекеры
                for task_id in current_tracker_ids - active_task_ids:
                    if task_id in self.trackers:
                        await self.trackers[task_id].cleanup()
                        del self.trackers[task_id]
                        log.info(f"🗑️ Удален трекер для задачи #{task_id}")
                
                # Добавляем новые трекеры
                for task in active_tasks:
                    if task.id not in self.trackers:
                        client = self.clients.get(task.bot_id)
                        if client:
                            tracker = BlondinkaTaskTracker(task, client)
                            if await tracker.initialize():
                                self.trackers[task.id] = tracker
                                log.info(f"✅ Добавлен трекер для задачи #{task.id}")
                        else:
                            log.warning(f"⚠️ Не найден клиент для бота #{task.bot_id} для задачи #{task.id}")
                
        except Exception as e:
            log.error(f"❌ Ошибка при проверке обновлений БД блондинки: {e}")
        
    async def process_all_tasks(self):
        """Обрабатывает все активные задачи"""
        for tracker in list(self.trackers.values()):
            try:
                await tracker.process_publication()
                await tracker.process_deletions()
            except Exception as e:
                log.error(f"❌ Ошибка обработки задачи #{tracker.task.id}: {e}")
    
    async def check_client_connections(self):
        """Проверяет соединения клиентов"""
        for bot_id, client in list(self.clients.items()):
            try:
                if not await client.is_user_authorized():
                    log.warning(f"⚠️ Клиент бота #{bot_id} не авторизован, перезапускаем...")
                    await client.disconnect()
                    
                    with get_session() as session:
                        stmt = select(BotSession).where(BotSession.id == bot_id)
                        bot = session.execute(stmt).scalar_one_or_none()
                        if bot:
                            new_client = init_user_client(bot)
                            await new_client.start()
                            self.clients[bot_id] = new_client
                            log.info(f"✅ Клиент бота #{bot_id} перезапущен")
            except Exception as e:
                log.error(f"❌ Ошибка проверки соединения клиента #{bot_id}: {e}")
    
    async def cleanup(self):
        """Очистка ресурсов"""
        self.running = False
        
        for tracker in self.trackers.values():
            await tracker.cleanup()
        
        for client in self.clients.values():
            try:
                await client.disconnect()
            except Exception:
                pass
        self.clients.clear()
        self.trackers.clear()

# Глобальный менеджер
manager = BlondinkaManager()

async def process_blondinka_tasks():
    """Обрабатывает все активные задачи блондинки"""
    try:
        await manager.check_for_updates()
        await manager.process_all_tasks()
        await manager.check_client_connections()
    except Exception as e:
        log.error(f"❌ Ошибка в основном цикле обработки блондинки: {e}")

async def run_blondinka():
    """Запуск основного цикла блондинки"""
    log.info("🚀 Модуль блондинки запускается...")
    
    try:
        await manager.initialize()
        manager.running = True
        log.info("✅ Модуль блондинки успешно запущен")
        
        cycle_count = 0
        while manager.running:
            cycle_count += 1
            log.debug(f"🔄 Цикл обработки блондинки #{cycle_count}")
            
            await process_blondinka_tasks()
            await asyncio.sleep(CHECK_INTERVAL)
            
    except Exception as e:
        log.error(f"💥 Критическая ошибка в модуле блондинки: {e}")
    finally:
        await manager.cleanup()
        log.info("🛑 Модуль блондинки остановлен")

if __name__ == "__main__":
    asyncio.run(run_blondinka())