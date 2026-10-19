"""Endereços dos cadastros básicos operacionais.

O cadastro do condomínio é a porta de entrada do módulo: é ele que dá nome a
tudo o mais. Cada cadastro tem sua lista, e as ações — registrar, corrigir,
ativar, desativar — chegam sempre por POST, nunca por link.
"""
from django.urls import path

from apps.cadastros import views

app_name = "cadastros"

urlpatterns = [
    path("", views.condominio, name="condominio"),
    path("acessos/", views.acessos, name="acessos"),
    path("locais/", views.locais, name="locais"),
    path("locais/registrar/", views.criar_local, name="criar_local"),
    path("locais/<uuid:pk>/editar/", views.editar_local, name="editar_local"),
    path("locais/<uuid:pk>/alternar/", views.alternar_local, name="alternar_local"),
    path("equipamentos/", views.equipamentos, name="equipamentos"),
    path("equipamentos/registrar/", views.criar_equipamento, name="criar_equipamento"),
    path(
        "equipamentos/<uuid:pk>/editar/",
        views.editar_equipamento,
        name="editar_equipamento",
    ),
    path(
        "equipamentos/<uuid:pk>/alternar/",
        views.alternar_equipamento,
        name="alternar_equipamento",
    ),
    path("fornecedores/", views.fornecedores, name="fornecedores"),
    path("fornecedores/registrar/", views.criar_fornecedor, name="criar_fornecedor"),
    path(
        "fornecedores/<uuid:pk>/editar/",
        views.editar_fornecedor,
        name="editar_fornecedor",
    ),
    path(
        "fornecedores/<uuid:pk>/alternar/",
        views.alternar_fornecedor,
        name="alternar_fornecedor",
    ),
]
