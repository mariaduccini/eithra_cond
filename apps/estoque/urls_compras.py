"""Rotas das compras operacionais.

Compras ficam em ``/compras/``, e não sob ``/estoque/``, porque são outro módulo:
a operação vê uma coisa e não a outra. Por isso vivem em um módulo de rotas
próprio, com nome próprio, ainda dentro do mesmo app.
"""
from django.urls import path

from . import views

app_name = "compras"

urlpatterns = [
    path("", views.compras, name="listagem"),
    path("criar/", views.criar_compra_view, name="criar"),
    path("<uuid:pk>/", views.compra_detalhe, name="detalhe"),
    path("<uuid:pk>/editar/", views.editar_compra_view, name="editar"),
    path("<uuid:pk>/itens/adicionar/", views.adicionar_linha, name="adicionar_linha"),
    path(
        "<uuid:pk>/itens/<uuid:linha_pk>/editar/",
        views.editar_linha,
        name="editar_linha",
    ),
    path(
        "<uuid:pk>/itens/<uuid:linha_pk>/remover/",
        views.remover_linha,
        name="remover_linha",
    ),
    path("<uuid:pk>/confirmar/", views.confirmar, name="confirmar"),
    path("<uuid:pk>/cancelar/", views.cancelar, name="cancelar"),
]
