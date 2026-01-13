from sqlalchemy import (
    Column, BigInteger, Boolean, DateTime, ForeignKey,
    Integer, String, Text, Float, Time, Date, JSON,
    UniqueConstraint, func, Index, Table
)
from sqlalchemy.orm import relationship
from typing import Dict, List, Optional, Tuple, Set
from datetime import datetime, timedelta, time
import logging
import aiohttp
from db import Base
import random
import asyncio
from typing import TYPE_CHECKING

# Для аннотаций типов
if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# ============================================================
# 🔹 Telegram bots
# ============================================================


class BotSession(Base):
    __tablename__ = "telegram_botsession"

    id = Column(BigInteger, primary_key=True)
    api_id = Column(Integer, nullable=False)
    api_hash = Column(String(255), nullable=False)
    phone = Column(String(32), unique=True, nullable=False)
    session_string = Column(String(455), nullable=False)
    description = Column(Text)
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime)

    first_name = Column(String(32), default="")
    last_name = Column(String(32), default="")
    bio = Column(Text, comment="Описание в профиле Telegram")
    birthday = Column(Date, comment="День рождения")
    is_banned = Column(Boolean, default=False, comment="Забанен в Telegram")
    telegram_info = Column(
        JSON, default=dict, comment="Информация из Telegram API")
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)
    last_sync_at = Column(DateTime, comment="Дата последней синхронизации")

    ads_orders = relationship("AdsOrder", back_populates="bot", lazy="dynamic")
    channel_tasks = relationship("EntityPostTask", back_populates="bot")

    # One-to-one relationship с BotProfile
    profile = relationship("BotProfile", back_populates="bot", uselist=False)

    def __repr__(self):
        return f"<BotSession(id={self.id}, phone={self.phone}, name={self.name})>"

class BotProfile(Base):
    __tablename__ = "telegram_botprofile"

    id = Column(BigInteger, primary_key=True)
    bot_id = Column(BigInteger, ForeignKey("telegram_botsession.id"), unique=True, nullable=False)

    # Поля профиля
    gender = Column(String(10), default="male")  # male / female
    current_status = Column(String(20), nullable=True)  # pro / experienced / tourist / advertiser
    country = Column(String(100), nullable=True)
    owner_type = Column(String(20), default="none")  # own / foreign / own_bot / none
    telegram_status = Column(String(10), default="regular")  # regular / premium
    notes = Column(Text, nullable=True)

    # Метрики
    admin_groups_last_updated = Column(DateTime, nullable=True)
    subscriber_groups_last_updated = Column(DateTime, nullable=True)
    warnings_last_updated = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Обратная связь на BotSession
    bot = relationship("BotSession", back_populates="profile")

    def __repr__(self):
        return f"<BotProfile(bot_id={self.bot_id}, gender={self.gender}, status={self.current_status})>"


class AdsOrder(Base):
    __tablename__ = "api_adsorder"

    id = Column(BigInteger, primary_key=True)

    is_active = Column(Boolean, default=True)
    is_paid = Column(Boolean, default=False)

    name = Column(String(255))
    post_link = Column(String(512))

    customer_telegram = Column(String(64))
    customer_fullname = Column(String(255))
    customer_status = Column(String(16))

    notify_customer = Column(Boolean, default=True)
    notify_admin = Column(Boolean, default=True)

    bot_id = Column(BigInteger, ForeignKey(
        "telegram_botsession.id"), nullable=False)
    target_id = Column(BigInteger, ForeignKey(
        "api_mainentity.id"), nullable=False)

    ordered_at = Column(DateTime, default=datetime.utcnow)
    publish_at = Column(DateTime, nullable=False)

    published_at = Column(DateTime, nullable=True)
    pinned_at = Column(DateTime, nullable=True)
    unpinned_at = Column(DateTime, nullable=True)
    deleted_at = Column(DateTime, nullable=True)

    target_message_id = Column(BigInteger, nullable=True)

    bot = relationship(
        "BotSession", back_populates="ads_orders", lazy="joined")
    target = relationship(
        "MainEntity", back_populates="ads_orders", lazy="joined")
# ============================================================
# 🔹 Common entities
# ============================================================


class ChannelTaskGroup(Base):
    __tablename__ = "api_channeltaskgroup"

    id = Column(BigInteger, primary_key=True)
    created_at = Column(DateTime)
    subtasks = relationship("EntityPostTask", back_populates="group")


class MainEntity(Base):
    __tablename__ = "api_mainentity"

    id = Column(BigInteger, primary_key=True)
    name = Column(String(255))
    order = Column(Integer)
    telegram_id = Column(BigInteger, unique=True)
    entity_type = Column(String(16))          # channel / group
    destination_type = Column(String(16))     # draft / all / main
    description = Column(Text)
    owner = Column(String(32))
    link = Column(String(255))
    publish_link = Column(String(255))
    tags = Column(String(255))
    text_suffix = Column(String(1024))
    is_add_suffix = Column(Boolean, default=True)
    photo = Column(String(255))

    category_id = Column(BigInteger, ForeignKey(
        "admin_panel_category.id"), nullable=True)
    category = relationship("Category", back_populates="entities")

    country_id = Column(BigInteger, ForeignKey(
        "admin_panel_country.id"), nullable=True)
    country = relationship("Country", back_populates="entities")

    # Связь Many-to-Many с Category
    entity_category_links = relationship(
        "EntityCategory", back_populates="entity")

    source_tasks = relationship(
        "EntityPostTask", foreign_keys="EntityPostTask.source_id", back_populates="source")
    target_tasks = relationship(
        "EntityPostTask", foreign_keys="EntityPostTask.target_id", back_populates="target")

    ads_orders = relationship("AdsOrder", back_populates="target")

    # Свойство для удобного доступа к связанным категориям через Many-to-Many
    @property
    def categories(self):
        """Возвращает список категорий через Many-to-Many связь"""
        return [link.category for link in self.entity_category_links]

    # Свойство для удобного доступа к URL темы для текущей задачи
    def get_theme_url_for_category(self, category_id: int) -> Optional[str]:
        """Возвращает URL темы для конкретной категории"""
        for link in self.entity_category_links:
            if link.category_id == category_id:
                return link.theme_url
        return None


class Category(Base):
    """
    Справочник категорий.
    """
    __tablename__ = "admin_panel_category"

    id = Column(BigInteger, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Новая связь Many-to-Many с MainEntity
    category_entity_links = relationship(
        "EntityCategory", back_populates="category")

    # Существующая связь One-to-Many (оставляем)
    entities = relationship("MainEntity", back_populates="category")

    def __str__(self):
        return self.name

# ============================================================
# 🔹 Entity-Category Many-to-Many связь
# ============================================================


class EntityCategory(Base):
    """Промежуточная модель для связи Many-to-Many между MainEntity и Category"""
    __tablename__ = "main_entity_category_links"  # Исправляем имя таблицы

    id = Column(BigInteger, primary_key=True)
    entity_id = Column(BigInteger, ForeignKey(
        "api_mainentity.id"), nullable=False)
    category_id = Column(BigInteger, ForeignKey(
        "admin_panel_category.id"), nullable=False)
    theme_url = Column(String(500), nullable=False, comment="URL темы")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Связи
    entity = relationship("MainEntity", back_populates="entity_category_links")
    category = relationship("Category", back_populates="category_entity_links")

    class Meta:
        __table_args__ = (
            UniqueConstraint('entity_id', 'category_id',
                             name='uq_entity_category'),
        )

    def __repr__(self):
        return f"<EntityCategory entity_id={self.entity_id} category_id={self.category_id}>"


class Country(Base):
    __tablename__ = "admin_panel_country"

    id = Column(BigInteger, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    time_zone_delta = Column(
        Float, default=0.0, comment="Смещение относительно МСК (может быть отрицательным)")

    entities = relationship("MainEntity", back_populates="country")

    def __repr__(self):
        return f"<Country(name={self.name}, delta={self.time_zone_delta})>"


# ============================================================
# 🔹 Main posting tasks
# ============================================================
class EntityPostTask(Base):
    __tablename__ = "api_entityposttask"

    id = Column(BigInteger, primary_key=True)
    choice_mode = Column(String(20))      # random / sequential
    after_publish = Column(String(20))    # remove / cycle
    created_at = Column(DateTime)
    updated_at = Column(DateTime)

    bot_id = Column(BigInteger, ForeignKey("telegram_botsession.id"))
    group_id = Column(BigInteger, ForeignKey("api_channeltaskgroup.id"))
    source_id = Column(BigInteger, ForeignKey("api_mainentity.id"))
    target_id = Column(BigInteger, ForeignKey("api_mainentity.id"))

    is_active = Column(Boolean, default=True)
    is_global_active = Column(Boolean, default=True)

    bot = relationship("BotSession", back_populates="channel_tasks")
    group = relationship("ChannelTaskGroup", back_populates="subtasks")

    source = relationship("MainEntity", foreign_keys=[
                          source_id], back_populates="source_tasks")
    target = relationship("MainEntity", foreign_keys=[
                          target_id], back_populates="target_tasks")

    times = relationship("TaskTime", back_populates="task", lazy="joined")


class TaskTime(Base):
    __tablename__ = "api_tasktime"

    id = Column(BigInteger, primary_key=True)
    weekday = Column(Integer)
    seconds_from_day_start = Column(Integer)
    task_id = Column(BigInteger, ForeignKey("api_entityposttask.id"))

    task = relationship("EntityPostTask", back_populates="times")

# ============================================================
# 🔹 Daily Pinning Tasks
# ============================================================


class DailyPinningTask(Base):
    __tablename__ = "daily_pinning_tasks"

    id = Column(BigInteger, primary_key=True)
    is_active = Column(Boolean, default=True)

    # Связи
    channel_id = Column(BigInteger, ForeignKey(
        "api_mainentity.id"), nullable=False)
    bot_id = Column(BigInteger, ForeignKey(
        "telegram_botsession.id"), nullable=False)

    # Конфигурация
    # Ссылка на пост для закрепления
    post_link = Column(String(512), nullable=False)
    start_time = Column(Time, nullable=False)        # Время начала интервала
    # Время окончания интервала
    end_time = Column(Time, nullable=False)
    # Через сколько минут откреплять
    unpin_after_minutes = Column(Integer, nullable=False)
    # Через сколько минут удалять уведомление
    delete_notification_after_minutes = Column(Integer, nullable=False)

    # Статистика
    total_yesterday = Column(Integer, default=0)     # Всего постов вчера
    dummy_yesterday = Column(Integer, default=0)     # Пустышек вчера
    total_today = Column(Integer, default=0)         # Всего постов сегодня
    dummy_today = Column(Integer, default=0)         # Пустышек сегодня

    # Состояние
    pinned_at = Column(DateTime, nullable=True)      # Когда закреплено
    unpinned_at = Column(DateTime, nullable=True)    # Когда откреплено
    notification_deleted_at = Column(
        DateTime, nullable=True)  # Когда удалено уведомление
    # ID закрепленного сообщения
    pinned_message_id = Column(BigInteger, nullable=True)
    last_cycle_date = Column(Date, nullable=True)    # Дата последнего цикла

    # Связи
    channel = relationship("MainEntity", backref="daily_pinning_tasks")
    bot = relationship("BotSession", backref="daily_pinning_tasks")


class ViewBoostTask(Base):
    __tablename__ = "view_boost_tasks"

    id = Column(BigInteger, primary_key=True)
    is_active = Column(Boolean, default=True)
    target_id = Column(BigInteger, ForeignKey(
        "api_mainentity.id"), nullable=False)
    bot_id = Column(BigInteger, ForeignKey(
        "telegram_botsession.id"), nullable=False)
    settings_id = Column(BigInteger, ForeignKey(
        "booster_settings.id"), nullable=True)

    view_coefficient = Column(Integer, default=50)
    normalization_mode = Column(String(20), default="daily")
    show_expenses_for = Column(String(10), default="month")
    subscribers_count = Column(Integer, default=0)
    day_before_yesterday_percent = Column(Float, default=0.0)
    yesterday_percent = Column(Float, default=0.0)
    today_percent = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Связи
    target = relationship("MainEntity")
    bot = relationship("BotSession")

    settings = relationship("BoosterSettings", backref="view_boost_tasks")
    expenses = relationship("ViewBoostExpense", back_populates="task")


class ViewDistribution(Base):
    """
    Распределение просмотров по часам для задачи умного просмотра (SQLAlchemy версия).
    """
    __tablename__ = "view_boost_distributions"

    id = Column(BigInteger, primary_key=True)

    # Данные распределения для 4 режимов
    morning_distribution = Column(
        JSON, default=dict, comment="Распределение утренних постов по часам (05:00-10:00)")
    day_distribution = Column(
        JSON, default=dict, comment="Распределение дневных постов по часам (10:00-16:00)")
    evening_distribution = Column(
        JSON, default=dict, comment="Распределение вечерних постов по часам (16:00-22:00)")
    night_distribution = Column(
        JSON, default=dict, comment="Распределение ночных постов по часам (22:00-05:00)")

    # Метаданные
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, comment="Дата последнего обновления")

    def __repr__(self):
        return f"<ViewDistribution>"


class ViewBoostExpense(Base):
    __tablename__ = "view_boost_expenses"

    id = Column(BigInteger, primary_key=True)
    task_id = Column(BigInteger, ForeignKey(
        "view_boost_tasks.id"), nullable=False)
    service_id = Column(Integer, nullable=True, default=0)
    views_count = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Связи
    task = relationship("ViewBoostTask", back_populates="expenses")


class ActivePostTracking(Base):
    __tablename__ = "view_boost_active_posts"

    id = Column(BigInteger, primary_key=True)
    task_id = Column(BigInteger, ForeignKey(
        "view_boost_tasks.id"), nullable=False)
    message_id = Column(BigInteger, nullable=False)
    post_type = Column(String(10))  # morning/main
    total_views_needed = Column(Integer, nullable=False)
    publish_time = Column(DateTime, nullable=False)
    completed_hours = Column(JSON)  # Список обработанных часов
    is_completed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    # Связи
    task = relationship("ViewBoostTask", backref="active_posts")

    __table_args__ = (
        UniqueConstraint('task_id', 'message_id', name='uq_task_message'),
    )

# ============================================================
# 🔹 Old Views Booster Models
# ============================================================


class OldViewsTask(Base):
    __tablename__ = "old_views_tasks"

    id = Column(BigInteger, primary_key=True)
    is_active = Column(Boolean, default=True)
    target_id = Column(BigInteger, ForeignKey(
        "api_mainentity.id"), nullable=False)
    bot_id = Column(BigInteger, ForeignKey(
        "telegram_botsession.id"), nullable=False)
    settings_id = Column(BigInteger, ForeignKey(
        "booster_settings.id"), nullable=True)

    # Конфигурация (ОБНОВЛЕННЫЕ ПОЛЯ)
    view_coefficient = Column(Integer, default=50, comment="Охват ERR-24 в %")
    normalization_mode = Column(String(
        20), default="monthly", comment="Период нормализации: monthly/bi_monthly/weekly/bi_weekly/daily")
    posts_normalization = Column(String(
        20), default="last_100", comment="Нормализация постов: last_100/last_200/last_300/first_100/first_200/first_300")
    views_multiplier = Column(Integer, default=1, comment="Кратность оценки")

    run_once = Column(Boolean, default=False, comment="Запустить разово")
    exclude_period = Column(String(
        20), default="none", comment="Исключая последние: 1_day/2_days/1_week/2_weeks/none")

    # Статистика
    subscribers_count = Column(
        Integer, default=0, comment="Количество подписчиков")
    last_successful_run = Column(
        DateTime, nullable=True, comment="Дата последнего успешного запуска")

    # Временные метки
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Связи
    target = relationship("MainEntity")
    bot = relationship("BotSession")
    settings = relationship("BoosterSettings", backref="old_views_tasks")
    expenses = relationship("OldViewsExpense", back_populates="task")


class OldViewsExpense(Base):
    __tablename__ = "old_views_expenses"

    id = Column(BigInteger, primary_key=True)
    task_id = Column(BigInteger, ForeignKey(
        "old_views_tasks.id"), nullable=False)
    service_id = Column(Integer, nullable=True, default=0)
    post_message_id = Column(BigInteger, nullable=False,
                             comment="ID поста в Telegram")
    views_count = Column(Integer, nullable=False,
                         comment="Количество отправленных просмотров")
    price = Column(Float, nullable=False, comment="Стоимость")
    created_at = Column(DateTime, default=datetime.utcnow)

    # Связи
    task = relationship("OldViewsTask", back_populates="expenses")

# ============================================================
# 🔹 Subscribers Booster Models
# ============================================================


class SubscribersBoostTask(Base):
    __tablename__ = "subscribers_boost_tasks"

    id = Column(BigInteger, primary_key=True)
    is_active = Column(Boolean, default=True)
    target_id = Column(BigInteger, ForeignKey(
        "api_mainentity.id"), nullable=False)
    bot_id = Column(BigInteger, ForeignKey(
        "telegram_botsession.id"), nullable=False)
    settings_id = Column(BigInteger, ForeignKey(
        "booster_settings.id"), nullable=True)

    # Конфигурация
    check_interval = Column(Integer, default=60,
                            comment="Частота проверки в минутах")
    max_subscribers = Column(
        Integer, default=0, comment="Максимальное количество подписчиков")
    notify_on_exceed = Column(Boolean, default=False,
                              comment="Оповещать при превышении лимита")

    last_processed_event_id = Column(
        BigInteger, nullable=True, comment="ID последнего обработанного системного сообщения")

    # Временные метки
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Связи
    target = relationship("MainEntity")
    bot = relationship("BotSession")
    settings = relationship(
        "BoosterSettings", backref="subscribers_boost_tasks")
    checks = relationship("SubscribersCheck", back_populates="task")
    subscriber_lists = relationship("SubscriberList", back_populates="task")
    expenses = relationship("SubscribersBoostExpense", back_populates="task")


class SubscriberList(Base):
    __tablename__ = "subscriber_lists"

    id = Column(BigInteger, primary_key=True)
    task_id = Column(BigInteger, ForeignKey(
        "subscribers_boost_tasks.id"), nullable=False)

    # Список подписчиков (храним user_id)
    subscriber_ids = Column(JSON, nullable=False,
                            comment="Список ID подписчиков")

    # Временные метки
    created_at = Column(DateTime, default=datetime.utcnow)

    # Связи
    task = relationship("SubscribersBoostTask",
                        back_populates="subscriber_lists")


class SubscribersCheck(Base):
    __tablename__ = "subscribers_checks"

    id = Column(BigInteger, primary_key=True)
    task_id = Column(BigInteger, ForeignKey(
        "subscribers_boost_tasks.id"), nullable=False)

    # Статистика
    total_subscribers = Column(
        Integer, nullable=False, comment="Общее количество подписчиков")
    new_subscriptions = Column(
        Integer, default=0, comment="Новые подписки с последней проверки")
    new_unsubscriptions = Column(
        Integer, default=0, comment="Новые отписки с последней проверки")
    unsubscribed_users = Column(
        JSON, default=[], comment="Список ID отписавшихся пользователей")

    # Временные метки
    created_at = Column(DateTime, default=datetime.utcnow)

    # Связи
    task = relationship("SubscribersBoostTask", back_populates="checks")


class SubscribersBoostExpense(Base):
    __tablename__ = "subscribers_boost_expenses"

    id = Column(BigInteger, primary_key=True)
    task_id = Column(BigInteger, ForeignKey(
        "subscribers_boost_tasks.id"), nullable=False)
    service_id = Column(Integer, nullable=True, default=0)
    subscribers_count = Column(
        Integer, nullable=False, comment="Количество отправленных подписчиков")
    price = Column(Float, nullable=False, comment="Стоимость")
    created_at = Column(DateTime, default=datetime.utcnow)

    # Связи
    task = relationship("SubscribersBoostTask", back_populates="expenses")
# ============================================================
# 🔹 Reaction Booster Models (SQLAlchemy)
# ============================================================


class ReactionBoostTask(Base):
    __tablename__ = "reaction_boost_tasks"

    id = Column(BigInteger, primary_key=True)

    is_active = Column(Boolean, default=True)

    target_id = Column(BigInteger, ForeignKey(
        "api_mainentity.id"), nullable=False)
    bot_id = Column(BigInteger, ForeignKey(
        "telegram_botsession.id"), nullable=False)

    # Конфигурация
    posts_count = Column(Integer, default=10,
                         comment="Количество постов для обработки")
    reactions_per_post = Column(
        Integer, default=5, comment="Количество реакций на пост")
    reaction_type = Column(String(20), default="positive",
                           comment="Тип реакций: positive/neutral/negative")
    frequency_days = Column(
        Integer, default=1, comment="Частота запуска в днях")
    launch_time = Column(Time, nullable=False, comment="Время запуска (HH:MM)")

    # Запуск вне расписания
    run_once_now = Column(Boolean, default=False,
                          comment="Запустить только сейчас (разовый запуск)")

    # Статистика
    last_launch = Column(DateTime, nullable=True,
                         comment="Дата последнего запуска")

    # Временные метки
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Связи
    target = relationship("MainEntity")
    bot = relationship("BotSession")
    records = relationship(
        "ReactionRecord",
        back_populates="task",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<ReactionBoostTask id={self.id} target={getattr(self.target, 'name', None)!r}>"


class ReactionRecord(Base):
    __tablename__ = "reaction_records"

    id = Column(BigInteger, primary_key=True)
    task_id = Column(BigInteger, ForeignKey(
        "reaction_boost_tasks.id"), nullable=False)
    post_message_id = Column(BigInteger, nullable=False,
                             comment="ID поста в Telegram")
    bot_id = Column(BigInteger, nullable=False,
                    comment="ID бота, поставившего реакцию")
    reaction = Column(String(50), nullable=False,
                      comment="Тип реакции (emoji)")
    created_at = Column(DateTime, default=datetime.utcnow)

    # Связи
    task = relationship("ReactionBoostTask", back_populates="records")

    def __repr__(self):
        return f"<ReactionRecord id={self.id} task_id={self.task_id} reaction={self.reaction!r}>"


logger = logging.getLogger(__name__)

# ============================================================
# 🔹 Channel Sync Models
# ============================================================


class ChannelSyncTask(Base):
    __tablename__ = "channel_sync_tasks"

    id = Column(BigInteger, primary_key=True)
    is_active = Column(Boolean, default=True)
    source_id = Column(BigInteger, ForeignKey(
        "api_mainentity.id"), nullable=False)
    target_id = Column(BigInteger, ForeignKey(
        "api_mainentity.id"), nullable=False)
    bot_id = Column(BigInteger, ForeignKey(
        "telegram_botsession.id"), nullable=False)

    # Конфигурация синхронизации
    update_period_days = Column(
        Integer, nullable=True, comment="Период актуализации в днях (null - никогда)")
    update_range = Column(String(20), default="new_only",
                          comment="Диапазон актуализации: new_only/full")
    run_once_task = Column(
        Boolean, default=False, comment="Немедленная синхронизация только новых постов")

    # Статистика
    source_subscribers_count = Column(
        Integer, default=0, comment="Текущее количество подписчиков источника")
    last_sync_date = Column(DateTime, nullable=True,
                            comment="Дата последней синхронизации")

    # Временные метки
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    target_posts_count = Column(
        Integer, default=0, comment="Количество постов в целевом канале")
    scheduled_time = Column(Time, default=datetime.utcnow,
                            comment="Время запуска по расписанию")

    # Связи
    source = relationship("MainEntity", foreign_keys=[source_id])
    target = relationship("MainEntity", foreign_keys=[target_id])
    bot = relationship("BotSession")
    history = relationship("ChannelSyncHistory",
                           back_populates="task", cascade="all, delete-orphan")
    progress = relationship("ChannelSyncProgress", back_populates="task",
                            uselist=False, cascade="all, delete-orphan")


class ChannelSyncHistory(Base):
    __tablename__ = "channel_sync_history"

    id = Column(BigInteger, primary_key=True)
    task_id = Column(BigInteger, ForeignKey(
        "channel_sync_tasks.id"), nullable=False)
    sync_date = Column(DateTime, default=datetime.utcnow)
    posts_before = Column(Integer, default=0,
                          comment="Количество постов в цели до синхронизации")
    posts_after = Column(
        Integer, default=0, comment="Количество постов в цели после синхронизации")
    source_subscribers_count = Column(
        Integer, default=0, comment="Количество подписчиков источника на момент синхронизации")
    last_post_url = Column(String(512), nullable=True,
                           comment="URL последнего поста")

    # Связи
    task = relationship("ChannelSyncTask", back_populates="history")


class ChannelSyncProgress(Base):
    __tablename__ = "channel_sync_progress"

    id = Column(BigInteger, primary_key=True)
    task_id = Column(BigInteger, ForeignKey(
        "channel_sync_tasks.id"), nullable=False)
    total_posts_to_copy = Column(
        Integer, default=0, comment="Всего постов для копирования")
    copied_posts = Column(Integer, default=0, comment="Скопировано постов")
    last_copied_message_id = Column(
        BigInteger, nullable=True, comment="ID последнего скопированного сообщения")
    is_completed = Column(Boolean, default=False,
                          comment="Завершена ли текущая синхронизация")
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Связи
    task = relationship("ChannelSyncTask", back_populates="progress")


class BlondinkaTask(Base):
    __tablename__ = "blondinka_tasks"

    id = Column(BigInteger, primary_key=True)
    is_active = Column(Boolean, default=True)
    run_now = Column(Boolean, default=True)
    bot_id = Column(BigInteger, ForeignKey(
        "telegram_botsession.id"), nullable=False)
    group_id = Column(BigInteger, ForeignKey(
        "api_mainentity.id"), nullable=False)
    group_theme_id = Column(BigInteger, ForeignKey(
        "blondinka_group_themes.id"), nullable=True)
    owner_type = Column(String(20), default="own")
    delete_post_after = Column(Integer, nullable=True)
    # subscribers_count = Column(Integer, default=0)
    # subscribers_updated = Column(DateTime, nullable=True)
    working_days = Column(JSON, default=[])
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Связи
    bot = relationship("BotSession")
    group = relationship("MainEntity")
    group_theme = relationship("GroupTheme")
    schedules = relationship("BlondinkaSchedule", back_populates="task")
    logs = relationship("BlondinkaLog", back_populates="task")
    task_dialogs = relationship("BlondinkaTaskDialog", back_populates="task")


class BlondinkaSchedule(Base):
    __tablename__ = "blondinka_schedules"

    id = Column(BigInteger, primary_key=True)
    task_id = Column(BigInteger, ForeignKey(
        "blondinka_tasks.id"), nullable=False)
    day_of_week = Column(Integer, nullable=False)
    publish_time = Column(Time, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Связи
    task = relationship("BlondinkaTask", back_populates="schedules")


class BlondinkaDialog(Base):
    __tablename__ = "blondinka_dialogs"

    id = Column(BigInteger, primary_key=True)
    theme_id = Column(BigInteger, ForeignKey(
        "blondinka_group_themes.id"), nullable=False)
    message = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True)
    order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Связи
    theme = relationship("GroupTheme", back_populates="dialogs")
    task_dialogs = relationship("BlondinkaTaskDialog", back_populates="dialog")


class GroupTheme(Base):
    __tablename__ = "blondinka_group_themes"

    id = Column(BigInteger, primary_key=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)

    # Связь с Category (One-to-One)
    category_id = Column(BigInteger, ForeignKey(
        "admin_panel_category.id"), nullable=True)
    category = relationship("Category", backref="theme")

    # Связи
    dialogs = relationship("BlondinkaDialog", back_populates="theme")
    tasks = relationship("BlondinkaTask", back_populates="group_theme")


class BlondinkaTaskDialog(Base):
    """Промежуточная модель для связи задачи с диалогами и управления активностью"""
    __tablename__ = "blondinka_task_dialogs"

    id = Column(BigInteger, primary_key=True)
    task_id = Column(BigInteger, ForeignKey(
        "blondinka_tasks.id"), nullable=False)
    dialog_id = Column(BigInteger, ForeignKey(
        "blondinka_dialogs.id"), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Связи
    task = relationship("BlondinkaTask", back_populates="task_dialogs")
    dialog = relationship("BlondinkaDialog", back_populates="task_dialogs")

    # Уникальный индекс для предотвращения дублирования связей
    __table_args__ = (
        UniqueConstraint('task_id', 'dialog_id', name='uq_task_dialog'),
    )


class BlondinkaLog(Base):
    __tablename__ = "blondinka_logs"

    id = Column(BigInteger, primary_key=True)
    task_id = Column(BigInteger, ForeignKey(
        "blondinka_tasks.id"), nullable=False)
    post_content = Column(Text, nullable=False)
    post_url = Column(String(512), nullable=True)
    is_success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Связи
    task = relationship("BlondinkaTask", back_populates="logs")


class BoosterServiceRotation(Base):
    """
    Модель для ротации сервисов бустера - полностью самостоятельная
    """
    __tablename__ = "booster_service_rotation"

    id = Column(BigInteger, primary_key=True)

    # Поля для отслеживания ротации
    module = Column(String(50), nullable=False,
                    comment="Модуль: new_views, old_views, subscribers")
    service_type = Column(String(50), nullable=False,
                          default="", comment="Тип сервиса (для совместимости)")
    is_active = Column(Boolean, default=True, comment="Активен ли сервис")
    last_used_tariff_id = Column(
        Integer, default=0, comment="ID последнего использованного тарифа")
    default_service_id = Column(
        Integer, default=0, comment="ID сервиса по умолчанию")

    created_at = Column(DateTime(timezone=True),
                        server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(
    ), onupdate=func.now(), nullable=False)

    # Кэш для хранения информации об активных заказах
    active_orders_cache = Column(
        JSON, default=dict, comment="Кэш активных заказов по service_id")
    last_orders_check = Column(
        DateTime, nullable=True, comment="Время последней проверки заказов")

    # Проценты для специальных service_id
    SPECIAL_PERCENTAGES = {
        "new_views": {
            4217: 80,
        },
        "old_views": {
            2735: 80,
        },
        "subscribers": {
            3359: 80,
        }
    }

    def _calculate_probability_distribution(self, tariffs: List['BoosterTariff'], needed_count: int) -> List[Tuple[int, float]]:
        """
        Рассчитывает вероятностное распределение для доступных тарифов.

        Правила:
        1. Тарифы с min_limit > needed_count полностью исключаются
        2. Чем ниже цена (price_per_1000), тем выше вероятность
        3. Специальные service_id получают бонус к вероятности из SPECIAL_PERCENTAGES
        4. Основные тарифы (is_primary=True) получают дополнительный бонус

        :param tariffs: Список тарифов BoosterTariff
        :param needed_count: Нужное количество просмотров/подписчиков
        :return: Список кортежей (service_id, вероятность)
        """
        # Шаг 1: Фильтрация по min_limit
        suitable_tariffs = []
        for tariff in tariffs:
            if tariff.min_limit <= needed_count:
                suitable_tariffs.append(tariff)

        if not suitable_tariffs:
            logger.warning(
                f"❌ Для needed_count={needed_count} нет подходящих тарифов (min_limit слишком высок)")
            return []

        # Шаг 2: Расчет базовых вероятностей на основе цены
        # Чем ниже цена, тем выше вероятность (обратная зависимость)
        prices = [t.price_per_1000 for t in suitable_tariffs]
        if all(p == 0 for p in prices):
            # Если все цены нулевые, используем равное распределение
            base_probabilities = [
                1.0 / len(suitable_tariffs)] * len(suitable_tariffs)
        else:
            # Нормализуем цены и инвертируем (низкая цена = высокая вероятность)
            max_price = max(prices)
            min_price = min(prices) if min(prices) > 0 else 1

            # Используем формулу: вероятность обратно пропорциональна цене
            base_probabilities = []
            for tariff in suitable_tariffs:
                if tariff.price_per_1000 == 0:
                    # Бесплатный тариф получает максимальную вероятность
                    prob = 1.0
                else:
                    # Инвертируем цену: чем дешевле, тем выше вероятность
                    prob = max_price / tariff.price_per_1000 if tariff.price_per_1000 > 0 else 1.0
                base_probabilities.append(prob)

            # Нормализуем базовые вероятности
            total = sum(base_probabilities)
            base_probabilities = [p / total for p in base_probabilities]

        # Шаг 3: Применение модификаторов
        final_probabilities = []
        for i, tariff in enumerate(suitable_tariffs):
            probability = base_probabilities[i]

            # Бонус для специальных service_id
            special_percent = self.SPECIAL_PERCENTAGES.get(
                self.module, {}).get(tariff.service_id)
            if special_percent:
                # Пример: если special_percent=80, то вероятность умножается на 1.8
                probability *= (1.0 + special_percent / 100.0)

            # Бонус для основных тарифов
            if tariff.is_primary:
                probability *= 1.5  # 50% бонус

            final_probabilities.append(probability)

        # Шаг 4: Нормализация финальных вероятностей
        total = sum(final_probabilities)
        if total == 0:
            # Если все вероятности нулевые, используем равное распределение
            normalized_probabilities = [
                1.0 / len(final_probabilities)] * len(final_probabilities)
        else:
            normalized_probabilities = [p / total for p in final_probabilities]

        # Формируем результат
        result = []
        for i, tariff in enumerate(suitable_tariffs):
            result.append((tariff.service_id, normalized_probabilities[i]))

        # Сортируем по убыванию вероятности для логирования
        result.sort(key=lambda x: x[1], reverse=True)

        logger.debug(
            f"📊 Распределение вероятностей для module={self.module}, needed_count={needed_count}:")
        for service_id, prob in result:
            tariff_info = next(
                (t for t in suitable_tariffs if t.service_id == service_id), None)
            if tariff_info:
                logger.debug(f"  • service_id={service_id}: {prob:.2%} (цена={tariff_info.price_per_1000}, "
                             f"min_limit={tariff_info.min_limit}, primary={tariff_info.is_primary})")

        return result

    @staticmethod
    async def create_order(task_id: int, task_type: str, service_id: int,
                           external_order_id: str, quantity: int, price: float,
                           expense_id: Optional[int] = None) -> 'BoosterOrder':
        """
        Создаёт запись о заказе
        """
        try:
            # Имитируем session, так как в оригинальном коде session не передавался
            from sqlalchemy.orm import Session
            session = Session.object_session(
                self) if hasattr(self, '__dict__') else None

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

            if session:
                session.add(order)
                session.commit()

            logger.info(
                f"✅ Создан заказ {external_order_id} для задачи {task_type} #{task_id}, service_id: {service_id}")
            return order

        except Exception as e:
            logger.error(f"❌ Ошибка при создании заказа: {e}")
            raise

    async def check_active_orders(self, session, booster_settings: 'BoosterSettings') -> Dict[int, int]:
        """
        Проверяет активные заказы через API и возвращает количество активных заказов по service_id
        """
        from sqlalchemy import select, and_

        try:
            # Получаем текущее время с учетом часового пояса UTC
            now_utc = datetime.now(pytz.UTC)

            # Проверяем, нужно ли обновлять кэш (раз в 5 минут)
            if (self.last_orders_check and
                # Исправлено здесь
                (now_utc - self.last_orders_check.replace(tzinfo=pytz.UTC) if self.last_orders_check.tzinfo is None else self.last_orders_check).total_seconds() < 300 and
                    self.active_orders_cache):
                logger.debug(
                    f"📊 Используем кэшированные данные об активных заказов для {self.module}")
                return self.active_orders_cache

            # Получаем все активные заказы для этого модуля из нашей БД
            stmt = select(BoosterOrder).where(
                and_(
                    BoosterOrder.task_type == self.module,
                    BoosterOrder.status.in_(['pending', 'in_progress']),
                    BoosterOrder.external_order_id.isnot(None)
                )
            )
            active_orders = session.execute(stmt).scalars().all()

            if not active_orders:
                logger.debug(
                    f"📊 Нет активных заказов для модуля {self.module}")
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
                external_order_ids = [str(order.external_order_id)
                                      for order in orders if order.external_order_id]

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

                                    # Подсчитываем активные заказы
                                    for order_id, status_info in data.items():
                                        if isinstance(status_info, dict):
                                            status = status_info.get(
                                                'status', '')
                                            # Активными считаем заказы, которые ещё не завершены
                                            if status not in ['Completed', 'Canceled', 'Fail']:
                                                active_count += 1

                                                # Обновляем статус в БД если нужно
                                                order = next((o for o in orders if str(
                                                    o.external_order_id) == order_id), None)
                                                if order and order.status != 'in_progress':
                                                    order.status = 'in_progress'
                                                    order.api_response = status_info

                                                elif order and status in ['Completed', 'Canceled', 'Fail']:
                                                    order.status = 'completed' if status == 'Completed' else 'failed'
                                                    order.completed_at = datetime.now(
                                                        pytz.UTC)
                                                    order.api_response = status_info

                        session.commit()

                    except Exception as e:
                        logger.error(
                            f"❌ Ошибка при проверке заказов для service_id {service_id}: {e}")
                        # В случае ошибки считаем все заказы активными для безопасности
                        active_count += len(batch)

                active_orders_count[service_id] = active_count
                logger.debug(
                    f"📊 Service_id {service_id}: {active_count} активных заказов")

            # Обновляем кэш
            self.active_orders_cache = active_orders_count
            self.last_orders_check = now_utc  # Используем уже созданный now_utc
            session.commit()

            logger.info(
                f"✅ Обновлены данные об активных заказов для {self.module}")
            return active_orders_count

        except Exception as e:
            logger.error(
                f"❌ Ошибка при проверке активных заказов для {self.module}: {e}")
            return {}

    def _get_available_tariffs(self, session, tariffs: list, count: int,
                               active_orders_count: Dict[int, int]) -> List[dict]:
        """
        Возвращает список доступных тарифов с учетом очередей
        """
        available_tariffs = []

        for tariff in tariffs:
            # Проверяем минимальный лимит
            if tariff.min_limit > count:
                continue

            if (tariff.module == self.module and
                    tariff.is_active):

                # Проверяем очередь для этого service_id
                active_orders = active_orders_count.get(tariff.service_id, 0)
                has_queue = active_orders >= 2  # Более 2 активных заказов = очередь

                available_tariffs.append({
                    'tariff': tariff,
                    'service_id': tariff.service_id,
                    'is_primary': tariff.is_primary,
                    'price_per_1000': tariff.price_per_1000,
                    'active_orders': active_orders,
                    'has_queue': has_queue,
                    'min_limit': tariff.min_limit
                })

        # Сортируем: сначала без очереди, затем основные тарифы, затем по цене
        available_tariffs.sort(key=lambda x: (
            x['has_queue'],  # False (0) first, True (1) second
            not x['is_primary'],  # Primary first
            x['price_per_1000']  # Cheaper first
        ))

        return available_tariffs

    async def get_next_service_id(self, session, tariffs: list, count: int,
                                  booster_settings: 'BoosterSettings') -> int:
        """
        Возвращает следующий service_id с учетом очередей активных заказов
        """
        from datetime import datetime

        try:
            # Получаем информацию об активных заказах
            active_orders_count = await self.check_active_orders(session, booster_settings)

            # Получаем доступные тарифы с учетом очередей
            available_tariffs = self._get_available_tariffs(
                session, tariffs, count, active_orders_count)

            if not available_tariffs:
                logger.warning(
                    f"⚠️ Для модуля {self.module} и count={count} нет доступных тарифов, используем default_service_id: {self.default_service_id}")
                return self.default_service_id

            # Фильтруем тарифы без очереди
            no_queue_tariffs = [
                t for t in available_tariffs if not t['has_queue']]

            if no_queue_tariffs:
                # Выбираем из тарифов без очереди
                # Используем вероятностное распределение
                distribution = self._calculate_probability_distribution(
                    [t['tariff'] for t in no_queue_tariffs],
                    count
                )

                if distribution:
                    service_ids, probabilities = zip(*distribution)
                    total_prob = sum(probabilities)
                    normalized_probabilities = [
                        prob / total_prob for prob in probabilities]
                    chosen_service_id = random.choices(
                        service_ids, weights=normalized_probabilities, k=1)[0]
                else:
                    # Если нет распределения, выбираем первый подходящий
                    chosen_tariff = no_queue_tariffs[0]
                    chosen_service_id = chosen_tariff['service_id']

                chosen_tariff_info = next(
                    (t for t in no_queue_tariffs if t['service_id'] == chosen_service_id), None)
                logger.info(f"✅ Выбран тариф без очереди: service_id={chosen_service_id}, "
                            f"цена={chosen_tariff_info['price_per_1000'] if chosen_tariff_info else 'N/A'}, "
                            f"активных заказов={chosen_tariff_info['active_orders'] if chosen_tariff_info else 'N/A'}")
            else:
                # Все тарифы в очереди - используем основной тариф
                primary_tariffs = [
                    t for t in available_tariffs if t['is_primary']]

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
            chosen_tariff = next(
                (t['tariff'] for t in available_tariffs if t['service_id'] == chosen_service_id), None)
            if chosen_tariff:
                self.last_used_tariff_id = chosen_tariff.id
            self.updated_at = datetime.now()
            session.commit()

            return chosen_service_id

        except Exception as e:
            session.rollback()
            logger.error(
                f"❌ Критическая ошибка в выборе тарифа для {self.module} (count={count}): {e}")
            logger.info(
                f"🔄 Используем default_service_id: {self.default_service_id}")
            return self.default_service_id

    @classmethod
    def get_or_create_rotation(cls, session, module_name: str, default_service_id: int = None):
        """
        Получает или создает запись ротации для модуля
        """
        try:
            rotation = session.query(cls).filter(
                cls.module == module_name).first()
            if not rotation:
                rotation = cls(
                    module=module_name,
                    service_type=module_name,  # Для совместимости
                    default_service_id=default_service_id or 0
                )
                session.add(rotation)
                session.commit()
            return rotation
        except Exception as e:
            logger.error(
                f"❌ Ошибка получения/создания ротации для {module_name}: {e}")
            session.rollback()
            # Возвращаем временный объект
            return cls(
                module=module_name,
                service_type=module_name,
                default_service_id=default_service_id or 0
            )

    @classmethod
    async def get_next_service_id_for_module(cls, session, module_name: str,
                                             tariffs: list, default_service_id: int = None,
                                             count: int = 10, booster_settings: 'BoosterSettings' = None) -> int:
        """
        Статический метод для получения следующего service_id с учетом очередей
        """
        logger.debug(
            f"🎯 Запрос следующего service_id для модуля: {module_name}, count: {count}")

        if not booster_settings:
            logger.error(
                "❌ Не переданы booster_settings для проверки очередей")
            return default_service_id or 0

        try:
            rotation = cls.get_or_create_rotation(
                session, module_name, default_service_id)
            result = await rotation.get_next_service_id(session, tariffs, count, booster_settings)
            logger.info(
                f"✅ Получен service_id {result} для модуля {module_name} (count={count})")
            return result
        except Exception as e:
            logger.error(
                f"❌ Ошибка в get_next_service_id_for_module для {module_name} (count={count}): {e}")
            return default_service_id or 0

    # Обновляем старые методы для обратной совместимости
    def get_next_service_id_sync(self, session, tariffs: list, count: int) -> int:
        """
        Синхронная версия для обратной совместимости (без проверки очередей)
        """
        import asyncio
        return asyncio.run(self.get_next_service_id(session, tariffs, count, None))

    @classmethod
    def get_next_service_id_for_module_sync(cls, session, module_name: str,
                                            tariffs: list, default_service_id: int = None,
                                            count: int = 10) -> int:
        """
        Синхронная версия для обратной совместимости
        """
        import asyncio
        return asyncio.run(cls.get_next_service_id_for_module(
            session, module_name, tariffs, default_service_id, count, None
        ))

# ============================================================
# 🔹 Booster Orders Tracking
# ============================================================


class BoosterOrder(Base):
    """
    Модель для отслеживания заказов на накрутку
    """
    __tablename__ = "booster_orders"

    id = Column(BigInteger, primary_key=True)

    # Ссылка на задачу
    task_id = Column(BigInteger, nullable=False,
                     comment="ID задачи (ViewBoostTask/OldViewsTask/SubscribersBoostTask)")
    task_type = Column(String(20), nullable=False,
                       comment="Тип задачи: new_views/old_views/subscribers")

    # Данные заказа
    external_order_id = Column(
        String(50), nullable=False, comment="ID заказа во внешней системе")
    service_id = Column(Integer, nullable=False, comment="ID сервиса накрутки")

    # Статус
    status = Column(String(20), default="pending",
                    comment="Статус: pending/in_progress/completed/failed")
    api_response = Column(JSON, nullable=True,
                          comment="Последний ответ от API")

    # Параметры заказа
    quantity = Column(Integer, nullable=False,
                      comment="Количество (просмотры/подписчики)")
    price = Column(Float, nullable=False, comment="Стоимость")

    # Временные метки
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    # Связь с расходом (если есть)
    expense_id = Column(BigInteger, nullable=True)

    def __repr__(self):
        return f"<BoosterOrder(id={self.id}, external={self.external_order_id}, service={self.service_id}, status={self.status})>"


BoosterOrder.__table_args__ = (
    Index('idx_booster_orders_external', BoosterOrder.external_order_id),
    Index('idx_booster_orders_service',
          BoosterOrder.service_id, BoosterOrder.status),
    Index('idx_booster_orders_task', BoosterOrder.task_id, BoosterOrder.task_type),
)


class BoosterSettings(Base):
    """
    Настройки бустера
    """
    __tablename__ = "booster_settings"

    id = Column(BigInteger, primary_key=True)

    # --- Основные поля аккаунта ---
    url = Column(String(255), nullable=False,
                 default="", comment="Ссылка на биржу")
    api_key = Column(String(128), nullable=False, default="",
                     comment="API ключ от сервиса накрутки")

    # --- Сервисы ---
    new_views_service_id = Column(
        Integer, default=0, comment="ID сервиса умных просмотров новых постов")
    old_views_service_id = Column(
        Integer, default=0, comment="ID сервиса просмотров старых постов")
    subscribers_service_id = Column(
        Integer, default=0, comment="ID сервиса подписчиков каналов/групп")

    # --- Статус активности каждого модуля ---
    is_active_new_views = Column(
        Boolean, default=False, comment="Активен модуль новых просмотров")
    is_active_old_views = Column(
        Boolean, default=False, comment="Активен модуль старых просмотров")
    is_active_subscribers = Column(
        Boolean, default=False, comment="Активен модуль подписчиков")

    # --- Минимальные лимиты ---
    min_new_views = Column(
        Integer, default=0, comment="Минимальное количество умных просмотров")
    min_old_views = Column(
        Integer, default=0, comment="Минимальное количество просмотров старых постов")
    min_subscribers = Column(
        Integer, default=0, comment="Минимальное количество подписчиков")

    # --- Баланс и метаданные ---
    balance = Column(Float, default=0.0, comment="Баланс на бирже")
    last_balance_check = Column(
        DateTime, nullable=True, comment="Дата последней проверки баланса")

    created_at = Column(DateTime(timezone=True),
                        server_default=func.now(), comment="Дата создания записи")
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(
    ), comment="Дата последнего обновления")

    # Связь с тарифами
    tariffs = relationship("BoosterTariff", back_populates="booster")

    def get_active_service_id(self, session, module_name: str) -> int:
        """
        Удобный метод для получения активного service_id через ротацию
        """
        logger.debug(
            f"🎯 Получение активного service_id для {module_name} через ротацию")

        # Получаем дефолтный service_id из настроек
        default_service_id = getattr(self, f"{module_name}_service_id", 0)

        # Используем ротацию для получения следующего service_id
        service_id = BoosterServiceRotation.get_next_service_id_for_module(
            session=session,
            module_name=module_name,
            tariffs=self.tariffs,
            default_service_id=default_service_id
        )

        logger.info(
            f"✅ Для модуля {module_name} получен service_id: {service_id}")
        return service_id

    # Удобные методы для каждого модуля
    def get_active_new_views_service_id(self, session) -> int:
        return self.get_active_service_id(session, "new_views")

    def get_active_old_views_service_id(self, session) -> int:
        return self.get_active_service_id(session, "old_views")

    def get_active_subscribers_service_id(self, session) -> int:
        return self.get_active_service_id(session, "subscribers")

    def __repr__(self):
        return (
            f"<BoosterSettings(id={self.id}, api_key='{self.api_key[:6]}...', "
            f"balance={self.balance}, new_views={self.new_views_service_id}, "
            f"old_views={self.old_views_service_id}, subs={self.subscribers_service_id})>"
        )


class BoosterTariff(Base):
    __tablename__ = "booster_tariffs"

    id = Column(BigInteger, primary_key=True)
    booster_id = Column(BigInteger, ForeignKey(
        "booster_settings.id"), nullable=False)
    # "new_views", "old_views", "subscribers"
    module = Column(String(32), nullable=False)
    service_id = Column(Integer, nullable=False)
    min_limit = Column(Integer, default=0)
    price_per_1000 = Column(Float, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    is_primary = Column(Boolean, default=False)

    # Связь с настройками
    booster = relationship("BoosterSettings", back_populates="tariffs")

    def __repr__(self):
        return f"<BoosterTariff(id={self.id}, module={self.module}, service_id={self.service_id}, active={self.is_active})>"
# ============================================================
# 🔹 Currency models (from 'currency' app)
# ============================================================

class CurrencyGlobals(Base):
    __tablename__ = "currency_globals"
    id = Column(BigInteger, primary_key=True)
    is_active = Column(Boolean, default=True)
    publication_time = Column(Time)
    pin_main_chat = Column(Integer, default=0)
    pin_safe_exchange = Column(Integer, default=0)
    cover = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class CurrencyLocation(Base):
    __tablename__ = "currency_location"
    id = Column(BigInteger, primary_key=True)
    name = Column(String(100))
    hashtag = Column(String(100))
    emoji = Column(String(10))
    is_active = Column(Boolean, default=True)
    
    # Связи
    country_id = Column(BigInteger, ForeignKey("admin_panel_country.id"), nullable=True)
    bot_id = Column(BigInteger, ForeignKey("telegram_botsession.id"), nullable=False)
    main_chat_id = Column(BigInteger, ForeignKey("api_mainentity.id"), nullable=False)
    safe_exchange_id = Column(BigInteger, ForeignKey("api_mainentity.id"), nullable=True)
    
    # Внешние ссылки
    google_rate_url = Column(String(500), nullable=True)
    xe_rate_url = Column(String(500), nullable=True)
    bank_1_url = Column(String(500), nullable=True)
    bank_2_url = Column(String(500), nullable=True)
    bank_3_url = Column(String(500), nullable=True)
    atm_url = Column(String(500), nullable=True)
    magic_country_url = Column(String(500), nullable=True)
    
    # Статистика и состояние
    last_status = Column(String(20), nullable=True)
    last_published = Column(DateTime, nullable=True)
    error_count = Column(Integer, default=0)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    country = relationship("Country", foreign_keys=[country_id])
    bot = relationship("BotSession", foreign_keys=[bot_id])
    main_chat = relationship("MainEntity", foreign_keys=[main_chat_id])
    safe_exchange = relationship("MainEntity", foreign_keys=[safe_exchange_id])
    pairs = relationship("CurrencyPair", back_populates="location")

class CurrencyPair(Base):
    __tablename__ = "currency_currencypair"
    id = Column(BigInteger, primary_key=True)
    location_id = Column(BigInteger, ForeignKey("currency_location.id"), nullable=False)
    from_code = Column(String(10))
    to_code = Column(String(10))
    is_active = Column(Boolean, default=True)
    last_rate = Column(Float, nullable=True)
    last_trend = Column(String(10), nullable=True)
    order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    location = relationship("CurrencyLocation", back_populates="pairs")
    history = relationship("CurrencyRateHistory", back_populates="pair")

class CurrencyRateHistory(Base):
    __tablename__ = "currency_ratehistory"
    id = Column(BigInteger, primary_key=True)
    pair_id = Column(BigInteger, ForeignKey("currency_currencypair.id"), nullable=False)
    rate = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)

    pair = relationship("CurrencyPair", back_populates="history")