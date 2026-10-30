"""A conferência pós-execução: a regra herdada, a execução e a decisão.

Uma manutenção executada pode passar por uma conferência antes de ser
concluída. Aqui se confere que a regra nasce copiada do plano e não muda se o
plano mudar depois, que registrar a execução não conclui a manutenção, que a
conferência tem vocabulário próprio — separada da situação — e que aprovar,
reprovar e não conseguir avaliar deixam o histórico e o estado coerentes.

A conferência olha o que foi entregue. Ela não atesta qualidade técnica, e não
conclui sozinha: a conclusão continua sendo uma decisão à parte.
"""
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.condominios.constants import Papel
from apps.manutencoes.constants import (
    FrequenciaUnidade,
    ResultadoCriterio,
    StatusConferencia,
    StatusManutencao,
    TipoEventoManutencao,
    TipoManutencao,
)
from apps.manutencoes.models import (
    CriterioConferenciaPreventiva,
    HistoricoManutencao,
    Manutencao,
    PlanoPreventivo,
)
from apps.manutencoes.permissoes import pode_conferir, pode_registrar_execucao
from apps.manutencoes.services import (
    ErroDeManutencao,
    aprovar_conferencia,
    concluir_manutencao,
    criar_manutencao,
    gerar_ocorrencia_preventiva,
    nao_avaliar_conferencia,
    registrar_execucao,
    reprovar_conferencia,
)
from apps.manutencoes.tests.apoio import (
    criar_condominio,
    criar_demanda,
    criar_pessoa,
    criar_vinculo,
)


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
        plano=plano,
        descricao=descricao,
        ordem=ordem,
        obrigatorio=obrigatorio,
    )
    criterio.full_clean()
    criterio.save()
    return criterio


class RegraHerdadaTests(TestCase):
    """A regra de conferência nasce no plano e é copiada para a ocorrência."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)

    def test_ocorrencia_herda_a_regra_de_conferencia(self):
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        ocorrencia = gerar_ocorrencia_preventiva(plano=plano, usuario=self.gestao)

        self.assertTrue(ocorrencia.exige_conferencia)
        self.assertEqual(ocorrencia.status_conferencia, StatusConferencia.PENDENTE)

    def test_plano_sem_conferencia_gera_ocorrencia_nao_exigida(self):
        plano = _plano(self.condominio, self.gestao, exige_conferencia=False)
        ocorrencia = gerar_ocorrencia_preventiva(plano=plano, usuario=self.gestao)

        self.assertFalse(ocorrencia.exige_conferencia)
        self.assertEqual(
            ocorrencia.status_conferencia, StatusConferencia.NAO_EXIGIDA
        )

    def test_alteracao_posterior_no_plano_nao_alcanca_a_ocorrencia(self):
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        ocorrencia = gerar_ocorrencia_preventiva(plano=plano, usuario=self.gestao)

        # A gestão decide que aquele plano não exige mais conferência. A decisão
        # vale das próximas ocorrências em diante, e não reescreve o passado.
        plano.exige_conferencia = False
        plano.save(update_fields=["exige_conferencia"])

        ocorrencia.refresh_from_db()
        self.assertTrue(ocorrencia.exige_conferencia)
        self.assertEqual(ocorrencia.status_conferencia, StatusConferencia.PENDENTE)

    def test_conferencia_e_coerente_com_a_regra_que_a_exige(self):
        """A regra e a conferência andam juntas; o banco recusa o incoerente."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Manutencao.objects.create(
                    condominio=self.condominio,
                    titulo="Incoerente",
                    criado_por=self.gestao,
                    exige_conferencia=True,
                    status_conferencia=StatusConferencia.NAO_EXIGIDA,
                )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Manutencao.objects.create(
                    condominio=self.condominio,
                    titulo="Incoerente demais",
                    criado_por=self.gestao,
                    exige_conferencia=False,
                    status_conferencia=StatusConferencia.PENDENTE,
                )


class CriteriosTests(TestCase):
    """Os critérios do plano viram retrato próprio da ocorrência."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)

    def test_plano_pode_ter_varios_criterios_na_ordem_declarada(self):
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        _criterio(plano, "Água visualmente limpa", ordem=2)
        _criterio(plano, "Ausência de resíduos", ordem=0)
        _criterio(plano, "Equipamento sem vazamento aparente", ordem=1)

        descricoes = list(
            plano.criterios_conferencia.values_list("descricao", flat=True)
        )
        self.assertEqual(
            descricoes,
            [
                "Ausência de resíduos",
                "Equipamento sem vazamento aparente",
                "Água visualmente limpa",
            ],
        )

    def test_snapshot_dos_criterios_na_ocorrencia(self):
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        _criterio(plano, "Ausência de resíduos", ordem=0)
        _criterio(plano, "Relatório técnico anexado", ordem=1, obrigatorio=False)

        ocorrencia = gerar_ocorrencia_preventiva(plano=plano, usuario=self.gestao)

        itens = list(
            ocorrencia.criterios_conferencia.values_list(
                "descricao", "ordem", "obrigatorio"
            )
        )
        self.assertEqual(
            itens,
            [
                ("Ausência de resíduos", 0, True),
                ("Relatório técnico anexado", 1, False),
            ],
        )

    def test_mudanca_no_plano_nao_altera_o_snapshot_anterior(self):
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        _criterio(plano, "Ausência de resíduos", ordem=0)

        ocorrencia = gerar_ocorrencia_preventiva(plano=plano, usuario=self.gestao)

        # Depois de a ocorrência nascer, o plano muda: um critério novo aparece
        # e outro é renomeado. Nada disso alcança o retrato que já foi gravado.
        _criterio(plano, "Teste funcional realizado", ordem=1)
        primeiro = plano.criterios_conferencia.get(ordem=0)
        primeiro.descricao = "Sem resíduos visíveis"
        primeiro.save(update_fields=["descricao"])

        descricoes = list(
            ocorrencia.criterios_conferencia.values_list("descricao", flat=True)
        )
        self.assertEqual(descricoes, ["Ausência de resíduos"])

    def test_plano_sem_criterios_gera_ocorrencia_sem_criterios(self):
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        ocorrencia = gerar_ocorrencia_preventiva(plano=plano, usuario=self.gestao)
        self.assertEqual(ocorrencia.criterios_conferencia.count(), 0)

    def test_criterio_sem_descricao_e_recusado(self):
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        with self.assertRaises(ValidationError):
            CriterioConferenciaPreventiva(
                plano=plano, descricao="   "
            ).full_clean()


class ExecucaoTests(TestCase):
    """Registrar a execução guarda o que foi feito, sem concluir a manutenção."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)
        self.plano = _plano(
            self.condominio, self.gestao, exige_conferencia=True
        )
        self.manutencao = gerar_ocorrencia_preventiva(
            plano=self.plano, usuario=self.gestao
        )

    def test_registrar_execucao_guarda_o_servico_e_a_data(self):
        registrar_execucao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Selos trocados e conjunto revisado.",
        )
        self.manutencao.refresh_from_db()
        self.assertEqual(
            self.manutencao.descricao_servico,
            "Selos trocados e conjunto revisado.",
        )
        self.assertIsNotNone(self.manutencao.data_execucao)

    def test_execucao_gera_evento_no_historico(self):
        registrar_execucao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Conjunto revisado.",
        )
        evento = self.manutencao.historico.filter(
            tipo_evento=TipoEventoManutencao.EXECUCAO
        ).get()
        self.assertEqual(evento.usuario, self.gestao)

    def test_execucao_nao_conclui_a_manutencao(self):
        registrar_execucao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Conjunto revisado.",
        )
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.status, StatusManutencao.ABERTA)
        self.assertIsNone(self.manutencao.data_conclusao)

    def test_ocorrencia_com_conferencia_fica_pendente(self):
        registrar_execucao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Conjunto revisado.",
        )
        self.manutencao.refresh_from_db()
        self.assertEqual(
            self.manutencao.status_conferencia, StatusConferencia.PENDENTE
        )

    def test_execucao_sem_descricao_e_recusada(self):
        with self.assertRaises(ErroDeManutencao):
            registrar_execucao(
                manutencao=self.manutencao,
                usuario=self.gestao,
                descricao_servico="   ",
            )

    def test_data_de_execucao_no_futuro_e_recusada(self):
        with self.assertRaises(ErroDeManutencao):
            registrar_execucao(
                manutencao=self.manutencao,
                usuario=self.gestao,
                descricao_servico="Conjunto revisado.",
                data_execucao=timezone.localdate() + timedelta(days=1),
            )

    def test_manutencao_encerrada_nao_registra_execucao(self):
        registrar_execucao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Conjunto revisado.",
        )
        self.manutencao.refresh_from_db()
        aprovar_conferencia(manutencao=self.manutencao, usuario=self.gestao)
        self.manutencao.refresh_from_db()
        concluir_manutencao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Serviço encerrado.",
        )
        with self.assertRaises(ErroDeManutencao):
            registrar_execucao(
                manutencao=self.manutencao,
                usuario=self.gestao,
                descricao_servico="Tentativa tardia.",
            )

    def test_manutencao_sem_conferencia_continua_nao_exigida(self):
        corretiva = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Trocar a bomba do reservatório",
        )
        registrar_execucao(
            manutencao=corretiva,
            usuario=self.gestao,
            descricao_servico="Bomba trocada.",
        )
        corretiva.refresh_from_db()
        self.assertEqual(
            corretiva.status_conferencia, StatusConferencia.NAO_EXIGIDA
        )
        self.assertIsNotNone(corretiva.data_execucao)


class AprovacaoTests(TestCase):
    """Aprovar registra a decisão; não conclui e não se repete."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)
        self.confere = criar_pessoa("supervisao@exemplo.test", "Supervisão")
        criar_vinculo(self.confere, self.condominio, Papel.SUPERVISORA)
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        self.manutencao = gerar_ocorrencia_preventiva(
            plano=plano, usuario=self.gestao
        )
        registrar_execucao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Conjunto revisado.",
        )

    def test_aprovar_registra_a_decisao_e_o_evento(self):
        aprovar_conferencia(
            manutencao=self.manutencao, usuario=self.confere
        )
        self.manutencao.refresh_from_db()
        self.assertEqual(
            self.manutencao.status_conferencia, StatusConferencia.APROVADA
        )
        evento = self.manutencao.historico.filter(
            tipo_evento=TipoEventoManutencao.CONFERENCIA_APROVADA
        ).get()
        self.assertEqual(evento.usuario, self.confere)

    def test_nao_aprova_sem_execucao_registrada(self):
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        outra = gerar_ocorrencia_preventiva(plano=plano, usuario=self.gestao)
        with self.assertRaises(ErroDeManutencao):
            aprovar_conferencia(manutencao=outra, usuario=self.confere)

    def test_nao_aprova_duas_vezes(self):
        aprovar_conferencia(manutencao=self.manutencao, usuario=self.confere)
        with self.assertRaises(ErroDeManutencao):
            aprovar_conferencia(manutencao=self.manutencao, usuario=self.confere)

    def test_aprovacao_nao_conclui_a_manutencao(self):
        aprovar_conferencia(manutencao=self.manutencao, usuario=self.confere)
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.status, StatusManutencao.ABERTA)
        self.assertIsNone(self.manutencao.data_conclusao)

    def test_nao_aprova_manutencao_sem_conferencia(self):
        corretiva = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Trocar a bomba do reservatório",
        )
        with self.assertRaises(ErroDeManutencao):
            aprovar_conferencia(manutencao=corretiva, usuario=self.confere)


class ReprovacaoTests(TestCase):
    """Reprovar exige motivo, mantém a manutenção aberta e permite corrigir."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        self.manutencao = gerar_ocorrencia_preventiva(
            plano=plano, usuario=self.gestao
        )
        registrar_execucao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Conjunto revisado.",
        )

    def test_reprovar_exige_motivo(self):
        with self.assertRaises(ErroDeManutencao):
            reprovar_conferencia(
                manutencao=self.manutencao, usuario=self.gestao, motivo="   "
            )

    def test_reprovar_mantem_a_manutencao_aberta(self):
        reprovar_conferencia(
            manutencao=self.manutencao,
            usuario=self.gestao,
            motivo="Faltou o relatório técnico.",
        )
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.status, StatusManutencao.ABERTA)
        self.assertIsNone(self.manutencao.data_conclusao)
        self.assertEqual(
            self.manutencao.status_conferencia, StatusConferencia.REPROVADA
        )

    def test_reprovar_registra_o_motivo_no_historico(self):
        reprovar_conferencia(
            manutencao=self.manutencao,
            usuario=self.gestao,
            motivo="Faltou o relatório técnico.",
        )
        evento = self.manutencao.historico.filter(
            tipo_evento=TipoEventoManutencao.CONFERENCIA_REPROVADA
        ).get()
        self.assertIn("Faltou o relatório técnico.", evento.descricao)

    def test_reprovar_sem_execucao_registrada_e_recusado(self):
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        outra = gerar_ocorrencia_preventiva(plano=plano, usuario=self.gestao)
        with self.assertRaises(ErroDeManutencao):
            reprovar_conferencia(
                manutencao=outra, usuario=self.gestao, motivo="Nada a conferir."
            )

    def test_nova_execucao_apos_reprovacao_reabre_a_conferencia(self):
        reprovar_conferencia(
            manutencao=self.manutencao,
            usuario=self.gestao,
            motivo="Faltou o relatório técnico.",
        )
        registrar_execucao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Relatório anexado e ajuste refeito.",
        )
        self.manutencao.refresh_from_db()
        self.assertEqual(
            self.manutencao.status_conferencia, StatusConferencia.PENDENTE
        )

        execucoes = self.manutencao.historico.filter(
            tipo_evento=TipoEventoManutencao.EXECUCAO
        )
        self.assertEqual(execucoes.count(), 2)
        self.assertTrue(
            execucoes.filter(descricao__startswith="Nova execução").exists()
        )

    def test_nao_confere_de_novo_sem_nova_execucao(self):
        reprovar_conferencia(
            manutencao=self.manutencao,
            usuario=self.gestao,
            motivo="Faltou o relatório técnico.",
        )
        with self.assertRaises(ErroDeManutencao):
            aprovar_conferencia(manutencao=self.manutencao, usuario=self.gestao)


class NaoAvaliarTests(TestCase):
    """Não conseguir avaliar não aprova nem reprova: a pendência continua."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        self.manutencao = gerar_ocorrencia_preventiva(
            plano=plano, usuario=self.gestao
        )
        registrar_execucao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Conjunto revisado.",
        )

    def test_nao_avaliar_mantem_a_conferencia_pendente(self):
        nao_avaliar_conferencia(
            manutencao=self.manutencao,
            usuario=self.gestao,
            observacao="Sem acesso à casa de máquinas hoje.",
        )
        self.manutencao.refresh_from_db()
        self.assertEqual(
            self.manutencao.status_conferencia, StatusConferencia.PENDENTE
        )
        self.assertEqual(self.manutencao.status, StatusManutencao.ABERTA)

    def test_nao_avaliar_registra_historico(self):
        nao_avaliar_conferencia(
            manutencao=self.manutencao,
            usuario=self.gestao,
            observacao="Sem acesso à casa de máquinas hoje.",
        )
        evento = self.manutencao.historico.filter(
            tipo_evento=TipoEventoManutencao.CONFERENCIA_NAO_AVALIADA
        ).get()
        self.assertIn("Sem acesso à casa de máquinas hoje.", evento.descricao)

    def test_nao_avaliar_exige_conferencia_devida(self):
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        outra = gerar_ocorrencia_preventiva(plano=plano, usuario=self.gestao)
        with self.assertRaises(ErroDeManutencao):
            nao_avaliar_conferencia(manutencao=outra, usuario=self.gestao)

    def test_a_conferencia_pode_ser_retomada_depois(self):
        nao_avaliar_conferencia(
            manutencao=self.manutencao,
            usuario=self.gestao,
            observacao="Sem acesso hoje.",
        )
        aprovar_conferencia(manutencao=self.manutencao, usuario=self.gestao)
        self.manutencao.refresh_from_db()
        self.assertEqual(
            self.manutencao.status_conferencia, StatusConferencia.APROVADA
        )


class PermissoesTests(TestCase):
    """Quem registra a execução e quem confere, sem ampliar privilégio."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)
        self.supervisora = criar_pessoa("sup@exemplo.test", "Supervisão")
        criar_vinculo(self.supervisora, self.condominio, Papel.SUPERVISORA)
        self.operacional = criar_pessoa("op@exemplo.test", "Operação")
        criar_vinculo(self.operacional, self.condominio, Papel.OPERACIONAL)

        self.plano = _plano(
            self.condominio,
            self.gestao,
            exige_conferencia=True,
            responsavel=self.operacional,
        )
        self.manutencao = gerar_ocorrencia_preventiva(
            plano=self.plano, usuario=self.gestao
        )

    def test_gestao_e_supervisao_conferem(self):
        self.assertTrue(pode_conferir(self.gestao, self.manutencao))
        self.assertTrue(pode_conferir(self.supervisora, self.manutencao))

    def test_quem_executa_nao_confere_nesta_fase(self):
        self.assertFalse(pode_conferir(self.operacional, self.manutencao))

    def test_nao_confere_sem_conferencia_exigida(self):
        corretiva = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Trocar a bomba do reservatório",
        )
        self.assertFalse(pode_conferir(self.gestao, corretiva))

    def test_registrar_execucao_acompanha_quem_opera(self):
        self.assertTrue(pode_registrar_execucao(self.gestao, self.manutencao))
        self.assertTrue(
            pode_registrar_execucao(self.supervisora, self.manutencao)
        )
        # O operacional é o responsável desta manutenção, então pode registrar.
        self.assertTrue(
            pode_registrar_execucao(self.operacional, self.manutencao)
        )

    def test_operacional_sem_responsabilidade_nao_registra(self):
        outro_operacional = criar_pessoa("op2@exemplo.test", "Outra operação")
        criar_vinculo(outro_operacional, self.condominio, Papel.OPERACIONAL)
        self.assertFalse(
            pode_registrar_execucao(outro_operacional, self.manutencao)
        )

    def test_execucao_aprovada_nao_registra_de_novo(self):
        registrar_execucao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Conjunto revisado.",
        )
        aprovar_conferencia(manutencao=self.manutencao, usuario=self.gestao)
        self.manutencao.refresh_from_db()
        self.assertFalse(
            pode_registrar_execucao(self.gestao, self.manutencao)
        )


class CompatibilidadeTests(TestCase):
    """O ciclo que já existia continua igual: corretiva, demanda e conclusão."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)

    def test_corretiva_nasce_sem_conferencia(self):
        manutencao = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Trocar a bomba do reservatório",
        )
        self.assertEqual(manutencao.tipo, TipoManutencao.CORRETIVA)
        self.assertFalse(manutencao.exige_conferencia)
        self.assertEqual(
            manutencao.status_conferencia, StatusConferencia.NAO_EXIGIDA
        )

    def test_conclusao_direta_continua_funcionando(self):
        manutencao = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Trocar a bomba do reservatório",
        )
        concluir_manutencao(
            manutencao=manutencao,
            usuario=self.gestao,
            descricao_servico="Bomba trocada.",
        )
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.CONCLUIDA)
        self.assertIsNotNone(manutencao.data_conclusao)

    def test_demanda_de_origem_continua_intacta(self):
        demanda = criar_demanda(self.condominio, self.gestao, "Vazamento")
        manutencao = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Reparar o vazamento",
            demanda_origem=demanda,
        )
        self.assertEqual(manutencao.demanda_origem_id, demanda.pk)
        self.assertIsNone(manutencao.plano_preventivo_id)
        self.assertFalse(manutencao.exige_conferencia)

    def test_geracao_preventiva_sem_conferencia_continua(self):
        plano = _plano(self.condominio, self.gestao)
        manutencao = gerar_ocorrencia_preventiva(plano=plano, usuario=self.gestao)
        self.assertEqual(manutencao.tipo, TipoManutencao.PREVENTIVA)
        self.assertEqual(manutencao.plano_preventivo_id, plano.pk)
        self.assertEqual(
            manutencao.status_conferencia, StatusConferencia.NAO_EXIGIDA
        )
        self.assertEqual(HistoricoManutencao.objects.count(), 1)


class ConclusaoComConferenciaTests(TestCase):
    """Uma manutenção que exige conferência só conclui depois de aprovada."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        self.manutencao = gerar_ocorrencia_preventiva(
            plano=plano, usuario=self.gestao
        )
        registrar_execucao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Conjunto revisado.",
        )

    def _concluir(self):
        self.manutencao.refresh_from_db()
        return concluir_manutencao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Serviço encerrado.",
        )

    def test_conclusao_com_conferencia_pendente_e_recusada(self):
        with self.assertRaises(ErroDeManutencao):
            self._concluir()
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.status, StatusManutencao.ABERTA)
        self.assertIsNone(self.manutencao.data_conclusao)

    def test_conclusao_com_conferencia_reprovada_e_recusada(self):
        reprovar_conferencia(
            manutencao=self.manutencao,
            usuario=self.gestao,
            motivo="Faltou o relatório.",
        )
        with self.assertRaises(ErroDeManutencao):
            self._concluir()
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.status, StatusManutencao.ABERTA)

    def test_conclusao_com_conferencia_aprovada_e_permitida(self):
        aprovar_conferencia(manutencao=self.manutencao, usuario=self.gestao)
        self._concluir()
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.status, StatusManutencao.CONCLUIDA)
        self.assertIsNotNone(self.manutencao.data_conclusao)

    def test_corretiva_sem_conferencia_conclui_como_antes(self):
        corretiva = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Trocar a bomba do reservatório",
        )
        concluir_manutencao(
            manutencao=corretiva,
            usuario=self.gestao,
            descricao_servico="Bomba trocada.",
        )
        corretiva.refresh_from_db()
        self.assertEqual(corretiva.status, StatusManutencao.CONCLUIDA)

    def test_banco_recusa_concluida_sem_aprovacao(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Manutencao.objects.filter(pk=self.manutencao.pk).update(
                    status=StatusManutencao.CONCLUIDA,
                    data_conclusao=timezone.now(),
                )


class CriteriosObrigatoriosTests(TestCase):
    """O item obrigatório só libera a aprovação quando está conforme."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        _criterio(plano, "Ausência de resíduos", ordem=0, obrigatorio=True)
        _criterio(plano, "Relatório técnico anexado", ordem=1, obrigatorio=True)
        _criterio(plano, "Pintura alinhada", ordem=2, obrigatorio=False)
        self.manutencao = gerar_ocorrencia_preventiva(
            plano=plano, usuario=self.gestao
        )
        registrar_execucao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Conjunto revisado.",
        )
        self.itens = {
            criterio.descricao: criterio
            for criterio in self.manutencao.criterios_conferencia.all()
        }

    def test_obrigatorio_nao_avaliado_bloqueia_aprovacao(self):
        with self.assertRaises(ErroDeManutencao):
            aprovar_conferencia(manutencao=self.manutencao, usuario=self.gestao)

    def test_obrigatorio_nao_conforme_bloqueia_aprovacao(self):
        with self.assertRaises(ErroDeManutencao):
            aprovar_conferencia(
                manutencao=self.manutencao,
                usuario=self.gestao,
                avaliacoes=[
                    {
                        "criterio": self.itens["Ausência de resíduos"],
                        "resultado": ResultadoCriterio.CONFORME,
                    },
                    {
                        "criterio": self.itens["Relatório técnico anexado"],
                        "resultado": ResultadoCriterio.NAO_CONFORME,
                    },
                ],
            )

    def test_obrigatorio_nao_se_aplica_nao_libera_aprovacao(self):
        with self.assertRaises(ErroDeManutencao):
            aprovar_conferencia(
                manutencao=self.manutencao,
                usuario=self.gestao,
                avaliacoes=[
                    {
                        "criterio": self.itens["Ausência de resíduos"],
                        "resultado": ResultadoCriterio.CONFORME,
                    },
                    {
                        "criterio": self.itens["Relatório técnico anexado"],
                        "resultado": ResultadoCriterio.NAO_SE_APLICA,
                    },
                ],
            )

    def test_todos_obrigatorios_conformes_permitem_aprovacao(self):
        aprovar_conferencia(
            manutencao=self.manutencao,
            usuario=self.gestao,
            avaliacoes=[
                {
                    "criterio": self.itens["Ausência de resíduos"],
                    "resultado": ResultadoCriterio.CONFORME,
                },
                {
                    "criterio": self.itens["Relatório técnico anexado"],
                    "resultado": ResultadoCriterio.CONFORME,
                },
            ],
        )
        self.manutencao.refresh_from_db()
        self.assertEqual(
            self.manutencao.status_conferencia, StatusConferencia.APROVADA
        )

    def test_item_opcional_nao_avaliado_nao_bloqueia(self):
        aprovar_conferencia(
            manutencao=self.manutencao,
            usuario=self.gestao,
            avaliacoes=[
                {
                    "criterio": self.itens["Ausência de resíduos"],
                    "resultado": ResultadoCriterio.CONFORME,
                },
                {
                    "criterio": self.itens["Relatório técnico anexado"],
                    "resultado": ResultadoCriterio.CONFORME,
                },
            ],
        )
        self.manutencao.refresh_from_db()
        opcional = self.manutencao.criterios_conferencia.get(
            descricao="Pintura alinhada"
        )
        self.assertEqual(opcional.resultado, ResultadoCriterio.NAO_AVALIADO)

    def test_aprovacao_frustrada_nao_grava_avaliacao_parcial(self):
        with self.assertRaises(ErroDeManutencao):
            aprovar_conferencia(
                manutencao=self.manutencao,
                usuario=self.gestao,
                avaliacoes=[
                    {
                        "criterio": self.itens["Ausência de resíduos"],
                        "resultado": ResultadoCriterio.CONFORME,
                    }
                ],
            )
        self.manutencao.refresh_from_db()
        self.assertEqual(
            self.manutencao.status_conferencia, StatusConferencia.PENDENTE
        )
        # A transação inteira volta atrás: nada de avaliação pela metade.
        item = self.manutencao.criterios_conferencia.get(
            descricao="Ausência de resíduos"
        )
        self.assertEqual(item.resultado, ResultadoCriterio.NAO_AVALIADO)

    def test_item_de_outra_manutencao_e_recusado(self):
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        _criterio(plano, "Outro item", ordem=0)
        outra = gerar_ocorrencia_preventiva(plano=plano, usuario=self.gestao)
        item_alheio = outra.criterios_conferencia.get()

        with self.assertRaises(ErroDeManutencao):
            aprovar_conferencia(
                manutencao=self.manutencao,
                usuario=self.gestao,
                avaliacoes=[
                    {
                        "criterio": item_alheio,
                        "resultado": ResultadoCriterio.CONFORME,
                    }
                ],
            )


class ReexecucaoComCriteriosTests(TestCase):
    """Reprovar e executar de novo reabre os itens sem perder o que foi dito."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        _criterio(plano, "Ausência de resíduos", ordem=0, obrigatorio=True)
        self.manutencao = gerar_ocorrencia_preventiva(
            plano=plano, usuario=self.gestao
        )
        registrar_execucao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Primeira entrega.",
        )

    def test_reprovacao_aponta_itens_nao_conformes(self):
        item = self.manutencao.criterios_conferencia.get()
        reprovar_conferencia(
            manutencao=self.manutencao,
            usuario=self.gestao,
            motivo="Sobrou resíduo.",
            criterios_nao_conformes=[
                {"criterio": item, "observacao": "Ainda havia areia."}
            ],
        )
        item.refresh_from_db()
        self.assertEqual(item.resultado, ResultadoCriterio.NAO_CONFORME)
        self.assertEqual(item.observacao, "Ainda havia areia.")

        evento = self.manutencao.historico.filter(
            tipo_evento=TipoEventoManutencao.CONFERENCIA_REPROVADA
        ).get()
        self.assertIn("Ausência de resíduos", evento.descricao)
        self.assertIn(
            "Ausência de resíduos", evento.dados_novos["criterios_nao_conformes"]
        )

    def test_nova_execucao_reabre_os_itens(self):
        item = self.manutencao.criterios_conferencia.get()
        reprovar_conferencia(
            manutencao=self.manutencao,
            usuario=self.gestao,
            motivo="Sobrou resíduo.",
            criterios_nao_conformes=[{"criterio": item}],
        )
        registrar_execucao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Limpeza refeita.",
        )
        item.refresh_from_db()
        self.assertEqual(item.resultado, ResultadoCriterio.NAO_AVALIADO)
        self.manutencao.refresh_from_db()
        self.assertEqual(
            self.manutencao.status_conferencia, StatusConferencia.PENDENTE
        )

    def test_avaliacao_anterior_fica_no_historico(self):
        item = self.manutencao.criterios_conferencia.get()
        reprovar_conferencia(
            manutencao=self.manutencao,
            usuario=self.gestao,
            motivo="Sobrou resíduo.",
            criterios_nao_conformes=[{"criterio": item}],
        )
        registrar_execucao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Limpeza refeita.",
        )
        # Mesmo com o item reaberto, o que foi reprovado antes continua legível.
        evento = self.manutencao.historico.filter(
            tipo_evento=TipoEventoManutencao.CONFERENCIA_REPROVADA
        ).get()
        self.assertEqual(
            evento.dados_novos["criterios_nao_conformes"], ["Ausência de resíduos"]
        )

    def test_nova_conferencia_pode_aprovar(self):
        item = self.manutencao.criterios_conferencia.get()
        reprovar_conferencia(
            manutencao=self.manutencao,
            usuario=self.gestao,
            motivo="Sobrou resíduo.",
            criterios_nao_conformes=[{"criterio": item}],
        )
        registrar_execucao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Limpeza refeita.",
        )
        aprovar_conferencia(
            manutencao=self.manutencao,
            usuario=self.gestao,
            avaliacoes=[
                {
                    "criterio": self.manutencao.criterios_conferencia.get(),
                    "resultado": ResultadoCriterio.CONFORME,
                }
            ],
        )
        self.manutencao.refresh_from_db()
        self.assertEqual(
            self.manutencao.status_conferencia, StatusConferencia.APROVADA
        )


class NaoAvaliarComCriteriosTests(TestCase):
    """Não conseguir avaliar não satisfaz item obrigatório nem conclui."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)
        plano = _plano(self.condominio, self.gestao, exige_conferencia=True)
        _criterio(plano, "Ausência de resíduos", ordem=0, obrigatorio=True)
        self.manutencao = gerar_ocorrencia_preventiva(
            plano=plano, usuario=self.gestao
        )
        registrar_execucao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Conjunto revisado.",
        )

    def test_nao_avaliar_nao_marca_item_como_conforme(self):
        nao_avaliar_conferencia(
            manutencao=self.manutencao,
            usuario=self.gestao,
            observacao="Sem acesso hoje.",
        )
        item = self.manutencao.criterios_conferencia.get()
        self.assertEqual(item.resultado, ResultadoCriterio.NAO_AVALIADO)
        self.manutencao.refresh_from_db()
        self.assertEqual(
            self.manutencao.status_conferencia, StatusConferencia.PENDENTE
        )

    def test_aprovacao_continua_bloqueada_apos_nao_avaliar(self):
        nao_avaliar_conferencia(
            manutencao=self.manutencao,
            usuario=self.gestao,
            observacao="Sem acesso hoje.",
        )
        with self.assertRaises(ErroDeManutencao):
            aprovar_conferencia(manutencao=self.manutencao, usuario=self.gestao)

    def test_conclusao_continua_bloqueada_apos_nao_avaliar(self):
        nao_avaliar_conferencia(
            manutencao=self.manutencao,
            usuario=self.gestao,
            observacao="Sem acesso hoje.",
        )
        with self.assertRaises(ErroDeManutencao):
            concluir_manutencao(
                manutencao=self.manutencao,
                usuario=self.gestao,
                descricao_servico="Serviço encerrado.",
            )
