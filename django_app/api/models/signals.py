from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
import logging

from admin_panel.models import Country, Category
from .blondinka import GroupTheme
from .entities import MainEntity
from .publication_tasks import EntityPostTask, TaskTime

log = logging.getLogger("django")

# ==============================
#   Изменение таймзоны у страны
# ==============================

@receiver(pre_save, sender=Country)
def remember_old_delta(sender, instance, **kwargs):
    """Перед сохранением страны запоминаем старую дельту."""
    if instance.pk:
        try:
            old = Country.objects.get(pk=instance.pk)
            instance._old_delta = old.time_zone_delta
        except Country.DoesNotExist:
            instance._old_delta = instance.time_zone_delta
    else:
        instance._old_delta = instance.time_zone_delta

@receiver(post_save, sender=Country)
def update_task_times_on_country_delta_change(sender, instance, **kwargs):
    """При изменении дельты пересчитываем все задачи, связанные со страной."""
    if getattr(instance, "_tz_updated", False):
        return  # защита от двойного вызова
    instance._tz_updated = True

    old_delta = getattr(instance, "_old_delta", instance.time_zone_delta)
    new_delta = instance.time_zone_delta
    if old_delta == new_delta:
        return

    delta_diff_sec = int((new_delta - old_delta) * 3600)

    entities = MainEntity.objects.filter(country=instance)
    tasks = EntityPostTask.objects.filter(target__in=entities)

    updated = 0
    for task in tasks:
        for tt in task.times.all():
            new_seconds = (tt.seconds_from_day_start - delta_diff_sec) % 86400
            tt.seconds_from_day_start = int(new_seconds)
            tt.save(update_fields=["seconds_from_day_start"])
            updated += 1

    log.info(
        f"🕒 Изменена дельта страны '{instance.name}' ({old_delta:+} → {new_delta:+}); "
        f"скорректировано {updated} TaskTime записей"
    )

# ==============================
#   Изменение страны у канала/группы
# ==============================

def _adjust_task_times_for_country_change(prev_country, new_country, target_obj):
    """Корректирует время задач при изменении страны у канала или группы."""

    old_delta = getattr(prev_country, "time_zone_delta", 0.0)
    new_delta = getattr(new_country, "time_zone_delta", 0.0)
    delta_diff_sec = int((new_delta - old_delta) * 3600)

    tasks = EntityPostTask.objects.filter(target=target_obj)
    obj_type = getattr(target_obj, "type", "объекта")

    updated = 0
    for task in tasks:
        for tt in task.times.all():
            new_seconds = (tt.seconds_from_day_start - delta_diff_sec) % 86400
            tt.seconds_from_day_start = int(new_seconds)
            tt.save(update_fields=["seconds_from_day_start"])
            updated += 1

    log.info(
        f"🌍 Изменена страна у {obj_type} '{target_obj}': "
        f"{getattr(prev_country, 'name', '—')} → {getattr(new_country, 'name', '—')}; "
        f"скорректировано {updated} таймингов"
    )

@receiver(pre_save, sender=MainEntity)
def remember_old_country_entity(sender, instance, **kwargs):
    if instance.pk:
        try:
            old = MainEntity.objects.get(pk=instance.pk)
            instance._old_country = old.country
        except MainEntity.DoesNotExist:
            instance._old_country = None
    else:
        instance._old_country = None

@receiver(post_save, sender=MainEntity)
def adjust_times_on_entity_country_change(sender, instance, **kwargs):
    old_country = getattr(instance, "_old_country", None)
    new_country = instance.country
    if old_country != new_country:
        _adjust_task_times_for_country_change(old_country, new_country, instance)


@receiver(post_save, sender=Category)
def sync_category_to_theme(sender, instance, created, **kwargs):
    """
    Автоматически создает или обновляет тему при сохранении категории.
    """
    if created:
        # Создаем новую тему для новой категории
        GroupTheme.objects.create(
            name=instance.name,
            category=instance
        )
    else:
        # Обновляем название темы, если категория изменилась
        try:
            theme = GroupTheme.objects.get(category=instance)
            if theme.name != instance.name:
                theme.name = instance.name
                theme.save()
        except GroupTheme.DoesNotExist:
            # Если тема не существует, создаем ее
            GroupTheme.objects.create(
                name=instance.name,
                category=instance
            )


@receiver(post_delete, sender=Category)
def delete_theme_on_category_delete(sender, instance, **kwargs):
    """
    Удаляет тему при удалении категории.
    """
    try:
        theme = GroupTheme.objects.get(category=instance)
        # Проверяем, используется ли тема в задачах
        if not theme.tasks.exists():
            theme.delete()
        else:
            # Если тема используется, отвязываем от категории и делаем ручной
            theme.category = None
            theme.save()
    except GroupTheme.DoesNotExist:
        pass