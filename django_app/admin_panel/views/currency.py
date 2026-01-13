# admin_panel/views/currency.py

from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.contrib import messages
from django.utils import timezone
from django.http import HttpResponseRedirect
from django.views.decorators.csrf import csrf_protect
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator

from api.models import CurrencyLocation, CurrencyPair, CurrencyGlobals, MainEntity
from admin_panel.models import Country
from telegram.models import BotSession


@login_required
def currency_locations_view(request):
    """
    Страница списка всех локаций курса валют.
    """
    locations = CurrencyLocation.objects.select_related(
        "country", "bot", "main_chat"
    ).order_by("-created_at")
    
    # Получаем глобальные настройки (создаем если нет)
    globals_obj, _ = CurrencyGlobals.objects.get_or_create(
        id=1,
        defaults={
            "is_active": True,
            "publication_time": "08:00:00",
        }
    )
    
    # Обработка сохранения глобальных настроек
    if request.method == "POST" and "save_globals" in request.POST:
        globals_obj.publication_time = request.POST.get("publication_time", "08:00:00")
        globals_obj.pin_main_chat = int(request.POST.get("pin_main", 0))
        globals_obj.pin_safe_exchange = int(request.POST.get("pin_safe", 0))
        
        if "cover" in request.FILES:
            globals_obj.cover = request.FILES["cover"]
        
        globals_obj.save()
        messages.success(request, "Глобальные настройки сохранены")
        return redirect("admin_panel:currency_list")
    
    # Пагинация
    paginator = Paginator(locations, 20)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    
    context = {
        "locations": page_obj,
        "page_obj": page_obj,
        "globals_obj": globals_obj,
        "page_title": "Курс валют",
        "page_size": 20,
    }
    
    return render(request, "admin_panel/plugins/currency/list.html", context)


@login_required
def currency_location_add(request):
    """
    Создание новой локации курса валют.
    """
    return _currency_location_edit_common(request, location_id=None)


@login_required
def currency_location_edit(request, location_id: int):
    """
    Редактирование существующей локации курса валют.
    """
    return _currency_location_edit_common(request, location_id)


@csrf_protect
@login_required
def _currency_location_edit_common(request, location_id=None):
    """
    Общая функция для создания и редактирования локаций курса валют.
    """
    location = get_object_or_404(CurrencyLocation, id=location_id) if location_id else None
    entities = MainEntity.objects.order_by("name")
    bots = BotSession.objects.filter(is_active=True)
    countries = Country.objects.all()
    
    # Получаем пары для редактирования
    pairs = list(location.pairs.all()) if location else []

    # Дополняем до 5 пар для формы
    while len(pairs) < 5:
        pairs.append(CurrencyPair())

        
    context = {
        "location": location,
        "entities": entities,
        "bots": bots,
        "countries": countries,
        "pairs": pairs[:5],  # Максимум 5 пар
        "page_title": (
            "Курс валют"
            + (f" (Редактирование локации #{location.id})" if location else " (Создание локации)")
        ),
    }
    
    if request.method == "POST":
        data = request.POST
        
        try:
            # Получение выбранных объектов
            bot = get_object_or_404(BotSession, id=data.get("bot")) if data.get("bot") else None
            main_chat = get_object_or_404(MainEntity, id=data.get("main_chat")) if data.get("main_chat") else None
            country = get_object_or_404(Country, id=data.get("country")) if data.get("country") else None
            safe_exchange = (
                get_object_or_404(MainEntity, id=data.get("safe_exchange"))
                if data.get("safe_exchange")
                else None
            )
            
            if not bot or not main_chat:
                messages.error(request, "Необходимо выбрать бота и основной чат")
                return render(request, "admin_panel/plugins/currency/edit.html", context)
            
            # Создание или обновление локации
            if location is None:
                location = CurrencyLocation.objects.create(
                    name=data.get("name", ""),
                    hashtag=data.get("hashtag", ""),
                    emoji=data.get("emoji", ""),
                    country=country,
                    bot=bot,
                    main_chat=main_chat,
                    safe_exchange=safe_exchange,
                    google_rate_url=data.get("google_rate_url", ""),
                    xe_rate_url=data.get("xe_rate_url", ""),
                    bank_1_url=data.get("bank_1_url", ""),
                    bank_2_url=data.get("bank_2_url", ""),
                    bank_3_url=data.get("bank_3_url", ""),
                    atm_url=data.get("atm_url", ""),
                    magic_country_url=data.get("magic_country_url", ""),
                    is_active=data.get("is_active") == "on",
                )
                messages.success(request, f"Создана новая локация #{location.id}")
            else:
                location.name = data.get("name", location.name)
                location.hashtag = data.get("hashtag", location.hashtag)
                location.emoji = data.get("emoji", location.emoji)
                location.country = country
                location.bot = bot
                location.main_chat = main_chat
                location.safe_exchange = safe_exchange
                location.google_rate_url = data.get("google_rate_url", location.google_rate_url)
                location.xe_rate_url = data.get("xe_rate_url", location.xe_rate_url)
                location.bank_1_url = data.get("bank_1_url", location.bank_1_url)
                location.bank_2_url = data.get("bank_2_url", location.bank_2_url)
                location.bank_3_url = data.get("bank_3_url", location.bank_3_url)
                location.atm_url = data.get("atm_url", location.atm_url)
                location.magic_country_url = data.get("magic_country_url", location.magic_country_url)
                location.is_active = data.get("is_active") == "on"
                location.updated_at = timezone.now()
                location.save()
                messages.success(request, f"Изменения в локации #{location.id} сохранены")
            
            # Обработка валютных пар
            # Удаляем старые пары
            if location_id:
                CurrencyPair.objects.filter(location=location).delete()
            
            # Создаем новые пары
            for i in range(1, 6):  # Максимум 5 пар
                from_code = data.get(f"pair_{i}_from", "").strip().upper()
                to_code = data.get(f"pair_{i}_to", "").strip().upper()
                
                if from_code and to_code:
                    CurrencyPair.objects.create(
                        location=location,
                        from_code=from_code,
                        to_code=to_code,
                        order=i,
                        is_active=True,
                    )
            
            return HttpResponseRedirect(reverse("admin_panel:currency_list"))
            
        except Exception as e:
            messages.error(request, f"Ошибка при сохранении: {e}")
            return render(request, "admin_panel/plugins/currency/edit.html", context)
    
    return render(request, "admin_panel/plugins/currency/edit.html", context)


@csrf_protect
@login_required
def currency_location_delete(request, location_id: int):
    """
    Удаление локации курса валют.
    """
    location = get_object_or_404(CurrencyLocation, id=location_id)
    
    if request.method == "POST":
        location.delete()
        messages.success(request, f"Локация #{location_id} удалена.")
        return HttpResponseRedirect(reverse("admin_panel:currency_list"))
    
    return render(request, "admin_panel/confirm_delete.html", {
        "object": location,
        "cancel_url": reverse("admin_panel:currency_list"),
        "page_title": f"Удаление локации #{location.id}",
    })

