# admin_panel/views/entity_post.py

import logging
from django.shortcuts import render, redirect, reverse, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.http import HttpResponseBadRequest
from django.contrib import messages

from api.models import EntityPostTask, ChannelTaskGroup, TaskTime, MainEntity
from telegram.models import BotSession

log = logging.getLogger(__name__)

# ----- Константы -----
WEEKDAYS = {"0": "Пн", "1": "Вт", "2": "Ср", "3": "Чт", "4": "Пт", "5": "Сб", "6": "Вс"}


# ----- Вспомогательные -----
def parse_days(days_list):
    """
    Преобразует список дней недели из формы в список чисел.
    Поддерживает 'all' → 0..6
    """
    if not days_list:
        return []
    if "all" in days_list:
        return list(range(7))
    return [int(d) for d in days_list if d.isdigit()]


# ========================
#   СПИСОК ГРУПП ЗАДАЧ
# ========================
@login_required
def entity_post_tasks_view(request):
    """
    Список групп задач (ChannelTaskGroup) с подзадачами.
    """
    groups = (
        ChannelTaskGroup.objects
        .prefetch_related("subtasks__bot", "subtasks__source", "subtasks__target", "subtasks__times")
        .all()
    )

    task_groups = []
    for g in groups:
        if not g.subtasks.exists():
            continue

        base = g.subtasks.first()
        targets = [
            {"id": t.target_id, "name": t.target.name if t.target else "—", "is_active": t.is_active}
            for t in g.subtasks.all() if t.target
        ]

        time_val = None
        if base.times.exists():
            raw_sec = base.times.first().seconds_from_day_start
            first_target = base.target
            delta = getattr(first_target.country, "time_zone_delta", 0) if (first_target and first_target.country) else 0
            time_val = int((raw_sec + delta * 3600) % 86400)

        task_groups.append({
            "id": g.id,
            "bot": base.bot,
            "source": base.source,
            "is_global_active": all(t.is_global_active for t in g.subtasks.all()),
            "targets_count": len(targets),
            "targets": targets,
            "days": [t.weekday for t in base.times.all()],
            "time": time_val,
            "choice_mode": base.choice_mode,
            "after_publish": base.after_publish,
        })

    return render(request, "admin_panel/plugins/entity_post/tasks.html", {
        "task_groups": task_groups,
        "weekdays": WEEKDAYS,
    })


# ========================
#   СОЗДАНИЕ
# ========================
@login_required
@require_http_methods(["GET", "POST"])
def entity_post_task_create(request):
    if request.method == "POST":
        try:
            bot = BotSession.objects.last()
            if not bot:
                return HttpResponseBadRequest("Нет доступных ботов")

            source_id = request.POST.get("source_id")
            choice_mode = request.POST.get("choice_mode", "random")
            after_publish = request.POST.get("after_publish", "cycle")
            days = parse_days(request.POST.getlist("days"))
            hours = int(request.POST.get("hours", 12))
            minutes = int(request.POST.get("minutes", 0))
            sec_local = hours * 3600 + minutes * 60
            target_ids = request.POST.getlist("target_ids")
            is_global_active = request.POST.get("is_global_active") == "1"

            if not source_id or not target_ids:
                return HttpResponseBadRequest("Не хватает обязательных полей")

            group = ChannelTaskGroup.objects.create()

            for idx, tid in enumerate(target_ids):
                is_active = request.POST.get(f"is_active_{idx}") == "1"
                target_entity = MainEntity.objects.filter(pk=tid).select_related("country").first()
                if not target_entity:
                    continue

                delta = getattr(target_entity.country, "time_zone_delta", 0) if target_entity.country else 0
                sec = int((sec_local - delta * 3600) % 86400)

                task = EntityPostTask.objects.create(
                    bot=bot,
                    group=group,
                    choice_mode=choice_mode,
                    after_publish=after_publish,
                    source=MainEntity.objects.filter(pk=source_id).first(),
                    target=target_entity,
                    is_active=is_active,
                    is_global_active=is_global_active,
                )

                for d in days:
                    TaskTime.objects.create(task=task, weekday=d, seconds_from_day_start=sec)

            messages.success(request, f"Создана группа #{group.id}")
            return redirect("admin_panel:entity_post_tasks_view")

        except Exception as e:
            log.exception("Ошибка при создании группы задач")
            return HttpResponseBadRequest(f"Ошибка: {e}")

    entities = MainEntity.objects.all().order_by("order", "name")
    return render(request, "admin_panel/plugins/entity_post/task_create.html", {
        "weekdays": WEEKDAYS,
        "entities": entities
    })


# ========================
#   РЕДАКТИРОВАНИЕ
# ========================
@login_required
@require_http_methods(["GET", "POST"])
def entity_post_task_edit(request, group_id: int):
    group = get_object_or_404(ChannelTaskGroup, pk=group_id)
    tasks = list(group.subtasks.select_related("target", "source"))

    if not tasks:
        return HttpResponseBadRequest("У группы нет подзадач")

    base = tasks[0]
    times = TaskTime.objects.filter(task=base).order_by("weekday", "seconds_from_day_start")
    days = [str(t.weekday) for t in times]

    time_hours, time_minutes = 12, 0
    if times.exists():
        raw_sec = times.first().seconds_from_day_start
        g = base.target
        delta = getattr(g.country, "time_zone_delta", 0) if (g and g.country) else 0
        local_sec = int((raw_sec + delta * 3600) % 86400)
        time_hours, time_minutes = local_sec // 3600, (local_sec % 3600) // 60

    entities = MainEntity.objects.all().order_by("order", "name")

    context = {
        "tasks": tasks,
        "weekdays": WEEKDAYS,
        "entities": entities,
        "days": days,
        "time_hours": time_hours,
        "time_minutes": time_minutes,
    }

    if request.method == "POST":
        try:
            bot = BotSession.objects.last()
            if not bot:
                return HttpResponseBadRequest("Нет доступных ботов")

            source_id = request.POST.get("source_id")
            choice_mode = request.POST.get("choice_mode", "random")
            after_publish = request.POST.get("after_publish", "cycle")
            is_global_active = request.POST.get("is_global_active") == "1"
            EntityPostTask.objects.filter(group=group).update(is_global_active=is_global_active)
            days = parse_days(request.POST.getlist("days"))

            def _as_int(val, default):
                try:
                    return int(val)
                except (TypeError, ValueError):
                    return default

            hours = _as_int(request.POST.get("hours"), 12)
            minutes = _as_int(request.POST.get("minutes"), 0)
            sec_local = hours * 3600 + minutes * 60

            deleted_raw = request.POST.get("deleted_targets", "")
            ids_to_delete = [int(i) for i in deleted_raw.split(",") if i.strip().isdigit()]
            if ids_to_delete:
                EntityPostTask.objects.filter(group=group, id__in=ids_to_delete).delete()

            target_ids_raw = request.POST.getlist("target_ids")
            task_ids_raw = request.POST.getlist("task_ids")
            is_active_vals = [request.POST.get(f"is_active_{i}") for i in range(len(target_ids_raw))]

            if not source_id or not target_ids_raw:
                return HttpResponseBadRequest("Не хватает обязательных полей")

            submitted = {}
            for idx, tid in enumerate(target_ids_raw):
                tid = int(tid)
                task_id = task_ids_raw[idx] if idx < len(task_ids_raw) else None
                is_active_local = (is_active_vals[idx] == "1")
                submitted[task_id or f"new-{idx}"] = (tid, is_active_local)

            for t in EntityPostTask.objects.filter(group=group).order_by("id"):
                if str(t.id) not in submitted:
                    continue
                tid, is_active_local = submitted[str(t.id)]
                target_entity = MainEntity.objects.filter(pk=tid).select_related("country").first()
                delta = getattr(target_entity.country, "time_zone_delta", 0) if (target_entity and target_entity.country) else 0
                sec_stored = int((sec_local - delta * 3600) % 86400)

                t.choice_mode = choice_mode
                t.after_publish = after_publish
                t.source = MainEntity.objects.filter(pk=source_id).first()
                t.target = target_entity
                t.is_active = is_active_local
                t.is_global_active = is_global_active
                t.save()

                TaskTime.objects.filter(task=t).delete()
                for d in days:
                    TaskTime.objects.create(task=t, weekday=d, seconds_from_day_start=sec_stored)

            for key, (tid, is_active_local) in submitted.items():
                if not key.startswith("new-"):
                    continue
                target_entity = MainEntity.objects.filter(pk=tid).select_related("country").first()
                delta = getattr(target_entity.country, "time_zone_delta", 0) if (target_entity and target_entity.country) else 0
                sec_stored = int((sec_local - delta * 3600) % 86400)

                new_task = EntityPostTask.objects.create(
                    group=group,
                    bot=bot,
                    choice_mode=choice_mode,
                    after_publish=after_publish,
                    source=MainEntity.objects.filter(pk=source_id).first(),
                    target=target_entity,
                    is_active=is_active_local,
                    is_global_active=is_global_active,
                )

                for d in days:
                    TaskTime.objects.create(task=new_task, weekday=d, seconds_from_day_start=sec_stored)

            messages.success(request, f"Группа #{group.id} обновлена")
            return redirect(reverse("admin_panel:entity_post_tasks_view"))

        except Exception as e:
            log.exception("Ошибка редактирования группы задач")
            return HttpResponseBadRequest(f"Ошибка: {e}")

    return render(request, "admin_panel/plugins/entity_post/task_edit.html", context)


# ========================
#   УДАЛЕНИЕ
# ========================
@login_required
@require_http_methods(["POST"])
def entity_post_task_delete(request, group_id: int):
    try:
        group = get_object_or_404(ChannelTaskGroup, pk=group_id)
        count = group.subtasks.count()
        group.delete()
        messages.success(request, f"Удалена группа #{group_id} ({count} подзадач)")
        return redirect("admin_panel:entity_post_tasks_view")
    except Exception as e:
        log.exception("Ошибка удаления группы задач")
        return HttpResponseBadRequest(f"Ошибка: {e}")
