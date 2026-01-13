# api/models.py
"""
Главный файл моделей - импортирует все модели из отдельных модулей
для сохранения обратной совместимости.
"""

# Импорт всех моделей из отдельных файлов
from .models.entities import *
from .models.publication_tasks import *
from .models.ads import *
from .models.pinning import *
from .models.view_booster import *
from .models.old_views import *
from .models.subscribers import *
from .models.reactions import *
from .models.channel_sync import *
from .models.blondinka import *
from .models.booster import *

# Импорт сигналов для их регистрации
from .models.signals import *

# Экспорт всех моделей для обратной совместимости
__all__ = [
    # core.py
    'MainEntity', 'EntityCategory',

    # publication_tasks.py
    'EntityPostTask', 'TaskTime', 'ChannelTaskGroup', 'EntityPostTaskQuerySet',

    # ads.py
    'AdsOrder', 'AdTargetEntity',

    # pinning.py
    'DailyPinningTask',

    # view_booster.py
    'ViewBoostTask', 'ViewBoostExpense', 'ViewDistribution', 'ActivePostTracking',

    # old_views.py
    'OldViewsTask', 'OldViewsExpense',

    # subscribers.py
    'SubscribersBoostTask', 'SubscribersBoostExpense', 'SubscribersCheck', 'SubscriberList',

    # reactions.py
    'ReactionBoostTask', 'ReactionRecord',

    # channel_sync.py
    'ChannelSyncTask', 'ChannelSyncHistory', 'ChannelSyncProgress',

    # blondinka.py
    'GroupTheme', 'BlondinkaDialog', 'BlondinkaSchedule', 'BlondinkaTask', 'BlondinkaLog',

    # booster.py
    'BoosterSettings', 'BoosterServiceRotation', 'BoosterTariff',
]
