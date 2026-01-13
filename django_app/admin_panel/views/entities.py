# admin_panel/views/entities.py

import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone

from admin_panel.models import Country, Category
from api.models import MainEntity, EntityCategory
from .config import destination_types, owners

log = logging.getLogger(__name__)


def _resolve_name(model, val):
    """
    Получение объекта модели по id или name.
    Используется для привязки country/category из формы.
    Возвращает объект модели или None.
    """
    if not val:
        return None
    s = str(val).strip()
    if s.isdigit():
        try:
            return model.objects.get(id=int(s))
        except model.DoesNotExist:
            return None
    try:
        return model.objects.get(name=s)
    except model.DoesNotExist:
        return None


@login_required
def entities_page_view(request):
    """
    Отображение списка всех Telegram-сообществ (каналы и группы).
    Обновляет кэш количества задач для сущностей старше 14 дней.
    """
    entities = MainEntity.objects.select_related("country", "category").order_by("-id")
    now = timezone.now()

    for e in entities:
        if not e.cached_task_updated or (now - e.cached_task_updated).days > 14:
            e.refresh_task_count()

    return render(request, "admin_panel/entities.html", {"entities": entities})


@login_required
@require_http_methods(["GET", "POST"])
def entity_add_view(request):
    """
    Создание нового Telegram-сообщества.
    GET  → отображение формы добавления.
    POST → сохранение нового объекта MainEntity.
    """
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        link = (request.POST.get("link") or "").strip()
        tags = (request.POST.get("tags") or "").strip()
        tg_id_raw = (request.POST.get("telegram_id") or "").strip()
        country_val = request.POST.get("country") or request.POST.get("country_id")
        category_val = request.POST.get("category") or request.POST.get("category_id")
        owner = (request.POST.get("owner") or "").strip()
        destination_type = (request.POST.get("destination_type") or "").strip()
        entity_type = (request.POST.get("entity_type") or "").strip()
        description = (request.POST.get("description") or "").strip()
        text_suffix = (request.POST.get("text_suffix") or "").strip()

        try:
            if entity_type == "group" and tg_id_raw and not tg_id_raw.startswith("-"):
                tg_id_raw = f"-{tg_id_raw}"
            telegram_id = int(tg_id_raw)
        except ValueError:
            messages.error(request, "Telegram ID обязателен и должен быть числом")
            return redirect("admin_panel:entities_page")

        country = _resolve_name(Country, country_val)
        category = _resolve_name(Category, category_val)

        obj = MainEntity(
            name=name,
            telegram_id=telegram_id,
            country=country,
            category=category,
            owner=owner,
            entity_type=entity_type,
            destination_type=destination_type,
            link=link,
            tags=tags,
            description=description,
            text_suffix=text_suffix,
            is_add_suffix=False,
        )

        if "photo" in request.FILES:
            obj.photo = request.FILES["photo"]

        obj.save()
        process_category_themes(request, obj)

        messages.success(request, f"{obj.type_display} {obj.name} создан(а)")
        return redirect("admin_panel:entities_page")

    return render(request, "admin_panel/entities/add.html", {
        "countries": Country.objects.all(),
        "categories": Category.objects.all(),
        "owners": owners,
        "destination_types": destination_types,
    })


@login_required
@require_http_methods(["GET", "POST"])
def entity_edit_view(request, entity_id: int):
    """
    Редактирование существующего Telegram-сообщества.
    GET  → отображение формы редактирования.
    POST → обновление объекта MainEntity.
    """
    entity = get_object_or_404(
        MainEntity.objects.select_related('country', 'category')
                         .prefetch_related('entity_category_links__category'),
        id=entity_id
    )

    context = {
        "group_id": entity_id,
        "group": entity,
        "countries": Country.objects.all(),
        "categories": Category.objects.all(),
        "owners": owners,
        "destination_types": destination_types,
    }

    if request.method == "POST":
        entity.name = (request.POST.get("name") or "").strip()
        entity.link = (request.POST.get("link") or "").strip()
        entity.tags = (request.POST.get("tags") or "").strip()
        entity.description = (request.POST.get("description") or "").strip()
        entity.text_suffix = (request.POST.get("text_suffix") or "").strip()
        entity.is_add_suffix = request.POST.get("is_add_suffix") == "1"
        entity.order = request.POST.get("order") or None

        country_val = request.POST.get("country")
        category_val = request.POST.get("category")

        entity.country = _resolve_name(Country, country_val)
        entity.category = _resolve_name(Category, category_val)

        entity.owner = (request.POST.get("owner") or "").strip()
        entity.destination_type = (request.POST.get("destination_type") or "").strip()

        if "photo" in request.FILES:
            entity.photo = request.FILES["photo"]

        entity.save()
        process_category_themes(request, entity)

        messages.success(request, f"{entity.type_display} #{entity_id} обновлён(а)")
        return render(request, "admin_panel/entities/edit.html", context)

    return render(request, "admin_panel/entities/edit.html", context)


def process_category_themes(request, entity):
    """
    Обработка сохранения и обновления тем категорий для сущности.
    Создает, обновляет или удаляет связи с категориями.
    """
    try:
        existing_links = entity.entity_category_links.all()
        existing_links_map = {str(link.category.id): link for link in existing_links}
        submitted_category_ids = set()

        for key, value in request.POST.items():
            if key.startswith('category_theme_url_'):
                category_id = key.replace('category_theme_url_', '')
                theme_url = value.strip()
                if theme_url:
                    try:
                        category_id_int = int(category_id)
                        category = Category.objects.get(id=category_id_int)
                        if category_id in existing_links_map:
                            link = existing_links_map[category_id]
                            if link.theme_url != theme_url:
                                link.theme_url = theme_url
                                link.save()
                                log.info(f"Обновлена тема категории #{link.id} для entity #{entity.id}")
                        else:
                            EntityCategory.objects.create(
                                entity=entity,
                                category=category,
                                theme_url=theme_url
                            )
                            log.info(f"Создана новая тема категории для entity #{entity.id}")

                        submitted_category_ids.add(category_id)

                    except (Category.DoesNotExist, ValueError) as e:
                        log.warning(f"Ошибка обработки категории {category_id}: {e}")
                        continue

        for category_id, link in existing_links_map.items():
            if category_id not in submitted_category_ids:
                link.delete()
                log.info(f"Удалена тема категории #{link.id} для entity #{entity.id}")

    except Exception as e:
        log.error(f"Ошибка обработки тем категорий для entity #{entity.id}: {e}")
        # Не прерываем сохранение основной сущности


@login_required
@require_http_methods(["POST"])
def entity_delete_view(request, entity_id: int):
    """
    Удаление указанного Telegram-сообщества.
    """
    entity = get_object_or_404(MainEntity, id=entity_id)
    name = entity.name
    entity.delete()
    messages.success(request, f"{entity.type_display} {name} (#{entity_id}) удалён(а)")
    return redirect("admin_panel:entities_page")
