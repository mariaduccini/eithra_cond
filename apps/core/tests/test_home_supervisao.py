"""O que precisa da supervisão: a leitura agregada e a área na Home.

A Home não decide o que a supervisão precisa fazer — ela consome a leitura
derivada (``apps.core.supervisao``), que reúne manutenções, ocorrências de
rotina e demandas, e apresenta. Estes testes travam isso: quais ações cada
origem gera, a que bloco pertencem, a ordem dentro do bloco, quem vê a área, o
isolamento por condomínio, o que desaparece quando o ciclo fecha, o destino de
cada clique e a ausência da área para quem não conduz a operação.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.condominios.constants import Papel
from apps.demandas.constants import StatusDemanda
from apps.demandas.models import Demanda
from apps.manutencoes.constants import StatusManutencao, TipoManutencao
from apps.manutencoes.models import Manutencao
from apps.manutencoes.tests.apoio import (
    criar_condominio,
    criar_local,
    criar_pessoa,
    criar_vinculo,
)
from apps.core.supervisao import (
    ORIGEM_DEMANDA,
    ORIGEM_MANUTENCAO,
    ORIGEM_ROTINA,
    BlocoDaSupervisao,
    TipoAcao,
    obter_acoes_da_supervisao,
    pode_ver_acoes_da_supervisao,
)
from apps.rotinas.constants import ResultadoExecucao, StatusExecucao
from apps.rotinas.models import ExecucaoRotina, RotinaRecorrente


def _manutencao(condominio, criado_por, **campos):
    """Uma manutenção do condomínio, com o que o teste quiser trocar."""
    base = {
        "titulo": "Serviço de teste",
        "status": StatusManutencao.ABERTA,
        "tipo": TipoManutencao.CORRETIVA,
    }
    base.update(campos)
    return Manutencao.objects.create(
        condominio=condominio, criado_por=criado_por, **base
    )


def _execucao(condominio, criado_por, *, titulo="Ronda do prédio", local=None, **campos):
    """Uma ocorrência de rotina do condomínio, com o que o teste quiser trocar."""
    rotina = RotinaRecorrente.objects.create(
        condominio=condominio,
        titulo=titulo,
        data_inicio=timezone.localdate() - timedelta(days=30),
        criado_por=criado_por,
        local=local,
    )
    base = {
        "rotina": rotina,
        "condominio": condominio,
        "data_prevista": timezone.localdate(),
    }
    base.update(campos)
    return ExecucaoRotina.objects.create(**base)


def _demanda(condominio, criado_por, **campos):
    """Uma demanda do condomínio, com o que o teste quiser trocar."""
    base = {"titulo": "Demanda de teste", "status": StatusDemanda.ABERTA}
    base.update(campos)
    return Demanda.objects.create(
        condominio=condominio, criado_por=criado_por, **base
    )


class DerivacaoDaSupervisaoTests(TestCase):
    """A leitura agregada: qual ação cada origem gera, e nenhuma a mais."""

    def setUp(self):
        self.hoje = timezone.localdate()
        self.condominio = criar_condominio("Alpha")
        self.supervisora = criar_pessoa("sup@exemplo.test", "Supervisão")
        criar_vinculo(self.supervisora, self.condominio, Papel.SUPERVISORA)

    def _acoes(self):
        return obter_acoes_da_supervisao(
            usuario=self.supervisora, hoje=self.hoje
        )

    def test_visita_atrasada_vira_acao_de_agora(self):
        _manutencao(
            self.condominio,
            self.supervisora,
            titulo="Elevadores Bahia",
            data_prevista=self.hoje - timedelta(days=2),
        )
        acao = self._acoes()[0]
        self.assertEqual(acao.tipo, TipoAcao.VISITA_ATRASADA)
        self.assertEqual(acao.bloco, BlocoDaSupervisao.AGORA)

    def test_visita_de_hoje_vira_acao_do_dia(self):
        _manutencao(
            self.condominio,
            self.supervisora,
            data_prevista=self.hoje,
        )
        acao = self._acoes()[0]
        self.assertEqual(acao.tipo, TipoAcao.VISITA_HOJE)
        self.assertEqual(acao.bloco, BlocoDaSupervisao.HOJE)

    def test_visita_ja_executada_pede_fechamento(self):
        _manutencao(
            self.condominio,
            self.supervisora,
            titulo="Manutenção da bomba G1",
            data_prevista=self.hoje - timedelta(days=1),
            data_execucao=timezone.now(),
        )
        acao = self._acoes()[0]
        self.assertEqual(acao.tipo, TipoAcao.VISITA_FECHAMENTO)
        self.assertEqual(acao.bloco, BlocoDaSupervisao.AGORA)

    def test_servico_em_andamento_entra_como_pendencia(self):
        _manutencao(
            self.condominio,
            self.supervisora,
            status=StatusManutencao.EM_ANDAMENTO,
        )
        acao = self._acoes()[0]
        self.assertEqual(acao.tipo, TipoAcao.SERVICO_EM_ANDAMENTO)
        self.assertEqual(acao.bloco, BlocoDaSupervisao.PENDENCIA)

    def test_rotina_do_dia_vira_acao_do_dia(self):
        _execucao(self.condominio, self.supervisora, data_prevista=self.hoje)
        acao = self._acoes()[0]
        self.assertEqual(acao.tipo, TipoAcao.ATIVIDADE_HOJE)
        self.assertEqual(acao.nome, "Ronda do prédio")

    def test_rotina_atrasada_vira_acao_de_agora(self):
        _execucao(
            self.condominio,
            self.supervisora,
            data_prevista=self.hoje - timedelta(days=3),
        )
        self.assertEqual(self._acoes()[0].tipo, TipoAcao.ATIVIDADE_ATRASADA)

    def test_rotina_iniciada_e_nao_encerrada_e_pendencia(self):
        _execucao(
            self.condominio,
            self.supervisora,
            status=StatusExecucao.EM_ANDAMENTO,
            iniciado_em=timezone.now(),
        )
        acao = self._acoes()[0]
        self.assertEqual(acao.tipo, TipoAcao.ATIVIDADE_EM_ANDAMENTO)
        self.assertEqual(acao.bloco, BlocoDaSupervisao.PENDENCIA)

    def test_demanda_com_proximo_passo_vencido_vira_acao_de_agora(self):
        _demanda(
            self.condominio,
            self.supervisora,
            status=StatusDemanda.EM_ANDAMENTO,
            proxima_acao="Cobrar o orçamento",
            prazo_proxima_acao=self.hoje - timedelta(days=1),
        )
        acao = self._acoes()[0]
        self.assertEqual(acao.tipo, TipoAcao.DEMANDA_ACAO_VENCIDA)
        self.assertEqual(acao.bloco, BlocoDaSupervisao.AGORA)

    def test_demanda_acompanhada_sem_prazo_vira_pendencia(self):
        _demanda(
            self.condominio,
            self.supervisora,
            status=StatusDemanda.EM_ANDAMENTO,
            proxima_acao="Aguardar o fornecedor",
        )
        acao = self._acoes()[0]
        self.assertEqual(acao.tipo, TipoAcao.DEMANDA_ACOMPANHAMENTO)
        self.assertEqual(acao.bloco, BlocoDaSupervisao.PENDENCIA)

    def test_recem_aberta_sem_proximo_passo_nao_entra(self):
        # Ainda pode estar sendo triada: não é ação da supervisão.
        _demanda(self.condominio, self.supervisora)
        self.assertEqual(self._acoes(), [])

    def test_manutencao_encerrada_desaparece(self):
        _manutencao(
            self.condominio,
            self.supervisora,
            titulo="Serviço encerrado",
            status=StatusManutencao.CONCLUIDA,
            data_prevista=self.hoje - timedelta(days=2),
            data_conclusao=timezone.now(),
        )
        self.assertEqual(self._acoes(), [])

    def test_rotina_encerrada_desaparece(self):
        _execucao(
            self.condominio,
            self.supervisora,
            status=StatusExecucao.CONCLUIDA,
            concluido_em=timezone.now(),
            resultado=ResultadoExecucao.OK,
        )
        self.assertEqual(self._acoes(), [])

    def test_demanda_encerrada_desaparece(self):
        _demanda(
            self.condominio,
            self.supervisora,
            status=StatusDemanda.CONCLUIDA,
            concluido_em=timezone.now(),
            proxima_acao="Cobrar o orçamento",
            prazo_proxima_acao=self.hoje - timedelta(days=1),
        )
        self.assertEqual(self._acoes(), [])

    def test_atrasado_pesa_antes_do_compromisso_do_dia(self):
        _manutencao(
            self.condominio,
            self.supervisora,
            titulo="Visita de hoje",
            data_prevista=self.hoje,
        )
        _manutencao(
            self.condominio,
            self.supervisora,
            titulo="Visita atrasada",
            data_prevista=self.hoje - timedelta(days=4),
        )
        acoes = self._acoes()
        self.assertEqual(acoes[0].nome, "Visita atrasada")
        self.assertEqual(acoes[1].nome, "Visita de hoje")

    def test_sem_escopo_nao_devolve_acao(self):
        _manutencao(
            self.condominio,
            self.supervisora,
            data_prevista=self.hoje - timedelta(days=1),
        )
        self.assertEqual(obter_acoes_da_supervisao(hoje=self.hoje), [])


class NomeDaAcaoTests(TestCase):
    """A ação nomeia o registro como a supervisão o reconhece."""

    def setUp(self):
        self.hoje = timezone.localdate()
        self.condominio = criar_condominio("Alpha")
        self.supervisora = criar_pessoa("sup@exemplo.test", "Supervisão")
        criar_vinculo(self.supervisora, self.condominio, Papel.SUPERVISORA)

    def test_rotina_usa_o_titulo_da_rotina(self):
        _execucao(
            self.condominio,
            self.supervisora,
            titulo="Ronda do prédio",
            data_prevista=self.hoje,
        )
        acoes = obter_acoes_da_supervisao(usuario=self.supervisora, hoje=self.hoje)
        self.assertEqual(acoes[0].nome, "Ronda do prédio")

    def test_manutencao_usa_o_titulo_da_manutencao(self):
        _manutencao(
            self.condominio,
            self.supervisora,
            titulo="Bomba do bloco A",
            data_prevista=self.hoje,
        )
        acoes = obter_acoes_da_supervisao(usuario=self.supervisora, hoje=self.hoje)
        self.assertEqual(acoes[0].nome, "Bomba do bloco A")


class AreaDaSupervisaoNaHomeTests(TestCase):
    """A área "O que precisa de você" existe para quem conduz a operação."""

    def setUp(self):
        self.hoje = timezone.localdate()
        self.condominio = criar_condominio("Alpha")
        self.supervisora = criar_pessoa("sup@exemplo.test", "Supervisão")
        criar_vinculo(self.supervisora, self.condominio, Papel.SUPERVISORA)
        self.client.force_login(self.supervisora)

    def home(self):
        return self.client.get(reverse("core:home"))

    def test_area_aparece_com_a_acao_da_manutencao(self):
        _manutencao(
            self.condominio,
            self.supervisora,
            titulo="Elevadores Bahia",
            data_prevista=self.hoje - timedelta(days=1),
        )
        resposta = self.home()
        self.assertContains(resposta, "O que precisa de você")
        self.assertContains(resposta, "Elevadores Bahia")
        self.assertContains(resposta, "Agora")

    def test_area_aparece_com_a_ronda_do_dia(self):
        _execucao(
            self.condominio,
            self.supervisora,
            titulo="Ronda do prédio",
            data_prevista=self.hoje,
        )
        resposta = self.home()
        self.assertContains(resposta, "O que precisa de você")
        self.assertContains(resposta, "Ronda do prédio")
        self.assertContains(resposta, "Ainda hoje")

    def test_item_da_manutencao_aponta_para_o_acompanhamento(self):
        manutencao = _manutencao(
            self.condominio,
            self.supervisora,
            titulo="Troca de lâmpada",
            data_prevista=self.hoje,
        )
        self.assertContains(
            self.home(),
            reverse(
                "core:acompanhamento", args=[ORIGEM_MANUTENCAO, manutencao.pk]
            ),
        )

    def test_item_da_rotina_aponta_para_o_acompanhamento(self):
        execucao = _execucao(
            self.condominio,
            self.supervisora,
            data_prevista=self.hoje,
        )
        self.assertContains(
            self.home(),
            reverse("core:acompanhamento", args=[ORIGEM_ROTINA, execucao.pk]),
        )

    def test_item_da_demanda_aponta_para_o_acompanhamento(self):
        demanda = _demanda(
            self.condominio,
            self.supervisora,
            status=StatusDemanda.EM_ANDAMENTO,
            proxima_acao="Cobrar o orçamento",
            prazo_proxima_acao=self.hoje - timedelta(days=1),
        )
        self.assertContains(
            self.home(),
            reverse("core:acompanhamento", args=[ORIGEM_DEMANDA, demanda.pk]),
        )

    def test_sem_acao_nao_desenha_a_area(self):
        resposta = self.home()
        self.assertNotContains(resposta, "O que precisa de você")

    def test_conta_o_que_precisa_da_supervisao(self):
        _manutencao(
            self.condominio,
            self.supervisora,
            data_prevista=self.hoje - timedelta(days=1),
        )
        self.assertContains(self.home(), "1 coisa precisa da sua atenção")

    def test_nao_usa_tabela(self):
        _manutencao(
            self.condominio,
            self.supervisora,
            titulo="Item do dia",
            data_prevista=self.hoje,
        )
        texto = self.home().content.decode()
        self.assertIn('class="eithra-lista', texto)
        self.assertNotIn("<table", texto)


class QuemVeAAreaDaSupervisaoTests(TestCase):
    """Quem conduz a operação vê; quem não conduz não recebe a área."""

    def setUp(self):
        self.hoje = timezone.localdate()
        self.condominio = criar_condominio("Alpha")
        self.autora = criar_pessoa("autora@exemplo.test", "Autora")
        _manutencao(
            self.condominio,
            self.autora,
            titulo="Elevadores Bahia",
            data_prevista=self.hoje - timedelta(days=1),
        )

    def _home(self, usuario, papel):
        criar_vinculo(usuario, self.condominio, papel)
        self.client.force_login(usuario)
        return self.client.get(reverse("core:home"))

    def test_supervisao_ve_a_area(self):
        supervisora = criar_pessoa("sup@exemplo.test", "Supervisão")
        resposta = self._home(supervisora, Papel.SUPERVISORA)
        self.assertContains(resposta, "O que precisa de você")

    def test_gestao_nao_ve_a_area(self):
        sindica = criar_pessoa("sindica@exemplo.test", "Síndica")
        resposta = self._home(sindica, Papel.SINDICA)
        self.assertNotContains(resposta, "O que precisa de você")

    def test_operacao_nao_ve_a_area(self):
        operacional = criar_pessoa("op@exemplo.test", "Operação")
        resposta = self._home(operacional, Papel.OPERACIONAL)
        self.assertNotContains(resposta, "O que precisa de você")

    def test_sem_papel_a_leitura_nao_tem_escopo(self):
        sindica = criar_pessoa("sindica@exemplo.test", "Síndica")
        criar_vinculo(sindica, self.condominio, Papel.SINDICA)
        self.assertFalse(pode_ver_acoes_da_supervisao(sindica))
        self.assertEqual(
            obter_acoes_da_supervisao(usuario=sindica, hoje=self.hoje), []
        )


class IsolamentoDaSupervisaoTests(TestCase):
    """A área nunca recebe ação de condomínio fora do escopo da pessoa."""

    def test_nao_mostra_acao_de_condominio_sem_acesso(self):
        alpha = criar_condominio("Alpha")
        beta = criar_condominio("Beta")
        supervisora = criar_pessoa("sup@exemplo.test", "Supervisão")
        criar_vinculo(supervisora, alpha, Papel.SUPERVISORA)
        _manutencao(
            beta,
            supervisora,
            titulo="Segredo de outro condomínio",
            data_prevista=timezone.localdate() - timedelta(days=1),
        )
        self.client.force_login(supervisora)
        resposta = self.client.get(reverse("core:home"))
        self.assertNotContains(resposta, "Segredo de outro condomínio")
