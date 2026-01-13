# templatetags/notification_filters.py
from django import template

register = template.Library()

@register.filter
def severity_color(severity):
    color_map = {
        10: 'blue',    # DEBUG
        20: 'green',   # INFO  
        30: 'yellow',  # WARNING
        40: 'orange',  # ERROR
        50: 'red'      # CRITICAL
    }
    return color_map.get(severity, 'gray')

@register.filter  
def status_color(status):
    color_map = {
        'NEW': 'primary',
        'ACKNOWLEDGED': 'secondary', 
        'IN_PROGRESS': 'info',
        'RESOLVED': 'success',
        'DEFERRED': 'warning',
        'IGNORED': 'secondary'
    }
    return color_map.get(status, 'secondary')
