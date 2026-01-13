# views/notifications.py
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.db.models import Q, Count
from django.core.paginator import Paginator
from django.contrib import messages

from api.models import SystemNotification, NotificationModule, NotificationType
from api.services.notification_service import NotificationService

@login_required
def notifications_list_view(request):
    """Страница списка уведомлений"""
    # Базовый queryset - все уведомления, фильтрация будет на клиенте
    notifications = SystemNotification.objects.select_related(
        'module', 'notification_type', 'entity'
    ).prefetch_related('actions').order_by('-created_at')
    
    # Статистика
    stats = {
        'total': SystemNotification.objects.count(),
        'active': SystemNotification.objects.filter(
            status__in=['NEW', 'ACKNOWLEDGED', 'IN_PROGRESS']
        ).count(),
        'resolved': SystemNotification.objects.filter(status='RESOLVED').count(),
    }
    
    context = {
        'notifications': notifications,
        'page_title': 'Системные уведомления',
        'modules': NotificationModule.objects.filter(is_active=True),
        'severity_levels': NotificationType.SEVERITY_LEVELS,
        'stats': stats,
        'highlight_id': request.GET.get('highlight'),
    }
    
    return render(request, "admin_panel/notifications/list.html", context)

@login_required
@require_http_methods(["POST"])
def notification_resolve_view(request, notification_id):
    """Пометить уведомление как решенное"""
    notification = get_object_or_404(SystemNotification, id=notification_id)
    
    NotificationService.resolve_notification(
        notification.notification_code,
        user=request.user,
        comment=request.POST.get('comment', '')
    )
    
    messages.success(request, f"Уведомление #{notification.notification_code} помечено как решенное")
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True})
    
    return redirect('admin_panel:notifications_list')

@login_required
def notification_detail_view(request, notification_id):
    """Детальная страница уведомления"""
    notification = get_object_or_404(
        SystemNotification.objects.select_related('module', 'notification_type', 'entity'),
        id=notification_id
    )
    
    context = {
        'notification': notification,
        'page_title': f'Уведомление {notification.notification_code}',
    }
    
    return render(request, "admin_panel/notifications/detail.html", context)

# API endpoints для dropdown
@login_required
def api_notification_count(request):
    """API: количество активных уведомлений"""
    count = SystemNotification.objects.filter(
        status__in=['NEW', 'ACKNOWLEDGED', 'IN_PROGRESS']
    ).count()
    
    return JsonResponse({'count': count})

@login_required
def api_recent_notifications(request):
    """API: последние уведомления для dropdown"""
    notifications = SystemNotification.objects.filter(
        status__in=['NEW', 'ACKNOWLEDGED', 'IN_PROGRESS']
    ).select_related('notification_type').order_by('-created_at')[:5]
    
    notifications_data = []
    for notification in notifications:
        notifications_data.append({
            'id': notification.id,
            'title': notification.title,
            'message': notification.message,
            'severity': notification.notification_type.severity,
            'created_at': notification.created_at.isoformat(),
            'notification_code': notification.notification_code,
        })
    
    return JsonResponse(notifications_data, safe=False)
