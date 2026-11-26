"""Apaga rascunhos de compra que nasceram vazios e nunca viraram nada.

Um rascunho fantasma é o que ficou pela metade sem nunca ter tido conteúdo: sem
itens, sem documento, sem fornecedor e sem valor. Depois de um dia parado, ele
não é mais trabalho em andamento — é lixo na lista. O comando respeita o dia de
espera para não apagar o que alguém acabou de abrir.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.estoque.constants import StatusCompra
from apps.estoque.models import Compra


class Command(BaseCommand):
    help = "Apaga rascunhos de compra sem itens, sem documento, sem fornecedor e sem valor."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dias",
            type=int,
            default=1,
            help="Idade mínima, em dias, para um rascunho ser considerado fantasma.",
        )

    def handle(self, *args, **opcoes):
        limite = timezone.now() - timedelta(days=opcoes["dias"])
        vazios = Compra.objects.filter(
            status=StatusCompra.RASCUNHO,
            criado_em__lt=limite,
            fornecedor__isnull=True,
            valor_reembolso__isnull=True,
            itens__isnull=True,
            documentos__isnull=True,
        )

        quantidade = vazios.count()
        if quantidade:
            vazios.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"{quantidade} rascunho(s) sem conteúdo apagado(s)."
            )
        )
