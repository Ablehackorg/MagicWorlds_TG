# admin_panel/views/reaction_boost.py

from datetime import datetime, timedelta

from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.utils import timezone

from api.models import ReactionBoostTask, ReactionRecord, MainEntity
from telegram.models import BotSession


# ============================================================
# 🔹 Список задач лайкера постов (только параметры)
# ============================================================
def reaction_tasks_view(request):
    """Страница списка задач лайкера постов (только параметры)."""
    tasks = list(
        ReactionBoostTask.objects.select_related("target").order_by("-created_at")
    )

    # Добавляем параметры, но не статистику
    for t in tasks:
        # Здесь оставляем только параметры задачи
        t.posts_count_display = t.posts_count
        t.reactions_per_post_display = t.reactions_per_post
        t.frequency_days_display = t.frequency_days

    context = {
        "tasks": tasks,
        "page_title": "Параметры лайкера постов",
    }
    return render(request, "admin_panel/plugins/reaction_booster/tasks.html", context)


# ============================================================
# 🔹 Добавление
# ============================================================
def reaction_task_add(request):
    return _reaction_task_edit_common(request, task_id=None)


# ============================================================
# 🔹 Редактирование
# ============================================================
def reaction_task_edit(request, task_id: int):
    return _reaction_task_edit_common(request, task_id)


# ============================================================
# 🔹 Общая функция (add/edit)
# ============================================================
def _reaction_task_edit_common(request, task_id=None):
    task = get_object_or_404(ReactionBoostTask, id=task_id) if task_id else None
    entities = MainEntity.objects.order_by("name")

    # Последний активный бот
    last_bot = BotSession.objects.filter(is_active=True).last()

    # Таблица быстрого доступа (внизу страницы)
    all_tasks = list(
        ReactionBoostTask.objects.select_related("target").order_by("-created_at")
    )

    page_suffix = (
        f" (Редактирование задачи #{task.id})" if task else " (Создание задачи)"
    )

    context = {
        "task": task,
        "entities": entities,
        "all_tasks": all_tasks,
        "page_title": f"Параметры лайкера постов{page_suffix}",
    }

    if request.method == "POST":
        data = request.POST

        # Цель
        try:
            target = get_object_or_404(MainEntity, id=data.get("target_id"))
        except Exception as e:
            messages.error(request, f"Не удалось найти выбранный канал/группу: {e}")
            return redirect(request.path)

        if not last_bot:
            messages.error(request, "Нет активных ботов для запуска лайкера постов.")
            return redirect(request.path)

        # Конфигурация
        def _int(val, default):
            try:
                return int(val)
            except (TypeError, ValueError):
                return default

        posts_count = _int(
            data.get("posts_count"),
            task.posts_count if task else 10,
        )
        reactions_per_post = _int(
            data.get("reactions_per_post"),
            task.reactions_per_post if task else 5,
        )
        frequency_days = _int(
            data.get("frequency_days"),
            task.frequency_days if task else 1,
        )

        reaction_type = data.get(
            "reaction_type",
            task.reaction_type if task else "positive",
        )

        # Время запуска
        time_str = data.get("launch_time") or (
            task.launch_time.strftime("%H:%M")
            if task and task.launch_time
            else "10:00"
        )
        try:
            launch_time = datetime.strptime(time_str, "%H:%M").time()
        except ValueError:
            messages.error(request, "Неверный формат времени запуска. Используйте HH:MM.")
            return redirect(request.path)

        is_active = data.get("is_active") == "1"
        run_once_now = data.get("run_once_now") == "1"

        # Создание / обновление
        if task is None:
            task = ReactionBoostTask.objects.create(
                target=target,
                bot=last_bot,
                posts_count=posts_count,
                reactions_per_post=reactions_per_post,
                reaction_type=reaction_type,
                frequency_days=frequency_days,
                launch_time=launch_time,
                run_once_now=run_once_now,
                is_active=is_active,
            )
            messages.success(request, f"Создана новая задача лайкера #{task.id}")
        else:
            task.target = target
            task.posts_count = posts_count
            task.reactions_per_post = reactions_per_post
            task.reaction_type = reaction_type
            task.frequency_days = frequency_days
            task.launch_time = launch_time
            task.run_once_now = run_once_now
            task.is_active = is_active
            task.updated_at = timezone.now()
            task.save()
            messages.success(request, f"Изменения в задаче #{task.id} сохранены")

        return redirect(reverse("admin_panel:reaction_tasks_view"))
    return render(request, "admin_panel/plugins/reaction_booster/task_edit.html", context)


# ============================================================
# 🔹 Удаление
# ============================================================
def reaction_task_delete(request, task_id: int):
    task = get_object_or_404(ReactionBoostTask, id=task_id)
    if request.method == "POST":
        task.delete()
        messages.success(request, f"Задача #{task_id} удалена.")
        return HttpResponseRedirect(reverse("admin_panel:reaction_tasks_view"))

    return render(
        request,
        "admin_panel/confirm_delete.html",
        {
            "object": task,
            "cancel_url": reverse("admin_panel:reaction_tasks_view"),
            "page_title": f"Удаление задачи #{task.id}",
        },
    )