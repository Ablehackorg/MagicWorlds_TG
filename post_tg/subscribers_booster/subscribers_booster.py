# subscribers_booster.py

import os
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set
import pytz

from telethon import TelegramClient, events
from telethon import functions, types
from telethon.tl.types import Channel, Chat, MessageService
from telethon.errors import FloodWaitError
import aiohttp
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from utils.db_utils import get_session
from telegram_client import init_user_client
from entity_resolver import ensure_peer
from models import (SubscribersBoostTask, SubscribersCheck, BoosterServiceRotation,
                   SubscribersBoostExpense, MainEntity, BotSession, BoosterSettings, BoosterTariff)
from models import BoosterOrder
# Настройка логирования
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                   format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("subscribers_booster")
log.info(get_session)
# Константы
DEFAULT_CHECK_INTERVAL = int(os.getenv("SUBSCRIBERS_CHECK_INTERVAL", "60"))
TZ = pytz.timezone(os.getenv("TZ", "Europe/Moscow"))
UTC_TZ = pytz.UTC

# Настройки прокси
PROXY_URL = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")

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


async def api_send_subscribers(subscribers_count: int, channel_link: str, api_key: str, service_id: int, task_id: int) -> Tuple[Optional[str], float]:
    """Отправляет запрос на API для накрутки подписчиков через прокси."""
    try:
        if not api_key:
            log.error("❌ API KEY не установлен в настройках")
            return None, 0.0

        if not service_id or service_id <= 0:
            log.error(f"❌ Некорректный service_id: {service_id}")
            return None, 0.0

        base_urls = ["https://twiboost.com/api/v2"]
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"}

        for base in base_urls:
            try:
                add_url = f"{base}?action=add&service={service_id}&link={channel_link}&quantity={subscribers_count}&key={api_key}"
                log.info(f"📊 Отправка API запроса для подписчиков: service_id={service_id}, quantity={subscribers_count}")

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
                            await save_booster_order(
                                task_id=task_id,
                                task_type="subscribers",
                                service_id=service_id,
                                external_order_id=str(order_id),
                                quantity=subscribers_count,
                                price=0.0,  # Пока неизвестно, обновим позже
                                expense_id=None  # Можно передать позже
                            )
                        except Exception as e:
                            log.error(f"❌ Ошибка сохранения заказа в БД: {e}")

                        # ВАЖНО: Ждем немного перед проверкой статуса
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

                            # Обрабатываем разные форматы ответа
                            charge = 0.0
                            if isinstance(status_data, dict):
                                # Стандартный формат: {"order_id": {"status": "...", "charge": ...}}
                                for order_key, order_data in status_data.items():
                                    if isinstance(order_data, dict):
                                        charge = order_data.get("charge", 0.0)
                                        if charge:
                                            break
                            elif "charge" in str(status_data):
                                # Альтернативный формат
                                import re
                                charge_match = re.search(r'"charge"\s*:\s*([\d\.]+)', str(status_data))
                                if charge_match:
                                    charge = float(charge_match.group(1))

                            if charge == 0:
                                log.warning(f"⚠️ Цена (charge) не найдена в ответе: {status_data}")
                                # Используем расчетную цену
                                with get_session() as db_session:
                                    from models import BoosterTariff
                                    tariff = db_session.execute(
                                        select(BoosterTariff)
                                        .where(BoosterTariff.service_id == service_id)
                                        .where(BoosterTariff.is_active == True)
                                    ).scalar_one_or_none()
                                    
                                    if tariff and tariff.price_per_1000 > 0:
                                        charge = (tariff.price_per_1000 / 1000) * subscribers_count
                                        log.info(f"💰 Используем расчетную цену: {charge:.4f} (на основе tariff.price_per_1000={tariff.price_per_1000})")

                            log.info(f"💰 Получена цена (charge) за подписчиков: {charge}")
                            return str(order_id), float(charge)

            except Exception as e:
                log.error(f"❌ Ошибка при работе с базовым URL {base}: {e}")
                continue

        log.error("❌ Все попытки отправки запроса на подписчиков завершились ошибкой")
        return None, 0.0

    except Exception as e:
        log.error(f"💥 Критическая ошибка при работе с API подписчиков: {e}")
        return None, 0.0

async def save_booster_order(
    task_id: int,
    task_type: str,
    service_id: int,
    external_order_id: str,
    quantity: int,
    price: float = 0.0,
    expense_id: Optional[int] = None
) -> bool:
    """Сохраняет заказ в таблицу BoosterOrder"""
    try:
        from models import BoosterOrder
        
        with get_session() as session:
            order = BoosterOrder(
                task_id=task_id,
                task_type=task_type,
                service_id=service_id,
                external_order_id=external_order_id,
                quantity=quantity,
                price=price,
                expense_id=expense_id,
                status='pending'
            )
            session.add(order)
            session.commit()
            log.info(f"✅ Заказ сохранен в БД: {external_order_id} для задачи {task_type} #{task_id}")
            return True
    except Exception as e:
        log.error(f"❌ Ошибка сохранения заказа в БД: {e}")
        return False

class SubscribersTracker:
    """Трекер для отслеживания подписчиков канала в реальном времени через сырые события"""
    
    def __init__(self, task_id: int, client: TelegramClient):
        self.task_id = task_id
        self.client = client
        self.is_running = True
        self.current_task_data: Optional[SubscribersBoostTask] = None
        self.current_target_data: Optional[MainEntity] = None
        
        # Статистика за текущий период
        self.current_subscriptions = 0
        self.current_unsubscriptions = 0
        self.last_check_time = datetime.now(UTC_TZ)
        
        # Обработчики событий
        self.event_handlers = []
        self.channel_entity = None
        
        # Защита от дублирования событий
        self._processed_events: Set[str] = set()
        self._event_timeout = 300  # 5 минут
        
        # Состояние для фоновой сверки
        self._last_count = None
        self._last_user_ids: set[int] = set()
        
        # Фоновая задача
        self._background_task = None
    
    def _load_task_data_from_db(self) -> tuple:
        """Загружает актуальные данные задачи из БД ВКЛЮЧАЯ last_processed_event_id"""
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
                
                log.info(f"✅ Загружена задача #{self.task_id} для канала {task_result.target.name if task_result.target else 'НЕТ'} "
                        f"(последний ID: {task_result.last_processed_event_id or 'не установлен'})")
                return task_result, task_result.target
                
        except Exception as e:
            log.error(f"❌ Ошибка загрузки задачи подписчиков #{self.task_id}: {e}")
            return None, None

    async def load_task_data(self):
        """Загружает данные задачи"""
        self.current_task_data, self.current_target_data = self._load_task_data_from_db()
        
        log.info(f"📊 Результат загрузки данных для задачи #{self.task_id}: "
                f"task={self.current_task_data is not None}, "
                f"target={self.current_target_data is not None}")
        
        return self.current_task_data is not None and self.current_target_data is not None

    async def get_current_subscribers_count(self, target: MainEntity) -> int:
        """Получает текущее количество подписчиков канала"""
        try:
            target_entity = await ensure_peer(self.client, telegram_id=target.telegram_id, link=target.link)
            channel = await self.client.get_entity(target_entity)
            subscribers_count = 0

            try:
                full = await self.client(functions.channels.GetFullChannelRequest(channel))
                if full.full_chat.participants_count:
                    subscribers_count = full.full_chat.participants_count
            except Exception as e:
                try:
                    full = await self.client(functions.messages.GetFullChatRequest(channel.id))
                    if full.full_chat.participants_count:
                        subscribers_count = full.full_chat.participants_count
                except Exception as inner_e:
                    log.warning(f"⚠️ Не удалось получить количество подписчиков для {target.name}: {inner_e}")

            subscribers_count = int(subscribers_count) if subscribers_count else 0
            return subscribers_count
                
        except Exception as e:
            log.error(f"❌ Ошибка получения количества подписчиков для канала {target.name}: {e}")
            return 0
    
    async def setup_event_handler(self):
        """Настраивает обработчик через admin log с сохранением последнего обработанного ID"""
        if not self.current_target_data:
            return False

        try:
            target_entity = await ensure_peer(
                self.client,
                telegram_id=self.current_target_data.telegram_id,
                link=self.current_target_data.link
            )

            self.channel_entity = await self.client.get_entity(target_entity)
            log.info(f"📡 Настраивается admin-log обработчик для {self.current_target_data.name}")

            # 🔹 Загружаем последний обработанный ID из БД
            last_processed_id = self.current_task_data.last_processed_event_id or 0
            processed_event_ids: set[int] = set()
            
            # 🔹 Получаем все события, начиная с последнего обработанного
            try:
                init_log = await self.client(functions.channels.GetAdminLogRequest(
                    channel=self.channel_entity,
                    q='',
                    min_id=last_processed_id,  # 🔹 НАЧИНАЕМ С ПОСЛЕДНЕГО ОБРАБОТАННОГО
                    max_id=0,
                    limit=200,
                ))
                if init_log and init_log.events:
                    # 🔹 Обрабатываем только НОВЫЕ события (ID > last_processed_id)
                    new_events = [ev for ev in init_log.events if ev.id > last_processed_id]
                    if new_events:
                        max_event_id = max(ev.id for ev in new_events)
                        processed_event_ids = {ev.id for ev in new_events}
                        
                        # 🔹 СРАЗУ ЖЕ ОБРАБАТЫВАЕМ события, которые произошли пока скрипт не работал
                        joins, leaves = await self._process_events_batch(new_events)
                        self.current_subscriptions += joins
                        self.current_unsubscriptions += leaves
                        
                        # 🔹 ОБНОВЛЯЕМ последний обработанный ID в БД
                        await self._update_last_processed_id(max_event_id)
                        
                        log.info(f"⚙️ При запуске обработано {len(new_events)} пропущенных событий в {self.current_target_data.name}: +{joins}/-{leaves}")
            except Exception as e:
                log.warning(f"⚠️ Не удалось получить начальный admin log для {self.current_target_data.name}: {e}")

            last_check_time = datetime.now(UTC_TZ)

            async def admin_log_checker():
                """Периодическая проверка новых событий через getAdminLog с сохранением ID"""
                nonlocal processed_event_ids, last_check_time

                while self.is_running:
                    try:
                        # 🔹 ВСЕГДА запрашиваем события, начиная с последнего обработанного ID
                        current_last_id = self.current_task_data.last_processed_event_id or 0
                        
                        result = await self.client(functions.channels.GetAdminLogRequest(
                            channel=self.channel_entity,
                            q='',
                            min_id=current_last_id,  # 🔹 НАЧИНАЕМ С ПОСЛЕДНЕГО ОБРАБОТАННОГО
                            max_id=0,
                            limit=100,
                        ))

                        if result and result.events:
                            # 🔹 Фильтруем только новые события (ID > current_last_id)
                            new_events = [ev for ev in result.events if ev.id > current_last_id]
                            
                            if new_events:
                                joins, leaves = 0, 0
                                max_event_id = current_last_id
                                
                                for ev in new_events:
                                    if ev.id > max_event_id:
                                        max_event_id = ev.id
                                    
                                    action = ev.action
                                    user_id = getattr(ev, "user_id", None)
                                    event_time = datetime.fromtimestamp(ev.date.timestamp(), UTC_TZ)
                                    
                                    if isinstance(action, types.ChannelAdminLogEventActionParticipantJoin):
                                        joins += 1
                                        log.debug(f"🟢 JOIN user={user_id} в {self.current_target_data.name}")
                                    elif isinstance(action, types.ChannelAdminLogEventActionParticipantLeave):
                                        leaves += 1
                                        log.debug(f"🔴 LEAVE user={user_id} в {self.current_target_data.name}")
                                    elif isinstance(action, types.ChannelAdminLogEventActionParticipantInvite):
                                        joins += 1
                                        log.debug(f"🟣 INVITE user={user_id} в {self.current_target_data.name}")

                                # 🔹 НЕМЕДЛЕННО обновляем последний обработанный ID
                                if max_event_id > current_last_id:
                                    await self._update_last_processed_id(max_event_id)

                                if joins or leaves:
                                    self.current_subscriptions += joins
                                    self.current_unsubscriptions += leaves
                                    
                                    log.info(f"📋 [{self.current_target_data.name}] Обработано {len(new_events)} событий: +{joins}/-{leaves} (последний ID: {max_event_id})")
                                
                                last_check_time = datetime.now(UTC_TZ)
                            else:
                                log.debug(f"⏳ [{self.current_target_data.name}] Новых событий нет (последний ID: {current_last_id})")

                        await asyncio.sleep(60)  # Проверяем каждую минуту

                    except FloodWaitError as e:
                        log.warning(f"⏳ FloodWait {e.seconds} сек. для {self.current_target_data.name}")
                        await asyncio.sleep(e.seconds)
                    except asyncio.CancelledError:
                        break
                    except Exception as e:
                        log.error(f"❌ Ошибка при проверке admin log для {self.current_target_data.name}: {e}")
                        await asyncio.sleep(120)

            self._background_task = asyncio.create_task(admin_log_checker())
            log.info(f"🎯 Admin-log мониторинг активирован для {self.current_target_data.name} (начальный ID: {last_processed_id})")
            return True

        except Exception as e:
            log.error(f"❌ Ошибка настройки admin-log обработчика: {e}")
            return False

    async def _process_events_batch(self, events: list) -> tuple[int, int]:
        """Обрабатывает пачку событий и возвращает количество подписок/отписок"""
        joins, leaves = 0, 0
        
        for ev in events:
            action = ev.action
            user_id = getattr(ev, "user_id", None)
            
            if isinstance(action, types.ChannelAdminLogEventActionParticipantJoin):
                joins += 1
            elif isinstance(action, types.ChannelAdminLogEventActionParticipantLeave):
                leaves += 1
            elif isinstance(action, types.ChannelAdminLogEventActionParticipantInvite):
                joins += 1
        
        return joins, leaves

    async def _update_last_processed_id(self, event_id: int):
        """Обновляет последний обработанный ID события в БД"""
        try:
            with get_session() as session:
                task = session.execute(
                    select(SubscribersBoostTask)
                    .where(SubscribersBoostTask.id == self.task_id)
                ).scalar_one()
                
                task.last_processed_event_id = event_id
                session.commit()
                
                # Также обновляем в текущем объекте
                if self.current_task_data:
                    self.current_task_data.last_processed_event_id = event_id
                    
                log.debug(f"💾 Обновлен last_processed_event_id={event_id} для задачи #{self.task_id}")
        except Exception as e:
            log.error(f"❌ Ошибка обновления last_processed_event_id для задачи #{self.task_id}: {e}")

    def stop(self):
        """Останавливает трекер и фоновую сверку"""
        self.is_running = False
        for handler in self.event_handlers:
            try:
                self.client.remove_event_handler(handler)
            except Exception:
                pass
        self.event_handlers.clear()
        if hasattr(self, "_background_task") and self._background_task:
            self._background_task.cancel()
    
    async def _save_check_record(self, total_subscribers: int, new_subscriptions: int, 
                               new_unsubscriptions: int, unsubscribed_users: List[int] = None):
        """Сохраняет запись о проверке в БД"""
        try:
            with get_session() as session:
                check = SubscribersCheck(
                    task_id=self.task_id,
                    total_subscribers=total_subscribers,
                    new_subscriptions=new_subscriptions,
                    new_unsubscriptions=new_unsubscriptions,
                    unsubscribed_users=unsubscribed_users or []
                )
                session.add(check)
                session.commit()
                log.debug(f"💾 Сохранена запись проверки: подписчиков={total_subscribers}, подписки={new_subscriptions}, отписки={new_unsubscriptions}")
        except Exception as e:
            log.error(f"❌ Ошибка сохранения записи проверки: {e}")
    
    async def _save_expense(self, subscribers_count: int, price: float, service_id: int, order_id: str = None):
        """Сохраняет информацию о расходе на подписчиков"""
        try:
            with get_session() as session:
                expense = SubscribersBoostExpense(
                    task_id=self.task_id,
                    subscribers_count=subscribers_count,
                    price=price,  # <-- Убедитесь что price это float, а не tuple
                    service_id=service_id
                )
                session.add(expense)
                session.flush()  # Получаем ID расхода
                
                # Обновляем заказ в БД с ценой и expense_id
                if order_id and isinstance(price, (int, float)):  # <-- Проверка типа
                    from models import BoosterOrder
                    from sqlalchemy import update
                    
                    # Находим и обновляем заказ
                    stmt = update(BoosterOrder).where(
                        BoosterOrder.external_order_id == order_id
                    ).values(
                        price=float(price),  # <-- Явное преобразование
                        expense_id=expense.id,
                        status='in_progress',
                        updated_at=datetime.utcnow()
                    )
                    session.execute(stmt)
                
                session.commit()
                log.info(f"💾 Сохранен расход на подписчиков: {subscribers_count} подписчиков, цена: {price}, order: {order_id}")
        except Exception as e:
            log.error(f"❌ Ошибка сохранения расхода на подписчиков: {e}")

    async def process_periodic_check(self):
        """Выполняет периодическую проверку без перезагрузки основных настроек"""
        if not self.is_running or not self.current_task_data or not self.current_target_data:
            log.warning(f"⚠️ Трекер не готов для проверки: is_running={self.is_running}, "
                       f"task_data={self.current_task_data is not None}, "
                       f"target_data={self.current_target_data is not None}")
            return
        
        # Проверяем только активность задачи, не перезагружая все данные
        if not await self.check_task_active():
            log.info(f"🛑 Задача #{self.task_id} деактивирована в БД")
            self.stop()
            return
        
        try:
            log.info(f"🔍 Периодическая проверка для задачи #{self.task_id}, канал: {self.current_target_data.name}")
            
            # Получаем текущее количество подписчиков
            current_total = await self.get_current_subscribers_count(self.current_target_data)
            
            if current_total == 0:
                log.error(f"❌ Не удалось получить подписчиков для канала {self.current_target_data.name}")
                return
            
            # ВАЖНО: Сохраняем статистику за период ПЕРЕД отправкой в API
            await self._save_check_record(current_total, self.current_subscriptions, self.current_unsubscriptions)
            
            # Рассчитываем количество подписчиков для отправки в API
            subscribers_to_send = self._calculate_subscribers_to_send(self.current_unsubscriptions)
            
            # Отправляем запрос на API если нужно
            if subscribers_to_send > 0:
                log.info(f"📤 Подготовка к отправке {subscribers_to_send} подписчиков для компенсации {self.current_unsubscriptions} отписок")
                
                channel_link = self.current_target_data.link if self.current_target_data.link else f"https://t.me/c/{abs(self.current_target_data.telegram_id)}"
                
                with get_session() as session:
                    settings = get_booster_settings(session)
                    if not settings:
                        log.error("❌ Не найдены глобальные настройки бустера")
                        return
                    
                    if not settings.api_key:
                        log.error("🚨 КРИТИЧЕСКАЯ ОШИБКА: API ключ пустой в настройках бустера!")
                        return

                    # ИСПОЛЬЗУЕМ НОВЫЙ МЕТОД С ПРОВЕРКОЙ ОЧЕРЕДЕЙ
                    service_id = await BoosterServiceRotation.get_next_service_id_for_module(
                        session=session,
                        module_name="subscribers",
                        tariffs=settings.tariffs,
                        default_service_id=settings.subscribers_service_id,
                        count=subscribers_to_send,
                        booster_settings=settings  # Для проверки очередей
                    )
                    
                    if not service_id:
                        log.error("❌ Не найден service_id для подписчиков")
                        return
                    
                    # Логируем внутри контекста сессии
                    log.info(f"🔧 Параметры API: service_id={service_id}, channel_link={channel_link}")
                    
                    order_id, price = await api_send_subscribers(
                        subscribers_count=subscribers_to_send,
                        channel_link=channel_link,
                        api_key=settings.api_key,
                        service_id=service_id,
                        task_id=self.task_id
                    )
                    
                    if price > 0 and order_id:
                        log.info(f"✅ Успешно отправлен запрос на {subscribers_to_send} подписчиков, цена: {price}, order: {order_id}")
                        # Сохраняем расход с обновленной ценой
                        await self._save_expense(subscribers_to_send, price, service_id, order_id)
                        
                        # Сбрасываем счетчик отписок после успешной отправки
                        self.current_unsubscriptions = 0
                    else:
                        log.error(f"❌ Ошибка отправки запроса на подписчиков или нулевая цена")
            else:
                if self.current_unsubscriptions > 0:
                    log.info(f"✅ Обнаружены отписки: {self.current_unsubscriptions}, но отправка не требуется")
                else:
                    log.info(f"✅ Отписок не обнаружено")
            
            # Сбрасываем счетчик подписок
            self.current_subscriptions = 0
            
            log.info(f"✅ Периодическая проверка завершена: подписчиков={current_total}")
            
        except Exception as e:
            log.error(f"❌ Ошибка периодической проверки для задачи #{self.task_id}: {e}")
            raise  # Пробрасываем для обработки в safe_process_periodic_check

    def _calculate_subscribers_to_send(self, new_unsubscriptions: int) -> int:
        """Рассчитывает количество подписчиков для отправки в API с учетом лимита"""
        if new_unsubscriptions <= 0:
            return 0
            
        # ВАЖНО: Проверяем что задача активна и данные загружены
        if not self.current_task_data:
            log.error("❌ Данные задачи не загружены")
            return 0
            
        if self.current_task_data.max_subscribers > 0:
            subscribers_to_send = min(new_unsubscriptions, self.current_task_data.max_subscribers)
            if subscribers_to_send < new_unsubscriptions:
                log.info(f"📊 Лимит ограничивает отправку: {new_unsubscriptions} отписок → {subscribers_to_send} подписчиков")
            else:
                log.info(f"📊 Отправка {subscribers_to_send} подписчиков для компенсации {new_unsubscriptions} отписок")
            return subscribers_to_send
        else:
            log.info(f"📊 Отправка {new_unsubscriptions} подписчиков для компенсации отписок")
            return new_unsubscriptions
    
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
    
    async def recover_from_error(self):
        """Восстановление после ошибок"""
        try:
            # Перезагружаем только минимально необходимые данные
            await self.load_task_data()
            log.info(f"✅ Восстановление данных трекера #{self.task_id} выполнено")
            return True
        except Exception as e:
            log.error(f"❌ Не удалось восстановить трекер #{self.task_id}: {e}")
            self.stop()
            return False
    
    async def safe_process_periodic_check(self):
        """Защищенная версия с восстановлением при ошибках"""
        try:
            await self.process_periodic_check()
        except Exception as e:
            log.error(f"❌ Критическая ошибка в проверке задачи #{self.task_id}: {e}")
            # Попытка восстановления
            await self.recover_from_error()
    
    async def process_periodic_check(self):
        """Выполняет периодическую проверку без перезагрузки основных настроек"""
        if not self.is_running or not self.current_task_data or not self.current_target_data:
            log.warning(f"⚠️ Трекер не готов для проверки: is_running={self.is_running}, "
                       f"task_data={self.current_task_data is not None}, "
                       f"target_data={self.current_target_data is not None}")
            return
        
        # Проверяем только активность задачи, не перезагружая все данные
        if not await self.check_task_active():
            log.info(f"🛑 Задача #{self.task_id} деактивирована в БД")
            self.stop()
            return
        
        try:
            log.info(f"🔍 Периодическая проверка для задачи #{self.task_id}, канал: {self.current_target_data.name}")
            
            # Получаем текущее количество подписчиков
            current_total = await self.get_current_subscribers_count(self.current_target_data)
            
            if current_total == 0:
                log.error(f"❌ Не удалось получить подписчиков для канала {self.current_target_data.name}")
                return
            
            # Сохраняем статистику за период
            await self._save_check_record(current_total, self.current_subscriptions, self.current_unsubscriptions)
            
            # Рассчитываем количество подписчиков для отправки в API
            subscribers_to_send = self._calculate_subscribers_to_send(self.current_unsubscriptions)
            
            # Отправляем запрос на API если нужно
            if subscribers_to_send > 0:
                log.info(f"📤 Подготовка к отправке {subscribers_to_send} подписчиков для компенсации {self.current_unsubscriptions} отписок")
                
                channel_link = self.current_target_data.link if self.current_target_data.link else f"https://t.me/c/{abs(self.current_target_data.telegram_id)}"
                
                with get_session() as session:
                    # Получаем настройки из БД (без кэша)
                    settings = get_booster_settings(session)
                    if not settings:
                        log.error("❌ Не найдены глобальные настройки бустера")
                        return
                    
                    # ПРОВЕРЯЕМ КРИТИЧЕСКИЕ ПОЛЯ
                    if not settings.api_key:
                        log.error("🚨 КРИТИЧЕСКАЯ ОШИБКА: API ключ пустой в настройках бустера!")
                        return

                    service_id = await BoosterServiceRotation.get_next_service_id_for_module(
                        session=session,
                        module_name="subscribers",
                        tariffs=settings.tariffs,
                        default_service_id=settings.subscribers_service_id,
                        count=subscribers_to_send,
                        booster_settings=settings  # Для проверки очередей
                    )
                    if not service_id:
                        log.error("❌ Не найден service_id для подписчиков")
                        return
                    
                    # Логируем внутри контекста сессии
                    log.info(f"🔧 Параметры API: service_id={service_id}, channel_link={channel_link}, "
                            f"api_key={'***' + settings.api_key[-4:] if settings.api_key else '🚨 НЕТ'}")
                    
                    order_id, price = await api_send_subscribers(  # ← Измените на кортеж
                        subscribers_count=subscribers_to_send,
                        channel_link=channel_link,
                        api_key=settings.api_key,
                        service_id=service_id,
                        task_id=self.task_id
                    )
                    
                    if price > 0:
                        log.info(f"✅ Успешно отправлен запрос на {subscribers_to_send} подписчиков, цена: {price}")
                        await self._save_expense(subscribers_to_send, price, service_id)
                        
                        # Сбрасываем счетчик отписок после успешной отправки
                        self.current_unsubscriptions = 0
                    else:
                        log.error(f"❌ Ошибка отправки запроса на подписчиков или нулевая цена")
            else:
                if self.current_unsubscriptions > 0:
                    log.info(f"✅ Обнаружены отписки: {self.current_unsubscriptions}, но отправка не требуется")
                else:
                    log.info(f"✅ Отписок не обнаружено")
            
            # Сбрасываем счетчик подписок
            self.current_subscriptions = 0
            
            log.info(f"✅ Периодическая проверка завершена: подписчиков={current_total}")
            
        except Exception as e:
            log.error(f"❌ Ошибка периодической проверки для задачи #{self.task_id}: {e}")
            raise  # Пробрасываем для обработки в safe_process_periodic_check
    
    def get_check_interval(self) -> int:
        """Возвращает интервал проверки в секундах"""
        if self.current_task_data and self.current_task_data.check_interval:
            return self.current_task_data.check_interval * 60
        return DEFAULT_CHECK_INTERVAL * 60
    

class SubscribersBoostManager:
    """Менеджер для управления всеми задачами отслеживания подписчиков"""
    
    def __init__(self):
        self.trackers: Dict[int, SubscribersTracker] = {}
        self.clients: Dict[int, TelegramClient] = {}
        self.periodic_tasks: Dict[int, asyncio.Task] = {}
        
    async def initialize(self):
        """Инициализация менеджера"""
        log.info("🔄 Инициализация менеджера отслеживания подписчиков...")
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
            log.info("🔍 Нет активных задач отслеживания подписчиков")
            return
        
        log.info(f"🔍 Загружено {len(tasks_result)} активных задач подписчиков")
        
        bot_ids = sorted(set(t.bot_id for t in tasks_result))
        
        with get_session() as session:
            bots = {b.id: b for b in session.execute(select(BotSession).where(BotSession.id.in_(bot_ids))).scalars().all()}
        
        # Инициализация клиентов
        for bot_id in bot_ids:
            if bot_id not in self.clients:
                try:
                    client = init_user_client(bots[bot_id])
                    await client.start()
                    if not await client.is_user_authorized():
                        raise RuntimeError(f"Бот #{bot_id} не авторизован")
                    self.clients[bot_id] = client
                    log.info(f"✅ Бот #{bot_id} авторизован для отслеживания подписчиков")
                except Exception as e:
                    log.error(f"❌ Ошибка инициализации бота #{bot_id}: {e}")
        
        # Создание и настройка трекеров
        for task in tasks_result:
            client = self.clients.get(task.bot_id)
            if client and task.id not in self.trackers:
                tracker = SubscribersTracker(task.id, client)
                if await tracker.load_task_data():
                    if await tracker.setup_event_handler():
                        self.trackers[task.id] = tracker
                        self._start_periodic_check(task.id, tracker)
                        log.info(f"✅ Трекер подписчиков создан для задачи #{task.id}")
                    else:
                        log.error(f"❌ Не удалось настроить обработчик событий для задачи #{task.id}")
                else:
                    log.error(f"❌ Не удалось загрузить данные для задачи #{task.id}")

    def _start_periodic_check(self, task_id: int, tracker: SubscribersTracker):
        async def periodic_check():
            check_interval = tracker.get_check_interval()
            log.info(f"⏰ Установлен интервал проверки для задачи #{task_id}: {check_interval} секунд")
            
            while tracker.is_running:
                try:
                    # ЖДЕМ ПЕРЕД проверкой, а не после
                    await asyncio.sleep(check_interval)
                    await tracker.safe_process_periodic_check()
                except Exception as e:
                    log.error(f"❌ Ошибка в периодической проверке задачи #{task_id}: {e}")
                    await asyncio.sleep(60)
        task = asyncio.create_task(periodic_check())
        self.periodic_tasks[task_id] = task

    async def check_for_updates(self):
        """Проверяет обновления в БД и обновляет трекеры"""
        with get_session() as session:
            active_tasks = session.execute(select(SubscribersBoostTask).where(SubscribersBoostTask.is_active == True)).scalars().all()
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
                    log.info(f"🗑️ Удален трекер подписчиков для задачи #{task_id}")
            
            # Добавляем новые трекеры
            for task in active_tasks:
                if task.id not in self.trackers:
                    client = self.clients.get(task.bot_id)
                    if client:
                        tracker = SubscribersTracker(task.id, client)
                        if await tracker.load_task_data():
                            if await tracker.setup_event_handler():
                                self.trackers[task.id] = tracker
                                self._start_periodic_check(task.id, tracker)
                                log.info(f"✅ Добавлен трекер подписчиков для задачи #{task.id}")
    
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
manager = SubscribersBoostManager()

async def run_subscribers_booster():
    """Запуск основного цикла отслеживания подписчиков"""
    log.info("🚀 Модуль отслеживания подписчиков запускается...")
    
    try:
        await manager.initialize()
        log.info("✅ Модуль отслеживания подписчиков успешно запущен")
        
        # Основной цикл для проверки обновлений БД
        while True:
            try:
                await asyncio.sleep(300)
                await manager.check_for_updates()
            except Exception as e:
                log.error(f"❌ Ошибка при проверке обновлений БД: {e}")
                await asyncio.sleep(60)
            
    except Exception as e:
        log.error(f"💥 Критическая ошибка в модуле отслеживания подписчиков: {e}")
    finally:
        await manager.cleanup()
        log.info("🛑 Модуль отслеживания подписчиков остановлен")

if __name__ == "__main__":
    asyncio.run(run_subscribers_booster())