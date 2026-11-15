"""Endereços da capacidade transversal de comunicação.

A lista é a porta de entrada; cada comunicado tem sua própria página. Escrever,
revisar e gerar o card são decisões da gestão e chegam sempre por POST, nunca por
link. Só a leitura do card gerado é um GET: ela não muda nada, apenas entrega o
arquivo a quem pode ver o comunicado.
"""
from django.urls import path

from apps.comunicacao import views

app_name = "comunicacao"

urlpatterns = [
    path("", views.listagem, name="listagem"),
    path("novo/", views.criar, name="criar"),
    path("<uuid:pk>/", views.detalhe, name="detalhe"),
    path("<uuid:pk>/editar/", views.editar, name="editar"),
    path("<uuid:pk>/gerar/", views.gerar, name="gerar"),
    path("<uuid:pk>/arquivo/", views.arquivo, name="arquivo"),
]
