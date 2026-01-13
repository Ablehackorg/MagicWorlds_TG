from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Optional

from django.conf import settings

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect

from admin_panel.models import Country
from api.models import MainEntity, WeatherCity, WeatherPublishLog, WeatherTask, WeatherTaskTarget
from api.services.weatherbot_service import build_message, fetch_4days, geocode_city, pick_kind


def _entity_label(e: MainEntity) -> str:
    name = getattr(e, "name", None) or getattr(e, "title", None) or getattr(e, "username", None) or f"Entity#{e.id}"
    tg_id = getattr(e, "telegram_id", None) or getattr(e, "chat_id", None) or ""
    etype = getattr(e, "type", None) or getattr(e, "kind", None) or getattr(e, "entity_type", None) or ""
    parts = [str(name)]
    if etype:
        parts.append(f"({etype})")
    if tg_id:
        parts.append(f"[{tg_id}]")
    return " ".join(parts)


def _daterange(day):
    start = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time()))
    end = start + timedelta(days=1)
    return start, end


def _logs_for_day(task: WeatherTask, day):
    start, end = _daterange(day)
    return (
        task.logs.filter(created_at__gte=start, created_at__lt=end)
        .select_related("target")
        .order_by("-created_at")
    )


def _resolve_sender():
    from tg_parser import client as tg_client_mod  # type: ignore

    get_client = getattr(tg_client_mod, "get_client", None)
    run_in_client = getattr(tg_client_mod, "run_in_client", None)
    if not get_client or not run_in_client:
        raise RuntimeError("tg_parser.client должен иметь get_client() и run_in_client(coro)")
    return get_client, run_in_client


def _gif_path(task: WeatherTask, code: int, *, is_night: bool) -> Optional[str]:
    """Выбираем GIF-фон.

    Приоритет:
      1) если включён summer_only_backgrounds: берём загруженный gif_sunny, иначе дефолт sunny
      2) если use_default_backgrounds=True: берём дефолтные фоны из static/weatherbot/backgrounds/(day|night)
      3) иначе — загруженные в задаче GIF (солнечно/пасмурно/осадки)
    """

    def _default_path(code_: int) -> str:
        k_ = pick_kind(code_)
        folder = "night" if is_night else "day"
        filename = {"sunny": "sunny.gif", "cloudy": "cloudy.gif", "precip": "precip.gif"}[k_]
        p = Path(settings.BASE_DIR) / "static" / "weatherbot" / "backgrounds" / folder / filename
        return str(p)

    if getattr(task, "summer_only_backgrounds", False):
        if task.gif_sunny:
            return task.gif_sunny.path
        if getattr(task, "use_default_backgrounds", True):
            return _default_path(0)
        return None

    if getattr(task, "use_default_backgrounds", True):
        return _default_path(code)

    k = pick_kind(code)
    if k == "sunny" and task.gif_sunny:
        return task.gif_sunny.path
    if k == "cloudy" and task.gif_cloudy:
        return task.gif_cloudy.path
    if k == "precip" and task.gif_precip:
        return task.gif_precip.path
    return None


@login_required
def weatherbot_tasks_view(request):
    tasks = (
        WeatherTask.objects.select_related("city", "city__country")
        .annotate(targets_count=Count("targets"))
        .order_by("-id")
    )

    today = timezone.localdate()
    yesterday = today - timedelta(days=1)

    rows = []
    for t in tasks:
        err_today = t.logs.filter(created_at__date=today, is_ok=False).count()
        err_yest = t.logs.filter(created_at__date=yesterday, is_ok=False).count()
        rows.append(
            {
                "task": t,
                "targets_count": getattr(t, "targets_count", 0),
                "err_today": err_today,
                "err_yesterday": err_yest,
            }
        )

    return render(
        request,
        "admin_panel/plugins/weatherbot/list.html",
        {
            "page_title": "Бот погоды",
            "rows": rows,
        },
    )


@login_required
def weatherbot_task_add(request):
    return _weatherbot_edit_common(request, task_id=None)


@login_required
def weatherbot_task_edit(request, task_id: int):
    return _weatherbot_edit_common(request, task_id=task_id)


@login_required
def weatherbot_cities_api(request, country_id: int):
    cities = WeatherCity.objects.filter(country_id=country_id).order_by("name").values("id", "name")
    return JsonResponse({"cities": list(cities)})


@csrf_protect
@login_required
def _weatherbot_edit_common(request, task_id: Optional[int]):
    task = get_object_or_404(WeatherTask, id=task_id) if task_id else None

    countries = Country.objects.all().order_by("name")
    entities_qs = MainEntity.objects.all().order_by("id")
    entities = [{"id": e.id, "label": _entity_label(e)} for e in entities_qs]

    selected_targets = []
    if task:
        selected_targets = [
            {"id": t.id, "label": _entity_label(t.entity), "entity_id": t.entity_id}
            for t in task.targets.select_related("entity").all()
        ]

    today = timezone.localdate()
    logs_today = _logs_for_day(task, today) if task else []
    logs_yesterday = _logs_for_day(task, today - timedelta(days=1)) if task else []
    logs_prev = _logs_for_day(task, today - timedelta(days=2)) if task else []

    def render_page():
        return render(
            request,
            "admin_panel/plugins/weatherbot/edit.html",
            {
                "page_title": "Бот погоды",
                "task": task,
                "countries": countries,
                "entities": entities,
                "selected_targets": selected_targets,
                "logs_today": logs_today,
                "logs_yesterday": logs_yesterday,
                "logs_prev": logs_prev,
            },
        )

    if request.method != "POST":
        return render_page()

    data = request.POST

    # Удалить
    if data.get("delete_task") == "1":
        if not task:
            messages.error(request, "Нечего удалять")
            return redirect("admin_panel:weatherbot_tasks_view")
        task.delete()
        messages.success(request, f"Задача #{task_id} удалена")
        return redirect("admin_panel:weatherbot_tasks_view")

    # Отправить сейчас
    if data.get("send_now") == "1":
        if not task:
            messages.error(request, "Сначала сохраните задачу")
            return render_page()
        try:
            if task.city.latitude is None or task.city.longitude is None:
                geo = geocode_city(task.city.name)
                if not geo:
                    raise RuntimeError("Не удалось геокодировать город")
                task.city.latitude = geo["latitude"]
                task.city.longitude = geo["longitude"]
                task.city.save(update_fields=["latitude", "longitude"])

            days = fetch_4days(task.city.latitude, task.city.longitude)
            text = build_message(task.city.country.name, task.city.name, days)
            is_night = timezone.localtime().hour >= 18
            file_path = _gif_path(task, days[0].code, is_night=is_night)

            get_client, run_in_client = _resolve_sender()

            async def _coro():
                client = get_client()
                for tgt in task.targets.select_related("entity").all():
                    chat_id = getattr(tgt.entity, "telegram_id", None) or getattr(tgt.entity, "chat_id", None)
                    if not chat_id:
                        raise RuntimeError(f"У цели нет telegram_id/chat_id (entity_id={tgt.entity_id})")
                    if file_path:
                        await client.send_file(chat_id, file_path, caption=text, parse_mode="html")
                    else:
                        await client.send_message(chat_id, text, parse_mode="html")

            run_in_client(_coro())
            WeatherPublishLog.objects.create(task=task, kind="manual", is_ok=True)
            messages.success(request, "Отправлено ✅")
        except Exception as e:
            WeatherPublishLog.objects.create(task=task, kind="manual", is_ok=False, error=str(e))
            messages.error(request, f"Ошибка отправки: {e}")

        return redirect("admin_panel:weatherbot_task_edit", task_id=task.id)

    # Добавить цель
    add_entity_id = (data.get("add_entity_id") or "").strip()
    if add_entity_id and task:
        try:
            ent = get_object_or_404(MainEntity, id=int(add_entity_id))
            WeatherTaskTarget.objects.get_or_create(task=task, entity=ent)
            messages.success(request, "Цель добавлена")
        except Exception as e:
            messages.error(request, f"Не удалось добавить цель: {e}")
        return redirect("admin_panel:weatherbot_task_edit", task_id=task.id)

    # Удалить цель
    remove_target_id = (data.get("remove_target_id") or "").strip()
    if remove_target_id and task:
        try:
            WeatherTaskTarget.objects.filter(task=task, id=int(remove_target_id)).delete()
            messages.success(request, "Цель удалена")
        except Exception as e:
            messages.error(request, f"Не удалось удалить цель: {e}")
        return redirect("admin_panel:weatherbot_task_edit", task_id=task.id)

    # Сохранение задачи
    try:
        country_id = data.get("country")
        city_id = data.get("city")
        if not country_id or not city_id:
            messages.error(request, "Выберите страну и город")
            return render_page()

        city = get_object_or_404(WeatherCity, id=int(city_id), country_id=int(country_id))

        is_enabled = data.get("is_enabled") == "on"
        morning_time = data.get("morning_time") or "10:30"
        evening_time = data.get("evening_time") or "20:30"
        summer_only = data.get("summer_only") == "on"
        use_default_backgrounds = data.get("use_default_backgrounds") == "on"
        use_default_icons = data.get("use_default_icons") == "on"

        if task is None:
            task = WeatherTask.objects.create(
                city=city,
                is_enabled=is_enabled,
                morning_time=morning_time,
                evening_time=evening_time,
                summer_only_backgrounds=summer_only,
                use_default_backgrounds=use_default_backgrounds,
                use_default_icons=use_default_icons,
            )
            messages.success(request, f"Создана задача #{task.id}")
        else:
            task.city = city
            task.is_enabled = is_enabled
            task.morning_time = morning_time
            task.evening_time = evening_time
            task.summer_only_backgrounds = summer_only
            task.use_default_backgrounds = use_default_backgrounds
            task.use_default_icons = use_default_icons

            if "gif_sunny" in request.FILES:
                task.gif_sunny = request.FILES["gif_sunny"]
            if "gif_cloudy" in request.FILES:
                task.gif_cloudy = request.FILES["gif_cloudy"]
            if "gif_precip" in request.FILES:
                task.gif_precip = request.FILES["gif_precip"]

            task.save()
            messages.success(request, f"Задача #{task.id} сохранена")

        return redirect("admin_panel:weatherbot_task_edit", task_id=task.id)

    except Exception as e:
        messages.error(request, f"Ошибка сохранения: {e}")
        return render_page()
