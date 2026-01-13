# admin_panel/views/blondinka.py

import json
import logging
import os
from datetime import timedelta

from django.contrib import messages
from django.db.models import Count
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required

from admin_panel.models import Category, Country
from api.models import (
    BlondinkaDialog,
    BlondinkaLog,
    BlondinkaSchedule,
    BlondinkaTask,
    BlondinkaTaskDialog,
    GroupTheme,
    MainEntity,
)
from telegram.models import BotSession

LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("blondinka")


@login_required
@csrf_exempt
def get_theme_dialogs(request):
    """
    API-метод для получения активных диалогов выбранной темы.

    Используется в административном интерфейсе при редактировании задачи,
    чтобы динамически подгружать список диалогов, привязанных к теме группы.
    """
    theme_id = request.GET.get("theme_id")
    if not theme_id:
        return JsonResponse({"success": False, "error": "Theme ID is required"})

    try:
        theme = GroupTheme.objects.get(id=theme_id)
        dialogs = theme.dialogs.filter(is_active=True).values("id", "message")
        return JsonResponse({"success": True, "dialogs": list(dialogs)})
    except GroupTheme.DoesNotExist:
        return JsonResponse({"success": False, "error": "Theme not found"})


@login_required
@csrf_exempt
def update_bot_name(request):
    """
    API-метод для обновления отображаемого имени бота.

    Используется из административного интерфейса без перезагрузки страницы.
    """
    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "Only POST method allowed"}
        )

    try:
        data = json.loads(request.body)
        bot_id = data.get("bot_id")
        new_name = data.get("new_name")

        if not bot_id or not new_name:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Bot ID and new name are required",
                }
            )

        bot = BotSession.objects.get(id=bot_id)
        bot.name = new_name
        bot.save()

        return JsonResponse({"success": True})
    except BotSession.DoesNotExist:
        return JsonResponse({"success": False, "error": "Bot not found"})
    except Exception as exc:
        return JsonResponse({"success": False, "error": str(exc)})


@login_required
def blondinka_tasks_view(request):
    """
    Отображает страницу со списком всех задач бота «Блондинка».

    Дополнительно рассчитывает статистику успешных публикаций
    за сегодня, вчера и последнюю неделю.
    """
    tasks = (
        BlondinkaTask.objects
        .select_related("bot", "group", "group__country")
        .order_by("-created_at")
    )

    today = timezone.now().date()
    yesterday = today - timedelta(days=1)
    week_ago = today - timedelta(days=7)

    for task in tasks:
        task.posts_today = BlondinkaLog.objects.filter(
            task=task,
            created_at__date=today,
            is_success=True,
        ).count()

        task.posts_yesterday = BlondinkaLog.objects.filter(
            task=task,
            created_at__date=yesterday,
            is_success=True,
        ).count()

        task.posts_week = BlondinkaLog.objects.filter(
            task=task,
            created_at__date__gte=week_ago,
            is_success=True,
        ).count()

    owner_types = (
        tasks.values_list("owner_type", flat=True)
        .distinct()
    )

    context = {
        "tasks": tasks,
        "owner_types": owner_types,
        "page_title": "Бот-Блондинка",
    }
    return render(
        request,
        "admin_panel/plugins/blondinka/tasks.html",
        context,
    )


def _blondinka_task_edit_common(request, task_id=None):
    """
    Общая логика создания и редактирования задачи «Блондинка».

    Используется как для добавления новой задачи, так и для
    редактирования существующей, в зависимости от наличия task_id.
    """
    task = (
        get_object_or_404(BlondinkaTask, id=task_id)
        if task_id else None
    )

    entities = MainEntity.objects.order_by("name")

    entities_json = json.dumps([
        {
            "id": entity.id,
            "name": entity.name,
            "link": entity.link or "",
            "telegram_id": entity.telegram_id or "",
            "description": entity.description or "",
            "country": entity.country.name if entity.country else "",
            "category": entity.category.name if entity.category else "",
            "photo_url": entity.photo.url if entity.photo else "",
            "owner": entity.owner or "own",
        }
        for entity in entities
    ])

    bots = BotSession.objects.filter(is_active=True)
    countries = Country.objects.all()
    categories = Category.objects.all()
    group_themes = GroupTheme.objects.all()

    all_themes_dialogs = {}
    for theme in group_themes:
        dialogs = (
            BlondinkaDialog.objects
            .filter(theme=theme)
            .values("id", "message", "is_active")
            .order_by("order")
        )
        all_themes_dialogs[theme.id] = list(dialogs)

    task_dialogs_activity = {}
    if task and task.group_theme:
        task_dialogs = (
            BlondinkaTaskDialog.objects
            .filter(task=task)
            .select_related("dialog")
        )
        for task_dialog in task_dialogs:
            task_dialogs_activity[
                task_dialog.dialog_id
            ] = task_dialog.is_active

    week_days = [
        (0, "Понедельник"),
        (1, "Вторник"),
        (2, "Среда"),
        (3, "Четверг"),
        (4, "Пятница"),
        (5, "Суббота"),
        (6, "Воскресенье"),
    ]

    delete_post_choices = [
        (24, "Сутки"),
        (48, "2 суток"),
        (72, "3 суток"),
        (None, "Не удалять"),
    ]

    other_groups = []
    other_groups_count = 0
    if task and task.group and task.group.country:
        other_groups = (
            BlondinkaTask.objects
            .filter(
                bot=task.bot,
                group__country=task.group.country,
            )
            .exclude(id=task.id)
            .select_related("group")[:10]
        )
        other_groups_count = (
            BlondinkaTask.objects
            .filter(
                bot=task.bot,
                group__country=task.group.country,
            )
            .exclude(id=task.id)
            .count()
        )

    context = {
        "task": task,
        "entities": entities,
        "entities_json": entities_json,
        "bots": bots,
        "countries": countries,
        "categories": categories,
        "group_themes": group_themes,
        "week_days": week_days,
        "delete_post_choices": delete_post_choices,
        "other_groups": other_groups,
        "other_groups_count": other_groups_count,
        "all_themes_dialogs_json": json.dumps(all_themes_dialogs),
        "task_dialogs_activity_json": json.dumps(task_dialogs_activity),
        "run_now_checked": task.run_now if task else False,
        "page_title": (
            "Бот-Блондинка"
            + (
                f" (Редактирование задачи #{task.id})"
                if task else " (Создание задачи)"
            )
        ),
    }

    if request.method == "POST":
        return _handle_task_form_submit(
            request,
            task,
            week_days,
        )

    return render(
        request,
        "admin_panel/plugins/blondinka/task_edit.html",
        context,
    )


def process_task_dialogs(request, task):
    """
    Обрабатывает привязку диалогов к задаче.

    Создаёт, удаляет и обновляет связи BlondinkaTaskDialog
    в соответствии с данными формы.
    """
    if not task.group_theme:
        return

    post_data = request.POST

    BlondinkaTaskDialog.objects.filter(task=task).delete()

    dialogs_to_process = {}

    for key, value in post_data.items():
        if key.startswith("task_dialog_active_"):
            dialog_id = key.replace("task_dialog_active_", "")
            dialogs_to_process[dialog_id] = value == "1"

        elif key.startswith("task_dialog_new_") and not key.endswith("_active"):
            temp_id = key.replace("task_dialog_new_", "")
            message = value.strip()

            if not message:
                continue

            active_key = f"task_dialog_new_active_{temp_id}"
            is_active = post_data.get(active_key) == "1"

            new_dialog = BlondinkaDialog.objects.create(
                theme=task.group_theme,
                message=message,
                is_active=True,
                order=0,
            )
            dialogs_to_process[str(new_dialog.id)] = is_active

    for key, value in post_data.items():
        if key.startswith("dialog_delete_"):
            dialog_id = key.replace("dialog_delete_", "")
            if value == "1":
                dialogs_to_process.pop(dialog_id, None)

    for dialog_id_str, is_active in dialogs_to_process.items():
        try:
            dialog = BlondinkaDialog.objects.get(
                id=int(dialog_id_str),
                theme=task.group_theme,
            )
            BlondinkaTaskDialog.objects.create(
                task=task,
                dialog=dialog,
                is_active=is_active,
            )
        except (ValueError, BlondinkaDialog.DoesNotExist):
            continue


@login_required
def blondinka_task_add(request):
    """
    Представление для создания новой задачи.
    """
    return _blondinka_task_edit_common(request)


@login_required
def blondinka_task_edit(request, task_id: int):
    """
    Представление для редактирования существующей задачи.
    """
    return _blondinka_task_edit_common(request, task_id)


@login_required
def blondinka_task_delete(request, task_id: int):
    """
    Удаляет задачу после подтверждения пользователя.
    """
    task = get_object_or_404(BlondinkaTask, id=task_id)

    if request.method == "POST":
        task.delete()
        messages.success(
            request,
            f"Задача #{task_id} удалена.",
        )
        return HttpResponseRedirect(
            reverse("admin_panel:blondinka_tasks_view")
        )

    return render(
        request,
        "admin_panel/confirm_delete.html",
        {
            "object": task,
            "cancel_url": reverse(
                "admin_panel:blondinka_tasks_view"
            ),
            "page_title": f"Удаление задачи #{task.id}",
        },
    )
