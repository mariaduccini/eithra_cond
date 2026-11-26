"""O prédio vivo: o estado de cada nível e o painel que abre o que há ali.

A tela mostra o desenho do prédio com uma bolinha por nível e, no toque, o que
existe dentro dele. Estes testes travam o essencial: o status que cada caso
produz, o equipamento do nível aparecendo no painel e o morador barrado.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Equipamento, LocalOperacional
from apps.condominios.constants import Papel
from apps.core.predio import ATENCAO, OK, URGENTE, niveis_do_predio
from apps.demandas.constants import PrioridadeDemanda
from apps.demandas.models import Demanda
from apps.demandas.tests.apoio import criar_condominio, criar_pessoa, criar_vinculo
from apps.manutencoes.constants import StatusManutencao
from apps.manutencoes.models import Manutencao, PlanoPreventivo


class BaseDoPredioTests(TestCase):
    """Um condomínio, a gestão que responde por ele e o desenho já montado."""

    def setUp(self):
        self.hoje = timezone.localdate()
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)

    def local(self, nome):
        return LocalOperacional.objects.create(condominio=self.condominio, nome=nome)

    def demanda(self, local, prioridade=PrioridadeDemanda.NORMAL):
        return Demanda.objects.create(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Ocorrência do nível",
            prioridade=prioridade,
            local=local,
        )

    def status(self, chave):
        return niveis_do_predio(self.condominio, hoje=self.hoje).niveis[chave]["status"]

    def get_predio(self, usuario):
        self.client.force_login(usuario)
        return self.client.get(reverse("core:predio"))


class StatusDoNivelTests(BaseDoPredioTests):
    """O que acende a bolinha de um nível — e o que a deixa em dia."""

    def test_nivel_sem_pendencia_fica_em_dia(self):
        self.local("3º andar")

        self.assertEqual(self.status("baixos"), OK)

    def test_demanda_urgente_acende_o_nivel(self):
        self.demanda(self.local("3º andar"), prioridade=PrioridadeDemanda.URGENTE)

        self.assertEqual(self.status("baixos"), URGENTE)

    def test_demanda_normal_pede_atencao(self):
        self.demanda(self.local("10º andar"))

        self.assertEqual(self.status("duplex"), ATENCAO)

    def test_manutencao_atrasada_acende_o_nivel(self):
        local = self.local("G4 · Casa de bombas de recalque")
        Manutencao.objects.create(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Trocar bomba",
            responsavel=self.gestao,
            data_prevista=self.hoje - timedelta(days=3),
            status=StatusManutencao.ABERTA,
            local=local,
        )

        self.assertEqual(self.status("g4"), URGENTE)

    def test_preventiva_proxima_pede_atencao(self):
        PlanoPreventivo.objects.create(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Revisar gerador",
            local=self.local("1º andar"),
            proxima_data_prevista=self.hoje + timedelta(days=5),
        )

        self.assertEqual(self.status("baixos"), ATENCAO)


class PainelDoPredioTests(BaseDoPredioTests):
    """O painel abre com o que há em cada nível — e o morador não entra."""

    def test_painel_lista_os_equipamentos_do_nivel(self):
        local = self.local("G4 · Casa de bombas de recalque")
        Equipamento.objects.create(
            condominio=self.condominio, nome="Bomba de recalque", local=local
        )

        resposta = self.get_predio(self.gestao)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Bomba de recalque")

    def test_morador_nao_ve_o_predio(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)

        resposta = self.get_predio(morador)

        self.assertEqual(resposta.status_code, 403)
