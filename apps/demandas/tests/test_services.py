"""Operações do domínio: criar, editar, mover, concluir, cancelar (§16, §34)."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.demandas.constants import (
    PrioridadeDemanda,
    StatusDemanda,
    TipoEventoDemanda,
)
from apps.demandas.models import Demanda, HistoricoDemanda
from apps.demandas.services import (
    ErroDeDemanda,
    alterar_status,
    criar_demanda,
    editar_demanda,
    registrar_observacao,
)
from apps.demandas.tests.apoio import criar_condominio, criar_local, criar_pessoa


class CriarDemandaTests(TestCase):
    """Registrar demanda é o primeiro evento da memória dela (§16)."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")

    def test_cria_e_registra_no_historico(self):
        demanda = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Trocar lâmpada do corredor",
        )
        self.assertEqual(demanda.titulo, "Trocar lâmpada do corredor")
        self.assertEqual(demanda.status, StatusDemanda.ABERTA)
        self.assertEqual(demanda.prioridade, PrioridadeDemanda.NORMAL)

        evento = demanda.historico.get()
        self.assertEqual(evento.tipo_evento, TipoEventoDemanda.CRIADA)
        self.assertEqual(evento.usuario, self.gestao)

    def test_titulo_espacos_sao_colapsados(self):
        demanda = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="  Vazamento   na   garagem ",
        )
        self.assertEqual(demanda.titulo, "Vazamento na garagem")

    def test_descricao_preserva_paragrafos(self):
        demanda = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Compra",
            descricao="Primeira linha.\n\nSegunda linha.",
        )
        self.assertEqual(demanda.descricao, "Primeira linha.\n\nSegunda linha.")

    def test_local_do_cadastro_e_gravado(self):
        local = criar_local(self.condominio, "Garagem")
        demanda = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Vazamento",
            local=local,
        )
        self.assertEqual(demanda.local, local)


class EditarDemandaTests(TestCase):
    """Só o que mudou vira evento: o histórico registra decisão (§11)."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        self.demanda = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Original",
        )

    def test_mudanca_de_responsavel_vira_evento(self):
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        editar_demanda(
            demanda=self.demanda,
            usuario=self.gestao,
            titulo=self.demanda.titulo,
            prioridade=self.demanda.prioridade,
            responsavel=operacional,
        )
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.responsavel, operacional)

        evento = self.demanda.historico.filter(
            tipo_evento=TipoEventoDemanda.RESPONSAVEL
        ).get()
        self.assertEqual(evento.dados_novos["responsavel"], str(operacional.pk))

    def test_mudanca_de_prioridade_vira_evento(self):
        editar_demanda(
            demanda=self.demanda,
            usuario=self.gestao,
            titulo=self.demanda.titulo,
            prioridade=PrioridadeDemanda.ALTA,
        )
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.prioridade, PrioridadeDemanda.ALTA)
        self.assertTrue(
            self.demanda.historico.filter(
                tipo_evento=TipoEventoDemanda.PRIORIDADE
            ).exists()
        )

    def test_salvar_sem_mudar_nao_gera_evento(self):
        editar_demanda(
            demanda=self.demanda,
            usuario=self.gestao,
            titulo=self.demanda.titulo,
            prioridade=self.demanda.prioridade,
            descricao=self.demanda.descricao,
            local_contexto=self.demanda.local_contexto,
        )
        # Apenas o evento de criação deve existir.
        self.assertEqual(self.demanda.historico.count(), 1)

    def test_mudanca_de_local_vira_evento(self):
        local = criar_local(self.condominio, "Garagem")
        editar_demanda(
            demanda=self.demanda,
            usuario=self.gestao,
            titulo=self.demanda.titulo,
            prioridade=self.demanda.prioridade,
            local=local,
        )
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.local, local)
        self.assertTrue(
            self.demanda.historico.filter(tipo_evento=TipoEventoDemanda.EDICAO).exists()
        )

    def test_edicao_preserva_o_texto_legado_quando_nao_informado(self):
        self.demanda.local_contexto = "piscina"
        self.demanda.save(update_fields=["local_contexto"])

        editar_demanda(
            demanda=self.demanda,
            usuario=self.gestao,
            titulo="Título novo",
            prioridade=self.demanda.prioridade,
        )
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.local_contexto, "piscina")

    def test_mudanca_de_prazo_vira_evento(self):
        novo_prazo = timezone.localdate() + timedelta(days=10)
        editar_demanda(
            demanda=self.demanda,
            usuario=self.gestao,
            titulo=self.demanda.titulo,
            prioridade=self.demanda.prioridade,
            prazo=novo_prazo,
        )
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.prazo, novo_prazo)
        self.assertTrue(
            self.demanda.historico.filter(tipo_evento=TipoEventoDemanda.PRAZO).exists()
        )


class AlterarStatusTests(TestCase):
    """Concluir registra quando; cancelar registra quando; voltar limpa (§4, §11)."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        self.demanda = criar_demanda(
            condominio=self.condominio, criado_por=self.gestao, titulo="Demanda"
        )

    def test_movimento_simples_vira_evento_de_status(self):
        alterar_status(
            demanda=self.demanda, usuario=self.gestao, novo_status=StatusDemanda.EM_ANDAMENTO
        )
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.status, StatusDemanda.EM_ANDAMENTO)
        self.assertTrue(
            self.demanda.historico.filter(tipo_evento=TipoEventoDemanda.STATUS).exists()
        )

    def test_concluir_registra_quando(self):
        alterar_status(
            demanda=self.demanda, usuario=self.gestao, novo_status=StatusDemanda.CONCLUIDA
        )
        self.demanda.refresh_from_db()
        self.assertIsNotNone(self.demanda.concluido_em)
        self.assertTrue(
            self.demanda.historico.filter(tipo_evento=TipoEventoDemanda.CONCLUIDA).exists()
        )

    def test_cancelar_registra_quando(self):
        alterar_status(
            demanda=self.demanda, usuario=self.gestao, novo_status=StatusDemanda.CANCELADA
        )
        self.demanda.refresh_from_db()
        self.assertIsNotNone(self.demanda.cancelado_em)
        self.assertTrue(
            self.demanda.historico.filter(tipo_evento=TipoEventoDemanda.CANCELADA).exists()
        )

    def test_voltar_a_andar_limpa_as_datas(self):
        alterar_status(
            demanda=self.demanda, usuario=self.gestao, novo_status=StatusDemanda.CONCLUIDA
        )
        alterar_status(
            demanda=self.demanda, usuario=self.gestao, novo_status=StatusDemanda.ABERTA
        )
        self.demanda.refresh_from_db()
        self.assertIsNone(self.demanda.concluido_em)
        self.assertIsNone(self.demanda.cancelado_em)

    def test_mesma_situacao_nao_gera_evento_nem_data(self):
        alterar_status(
            demanda=self.demanda, usuario=self.gestao, novo_status=StatusDemanda.ABERTA
        )
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.historico.count(), 1)

    def test_situacao_invalida_e_recusada(self):
        with self.assertRaises(ErroDeDemanda):
            alterar_status(
                demanda=self.demanda, usuario=self.gestao, novo_status="inexistente"
            )


class ObservacaoTests(TestCase):
    """Observar registra o que se sabe, sem mexer nos campos (§11)."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        self.demanda = criar_demanda(
            condominio=self.condominio, criado_por=self.gestao, titulo="Demanda"
        )

    def test_observacao_vira_evento_sem_alterar_campos(self):
        antes = (self.demanda.titulo, self.demanda.status, self.demanda.prioridade)
        registrar_observacao(
            demanda=self.demanda, usuario=self.gestao, texto="Orçamento pedido ao fornecedor"
        )
        self.demanda.refresh_from_db()
        self.assertEqual(
            (self.demanda.titulo, self.demanda.status, self.demanda.prioridade), antes
        )
        self.assertTrue(
            self.demanda.historico.filter(
                tipo_evento=TipoEventoDemanda.OBSERVACAO
            ).exists()
        )

    def test_observacao_vazia_e_recusada(self):
        with self.assertRaises(ErroDeDemanda):
            registrar_observacao(demanda=self.demanda, usuario=self.gestao, texto="   ")


class TransacaoTests(TestCase):
    """A demanda e sua memória mudam juntas, ou nada muda (§34)."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")

    def test_criar_grava_demanda_e_historico_juntos(self):
        criar_demanda(
            condominio=self.condominio, criado_por=self.gestao, titulo="Nova"
        )
        self.assertEqual(Demanda.objects.count(), 1)
        self.assertEqual(HistoricoDemanda.objects.count(), 1)

    def test_falha_ao_registrar_desfaz_a_criacao(self):
        # A descrição do histórico tem limite; forçar um valor impossível derruba
        # a transação inteira, e a demanda não pode permanecer sem memória.
        from apps.demandas import services

        original = services._registrar

        def _explode(*args, **kwargs):
            raise RuntimeError("falha proposital")

        services._registrar = _explode
        try:
            with self.assertRaises(RuntimeError):
                services.criar_demanda(
                    condominio=self.condominio, criado_por=self.gestao, titulo="Sem memória"
                )
        finally:
            services._registrar = original

        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(HistoricoDemanda.objects.count(), 0)
