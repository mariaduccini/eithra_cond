from django.urls import path

from . import views

app_name = "portaria"

urlpatterns = [
    path("", views.painel, name="painel"),
    path("interpretar/", views.interpretar_relato, name="interpretar"),
    path("transcrever/", views.transcrever_audio, name="transcrever"),
    path("confirmar/", views.confirmar_relato, name="confirmar"),
    path("autorizadores/", views.buscar_autorizador, name="buscar_autorizador"),
    path("busca/", views.buscar_portaria, name="buscar_portaria"),
    path("esperados/registrar/", views.criar_esperado, name="criar_esperado"),
    path(
        "chegadas/nao-prevista/",
        views.criar_chegada_nao_prevista,
        name="criar_chegada_nao_prevista",
    ),
    path(
        "autorizacoes/<uuid:pk>/verificar/",
        views.verificar,
        name="verificar",
    ),
    path("esperados/<uuid:pk>/chegou/", views.chegou, name="chegou"),
    path("acessos/<uuid:pk>/saiu/", views.saiu, name="saiu"),
]
