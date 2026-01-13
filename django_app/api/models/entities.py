from django.db import models
from django.core.files.storage import FileSystemStorage
from django.conf import settings
import os

from admin_panel.models import Country

# Настройки для хранения аватаров
avatar_storage = FileSystemStorage(
    location=settings.AVATARS_ROOT,
    base_url=settings.AVATARS_URL,
)

def group_photo_path(instance, filename):
    """Формирует путь для сохранения фото канала/группы."""
    ext = os.path.splitext(filename)[1] or ".jpg"
    return f"{instance.telegram_id}{ext}"

class MainEntity(models.Model):
    """Основная таблица для Telegram-сообществ."""
    ENTITY_TYPES = [
        ("channel", "Канал"),
        ("group", "Группа"),
    ]

    ENTITY_DEST_TYPES = [
        ("draft", "Библиотека"),
        ("all", "Все"),
        ("main", "Основной")
    ]

    name = models.CharField(max_length=255)
    order = models.IntegerField(null=True, blank=True, help_text="Приоритет сортировки")
    telegram_id = models.BigIntegerField(unique=True)
    entity_type = models.CharField(max_length=16, choices=ENTITY_TYPES)
    destination_type = models.CharField(max_length=16, choices=ENTITY_DEST_TYPES, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    owner = models.CharField(max_length=32, blank=True, null=True)
    link = models.CharField(max_length=255, blank=True, null=True)
    publish_link = models.CharField(max_length=255, blank=True, null=True)
    tags = models.CharField(max_length=255, blank=True, null=True)
    text_suffix = models.CharField(max_length=1024, blank=True, null=True)
    is_add_suffix = models.BooleanField(default=True)

    # связи с директориями
    category = models.ForeignKey(
        "admin_panel.Category",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="entities",
    )
    country = models.ForeignKey(
        "admin_panel.Country",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="entities",
    )

    # Новая связь Many-to-Many с Category через промежуточную модель
    categories = models.ManyToManyField(
        "admin_panel.Category",
        through='EntityCategory',
        related_name='entity_categories'
    )

    cached_task_count = models.PositiveIntegerField(default=0)
    cached_task_updated = models.DateTimeField(blank=True, null=True)

    def refresh_task_count(self, force=False):
        """
        Пересчитывает количество уникальных активных групп задач,
        в которых эта сущность участвует как цель (target).
        """
        from .publication_tasks import EntityPostTask
        from django.utils import timezone
        
        now = timezone.now()

        if (
            force
            or not self.cached_task_updated
            or (now - self.cached_task_updated).days > 0
        ):
            count_target = (
                EntityPostTask.objects.filter(
                    target=self,
                    is_active=True,
                    is_global_active=True,
                    group__isnull=False
                )
                .values("group")
                .distinct()
                .count()
            )
            count_source = (
                EntityPostTask.objects.filter(
                    source=self,
                    is_active=True,
                    is_global_active=True,
                    group__isnull=False
                )
                .values("group")
                .distinct()
                .count()
            )
            count = count_target + count_source
            self.cached_task_count = count
            self.cached_task_updated = now
            self.save(update_fields=["cached_task_count", "cached_task_updated"])

        return self.cached_task_count

    @property
    def type_display(self) -> str:
        return "Группа" if self.entity_type == "group" else "Канал"

    @property
    def tags_list(self) -> list:
        """Возвращает список тегов (строка → list)."""
        return [t.strip() for t in self.tags.split(",") if t.strip()] if self.tags else []

    photo = models.ImageField(
        storage=avatar_storage,
        upload_to=group_photo_path,
        blank=True,
        null=True,
    )

    def __str__(self):
        return self.name

class EntityCategory(models.Model):
    """
    Промежуточная модель для связи Many-to-Many между MainEntity и Category
    с обязательным полем URL темы.
    """
    entity = models.ForeignKey(
        MainEntity,
        on_delete=models.CASCADE,
        related_name='entity_category_links'
    )
    category = models.ForeignKey(
        'admin_panel.Category',
        on_delete=models.CASCADE,
        related_name='category_entity_links'
    )
    theme_url = models.URLField(
        max_length=500,
        verbose_name="URL темы",
        help_text="Обязательный URL темы для этой связи"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Связь Entity-Category"
        verbose_name_plural = "Связи Entity-Category"
        unique_together = ['entity', 'category']
        db_table = 'main_entity_category_links'

    def __str__(self):
        return f"{self.entity.name} - {self.category.name} ({self.theme_url})"
