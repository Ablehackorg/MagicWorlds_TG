# admin_panel/views/old_views_booster.py

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.contrib import messages
from django.utils import timezone
from django.http import HttpResponseRedirect
from django.db.models import Sum
from datetime import timedelta
import datetime

from api.models import OldViewsTask, OldViewsExpense, MainEntity
from telegram.models import BotSession


# ============================================================
# 🔹 Список задач
# ============================================================
def old_views_tasks_view(request):
    """Страница списка задач старых просмотров."""
    tasks = OldViewsTask.objects.select_related("target").order_by("-created_at")

    # Подсчёт расходов за месяц
    for task in tasks:
        start_date = timezone.now() - timedelta(days=30)
        period_expense = OldViewsExpense.objects.filter(
            task=task,
            created_at__gte=start_date
        ).aggregate(total=Sum('price'))['total'] or 0
        task.monthly_expense_value = period_expense

    context = {
        "tasks": tasks,
        "page_title": "Нормализатор просмотров",
    }
    return render(request, "admin_panel/plugins/old_views_booster/tasks.html", context)


# ============================================================
# 🔹 Добавление
# ============================================================
def old_views_task_add(request):
    """Создание новой задачи."""
    return _old_views_task_edit_common(request, task_id=None)


# ============================================================
# 🔹 Редактирование
# ============================================================
def old_views_task_edit(request, task_id: int):
    """Редактирование существующей задачи."""
    return _old_views_task_edit_common(request, task_id)


# ============================================================
# 🔹 Общая функция (add/edit)
# ============================================================
def _old_views_task_edit_common(request, task_id=None):
    task = get_object_or_404(OldViewsTask, id=task_id) if task_id else None
    entities = MainEntity.objects.order_by("name")
    last_bot = BotSession.objects.filter(is_active=True).last()

    # Все задачи для таблицы быстрого доступа
    all_tasks = OldViewsTask.objects.select_related("target").order_by("created_at")
    all_tasks_data = []
    for t in all_tasks:
        start_date = timezone.now() - timedelta(days=30)
        period_expense = OldViewsExpense.objects.filter(
            task=t,
            created_at__gte=start_date
        ).aggregate(total=Sum('price'))['total'] or 0
        all_tasks_data.append({
            'id': t.id,
            'is_active': t.is_active,
            'target': t.target,
            'normalization_mode': t.normalization_mode,
            'posts_normalization': t.posts_normalization,
            'subscribers_count': t.subscribers_count,
            'view_coefficient': t.view_coefficient,
            'views_multiplier': t.views_multiplier,
            'monthly_expense_value': period_expense,
        })

    # Форматирование времени для отображения в шаблоне
    normalization_time_display = ""
    if task and task.normalization_time:
        normalization_time_display = task.normalization_time.strftime('%H:%M')

    context = {
        "task": task,
        "entities": entities,
        "all_tasks": all_tasks_data,
        "normalization_time_display": normalization_time_display,
        "page_title": f"Нормализатор просмотров" + (f" (Редактирование задачи #{task.id})" if task else " (Создание задачи)"),
    }

    if request.method == "POST":
        data = request.POST
        target = get_object_or_404(MainEntity, id=data.get("target_id"))

        # Обработка времени
        normalization_time_str = data.get("normalization_time", "00:00")
        try:
            # Преобразуем строку времени в объект time
            normalization_time = datetime.datetime.strptime(normalization_time_str, '%H:%M').time()
        except (ValueError, TypeError):
            normalization_time = datetime.time(0, 0)  # значение по умолчанию

        if task is None:
            task = OldViewsTask.objects.create(
                target=target,
                bot=last_bot,
                normalization_mode=data.get("normalization_mode", "monthly"),
                normalization_time=normalization_time,
                run_once=data.get("run_once") == "1",
                exclude_period=data.get("exclude_period", "none"),
                posts_normalization=data.get("posts_normalization", "last_100"),
                view_coefficient=int(data.get("view_coefficient", 50)),
                views_multiplier=int(data.get("views_multiplier", 1)),
                is_active=data.get("is_active") == "1",
            )
            messages.success(request, f"Создана новая задача #{task.id}")
        else:
            task.target = target
            task.normalization_mode = data.get("normalization_mode", task.normalization_mode)
            task.normalization_time = normalization_time
            # Для run_once используем отдельную логику - сбрасываем после выполнения
            if data.get("run_once") == "1":
                task.run_once = True
            task.exclude_period = data.get("exclude_period", task.exclude_period)
            task.posts_normalization = data.get("posts_normalization", task.posts_normalization)
            task.view_coefficient = int(data.get("view_coefficient", task.view_coefficient))
            task.views_multiplier = int(data.get("views_multiplier", task.views_multiplier))
            task.is_active = data.get("is_active") == "1"
            task.updated_at = timezone.now()
            # Если это разовый запуск, сбрасываем last_successful_run
            if data.get("run_once") == "1":
                task.last_successful_run = None
            task.save()
            messages.success(request, f"Изменения в задаче #{task.id} сохранены")

        return redirect(reverse("admin_panel:old_views_tasks_view"))

    return render(request, "admin_panel/plugins/old_views_booster/task_edit.html", context)


# ============================================================
# 🔹 Удаление
# ============================================================
def old_views_task_delete(request, task_id: int):
    """Удаление задачи."""
    task = get_object_or_404(OldViewsTask, id=task_id)
    if request.method == "POST":
        task.delete()
        messages.success(request, f"Задача #{task_id} удалена.")
        return HttpResponseRedirect(reverse("admin_panel:old_views_tasks_view"))

    return render(request, "admin_panel/confirm_delete.html", {
        "object": task,
        "cancel_url": reverse("admin_panel:old_views_tasks_view"),
        "page_title": f"Удаление задачи #{task.id}",
    })