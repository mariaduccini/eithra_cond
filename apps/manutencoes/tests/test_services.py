"""Operações do domínio de manutenções: criar, editar, mover, concluir, cancelar."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.core.constants import Prioridade
from apps.manutencoes.constants import (
    StatusManutencao,
    TipoEventoManutencao,
    TipoManutencao,
)
from apps.manutencoes.models import HistoricoManutencao, Manutencao
from apps.manutencoes.services import (
    ErroDeManutencao,
    alterar_status,
    cancelar_manutencao,
    concluir_manutencao,
    criar_manutencao,
    definir_ativo,
    editar_manutencao,
)
from apps.manutencoes.tests.apoio import (
    criar_condominio,
    criar_demanda,
    criar_equipamento,
    criar_fornecedor,
    criar_local,
    criar_pessoa,
)


class CriarManutencaoTests(TestCase):
    """Registrar é o primeiro evento da memória da manutenção."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")

    def test_cria_e_registra_no_historico(self):
        manutencao = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Trocar a bomba do reservatório",
        )
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)
        self.assertEqual(manutencao.tipo, TipoManutencao.CORRETIVA)
        self.assertEqual(manutencao.prioridade, Prioridade.NORMAL)

        evento = manutencao.historico.get()
        self.assertEqual(evento.tipo_evento, TipoEventoManutencao.CRIADA)
        self.assertEqual(evento.usuario, self.gestao)

    def test_titulo_espacos_sao_colapsados(self):
        manutencao = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="  Troca   da   bomba ",
        )
        self.assertEqual(manutencao.titulo, "Troca da bomba")

    def test_nascer_de_demanda_nao_altera_a_demanda(self):
        demanda = criar_demanda(
            self.condominio,
            self.gestao,
            titulo="Vazamento no subsolo",
            descricao="Água acumulando perto do quadro.",
            prioridade=Prioridade.ALTA,
        )
        antes = (demanda.status, demanda.prioridade, demanda.titulo)

        manutencao = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo=demanda.titulo,
            demanda_origem=demanda,
        )
        self.assertEqual(manutencao.demanda_origem, demanda)

        # A procedência é preservada sem tocar na demanda: ela continua como estava.
        demanda.refresh_from_db()
        self.assertEqual((demanda.status, demanda.prioridade, demanda.titulo), antes)

    def test_equipamento_com_local_preenche_o_local(self):
        local = criar_local(self.condominio, "Casa de máquinas")
        equipamento = criar_equipamento(self.condominio, "Bomba", local=local)
        manutencao = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Revisar bomba",
            equipamento=equipamento,
        )
        self.assertEqual(manutencao.local, local)

    def test_local_divergente_do_equipamento_e_recusado(self):
        local = criar_local(self.condominio, "Casa de máquinas")
        outro_local = criar_local(self.condominio, "Telhado")
        equipamento = criar_equipamento(self.condominio, "Bomba", local=local)
        with self.assertRaises(ErroDeManutencao):
            criar_manutencao(
                condominio=self.condominio,
                criado_por=self.gestao,
                titulo="Revisar bomba",
                equipamento=equipamento,
                local=outro_local,
            )

    def test_demanda_de_outro_condominio_e_recusada(self):
        outro = criar_condominio("Beta")
        alheia = criar_demanda(outro, self.gestao, "Demanda do Beta")
        with self.assertRaises(ErroDeManutencao):
            criar_manutencao(
                condominio=self.condominio,
                criado_por=self.gestao,
                titulo="Serviço",
                demanda_origem=alheia,
            )

    def test_custo_previsto_negativo_e_recusado(self):
        with self.assertRaises(ErroDeManutencao):
            criar_manutencao(
                condominio=self.condominio,
                criado_por=self.gestao,
                titulo="Serviço",
                custo_previsto=-1,
            )


class EditarManutencaoTests(TestCase):
    """Só o que mudou vira evento: o histórico registra decisão."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        self.manutencao = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Original",
        )

    def _editar(self, **campos):
        base = {
            "manutencao": self.manutencao,
            "usuario": self.gestao,
            "tipo": self.manutencao.tipo,
            "titulo": self.manutencao.titulo,
            "prioridade": self.manutencao.prioridade,
        }
        base.update(campos)
        return editar_manutencao(**base)

    def test_mudanca_de_responsavel_vira_evento(self):
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        self._editar(responsavel=operacional)
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.responsavel, operacional)
        evento = self.manutencao.historico.filter(
            tipo_evento=TipoEventoManutencao.RESPONSAVEL
        ).get()
        self.assertEqual(evento.dados_novos["responsavel"], str(operacional.pk))

    def test_mudanca_de_prioridade_vira_evento(self):
        self._editar(prioridade=Prioridade.URGENTE)
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.prioridade, Prioridade.URGENTE)
        self.assertTrue(
            self.manutencao.historico.filter(
                tipo_evento=TipoEventoManutencao.PRIORIDADE
            ).exists()
        )

    def test_mudanca_de_tipo_vira_evento(self):
        self._editar(tipo=TipoManutencao.PREVENTIVA)
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.tipo, TipoManutencao.PREVENTIVA)

    def test_salvar_sem_mudar_nao_gera_evento(self):
        self._editar()
        # Apenas o evento de criação deve existir.
        self.assertEqual(self.manutencao.historico.count(), 1)

    def test_mudanca_de_custo_vira_evento(self):
        self._editar(custo_previsto=150)
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.custo_previsto, 150)


class AlterarStatusTests(TestCase):
    """Começar registra quando; voltar limpa a conclusão; cancelar tem ação própria."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        self.manutencao = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Serviço",
        )

    def test_primeira_entrada_em_andamento_grava_inicio(self):
        alterar_status(
            manutencao=self.manutencao,
            usuario=self.gestao,
            novo_status=StatusManutencao.EM_ANDAMENTO,
        )
        self.manutencao.refresh_from_db()
        self.assertIsNotNone(self.manutencao.data_inicio)

    def test_inicio_nao_e_reescrito_a_cada_ida_e_volta(self):
        alterar_status(
            manutencao=self.manutencao,
            usuario=self.gestao,
            novo_status=StatusManutencao.EM_ANDAMENTO,
        )
        primeiro_inicio = self.manutencao.data_inicio

        alterar_status(
            manutencao=self.manutencao,
            usuario=self.gestao,
            novo_status=StatusManutencao.AGUARDANDO,
        )
        alterar_status(
            manutencao=self.manutencao,
            usuario=self.gestao,
            novo_status=StatusManutencao.EM_ANDAMENTO,
        )
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.data_inicio, primeiro_inicio)

    def test_movimento_simples_vira_evento_de_status(self):
        alterar_status(
            manutencao=self.manutencao,
            usuario=self.gestao,
            novo_status=StatusManutencao.PROGRAMADA,
        )
        self.assertTrue(
            self.manutencao.historico.filter(
                tipo_evento=TipoEventoManutencao.STATUS
            ).exists()
        )

    def test_concluir_pelo_movimento_comum_e_recusado(self):
        with self.assertRaises(ErroDeManutencao):
            alterar_status(
                manutencao=self.manutencao,
                usuario=self.gestao,
                novo_status=StatusManutencao.CONCLUIDA,
            )

    def test_cancelar_pelo_movimento_delega_para_o_cancelamento(self):
        alterar_status(
            manutencao=self.manutencao,
            usuario=self.gestao,
            novo_status=StatusManutencao.CANCELADA,
        )
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.status, StatusManutencao.CANCELADA)
        self.assertIsNone(self.manutencao.data_conclusao)

    def test_sair_da_conclusao_limpa_a_data(self):
        concluir_manutencao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Serviço feito.",
        )
        self.manutencao.refresh_from_db()
        self.assertIsNotNone(self.manutencao.data_conclusao)

        alterar_status(
            manutencao=self.manutencao,
            usuario=self.gestao,
            novo_status=StatusManutencao.EM_ANDAMENTO,
        )
        self.manutencao.refresh_from_db()
        self.assertIsNone(self.manutencao.data_conclusao)

    def test_mesma_situacao_nao_gera_evento(self):
        alterar_status(
            manutencao=self.manutencao,
            usuario=self.gestao,
            novo_status=StatusManutencao.ABERTA,
        )
        self.assertEqual(self.manutencao.historico.count(), 1)

    def test_situacao_invalida_e_recusada(self):
        with self.assertRaises(ErroDeManutencao):
            alterar_status(
                manutencao=self.manutencao,
                usuario=self.gestao,
                novo_status="inexistente",
            )


class ConcluirManutencaoTests(TestCase):
    """Concluir exige dizer o que foi feito e registra quando."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        self.manutencao = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Serviço",
        )

    def test_concluir_registra_quando_e_servico(self):
        concluir_manutencao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Bomba substituída.",
        )
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.status, StatusManutencao.CONCLUIDA)
        self.assertIsNotNone(self.manutencao.data_conclusao)
        self.assertEqual(self.manutencao.descricao_servico, "Bomba substituída.")
        self.assertTrue(
            self.manutencao.historico.filter(
                tipo_evento=TipoEventoManutencao.CONCLUIDA
            ).exists()
        )

    def test_servico_e_obrigatorio(self):
        with self.assertRaises(ErroDeManutencao):
            concluir_manutencao(
                manutencao=self.manutencao,
                usuario=self.gestao,
                descricao_servico="   ",
            )

    def test_custo_real_pode_ficar_vazio(self):
        concluir_manutencao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Feito.",
        )
        self.manutencao.refresh_from_db()
        self.assertIsNone(self.manutencao.custo_real)

    def test_custo_real_e_registrado_quando_informado(self):
        concluir_manutencao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Feito.",
            custo_real=320,
        )
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.custo_real, 320)

    def test_concluir_duas_vezes_e_recusado(self):
        concluir_manutencao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Feito.",
        )
        with self.assertRaises(ErroDeManutencao):
            concluir_manutencao(
                manutencao=self.manutencao,
                usuario=self.gestao,
                descricao_servico="De novo.",
            )


class ConclusaoComDataDoFatoTests(TestCase):
    """Concluir guarda o dia em que o serviço aconteceu, não o dia do registro.

    Uma preventiva feita em setembro pode ser lançada agora; o que fica guardado
    é o dia do serviço. O futuro é recusado porque ainda não aconteceu.
    """

    def setUp(self):
        self.condominio = criar_condominio()
        self.gestao = criar_pessoa("gestao@data.test", "Gestão")
        self.manutencao = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Preventiva do elevador",
        )

    def _dia_guardado(self):
        self.manutencao.refresh_from_db()
        return timezone.localtime(self.manutencao.data_conclusao).date()

    def test_conclusao_com_a_data_de_hoje(self):
        concluir_manutencao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Revisão feita.",
            data_conclusao=timezone.localdate(),
        )
        self.assertEqual(self._dia_guardado(), timezone.localdate())

    def test_conclusao_retroativa_guarda_o_dia_do_servico(self):
        # §5: hoje é 22/10, mas o serviço foi em 15/09 — o dia guardado é o do fato.
        servico_em = timezone.localdate() - timedelta(days=37)
        concluir_manutencao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Bomba substituída.",
            data_conclusao=servico_em,
        )
        self.assertEqual(self._dia_guardado(), servico_em)

    def test_data_futura_e_recusada(self):
        amanha = timezone.localdate() + timedelta(days=1)
        with self.assertRaises(ErroDeManutencao):
            concluir_manutencao(
                manutencao=self.manutencao,
                usuario=self.gestao,
                descricao_servico="Feito.",
                data_conclusao=amanha,
            )
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.status, StatusManutencao.ABERTA)
        self.assertIsNone(self.manutencao.data_conclusao)

    def test_sem_data_informada_vale_o_momento_do_registro(self):
        concluir_manutencao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Feito.",
        )
        self.assertEqual(self._dia_guardado(), timezone.localdate())

    def test_conclusao_muda_a_situacao_para_concluida(self):
        concluir_manutencao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Feito.",
            data_conclusao=timezone.localdate(),
        )
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.status, StatusManutencao.CONCLUIDA)

    def test_historico_registra_uma_unica_conclusao(self):
        concluir_manutencao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Feito.",
            data_conclusao=timezone.localdate(),
        )
        eventos = self.manutencao.historico.filter(
            tipo_evento=TipoEventoManutencao.CONCLUIDA
        )
        self.assertEqual(eventos.count(), 1)

    def test_custo_real_e_preservado_na_conclusao(self):
        concluir_manutencao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Feito.",
            custo_real=320,
            data_conclusao=timezone.localdate(),
        )
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.custo_real, 320)

    def test_servico_realizado_e_preservado_na_conclusao(self):
        concluir_manutencao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Trocado o rolamento do motor.",
            data_conclusao=timezone.localdate(),
        )
        self.manutencao.refresh_from_db()
        self.assertEqual(
            self.manutencao.descricao_servico, "Trocado o rolamento do motor."
        )

    def test_conclusao_preserva_demanda_de_origem_e_equipamento(self):
        local = criar_local(self.condominio, "Casa de máquinas")
        equipamento = criar_equipamento(self.condominio, "Bomba", local=local)
        demanda = criar_demanda(self.condominio, self.gestao, "Barulho na bomba")
        manutencao = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Revisar bomba",
            equipamento=equipamento,
            demanda_origem=demanda,
        )
        concluir_manutencao(
            manutencao=manutencao,
            usuario=self.gestao,
            descricao_servico="Feito.",
            data_conclusao=timezone.localdate(),
        )
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.demanda_origem, demanda)
        self.assertEqual(manutencao.equipamento, equipamento)
        self.assertEqual(manutencao.local, local)


class CancelarManutencaoTests(TestCase):
    """Cancelar é desfecho operacional: registra quem e quando, sem data de conclusão."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        self.manutencao = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Serviço",
        )

    def test_cancelar_registra_sem_data_de_conclusao(self):
        cancelar_manutencao(
            manutencao=self.manutencao, usuario=self.gestao, motivo="Serviço não será feito"
        )
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.status, StatusManutencao.CANCELADA)
        self.assertIsNone(self.manutencao.data_conclusao)
        evento = self.manutencao.historico.filter(
            tipo_evento=TipoEventoManutencao.CANCELADA
        ).get()
        self.assertIn("Serviço não será feito", evento.descricao)

    def test_cancelar_sem_motivo_e_valido(self):
        cancelar_manutencao(manutencao=self.manutencao, usuario=self.gestao)
        self.manutencao.refresh_from_db()
        self.assertEqual(self.manutencao.status, StatusManutencao.CANCELADA)

    def test_cancelar_limpa_conclusao_anterior(self):
        concluir_manutencao(
            manutencao=self.manutencao,
            usuario=self.gestao,
            descricao_servico="Feito.",
        )
        cancelar_manutencao(manutencao=self.manutencao, usuario=self.gestao)
        self.manutencao.refresh_from_db()
        self.assertIsNone(self.manutencao.data_conclusao)

    def test_cancelar_duas_vezes_e_recusado(self):
        cancelar_manutencao(manutencao=self.manutencao, usuario=self.gestao)
        with self.assertRaises(ErroDeManutencao):
            cancelar_manutencao(manutencao=self.manutencao, usuario=self.gestao)


class DefinirAtivoTests(TestCase):
    """Desativar é retirada administrativa: não muda situação nem histórico."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        self.manutencao = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Serviço",
        )

    def test_desativar_nao_mexe_na_situacao_nem_no_historico(self):
        definir_ativo(manutencao=self.manutencao, ativo=False)
        self.manutencao.refresh_from_db()
        self.assertFalse(self.manutencao.ativo)
        self.assertEqual(self.manutencao.status, StatusManutencao.ABERTA)
        self.assertEqual(self.manutencao.historico.count(), 1)

    def test_devolver_a_operacao(self):
        definir_ativo(manutencao=self.manutencao, ativo=False)
        definir_ativo(manutencao=self.manutencao, ativo=True)
        self.manutencao.refresh_from_db()
        self.assertTrue(self.manutencao.ativo)


class TransacaoTests(TestCase):
    """A manutenção e sua memória mudam juntas, ou nada muda."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")

    def test_criar_grava_manutencao_e_historico_juntos(self):
        criar_manutencao(
            condominio=self.condominio, criado_por=self.gestao, titulo="Nova"
        )
        self.assertEqual(Manutencao.objects.count(), 1)
        self.assertEqual(HistoricoManutencao.objects.count(), 1)

    def test_falha_ao_registrar_desfaz_a_criacao(self):
        from apps.manutencoes import services

        original = services._registrar

        def _explode(*args, **kwargs):
            raise RuntimeError("falha proposital")

        services._registrar = _explode
        try:
            with self.assertRaises(RuntimeError):
                services.criar_manutencao(
                    condominio=self.condominio,
                    criado_por=self.gestao,
                    titulo="Sem memória",
                )
        finally:
            services._registrar = original

        self.assertEqual(Manutencao.objects.count(), 0)
        self.assertEqual(HistoricoManutencao.objects.count(), 0)
