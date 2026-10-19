"""Quem vê e quem decide no estoque e nas compras.

A resposta vem sempre do vínculo ativo da pessoa com o condomínio. O que se
fixa aqui é o desenho aprovado: a operação enxerga o estoque, mas não as
compras; a supervisão opera as compras, mas não cancela; cancelar é de quem
responde pelo condomínio.
"""
from datetime import date

from django.test import TestCase
from django.utils import timezone

from apps.condominios.constants import Papel
from apps.estoque.constants import StatusCompra
from apps.estoque.models import Compra
from apps.estoque.permissoes import (
    TIPOS_DE_GESTAO,
    condominios_para_ver_compras,
    manutencoes_para_retirada,
    pode_cancelar_compra,
    pode_confirmar_compra,
    pode_gerenciar_itens,
    pode_registrar_movimento,
    pode_registrar_saida_para_manutencao,
    pode_ver_compras,
    pode_ver_estoque,
    pode_ver_item,
    tipos_de_movimento_permitidos,
)
from apps.estoque.tests.apoio import (
    criar_condominio,
    criar_item_estoque,
    criar_pessoa,
    criar_vinculo,
)
from apps.manutencoes.services import criar_manutencao


class BaseDePermissoes(TestCase):
    """Um condomínio, um item e uma pessoa de cada papel."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.sindica = self._pessoa("sindica@exemplo.test", Papel.SINDICA)
        self.supervisora = self._pessoa("sup@exemplo.test", Papel.SUPERVISORA)
        self.operacional = self._pessoa("op@exemplo.test", Papel.OPERACIONAL)
        self.porteiro = self._pessoa("porteiro@exemplo.test", Papel.PORTEIRO)
        self.morador = self._pessoa("morador@exemplo.test", Papel.MORADOR)
        self.item = criar_item_estoque(self.condominio, self.sindica)

    def _pessoa(self, email, papel):
        pessoa = criar_pessoa(email, papel.label)
        criar_vinculo(pessoa, self.condominio, papel=papel)
        return pessoa

    def _manutencao(self, responsavel=None):
        return criar_manutencao(
            condominio=self.condominio,
            criado_por=self.sindica,
            titulo="Serviço de teste",
            responsavel=responsavel,
        )

    def _compra(self, status=StatusCompra.RASCUNHO):
        return Compra.objects.create(
            condominio=self.condominio,
            data_compra=date.today(),
            status=status,
            confirmada_em=timezone.now() if status == StatusCompra.CONFIRMADA else None,
        )


class VerEstoqueTests(BaseDePermissoes):
    """O estoque é de quem responde, supervisiona ou opera."""

    def test_quem_ve_o_estoque(self):
        for pessoa in (self.sindica, self.supervisora, self.operacional):
            self.assertTrue(pode_ver_estoque(pessoa, self.condominio))

    def test_quem_nao_ve_o_estoque(self):
        for pessoa in (self.porteiro, self.morador):
            self.assertFalse(pode_ver_estoque(pessoa, self.condominio))

    def test_ver_item_segue_o_condominio_do_item(self):
        self.assertTrue(pode_ver_item(self.sindica, self.item))
        self.assertFalse(pode_ver_item(self.morador, self.item))
        self.assertFalse(pode_ver_item(self.sindica, None))


class VerComprasTests(BaseDePermissoes):
    """A operação não entra em compras nesta fase — nem enxerga."""

    def test_operacao_nao_ve_compras(self):
        self.assertFalse(pode_ver_compras(self.operacional, self.condominio))
        self.assertFalse(
            condominios_para_ver_compras(self.operacional).exists()
        )

    def test_gestao_e_supervisao_veem_compras(self):
        self.assertTrue(pode_ver_compras(self.sindica, self.condominio))
        self.assertTrue(pode_ver_compras(self.supervisora, self.condominio))

    def test_portaria_e_moradia_nao_veem_compras(self):
        self.assertFalse(pode_ver_compras(self.porteiro, self.condominio))
        self.assertFalse(pode_ver_compras(self.morador, self.condominio))


class GerenciarItemTests(BaseDePermissoes):
    """Cadastrar e registrar movimento de gestão é de quem opera."""

    def test_operacao_nao_gerencia_nem_movimenta(self):
        self.assertFalse(pode_gerenciar_itens(self.operacional, self.condominio))
        self.assertFalse(pode_registrar_movimento(self.operacional, self.item))
        self.assertEqual(tipos_de_movimento_permitidos(self.operacional, self.item), frozenset())

    def test_gestao_e_supervisao_movimentam(self):
        for pessoa in (self.sindica, self.supervisora):
            self.assertTrue(pode_gerenciar_itens(pessoa, self.condominio))
            self.assertEqual(
                tipos_de_movimento_permitidos(pessoa, self.item),
                frozenset(TIPOS_DE_GESTAO),
            )


class RetiradaParaManutencaoTests(BaseDePermissoes):
    """A operação só retira para as manutenções que são dela."""

    def test_gestao_retira_para_qualquer_manutencao(self):
        manutencao = self._manutencao()
        self.assertTrue(
            pode_registrar_saida_para_manutencao(self.sindica, manutencao, self.item)
        )

    def test_operacao_retira_para_a_propria_manutencao(self):
        manutencao = self._manutencao(responsavel=self.operacional)
        self.assertTrue(
            pode_registrar_saida_para_manutencao(
                self.operacional, manutencao, self.item
            )
        )

    def test_operacao_nao_retira_para_manutencao_alheia(self):
        manutencao = self._manutencao(responsavel=self.sindica)
        self.assertFalse(
            pode_registrar_saida_para_manutencao(
                self.operacional, manutencao, self.item
            )
        )

    def test_manutencoes_para_retirada_respeita_o_papel(self):
        minha = self._manutencao(responsavel=self.operacional)
        outra = self._manutencao(responsavel=self.sindica)

        para_gestao = manutencoes_para_retirada(self.sindica)
        self.assertIn(minha, para_gestao)
        self.assertIn(outra, para_gestao)

        para_operacao = manutencoes_para_retirada(self.operacional)
        self.assertIn(minha, para_operacao)
        self.assertNotIn(outra, para_operacao)

    def test_sem_vinculo_nao_retira(self):
        self.assertFalse(
            manutencoes_para_retirada(self.porteiro).exists()
        )


class ConfirmarECancelarCompraTests(BaseDePermissoes):
    """Confirmar é de quem opera; cancelar é de quem responde pelo condomínio."""

    def test_supervisao_confirma_mas_nao_cancela(self):
        compra = self._compra()
        self.assertTrue(pode_confirmar_compra(self.supervisora, compra))
        self.assertFalse(pode_cancelar_compra(self.supervisora, compra))

    def test_gestao_confirma_e_cancela_rascunho(self):
        compra = self._compra()
        self.assertTrue(pode_confirmar_compra(self.sindica, compra))
        self.assertTrue(pode_cancelar_compra(self.sindica, compra))

    def test_compra_confirmada_nao_se_cancela_nem_se_confirma(self):
        compra = self._compra(status=StatusCompra.CONFIRMADA)
        self.assertFalse(pode_cancelar_compra(self.sindica, compra))
        self.assertFalse(pode_confirmar_compra(self.sindica, compra))

    def test_operacao_nao_confirma_nem_cancela(self):
        compra = self._compra()
        self.assertFalse(pode_confirmar_compra(self.operacional, compra))
        self.assertFalse(pode_cancelar_compra(self.operacional, compra))


class VinculoInativoTests(BaseDePermissoes):
    """Vínculo desativado não autoriza nada."""

    def test_vinculo_inativo_nao_ve_nem_opera(self):
        pessoa = criar_pessoa("inativo@exemplo.test", "Inativo")
        criar_vinculo(pessoa, self.condominio, papel=Papel.SINDICA, ativo=False)

        self.assertFalse(pode_ver_estoque(pessoa, self.condominio))
        self.assertFalse(pode_gerenciar_itens(pessoa, self.condominio))
        self.assertFalse(
            condominios_para_ver_compras(pessoa).exists()
        )
