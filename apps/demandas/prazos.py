"""A política de prazo das demandas: o que a configuração manda.

Esta é a única autoridade sobre a data prevista de uma demanda. O relato não
cria prazo — quem cria é a política do sistema, lida da configuração, e só
quando existe regra aplicável. Sem regra, não há data: o silêncio é a resposta
honesta, e o registro continua acontecendo sem prazo.

A regra é determinística de propósito. A leitura do relato diz o peso da
demanda — e só isso; ela não opina sobre data. A política traduz esse peso em
dias, e a data é a do registro somada ao prazo configurado. Nenhum serviço de
linguagem define SLA aqui: ele entende o relato, a regra define a data.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.conf import settings
from django.utils import timezone

from apps.core.constants import Prioridade

# A configuração diz quantos dias cada prioridade concede a partir do dia do
# registro. Vazio é o padrão — e vazio significa "sem prazo automático": nada é
# inventado para parecer completo.
CHAVE_DA_CONFIGURACAO = "EITHRA_DEMANDAS_PRAZO_DIAS"


def politica() -> dict[str, int]:
    """A política configurada, por prioridade, com os dias que cada uma concede."""
    configurado = getattr(settings, CHAVE_DA_CONFIGURACAO, None) or {}
    return {
        str(prioridade): int(dias)
        for prioridade, dias in dict(configurado).items()
        if _dias_validos(dias)
    }


def _dias_validos(dias) -> bool:
    """Um prazo só vale se for um número de dias positivo."""
    try:
        return int(dias) > 0
    except (TypeError, ValueError):
        return False


def dias_do_prazo(prioridade) -> int | None:
    """Quantos dias a política concede a esta prioridade — ou nada, sem regra.

    É a mesma autoridade que calcula a data, exposta para que a tela possa
    dizer o prazo por extenso ("15 dias · 12/11/2026") sem recalcular nada por
    conta própria e sem repetir o número da configuração em outro lugar.
    """
    return politica().get(str(prioridade))


def prazo_do_registro(prioridade, *, base: date | None = None) -> date | None:
    """A data prevista, ou nada quando não há regra aplicável.

    ``base`` é o dia do registro; sem ele, hoje. É sempre a data do registro
    somada ao prazo configurado — nunca uma data escolhida pela leitura do
    relato.
    """
    dias = politica().get(str(prioridade))
    if not dias:
        return None
    return (base or timezone.localdate()) + timedelta(days=dias)


def motivo_do_prazo(prioridade) -> str:
    """A frase humana que explica o prazo derivado, ou nada quando não há regra."""
    dias = politica().get(str(prioridade))
    if not dias:
        return ""
    return f"Prazo derivado da política: {Prioridade(prioridade).label} em {dias} dias."
