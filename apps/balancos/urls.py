"""Endereços da entrada de balanços."""
from django.urls import path

from apps.balancos import views

app_name = "balancos"

urlpatterns = [
    path("", views.importacoes, name="importacoes"),
    path("importar/", views.importar, name="importar"),
    path("<uuid:pk>/arquivo/", views.acessar_arquivo, name="arquivo"),
    path("<uuid:pk>/descartar/", views.descartar, name="descartar"),
]
