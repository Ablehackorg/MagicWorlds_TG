from django.db import models

class SubscribersBoostTask(models.Model):
    """Задача нормализатора подписчиков (subscribers_booster)."""
    is_active = models.BooleanField(default=True, verbose_name="Активность")

    target = models.ForeignKey(
        "MainEntity",
        on_delete=models.CASCADE,
        related_name="subscribers_boost_tasks",
        verbose_name="Канал-группа"
    )
    bot = models.ForeignKey(
        "telegram.BotSession",
        on_delete=models.CASCADE,
        related_name="subscribers_boost_tasks",
        verbose_name="Бот"
    )
    settings = models.ForeignKey(
        "BoosterSettings",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="subscribers_boost_tasks",
        verbose_name="Настройки бустера"
    )

    # Конфигурация
    check_interval = models.PositiveIntegerField(
        default=60,
        verbose_name="Частота проверки (минуты)"
    )
    max_subscribers = models.PositiveIntegerField(
        default=0,
        verbose_name="Максимальное количество подписчиков"
    )
    notify_on_exceed = models.BooleanField(
        default=False,
        verbose_name="Оповещать при превышении лимита"
    )

    # Доп. поле (только для Django)
    tracking_mode = models.CharField(
        max_length=10,
        choices=[("unsubs", "Отписки"), ("total", "Всего")],
        default="unsubs",
        verbose_name="Учёт"
    )

    last_processed_event_id = models.BigIntegerField(blank=True, null=True, verbose_name="ID последнего обработанного системного сообщения")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        db_table = "subscribers_boost_tasks"
        verbose_name = "Задача нормализатора подписчиков"
        verbose_name_plural = "Задачи нормализатора подписчиков"
        ordering = ["-created_at"]

    def __str__(self):
        return f"SubscribersBoost#{self.id} → {getattr(self.target, 'name', '—')}"

class SubscribersBoostExpense(models.Model):
    """Расходы по накрутке подписчиков."""
    task = models.ForeignKey(
        SubscribersBoostTask,
        on_delete=models.CASCADE,
        related_name="expenses",
        verbose_name="Задача"
    )
    subscribers_count = models.PositiveIntegerField(verbose_name="Количество подписчиков")
    service_id = models.PositiveIntegerField(verbose_name="Айди тарифа", default=0)
    price = models.FloatField(verbose_name="Стоимость")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "subscribers_boost_expenses"
        verbose_name = "Расход подписчиков"
        verbose_name_plural = "Расходы подписчиков"
        ordering = ["-created_at"]

    def __str__(self):
        return f"SubsExpense#{self.id} ({self.subscribers_count} subs, {self.price:.2f}₽)"

class SubscribersCheck(models.Model):
    """Результат проверки количества подписчиков."""
    task = models.ForeignKey(
        SubscribersBoostTask,
        on_delete=models.CASCADE,
        related_name="checks",
        verbose_name="Задача"
    )
    total_subscribers = models.PositiveIntegerField(verbose_name="Всего подписчиков")
    new_subscriptions = models.PositiveIntegerField(default=0, verbose_name="Новые подписки")
    new_unsubscriptions = models.PositiveIntegerField(default=0, verbose_name="Отписки")
    unsubscribed_users = models.JSONField(default=list, verbose_name="Список ID отписавшихся")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "subscribers_checks"
        verbose_name = "Проверка подписчиков"
        verbose_name_plural = "Проверки подписчиков"
        ordering = ["-created_at"]

    def __str__(self):
        return f"SubsCheck#{self.id} ({self.total_subscribers} total)"

class SubscriberList(models.Model):
    """Список подписчиков, сохранённый при проверке."""
    task = models.ForeignKey(
        SubscribersBoostTask,
        on_delete=models.CASCADE,
        related_name="subscriber_lists",
        verbose_name="Задача"
    )
    subscriber_ids = models.JSONField(verbose_name="Список ID подписчиков")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "subscriber_lists"
        verbose_name = "Список подписчиков"
        verbose_name_plural = "Списки подписчиков"
        ordering = ["-created_at"]

    def __str__(self):
        return f"SubsList#{self.id} ({len(self.subscriber_ids)} ids)"
