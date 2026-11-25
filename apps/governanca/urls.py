"""Endereços da governança.

Contratos, garantias e documentos são obrigações que o condomínio assumiu. Cada
um tem sua lista, e as decisões — registrar, corrigir, ativar, desativar —
chegam sempre por POST, nunca por link. Os catálogos de tipos continuam
amarrados ao domínio de governança, mas a navegação até eles agora passa pelo
agrupador ``Tipos e categorias``.
"""
from django.urls import path

from apps.governanca import views

app_name = "governanca"

urlpatterns = [
    path("vencimentos/", views.vencimentos, name="vencimentos"),
    path("contratos/", views.contratos, name="contratos"),
    path("contratos/registrar/", views.criar_contrato, name="criar_contrato"),
    path("contratos/<uuid:pk>/editar/", views.editar_contrato, name="editar_contrato"),
    path(
        "contratos/<uuid:pk>/alternar/",
        views.alternar_contrato,
        name="alternar_contrato",
    ),
    path("contratos/tipos/", views.tipos_de_contrato, name="tipos_de_contrato"),
    path(
        "contratos/tipos/registrar/",
        views.criar_tipo_contrato,
        name="criar_tipo_contrato",
    ),
    path(
        "contratos/tipos/<uuid:pk>/editar/",
        views.editar_tipo_contrato,
        name="editar_tipo_contrato",
    ),
    path(
        "contratos/tipos/<uuid:pk>/alternar/",
        views.alternar_tipo_contrato,
        name="alternar_tipo_contrato",
    ),
    path("garantias/", views.garantias, name="garantias"),
    path("garantias/registrar/", views.criar_garantia, name="criar_garantia"),
    path("garantias/<uuid:pk>/editar/", views.editar_garantia, name="editar_garantia"),
    path(
        "garantias/<uuid:pk>/alternar/",
        views.alternar_garantia,
        name="alternar_garantia",
    ),
    path("documentos/", views.documentos, name="documentos"),
    path("documentos/registrar/", views.criar_documento, name="criar_documento"),
    path(
        "documentos/<uuid:pk>/editar/",
        views.editar_documento,
        name="editar_documento",
    ),
    path(
        "documentos/<uuid:pk>/alternar/",
        views.alternar_documento,
        name="alternar_documento",
    ),
    path(
        "documentos/<uuid:pk>/arquivos/",
        views.anexar_arquivo,
        name="anexar_arquivo",
    ),
    path("documentos/tipos/", views.tipos_de_documento, name="tipos_de_documento"),
    path(
        "documentos/tipos/registrar/",
        views.criar_tipo_documento,
        name="criar_tipo_documento",
    ),
    path(
        "documentos/tipos/<uuid:pk>/editar/",
        views.editar_tipo_documento,
        name="editar_tipo_documento",
    ),
    path(
        "documentos/tipos/<uuid:pk>/alternar/",
        views.alternar_tipo_documento,
        name="alternar_tipo_documento",
    ),
]
