# admin_panel/models.py

from django.db import models


class Plugin(models.Model):
    """
    Модель «Плагин» — описание внешнего сервиса/контейнера.
    Используется для отображения в UI и управления через Docker.
    """

    CATEGORY_CHOICES = [
        ("group", "Публикатор в группу"),
        ("channel", "Публикатор в канал"),
    ]

    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)  # ссылка вида /plugins/<slug>
    description = models.TextField(blank=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    container_name = models.CharField(max_length=100)  # имя контейнера в Docker
    is_active = models.BooleanField(default=False)

    def __str__(self):
        return self.name

class Country(models.Model):
    """
    Справочник стран.
    """
    name = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    time_zone_delta = models.FloatField(
        default=0.0,
        help_text="Смещение по времени относительно МСК (может быть отрицательным и дробным)"
    )


    class Meta:
        verbose_name = "Страна"
        verbose_name_plural = "Страны"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Category(models.Model):
    """
    Справочник категорий.
    """
    name = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Категория"
        verbose_name_plural = "Категории"
        ordering = ["name"]

    def __str__(self):
        return self.name

