"""Compressão de PDF grande antes de guardar.

Um balancete escaneado passa fácil de 20 MB: a digitalização de muitas páginas
em imagem gera arquivo que ninguém consegue abrir no celular e que ocupa o
bucket sem necessidade. Antes de guardar, o que passa do limite é reescrito
pelo Ghostscript em qualidade de leitura — ``/ebook`` —, que reduz muito o
tamanho sem apagar o que a leitura precisa ver.

Nada aqui é obrigatório para o fluxo: se o Ghostscript não estiver instalado ou
falhar, o PDF original segue intacto. Comprimir é uma conveniência, e uma
conveniência não pode impedir o recebimento de um documento legítimo.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# Acima disto o PDF é comprimido. Abaixo, reescrever o arquivo só gastaria tempo
# e poderia até piorar a qualidade sem ganho real de tamanho.
LIMITE_DE_COMPRESSAO = 20 * 1024 * 1024

# Como o Ghostscript costuma se chamar no PATH, do mais provável ao menos.
NOMES_DO_GHOSTSCRIPT = ("gswin64c", "gswin32c", "gs")

# Teto de tempo para a compressão. Um documento grande leva alguns segundos; se
# passar disso, algo saiu errado e o original é a resposta segura.
TEMPO_LIMITE_SEGUNDOS = 180


def _executavel() -> str | None:
    """Encontra o Ghostscript, no PATH ou na instalação padrão do Windows."""
    for nome in NOMES_DO_GHOSTSCRIPT:
        caminho = shutil.which(nome)
        if caminho:
            return caminho

    base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "gs"
    if base.is_dir():
        candidatos = sorted(base.glob("gs*/bin/gswin64c.exe"), reverse=True)
        if candidatos:
            return str(candidatos[0])
    return None


def ghostscript_disponivel() -> bool:
    """Diz se há com que comprimir. Não é erro não haver."""
    return _executavel() is not None


def comprimir_pdf(
    conteudo: bytes, *, limite: int = LIMITE_DE_COMPRESSAO
) -> bytes:
    """Devolve o PDF comprimido quando vale a pena; senão, o próprio original.

    A compressão só devolve resultado quando ele é de fato menor: se o
    Ghostscript não existe, falha ou produz um arquivo maior, o original volta
    sem alteração. Nunca lança — o recebimento do documento não depende disto.
    """
    if len(conteudo) <= limite:
        return conteudo

    executavel = _executavel()
    if not executavel:
        return conteudo

    with tempfile.TemporaryDirectory(prefix="eithra-pdf-") as pasta:
        origem = Path(pasta) / "origem.pdf"
        destino = Path(pasta) / "comprimido.pdf"
        origem.write_bytes(conteudo)

        comando = [
            executavel,
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            "-dPDFSETTINGS=/ebook",
            "-dNOPAUSE",
            "-dQUIET",
            "-dBATCH",
            f"-sOutputFile={destino}",
            str(origem),
        ]
        try:
            resultado = subprocess.run(
                comando,
                capture_output=True,
                timeout=TEMPO_LIMITE_SEGUNDOS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return conteudo

        if resultado.returncode != 0 or not destino.is_file():
            return conteudo

        comprimido = destino.read_bytes()

    if not comprimido or len(comprimido) >= len(conteudo):
        return conteudo
    return comprimido
