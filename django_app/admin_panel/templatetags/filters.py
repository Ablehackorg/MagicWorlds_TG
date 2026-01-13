# admin_panel/templatetags/custom_filters.py
from django import template
from django.utils import timezone
from django.utils.safestring import mark_safe

register = template.Library()

@register.filter
def get_item(obj, key):
    """
    Возвращает элемент словаря по ключу (строковому или числовому).
    """
    try:
        if obj is None:
            return 0
        key = str(key)
        if isinstance(obj, dict):
            val = obj.get(key)
            if val is None and key.isdigit():
                val = obj.get(int(key))
            return val if val not in [None, ""] else 0
        return getattr(obj, key, 0)
    except Exception:
        return 0

@register.filter
def type_obj(obj):
    return type(obj)

@register.filter
def seconds_to_hours(seconds):
    try:
        return int(seconds) // 3600
    except:
        return 0

@register.filter
def seconds_to_minutes(seconds):
    try:
        return (int(seconds) % 3600) // 60
    except:
        return 0

@register.filter
def add_hours(value, hours):
    if value:
        return value + timezone.timedelta(hours=hours)
    return value

@register.filter
def absolute(value):
    """
    Безопасный абсолют: просто math.fabs, без рекурсии.
    Возвращает число без знака.
    """
    try:
        # int сначала, чтобы 5.0 не превращалось в '5.0'
        return abs(int(value))
    except (TypeError, ValueError):
        try:
            return abs(float(value))
        except (TypeError, ValueError):
            return value


@register.filter(is_safe=True)
def signed_delta_html(value):
    """
    Возвращает готовый HTML со стилями в зависимости от знака:
    >0  → зелёный жирный, с плюсом
    <0  → красный, со знаком минус
    =0  → серый 0

    Пример вывода:
    <span class="text-success font-weight-bold">+12</span>
    <span class="text-danger">-7</span>
    <span class="text-muted">0</span>

    mark_safe используется потому что мы генерим span вручную.
    """
    try:
        num = int(value)
    except (TypeError, ValueError):
        # Если вообще не число — вернём как есть, без оформления
        return value

    if num > 0:
        return mark_safe(f'<span class="text-success font-weight-bold">+{num}</span>')
    elif num < 0:
        # тут уже минус есть в самом числе, отдельную "−" не добавляем
        return mark_safe(f'<span class="text-danger">{num}</span>')
    else:
        return mark_safe('<span class="text-muted">0</span>')


@register.filter
def divisibleby(value, arg):
    """Проверяет, делится ли число без остатка."""
    try:
        return int(value) % int(arg) == 0
    except (TypeError, ValueError, ZeroDivisionError):
        return False


@register.filter
def human_minutes_or_hours(minutes):
    """
    Преобразует количество минут в удобный формат:
    - если делится на 60 без остатка → часы
    - иначе → минуты
    """
    try:
        m = int(minutes)
        if m % 60 == 0:
            return f"{m // 60} час"
        return f"{m} мин"
    except (TypeError, ValueError):
        return "—"

@register.filter
def interval_display(value):
    """
    Преобразует интервал (в минутах) в человекочитаемый формат:
    - если делится на 60 → часы
    - иначе → минуты
    """
    try:
        val = int(value)
        if val % 60 == 0:
            hours = val // 60
            return f"{hours} час"
        return f"{val} мин"
    except (TypeError, ValueError):
        return "—"

@register.filter
def get_time(schedules, day_num):
    """Получить время для дня недели из расписания"""
    schedule = schedules.filter(day_of_week=day_num).first()
    return schedule.publish_time.strftime('%H:%M') if schedule else '09:00'

@register.filter
def get_active(schedules, day_num):
    """Получить активность для дня недели из расписания"""
    schedule = schedules.filter(day_of_week=day_num).first()
    return schedule.is_active if schedule else True