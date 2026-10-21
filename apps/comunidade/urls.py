"""Endereços da comunidade: unidades, pessoas, veículos e vínculos.

Três cadastros e o que os liga. Cada cadastro tem sua lista, sua ficha e as
ações que mudam o que já existe — registrar, corrigir, ativar, desativar —, e
todas chegam por POST, nunca por link.

Os vínculos não têm lista própria: eles nascem e terminam dentro da ficha de
quem os liga. Por isso os endereços de vincular moram sob a ficha de origem e
os de encerrar ficam reunidos à parte, porque um vínculo tem duas pontas e a
tela de onde a ação partiu é que decide para onde voltar.
"""
from django.urls import path

from apps.comunidade import views

app_name = "comunidade"

urlpatterns = [
    # -- Unidades ----------------------------------------------------------
    path("unidades/", views.unidades, name="unidades"),
    path("unidades/registrar/", views.criar_unidade, name="criar_unidade"),
    path("unidades/<uuid:pk>/", views.unidade, name="unidade"),
    path("unidades/<uuid:pk>/editar/", views.editar_unidade, name="editar_unidade"),
    path(
        "unidades/<uuid:pk>/alternar/",
        views.alternar_unidade,
        name="alternar_unidade",
    ),
    path(
        "unidades/<uuid:pk>/pessoas/vincular/",
        views.vincular_pessoa,
        name="vincular_pessoa",
    ),
    path(
        "unidades/<uuid:pk>/veiculos/vincular/",
        views.vincular_veiculo,
        name="vincular_veiculo",
    ),
    # -- Pessoas -----------------------------------------------------------
    path("pessoas/", views.pessoas, name="pessoas"),
    path("pessoas/registrar/", views.criar_pessoa, name="criar_pessoa"),
    path("pessoas/<uuid:pk>/", views.pessoa, name="pessoa"),
    path("pessoas/<uuid:pk>/editar/", views.editar_pessoa, name="editar_pessoa"),
    path(
        "pessoas/<uuid:pk>/alternar/",
        views.alternar_pessoa,
        name="alternar_pessoa",
    ),
    path(
        "pessoas/<uuid:pk>/unidades/vincular/",
        views.vincular_unidade,
        name="vincular_unidade",
    ),
    path(
        "vinculos/pessoa-unidade/<uuid:pk>/transferir/",
        views.transferir_unidade,
        name="transferir_unidade",
    ),
    # -- Veículos ----------------------------------------------------------
    path("veiculos/", views.veiculos, name="veiculos"),
    path("veiculos/registrar/", views.criar_veiculo, name="criar_veiculo"),
    path("veiculos/<uuid:pk>/", views.veiculo, name="veiculo"),
    path("veiculos/<uuid:pk>/editar/", views.editar_veiculo, name="editar_veiculo"),
    path(
        "veiculos/<uuid:pk>/alternar/",
        views.alternar_veiculo,
        name="alternar_veiculo",
    ),
    path(
        "veiculos/<uuid:pk>/pessoas/vincular/",
        views.vincular_pessoa_veiculo,
        name="vincular_pessoa_veiculo",
    ),
    path(
        "veiculos/<uuid:pk>/unidades/vincular/",
        views.vincular_unidade_veiculo,
        name="vincular_unidade_veiculo",
    ),
    # -- Encerrar vínculos --------------------------------------------------
    path(
        "vinculos/pessoa-unidade/<uuid:pk>/encerrar/",
        views.encerrar_vinculo_pessoa_unidade,
        name="encerrar_vinculo_pessoa_unidade",
    ),
    path(
        "vinculos/veiculo-pessoa/<uuid:pk>/encerrar/",
        views.encerrar_vinculo_veiculo_pessoa,
        name="encerrar_vinculo_veiculo_pessoa",
    ),
    path(
        "vinculos/veiculo-unidade/<uuid:pk>/encerrar/",
        views.encerrar_vinculo_veiculo_unidade,
        name="encerrar_vinculo_veiculo_unidade",
    ),
]
