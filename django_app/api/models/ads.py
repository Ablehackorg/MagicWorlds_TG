from django.db import models
from django.utils import timezone
from django.core.files.storage import FileSystemStorage
from django.conf import settings
import os

from .entities import avatar_storage

def ad_target_photo_path(instance, filename):
    """Путь для фото в рекламных целях."""
    ext = os.path.splitext(filename)[1] or ".jpg"
    return f"ads_targets/{instance.telegram_id or 'noid'}{ext}"

class AdsOrder(models.Model):
    """
    Задача рекламной публикации «из ссылки на пост».
    Выполняется конкретным ботом в указанную Цель (MainEntity).
    """
    CUSTOMER_STATUS = [
        ("permanent", "Постоянный"),
        ("new", "Новый"),
    ]

    # управление
    is_active = models.BooleanField(default=True, verbose_name="Активность")
    is_paid = models.BooleanField(default=False, verbose_name="Оплачен")

    # описание
    name = models.CharField(max_length=255, verbose_name="Название задачи")
    post_link = models.CharField(max_length=512, verbose_name="Ссылка на пост (t.me/...)")

    # заказчик
    customer_telegram = models.CharField(max_length=64, verbose_name="Телеграм заказчика")
    customer_fullname = models.CharField(max_length=255, blank=True, null=True, verbose_name="ФИО заказчика")
    customer_status = models.CharField(max_length=16, choices=CUSTOMER_STATUS, default="new", verbose_name="Статус заказчика")

    notify_customer = models.BooleanField(default=True, verbose_name="Уведомлять заказчика")
    notify_admin = models.BooleanField(default=True, verbose_name="Уведомлять администратора")

    # цель публикации
    bot = models.ForeignKey("telegram.BotSession", on_delete=models.CASCADE, related_name="ads_orders", verbose_name="Бот")
    target = models.ForeignKey("MainEntity", on_delete=models.CASCADE, related_name="ads_orders", verbose_name="Группа/Канал-цель")

    source = models.ForeignKey(
        "AdTargetEntity",
        on_delete=models.SET_NULL,
        related_name="ads_orders",
        null=True,
        blank=True,
        verbose_name="Локальные данные цели"
    )

    # плановые даты
    ordered_at = models.DateTimeField(default=timezone.now, verbose_name="Дата заказа")
    publish_at = models.DateTimeField(verbose_name="Дата публикации")

    # фактические даты
    published_at = models.DateTimeField(blank=True, null=True, verbose_name="Фактическая дата публикации")
    pinned_at = models.DateTimeField(blank=True, null=True, verbose_name="Фактическая дата закрепления")
    unpinned_at = models.DateTimeField(blank=True, null=True, verbose_name="Фактическая дата открепления")
    deleted_at = models.DateTimeField(blank=True, null=True, verbose_name="Фактическая дата удаления")

    # тех. поля
    target_message_id = models.BigIntegerField(blank=True, null=True, verbose_name="ID сообщения в цели")

    class Meta:
        verbose_name = "Рекламная публикация"
        verbose_name_plural = "Рекламные публикации"
        db_table = "api_adsorder"
        ordering = ["-ordered_at"]

    def __str__(self):
        return f"AdsOrder#{self.id} {self.name} → {getattr(self.target, 'name', '—')}"

    # удобные флаги
    @property
    def is_fully_done(self) -> bool:
        return bool(self.published_at and self.pinned_at and self.unpinned_at and self.deleted_at)

    def update_source_data(self, source_data, photo_file=None):
        """Создает или обновляет связанную AdTargetEntity"""
        if self.source:
            source_entity = self.source
        else:
            source_entity = AdTargetEntity()
        
        source_entity.link = source_data.get('link', '')
        source_entity.telegram_id = source_data.get('telegram_id')
        source_entity.name = source_data.get('name', '')
        source_entity.description = source_data.get('description', '')
        source_entity.entity_type = source_data.get('entity_type', 'channel')
        
        if photo_file:
            source_entity.photo = photo_file
        
        source_entity.save()
        
        self.source = source_entity
        self.save()
        
        return source_entity

class AdTargetEntity(models.Model):
    """
    Локальная копия цели рекламы (канал/группа),
    используется для хранения сведений, введённых вручную или автозаполненных.
    """
    ENTITY_TYPES = [
        ("channel", "Канал"),
        ("group", "Группа"),
    ]

    name = models.CharField(max_length=255, verbose_name="Название", blank=True, null=True)
    telegram_id = models.BigIntegerField(blank=True, null=True, verbose_name="Telegram ID")
    link = models.CharField(max_length=255, blank=True, null=True, verbose_name="Ссылка (@ или t.me/...)")
    description = models.TextField(blank=True, null=True, verbose_name="Описание")
    entity_type = models.CharField(max_length=16, choices=ENTITY_TYPES, blank=True, null=True, verbose_name="Тип (канал/группа)")

    photo = models.ImageField(
        storage=avatar_storage,
        upload_to=ad_target_photo_path,
        blank=True,
        null=True,
        verbose_name="Фото"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Рекламная цель"
        verbose_name_plural = "Рекламные цели"
        db_table = "api_adtargetentity"
        ordering = ["-created_at"]

    def __str__(self):
        return self.name or f"AdTarget#{self.id}"
