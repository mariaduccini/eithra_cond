"""Casamento de texto por palavra, na forma como a operação fala.

Quem relata um problema ou anuncia uma chegada escreve como fala: sem acento no
celular, com maiúscula no meio da frase, com espaço sobrando. Comparar texto cru
faria a mesma palavra deixar de casar por causa de um acento — e "infiltração"
deixaria de ser reconhecida porque ninguém digita o til.

Este módulo é pequeno de propósito. Ele normaliza e casa palavra inteira; não
sabe o que nenhuma palavra significa. Quem lê o texto é que decide isso.

Nasceu para o núcleo de áudio, que precisa conferir a mesma coisa em domínios
diferentes. ``apps/demandas/derivacao.py`` tem as suas próprias cópias destas
duas funções, escritas antes deste módulo existir; unificá-las é uma limpeza de
baixo risco, deixada para uma rodada própria porque tocaria o domínio de
Demandas.

Também guarda ``normalizar_endereco_email``, a redução pura de um remetente ao
seu endereço canônico. Ela vivia no modelo de cadastro, mas é só texto: quem
compara remetente — o cadastro e a leitura de fonte externa — não deve depender
do módulo de ORM por causa disso.
"""
from __future__ import annotations

import re
from email.utils import parseaddr
from unicodedata import combining, normalize


def normalizar(texto: str | None) -> str:
    """O texto em minúsculas, sem acento e sem espaço sobrando."""
    if not texto:
        return ""
    sem_acento = "".join(
        letra for letra in normalize("NFD", texto) if not combining(letra)
    )
    return " ".join(sem_acento.lower().split())


def normalizar_endereco_email(valor: str | None) -> str:
    """O endereço canônico de um remetente, sem o nome de exibição.

    ``Atlas Elevadores <Financeiro@Atlas.com.br>`` vira
    ``financeiro@atlas.com.br``. Mora aqui, e não no modelo nem no provider,
    porque é redução de texto pura: nem o cadastro (``apps/core/models.py``) nem
    a leitura de fonte externa (``apps/core/gmail.py``) devem depender do módulo
    de ORM só para comparar remetente — duas cópias desta regra divergiriam com
    o tempo.
    """
    _, endereco = parseaddr(" ".join((valor or "").split()))
    endereco = " ".join(endereco.split()).lower()
    return endereco if "@" in endereco else ""


def contem(texto_normalizado: str, palavras) -> bool:
    """Se alguma das palavras aparece inteira no texto.

    Inteira, e não como pedaço: "sala" não pode casar dentro de "salão", e
    "saiu" não pode casar dentro de "saiu" por acidente de prefixo em outra
    palavra. O plural é admitido porque a fala alterna entre os dois sem avisar.
    """
    return any(
        re.search(rf"\b{re.escape(palavra)}s?\b", texto_normalizado)
        for palavra in palavras
    )


def citado(texto_normalizado: str, palavra: str) -> bool:
    """Se uma palavra específica aparece inteira no texto."""
    return contem(texto_normalizado, (palavra,))
