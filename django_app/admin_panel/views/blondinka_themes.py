# admin_panel/views/blondinka_themes.py

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from admin_panel.models import Category
from api.models import BlondinkaDialog, GroupTheme


@login_required
def themes_list(request):
    """
    Отображает список всех тем групп.

    Перед отображением гарантирует наличие тем для всех категорий:
    если для категории тема отсутствует, она создаётся автоматически.
    """
    all_categories = Category.objects.all()

    for category in all_categories:
        GroupTheme.objects.get_or_create(
            category=category,
            defaults={"name": category.name},
        )

    themes = (
        GroupTheme.objects
        .all()
        .annotate(
            dialogs_count=Count("dialogs", distinct=True),
            tasks_count=Count("tasks", distinct=True),
        )
        .order_by("name")
    )

    return render(
        request,
        "admin_panel/plugins/blondinka/themes_list.html",
        {
            "themes": themes,
            "page_title": "Темы групп",
        },
    )


@login_required
def theme_add(request):
    """
    Представление для создания новой ручной темы.

    Ручная тема не привязана к категории и управляется полностью вручную.
    """
    return theme_edit(request, theme_id=None)


@login_required
def theme_edit(request, theme_id):
    """
    Создание или редактирование темы.

    Поведение зависит от типа темы:
    - ручная тема: название задаётся пользователем;
    - тема категории: название синхронизируется с категорией.
    """
    theme = (
        get_object_or_404(GroupTheme, id=theme_id)
        if theme_id else None
    )

    context = {
        "theme": theme,
        "page_title": (
            "Редактирование темы"
            if theme else "Создание темы"
        )
        + (f" «{theme.name}»" if theme else ""),
    }

    if request.method == "POST":
        try:
            with transaction.atomic():
                if theme is None:
                    name = request.POST.get("name", "").strip()
                    if not name:
                        messages.error(
                            request,
                            "Название темы обязательно",
                        )
                        return redirect(request.path)

                    theme = GroupTheme.objects.create(
                        name=name,
                        category=None,
                    )
                    action_message = (
                        f"Создана новая тема «{theme.name}»"
                    )
                else:
                    theme.updated_at = timezone.now()

                    if not theme.category_id:
                        name = request.POST.get("name", "").strip()
                        if name:
                            theme.name = name
                    else:
                        if theme.category:
                            theme.name = theme.category.name

                    theme.save()
                    action_message = (
                        f"Тема «{theme.name}» обновлена"
                    )

                process_dialogs(request, theme)

                messages.success(request, action_message)
                return HttpResponseRedirect(
                    reverse(
                        "admin_panel:theme_edit",
                        args=[theme.id],
                    )
                )

        except Exception as exc:
            messages.error(
                request,
                f"Ошибка при сохранении: {exc}",
            )
            return redirect(request.path)

    return render(
        request,
        "admin_panel/plugins/blondinka/theme_edit.html",
        context,
    )


def process_dialogs(request, theme):
    """
    Обрабатывает список диалогов, связанных с темой.

    Поддерживает:
    - создание новых диалогов;
    - обновление существующих;
    - удаление помеченных диалогов.
    """
    for key, value in request.POST.items():
        if not key.startswith("dialog_message_"):
            continue

        dialog_id = key.replace("dialog_message_", "")
        message = value.strip()

        if not message:
            continue

        if dialog_id.startswith("new_"):
            BlondinkaDialog.objects.create(
                theme=theme,
                message=message,
                is_active=True,
                order=0,
            )
        else:
            try:
                dialog = BlondinkaDialog.objects.get(
                    id=dialog_id,
                    theme=theme,
                )
                dialog.message = message
                dialog.updated_at = timezone.now()
                dialog.save()
            except BlondinkaDialog.DoesNotExist:
                pass

    for key in request.POST.keys():
        if not key.startswith("dialog_delete_"):
            continue

        dialog_id = key.replace("dialog_delete_", "")
        if dialog_id.startswith("new_"):
            continue

        try:
            dialog = BlondinkaDialog.objects.get(
                id=dialog_id,
                theme=theme,
            )
            dialog.delete()
        except BlondinkaDialog.DoesNotExist:
            pass


@login_required
@require_POST
def theme_delete(request, theme_id):
    """
    Удаляет тему, если она не используется в задачах.

    Темы, связанные с категориями, не удаляются полностью —
    только управляются через настройки категории.
    """
    theme = get_object_or_404(GroupTheme, id=theme_id)
    theme_name = theme.name

    if theme.tasks.exists():
        messages.error(
            request,
            (
                f"Невозможно удалить тему «{theme_name}», "
                f"так как она используется в "
                f"{theme.tasks.count()} задачах"
            ),
        )
        return HttpResponseRedirect(
            reverse("admin_panel:themes_list")
        )

    if theme.category_id:
        messages.warning(
            request,
            (
                f"Тема «{theme_name}» связана с категорией. "
                f"Можно только отключить её в настройках темы."
            ),
        )
        return HttpResponseRedirect(
            reverse("admin_panel:themes_list")
        )

    theme.dialogs.all().delete()
    theme.delete()

    messages.success(
        request,
        f"Тема «{theme_name}» удалена",
    )
    return HttpResponseRedirect(
        reverse("admin_panel:themes_list")
    )
