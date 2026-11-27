"""As ações rápidas do detalhe: a barra do celular e as sugestões do momento.

No celular, as ações do dia a dia deixam de viver escondidas no menu de três
pontos: ficam numa barra fixa no rodapé, com ícones e rótulo curto. Acima dela,
até três atalhos por regra — sem IA — dizem o que a demanda pede agora. Nada
aqui decide o destino da demanda: cada ação passa pelas mesmas portas de sempre.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.condominios.constants import Papel
from apps.demandas.constants import StatusDemanda
from apps.demandas.models import HistoricoDemanda
from apps.demandas.services import alterar_status, criar_demanda
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)


class BaseDeAcoesRapidas(TestCase):
    """Uma demanda de teste e a gestão do condomínio logada."""

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

    def _detalhe(self, demanda):
        return self.client.get(reverse("demandas:detalhe", args=[demanda.pk]))


class BarraRapidaTests(BaseDeAcoesRapidas):
    """A barra do celular e os ícones do cabeçalho oferecem as mesmas ações."""

    def test_a_barra_aparece_no_detalhe(self):
        demanda = self._demanda()

        resposta = self._detalhe(demanda)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "eithra-barra-rapida")
        self.assertContains(resposta, "eithra-barra-item")
        for rotulo in ("Atualizar", "Anexar", "Situação", "Responsável"):
            self.assertContains(resposta, rotulo)

    def test_o_chip_de_situacao_muda_a_situacao(self):
        demanda = self._demanda()

        resposta = self.client.post(
            reverse("demandas:mudar_status", args=[demanda.pk]),
            {"acao": StatusDemanda.EM_ANDAMENTO},
        )

        self.assertEqual(resposta.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.EM_ANDAMENTO)

    def test_morador_nao_ve_a_barra_nem_muda_o_responsavel(self):
        demanda = self._demanda()
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)

        detalhe = self._detalhe(demanda)
        troca = self.client.post(
            reverse("demandas:mudar_responsavel", args=[demanda.pk]), {"alvo": "eu"}
        )

        self.assertEqual(detalhe.status_code, 404)
        self.assertEqual(troca.status_code, 404)


class SugestoesDoMomentoTests(BaseDeAcoesRapidas):
    """Os atalhos por regra, conforme o estado da demanda.

    A conferência é pelo rótulo que a tela recebe, e não pelo texto da página:
    "Cobrar" também aparece na próxima ação sugerida do dia a dia, e procurar a
    palavra solta no HTML diria o que não se quer saber.
    """

    def _rotulos(self, demanda):
        resposta = self._detalhe(demanda)
        self.assertEqual(resposta.status_code, 200)
        return [sugestao["rotulo"] for sugestao in resposta.context["sugestoes_do_momento"]]

    def test_sem_responsavel_sugere_assumir(self):
        demanda = self._demanda()

        self.assertIn("Assumir", self._rotulos(demanda))

    def test_aguardando_ha_16_dias_sugere_cobrar(self):
        demanda = self._demanda(responsavel=self.gestao)
        alterar_status(
            demanda=demanda, usuario=self.gestao, novo_status=StatusDemanda.AGUARDANDO
        )
        HistoricoDemanda.objects.filter(demanda=demanda).update(
            data_hora=timezone.now() - timedelta(days=16)
        )

        self.assertIn("Cobrar", self._rotulos(demanda))

    def test_demanda_recem_criada_nao_sugere_cobrar_nem_dar_noticia(self):
        demanda = self._demanda(responsavel=self.gestao)

        rotulos = self._rotulos(demanda)

        self.assertNotIn("Cobrar", rotulos)
        self.assertNotIn("Dar notícia", rotulos)

    def test_a_sugestao_aparece_como_chip_na_tela(self):
        demanda = self._demanda()

        resposta = self._detalhe(demanda)

        self.assertContains(resposta, "eithra-sugestao")
        self.assertContains(resposta, "Assumir")
