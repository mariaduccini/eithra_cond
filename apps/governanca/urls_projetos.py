"""Endereços dos projetos.

O projeto é a obra tocada aos poucos, e não vive sob o prefixo da governança:
entra pela raiz do sistema, em ``/projetos/``, porque é a obra que a gestão
acompanha, e não um cadastro de obrigação com prazo. As decisões — a entrega
que anda, o valor que se corrige, a demanda que abre — chegam sempre por POST.
"""
from django.urls import path

from apps.governanca import views

app_name = "projetos"

urlpatterns = [
    path("", views.projetos, name="lista"),
    path("<uuid:pk>/", views.projeto, name="detalhe"),
    path(
        "<uuid:pk>/itens/<uuid:item_pk>/avancar/",
        views.avancar_item,
        name="avancar_item",
    ),
    path(
        "<uuid:pk>/itens/<uuid:item_pk>/cancelar/",
        views.cancelar_item,
        name="cancelar_item",
    ),
    path(
        "<uuid:pk>/itens/<uuid:item_pk>/valores/",
        views.editar_valores_item,
        name="editar_valores_item",
    ),
    path(
        "<uuid:pk>/itens/<uuid:item_pk>/demanda/",
        views.abrir_demanda,
        name="abrir_demanda",
    ),
]
