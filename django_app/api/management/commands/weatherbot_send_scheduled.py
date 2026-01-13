from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from api.models import WeatherPublishLog, WeatherTask
from api.services.weatherbot_service import build_message, fetch_4days, geocode_city, pick_kind


def _resolve_sender():
    # Требование: реальная отправка через Telethon/существующий клиент
    from tg_parser import client as tg_client_mod  # type: ignore

    get_client = getattr(tg_client_mod, "get_client", None)
    run_in_client = getattr(tg_client_mod, "run_in_client", None)
    if not get_client or not run_in_client:
        raise RuntimeError("tg_parser.client должен иметь get_client() и run_in_client(coro)")
    return get_client, run_in_client


def _gif_path(task: WeatherTask, code: int, *, is_night: bool):
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

    # Вариант B: дефолтные фоны из проекта
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


class Command(BaseCommand):
    help = "WeatherBot: безопасно вызывать раз в минуту (плановая отправка утро/вечер)"

    def handle(self, *args, **options):
        get_client, run_in_client = _resolve_sender()

        now = timezone.localtime()
        today = now.date()

        tasks = (
            WeatherTask.objects.filter(is_enabled=True)
            .select_related("city", "city__country")
            .prefetch_related("targets", "targets__entity")
        )

        for task in tasks:
            try:
                # координаты
                if task.city.latitude is None or task.city.longitude is None:
                    geo = geocode_city(task.city.name)
                    if not geo:
                        WeatherPublishLog.objects.create(task=task, kind="manual", is_ok=False, error="Geocoding not found")
                        continue
                    task.city.latitude = geo["latitude"]
                    task.city.longitude = geo["longitude"]
                    task.city.save(update_fields=["latitude", "longitude"])

                # пора ли отправлять (без дублей)
                need_morning = (now.time() >= task.morning_time) and (
                    not task.last_morning_sent_at
                    or timezone.localtime(task.last_morning_sent_at).date() != today
                )
                need_evening = (now.time() >= task.evening_time) and (
                    not task.last_evening_sent_at
                    or timezone.localtime(task.last_evening_sent_at).date() != today
                )

                if not need_morning and not need_evening:
                    continue

                kind = "morning" if need_morning else "evening"

                days = fetch_4days(task.city.latitude, task.city.longitude)
                text = build_message(task.city.country.name, task.city.name, days)
                file_path = _gif_path(task, days[0].code, is_night=(kind == "evening"))

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
                WeatherPublishLog.objects.create(task=task, kind=kind, is_ok=True)

                with transaction.atomic():
                    if kind == "morning":
                        task.last_morning_sent_at = timezone.now()
                    else:
                        task.last_evening_sent_at = timezone.now()
                    task.save(update_fields=["last_morning_sent_at", "last_evening_sent_at"])

            except Exception as e:
                WeatherPublishLog.objects.create(task=task, kind="manual", is_ok=False, error=str(e))
                self.stderr.write(f"[WeatherBot] Task#{task.id} error: {e}")
