"""Recebimento do arquivo de balanço: do PDF ao registro transitório.

Este é o caminho por onde entra um documento. Nada aqui cria balanço: o que
nasce é uma ``ImportacaoBalanco`` — uma tentativa, ainda não confirmada. Só a
confirmação da gestão, em fase futura, faz o mês valer.

O upload não mora na view nem em ``save()``, e não há signal escondido: a
sequência é explícita e cada passo tem um nome. Quem chama é que decide o que
dizer ao usuário.

Duas coisas diferentes são guardadas em dois lugares diferentes:

- o banco guarda o registro e a referência do objeto;
- o bucket privado guarda o arquivo.

Não existe transação que cubra os dois. Se o arquivo for enviado e a gravação
seguinte falhar, o envio é desfeito na medida do possível; se o arquivo não for
enviado, a tentativa fica registrada como não recebida, e não como sucesso.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.utils import timezone

from apps.balancos.constants import StatusImportacao
from apps.balancos.detector import ErroDeDeteccaoDeLayout, detectar_layout_balanco
from apps.balancos.models import ImportacaoBalanco
from apps.balancos.storage import ErroDeStorage, obter_provider

# Assinatura de um PDF de verdade. Extensão e tipo informado pelo navegador não
# provam nada: o que vale é o começo do arquivo.
ASSINATURA_PDF = b"%PDF-"

MIME_PDF = "application/pdf"

# Nome do objeto dentro do bucket. O nome que o usuário deu ao arquivo fica
# apenas no banco: o caminho do objeto não carrega dado pessoal nem depende do
# que o navegador enviou.
NOME_DO_OBJETO = "documento.pdf"


class ErroDeValidacaoDeArquivo(Exception):
    """O arquivo recebido não serve. A mensagem é para a gestão."""


@dataclass(frozen=True)
class ResultadoDoRecebimento:
    """O que aconteceu com a tentativa de importação.

    ``duplicada`` diz que o arquivo já estava aqui: nesse caso ``importacao`` é
    a que já existia, e nada novo foi guardado.
    """

    importacao: ImportacaoBalanco
    duplicada: bool = False


def limite_de_upload() -> int:
    """Tamanho máximo aceito para um arquivo, em bytes."""
    return int(getattr(settings, "BALANCOS_MAX_UPLOAD_BYTES", 0) or 0)


def formatar_tamanho(bytes_: int) -> str:
    """Escreve um tamanho em linguagem comum, sem expor bytes crus."""
    if bytes_ >= 1024 * 1024:
        return f"{bytes_ / (1024 * 1024):.0f} MB"
    if bytes_ >= 1024:
        return f"{bytes_ / 1024:.0f} KB"
    return f"{bytes_} bytes"


def sanitizar_nome(nome: str) -> str:
    """Guarda o nome do arquivo como a pessoa o conhece.

    Apenas o nome, sem caminho, e sem caractere de controle: ele volta para a
    tela e não deve poder forjar diretório nem quebrar a listagem.
    """
    original = (nome or "").strip().replace("\\", "/")
    limpo = original.rsplit("/", 1)[-1]
    limpo = "".join(caractere for caractere in limpo if caractere.isprintable())
    return (limpo.strip() or "Documento.pdf")[:255]


def conteudo_inicia_com_pdf(arquivo) -> bool:
    """Lê o começo do arquivo para confirmar a assinatura do PDF."""
    try:
        posicao = arquivo.tell()
    except (AttributeError, OSError):
        posicao = None

    try:
        arquivo.seek(0)
        inicio = arquivo.read(len(ASSINATURA_PDF))
    finally:
        if posicao is not None:
            arquivo.seek(posicao)

    return bytes(inicio or b"").startswith(ASSINATURA_PDF)


def validar_arquivo(arquivo) -> None:
    """Confere o arquivo antes de qualquer gravação.

    Um arquivo que não passa daqui não vira nem registro: não houve
    recebimento, então não há o que guardar.
    """
    if arquivo is None:
        raise ErroDeValidacaoDeArquivo("Escolha o arquivo do balanço em PDF.")

    nome = (getattr(arquivo, "name", "") or "").strip()
    if not nome.lower().endswith(".pdf"):
        raise ErroDeValidacaoDeArquivo("O arquivo precisa estar em PDF.")

    tamanho = int(getattr(arquivo, "size", 0) or 0)
    if tamanho <= 0:
        raise ErroDeValidacaoDeArquivo("O arquivo está vazio.")

    limite = limite_de_upload()
    if limite and tamanho > limite:
        raise ErroDeValidacaoDeArquivo(
            f"O arquivo tem {formatar_tamanho(tamanho)} e passa do limite de "
            f"{formatar_tamanho(limite)}."
        )

    informado = (getattr(arquivo, "content_type", "") or "").split(";")[0].strip().lower()
    if informado and informado != MIME_PDF:
        raise ErroDeValidacaoDeArquivo("O arquivo precisa estar em PDF.")

    if not conteudo_inicia_com_pdf(arquivo):
        raise ErroDeValidacaoDeArquivo(
            "O arquivo não parece um PDF de verdade. Confira o documento e envie de novo."
        )


def ler_conteudo(arquivo) -> bytes:
    """Traz o arquivo inteiro para calcular o hash e enviar ao storage.

    O balanço é um documento único, e o limite configurado mantém a leitura
    dentro de um tamanho previsível. Não há fluxo contínuo a preservar aqui.
    """
    arquivo.seek(0)
    return arquivo.read()


def calcular_hash(conteudo: bytes) -> str:
    """Hash SHA-256 do arquivo, em hexadecimal minúsculo."""
    return hashlib.sha256(conteudo).hexdigest()


def montar_chave(*, condominio, importacao) -> str:
    """Caminho do objeto dentro do bucket.

    Só identificadores: nem nome de condomínio em texto, nem nome de arquivo,
    nem competência ainda não confirmada. O identificador da importação já
    separa cada tentativa, então duas remessas do mesmo arquivo não se
    sobrescrevem.
    """
    return f"balancos/{condominio.pk}/{importacao.pk}/{NOME_DO_OBJETO}"


def verificar_duplicidade(condominio, sha256: str) -> ImportacaoBalanco | None:
    """Procura o mesmo arquivo já recebido para o mesmo condomínio.

    Duas situações não contam como repetição: uma importação descartada (a
    gestão já recusou aquele arquivo) e uma que nem chegou (falha de
    recebimento, que a pessoa vai querer tentar de novo). E o mesmo arquivo em
    outro condomínio é outro documento — repetição só vale dentro do
    condomínio.
    """
    return (
        ImportacaoBalanco.objects.filter(condominio=condominio, sha256=sha256)
        .exclude(status__in=[StatusImportacao.DESCARTADO, StatusImportacao.ERRO])
        .order_by("-criado_em")
        .first()
    )


def armazenar_documento(*, provider, chave: str, conteudo: bytes) -> None:
    """Entrega o arquivo ao storage privado, sob a chave informada."""
    provider.enviar(key=chave, conteudo=conteudo, content_type=MIME_PDF)


def receber_importacao(*, condominio, usuario, arquivo) -> ResultadoDoRecebimento:
    """Recebe o PDF, guarda o arquivo e cria o registro transitório.

    Nenhum ``BalancoMensal`` nasce aqui. O que fica registrado é a tentativa,
    com o hash do arquivo, a referência do objeto e o layout detectado quando a
    leitura do PDF consegue classificá-lo.
    """
    validar_arquivo(arquivo)
    conteudo = ler_conteudo(arquivo)
    sha256 = calcular_hash(conteudo)

    ja_recebida = verificar_duplicidade(condominio, sha256)
    if ja_recebida is not None:
        return ResultadoDoRecebimento(importacao=ja_recebida, duplicada=True)

    provider = obter_provider()

    importacao = ImportacaoBalanco(
        condominio=condominio,
        criado_por=usuario,
        nome_original=sanitizar_nome(getattr(arquivo, "name", "")),
        mime_type=MIME_PDF,
        tamanho_bytes=len(conteudo),
        sha256=sha256,
        storage_provider=provider.nome,
        storage_bucket=provider.bucket,
        status=StatusImportacao.RECEBIDO,
    )
    importacao.storage_key = montar_chave(condominio=condominio, importacao=importacao)
    # A gravação vem antes do envio: é ela que dá identidade à importação e
    # define a chave do objeto.
    importacao.save()

    try:
        armazenar_documento(
            provider=provider, chave=importacao.storage_key, conteudo=conteudo
        )
    except ErroDeStorage as erro:
        # O arquivo não chegou ao bucket. A tentativa fica registrada como não
        # recebida — nunca como sucesso — e nada precisa ser removido.
        importacao.status = StatusImportacao.ERRO
        importacao.erro_processamento = str(erro)
        importacao.save(update_fields=["status", "erro_processamento", "atualizado_em"])
        return ResultadoDoRecebimento(importacao=importacao)

    try:
        detectar_layout_balanco(importacao, provider=provider)
    except (ErroDeStorage, ErroDeDeteccaoDeLayout):
        importacao.refresh_from_db()
        return ResultadoDoRecebimento(importacao=importacao)
    except DatabaseError:
        # O arquivo já está no bucket e o registro não pôde ser atualizado:
        # desfaz o envio para não deixar objeto sem dono.
        try:
            provider.remover(key=importacao.storage_key)
        except ErroDeStorage:
            pass
        raise

    return ResultadoDoRecebimento(importacao=importacao)


def gerar_acesso_temporario(importacao, *, validade_segundos: int | None = None) -> str:
    """Devolve um acesso curto ao arquivo, gerado na hora.

    Nada é guardado: a próxima visita gera outro acesso. É assim que o arquivo
    continua privado sem deixar de ser consultável por quem pode.
    """
    if not importacao.storage_key:
        raise ErroDeStorage("O arquivo desta importação não está disponível.")

    segundos = int(
        validade_segundos
        or getattr(settings, "BALANCOS_URL_ACESSO_SEGUNDOS", 60)
        or 60
    )
    return obter_provider().url_assinada(
        key=importacao.storage_key, validade_segundos=segundos
    )


def descartar_importacao(importacao) -> None:
    """Recusa a importação e libera o arquivo do bucket.

    O registro não é apagado: fica a memória de que aquele arquivo foi recebido
    e recusado, com a data. O que sai é o objeto, para um descarte não continuar
    ocupando espaço.
    """
    if importacao.confirmado_em:
        raise ErroDeStorage("Uma importação já confirmada não é descartada.")

    importacao.status = StatusImportacao.DESCARTADO
    importacao.descartado_em = timezone.now()
    importacao.save(update_fields=["status", "descartado_em", "atualizado_em"])

    _remover_objeto(importacao)


def _remover_objeto(importacao) -> None:
    """Remove o arquivo do bucket sem tornar a operação refém do storage.

    Se a remoção — ou a própria configuração — falhar, a referência continua no
    registro e o objeto pode ser limpo depois. O descarte em si já está
    gravado. Nada disso vira erro para a gestão.
    """
    if not importacao.storage_key:
        return

    try:
        obter_provider().remover(key=importacao.storage_key)
    except (ErroDeStorage, ImproperlyConfigured):
        return
