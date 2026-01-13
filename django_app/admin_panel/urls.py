from django.urls import path, re_path
from django.contrib.auth import views as auth_views
from . import views


# Пространство имён для url-шаблонов
app_name = "admin_panel"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),

    # Уведомления
    path('notifications/', views.notifications_list_view, name='notifications_list'),
    path('notifications/<int:notification_id>/', views.notification_detail_view, name='notification_detail'),
    path('notifications/<int:notification_id>/resolve/', views.notification_resolve_view, name='notification_resolve'),

    # === GroupPost задачи ===
    path("plugins/entity_post/tasks/", views.entity_post_tasks_view, name="entity_post_tasks_view"),
    path("plugins/entity_post/tasks/create/", views.entity_post_task_create, name="entity_post_task_create"),
    path("plugins/entity_post/tasks/edit/<int:group_id>/", views.entity_post_task_edit, name="entity_post_task_edit"),
    path("plugins/entity_post/tasks/delete/<int:group_id>/", views.entity_post_task_delete, name="entity_post_task_delete"),

    # === Директории: каналы и группы ===
    path("directories/entities/", views.entities_page_view, name="entities_page"),
    path("directories/entities/add/", views.entity_add_view, name="entities_add"),
    path("directories/entities/edit/<int:entity_id>/", views.entity_edit_view, name="entities_edit"),
    path("directories/entities/delete/<int:entity_id>/", views.entity_delete_view, name="entities_delete"),

    path("directories/", views.directories_page, name="directories_page"),

    # === Справочники: страны и категории (AJAX API) ===
    path("countries/", views.countries_page, name="countries_page"),
    path("categories/", views.categories_page, name="categories_page"),
    path("countries/add/", views.country_add_ajax, name="country_add_ajax"),
    path("categories/add/", views.category_add_ajax, name="category_add_ajax"),
    path("countries/<int:pk>/update/", views.country_update_ajax, name="country_update_ajax"),
    path("categories/<int:pk>/update/", views.category_update_ajax, name="category_update_ajax"),
    path("countries/<int:pk>/delete/", views.country_delete_ajax, name="country_delete_ajax"),
    path("categories/<int:pk>/delete/", views.category_delete_ajax, name="category_delete_ajax"),

    # === Плагины (контейнеры) ===
    path("plugins/", views.plugins_page, name="plugins_page"),
    path("plugins/<int:plugin_id>/logs/", views.plugin_logs, name="plugin_logs"),
    path("plugins/<int:plugin_id>/<str:action>/", views.plugin_action, name="plugin_action"),


    # === WeatherBot (Погода) ===
    path("plugins/weatherbot/tasks/", views.weatherbot_tasks_view, name="weatherbot_tasks_view"),
    path("plugins/weatherbot/tasks/create/", views.weatherbot_task_add, name="weatherbot_task_add"),
    path("plugins/weatherbot/tasks/edit/<int:task_id>/", views.weatherbot_task_edit, name="weatherbot_task_edit"),
    path("plugins/weatherbot/api/cities/<int:country_id>/", views.weatherbot_cities_api, name="weatherbot_cities_api"),

    # === Auth ===
    path("login/", auth_views.LoginView.as_view(template_name="admin_panel/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    # === Боты ===
    path('bots/', views.bots_list, name='bots_page'),
    path('bots/add/', views.add_bot, name='add_bot'),
    path('bots/<int:bot_id>/sync/', views.fetch_bot_telegram_info, name='bot_sync'),
    path('bots/update/', views.update_bot, name='bot_update'),
    path('bots/bulk-update/', views.bulk_update_bots, name='bots_bulk_update'),
    path('bots/<int:bot_id>/delete/', views.delete_bot, name='bot_delete'),
    path('bots/<int:bot_id>/update_avatar/', views.update_bot_avatar, name='bot_update_avatar'),
    path('bots/<int:bot_id>/remove_avatar/', views.remove_bot_avatar, name='bot_remove_avatar'),

    # === Реклама ===
    path("ads/tasks/", views.ads_tasks_view, name="ads_tasks_view"),
    path("ads/tasks/create/", views.ads_task_add, name="ads_task_add"),
    path("ads/tasks/edit/<int:task_id>/", views.ads_task_edit, name="ads_task_edit"),
    path("ads/tasks/delete/<int:task_id>/", views.ads_task_delete, name="ads_task_delete"),

    # === Скрипт-Пустышка ===
    path("daily_pinning/", views.daily_pinning_tasks_view, name="daily_pinning_tasks_view"),
    path("daily_pinning/add/", views.daily_pinning_task_add, name="daily_pinning_task_add"),
    path("daily_pinning/edit/<int:task_id>/", views.daily_pinning_task_edit, name="daily_pinning_task_edit"),
    path("daily_pinning/delete/<int:task_id>/", views.daily_pinning_task_delete, name="daily_pinning_task_delete"),

    path("booster_settings/", views.booster_settings_view, name="booster_settings"),
    path("booster_settings/check/", views.booster_check_ajax, name="booster_check_ajax"),

    # Умный просмотр новых постов
    path('view_boost/', views.view_boost_tasks_view, name='view_boost_tasks_view'),
    path('view_boost/add/', views.view_boost_task_add, name='view_boost_task_add'),
    path('view_boost/<int:task_id>/edit/', views.view_boost_task_edit, name='view_boost_task_edit'),
    path('view_boost/<int:task_id>/delete/', views.view_boost_task_delete, name='view_boost_task_delete'),
    path("view_boost/smart_view/", views.views_settings_view, name="smart_view_settings"),

    # === Старые просмотры ===
    path("old_views_booster/", views.old_views_tasks_view, name="old_views_tasks_view"),
    path("old_views_booster/add/", views.old_views_task_add, name="old_views_task_add"),
    path("old_views_booster/<int:task_id>/edit/", views.old_views_task_edit, name="old_views_task_edit"),
    path("old_views_booster/<int:task_id>/delete/", views.old_views_task_delete, name="old_views_task_delete"),

    # === Подписчики ===
    path("subscribers_booster/", views.subscribers_tasks_view, name="subscribers_tasks_view"),
    path("subscribers_booster/add/", views.subscribers_task_add, name="subscribers_task_add"),
    path("subscribers_booster/edit/<int:task_id>/", views.subscribers_task_edit, name="subscribers_task_edit"),
    path("subscribers_booster/delete/<int:task_id>/", views.subscribers_task_delete, name="subscribers_task_delete"),

    # === Реакции ===
    path("reaction_booster/", views.reaction_tasks_view, name="reaction_tasks_view"),
    path("reaction_booster/add/", views.reaction_task_add, name="reaction_task_add"),
    path("reaction_booster/edit/<int:task_id>/", views.reaction_task_edit, name="reaction_task_edit"),
    path("reaction_booster/delete/<int:task_id>/", views.reaction_task_delete, name="reaction_task_delete"),
    path('reaction_stats/', views.reaction_stats_view, name='reaction_stats_view'),

    # === Синхронизация каналов ===
    path('channel_sync/', views.channel_sync_tasks_view, name='channel_sync_tasks_view'),
    path('channel_sync/add/', views.channel_sync_task_add, name='channel_sync_task_add'),
    path('channel_sync/<int:task_id>/edit/', views.channel_sync_task_edit, name='channel_sync_task_edit'),
    path('channel_sync/<int:task_id>/delete/', views.channel_sync_task_delete, name='channel_sync_task_delete'),

    path("blondinka/", views.blondinka_tasks_view, name="blondinka_tasks_view"),
    path("blondinka/add/", views.blondinka_task_add, name="blondinka_task_add"),
    path("blondinka/edit/<int:task_id>/", views.blondinka_task_edit, name="blondinka_task_edit"),
    path("blondinka/delete/<int:task_id>/", views.blondinka_task_delete, name="blondinka_task_delete"),
    path('blondinka/themes/', views.themes_list, name='themes_list'),
    path('blondinka/themes/add/', views.theme_add, name='theme_add'),
    path('blondinka/themes/<int:theme_id>/edit/', views.theme_edit, name='theme_edit'),
    path('blondinka/themes/<int:theme_id>/delete/', views.theme_delete, name='theme_delete'),
    path('api/get_theme_dialogs/', views.get_theme_dialogs, name='get_theme_dialogs'),
    path('api/update_bot_name/', views.update_bot_name, name='update_bot_name'),

    # === Курс валют ===
    path("currency/", views.currency_locations_view, name="currency_list"),
    path("currency/add/", views.currency_location_add, name="currency_create"),
    path("currency/edit/<int:location_id>/", views.currency_location_edit, name="currency_edit"),
    path("currency/delete/<int:location_id>/", views.currency_location_delete, name="currency_delete"),

    path('bots/sync/<int:bot_id>/', views.fetch_bot_telegram_info, name='bot_sync'),
    path('bots/update/', views.update_bot, name='bot_update'),
    path('bots/bulk-update/', views.bulk_update_bots, name='bots_bulk_update'),
    path('bots/delete/<int:bot_id>/', views.delete_bot, name='bot_delete'),
    path('bots/avatar/update/<int:bot_id>/', views.update_bot_avatar, name='update_bot_avatar'),
    path('bots/avatar/remove/<int:bot_id>/', views.remove_bot_avatar, name='remove_bot_avatar'),
    path('bots/fetch-avatar/<int:bot_id>/', views.fetch_bot_avatar, name='fetch_bot_avatar'),
    path('bots/edit/<int:bot_id>/', views.bot_edit_view, name='bot_edit'),
    path('bots/<int:bot_id>/groups/<str:group_type>/', views.fetch_bot_groups, name='fetch_bot_groups'), # group_type: admin или subscriber


    path("stats/twiboost", views.twiboost_stats_view, name="twiboost_stats_view",
    ),

]
