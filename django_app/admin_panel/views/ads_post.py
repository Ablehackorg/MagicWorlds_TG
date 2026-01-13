import logging
import time
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from api.models import AdsOrder, MainEntity
from telegram.models import BotSession

log = logging.getLogger(__name__)


@login_required
def ads_tasks_view(request):
    """
    Отображает страницу со списком всех рекламных задач.

    Выполняет загрузку рекламных заказов с привязанными целями и ботами,
    формирует удобную структуру данных для шаблона.
    """
    try:
        tasks = (
            AdsOrder.objects
            .select_related("target", "bot")
            .order_by("-ordered_at")
        )
    except Exception as exc:
        log.exception("Ошибка при загрузке списка рекламных задач")
        return render(
            request,
            "admin_panel/plugins/ads_post/tasks.html",
            {
                "api_error": str(exc),
                "tasks": [],
            },
        )

    task_groups = []
    for task in tasks:
        task_groups.append({
            "id": task.id,
            "is_active": task.is_active,
            "name": task.name,
            "target": getattr(task.target, "name", "—"),
            "publish_at": task.publish_at,
            "is_paid": task.is_paid,
            "pinned_at": task.pinned_at,
            "unpinned_at": task.unpinned_at,
            "published_at": task.published_at,
            "deleted_at": task.deleted_at,
            "time_now": datetime.fromtimestamp(time.time()),
        })

    return render(
        request,
        "admin_panel/plugins/ads_post/tasks.html",
        {"task_groups": task_groups},
    )


@login_required
@require_http_methods(["GET", "POST"])
def ads_task_add(request):
    """
    Создание новой рекламной задачи.
    """
    if request.method == "POST":
        return _handle_ads_form(request)

    return _render_ads_form(
        request,
        task=None,
        template_name="admin_panel/plugins/ads_post/task_create.html",
    )


@login_required
@require_http_methods(["GET", "POST"])
def ads_task_edit(request, task_id: int):
    """
    Редактирование существующей рекламной задачи.
    """
    task = get_object_or_404(AdsOrder, pk=task_id)

    if request.method == "POST":
        return _handle_ads_form(request, task)

    planned_pin_end = (
        task.publish_at + timezone.timedelta(hours=1)
        if task.publish_at else None
    )
    planned_feed_end = (
        task.publish_at + timezone.timedelta(hours=24)
        if task.publish_at else None
    )

    entities = MainEntity.objects.all().order_by("name")

    return _render_ads_form(
        request,
        task=task,
        template_name="admin_panel/plugins/ads_post/task_edit.html",
        extra_context={
            "planned_pin_end": planned_pin_end,
            "planned_feed_end": planned_feed_end,
            "entities": entities,
            "time_now": datetime.fromtimestamp(time.time()),
        },
    )


def _handle_ads_form(request, task=None):
    """
    Обрабатывает сохранение формы рекламной задачи.

    Используется как для создания новой задачи, так и для обновления
    существующей. Все операции выполняются в атомарной транзакции.
    """
    try:
        with transaction.atomic():
            post = request.POST
            files = request.FILES

            name = post.get("name", "").strip()
            customer_status = post.get("customer_status", "new")
            customer_telegram = post.get("customer_telegram", "").strip()
            customer_fullname = post.get("customer_fullname", "").strip()
            notify_customer = bool(post.get("notify_customer"))
            notify_admin = bool(post.get("notify_admin"))
            is_paid = bool(post.get("is_paid"))
            is_active = bool(post.get("is_active"))
            post_link = post.get("post_link", "").strip()

            source_link = post.get("source_link", "").strip()
            source_telegram_id = post.get("source_telegram_id", "").strip()
            source_name = post.get("source_name", "").strip()
            source_description = post.get("source_description", "").strip()
            source_photo = files.get("source_photo")

            target_id = post.get("target_id")
            if not target_id:
                messages.error(request, "Не выбрана цель размещения рекламы")
                return redirect(request.path)

            try:
                target = MainEntity.objects.get(pk=target_id)
            except MainEntity.DoesNotExist:
                messages.error(request, "Выбранная цель не найдена")
                return redirect(request.path)

            bot = BotSession.objects.filter(is_active=True).last()
            if not bot:
                messages.error(request, "Нет активных ботов для публикации")
                return redirect(request.path)

            try:
                ordered_date = post.get("ordered_date", "").strip()
                ordered_time = post.get("ordered_time", "").strip()

                if ordered_date and ordered_time:
                    ordered_at = timezone.make_aware(
                        timezone.datetime.strptime(
                            f"{ordered_date} {ordered_time}",
                            "%Y-%m-%d %H:%M",
                        )
                    )
                else:
                    ordered_at = datetime.fromtimestamp(time.time())

                publish_date = post.get("publish_date", "").strip()
                publish_time = post.get("publish_time", "").strip()

                if not publish_date or not publish_time:
                    messages.error(request, "Не указана дата и время публикации")
                    return redirect(request.path)

                publish_at = timezone.make_aware(
                    timezone.datetime.strptime(
                        f"{publish_date} {publish_time}",
                        "%Y-%m-%d %H:%M",
                    )
                )
            except ValueError as exc:
                messages.error(request, f"Ошибка формата даты: {exc}")
                return redirect(request.path)

            if not task:
                task = AdsOrder()
                messages.success(request, "Рекламная задача успешно создана")
            else:
                messages.success(request, "Изменения успешно сохранены")

            task.name = name
            task.customer_status = customer_status
            task.customer_telegram = customer_telegram
            task.customer_fullname = customer_fullname
            task.notify_customer = notify_customer
            task.notify_admin = notify_admin
            task.target = target
            task.bot = bot
            task.ordered_at = ordered_at
            task.publish_at = publish_at
            task.is_paid = is_paid
            task.is_active = is_active
            task.post_link = post_link
            task.save()

            source_data = {
                "link": source_link,
                "telegram_id": int(source_telegram_id)
                if source_telegram_id else None,
                "name": source_name,
                "description": source_description,
                "entity_type": "channel",
            }

            task.update_source_data(source_data, source_photo)

    except Exception as exc:
        log.exception("Ошибка при сохранении рекламной задачи")
        messages.error(request, f"Ошибка сохранения: {exc}")
        return redirect(request.path)

    return redirect(reverse("admin_panel:ads_tasks_view"))


def _render_ads_form(request, task, template_name, extra_context=None):
    """
    Формирует и отображает страницу добавления или редактирования задачи.
    """
    entities = MainEntity.objects.all().order_by("name")

    source_data = None
    if task and hasattr(task, "source") and task.source:
        source_data = task.source

    context = {
        "task": task,
        "source": source_data,
        "entities": entities,
        "is_edit": bool(task),
        "time_now": datetime.fromtimestamp(time.time()),
    }

    if extra_context:
        context.update(extra_context)

    return render(request, template_name, context)


@login_required
@require_http_methods(["POST"])
def ads_task_delete(request, task_id: int):
    """
    Удаляет рекламную задачу.
    """
    try:
        task = get_object_or_404(AdsOrder, pk=task_id)
        task.delete()
        messages.success(request, f"Рекламная задача #{task_id} удалена")
    except Exception as exc:
        log.exception("Ошибка при удалении рекламной задачи")
        return HttpResponseBadRequest(f"Ошибка удаления: {exc}")

    return redirect("admin_panel:ads_tasks_view")
