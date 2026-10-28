"""Endereços do núcleo."""
from django.urls import path

from apps.core import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("decisoes/<uuid:pk>/", views.confirmacao, name="confirmacao"),
    path("decisoes/<uuid:pk>/confirmar/", views.confirmar, name="confirmar"),
]
