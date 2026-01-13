// static/js/notifications.js
class NotificationManager {
    constructor() {
        this.updateInterval = 30000; // 30 секунд
        this.init();
    }

    init() {
        this.loadNotificationCount();
        this.loadDropdownNotifications();
        this.setupEventListeners();
        this.startAutoUpdate();
    }

    async loadNotificationCount() {
        try {
            const response = await fetch('/api/notifications/count/');
            const data = await response.json();
            
            const countElement = document.getElementById('notificationCount');
            const dropdownHeader = document.getElementById('dropdownHeader');
            
            if (countElement) {
                countElement.textContent = data.count;
                countElement.style.display = data.count > 0 ? 'inline' : 'none';
            }
            
            if (dropdownHeader) {
                dropdownHeader.textContent = `${data.count} Уведомлений`;
            }
        } catch (error) {
            console.error('Ошибка загрузки счетчика уведомлений:', error);
        }
    }

    async loadDropdownNotifications() {
        const container = document.getElementById('notificationDropdownItems');
        if (!container) return;

        try {
            const response = await fetch('/api/notifications/recent/');
            const notifications = await response.json();
            
            container.innerHTML = '';
            
            if (notifications.length === 0) {
                container.innerHTML = `
                    <div class="text-center py-2 text-muted">
                        <small>Нет новых уведомлений</small>
                    </div>
                `;
                return;
            }

            notifications.forEach(notification => {
                const severityClass = this.getSeverityClass(notification.severity);
                const timeAgo = this.getTimeAgo(notification.created_at);
                
                const notificationElement = `
                    <a href="{% url 'admin_panel:notifications_list' %}?highlight=${notification.id}" class="dropdown-item">
                        <div class="d-flex align-items-start">
                            <span class="status-dot ${severityClass} mr-2 mt-1"></span>
                            <div class="flex-grow-1">
                                <div class="text-sm font-weight-bold">${this.escapeHtml(notification.title)}</div>
                                <div class="text-xs text-muted">${this.escapeHtml(notification.message)}</div>
                                <div class="text-xs text-muted">${timeAgo}</div>
                            </div>
                        </div>
                    </a>
                    <div class="dropdown-divider"></div>
                `;
                container.innerHTML += notificationElement;
            });

        } catch (error) {
            console.error('Ошибка загрузки уведомлений:', error);
            container.innerHTML = `
                <div class="text-center py-2 text-danger">
                    <small>Ошибка загрузки</small>
                </div>
            `;
        }
    }

    getSeverityClass(severity) {
        const severityMap = {
            10: 'blue',    // DEBUG
            20: 'green',   // INFO
            30: 'yellow',  // WARNING
            40: 'orange',  // ERROR
            50: 'red'      // CRITICAL
        };
        return severityMap[severity] || 'gray';
    }

    getTimeAgo(timestamp) {
        const now = new Date();
        const time = new Date(timestamp);
        const diffMs = now - time;
        const diffMins = Math.floor(diffMs / 60000);
        const diffHours = Math.floor(diffMs / 3600000);
        const diffDays = Math.floor(diffMs / 86400000);

        if (diffMins < 1) return 'только что';
        if (diffMins < 60) return `${diffMins} мин назад`;
        if (diffHours < 24) return `${diffHours} ч назад`;
        return `${diffDays} дн назад`;
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    setupEventListeners() {
        // Обновление при открытии dropdown
        const dropdown = document.getElementById('notificationsDropdown');
        if (dropdown) {
            dropdown.addEventListener('click', () => {
                this.loadDropdownNotifications();
            });
        }
    }

    startAutoUpdate() {
        setInterval(() => {
            this.loadNotificationCount();
        }, this.updateInterval);
    }
}

// Инициализация при загрузке страницы
document.addEventListener('DOMContentLoaded', () => {
    new NotificationManager();
});