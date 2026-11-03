"""Leitura e conferência de imagens pelo conteúdo real.

Uma imagem escolhida na tela chega com um nome e com um tipo que o navegador
informou — e nenhum dos dois é fonte de confiança. Quem diz o que o arquivo é:
o começo dele.

Este módulo nasceu junto com a foto de quem usa o Eithra e passou a ser
compartilhado quando o condomínio ganhou logo própria. Os dois casos aceitam o
mesmo conjunto de imagens e são medidos pela mesma régua; o que muda entre eles
é só o assunto da frase, e por isso ele é um parâmetro, e não uma segunda
cópia da regra.

Aqui não se guarda nada: a imagem aprovada volta para quem chamou, que decide
onde ela vive.
"""
from __future__ import annotations

from apps.core.arquivos import formatar_tamanho, ler_conteudo

# Assinatura real de cada imagem aceita. O que vale é o começo do arquivo, e
# não a extensão nem o tipo que o navegador declarou.
ASSINATURAS = (
    ("image/jpeg", b"\xff\xd8\xff", "jpg"),
    ("image/png", b"\x89PNG\r\n\x1a\n", "png"),
)

# WEBP é um contêiner RIFF: o tipo está na marca no meio do cabeçalho, e não no
# início do arquivo.
MARCA_RIFF = b"RIFF"
MARCA_WEBP = b"WEBP"
MIME_WEBP = "image/webp"
EXTENSAO_WEBP = "webp"

# Como a lista de formatos aceitos é dita a quem lê a recusa.
FORMATOS_ACEITOS = "JPG, PNG ou WEBP"


def tipo_da_imagem(conteudo: bytes) -> tuple[str, str] | None:
    """O tipo e a extensão da imagem, lidos do próprio arquivo.

    Devolve ``None`` quando o conteúdo não é nenhuma das imagens aceitas.
    """
    for mime, assinatura, extensao in ASSINATURAS:
        if conteudo.startswith(assinatura):
            return mime, extensao

    if conteudo[:4] == MARCA_RIFF and conteudo[8:12] == MARCA_WEBP:
        return MIME_WEBP, EXTENSAO_WEBP

    return None


def validar_imagem(
    arquivo,
    *,
    limite_em_bytes: int,
    erro,
    sem_arquivo: str,
    assunto: str = "foto",
) -> tuple[bytes, str, str]:
    """Confere a imagem e devolve o que dela precisa ser guardado.

    Devolve o conteúdo, o tipo real e a extensão. ``erro`` é a exceção com que o
    domínio explica a recusa na própria língua, e ``sem_arquivo`` é a frase dele
    para quando nada foi escolhido: a régua é comum, a conversa continua sendo de
    quem chamou.

    O que não passa daqui não vira arquivo. Não é imagem aceita o que não traz a
    assinatura real — o nome que recebeu não decide nada. E o tipo informado não
    é repassado: quando o navegador diz uma coisa e o arquivo é outra, recusar é
    a única resposta honesta, porque aceitar seria guardar um arquivo com um
    tipo que ele não tem.
    """
    if arquivo is None:
        raise erro(sem_arquivo)

    tamanho = int(getattr(arquivo, "size", 0) or 0)
    if tamanho <= 0:
        raise erro("A imagem está vazia.")

    if limite_em_bytes and tamanho > limite_em_bytes:
        raise erro(
            f"A imagem tem {formatar_tamanho(tamanho)} e passa do limite de "
            f"{formatar_tamanho(limite_em_bytes)}."
        )

    conteudo = ler_conteudo(arquivo)
    detectado = tipo_da_imagem(conteudo)
    if detectado is None:
        raise erro(
            f"O arquivo não é uma imagem aceita. Envie uma {assunto} "
            f"{FORMATOS_ACEITOS}."
        )
    mime, extensao = detectado

    informado = (
        (getattr(arquivo, "content_type", "") or "").split(";")[0].strip().lower()
    )
    if informado and informado != mime:
        raise erro(
            f"O arquivo enviado não corresponde a uma imagem aceita. Envie uma "
            f"{assunto} {FORMATOS_ACEITOS}."
        )

    return conteudo, mime, extensao
