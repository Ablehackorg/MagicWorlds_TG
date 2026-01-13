from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def dashboard(request):
    """
    Главная страница панели управления.
    Определяет роль пользователя:
    - advertiser → рекламодатель
    - moderator  → модератор
    """
    user = request.user
    if user.groups.filter(name="Advertiser").exists():
        role = "advertiser"
    else:
        role = "moderator"
    return render(request, "admin_panel/dashboard.html", {"role": role})
