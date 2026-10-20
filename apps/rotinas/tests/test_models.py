"""Modelos da rotina e da execução: coerência, vigência e leitura das listas (§65).

A rotina guarda a regra; a execução guarda o que aconteceu. Nada aqui pertence a
outro condomínio, o equipamento e o local concordam, e a situação de uma
execução combina com as datas que ela registra.
"""
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.core.constants import Prioridade
from apps.rotinas.constants import (
    Periodicidade,
    ResultadoExecucao,
    StatusExecucao,
    TipoRotina,
)
from apps.rotinas.models import ExecucaoRotina, RotinaRecorrente
from apps.rotinas.tests.apoio import (
    criar_condominio,
    criar_equipamento,
    criar_fornecedor,
    criar_local,
    criar_pessoa,
)


class CoerenciaDaRotinaTests(TestCase):
    """Nada de outro condomínio entra numa rotina, e local e equipamento concordam."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.outro = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")

    def _rotina(self, **campos):
        base = {
            "condominio": self.condominio,
            "titulo": "Rotina de teste",
            "data_inicio": date(2026, 1, 10),
            "criado_por": self.gestao,
        }
        base.update(campos)
        return RotinaRecorrente(**base)

    def test_titulo_espacos_sao_colapsados(self):
        rotina = self._rotina(titulo="  Limpeza   da   caixa ")
        rotina.save()
        self.assertEqual(rotina.titulo, "Limpeza da caixa")

    def test_descricao_preserva_paragrafos(self):
        rotina = self._rotina(descricao="Primeira linha.\n\nSegunda linha.")
        rotina.save()
        self.assertEqual(rotina.descricao, "Primeira linha.\n\nSegunda linha.")

    def test_titulo_vazio_e_recusado(self):
        with self.assertRaises(ValidationError):
            self._rotina(titulo="   ").full_clean()

    def test_intervalo_menor_que_um_e_recusado(self):
        with self.assertRaises(ValidationError):
            self._rotina(intervalo=0).full_clean()

    def test_dia_do_mes_fora_da_faixa_e_recusado(self):
        with self.assertRaises(ValidationError):
            self._rotina(dia_mes=40).full_clean()

    def test_mes_do_ano_fora_da_faixa_e_recusado(self):
        with self.assertRaises(ValidationError):
            self._rotina(mes_ano=13).full_clean()

    def test_fim_anterior_ao_inicio_e_recusado(self):
        with self.assertRaises(ValidationError):
            self._rotina(
                data_inicio=date(2026, 3, 1), data_fim=date(2026, 2, 1)
            ).full_clean()

    def test_local_de_outro_condominio_e_recusado(self):
        alheio = criar_local(self.outro, "Garagem do Beta")
        with self.assertRaises(ValidationError):
            self._rotina(local=alheio).full_clean()

    def test_equipamento_de_outro_condominio_e_recusado(self):
        alheio = criar_equipamento(self.outro, "Portão do Beta")
        with self.assertRaises(ValidationError):
            self._rotina(equipamento=alheio).full_clean()

    def test_fornecedor_de_outro_condominio_e_recusado(self):
        alheio = criar_fornecedor(self.outro, "Fornecedor do Beta")
        with self.assertRaises(ValidationError):
            self._rotina(fornecedor=alheio).full_clean()

    def test_local_divergente_do_equipamento_e_recusado(self):
        local = criar_local(self.condominio, "Casa de máquinas")
        outro_local = criar_local(self.condominio, "Telhado")
        equipamento = criar_equipamento(self.condominio, "Bomba", local=local)
        with self.assertRaises(ValidationError):
            self._rotina(equipamento=equipamento, local=outro_local).full_clean()

    def test_local_coincidente_com_o_do_equipamento_e_aceito(self):
        local = criar_local(self.condominio, "Casa de máquinas")
        equipamento = criar_equipamento(self.condominio, "Bomba", local=local)
        self._rotina(equipamento=equipamento, local=local).full_clean()

    def test_padroes(self):
        rotina = self._rotina()
        self.assertEqual(rotina.tipo, TipoRotina.OPERACIONAL)
        self.assertEqual(rotina.periodicidade_tipo, Periodicidade.MENSAL)
        self.assertEqual(rotina.intervalo, 1)
        self.assertEqual(rotina.prioridade, Prioridade.NORMAL)
        self.assertTrue(rotina.ativo)
        self.assertFalse(rotina.gera_manutencao)


class LeituraDaRotinaTests(TestCase):
    """As perguntas que a lista de rotinas faz: vencida, próxima, ordem."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.outro = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        self.hoje = timezone.localdate()

    def _rotina(self, **campos):
        base = {
            "condominio": self.condominio,
            "titulo": "Rotina",
            "data_inicio": self.hoje - timedelta(days=30),
            "criado_por": self.gestao,
        }
        base.update(campos)
        return RotinaRecorrente.objects.create(**base)

    def test_do_condominio_isola(self):
        self._rotina(titulo="Daqui")
        RotinaRecorrente.objects.create(
            condominio=self.outro,
            titulo="De lá",
            data_inicio=self.hoje,
            criado_por=self.gestao,
        )
        self.assertEqual(
            RotinaRecorrente.objects.do_condominio(self.condominio).count(), 1
        )

    def test_ativas_ignora_inativas(self):
        ativa = self._rotina(titulo="Ativa")
        self._rotina(titulo="Inativa", ativo=False)
        self.assertEqual(
            list(RotinaRecorrente.objects.ativas().values_list("pk", flat=True)),
            [ativa.pk],
        )

    def test_vencidas_olha_a_proxima_execucao(self):
        vencida = self._rotina(
            titulo="Vencida", proxima_execucao=self.hoje - timedelta(days=1)
        )
        self._rotina(titulo="Futura", proxima_execucao=self.hoje + timedelta(days=1))
        self.assertEqual(
            list(RotinaRecorrente.objects.vencidas().values_list("pk", flat=True)),
            [vencida.pk],
        )

    def test_inativa_nao_conta_como_vencida(self):
        self._rotina(
            titulo="Vencida mas inativa",
            proxima_execucao=self.hoje - timedelta(days=1),
            ativo=False,
        )
        self.assertFalse(RotinaRecorrente.objects.vencidas().exists())

    def test_proximas_nos_proximos_dias(self):
        proxima = self._rotina(
            titulo="Vence em 3 dias", proxima_execucao=self.hoje + timedelta(days=3)
        )
        self._rotina(titulo="Longe", proxima_execucao=self.hoje + timedelta(days=30))
        self.assertEqual(
            list(RotinaRecorrente.objects.proximas().values_list("pk", flat=True)),
            [proxima.pk],
        )

    def test_por_responsavel(self):
        minha = self._rotina(titulo="Minha", responsavel=self.gestao)
        self._rotina(titulo="De outro")
        self.assertEqual(
            list(
                RotinaRecorrente.objects.por_responsavel(self.gestao).values_list(
                    "pk", flat=True
                )
            ),
            [minha.pk],
        )

    def test_por_local_e_por_equipamento(self):
        local = criar_local(self.condominio, "Casa de máquinas")
        bomba = criar_equipamento(self.condominio, "Bomba", local=local)
        no_local = self._rotina(titulo="No local", local=local, equipamento=bomba)
        self._rotina(titulo="Sem local")

        self.assertEqual(
            list(
                RotinaRecorrente.objects.por_local(local).values_list("pk", flat=True)
            ),
            [no_local.pk],
        )
        self.assertEqual(
            list(
                RotinaRecorrente.objects.por_equipamento(bomba).values_list(
                    "pk", flat=True
                )
            ),
            [no_local.pk],
        )

    def test_buscar_alcanca_titulo_e_descricao(self):
        por_titulo = self._rotina(titulo="Limpeza da caixa d'água")
        por_descricao = self._rotina(
            titulo="Inspeção", descricao="Verificar a bomba de recalque"
        )
        self._rotina(titulo="Outra coisa")

        self.assertEqual(
            list(
                RotinaRecorrente.objects.buscar("caixa").values_list("pk", flat=True)
            ),
            [por_titulo.pk],
        )
        self.assertEqual(
            list(
                RotinaRecorrente.objects.buscar("bomba").values_list("pk", flat=True)
            ),
            [por_descricao.pk],
        )

    def test_ordem_de_atencao_deixa_sem_proxima_no_fim(self):
        com_data = self._rotina(
            titulo="Com data", proxima_execucao=self.hoje + timedelta(days=2)
        )
        self._rotina(titulo="Sem data")
        primeira = RotinaRecorrente.objects.na_ordem_de_atencao().first()
        self.assertEqual(primeira.pk, com_data.pk)


class CoerenciaDaExecucaoTests(TestCase):
    """A situação de uma execução combina com as datas que ela registra."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.outro = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        self.rotina = RotinaRecorrente.objects.create(
            condominio=self.condominio,
            titulo="Rotina",
            data_inicio=timezone.localdate() - timedelta(days=30),
            criado_por=self.gestao,
        )

    def _execucao(self, **campos):
        base = {
            "rotina": self.rotina,
            "condominio": self.condominio,
            "data_prevista": timezone.localdate(),
        }
        base.update(campos)
        return ExecucaoRotina(**base)

    def test_concluida_exige_quando_e_resultado(self):
        with self.assertRaises(ValidationError) as erro:
            self._execucao(status=StatusExecucao.CONCLUIDA).full_clean()
        self.assertIn("concluido_em", erro.exception.message_dict)
        self.assertIn("resultado", erro.exception.message_dict)

    def test_concluida_com_quando_e_resultado_e_aceita(self):
        self._execucao(
            status=StatusExecucao.CONCLUIDA,
            concluido_em=timezone.now(),
            resultado=ResultadoExecucao.OK,
        ).full_clean()

    def test_quando_sem_conclusao_e_recusado(self):
        with self.assertRaises(ValidationError):
            self._execucao(concluido_em=timezone.now()).full_clean()

    def test_em_andamento_exige_inicio(self):
        with self.assertRaises(ValidationError):
            self._execucao(status=StatusExecucao.EM_ANDAMENTO).full_clean()

    def test_rotina_de_outro_condominio_e_recusada(self):
        alheia = RotinaRecorrente.objects.create(
            condominio=self.outro,
            titulo="Do Beta",
            data_inicio=timezone.localdate(),
            criado_por=self.gestao,
        )
        with self.assertRaises(ValidationError):
            self._execucao(rotina=alheia).full_clean()

    def test_mesma_rotina_e_data_nao_repete(self):
        self._execucao().save()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._execucao().save()

    def test_esta_encerrada_por_situacao(self):
        self.assertTrue(
            self._execucao(status=StatusExecucao.IGNORADA).esta_encerrada
        )
        self.assertFalse(
            self._execucao(status=StatusExecucao.PENDENTE).esta_encerrada
        )

    def test_esta_atrasada_e_derivada(self):
        atrasada = self._execucao(
            data_prevista=timezone.localdate() - timedelta(days=1)
        )
        hoje = self._execucao(data_prevista=timezone.localdate())
        self.assertTrue(atrasada.esta_atrasada)
        self.assertFalse(hoje.esta_atrasada)

    def test_encerrada_nao_esta_atrasada(self):
        encerrada = self._execucao(
            status=StatusExecucao.CONCLUIDA,
            data_prevista=timezone.localdate() - timedelta(days=5),
            concluido_em=timezone.now(),
            resultado=ResultadoExecucao.OK,
        )
        self.assertFalse(encerrada.esta_atrasada)


class LeituraDaExecucaoTests(TestCase):
    """As perguntas que a lista de execuções faz: atrasada, hoje, próxima, minha."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.outro = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        self.hoje = timezone.localdate()
        self.rotina = RotinaRecorrente.objects.create(
            condominio=self.condominio,
            titulo="Rotina",
            data_inicio=self.hoje - timedelta(days=30),
            criado_por=self.gestao,
        )
        self.outra_rotina = RotinaRecorrente.objects.create(
            condominio=self.condominio,
            titulo="Outra rotina",
            data_inicio=self.hoje - timedelta(days=30),
            criado_por=self.gestao,
        )

    def _execucao(self, **campos):
        base = {
            "rotina": self.rotina,
            "condominio": self.condominio,
            "data_prevista": self.hoje,
        }
        base.update(campos)
        return ExecucaoRotina.objects.create(**base)

    def test_atrasadas_usa_apenas_a_data_prevista(self):
        atrasada = self._execucao(data_prevista=self.hoje - timedelta(days=2))
        self._execucao(data_prevista=self.hoje)
        self.assertEqual(
            list(ExecucaoRotina.objects.atrasadas().values_list("pk", flat=True)),
            [atrasada.pk],
        )

    def test_encerrada_nao_conta_como_pendente(self):
        self._execucao(
            data_prevista=self.hoje - timedelta(days=2),
            status=StatusExecucao.IGNORADA,
        )
        self.assertEqual(ExecucaoRotina.objects.pendentes().count(), 0)

    def test_de_hoje_e_proximas(self):
        hoje = self._execucao(data_prevista=self.hoje)
        proxima = self._execucao(
            rotina=self.outra_rotina, data_prevista=self.hoje + timedelta(days=3)
        )
        self.assertEqual(
            list(ExecucaoRotina.objects.de_hoje().values_list("pk", flat=True)),
            [hoje.pk],
        )
        self.assertEqual(
            list(ExecucaoRotina.objects.proximas().values_list("pk", flat=True)),
            [proxima.pk],
        )

    def test_minhas_so_do_responsavel(self):
        minha = self._execucao(responsavel=self.gestao)
        self._execucao(rotina=self.outra_rotina)
        self.assertEqual(
            list(
                ExecucaoRotina.objects.minhas(self.gestao).values_list("pk", flat=True)
            ),
            [minha.pk],
        )

    def test_concluidas(self):
        concluida = self._execucao(
            status=StatusExecucao.CONCLUIDA,
            concluido_em=timezone.now(),
            resultado=ResultadoExecucao.OK,
        )
        self._execucao(rotina=self.outra_rotina)
        self.assertEqual(
            list(ExecucaoRotina.objects.concluidas().values_list("pk", flat=True)),
            [concluida.pk],
        )

    def test_por_rotina(self):
        da_rotina = self._execucao()
        self._execucao(rotina=self.outra_rotina)
        self.assertEqual(
            list(
                ExecucaoRotina.objects.por_rotina(self.rotina).values_list(
                    "pk", flat=True
                )
            ),
            [da_rotina.pk],
        )

    def test_ordem_de_atencao_traz_o_que_vence_primeiro(self):
        self._execucao(rotina=self.outra_rotina, data_prevista=self.hoje)
        atrasada = self._execucao(data_prevista=self.hoje - timedelta(days=3))
        primeira = ExecucaoRotina.objects.na_ordem_de_atencao().first()
        self.assertEqual(primeira.pk, atrasada.pk)
