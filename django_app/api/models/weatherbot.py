from __future__ import annotations

from django.db import models

from admin_panel.models import Country
from .entities import MainEntity


class WeatherCity(models.Model):
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name="weather_cities")
    name = models.CharField(max_length=255)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    class Meta:
        unique_together = ("country", "name")
        ordering = ["country__name", "name"]

    def __str__(self) -> str:
        return f"{self.country.name} — {self.name}"


class WeatherTask(models.Model):
    city = models.ForeignKey(WeatherCity, on_delete=models.PROTECT, related_name="tasks")

    is_enabled = models.BooleanField(default=True)

    morning_time = models.TimeField(default="10:30:00")
    evening_time = models.TimeField(default="20:30:00")

    summer_only_backgrounds = models.BooleanField(default=False)

    # Вариант B: дефолтная коллекция фонов/иконок лежит в проекте (static/weatherbot/..)
    # Если выключить — используем загруженные в задаче GIF.
    use_default_backgrounds = models.BooleanField(default=True)
    use_default_icons = models.BooleanField(default=True)

    gif_sunny = models.FileField(upload_to="weatherbot/gifs/", null=True, blank=True)
    gif_cloudy = models.FileField(upload_to="weatherbot/gifs/", null=True, blank=True)
    gif_precip = models.FileField(upload_to="weatherbot/gifs/", null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    last_morning_sent_at = models.DateTimeField(null=True, blank=True)
    last_evening_sent_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"WeatherTask#{self.id} {self.city}"


class WeatherTaskTarget(models.Model):
    task = models.ForeignKey(WeatherTask, on_delete=models.CASCADE, related_name="targets")
    entity = models.ForeignKey(MainEntity, on_delete=models.CASCADE, related_name="weather_targets")

    class Meta:
        unique_together = ("task", "entity")


class WeatherPublishLog(models.Model):
    POST_KIND = [
        ("morning", "Утренний"),
        ("evening", "Вечерний"),
        ("manual", "Ручной"),
    ]

    task = models.ForeignKey(WeatherTask, on_delete=models.CASCADE, related_name="logs")
    target = models.ForeignKey(MainEntity, on_delete=models.SET_NULL, null=True, blank=True)
    kind = models.CharField(max_length=16, choices=POST_KIND)
    is_ok = models.BooleanField(default=True)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
