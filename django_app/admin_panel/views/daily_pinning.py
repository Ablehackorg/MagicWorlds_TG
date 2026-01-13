# admin_panel/views/daily_pinning.py

from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib import messages
from django.utils import timezone
from django.http import HttpResponseRedirect
from django.views.decorators.csrf import csrf_protect
from django.contrib.auth.decorators import login_required

from api.models import DailyPinningTask, MainEntity
from telegram.models import BotSession


@login_required
def daily_pinning_tasks_view(request):
    """
    Страница списка задач ежедневного закрепления.
    Отображает все задачи с ботами и каналами.
    """
    tasks = DailyPinningTask.objects.select_related("channel", "bot").order_by("-created_at")

    context = {
        "tasks": tasks,
        "page_title": "Эмулятор постов в канале",
    }

    return render(request, "admin_panel/plugins/daily_pinner/tasks.html", context)


@login_required
def daily_pinning_task_add(request):
    """
    Создание новой задачи ежедневного закрепления.
    Вызывает общую функцию обработки формы.
    """
    return _daily_pinning_task_edit_common(request, task_id=None)


@login_required
def daily_pinning_task_edit(request, task_id: int):
    """
    Редактирование существующей задачи ежедневного закрепления.
    Вызывает общую функцию обработки формы с task_id.
    """
    return _daily_pinning_task_edit_common(request, task_id)


@csrf_protect
@login_required
def _daily_pinning_task_edit_common(request, task_id=None):
    """
    Общая функция для создания и редактирования задач.
    Обрабатывает форму, создает или обновляет объект DailyPinningTask,
    подготавливает данные для шаблона редактирования.
    """
    task = get_object_or_404(DailyPinningTask, id=task_id) if task_id else None
    entities = MainEntity.objects.order_by("name")
    bots = BotSession.objects.filter(is_active=True)

    # Все задачи для таблицы быстрого доступа
    all_tasks = DailyPinningTask.objects.select_related("channel", "bot").order_by("-created_at")

    context = {
        "task": task,
        "entities": entities,
        "bots": bots,
        "all_tasks": all_tasks,
        "page_title": f"Скрипт-пустышка для каналов"
                      + (f" (Редактирование задачи #{task.id})" if task else " (Создание задачи)"),
    }

    if request.method == "POST":
        data = request.POST

        try:
            # Получение выбранного канала и бота
            channel = get_object_or_404(MainEntity, id=data.get("channel_id"))
            bot = get_object_or_404(BotSession, id=data.get("bot_id")) if data.get("bot_id") else bots.last()
        except Exception as e:
            messages.error(request, f"Не удалось найти выбранные объекты: {e}")
            return redirect(request.path)

        # Создание новой задачи или обновление существующей
        if task is None:
            task = DailyPinningTask.objects.create(
                bot=bot,
                channel=channel,
                post_link=data.get("post_link", ""),
                start_time=data.get("start_time") or "08:00:00",
                end_time=data.get("end_time") or "18:00:00",
                unpin_after_minutes=int(data.get("unpin_after_minutes", 30)),
                delete_notification_after_minutes=int(data.get("delete_notification_after_minutes", 30)),
                is_active=data.get("is_active") == "1",
            )
            messages.success(request, f"Создана новая задача #{task.id}")
        else:
            task.bot = bot
            task.channel = channel
            task.post_link = data.get("post_link", "")
            task.start_time = data.get("start_time") or task.start_time
            task.end_time = data.get("end_time") or task.end_time
            task.unpin_after_minutes = int(data.get("unpin_after_minutes", task.unpin_after_minutes))
            task.delete_notification_after_minutes = int(data.get("delete_notification_after_minutes", task.delete_notification_after_minutes))
            task.is_active = data.get("is_active") == "1"
            task.updated_at = timezone.now()
            task.save()
            messages.success(request, f"Изменения в задаче #{task.id} сохранены")

        return HttpResponseRedirect(reverse("admin_panel:daily_pinning_tasks_view"))

    return render(request, "admin_panel/plugins/daily_pinner/task_edit.html", context)


@csrf_protect
@login_required
def daily_pinning_task_delete(request, task_id: int):
    """
    Удаление задачи ежедневного закрепления.
    Обрабатывает POST-запрос и удаляет задачу с уведомлением пользователя.
    """
    task = get_object_or_404(DailyPinningTask, id=task_id)

    if request.method == "POST":
        task.delete()
        messages.success(request, f"Задача #{task_id} удалена.")
        return HttpResponseRedirect(reverse("admin_panel:daily_pinning_tasks_view"))

    return render(request, "admin_panel/confirm_delete.html", {
        "object": task,
        "cancel_url": reverse("admin_panel:daily_pinning_tasks_view"),
        "page_title": f"Удаление задачи #{task.id}",
    })
