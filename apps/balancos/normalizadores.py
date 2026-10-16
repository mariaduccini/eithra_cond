"""Normalização determinística do que aparece no balanço.

Aqui não se interpreta nada: converte-se texto em valor, data e descrição de
forma previsível e verificável. Toda regra é local, sem IA, e o texto original
nunca é destruído — quem chama guarda o original ao lado do normalizado.

O dinheiro é sempre ``Decimal``. Nada aqui devolve ``float``: um centavo
perdido em arredondamento binário apareceria depois como divergência de total,
e a conferência da gestão perderia sentido.

Valor só é lido quando há contexto. ``texto_para_decimal`` converte um trecho
que já se sabe ser um valor; ele não sai procurando números pelo documento.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

CENTAVO = Decimal("0.01")

MESES = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

# Um valor em dinheiro do jeito que aparece em documento brasileiro. Usado para
# reconhecer o trecho; a conversão em si é feita por ``texto_para_decimal``.
PADRAO_VALOR = (
    r"\(?\s*-?\s*(?:R\$\s*)?\d{1,3}(?:\.\d{3})*(?:,\d{2})?\s*\)?-?"
)
_RE_VALOR = re.compile(rf"^{PADRAO_VALOR}$")

_RE_DATA = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?$")

# Aceita mais de um separador: "1.234,56" tem milhar e decimal, e a versão
# anterior desta regra recusava justamente o formato brasileiro mais comum.
_RE_APENAS_NUMERO = re.compile(r"^\d+(?:[.,]\d+)*$")
_RE_MILHAR_BR = re.compile(r"^\d{1,3}(?:\.\d{3})+$")

_RE_CNPJ_CPF = re.compile(r"\d{11}|\d{14}")
_RE_DOCUMENTO = re.compile(r"\d{4,}")
_RE_PARCELA = re.compile(r"(\d{1,3})\s*/\s*(\d{1,3})")


def sem_acento(texto: str) -> str:
    """Remove acentos sem mexer no resto do texto."""
    decomposto = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def normalizar_espacos(texto: str) -> str:
    """Colapsa espaços, inclusive o espaço duro que o PDF costuma trazer."""
    return " ".join((texto or "").replace("\xa0", " ").split())


def chave_de_comparacao(texto: str) -> str:
    """Forma estável para comparar descrições.

    Serve para reconhecer o mesmo lançamento escrito de dois jeitos — acento,
    caixa e espaço não devem criar duas despesas diferentes. É o que permitirá,
    mais adiante, dizer "isto apareceu em oito dos últimos nove meses".
    """
    limpo = sem_acento(normalizar_espacos(texto)).lower()
    permitido = [c if (c.isalnum() or c.isspace()) else " " for c in limpo]
    return " ".join("".join(permitido).split())


def decimal_para_texto(valor: Decimal | None) -> str | None:
    """Escreve um ``Decimal`` no contrato, sempre com dois centavos.

    O texto — e não o número de ponto flutuante — é o que vai para o JSON: é o
    único jeito de o valor voltar idêntico do banco.
    """
    if valor is None:
        return None
    return str(valor.quantize(CENTAVO))


def texto_para_decimal(texto: str) -> Decimal | None:
    """Converte um trecho brasileiro de dinheiro em ``Decimal``.

    Aceita ``1.234,56``, ``R$ 1.234,56``, ``-1.234,56`` e ``(1.234,56)`` — os
    parênteses são a forma contábil de dizer negativo. Devolve ``None`` quando o
    trecho não é um valor; quem decide se aquilo era dinheiro é quem chamou.
    """
    if texto is None:
        return None

    bruto = normalizar_espacos(str(texto)).upper().replace("R$", "").replace(" ", "")
    if not bruto:
        return None

    negativo = False
    if bruto.startswith("(") and bruto.endswith(")"):
        negativo = True
        bruto = bruto[1:-1].strip()
    if bruto.startswith("-"):
        negativo = True
        bruto = bruto[1:]
    if bruto.endswith("-"):
        negativo = True
        bruto = bruto[:-1]

    if not bruto or not _RE_APENAS_NUMERO.match(bruto):
        return None

    normalizado = _separar_decimal_e_milhar(bruto)
    if normalizado is None:
        return None

    try:
        valor = Decimal(normalizado)
    except InvalidOperation:
        return None

    return -valor if negativo else valor


def _separar_decimal_e_milhar(bruto: str) -> str | None:
    """Decide o que é separador de milhar e o que é vírgula decimal.

    Com os dois sinais presentes, o último é o decimal. Com apenas um, o
    formato brasileiro manda: pontos em grupos de três são milhar. Um ponto
    solto que não forma grupo de três é tratado como decimal, para não ler
    ``1.5`` como mil e quinhentos.
    """
    tem_virgula = "," in bruto
    tem_ponto = "." in bruto

    if tem_virgula and tem_ponto:
        if bruto.rfind(",") > bruto.rfind("."):
            return bruto.replace(".", "").replace(",", ".")
        return bruto.replace(",", "")

    if tem_virgula:
        return bruto.replace(",", ".")

    if tem_ponto:
        if _RE_MILHAR_BR.match(bruto):
            return bruto.replace(".", "")
        return bruto

    return bruto


def texto_para_data(texto: str, *, ano_padrao: int | None = None) -> date | None:
    """Converte uma data de documento em ``date``.

    Aceita ``10/08/2026``, ``10-08-2026`` e ``10/08``. Sem ano explícito, usa o
    ano informado por quem chamou; sem essa referência, devolve ``None`` em vez
    de escolher um ano por conta própria.
    """
    encontrado = _RE_DATA.match(normalizar_espacos(texto))
    if not encontrado:
        return None

    dia = int(encontrado.group(1))
    mes = int(encontrado.group(2))
    ano_texto = encontrado.group(3)

    if ano_texto:
        ano = int(ano_texto)
        if ano < 100:
            ano += 2000
    elif ano_padrao:
        ano = int(ano_padrao)
    else:
        return None

    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


def texto_para_competencia(texto: str) -> date | None:
    """Converte a competência do documento no primeiro dia do mês.

    ``Agosto/2026`` e ``08/2026`` viram ``2026-08-01``: a competência representa
    o mês, nunca um dia avulso.

    O separador é preservado de propósito — só acento e caixa são removidos.
    Sem ele, ``08/2026`` ficaria igual a ``08 2026`` e a leitura perderia a
    pista de que ali havia um mês. Uma data completa como ``10/08/2026`` não
    conta: o mês precisa aparecer sozinho para valer como competência.
    """
    limpo = sem_acento(normalizar_espacos(texto)).lower()
    if not limpo:
        return None

    por_nome = re.search(r"([a-z]+)\s*(?:/|\s+de\s+|\s+)\s*(\d{4})", limpo)
    if por_nome and por_nome.group(1) in MESES:
        return date(int(por_nome.group(2)), MESES[por_nome.group(1)], 1)

    por_numero = re.search(r"(?<![\d/])(0?[1-9]|1[0-2])\s*/\s*(\d{4})(?![\d/])", limpo)
    if por_numero:
        return date(int(por_numero.group(2)), int(por_numero.group(1)), 1)

    por_inverso = re.search(r"\b(\d{4})\s*-\s*(0?[1-9]|1[0-2])\b", limpo)
    if por_inverso:
        return date(int(por_inverso.group(1)), int(por_inverso.group(2)), 1)

    return None


def texto_para_cnpj_cpf(texto: str) -> str | None:
    """Devolve só os dígitos de um CNPJ ou CPF, quando houver."""
    if not texto:
        return None
    achado = _RE_CNPJ_CPF.search(str(texto))
    return achado.group(0) if achado else None


def texto_para_documento(texto: str) -> str | None:
    """Número de nota fiscal ou documento, só os dígitos."""
    if not texto:
        return None
    achado = _RE_DOCUMENTO.search(str(texto))
    return achado.group(0) if achado else None


def texto_para_parcela(texto: str) -> str | None:
    """Reconhece ``3/6`` como terceira de seis parcelas.

    Só lê o que está marcado como parcela; uma barra solta pode ser data e não
    deve virar parcela por engano.
    """
    if not texto:
        return None
    achado = _RE_PARCELA.search(str(texto))
    if not achado:
        return None
    atual, total = int(achado.group(1)), int(achado.group(2))
    if not 0 < atual <= total:
        return None
    return f"{atual}/{total}"


def parece_valor(texto: str) -> bool:
    """Diz se o trecho tem cara de valor, sem convertê-lo."""
    return bool(_RE_VALOR.match(normalizar_espacos(texto)))
