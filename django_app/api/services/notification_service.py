# services/notification_service.py
from django.utils import timezone
from datetime import timedelta
import logging

# Добавляем импорты моделей
from api.models.notifications import (
    SystemNotification,
    NotificationModule,
    NotificationType,
    NotificationAction
)

logger = logging.getLogger(__name__)


class NotificationService:

    @staticmethod
    def generate_notification_code(module_code, type_code, severity_level):
        """Генерация кода уведомления: MOD-TYP-SEV"""
        return f"{module_code:03d}-{type_code:03d}-{severity_level:02d}"

    @staticmethod
    def create_notification(module_code, type_code, severity, title, message, **kwargs):
        """
        Создание нового уведомления

        Args:
            module_code: код модуля (001, 002, etc)
            type_code: код типа ошибки (001, 002, etc)  
            severity: уровень критичности (10-50)
            title: заголовок уведомления
            message: текст уведомления
            **kwargs: дополнительные параметры (entity, task_id, details, etc)
        """
        try:
            # Получаем модуль и тип
            module = NotificationModule.objects.get(code=module_code)
            notification_type = NotificationType.objects.get(
                module=module, code=type_code, severity=severity
            )

            # Генерируем код уведомления
            notification_code = NotificationService.generate_notification_code(
                int(module_code), int(type_code), severity
            )

            # Создаем уведомление
            notification = SystemNotification.objects.create(
                notification_code=notification_code,
                module=module,
                notification_type=notification_type,
                title=title,
                message=message,
                details=kwargs.get('details', {}),
                entity=kwargs.get('entity'),
                task_id=kwargs.get('task_id'),
                external_id=kwargs.get('external_id', ''),
                source=kwargs.get('source', ''),
                stack_trace=kwargs.get('stack_trace', ''),
            )

            # Логируем создание
            logger.info(f"Создано уведомление {notification_code}: {title}")

            return notification

        except Exception as e:
            logger.error(f"Ошибка создания уведомления: {e}")
            return None

    @staticmethod
    def resolve_notification(notification_code, user=None, comment=""):
        """Пометить уведомление как решенное"""
        try:
            notification = SystemNotification.objects.get(
                notification_code=notification_code)
            notification.status = 'RESOLVED'
            notification.resolved_at = timezone.now()
            notification.save()

            # Записываем действие
            if user:
                NotificationAction.objects.create(
                    notification=notification,
                    user=user,
                    action='RESOLVED',
                    comment=comment
                )

            return True
        except SystemNotification.DoesNotExist:
            return False

    @staticmethod
    def get_active_notifications(module_code=None, severity_min=20):
        """Получить активные уведомления"""
        queryset = SystemNotification.objects.filter(
            status__in=['NEW', 'ACKNOWLEDGED', 'IN_PROGRESS']
        )

        if module_code:
            queryset = queryset.filter(module__code=module_code)

        if severity_min:
            queryset = queryset.filter(
                notification_type__severity__gte=severity_min)

        return queryset.order_by('-notification_type__severity', '-created_at')

    @staticmethod
    def cleanup_old_notifications(days=30):
        """Очистка старых уведомлений"""
        cutoff_date = timezone.now() - timedelta(days=days)
        deleted_count, _ = SystemNotification.objects.filter(
            created_at__lt=cutoff_date,
            status='RESOLVED'
        ).delete()
        return deleted_count
