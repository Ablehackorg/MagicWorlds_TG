# admin_panel/views/channel_sync.py
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib import messages
from django.utils import timezone
from django.http import HttpResponseRedirect
from django.db.models import Count, Q
from django.views.decorators.csrf import csrf_protect
from django.contrib.auth.decorators import login_required

from admin_panel.models import Category, Country
from telegram.models import BotSession
from api.models import MainEntity, ChannelSyncTask, ChannelSyncHistory, ChannelSyncProgress


@login_required
def channel_sync_tasks_view(request):
    """
    Страница списка задач синхронизации библиотек.
    Вычисляет прогресс каждой задачи и передает данные в шаблон.
    """
    tasks = ChannelSyncTask.objects.select_related(
        'source', 'target', 'source__country'
    ).prefetch_related('progress').order_by("-created_at")

    tasks_with_progress = []

    for task in tasks:
        copied_posts = 0
        total_posts = 0
        true_copied_posts = 0

        if hasattr(task, 'progress') and task.progress:
            progress = task.progress
            copied_posts = progress.copied_posts
            total_posts = progress.total_posts_to_copy
            true_copied_posts = copied_posts

            if progress.is_completed:
                sync_progress_percent = 100
                copied_posts = total_posts
            elif total_posts > 0:
                sync_progress_percent = (copied_posts / total_posts) * 100
            else:
                sync_progress_percent = 0
        else:
            sync_progress_percent = 0

        task_data = {
            'task': task,
            'sync_progress_percent': sync_progress_percent,
            'copied_posts': copied_posts,
            'true_copied_posts': true_copied_posts,
            'total_posts_to_copy': total_posts,
        }
        tasks_with_progress.append(task_data)

    context = {
        "tasks_with_progress": tasks_with_progress,
        "page_title": "Клон-Актуализация",
    }

    return render(request, "admin_panel/plugins/channel_sync/tasks.html", context)


@login_required
def channel_sync_task_add(request):
    """
    Создание новой задачи синхронизации.
    Вызывает общую функцию обработки формы.
    """
    return _channel_sync_task_edit_common(request, task_id=None)


@login_required
def channel_sync_task_edit(request, task_id: int):
    """
    Редактирование существующей задачи синхронизации.
    Вызывает общую функцию обработки формы с task_id.
    """
    return _channel_sync_task_edit_common(request, task_id)


@csrf_protect
@login_required
def _channel_sync_task_edit_common(request, task_id=None):
    """
    Общая функция для создания и редактирования задач.
    Обрабатывает форму, создает или обновляет объект ChannelSyncTask,
    а также вычисляет прогресс синхронизации для передачи в шаблон.
    """
    task = get_object_or_404(
        ChannelSyncTask.objects.prefetch_related('progress'),
        id=task_id
    ) if task_id else None

    # Получаем все сущности для выбора источника и целевой библиотеки
    entities = MainEntity.objects.order_by("name")

    # Выбираем последнего активного бота по умолчанию
    last_bot = BotSession.objects.filter(is_active=True, id=1).last()

    # История последних 10 синхронизаций для задачи
    history = []
    if task:
        history = ChannelSyncHistory.objects.filter(task=task).order_by('-sync_date')[:10]

    # Вычисление прогресса синхронизации для передачи в контекст
    sync_progress_percent = 0
    if task and hasattr(task, 'progress') and task.progress:
        if task.progress.is_completed:
            sync_progress_percent = 100
        elif task.progress.total_posts_to_copy > 0:
            sync_progress_percent = (task.progress.copied_posts / task.progress.total_posts_to_copy) * 100

    context = {
        "task": task,
        "entities": entities,
        "history": history,
        "sync_progress_percent": sync_progress_percent,
        "page_title": f"Клон-Актуализация" + (f" (Редактирование задачи #{task.id})"
                                               if task else " (Создание задачи)"),
    }

    if request.method == "POST":
        data = request.POST

        try:
            # Обработка source entity
            source_id = data.get("source_id")
            if source_id:
                source = get_object_or_404(MainEntity, id=source_id)
            else:
                source_data = {
                    'link': data.get('source_link', ''),
                    'telegram_id': data.get('source_telegram_id'),
                    'name': data.get('source_name', ''),
                    'description': data.get('source_description', ''),
                }
                source_telegram_id = source_data['telegram_id']
                if source_telegram_id:
                    source, created = MainEntity.objects.get_or_create(
                        telegram_id=source_telegram_id,
                        defaults=source_data
                    )
                    if not created:
                        for key, value in source_data.items():
                            if value:
                                setattr(source, key, value)
                        source.save()
                else:
                    messages.error(request, "Необходимо указать Telegram ID или выбрать существующий канал")
                    return render(request, "admin_panel/plugins/channel_sync/task_edit.html", context)

            # Обработка target entity
            target_id = data.get("target_id")
            if not target_id:
                messages.error(request, "Необходимо выбрать канал-библиотеку")
                return render(request, "admin_panel/plugins/channel_sync/task_edit.html", context)
            target = get_object_or_404(MainEntity, id=target_id)

            # Получение выбранного бота
            bot = get_object_or_404(BotSession, id=data.get("bot_id", last_bot.id))

            # Обработка времени запуска
            scheduled_time = data.get("scheduled_time") or "00:00"

            # Флаг немедленного запуска
            run_once_task = data.get("run_once_task") == "1"

            # Создание или обновление задачи
            if task is None:
                task = ChannelSyncTask.objects.create(
                    source=source,
                    target=target,
                    bot=bot,
                    update_period_days=data.get("update_period_days") or None,
                    update_range=data.get("update_range", "new_only"),
                    scheduled_time=scheduled_time,
                    run_once_task=run_once_task,
                    is_active=data.get("is_active") == "1",
                )
                # Создание прогресса для новой задачи
                ChannelSyncProgress.objects.create(task=task)
                messages.success(request, f"Создана новая задача #{task.id}")
            else:
                task.source = source
                task.target = target
                task.bot = bot
                task.update_period_days = data.get("update_period_days") or None
                task.update_range = data.get("update_range", task.update_range)
                task.scheduled_time = scheduled_time
                task.run_once_task = run_once_task
                task.is_active = data.get("is_active") == "1"

                if data.get("run_once_now") == "1":
                    task.run_once_now = True
                    messages.info(request, f"Задача #{task.id} будет синхронизирована в ближайшие 10 секунд")

                task.save()
                messages.success(request, f"Изменения в задаче #{task.id} сохранены")

            return redirect(reverse("admin_panel:channel_sync_tasks_view"))

        except Exception as e:
            messages.error(request, f"Ошибка сохранения: {e}")
            import traceback
            traceback.print_exc()

    return render(request, "admin_panel/plugins/channel_sync/task_edit.html", context)


@csrf_protect
@login_required
def channel_sync_task_delete(request, task_id: int):
    """
    Удаление задачи синхронизации.
    Обрабатывает POST-запрос и удаляет задачу.
    """
    task = get_object_or_404(
        ChannelSyncTask.objects.prefetch_related('progress'),
        id=task_id
    ) if task_id else None

    # Получаем все сущности для выбора источника и целевой библиотеки
    entities = MainEntity.objects.order_by("name")

    # Выбираем последнего активного бота по умолчанию
    last_bot = BotSession.objects.filter(is_active=True, id=1).last()

    # История последних 10 синхронизаций для задачи
    history = []
    if task:
        history = ChannelSyncHistory.objects.filter(task=task).order_by('-sync_date')[:10]

    # Вычисление прогресса синхронизации для передачи в контекст
    sync_progress_percent = 0
    if task and hasattr(task, 'progress') and task.progress:
        if task.progress.is_completed:
            sync_progress_percent = 100
        elif task.progress.total_posts_to_copy > 0:
            sync_progress_percent = (task.progress.copied_posts / task.progress.total_posts_to_copy) * 100

    context = {
        "task": task,
        "entities": entities,
        "history": history,
        "sync_progress_percent": sync_progress_percent,
        "page_title": f"Клон-Актуализация" + (f" (Редактирование задачи #{task.id})"
                                               if task else " (Создание задачи)"),
    }

    if request.method == "POST":
        task.delete()
        messages.success(request, f"Задача #{task_id} удалена.")
        return HttpResponseRedirect(reverse("admin_panel:channel_sync_tasks_view"))

    
    return render(request, "admin_panel/plugins/channel_sync/task_edit.html", context)