from django.urls import path, include
from rest_framework.routers import DefaultRouter

app_name = "api"

urlpatterns = [
    path("", include("tg_parser.urls")),
]
