# view_booster.py

import os
import asyncio
import logging
from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple, Set
import pytz
import requests
from urllib3.exceptions import InsecureRequestWarning
import random

# Отключаем предупреждения о небезопасных SSL запросах
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

from telethon import TelegramClient, events
from telethon import functions
from telethon.tl.types import Channel, Chat, MessageService, Message
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload, joinedload

from utils.db_utils import get_session
from telegram_client import init_user_client
from entity_resolver import ensure_peer
from models import ViewBoostTask, ViewDistribution, ViewBoostExpense, MainEntity, BotSession, BoosterSettings, BoosterServiceRotation, BoosterTariff

# Настройка логирования
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                   format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("view_booster")

# Константы
CHECK_INTERVAL = int(os.getenv("VIEW_BOOST_CHECK_INTERVAL", "30"))
TZ = pytz.timezone(os.getenv("TZ", "Europe/Moscow"))
UTC_TZ = pytz.UTC

# Настройки прокси
PROXY_URL = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
PROXIES = {
    "http": PROXY_URL,
    "https": PROXY_URL,
} if PROXY_URL else None

# Временные интервалы для 4 режимов постов
MORNING_START = time(5, 0)    # 5:00
MORNING_END = time(9, 59)     # 9:59
DAY_START = time(10, 0)       # 10:00  
DAY_END = time(15, 59)        # 15:59
EVENING_START = time(16, 0)   # 16:00
EVENING_END = time(21, 59)    # 21:59
NIGHT_START = time(22, 0)     # 22:00
NIGHT_END = time(4, 59)       # 4:59 следующего дня

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
        ).unique().scalar_one_or_none()
        
        if settings:
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

def get_view_distributions(session):
    """Получает распределения просмотров для всех типов постов с использованием существующей сессии"""
    try:
        stmt = select(ViewDistribution)
        dist = session.execute(stmt).scalar_one_or_none()
        if dist:
            return dist.morning_distribution, dist.day_distribution, dist.evening_distribution, dist.night_distribution
        else:
            log.warning("⚠️ Распределения просмотров не найдены в БД, используются значения по умолчанию")
            return {}, {}, {}, {}
    except Exception as e:
        log.error(f"❌ Ошибка загрузки распределений просмотров: {e}")
        return {}, {}, {}, {}

def get_entity_timezone(entity: MainEntity):
    """
    Возвращает pytz.FixedOffset для страны сущности.
    Если страна не указана, возвращает Москву.
    """
    from datetime import timedelta
    delta = 0
    if entity and getattr(entity, "country", None) and entity.country.time_zone_delta is not None:
        delta = entity.country.time_zone_delta
    # Смещение в минутах
    return pytz.FixedOffset(int(delta * 60))

async def get_service_id(views_count: int) -> int:
    with get_session() as session:
        settings = get_booster_settings(session)
        if not settings:
            log.error("❌ Настройки бустера не найдены в БД")
            return 0

        service_id = BoosterServiceRotation.get_next_service_id_for_module(
            session=session,
            module_name="new_views",
            tariffs=settings.tariffs,
            count=views_count
        )

        return service_id

async def api_send_views(views_count: int, tg_post_link: str, task_id: int = None) -> List[dict]:
    """Отправляет запрос на API для накрутки просмотров через прокси."""
    transactions = []
    
    try:
        log.debug(f"🔍 Начало api_send_views для {views_count} просмотров")
        
        with get_session() as session:
            log.debug(f"🔍 Получение настроек бустера")
            settings = get_booster_settings(session)
            if not settings or not settings.api_key:
                log.error("❌ Настройки бустера не найдены или отсутствует API ключ")
                return transactions
            
            api_key = settings.api_key
            log.debug(f"🔍 API ключ получен")
        
        # 1. Вычисляем 25% от общего количества
        primary_views_needed = min(50, int(views_count * 0.25))
        log.debug(f"🔍 25% просмотров: {primary_views_needed}")
        
        # 2. Получаем тарифы для модуля new_views
        with get_session() as session:
            log.debug(f"🔍 Получение тарифов из БД")
            all_tariffs_stmt = select(BoosterTariff).where(
                BoosterTariff.booster_id == settings.id,
                BoosterTariff.module == "new_views",
                BoosterTariff.is_active == True,
                BoosterTariff.min_limit <= views_count
            )
            all_tariffs = session.execute(all_tariffs_stmt).scalars().all()
            log.debug(f"🔍 Найдено {len(all_tariffs)} тарифов")
        
            # Получаем или создаем экземпляр ротации для модуля new_views
            rotation = BoosterServiceRotation.get_or_create_rotation(
                session=session,
                module_name="new_views",
                default_service_id=getattr(settings, "new_views_service_id", 0)
            )
            
            # Проверяем очереди для всех тарифов через экземпляр ротации
            active_orders_count = await rotation.check_active_orders(
                session=session, 
                booster_settings=settings
            )
            
            # Фильтруем тарифы без очереди
            no_queue_tariffs = []
            for tariff in all_tariffs:
                active_orders = active_orders_count.get(tariff.service_id, 0)
                has_queue = active_orders >= 2
                
                if not has_queue:
                    no_queue_tariffs.append(tariff)
            
            # Если нет тарифов без очереди - используем все доступные
            available_tariffs = no_queue_tariffs if no_queue_tariffs else all_tariffs
            
            if not available_tariffs:
                log.error("❌ Нет доступных тарифов для модуля new_views")
                return transactions
            
            # Ищем основной тариф среди доступных
            primary_tariff = None
            for tariff in available_tariffs:
                if tariff.is_primary:
                    primary_tariff = tariff
                    break
            
            # Если основного тарифа нет в доступных - берем первый без очереди
            if not primary_tariff:
                primary_tariff = available_tariffs[0]
                log.info(f"⚠️ Основной тариф не доступен, используем: service_id={primary_tariff.service_id}")
            
            log.info(f"✅ Найден тариф для 25%: service_id={primary_tariff.service_id}, активных заказов: {active_orders_count.get(primary_tariff.service_id, 0)}")
            
            # 3. Проверяем min_limit для основного тарифа
            if primary_views_needed < primary_tariff.min_limit:
                primary_views_needed = primary_tariff.min_limit
            
            # 4. Проверяем, что после вычитания 25% остается хотя бы min_limit
            remaining_views = views_count - primary_views_needed
            
            # Получаем тариф для оставшихся просмотров
            secondary_tariff = None
            if remaining_views > 0:
                # Ищем тариф без очереди для оставшихся просмотров
                possible_secondary_tariffs = []
                for tariff in available_tariffs:
                    if (tariff.service_id != primary_tariff.service_id and 
                        tariff.min_limit <= remaining_views):
                        possible_secondary_tariffs.append(tariff)
                
                if possible_secondary_tariffs:
                    # Выбираем случайный тариф из доступных
                    import random
                    secondary_tariff = random.choice(possible_secondary_tariffs)
                else:
                    # Если нет подходящих вторичных тарифов, отправляем все через основной
                    log.info(f"📊 Отправляем все {views_count} через тариф service_id={primary_tariff.service_id}")
                    primary_views_needed = views_count
                    remaining_views = 0
            
            if remaining_views < primary_tariff.min_limit and remaining_views > 0:
                # Отправляем все через основной тариф
                log.info(f"📊 Отправляем все {views_count} через тариф service_id={primary_tariff.service_id}")
                
                order_id, price = await _send_single_order(
                    service_id=primary_tariff.service_id,
                    tg_post_link=tg_post_link,
                    api_key=api_key,
                    quantity=views_count,
                    task_id=task_id,
                    task_type="new_views"
                )
                
                if order_id and price > 0:
                    transactions.append({
                        "service_id": primary_tariff.service_id,
                        "views_count": views_count,
                        "price": price,
                        "order_id": order_id
                    })
                
                return transactions
            else:
                # 5. Отправляем 25% через основной тариф
                log.info(f"📊 Отправляем {primary_views_needed} через тариф service_id={primary_tariff.service_id}")
                
                order_id_1, price_1 = await _send_single_order(
                    service_id=primary_tariff.service_id,
                    tg_post_link=tg_post_link,
                    api_key=api_key,
                    quantity=primary_views_needed,
                    task_id=task_id,
                    task_type="new_views"
                )
                
                if order_id_1 and price_1 > 0:
                    transactions.append({
                        "service_id": primary_tariff.service_id,
                        "views_count": primary_views_needed,
                        "price": price_1,
                        "order_id": order_id_1
                    })
                
                # 6. Отправляем оставшиеся просмотры через вторичный тариф
                if remaining_views > 0 and secondary_tariff:
                    log.info(f"📊 Отправляем {remaining_views} через тариф service_id={secondary_tariff.service_id}")
                    
                    order_id_2, price_2 = await _send_single_order(
                        service_id=secondary_tariff.service_id,
                        tg_post_link=tg_post_link,
                        api_key=api_key,
                        quantity=remaining_views,
                        task_id=task_id,
                        task_type="new_views"
                    )
                    
                    if order_id_2 and price_2 > 0:
                        transactions.append({
                            "service_id": secondary_tariff.service_id,
                            "views_count": remaining_views,
                            "price": price_2,
                            "order_id": order_id_2
                        })
                
                return transactions
    
    except Exception as e:
        log.error(f"💥 Критическая ошибка: {e}")
        return transactions

async def _send_single_order(service_id: int, tg_post_link: str, api_key: str, quantity: int, 
                            task_id: int = None, task_type: str = "new_views") -> Tuple[Optional[str], float]:
    """Отправляет один заказ и сохраняет его в БД"""
    try:
        params = f"service={service_id}&link={tg_post_link}&quantity={quantity}"
        success, result, error = _safe_twiboost_get("add", api_key, params)
        
        if not success:
            log.error(f"❌ Ошибка API для service_id {service_id}: {error}")
            return None, 0.0
        
        order_id = result.get("order")
        if not order_id:
            log.error(f"❌ Ответ без 'order': {result}")
            return None, 0.0
        
        log.info(f"✅ Заказ создан успешно, order={order_id}")
        
        # Сохраняем заказ в БД
        with get_session() as session:
            from models import BoosterOrder
            booster_order = BoosterOrder(
                task_id=task_id,
                task_type=task_type,
                service_id=service_id,
                external_order_id=str(order_id),
                quantity=quantity,
                price=0.0,
                status='pending'
            )
            session.add(booster_order)
            session.flush()
        
        # Получаем цену
        success, status_data, error = _safe_twiboost_get("status", api_key, f"order={order_id}")
        if not success:
            log.error(f"❌ Ошибка API (status): {error}")
            return None, 0.0
        
        charge = status_data.get("charge", 0.0)
        
        # Обновляем заказ с ценой
        with get_session() as session:
            from sqlalchemy import update
            from models import BoosterOrder
            
            stmt = update(BoosterOrder).where(
                BoosterOrder.external_order_id == str(order_id)
            ).values(
                price=float(charge),
                status='in_progress',
                updated_at=datetime.utcnow()
            )
            session.execute(stmt)
            session.commit()
        
        return str(order_id), float(charge)
        
    except Exception as e:
        log.error(f"❌ Ошибка отправки заказа для service_id {service_id}: {e}")
        return None, 0.0


class TrackedPost:
    """Отслеживаемый пост"""
    
    def __init__(self, message_id: int, post_type: str, total_views_needed: int, 
                 publish_time: datetime, task_id: int, channel_telegram_id: int, 
                 channel_username: str = None, entity_timezone=None):
        self.message_id = message_id
        self.post_type = post_type  # "morning", "day", "evening", "night"
        self.total_views_needed = total_views_needed
        self.publish_time = publish_time
        self.task_id = task_id
        self.channel_telegram_id = channel_telegram_id
        self.channel_username = channel_username
        self.entity_timezone = entity_timezone or TZ
        self.completed_hours = set()
        self.is_running = True
        self.last_processed_hour = None
        self.last_processed_day = None
        self.original_total_views = total_views_needed
        
    def _get_tg_post_link(self):
        """Формирует ссылку на пост в Telegram"""
        if self.channel_username:
            return f"https://t.me/{self.channel_username}/{self.message_id}"
        else:
            chat_id = abs(self.channel_telegram_id)
            return f"https://t.me/c/{chat_id}/{self.message_id}"
    
    async def process(self):
        """Основной процесс обработки поста"""
        start_time = datetime.now(self.entity_timezone)
        log.info(f"🚀 Начата обработка поста {self.message_id} для задачи #{self.task_id}, "
                f"изначальное количество просмотров: {self.original_total_views}")
        
        # Обрабатываем пост в течение 24 часов
        while datetime.now(self.entity_timezone) < start_time + timedelta(hours=24) and self.is_running:
            try:
                current_time = datetime.now(self.entity_timezone)
                
                current_hour_info = self._get_current_hour_info()
                
                if current_hour_info and current_hour_info != self.last_processed_hour:
                    # Проверяем, что текущее время соответствует расписанию отправки
                    if await self._should_process_hour(current_hour_info):
                        await self._process_hour(current_hour_info)
                        self.last_processed_hour = current_hour_info
                        self.completed_hours.add(current_hour_info)
                    else:
                        log.debug(f"⏰ Пропуск часа {current_hour_info} - не время для отправки")
                
                await asyncio.sleep(60)  # Проверяем каждую минуту
                
            except Exception as e:
                log.error(f"❌ Ошибка в основном цикле обработки поста {self.message_id}: {e}")
                await asyncio.sleep(60)
        
        self.is_running = False
        log.info(f"✅ Завершено отслеживание поста {self.message_id} (24 часа истекли)")
    
    def _get_current_hour_info(self) -> Optional[tuple]:
        """
        Возвращает информацию о текущем часе для распределения просмотров.
        
        Используем относительные часы от времени публикации (1-24)
        
        Возвращает: (day_type, hour) или None если время вышло за пределы расписания
        """
        try:
            now = datetime.now(self.entity_timezone)
            time_since_publish = now - self.publish_time
            
            # Один день: 24 часа от времени публикации
            if time_since_publish <= timedelta(hours=24):
                relative_hour = int(time_since_publish.total_seconds() / 3600) + 1
                if 1 <= relative_hour <= 24:
                    log.debug(f"📅 Относительный час: {relative_hour} "
                             f"(с момента публикации: {time_since_publish})")
                    return ("day1", relative_hour)
            
            log.debug(f"⏭️ Время вышло за пределы 24 часов: {time_since_publish}")
            return None
            
        except Exception as e:
            log.error(f"❌ Ошибка расчета текущего часа: {e}")
            return None

    async def _should_process_hour(self, hour_info: tuple) -> bool:
        """
        Проверяет, должно ли текущее время соответствовать расписанию отправки для данного часа.
        Это предотвращает отправку просмотров не вовремя.
        """
        try:
            day_type, hour = hour_info
            now = datetime.now(self.entity_timezone)
            current_hour = now.hour
            current_minute = now.minute
            
            # Для точной отправки проверяем, что мы в правильном временном интервале
            # Используем относительные часы от времени публикации
            expected_hour = (self.publish_time.hour + hour - 1) % 24
            
            # Допускаем отклонение +/- 30 минут для отправки
            time_diff = abs(current_hour - expected_hour)
            if time_diff == 0 and current_minute <= 30:
                return True
            elif time_diff == 1 and current_minute >= 30:
                return True
                
            log.debug(f"⏰ Пропуск: текущий час {current_hour}:{current_minute}, ожидаемый ~{expected_hour}:00-30")
            return False
            
        except Exception as e:
            log.error(f"❌ Ошибка проверки времени отправки: {e}")
            return True  # В случае ошибки разрешаем отправку
    
    async def _process_hour(self, hour_info: tuple):
        """Обрабатывает конкретный час согласно расписанию"""
        day_type, hour = hour_info
        
        with get_session() as session:
            MORNING_POST_DISTRIBUTION, DAY_POST_DISTRIBUTION, EVENING_POST_DISTRIBUTION, NIGHT_POST_DISTRIBUTION = get_view_distributions(session)
        
        # Выбираем распределение
        if self.post_type == "morning":
            distribution = MORNING_POST_DISTRIBUTION
        elif self.post_type == "day":
            distribution = DAY_POST_DISTRIBUTION
        elif self.post_type == "evening":
            distribution = EVENING_POST_DISTRIBUTION
        else:
            distribution = NIGHT_POST_DISTRIBUTION
        
        day_distribution = distribution.get(day_type, {})
        hour_percent = day_distribution.get(str(hour)) or day_distribution.get(hour) or 0
        
        if hour_percent > 0:
            views_needed = int(self.original_total_views * hour_percent / 100)
            
            if views_needed > 0:
                tg_post_link = self._get_tg_post_link()
                
                transactions = await api_send_views(views_needed, tg_post_link, self.task_id)
                
                # Сохраняем КАЖДУЮ транзакцию отдельно
                for tx in transactions:
                    await self._save_expense(
                        views_count=tx["views_count"],
                        price=tx["price"],
                        hour_percent=hour_percent,
                        day_type=day_type,
                        hour=hour,
                        service_id=tx["service_id"],  # ⚠️ Реальный service_id из API!
                        order_id=tx.get("order_id")   # Добавляем order_id
                    )
                
                log.info(f"📈 Отправлено {views_needed} просмотров через {len(transactions)} тариф(а/ов)")
            else:
                log.warning(f"⚠️ Рассчитано 0 просмотров")
        
        elif day_type == "day1" and hour == 1:
            # Обработка первого часа по умолчанию
            await self._process_first_hour_default()

    async def _process_first_hour_default(self):
        """Обрабатывает первый час по умолчанию"""
        try:
            default_percent = 5
            views_needed = int(self.original_total_views * default_percent / 100)
            
            if views_needed > 0:
                tg_post_link = self._get_tg_post_link()
                
                # ⚠️ Вызываем api_send_views напрямую, без get_service_id()
                transactions = await api_send_views(views_needed, tg_post_link, self.task_id)
                
                # Сохраняем каждую транзакцию
                for tx in transactions:
                    await self._save_expense(
                        views_count=tx["views_count"],
                        price=tx["price"],
                        hour_percent=default_percent,
                        day_type="day1",
                        hour=1,
                        service_id=tx["service_id"]  # ⚠️ Реальный service_id!
                    )
        except Exception as e:
            log.error(f"❌ Ошибка обработки первого часа: {e}")

    # Обновить метод _save_expense в TrackedPost:
    async def _save_expense(self, views_count: int, price: float, hour_percent: float, 
                           day_type: str, hour: int, service_id: int, order_id: str = None):
        """Сохраняет информацию о расходе"""
        try:
            with get_session() as session:
                expense = ViewBoostExpense(
                    task_id=self.task_id,
                    views_count=views_count,
                    service_id=service_id,  # ⚠️ Реальный service_id из API
                    price=price,
                    # created_at автоматически
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
                
                log.debug(f"💾 Сохранен расход: {views_count} просмотров, "
                         f"service_id={service_id}, цена={price}, order={order_id}")
        except Exception as e:
            log.error(f"❌ Ошибка сохранения расхода: {e}")
    
    def stop(self):
        """Останавливает обработку поста"""
        self.is_running = False

class PostTracker:
    """Трекер для отслеживания постов и управления просмотрами"""
    
    def __init__(self, task_id: int, client: TelegramClient):
        self.task_id = task_id
        self.client = client
        self.active_posts: Dict[int, TrackedPost] = {}
        self.message_handlers = []
        self.target_entity = None
        self.target = None
        
    def _get_fresh_task_data(self) -> Tuple[Optional[ViewBoostTask], Optional[MainEntity]]:
        """Получает СВЕЖИЕ данные задачи и целевого канала из БД"""
        try:
            with get_session() as session:
                stmt = (
                    select(ViewBoostTask)
                    .where(ViewBoostTask.id == self.task_id)
                    .options(
                        joinedload(ViewBoostTask.target).joinedload(MainEntity.country)
                    )
                )
                task = session.execute(stmt).unique().scalar_one_or_none()
                
                if task and task.is_active and task.target:
                    log.info(f"✅ Загружены актуальные данные задачи #{self.task_id} и канала {task.target.name}")
                    return task, task.target
                else:
                    log.info(f"🛑 Задача #{self.task_id} неактивна или канал не найден")
                    return None, None
                    
        except Exception as e:
            log.error(f"❌ Ошибка загрузки задачи #{self.task_id}: {e}")
            return None, None

    async def _initialize_target_entity(self):
        """Инициализирует target_entity для канала"""
        if self.target_entity is None:
            task, target = self._get_fresh_task_data()
            if not task or not target:
                return False
            
            self.target = target
            try:
                self.target_entity = await ensure_peer(
                    self.client, 
                    telegram_id=target.telegram_id,
                    link=target.link
                )
                log.info(f"✅ Инициализирован target_entity для канала {target.name}")
                return True
            except Exception as e:
                log.error(f"❌ Ошибка инициализации target_entity для канала {target.name}: {e}")
                return False
        return True

    async def _setup_message_handler(self):
        """Настраивает обработчик новых сообщений для канала"""
        if not await self._initialize_target_entity():
            return False
            
        try:
            # Создаем обработчик для новых сообщений
            @self.client.on(events.NewMessage(chats=self.target_entity))
            async def handler(event):
                await self._handle_new_message(event.message)
            
            self.message_handlers.append(handler)
            log.info(f"✅ Настроен обработчик сообщений для канала {self.target.name}")
            return True
            
        except Exception as e:
            log.error(f"❌ Ошибка настройки обработчика сообщений для канала {self.target.name}: {e}")
            return False

    async def _handle_new_message(self, message: Message):
        """Обрабатывает новое сообщение из канала в реальном времени"""
        try:
            # Пропускаем служебные сообщения
            if isinstance(message, MessageService) or getattr(message, 'action', None):
                return
            
            # Обрабатываем только ПЕРВУЮ часть альбома
            if hasattr(message, 'grouped_id') and message.grouped_id is not None:
                # Проверяем, является ли это сообщение первой частью альбома
                # Для этого сравниваем его ID с другими сообщениями в том же альбоме
                # Первой частью считаем сообщение с наименьшим ID в группе
                async for other_message in self.client.iter_messages(
                    self.target_entity, 
                    limit=10  # Проверяем ближайшие сообщения
                ):
                    if (hasattr(other_message, 'grouped_id') and 
                        other_message.grouped_id == message.grouped_id and 
                        other_message.id < message.id):
                        # Нашли сообщение с меньшим ID в той же группе - пропускаем текущее
                        log.debug(f"⏭️ Пропуск части альбома {message.grouped_id}, сообщение {message.id} (не первое)")
                        return
                
                log.info(f"🎯 Обрабатываем ПЕРВУЮ часть альбома {message.grouped_id}, сообщение {message.id}")
            # Если это не альбом или это первая часть альбома - продолжаем обработку
            
            # Пропускаем уже отслеживаемые сообщения
            if message.id in self.active_posts:
                return
            
            # ЗАГРУЖАЕМ СВЕЖИЕ ДАННЫЕ ЗАДАЧИ
            task, target = self._get_fresh_task_data()
            if not task or not task.is_active or not target:
                return
            
            log.info(f"🔍 Получено новое сообщение {message.id} из канала {target.name} в реальном времени")
            await self._start_tracking(message, task, target)
            
        except Exception as e:
            log.error(f"❌ Ошибка обработки нового сообщения {message.id}: {e}")

    async def _start_tracking(self, message, task: ViewBoostTask, target: MainEntity):
        """Начинает отслеживание нового поста"""
        try:
            # Преобразование времени в часовой пояс канала
            if message.date.tzinfo is None:
                utc_time = UTC_TZ.localize(message.date)
            else:
                utc_time = message.date
            
            entity_tz = get_entity_timezone(target)
            message_time_local = utc_time.astimezone(entity_tz)
            
            # ИЗМЕНЕНИЕ: проверяем, что пост опубликован не более 24 часов назад
            current_time_local = datetime.now(entity_tz)
            time_diff = current_time_local - message_time_local
            
            if time_diff > timedelta(hours=24):
                log.debug(f"⏭️ Пропуск старого сообщения {message.id} (разница: {time_diff}, больше 24 часов)")
                return
            
            # ИСПРАВЛЕНИЕ: правильное определение типа поста с 4 режимами
            post_type = self._get_post_type(message_time_local)
            total_views_needed = self._calculate_total_views(task)
            
            # Минимальное количество просмотров
            if total_views_needed <= 0:
                total_views_needed = 100
                log.warning(f"⚠️ Установлено минимальное количество просмотров: {total_views_needed}")
            
            channel_username = None
            if target.link:
                channel_username = target.link.replace('https://t.me/', '').replace('@', '')
            
            tracked_post = TrackedPost(
                message_id=message.id,
                post_type=post_type,
                total_views_needed=total_views_needed,
                publish_time=message_time_local,  # Сохраняем в часовом поясе канала
                task_id=self.task_id,
                channel_telegram_id=target.telegram_id,
                channel_username=channel_username,
                entity_timezone=entity_tz  # Передаем часовой пояс канала
            )
            
            self.active_posts[message.id] = tracked_post
            
            # ИЗМЕНЕНИЕ: логируем оставшееся время для обработки (24 часа)
            time_remaining = timedelta(hours=24) - time_diff
            log.info(f"🎯 Начато отслеживание поста {message.id} для задачи #{self.task_id} "
                    f"(тип: {post_type}, нужно просмотров: {total_views_needed}, "
                    f"время публикации: {message_time_local.strftime('%Y-%m-%d %H:%M')} {entity_tz}, "
                    f"осталось времени: {time_remaining})")
            
            # Запускаем асинхронную обработку поста
            asyncio.create_task(tracked_post.process())
            
        except Exception as e:
            log.error(f"❌ Ошибка начала отслеживания поста {message.id}: {e}")

    async def check_historical_posts(self):
        """Проверяет исторические посты при запуске"""
        if not await self._initialize_target_entity():
            return
            
        # ЗАГРУЖАЕМ СВЕЖИЕ ДАННЫЕ ЗАДАЧИ (уже с загруженной country)
        task, target = self._get_fresh_task_data()
        if not task or not task.is_active or not target:
            return
            
        try:
            log.info(f"🔍 Проверка исторических сообщений в канале {target.name}")
            
            # Используем entity_tz из уже загруженного target с country
            entity_tz = get_entity_timezone(target)
            
            # Получаем сообщения за последние 24 часа
            messages = []
            processed_albums = set()  # Для отслеживания уже обработанных альбомов
            entity_tz = get_entity_timezone(target)
            current_time_entity = datetime.now(entity_tz)
            
            async for message in self.client.iter_messages(
                self.target_entity, 
                limit=50
            ):
                # Пропускаем служебные сообщения
                if isinstance(message, MessageService) or getattr(message, 'action', None):
                    continue
                    
                # ИСПРАВЛЕНИЕ: для альбомов обрабатываем только первую часть
                if hasattr(message, 'grouped_id') and message.grouped_id is not None:
                    if message.grouped_id in processed_albums:
                        log.debug(f"⏭️ Пропуск части альбома {message.grouped_id}, сообщение {message.id} (альбом уже обработан)")
                        continue
                    
                    # Находим первую часть альбома (сообщение с наименьшим ID)
                    first_message = message
                    async for other_message in self.client.iter_messages(
                        self.target_entity, 
                        limit=20
                    ):
                        if (hasattr(other_message, 'grouped_id') and 
                            other_message.grouped_id == message.grouped_id and 
                            other_message.id < first_message.id):
                            first_message = other_message
                    
                    # Если текущее сообщение не является первой частью - пропускаем
                    if first_message.id != message.id:
                        log.debug(f"⏭️ Пропуск части альбома {message.grouped_id}, сообщение {message.id} (не первое)")
                        continue
                    
                    # Помечаем альбом как обработанный
                    processed_albums.add(message.grouped_id)
                    log.info(f"🎯 Обрабатываем ПЕРВУЮ часть альбома {message.grouped_id}, сообщение {message.id}")
                
                # Фильтруем по времени в часовом поясе канала
                if message.date.tzinfo is None:
                    message_utc = UTC_TZ.localize(message.date)
                else:
                    message_utc = message.date
                
                message_time_entity = message_utc.astimezone(entity_tz)
                time_diff = current_time_entity - message_time_entity
                
                # Берем посты за последние 24 часа
                if time_diff <= timedelta(hours=24):
                    messages.append(message)
                else:
                    break
            
            log.info(f"📨 Получено {len(messages)} исторических сообщений из канала {target.name} за последние 24 часа")
            
            # Обрабатываем сообщения в обратном порядке (от старых к новым)
            for message in reversed(messages):
                await self._handle_new_message(message)
                    
        except Exception as e:
            log.error(f"❌ Ошибка проверки исторических постов: {e}")

    async def cleanup_old_posts(self):
        """Очищает старые отслеживаемые посты"""
        current_time = datetime.now(TZ)
        posts_to_remove = []
        
        for message_id, tracked_post in self.active_posts.items():
            # Используем часовой пояс канала для расчета времени
            current_time_entity = datetime.now(tracked_post.entity_timezone)
            time_since_publish = current_time_entity - tracked_post.publish_time
            
            # ИЗМЕНЕНИЕ: удаляем посты старше 36 часов (24 + запас)
            if (not tracked_post.is_running or 
                time_since_publish > timedelta(hours=36)):
                posts_to_remove.append(message_id)
        
        for message_id in posts_to_remove:
            if message_id in self.active_posts:
                self.active_posts[message_id].stop()
                del self.active_posts[message_id]
                log.info(f"🗑️ Удален отслеживаемый пост {message_id} (старше 36 часов)")
    
    def _get_post_type(self, post_date: datetime) -> str:
        """
        Определение типа поста с 4 режимами.
        Утро: 05:00-10:00, День: 10:00-16:00, Вечер: 16:00-22:00, Ночь: 22:00-05:00
        """
        try:
            post_time = post_date.time()
            
            # Утро: с 5:00 до 10:00
            if MORNING_START <= post_time < time(10, 0):
                post_type = "morning"
            # День: с 10:00 до 16:00
            elif time(10, 0) <= post_time < time(16, 0):
                post_type = "day"
            # Вечер: с 16:00 до 22:00
            elif time(16, 0) <= post_time < time(22, 0):
                post_type = "evening"
            # Ночь: с 22:00 до 5:00 следующего дня
            else:
                post_type = "night"
                
            log.info(f"🕒 Определен тип поста: {post_type} "
                    f"(время публикации: {post_time.strftime('%H:%M')} по времени канала)")
            return post_type
        except Exception as e:
            log.error(f"❌ Ошибка определения типа поста: {e}")
            return "day"  # По умолчанию дневной
    
    def _calculate_total_views(self, task: ViewBoostTask) -> int:
        """Рассчитывает общее количество необходимых просмотров"""
        try:
            views = int((task.view_coefficient / 100) * task.subscribers_count)
            log.info(f"📊 Расчет просмотров: {task.view_coefficient}% от {task.subscribers_count} подписчиков = {views} просмотров")
            return max(views, 100)  # Минимум 100 просмотров
        except Exception as e:
            log.error(f"❌ Ошибка расчета просмотров: {e}")
            return 100

    async def update_subscribers_count(self):
        """Обновляет количество подписчиков в канале"""
        # ЗАГРУЖАЕМ СВЕЖИЕ ДАННЫЕ ЗАДАЧИ (уже с загруженной country)
        task, target = self._get_fresh_task_data()
        if not task or not target:
            return
            
        try:
            log.info(f"👥 Обновление количества подписчиков для канала {target.name}")
            
            # Используем entity_tz из уже загруженного target с country
            entity_tz = get_entity_timezone(target)
            target_entity = await ensure_peer(
                self.client, 
                telegram_id=target.telegram_id,
                link=target.link
            )
            
            # Получаем информацию о канале
            channel = await self.client.get_entity(target_entity)
            subscribers = 0

            try:
                # Для каналов
                full = await self.client(functions.channels.GetFullChannelRequest(channel))
                if full.full_chat.participants_count:
                    subscribers = full.full_chat.participants_count
            except Exception:
                try:
                    # Для чатов и супергрупп
                    full = await self.client(functions.messages.GetFullChatRequest(channel.id))
                    if full.full_chat.participants_count:
                        subscribers = full.full_chat.participants_count
                except Exception:
                    log.warning(f"⚠️ Не удалось получить количество подписчиков для {target.name}")

            subscribers = int(subscribers) if subscribers else 0
            log.info(f"📊 Получено подписчиков: {subscribers} для канала {target.name}")
            
            # Обновляем подписчиков в БД
            with get_session() as session:
                task_db = session.get(ViewBoostTask, self.task_id)
                old_subscribers = task_db.subscribers_count
                task_db.subscribers_count = subscribers
                session.commit()
            
            log.info(f"📊 Обновлено количество подписчиков для задачи #{self.task_id}: {old_subscribers} -> {subscribers}")
                
        except Exception as e:
            log.error(f"❌ Ошибка обновления подписчиков для задачи #{self.task_id}: {e}")

    async def cleanup_handlers(self):
        """Очищает обработчики сообщений"""
        # Telethon автоматически управляет обработчиками, поэтому просто очищаем список
        self.message_handlers.clear()

class ViewBoostManager:
    """Менеджер для управления всеми задачами умного просмотра"""
    
    def __init__(self):
        self.trackers: Dict[int, PostTracker] = {}
        self.clients: Dict[int, TelegramClient] = {}
        self.running = False
        
    async def initialize(self):
        """Инициализация менеджера"""
        log.info("🔄 Инициализация менеджера умного просмотра...")
        await self._load_tasks()
        
    async def _load_tasks(self):
        """Загружает активные задачи из БД"""
        try:
            with get_session() as session:
                stmt = select(ViewBoostTask).where(
                    ViewBoostTask.is_active == True
                ).options(
                    joinedload(ViewBoostTask.target).joinedload(MainEntity.country)
                )
                tasks = session.execute(stmt).unique().scalars().all()
        except Exception as e:
            log.error(f"❌ Ошибка загрузки задач: {e}")
            tasks = []
        
        if not tasks:
            log.info("🔍 Нет активных задач умного просмотра")
            return
        
        log.info(f"🔍 Загружено {len(tasks)} активных задач")
        
        bot_ids = sorted(set(t.bot_id for t in tasks))
        
        try:
            with get_session() as session:
                stmt = select(BotSession).where(BotSession.id.in_(bot_ids))
                bots = {b.id: b for b in session.execute(stmt).scalars().all()}
        except Exception as e:
            log.error(f"❌ Ошибка загрузки ботов: {e}")
            bots = {}
        
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
                    
                    # ЗАПУСКАЕМ КЛИЕНТ В ФОНОВОМ РЕЖИМЕ ДЛЯ ПРОСЛУШИВАНИЯ
                    await client.start()
                    
                    self.clients[bot_id] = client
                    log.info(f"✅ Бот #{bot_id} авторизован и запущен для прослушивания")
                except Exception as e:
                    log.error(f"❌ Ошибка инициализации бота #{bot_id}: {e}")
        
        # Создание трекеров
        for task in tasks:
            client = self.clients.get(task.bot_id)
            if client and task.id not in self.trackers:
                tracker = PostTracker(task.id, client)
                self.trackers[task.id] = tracker
                await tracker.update_subscribers_count()
                
                # Настраиваем обработчики событий и проверяем исторические сообщения
                await tracker._setup_message_handler()
                await tracker.check_historical_posts()
                
                log.info(f"✅ Трекер создан для задачи #{task.id}")

    async def check_for_updates(self):
        """Проверяет обновления в БД и обновляет трекеры"""
        try:
            with get_session() as session:
                stmt = select(ViewBoostTask).where(
                    ViewBoostTask.is_active == True
                ).options(
                    joinedload(ViewBoostTask.target).joinedload(MainEntity.country)
                )
                active_tasks = session.execute(stmt).unique().scalars().all()
                
                active_task_ids = {t.id for t in active_tasks}
                current_tracker_ids = set(self.trackers.keys())
                
                # Удаляем неактивные трекеры
                for task_id in current_tracker_ids - active_task_ids:
                    if task_id in self.trackers:
                        tracker = self.trackers[task_id]
                        await tracker.cleanup_handlers()
                        for tracked_post in tracker.active_posts.values():
                            tracked_post.stop()
                        del self.trackers[task_id]
                        log.info(f"🗑️ Удален трекер для задачи #{task_id}")
                
                # Добавляем новые трекеры
                for task in active_tasks:
                    if task.id not in self.trackers:
                        client = self.clients.get(task.bot_id)
                        if client:
                            tracker = PostTracker(task.id, client)
                            self.trackers[task.id] = tracker
                            await tracker.update_subscribers_count()
                            
                            # Настраиваем обработчики событий и проверяем исторические сообщения
                            await tracker._setup_message_handler()
                            await tracker.check_historical_posts()
                            
                            log.info(f"✅ Добавлен трекер для задачи #{task.id}")
                        else:
                            log.warning(f"⚠️ Не найден клиент для бота #{task.bot_id} для задачи #{task.id}")
                
                # Обновляем существующие трекеры
                for task in active_tasks:
                    if task.id in self.trackers:
                        tracker = self.trackers[task.id]
                        # Проверяем исторические сообщения для существующих трекеров
                        await tracker.check_historical_posts()
                        
        except Exception as e:
            log.error(f"❌ Ошибка при проверке обновлений БД: {e}")

    async def process_all_tasks(self):
        """Обрабатывает все активные задачи"""
        for tracker in list(self.trackers.values()):
            try:
                await tracker.cleanup_old_posts()
            except Exception as e:
                log.error(f"❌ Ошибка обработки задачи #{tracker.task_id}: {e}")

    async def check_client_connections(self):
        """Проверяет соединения клиентов и перезапускает при необходимости"""
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
                            new_client.start()  # Запускаем в фоновом режиме
                            self.clients[bot_id] = new_client
                            log.info(f"✅ Клиент бота #{bot_id} перезапущен")
            except Exception as e:
                log.error(f"❌ Ошибка проверки соединения клиента #{bot_id}: {e}")

    async def cleanup(self):
        """Очистка ресурсов"""
        self.running = False
        
        for tracker in self.trackers.values():
            await tracker.cleanup_handlers()
            for tracked_post in tracker.active_posts.values():
                tracked_post.stop()
        
        for client in self.clients.values():
            try:
                await client.disconnect()
            except Exception:
                pass
        self.clients.clear()
        self.trackers.clear()

# Глобальный менеджер
manager = ViewBoostManager()

async def process_view_boost_tasks():
    """Обрабатывает все активные задачи накрутки просмотров"""
    try:
        await manager.check_for_updates()
        await manager.process_all_tasks()
        await manager.check_client_connections()  # Добавляем проверку соединений
    except Exception as e:
        log.error(f"❌ Ошибка в основном цикле обработки: {e}")

async def run_view_booster():
    """Запуск основного цикла накрутки просмотров"""
    log.info("🚀 Модуль умного просмотра запускается...")
    
    try:
        await manager.initialize()
        manager.running = True
        log.info("✅ Модуль умного просмотра успешно запущен")
        
        cycle_count = 0
        while manager.running:
            cycle_count += 1
            log.debug(f"🔄 Цикл обработки #{cycle_count}")
            
            await process_view_boost_tasks()
            await asyncio.sleep(CHECK_INTERVAL)
            
    except Exception as e:
        log.error(f"💥 Критическая ошибка в модуле умного просмотра: {e}")
    finally:
        await manager.cleanup()
        log.info("🛑 Модуль умного просмотра остановлен")

if __name__ == "__main__":
    asyncio.run(run_view_booster())