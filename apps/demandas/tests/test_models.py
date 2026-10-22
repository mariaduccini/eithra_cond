"""Modelo e leituras reutilizáveis da demanda (§10, §11, §13, §26, §27)."""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.demandas.constants import (
    OrigemDemanda,
    PrioridadeDemanda,
    StatusDemanda,
    TipoEventoDemanda,
)
from apps.demandas.models import Demanda, HistoricoDemanda
from apps.demandas.tests.apoio import criar_condominio, criar_local, criar_pessoa


class DemandaModeloTests(TestCase):
    """O que a demanda é por padrão e o que as datas precisam concordar."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.pessoa = criar_pessoa("gestao@exemplo.test", "Gestão")

    def _criar(self, **campos):
        base = {
            "condominio": self.condominio,
            "titulo": "Vazamento na garagem",
            "criado_por": self.pessoa,
        }
        base.update(campos)
        return Demanda.objects.create(**base)

    def test_status_default_aberta(self):
        demanda = self._criar()
        self.assertEqual(demanda.status, StatusDemanda.ABERTA)

    def test_prioridade_default_normal(self):
        demanda = self._criar()
        self.assertEqual(demanda.prioridade, PrioridadeDemanda.NORMAL)

    def test_origem_default_manual(self):
        demanda = self._criar()
        self.assertEqual(demanda.origem, OrigemDemanda.MANUAL)

    def test_demanda_nasce_aberta_e_nao_encerrada(self):
        demanda = self._criar()
        self.assertFalse(demanda.esta_encerrada)

    def test_concluida_esta_encerrada(self):
        demanda = self._criar(status=StatusDemanda.CONCLUIDA, concluido_em=timezone.now())
        self.assertTrue(demanda.esta_encerrada)

    def test_constraint_recusa_concluida_sem_data(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._criar(status=StatusDemanda.CONCLUIDA, concluido_em=None)

    def test_constraint_recusa_data_de_conclusao_sem_status(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._criar(status=StatusDemanda.ABERTA, concluido_em=timezone.now())

    def test_clean_aponta_concluida_sem_data(self):
        demanda = Demanda(
            condominio=self.condominio,
            titulo="Sem título coerente",
            criado_por=self.pessoa,
            status=StatusDemanda.CONCLUIDA,
        )
        with self.assertRaises(ValidationError):
            demanda.clean()

    def test_str_e_o_titulo(self):
        demanda = self._criar(titulo="Troca de lâmpada")
        self.assertEqual(str(demanda), "Troca de lâmpada")

    def test_local_e_opcional(self):
        demanda = self._criar()
        self.assertIsNone(demanda.local_id)

    def test_local_para_exibir_usa_o_cadastro_quando_existe(self):
        local = criar_local(self.condominio, "Garagem")
        demanda = self._criar(local=local, local_contexto="texto antigo")
        self.assertEqual(demanda.local_para_exibir, "Garagem")

    def test_local_para_exibir_cai_no_texto_legado(self):
        demanda = self._criar(local_contexto="piscina")
        self.assertEqual(demanda.local_para_exibir, "piscina")

    def test_clean_recusa_local_de_outro_condominio(self):
        outro = criar_condominio("Beta")
        local_alheio = criar_local(outro, "Garagem do Beta")
        demanda = Demanda(
            condominio=self.condominio,
            titulo="Vazamento",
            criado_por=self.pessoa,
            local=local_alheio,
        )
        with self.assertRaises(ValidationError):
            demanda.clean()


class DemandaQuerySetTests(TestCase):
    """As perguntas que a tela e o painel Hoje fazem (§26, §27)."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.pessoa = criar_pessoa("gestao@exemplo.test", "Gestão")
        self.hoje = timezone.localdate()

    def _criar(self, *, titulo, prazo=None, status=StatusDemanda.ABERTA, **campos):
        return Demanda.objects.create(
            condominio=self.condominio,
            titulo=titulo,
            criado_por=self.pessoa,
            prazo=prazo,
            status=status,
            concluido_em=timezone.now() if status == StatusDemanda.CONCLUIDA else None,
            **campos,
        )

    def test_vencidas_traz_prazo_passado_em_aberto(self):
        self._criar(titulo="Vencida", prazo=self.hoje - timedelta(days=1))
        self._criar(titulo="Futura", prazo=self.hoje + timedelta(days=1))

        vencidas = Demanda.objects.vencidas()
        self.assertEqual([d.titulo for d in vencidas], ["Vencida"])

    def test_vencidas_ignora_encerradas(self):
        self._criar(
            titulo="Concluída atrasada",
            prazo=self.hoje - timedelta(days=2),
            status=StatusDemanda.CONCLUIDA,
        )
        self.assertEqual(Demanda.objects.vencidas().count(), 0)

    def test_vencendo_hoje(self):
        self._criar(titulo="Hoje", prazo=self.hoje)
        self._criar(titulo="Amanhã", prazo=self.hoje + timedelta(days=1))

        self.assertEqual([d.titulo for d in Demanda.objects.vencendo_hoje()], ["Hoje"])

    def test_proximas_traz_os_proximos_dias(self):
        self._criar(titulo="Em 3 dias", prazo=self.hoje + timedelta(days=3))
        self._criar(titulo="Em 30 dias", prazo=self.hoje + timedelta(days=30))

        self.assertEqual([d.titulo for d in Demanda.objects.proximas()], ["Em 3 dias"])

    def test_sem_prazo(self):
        self._criar(titulo="Sem prazo")
        self._criar(titulo="Com prazo", prazo=self.hoje)

        self.assertEqual([d.titulo for d in Demanda.objects.sem_prazo()], ["Sem prazo"])

    def test_urgentes(self):
        self._criar(titulo="Urgente", prioridade=PrioridadeDemanda.URGENTE)
        self._criar(titulo="Normal")

        self.assertEqual([d.titulo for d in Demanda.objects.urgentes()], ["Urgente"])

    def test_minhas_traz_apenas_o_responsavel(self):
        eu = criar_pessoa("eu@exemplo.test", "Eu")
        outra = criar_pessoa("outra@exemplo.test", "Outra")
        self._criar(titulo="Minha", responsavel=eu)
        self._criar(titulo="Da outra", responsavel=outra)

        self.assertEqual([d.titulo for d in Demanda.objects.minhas(eu)], ["Minha"])

    def test_minhas_ignora_encerradas(self):
        eu = criar_pessoa("eu@exemplo.test", "Eu")
        self._criar(
            titulo="Minha concluída",
            responsavel=eu,
            status=StatusDemanda.CONCLUIDA,
        )
        self.assertEqual(Demanda.objects.minhas(eu).count(), 0)

    def test_ordem_de_atencao_urgente_antes_de_vencida(self):
        urgente = self._criar(
            titulo="Urgente",
            prioridade=PrioridadeDemanda.URGENTE,
            prazo=self.hoje + timedelta(days=5),
        )
        vencida = self._criar(titulo="Vencida", prazo=self.hoje - timedelta(days=1))
        normal = self._criar(titulo="Normal", prazo=self.hoje + timedelta(days=2))

        ordem = list(Demanda.objects.na_ordem_de_atencao())
        self.assertEqual(ordem, [urgente, vencida, normal])


class HistoricoDemandaTests(TestCase):
    """O histórico é a memória da demanda e só cresce (§11)."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.pessoa = criar_pessoa("gestao@exemplo.test", "Gestão")
        self.demanda = Demanda.objects.create(
            condominio=self.condominio,
            titulo="Demanda",
            criado_por=self.pessoa,
        )

    def test_evento_guarda_quem_e_quando(self):
        evento = HistoricoDemanda.objects.create(
            demanda=self.demanda,
            usuario=self.pessoa,
            tipo_evento=TipoEventoDemanda.CRIADA,
            descricao="Demanda criada",
        )
        self.assertEqual(evento.demanda, self.demanda)
        self.assertEqual(evento.usuario, self.pessoa)
        self.assertIsNotNone(evento.data_hora)

    def test_historico_ordenado_do_mais_recente(self):
        primeiro = HistoricoDemanda.objects.create(
            demanda=self.demanda,
            usuario=self.pessoa,
            tipo_evento=TipoEventoDemanda.CRIADA,
            descricao="Primeiro",
        )
        segundo = HistoricoDemanda.objects.create(
            demanda=self.demanda,
            usuario=self.pessoa,
            tipo_evento=TipoEventoDemanda.STATUS,
            descricao="Segundo",
        )
        self.assertEqual(list(self.demanda.historico.all()), [segundo, primeiro])
