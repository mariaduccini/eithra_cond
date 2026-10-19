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
    path("<uuid:pk>/", views.detalhe, name="detalhe"),
    path("<uuid:pk>/editar/", views.editar, name="editar"),
    path("<uuid:pk>/situacao/", views.mudar_status, name="mudar_status"),
    path("<uuid:pk>/concluir/", views.concluir, name="concluir"),
    path("<uuid:pk>/cancelar/", views.cancelar, name="cancelar"),
    path("<uuid:pk>/alternar/", views.alternar_ativo, name="alternar_ativo"),
]
