"""Material de teste neutro: arquivos de prova e um armazenamento em memória.

Nada aqui pertence a um domínio. O PDF, o PNG, o JPEG e o contêiner WEBP existem
para exercitar o reconhecimento pelo começo real do arquivo, e o armazenamento
em memória deixa o domínio ser testado sem rede. Como esse material é
compartilhado — balanços, demandas, manutenções e o próprio core o usam —, ele
mora na camada neutra: nenhum domínio precisa depender de outro só para testar.

O apoio de cada domínio segue reexportando estes nomes, para que os testes
continuem importando do lugar de sempre. Nenhum arquivo real entra aqui.
"""
import hashlib
import zlib

from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.storage import ErroDeStorage, StorageProvider

# PDF sintético: só a assinatura e o essencial. Não é um documento real e não
# precisa ser renderizável — o que se verifica é o reconhecimento do formato.
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


class StorageDeMentira(StorageProvider):
    """Storage em memória: guarda o que foi enviado e o que foi removido.

    Existe para que o domínio seja exercitado sem rede. Pode ser programado
    para falhar no envio ou na remoção, que é como se verifica que uma falha de
    armazenamento não vira sucesso nem mascara a causa de uma gravação que não
    chegou a acontecer.
    """

    nome = "mentira"

    def __init__(
        self,
        bucket="bucket-de-teste",
        falhar_ao_enviar=False,
        falhar_ao_remover=False,
    ):
        super().__init__(bucket=bucket)
        self.objetos: dict[str, bytes] = {}
        self.removidos: list[str] = []
        self.assinaturas: list[tuple[str, int]] = []
        self.falhar_ao_enviar = falhar_ao_enviar
        self.falhar_ao_remover = falhar_ao_remover

    def enviar(self, *, key, conteudo, content_type):
        if self.falhar_ao_enviar:
            raise ErroDeStorage("Não foi possível falar com o armazenamento.")
        self.objetos[key] = conteudo

    def remover(self, *, key):
        self.removidos.append(key)
        if self.falhar_ao_remover:
            raise ErroDeStorage("Não foi possível falar com o armazenamento.")
        self.objetos.pop(key, None)

    def url_assinada(self, *, key, validade_segundos):
        self.assinaturas.append((key, validade_segundos))
        return f"https://exemplo.invalido/{key}?validade={validade_segundos}"


__all__ = [
    "CONTEUDO_JPG",
    "CONTEUDO_PDF",
    "CONTEUDO_PNG",
    "CONTEUDO_WEBP",
    "HASH_PDF",
    "NOME_ARQUIVO",
    "NOME_FOTO",
    "StorageDeMentira",
    "foto",
    "gerar_png",
    "gerar_webp",
    "jpg",
    "pdf",
    "png",
    "webp",
]
