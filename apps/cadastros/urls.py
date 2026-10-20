"""Endereços dos cadastros básicos operacionais.

O cadastro do condomínio é a porta de entrada do módulo: é ele que dá nome a
tudo o mais. Cada cadastro tem sua lista, e as ações — registrar, corrigir,
ativar, desativar — chegam sempre por POST, nunca por link.

O catálogo de tipos de cada cadastro abre ao lado da lista a que pertence,
porque o tipo não tem vida própria: ele existe para nomear o que se cadastra.
"""
from django.urls import path

from apps.cadastros import views

app_name = "cadastros"

urlpatterns = [
    path("", views.condominio, name="condominio"),
    path("acessos/", views.acessos, name="acessos"),
    path("acessos/registrar/", views.adicionar_acesso, name="adicionar_acesso"),
    path(
        "acessos/<uuid:pk>/papel/",
        views.alterar_papel_acesso,
        name="alterar_papel_acesso",
    ),
    path(
        "acessos/<uuid:pk>/alternar/",
        views.alternar_acesso,
        name="alternar_acesso",
    ),
    path("locais/", views.locais, name="locais"),
    path("locais/registrar/", views.criar_local, name="criar_local"),
    path("locais/<uuid:pk>/editar/", views.editar_local, name="editar_local"),
    path("locais/<uuid:pk>/alternar/", views.alternar_local, name="alternar_local"),
    path("locais/tipos/", views.tipos_de_local, name="tipos_de_local"),
    path(
        "locais/tipos/registrar/",
        views.criar_tipo_local,
        name="criar_tipo_local",
    ),
    path(
        "locais/tipos/<uuid:pk>/editar/",
        views.editar_tipo_local,
        name="editar_tipo_local",
    ),
    path(
        "locais/tipos/<uuid:pk>/alternar/",
        views.alternar_tipo_local,
        name="alternar_tipo_local",
    ),
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
    path(
        "equipamentos/tipos/",
        views.tipos_de_equipamento,
        name="tipos_de_equipamento",
    ),
    path(
        "equipamentos/tipos/registrar/",
        views.criar_tipo_equipamento,
        name="criar_tipo_equipamento",
    ),
    path(
        "equipamentos/tipos/<uuid:pk>/editar/",
        views.editar_tipo_equipamento,
        name="editar_tipo_equipamento",
    ),
    path(
        "equipamentos/tipos/<uuid:pk>/alternar/",
        views.alternar_tipo_equipamento,
        name="alternar_tipo_equipamento",
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
    path(
        "fornecedores/especialidades/",
        views.especialidades,
        name="especialidades",
    ),
    path(
        "fornecedores/especialidades/registrar/",
        views.criar_especialidade,
        name="criar_especialidade",
    ),
    path(
        "fornecedores/especialidades/<uuid:pk>/editar/",
        views.editar_especialidade,
        name="editar_especialidade",
    ),
    path(
        "fornecedores/especialidades/<uuid:pk>/alternar/",
        views.alternar_especialidade,
        name="alternar_especialidade",
    ),
]
