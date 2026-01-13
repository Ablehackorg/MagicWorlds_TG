from django.db import models
from django.core.files.storage import default_storage
import os


def currency_cover_path(instance, filename):
    """Путь для сохранения заставок валют"""
    ext = os.path.splitext(filename)[1]
    return f"currency/covers/{instance.id}{ext}"


class CurrencyGlobals(models.Model):
    """
    Глобальные настройки плагина "Курс валют".
    Должна быть только одна запись.
    """
    is_active = models.BooleanField(default=True, verbose_name="Активность плагина")
    
    # Время публикации
    publication_time = models.TimeField(
        default="08:00:00",
        verbose_name="Время публикации"
    )
    
    # Настройки закрепления
    pin_main_chat = models.IntegerField(
        default=0,
        choices=[(0, "Не закреплять"), (1, "1 сутки"), (2, "2 суток")],
        verbose_name="Закреп в основном чате"
    )
    pin_safe_exchange = models.IntegerField(
        default=0,
        choices=[(0, "Не закреплять"), (1, "1 сутки"), (2, "2 суток")],
        verbose_name="Закреп в безопасном обмене"
    )
    
    # Заставка
    cover = models.ImageField(
        upload_to=currency_cover_path,
        blank=True,
        null=True,
        verbose_name="Заставка (фото/видео)"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = "currency_globals"
        verbose_name = "Глобальные настройки валют"
        verbose_name_plural = "Глобальные настройки валют"
    
    def __str__(self):
        return "Глобальные настройки валют"
    
    def save(self, *args, **kwargs):
        # Удаляем старый файл при обновлении
        if self.pk:
            try:
                old_instance = CurrencyGlobals.objects.get(pk=self.pk)
                if old_instance.cover and old_instance.cover != self.cover:
                    if default_storage.exists(old_instance.cover.name):
                        default_storage.delete(old_instance.cover.name)
            except CurrencyGlobals.DoesNotExist:
                pass
        super().save(*args, **kwargs)


class CurrencyLocation(models.Model):
    """
    Локация для публикации курса валют (страна/регион).
    """
    # Управление
    is_active = models.BooleanField(default=True, verbose_name="Активность")
    
    # Основные данные
    name = models.CharField(max_length=100, verbose_name="Название (для админки)")
    hashtag = models.CharField(max_length=100, blank=True, verbose_name="Хэштеги")
    emoji = models.CharField(max_length=10, blank=True, verbose_name="Эмодзи страны")
    
    # Связи
    country = models.ForeignKey(
        "admin_panel.Country",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="currency_locations",
        verbose_name="Страна"
    )
    bot = models.ForeignKey(
        "telegram.BotSession",
        on_delete=models.CASCADE,
        related_name="currency_locations",
        verbose_name="Бот для публикации"
    )
    main_chat = models.ForeignKey(
        "MainEntity",
        on_delete=models.CASCADE,
        related_name="currency_main_locations",
        verbose_name="Основной чат (цель)"
    )
    safe_exchange = models.ForeignKey(
        "MainEntity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="currency_safe_locations",
        verbose_name="Чат 'Безопасный обмен'"
    )
    
    # Внешние ссылки
    google_rate_url = models.URLField(blank=True, null=True, verbose_name="Google Rate URL")
    xe_rate_url = models.URLField(blank=True, null=True, verbose_name="XE Rate URL")
    bank_1_url = models.URLField(blank=True, null=True, verbose_name="Банк-1 URL")
    bank_2_url = models.URLField(blank=True, null=True, verbose_name="Банк-2 URL")
    bank_3_url = models.URLField(blank=True, null=True, verbose_name="Банк-3 URL")
    atm_url = models.URLField(blank=True, null=True, verbose_name="ATM-банкоматы URL")
    magic_country_url = models.URLField(blank=True, null=True, verbose_name="Волшебная страна URL")
    
    # Статистика и состояние
    last_status = models.CharField(
        max_length=20,
        choices=[("success", "Успешно"), ("error", "Ошибка")],
        blank=True,
        null=True,
        verbose_name="Последний статус"
    )
    last_published = models.DateTimeField(blank=True, null=True, verbose_name="Последняя публикация")
    error_count = models.PositiveIntegerField(default=0, verbose_name="Количество ошибок")
    
    # Метаданные
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = "currency_location"
        verbose_name = "Локация валют"
        verbose_name_plural = "Локации валют"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=['is_active']),
            models.Index(fields=['bot']),
            models.Index(fields=['country']),
        ]
    
    def __str__(self):
        country_name = self.country.name if self.country else self.name
        return f"{self.emoji} {country_name}"


class CurrencyPair(models.Model):
    """
    Валютная пара для локации.
    """
    location = models.ForeignKey(
        CurrencyLocation,
        on_delete=models.CASCADE,
        related_name="pairs",
        verbose_name="Локация"
    )
    
    from_code = models.CharField(max_length=10, verbose_name="Из валюты (код)")
    to_code = models.CharField(max_length=10, verbose_name="В валюту (код)")
    
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    
    # Последние данные курса (для отображения тренда)
    last_rate = models.FloatField(blank=True, null=True, verbose_name="Последний курс")
    last_trend = models.CharField(
        max_length=10,
        blank=True,
        choices=[("⏹", "Без изменений"), ("🔼", "Рост"), ("🔽", "Падение")],
        verbose_name="Последний тренд"
    )
    
    order = models.IntegerField(default=0, verbose_name="Порядок сортировки")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = "currency_currencypair"
        verbose_name = "Валютная пара"
        verbose_name_plural = "Валютные пары"
        ordering = ["order", "id"]
        indexes = [
            models.Index(fields=['location', 'is_active']),
        ]
    
    def __str__(self):
        return f"{self.from_code}/{self.to_code} ({self.location.name})"


class CurrencyRateHistory(models.Model):
    """
    История курсов валют.
    """
    pair = models.ForeignKey(
        CurrencyPair,
        on_delete=models.CASCADE,
        related_name="history",
        verbose_name="Валютная пара"
    )
    
    rate = models.FloatField(verbose_name="Курс")
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата")
    
    class Meta:
        db_table = "currency_ratehistory"
        verbose_name = "История курса"
        verbose_name_plural = "История курсов"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=['pair', '-created_at']),
        ]
    
    def __str__(self):
        return f"{self.pair.from_code}/{self.pair.to_code}: {self.rate} ({self.created_at})"

