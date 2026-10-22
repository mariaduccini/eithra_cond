"""Operações do domínio de rotinas: gerar, começar, concluir, ignorar, cancelar (§§66–68).

Cada operação acontece inteira, em uma transação: ou a ocorrência e a rotina
mudam juntas, ou nada muda. Nada dispara sozinho — a rotina lembra, e quem cria
a consequência é a pessoa, na confirmação.
"""
from datetime import date, timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from apps.core.constants import Prioridade
from apps.demandas.constants import OrigemDemanda
from apps.demandas.models import Demanda
from apps.manutencoes.constants import TipoManutencao
from apps.manutencoes.models import Manutencao
from apps.rotinas.constants import (
    SITUACOES_PENDENTES,
    Periodicidade,
    ResultadoExecucao,
    StatusExecucao,
)
from apps.rotinas.services import (
    ErroDeRotina,
    abrir_demanda_da_execucao,
    cancelar_execucao,
    concluir_execucao,
    criar_manutencao_da_execucao,
    criar_rotina,
    definir_ativo,
    editar_rotina,
    gerar_proxima_execucao,
    ignorar_execucao,
    iniciar_execucao,
    tratar_item_checklist,
)
from apps.rotinas.tests.apoio import (
    criar_checklist,
    criar_condominio,
    criar_equipamento,
    criar_fornecedor,
    criar_item_do_checklist,
    criar_local,
    criar_pessoa,
)


class BaseDeRotinas(TestCase):
    """Um condomínio e uma gestão, com uma rotina mensal de datas previsíveis."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")

    def _criar(self, **campos):
        base = {
            "condominio": self.condominio,
            "criado_por": self.gestao,
            "titulo": "Limpeza da caixa d'água",
            "data_inicio": date(2026, 1, 10),
            "periodicidade_tipo": Periodicidade.MENSAL,
            "dia_mes": 10,
        }
        base.update(campos)
        return criar_rotina(**base)

    def _pendente(self, rotina):
        return rotina.execucoes.filter(status=StatusExecucao.PENDENTE).get()


class CriarRotinaTests(BaseDeRotinas):
    """Registrar a rotina já prepara a primeira ocorrência."""

    def test_cria_e_prepara_a_primeira_ocorrencia(self):
        rotina = self._criar()
        self.assertEqual(rotina.proxima_execucao, date(2026, 1, 10))
        self.assertEqual(rotina.execucoes.count(), 1)
        self.assertEqual(rotina.execucoes.get().data_prevista, date(2026, 1, 10))
        self.assertEqual(rotina.execucoes.get().status, StatusExecucao.PENDENTE)

    def test_titulo_espacos_sao_colapsados(self):
        rotina = self._criar(titulo="  Limpeza   da   caixa ")
        self.assertEqual(rotina.titulo, "Limpeza da caixa")

    def test_responsavel_vira_o_da_primeira_ocorrencia(self):
        rotina = self._criar(responsavel=self.gestao)
        self.assertEqual(rotina.execucoes.get().responsavel, self.gestao)

    def test_equipamento_com_local_preenche_o_local(self):
        local = criar_local(self.condominio, "Casa de máquinas")
        equipamento = criar_equipamento(self.condominio, "Bomba", local=local)
        rotina = self._criar(equipamento=equipamento)
        self.assertEqual(rotina.local, local)

    def test_local_divergente_do_equipamento_e_recusado(self):
        local = criar_local(self.condominio, "Casa de máquinas")
        outro_local = criar_local(self.condominio, "Telhado")
        equipamento = criar_equipamento(self.condominio, "Bomba", local=local)
        with self.assertRaises(ErroDeRotina):
            self._criar(equipamento=equipamento, local=outro_local)

    def test_dia_do_mes_invalido_e_recusado(self):
        with self.assertRaises(ErroDeRotina):
            self._criar(dia_mes=40)

    def test_fim_anterior_ao_inicio_e_recusado(self):
        with self.assertRaises(ErroDeRotina):
            self._criar(data_inicio=date(2026, 3, 1), data_fim=date(2026, 2, 1))


class EditarRotinaTests(BaseDeRotinas):
    """Editar a regra vale para o que vem depois; a ocorrência em aberto não se move."""

    def test_edicao_muda_o_ritmo_e_o_titulo(self):
        rotina = self._criar()
        pendente = self._pendente(rotina)
        editar_rotina(
            rotina=rotina,
            titulo="Limpeza trimestral",
            tipo=rotina.tipo,
            periodicidade_tipo=Periodicidade.MENSAL,
            intervalo=3,
            dia_mes=10,
            data_inicio=rotina.data_inicio,
            prioridade=Prioridade.NORMAL,
        )
        rotina.refresh_from_db()
        self.assertEqual(rotina.titulo, "Limpeza trimestral")
        self.assertEqual(rotina.intervalo, 3)
        # A ocorrência em aberto continua sendo a próxima.
        self.assertEqual(rotina.proxima_execucao, pendente.data_prevista)


class GerarProximaExecucaoTests(BaseDeRotinas):
    """Preparar a próxima ocorrência e nunca duplicar a mesma data (§§66–67)."""

    def test_prepara_a_ocorrencia_seguinte(self):
        rotina = self._criar()
        gerar_proxima_execucao(rotina)
        rotina.refresh_from_db()
        datas = set(rotina.execucoes.values_list("data_prevista", flat=True))
        self.assertEqual(datas, {date(2026, 1, 10), date(2026, 2, 10)})
        # Preparar a seguinte não muda a próxima a tratar: a de 10/01 continua
        # em aberto, e é ela que espera alguém.
        self.assertEqual(rotina.proxima_execucao, date(2026, 1, 10))

    def test_rotina_inativa_nao_gera(self):
        rotina = self._criar()
        definir_ativo(rotina=rotina, ativo=False)
        self.assertIsNone(gerar_proxima_execucao(rotina))
        self.assertEqual(rotina.execucoes.count(), 1)

    def test_sem_data_por_vir_devolve_nada(self):
        rotina = self._criar(data_fim=date(2026, 1, 10))
        # A primeira ocorrência ainda cabe no fim; a seguinte, não.
        self.assertIsNone(gerar_proxima_execucao(rotina))
        rotina.refresh_from_db()
        # Nada mais será preparado, mas a de 10/01 segue em aberto: é a próxima.
        self.assertEqual(rotina.proxima_execucao, date(2026, 1, 10))
        self.assertEqual(rotina.execucoes.count(), 1)

    def test_mesma_data_calculada_nao_gera_duas(self):
        # §67 — dupla tentativa de geração para a mesma data.
        rotina = self._criar()
        alvo = rotina.proxima_execucao
        with mock.patch(
            "apps.rotinas.services._calcular_proxima", return_value=alvo
        ):
            execucao = gerar_proxima_execucao(rotina)
        self.assertEqual(execucao.data_prevista, alvo)
        self.assertEqual(
            rotina.execucoes.filter(data_prevista=alvo).count(), 1
        )


class SemanticaDeProximaExecucaoTests(BaseDeRotinas):
    """``proxima_execucao`` diz sempre a próxima ocorrência a ser tratada.

    O campo não guarda a data que acabou de ser calculada — guarda a ocorrência
    em aberto mais antiga e, quando não há nenhuma, a próxima data válida da
    regra. O valor não depende de qual operação o escreveu.
    """

    def _abertas(self, rotina):
        """As datas das ocorrências que ainda esperam alguém."""
        return set(
            rotina.execucoes.filter(status__in=SITUACOES_PENDENTES).values_list(
                "data_prevista", flat=True
            )
        )

    def _em(self, rotina, dia):
        return rotina.execucoes.get(data_prevista=date(2026, *dia))

    def test_uma_ocorrencia_aberta_e_a_proxima(self):
        rotina = self._criar()
        self.assertEqual(rotina.proxima_execucao, date(2026, 1, 10))

    def test_duas_ocorrencias_abertas_aponta_para_a_mais_antiga(self):
        rotina = self._criar()
        gerar_proxima_execucao(rotina)
        rotina.refresh_from_db()
        self.assertEqual(len(self._abertas(rotina)), 2)
        self.assertEqual(rotina.proxima_execucao, date(2026, 1, 10))

    def test_geracao_antecipada_nao_move_a_proxima(self):
        # §5 — a de 21/10 entra adiantada; a de 20/10 continua sendo a próxima,
        # e só assume depois que ela for encerrada.
        rotina = self._criar(
            data_inicio=date(2026, 10, 20),
            periodicidade_tipo=Periodicidade.DIARIA,
            dia_mes=None,
        )
        gerar_proxima_execucao(rotina)
        rotina.refresh_from_db()
        self.assertEqual(
            self._abertas(rotina), {date(2026, 10, 20), date(2026, 10, 21)}
        )
        self.assertEqual(rotina.proxima_execucao, date(2026, 10, 20))

        concluir_execucao(
            execucao=self._em(rotina, (10, 20)),
            usuario=self.gestao,
            resultado=ResultadoExecucao.OK,
        )
        rotina.refresh_from_db()
        self.assertEqual(rotina.proxima_execucao, date(2026, 10, 21))

    def test_concluir_a_mais_antiga_move_a_proxima(self):
        rotina = self._criar()
        gerar_proxima_execucao(rotina)
        concluir_execucao(
            execucao=self._em(rotina, (1, 10)),
            usuario=self.gestao,
            resultado=ResultadoExecucao.OK,
        )
        rotina.refresh_from_db()
        self.assertEqual(rotina.proxima_execucao, date(2026, 2, 10))

    def test_ignorar_a_mais_antiga_move_a_proxima(self):
        rotina = self._criar()
        gerar_proxima_execucao(rotina)
        ignorar_execucao(
            execucao=self._em(rotina, (1, 10)),
            usuario=self.gestao,
            justificativa="Reservatório em manutenção.",
        )
        rotina.refresh_from_db()
        self.assertEqual(rotina.proxima_execucao, date(2026, 2, 10))

    def test_cancelar_a_mais_antiga_move_a_proxima(self):
        rotina = self._criar()
        gerar_proxima_execucao(rotina)
        cancelar_execucao(
            execucao=self._em(rotina, (1, 10)),
            usuario=self.gestao,
            justificativa="Não vai acontecer neste ciclo.",
        )
        rotina.refresh_from_db()
        self.assertEqual(rotina.proxima_execucao, date(2026, 2, 10))

    def test_sem_ocorrencia_aberta_a_proxima_vem_da_regra(self):
        # Um histórico sem nada em aberto: a data vem da regra, não de execução.
        rotina = self._criar()
        rotina.execucoes.all().delete()
        definir_ativo(rotina=rotina, ativo=False)
        rotina.refresh_from_db()
        self.assertIsNone(rotina.proxima_execucao)

        definir_ativo(rotina=rotina, ativo=True)
        rotina.refresh_from_db()
        self.assertEqual(rotina.proxima_execucao, date(2026, 1, 10))

    def test_fim_de_vigencia_esvazia_a_proxima(self):
        rotina = self._criar(data_fim=date(2026, 1, 10))
        concluir_execucao(
            execucao=self._pendente(rotina),
            usuario=self.gestao,
            resultado=ResultadoExecucao.OK,
        )
        rotina.refresh_from_db()
        # Nada em aberto e nenhuma data futura cabendo no fim de vigência.
        self.assertEqual(rotina.execucoes.count(), 1)
        self.assertIsNone(rotina.proxima_execucao)

    def test_rotina_inativa_sem_aberta_nao_tem_proxima(self):
        rotina = self._criar()
        rotina.execucoes.all().delete()
        definir_ativo(rotina=rotina, ativo=False)
        rotina.refresh_from_db()
        self.assertIsNone(rotina.proxima_execucao)

    def test_rotina_inativa_mantem_o_que_esta_em_aberto(self):
        # Desativar não apaga o que existe: a ocorrência em aberto segue sendo
        # o que espera alguém, e continua anunciada.
        rotina = self._criar()
        definir_ativo(rotina=rotina, ativo=False)
        rotina.refresh_from_db()
        self.assertEqual(rotina.execucoes.count(), 1)
        self.assertEqual(rotina.proxima_execucao, date(2026, 1, 10))


class IniciarExecucaoTests(BaseDeRotinas):
    """Começar registra quando, e só acontece uma vez."""

    def test_iniciar_grava_quando(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        iniciar_execucao(execucao=execucao, usuario=self.gestao)
        execucao.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.EM_ANDAMENTO)
        self.assertIsNotNone(execucao.iniciado_em)

    def test_iniciar_de_novo_e_recusado(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        iniciar_execucao(execucao=execucao, usuario=self.gestao)
        with self.assertRaises(ErroDeRotina):
            iniciar_execucao(execucao=execucao, usuario=self.gestao)

    def test_iniciar_encerrada_e_recusada(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        ignorar_execucao(
            execucao=execucao, usuario=self.gestao, justificativa="Não se aplicava."
        )
        execucao.refresh_from_db()
        with self.assertRaises(ErroDeRotina):
            iniciar_execucao(execucao=execucao, usuario=self.gestao)


class ConcluirExecucaoTests(BaseDeRotinas):
    """Concluir exige o resultado e prepara a ocorrência seguinte."""

    def test_concluir_registra_e_prepara_a_proxima(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        concluir_execucao(
            execucao=execucao,
            usuario=self.gestao,
            resultado=ResultadoExecucao.OK,
            observacao="Caixa limpa.",
        )
        execucao.refresh_from_db()
        rotina.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.CONCLUIDA)
        self.assertIsNotNone(execucao.concluido_em)
        self.assertEqual(execucao.observacao_execucao, "Caixa limpa.")
        self.assertEqual(rotina.proxima_execucao, date(2026, 2, 10))
        self.assertEqual(rotina.execucoes.count(), 2)

    def test_concluir_exige_resultado(self):
        rotina = self._criar()
        with self.assertRaises(ErroDeRotina):
            concluir_execucao(
                execucao=self._pendente(rotina),
                usuario=self.gestao,
                resultado="",
            )

    def test_concluir_duas_vezes_e_recusado(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        concluir_execucao(
            execucao=execucao, usuario=self.gestao, resultado=ResultadoExecucao.OK
        )
        with self.assertRaises(ErroDeRotina):
            concluir_execucao(
                execucao=execucao,
                usuario=self.gestao,
                resultado=ResultadoExecucao.OK,
            )


class ConclusaoComDataDoFatoTests(BaseDeRotinas):
    """Concluir guarda o dia da verificação, e a agenda continua vindo da regra.

    Uma rotina feita em setembro pode ser lançada agora: o dia informado vai
    para ``concluido_em``, que é o quando do fato. A próxima ocorrência continua
    vindo de ``data_prevista`` — por isso um lançamento retroativo não puxa a
    agenda para trás.
    """

    def _dia_guardado(self, execucao):
        execucao.refresh_from_db()
        return timezone.localtime(execucao.concluido_em).date()

    def test_execucao_de_hoje(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        concluir_execucao(
            execucao=execucao,
            usuario=self.gestao,
            resultado=ResultadoExecucao.OK,
            data_execucao=timezone.localdate(),
        )
        self.assertEqual(self._dia_guardado(execucao), timezone.localdate())

    def test_execucao_historica_guarda_o_dia_do_fato(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        feita_em = timezone.localdate() - timedelta(days=40)
        concluir_execucao(
            execucao=execucao,
            usuario=self.gestao,
            resultado=ResultadoExecucao.OK,
            data_execucao=feita_em,
        )
        self.assertEqual(self._dia_guardado(execucao), feita_em)

    def test_data_futura_e_recusada(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        amanha = timezone.localdate() + timedelta(days=1)
        with self.assertRaises(ErroDeRotina):
            concluir_execucao(
                execucao=execucao,
                usuario=self.gestao,
                resultado=ResultadoExecucao.OK,
                data_execucao=amanha,
            )
        execucao.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.PENDENTE)
        self.assertIsNone(execucao.concluido_em)

    def test_sem_data_informada_vale_o_momento_do_registro(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        concluir_execucao(
            execucao=execucao,
            usuario=self.gestao,
            resultado=ResultadoExecucao.OK,
        )
        self.assertEqual(self._dia_guardado(execucao), timezone.localdate())

    def test_resultado_e_preservado(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        concluir_execucao(
            execucao=execucao,
            usuario=self.gestao,
            resultado=ResultadoExecucao.PROBLEMA,
            observacao="Vazamento encontrado.",
            data_execucao=timezone.localdate() - timedelta(days=10),
        )
        execucao.refresh_from_db()
        self.assertEqual(execucao.resultado, ResultadoExecucao.PROBLEMA)
        self.assertEqual(execucao.observacao_execucao, "Vazamento encontrado.")

    def test_execucao_historica_encerra_sem_tocar_no_ciclo(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        ciclo = execucao.data_prevista
        concluir_execucao(
            execucao=execucao,
            usuario=self.gestao,
            resultado=ResultadoExecucao.OK,
            data_execucao=timezone.localdate() - timedelta(days=40),
        )
        execucao.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.CONCLUIDA)
        self.assertEqual(execucao.data_prevista, ciclo)

    def test_execucao_retroativa_nao_corrompe_a_proxima(self):
        # §9: registrar uma execução em data passada não puxa a agenda para trás.
        hoje = timezone.localdate()
        rotina = self._criar(
            data_inicio=hoje, periodicidade_tipo=Periodicidade.DIARIA, dia_mes=None
        )
        execucao = self._pendente(rotina)
        self.assertEqual(execucao.data_prevista, hoje)

        concluir_execucao(
            execucao=execucao,
            usuario=self.gestao,
            resultado=ResultadoExecucao.OK,
            data_execucao=hoje - timedelta(days=30),
        )
        execucao.refresh_from_db()
        rotina.refresh_from_db()

        self.assertEqual(execucao.data_prevista, hoje)
        # A agenda avança a partir da ocorrência (amanhã), nunca da data informada.
        self.assertEqual(rotina.proxima_execucao, hoje + timedelta(days=1))

    def test_checklist_obrigatorio_segura_a_conclusao(self):
        checklist = criar_checklist(self.condominio, self.gestao)
        criar_item_do_checklist(checklist, "Fechar o registro", obrigatorio=True)
        rotina = self._criar(checklist_modelo=checklist)
        execucao = self._pendente(rotina)

        with self.assertRaises(ErroDeRotina):
            concluir_execucao(
                execucao=execucao,
                usuario=self.gestao,
                resultado=ResultadoExecucao.OK,
                data_execucao=timezone.localdate() - timedelta(days=5),
            )

        item = execucao.itens.get()
        tratar_item_checklist(
            item=item,
            usuario=self.gestao,
            status="concluido",
        )
        concluir_execucao(
            execucao=execucao,
            usuario=self.gestao,
            resultado=ResultadoExecucao.OK,
            data_execucao=timezone.localdate() - timedelta(days=5),
        )
        execucao.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.CONCLUIDA)

    def test_abrir_demanda_continua_funcionando(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        concluir_execucao(
            execucao=execucao,
            usuario=self.gestao,
            resultado=ResultadoExecucao.PROBLEMA,
            data_execucao=timezone.localdate() - timedelta(days=20),
        )
        abrir_demanda_da_execucao(
            execucao=execucao,
            usuario=self.gestao,
            titulo="Vazamento na caixa",
        )
        execucao.refresh_from_db()
        self.assertIsNotNone(execucao.demanda_gerada)
        self.assertEqual(execucao.demanda_gerada.titulo, "Vazamento na caixa")

    def test_criar_manutencao_continua_funcionando(self):
        rotina = self._criar(gera_manutencao=True)
        execucao = self._pendente(rotina)
        concluir_execucao(
            execucao=execucao,
            usuario=self.gestao,
            resultado=ResultadoExecucao.PROBLEMA,
            data_execucao=timezone.localdate() - timedelta(days=20),
        )
        criar_manutencao_da_execucao(
            execucao=execucao,
            usuario=self.gestao,
            titulo="Revisar bomba",
        )
        execucao.refresh_from_db()
        self.assertIsNotNone(execucao.manutencao_gerada)
        self.assertEqual(execucao.manutencao_gerada.tipo, TipoManutencao.PREVENTIVA)


class IgnorarExecucaoTests(BaseDeRotinas):
    """Ignorar diz que a atividade não se aplicava; a rotina segue adiante."""

    def test_ignorar_exige_justificativa(self):
        rotina = self._criar()
        with self.assertRaises(ErroDeRotina):
            ignorar_execucao(
                execucao=self._pendente(rotina), usuario=self.gestao, justificativa="  "
            )

    def test_ignorar_encerra_e_prepara_a_proxima(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        ignorar_execucao(
            execucao=execucao,
            usuario=self.gestao,
            justificativa="Reservatório em manutenção.",
        )
        execucao.refresh_from_db()
        rotina.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.IGNORADA)
        self.assertEqual(rotina.proxima_execucao, date(2026, 2, 10))


class CancelarExecucaoTests(BaseDeRotinas):
    """Cancelar encerra a ocorrência sem cancelar a rotina."""

    def test_cancelar_exige_justificativa(self):
        rotina = self._criar()
        with self.assertRaises(ErroDeRotina):
            cancelar_execucao(
                execucao=self._pendente(rotina), usuario=self.gestao, justificativa=""
            )

    def test_cancelar_encerra_e_prepara_a_proxima(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        cancelar_execucao(
            execucao=execucao,
            usuario=self.gestao,
            justificativa="Não vai acontecer neste ciclo.",
        )
        execucao.refresh_from_db()
        rotina.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.CANCELADA)
        self.assertTrue(rotina.ativo)
        self.assertEqual(rotina.proxima_execucao, date(2026, 2, 10))


class DefinirAtivoTests(BaseDeRotinas):
    """Desativar é retirada administrativa: para de gerar, não apaga nada."""

    def test_desativar_nao_apaga_o_que_existe(self):
        rotina = self._criar()
        definir_ativo(rotina=rotina, ativo=False)
        rotina.refresh_from_db()
        self.assertFalse(rotina.ativo)
        self.assertEqual(rotina.execucoes.count(), 1)

    def test_reativar_mantem_a_ocorrencia_em_aberto(self):
        rotina = self._criar()
        pendente = self._pendente(rotina)
        definir_ativo(rotina=rotina, ativo=False)
        definir_ativo(rotina=rotina, ativo=True)
        rotina.refresh_from_db()
        self.assertTrue(rotina.ativo)
        self.assertEqual(rotina.proxima_execucao, pendente.data_prevista)
        self.assertEqual(rotina.execucoes.count(), 1)


class IntegracaoTests(BaseDeRotinas):
    """A consequência nasce só na confirmação, com procedência e vínculo (§68)."""

    def test_abrir_demanda_registra_a_procedencia(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        self.assertEqual(Demanda.objects.count(), 0)

        abrir_demanda_da_execucao(
            execucao=execucao,
            usuario=self.gestao,
            titulo="Vazamento na caixa",
            descricao="Encontrado durante a limpeza.",
            prioridade=Prioridade.ALTA,
        )
        execucao.refresh_from_db()
        demanda = execucao.demanda_gerada
        self.assertIsNotNone(demanda)
        self.assertEqual(demanda.origem, OrigemDemanda.SISTEMA)
        self.assertEqual(demanda.criado_por, self.gestao)
        self.assertEqual(demanda.titulo, "Vazamento na caixa")

    def test_demanda_sem_titulo_usa_o_da_rotina(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        abrir_demanda_da_execucao(execucao=execucao, usuario=self.gestao)
        execucao.refresh_from_db()
        self.assertEqual(
            execucao.demanda_gerada.titulo, "Rotina: Limpeza da caixa d'água"
        )

    def test_nao_abre_duas_demandas(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        abrir_demanda_da_execucao(execucao=execucao, usuario=self.gestao)
        with self.assertRaises(ErroDeRotina):
            abrir_demanda_da_execucao(execucao=execucao, usuario=self.gestao)
        self.assertEqual(Demanda.objects.count(), 1)

    def test_criar_manutencao_traz_o_contexto_da_rotina(self):
        local = criar_local(self.condominio, "Casa de máquinas")
        equipamento = criar_equipamento(self.condominio, "Bomba", local=local)
        fornecedor = criar_fornecedor(self.condominio, "Bombas Alfa")
        rotina = self._criar(
            equipamento=equipamento,
            local=local,
            fornecedor=fornecedor,
            responsavel=self.gestao,
        )
        execucao = self._pendente(rotina)
        self.assertEqual(Manutencao.objects.count(), 0)

        criar_manutencao_da_execucao(
            execucao=execucao,
            usuario=self.gestao,
            titulo="Revisar bomba",
            descricao_problema="Ruído acima do normal.",
        )
        execucao.refresh_from_db()
        manutencao = execucao.manutencao_gerada
        self.assertIsNotNone(manutencao)
        self.assertEqual(manutencao.tipo, TipoManutencao.PREVENTIVA)
        self.assertEqual(manutencao.local, local)
        self.assertEqual(manutencao.equipamento, equipamento)
        self.assertEqual(manutencao.fornecedor, fornecedor)
        self.assertEqual(manutencao.responsavel, self.gestao)
        self.assertEqual(manutencao.data_prevista, execucao.data_prevista)

    def test_nao_cria_duas_manutencoes(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        criar_manutencao_da_execucao(execucao=execucao, usuario=self.gestao)
        with self.assertRaises(ErroDeRotina):
            criar_manutencao_da_execucao(execucao=execucao, usuario=self.gestao)
        self.assertEqual(Manutencao.objects.count(), 1)

    def test_concluir_nao_cria_consequencia_sozinho(self):
        rotina = self._criar()
        execucao = self._pendente(rotina)
        concluir_execucao(
            execucao=execucao,
            usuario=self.gestao,
            resultado=ResultadoExecucao.PROBLEMA,
            observacao="Vazamento encontrado.",
        )
        # Nada de demanda ou manutenção nasce sozinho; nem a rotina muda.
        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(Manutencao.objects.count(), 0)
        rotina.refresh_from_db()
        self.assertTrue(rotina.ativo)
        self.assertEqual(rotina.titulo, "Limpeza da caixa d'água")
