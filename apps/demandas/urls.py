"""Endereços das demandas operacionais.

A lista é a porta de entrada; cada demanda tem sua própria página. As ações —
registrar, editar, mudar situação, observar, anexar evidência — são decisões da
gestão e chegam sempre por POST, nunca por link. Só a leitura do arquivo de uma
evidência é um GET: ela não muda nada, apenas entrega o que já está guardado a
quem pode ver a demanda.
"""
from django.urls import path

from apps.demandas import views

app_name = "demandas"

urlpatterns = [
    path("", views.listagem, name="listagem"),
    path("registrar/", views.criar, name="criar"),
    path("transcrever/", views.transcrever_audio, name="transcrever"),
    path("concluir-em-lote/", views.concluir_em_lote, name="concluir_em_lote"),
    # O acompanhamento do morador vive fora do login: o token no endereço é a
    # credencial. A leitura é um GET livre; a resposta à avaliação chega por
    # POST e também não pede conta — é o morador quem responde.
    path(
        "acompanhar/<uuid:token>/",
        views.acompanhar_publico,
        name="acompanhar_publico",
    ),
    path("acompanhar/<uuid:token>/avaliar/", views.avaliar, name="avaliar"),
    path("<uuid:pk>/", views.detalhe, name="detalhe"),
    path("<uuid:pk>/editar/", views.editar, name="editar"),
    path("<uuid:pk>/atualizar/", views.atualizar, name="atualizar"),
    path("<uuid:pk>/concluir/", views.concluir, name="concluir"),
    path("<uuid:pk>/situacao/", views.mudar_status, name="mudar_status"),
    path("<uuid:pk>/observar/", views.observar, name="observar"),
    path("<uuid:pk>/acompanhamento/", views.acompanhar, name="acompanhar"),
    # A pré-visualização da gestão: cria o acompanhamento do morador quando ele
    # ainda não existe e leva à página pública, para a gestão ver o que o morador
    # vê. É um ato da gestão, e por isso chega por POST.
    path(
        "<uuid:pk>/acompanhamento/previa/",
        views.pre_visualizar_acompanhamento,
        name="pre_visualizar_acompanhamento",
    ),
    path("<uuid:pk>/evidencia/adicionar/", views.adicionar_evidencia, name="adicionar_evidencia"),
    path(
        "<uuid:pk>/evidencia/<uuid:evidencia_id>/arquivo/",
        views.evidencia_arquivo,
        name="evidencia_arquivo",
    ),
]
