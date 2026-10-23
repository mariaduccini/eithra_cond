"""Endereços das demandas operacionais.

A lista é a porta de entrada; cada demanda tem sua própria página. As ações —
registrar, editar, mudar situação, observar — são decisões da gestão e chegam
sempre por POST, nunca por link.
"""
from django.urls import path

from apps.demandas import views

app_name = "demandas"

urlpatterns = [
    path("", views.listagem, name="listagem"),
    path("registrar/", views.criar, name="criar"),
    path("<uuid:pk>/", views.detalhe, name="detalhe"),
    path("<uuid:pk>/editar/", views.editar, name="editar"),
    path("<uuid:pk>/atualizar/", views.atualizar, name="atualizar"),
    path("<uuid:pk>/situacao/", views.mudar_status, name="mudar_status"),
    path("<uuid:pk>/observar/", views.observar, name="observar"),
]
