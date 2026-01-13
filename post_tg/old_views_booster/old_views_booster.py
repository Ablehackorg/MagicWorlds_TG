# old_views_booster.py

import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set
import pytz
import requests
from urllib3.exceptions import InsecureRequestWarning

# Отключаем предупреждения о небезопасных SSL запросах
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

from telethon import TelegramClient, functions
from telethon.tl.types import MessageService
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from utils.db_utils import get_session
from telegram_client import init_user_client
from entity_resolver import ensure_peer
from models import MainEntity, BotSession, OldViewsTask, OldViewsExpense, BoosterSettings, BoosterTariff, BoosterServiceRotation

# Настройка логирования
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                   format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("old_views_booster")

# Константы
CHECK_INTERVAL = int(os.getenv("OLD_VIEWS_CHECK_INTERVAL", "60"))
TZ = pytz.timezone(os.getenv("TZ", "Europe/Moscow"))
UTC_TZ = pytz.UTC

# Настройки прокси
PROXY_URL = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
PROXIES = {
    "http": PROXY_URL,
    "https": PROXY_URL,
} if PROXY_URL else None

def _safe_twiboost_get(endpoint: str, api_key: str, params: str = "") -> tuple:
    """
    Универсальный запрос к Twiboost через прокси.
    Возвращает: (success, data, error_message)
    """
    base_urls = [
        "https://twiboost.com/api/v2"
    ]

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
    }

    for base in base_urls:
        try:
            full_url = f"{base}?action={endpoint}&key={api_key}"
            if params:
                full_url += f"&{params}"
                
            log.debug(f"📊 API запрос: {base}?action={endpoint}&{params.split('&key=')[0]}...")

            response = requests.get(
                full_url,
                headers=headers,
                timeout=15,
                verify=False,
                proxies=PROXIES,
            )

            if response.status_code == 200:
                try:
                    data = response.json()
                    return True, data, None
                except Exception as e:
                    log.warning(f"⚠️ Ошибка парсинга JSON: {e}")
                    continue
            else:
                log.warning(f"⚠️ API вернул статус {response.status_code}: {response.text[:200]}")
        except requests.exceptions.Timeout:
            log.error(f"⏱️ Таймаут при подключении к {base}")
            continue
        except requests.exceptions.ProxyError as e:
            log.error(f"🔌 Ошибка прокси: {e}")
            return False, None, f"Proxy error: {e}"
        except Exception as e:
            log.warning(f"⚠️ Ошибка запроса к {base}: {e}")
            continue

    return False, None, "All API endpoints failed"

def get_booster_settings(session) -> Optional[BoosterSettings]:
    """Получает глобальные настройки бустера из БД с использованием существующей сессии"""
    try:
        settings = session.execute(
            select(BoosterSettings)
            .options(
                selectinload(BoosterSettings.tariffs),
            )
        ).unique().scalar_one_or_none()
        
        if settings:
            # Проверяем критически важные поля
            if not settings.api_key:
                log.error("🚨 КРИТИЧЕСКАЯ ОШИБКА: API ключ в настройках бустера пустой!")
            
            log.info(f"✅ Загружены глобальные настройки бустера: "
                    f"API ключ={'***' + settings.api_key[-4:] if settings.api_key else '🚨 НЕТ'}, "
                    f"URL={settings.url or '🚨 НЕТ'}")
            
            return settings
        else:
            log.error("❌ Глобальные настройки бустера не найдены в БД")
            return None
            
    except Exception as e:
        log.error(f"❌ Ошибка загрузки глобальных настроек бустера: {e}")
        return None

        
class OldViewsProcessor:
    """Обработчик задачи накрутки просмотров для старых постов"""
    
    def __init__(self, task_id: int, client: TelegramClient):
        self.task_id = task_id
        self.client = client
        self.is_processing = False
    
    def _get_fresh_task_data(self) -> Optional[OldViewsTask]:
        """Получает СВЕЖИЕ данные задачи из БД (без кэширования)"""
        try:
            with get_session() as session:
                stmt = select(OldViewsTask).where(OldViewsTask.id == self.task_id)
                task = session.execute(stmt).scalar_one_or_none()
                if task and task.is_active:
                    log.info(f"✅ Загружена актуальная задача #{self.task_id} (активна: {task.is_active})")
                    return task
                elif task:
                    log.info(f"🛑 Задача #{self.task_id} найдена, но неактивна")
                else:
                    log.info(f"🛑 Задача #{self.task_id} не найдена в БД")
                return None
        except Exception as e:
            log.error(f"❌ Ошибка загрузки задачи #{self.task_id}: {e}")
            return None
    
    def _get_fresh_settings(self, session) -> Optional[BoosterSettings]:
        """Получает СВЕЖИЕ настройки из БД (без кэширования) с использованием переданной сессии"""
        try:
            settings = get_booster_settings(session)
            if settings:
                log.info(f"✅ Загружены актуальные настройки бустера (old_views активен: {settings.is_active_old_views})")
            else:
                log.error("❌ Настройки бустера не найдены в БД")
            
            return settings
        except Exception as e:
            log.error(f"❌ Ошибка загрузки настроек: {e}")
            return None
    
    def _get_fresh_target_data(self, target_id: int) -> Optional[MainEntity]:
        """Получает СВЕЖИЕ данные целевого канала из БД"""
        try:
            with get_session() as session:
                stmt = select(MainEntity).where(MainEntity.id == target_id)
                target = session.execute(stmt).scalar_one_or_none()
                if target:
                    log.info(f"✅ Загружены актуальные данные канала: {target.name}")
                return target
        except Exception as e:
            log.error(f"❌ Ошибка загрузки данных канала #{target_id}: {e}")
            return None

    def _should_process_task(self, task: OldViewsTask, settings: BoosterSettings) -> bool:
        """Проверяет, нужно ли обрабатывать задачу с учетом новых полей"""
        if not settings or not settings.is_active_old_views:
            log.info(f"⏭️ Модуль старых просмотров неактивен в настройках")
            return False
        
        # Обработка run_once флага
        if hasattr(task, 'run_once') and task.run_once:
            log.info(f"🎯 Обработка разового запуска задачи #{self.task_id}")
            return True
        
        # Если дата запуска не задана - устанавливаем сегодняшнюю
        if not task.last_successful_run:
            log.info(f"⏰ Устанавливаем дату запуска для задачи #{self.task_id} на сегодня")
            self._set_initial_run_date(task)
            return False
        
        # Проверяем, прошло ли достаточно времени с установленной даты запуска
        if not self._is_enough_time_passed(task):
            log.info(f"⏭️ Недостаточно времени прошло с установленной даты запуска для задачи #{self.task_id}")
            return False
        
        # Обработка exclude_period
        if hasattr(task, 'exclude_period') and task.exclude_period != "none":
            if self._is_excluded_by_period(task):
                log.info(f"⏭️ Задача #{self.task_id} исключена по периоду {task.exclude_period}")
                return False
        
        # Существующая логика для normalization_mode
        if task.normalization_mode == "now" and task.last_successful_run:
            log.info(f"⏭️ Задача #{self.task_id} уже обрабатывалась (режим 'сейчас')")
            return False
        
        if task.normalization_mode == "monthly" and task.last_successful_run:
            last_run = task.last_successful_run.replace(tzinfo=UTC_TZ)
            now = datetime.now(UTC_TZ)
            if (now - last_run).days < 30:
                log.info(f"⏭️ Задача #{self.task_id} обрабатывалась менее месяца назад")
                return False
        
        # Новая логика для других периодов
        if task.normalization_mode in ["bi_monthly", "weekly", "bi_weekly", "daily"]:
            if not self._should_process_by_schedule(task):
                return False
        
        return True

    def _set_initial_run_date(self, task: OldViewsTask):
        """Устанавливает начальную дату запуска (сегодня) если её нет"""
        try:
            with get_session() as session:
                stmt = select(OldViewsTask).where(OldViewsTask.id == self.task_id)
                fresh_task = session.execute(stmt).scalar_one_or_none()
                if fresh_task:
                    # Устанавливаем дату запуска на сегодня (UTC)
                    fresh_task.last_successful_run = datetime.utcnow()
                    fresh_task.updated_at = datetime.utcnow()
                    session.commit()
                    log.info(f"📅 Установлена начальная дата запуска для задачи #{self.task_id}: {fresh_task.last_successful_run}")
        except Exception as e:
            log.error(f"❌ Ошибка установки начальной даты запуска для задачи #{self.task_id}: {e}")

    def _is_enough_time_passed(self, task: OldViewsTask) -> bool:
        """Проверяет, прошло ли достаточно времени с установленной даты запуска"""
        if not task.last_successful_run:
            return False
        
        last_run = task.last_successful_run.replace(tzinfo=UTC_TZ)
        now = datetime.now(UTC_TZ)
        
        # Определяем необходимый интервал в зависимости от режима нормализации
        required_days = self._get_required_days_for_mode(task.normalization_mode)
        
        # Если требуется 0 дней (режим "now") - пропускаем
        if required_days == 0:
            return True
        
        # Проверяем, прошло ли достаточно дней
        days_passed = (now - last_run).days
        if days_passed < required_days:
            log.info(f"⏳ Задача #{self.task_id}: прошло {days_passed} дней, требуется {required_days} дней")
            return False
        
        log.info(f"✅ Задача #{self.task_id}: прошло достаточно времени ({days_passed} дней)")
        return True

    def _get_required_days_for_mode(self, normalization_mode: str) -> int:
        """Возвращает количество дней, которые должны пройти перед первым запуском"""
        periods = {
            "bi_monthly": 15,   # 15 дней
            "monthly": 30,      # 30 дней
            "weekly": 7,        # 7 дней  
            "bi_weekly": 4,     # 4 дня (округление 3.5 до 4)
            "daily": 1,         # 1 день
            "now": 0,           # 0 дней (обработать сразу)
        }
        return periods.get(normalization_mode, 1)  # По умолчанию 1 день

    def _is_excluded_by_period(self, task: OldViewsTask) -> bool:
        """Проверяет исключение по периоду"""
        if not task.last_successful_run:
            return False
            
        periods = {
            "1_day": timedelta(days=1),
            "2_days": timedelta(days=2),
            "1_week": timedelta(weeks=1),
            "2_weeks": timedelta(weeks=2),
        }
        
        if task.exclude_period in periods:
            exclusion_period = periods[task.exclude_period]
            last_run = task.last_successful_run.replace(tzinfo=UTC_TZ)
            now = datetime.now(UTC_TZ)
            
            if (now - last_run) < exclusion_period:
                return True
        
        return False

    def _should_process_by_schedule(self, task: OldViewsTask) -> bool:
        """Проверяет выполнение по расписанию с учетом времени"""
        if not task.last_successful_run:
            return False  # Не должно произойти, т.к. дата уже установлена
        
        last_run = task.last_successful_run.replace(tzinfo=UTC_TZ)
        now = datetime.now(UTC_TZ)
        
        # Если это первый запуск после ожидания - пропускаем дополнительные проверки
        # (они уже выполнены в _is_enough_time_passed)
        required_days = self._get_required_days_for_mode(task.normalization_mode)
        days_passed = (now - last_run).days
        
        # Если это первый запуск после установленной даты
        if days_passed >= required_days:
            log.info(f"🔄 Первый запуск задачи #{self.task_id} после ожидания {required_days} дней")
            return True
        
        # Для последующих запусков - стандартная логика
        periods = {
            "bi_monthly": 15,  # дней
            "weekly": 7,       # дней  
            "bi_weekly": 3.5,  # дней
            "daily": 1,        # день
        }
        
        if task.normalization_mode in periods:
            required_days = periods[task.normalization_mode]
            if (now - last_run).days < required_days:
                return False

        return True

    def _get_posts_limit(self, posts_normalization: str) -> int:
        """Определяет лимит постов на основе режима нормализации"""
        limits = {
            "last_100": 100,
            "last_200": 200, 
            "last_300": 300,
            "first_100": 100,
            "first_200": 200,
            "first_300": 300
        }
        return limits.get(posts_normalization, 100)
    
    def _is_reverse_order(self, posts_normalization: str) -> bool:
        """Определяет порядок получения постов"""
        return posts_normalization.startswith("first")
    
    async def _get_channel_posts(self, target: MainEntity, limit: int, reverse: bool = False) -> List:
        """Получает посты из канала с учетом хронологии"""
        try:
            target_entity = await ensure_peer(
                self.client, 
                telegram_id=target.telegram_id,
                link=target.link
            )
            
            messages = []
            
            if reverse:
                # Для "первых N" постов - получаем с самого начала
                offset_date = datetime(2015, 1, 1)
                async for message in self.client.iter_messages(
                    target_entity, 
                    limit=limit,
                    offset_date=offset_date,
                    reverse=True
                ):
                    if isinstance(message, MessageService) or getattr(message, 'action', None):
                        continue
                    messages.append(message)
                
                messages = messages[:limit]
                log.info(f"📨 Получено {len(messages)} первых постов (самые старые)")
                
            else:
                # Для "последних N" постов - стандартная логика, но пропускаем самые свежие
                async for message in self.client.iter_messages(
                    target_entity, 
                    limit=limit + 15
                ):
                    if isinstance(message, MessageService) or getattr(message, 'action', None):
                        continue
                    messages.append(message)
                
                messages.sort(key=lambda m: m.id, reverse=True)
                messages = messages[15:15 + limit]
                log.info(f"📨 Получено {len(messages)} последних постов (исключая свежие 15)")
            
            if messages:
                dates = [msg.date for msg in messages if hasattr(msg, 'date')]
                if dates:
                    min_date = min(dates)
                    max_date = max(dates)
                    log.info(f"🕒 Диапазон дат постов: {min_date} - {max_date}")
            
            return messages
            
        except Exception as e:
            log.error(f"❌ Ошибка получения постов из канала {target.name}: {e}")
            return []
    
    def _get_tg_post_link(self, message, target: MainEntity) -> str:
        """Формирует ссылку на пост в Telegram"""
        try:
            if target.link:
                channel_username = target.link.replace('https://t.me/', '').replace('@', '')
                return f"https://t.me/{channel_username}/{message.id}"
            else:
                chat_id = abs(target.telegram_id)
                return f"https://t.me/c/{chat_id}/{message.id}"
        except Exception as e:
            log.error(f"❌ Ошибка формирования ссылки для поста {message.id}: {e}")
            return ""
    
    def _calculate_required_views(self, task: OldViewsTask, settings: BoosterSettings, subscribers_count: int, current_views: int) -> int:
        """Рассчитывает необходимое количество просмотров с учетом текущих просмотров и кратности"""
        try:
            base_views = int((task.view_coefficient / 100) * subscribers_count)
            views_needed = base_views - current_views
            
            if views_needed <= 0:
                return 0
            
            if task.views_multiplier > 1:
                remainder = views_needed % task.views_multiplier
                if remainder > 0:
                    if remainder >= task.views_multiplier / 2:
                        rounded_views = views_needed + (task.views_multiplier - remainder)
                    else:
                        rounded_views = views_needed - remainder
                else:
                    rounded_views = views_needed
            else:
                rounded_views = views_needed
            
            min_views = settings.min_old_views if settings else 0
            final_views = max(rounded_views, min_views)
            
            log.info(f"📊 Расчет просмотров: {task.view_coefficient}% от {subscribers_count} = {base_views}, "
                    f"текущие просмотры: {current_views}, нужно добавить: {views_needed}, "
                    f"кратность {task.views_multiplier}, итого: {final_views}")
            
            return final_views
            
        except Exception as e:
            log.error(f"❌ Ошибка расчета просмотров: {e}")
            return 0
    
    async def _needs_boost(self, message, subscribers_count: int) -> bool:
        """Проверяет, нужна ли накрутка просмотров для поста"""
        try:
            current_views = message.views if hasattr(message, 'views') else 0
            min_required = subscribers_count // 3
            needs_boost = current_views < min_required
            
            log.debug(f"📊 Пост {message.id}: просмотров {current_views}, минимум {min_required}, нужно накрутить: {needs_boost}")
            
            return needs_boost
            
        except Exception as e:
            log.error(f"❌ Ошибка проверки необходимости накрутки: {e}")
            return False
    
    async def get_service_id(self, session, views_count: int, settings: BoosterSettings) -> int:
        """Получает service_id через ротацию"""
        service_id = BoosterServiceRotation.get_next_service_id_for_module(
            session=session,
            module_name="old_views",
            tariffs=settings.tariffs,
            default_service_id=settings.old_views_service_id,
            count=views_count,
        )
        return service_id

    async def api_send_views(self, views_count: int, tg_post_link: str, session, settings: BoosterSettings, service_id = None) -> Tuple[Optional[str], float]:
        """Отправляет запрос на API для накрутки просмотров"""
        try:
            if not tg_post_link:
                log.error("❌ Не удалось сформировать ссылку на пост")
                return None, 0.0

            if not settings.api_key:
                log.error("❌ API KEY не установлен в настройках")
                return None, 0.0
            
            if not service_id:
                log.error("❌ Не удалось получить service_id для старых просмотров")
                return None, 0.0

            if PROXIES:
                log.info(f"🔌 Используется прокси")
            else:
                log.info("🌐 Прокси не используется")

            params = f"service={service_id}&link={tg_post_link}&quantity={views_count}"
            success, result, error = _safe_twiboost_get("add", settings.api_key, params)
            
            if not success:
                log.error(f"❌ Ошибка API (add): {error}")
                return None, 0.0

            order_id = result.get("order")
            if not order_id:
                log.error(f"❌ Ответ без 'order': {result}")
                return None, 0.0

            log.info(f"✅ Заказ создан успешно, order={order_id}")

            # Сохраняем заказ в БД
            from models import BoosterOrder
            booster_order = BoosterOrder(
                task_id=self.task_id,
                task_type="old_views",
                service_id=service_id,
                external_order_id=str(order_id),
                quantity=views_count,
                price=0.0,  # Пока неизвестно
                status='pending'
            )
            session.add(booster_order)
            session.flush()  # Получаем ID заказа

            success, status_data, error = _safe_twiboost_get("status", settings.api_key, f"order={order_id}")
            
            if not success:
                log.error(f"❌ Ошибка API (status): {error}")
                return None, 0.0

            charge = status_data.get("charge")
            if charge is None:
                log.warning(f"⚠️ Цена (charge) не найдена в ответе: {status_data}")
                return None, 0.0

            # Обновляем заказ с ценой
            booster_order.price = float(charge)
            booster_order.status = 'in_progress'
            
            log.info(f"💰 Получена цена (charge): {charge}")
            return str(order_id), float(charge)

        except Exception as e:
            log.error(f"💥 Критическая ошибка при работе с API: {e}")
            return None, 0.0
    
    def _save_expense(self, post_message_id: int, views_count: int, price: float, service_id: int, order_id: str = None):
        """Сохраняет информацию о расходе"""
        try:
            with get_session() as session:
                expense = OldViewsExpense(
                    task_id=self.task_id,
                    post_message_id=post_message_id,
                    views_count=views_count,
                    price=price,
                    service_id=service_id
                )
                session.add(expense)
                session.flush()  # Получаем ID расхода
                
                # Обновляем заказ в БД с expense_id
                if order_id:
                    from models import BoosterOrder
                    from sqlalchemy import update
                    
                    stmt = update(BoosterOrder).where(
                        BoosterOrder.external_order_id == order_id
                    ).values(
                        expense_id=expense.id,
                        updated_at=datetime.utcnow()
                    )
                    session.execute(stmt)
                
                session.commit()
                log.info(f"💾 Сохранен расход для поста {post_message_id}: {views_count} просмотров, цена: {price}, order: {order_id}")
        except Exception as e:
            log.error(f"❌ Ошибка сохранения расхода: {e}")
        
    def _update_task_success(self, task: OldViewsTask):
        """Обновляет временную метку успешного выполнения задачи и сбрасывает run_once"""
        try:
            with get_session() as session:
                stmt = select(OldViewsTask).where(OldViewsTask.id == self.task_id)
                fresh_task = session.execute(stmt).scalar_one_or_none()
                if fresh_task:
                    fresh_task.last_successful_run = datetime.utcnow()
                    fresh_task.updated_at = datetime.utcnow()
                    
                    # Сбрасываем флаг run_once после выполнения
                    if hasattr(fresh_task, 'run_once') and fresh_task.run_once:
                        fresh_task.run_once = False
                        log.info(f"🔄 Сброшен флаг run_once для задачи #{self.task_id}")
                    
                    session.commit()
                    log.info(f"🕒 Обновлена дата последнего успешного запуска задачи #{self.task_id}")
        except Exception as e:
            log.error(f"❌ Ошибка обновления временной метки: {e}")

    async def _get_subscribers_count(self, target: MainEntity) -> int:
        """Получает количество подписчиков из канала"""
        try:
            log.info(f"👥 Получение количества подписчиков для канала {target.name}")
            
            target_entity = await ensure_peer(
                self.client, 
                telegram_id=target.telegram_id,
                link=target.link
            )
            
            channel = await self.client.get_entity(target_entity)
            subscribers = 0

            try:
                full = await self.client(functions.channels.GetFullChannelRequest(channel))
                if full.full_chat.participants_count:
                    subscribers = full.full_chat.participants_count
            except Exception:
                try:
                    full = await self.client(functions.messages.GetFullChatRequest(channel.id))
                    if full.full_chat.participants_count:
                        subscribers = full.full_chat.participants_count
                except Exception:
                    log.warning(f"⚠️ Не удалось получить количество подписчиков для {target.name}")

            subscribers = int(subscribers) if subscribers else 0
            log.info(f"📊 Получено подписчиков: {subscribers} для канала {target.name}")
            
            await self._update_subscribers_count(subscribers)
            
            return subscribers
                
        except Exception as e:
            log.error(f"❌ Ошибка получения подписчиков для канала {target.name}: {e}")
            return 0
    
    async def _update_subscribers_count(self, subscribers_count: int):
        """Обновляет количество подписчиков в БД"""
        try:
            with get_session() as session:
                stmt = select(OldViewsTask).where(OldViewsTask.id == self.task_id)
                task = session.execute(stmt).scalar_one_or_none()
                if task:
                    old_subscribers = task.subscribers_count
                    task.subscribers_count = subscribers_count
                    session.commit()
                    log.info(f"📊 Обновлено количество подписчиков для задачи #{self.task_id}: {old_subscribers} -> {subscribers_count}")
        except Exception as e:
            log.error(f"❌ Ошибка обновления подписчиков: {e}")
    
    async def process(self):
        """Основной процесс обработки задачи с поддержкой новых полей"""
        if self.is_processing:
            log.info(f"⏭️ Задача #{self.task_id} уже обрабатывается")
            return
            
        self.is_processing = True
        
        try:
            task = self._get_fresh_task_data()
            if not task:
                log.info(f"⏭️ Задача #{self.task_id} неактивна или не найдена")
                return
            
            with get_session() as session:
                settings = self._get_fresh_settings(session)
                
                if not self._should_process_task(task, settings):
                    return
                
                log.info(f"🚀 Начата обработка задачи #{self.task_id} для старых постов "
                        f"(режим: {task.normalization_mode}, нормализация: {task.posts_normalization})")
                
                # Логируем дополнительные параметры если они есть
                if hasattr(task, 'run_once') and task.run_once:
                    log.info(f"🎯 РАЗОВЫЙ ЗАПУСК задачи #{self.task_id}")
                if hasattr(task, 'exclude_period'):
                    log.info(f"⏰ Исключение периода: {task.exclude_period}")
                # УБРАТЬ УПОМИНАНИЕ normalization_time
                
                target = self._get_fresh_target_data(task.target_id)
                if not target:
                    log.error(f"❌ Целевой канал не найден для задачи #{self.task_id}")
                    return
                
                subscribers_count = await self._get_subscribers_count(target)
                if subscribers_count <= 0:
                    log.warning(f"⚠️ Не удалось получить количество подписчиков для канала {target.name}")
                    return
                
                posts_limit = self._get_posts_limit(task.posts_normalization)
                reverse_order = self._is_reverse_order(task.posts_normalization)
                
                log.info(f"📋 Получение {posts_limit} постов (порядок: {'первые' if reverse_order else 'последние'})")
                
                posts = await self._get_channel_posts(target, posts_limit, reverse_order)
                
                if not posts:
                    log.warning(f"⚠️ Не найдено постов в канале {target.name}")
                    return
                
                log.info(f"📨 Получено {len(posts)} постов из канала {target.name}")
                
                processed_posts = 0
                boosted_posts = 0
                
                for post in posts:
                    try:
                        if await self._needs_boost(post, subscribers_count):
                            tg_post_link = self._get_tg_post_link(post, target)
                            
                            if not tg_post_link:
                                log.error(f"❌ Не удалось сформировать ссылку для поста {post.id}")
                                continue
                            
                            current_views = post.views if hasattr(post, 'views') else 0
                            required_views = self._calculate_required_views(task, settings, subscribers_count, current_views)
                            
                            if required_views > 0:
                                log.info(f"🎯 Накрутка для пост {post.id}: {required_views} просмотров (текущие: {current_views})")
                                
                                # ИСПОЛЬЗУЕМ НОВЫЙ МЕТОД С ПРОВЕРКОЙ ОЧЕРЕДЕЙ
                                service_id = await BoosterServiceRotation.get_next_service_id_for_module(
                                    session=session,
                                    module_name="old_views",
                                    tariffs=settings.tariffs,
                                    default_service_id=settings.old_views_service_id,
                                    count=required_views,
                                    booster_settings=settings  # Передаем настройки для проверки очередей
                                )
                                
                                if not service_id:
                                    log.error(f"❌ Не удалось получить service_id для поста {post.id}")
                                    continue
                                
                                order_id, price = await self.api_send_views(required_views, tg_post_link, session, settings, service_id)
                                
                                if price > 0 and order_id:
                                    self._save_expense(post.id, required_views, price, service_id, order_id)
                                    boosted_posts += 1
                                    log.info(f"✅ Успешная накрутка для поста {post.id}, order: {order_id}")
                                else:
                                    log.error(f"❌ Ошибка накрутки для поста {post.id}")
                        
                        processed_posts += 1
                        await asyncio.sleep(1)
                        
                    except Exception as e:
                        log.error(f"❌ Ошибка обработки поста {post.id}: {e}")
                        continue
                
                if boosted_posts > 0:
                    self._update_task_success(task)
                
                log.info(f"✅ Завершена обработка задачи #{self.task_id}: "
                        f"обработано {processed_posts} постов, "
                        f"накручено для {boosted_posts} постов")
            
        except Exception as e:
            log.error(f"💥 Критическая ошибка обработки задачи #{self.task_id}: {e}")
        finally:
            self.is_processing = False

class OldViewsManager:
    """Менеджер для управления всеми задачами накрутки старых просмотров"""
    
    def __init__(self):
        self.processors: Dict[int, OldViewsProcessor] = {}
        self.clients: Dict[int, TelegramClient] = {}
        
    async def initialize(self):
        """Инициализация менеджера"""
        log.info("🔄 Инициализация менеджера старых просмотров...")
        await self._load_tasks()
        
    async def _load_tasks(self):
        """Загружает активные задачи из БД"""
        try:
            with get_session() as session:
                stmt = select(OldViewsTask).where(OldViewsTask.is_active == True)
                tasks = session.execute(stmt).scalars().all()
        except Exception as e:
            log.error(f"❌ Ошибка загрузки задач: {e}")
            tasks = []
        
        if not tasks:
            log.info("🔍 Нет активных задач накрутки старых просмотров")
            return
        
        log.info(f"🔍 Загружено {len(tasks)} активных задач")
        
        bot_ids = sorted(set(t.bot_id for t in tasks))
        
        with get_session() as session:
            stmt = select(BotSession).where(BotSession.id.in_(bot_ids))
            bots = {b.id: b for b in session.execute(stmt).scalars().all()}
        
        # Инициализация клиентов
        for bot_id in bot_ids:
            if bot_id not in self.clients:
                try:
                    bot = bots.get(bot_id)
                    if not bot:
                        log.error(f"❌ Бот #{bot_id} не найден в базе данных")
                        continue
                        
                    client = init_user_client(bot)
                    await client.start()
                    if not await client.is_user_authorized():
                        raise RuntimeError(f"Бот #{bot_id} не авторизован")
                    self.clients[bot_id] = client
                    log.info(f"✅ Бот #{bot_id} авторизован")
                except Exception as e:
                    log.error(f"❌ Ошибка инициализации бота #{bot_id}: {e}")
        
        # Создание процессоров
        for task in tasks:
            client = self.clients.get(task.bot_id)
            if client and task.id not in self.processors:
                processor = OldViewsProcessor(task.id, client)
                self.processors[task.id] = processor
                log.info(f"✅ Процессор создан для задачи #{task.id}")

    async def check_for_updates(self):
        """Проверяет обновления в БД и обновляет процессоры"""
        try:
            with get_session() as session:
                stmt = select(OldViewsTask).where(OldViewsTask.is_active == True)
                active_tasks = session.execute(stmt).scalars().all()
        except Exception as e:
            log.error(f"❌ Ошибка проверки обновлений: {e}")
            return
            
        active_task_ids = {t.id for t in active_tasks}
        current_processor_ids = set(self.processors.keys())
        
        # Удаляем неактивные трекеры
        for task_id in current_processor_ids - active_task_ids:
            if task_id in self.processors:
                del self.processors[task_id]
                log.info(f"🗑️ Удален процессор для неактивной задачи #{task_id}")
        
        # Добавляем новые трекеры
        for task in active_tasks:
            if task.id not in self.processors:
                client = self.clients.get(task.bot_id)
                if client:
                    processor = OldViewsProcessor(task.id, client)
                    self.processors[task.id] = processor
                    log.info(f"✅ Добавлен процессор для новой задачи #{task.id}")
                else:
                    log.warning(f"⚠️ Не найден клиент для бота #{task.bot_id} для задачи #{task.id}")

    async def process_all_tasks(self):
        """Обрабатывает все активные задачи"""
        # Сначала проверяем актуальность задач
        await self.check_for_updates()
        
        # Затем обрабатываем только актуальные задачи
        for processor in list(self.processors.values()):
            try:
                await processor.process()
            except Exception as e:
                log.error(f"❌ Ошибка обработки задачи #{processor.task_id}: {e}")
    
    async def cleanup(self):
        """Очистка ресурсов"""
        for client in self.clients.values():
            try:
                await client.disconnect()
            except Exception:
                pass
        self.clients.clear()
        self.processors.clear()

# Глобальный менеджер
manager = OldViewsManager()

async def process_old_views_tasks():
    """Обрабатывает все активные задачи накрутки старых просмотров"""
    try:
        await manager.check_for_updates()
        await manager.process_all_tasks()
    except Exception as e:
        log.error(f"❌ Ошибка в основном цикле обработки: {e}")

async def run_old_views_booster():
    """Запуск основного цикла накрутки старых просмотров"""
    log.info("🚀 Модуль старых просмотров запускается...")
    
    try:
        await manager.initialize()
        log.info("✅ Модуль старых просмотров успешно запущен")
        
        cycle_count = 0
        while True:
            cycle_count += 1
            log.debug(f"🔄 Цикл обработки #{cycle_count}")
            
            await process_old_views_tasks()
            await asyncio.sleep(CHECK_INTERVAL)
            
    except Exception as e:
        log.error(f"💥 Критическая ошибка в модуле старых просмотров: {e}")
    finally:
        await manager.cleanup()
        log.info("🛑 Модуль старых просмотров остановлен")


if __name__ == "__main__":
    asyncio.run(run_old_views_booster())