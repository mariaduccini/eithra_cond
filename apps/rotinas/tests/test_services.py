"""Operações do domínio de rotinas: gerar, começar, concluir, ignorar, cancelar (§§66–68).

Cada operação acontece inteira, em uma transação: ou a ocorrência e a rotina
mudam juntas, ou nada muda. Nada dispara sozinho — a rotina lembra, e quem cria
a consequência é a pessoa, na confirmação.
"""
from datetime import date
from unittest import mock

from django.test import TestCase

from apps.core.constants import Prioridade
from apps.demandas.constants import OrigemDemanda
from apps.demandas.models import Demanda
from apps.manutencoes.constants import TipoManutencao
from apps.manutencoes.models import Manutencao
from apps.rotinas.constants import (
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
)
from apps.rotinas.tests.apoio import (
    criar_condominio,
    criar_equipamento,
    criar_fornecedor,
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
        self.assertEqual(rotina.proxima_execucao, date(2026, 2, 10))

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
        self.assertIsNone(rotina.proxima_execucao)

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
