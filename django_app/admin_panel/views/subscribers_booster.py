# admin_panel/views/subscribers_booster.py

from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib import messages
from django.utils import timezone
from django.http import HttpResponseRedirect
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from datetime import timedelta

from api.models import (
    SubscribersBoostTask,
    SubscribersBoostExpense,
    SubscribersCheck,
    MainEntity,
)
from telegram.models import BotSession
from api.models import BoosterSettings, BoosterTariff


# ============================================================
# 🔹 Список задач
# ============================================================
def subscribers_tasks_view(request):
    """Страница списка задач нормализатора подписчиков."""
    tasks = SubscribersBoostTask.objects.select_related("target").order_by("-created_at")

    now = timezone.now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    two_days_ago = now - timedelta(days=2)
    yesterday = now - timedelta(days=1)
    today = now

    for task in tasks:
        # Расход за месяц
        monthly_expense = SubscribersBoostExpense.objects.filter(
            task=task, created_at__gte=month_ago
        ).aggregate(total=Sum("price"))["total"] or 0
        task.monthly_expense_value = monthly_expense

        # Статистика подписок/отписок
        weekly_checks = SubscribersCheck.objects.filter(task=task, created_at__gte=week_ago)
        monthly_checks = SubscribersCheck.objects.filter(task=task, created_at__gte=month_ago)

        if task.tracking_mode == "unsubs":
            task.weekly_total = weekly_checks.aggregate(s=Sum("new_unsubscriptions"))["s"] or 0
            task.monthly_total = monthly_checks.aggregate(s=Sum("new_unsubscriptions"))["s"] or 0
        else:
            weekly_subs = weekly_checks.aggregate(s=Sum("new_subscriptions"))["s"] or 0
            weekly_unsubs = weekly_checks.aggregate(s=Sum("new_unsubscriptions"))["s"] or 0
            monthly_subs = monthly_checks.aggregate(s=Sum("new_subscriptions"))["s"] or 0
            monthly_unsubs = monthly_checks.aggregate(s=Sum("new_unsubscriptions"))["s"] or 0

            task.weekly_total = weekly_subs - weekly_unsubs
            task.monthly_total = monthly_subs - monthly_unsubs

        # Общие значения (позавчера, вчера, сегодня)
        day_expenses = SubscribersBoostExpense.objects.filter(
            task=task, created_at__date__gte=two_days_ago.date()
        ).values("created_at__date").annotate(total=Sum("subscribers_count"))

        by_date = {item["created_at__date"]: item["total"] for item in day_expenses}

        task.day_before_yesterday_value = by_date.get(two_days_ago.date(), 0)
        task.yesterday_value = by_date.get(yesterday.date(), 0)
        task.today_value = by_date.get(today.date(), 0)

    context = {
        "tasks": tasks,
        "page_title": "Нормализатор подписчиков",
    }
    return render(request, "admin_panel/plugins/subscribers_booster/tasks.html", context)


# ============================================================
# 🔹 Добавление
# ============================================================
def subscribers_task_add(request):
    """Создание новой задачи нормализатора подписчиков."""
    return _subscribers_task_edit_common(request, task_id=None)


# ============================================================
# 🔹 Редактирование
# ============================================================
def subscribers_task_edit(request, task_id: int):
    """Редактирование существующей задачи нормализатора подписчиков."""
    return _subscribers_task_edit_common(request, task_id)


# ============================================================
# 🔹 Общая функция (add/edit)
# ============================================================
def _subscribers_task_edit_common(request, task_id=None):
    task = get_object_or_404(SubscribersBoostTask, id=task_id) if task_id else None
    entities = MainEntity.objects.order_by("name")
    last_bot = BotSession.objects.filter(is_active=True).last()

    # Все задачи для таблицы быстрого доступа
    all_tasks = SubscribersBoostTask.objects.select_related("target").order_by("created_at")
    all_tasks_data = []

    now = timezone.now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    two_days_ago = now - timedelta(days=2)
    yesterday = now - timedelta(days=1)
    today = now

    for t in all_tasks:
        monthly_expense = SubscribersBoostExpense.objects.filter(
            task=t, created_at__gte=month_ago
        ).aggregate(total=Sum("price"))["total"] or 0

        weekly_checks = SubscribersCheck.objects.filter(task=t, created_at__gte=week_ago)
        monthly_checks = SubscribersCheck.objects.filter(task=t, created_at__gte=month_ago)

        if t.tracking_mode == "unsubs":
            weekly_total = weekly_checks.aggregate(s=Sum("new_unsubscriptions"))["s"] or 0
            monthly_total = monthly_checks.aggregate(s=Sum("new_unsubscriptions"))["s"] or 0
        else:
            weekly_subs = weekly_checks.aggregate(s=Sum("new_subscriptions"))["s"] or 0
            weekly_unsubs = weekly_checks.aggregate(s=Sum("new_unsubscriptions"))["s"] or 0
            monthly_subs = monthly_checks.aggregate(s=Sum("new_subscriptions"))["s"] or 0
            monthly_unsubs = monthly_checks.aggregate(s=Sum("new_unsubscriptions"))["s"] or 0
            weekly_total = weekly_subs - weekly_unsubs
            monthly_total = monthly_subs - monthly_unsubs

        day_expenses = SubscribersBoostExpense.objects.filter(
            task=t, created_at__date__gte=two_days_ago.date()
        ).values("created_at__date").annotate(total=Sum("subscribers_count"))

        by_date = {item["created_at__date"]: item["total"] for item in day_expenses}
        all_tasks_data.append({
            "id": t.id,
            "is_active": t.is_active,
            "target": t.target,
            "check_interval": t.check_interval,
            "tracking_mode": t.tracking_mode,
            "weekly_total": weekly_total,
            "monthly_total": monthly_total,
            "monthly_expense_value": monthly_expense,
            "day_before_yesterday_value": by_date.get(two_days_ago.date(), 0),
            "yesterday_value": by_date.get(yesterday.date(), 0),
            "today_value": by_date.get(today.date(), 0),
        })

    settings_obj = BoosterSettings.get_singleton()
    tariffs = {
        "old_views": BoosterTariff.objects.filter(booster=settings_obj, module="old_views").order_by("min_limit"),
        "new_views": BoosterTariff.objects.filter(booster=settings_obj, module="new_views").order_by("min_limit"),
        "subscribers": BoosterTariff.objects.filter(booster=settings_obj, module="subscribers").order_by("min_limit"),
    }

    context = {
        "task": task,
        "entities": entities,
        "all_tasks": all_tasks_data,
        "page_title": "Нормализатор подписчиков"
        + (f" (Редактирование задачи #{task.id})" if task else " (Создание задачи)"),
        "target": task.target if task else None,
        "settings": settings_obj,
        "tariffs": tariffs,
    }

    # === POST ===
    if request.method == "POST":
        data = request.POST
        # log.info("POST data:", dict(data))
        for key, value in data.items():
            if 'active' in key:
                pass
                # log.info(f"{key}: {value}")
        # === 🔹 Обработка BoosterSettings ===
        settings_obj = BoosterSettings.get_singleton()
        settings_obj.is_active = data.get("booster_enabled") == "on"
        settings_obj.api_key = data.get("api_key", "").strip()
        settings_obj.url = data.get("url", "").strip()
        settings_obj.balance_alert_limit = int(data.get("balance_alert_limit", 0))
        settings_obj.balance_alert_enabled = data.get("balance_alert_enabled") == "on"
        settings_obj.save()

        # === 🔹 Обновление тарифов ===
        for module in ["old_views", "new_views", "subscribers"]:
            # Удаляем старые тарифы и пересоздаём (или обновляем существующие)
            index = 1
            while True:
                service_id_key = f"{module}_id_{index}"
                if service_id_key not in data:
                    break
                
                service_id = int(data.get(service_id_key, 0))
                min_limit = int(data.get(f"{module}_min_{index}", 0))
                price = float(data.get(f"{module}_tariff_{index}", 0))
                comment = data.get(f"{module}_comment_{index}", "").strip()

                is_active_key = f"{module}_active_{index}"
                is_active = data.get(is_active_key) == "1"  # Используем get вместо прямого доступа
                
                tariff, created = BoosterTariff.objects.update_or_create(
                    booster=settings_obj, 
                    module=module, 
                    service_id=service_id,
                    min_limit=min_limit,
                    comment=comment,
                    is_active=is_active,
                    defaults={
                        'price_per_1000': price,
                        'comment': comment,
                        'is_active': is_active
                    }
                )
                
                log_message = "Создан" if created else "Обновлен"
                # log.info(f"{log_message} тариф для {module}: service_id={service_id}, min_limit={min_limit}, price={price}, active={is_active}, comment='{comment}'")
                
                index += 1

        # === 🔹 Обновление задачи ===
        target_id = data.get("target_id")
        if target_id:
            target = get_object_or_404(MainEntity, id=target_id)
            check_interval = int(data.get("check_interval", 60))
            tracking_mode = data.get("tracking_mode", "unsubs")
            
            # ИСПРАВЛЕНИЕ: Правильно получаем значение is_active для задачи
            is_active = data.get("is_active") == "1"
            
            max_subscribers = int(data.get("max_subscribers", 0))
            notify_on_exceed = data.get("notify_on_exceed") == "1"

            if task is None:
                task = SubscribersBoostTask.objects.create(
                    target=target,
                    bot=last_bot,
                    check_interval=check_interval,
                    tracking_mode=tracking_mode,
                    max_subscribers=max_subscribers,
                    notify_on_exceed=notify_on_exceed,
                    is_active=is_active,
                )
                messages.success(request, f"Создана новая задача #{task.id}")
            else:
                task.target = target
                task.check_interval = check_interval
                task.tracking_mode = tracking_mode
                task.max_subscribers = max_subscribers
                task.notify_on_exceed = notify_on_exceed
                task.is_active = is_active
                task.updated_at = timezone.now()
                task.save()
                messages.success(request, f"Изменения в задаче #{task.id} сохранены")

        # return render(request, "admin_panel/plugins/subscribers_booster/task_edit.html", context)
        return redirect(reverse("admin_panel:subscribers_tasks_view"))
    # === GET ===

    return render(request, "admin_panel/plugins/subscribers_booster/task_edit.html", context)


# ============================================================
# 🔹 Удаление
# ============================================================
def subscribers_task_delete(request, task_id: int):
    """Удаление задачи нормализатора подписчиков."""
    task = get_object_or_404(SubscribersBoostTask, id=task_id)
    if request.method == "POST":
        task.delete()
        messages.success(request, f"Задача #{task_id} удалена.")
        return HttpResponseRedirect(reverse("admin_panel:subscribers_tasks_view"))

    return render(request, "admin_panel/confirm_delete.html", {
        "object": task,
        "cancel_url": reverse("admin_panel:subscribers_tasks_view"),
        "page_title": f"Удаление задачи #{task.id}",
    })
