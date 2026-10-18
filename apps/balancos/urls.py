"""Endereços da entrada de balanços e do balanço confirmado."""
from django.urls import path

from apps.balancos import views

app_name = "balancos"

urlpatterns = [
    path("", views.importacoes, name="importacoes"),
    path("importar/", views.importar, name="importar"),
    path("<uuid:pk>/", views.detalhe, name="detalhe"),
    path("<uuid:pk>/arquivo/", views.acessar_arquivo, name="arquivo"),
    path("<uuid:pk>/descartar/", views.descartar, name="descartar"),
    path("<uuid:pk>/confirmar/", views.confirmar, name="confirmar"),
    path("mes/<uuid:pk>/", views.balanco, name="balanco"),
    path("mes/<uuid:pk>/arquivo/", views.arquivo_do_balanco, name="balanco_arquivo"),
]
