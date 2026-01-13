# admin_panel/views/stats/reactions.py - НОВЫЙ ФАЙЛ ДЛЯ СТАТИСТИКИ

from datetime import datetime, date, time, timedelta

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone

from api.models import ReactionBoostTask, ReactionRecord


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
def reaction_stats_view(request):
    """
    Страница "Статистика реакций":
    – показывает сумму реакций по дням недели
      для всех задач ReactionBoostTask за выбранную неделю (Пн–Вс).
    – поддерживает AJAX-обновление таблицы.
    """
    raw_period = request.GET.get("period", "current")
    period, start_dt, end_dt, start_date, end_date = _get_week_bounds(raw_period)
    tz = timezone.get_current_timezone()

    rows = []
    
    # Получаем все активные задачи
    tasks = ReactionBoostTask.objects.select_related("target").filter(is_active=True)
    
    for task in tasks:
        # Получаем реакции за выбранный период
        reactions = ReactionRecord.objects.filter(
            task=task,
            created_at__gte=start_dt,
            created_at__lt=end_dt
        )
        
        # Группируем по дням недели
        days = [0] * 7
        total = 0
        
        for reaction in reactions:
            dt_local = reaction.created_at.astimezone(tz)
            weekday_idx = dt_local.weekday()  # 0=Пн...6=Вс
            if 0 <= weekday_idx <= 6:
                days[weekday_idx] += 1
                total += 1
        
        rows.append({
            "id": task.id,
            "task_id": task.id,
            "channel_name": task.target.name if task.target else "—",
            "channel_id": task.target.id if task.target else None,
            "posts_count": task.posts_count,
            "reactions_per_post": task.reactions_per_post,
            "frequency_days": task.frequency_days,
            "days": days,
            "total": total,
            "is_active": task.is_active,
        })
    
    # Сортируем по ID задачи
    rows.sort(key=lambda x: x["id"])
    
    context = {
        "rows": rows,
        "current_period": period,   # 'current' | 'prev' | 'prev2'
        "week_start": start_date,   # date
        "week_end": end_date - timedelta(days=1),  # последний день недели (Вс)
    }

    # AJAX-запрос — отдаем только HTML таблицы
    if (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or request.GET.get("ajax") == "1"
    ):
        html = render_to_string(
            "admin_panel/stats/reactions/_stats_table.html",
            context=context,
            request=request,
        )
        return JsonResponse({"html": html})

    # Обычный запрос — рендерим полную страницу
    return render(
        request,
        "admin_panel/stats/reactions/stats.html",
        context,
    )
