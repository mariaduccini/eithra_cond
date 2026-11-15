"""Onde o card de um comunicado fica guardado.

O card é um arquivo privado: ele é gerado para ser compartilhado pela gestão, e
não para ficar exposto num endereço público. Por isso ele vai para o mesmo
armazenamento privado que os demais arquivos do Eithra usam, num espaço próprio
— o card de um condomínio não se mistura com a evidência de outro assunto —, e
aqui fica apenas a referência do objeto.

O acesso é pedido ao provedor na hora: assinado e temporário onde isso existe, o
endereço da própria mídia onde não existe, e nunca uma URL pública guardada. O
bucket é decisão do ambiente: sem ele definido no provedor remoto, o envio é
recusado, e não desviado em silêncio para outro lugar.
"""
from __future__ import annotations

from django.conf import settings

from apps.core.storage import ErroDeStorage, obter_provider

# A variável de ambiente que diz em qual bucket privado os cards vivem. O nome é
# citado na recusa do provedor: sem ele, a falta da variável seria reportada com
# o nome do bucket de outro domínio.
NOME_DO_BUCKET = "COMUNICACAO_STORAGE_BUCKET"

# O card sai sempre em PNG — é o formato que o WhatsApp e o celular abrem sem
# susto, e o único que este domínio produz.
MIME_DO_CARD = "image/png"
EXTENSAO_DO_CARD = "png"


def montar_chave(*, comunicacao, extensao: str = EXTENSAO_DO_CARD) -> str:
    """Caminho do objeto dentro do bucket privado.

    Só identificadores: o condomínio, o comunicado e a extensão. O caminho não
    carrega dado pessoal e não permite escapar da pasta do condomínio.
    """
    return (
        f"comunicacao/{comunicacao.condominio_id}/{comunicacao.pk}/card.{extensao}"
    )


def provedor_dos_cartoes():
    """O provedor no bucket privado dos cards de comunicação.

    O bucket é do domínio: se ele não estiver definido no ambiente, o envio é
    recusado — não existe queda silenciosa para disco local.
    """
    return obter_provider(
        bucket=getattr(settings, NOME_DO_BUCKET, ""),
        nome_do_bucket=NOME_DO_BUCKET,
    )


def guardar_cartao(*, comunicacao, conteudo: bytes):
    """Guarda o card no bucket e devolve o provedor e a chave do objeto.

    O arquivo vai para o armazenamento antes de a referência existir no banco:
    uma falha de armazenamento não deixa um comunicado apontando para um card
    que não subiu.
    """
    chave = montar_chave(comunicacao=comunicacao)
    provedor = provedor_dos_cartoes()
    provedor.enviar(key=chave, conteudo=conteudo, content_type=MIME_DO_CARD)
    return provedor, chave


def acesso_temporario(comunicacao, *, validade_segundos: int | None = None) -> str:
    """Devolve um acesso curto ao card, gerado na hora — nunca guardado."""
    if not comunicacao.storage_key:
        raise ErroDeStorage("O card deste comunicado ainda não foi gerado.")

    segundos = int(
        validade_segundos
        or getattr(settings, "COMUNICACAO_URL_ACESSO_SEGUNDOS", 60)
        or 60
    )
    return provedor_dos_cartoes().url_assinada(
        key=comunicacao.storage_key, validade_segundos=segundos
    )
