from .dashboard import dashboard

from .notifications import (
	notifications_list_view,
	notification_detail_view,
	notification_resolve_view
)

from .directories import (
    countries_page,
    categories_page,
    country_add_ajax,
    category_add_ajax,
    country_update_ajax,
    category_update_ajax,
    country_delete_ajax,
    category_delete_ajax,
    directories_page

)
from .entities import entities_page_view, entity_add_view, entity_edit_view, entity_delete_view
from .bots import *
from .plugins import (
	plugins_page,
	plugin_action,
	plugin_logs
)

from .entity_post import (
	entity_post_task_create,
	entity_post_task_delete,
	entity_post_task_edit,
	entity_post_tasks_view,

)


from .ads_post import (
	ads_tasks_view,
	ads_task_add,
	ads_task_edit,
	ads_task_delete,
)

from .daily_pinning import (
	daily_pinning_tasks_view,
	daily_pinning_task_add,
	daily_pinning_task_edit,
	daily_pinning_task_delete
)

from .booster_settings import (
	booster_settings_view,
	booster_check_ajax
)

from .view_boost import (
	view_boost_tasks_view,
	view_boost_task_add,
	view_boost_task_edit,
	view_boost_task_delete,
)

from .smart_view_settings import (
	views_settings_view
)

from .old_views_boost import (
	old_views_tasks_view,
	old_views_task_add,
	old_views_task_edit,
	old_views_task_delete
)

from .subscribers_booster import (
	subscribers_tasks_view,
	subscribers_task_add,
	subscribers_task_edit,
	subscribers_task_delete
)

from .reaction_boost import (
	reaction_tasks_view,
	reaction_task_add,
	reaction_task_edit,
	reaction_task_delete
)
from .stats.reactions import (
	reaction_stats_view
)

from .channel_sync import (
	channel_sync_tasks_view,
	channel_sync_task_add,
	channel_sync_task_edit,
	channel_sync_task_delete
)

from .blondinka import (
	blondinka_tasks_view,
	blondinka_task_add,
	blondinka_task_edit,
	blondinka_task_delete,
	get_theme_dialogs,
	update_bot_name
)
from .blondinka_themes import (
	themes_list,
	theme_add,
	theme_edit,
	theme_delete
)

from .currency import (
	currency_locations_view,
	currency_location_add,
	currency_location_edit,
	currency_location_delete
)


from .stats.twiboost import twiboost_stats_view

from .weatherbot import (
	weatherbot_tasks_view,
	weatherbot_task_add,
	weatherbot_task_edit,
	weatherbot_cities_api,
)
