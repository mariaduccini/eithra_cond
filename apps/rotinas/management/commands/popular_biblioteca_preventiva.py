"""Carrega os modelos preventivos iniciais sem alterar o que já existe."""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.rotinas.catalogo_preventivo import MODELOS_INICIAIS
from apps.rotinas.models import ModeloAtividadeProgramada


class Command(BaseCommand):
    help = (
        "Carrega modelos preventivos ausentes. Modelos existentes, inclusive "
        "inativos ou personalizados, não são sobrescritos."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        criados = 0
        existentes = 0
        for dados in MODELOS_INICIAIS:
            _, criado = ModeloAtividadeProgramada.objects.get_or_create(
                chave=dados["chave"], defaults=dados
            )
            criados += int(criado)
            existentes += int(not criado)

        self.stdout.write(
            f"Modelos criados: {criados}; modelos preservados: {existentes}; "
            f"total inicial: {len(MODELOS_INICIAIS)}."
        )
