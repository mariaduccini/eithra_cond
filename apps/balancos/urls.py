"""Endereços da entrada de balanços e do balanço confirmado."""
from django.urls import path

from apps.balancos import views

app_name = "balancos"

urlpatterns = [
    path("", views.importacoes, name="importacoes"),
    path("importar/", views.importar, name="importar"),
    # Conferência antes de publicar: a tela única onde o mês passa a valer. Com
    # um balancete lido, ela abre com o que a leitura propôs; sem ele, abre
    # vazia, para o mês lançado à mão.
    path("lancar-sem-pdf/", views.conferencia, name="lancar_sem_pdf"),
    path(
        "lancar-sem-pdf/publicar/",
        views.publicar_conferencia,
        name="publicar_sem_pdf",
    ),
    path("<uuid:pk>/conferencia/", views.conferencia, name="conferencia"),
    path(
        "<uuid:pk>/conferencia/publicar/",
        views.publicar_conferencia,
        name="publicar_conferencia",
    ),
    path("<uuid:pk>/", views.detalhe, name="detalhe"),
    path("<uuid:pk>/reler/", views.reler, name="reler"),
    path("<uuid:pk>/arquivo/", views.acessar_arquivo, name="arquivo"),
    path("<uuid:pk>/excluir/", views.excluir, name="excluir"),
    path("<uuid:pk>/confirmar/", views.confirmar, name="confirmar"),
    path("mes/<uuid:pk>/", views.balanco, name="balanco"),
    path("mes/<uuid:pk>/arquivo/", views.arquivo_do_balanco, name="balanco_arquivo"),
    path(
        "mes/<uuid:pk>/corrigir/",
        views.corrigir_lancamento,
        name="corrigir_lancamento",
    ),
    path(
        "mes/<uuid:pk>/nao-despesa/",
        views.marcar_nao_despesa,
        name="marcar_nao_despesa",
    ),
    path(
        "mes/<uuid:pk>/adicionar/",
        views.adicionar_lancamento,
        name="adicionar_lancamento",
    ),
    # Evidências visuais: a foto entra, é ajustada, sai — e é aberta por um
    # acesso curto, gerado na hora. Nada disso é REST: são decisões da gestão.
    path(
        "mes/<uuid:pk>/evidencia/adicionar/",
        views.adicionar_evidencia,
        name="evidencia_adicionar",
    ),
    path(
        "mes/<uuid:pk>/evidencia/<uuid:evidencia_id>/editar/",
        views.editar_evidencia,
        name="evidencia_editar",
    ),
    path(
        "mes/<uuid:pk>/evidencia/<uuid:evidencia_id>/remover/",
        views.remover_evidencia,
        name="evidencia_remover",
    ),
    path(
        "mes/<uuid:pk>/evidencia/<uuid:evidencia_id>/imagem/",
        views.imagem_da_evidencia,
        name="evidencia_imagem",
    ),
]
