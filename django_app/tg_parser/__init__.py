# tg_parser/__init__.py
from .views import parse_channel
from .profile_views import update_profile, update_profile_picture

__all__ = ['parse_channel', 'update_profile', 'update_profile_picture']
