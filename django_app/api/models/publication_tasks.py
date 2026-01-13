from django.db import models
from django.utils import timezone

class EntityPostTaskQuerySet(models.QuerySet):
    """Дополнительные фильтры для выборки задач публикации."""

    def groups(self):
        return self.filter(target__entity_type="group")

    def channels(self):
        return self.filter(target__entity_type="channel")

class ChannelTaskGroup(models.Model):
    """
    Простая модель для объединения подзадач EntityPostTask в одну группу.
    Не содержит логики задачи — только связывает подзадачи.
    """
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Group#{self.id} ({self.subtasks.count()} подзадач)"

class EntityPostTask(models.Model):
    """
    Задача публикации (в канал или группу).
    Связана с конкретным ботом и источником/целью.
    """

    # режим выбора постов
    CHOICES = [
        ("random", "Случайно"),
        ("sequential", "Последовательно"),
    ]
    # поведение после публикации
    AFTER_PUBLISH = [
        ("remove", "Удалять"),
        ("cycle", "Циклировать"),
    ]
    # тип библиотеки постов
    TASK_TYPES = [
        ("draft", "Draft"),
        ("ads", "Ads"),
        ("selfpromo", "SelfPromo"),
    ]

    bot = models.ForeignKey(
        "telegram.BotSession",
        on_delete=models.CASCADE,
        related_name="channel_tasks",
    )

    # источник (канал или группа, одно из двух полей используется)
    source = models.ForeignKey(
        "MainEntity",
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name="source_tasks",
    )

    # цель (канал или группа, одно из двух полей используется)
    target = models.ForeignKey(
        "MainEntity",
        null=True, blank=True,
        on_delete=models.CASCADE,
        related_name="target_tasks",
    )

    is_active = models.BooleanField(default=True)
    is_global_active = models.BooleanField(default=True)
    choice_mode = models.CharField(max_length=20, choices=CHOICES, default="random")
    after_publish = models.CharField(max_length=20, choices=AFTER_PUBLISH, default="cycle")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = EntityPostTaskQuerySet.as_manager()

    group = models.ForeignKey(
        ChannelTaskGroup,
        on_delete=models.CASCADE,
        related_name="subtasks",
        null=True,
        blank=True,
    )

    def __str__(self):
        t = getattr(self.target, "type", "unknown")
        return f"Task#{self.id}: {self.source} → {self.target} ({t})"

class TaskTime(models.Model):
    """
    Тайминг задачи публикации.
    Содержит день недели и секунды от начала суток.
    """

    task = models.ForeignKey(EntityPostTask, on_delete=models.CASCADE, related_name="times")
    weekday = models.PositiveSmallIntegerField()           # 0 = Пн, 6 = Вс
    seconds_from_day_start = models.PositiveIntegerField() # 0..86399
