"""As demandas em aberto na Home: o que ainda depende de uma ação.

A Home não calcula prioridade nem prazo — ela consome a leitura do próprio
domínio de demandas (``DemandaQuerySet.na_ordem_de_atencao``) e apresenta o
topo da lista, no escopo que a pessoa enxerga. Estes testes travam isso: o que
aparece, o que não aparece, a ordem, o isolamento por condomínio, o clique para
o detalhe, o limite da tela e a ausência de consulta por item.
"""
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.condominios.constants import Papel
from apps.demandas.constants import PrioridadeDemanda, StatusDemanda
from apps.demandas.models import Demanda
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_local,
    criar_pessoa,
    criar_vinculo,
)


def _demanda(condominio, criado_por, **campos):
    """Uma demanda do condomínio, com o que o teste quiser trocar."""
    base = {"titulo": "Demanda de teste", "status": StatusDemanda.ABERTA}
    base.update(campos)
    return Demanda.objects.create(
        condominio=condominio, criado_por=criado_por, **base
    )


class BaseDaHomeComDemandasTests(TestCase):
    """Um condomínio e a gestão que entra."""

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


class BlocoDeDemandasTests(BaseDaHomeComDemandasTests):
    """A demanda em aberto aparece; o que não pede ação fica de fora."""

    def test_mostra_demanda_em_aberto(self):
        _demanda(self.condominio, self.gestao, titulo="Infiltração na garagem")
        self.assertContains(self.home(), "Infiltração na garagem")

    def test_demanda_concluida_nao_aparece(self):
        _demanda(
            self.condominio,
            self.gestao,
            titulo="Vazamento resolvido",
            status=StatusDemanda.CONCLUIDA,
            concluido_em=timezone.now(),
        )
        self.assertNotContains(self.home(), "Vazamento resolvido")

    def test_demanda_cancelada_nao_aparece(self):
        _demanda(
            self.condominio,
            self.gestao,
            titulo="Demanda cancelada",
            status=StatusDemanda.CANCELADA,
            cancelado_em=timezone.now(),
        )
        self.assertNotContains(self.home(), "Demanda cancelada")

    def test_demanda_desativada_nao_aparece(self):
        _demanda(self.condominio, self.gestao, titulo="Demanda desativada", ativo=False)
        self.assertNotContains(self.home(), "Demanda desativada")

    def test_sem_demanda_nao_desenha_o_bloco(self):
        self.assertNotContains(self.home(), "O que ainda depende de uma ação.")

    def test_item_aponta_para_o_detalhe(self):
        demanda = _demanda(self.condominio, self.gestao, titulo="Cobrar orçamento")
        self.assertContains(
            self.home(), reverse("demandas:detalhe", args=[demanda.pk])
        )

    def test_contexto_traz_local_e_prazo_sem_repetir_condominio(self):
        local = criar_local(self.condominio, nome="Garagem")
        _demanda(
            self.condominio,
            self.gestao,
            titulo="Trocar bomba",
            local=local,
            prazo=self.hoje + timedelta(days=3),
        )
        texto = self.texto_da_home()
        # Com um condomínio só, o nome dele não distingue nada e não se repete.
        self.assertNotIn("Alpha", texto)
        self.assertIn("Garagem", texto)
        self.assertIn(
            f"prazo {(self.hoje + timedelta(days=3)):%d/%m/%Y}", texto
        )


class OrdemDasDemandasTests(BaseDaHomeComDemandasTests):
    """A ordem de leitura é a do domínio: urgente, vencida, depois o resto."""

    def test_urgente_vem_antes_da_comum(self):
        _demanda(self.condominio, self.gestao, titulo="Demanda comum")
        _demanda(
            self.condominio,
            self.gestao,
            titulo="Demanda urgente",
            prioridade=PrioridadeDemanda.URGENTE,
        )
        texto = self.texto_da_home()
        self.assertLess(texto.index("Demanda urgente"), texto.index("Demanda comum"))

    def test_vencida_vem_antes_da_comum(self):
        _demanda(self.condominio, self.gestao, titulo="Demanda comum")
        _demanda(
            self.condominio,
            self.gestao,
            titulo="Demanda fora do prazo",
            prazo=self.hoje - timedelta(days=2),
        )
        texto = self.texto_da_home()
        self.assertLess(
            texto.index("Demanda fora do prazo"), texto.index("Demanda comum")
        )


class IsolamentoDaHomeComDemandasTests(TestCase):
    """A Home nunca recebe demanda de condomínio fora do escopo do usuário."""

    def setUp(self):
        self.alpha = criar_condominio("Alpha")
        self.beta = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.alpha, Papel.SINDICA)
        self.client.force_login(self.gestao)

    def test_nao_mostra_demanda_de_condominio_sem_acesso(self):
        _demanda(self.beta, self.gestao, titulo="Segredo de outro condomínio")
        resposta = self.client.get(reverse("core:home"))
        self.assertNotContains(resposta, "Segredo de outro condomínio")


class LimiteDasDemandasTests(BaseDaHomeComDemandasTests):
    """A Home mostra um punhado e oferece "Ver todas" — nunca uma lista enorme."""

    def test_mostra_no_maximo_cinco_demandas(self):
        # Prazos distintos e no futuro: ficam fora do bloco de decisão e a
        # ordem de atenção desempata pelo prazo — previsível. A quinta
        # aparece; a sexta fica de fora.
        for indice in range(7):
            _demanda(
                self.condominio,
                self.gestao,
                titulo=f"Demanda {indice}",
                prazo=self.hoje + timedelta(days=indice + 1),
            )
        resposta = self.home()
        self.assertContains(resposta, "Ver todas")
        # O topo são cinco: a quinta aparece, a sexta fica de fora.
        self.assertContains(resposta, "Demanda 4")
        self.assertNotContains(resposta, "Demanda 5")

    def test_sem_excedente_nao_oferece_ver_todas(self):
        _demanda(self.condominio, self.gestao, titulo="Uma só")
        self.assertNotContains(self.home(), "Ver todas")


class PerformanceDasDemandasTests(BaseDaHomeComDemandasTests):
    """A Home não faz uma consulta por demanda da lista."""

    def _consultas(self) -> int:
        with CaptureQueriesContext(connection) as capturadas:
            self.client.get(reverse("core:home"))
        return len(capturadas)

    def _uma_demanda(self, indice):
        local = criar_local(self.condominio, nome=f"Local {indice}")
        _demanda(
            self.condominio,
            self.gestao,
            titulo=f"Demanda {indice}",
            prioridade=PrioridadeDemanda.URGENTE,
            local=local,
            prazo=self.hoje - timedelta(days=indice + 1),
        )

    def test_consultas_nao_crescem_com_o_numero_de_demandas(self):
        self._uma_demanda(0)
        com_uma = self._consultas()
        for indice in range(1, 5):
            self._uma_demanda(indice)
        com_cinco = self._consultas()
        self.assertEqual(com_uma, com_cinco)
