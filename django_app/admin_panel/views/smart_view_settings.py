# admin_panel/views/smart_view_settings.py

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.urls import reverse

from api.models import BoosterSettings, ViewDistribution


import logging
logging.basicConfig(level=getattr(logging, "INFO", logging.INFO),
                   format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("view_settings")

@require_http_methods(["GET", "POST"])
def views_settings_view(request):
    """
    Настройки распределения просмотров для ViewBoostTask (24 часа, 4 режима).
    """
    settings = BoosterSettings.get_singleton()
    log.info(f"Сеттинг: {settings}, {settings.is_active_new_views}")
    distribution, _ = ViewDistribution.objects.get_or_create()

    if request.method == "POST":
        log.info(request.POST)
        # Управление активностью
        is_active = request.POST.get("is_active") == "1"
        settings.is_active_new_views = is_active
        settings.save(update_fields=["is_active_new_views"])

        # 🔹 Считывание значений для 4 режимов (24 часа)
        def extract(prefix, hours):
            data = {}
            for h in hours:
                try:
                    data[h] = int(request.POST.get(f"{prefix}_{h}", 0))
                except (ValueError, TypeError):
                    data[h] = 0
            return data

        # Теперь только один день (24 часа)
        morning_day1 = extract("morning_today", range(1, 25))
        day_day1 = extract("day_today", range(1, 25))
        evening_day1 = extract("evening_today", range(1, 25))
        night_day1 = extract("night_today", range(1, 25))

        distribution.morning_distribution = {"day1": morning_day1}
        distribution.day_distribution = {"day1": day_day1}
        distribution.evening_distribution = {"day1": evening_day1}
        distribution.night_distribution = {"day1": night_day1}
        distribution.save()

        messages.success(request, "Настройки успешно сохранены.")
        return redirect(reverse("admin_panel:smart_view_settings"))

    # 🔹 Подготовка данных для формы (24 часа)
    def get_val(struct, day, hour):
        try:
            day_data = struct.get(day, {})
            # Ищем по строковому ключу, но возвращаем число
            return int(day_data.get(str(hour), 0))
        except Exception:
            return 0

    morning = distribution.morning_distribution or {}
    day = distribution.day_distribution or {}
    evening = distribution.evening_distribution or {}
    night = distribution.night_distribution or {}
    
    log.info(f"Morning: {morning}")
    log.info(f"Day: {day}")
    log.info(f"Evening: {evening}")
    log.info(f"Night: {night}")
    
    context = {
        "is_active": settings.is_active_new_views,
        "range_1_12": range(1, 13),
        "range_13_24": range(13, 25),
        "page_title": f"Настройки умного просмотра",
        "morning_day1": {h: get_val(morning, "day1", h) for h in range(1, 25)},
        "day_day1": {h: get_val(day, "day1", h) for h in range(1, 25)},
        "evening_day1": {h: get_val(evening, "day1", h) for h in range(1, 25)},
        "night_day1": {h: get_val(night, "day1", h) for h in range(1, 25)},
        "range_4cols": [
            [1,2,3,4,5,6],
            [7,8,9,10,11,12],
            [13,14,15,16,17,18],
            [19,20,21,22,23,24],
        ],
    }

    return render(request, "admin_panel/plugins/view_boost/smart_view_settings.html", context)