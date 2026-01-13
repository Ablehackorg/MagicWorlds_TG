
# ==========================
#   Ежедневное закрепление (daily_pinner)
# ==========================

class DailyPinningTask(models.Model):
    """
    Задача ежедневного закрепления постов в каналах.
    Автоматически закрепляет посты, если за день опубликовано меньше 5.
    """
    
    # управление
    is_active = models.BooleanField(default=True, verbose_name="Активность")
    
    # связи
    channel = models.ForeignKey(
        "MainEntity", 
        on_delete=models.CASCADE, 
        related_name="daily_pinning_tasks",
        verbose_name="Канал"
    )
    bot = models.ForeignKey(
        "telegram.BotSession", 
        on_delete=models.CASCADE, 
        related_name="daily_pinning_tasks",
        verbose_name="Бот"
    )
    
    # конфигурация
    post_link = models.CharField(max_length=512, verbose_name="Ссылка на пост для закрепления")
    start_time = models.TimeField(verbose_name="Время начала интервала")
    end_time = models.TimeField(verbose_name="Время окончания интервала")
    unpin_after_minutes = models.PositiveIntegerField(
        verbose_name="Откреплять через (минут)",
        help_text="Через сколько минут откреплять сообщение"
    )
    delete_notification_after_minutes = models.PositiveIntegerField(
        verbose_name="Удалять уведомление через (минут)", 
        help_text="Через сколько минут удалять системное уведомление о закреплении"
    )
    
    # статистика
    total_yesterday = models.PositiveIntegerField(default=0, verbose_name="Всего постов вчера")
    dummy_yesterday = models.PositiveIntegerField(default=0, verbose_name="Пустышек вчера")
    total_today = models.PositiveIntegerField(default=0, verbose_name="Всего постов сегодня")
    dummy_today = models.PositiveIntegerField(default=0, verbose_name="Пустышек сегодня")
    
    # состояние
    pinned_at = models.DateTimeField(blank=True, null=True, verbose_name="Когда закреплено")
    unpinned_at = models.DateTimeField(blank=True, null=True, verbose_name="Когда откреплено")
    notification_deleted_at = models.DateTimeField(
        blank=True, null=True, 
        verbose_name="Когда удалено уведомление"
    )
    pinned_message_id = models.BigIntegerField(
        blank=True, null=True, 
        verbose_name="ID закрепленного сообщения"
    )
    last_cycle_date = models.DateField(
        blank=True, null=True,
        verbose_name="Дата последнего цикла"
    )
    
    # метаданные
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Задача ежедневного закрепления"
        verbose_name_plural = "Задачи ежедневного закрепления"
        db_table = "daily_pinning_tasks"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=['is_active']),
            models.Index(fields=['channel']),
            models.Index(fields=['bot']),
            models.Index(fields=['last_cycle_date']),
        ]

    def __str__(self):
        return f"DailyPin#{self.id} → {getattr(self.channel, 'name', '—')}"

    @property
    def is_in_interval(self) -> bool:
        """Находится ли текущее время в рабочем интервале задачи"""
        from datetime import datetime
        now = datetime.now().time()
        return self.start_time <= now <= self.end_time

    @property
    def cycle_completed(self) -> bool:
        """Завершен ли текущий цикл (откреплено + удалено уведомление)"""
        return bool(self.unpinned_at and self.notification_deleted_at)

    @property
    def needs_daily_reset(self) -> bool:
        """Нужно ли сбросить дневные счетчики (новый день)"""
        from datetime import date
        return self.last_cycle_date != date.today()

    @property
    def should_pin_today(self) -> bool:
        """Нужно ли сегодня закреплять посты"""
        if not self.is_in_interval:
            return False
        return self.total_today < 5

    @property
    def status_display(self) -> str:
        """Текстовое представление статуса"""
        if self.pinned_at and not self.unpinned_at:
            return "Закреплено"
        elif self.unpinned_at and not self.notification_deleted_at:
            return "Откреплено (ждем удаления уведомления)"
        elif self.cycle_completed:
            return "Цикл завершен"
        else:
            return "Ожидание интервала"

    def reset_for_new_day(self):
        """Сброс состояния для нового дня"""
        from datetime import date
        
        self.total_yesterday = self.total_today
        self.dummy_yesterday = self.dummy_today
        self.total_today = 0
        self.dummy_today = 0
        self.last_cycle_date = date.today()
        
        # Сбрасываем состояние для нового дня
        self.pinned_at = None
        self.unpinned_at = None
        self.notification_deleted_at = None
        self.pinned_message_id = None
        
        self.save(update_fields=[
            'total_yesterday', 'dummy_yesterday', 'total_today', 'dummy_today',
            'last_cycle_date', 'pinned_at', 'unpinned_at', 'notification_deleted_at',
            'pinned_message_id'
        ])

    def reset_cycle_state(self):
        """Сброс состояния цикла для возможного нового закрепления"""
        self.pinned_at = None
        self.unpinned_at = None
        self.notification_deleted_at = None
        self.pinned_message_id = None
        self.save(update_fields=[
            'pinned_at', 'unpinned_at', 'notification_deleted_at', 'pinned_message_id'
        ])


