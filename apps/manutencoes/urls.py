"""Endereços das manutenções operacionais.

A lista é a porta de entrada; cada manutenção tem sua própria página. As ações —
registrar, editar, mudar situação, concluir, cancelar e retirar da operação —
são decisões da gestão e chegam sempre por POST, nunca por link.
"""
from django.urls import path

from apps.manutencoes import views

app_name = "manutencoes"

urlpatterns = [
    path("", views.listagem, name="listagem"),
    path("registrar/", views.criar, name="criar"),
    path("transcrever/", views.transcrever_audio, name="transcrever"),
    path("planos/", views.planos, name="planos"),
    path("planos/registrar/", views.criar_plano, name="criar_plano"),
    path("planos/<uuid:pk>/editar/", views.editar_plano, name="editar_plano"),
    # A biblioteca de modelos de conferência vive dentro das preventivas: é uma
    # configuração estrutural do condomínio, não um módulo próprio.
    path(
        "planos/modelos/",
        views.modelos_de_conferencia,
        name="modelos_conferencia",
    ),
    path(
        "planos/modelos/registrar/",
        views.novo_modelo,
        name="criar_modelo_conferencia",
    ),
    path(
        "planos/modelos/<uuid:pk>/editar/",
        views.editar_modelo,
        name="editar_modelo_conferencia",
    ),
    path(
        "planos/modelos/<uuid:pk>/alternar/",
        views.alternar_modelo,
        name="alternar_modelo_conferencia",
    ),
    path("<uuid:pk>/", views.detalhe, name="detalhe"),
    path("<uuid:pk>/editar/", views.editar, name="editar"),
    path("<uuid:pk>/situacao/", views.mudar_status, name="mudar_status"),
    path("<uuid:pk>/execucao/", views.executar, name="registrar_execucao"),
    path("<uuid:pk>/conferir/", views.conferir, name="conferir"),
    path("<uuid:pk>/evidencias/", views.anexar_evidencia, name="adicionar_evidencia"),
    path(
        "<uuid:pk>/evidencias/<uuid:evidencia_id>/",
        views.evidencia_arquivo,
        name="evidencia_arquivo",
    ),
    path("<uuid:pk>/concluir/", views.concluir, name="concluir"),
    path("<uuid:pk>/cancelar/", views.cancelar, name="cancelar"),
    path("<uuid:pk>/alternar/", views.alternar_ativo, name="alternar_ativo"),
]
