"""Quando um plano preventivo volta a acontecer.

A regra é curta de propósito: a cada N dias, N meses ou N anos, contados a
partir da data vigente. Não há expressão de recorrência, não há motor de
calendário próprio e não há segunda data guardada no plano.

A aritmética de calendário — a virada de mês, o fevereiro de 28 ou 29, o dia 31
que não existe em todo mês — vive na camada neutra de recorrência, em
``apps.core.recorrencia``, e é de lá que a resposta vem. Repetir a conta aqui
seria criar uma segunda resposta possível para a mesma pergunta; buscá-la em
outro domínio seria fazer a preventiva depender de algo que não é dela.

Este módulo não conhece o modelo: recebe a frequência em palavras simples e
devolve a data. É por isso que pode ser conferido sozinho, incluindo os casos de
borda.
"""
from datetime import date

from apps.core.recorrencia import Periodicidade, calcular_proxima_data

from .constants import FrequenciaUnidade

# A palavra do plano traduzida para a conta que já existe.
_CONTA_DA_UNIDADE = {
    FrequenciaUnidade.DIAS: Periodicidade.INTERVALO_DIAS,
    FrequenciaUnidade.MESES: Periodicidade.MENSAL,
    FrequenciaUnidade.ANOS: Periodicidade.ANUAL,
}


def proxima_data(*, unidade: str, intervalo: int, a_partir_de: date) -> date:
    """A data seguinte do plano, depois de ``a_partir_de``.

    O dia do mês é o da própria data de partida, e um dia que não existe no mês
    de destino cai no último dia disponível: 31/01 mais um mês é 28/02 em ano
    comum e 29/02 em ano bissexto; 29/02 mais um ano é 28/02 do ano seguinte.
    """
    return calcular_proxima_data(
        _CONTA_DA_UNIDADE[unidade],
        data_inicio=a_partir_de,
        intervalo=intervalo,
        a_partir_de=a_partir_de,
    )
