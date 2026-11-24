"""Endereços das rotinas recorrentes.

A lista de rotinas é o cadastro; a de execuções é a lista de trabalho — o que
está atrasado, o que cai hoje e o que vem. As decisões — registrar, editar,
ativar, gerar a próxima ocorrência, começar, concluir, ignorar, cancelar, tratar
uma tarefa do checklist e abrir a consequência — chegam sempre por POST, nunca
por link. A ocorrência tem uma leitura própria, onde o checklist é acompanhado.
"""
from django.urls import path

from apps.rotinas import views

app_name = "rotinas"

urlpatterns = [
    path("", views.listagem, name="listagem"),
    path("registrar/", views.criar, name="criar"),
    path(
        "modelos-preventivos/",
        views.biblioteca_preventiva,
        name="biblioteca_preventiva",
    ),
    path(
        "modelos-preventivos/<slug:chave>/revisar/",
        views.revisar_modelo_preventivo,
        name="revisar_modelo_preventivo",
    ),
    path(
        "modelos-preventivos/<slug:chave>/nao-aplica/",
        views.dispensar_modelo_preventivo,
        name="dispensar_modelo_preventivo",
    ),
    path(
        "modelos-preventivos/<slug:chave>/reabrir/",
        views.reabrir_modelo_preventivo,
        name="reabrir_modelo_preventivo",
    ),
    path("execucoes/", views.execucoes, name="execucoes"),
    path("execucoes/<uuid:pk>/", views.execucao_detalhe, name="execucao"),
    path(
        "execucoes/<uuid:execucao_pk>/itens/<uuid:item_pk>/",
        views.tratar_item,
        name="tratar_item",
    ),
    path("checklists/", views.checklists, name="checklists"),
    path("checklists/registrar/", views.checklist_criar, name="checklist_criar"),
    path("checklists/<uuid:pk>/", views.checklist_detalhe, name="checklist"),
    path(
        "checklists/<uuid:pk>/editar/",
        views.checklist_editar,
        name="checklist_editar",
    ),
    path(
        "checklists/<uuid:pk>/alternar/",
        views.checklist_alternar,
        name="checklist_alternar",
    ),
    path(
        "checklists/<uuid:pk>/itens/",
        views.checklist_item_criar,
        name="checklist_item_criar",
    ),
    path(
        "checklists/<uuid:pk>/itens/<uuid:item_pk>/editar/",
        views.checklist_item_editar,
        name="checklist_item_editar",
    ),
    path(
        "checklists/<uuid:pk>/itens/<uuid:item_pk>/alternar/",
        views.checklist_item_alternar,
        name="checklist_item_alternar",
    ),
    path("<uuid:pk>/", views.detalhe, name="detalhe"),
    path("<uuid:pk>/editar/", views.editar, name="editar"),
    path("<uuid:pk>/alternar/", views.alternar_ativo, name="alternar_ativo"),
    path("<uuid:pk>/gerar/", views.executar_geracao, name="executar_geracao"),
    path("<uuid:pk>/iniciar/", views.iniciar, name="iniciar"),
    path("<uuid:pk>/concluir/", views.concluir, name="concluir"),
    path("<uuid:pk>/ignorar/", views.ignorar, name="ignorar"),
    path("<uuid:pk>/cancelar/", views.cancelar, name="cancelar"),
    path("<uuid:pk>/demanda/", views.demanda, name="demanda"),
    path("<uuid:pk>/manutencao/", views.manutencao, name="manutencao"),
]
