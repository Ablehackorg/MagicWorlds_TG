from django.db import models

class ViewBoostTask(models.Model):
    """
    Задача умного просмотра новых постов.
    Отслеживает новые посты в канале и управляет просмотрами по расписанию.
    """
    
    # управление
    is_active = models.BooleanField(default=True, verbose_name="Активность")
    
    # связи
    target = models.ForeignKey(
        "MainEntity", 
        on_delete=models.CASCADE, 
        related_name="view_boost_tasks",
        verbose_name="Канал-группа"
    )
    bot = models.ForeignKey(
        "telegram.BotSession", 
        on_delete=models.CASCADE, 
        related_name="view_boost_tasks",
        verbose_name="Бот"
    )

    settings = models.ForeignKey(
        "BoosterSettings",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="view_boost_tasks",
        verbose_name="Настройки бустера"
    )
    
    # конфигурация
    view_coefficient = models.PositiveIntegerField(
        default=50,
        verbose_name="Коэффициент просмотров",
        help_text="Процент от подписчиков, который должен быть достигнут за 24 часа (0-100%)"
    )
    normalization_mode = models.CharField(
        max_length=20,
        choices=[
            ("daily", "Суточный режим"),
            ("morning", "Утренний режим"), 
            ("day", "Дневной режим"),
            ("evening", "Вечерний режим"),
            ("night", "Ночной режим"),
        ],
        default="daily",
        verbose_name="Режим нормализации"
    )
    show_expenses_for = models.CharField(
        max_length=10,
        choices=[
            ("month", "Месяц"),
            ("week", "Неделя"),
        ],
        default="month",
        verbose_name="Показывать расход за"
    )
    
    # статистика
    subscribers_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Количество подписчиков"
    )
    day_before_yesterday_percent = models.FloatField(
        default=0.0,
        verbose_name="Позавчера (%)"
    )
    yesterday_percent = models.FloatField(
        default=0.0, 
        verbose_name="Вчера (%)"
    )
    today_percent = models.FloatField(
        default=0.0,
        verbose_name="Сегодня (%)"
    )
    
    # метаданные
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Задача умного просмотра"
        verbose_name_plural = "Задачи умного просмотра"
        db_table = "view_boost_tasks"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=['is_active']),
            models.Index(fields=['target']),
            models.Index(fields=['bot']),
            models.Index(fields=['normalization_mode']),
        ]

    def __str__(self):
        return f"ViewBoost#{self.id} → {getattr(self.target, 'name', '—')}"

    @property
    def api_key(self):
        """API ключ из связанных настроек"""
        return self.settings.api_key if self.settings else ""

    @property
    def service_id(self):
        """ID сервиса из связанных настроек"""
        return self.settings.new_views_service_id if self.settings else 0

    @property
    def total_views_needed(self) -> int:
        """Общее количество необходимых просмотров за 24 часа"""
        return int((self.view_coefficient / 100) * self.subscribers_count)

    @property
    def current_total_percent(self) -> float:
        """Общий накопленный процент просмотров"""
        return self.day_before_yesterday_percent + self.yesterday_percent + self.today_percent

    @property
    def monthly_expense(self) -> float:
        """Расход за месяц"""
        from django.utils import timezone
        from datetime import timedelta
        
        month_ago = timezone.now() - timedelta(days=30)
        expenses = ViewBoostExpense.objects.filter(
            task=self,
            created_at__gte=month_ago
        ).aggregate(total=models.Sum('price'))
        return expenses['total'] or 0.0
    
    @property
    def weekly_expense(self) -> float:
        """Расход за неделю"""
        from django.utils import timezone
        from datetime import timedelta
        
        week_ago = timezone.now() - timedelta(days=7)
        expenses = ViewBoostExpense.objects.filter(
            task=self,
            created_at__gte=week_ago
        ).aggregate(total=models.Sum('price'))
        return expenses['total'] or 0.0

    def reset_daily_percentages(self):
        """Сброс дневных процентов при новом дне"""
        self.day_before_yesterday_percent = self.yesterday_percent
        self.yesterday_percent = self.today_percent
        self.today_percent = 0.0
        self.save(update_fields=[
            'day_before_yesterday_percent', 
            'yesterday_percent', 
            'today_percent'
        ])

    def add_hourly_percent(self, hour_percent: float):
        """Добавление процента за текущий час"""
        self.today_percent += hour_percent
        self.save(update_fields=['today_percent'])

class ViewBoostExpense(models.Model):
    """
    Расходы на API для хранения статистики просмотров.
    Фиксируется каждый запрос к API с количеством просмотров и ценой.
    """
    
    task = models.ForeignKey(
        ViewBoostTask,
        on_delete=models.CASCADE,
        related_name="expenses",
        verbose_name="Задача"
    )
    
    # данные запроса
    views_count = models.PositiveIntegerField(verbose_name="Количество просмотров")
    price = models.FloatField(verbose_name="Цена за хранение")
    service_id = models.PositiveIntegerField(verbose_name="Айди тарифа", default=0)
    
    # метаданные
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        verbose_name = "Расход умного просмотра"
        verbose_name_plural = "Расходы умного просмотра"
        db_table = "view_boost_expenses"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=['task']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"Expense#{self.id} → {self.views_count} views (${self.price:.2f})"

class ViewDistribution(models.Model):
    """
    Распределение просмотров по часам для задачи умного просмотра (24 часа, 4 режима).
    """
    morning_distribution = models.JSONField(default=dict, verbose_name="Распределение утренних постов")
    day_distribution = models.JSONField(default=dict, verbose_name="Распределение дневных постов")
    evening_distribution = models.JSONField(default=dict, verbose_name="Распределение вечерних постов")
    night_distribution = models.JSONField(default=dict, verbose_name="Распределение ночных постов")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Распределение просмотров"
        verbose_name_plural = "Распределения просмотров"
        db_table = "view_boost_distributions"

    def __str__(self):
        return "View Distribution Settings"

class ActivePostTracking(models.Model):
    """
    Активное отслеживание поста в процессе накрутки просмотров.
    Каждый новый пост отслеживается 2 дня.
    """
    
    task = models.ForeignKey(
        ViewBoostTask,
        on_delete=models.CASCADE,
        related_name="active_posts",
        verbose_name="Задача"
    )
    
    # данные поста
    message_id = models.BigIntegerField(verbose_name="ID сообщения")
    post_type = models.CharField(
        max_length=10,
        choices=[
            ("morning", "Утренний"),
            ("main", "Основной"),
        ],
        verbose_name="Тип поста"
    )
    total_views_needed = models.PositiveIntegerField(verbose_name="Всего просмотров нужно")
    publish_time = models.DateTimeField(verbose_name="Время публикации")
    
    # состояние обработки
    completed_hours = models.JSONField(
        default=list,
        verbose_name="Обработанные часы",
        help_text="Список обработанных часов в формате [('day1', 1), ('day2', 7), ...]"
    )
    is_completed = models.BooleanField(default=False, verbose_name="Завершено")
    
    # метаданные
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="Завершено в")

    class Meta:
        verbose_name = "Активное отслеживание поста"
        verbose_name_plural = "Активные отслеживания постов"
        db_table = "view_boost_active_posts"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=['task']),
            models.Index(fields=['message_id']),
            models.Index(fields=['is_completed']),
            models.Index(fields=['publish_time']),
        ]
        unique_together = ['task', 'message_id']

    def __str__(self):
        return f"TrackedPost#{self.id} → msg{self.message_id}"

    @property
    def hours_since_publish(self) -> int:
        """Количество часов с момента публикации"""
        from django.utils import timezone
        delta = timezone.now() - self.publish_time
        return int(delta.total_seconds() // 3600)

    @property
    def current_hour_key(self) -> tuple:
        """Текущий час для обработки в формате (день, час)"""
        hours = self.hours_since_publish
        
        if hours < 24:
            return ("day1", hours + 1)
        else:
            from datetime import datetime
            current_hour = datetime.now().hour
            return ("day2", current_hour)

    def is_hour_processed(self, hour_key: tuple) -> bool:
        """Проверяет, обработан ли уже этот час"""
        return hour_key in self.completed_hours

    def mark_hour_processed(self, hour_key: tuple):
        """Помечает час как обработанный"""
        if hour_key not in self.completed_hours:
            self.completed_hours.append(hour_key)
            self.save(update_fields=['completed_hours'])

    def should_stop_tracking(self) -> bool:
        """Проверяет, нужно ли прекратить отслеживание (прошло 2 дня)"""
        from django.utils import timezone
        return timezone.now() > self.publish_time + timezone.timedelta(days=2)
