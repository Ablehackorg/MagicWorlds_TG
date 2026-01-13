# tg_parser/urls.py
from django.urls import path
from . import views
from . import profile_views


urlpatterns = [
    path('parse_channel', views.parse_channel, name='parse_channel'),
    # path('api/update_profile', profile_views.update_profile, name='update_profile'),
    # path('api/update_profile_picture', profile_views.update_profile_picture, name='update_profile_picture'),
]
