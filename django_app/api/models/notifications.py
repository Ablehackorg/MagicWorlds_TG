# models/notifications.py
from django.db import models
from django.utils import timezone
from django.conf import settings

class NotificationModule(models.Model):
    """Справочник модулей системы"""
    MODULE_TYPES = [
        ('CHANNEL_POST', 'Каналы/публикации'),
        ('GROUP_POST', 'Группы/публикации'),
        ('PARSER', 'Парсер'),
        ('BLONDINKA', 'Бот-Блондинка'),
        ('SUBSCRIBERS', 'Нормализатор подписчиков'),
        ('VIEWS', 'Просмотры'),
        ('REACTIONS', 'Реакции'),
        ('SYNC', 'Синхронизация'),
        ('ADS', 'Реклама'),
        ('SYSTEM', 'Система'),
    ]
    
    code = models.CharField(max_length=10, unique=True, verbose_name="Код модуля")
    name = models.CharField(max_length=100, verbose_name="Название модуля")
    module_type = models.CharField(max_length=20, choices=MODULE_TYPES)
    description = models.TextField(blank=True, verbose_name="Описание")
    is_active = models.BooleanField(default=True)

class NotificationType(models.Model):
    """Типы уведомлений/ошибок"""
    SEVERITY_LEVELS = [
        (10, 'DEBUG'),
        (20, 'INFO'),
        (30, 'WARNING'), 
        (40, 'ERROR'),
        (50, 'CRITICAL'),
    ]
    
    module = models.ForeignKey(NotificationModule, on_delete=models.CASCADE)
    code = models.CharField(max_length=10, verbose_name="Код типа")
    name = models.CharField(max_length=100, verbose_name="Название типа")
    severity = models.IntegerField(choices=SEVERITY_LEVELS, default=30)
    description = models.TextField(blank=True, verbose_name="Описание")
    auto_resolve = models.BooleanField(default=False, verbose_name="Авто-решение")
    auto_resolve_minutes = models.IntegerField(default=0, verbose_name="Авто-решение через (мин)")

class SystemNotification(models.Model):
    """Основная модель уведомлений"""
    STATUS_CHOICES = [
        ('NEW', 'Новое'),
        ('ACKNOWLEDGED', 'Принято к сведению'), 
        ('IN_PROGRESS', 'В работе'),
        ('RESOLVED', 'Решено'),
        ('DEFERRED', 'Отложено'),
        ('IGNORED', 'Игнорируется'),
    ]
    
    # Идентификатор уведомления (XXX-XXX-XXX)
    notification_code = models.CharField(max_length=11, unique=True, db_index=True)
    
    # Связи с модулем и типом
    module = models.ForeignKey(NotificationModule, on_delete=models.CASCADE)
    notification_type = models.ForeignKey(NotificationType, on_delete=models.CASCADE)
    
    # Основная информация
    title = models.CharField(max_length=255, verbose_name="Заголовок")
    message = models.TextField(verbose_name="Сообщение")
    details = models.JSONField(default=dict, blank=True, verbose_name="Детали")
    
    # Статус и время
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='NEW')
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    
    # Контекст
    entity = models.ForeignKey('MainEntity', on_delete=models.SET_NULL, null=True, blank=True)
    task_id = models.IntegerField(null=True, blank=True, verbose_name="ID задачи")
    external_id = models.CharField(max_length=100, blank=True, verbose_name="Внешний ID")
    
    # Мета-информация
    source = models.CharField(max_length=100, blank=True, verbose_name="Источник")
    stack_trace = models.TextField(blank=True, verbose_name="Трассировка")
    
    class Meta:
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['module', 'notification_type']),
            models.Index(fields=['entity', 'created_at']),
        ]
        ordering = ['-created_at']

class NotificationAction(models.Model):
    """Действия по уведомлениям"""
    notification = models.ForeignKey(SystemNotification, on_delete=models.CASCADE, related_name='actions')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=50, verbose_name="Действие")
    comment = models.TextField(blank=True, verbose_name="Комментарий")
    created_at = models.DateTimeField(default=timezone.now)
