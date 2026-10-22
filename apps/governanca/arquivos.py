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

from apps.balancos.services import (
    calcular_hash,
    formatar_tamanho,
    ler_conteudo,
    sanitizar_nome,
)
from apps.core.storage import ErroDeStorage, obter_provider

from .models import ArquivoDocumento, DocumentoOperacional

# Assinatura real de cada formato aceito. O que vale é o começo do arquivo.
ASSINATURAS = (
    ("application/pdf", b"%PDF-", "pdf"),
    ("image/jpeg", b"\xff\xd8\xff", "jpg"),
    ("image/png", b"\x89PNG\r\n\x1a\n", "png"),
)

# Como a tela nomeia cada formato aceito. Tipo desconhecido não vira sigla.
ROTULO_POR_MIME = {
    "application/pdf": "PDF",
    "image/jpeg": "Imagem JPG",
    "image/png": "Imagem PNG",
}

# O que a recusa diz quando o conteúdo não é nenhum dos formatos aceitos.
FORMATOS_ACEITOS = "PDF, JPG ou PNG"


class ErroDeArquivo(Exception):
    """O arquivo não pode ser guardado. A mensagem é para a gestão."""


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
    for mime, assinatura, extensao in ASSINATURAS:
        if conteudo.startswith(assinatura):
            return mime, extensao

    return None


def validar_arquivo(arquivo) -> tuple[bytes, str, str, str]:
    """Confere o arquivo e devolve o que dele precisa ser guardado.

    Devolve o conteúdo, o tipo real, a extensão e o nome já tratado. Um arquivo
    que não passa daqui não vira nem registro: não houve anexo, então não há o
    que guardar.
    """
    if arquivo is None:
        raise ErroDeArquivo("Escolha o arquivo que acompanha este documento.")

    tamanho = int(getattr(arquivo, "size", 0) or 0)
    if tamanho <= 0:
        raise ErroDeArquivo("O arquivo está vazio.")

    limite = limite_de_arquivo()
    if limite and tamanho > limite:
        raise ErroDeArquivo(
            f"O arquivo tem {formatar_tamanho(tamanho)} e passa do limite de "
            f"{formatar_tamanho(limite)}."
        )

    conteudo = ler_conteudo(arquivo)
    detectado = tipo_do_arquivo(conteudo)
    if detectado is None:
        raise ErroDeArquivo(
            f"O arquivo não é de um formato aceito. Envie em {FORMATOS_ACEITOS}."
        )
    mime, extensao = detectado

    informado = (
        (getattr(arquivo, "content_type", "") or "").split(";")[0].strip().lower()
    )
    if informado and informado != mime:
        # O navegador disse uma coisa e o arquivo é outra. Recusar é a única
        # resposta honesta: repassar o tipo informado seria guardar um arquivo
        # com um tipo que ele não tem.
        raise ErroDeArquivo(
            f"O arquivo enviado não corresponde a um formato aceito. Envie em "
            f"{FORMATOS_ACEITOS}."
        )

    return conteudo, mime, extensao, sanitizar_nome(getattr(arquivo, "name", ""))


def montar_chave(*, condominio, documento, arquivo, extensao: str) -> str:
    """Caminho do objeto dentro do bucket privado.

    Só identificadores: o nome que a pessoa deu ao arquivo não entra aqui. O
    caminho não carrega dado pessoal e não depende do que o navegador enviou.
    """
    return f"documentos/{condominio.pk}/{documento.pk}/{arquivo.pk}.{extensao}"


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
