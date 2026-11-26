"""Concluir uma demanda de compra oferece registrar a compra junto.

Uma demanda que fala de compra, ainda sem compra ligada, mostra as duas opções
no modal de concluir; a demanda comum não muda de tela. O detalhe mostra o link
da compra quando ela existe. O motor determinístico continua encerrando a
demanda — a oferta é só uma porta a mais, nunca um bloqueio.
"""
from datetime import date

from django.test import TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.demandas.constants import StatusDemanda
from apps.demandas.services import criar_demanda
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)
from apps.estoque.services import criar_compra


class BaseDaCompraDaDemanda(TestCase):
    """Uma gestão ligada ao condomínio e demandas de teste."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    def _demanda(self, **campos):
        base = {
            "condominio": self.condominio,
            "criado_por": self.gestao,
            "titulo": "Demanda de teste",
        }
        base.update(campos)
        return criar_demanda(**base)

    def _abrir_detalhe(self, demanda):
        return self.client.get(reverse("demandas:detalhe", args=[demanda.pk]))


class OpcoesAoConcluirTests(BaseDaCompraDaDemanda):
    """Quem vê o modal de concluir vê — ou não — a oferta de registrar compra."""

    def test_demanda_de_compra_sem_compra_oferece_as_duas_opcoes(self):
        demanda = self._demanda(titulo="Comprar lâmpadas para o corredor")

        resposta = self._abrir_detalhe(demanda)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Registrar compra agora")
        self.assertContains(resposta, "Concluir sem registrar")

    def test_demanda_comum_nao_oferece_registrar_compra(self):
        demanda = self._demanda(titulo="Vazamento no banheiro")

        resposta = self._abrir_detalhe(demanda)

        self.assertContains(resposta, "Concluir demanda")
        self.assertNotContains(resposta, "Registrar compra agora")
        self.assertNotContains(resposta, "Concluir sem registrar")

    def test_demanda_de_compra_ja_ligada_nao_oferece_de_novo(self):
        demanda = self._demanda(titulo="Comprar lâmpadas para o corredor")
        criar_compra(
            condominio=self.condominio,
            criado_por=self.gestao,
            data_compra=date.today(),
            demanda_origem=demanda,
        )

        resposta = self._abrir_detalhe(demanda)

        self.assertNotContains(resposta, "Registrar compra agora")

    def test_o_menu_do_detalhe_oferece_registrar_compra(self):
        demanda = self._demanda(titulo="Comprar lâmpadas para o corredor")

        resposta = self._abrir_detalhe(demanda)

        self.assertContains(resposta, "Registrar compra")


class ConcluirERegistrarTests(BaseDaCompraDaDemanda):
    """Registrar compra agora conclui a demanda e abre o formulário de compra."""

    def test_registrar_compra_agora_conclui_e_abre_o_formulario(self):
        demanda = self._demanda(titulo="Comprar lâmpadas para o corredor")

        resposta = self.client.post(
            reverse("demandas:concluir", args=[demanda.pk]),
            {"registrar_compra": "1"},
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(
            resposta.url,
            f"{reverse('compras:listagem')}?abrir=manual&demanda={demanda.pk}",
        )
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.CONCLUIDA)

    def test_concluir_sem_registrar_segue_para_o_historico(self):
        demanda = self._demanda(titulo="Comprar lâmpadas para o corredor")

        resposta = self.client.post(
            reverse("demandas:concluir", args=[demanda.pk]), {}
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(
            resposta.url,
            f"{reverse('demandas:detalhe', args=[demanda.pk])}?aba=historico",
        )
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.CONCLUIDA)


class LinkDaCompraNoDetalheTests(BaseDaCompraDaDemanda):
    """O detalhe da demanda mostra a compra ligada, com link para ela."""

    def test_o_detalhe_mostra_o_link_da_compra(self):
        demanda = self._demanda(titulo="Comprar lâmpadas para o corredor")
        compra = criar_compra(
            condominio=self.condominio,
            criado_por=self.gestao,
            data_compra=date.today(),
            demanda_origem=demanda,
        )

        resposta = self._abrir_detalhe(demanda)

        self.assertContains(resposta, "Compra registrada")
        self.assertContains(resposta, reverse("compras:detalhe", args=[compra.pk]))
