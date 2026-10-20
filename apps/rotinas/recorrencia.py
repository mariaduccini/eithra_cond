"""O cálculo das ocorrências de uma rotina recorrente.

Uma regra de repetição é fácil de dizer e fácil de errar ao calcular: "todo dia
31" não acontece em todo mês, e "todo dia 29 de fevereiro" não acontece em todo
ano. Por isso a conta vive aqui, em uma função só — a mesma resposta vale para
a tela, para o serviço e para o teste, e ninguém precisa repetir a aritmética em
outro lugar.

Este módulo não conhece o modelo. Ele recebe a regra em palavras simples e
devolve a data; é por isso que pode ser conferido sozinho, incluindo os casos de
borda. A montagem da regra a partir de uma rotina é do serviço, onde o modelo
já está em jogo.
"""
import calendar

from datetime import date, timedelta

from .constants import Periodicidade


def ultimo_dia_do_mes(ano: int, mes: int) -> int:
    """Quantos dias tem o mês. Fevereiro responde 28 ou 29, conforme o ano."""
    return calendar.monthrange(ano, mes)[1]


def _no_mes(ano: int, mes: int, dia_desejado: int) -> date:
    """A data do dia pedido naquele mês, ajustada para o último quando não existe.

    É a regra do dia 31: em um mês de 30 dias — ou em fevereiro — a ocorrência
    acontece no último dia do mês, e não deixa de acontecer.
    """
    return date(ano, mes, min(dia_desejado, ultimo_dia_do_mes(ano, mes)))


def _somar_meses(ano: int, mes: int, quantidade: int) -> tuple[int, int]:
    """O ano e o mês que resultam de avançar ``quantidade`` meses."""
    total = ano * 12 + (mes - 1) + quantidade
    return total // 12, total % 12 + 1


def _proxima_em_dias(
    data_inicio: date, intervalo: int, a_partir_de: date | None
) -> date:
    """A cada N dias. Sem referência, a rotina começa na própria data de início."""
    if a_partir_de is None:
        return data_inicio
    return max(a_partir_de + timedelta(days=intervalo), data_inicio)


def _primeira_na_semana(data_inicio: date, dia_semana) -> date:
    """A primeira data, a partir do início, que cai no dia da semana pedido."""
    alvo = data_inicio.weekday() if dia_semana is None else int(dia_semana)
    return data_inicio + timedelta(days=(alvo - data_inicio.weekday()) % 7)


def _proxima_semanal(
    data_inicio: date, intervalo: int, dia_semana, a_partir_de: date | None
) -> date:
    """A cada N semanas, sempre no mesmo dia da semana."""
    primeira = _primeira_na_semana(data_inicio, dia_semana)
    if a_partir_de is None or a_partir_de < primeira:
        return primeira
    semanas = (a_partir_de - primeira).days // 7 + 1
    return primeira + timedelta(weeks=semanas * intervalo)


def _primeira_no_dia_do_mes(data_inicio: date, dia: int) -> date:
    """A primeira data, a partir do início, que cai no dia do mês pedido."""
    candidata = _no_mes(data_inicio.year, data_inicio.month, dia)
    if candidata < data_inicio:
        ano, mes = _somar_meses(data_inicio.year, data_inicio.month, 1)
        candidata = _no_mes(ano, mes, dia)
    return candidata


def _proxima_mensal(
    data_inicio: date, intervalo: int, dia: int, a_partir_de: date | None
) -> date:
    """A cada N meses, sempre no mesmo dia do mês — no último, quando não houver."""
    primeira = _primeira_no_dia_do_mes(data_inicio, dia)
    if a_partir_de is None or a_partir_de < primeira:
        return primeira

    meses = (a_partir_de.year - primeira.year) * 12 + (a_partir_de.month - primeira.month)

    # A ocorrência do próprio mês da referência pode ainda estar por vir — mas só
    # vale se aquele mês estiver alinhado ao intervalo.
    if meses % intervalo == 0:
        candidata = _no_mes(a_partir_de.year, a_partir_de.month, dia)
        if candidata > a_partir_de:
            return candidata

    passos = meses // intervalo + 1
    ano, mes = _somar_meses(primeira.year, primeira.month, passos * intervalo)
    return _no_mes(ano, mes, dia)


def _primeira_no_dia_do_ano(data_inicio: date, dia: int, mes: int) -> date:
    """A primeira data, a partir do início, que cai no dia e mês pedidos."""
    candidata = _no_mes(data_inicio.year, mes, dia)
    if candidata < data_inicio:
        candidata = _no_mes(data_inicio.year + 1, mes, dia)
    return candidata


def _proxima_anual(
    data_inicio: date, intervalo: int, dia: int, mes: int, a_partir_de: date | None
) -> date:
    """A cada N anos, sempre no mesmo dia e mês — 29 de fevereiro inclusive."""
    primeira = _primeira_no_dia_do_ano(data_inicio, dia, mes)
    if a_partir_de is None or a_partir_de < primeira:
        return primeira

    anos = a_partir_de.year - primeira.year

    if anos % intervalo == 0:
        candidata = _no_mes(a_partir_de.year, mes, dia)
        if candidata > a_partir_de:
            return candidata

    passos = anos // intervalo + 1
    return _no_mes(primeira.year + passos * intervalo, mes, dia)


def calcular_proxima_data(
    periodicidade: str,
    *,
    data_inicio: date,
    intervalo: int = 1,
    dia_semana=None,
    dia_mes=None,
    mes_ano=None,
    a_partir_de: date | None = None,
    data_fim: date | None = None,
) -> date | None:
    """A próxima ocorrência depois da referência — ou a primeira, se não houver.

    ``a_partir_de`` é a última ocorrência já conhecida e é sempre exclusiva: a
    data devolvida é estritamente posterior a ela. Sem referência, a conta
    começa na ``data_inicio``.

    Devolve ``None`` quando a rotina não tem mais o que gerar — porque a
    ocorrência cairia depois da ``data_fim``. Nada de execução anterior à data
    de início, e nada depois do fim.
    """
    intervalo = max(int(intervalo or 1), 1)
    dia = data_inicio.day if dia_mes is None else int(dia_mes)
    mes = data_inicio.month if mes_ano is None else int(mes_ano)

    if periodicidade == Periodicidade.SEMANAL:
        candidata = _proxima_semanal(data_inicio, intervalo, dia_semana, a_partir_de)
    elif periodicidade == Periodicidade.MENSAL:
        candidata = _proxima_mensal(data_inicio, intervalo, dia, a_partir_de)
    elif periodicidade == Periodicidade.ANUAL:
        candidata = _proxima_anual(data_inicio, intervalo, dia, mes, a_partir_de)
    else:
        candidata = _proxima_em_dias(data_inicio, intervalo, a_partir_de)

    if data_fim is not None and candidata > data_fim:
        return None
    return candidata
