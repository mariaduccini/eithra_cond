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
        "<uuid:pk>/documentos/adicionar/",
        views.adicionar_documento_compra_view,
        name="adicionar_documento",
    ),
    path(
        "<uuid:pk>/documentos/<uuid:documento_pk>/arquivo/",
        views.baixar_documento_compra,
        name="baixar_documento",
    ),
    path(
        "<uuid:pk>/documentos/<uuid:documento_pk>/itens/adicionar/",
        views.trazer_itens_identificados_view,
        name="trazer_itens_identificados",
    ),
    path(
        "<uuid:pk>/documentos/<uuid:documento_pk>/reprocessar/",
        views.reprocessar_documento_view,
        name="reprocessar_documento",
    ),
    path(
        "<uuid:pk>/documentos/<uuid:documento_pk>/itens/<int:indice>/selecionar/",
        views.selecionar_item_extraido_view,
        name="selecionar_item_extraido",
    ),
    path(
        "<uuid:pk>/documentos/<uuid:documento_pk>/itens/<int:indice>/cadastrar/",
        views.cadastrar_item_extraido_view,
        name="cadastrar_item_extraido",
    ),
    path(
        "<uuid:pk>/documentos/<uuid:documento_pk>/itens/<int:indice>/adicionar/",
        views.incluir_item_extraido_view,
        name="incluir_item_extraido",
    ),
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
    path(
        "<uuid:pk>/reembolso/",
        views.marcar_reembolso_compra_view,
        name="marcar_reembolso",
    ),
    path(
        "<uuid:pk>/reembolso/realizado/",
        views.marcar_reembolso_realizado_view,
        name="marcar_reembolso_realizado",
    ),
    path("<uuid:pk>/excluir/", views.excluir_rascunho, name="excluir"),
    path("<uuid:pk>/cancelar/", views.cancelar, name="cancelar"),
]
