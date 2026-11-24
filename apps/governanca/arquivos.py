"""Os arquivos que comprovam um documento operacional.

O documento guarda o registro — o que é, de que tipo, até quando vale, a que se
liga. O arquivo é o que sustenta esse registro: o PDF do laudo, a foto do
certificado afixado, a imagem da apólice assinada. Não há cadastro novo nem
acervo paralelo: o arquivo se prende ao documento que já existe, e o que fica
guardado é a referência do objeto no bucket privado — nunca o arquivo em disco
local nem um endereço público.

O formato é conferido pelo conteúdo real, e não pela extensão nem pelo que o
navegador informou: nenhum dos dois é fonte de confiança. O nome que a pessoa deu
ao arquivo é guardado apenas para ela reconhecê-lo — ele nunca define onde o
objeto fica no storage. O acesso é temporário e gerado na hora, como o do PDF do
balanço e o da evidência visual.
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from apps.core.arquivos import (
    ASSINATURAS_DE_ARQUIVO,
    FORMATOS_DE_ARQUIVO_ACEITOS,
    ErroDeArquivo,
    calcular_hash,
    formatar_tamanho,
    montar_chave_documento,
    tipo_de_arquivo,
    validar_arquivo,
)
from apps.core.storage import ErroDeStorage, obter_provider

from .models import ArquivoDocumento, DocumentoOperacional

# Assinatura real de cada formato aceito. O que vale é o começo do arquivo.
ASSINATURAS = ASSINATURAS_DE_ARQUIVO

# Como a tela nomeia cada formato aceito. Tipo desconhecido não vira sigla.
ROTULO_POR_MIME = {
    "application/pdf": "PDF",
    "image/jpeg": "Imagem JPG",
    "image/png": "Imagem PNG",
}

# O que a recusa diz quando o conteúdo não é nenhum dos formatos aceitos.
FORMATOS_ACEITOS = FORMATOS_DE_ARQUIVO_ACEITOS


# -- O arquivo ------------------------------------------------------------------


def limite_de_arquivo() -> int:
    """Tamanho máximo aceito para um arquivo, em bytes.

    É o mesmo limite já definido para os arquivos do Eithra: o que se recusa
    cedo é um arquivo absurdo, não o uso normal.
    """
    return int(getattr(settings, "BALANCOS_MAX_UPLOAD_BYTES", 0) or 0)


def limite_legivel() -> str:
    """O limite em linguagem comum, para a tela dizer sem expor bytes."""
    limite = limite_de_arquivo()
    return formatar_tamanho(limite) if limite else ""


def tipo_do_arquivo(conteudo: bytes) -> tuple[str, str] | None:
    """O tipo e a extensão do arquivo, lidos do próprio conteúdo."""
    return tipo_de_arquivo(conteudo)


def montar_chave(*, condominio, documento, arquivo, extensao: str) -> str:
    """Caminho do objeto dentro do bucket privado.

    Só identificadores: o nome que a pessoa deu ao arquivo não entra aqui. O
    caminho não carrega dado pessoal e não depende do que o navegador enviou.
    """
    return montar_chave_documento(
        condominio=condominio,
        documento=documento,
        arquivo=arquivo,
        extensao=extensao,
    )


def provedor_dos_documentos():
    """O provedor no bucket privado dos documentos operacionais.

    O bucket é do domínio: se ele não estiver definido no ambiente, o envio é
    recusado — não existe queda silenciosa para disco local.
    """
    return obter_provider(bucket=getattr(settings, "DOCUMENTOS_STORAGE_BUCKET", ""))


def acesso_temporario(arquivo, *, validade_segundos: int | None = None) -> str:
    """Devolve um acesso curto ao arquivo, gerado na hora — nunca guardado."""
    if not arquivo.storage_key:
        raise ErroDeStorage("O arquivo deste documento não está disponível.")

    segundos = int(
        validade_segundos
        or getattr(settings, "BALANCOS_URL_ACESSO_SEGUNDOS", 60)
        or 60
    )
    return provedor_dos_documentos().url_assinada(
        key=arquivo.storage_key, validade_segundos=segundos
    )


def resumo_do_arquivo(arquivo) -> dict:
    """O que a tela precisa saber sobre o arquivo, sem termo técnico.

    O acesso é resolvido aqui, e a falta dele não derruba a tela: um documento
    cujo arquivo não pôde ser alcançado agora continua listado pelo nome.
    """
    try:
        acesso = acesso_temporario(arquivo)
    except (ErroDeStorage, ImproperlyConfigured):
        acesso = ""

    return {
        "pk": arquivo.pk,
        "nome": arquivo.nome_original,
        "tipo": ROTULO_POR_MIME.get(arquivo.mime_type, "Arquivo"),
        "tamanho": formatar_tamanho(arquivo.tamanho_bytes),
        "enviado_em": timezone.localtime(arquivo.enviado_em).strftime("%d/%m/%Y"),
        "acesso": acesso,
    }


# -- Anexar ---------------------------------------------------------------------


@transaction.atomic
def anexar_arquivo(*, documento, arquivo, usuario) -> ArquivoDocumento:
    """Guarda o arquivo preso ao documento, em uma transação.

    O objeto vai para o bucket antes de o registro existir, para que uma falha
    de armazenamento não deixe um arquivo registrado sem conteúdo guardado.
    """
    bloqueado = (
        DocumentoOperacional.objects.select_for_update()
        .select_related("condominio")
        .get(pk=documento.pk)
    )

    conteudo, mime, extensao, nome = validar_arquivo(arquivo)
    sha256 = calcular_hash(conteudo)

    if ArquivoDocumento.objects.filter(documento=bloqueado, sha256=sha256).exists():
        raise ErroDeArquivo("Este arquivo já está anexado a este documento.")

    registro = ArquivoDocumento(
        documento=bloqueado,
        nome_original=nome,
        mime_type=mime,
        tamanho_bytes=len(conteudo),
        sha256=sha256,
        storage_key="",  # provisório: a chave depende do identificador
        enviado_por=usuario,
    )
    registro.storage_key = montar_chave(
        condominio=bloqueado.condominio,
        documento=bloqueado,
        arquivo=registro,
        extensao=extensao,
    )

    # A conferência vem antes do envio: o que não passa no modelo não chega a
    # virar objeto no bucket.
    registro.full_clean()
    provider = provedor_dos_documentos()
    provider.enviar(key=registro.storage_key, conteudo=conteudo, content_type=mime)
    registro.storage_provider = provider.nome
    registro.storage_bucket = provider.bucket
    registro.save()

    return registro
