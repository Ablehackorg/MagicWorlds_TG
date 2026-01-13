from django.db import models

class ReactionBoostTask(models.Model):
    """Задача лайкера постов (reaction_booster)."""

    is_active = models.BooleanField(default=True, verbose_name="Активность")

    target = models.ForeignKey(
        "MainEntity",
        on_delete=models.CASCADE,
        related_name="reaction_boost_tasks",
        verbose_name="Канал-группа",
    )
    bot = models.ForeignKey(
        "telegram.BotSession",
        on_delete=models.CASCADE,
        related_name="reaction_boost_tasks",
        verbose_name="Бот",
    )

    # Конфигурация
    posts_count = models.PositiveIntegerField(
        default=10,
        verbose_name="Количество постов",
        help_text="Сколько постов обрабатывать за один запуск",
    )
    reactions_per_post = models.PositiveIntegerField(
        default=5,
        verbose_name="Реакций на пост",
    )
    reaction_type = models.CharField(
        max_length=20,
        choices=[
            ("positive", "Положительные"),
            ("neutral", "Нейтральные"),
            ("negative", "Отрицательные"),
        ],
        default="positive",
        verbose_name="Тип реакций",
    )
    frequency_days = models.PositiveIntegerField(
        default=1,
        verbose_name="Частота запуска (дни)",
        help_text="Как часто запускать задачу, в днях",
    )
    launch_time = models.TimeField(
        verbose_name="Время запуска",
        help_text="Локальное время для запуска задачи",
    )

    # Кнопка «Запустить только сейчас»
    run_once_now = models.BooleanField(
        default=False,
        verbose_name="Запустить только сейчас",
        help_text="Если включено — задача выполнится один раз вне расписания",
    )

    # Статистика
    last_launch = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Дата последнего запуска",
    )

    # Метаданные
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        db_table = "reaction_boost_tasks"
        verbose_name = "Задача лайкера постов"
        verbose_name_plural = "Задачи лайкера постов"
        ordering = ["-created_at"]

    def __str__(self):
        return f"ReactionBoost#{self.id} → {getattr(self.target, 'name', '—')}"

class ReactionRecord(models.Model):
    """Факт поставленной реакции."""

    task = models.ForeignKey(
        ReactionBoostTask,
        on_delete=models.CASCADE,
        related_name="records",
        verbose_name="Задача",
    )
    post_message_id = models.BigIntegerField(
        verbose_name="ID поста в Telegram",
    )
    bot_id = models.BigIntegerField(
        verbose_name="ID бота, поставившего реакцию",
    )
    reaction = models.CharField(
        max_length=50,
        verbose_name="Реакция (emoji)",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        db_table = "reaction_records"
        verbose_name = "Реакция"
        verbose_name_plural = "Реакции"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Reaction#{self.id} ({self.reaction})"
