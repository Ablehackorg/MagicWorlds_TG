# admin_panel/views/plugins.py

import logging
import docker
from typing import Dict, Any

from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponseBadRequest
from django.contrib.auth.decorators import login_required

from admin_panel.models import Plugin

log = logging.getLogger(__name__)

REQ_TIMEOUT = 5  # seconds


# ==================== Docker helpers ====================

def _docker():
    """Возвращает клиент Docker SDK."""
    return docker.from_env()

def _container_status(name: str | None) -> str:
    """
    Возвращает статус контейнера:
    - not_configured: если имя контейнера не задано
    - not_found: если контейнер не найден
    - иначе фактический статус ("running", "exited" и т.д.)
    """
    if not name:
        return "not_configured"
    try:
        c = _docker().containers.get(name)
        return c.status or "unknown"
    except Exception as e:
        log.debug("docker get(%s) failed: %s", name, e)
        return "not_found"

def _serialize(p: Plugin) -> Dict[str, Any]:
    """Преобразует объект Plugin в словарь для шаблона."""
    container = getattr(p, "container_name", None)
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description or "",
        "is_active": bool(p.is_active),
        "category": getattr(p, "category", "system") or "system",
        "container_name": container or "",
        "docker_status": _container_status(container),
        "slug": p.slug,
    }


# ==================== Views ====================

@login_required
def plugins_page(request):
    """
    Отображает список всех плагинов.
    Плагины группируются по категориям: working, system, theme.
    """
    plugins = Plugin.objects.all()
    categorized = {"working": [], "system": [], "theme": []}
    for p in plugins:
        d = _serialize(p)
        cat = d["category"] if d["category"] in categorized else "working"
        categorized[cat].append(d)

    return render(request, "admin_panel/plugins/plugins.html", {
        "working_plugins": categorized["working"],
        "system_plugins": categorized["system"],
        "theme_plugins": categorized["theme"],
    })


@login_required
def plugin_action(request, plugin_id: int, action: str):
    """
    Управление контейнером плагина:
    - start
    - stop
    - restart
    """
    p = get_object_or_404(Plugin, pk=plugin_id)
    container = getattr(p, "container_name", None)
    if not container:
        return HttpResponseBadRequest("Container is not configured for this plugin")

    client = _docker()
    try:
        c = client.containers.get(container)
    except Exception:
        c = None

    if action == "start":
        if not c:
            return HttpResponseBadRequest("Container not found")
        c.start()
        p.is_active = True
    elif action == "stop":
        if not c:
            return HttpResponseBadRequest("Container not found")
        c.stop()
        p.is_active = False
    elif action == "restart":
        if not c:
            return HttpResponseBadRequest("Container not found")
        c.restart()
        p.is_active = True
    else:
        return HttpResponseBadRequest("Unknown action")

    p.save()
    return redirect("admin_panel:plugins_page")


@login_required
def plugin_logs(request, plugin_id: int):
    """
    Отображает последние логи контейнера.
    GET-параметр tail определяет количество строк (по умолчанию 500).
    """
    from django.conf import settings
    p = get_object_or_404(Plugin, pk=plugin_id)
    container = getattr(p, "container_name", None)
    if not container:
        return HttpResponseBadRequest("Container is not configured for this plugin")

    tail = int(request.GET.get("tail", getattr(settings, "PLUGINS_DEFAULT_LOG_TAIL", 500)))
    try:
        c = _docker().containers.get(container)
        logs = c.logs(tail=tail).decode("utf-8", errors="replace")
    except Exception as e:
        logs = f"Failed to read logs: {e}"

    d = _serialize(p)
    return render(request, "admin_panel/plugins/plugin_logs.html", {
        "plugin": d,
        "tail": tail,
        "logs": logs,
    })
