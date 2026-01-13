from django.urls import reverse

BREADCRUMB_MAP = {
    # --- Разделы ---
    "plugins": {"name": "Плагины", "url": lambda: reverse("admin_panel:plugins_page")},
    "directories": {"name": "Справочники", "url": lambda: reverse("admin_panel:directories_page")},

    # --- Подразделы (нет ссылки) ---
    "plugins-working": {"name": "В работе", "url": None, "parent": "plugins"},
    "plugins-system": {"name": "Системные", "url": None, "parent": "plugins"},
    "plugins-theme": {"name": "Тематические", "url": None, "parent": "plugins"},

    # --- Пункты ---
    "entity_post_tasks": {
        "name": "Публикатор постов",
        "url": lambda: reverse("admin_panel:entity_post_tasks_view"),
        "parent": "plugins-working",
    },
    "ads_tasks": {
        "name": "Постинг рекламы 1-24",
        "url": lambda: reverse("admin_panel:ads_tasks_view"),
        "parent": "plugins-working"
    },
    "daily_pinning": {
        "name": "Эмулятор постов",
        "url": lambda: reverse("admin_panel:daily_pinning_tasks_view"),
        "parent": "plugins-working"
    },
    "subscribers_booster": {
        "name": "Нормализатор подписчиков",
        "url": lambda: reverse("admin_panel:subscribers_tasks_view"),
        "parent": "plugins-working"
    },
    "view_boost": {
        "name": "Умный просмотр новых постов",
        "url": lambda: reverse("admin_panel:view_boost_tasks_view"),
        "parent": "plugins-working"
    },
    "old_views_booster": {
        "name": "Нормализатор старых просмотров",
        "url": lambda: reverse("admin_panel:old_views_tasks_view"),
        "parent": "plugins-working"
    },
    "blondinka": {
        "name": "Бот-Блондинка",
        "url": lambda: reverse("admin_panel:blondinka_tasks_view"),
        "parent": "plugins-working"
    },
    "bots": {
        "name": "Боты",
        "url": lambda: reverse("admin_panel:bots_page"),
        "parent": "directories",
    },
    "entities": {
        "name": "Сообщества ТГ",
        "url": lambda: reverse("admin_panel:entities_page"),
        "parent": "directories",
    },
    "countries": {
        "name": "Страны",
        "url": lambda: reverse("admin_panel:countries_page"),
        "parent": "directories",
    },
    "categories": {
        "name": "Категории",
        "url": lambda: reverse("admin_panel:categories_page"),
        "parent": "directories",
    },
    "themes": {
        "name": "Диалоги",
        "url": lambda: reverse("admin_panel:themes_list"),
        "parent": "blondinka",
    },
    "booster_settings": {
        "name": "Настройки бустера",
        "url": lambda: reverse("admin_panel:booster_settings"),
        "parent": "directories",
    },
}


def breadcrumbs(request):
    path_parts = [p for p in request.path.strip("/").split("/") if p]
    crumbs = []

    def build_chain(key):
        """Рекурсивно возвращает цепочку от корня до key"""
        if key not in BREADCRUMB_MAP:
            return []
        cfg = BREADCRUMB_MAP[key]
        url = cfg["url"]() if callable(cfg.get("url")) else cfg.get("url")
        crumb = {"name": cfg["name"], "url": url}
        parent_chain = build_chain(cfg["parent"]) if "parent" in cfg else []
        return parent_chain + [crumb]

    # Сначала пробуем найти точное соответствие по последней части пути
    if path_parts:
        last_part = path_parts[-1]
        if last_part in BREADCRUMB_MAP:
            chain = build_chain(last_part)
            for c in chain:
                if c not in crumbs:
                    crumbs.append(c)
        else:
            # Пробуем найти по комбинациям
            found = False
            for i in range(len(path_parts)):
                # Пробуем комбинации от самых длинных к коротким
                for length in range(min(3, len(path_parts) - i), 0, -1):
                    combo = "_".join(path_parts[i:i+length])
                    if combo in BREADCRUMB_MAP:
                        chain = build_chain(combo)
                        for c in chain:
                            if c not in crumbs:
                                crumbs.append(c)
                        found = True
                        break
                if found:
                    break

    # Если ничего не нашли, создаем простые крошки из пути
    if not crumbs:
        url_so_far = ""
        for i, part in enumerate(path_parts):
            url_so_far += f"/{part}"
            name = part.replace("_", " ").title()
            crumbs.append({"name": name, "url": url_so_far if i < len(path_parts) - 1 else None})

    return {"breadcrumbs": crumbs}