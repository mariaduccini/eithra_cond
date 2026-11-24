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

from django.conf import settings

# Assinatura de um PDF de verdade. Extensão e tipo informado pelo navegador não
# provam nada: o que vale é o começo do arquivo.
ASSINATURA_PDF = b"%PDF-"

MIME_PDF = "application/pdf"
ASSINATURAS_DE_ARQUIVO = (
    (MIME_PDF, ASSINATURA_PDF, "pdf"),
    ("image/jpeg", b"\xff\xd8\xff", "jpg"),
    ("image/png", b"\x89PNG\r\n\x1a\n", "png"),
)
FORMATOS_DE_ARQUIVO_ACEITOS = "PDF, JPG ou PNG"


class ErroDeArquivo(Exception):
    """O arquivo recebido não pode ser usado."""


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


def sanitizar_nome(nome: str) -> str:
    """Retém apenas o nome legível, sem caminho nem caracteres de controle."""
    original = (nome or "").strip().replace("\\", "/")
    limpo = original.rsplit("/", 1)[-1]
    limpo = "".join(caractere for caractere in limpo if caractere.isprintable())
    return (limpo.strip() or "Documento.pdf")[:255]


def tipo_de_arquivo(conteudo: bytes) -> tuple[str, str] | None:
    """Identifica PDF, JPEG ou PNG pela assinatura real do conteúdo."""
    for mime, assinatura, extensao in ASSINATURAS_DE_ARQUIVO:
        if conteudo.startswith(assinatura):
            return mime, extensao
    return None


def validar_arquivo(arquivo) -> tuple[bytes, str, str, str]:
    """Valida tamanho, assinatura e MIME antes de persistir um arquivo."""
    if arquivo is None:
        raise ErroDeArquivo("Escolha o arquivo que acompanha este documento.")

    tamanho = int(getattr(arquivo, "size", 0) or 0)
    if tamanho <= 0:
        raise ErroDeArquivo("O arquivo está vazio.")

    limite = int(getattr(settings, "BALANCOS_MAX_UPLOAD_BYTES", 0) or 0)
    if limite and tamanho > limite:
        raise ErroDeArquivo(
            f"O arquivo tem {formatar_tamanho(tamanho)} e passa do limite de "
            f"{formatar_tamanho(limite)}."
        )

    conteudo = ler_conteudo(arquivo)
    detectado = tipo_de_arquivo(conteudo)
    if detectado is None:
        raise ErroDeArquivo(
            f"O arquivo não é de um formato aceito. Envie em "
            f"{FORMATOS_DE_ARQUIVO_ACEITOS}."
        )
    mime, extensao = detectado
    informado = (
        (getattr(arquivo, "content_type", "") or "")
        .split(";")[0]
        .strip()
        .lower()
    )
    if informado and informado != mime:
        raise ErroDeArquivo(
            "O arquivo enviado não corresponde a um formato aceito. "
            f"Envie em {FORMATOS_DE_ARQUIVO_ACEITOS}."
        )
    return conteudo, mime, extensao, sanitizar_nome(getattr(arquivo, "name", ""))


def montar_chave_documento(*, condominio, documento, arquivo, extensao: str) -> str:
    """Gera chave de storage só com identificadores, nunca com nome do usuário."""
    return f"documentos/{condominio.pk}/{documento.pk}/{arquivo.pk}.{extensao}"


__all__ = [
    "ASSINATURA_PDF",
    "ASSINATURAS_DE_ARQUIVO",
    "ErroDeArquivo",
    "FORMATOS_DE_ARQUIVO_ACEITOS",
    "MIME_PDF",
    "calcular_hash",
    "formatar_tamanho",
    "ler_conteudo",
    "montar_chave_documento",
    "sanitizar_nome",
    "tipo_de_arquivo",
    "validar_arquivo",
]
