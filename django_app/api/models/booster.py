from django.db import models
import aiohttp
import asyncio
from typing import Dict, List, Optional
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)

class BoosterSettings(models.Model):
    """Настройки бустера для всех сервисов накрутки (одна запись)."""
    is_active = models.BooleanField(default=True, verbose_name="Выключить/включить все тарифы")

    api_key = models.CharField(max_length=128, verbose_name="API ключ")
    url = models.URLField(verbose_name="Ссылка на биржу", default="https://twiboost.com", blank=True)

    balance_alert_limit = models.IntegerField(default=0, verbose_name="Баланс оповещения")
    is_balance_notify = models.BooleanField(default=False, verbose_name="Включение оповещений о низком балансе")

    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0, verbose_name="Баланс на бирже")
    last_balance_check = models.DateTimeField(null=True, blank=True, verbose_name="Дата последней проверки баланса")

    new_views_service_id = models.IntegerField(verbose_name="ID сервиса новых просмотров", default=0)
    old_views_service_id = models.IntegerField(verbose_name="ID сервиса старых просмотров", default=0)
    subscribers_service_id = models.IntegerField(verbose_name="ID сервиса подписчиков", default=0)

    is_active_new_views = models.BooleanField(default=False, verbose_name="Модуль: новые просмотры активен")
    is_active_old_views = models.BooleanField(default=False, verbose_name="Модуль: старые просмотры активен")
    is_active_subscribers = models.BooleanField(default=False, verbose_name="Модуль: подписчики активен")

    min_new_views = models.IntegerField(default=0, verbose_name="Мин. новые просмотры")
    min_old_views = models.IntegerField(default=0, verbose_name="Мин. старые просмотры")
    min_subscribers = models.IntegerField(default=0, verbose_name="Мин. подписчики")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Настройки бустера"
        verbose_name_plural = "Настройки бустера"
        db_table = "booster_settings"

    def __str__(self):
        return f"BoosterSettings (API: {self.api_key[:10]}...)"

    def save(self, *args, **kwargs):
        if not self.pk and BoosterSettings.objects.exists():
            self.pk = BoosterSettings.objects.first().pk
        super().save(*args, **kwargs)

    @classmethod
    def get_singleton(cls):
        obj, _ = cls.objects.get_or_create(pk=1, defaults={"api_key": ""})
        return obj

class BoosterServiceRotation(models.Model):
    """
    Модель для ротации сервисов бустера с учетом очередей
    """
    MODULE_CHOICES = [
        ('new_views', 'Новые просмотры'),
        ('old_views', 'Старые просмотры'), 
        ('subscribers', 'Подписчики'),
    ]
    
    module = models.CharField(
        max_length=20, 
        choices=MODULE_CHOICES,
        verbose_name="Модуль"
    )
    service_type = models.CharField(
        max_length=20,
        choices=MODULE_CHOICES,
        blank=True,
        default='',
        verbose_name="Тип сервиса"
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активен"
    )
    last_used_tariff_id = models.IntegerField(
        default=0,
        verbose_name="ID последнего использованного тарифа"
    )
    default_service_id = models.IntegerField(
        default=0,
        verbose_name="ID сервиса по умолчанию"
    )
    
    # Кэш для хранения информации об активных заказах
    active_orders_cache = models.JSONField(
        default=dict,
        verbose_name="Кэш активных заказов"
    )
    last_orders_check = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Время последней проверки заказов"
    )
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Ротация сервисов бустера"
        verbose_name_plural = "Ротации сервисов бустера"
        db_table = "booster_service_rotation"
        unique_together = ['module']

    def __str__(self):
        return f"{self.get_module_display()} rotation (last: {self.last_used_tariff_id})"

    @classmethod
    def get_or_create_rotation(cls, module_name: str, default_service_id: int = 0) -> 'BoosterServiceRotation':
        """
        Получает или создает запись ротации для модуля
        """
        rotation, created = cls.objects.get_or_create(
            module=module_name,
            defaults={
                'service_type': module_name,
                'default_service_id': default_service_id,
                'is_active': True,
                'last_used_tariff_id': 0,
                'active_orders_cache': {},
            }
        )
        return rotation

    @staticmethod
    async def create_order(task_id: int, task_type: str, service_id: int, 
                          external_order_id: str, quantity: int, price: float,
                          expense_id: Optional[int] = None) -> 'BoosterOrder':
        """
        Создаёт запись о заказе
        """
        try:
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
        
            order.save()
            
            logger.info(f"✅ Создан заказ {external_order_id} для задачи {task_type} #{task_id}, service_id: {service_id}")
            return order
            
        except Exception as e:
            logger.error(f"❌ Ошибка при создании заказа: {e}")
            raise

    async def check_active_orders(self, booster_settings: 'BoosterSettings') -> Dict[int, int]:
        """
        Проверяет активные заказы через API и возвращает количество активных заказов по service_id
        """
        try:
            # Проверяем, нужно ли обновлять кэш (раз в 5 минут)
            if (self.last_orders_check and 
                (datetime.now() - self.last_orders_check).seconds < 300 and
                self.active_orders_cache):
                logger.debug(f"📊 Используем кэшированные данные об активных заказах для {self.module}")
                return self.active_orders_cache

            # Получаем все активные заказы для этого модуля из нашей БД
            active_orders = BoosterOrder.objects.filter(
                task_type=self.module,
                status__in=['pending', 'in_progress'],
                external_order_id__isnull=False
            )
            
            if not active_orders.exists():
                logger.debug(f"📊 Нет активных заказов для модуля {self.module}")
                return {}

            # Группируем заказы по service_id
            orders_by_service = {}
            for order in active_orders:
                if order.service_id not in orders_by_service:
                    orders_by_service[order.service_id] = []
                orders_by_service[order.service_id].append(order)
            
            # Проверяем статусы через API
            api_key = booster_settings.api_key
            url = booster_settings.url.rstrip('/') + "/api/v2"
            
            active_orders_count = {}
            
            for service_id, orders in orders_by_service.items():
                # Собираем ID внешних заказов
                external_order_ids = [str(order.external_order_id) for order in orders if order.external_order_id]
                
                if not external_order_ids:
                    active_orders_count[service_id] = 0
                    continue
                
                # Запрашиваем статусы пачками
                active_count = 0
                batch_size = 50
                
                for i in range(0, len(external_order_ids), batch_size):
                    batch = external_order_ids[i:i+batch_size]
                    
                    try:
                        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as client_session:
                            params = {
                                'action': 'status',
                                'orders': ','.join(batch),
                                'key': api_key
                            }
                            
                            async with client_session.get(url, params=params) as response:
                                if response.status == 200:
                                    data = await response.json()
                                    
                                    orders_to_update = []
                                    # Подсчитываем активные заказы
                                    for order_id, status_info in data.items():
                                        if isinstance(status_info, dict):
                                            status = status_info.get('status', '')
                                            order = next((o for o in orders if str(o.external_order_id) == order_id), None)
                                            
                                            if order:
                                                # Активными считаем заказы, которые ещё не завершены
                                                if status not in ['Completed', 'Canceled', 'Fail']:
                                                    active_count += 1
                                                    
                                                    if order.status != 'in_progress':
                                                        order.status = 'in_progress'
                                                        order.api_response = status_info
                                                        orders_to_update.append(order)
                                                        
                                                elif status in ['Completed', 'Canceled', 'Fail']:
                                                    order.status = 'completed' if status == 'Completed' else 'failed'
                                                    order.completed_at = datetime.now()
                                                    order.api_response = status_info
                                                    orders_to_update.append(order)
                                    
                                    # Массовое обновление
                                    if orders_to_update:
                                        BoosterOrder.objects.bulk_update(
                                            orders_to_update,
                                            ['status', 'api_response', 'completed_at', 'updated_at']
                                        )
                        
                    except Exception as e:
                        logger.error(f"❌ Ошибка при проверке заказов для service_id {service_id}: {e}")
                        # В случае ошибки считаем все заказы активными для безопасности
                        active_count += len(batch)
                
                active_orders_count[service_id] = active_count
                logger.debug(f"📊 Service_id {service_id}: {active_count} активных заказов")
            
            # Обновляем кэш
            self.active_orders_cache = active_orders_count
            self.last_orders_check = datetime.now()
            self.save(update_fields=['active_orders_cache', 'last_orders_check', 'updated_at'])
            
            logger.info(f"✅ Обновлены данные об активных заказах для {self.module}")
            return active_orders_count
            
        except Exception as e:
            logger.error(f"❌ Ошибка при проверке активных заказов для {self.module}: {e}")
            return {}

    def _get_available_tariffs(self, tariffs: list, count: int, 
                              active_orders_count: Dict[int, int]) -> List[dict]:
        """
        Возвращает список доступных тарифов с учетом очередей
        """
        available_tariffs = []
        
        for tariff in tariffs:
            if (tariff.module == self.module and 
                tariff.is_active and 
                tariff.min_limit <= count):
                
                # Проверяем очередь для этого service_id
                active_orders = active_orders_count.get(tariff.service_id, 0)
                has_queue = active_orders >= 2  # Более 2 активных заказов = очередь
                
                available_tariffs.append({
                    'tariff': tariff,
                    'service_id': tariff.service_id,
                    'is_primary': tariff.is_primary,
                    'price_per_1000': tariff.price_per_1000,
                    'active_orders': active_orders,
                    'has_queue': has_queue
                })
        
        # Сортируем: сначала без очереди, затем основные тарифы, затем по цене
        available_tariffs.sort(key=lambda x: (
            x['has_queue'],  # False (0) first, True (1) second
            not x['is_primary'],  # Primary first
            x['price_per_1000']  # Cheaper first
        ))
        
        return available_tariffs

    async def get_next_service_id(self, tariffs: list, count: int, 
                                 booster_settings: 'BoosterSettings' = None) -> int:
        """
        Возвращает следующий service_id с учетом очередей активных заказов
        """
        import random
        
        try:
            if not booster_settings:
                # Для обратной совместимости используем синглтон
                booster_settings = BoosterSettings.get_singleton()
            
            # Получаем информацию об активных заказах
            active_orders_count = await self.check_active_orders(booster_settings)
            
            # Получаем доступные тарифы с учетом очередей
            available_tariffs = self._get_available_tariffs(tariffs, count, active_orders_count)
            
            if not available_tariffs:
                logger.warning(f"⚠️ Для модуля {self.module} и count={count} нет доступных тарифов, используем default_service_id: {self.default_service_id}")
                return self.default_service_id
            
            # Фильтруем тарифы без очереди
            no_queue_tariffs = [t for t in available_tariffs if not t['has_queue']]
            
            if no_queue_tariffs:
                # Выбираем из тарифов без очереди
                # Используем круговой алгоритм
                if not self.last_used_tariff_id:
                    # Первый запуск
                    chosen_tariff = no_queue_tariffs[0]
                else:
                    # Ищем следующий тариф
                    last_index = next((i for i, t in enumerate(no_queue_tariffs) 
                                      if t['tariff'].id == self.last_used_tariff_id), -1)
                    next_index = (last_index + 1) % len(no_queue_tariffs) if last_index != -1 else 0
                    chosen_tariff = no_queue_tariffs[next_index]
                
                chosen_service_id = chosen_tariff['service_id']
                logger.info(f"✅ Выбран тариф без очереди: service_id={chosen_service_id}, "
                          f"активных заказов={chosen_tariff['active_orders']}")
            else:
                # Все тарифы в очереди - используем основной тариф
                primary_tariffs = [t for t in available_tariffs if t['is_primary']]
                
                if primary_tariffs:
                    # Используем основной тариф
                    chosen_tariff = primary_tariffs[0]
                    chosen_service_id = chosen_tariff['service_id']
                    logger.warning(f"⚠️ Все тарифы в очереди, используем основной: service_id={chosen_service_id}, "
                                 f"активных заказов={chosen_tariff['active_orders']}")
                else:
                    # Нет основных тарифов - выбираем первый доступный
                    chosen_tariff = available_tariffs[0]
                    chosen_service_id = chosen_tariff['service_id']
                    logger.warning(f"⚠️ Все тарифы в очереди и нет основного, используем первый доступный: "
                                 f"service_id={chosen_service_id}, активных заказов={chosen_tariff['active_orders']}")
            
            # Обновляем ротацию
            self.last_used_tariff_id = chosen_tariff['tariff'].id
            self.save(update_fields=['last_used_tariff_id', 'updated_at'])
            
            return chosen_service_id

        except Exception as e:
            logger.error(f"❌ Критическая ошибка в выборе тарифа для {self.module} (count={count}): {e}")
            logger.info(f"🔄 Используем default_service_id: {self.default_service_id}")
            return self.default_service_id

    @classmethod
    async def get_next_service_id_for_module(cls, module_name: str, 
                                           tariffs: list, default_service_id: int = 0, 
                                           count: int = 10, booster_settings: 'BoosterSettings' = None) -> int:
        """
        Статический метод для получения следующего service_id с учетом очередей
        """
        logger.debug(f"🎯 Запрос следующего service_id для модуля: {module_name}, count: {count}")
        
        if not booster_settings:
            booster_settings = BoosterSettings.get_singleton()
            
        try:
            rotation = cls.get_or_create_rotation(module_name, default_service_id)
            result = await rotation.get_next_service_id(tariffs, count, booster_settings)
            logger.info(f"✅ Получен service_id {result} для модуля {module_name} (count={count})")
            return result
        except Exception as e:
            logger.error(f"❌ Ошибка в get_next_service_id_for_module для {module_name} (count={count}): {e}")
            return default_service_id

    # Методы для обратной совместимости
    def get_next_service_id_sync(self, tariffs: list, count: int = 10) -> int:
        """
        Синхронная версия для обратной совместимости
        """
        try:
            return asyncio.run(self.get_next_service_id(tariffs, count))
        except:
            # Если асинхронный режим недоступен, используем старую логику
            return self._get_next_service_id_fallback(tariffs)

    @classmethod
    def get_next_service_id_for_module_sync(cls, module_name: str, 
                                          tariffs: list, default_service_id: int = 0, 
                                          count: int = 10) -> int:
        """
        Синхронная версия для обратной совместимости
        """
        try:
            return asyncio.run(cls.get_next_service_id_for_module(
                module_name, tariffs, default_service_id, count
            ))
        except:
            # Если асинхронный режим недоступен, используем старую логику
            rotation = cls.get_or_create_rotation(module_name, default_service_id)
            return rotation._get_next_service_id_fallback(tariffs)

    def _get_next_service_id_fallback(self, tariffs: list) -> int:
        """
        Запасной метод без проверки очередей
        """
        try:
            active_tariffs = [t for t in tariffs if t.module == self.module and t.is_active]
            if not active_tariffs:
                return self.default_service_id

            active_tariffs.sort(key=lambda t: t.id)

            if not self.last_used_tariff_id:
                next_tariff = active_tariffs[0]
            else:
                try:
                    last_index = next(i for i, t in enumerate(active_tariffs) if t.id == self.last_used_tariff_id)
                    next_index = (last_index + 1) % len(active_tariffs)
                    next_tariff = active_tariffs[next_index]
                except StopIteration:
                    next_tariff = active_tariffs[0]

            self.last_used_tariff_id = next_tariff.id
            self.save()

            return next_tariff.service_id

        except Exception as e:
            logger.error(f"❌ Ошибка в запасном методе выбора тарифа для {self.module}: {e}")
            return self.default_service_id

class BoosterTariff(models.Model):
    """Тарифный план для одного из модулей бустера (old_views/new_views/subscribers)."""

    MODULE_CHOICES = [
        ("old_views", "Просмотры старых постов"),
        ("new_views", "Умные просмотры новых постов"),
        ("subscribers", "Подписчики каналов-групп"),
    ]

    booster = models.ForeignKey(
        BoosterSettings,
        on_delete=models.CASCADE,
        related_name="tariffs",
        verbose_name="Настройки бустера",
    )
    module = models.CharField(max_length=32, choices=MODULE_CHOICES, verbose_name="Модуль")
    comment = models.CharField(verbose_name="Комментарий", default=" ", null=True)
    service_id = models.IntegerField(verbose_name="ID тарифа", default=0)
    min_limit = models.IntegerField(verbose_name="Минимум", default=0)
    price_per_1000 = models.FloatField(verbose_name="Цена за 1000", default=0)
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    is_primary = models.BooleanField(default=False, verbose_name="Основной тариф")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "booster_tariffs"
        verbose_name = "Тариф бустера"
        verbose_name_plural = "Тарифы бустера"
        ordering = ["module", "min_limit"]

    def __str__(self):
        return f"{self.get_module_display()} (ID={self.service_id}, {self.price_per_1000}₽)"

    def save(self, *args, **kwargs):
        # Если этот тариф становится основным, снимаем флаг с других тарифов этого модуля
        if self.is_primary and self.pk:
            BoosterTariff.objects.filter(
                booster=self.booster,
                module=self.module,
                is_primary=True
            ).exclude(pk=self.pk).update(is_primary=False)
        
        super().save(*args, **kwargs)

class BoosterOrder(models.Model):
    """
    Модель для отслеживания заказов на накрутку
    """
    STATUS_CHOICES = [
        ('pending', 'Ожидает'),
        ('in_progress', 'В процессе'),
        ('completed', 'Завершён'),
        ('failed', 'Ошибка'),
    ]
    
    TASK_TYPE_CHOICES = [
        ('new_views', 'Новые просмотры'),
        ('old_views', 'Старые просмотры'),
        ('subscribers', 'Подписчики'),
    ]
    
    # Ссылка на задачу
    task_id = models.BigIntegerField(verbose_name="ID задачи")
    task_type = models.CharField(
        max_length=20, 
        choices=TASK_TYPE_CHOICES, 
        verbose_name="Тип задачи"
    )
    
    # Данные заказа
    external_order_id = models.CharField(
        max_length=50, 
        verbose_name="ID заказа во внешней системе"
    )
    service_id = models.IntegerField(verbose_name="ID сервиса накрутки")
    
    # Статус
    status = models.CharField(
        max_length=20, 
        choices=STATUS_CHOICES, 
        default='pending',
        verbose_name="Статус"
    )
    api_response = models.JSONField(
        null=True, 
        blank=True,
        verbose_name="Последний ответ от API"
    )
    
    # Параметры заказа
    quantity = models.IntegerField(verbose_name="Количество")
    price = models.FloatField(verbose_name="Стоимость")
    
    # Временные метки
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="Завершено")
    
    # Связь с расходом (если есть)
    expense_id = models.BigIntegerField(null=True, blank=True, verbose_name="ID расхода")
    
    class Meta:
        db_table = "booster_orders"
        verbose_name = "Заказ на накрутку"
        verbose_name_plural = "Заказы на накрутку"
        indexes = [
            models.Index(fields=['external_order_id']),
            models.Index(fields=['service_id', 'status']),
            models.Index(fields=['task_id', 'task_type']),
        ]
    
    def __str__(self):
        return f"Заказ {self.external_order_id} (сервис: {self.service_id})"