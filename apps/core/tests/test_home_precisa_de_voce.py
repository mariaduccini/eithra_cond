"""O bloco "Precisa de você": o que já venceu ou vence hoje abre o painel.

O objetivo é simples e travado aqui: quando há um documento vencido ou uma
demanda com prazo hoje, o painel nunca diz que nada pede atenção nem que tudo
está em ordem. As frases de sossego aparecem só quando a lista está de fato
vazia. A lista reúne, sem repetir regra, o que cada módulo já responde — os
vencimentos da governança, as demandas com prazo até hoje e as atividades
programadas atrasadas.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.condominios.constants import Papel
from apps.demandas.constants import StatusDemanda
from apps.demandas.models import Demanda
from apps.governanca.tests.apoio import criar_contrato, criar_documento
from apps.manutencoes.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)


class BaseDaHomePrecisaTests(TestCase):
    """Um condomínio só e a gestão que entra — quem responde pelo condomínio."""

    def setUp(self):
        self.hoje = timezone.localdate()
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)
        self.client.force_login(self.gestao)

    def home(self):
        return self.client.get(reverse("core:home"))

    def texto_da_home(self) -> str:
        return self.home().content.decode()

    def _demanda(self, **campos):
        base = {"titulo": "Demanda de teste", "status": StatusDemanda.ABERTA}
        base.update(campos)
        return Demanda.objects.create(
            condominio=self.condominio, criado_por=self.gestao, **base
        )


class BlocoPrecisaDeVoceTests(BaseDaHomePrecisaTests):
    """Vencido e prazo do dia entram; sem nada, o painel diz que está em ordem."""

    def test_documento_vencido_aparece_e_esconde_o_sossego(self):
        criar_documento(
            self.condominio,
            self.gestao,
            titulo="Laudo vencido",
            data_validade=self.hoje - timedelta(days=3),
        )
        resposta = self.home()
        self.assertContains(resposta, "Precisa de você")
        self.assertContains(resposta, "Laudo vencido")
        self.assertNotContains(resposta, "Nada pede sua atenção")
        self.assertNotContains(resposta, "Tudo em ordem por aqui.")

    def test_demanda_com_prazo_hoje_aparece(self):
        self._demanda(titulo="Trocar a bomba", prazo=self.hoje)
        resposta = self.home()
        self.assertContains(resposta, "Precisa de você")
        self.assertContains(resposta, "Trocar a bomba")
        self.assertContains(resposta, "prazo hoje")

    def test_sem_pendencias_mostra_que_esta_tudo_em_ordem(self):
        resposta = self.home()
        self.assertNotContains(resposta, "Precisa de você")
        self.assertContains(resposta, "Nada pede sua atenção")
        self.assertContains(resposta, "Tudo em ordem por aqui.")

    def test_excedente_vira_ver_mais_na_propria_home(self):
        for indice in range(9):
            criar_contrato(
                self.condominio,
                self.gestao,
                objeto=f"Contrato {indice}",
                data_fim=self.hoje - timedelta(days=indice + 1),
            )
        self.assertContains(self.home(), "Ver mais 1")

    def test_nome_do_condominio_nao_aparece_com_um_condominio_so(self):
        self._demanda(titulo="Trocar a bomba", prazo=self.hoje)
        self.assertNotIn("Alpha", self.texto_da_home())
