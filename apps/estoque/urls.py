"""Rotas do estoque operacional."""
from django.urls import path

from . import views

app_name = "estoque"

urlpatterns = [
    path("", views.itens, name="itens"),
    path("itens/criar/", views.criar_item_view, name="criar_item"),
    path("itens/<uuid:pk>/", views.item_detalhe, name="item_detalhe"),
    path("itens/<uuid:pk>/editar/", views.editar_item_view, name="editar_item"),
    path("itens/<uuid:pk>/movimentar/", views.movimentar_item, name="movimentar_item"),
    path(
        "itens/<uuid:pk>/retirar/",
        views.retirar_para_manutencao,
        name="retirar_para_manutencao",
    ),
    path("itens/<uuid:pk>/alternar/", views.alternar_item, name="alternar_item"),
]
