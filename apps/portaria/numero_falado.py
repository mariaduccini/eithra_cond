"""O número que a portaria fala, lido como o condomínio o escreve.

Leitura contextual da busca de destino: só o campo da unidade passa por aqui.
"novecentos e dois" e "nove zero dois" são o mesmo que "902" — mas esta
conversão não decide nada sozinha. Quem decide se o 902 existe é o cadastro: o
número só vira destino quando casa com uma unidade real e ativa do condomínio.
Sem correspondência, não se assume nada.

Nada de voz acontece aqui: a transcrição chega pronta de ``apps/core/audio.py``
e não é tocada. Nomes, empresas e observações também não passam por esta
leitura — só o destino. Por isso o módulo é pequeno e não conhece modelo algum:
ele converte texto em número, e quem confirma o número é o cadastro.
"""
from __future__ import annotations

import re

from apps.core import texto

# Palavras que acompanham o número falado sem fazer parte dele: "apartamento
# novecentos e dois" é o mesmo destino que "novecentos e dois".
_PALAVRAS_IGNORADAS = frozenset(
    {
        "apartamento",
        "apto",
        "ap",
        "unidade",
        "bloco",
        "torre",
        "casa",
        "loja",
        "sala",
    }
)

_UNIDADES = {
    "zero": 0,
    "um": 1,
    "uma": 1,
    "dois": 2,
    "duas": 2,
    "tres": 3,
    "quatro": 4,
    "cinco": 5,
    "seis": 6,
    "sete": 7,
    "oito": 8,
    "nove": 9,
}

_DEZ_A_DEZENOVE = {
    "dez": 10,
    "onze": 11,
    "doze": 12,
    "treze": 13,
    "quatorze": 14,
    "catorze": 14,
    "quinze": 15,
    "dezesseis": 16,
    "dezasseis": 16,
    "dezessete": 17,
    "dezassete": 17,
    "dezoito": 18,
    "dezenove": 19,
    "dezanove": 19,
}

_DEZENAS = {
    "vinte": 20,
    "trinta": 30,
    "quarenta": 40,
    "cinquenta": 50,
    "cincoenta": 50,
    "sessenta": 60,
    "setenta": 70,
    "oitenta": 80,
    "noventa": 90,
}

_CENTENAS = {
    "cem": 100,
    "cento": 100,
    "duzentos": 200,
    "trezentos": 300,
    "quatrocentos": 400,
    "quinhentos": 500,
    "seiscentos": 600,
    "setecentos": 700,
    "oitocentos": 800,
    "novecentos": 900,
}

_DIGITOS = re.compile(r"\d+")
_SO_DIGITOS = re.compile(r"(\d+)[a-z]?")


def em_digitos(valor: str | None) -> str | None:
    """O número falado em dígitos, ou None quando não há número na fala.

    "902", "novecentos e dois" e "nove zero dois" devolvem "902". Texto que não
    é número — um nome, uma empresa, uma observação — devolve None, e a busca
    segue pelo caminho de sempre, sem normalização nenhuma.
    """
    tokens = _tokens(valor)
    if not tokens:
        return None

    if len(tokens) == 1:
        achado = _SO_DIGITOS.fullmatch(tokens[0])
        if achado:
            return _sem_zeros(achado.group(1))

    # Dígito a dígito: "nove zero dois" é o 902, não 9 + 0 + 2.
    if len(tokens) >= 2 and all(token in _UNIDADES for token in tokens):
        return _sem_zeros("".join(str(_UNIDADES[token]) for token in tokens))

    numero = _em_extenso(tokens)
    return None if numero is None else str(numero)


def digitos(identificador: str | None) -> set[str]:
    """Os números que o identificador de uma unidade contém.

    "902" e "Bloco A · 902" chegam aqui como ``{"902"}``: o bloco ou o tipo não
    escondem o apartamento. É sobre estes números que a fala é comparada.
    """
    return {_sem_zeros(achado) for achado in _DIGITOS.findall(identificador or "")}


def sugestao(numero: str, identificadores) -> str:
    """A sugestão para um número que não existe, quando ela é segura.

    Só há sugestão quando o número reconhecido está a um dígito de exatamente
    uma unidade real: havendo mais de uma vizinha, não há como saber qual, e a
    resposta é o silêncio. Nunca inventa apartamento — o vizinho sai do
    cadastro, nunca da fala.
    """
    vizinhos = {
        achado
        for identificador in identificadores
        for achado in digitos(identificador)
        if _a_um_digito(achado, numero)
    }
    if len(vizinhos) != 1:
        return ""
    return f"Não encontrei o apartamento {numero}. Você quis dizer {vizinhos.pop()}?"


def _tokens(valor):
    if not valor:
        return []
    partes = texto.normalizar(valor).split()
    while partes and partes[0] in _PALAVRAS_IGNORADAS:
        partes.pop(0)
    # O "e" liga as partes do número falado ("novecentos e dois") e não conta.
    return [parte for parte in partes if parte != "e"]


def _em_extenso(tokens):
    """Soma uma fala composta como o português a escreve, ou None se não casar."""
    total = 0
    atual = 0
    reconhecido = False
    for token in tokens:
        if token == "mil":
            total += (atual or 1) * 1000
            atual = 0
        elif token in _CENTENAS:
            atual += _CENTENAS[token]
        elif token in _DEZ_A_DEZENOVE:
            atual += _DEZ_A_DEZENOVE[token]
        elif token in _DEZENAS:
            atual += _DEZENAS[token]
        elif token in _UNIDADES:
            atual += _UNIDADES[token]
        else:
            return None
        reconhecido = True
    if not reconhecido:
        return None
    return total + atual


def _sem_zeros(valor: str) -> str:
    return valor.lstrip("0") or "0"


def _a_um_digito(um: str, outro: str) -> bool:
    """Se os dois números diferem em exatamente um dígito, na mesma posição."""
    return len(um) == len(outro) and sum(
        1 for a, b in zip(um, outro) if a != b
    ) == 1
