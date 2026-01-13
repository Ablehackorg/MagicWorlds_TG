# admin_panel/views/booster_settings.py

import logging
import os

import requests
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.timezone import now
from django.views.decorators.http import require_http_methods, require_POST

from api.models import (
    BoosterSettings,
    BoosterTariff,
    OldViewsTask,
    SubscribersBoostTask,
    ViewBoostTask,
)

logger = logging.getLogger(__name__)

# Proxy configuration for all requests to Twiboost API
PROXY_URL = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
PROXIES = (
    {"http": PROXY_URL, "https": PROXY_URL}
    if PROXY_URL
    else None
)


def _safe_twiboost_get(endpoint, api_key):
    """
    Performs a safe GET request to the Twiboost API.

    Tries available base URLs and returns a unified response tuple.

    Returns:
        tuple:
            - success (bool)
            - parsed JSON data or None
            - raw response excerpt (str)
            - HTTP status code (int)
    """
    base_urls = ["https://twiboost.com/api/v2"]

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
    }

    for base_url in base_urls:
        try:
            url = f"{base_url}?action={endpoint}&key={api_key}"
            logger.debug("[_safe_twiboost_get] request → %s", url)

            response = requests.get(
                url,
                headers=headers,
                timeout=10,
                verify=False,
                proxies=PROXIES,
            )

            if response.status_code == 200:
                try:
                    return (
                        True,
                        response.json(),
                        response.text[:1000],
                        response.status_code,
                    )
                except Exception as exc:
                    logger.warning(
                        "[_safe_twiboost_get] JSON parse error: %s",
                        exc,
                    )
        except Exception as exc:
            logger.warning(
                "[_safe_twiboost_get] request failed (%s): %s",
                base_url,
                exc,
            )

    return False, None, "mock", 500


@login_required
@require_http_methods(["GET", "POST"])
def booster_settings_view(request):
    """
    Displays and processes booster global settings and tariffs.

    Handles:
    - API credentials
    - module activation flags
    - tariff configuration
    """
    settings_obj = BoosterSettings.get_singleton()

    if request.method == "POST":
        settings_obj.url = (request.POST.get("url") or "").strip()
        settings_obj.api_key = (request.POST.get("api_key") or "").strip()
        settings_obj.is_active = request.POST.get("is_active") == "on"

        settings_obj.balance_alert_limit = int(
            request.POST.get("balance_alert_limit", 0)
        )
        settings_obj.is_balance_notify = (
            request.POST.get("is_balance_notify") == "on"
        )

        settings_obj.is_active_old_views = (
            request.POST.get("is_active_old_views") == "on"
        )
        settings_obj.is_active_new_views = (
            request.POST.get("is_active_new_views") == "on"
        )
        settings_obj.is_active_subscribers = (
            request.POST.get("is_active_subscribers") == "on"
        )

        settings_obj.save()

        _process_tariffs(request, settings_obj)

        messages.success(
            request,
            "Настройки бустера успешно сохранены",
        )
        return redirect("admin_panel:booster_settings")

    tariffs_by_module = {
        "old_views": settings_obj.tariffs.filter(
            module="old_views"
        ).order_by("id"),
        "new_views": settings_obj.tariffs.filter(
            module="new_views"
        ).order_by("id"),
        "subscribers": settings_obj.tariffs.filter(
            module="subscribers"
        ).order_by("id"),
    }

    old_views_channels = (
        OldViewsTask.objects
        .filter(is_active=True)
        .select_related("target")
        .values("target__id", "target__name", "is_active")
    )

    new_views_channels = (
        ViewBoostTask.objects
        .filter(is_active=True)
        .select_related("target")
        .values("target__id", "target__name", "is_active")
    )

    subscribers_channels = (
        SubscribersBoostTask.objects
        .filter(is_active=True)
        .select_related("target")
        .values("target__id", "target__name", "is_active")
    )

    active_channels = (
        old_views_channels
        .union(new_views_channels)
        .union(subscribers_channels)
    )

    api_ok = False
    if settings_obj.api_key:
        ok, data, _, _ = _safe_twiboost_get(
            "services",
            settings_obj.api_key,
        )
        api_ok = bool(ok and isinstance(data, list))

    return render(
        request,
        "admin_panel/booster_settings.html",
        {
            "settings": settings_obj,
            "api_ok": api_ok,
            "tariffs": tariffs_by_module,
            "channels": active_channels,
        },
    )


def _process_tariffs(request, settings_obj):
    """
    Parses and saves tariff configuration for all booster modules.

    Existing tariffs are fully replaced by submitted data.
    """
    settings_obj.tariffs.all().delete()

    modules = ["old_views", "new_views", "subscribers"]

    for module in modules:
        index = 1
        tariffs_data = []

        while True:
            service_id = request.POST.get(f"{module}_id_{index}")
            min_limit = request.POST.get(f"{module}_min_{index}")
            price = request.POST.get(f"{module}_tariff_{index}")

            if not service_id:
                break

            if service_id and min_limit and price:
                tariffs_data.append(
                    {
                        "service_id": int(service_id),
                        "min_limit": int(min_limit),
                        "price_per_1000": float(price),
                        "is_active": (
                            request.POST.get(
                                f"{module}_active_{index}"
                            ) == "1"
                        ),
                        "is_primary": (
                            request.POST.get(
                                f"{module}_primary_{index}"
                            ) == "1"
                        ),
                        "comment": request.POST.get(
                            f"{module}_comment_{index}",
                            "",
                        ),
                        "index": index,
                    }
                )
            index += 1

        for tariff in tariffs_data:
            try:
                BoosterTariff.objects.create(
                    booster=settings_obj,
                    module=module,
                    service_id=tariff["service_id"],
                    min_limit=tariff["min_limit"],
                    price_per_1000=tariff["price_per_1000"],
                    comment=tariff["comment"],
                    is_active=tariff["is_active"],
                    is_primary=tariff["is_primary"],
                )
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "Ошибка создания тарифа %s_%s: %s",
                    module,
                    tariff["index"],
                    exc,
                )

        _ensure_single_primary_per_module(settings_obj, module)


def _ensure_single_primary_per_module(settings_obj, module):
    """
    Ensures that exactly one primary tariff exists per module.
    """
    primary_tariffs = BoosterTariff.objects.filter(
        booster=settings_obj,
        module=module,
        is_primary=True,
    )

    if primary_tariffs.count() > 1:
        first_primary = primary_tariffs.first()
        primary_tariffs.exclude(
            pk=first_primary.pk
        ).update(is_primary=False)

    elif primary_tariffs.count() == 0:
        first_active = BoosterTariff.objects.filter(
            booster=settings_obj,
            module=module,
            is_active=True,
        ).first()
        if first_active:
            first_active.is_primary = True
            first_active.save()


@login_required
def booster_check_ajax(request):
    """
    AJAX endpoint for checking booster API status and balance.

    Modes:
    - status
    - balance
    - empty: performs both checks
    """
    settings_obj = BoosterSettings.get_singleton()
    api_key = settings_obj.api_key or ""
    mode = request.GET.get("mode", "")

    if not api_key:
        return JsonResponse(
            {
                "status": "error",
                "balance": 0,
                "last_balance_check": None,
                "error": "API ключ не установлен",
            }
        )

    response = {
        "status": "ok",
        "balance": None,
        "last_balance_check": None,
        "error": None,
    }

    try:
        if mode in ("", "status"):
            ok, data, raw, code = _safe_twiboost_get(
                "services",
                api_key,
            )
            if not (ok and isinstance(data, list)):
                response["status"] = "error"
                response["error"] = (
                    f"Ошибка API: код {code}, ответ: {raw}"
                )

        if mode in ("", "balance"):
            ok, data, raw, _ = _safe_twiboost_get(
                "balance",
                api_key,
            )
            if ok and isinstance(data, dict) and "balance" in data:
                settings_obj.balance = float(data["balance"])
                settings_obj.last_balance_check = now()
                settings_obj.save(
                    update_fields=[
                        "balance",
                        "last_balance_check",
                    ]
                )
                response["balance"] = settings_obj.balance
                response["last_balance_check"] = (
                    settings_obj.last_balance_check.strftime(
                        "%d.%m.%Y %H:%M"
                    )
                )
            else:
                response["status"] = "error"
                response["error"] = (
                    f"Не удалось получить баланс: {raw}"
                )

    except Exception as exc:
        logger.error(
            "[booster_check_ajax] unexpected error: %s",
            exc,
            exc_info=True,
        )
        response.update(
            {
                "status": "error",
                "error": f"Внутренняя ошибка: {exc}",
            }
        )

    return JsonResponse(response)


@login_required
@require_POST
def toggle_channel_status(request, channel_id):
    """
    Toggles activation state of an OldViewsTask by target channel ID.
    """
    try:
        task = OldViewsTask.objects.get(
            target_id=channel_id
        )
        task.is_active = not task.is_active
        task.save()

        return JsonResponse(
            {
                "success": True,
                "is_active": task.is_active,
            }
        )
    except OldViewsTask.DoesNotExist:
        return JsonResponse(
            {
                "success": False,
                "error": "Задача не найдена",
            },
            status=404,
        )


@login_required
def refresh_channels_list(request):
    """
    Placeholder endpoint for refreshing channel list.
    """
    return JsonResponse(
        {
            "success": True,
            "message": "Список каналов обновлен",
        }
    )
