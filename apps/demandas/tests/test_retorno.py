"""O retorno à parte interessada: quem espera, e se já foi avisado.

O domínio já diz por quem a demanda espera (``aguardando_tipo``) e já registra,
no próprio histórico, o comunicado que saiu dela — o único aviso que existe hoje.
Estes testes prendem a leitura derivada: não necessário, pendente e enviado saem
do aguardando e do histórico, sem campo novo, sem tabela e sem nada gravado por
causa da leitura.

O que ainda não tem sinal no domínio — o canal de envio e o "dispensado" — não é
inventado aqui; é dívida declarada na documentação da onda.
"""
from django.test import TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.demandas.constants import AguardandoTipo
from apps.demandas.services import (
    atualizar_acompanhamento,
    criar_demanda,
    registrar_comunicado,
)
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)
from apps.demandas.views import _retorno_para_tela


class RetornoDerivadoTests(TestCase):
    """A leitura do retorno vem do aguardando e do histórico, e nada grava."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Fernanda")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.demanda = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Orçamento do portão eletrônico",
        )

    def _aguardar(self, tipo=AguardandoTipo.FORNECEDOR, descricao=""):
        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            aguardando_tipo=tipo,
            aguardando_descricao=descricao,
        )
        self.demanda.refresh_from_db()

    def _retorno(self):
        return _retorno_para_tela(self.demanda)

    # -- estados derivados ----------------------------------------------------

    def test_sem_espera_o_retorno_nao_e_necessario(self):
        self.assertEqual(self._retorno()["estado"], "nao_necessario")

    def test_quem_espera_torna_o_retorno_pendente(self):
        self._aguardar(AguardandoTipo.FORNECEDOR)
        leitura = self._retorno()
        self.assertEqual(leitura["estado"], "pendente")
        self.assertEqual(leitura["quem"], "Fornecedor")
        self.assertIsNone(leitura["retorno"])

    def test_comunicado_apos_a_espera_marca_enviado(self):
        self._aguardar(AguardandoTipo.MORADOR)
        registrar_comunicado(self.demanda, self.gestao, "Aviso enviado ao morador")

        leitura = self._retorno()
        self.assertEqual(leitura["estado"], "enviado")
        self.assertEqual(leitura["quem"], "Morador")
        self.assertIsNotNone(leitura["retorno"])
        self.assertEqual(leitura["retorno"]["quem"], "Fernanda")
        self.assertIn("Hoje", leitura["retorno"]["quando"])

    def test_comunicado_anterior_a_espera_nao_conta(self):
        """Um aviso antigo não responde por uma espera que começou depois."""
        registrar_comunicado(self.demanda, self.gestao, "Aviso de antes")
        self._aguardar(AguardandoTipo.FORNECEDOR)
        self.assertEqual(self._retorno()["estado"], "pendente")

    def test_espera_redefinida_exige_novo_retorno(self):
        self._aguardar(AguardandoTipo.FORNECEDOR)
        registrar_comunicado(self.demanda, self.gestao, "Aviso ao fornecedor")
        self.assertEqual(self._retorno()["estado"], "enviado")

        # A espera mudou de dono: o retorno que valeu para o fornecedor não
        # responde mais por quem passou a esperar agora.
        self._aguardar(AguardandoTipo.MORADOR)
        self.assertEqual(self._retorno()["estado"], "pendente")

    def test_a_leitura_nao_grava_nada(self):
        """Não existe campo nem evento de retorno: a leitura é só leitura."""
        self._aguardar(AguardandoTipo.FORNECEDOR)
        registrar_comunicado(self.demanda, self.gestao, "Aviso")
        self.demanda.refresh_from_db()

        antes = self.demanda.historico.count()
        atualizado_antes = self.demanda.atualizado_em
        _retorno_para_tela(self.demanda)
        self.demanda.refresh_from_db()

        self.assertEqual(self.demanda.historico.count(), antes)
        self.assertEqual(self.demanda.atualizado_em, atualizado_antes)


class RetornoTelaTests(TestCase):
    """A leitura aparece no detalhe, e só quando há espera a responder."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Fernanda")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)
        self.demanda = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Infiltração na garagem",
        )

    def _html(self):
        resposta = self.client.get(
            reverse("demandas:detalhe", args=[self.demanda.pk])
        )
        self.assertEqual(resposta.status_code, 200)
        return resposta.content.decode()

    def test_sem_espera_a_linha_nao_ocupa_a_pagina(self):
        self.assertNotIn("Retorno à parte interessada", self._html())

    def test_espera_pendente_aparece_na_pagina(self):
        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            aguardando_tipo=AguardandoTipo.FORNECEDOR,
        )
        html = self._html()
        self.assertIn("Retorno à parte interessada", html)
        self.assertIn("Pendente", html)
        self.assertIn("aguardando Fornecedor", html)

    def test_retorno_enviado_aparece_com_quem_registrou(self):
        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            aguardando_tipo=AguardandoTipo.MORADOR,
        )
        registrar_comunicado(self.demanda, self.gestao, "Aviso ao morador")
        html = self._html()
        self.assertIn("Retorno à parte interessada", html)
        self.assertIn("Enviado", html)
        self.assertIn("por Fernanda", html)
