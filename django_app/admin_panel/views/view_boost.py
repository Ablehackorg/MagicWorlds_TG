# admin_panel/views/view_boost.py

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.contrib import messages
from django.utils import timezone
from django.http import HttpResponseRedirect
from django.db.models import Sum
from datetime import datetime, timedelta

from api.models import ViewBoostTask, ViewBoostExpense, MainEntity
from telegram.models import BotSession


# ============================================================
# 🔹 Список задач
# ============================================================
def view_boost_tasks_view(request):
    """Страница списка задач умного просмотра."""
    tasks = ViewBoostTask.objects.select_related("target").order_by("-created_at")
    
    # Рассчитываем расходы за месяц/неделю для каждой задачи
    for task in tasks:
        if task.show_expenses_for == "week":
            start_date = timezone.now() - timedelta(days=7)
            period_label = "нед."
        else:  # month
            start_date = timezone.now() - timedelta(days=30)
            period_label = "мес."
            
        period_expense = ViewBoostExpense.objects.filter(
            task=task,
            created_at__gte=start_date
        ).aggregate(total=Sum('price'))['total'] or 0
        
        # Добавляем вычисляемые поля в объект задачи
        task.expenses_period_value = period_expense
        task.expenses_period_label = period_label

    context = {
        "tasks": tasks,
        "page_title": "Умный просмотр новых постов",
    }
    return render(request, "admin_panel/plugins/view_boost/tasks.html", context)


# ============================================================
# 🔹 Добавление
# ============================================================
def view_boost_task_add(request):
    """Создание новой задачи."""
    return _view_boost_task_edit_common(request, task_id=None)


# ============================================================
# 🔹 Редактирование
# ============================================================
def view_boost_task_edit(request, task_id: int):
    """Редактирование существующей задачи."""
    return _view_boost_task_edit_common(request, task_id)


# ============================================================
# 🔹 Общая функция (add/edit)
# ============================================================
def _view_boost_task_edit_common(request, task_id=None):
    task = get_object_or_404(ViewBoostTask, id=task_id) if task_id else None
    entities = MainEntity.objects.order_by("name")
    
    # Автоматически выбираем последнего активного бота
    last_bot = BotSession.objects.filter(is_active=True).last()
    
    # Получаем все задачи для таблицы быстрого доступа с вычисляемыми полями
    all_tasks_data = []
    all_tasks = ViewBoostTask.objects.select_related("target").order_by("-created_at")
    
    for t in all_tasks:
        # Рассчитываем расходы для каждой задачи
        if t.show_expenses_for == "week":
            start_date = timezone.now() - timedelta(days=7)
            period_label = "нед."
        else:  # month
            start_date = timezone.now() - timedelta(days=30)
            period_label = "мес."
            
        period_expense = ViewBoostExpense.objects.filter(
            task=t,
            created_at__gte=start_date
        ).aggregate(total=Sum('price'))['total'] or 0
        
        all_tasks_data.append({
            'id': t.id,
            'is_active': t.is_active,
            'target': t.target,
            'subscribers_count': t.subscribers_count,
            'view_coefficient': t.view_coefficient,
            'show_expenses_for': t.show_expenses_for,
            'period_expense': period_expense,
            'period_label': period_label,
        })

    context = {
        "task": task,
        "entities": entities,
        "all_tasks": all_tasks_data,
        "page_title": f"Умный просмотр новых постов" + (f" (Редактирование задачи #{task.id})" if task else " (Создание задачи)"),
    }

    if request.method == "POST":
        data = request.POST

        try:
            target = get_object_or_404(MainEntity, id=data.get("target_id"))
        except Exception as e:
            messages.error(request, f"Не удалось найти выбранный канал: {e}")
            return redirect(request.path)

        # Обновление/создание
        if task is None:
            task = ViewBoostTask.objects.create(
                target=target,
                bot=last_bot,
                view_coefficient=int(data.get("view_coefficient", 50)),
                normalization_mode=data.get("normalization_mode", "daily"),
                show_expenses_for=data.get("show_expenses_for", "month"),
                is_active=data.get("is_active") == "1",
            )
            messages.success(request, f"Создана новая задача #{task.id}")
        else:
            task.target = target
            task.view_coefficient = int(data.get("view_coefficient", task.view_coefficient))
            task.normalization_mode = data.get("normalization_mode", task.normalization_mode)
            task.show_expenses_for = data.get("show_expenses_for", task.show_expenses_for)
            task.is_active = data.get("is_active") == "1"
            task.updated_at = timezone.now()
            task.save()
            messages.success(request, f"Изменения в задаче #{task.id} сохранены")

        # return render(request, "admin_panel/plugins/view_boost/task_edit.html", context)
        return redirect(reverse("admin_panel:view_boost_tasks_view"))
    return render(request, "admin_panel/plugins/view_boost/task_edit.html", context)


# ============================================================
# 🔹 Удаление
# ============================================================
def view_boost_task_delete(request, task_id: int):
    """Удаление задачи."""
    task = get_object_or_404(ViewBoostTask, id=task_id)
    if request.method == "POST":
        task.delete()
        messages.success(request, f"Задача #{task_id} удалена.")
        return HttpResponseRedirect(reverse("admin_panel:view_boost_tasks_view"))

    return render(request, "admin_panel/confirm_delete.html", {
        "object": task,
        "cancel_url": reverse("admin_panel:view_boost_tasks_view"),
        "page_title": f"Удаление задачи #{task.id}",
    })