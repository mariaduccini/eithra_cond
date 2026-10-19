"""Modelos da manutenção: coerência, datas e leitura da lista."""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from apps.core.constants import Prioridade
from apps.manutencoes.constants import StatusManutencao, TipoManutencao
from apps.manutencoes.models import Manutencao
from apps.manutencoes.tests.apoio import (
    criar_condominio,
    criar_demanda,
    criar_equipamento,
    criar_fornecedor,
    criar_local,
    criar_pessoa,
)


class CoerenciaTests(TestCase):
    """Nada de outro condomínio entra numa manutenção, e local e equipamento concordam."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.outro = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")

    def _manutencao(self, **campos):
        base = {
            "condominio": self.condominio,
            "titulo": "Serviço de teste",
            "criado_por": self.gestao,
        }
        base.update(campos)
        return Manutencao(**base)

    def test_titulo_espacos_sao_colapsados(self):
        manutencao = self._manutencao(titulo="  Troca   da   bomba ")
        manutencao.save()
        self.assertEqual(manutencao.titulo, "Troca da bomba")

    def test_problema_preserva_paragrafos(self):
        manutencao = self._manutencao(
            descricao_problema="Primeira linha.\n\nSegunda linha."
        )
        manutencao.save()
        self.assertEqual(manutencao.descricao_problema, "Primeira linha.\n\nSegunda linha.")

    def test_titulo_vazio_e_recusado(self):
        with self.assertRaises(ValidationError):
            self._manutencao(titulo="   ").full_clean()

    def test_local_de_outro_condominio_e_recusado(self):
        alheio = criar_local(self.outro, "Garagem do Beta")
        with self.assertRaises(ValidationError):
            self._manutencao(local=alheio).full_clean()

    def test_equipamento_de_outro_condominio_e_recusado(self):
        alheio = criar_equipamento(self.outro, "Portão do Beta")
        with self.assertRaises(ValidationError):
            self._manutencao(equipamento=alheio).full_clean()

    def test_fornecedor_de_outro_condominio_e_recusado(self):
        alheio = criar_fornecedor(self.outro, "Fornecedor do Beta")
        with self.assertRaises(ValidationError):
            self._manutencao(fornecedor=alheio).full_clean()

    def test_demanda_de_origem_de_outro_condominio_e_recusada(self):
        alheia = criar_demanda(self.outro, self.gestao, "Demanda do Beta")
        with self.assertRaises(ValidationError):
            self._manutencao(demanda_origem=alheia).full_clean()

    def test_local_divergente_do_equipamento_e_recusado(self):
        local = criar_local(self.condominio, "Casa de máquinas")
        outro_local = criar_local(self.condominio, "Telhado")
        equipamento = criar_equipamento(self.condominio, "Bomba", local=local)
        with self.assertRaises(ValidationError):
            self._manutencao(equipamento=equipamento, local=outro_local).full_clean()

    def test_local_coincidente_com_o_do_equipamento_e_aceito(self):
        local = criar_local(self.condominio, "Casa de máquinas")
        equipamento = criar_equipamento(self.condominio, "Bomba", local=local)
        self._manutencao(equipamento=equipamento, local=local).full_clean()

    def test_concluida_exige_data_e_servico(self):
        manutencao = self._manutencao(status=StatusManutencao.CONCLUIDA)
        with self.assertRaises(ValidationError) as erro:
            manutencao.full_clean()
        self.assertIn("data_conclusao", erro.exception.message_dict)
        self.assertIn("descricao_servico", erro.exception.message_dict)

    def test_data_de_conclusao_sem_conclusao_e_recusada(self):
        manutencao = self._manutencao(data_conclusao=timezone.now())
        with self.assertRaises(ValidationError):
            manutencao.full_clean()

    def test_custo_negativo_e_recusado(self):
        with self.assertRaises(ValidationError):
            self._manutencao(custo_previsto=-1).full_clean()
        with self.assertRaises(ValidationError):
            self._manutencao(custo_real=-1).full_clean()

    def test_padroes(self):
        manutencao = self._manutencao()
        self.assertEqual(manutencao.tipo, TipoManutencao.CORRETIVA)
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)
        self.assertEqual(manutencao.prioridade, Prioridade.NORMAL)
        self.assertTrue(manutencao.ativo)
        self.assertFalse(manutencao.esta_encerrada)

    def test_encerrada_em_concluida_e_cancelada(self):
        self.assertTrue(
            self._manutencao(status=StatusManutencao.CONCLUIDA).esta_encerrada
        )
        self.assertTrue(
            self._manutencao(status=StatusManutencao.CANCELADA).esta_encerrada
        )


class LeituraDaListaTests(TestCase):
    """As perguntas que a lista faz: atraso, hoje, próximas, minhas, ordem."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.outro = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        self.hoje = timezone.localdate()

    def _manutencao(self, **campos):
        base = {
            "condominio": self.condominio,
            "titulo": "Serviço",
            "criado_por": self.gestao,
        }
        base.update(campos)
        return Manutencao.objects.create(**base)

    def test_atrasadas_usa_apenas_data_prevista(self):
        atrasada = self._manutencao(
            titulo="Atrasada", data_prevista=self.hoje - timedelta(days=2)
        )
        self._manutencao(titulo="Hoje", data_prevista=self.hoje)
        self.assertEqual(
            list(Manutencao.objects.atrasadas().values_list("pk", flat=True)),
            [atrasada.pk],
        )

    def test_encerrada_nao_conta_como_atrasada(self):
        self._manutencao(
            titulo="Concluída",
            data_prevista=self.hoje - timedelta(days=2),
            status=StatusManutencao.CONCLUIDA,
            data_conclusao=timezone.now(),
            descricao_servico="Feito.",
        )
        self.assertFalse(Manutencao.objects.atrasadas().exists())

    def test_previstas_para_hoje_e_proximas(self):
        hoje = self._manutencao(titulo="Hoje", data_prevista=self.hoje)
        proxima = self._manutencao(
            titulo="Próxima", data_prevista=self.hoje + timedelta(days=3)
        )
        self.assertEqual(
            list(Manutencao.objects.previstas_para_hoje().values_list("pk", flat=True)),
            [hoje.pk],
        )
        self.assertEqual(
            list(Manutencao.objects.proximas().values_list("pk", flat=True)),
            [proxima.pk],
        )

    def test_sem_previsao(self):
        sem = self._manutencao(titulo="Sem previsão")
        self._manutencao(titulo="Com previsão", data_prevista=self.hoje)
        self.assertEqual(
            list(Manutencao.objects.sem_previsao().values_list("pk", flat=True)),
            [sem.pk],
        )

    def test_minhas_so_do_responsavel(self):
        eu = self._manutencao(titulo="Minha", responsavel=self.gestao)
        self._manutencao(titulo="De outro")
        self.assertEqual(
            list(Manutencao.objects.minhas(self.gestao).values_list("pk", flat=True)),
            [eu.pk],
        )

    def test_do_condominio_isola(self):
        self._manutencao(titulo="Daqui")
        Manutencao.objects.create(
            condominio=self.outro, titulo="De lá", criado_por=self.gestao
        )
        self.assertEqual(Manutencao.objects.do_condominio(self.condominio).count(), 1)

    def test_ordem_de_atencao_traz_urgente_primeiro(self):
        self._manutencao(titulo="Normal", data_prevista=self.hoje - timedelta(days=1))
        urgente = self._manutencao(
            titulo="Urgente", prioridade=Prioridade.URGENTE
        )
        primeira = Manutencao.objects.na_ordem_de_atencao().first()
        self.assertEqual(primeira.pk, urgente.pk)
