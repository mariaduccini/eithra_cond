"""Relê as importações que ficaram sem leitura.

Um balancete escaneado pode ter chegado antes de o OCR local existir no
ambiente e ficado sem leitura automática. Este comando pega essas importações
— as de layout desconhecido — e repete a detecção e a leitura sobre o arquivo
já guardado, sem novo upload.

O que ele **não** faz, de propósito:

* não toca em balanço já confirmado nem em importação com desfecho — a
  releitura só vale para o que ainda não virou leitura;
* não altera totais oficiais: a releitura alimenta a conferência, e quem
  confirma o mês continua sendo a gestão.

A saída diz, linha a linha, o que aconteceu com cada arquivo:
``arquivo → layout → situação``.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.balancos.constants import LayoutBalanco, StatusImportacao
from apps.balancos.detector import ErroDeDeteccaoDeLayout
from apps.balancos.models import ImportacaoBalanco
from apps.balancos.services import ErroDeReleitura, reler_importacao


class Command(BaseCommand):
    help = (
        "Repete a detecção e a leitura das importações que ficaram sem leitura "
        "(layout desconhecido), sem novo upload."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--desconhecidas",
            action="store_true",
            help="Relê todas as importações de layout desconhecido, sem desfecho.",
        )

    def handle(self, *args, **opcoes):
        if not opcoes["desconhecidas"]:
            self.stdout.write(
                "Nada a fazer. Use --desconhecidas para reler as importações de "
                "layout desconhecido."
            )
            return

        importacoes = (
            ImportacaoBalanco.objects.select_related("condominio")
            .filter(
                layout_detectado=LayoutBalanco.DESCONHECIDO,
                status__in=(StatusImportacao.PRONTO, StatusImportacao.ERRO),
                confirmado_em__isnull=True,
                descartado_em__isnull=True,
            )
            .order_by("condominio__nome", "criado_em")
        )

        total = 0
        for importacao in importacoes:
            total += 1
            try:
                reler_importacao(importacao)
            except (ErroDeReleitura, ErroDeDeteccaoDeLayout) as erro:
                self.stderr.write(f"{importacao.nome_original} → falhou: {erro}")
                continue
            importacao.refresh_from_db()
            self.stdout.write(
                f"{importacao.nome_original} → "
                f"{importacao.get_layout_detectado_display()} → "
                f"{importacao.situacao_para_a_gestao}"
            )

        if not total:
            self.stdout.write("Nenhuma importação desconhecida para reler.")
            return

        self.stdout.write(f"\n{total} importação(ões) relida(s).")
