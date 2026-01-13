# admin_panel/views/directories.py

from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render
from admin_panel.models import Country, Category
from api.models import EntityPostTask
import math


# ==================== Pages ====================

@login_required
def directories_page(request):
    """
    Главная страница «Справочники».
    Служит точкой входа для перехода к спискам стран и категорий.
    """
    return render(request, "admin_panel/directories.html")


@login_required
def countries_page(request):
    """
    Отображение списка всех стран.
    Конвертирует временную зону в читаемый формат +HH:MM или -HH:MM.
    """
    countries = Country.objects.all()
    parsed = []

    for c in countries:
        delta = float(c.time_zone_delta or 0)
        sign = "+" if delta >= 0 else "-"
        abs_delta = abs(delta)
        hours = int(abs_delta)
        minutes = int(round((abs_delta - hours) * 60))
        if minutes >= 60:
            minutes = 45  # защита от ошибок float

        parsed.append({
            "id": c.id,
            "name": c.name,
            "time_zone_delta": delta,
            "sign": sign,
            "hours": hours,
            "minutes": minutes,
            "delta_str": f"{sign}{hours:02d}:{minutes:02d}"
        })

    return render(request, "admin_panel/countries.html", {"countries": parsed})


@login_required
def categories_page(request):
    """
    Отображение списка всех категорий.
    """
    categories = Category.objects.all()
    return render(request, "admin_panel/categories.html", {"categories": categories})


# ==================== Helpers ====================

def _parse_delta_from_request(request):
    """
    Получение временной зоны из POST-запроса.
    Формирует float на основе sign, hours, minutes.
    """
    sign = request.POST.get("tz_sign") or "+"
    try:
        hours = int(request.POST.get("tz_hours") or 0)
    except ValueError:
        hours = 0

    try:
        minutes = int(request.POST.get("tz_minutes") or 0)
    except ValueError:
        minutes = 0

    total = hours + minutes / 60
    if sign == "-":
        total = -total

    return total


def _delta_to_str(delta: float) -> str:
    """
    Конвертация float временной зоны в строку формата +HH:MM или -HH:MM.
    """
    sign = "+" if delta >= 0 else "-"
    abs_delta = abs(delta)
    hours = int(abs_delta)
    minutes = int(round((abs_delta - hours) * 60))
    if minutes >= 60:
        minutes = 45  # ограничение шага

    return f"{sign}{hours:02d}:{minutes:02d}"


# ==================== AJAX API: Country ====================

@login_required
@csrf_exempt
def country_add_ajax(request):
    """
    Создание новой страны через AJAX.
    Ожидает POST: name, tz_sign, tz_hours, tz_minutes.
    """
    if request.method == "POST":
        name = request.POST.get("name")
        delta = _parse_delta_from_request(request)

        if name:
            country = Country.objects.create(name=name, time_zone_delta=delta)
            return JsonResponse({
                "id": country.id,
                "name": country.name,
                "time_zone_delta": country.time_zone_delta,
                "delta_str": _delta_to_str(country.time_zone_delta)
            })

    return JsonResponse({"error": "bad request"}, status=400)


@login_required
@csrf_exempt
def country_update_ajax(request, pk):
    """
    Обновление страны по ID через AJAX.
    Ожидает POST: name, tz_sign, tz_hours, tz_minutes.
    """
    if request.method == "POST":
        try:
            country = Country.objects.get(pk=pk)
        except Country.DoesNotExist:
            return JsonResponse({"error": "not found"}, status=404)

        name = request.POST.get("name")
        if name:
            country.name = name

        country.time_zone_delta = _parse_delta_from_request(request)
        country.save()

        return JsonResponse({
            "id": country.id,
            "name": country.name,
            "time_zone_delta": country.time_zone_delta,
            "delta_str": _delta_to_str(country.time_zone_delta)
        })

    return JsonResponse({"error": "bad request"}, status=400)


@login_required
@csrf_exempt
def country_delete_ajax(request, pk):
    """
    Удаление страны по ID через AJAX.
    Возвращает {"deleted": True} при успешном удалении.
    """
    if request.method == "POST":
        try:
            Country.objects.get(pk=pk).delete()
            return JsonResponse({"deleted": True})
        except Country.DoesNotExist:
            return JsonResponse({"error": "not found"}, status=404)

    return JsonResponse({"error": "bad request"}, status=400)


# ==================== AJAX API: Category ====================

@login_required
@csrf_exempt
def category_add_ajax(request):
    """
    Создание новой категории через AJAX.
    Ожидает POST: name.
    """
    if request.method == "POST":
        name = request.POST.get("name")
        if name:
            category = Category.objects.create(name=name)
            return JsonResponse({"id": category.id, "name": category.name})

    return JsonResponse({"error": "bad request"}, status=400)


@login_required
@csrf_exempt
def category_update_ajax(request, pk):
    """
    Обновление категории по ID через AJAX.
    Ожидает POST: name.
    """
    if request.method == "POST":
        try:
            category = Category.objects.get(pk=pk)
        except Category.DoesNotExist:
            return JsonResponse({"error": "not found"}, status=404)

        name = request.POST.get("name")
        if name:
            category.name = name
            category.save()

        return JsonResponse({"id": category.id, "name": category.name})

    return JsonResponse({"error": "bad request"}, status=400)


@login_required
@csrf_exempt
def category_delete_ajax(request, pk):
    """
    Удаление категории по ID через AJAX.
    Возвращает {"deleted": True} при успешном удалении.
    """
    if request.method == "POST":
        try:
            Category.objects.get(pk=pk).delete()
            return JsonResponse({"deleted": True})
        except Category.DoesNotExist:
            return JsonResponse({"error": "not found"}, status=404)

    return JsonResponse({"error": "bad request"}, status=400)
