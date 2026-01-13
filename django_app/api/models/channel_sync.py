from django.db import models
from django.utils import timezone

class ChannelSyncTask(models.Model):
    """Задача синхронизации каналов (клонирования библиотек)"""
    
    UPDATE_RANGE_CHOICES = [
        ("new_only", "Только новые"),
        ("full", "Весь канал"),
    ]
    
    UPDATE_PERIOD_CHOICES = [
        (7, "Неделю"),
        (14, "Две недели"), 
        (30, "Месяц"),
        (None, "Никогда"),
    ]

    is_active = models.BooleanField(default=True, verbose_name="Активность")
    source = models.ForeignKey(
        "MainEntity",
        on_delete=models.CASCADE,
        related_name="channel_sync_source_tasks",
        verbose_name="Канал-Источник"
    )
    target = models.ForeignKey(
        "MainEntity", 
        on_delete=models.CASCADE,
        related_name="channel_sync_target_tasks",
        verbose_name="Канал-Библиотека"
    )
    target_posts_count = models.PositiveIntegerField(default=0, verbose_name="Количество постов в цели")
    scheduled_time = models.TimeField(default=timezone.now, verbose_name="Время запуска по расписанию")
    bot = models.ForeignKey(
        "telegram.BotSession",
        on_delete=models.CASCADE,
        related_name="channel_sync_tasks",
        verbose_name="Бот"
    )
    
    # Конфигурация синхронизации
    update_period_days = models.IntegerField(
        choices=UPDATE_PERIOD_CHOICES,
        null=True,
        blank=True,
        verbose_name="Период актуализации"
    )
    update_range = models.CharField(
        max_length=20,
        choices=UPDATE_RANGE_CHOICES,
        default="new_only",
        verbose_name="Диапазон актуализации"
    )
    run_once_task = models.BooleanField(
        default=False,
        verbose_name="Немедленная синхронизация"
    )
    
    # Статистика
    source_subscribers_count = models.IntegerField(
        default=0,
        verbose_name="Количество подписчиков источника"
    )
    last_sync_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Дата последней синхронизации"
    )
    
    # Временные метки
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        db_table = "channel_sync_tasks"
        verbose_name = "Задача синхронизации каналов"
        verbose_name_plural = "Задачи синхронизации каналов"
        ordering = ["-created_at"]

    def __str__(self):
        return f"ChannelSync#{self.id} → {getattr(self.source, 'name', '—')}"

    @property
    def sync_progress_percent(self):
        """Процент выполнения синхронизации"""
        progress = getattr(self, 'progress', None)
        if not progress or progress.total_posts_to_copy == 0:
            return 100 if progress and progress.is_completed else 0
        
        return min(100, int((progress.copied_posts / progress.total_posts_to_copy) * 100))

    @property
    def update_period_display(self):
        """Отображение периода обновления"""
        if not self.update_period_days:
            return "Никогда"
        elif self.update_period_days == 7:
            return "1 неделя"
        elif self.update_period_days == 14:
            return "2 недели"
        elif self.update_period_days == 30:
            return "1 месяц"
        return f"{self.update_period_days} дней"

class ChannelSyncHistory(models.Model):
    """История синхронизации каналов"""
    
    task = models.ForeignKey(
        ChannelSyncTask,
        on_delete=models.CASCADE,
        related_name="history",
        verbose_name="Задача"
    )
    sync_date = models.DateTimeField(auto_now_add=True, verbose_name="Дата синхронизации")
    posts_before = models.IntegerField(default=0, verbose_name="Постов до")
    posts_after = models.IntegerField(default=0, verbose_name="Постов после")
    source_subscribers_count = models.IntegerField(default=0, verbose_name="Подписчиков источника")
    last_post_url = models.URLField(blank=True, null=True, verbose_name="URL последнего поста")

    class Meta:
        db_table = "channel_sync_history"
        verbose_name = "История синхронизации"
        verbose_name_plural = "История синхронизации"
        ordering = ["-sync_date"]

    def __str__(self):
        return f"History#{self.id} for Task#{self.task_id}"

class ChannelSyncProgress(models.Model):
    """Прогресс выполнения синхронизации"""
    
    task = models.OneToOneField(
        ChannelSyncTask,
        on_delete=models.CASCADE,
        related_name="progress",
        verbose_name="Задача"
    )
    total_posts_to_copy = models.IntegerField(default=0, verbose_name="Всего постов для копирования")
    copied_posts = models.IntegerField(default=0, verbose_name="Скопировано постов")
    last_copied_message_id = models.BigIntegerField(
        null=True,
        blank=True,
        verbose_name="ID последнего скопированного сообщения"
    )
    is_completed = models.BooleanField(default=False, verbose_name="Завершено")
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="Начато")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="Завершено")

    class Meta:
        db_table = "channel_sync_progress"
        verbose_name = "Прогресс синхронизации"
        verbose_name_plural = "Прогрессы синхронизации"

    def __str__(self):
        return f"Progress for Task#{self.task_id}"
