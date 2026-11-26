"""O comando que apaga rascunhos de compra que nunca viraram nada.

Um rascunho sem item, sem documento, sem fornecedor e sem valor não diz nada a
ninguém; passado um dia, ele é lixo. O comando alcança só esse caso e nada mais —
estes testes prendem esse limite.
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.estoque.constants import DestinoDoItem, StatusCompra
from apps.estoque.models import Compra
from apps.estoque.services import adicionar_item_compra
from apps.estoque.tests.apoio import (
    criar_condominio,
    criar_fornecedor,
    criar_pessoa,
)


class LimparRascunhosVaziosTests(TestCase):
    """O comando apaga só o rascunho vazio e antigo."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")

    def _rascunho(self, **campos):
        base = {
            "condominio": self.condominio,
            "criado_por": self.gestao,
            "status": StatusCompra.RASCUNHO,
        }
        base.update(campos)
        return Compra.objects.create(**base)

    def _envelhecer(self, compra, dias=2):
        momento = timezone.now() - timedelta(days=dias)
        Compra.objects.filter(pk=compra.pk).update(criado_em=momento)

    def test_apaga_rascunho_vazio_e_antigo(self):
        vazio = self._rascunho()
        self._envelhecer(vazio)

        call_command("limpar_rascunhos_vazios", stdout=StringIO())

        self.assertFalse(Compra.objects.filter(pk=vazio.pk).exists())

    def test_preserva_o_recente_e_o_que_tem_conteudo(self):
        recente = self._rascunho()

        com_item = self._rascunho()
        adicionar_item_compra(
            compra=com_item,
            descricao="Lâmpada",
            quantidade=Decimal("1"),
            destino=DestinoDoItem.USO_IMEDIATO,
        )
        self._envelhecer(com_item)

        com_fornecedor = self._rascunho(
            fornecedor=criar_fornecedor(self.condominio, "Elétrica Central")
        )
        self._envelhecer(com_fornecedor)

        call_command("limpar_rascunhos_vazios", stdout=StringIO())

        self.assertTrue(Compra.objects.filter(pk=recente.pk).exists())
        self.assertTrue(Compra.objects.filter(pk=com_item.pk).exists())
        self.assertTrue(Compra.objects.filter(pk=com_fornecedor.pk).exists())

    def test_dias_ajusta_a_idade_minima(self):
        recente = self._rascunho()
        self._envelhecer(recente, dias=3)

        call_command("limpar_rascunhos_vazios", "--dias", "5", stdout=StringIO())
        self.assertTrue(Compra.objects.filter(pk=recente.pk).exists())

        call_command("limpar_rascunhos_vazios", "--dias", "1", stdout=StringIO())
        self.assertFalse(Compra.objects.filter(pk=recente.pk).exists())
