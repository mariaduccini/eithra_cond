"""Apoio aos testes de entrada do balanço.

Reúne apenas o que os testes repetem: pessoas, vínculos, um PDF genérico, uma
foto sintética e um storage de mentira. Nenhum teste unitário fala com o
Supabase real, e nenhum arquivo de balanço de verdade entra aqui.
"""
import hashlib
import zlib
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.balancos.storage import ErroDeStorage, StorageProvider
from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio

# PDF sintético: só a assinatura e o essencial. Não é um balanço real e não
# precisa ser renderizável — o que se verifica aqui é o recebimento.
CONTEUDO_PDF = b"%PDF-1.4\n% documento de teste\n%%EOF\n"
NOME_ARQUIVO = "balanco-do-mes.pdf"
HASH_PDF = hashlib.sha256(CONTEUDO_PDF).hexdigest()


def pdf(nome=NOME_ARQUIVO, conteudo=CONTEUDO_PDF, content_type="application/pdf"):
    """Monta um arquivo enviado, como o navegador entregaria."""
    return SimpleUploadedFile(nome, conteudo, content_type=content_type)


# -- Fotos de evidência ----------------------------------------------------------
# O domínio confere o começo real do arquivo, e é isso que estas fotos
# exercitam: um PNG de verdade (assinatura, IHDR e IDAT), um JPEG com cabeçalho
# legítimo e um contêiner WEBP. Nenhuma foto real entra nos testes.

NOME_FOTO = "evidencia.png"


def gerar_png(largura=1, altura=1) -> bytes:
    """Um PNG mínimo e válido: assinatura, IHDR, IDAT e IEND com CRC correto."""
    def bloco(tipo: bytes, dados: bytes) -> bytes:
        return (
            len(dados).to_bytes(4, "big")
            + tipo
            + dados
            + zlib.crc32(tipo + dados).to_bytes(4, "big")
        )

    ihdr = bloco(
        b"IHDR",
        largura.to_bytes(4, "big")
        + altura.to_bytes(4, "big")
        + bytes([8, 2, 0, 0, 0]),  # 8 bits por canal, RGB, sem entrelace
    )
    linhas = b"".join(b"\x00" + b"\x7f\xa3\xa8" * largura for _ in range(altura))
    return (
        b"\x89PNG\r\n\x1a\n"
        + ihdr
        + bloco(b"IDAT", zlib.compress(linhas))
        + bloco(b"IEND", b"")
    )


def gerar_webp() -> bytes:
    """Um contêiner WEBP mínimo: RIFF com a marca WEBP no lugar certo."""
    dados = b"\x00" * 12
    corpo = b"WEBP" + b"VP8 " + len(dados).to_bytes(4, "little") + dados
    return b"RIFF" + len(corpo).to_bytes(4, "little") + corpo


# JPEG sintético: SOI seguido do marcador APP0 com o cabeçalho JFIF, que é
# exatamente o que o domínio confere. Não há decodificação de imagem aqui.
CONTEUDO_JPG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    + b"\x00" * 16
    + b"\xff\xd9"
)
CONTEUDO_PNG = gerar_png()
CONTEUDO_WEBP = gerar_webp()


def foto(nome=NOME_FOTO, conteudo=None, content_type="image/png"):
    """Monta uma foto enviada, como o navegador entregaria.

    ``marca`` em :func:`png` e :func:`jpg` muda o conteúdo — e portanto o hash —
    sem mexer no começo do arquivo, que é o que o domínio confere.
    """
    return SimpleUploadedFile(nome, conteudo or CONTEUDO_PNG, content_type=content_type)


def png(nome=NOME_FOTO, marca=""):
    """Uma foto PNG de teste, com o hash que a marca escolher."""
    return foto(nome, CONTEUDO_PNG + marca.encode(), "image/png")


def jpg(nome="evidencia.jpg", marca=""):
    """Uma foto JPG de teste."""
    return foto(nome, CONTEUDO_JPG + marca.encode(), "image/jpeg")


def webp(nome="evidencia.webp", marca=""):
    """Uma foto WEBP de teste."""
    return foto(nome, CONTEUDO_WEBP + marca.encode(), "image/webp")


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


class StorageDeMentira(StorageProvider):
    """Storage em memória: guarda o que foi enviado e o que foi removido.

    Existe para que o domínio seja exercitado sem rede. Pode ser programado
    para falhar no envio, que é como se verifica que uma falha de armazenamento
    não vira sucesso.
    """

    nome = "mentira"

    def __init__(self, bucket="bucket-de-teste", falhar_ao_enviar=False):
        super().__init__(bucket=bucket)
        self.objetos: dict[str, bytes] = {}
        self.removidos: list[str] = []
        self.assinaturas: list[tuple[str, int]] = []
        self.falhar_ao_enviar = falhar_ao_enviar

    def enviar(self, *, key, conteudo, content_type):
        if self.falhar_ao_enviar:
            raise ErroDeStorage("Não foi possível falar com o armazenamento.")
        self.objetos[key] = conteudo

    def remover(self, *, key):
        self.removidos.append(key)
        self.objetos.pop(key, None)

    def url_assinada(self, *, key, validade_segundos):
        self.assinaturas.append((key, validade_segundos))
        return f"https://exemplo.invalido/{key}?validade={validade_segundos}"


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
