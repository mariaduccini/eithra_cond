"""As cores da identidade de um condomínio.

A cor principal é uma escolha da gestão; a secundária é derivada dela — um tom
mais claro do mesmo matiz —, para que a identidade não dependa de duas decisões
que podem divergir. Só o formato ``#RRGGBB`` é aceito: é o formato que a folha
de estilo, a prévia e o desenho do card entendem sem conversão.

Este módulo é a única oficina dessas cores. O cadastro do condomínio as valida
aqui, e quem desenha (a prévia na tela e o card em PNG) lê a cor já resolvida.
Nada aqui toca o banco, o armazenamento ou a tela.
"""
from __future__ import annotations

import re

# O formato aceito, e só ele: ``#`` e seis dígitos hexadecimais.
COR_HEXADECIMAL = re.compile(r"^#[0-9A-Fa-f]{6}$")

# A mistura usada para derivar a cor secundária: um tom mais claro do mesmo
# matiz, na mesma linguagem em que os modelos de comunicado já destacam o tom
# da faixa.
MISTURA_DO_TOM_CLARO = 0.35


def cor_valida(valor: str | None) -> bool:
    """Diz se o valor é uma cor no formato aceito."""
    return bool(COR_HEXADECIMAL.match((valor or "").strip()))


def cor_normalizada(valor: str | None) -> str:
    """A cor como fica guardada: ``#RRGGBB`` em maiúsculas — ou nada.

    Aceita a cor digitada sem o ``#`` e sobe as letras, para que o mesmo tom não
    apareça escrito de duas formas. Um valor fora do formato volta como veio,
    sem silêncio: quem recusa é o cadastro, e a recusa precisa citar o que foi
    dito.
    """
    texto = (valor or "").strip()
    if not texto:
        return ""
    if not texto.startswith("#"):
        texto = f"#{texto}"
    texto = texto.upper()
    return texto if cor_valida(texto) else (valor or "").strip()


def tom_claro(cor: str | None, mistura: float = MISTURA_DO_TOM_CLARO) -> str:
    """Um tom mais claro do mesmo matiz, no formato aceito — ou nada.

    A conta mistura cada canal com o branco, preservando a proporção entre eles:
    o tom continua sendo o da cor de origem, só que mais claro.
    """
    base = cor_normalizada(cor)
    if not cor_valida(base):
        return ""
    canais = [int(base[i : i + 2], 16) for i in (1, 3, 5)]
    claros = [round(canal + (255 - canal) * mistura) for canal in canais]
    return "#" + "".join(f"{canal:02X}" for canal in claros)


def cor_secundaria(cor_principal: str | None) -> str:
    """A cor secundária de um condomínio, derivada da principal — ou nada."""
    if not cor_valida(cor_principal):
        return ""
    return tom_claro(cor_principal)
