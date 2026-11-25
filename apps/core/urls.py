"""Endereços do núcleo."""
from django.urls import path

from apps.core import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("meu-dia/", views.meu_dia, name="meu_dia"),
    path("transparencia/<uuid:pk>/", views.transparencia, name="transparencia"),
    path("decisoes/<uuid:pk>/", views.confirmacao, name="confirmacao"),
    path("decisoes/<uuid:pk>/confirmar/", views.confirmar, name="confirmar"),
    path(
        "acompanhamento/<str:origem>/<uuid:pk>/",
        views.acompanhamento,
        name="acompanhamento",
    ),
    path(
        "acompanhamento/<str:origem>/<uuid:pk>/gesto/",
        views.acompanhar,
        name="acompanhar",
    ),
]
