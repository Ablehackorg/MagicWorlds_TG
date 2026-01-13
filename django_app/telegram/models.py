# telegram/models.py
from django.db import models
from django.utils import timezone


class BotSession(models.Model):
    api_id = models.IntegerField()
    first_name = models.CharField(max_length=32, default="")
    last_name = models.CharField(max_length=32, blank=True, default="")
    api_hash = models.CharField(max_length=255)
    phone = models.CharField(max_length=32, unique=True)
    session_string = models.CharField(max_length=455)
    description = models.TextField(blank=True, null=True)
    username = models.CharField(
        max_length=100, blank=True, null=True, verbose_name="Username")

    # Новые поля
    bio = models.TextField(blank=True, null=True,
                           verbose_name="Описание в профиле")
    birthday = models.DateField(
        null=True, blank=True, verbose_name="День рождения")
    avatar = models.ImageField(
        upload_to='avatars/bots/',
        blank=True,
        null=True,
        verbose_name="Аватарка",
        max_length=500
    )

    is_active = models.BooleanField(default=True)
    is_banned = models.BooleanField(default=False)

    # Поле для хранения дополнительной информации из Telegram в формате JSON
    telegram_info = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"BotSession {self.phone}"

    class Meta:
        ordering = ['-created_at']

    groups = models.ManyToManyField(
        'api.MainEntity', through='BotAdminGroup', related_name='bots_as_admin')

    @property
    def gender(self):
        return getattr(self.profile, 'gender', 'male') if hasattr(self, 'profile') else 'male'

    @property
    def current_status(self):
        return getattr(self.profile, 'current_status', None) if hasattr(self, 'profile') else None

    @property
    def country(self):
        return getattr(self.profile, 'country', None) if hasattr(self, 'profile') else None

    @property
    def owner_type(self):
        return getattr(self.profile, 'owner_type', 'none') if hasattr(self, 'profile') else 'none'

    @property
    def telegram_status(self):
        return getattr(self.profile, 'telegram_status', 'regular') if hasattr(self, 'profile') else 'regular'

    @property
    def notes(self):
        return getattr(self.profile, 'notes', None) if hasattr(self, 'profile') else None

    @property
    def plugins_count(self):
        return self.plugins.filter(is_active=True).count() if hasattr(self, 'plugins') else 0

    @property
    def groups_count(self):
        return self.admin_groups.filter(is_active=True).count() if hasattr(self, 'admin_groups') else 0

    @property
    def subscriber_groups_total(self):
        return self.subscriber_groups.count() if hasattr(self, 'subscriber_groups') else 0

    @property
    def name(self):
        """Совместимость со старым кодом"""
        return f"{self.first_name} {self.last_name}".strip()

    @name.setter
    def name(self, value):
        """Разделение имени и фамилии при установке"""
        parts = str(value).strip().split(' ', 1)
        self.first_name = parts[0]
        self.last_name = parts[1] if len(parts) > 1 else ""

    def get_telegram_info_display(self):
        """Возвращает форматированную информацию из telegram_info"""
        if not self.telegram_info:
            return "Не синхронизировано"

        info = []
        if self.telegram_info.get('username'):
            info.append(f"@{self.telegram_info['username']}")
        if self.telegram_info.get('dialogs_count'):
            info.append(f"Диалогов: {self.telegram_info['dialogs_count']}")
        if self.telegram_info.get('last_seen'):
            info.append(f"Был(а): {self.telegram_info['last_seen']}")

        return ", ".join(info) if info else "Есть информация"

    def get_status_badge(self):
        """Возвращает HTML бейдж статуса"""
        if self.is_banned:
            return '<span class="badge badge-danger">Забанен</span>'
        elif not self.is_active:
            return '<span class="badge badge-warning">Неактивен</span>'
        elif self.last_sync_at:
            return '<span class="badge badge-success">Активен</span>'
        else:
            return '<span class="badge badge-secondary">Не проверен</span>'

    @property
    def needs_sync(self):
        """Проверяет, нужна ли синхронизация (прошло больше часа)"""
        if not self.last_sync_at:
            return True
        return (timezone.now() - self.last_sync_at).total_seconds() > 3600


class BotProfile(models.Model):
    """Расширенный профиль бота."""
    bot = models.OneToOneField(
        BotSession, on_delete=models.CASCADE, related_name='profile')

    # Пометки
    GENDER_CHOICES = [
        ('male', 'Мужской'),
        ('female', 'Женский'),
    ]

    STATUS_CHOICES = [
        ('pro', 'Профи'),
        ('experienced', 'Опытный'),
        ('tourist', 'Турист'),
        ('advertiser', 'Рекламодатель'),
    ]

    OWNER_CHOICES = [
        ('own', 'Свой'),
        ('foreign', 'Чужой'),
        ('own_bot', 'Свой бот'),
        ('none', 'Не выбрано'),
    ]

    TELEGRAM_STATUS_CHOICES = [
        ('regular', 'Обычный'),
        ('premium', 'Премиум'),
    ]

    gender = models.CharField(
        max_length=10, choices=GENDER_CHOICES, default='male')
    current_status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, blank=True, null=True)
    country = models.CharField(max_length=100, blank=True, null=True)
    owner_type = models.CharField(
        max_length=20, choices=OWNER_CHOICES, default='none')
    telegram_status = models.CharField(
        max_length=10, choices=TELEGRAM_STATUS_CHOICES, default='regular')
    notes = models.TextField(blank=True, null=True,
                             verbose_name="Пометки об аккаунте")

    # Метрики
    admin_groups_last_updated = models.DateTimeField(null=True, blank=True)
    subscriber_groups_last_updated = models.DateTimeField(
        null=True, blank=True)
    warnings_last_updated = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Профиль бота"
        verbose_name_plural = "Профили ботов"

    def __str__(self):
        return f"Профиль бота {self.bot.phone}"


class BotNameHistory(models.Model):
    """История изменений имени бота."""
    bot = models.ForeignKey(
        BotSession, on_delete=models.CASCADE, related_name='name_history')
    first_name = models.CharField(max_length=32)
    last_name = models.CharField(max_length=32, blank=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    duration_days = models.IntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "История имени"
        verbose_name_plural = "История имен"
        ordering = ['-start_date']

    def save(self, *args, **kwargs):
        if self.start_date and self.end_date:
            delta = self.end_date - self.start_date
            self.duration_days = delta.days
        elif self.start_date:
            from datetime import date
            delta = date.today() - self.start_date
            self.duration_days = delta.days
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.start_date})"


class BotUsernameHistory(models.Model):
    """История изменений юзернейма бота."""
    bot = models.ForeignKey(
        BotSession, on_delete=models.CASCADE, related_name='username_history')
    username = models.CharField(max_length=100)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    duration_days = models.IntegerField(default=0)
    progress = models.IntegerField(
        default=0, help_text="Прогресс скрипта (0-100%)")
    last_scrape_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "История юзернейма"
        verbose_name_plural = "История юзернеймов"
        ordering = ['-start_date']

    def save(self, *args, **kwargs):
        if self.start_date and self.end_date:
            delta = self.end_date - self.start_date
            self.duration_days = delta.days
        elif self.start_date:
            from datetime import date
            delta = date.today() - self.start_date
            self.duration_days = delta.days
        super().save(*args, **kwargs)

    def __str__(self):
        return f"@{self.username} ({self.start_date})"


class BotPlugin(models.Model):
    """Плагины, доступные боту."""
    bot = models.ForeignKey(
        BotSession, on_delete=models.CASCADE, related_name='plugins')
    plugin = models.ForeignKey('admin_panel.Plugin', on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Плагин бота"
        verbose_name_plural = "Плагины ботов"
        unique_together = ['bot', 'plugin']

    def __str__(self):
        return f"{self.bot.phone} - {self.plugin.name}"


class BotAdminGroup(models.Model):
    """Группы, в которых бот является администратором."""
    bot = models.ForeignKey(
        BotSession, on_delete=models.CASCADE, related_name='admin_groups')
    group = models.ForeignKey(
        'api.MainEntity', on_delete=models.CASCADE, related_name='bot_admins')
    is_active = models.BooleanField(default=True)
    last_checked = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Группа администратора"
        verbose_name_plural = "Группы администраторов"
        unique_together = ['bot', 'group']

    def __str__(self):
        return f"{self.bot.phone} в {self.group.name}"


class BotSubscriberGroup(models.Model):
    """Группы, в которых бот является подписчиком."""
    bot = models.ForeignKey(
        BotSession, on_delete=models.CASCADE, related_name='subscriber_groups')
    group = models.ForeignKey(
        'api.MainEntity', on_delete=models.CASCADE, related_name='bot_subscribers')
    is_our = models.BooleanField(
        default=False, help_text="Является ли группой из MainEntity")
    last_checked = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Группа подписчика"
        verbose_name_plural = "Группы подписчиков"
        unique_together = ['bot', 'group']

    def __str__(self):
        return f"{self.bot.phone} подписан на {self.group.name}"


class BotWarning(models.Model):
    """Предупреждения для бота."""
    bot = models.ForeignKey(
        BotSession, on_delete=models.CASCADE, related_name='warnings')
    date = models.DateField()
    channel_name = models.CharField(max_length=255)
    reason = models.TextField(help_text="В чём обвиняется")
    severity = models.CharField(max_length=20, choices=[
        ('low', 'Низкая'),
        ('medium', 'Средняя'),
        ('high', 'Высокая'),
    ], default='medium')
    resolved = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Предупреждение"
        verbose_name_plural = "Предупреждения"
        ordering = ['-date']

    def __str__(self):
        return f"Предупреждение для {self.bot.phone} ({self.date})"
