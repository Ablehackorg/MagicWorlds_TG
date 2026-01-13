from django.urls import path
from . import views

app_name = "telegram"

urlpatterns = [
    path("start_auth/", views.start_auth, name="start_auth"),
    path("confirm_code/", views.confirm_code, name="confirm_code"),
    path("resend_code/", views.resend_code, name="resend_code")
]
