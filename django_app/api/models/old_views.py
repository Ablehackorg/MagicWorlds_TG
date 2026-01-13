from django.db import models

class OldViewsTask(models.Model):
    """Задача старых просмотров (old_views_booster)."""

    is_active = models.BooleanField(default=True, verbose_name="Активность")

    target = models.ForeignKey(
        "MainEntity",
        on_delete=models.CASCADE,
        related_name="old_views_tasks",
        verbose_name="Канал-группа"
    )
    bot = models.ForeignKey(
        "telegram.BotSession",
        on_delete=models.CASCADE,
        related_name="old_views_tasks",
        verbose_name="Бот"
    )
    settings = models.ForeignKey(
        "BoosterSettings",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="old_views_tasks",
        verbose_name="Настройки бустера"
    )

    # Конфигурация (новые поля)
    normalization_mode = models.CharField(
        max_length=20,
        choices=[
            ("monthly", "1 раз в месяц"),
            ("bi_monthly", "2 раза в месяц"),
            ("weekly", "1 раз в неделю"),
            ("bi_weekly", "2 раза в неделю"),
            ("daily", "Ежедневно"),
        ],
        default="monthly",
        verbose_name="Период нормализации"
    )
    normalization_time = models.TimeField(
        default="00:00",
        verbose_name="Время запуска"
    )
    run_once = models.BooleanField(
        default=False,
        verbose_name="Запустить разово"
    )
    exclude_period = models.CharField(
        max_length=20,
        choices=[
            ("1_day", "1 сут"),
            ("2_days", "2 сут"),
            ("1_week", "1 неделя"),
            ("2_weeks", "2 недели"),
            ("none", "Без исключений"),
        ],
        default="none",
        verbose_name="Исключая последние"
    )
    
    posts_normalization = models.CharField(
        max_length=20,
        choices=[
            ("last_100", "Последние 100"),
            ("last_200", "Последние 200"),
            ("last_300", "Последние 300"),
            ("first_100", "Первые 100"),
            ("first_200", "Первые 200"),
            ("first_300", "Первые 300"),
        ],
        default="last_100",
        verbose_name="Нормализовать посты"
    )
    view_coefficient = models.PositiveIntegerField(default=50, verbose_name="Охват ERR-24")
    views_multiplier = models.PositiveIntegerField(default=1, verbose_name="Кратность оценки")

    # Статистика
    subscribers_count = models.PositiveIntegerField(default=0, verbose_name="Всего подписчиков")
    last_successful_run = models.DateTimeField(null=True, blank=True, verbose_name="Дата последнего запуска")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        db_table = "old_views_tasks"
        verbose_name = "Задача старых просмотров"
        verbose_name_plural = "Задачи старых просмотров"
        ordering = ["-created_at"]

    def __str__(self):
        return f"OldViews#{self.id} → {getattr(self.target, 'name', '—')}"

    @property
    def monthly_expense(self):
        from django.utils import timezone
        from datetime import timedelta
        month_ago = timezone.now() - timedelta(days=30)
        return self.expenses.filter(created_at__gte=month_ago).aggregate(total=models.Sum('price'))['total'] or 0

class OldViewsExpense(models.Model):
    """Расходы для задач старых просмотров."""

    task = models.ForeignKey(
        OldViewsTask,
        on_delete=models.CASCADE,
        related_name="expenses",
        verbose_name="Задача"
    )
    post_message_id = models.BigIntegerField(verbose_name="ID поста")
    service_id = models.PositiveIntegerField(verbose_name="Айди тарифа", default=0)
    views_count = models.PositiveIntegerField(verbose_name="Количество просмотров")
    price = models.FloatField(verbose_name="Стоимость")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "old_views_expenses"
        verbose_name = "Расход старых просмотров"
        verbose_name_plural = "Расходы старых просмотров"
        ordering = ["-created_at"]

    def __str__(self):
        return f"OldViewsExpense#{self.id} ({self.views_count} views)"