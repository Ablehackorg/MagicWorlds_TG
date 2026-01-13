# second_subscribers_booster.py

import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set
import pytz
from math import ceil

from telethon import TelegramClient
from telethon import functions
import aiohttp
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from utils.db_utils import get_session
from telegram_client import init_user_client
from entity_resolver import ensure_peer
from models import (
    SubscribersBoostTask, 
    SubscribersBoostExpense, 
    MainEntity, 
    BotSession, 
    BoosterSettings,
    BoosterServiceRotation
)

# Настройка логирования
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("second_subscribers_booster")

# Константы
TZ = pytz.timezone(os.getenv("TZ", "Europe/Moscow"))
UTC_TZ = pytz.UTC

# Настройки прокси
PROXY_URL = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")

# Глобальное хранилище данных в памяти
_daily_tracker_data = {}  # task_id -> (morning_count, morning_date, initial_count_phase2)


class DailySubscribersTracker:
    """Трекер для ежедневной проверки подписчиков (дважды в сутки)"""
    
    def __init__(self, task_id: int, client: TelegramClient):
        self.task_id = task_id
        self.client = client
        self.is_running = True
        
        # Данные задачи и цели
        self.current_task_data: Optional[SubscribersBoostTask] = None
        self.current_target_data: Optional[MainEntity] = None
        
        # Данные для ежедневной проверки (хранятся только в памяти)
        self.morning_count: Optional[int] = None  # Количество подписчиков утром
        self.morning_date: Optional[datetime.date] = None  # Дата утренней проверки
        self.initial_count_phase2: Optional[int] = None  # Начальное количество перед фазой 2
        
        # Время проверок
        self.morning_check_hour = 9  # 09:00 утра
        self.evening_phase1_hour = 22  # 22:00 - начало вечерней проверки (фаза 1)
        self.evening_phase1_end_minute = 50  # До 22:50 - окончание фазы 1
        self.evening_phase2_hour = 23  # 23:00 - вторая проверка (фаза 2)
        self.evening_phase2_end_minute = 30  # До 23:30 - окончание фазы 2
        
        # Загружаем данные из глобального хранилища при инициализации
        self._load_from_memory()
        
    def _load_from_memory(self):
        """Загружает данные из глобального хранилища в память"""
        if self.task_id in _daily_tracker_data:
            data = _daily_tracker_data[self.task_id]
            self.morning_count = data.get('morning_count')
            self.morning_date = data.get('morning_date')
            self.initial_count_phase2 = data.get('initial_count_phase2')
            log.info(f"📝 Загружены данные из памяти для задачи #{self.task_id}: "
                    f"дата={self.morning_date}, утро={self.morning_count}, фаза2={self.initial_count_phase2}")
    
    def _save_to_memory(self):
        """Сохраняет данные в глобальное хранилище"""
        _daily_tracker_data[self.task_id] = {
            'morning_count': self.morning_count,
            'morning_date': self.morning_date,
            'initial_count_phase2': self.initial_count_phase2
        }
        log.debug(f"💾 Сохранены данные в память для задачи #{self.task_id}")
    
    def _clear_memory(self):
        """Очищает данные из памяти для этой задачи"""
        if self.task_id in _daily_tracker_data:
            del _daily_tracker_data[self.task_id]
            log.info(f"🗑️ Очищены данные из памяти для задачи #{self.task_id}")
    
    def _load_task_data_from_db(self) -> tuple:
        """Загружает актуальные данные задачи из БД"""
        try:
            with get_session() as session:
                task_result = session.execute(
                    select(SubscribersBoostTask)
                    .options(joinedload(SubscribersBoostTask.target))
                    .where(SubscribersBoostTask.id == self.task_id)
                ).unique().scalar_one_or_none()
                
                if not task_result:
                    log.error(f"❌ Задача #{self.task_id} не найдена в БД")
                    return None, None
                
                log.info(f"✅ Загружена задача #{self.task_id} для канала {task_result.target.name if task_result.target else 'НЕТ'}")
                return task_result, task_result.target
                
        except Exception as e:
            log.error(f"❌ Ошибка загрузки задачи подписчиков #{self.task_id}: {e}")
            return None, None

    async def load_task_data(self):
        """Загружает данные задачи из БД"""
        self.current_task_data, self.current_target_data = self._load_task_data_from_db()
        
        log.info(f"📊 Результат загрузки данных для задачи #{self.task_id}: "
                f"task={self.current_task_data is not None}, "
                f"target={self.current_target_data is not None}")
        
        return self.current_task_data is not None and self.current_target_data is not None

    async def get_current_subscribers_count(self) -> int:
        """Получает текущее публичное количество подписчиков канала"""
        if not self.current_target_data:
            log.error(f"❌ Нет данных о цели для задачи #{self.task_id}")
            return 0
            
        try:
            # Получаем entity канала
            try:
                target_entity = await ensure_peer(
                    self.client,
                    telegram_id=self.current_target_data.telegram_id,
                    link=self.current_target_data.link
                )
            except Exception as e:
                log.error(f"❌ Ошибка получения entity канала {self.current_target_data.name}: {e}")
                return 0
            
            # Получаем публичную информацию о канале
            try:
                # Метод 1: Пытаемся получить базовую информацию
                channel_info = await self.client.get_entity(target_entity)
                
                # Пытаемся получить количество подписчиков
                if hasattr(channel_info, 'participants_count') and channel_info.participants_count:
                    subscribers_count = channel_info.participants_count
                    log.debug(f"📊 Получено количество подписчиков из channel_info: {subscribers_count}")
                    return int(subscribers_count)
                
                # Метод 2: Если в базовой информации нет, получаем полную информацию
                log.debug(f"🔍 Получение полной информации о канале {self.current_target_data.name}")
                full = await self.client(functions.channels.GetFullChannelRequest(channel_info))
                
                if full.full_chat and hasattr(full.full_chat, 'participants_count'):
                    subscribers_count = full.full_chat.participants_count
                    if subscribers_count:
                        log.debug(f"📊 Получено количество подписчиков из GetFullChannel: {subscribers_count}")
                        return int(subscribers_count)
                    
            except ValueError as e:
                log.warning(f"⚠️ Ошибка получения информации о канале {self.current_target_data.name}: {e}")
                return 0
            except Exception as e:
                log.warning(f"⚠️ Неожиданная ошибка при получении информации о канале {self.current_target_data.name}: {e}")
                return 0

            log.warning(f"⚠️ Не удалось получить количество подписчиков для канала {self.current_target_data.name}")
            return 0
                
        except Exception as e:
            log.error(f"❌ Критическая ошибка получения количества подписчиков для канала {self.current_target_data.name}: {e}")
            return 0

    async def _api_send_subscribers(self, subscribers_count: int, channel_link: str, api_key: str, service_id: int) -> Tuple[bool, float]:
        """Отправляет запрос на API для накрутки подписчиков и возвращает (успех, цена)"""
        try:
            if not api_key:
                log.error("❌ API KEY не установлен в настройках")
                return False, 0.0

            base_urls = ["https://twiboost.com/api/v2"]
            headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"}

            for base in base_urls:
                try:
                    add_url = f"{base}?action=add&service={service_id}&link={channel_link}&quantity={subscribers_count}&key={api_key}"
                    log.info(f"📤 Отправка API запроса для {subscribers_count} подписчиков: {add_url.replace(api_key, '***')}")

                    connector = aiohttp.TCPConnector(ssl=False)
                    timeout = aiohttp.ClientTimeout(total=15)

                    async with aiohttp.ClientSession(headers=headers, timeout=timeout, connector=connector) as session:
                        async with session.get(add_url, proxy=PROXY_URL) as response:
                            text = await response.text()
                            if response.status != 200:
                                log.error(f"❌ Ошибка API (subscribers): статус {response.status}, ответ: {text}")
                                continue

                            try:
                                result = await response.json(content_type=None)
                            except Exception as e:
                                log.error(f"⚠️ Некорректный JSON ответ (subscribers): {text}, ошибка: {e}")
                                continue

                            order_id = result.get("order")
                            if not order_id:
                                log.error(f"❌ Ответ без 'order': {result}")
                                continue

                            log.info(f"✅ Заказ на подписчиков создан успешно, order={order_id}")

                            # Сохраняем заказ в БД
                            try:
                                await self._save_booster_order(
                                    service_id=service_id,
                                    external_order_id=str(order_id),
                                    quantity=subscribers_count,
                                    price=0.0  # Пока неизвестно, обновим позже
                                )
                            except Exception as e:
                                log.error(f"❌ Ошибка сохранения заказа в БД: {e}")

                            # Ждем немного перед проверкой статуса
                            await asyncio.sleep(2)
                            
                            status_url = f"{base}?action=status&order={order_id}&key={api_key}"
                            async with session.get(status_url, proxy=PROXY_URL) as status_response:
                                status_text = await status_response.text()
                                if status_response.status != 200:
                                    log.error(f"❌ Ошибка API (status): {status_response.status}, ответ: {status_text}")
                                    continue

                                try:
                                    status_data = await status_response.json(content_type=None)
                                except Exception as e:
                                    log.error(f"⚠️ Некорректный JSON ответ (status): {status_text}, ошибка: {e}")
                                    continue

                                charge = status_data.get("charge")
                                if charge is None:
                                    log.warning(f"⚠️ Цена (charge) не найдена в ответе: {status_data}")
                                    continue

                                log.info(f"💰 Получена цена (charge) за подписчиков: {charge}")
                                
                                # Обновляем заказ в БД с ценой
                                await self._update_booster_order(str(order_id), float(charge))
                                
                                return True, float(charge)

                except Exception as e:
                    log.error(f"❌ Ошибка при работе с базовым URL {base}: {e}")
                    continue

            log.error("❌ Все попытки отправки запроса на подписчиков завершились ошибкой")
            return False, 0.0

        except Exception as e:
            log.error(f"💥 Критическая ошибка при работе с API подписчиков: {e}")
            return False, 0.0

    async def _save_booster_order(self, service_id: int, external_order_id: str, quantity: int, price: float):
        """Сохраняет заказ в таблицу BoosterOrder"""
        try:
            with get_session() as session:
                from models import BoosterOrder
                
                order = BoosterOrder(
                    task_id=self.task_id,
                    task_type="subscribers",
                    service_id=service_id,
                    external_order_id=external_order_id,
                    quantity=quantity,
                    price=price,
                    status='pending' if price == 0 else 'in_progress'
                )
                session.add(order)
                session.commit()
                log.debug(f"💾 Сохранен заказ в BoosterOrder: task_id={self.task_id}, order={external_order_id}")
        except Exception as e:
            log.error(f"❌ Ошибка сохранения заказа в BoosterOrder: {e}")

    async def _update_booster_order(self, external_order_id: str, price: float):
        """Обновляет заказ в BoosterOrder с ценой"""
        try:
            with get_session() as session:
                from models import BoosterOrder
                from sqlalchemy import update
                
                stmt = update(BoosterOrder).where(
                    BoosterOrder.external_order_id == external_order_id
                ).values(
                    price=price,
                    status='in_progress',
                    updated_at=datetime.utcnow()
                )
                session.execute(stmt)
                session.commit()
                log.debug(f"💾 Обновлен заказ в BoosterOrder: order={external_order_id}, price={price}")
        except Exception as e:
            log.error(f"❌ Ошибка обновления заказа в BoosterOrder: {e}")

    async def _save_expense(self, subscribers_count: int, price: float, service_id: int, phase: int = 1, order_id: str = None):
        """Сохраняет информацию о расходе на подписчиков и привязывает к заказу"""
        try:
            with get_session() as session:
                expense = SubscribersBoostExpense(
                    task_id=self.task_id,
                    subscribers_count=subscribers_count,
                    price=price,
                    service_id=service_id,
                    metadata_={
                        "source": "daily_check",
                        "check_type": "public_count",
                        "phase": phase,
                        "timestamp": datetime.now(TZ).isoformat()
                    }
                )
                session.add(expense)
                session.flush()  # Получаем ID расхода
                
                # Обновляем заказ в БД с expense_id, если известен order_id
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
                log.info(f"💾 Сохранен расход на подписчиков (фаза {phase}): {subscribers_count} подписчиков, цена: {price}, order: {order_id}")
        except Exception as e:
            log.error(f"❌ Ошибка сохранения расхода на подписчиков: {e}")

    def _get_booster_settings(self, session):
        """Получает глобальные настройки бустера из БД"""
        try:
            from subscribers_booster import get_booster_settings as get_settings
            return get_settings(session)
        except ImportError:
            try:
                from sqlalchemy.orm import selectinload
                settings = session.execute(
                    select(BoosterSettings)
                    .options(
                        selectinload(BoosterSettings.tariffs),
                    )
                ).unique().scalar_one_or_none()
                
                if settings:
                    log.info(f"✅ Загружены глобальные настройки бустера")
                    return settings
                else:
                    log.error("❌ Глобальные настройки бустера не найдены в БД")
                    return None
                    
            except Exception as e:
                log.error(f"❌ Ошибка загрузки глобальных настроек бустера: {e}")
                return None

    async def morning_check(self):
        """Утренняя проверка в начале суток"""
        if not self.is_running or not self.current_target_data:
            return False
            
        today = datetime.now(TZ).date()
        
        # Проверяем, не выполнялась ли уже сегодня утренняя проверка
        if self.morning_date == today and self.morning_count is not None:
            log.info(f"⏭️ Утренняя проверка для задачи #{self.task_id} уже выполнена сегодня")
            return True
            
        try:
            log.info(f"🌅 ВЫПОЛНЯЮ УТРЕННЮЮ ПРОВЕРКУ для задачи #{self.task_id}, канал: {self.current_target_data.name}")
            
            # Получаем текущее публичное количество подписчиков
            current_count = await self.get_current_subscribers_count()
            
            if current_count == 0:
                log.error(f"❌ Не удалось получить количество подписчиков для канала {self.current_target_data.name}")
                return False
            
            # Сохраняем утреннее количество
            self.morning_count = current_count
            self.morning_date = today
            self.initial_count_phase2 = None  # Сбрасываем данные фазы 2
            self._save_to_memory()
            
            log.info(f"✅ УТРЕННЯЯ ПРОВЕРКА ЗАВЕРШЕНА для задачи #{self.task_id}: {current_count} подписчиков")
            return True
            
        except Exception as e:
            log.error(f"❌ Ошибка утренней проверки для задачи #{self.task_id}: {e}")
            return False

    def _calculate_distribution(self, total_subscribers: int, max_batches: int, min_batch_size: int = 5) -> List[int]:
        """Рассчитывает распределение подписчиков по партиям"""
        if total_subscribers <= 0:
            return []
        
        if total_subscribers <= min_batch_size:
            return [total_subscribers]
        
        # Базовая логика распределения
        batches = []
        
        if max_batches == 1:
            # Только одна партия
            return [total_subscribers]
        
        # Пытаемся равномерно распределить
        base_size = total_subscribers // max_batches
        remainder = total_subscribers % max_batches
        
        # Создаем базовые партии
        for i in range(max_batches):
            batch_size = base_size
            if i < remainder:
                batch_size += 1
            batches.append(batch_size)
        
        # Объединяем маленькие партии
        result_batches = []
        temp_batch = 0
        
        for batch in batches:
            if batch < min_batch_size:
                temp_batch += batch
            else:
                if temp_batch > 0:
                    # Добавляем маленькую партию к предыдущей нормальной
                    if result_batches:
                        result_batches[-1] += temp_batch
                    else:
                        result_batches.append(temp_batch)
                    temp_batch = 0
                result_batches.append(batch)
        
        # Если осталась маленькая партия
        if temp_batch > 0:
            if result_batches:
                result_batches[-1] += temp_batch
            else:
                result_batches.append(temp_batch)
        
        log.info(f"📊 Распределение {total_subscribers} подписчиков на {len(result_batches)} партий: {result_batches}")
        return result_batches

    async def _send_subscribers_batch(self, batch_size: int, service_id: int) -> Tuple[bool, float]:
        """Отправляет одну партию подписчиков через API"""
        if not self.current_target_data:
            return False, 0.0
            
        channel_link = self.current_target_data.link if self.current_target_data.link else f"https://t.me/c/{abs(self.current_target_data.telegram_id)}"
        
        try:
            with get_session() as session:
                settings = self._get_booster_settings(session)
                if not settings or not settings.api_key:
                    log.error("❌ Нет настроек или API ключа для отправки подписчиков")
                    return False, 0.0
                
                log.info(f"📤 Отправка партии {batch_size} подписчиков для задачи #{self.task_id}")
                success, price = await self._api_send_subscribers(
                    subscribers_count=batch_size,
                    channel_link=channel_link,
                    api_key=settings.api_key,
                    service_id=service_id
                )
                
                return success, price
                
        except Exception as e:
            log.error(f"❌ Ошибка отправки партии подписчиков: {e}")
            return False, 0.0

    async def evening_phase1(self):
        """Вечерняя проверка - фаза 1 (с 22:00 до 22:50)"""
        if not self.is_running or not self.current_target_data:
            return False
            
        today = datetime.now(TZ).date()
        
        # Проверяем, выполнялась ли сегодня утренняя проверка
        if self.morning_date != today or self.morning_count is None:
            log.warning(f"⚠️ Для задачи #{self.task_id} не выполнена утренняя проверка сегодня")
            return False
        
        try:
            log.info(f"🌃 ВЫПОЛНЯЮ ВЕЧЕРНЮЮ ПРОВЕРКУ (ФАЗА 1) для задачи #{self.task_id}, канал: {self.current_target_data.name}")
            
            # Получаем текущее публичное количество подписчиков
            evening_count = await self.get_current_subscribers_count()
            
            if evening_count == 0:
                log.error(f"❌ Не удалось получить количество подписчиков для канала {self.current_target_data.name}")
                return False
            
            # Сохраняем начальное количество для фазы 2
            self.initial_count_phase2 = evening_count
            self._save_to_memory()
            
            # Сравниваем с утренним количеством
            subscribers_change = self.morning_count - evening_count
            
            log.info(f"📊 ИЗМЕНЕНИЕ ПУБЛИЧНЫХ ПОДПИСЧИКОВ за день в {self.current_target_data.name}: "
                    f"утро={self.morning_count}, вечер(фаза1)={evening_count}, изменение={subscribers_change}")
            
            # Если количество подписчиков уменьшилось
            if subscribers_change > 0:
                log.info(f"📉 Обнаружена потеря {subscribers_change} публичных подписчиков за день в {self.current_target_data.name}")
                
                # Рассчитываем количество для отправки с учетом лимита
                subscribers_to_send = self._calculate_subscribers_to_send(subscribers_change)
                
                if subscribers_to_send > 0:
                    # Распределяем отправку до 22:50
                    await self._distribute_and_send_phase1(subscribers_to_send)
                else:
                    log.info(f"✅ Потеря подписчиков обнаружена, но отправка не требуется (лимит или 0)")
            else:
                log.info(f"✅ Количество публичных подписчиков не уменьшилось за день или увеличилось")
            
            log.info(f"✅ ВЕЧЕРНЯЯ ПРОВЕРКА (ФАЗА 1) ЗАВЕРШЕНА для задачи #{self.task_id}")
            return True
            
        except Exception as e:
            log.error(f"❌ Ошибка вечерней проверки (фаза 1) для задачи #{self.task_id}: {e}")
            return False

    async def _distribute_and_send_phase1(self, total_subscribers: int):
        """Распределяет и отправляет подписчики в фазе 1 до 22:50 с учетом очередей"""
        if total_subscribers <= 0:
            return
        
        # Рассчитываем время до 22:50
        now = datetime.now(TZ)
        end_time = now.replace(hour=self.evening_phase1_hour, minute=self.evening_phase1_end_minute, second=0)
        
        if now >= end_time:
            log.error(f"❌ Уже позже 22:50, нельзя начинать фазу 1")
            return
        
        time_available = (end_time - now).total_seconds()
        
        # Рассчитываем количество партий (минимум 2 минуты между партиями)
        min_interval = 120  # 2 минуты между партиями
        max_batches = max(1, int(time_available // min_interval))
        
        # Рассчитываем распределение по партиям
        batches = self._calculate_distribution(total_subscribers, max_batches, min_batch_size=5)
        
        if not batches:
            log.error(f"❌ Не удалось рассчитать распределение для {total_subscribers} подписчиков")
            return
        
        log.info(f"📊 ФАЗА 1: Распределение {total_subscribers} подписчиков на {len(batches)} партий, время до 22:50: {time_available:.0f} сек")
        
        try:
            with get_session() as session:
                settings = self._get_booster_settings(session)
                if not settings:
                    log.error("❌ Не найдены глобальные настройки бустера")
                    return
                
                total_sent = 0
                total_price = 0.0
                
                for i, batch_size in enumerate(batches):
                    # Проверяем, не вышли ли за временные рамки
                    current_time = datetime.now(TZ)
                    if current_time >= end_time:
                        log.warning(f"⚠️ Достигнуто время 22:50, прекращаем фазу 1")
                        break
                    
                    log.info(f"🚀 ФАЗА 1: Отправка партии {i+1}/{len(batches)} ({batch_size} подписчиков)")
                    
                    # ИСПОЛЬЗУЕМ НОВЫЙ МЕТОД С ПРОВЕРКОЙ ОЧЕРЕДЕЙ
                    service_id = await BoosterServiceRotation.get_next_service_id_for_module(
                        session=session,
                        module_name="subscribers",
                        tariffs=settings.tariffs,
                        default_service_id=settings.subscribers_service_id,
                        count=batch_size,
                        booster_settings=settings  # Передаем настройки для проверки очередей
                    )
                    
                    if not service_id:
                        log.error(f"❌ Не найден service_id для подписчиков")
                        continue
                    
                    # Отправляем партию
                    channel_link = self.current_target_data.link if self.current_target_data.link else f"https://t.me/c/{abs(self.current_target_data.telegram_id)}"
                    
                    success, price = await self._api_send_subscribers(
                        subscribers_count=batch_size,
                        channel_link=channel_link,
                        api_key=settings.api_key,
                        service_id=service_id
                    )
                    
                    if success and price > 0:
                        total_sent += batch_size
                        total_price += price
                        # Сохраняем расход
                        await self._save_expense(batch_size, price, service_id, phase=1, order_id=order_id if 'order_id' in locals() else None)
                        log.info(f"✅ ФАЗА 1: Партия {i+1} отправлена успешно")
                    else:
                        log.error(f"❌ ФАЗА 1: Ошибка отправки партии {i+1}")
                    
                    # Задержка между партиями (кроме последней)
                    if i < len(batches) - 1:
                        wait_time = min_interval
                        # Корректируем время, если нужно успеть до 22:50
                        time_left = (end_time - datetime.now(TZ)).total_seconds()
                        if time_left < wait_time * 2:  # Если мало времени
                            wait_time = max(60, time_left / 2)  # Минимум 1 минута
                        
                        log.info(f"⏳ ФАЗА 1: Задержка {wait_time:.0f} сек перед следующей партией")
                        await asyncio.sleep(wait_time)
                
                if total_sent > 0:
                    log.info(f"🎉 ФАЗА 1 ЗАВЕРШЕНА: отправлено {total_sent}/{total_subscribers} подписчиков, общая цена: {total_price}")
                else:
                    log.warning(f"⚠️ ФАЗА 1: Не отправлено ни одного подписчика")
                    
        except Exception as e:
            log.error(f"❌ Ошибка при распределении и отправке в фазе 1: {e}")

    async def evening_phase2(self):
        """Вечерняя проверка - фаза 2 (23:00 - 23:30)"""
        if not self.is_running or not self.current_target_data:
            return False
            
        today = datetime.now(TZ).date()
        
        # Проверяем, сохранилось ли начальное количество из фазы 1
        if self.initial_count_phase2 is None:
            log.warning(f"⚠️ Для задачи #{self.task_id} не выполнена фаза 1 или данные потеряны")
            return False
        
        try:
            log.info(f"🌌 ВЫПОЛНЯЮ ВЕЧЕРНЮЮ ПРОВЕРКУ (ФАЗА 2) для задачи #{self.task_id}, канал: {self.current_target_data.name}")
            
            # Получаем текущее публичное количество подписчиков
            current_count = await self.get_current_subscribers_count()
            
            if current_count == 0:
                log.error(f"❌ Не удалось получить количество подписчиков для канала {self.current_target_data.name}")
                return False
            
            # Сравниваем с количеством из фазы 1
            subscribers_change = self.initial_count_phase2 - current_count
            
            log.info(f"📊 ИЗМЕНЕНИЕ ПУБЛИЧНЫХ ПОДПИСЧИКОВ после фазы 1 в {self.current_target_data.name}: "
                    f"начало фазы1={self.initial_count_phase2}, текущее={current_count}, изменение={subscribers_change}")
            
            # Если количество подписчиков всё ещё меньше
            if subscribers_change > 0:
                log.info(f"📉 После фазы 1 всё ещё не хватает {subscribers_change} публичных подписчиков в {self.current_target_data.name}")
                
                # Рассчитываем количество для отправки с учетом лимита
                subscribers_to_send = self._calculate_subscribers_to_send(subscribers_change)
                
                if subscribers_to_send > 0:
                    # Отправляем одной партией
                    await self._send_final_batch(subscribers_to_send)
                else:
                    log.info(f"✅ Недостача подписчиков обнаружена, но отправка не требуется (лимит или 0)")
            else:
                log.info(f"✅ Количество публичных подписчиков восстановлено после фазы 1")
            
            # Очищаем все данные для следующего дня
            self.morning_count = None
            self.morning_date = None
            self.initial_count_phase2 = None
            self._clear_memory()
            
            log.info(f"✅ ВЕЧЕРНЯЯ ПРОВЕРКА (ФАЗА 2) ЗАВЕРШЕНА для задачи #{self.task_id}")
            return True
            
        except Exception as e:
            log.error(f"❌ Ошибка вечерней проверки (фаза 2) для задачи #{self.task_id}: {e}")
            return False

    async def _send_final_batch(self, subscribers_to_send: int):
        """Отправляет финальную партию подписчиков в фазе 2 с учетом очередей"""
        if subscribers_to_send <= 0:
            return
        
        try:
            with get_session() as session:
                settings = self._get_booster_settings(session)
                if not settings:
                    log.error("❌ Не найдены глобальные настройки бустера")
                    return
                
                # ИСПОЛЬЗУЕМ НОВЫЙ МЕТОД С ПРОВЕРКОЙ ОЧЕРЕДЕЙ
                service_id = await BoosterServiceRotation.get_next_service_id_for_module(
                    session=session,
                    module_name="subscribers",
                    tariffs=settings.tariffs,
                    default_service_id=settings.subscribers_service_id,
                    count=subscribers_to_send,
                    booster_settings=settings  # Передаем настройки для проверки очередей
                )
                
                if not service_id:
                    log.error("❌ Не найден service_id для подписчиков")
                    return
                
                log.info(f"🚀 ФАЗА 2: Отправка финальной партии {subscribers_to_send} подписчиков, service_id: {service_id}")
                
                # Отправляем партию
                channel_link = self.current_target_data.link if self.current_target_data.link else f"https://t.me/c/{abs(self.current_target_data.telegram_id)}"
                
                success, price = await self._api_send_subscribers(
                    subscribers_count=subscribers_to_send,
                    channel_link=channel_link,
                    api_key=settings.api_key,
                    service_id=service_id
                )
                
                if success and price > 0:
                    # Сохраняем расход
                    await self._save_expense(subscribers_to_send, price, service_id, phase=2, order_id=order_id if 'order_id' in locals() else None)
                    log.info(f"✅ ФАЗА 2: Финальная партия отправлена успешно, цена: {price}")
                else:
                    log.error(f"❌ ФАЗА 2: Ошибка отправки финальной партии")
                    
        except Exception as e:
            log.error(f"❌ Ошибка при отправке финальной партии: {e}")

    def _calculate_subscribers_to_send(self, lost_subscribers: int) -> int:
        """Рассчитывает количество подписчиков для отправки в API с учетом лимита"""
        if lost_subscribers <= 0:
            return 0
            
        if not self.current_task_data:
            log.error("❌ Данные задачи не загружены")
            return 0
            
        if self.current_task_data.max_subscribers > 0:
            subscribers_to_send = min(lost_subscribers, self.current_task_data.max_subscribers)
            if subscribers_to_send < lost_subscribers:
                log.info(f"📊 Лимит ограничивает отправку: {lost_subscribers} потеряно → {subscribers_to_send} для отправки")
            else:
                log.info(f"📊 Отправка {subscribers_to_send} подписчиков для компенсации {lost_subscribers} потерь")
            return subscribers_to_send
        else:
            log.info(f"📊 Отправка {lost_subscribers} подписчиков для компенсации потерь")
            return lost_subscribers

    async def check_task_active(self) -> bool:
        """Быстрая проверка, что задача всё ещё активна в БД"""
        try:
            with get_session() as session:
                task_active = session.execute(
                    select(SubscribersBoostTask.is_active)
                    .where(SubscribersBoostTask.id == self.task_id)
                ).scalar_one_or_none()
                
                if task_active is None:
                    log.warning(f"🛑 Задача #{self.task_id} не найдена в БД")
                    return False
                
                return task_active
                
        except Exception as e:
            log.error(f"❌ Ошибка проверки активности задачи #{self.task_id}: {e}")
            return True  # Продолжаем работу при ошибке проверки

    def stop(self):
        """Останавливает трекер и очищает данные из памяти"""
        self.is_running = False
        self._clear_memory()
        log.info(f"🛑 Остановлен трекер ежедневных проверок для задачи #{self.task_id}")


class DailySubscribersManager:
    """Менеджер для управления всеми задачами ежедневной проверки подписчиков"""
    
    def __init__(self):
        self.trackers: Dict[int, DailySubscribersTracker] = {}
        self.clients: Dict[int, TelegramClient] = {}
        self.periodic_tasks: Dict[int, asyncio.Task] = {}
        
    async def initialize(self):
        """Инициализация менеджера"""
        log.info("🔄 ИНИЦИАЛИЗАЦИЯ менеджера ежедневной проверки подписчиков...")
        await self._load_tasks()
        
    async def _load_tasks(self):
        """Загружает активные задачи из БД и настраивает трекеры"""
        with get_session() as session:
            tasks_result = session.execute(
                select(SubscribersBoostTask)
                .options(joinedload(SubscribersBoostTask.target), joinedload(SubscribersBoostTask.bot))
                .where(SubscribersBoostTask.is_active == True)
            ).unique().scalars().all()
        
        if not tasks_result:
            log.info("🔍 Нет активных задач для ежедневной проверки подписчиков")
            return
        
        log.info(f"🔍 Загружено {len(tasks_result)} активных задач для ежедневной проверки")
        
        bot_ids = sorted(set(t.bot_id for t in tasks_result))
        
        with get_session() as session:
            bots = {b.id: b for b in session.execute(
                select(BotSession).where(BotSession.id.in_(bot_ids))
            ).scalars().all()}
        
        # Инициализация клиентов
        for bot_id in bot_ids:
            if bot_id not in self.clients:
                try:
                    client = init_user_client(bots[bot_id])
                    await client.start()
                    if not await client.is_user_authorized():
                        raise RuntimeError(f"Бот #{bot_id} не авторизован")
                    self.clients[bot_id] = client
                    log.info(f"✅ Бот #{bot_id} авторизован для ежедневной проверки подписчиков")
                except Exception as e:
                    log.error(f"❌ Ошибка инициализации бота #{bot_id}: {e}")
        
        # Создание и настройка трекеров
        for task in tasks_result:
            client = self.clients.get(task.bot_id)
            if client and task.id not in self.trackers:
                tracker = DailySubscribersTracker(task.id, client)
                if await tracker.load_task_data():
                    self.trackers[task.id] = tracker
                    self._start_periodic_check(task.id, tracker)
                    log.info(f"✅ Трекер ежедневной проверки создан для задачи #{task.id}")
                else:
                    log.error(f"❌ Не удалось загрузить данные для задачи #{task.id}")

    def _start_periodic_check(self, task_id: int, tracker: DailySubscribersTracker):
        """Запускает периодическую проверку для задачи"""
        async def daily_check_loop():
            """Основной цикл ежедневных проверок"""
            while tracker.is_running:
                try:
                    # Проверяем активность задачи
                    if not await tracker.check_task_active():
                        log.info(f"🛑 Задача #{task_id} деактивирована в БД")
                        tracker.stop()
                        break
                    
                    # Получаем текущее время
                    now = datetime.now(TZ)
                    current_hour = now.hour
                    current_minute = now.minute
                    
                    # Определяем, какая проверка должна быть выполнена
                    # Утренняя проверка (09:00-09:10)
                    if current_hour == tracker.morning_check_hour and 0 <= current_minute < 10:
                        log.info(f"🌅 ВРЕМЯ УТРЕННЕЙ ПРОВЕРКИ для задачи #{task_id}")
                        await tracker.morning_check()
                        await asyncio.sleep(3600)  # Ждем 1 час
                    
                    # Вечерняя проверка - фаза 1 (22:00-22:10)
                    elif current_hour == tracker.evening_phase1_hour and 0 <= current_minute < 10:
                        log.info(f"🌃 ВРЕМЯ ВЕЧЕРНЕЙ ПРОВЕРКИ (ФАЗА 1) для задачи #{task_id}")
                        await tracker.evening_phase1()
                        await asyncio.sleep(3600)  # Ждем 1 час
                    
                    # Вечерняя проверка - фаза 2 (23:00-23:10)
                    elif current_hour == tracker.evening_phase2_hour and 0 <= current_minute < 10:
                        log.info(f"🌌 ВРЕМЯ ВЕЧЕРНЕЙ ПРОВЕРКИ (ФАЗА 2) для задачи #{task_id}")
                        await tracker.evening_phase2()
                        await asyncio.sleep(3600)  # Ждем 1 час
                    
                    else:
                        # Не время проверки, вычисляем сколько ждать до следующей проверки
                        wait_time = self._calculate_wait_time(now, tracker)
                        if wait_time > 60:  # Логируем только длительные ожидания
                            log.debug(f"⏳ До следующей проверки задачи #{task_id}: {wait_time/60:.1f} мин")
                        await asyncio.sleep(min(wait_time, 300))  # Максимум 5 минут
                        
                except Exception as e:
                    log.error(f"❌ Ошибка в цикле ежедневной проверки задачи #{task_id}: {e}")
                    await asyncio.sleep(300)  # Ждем 5 минут при ошибке
        
        # Запускаем задачу
        task = asyncio.create_task(daily_check_loop())
        self.periodic_tasks[task_id] = task
        log.info(f"⏰ Запущен цикл ежедневных проверок для задачи #{task_id}")

    def _calculate_wait_time(self, now: datetime, tracker: DailySubscribersTracker) -> int:
        """Вычисляет время до следующей проверки в секундах"""
        current_hour = now.hour
        current_minute = now.minute
        current_second = now.second
        
        # Определяем время следующей проверки
        next_check = None
        
        # Список всех проверок за сегодня
        checks_today = [
            (tracker.morning_check_hour, 0, "утренней"),
            (tracker.evening_phase1_hour, 0, "вечерней (фаза 1)"),
            (tracker.evening_phase2_hour, 0, "вечерней (фаза 2)")
        ]
        
        # Сортируем по времени
        checks_today.sort()
        
        # Ищем следующую проверку сегодня
        for hour, minute, name in checks_today:
            if hour > current_hour or (hour == current_hour and minute > current_minute):
                next_check = (hour, minute, name)
                break
        
        # Если сегодня больше нет проверок, берем утреннюю завтра
        if next_check is None:
            next_check = (tracker.morning_check_hour + 24, 0, "утренней (завтра)")
        
        # Вычисляем разницу во времени в секундах
        target_hour, target_minute, check_name = next_check
        
        seconds_to_wait = (target_hour - current_hour) * 3600 + \
                         (target_minute - current_minute) * 60 - \
                         current_second
        
        log.debug(f"⏰ Следующая проверка ({check_name}) для задачи #{tracker.task_id} через {seconds_to_wait/60:.1f} мин")
        return max(60, seconds_to_wait)  # Минимум 1 минута

    async def check_for_updates(self):
        """Проверяет обновления в БД и обновляет трекеры (каждые 5 минут)"""
        with get_session() as session:
            active_tasks = session.execute(
                select(SubscribersBoostTask)
                .where(SubscribersBoostTask.is_active == True)
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
                    log.info(f"🗑️ Удален трекер ежедневной проверки для задачи #{task_id}")
            
            # Добавляем новые трекеры
            for task in active_tasks:
                if task.id not in self.trackers:
                    client = self.clients.get(task.bot_id)
                    if client:
                        tracker = DailySubscribersTracker(task.id, client)
                        if await tracker.load_task_data():
                            self.trackers[task.id] = tracker
                            self._start_periodic_check(task.id, tracker)
                            log.info(f"✅ Добавлен трекер ежедневной проверки для задачи #{task.id}")
    
    async def cleanup(self):
        """Очистка ресурсов"""
        log.info("🧹 Начало очистки ресурсов менеджера ежедневной проверки...")
        for task_id, tracker in self.trackers.items():
            tracker.stop()
        for task_id, task in self.periodic_tasks.items():
            task.cancel()
        for client in self.clients.values():
            try:
                await client.disconnect()
                log.debug(f"🔌 Клиент отключен")
            except Exception as e:
                log.debug(f"⚠️ Ошибка отключения клиента: {e}")
        
        self.trackers.clear()
        self.clients.clear()
        self.periodic_tasks.clear()
        
        # Очищаем глобальное хранилище
        global _daily_tracker_data
        _daily_tracker_data.clear()
        log.info("✅ Ресурсы менеджера ежедневной проверки очищены")


# Глобальный менеджер
manager = DailySubscribersManager()

async def run_second_subscribers_checker():
    """Запуск основного цикла ежедневной проверки подписчиков"""
    log.info("🚀 МОДУЛЬ ЕЖЕДНЕВНОЙ ПРОВЕРКИ ПОДПИСЧИКОВ ЗАПУСКАЕТСЯ...")
    
    try:
        await manager.initialize()
        log.info("✅ Модуль ежедневной проверки подписчиков успешно запущен")
        
        # Основной цикл для проверки обновлений БД
        check_counter = 0
        while True:
            try:
                await asyncio.sleep(60)  # Проверяем каждую минуту
                check_counter += 1
                
                # Каждые 5 минут проверяем обновления БД
                if check_counter >= 5:
                    await manager.check_for_updates()
                    check_counter = 0
                    
            except KeyboardInterrupt:
                log.info("🛑 Остановка по Ctrl+C")
                break
            except Exception as e:
                log.error(f"❌ Ошибка в основном цикле проверки подписчиков: {e}")
                await asyncio.sleep(60)
            
    except Exception as e:
        log.error(f"💥 Критическая ошибка в модуле ежедневной проверки подписчиков: {e}")
    finally:
        await manager.cleanup()
        log.info("🛑 Модуль ежедневной проверки подписчиков остановлен")


if __name__ == "__main__":
    asyncio.run(run_second_subscribers_checker())