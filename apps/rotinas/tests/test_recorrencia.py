"""Cálculo da próxima ocorrência de uma rotina (§64).

A conta é conferida sozinha, sem modelo: é a mesma função que a tela, o serviço
e o teste usam. Cada caso aqui é um jeito de errar que a repetição esconde — o
dia 31 em um mês curto, o 29 de fevereiro em ano comum, o fim da vigência.

A função vem da camada neutra de recorrência, e não de um módulo de Rotinas: a
aritmética é a mesma para qualquer regra que se repita.
"""
from datetime import date

from django.test import TestCase

from apps.core.recorrencia import calcular_proxima_data
from apps.rotinas.constants import DiaSemana, Periodicidade


class DiariaTests(TestCase):
    """Todo dia: a primeira ocorrência é o início; a seguinte, o dia depois."""

    def test_sem_referencia_a_primeira_e_a_data_de_inicio(self):
        self.assertEqual(
            calcular_proxima_data(Periodicidade.DIARIA, data_inicio=date(2026, 1, 1)),
            date(2026, 1, 1),
        )

    def test_a_seguinte_e_o_dia_seguinte(self):
        self.assertEqual(
            calcular_proxima_data(
                Periodicidade.DIARIA,
                data_inicio=date(2026, 1, 1),
                a_partir_de=date(2026, 1, 1),
            ),
            date(2026, 1, 2),
        )


class SemanalTests(TestCase):
    """Semanal: sempre no dia da semana escolhido, de uma ou mais semanas."""

    def test_cai_no_dia_da_semana_escolhido(self):
        # 01/01/2026 é uma quinta; a próxima segunda é 05/01/2026.
        self.assertEqual(
            calcular_proxima_data(
                Periodicidade.SEMANAL,
                data_inicio=date(2026, 1, 1),
                dia_semana=DiaSemana.SEGUNDA,
            ),
            date(2026, 1, 5),
        )

    def test_a_cada_duas_semanas(self):
        self.assertEqual(
            calcular_proxima_data(
                Periodicidade.SEMANAL,
                data_inicio=date(2026, 1, 1),
                intervalo=2,
                dia_semana=DiaSemana.SEGUNDA,
                a_partir_de=date(2026, 1, 5),
            ),
            date(2026, 1, 19),
        )


class MensalTests(TestCase):
    """Mensal: sempre no mesmo dia do mês — no último, quando ele não existir."""

    def test_no_dia_escolhido(self):
        self.assertEqual(
            calcular_proxima_data(
                Periodicidade.MENSAL, data_inicio=date(2026, 1, 10), dia_mes=10
            ),
            date(2026, 1, 10),
        )

    def test_dia_31_em_fevereiro_vira_o_ultimo_dia(self):
        self.assertEqual(
            calcular_proxima_data(
                Periodicidade.MENSAL,
                data_inicio=date(2026, 1, 31),
                dia_mes=31,
                a_partir_de=date(2026, 1, 31),
            ),
            date(2026, 2, 28),
        )

    def test_dia_31_em_abril_vira_o_dia_30(self):
        self.assertEqual(
            calcular_proxima_data(
                Periodicidade.MENSAL,
                data_inicio=date(2026, 3, 31),
                dia_mes=31,
                a_partir_de=date(2026, 3, 31),
            ),
            date(2026, 4, 30),
        )


class AnoBissextoTests(TestCase):
    """Fevereiro respeita o ano: 29 dias quando bissexto, 28 quando comum."""

    def test_dia_31_em_fevereiro_bissexto_vira_o_dia_29(self):
        # 2028 é bissexto.
        self.assertEqual(
            calcular_proxima_data(
                Periodicidade.MENSAL,
                data_inicio=date(2028, 1, 31),
                dia_mes=31,
                a_partir_de=date(2028, 1, 31),
            ),
            date(2028, 2, 29),
        )

    def test_anual_de_29_de_fevereiro_em_ano_comum(self):
        # 2029 não é bissexto: a ocorrência cai no dia 28.
        self.assertEqual(
            calcular_proxima_data(
                Periodicidade.ANUAL,
                data_inicio=date(2028, 2, 29),
                dia_mes=29,
                mes_ano=2,
                a_partir_de=date(2028, 2, 29),
            ),
            date(2029, 2, 28),
        )


class AnualTests(TestCase):
    """Anual: sempre no mesmo dia e mês, adiante no intervalo escolhido."""

    def test_a_primeira_ocorrencia_no_mes_e_dia(self):
        self.assertEqual(
            calcular_proxima_data(
                Periodicidade.ANUAL,
                data_inicio=date(2026, 5, 1),
                dia_mes=15,
                mes_ano=6,
            ),
            date(2026, 6, 15),
        )

    def test_a_seguinte_avanca_um_ano(self):
        self.assertEqual(
            calcular_proxima_data(
                Periodicidade.ANUAL,
                data_inicio=date(2026, 6, 15),
                dia_mes=15,
                mes_ano=6,
                a_partir_de=date(2026, 6, 15),
            ),
            date(2027, 6, 15),
        )


class IntervaloDeDiasTests(TestCase):
    """A cada N dias: a mesma conta da diária, com outro passo."""

    def test_avanca_o_intervalo_informado(self):
        self.assertEqual(
            calcular_proxima_data(
                Periodicidade.INTERVALO_DIAS,
                data_inicio=date(2026, 1, 1),
                intervalo=10,
                a_partir_de=date(2026, 1, 1),
            ),
            date(2026, 1, 11),
        )


class DataFimTests(TestCase):
    """O fim da vigência: nada nasce depois dele."""

    def test_devolve_nada_quando_a_ocorrencia_passaria_do_fim(self):
        self.assertIsNone(
            calcular_proxima_data(
                Periodicidade.DIARIA,
                data_inicio=date(2026, 1, 1),
                a_partir_de=date(2026, 1, 5),
                data_fim=date(2026, 1, 5),
            )
        )

    def test_ainda_gera_quando_a_ocorrencia_e_o_proprio_fim(self):
        self.assertEqual(
            calcular_proxima_data(
                Periodicidade.DIARIA,
                data_inicio=date(2026, 1, 1),
                a_partir_de=date(2026, 1, 4),
                data_fim=date(2026, 1, 5),
            ),
            date(2026, 1, 5),
        )
