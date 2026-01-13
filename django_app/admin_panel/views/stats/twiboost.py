# admin_panel/views/stats/twiboost.py

import logging
from datetime import datetime, date, time, timedelta

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone

from api.models import (
    ViewBoostExpense,
    OldViewsExpense,
    SubscribersBoostExpense,
)

log = logging.getLogger(__name__)


MODULE_ORDER = {
    "subscribers": 0,
    "new_views": 1,
    "old_views": 2,
}

# Добавляем словарь для отображаемых названий модулей
MODULE_DISPLAY_NAMES = {
    "subscribers": "Подписчики",
    "new_views": "Умные просмотры",  # Изменено с "Просмотры" на "Умные просмотры"
    "old_views": "Просмотры",        # Оставлено как "Просмотры"
}


def _get_week_bounds(period: str):
    """
    period: 'current' | 'prev' | 'prev2'
    Возвращает:
      normalized_period, start_dt, end_dt, start_date, end_date
      где end_date = старт следующей недели (не включительно в фильтр)
    """
    today = timezone.localdate()
    monday_this_week = today - timedelta(days=today.weekday())  # 0 = Пн

    if period == "prev":
        start_date = monday_this_week - timedelta(days=7)
    elif period == "prev2":
        start_date = monday_this_week - timedelta(days=14)
    else:
        period = "current"
        start_date = monday_this_week

    end_date = start_date + timedelta(days=7)

    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(end_date, time.min), tz)

    return period, start_dt, end_dt, start_date, end_date


@login_required
def twiboost_stats_view(request):
    """
    Страница "Статистика сервиса":
    – показывает сумму просмотров/подписок по дням недели
      для всех service_id всех модулей (subscribers/new_views/old_views)
      за выбранную неделю (Пн–Вс).
    – поддерживает AJAX-обновление таблицы.
    """
    raw_period = request.GET.get("period", "current")
    period, start_dt, end_dt, start_date, end_date = _get_week_bounds(raw_period)
    tz = timezone.get_current_timezone()

    rows_map = {}
    # ключ: (module, service_id, channel_id) ->
    #  {
    #    "id": ... (минимальный id расхода),
    #    "module": "subscribers" | "new_views" | "old_views",
    #    "module_display": "Подписчики" | "Умные просмотры" | "Просмотры",  # Добавлено
    #    "service_id": int,
    #    "channel_name": str,
    #    "channel_id": int,
    #    "days": [пн..вс],
    #    "total": int,
    #  }

    def add_expenses(module_key, qs, count_field: str):
        nonlocal rows_map
        for exp in qs:
            task = getattr(exp, "task", None)
            if not task:
                continue
            entity = getattr(task, "target", None)
            if not entity:
                continue

            channel_id = entity.id
            channel_name = entity.name or "—"
            service_id = exp.service_id
            if not service_id:
              continue

            dt_local = exp.created_at.astimezone(tz)
            weekday_idx = dt_local.weekday()  # 0=Пн...6=Вс
            if weekday_idx < 0 or weekday_idx > 6:
                continue

            key = (module_key, service_id, channel_id)
            count = getattr(exp, count_field, 0) or 0

            if key not in rows_map:
                rows_map[key] = {
                    "id": exp.id,
                    "module": module_key,
                    "module_display": MODULE_DISPLAY_NAMES.get(module_key, module_key),  # Добавлено
                    "service_id": service_id,
                    "channel_name": channel_name,
                    "channel_id": channel_id,
                    "days": [0] * 7,
                    "total": 0,
                }
            else:
                if exp.id < rows_map[key]["id"]:
                    rows_map[key]["id"] = exp.id

            rows_map[key]["days"][weekday_idx] += count
            rows_map[key]["total"] += count

    base_filter = {
        "created_at__gte": start_dt,
        "created_at__lt": end_dt,
    }

    # Подписчики
    subs_qs = (
        SubscribersBoostExpense.objects
        .filter(**base_filter)
        .select_related("task__target")
    )
    add_expenses("subscribers", subs_qs, "subscribers_count")

    # Новые просмотры (теперь "Умные просмотры")
    new_views_qs = (
        ViewBoostExpense.objects
        .filter(**base_filter)
        .select_related("task__target")
    )
    add_expenses("new_views", new_views_qs, "views_count")

    # Старые просмотры
    old_views_qs = (
        OldViewsExpense.objects
        .filter(**base_filter)
        .select_related("task__target")
    )
    add_expenses("old_views", old_views_qs, "views_count")

    module_order = MODULE_ORDER

    rows = sorted(
        rows_map.values(),
        key=lambda r: (
            module_order.get(r["module"], 99),
            r["service_id"],
            (r["channel_name"] or "").lower(),
        ),
    )

    context = {
        "rows": rows,
        "current_period": period,   # 'current' | 'prev' | 'prev2'
        "week_start": start_date,   # date
        "week_end": end_date - timedelta(days=1),  # последний день недели (Вс)
    }

    # AJAX-запрос — отдаем только HTML таблицы (с заголовком недели)
    if (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or request.GET.get("ajax") == "1"
    ):
        html = render_to_string(
            "admin_panel/stats/twiboost/_stats_table.html",
            context=context,
            request=request,
        )
        return JsonResponse({"html": html})

    # Обычный запрос — рендерим полную страницу
    return render(
        request,
        "admin_panel/stats/twiboost/stats.html",
        context,
    )