from __future__ import annotations

from django.core.management.base import BaseCommand

from admin_panel.models import Country
from api.models import WeatherCity


DATA = {
    "Индонезия": ["Чангу", "Убуд"],
    "Египет": ["Шарм-эль-Шейх", "Хургада"],
    "Индия": ["Панаджи", "Дармсала"],
    "Таиланд": ["Паттайя", "Пхукет"],
    "Аргентина": ["Буэнос-Айрес", "Гинамар"],
    "Армения": ["Ереван", "Гюмри"],
    "Грузия": ["Батуми", "Тбилиси"],
    "Кипр": ["Лимассол", "Пафос"],
    "Мексика": ["Канкун", "Акапулько"],
    "ОАЭ": ["Дубай", "Шарджа"],
    "США": ["Лос-Анджелес", "Майами"],
    "Турция": ["Анталья", "Стамбул"],
}


class Command(BaseCommand):
    help = "Загрузить страны/города для WeatherBot"

    def handle(self, *args, **options):
        for country_name, cities in DATA.items():
            country, _ = Country.objects.get_or_create(name=country_name)
            for city_name in cities:
                WeatherCity.objects.get_or_create(country=country, name=city_name)

        self.stdout.write(self.style.SUCCESS("OK: страны/города добавлены"))
