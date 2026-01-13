from admin_panel.models import Country, Category

def _load_directories_from_django():
    # тянем ТОЛЬКО из Django БД
    countries = list(Country.objects.order_by("name").values("id", "name"))
    categories = list(Category.objects.order_by("name").values("id", "name"))
    return countries, categories

