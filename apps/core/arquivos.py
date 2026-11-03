"""Utilitários neutros de arquivo — sem dono de domínio.

Ler o conteúdo, medir um tamanho em linguagem comum, calcular o SHA-256 e
reconhecer a assinatura de um PDF não são regra de nenhum domínio do Eithra:
são o vocabulário mínimo de quem recebe um arquivo. Vivem aqui para que
Balanços, Manutenções, Demandas, Governança e a régua compartilhada de imagem
consumam a mesma implementação, em vez de um domínio depender do outro.

Nada neste módulo conhece model, condomínio, usuário, armazenamento ou regra de
negócio. Ele não guarda nada e não decide nada: recebe bytes ou um arquivo e
devolve o que foi medido.
"""
from __future__ import annotations

import hashlib

# Assinatura de um PDF de verdade. Extensão e tipo informado pelo navegador não
# provam nada: o que vale é o começo do arquivo.
ASSINATURA_PDF = b"%PDF-"

MIME_PDF = "application/pdf"


def formatar_tamanho(bytes_: int) -> str:
    """Escreve um tamanho em linguagem comum, sem expor bytes crus."""
    if bytes_ >= 1024 * 1024:
        return f"{bytes_ / (1024 * 1024):.0f} MB"
    if bytes_ >= 1024:
        return f"{bytes_ / 1024:.0f} KB"
    return f"{bytes_} bytes"


def ler_conteudo(arquivo) -> bytes:
    """Traz o arquivo inteiro para conferir o começo e calcular o resumo.

    O limite de tamanho de cada domínio mantém a leitura dentro de um tamanho
    previsível; não há fluxo contínuo a preservar aqui.
    """
    arquivo.seek(0)
    return arquivo.read()


def calcular_hash(conteudo: bytes) -> str:
    """Hash SHA-256 do arquivo, em hexadecimal minúsculo."""
    return hashlib.sha256(conteudo).hexdigest()


__all__ = [
    "ASSINATURA_PDF",
    "MIME_PDF",
    "calcular_hash",
    "formatar_tamanho",
    "ler_conteudo",
]
