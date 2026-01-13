from django.db import models

class GroupTheme(models.Model):
    """Темы для блондинки."""
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Связь с Category
    category = models.OneToOneField(
        "admin_panel.Category",
        on_delete=models.CASCADE,
        related_name="theme",
        null=True,
        blank=True
    )

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'blondinka_group_themes'
        verbose_name = 'Тема группы'
        verbose_name_plural = 'Темы групп'

class BlondinkaDialog(models.Model):
    """Диалоги (сообщения) для тем бота-блондинки"""
    
    theme = models.ForeignKey(
        GroupTheme,
        on_delete=models.CASCADE,
        related_name="dialogs",
        verbose_name="Тема"
    )
    
    message = models.TextField(verbose_name="Текст сообщения")
    is_active = models.BooleanField(default=True, verbose_name="Активно")
    order = models.PositiveIntegerField(default=0, verbose_name="Порядок")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "blondinka_dialogs"
        verbose_name = "Диалог бота"
        verbose_name_plural = "Диалоги бота"
        ordering = ["theme", "order"]

    def __str__(self):
        return f"{self.theme.name} - {self.message[:50]}..."

class BlondinkaSchedule(models.Model):
    """Расписание публикаций для бота-блондинки"""
    
    task = models.ForeignKey(
        "BlondinkaTask",
        on_delete=models.CASCADE,
        related_name="schedules",
        verbose_name="Задача"
    )
    
    day_of_week = models.PositiveSmallIntegerField(
        choices=[
            (0, "Понедельник"),
            (1, "Вторник"),
            (2, "Среда"),
            (3, "Четверг"),
            (4, "Пятница"),
            (5, "Суббота"),
            (6, "Воскресенье"),
        ],
        verbose_name="День недели"
    )
    
    publish_time = models.TimeField(verbose_name="Время публикации")
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "blondinka_schedules"
        verbose_name = "Расписание публикации"
        verbose_name_plural = "Расписания публикаций"
        ordering = ["day_of_week", "publish_time"]
        unique_together = ['task', 'day_of_week', 'publish_time']

    def __str__(self):
        return f"{self.get_day_of_week_display()} {self.publish_time}"

class BlondinkaTask(models.Model):
    """Задача для бота-блондинки"""
    
    DELETE_POST_CHOICES = [
        (24, "Сутки"),
        (48, "2 суток"),
        (72, "3 суток"),
        (None, "Не удалять"),
    ]
    
    is_active = models.BooleanField(default=True, verbose_name="Активность")
    run_now = models.BooleanField(default=False, verbose_name="Запустить сейчас")
    
    # связи
    bot = models.ForeignKey(
        "telegram.BotSession",
        on_delete=models.CASCADE,
        related_name="blondinka_tasks",
        verbose_name="Бот"
    )
    group = models.ForeignKey(
        "MainEntity",
        on_delete=models.CASCADE,
        related_name="blondinka_tasks",
        verbose_name="Группа"
    )
    group_theme = models.ForeignKey(
        GroupTheme,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
        verbose_name="Тема группы"
    )
    
    # конфигурация
    owner_type = models.CharField(
        max_length=20,
        choices=[
            ("own", "Своя"),
            ("foreign", "Чужая"),
        ],
        default="own",
        verbose_name="Чья группа"
    )
    
    delete_post_after = models.IntegerField(
        choices=DELETE_POST_CHOICES,
        null=True,
        blank=True,
        verbose_name="Удалять пост через"
    )
    
    # дни работы (храним как JSON список номеров дней 0-6, где 0-понедельник)
    working_days = models.JSONField(
        default=list,
        verbose_name="Дни работы"
    )
    
    # метаданные
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        db_table = "blondinka_tasks"
        verbose_name = "Задача бота-блондинки"
        verbose_name_plural = "Задачи бота-блондинки"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Blondinka#{self.id} → {getattr(self.group, 'name', '—')}"

    @property
    def working_days_display(self):
        """Отображение дней работы в виде сокращений"""
        day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        return ", ".join([day_names[day] for day in sorted(self.working_days)])

class BlondinkaTaskDialog(models.Model):
    """Связь задачи с диалогами темы и их активностью в рамках задачи"""
    
    task = models.ForeignKey(
        BlondinkaTask,
        on_delete=models.CASCADE,
        related_name="task_dialogs"
    )
    dialog = models.ForeignKey(
        BlondinkaDialog,
        on_delete=models.CASCADE,
        related_name="task_dialogs"
    )
    is_active = models.BooleanField(default=True, verbose_name="Активен в задаче")
    order = models.PositiveIntegerField(default=0, verbose_name="Порядок")
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = "blondinka_task_dialogs"
        unique_together = ['task', 'dialog']
        ordering = ['order']

    def __str__(self):
        return f"Task#{self.task.id} - Dialog#{self.dialog.id}"

class BlondinkaLog(models.Model):
    """Лог деятельности бота-блондинки."""
    
    task = models.ForeignKey(
        BlondinkaTask,
        on_delete=models.CASCADE,
        related_name="logs",
        verbose_name="Задача"
    )
    
    # данные поста
    post_content = models.TextField(verbose_name="Содержание поста")
    post_url = models.CharField(max_length=512, blank=True, null=True, verbose_name="Ссылка на пост")
    
    # результат
    is_success = models.BooleanField(default=True, verbose_name="Успешно")
    error_message = models.TextField(blank=True, null=True, verbose_name="Ошибка")
    
    # метаданные
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        db_table = "blondinka_logs"
        verbose_name = "Лог бота-блондинки"
        verbose_name_plural = "Логи бота-блондинки"
        ordering = ["-created_at"]

    def __str__(self):
        return f"BlondinkaLog#{self.id} → Task#{self.task_id}"