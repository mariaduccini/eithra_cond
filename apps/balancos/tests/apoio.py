"""Apoio aos testes de entrada do balanço.

Reúne apenas o que os testes repetem: pessoas, vínculos, um PDF com camada
textual e o que mais o detector de layout precisa. Nenhum teste unitário fala
com o Supabase real, e nenhum arquivo de balanço de verdade entra aqui.

O PDF genérico, a foto sintética e o storage em memória são material neutro e
vivem na camada compartilhada; são reexportados por aqui para que os testes
continuem importando do lugar de sempre, sem uma segunda cópia do mesmo arquivo.
"""
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio
from apps.core.tests.apoio import (  # noqa: F401  (reexportado aos testes)
    CONTEUDO_JPG,
    CONTEUDO_PDF,
    CONTEUDO_PNG,
    CONTEUDO_WEBP,
    HASH_PDF,
    NOME_ARQUIVO,
    NOME_FOTO,
    StorageDeMentira,
    foto,
    gerar_png,
    gerar_webp,
    jpg,
    pdf,
    png,
    webp,
)


def criar_pessoa(email, nome="Pessoa de Teste"):
    """Cria uma pessoa de teste. Nunca usa dados reais."""
    return get_user_model().objects.create_user(
        email=email, password="senha-forte-123", full_name=nome
    )


def criar_vinculo(usuario, condominio, papel=Papel.SINDICA, ativo=True):
    """Liga uma pessoa a um condomínio, com o papel exercido lá."""
    return AcessoUsuarioCondominio.objects.create(
        usuario=usuario, condominio=condominio, papel=papel, ativo=ativo
    )


class PaginaDeImagem:
    """Marca uma página sem camada textual, com uma imagem no lugar.

    Representa anexos e comprovantes digitalizados: entram no PDF, mas não
    podem influenciar a detecção do layout.
    """


def pdf_textual(*paginas, nome=NOME_ARQUIVO):
    """Monta um PDF sintético com camada textual legível.

    O objetivo é exercitar o detector de layout com marcadores controlados,
    sem depender de arquivo financeiro real nem de biblioteca gráfica externa.
    """
    return SimpleUploadedFile(nome, gerar_pdf_textual(*paginas), content_type="application/pdf")


def gerar_pdf_textual(*paginas) -> bytes:
    """Gera um PDF mínimo com texto pesquisável.

    Cada argumento é uma página. Uma string vira uma página com camada textual;
    uma ``PaginaDeImagem`` vira uma página sem texto, só com uma imagem — é assim
    que os testes representam anexos e comprovantes digitalizados, que não podem
    determinar o layout.
    """
    if not paginas:
        paginas = ("Documento sintético.",)

    # 1 = catálogo, 2 = árvore de páginas; o resto é atribuído na ordem.
    proximo_numero = 3
    objetos: dict[int, bytes] = {}

    def reservar() -> int:
        nonlocal proximo_numero
        numero = proximo_numero
        proximo_numero += 1
        return numero

    numero_fonte = reservar()
    objetos[numero_fonte] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    referencias_paginas: list[str] = []
    for pagina in paginas:
        numero_pagina = reservar()
        numero_conteudo = reservar()
        recursos = f"/Font << /F1 {numero_fonte} 0 R >>"

        if isinstance(pagina, PaginaDeImagem):
            numero_imagem = reservar()
            objetos[numero_imagem] = _objeto_de_imagem()
            recursos += f" /XObject << /Imagem {numero_imagem} 0 R >>"
            stream = b"q Q"
        else:
            stream = _stream_de_texto(pagina)

        objetos[numero_pagina] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << {recursos} >> "
            f"/Contents {numero_conteudo} 0 R >>"
        ).encode("latin-1")
        objetos[numero_conteudo] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
            + stream
            + b"\nendstream"
        )
        referencias_paginas.append(f"{numero_pagina} 0 R")

    objetos[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objetos[2] = (
        "<< /Type /Pages /Kids ["
        + " ".join(referencias_paginas)
        + f"] /Count {len(paginas)} >>"
    ).encode("latin-1")

    numeros = sorted(objetos)
    pdf_bytes = BytesIO()
    pdf_bytes.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    deslocamentos: dict[int, int] = {}

    for numero in numeros:
        deslocamentos[numero] = pdf_bytes.tell()
        pdf_bytes.write(f"{numero} 0 obj\n".encode("latin-1"))
        pdf_bytes.write(objetos[numero])
        pdf_bytes.write(b"\nendobj\n")

    inicio_xref = pdf_bytes.tell()
    pdf_bytes.write(f"xref\n0 {len(numeros) + 1}\n".encode("latin-1"))
    pdf_bytes.write(b"0000000000 65535 f \n")
    for numero in numeros:
        pdf_bytes.write(f"{deslocamentos[numero]:010d} 00000 n \n".encode("latin-1"))
    pdf_bytes.write(
        (
            f"trailer\n<< /Size {len(numeros) + 1} /Root 1 0 R >>\n"
            f"startxref\n{inicio_xref}\n%%EOF"
        ).encode("latin-1")
    )
    return pdf_bytes.getvalue()


def _objeto_de_imagem() -> bytes:
    """Imagem mínima de 1x1, só para o detector contar um XObject de imagem."""
    dados = bytes([0x00, 0x00, 0x00])
    return (
        "<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
        "/ColorSpace /DeviceRGB /BitsPerComponent 8 "
        f"/Length {len(dados)} >>\nstream\n".encode("latin-1")
        + dados
        + b"\nendstream"
    )


def _stream_de_texto(texto: str) -> bytes:
    linhas = [linha.strip() for linha in (texto or "").splitlines() if linha.strip()]
    if not linhas:
        linhas = ["Documento sintético."]

    partes = [b"BT\n/F1 12 Tf\n50 742 Td\n"]
    primeira = True
    for linha in linhas:
        if not primeira:
            partes.append(b"0 -16 Td\n")
        primeira = False
        escapada = (
            linha.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        )
        partes.append(f"({escapada}) Tj\n".encode("latin-1", errors="replace"))
    partes.append(b"ET")
    return b"".join(partes)
