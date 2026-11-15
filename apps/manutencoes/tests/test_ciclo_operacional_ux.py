"""O ciclo operacional da ocorrência preventiva, operado pela tela.

A ocorrência preventiva vai de ponta a ponta na mesma página: registrar a
execução, anexar as provas, conferir o que foi entregue, corrigir quando for
preciso e só então concluir. Aqui se prende o comportamento das portas da tela —
cada uma escrevendo pelo serviço oficial, nenhuma concluindo sozinha —, o que
cada estado oferece, o que a página mostra e o que o histórico guarda.

Nenhum teste fala com armazenamento de verdade: o provedor é substituído por um
storage em memória. A conferência é sempre do domínio — a tela organiza a
resposta, e o serviço decide se ela pode passar.
"""
from datetime import date

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.condominios.constants import Papel
from apps.manutencoes.constants import (
    FrequenciaUnidade,
    ResultadoCriterio,
    StatusConferencia,
    StatusManutencao,
    TipoEventoManutencao,
)
from apps.manutencoes.models import (
    CriterioConferenciaPreventiva,
    EvidenciaManutencao,
    HistoricoManutencao,
    PlanoPreventivo,
)
from apps.manutencoes.services import gerar_ocorrencia_preventiva
from apps.manutencoes.tests.apoio import (
    StorageDeMentira,
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
    jpg,
)

SENHA = "senha-forte-123"


def _plano(condominio, criado_por, **campos):
    """Um plano válido do condomínio, com o que o teste quiser trocar."""
    base = {
        "titulo": "Revisão da bomba de recalque",
        "descricao": "Revisar o conjunto e trocar o selo mecânico.",
        "frequencia_unidade": FrequenciaUnidade.MESES,
        "frequencia_intervalo": 3,
        "proxima_data_prevista": date(2026, 11, 15),
        "criado_por": criado_por,
    }
    base.update(campos)
    plano = PlanoPreventivo(condominio=condominio, **base)
    plano.full_clean()
    plano.save()
    return plano


def _criterio(plano, descricao, ordem=0, obrigatorio=True):
    """Um critério de conferência do plano."""
    criterio = CriterioConferenciaPreventiva(
        plano=plano, descricao=descricao, ordem=ordem, obrigatorio=obrigatorio
    )
    criterio.full_clean()
    criterio.save()
    return criterio


class BaseDoCiclo(TestCase):
    """Uma ocorrência preventiva com conferência e um storage em memória."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

        self.provider = StorageDeMentira()
        patcher = patch(
            "apps.manutencoes.evidencias.obter_provider", return_value=self.provider
        )
        self.patch_do_storage = patcher.start()
        self.addCleanup(patcher.stop)

    # -- Cenário ---------------------------------------------------------------

    def _ocorrencia(self, *, exige_conferencia=True, criterios=("Conferir o selo",)):
        plano = _plano(
            self.condominio, self.gestao, exige_conferencia=exige_conferencia
        )
        for ordem, descricao in enumerate(criterios):
            _criterio(plano, descricao, ordem=ordem)
        return gerar_ocorrencia_preventiva(plano=plano, usuario=self.gestao)

    def _critérios(self, manutencao):
        return list(manutencao.criterios_conferencia.order_by("ordem"))

    # -- Portas da tela --------------------------------------------------------

    def _executar(self, manutencao, descricao="Conjunto revisado.", data=None):
        dados = {"descricao_servico": descricao}
        if data is not None:
            dados["data_execucao"] = data
        return self.client.post(
            reverse("manutencoes:registrar_execucao", args=[manutencao.pk]), dados
        )

    def _conferir(
        self, manutencao, decisao, *, avaliacoes=(), motivo="", observacao=""
    ):
        dados = {
            "decisao": decisao,
            "motivo": motivo,
            "observacao": observacao,
            "criterio_id": [str(criterio.pk) for criterio, _ in avaliacoes],
            "criterio_resultado": [resultado for _, resultado in avaliacoes],
        }
        return self.client.post(
            reverse("manutencoes:conferir", args=[manutencao.pk]), dados
        )

    def _concluir(self, manutencao, descricao="Serviço encerrado."):
        return self.client.post(
            reverse("manutencoes:concluir", args=[manutencao.pk]),
            {"descricao_servico": descricao},
        )

    def _aprovar(self, manutencao):
        avaliacoes = [
            (criterio, ResultadoCriterio.CONFORME)
            for criterio in self._critérios(manutencao)
        ]
        return self._conferir(manutencao, "aprovar", avaliacoes=avaliacoes)


# -- Fluxo aprovado --------------------------------------------------------------


class FluxoAprovadoTests(BaseDoCiclo):
    """Executar, conferir tudo conforme e concluir, cada passo pela sua porta."""

    def test_estado_inicial_oferece_registrar_execucao(self):
        manutencao = self._ocorrencia()
        resposta = self.client.get(
            reverse("manutencoes:detalhe", args=[manutencao.pk])
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.context["pode_registrar_execucao"])
        self.assertContains(resposta, "Registrar execução")

    def test_registrar_execucao_grava_a_data_do_fato_sem_concluir(self):
        manutencao = self._ocorrencia()

        resposta = self._executar(manutencao, data="2026-11-10")

        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        self.assertIsNotNone(manutencao.data_execucao)
        # O campo volta do PostgreSQL em UTC; o dia informado é o dia local.
        self.assertEqual(
            timezone.localtime(manutencao.data_execucao).date(), date(2026, 11, 10)
        )
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)
        self.assertIsNone(manutencao.data_conclusao)

    def test_execucao_mantem_a_conferencia_pendente(self):
        manutencao = self._ocorrencia()
        self._executar(manutencao)

        manutencao.refresh_from_db()
        self.assertEqual(
            manutencao.status_conferencia, StatusConferencia.PENDENTE
        )

    def test_data_futura_e_recusada(self):
        manutencao = self._ocorrencia()

        resposta = self._executar(manutencao, data="2100-01-01")

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(resposta.context["modal_aberto"], "execucao")
        manutencao.refresh_from_db()
        self.assertIsNone(manutencao.data_execucao)

    def test_aprovar_tudo_conforme_nao_conclui(self):
        manutencao = self._ocorrencia()
        self._executar(manutencao)

        resposta = self._aprovar(manutencao)

        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        self.assertEqual(
            manutencao.status_conferencia, StatusConferencia.APROVADA
        )
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)
        self.assertIsNone(manutencao.data_conclusao)

    def test_detalhe_apos_aprovacao_aponta_o_proximo_passo(self):
        manutencao = self._ocorrencia()
        self._executar(manutencao)
        self._aprovar(manutencao)

        resposta = self.client.get(
            reverse("manutencoes:detalhe", args=[manutencao.pk])
        )

        self.assertTrue(resposta.context["manutencao"]["conferencia_aprovada"])
        self.assertContains(
            resposta, "Conferência aprovada — falta concluir a manutenção."
        )

    def test_concluir_apos_aprovacao_encerra_a_manutencao(self):
        manutencao = self._ocorrencia()
        self._executar(manutencao)
        self._aprovar(manutencao)

        resposta = self._concluir(manutencao)

        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.CONCLUIDA)
        self.assertIsNotNone(manutencao.data_conclusao)


# -- Fluxo reprovado e correção --------------------------------------------------


class FluxoReprovadoTests(BaseDoCiclo):
    """Reprovar aponta o que refazer; a nova execução reabre a conferência."""

    def test_reprovar_exige_motivo(self):
        manutencao = self._ocorrencia()
        criterio = self._critérios(manutencao)[0]
        self._executar(manutencao)

        resposta = self._conferir(
            manutencao,
            "reprovar",
            avaliacoes=[(criterio, ResultadoCriterio.NAO_CONFORME)],
            motivo="",
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(resposta.context["modal_aberto"], "conferir")
        manutencao.refresh_from_db()
        self.assertEqual(
            manutencao.status_conferencia, StatusConferencia.PENDENTE
        )

    def test_reprovar_marca_o_item_e_nao_encerra(self):
        manutencao = self._ocorrencia()
        criterio = self._critérios(manutencao)[0]
        self._executar(manutencao)

        resposta = self._conferir(
            manutencao,
            "reprovar",
            avaliacoes=[(criterio, ResultadoCriterio.NAO_CONFORME)],
            motivo="O selo ainda vaza.",
        )

        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        self.assertEqual(
            manutencao.status_conferencia, StatusConferencia.REPROVADA
        )
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)
        criterio.refresh_from_db()
        self.assertEqual(criterio.resultado, ResultadoCriterio.NAO_CONFORME)

    def test_detalhe_reprovado_mostra_correcao_necessaria(self):
        manutencao = self._ocorrencia()
        criterio = self._critérios(manutencao)[0]
        self._executar(manutencao)
        self._conferir(
            manutencao,
            "reprovar",
            avaliacoes=[(criterio, ResultadoCriterio.NAO_CONFORME)],
            motivo="O selo ainda vaza.",
        )

        resposta = self.client.get(
            reverse("manutencoes:detalhe", args=[manutencao.pk])
        )

        self.assertTrue(resposta.context["manutencao"]["conferencia_reprovada"])
        self.assertIn(criterio.descricao, resposta.context["manutencao"]["nao_conformes"])
        self.assertContains(resposta, "Correção necessária")
        self.assertContains(resposta, "Registrar nova execução")

    def test_nova_execucao_reabre_a_conferencia_e_reseta_o_item(self):
        manutencao = self._ocorrencia()
        criterio = self._critérios(manutencao)[0]
        self._executar(manutencao)
        self._conferir(
            manutencao,
            "reprovar",
            avaliacoes=[(criterio, ResultadoCriterio.NAO_CONFORME)],
            motivo="O selo ainda vaza.",
        )

        self._executar(manutencao, descricao="Selo trocado de novo.")

        manutencao.refresh_from_db()
        self.assertEqual(
            manutencao.status_conferencia, StatusConferencia.PENDENTE
        )
        criterio.refresh_from_db()
        self.assertEqual(criterio.resultado, ResultadoCriterio.NAO_AVALIADO)

    def test_reprovacao_anterior_fica_no_historico(self):
        manutencao = self._ocorrencia()
        criterio = self._critérios(manutencao)[0]
        self._executar(manutencao)
        self._conferir(
            manutencao,
            "reprovar",
            avaliacoes=[(criterio, ResultadoCriterio.NAO_CONFORME)],
            motivo="O selo ainda vaza.",
        )
        self._executar(manutencao, descricao="Selo trocado de novo.")

        tipos = list(
            HistoricoManutencao.objects.filter(manutencao=manutencao).values_list(
                "tipo_evento", flat=True
            )
        )
        self.assertIn(TipoEventoManutencao.CONFERENCIA_REPROVADA, tipos)
        self.assertEqual(tipos.count(TipoEventoManutencao.EXECUCAO), 2)

    def test_nao_avaliar_mantem_a_conferencia_pendente(self):
        manutencao = self._ocorrencia()
        self._executar(manutencao)

        resposta = self._conferir(
            manutencao, "nao_avaliar", observacao="Falta acesso ao local."
        )

        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        self.assertEqual(
            manutencao.status_conferencia, StatusConferencia.PENDENTE
        )
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)


# -- Bloqueios -------------------------------------------------------------------


class BloqueiosTests(BaseDoCiclo):
    """Concluir depende do domínio; sem permissão, o POST direto não passa."""

    def test_concluir_com_conferencia_pendente_e_recusado(self):
        manutencao = self._ocorrencia()
        self._executar(manutencao)

        resposta = self._concluir(manutencao)

        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)
        self.assertIsNone(manutencao.data_conclusao)

    def test_concluir_com_conferencia_reprovada_e_recusado(self):
        manutencao = self._ocorrencia()
        criterio = self._critérios(manutencao)[0]
        self._executar(manutencao)
        self._conferir(
            manutencao,
            "reprovar",
            avaliacoes=[(criterio, ResultadoCriterio.NAO_CONFORME)],
            motivo="O selo ainda vaza.",
        )

        resposta = self._concluir(manutencao)

        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)

    def test_conferir_de_novo_apos_decidida_e_recusado(self):
        manutencao = self._ocorrencia()
        self._executar(manutencao)
        self._aprovar(manutencao)

        resposta = self._aprovar(manutencao)

        # Depois de aprovada não há o que conferir: a porta recusa antes de o
        # serviço ser chamado, e o estado não se move.
        self.assertEqual(resposta.status_code, 403)
        manutencao.refresh_from_db()
        self.assertEqual(
            manutencao.status_conferencia, StatusConferencia.APROVADA
        )

    def test_operacional_sem_responsabilidade_nao_registra_execucao(self):
        manutencao = self._ocorrencia()
        operacional = criar_pessoa("op@exemplo.test", "Operação")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        self.client.force_login(operacional)

        resposta = self._executar(manutencao)

        self.assertEqual(resposta.status_code, 403)
        manutencao.refresh_from_db()
        self.assertIsNone(manutencao.data_execucao)

    def test_morador_recebe_404_na_execucao(self):
        manutencao = self._ocorrencia()
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)

        resposta = self._executar(manutencao)

        self.assertEqual(resposta.status_code, 404)

    def test_get_na_porta_de_execucao_e_recusado(self):
        manutencao = self._ocorrencia()

        resposta = self.client.get(
            reverse("manutencoes:registrar_execucao", args=[manutencao.pk])
        )

        self.assertEqual(resposta.status_code, 405)


# -- Evidências ------------------------------------------------------------------


class EvidenciasDoCicloTests(BaseDoCiclo):
    """Anexar guarda a prova sem decidir nada: não conclui, não aprova, não reabre."""

    def _anexar(self, manutencao, arquivo=None):
        return self.client.post(
            reverse("manutencoes:adicionar_evidencia", args=[manutencao.pk]),
            {"evidencias": [arquivo or jpg()]},
        )

    def test_anexar_guarda_a_prova_sem_mudar_a_situacao(self):
        manutencao = self._ocorrencia()
        self._executar(manutencao)

        resposta = self._anexar(manutencao)

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(
            EvidenciaManutencao.objects.filter(manutencao=manutencao).count(), 1
        )
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)
        self.assertEqual(
            manutencao.status_conferencia, StatusConferencia.PENDENTE
        )

    def test_evidencia_nao_reabre_conferencia_reprovada(self):
        manutencao = self._ocorrencia()
        criterio = self._critérios(manutencao)[0]
        self._executar(manutencao)
        self._conferir(
            manutencao,
            "reprovar",
            avaliacoes=[(criterio, ResultadoCriterio.NAO_CONFORME)],
            motivo="O selo ainda vaza.",
        )

        self._anexar(manutencao)

        manutencao.refresh_from_db()
        self.assertEqual(
            manutencao.status_conferencia, StatusConferencia.REPROVADA
        )

    def test_evidencia_aparece_na_tela(self):
        manutencao = self._ocorrencia()
        self._anexar(manutencao)

        resposta = self.client.get(
            reverse("manutencoes:detalhe", args=[manutencao.pk])
        )

        self.assertEqual(len(resposta.context["manutencao"]["evidencias"]), 1)
        self.assertContains(resposta, "Evidências da execução")


# -- Histórico -------------------------------------------------------------------


class HistoricoDoCicloTests(BaseDoCiclo):
    """A sequência legível do que aconteceu, do começo para o fim, sem reescrever."""

    def test_historico_guarda_a_ordem_e_preserva_a_reprovacao(self):
        manutencao = self._ocorrencia()
        criterio = self._critérios(manutencao)[0]
        self._executar(manutencao)
        self._conferir(
            manutencao,
            "reprovar",
            avaliacoes=[(criterio, ResultadoCriterio.NAO_CONFORME)],
            motivo="O selo ainda vaza.",
        )
        self._executar(manutencao, descricao="Selo trocado de novo.")
        self._aprovar(manutencao)
        self._concluir(manutencao)

        tipos = list(
            HistoricoManutencao.objects.filter(manutencao=manutencao)
            .order_by("data_hora")
            .values_list("tipo_evento", flat=True)
        )

        self.assertEqual(
            tipos,
            [
                TipoEventoManutencao.CRIADA,
                TipoEventoManutencao.EXECUCAO,
                TipoEventoManutencao.CONFERENCIA_REPROVADA,
                TipoEventoManutencao.EXECUCAO,
                TipoEventoManutencao.CONFERENCIA_APROVADA,
                TipoEventoManutencao.SERVICO,
                TipoEventoManutencao.CONCLUIDA,
            ],
        )

    def test_tela_mostra_o_historico_do_comeco_para_o_fim(self):
        manutencao = self._ocorrencia()
        self._executar(manutencao)

        resposta = self.client.get(
            reverse("manutencoes:detalhe", args=[manutencao.pk])
        )

        historico = resposta.context["historico"]
        self.assertEqual(historico[0]["tipo"], "Manutenção criada")
        self.assertEqual(historico[-1]["tipo"], "Execução registrada")


# -- Conferência depende da execução ---------------------------------------------


class ConferenciaDependeDaExecucaoTests(BaseDoCiclo):
    """Conferir pressupõe execução registrada — na tela e na porta da view.

    A ocorrência nasce com a conferência pendente, mesmo antes de o serviço ser
    executado. Pendente, então, não basta para conferir: sem execução não há
    entrega para olhar, e a ação não se oferece nem se aceita pelo acesso
    direto.
    """

    def test_antes_da_execucao_nao_oferece_conferir(self):
        manutencao = self._ocorrencia()

        resposta = self.client.get(
            reverse("manutencoes:detalhe", args=[manutencao.pk])
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(resposta.context["pode_conferir"])
        self.assertNotContains(resposta, "Conferir execução")
        self.assertContains(resposta, "Aguardando registro da execução")

    def test_antes_da_execucao_a_conferencia_direta_e_recusada(self):
        manutencao = self._ocorrencia()
        criterio = self._critérios(manutencao)[0]

        resposta = self._conferir(
            manutencao,
            "aprovar",
            avaliacoes=[(criterio, ResultadoCriterio.CONFORME)],
        )

        self.assertEqual(resposta.status_code, 403)
        manutencao.refresh_from_db()
        self.assertEqual(
            manutencao.status_conferencia, StatusConferencia.PENDENTE
        )

    def test_depois_da_execucao_oferece_e_permite_conferir(self):
        manutencao = self._ocorrencia()
        self._executar(manutencao)

        resposta = self.client.get(
            reverse("manutencoes:detalhe", args=[manutencao.pk])
        )
        self.assertTrue(resposta.context["pode_conferir"])
        self.assertContains(resposta, "Conferir execução")
        self.assertNotContains(resposta, "Aguardando registro da execução")

        decisao = self._aprovar(manutencao)
        self.assertEqual(decisao.status_code, 302)
        manutencao.refresh_from_db()
        self.assertEqual(
            manutencao.status_conferencia, StatusConferencia.APROVADA
        )

    def test_apos_reprovacao_nao_confere_antes_da_nova_execucao(self):
        manutencao = self._ocorrencia()
        criterio = self._critérios(manutencao)[0]
        self._executar(manutencao)
        self._conferir(
            manutencao,
            "reprovar",
            avaliacoes=[(criterio, ResultadoCriterio.NAO_CONFORME)],
            motivo="O selo ainda vaza.",
        )

        resposta = self.client.get(
            reverse("manutencoes:detalhe", args=[manutencao.pk])
        )
        self.assertFalse(resposta.context["pode_conferir"])
        self.assertNotContains(resposta, "Conferir execução")

        tentativa = self._aprovar(manutencao)
        self.assertEqual(tentativa.status_code, 403)
        manutencao.refresh_from_db()
        self.assertEqual(
            manutencao.status_conferencia, StatusConferencia.REPROVADA
        )

    def test_apos_nova_execucao_volta_a_poder_conferir(self):
        manutencao = self._ocorrencia()
        criterio = self._critérios(manutencao)[0]
        self._executar(manutencao)
        self._conferir(
            manutencao,
            "reprovar",
            avaliacoes=[(criterio, ResultadoCriterio.NAO_CONFORME)],
            motivo="O selo ainda vaza.",
        )
        self._executar(manutencao, descricao="Selo trocado de novo.")

        resposta = self.client.get(
            reverse("manutencoes:detalhe", args=[manutencao.pk])
        )
        self.assertTrue(resposta.context["pode_conferir"])
        self.assertContains(resposta, "Conferir execução")

        decisao = self._aprovar(manutencao)
        self.assertEqual(decisao.status_code, 302)
        manutencao.refresh_from_db()
        self.assertEqual(
            manutencao.status_conferencia, StatusConferencia.APROVADA
        )
