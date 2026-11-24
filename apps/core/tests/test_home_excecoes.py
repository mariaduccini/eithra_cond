"""As exceções que pedem decisão, na Home de quem responde pelo condomínio.

A Home não calcula exceção — ela consome a leitura derivada do domínio
(``apps.demandas.excecoes``) e apresenta. Estes testes travam isso: o que
aparece, o que não aparece, a precedência entre motivos, o isolamento por
condomínio, o clique para o contexto, o limite da tela e a ausência de termos
técnicos. Travam também quem vê o bloco: a gestão decide; a operação conduz.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.condominios.constants import Papel
from apps.demandas.constants import (
    AguardandoTipo,
    PrioridadeDemanda,
    RiscoDemanda,
    StatusDemanda,
)
from apps.demandas.excecoes import (
    TipoExcecao,
    obter_excecoes_de_demandas,
)
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


class DerivacaoDasExcecoesTests(TestCase):
    """A leitura do domínio: qual exceção cada demanda gera, e nenhuma a mais."""

    def setUp(self):
        self.hoje = timezone.localdate()
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)

    def _excecoes(self):
        return obter_excecoes_de_demandas(usuario=self.gestao, hoje=self.hoje)

    def test_demanda_urgente_vira_excecao(self):
        _demanda(
            self.condominio,
            self.gestao,
            prioridade=PrioridadeDemanda.URGENTE,
        )
        excecoes = self._excecoes()
        self.assertEqual(len(excecoes), 1)
        self.assertEqual(excecoes[0].tipo, TipoExcecao.URGENTE)

    def test_demanda_com_risco_vira_excecao(self):
        _demanda(self.condominio, self.gestao, risco=RiscoDemanda.ESTRUTURAL)
        self.assertEqual(self._excecoes()[0].tipo, TipoExcecao.RISCO_ALTO)

    def test_sem_risco_nao_vira_excecao(self):
        # "Sem risco identificado" é resposta declarada, não risco.
        _demanda(self.condominio, self.gestao, risco=RiscoDemanda.SEM_RISCO)
        self.assertEqual(self._excecoes(), [])

    def test_aguardando_aprovacao_vira_excecao(self):
        _demanda(
            self.condominio,
            self.gestao,
            aguardando_tipo=AguardandoTipo.APROVACAO,
        )
        self.assertEqual(self._excecoes()[0].tipo, TipoExcecao.AGUARDANDO_APROVACAO)

    def test_prazo_vencido_vira_excecao(self):
        _demanda(
            self.condominio, self.gestao, prazo=self.hoje - timedelta(days=2)
        )
        excecao = self._excecoes()[0]
        self.assertEqual(excecao.tipo, TipoExcecao.VENCIDA)
        self.assertEqual(excecao.dias_de_atraso, 2)

    def test_em_andamento_sem_proxima_acao_vira_excecao(self):
        _demanda(
            self.condominio, self.gestao, status=StatusDemanda.EM_ANDAMENTO
        )
        self.assertEqual(self._excecoes()[0].tipo, TipoExcecao.SEM_PROXIMA_ACAO)

    def test_recem_aberta_sem_proxima_acao_nao_vira_excecao(self):
        # Ainda pode estar sendo triada: não é lacuna.
        _demanda(self.condominio, self.gestao)
        self.assertEqual(self._excecoes(), [])

    def test_demanda_encerrada_nao_vira_excecao(self):
        _demanda(
            self.condominio,
            self.gestao,
            prioridade=PrioridadeDemanda.URGENTE,
            status=StatusDemanda.CONCLUIDA,
            concluido_em=timezone.now(),
        )
        self.assertEqual(self._excecoes(), [])

    def test_urgente_pesa_antes_do_risco(self):
        _demanda(
            self.condominio,
            self.gestao,
            prioridade=PrioridadeDemanda.URGENTE,
            risco=RiscoDemanda.INCENDIO,
        )
        self.assertEqual(self._excecoes()[0].tipo, TipoExcecao.URGENTE)

    def test_risco_pesa_antes_do_prazo(self):
        _demanda(
            self.condominio,
            self.gestao,
            risco=RiscoDemanda.SEGURANCA,
            prazo=self.hoje - timedelta(days=1),
        )
        self.assertEqual(self._excecoes()[0].tipo, TipoExcecao.RISCO_ALTO)

    def test_uma_demanda_gera_no_maximo_uma_excecao(self):
        _demanda(
            self.condominio,
            self.gestao,
            prioridade=PrioridadeDemanda.URGENTE,
            risco=RiscoDemanda.ELETRICO,
            prazo=self.hoje - timedelta(days=5),
        )
        self.assertEqual(len(self._excecoes()), 1)

    def test_maior_atraso_vem_primeiro(self):
        _demanda(
            self.condominio,
            self.gestao,
            titulo="Atraso pequeno",
            prazo=self.hoje - timedelta(days=1),
        )
        _demanda(
            self.condominio,
            self.gestao,
            titulo="Atraso grande",
            prazo=self.hoje - timedelta(days=9),
        )
        self.assertEqual(self._excecoes()[0].demanda.titulo, "Atraso grande")


class BlocoDeExcecoesNaHomeTests(TestCase):
    """O bloco "Precisa da sua decisão" existe para quem decide."""

    def setUp(self):
        self.hoje = timezone.localdate()
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)
        self.client.force_login(self.gestao)

    def home(self):
        return self.client.get(reverse("core:home"))

    def test_excecao_aparece_com_motivo_e_contexto(self):
        local = criar_local(self.condominio, nome="Garagem")
        _demanda(
            self.condominio,
            self.gestao,
            titulo="Fiação exposta",
            risco=RiscoDemanda.ELETRICO,
            local=local,
        )
        resposta = self.home()
        self.assertContains(resposta, "Precisa da sua decisão")
        self.assertContains(resposta, "Fiação exposta")
        self.assertContains(resposta, "Risco identificado")
        self.assertContains(resposta, "Garagem")

    def test_item_aponta_para_o_contexto_da_demanda(self):
        demanda = _demanda(
            self.condominio,
            self.gestao,
            titulo="Cobrar aprovação",
            aguardando_tipo=AguardandoTipo.APROVACAO,
        )
        self.assertContains(
            self.home(), reverse("demandas:detalhe", args=[demanda.pk])
        )

    def test_sem_excecao_nao_desenha_o_bloco(self):
        _demanda(self.condominio, self.gestao, titulo="Tudo certo")
        resposta = self.home()
        self.assertNotContains(resposta, "Precisa da sua decisão")

    def test_nao_mostra_codigo_tecnico(self):
        # O rótulo humano aparece; o código guardado, não.
        _demanda(
            self.condominio,
            self.gestao,
            titulo="Vazamento",
            risco=RiscoDemanda.SAUDE,
        )
        resposta = self.home()
        self.assertContains(resposta, "Risco identificado")
        self.assertNotContains(resposta, RiscoDemanda.SAUDE)

    def test_mostra_no_maximo_cinco_excecoes(self):
        for indice in range(7):
            _demanda(
                self.condominio,
                self.gestao,
                titulo=f"Exceção {indice}",
                prioridade=PrioridadeDemanda.URGENTE,
            )
        self.assertContains(self.home(), "Ver todas")


class QuemVeAsExcecoesTests(TestCase):
    """A operação conduz; quem decide é a gestão."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")

    def _home(self, usuario, papel):
        criar_vinculo(usuario, self.condominio, papel)
        self.client.force_login(usuario)
        return self.client.get(reverse("core:home"))

    def test_operacao_nao_recebe_o_bloco(self):
        operacional = criar_pessoa("op@exemplo.test", "Operação")
        _demanda(
            self.condominio,
            operacional,
            titulo="Fiação exposta",
            risco=RiscoDemanda.ELETRICO,
        )
        resposta = self._home(operacional, Papel.OPERACIONAL)
        self.assertNotContains(resposta, "Precisa da sua decisão")

    def test_supervisao_nao_recebe_o_bloco(self):
        supervisora = criar_pessoa("sup@exemplo.test", "Supervisão")
        _demanda(
            self.condominio,
            supervisora,
            titulo="Fiação exposta",
            risco=RiscoDemanda.ELETRICO,
        )
        resposta = self._home(supervisora, Papel.SUPERVISORA)
        self.assertNotContains(resposta, "Precisa da sua decisão")

    def test_gestao_recebe_o_bloco(self):
        sindica = criar_pessoa("sindica@exemplo.test", "Síndica")
        _demanda(
            self.condominio,
            sindica,
            titulo="Fiação exposta",
            risco=RiscoDemanda.ELETRICO,
        )
        resposta = self._home(sindica, Papel.SINDICA)
        self.assertContains(resposta, "Precisa da sua decisão")


class IsolamentoDasExcecoesTests(TestCase):
    """A Home nunca recebe exceção de condomínio fora do escopo do usuário."""

    def test_nao_mostra_excecao_de_condominio_sem_acesso(self):
        alpha = criar_condominio("Alpha")
        beta = criar_condominio("Beta")
        gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(gestao, alpha, Papel.SINDICA)
        _demanda(
            beta,
            gestao,
            titulo="Segredo de outro condomínio",
            prioridade=PrioridadeDemanda.URGENTE,
        )
        self.client.force_login(gestao)
        resposta = self.client.get(reverse("core:home"))
        self.assertNotContains(resposta, "Segredo de outro condomínio")
