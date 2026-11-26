"""A baixa de estoque junto do trabalho e a reposição com histórico.

O que se fixa aqui: os chips de material já vêm com a quantidade sugerida pelo
relato; a retirada rápida grava a saída com local e quem levou; o histórico
separa o que saiu com destino do que saiu sem destino; e a contagem lança o
ajuste datado — sem tocar no que ficou em branco.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.estoque.constants import TipoMovimentacaoEstoque
from apps.estoque.models import MovimentacaoEstoque
from apps.estoque.services import (
    chips_de_material,
    historico_de_reposicao,
    registrar_movimentacao,
    registrar_saida_do_trabalho,
    saidas_do_item,
)
from apps.estoque.tests.apoio import (
    criar_condominio,
    criar_item_estoque,
    criar_local,
    criar_pessoa,
    criar_vinculo,
)


class BaseDeEstoque(TestCase):
    """Uma gestão ligada a um condomínio, com itens e um lugar de onde sai."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)
        self.item = criar_item_estoque(self.condominio, self.gestao)

    def _entrada(self, item, quantidade, motivo="Saldo inicial"):
        return registrar_movimentacao(
            condominio=self.condominio,
            item=item,
            usuario=self.gestao,
            tipo=TipoMovimentacaoEstoque.AJUSTE_ENTRADA,
            quantidade=Decimal(quantidade),
            motivo=motivo,
        )

    def _saida(self, item, quantidade, local=None, tipo=None):
        return registrar_saida_do_trabalho(
            condominio=self.condominio,
            item=item,
            usuario=self.gestao,
            quantidade=Decimal(quantidade),
            tipo=tipo or TipoMovimentacaoEstoque.SAIDA_USO,
            motivo="Uso de teste",
            local=local,
        )


class ChipsDeMaterialTests(BaseDeEstoque):
    """Os chips do modal de conclusão: ordem de uso e palpite de quantidade."""

    def test_relato_sugere_a_quantidade_do_item(self):
        """«troquei 2 lâmpadas» pré-marca o chip com 2."""
        chips = chips_de_material(self.condominio, texto="troquei 2 lâmpadas")
        self.assertEqual(len(chips), 1)
        self.assertEqual(chips[0]["quantidade"], 2)

    def test_o_mais_usado_vem_primeiro(self):
        outro = criar_item_estoque(self.condominio, self.gestao, nome="Fita isolante")
        self._saida(outro, 1)
        self._saida(outro, 1)
        self._saida(self.item, 1)

        chips = chips_de_material(self.condominio)
        self.assertEqual(chips[0]["nome"], "Fita isolante")

    def test_condominio_sem_item_nao_tem_chip(self):
        vazio = criar_condominio("Vazio")
        self.assertEqual(chips_de_material(vazio), [])


class RetiradaRapidaTests(BaseDeEstoque):
    """A retirada de um passo grava a saída com local e quem levou."""

    def test_registrar_grava_saida_com_local_e_usuario(self):
        local = criar_local(self.condominio, "Almoxarifado")
        resposta = self.client.post(
            reverse("estoque:retirar_rapido"),
            {
                "condominio": str(self.condominio.pk),
                "material": f"{self.item.pk}:2",
                "local": str(local.pk),
            },
        )
        self.assertEqual(resposta.status_code, 302)

        saida = MovimentacaoEstoque.objects.get(
            tipo=TipoMovimentacaoEstoque.SAIDA_USO
        )
        self.assertEqual(saida.item, self.item)
        self.assertEqual(saida.quantidade, Decimal("2"))
        self.assertEqual(saida.local, local)
        self.assertEqual(saida.usuario, self.gestao)

    def test_sem_material_nao_grava_nada(self):
        self.client.post(
            reverse("estoque:retirar_rapido"),
            {"condominio": str(self.condominio.pk), "material": ""},
        )
        self.assertFalse(MovimentacaoEstoque.objects.exists())


class HistoricoDaReposicaoTests(BaseDeEstoque):
    """O histórico separa o que saiu com destino do que ninguém explicou."""

    def test_calcula_as_saidas_sem_destino(self):
        local = criar_local(self.condominio, "Almoxarifado")
        self._entrada(self.item, 10)
        self._saida(self.item, 3, local=local)
        self._saida(self.item, 2)

        historico = historico_de_reposicao(self.item)
        self.assertEqual(historico["saidas"], "5 unidade")
        self.assertEqual(historico["sem_destino"], "2 unidade")

    def test_a_lista_curta_traz_local_e_quem(self):
        local = criar_local(self.condominio, "Almoxarifado")
        self._entrada(self.item, 10)
        self._saida(self.item, 1, local=local)

        saida = saidas_do_item(self.item).first()
        self.assertEqual(saida.local, local)
        self.assertEqual(saida.usuario, self.gestao)


class ContagemTests(BaseDeEstoque):
    """A contagem lança o ajuste datado e ignora o campo em branco."""

    def test_ajusta_o_contado_e_ignora_o_branco(self):
        sem_contagem = criar_item_estoque(
            self.condominio, self.gestao, nome="Fita isolante"
        )
        resposta = self.client.post(
            reverse("estoque:contagem"),
            {
                "item": [str(self.item.pk), str(sem_contagem.pk)],
                f"quantidade-{self.item.pk}": "5",
                f"quantidade-{sem_contagem.pk}": "",
            },
        )
        self.assertEqual(resposta.status_code, 302)

        ajuste = MovimentacaoEstoque.objects.get()
        self.assertEqual(ajuste.item, self.item)
        self.assertEqual(ajuste.quantidade, Decimal("5"))
        self.assertEqual(ajuste.tipo, TipoMovimentacaoEstoque.AJUSTE_ENTRADA)
        self.assertIn("Diferença na contagem de", ajuste.motivo)
        self.assertFalse(
            MovimentacaoEstoque.objects.filter(item=sem_contagem).exists()
        )
