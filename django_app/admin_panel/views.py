from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import render
from admin_panel.models import Plugin

API_CHANNELS = "http://127.0.0.1:5001"   # базовый адрес FastAPI сервиса
API_GROUPS = "http://127.0.0.1:5002" 

def is_moderator(user):
    return user.groups.filter(name='moderator').exists()

def is_advertiser(user):
    return user.groups.filter(name='advertiser').exists()

@login_required
def dashboard(request):
    user = request.user

    # допустим, роли определяются через группу
    if user.groups.filter(name="Advertiser").exists():
        role = "advertiser"
    else:
        role = "moderator"

    return render(request, "admin_panel/dashboard.html", {"role": role})


LANGS = [
    ("ru", "Русский"),
    ("en", "English"),
    ("de", "Deutsch"),
]


def countries_page(request):
    return render(request, "admin_panel/countries.html", {"LANGS": LANGS})


def categories_page(request):
    return render(request, "admin_panel/categories.html", {"LANGS": LANGS})

@login_required
@user_passes_test(is_moderator)
def plugins(request):
    working_plugins = Plugin.objects.filter(is_active=True)
    system_plugins = Plugin.objects.filter(category="system", is_active=False)
    theme_plugins = Plugin.objects.filter(category="theme", is_active=False)
    
    context = {
        "working_plugins": working_plugins,
        "system_plugins": system_plugins,
        "theme_plugins": theme_plugins,
    }
    return render(request, "admin_panel/plugins.html", context)

def groups_page(request):
    """
    Страница списка всех групп
    """
    kinds = [
        ("main", "Основные группы"),
        ("draft", "Библиотеки групп"),
    ]
    return render(request, "admin_panel/groups.html", {"kinds": kinds})


def group_add_view(request):
    """
    Добавление новой группы
    """
    if request.method == "POST":
        tg_id = request.POST.get("tg_id")
        name = request.POST.get("name")
        description = request.POST.get("description")
        tags = request.POST.get("tags")

        try:
            requests.post(f"{API_GROUPS}/groups", data={
                "telegram_id": tg_id,
                "name": name,
                "description": description,
                "tags": tags,
            })
        except Exception as e:
            print("Ошибка при создании группы:", e)

        return redirect("admin_panel:groups_page")

    return render(request, "admin_panel/groups/add.html")


def group_edit(request, mode, kind, group_id):
    """
    Редактирование группы
    mode: "channel" или "group"
    kind: main|draft
    group_id: id группы
    """

    def endpoint_base(kind):
        if kind == "main":
            return f"{API_GROUPS}/groups"
        if kind == "draft":
            return f"{API_GROUPS}/drafts"
        return None

    base_url = endpoint_base(kind)

    group = {}
    try:
        resp = requests.get(f"{base_url}/{group_id}")
        if resp.ok:
            group = resp.json()
    except Exception as e:
        group = {"error": str(e)}

    context = {
        "mode": mode,
        "kind": kind,
        "group_id": group_id,
        "group": group,
    }
    return render(request, "admin_panel/groups/edit.html", context)


def channel_add_view(request):
    if request.method == "POST":
        tg_id = request.POST.get("tg_id")
        name = request.POST.get("name")
        country = request.POST.get("country")
        category = request.POST.get("category")
        link = request.POST.get("link")
        # здесь логика сохранения в БД
        return redirect("admin_panel:channels_page")  # например, на список каналов
    return render(request, "admin_panel/channels/add.html")

def channel_edit(request, mode, kind, chan_id):
    """
    mode: "channel" или "group"
    kind: main|draft|ads|selfpromo|groupDraft
    chan_id: id канала
    """
    # Определяем endpoint для конкретного типа
    def endpoint_base(kind):
        if kind == "main":
            return f"{API_CHANNELS}/main_channels"
        if kind == "draft":
            return f"{API_CHANNELS}/draft_channels"
        if kind == "ads":
            return f"{API_CHANNELS}/ads_channels"
        if kind == "selfpromo":
            return f"{API_CHANNELS}/selfpromo_channels"
        if kind == "groupDraft":
            return f"{API_CHANNELS.replace('/channel_post','/group_post')}/draft_channels"
        return None

    base_url = endpoint_base(kind)

    # Загружаем сам канал
    channel = {}
    try:
        resp = requests.get(f"{base_url}/{chan_id}")
        if resp.ok:
            channel = resp.json()
    except Exception as e:
        channel = {"error": str(e)}

    # Загружаем справочники
    countries, categories, main_channels = [], [], []
    try:
        countries = requests.get(f"{API_CHANNELS}/countries").json()
    except: pass
    try:
        categories = requests.get(f"{API_CHANNELS}/categories").json()
    except: pass

    if kind != "main":   # для не-основных каналов
        try:
            main_channels = requests.get(f"{API_CHANNELS}/main_channels").json()
        except: pass

    context = {
        "mode": mode,
        "kind": kind,
        "chan_id": chan_id,
        "channel": channel,
        "countries": countries,
        "categories": categories,
        "main_channels": main_channels,
    }
    return render(request, "admin_panel/channels/edit.html", context)

def channels_page(request):
    kinds = [
        ("main", "Основные"),
        ("draft", "Библиотеки"),
        ("ads", "Реклама"),
        ("selfpromo", "Самореклама"),
    ]
    return render(request, "admin_panel/channels.html", {"kinds": kinds})

def channel_post_tasks_editor(request, task_id):
    # Заглушка редактирования — передаем тестовые данные
    task = {
        "id": task_id,
        "status": "success",
        "target": "Канал ТГ",
        "days": "Пн, Ср",
        "time": "12:00",
        "source": "Сайт 1",
        "choice": "Случайный",
        "after": "По кругу"
    }
    return render(request, "admin_panel/post_tasks_editor.html", {"task": task})

def channel_post_tasks_view(request):
    # Тестовые данные (минимум 5 задач)
    tasks = [
        {
            "status": "success",
            "id": 1,
            "target": "Канал A",
            "days": "Пн, Ср",
            "time": "10:30",
            "source": "База 1",
            "choice": "Случайный",
            "after": "По кругу",
        },
        {
            "status": "danger",
            "id": 2,
            "target": "Группа B с длинным названием",
            "days": "Вт, Чт",
            "time": "12:00",
            "source": "База 2",
            "choice": "По порядку",
            "after": "Удаляем",
        },
        {
            "status": "success",
            "id": 3,
            "target": "Канал C",
            "days": "Пт",
            "time": "09:15",
            "source": "Источник C",
            "choice": "По имени",
            "after": "По кругу",
        },
        {
            "status": "danger",
            "id": 4,
            "target": "Канал D",
            "days": "Сб, Вс",
            "time": "18:45",
            "source": "Источник D",
            "choice": "Случайный",
            "after": "Удаляем",
        },
        {
            "status": "success",
            "id": 5,
            "target": "Группа E",
            "days": "Пн, Вт, Ср",
            "time": "14:00",
            "source": "Источник E",
            "choice": "По порядку",
            "after": "По кругу",
        },
    ]

    return render(request, "admin_panel/post_tasks.html", {"tasks": tasks})

def plugins_view(request):
    working_plugins = Plugin.objects.filter(is_active=True)
    system_plugins = Plugin.objects.filter(category="system", is_active=False)
    theme_plugins = Plugin.objects.filter(category="theme", is_active=False)

    context = {
        "working_plugins": working_plugins,
        "system_plugins": system_plugins,
        "theme_plugins": theme_plugins,
    }
    return render(request, "plugins/plugins.html", context)


# ----- Кнопки управления -----
def plugin_start(request, plugin_id):
    plugin = get_object_or_404(Plugin, pk=plugin_id)
    plugin.is_active = True
    plugin.save()
    return redirect('plugins')  # имя url для страницы плагинов


def plugin_stop(request, plugin_id):
    plugin = get_object_or_404(Plugin, pk=plugin_id)
    plugin.is_active = False
    plugin.save()
    return redirect('plugins')


def plugin_restart(request, plugin_id):
    plugin = get_object_or_404(Plugin, pk=plugin_id)
    # простой пример: выключаем и включаем
    plugin.is_active = False
    plugin.save()
    plugin.is_active = True
    plugin.save()
    return redirect('plugins')


def plugin_logs(request, plugin_id):
    plugin = get_object_or_404(Plugin, pk=plugin_id)
    return render(request, "plugins/plugin_logs.html", {"plugin": plugin})