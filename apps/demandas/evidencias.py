"""As fotos, os vídeos e os documentos que sustentam o que se sabe de uma demanda.

A evidência não é um registro novo nem um acervo paralelo: ela se prende à
demanda que já existe e à movimentação que a trouxe. O que fica guardado aqui é
a referência do objeto no bucket privado — nunca o arquivo em disco local nem um
endereço público.

O formato é conferido pelo conteúdo real, e não pela extensão nem pelo que o
navegador informou: nenhum dos dois é fonte de confiança. Para a foto de sempre,
a régua já existe e é compartilhada (``apps.core.imagem``); para o vídeo e para o
PDF, ela nasce aqui com o mesmo critério — a assinatura, lida no começo do
arquivo.

A foto tirada no celular costuma chegar em HEIC, que o banco não guarda e o
navegador nem sempre sabe mostrar. Ela é lida, reduzida e convertida para JPG
antes de ser guardada: o que vive no bucket é sempre JPG, na orientação certa e
dentro do limite. O formato de origem não é preservado — só o que ele mostra.

O nome que a pessoa deu ao arquivo é guardado apenas para ela reconhecê-lo: ele
nunca define onde o objeto fica no storage. A chave é montada só com
identificadores, e por isso não carrega dado pessoal nem depende do que veio do
navegador.

Nada aqui decide o destino da demanda. Anexar uma prova não conclui, não cancela
e não cria demanda: quem decide é o serviço oficial do domínio, depois da
confirmação de quem registrou.
"""
from __future__ import annotations

import io

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from PIL import Image, ImageOps

from apps.balancos.services import calcular_hash, formatar_tamanho, ler_conteudo
from apps.core.arquivos import ASSINATURA_PDF, MIME_PDF
from apps.core.imagem import tipo_da_imagem, validar_imagem
from apps.core.storage import ErroDeStorage, obter_provider

from .constants import MIMES_DE_EVIDENCIA, TipoMidiaEvidencia
from .models import Demanda, EvidenciaDemanda

# O leitor de HEIC/HEIF é opcional na importação: se a biblioteca não estiver
# disponível, o resto do módulo continua funcionando e só a conversão de foto de
# celular é que não acontece — com uma recusa clara, nunca um erro de servidor.
try:  # pragma: no cover - a presença da lib é ambiente, não regra
    import pillow_heif

    pillow_heif.register_heif_opener()
    _HEIC_DISPONIVEL = True
except Exception:  # pragma: no cover
    _HEIC_DISPONIVEL = False

# O que a tela oferece no seletor de arquivo. É só a primeira triagem do
# navegador — o que decide é o conteúdo real, conferido logo abaixo.
#
# Esta lista é maior do que a que o banco aceita: HEIC e HEIF chegam do celular e
# viram JPG antes de guardar, e as extensões entram porque alguns celulares
# mandam HEIC sem informar o content-type.
ACEITE_DE_EVIDENCIA = ",".join(
    (
        *MIMES_DE_EVIDENCIA,
        "image/heic",
        "image/heif",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".mp4",
        ".webm",
        ".heic",
        ".heif",
        ".pdf",
    )
)

# O que a recusa diz quando o arquivo não é nenhuma das evidências aceitas.
FORMATOS_ACEITOS = "JPG, PNG, WEBP, HEIC, PDF, MP4 ou WEBM"

# Assinatura real dos vídeos aceitos. MP4 é um contêiner ISO e traz a marca
# ``ftyp`` no quinto byte; WEBM é Matroska e começa pela assinatura própria.
MARCA_MP4 = b"ftyp"
POSICAO_MP4 = 4
ASSINATURA_WEBM = b"\x1a\x45\xdf\xa3"

# HEIC e HEIF usam o mesmo contêiner ISO do MP4 — ``ftyp`` no quinto byte — e se
# distinguem pela marca que vem logo depois. É por isso que a foto de celular é
# reconhecida antes do vídeo: sem essa ordem, um HEIC passaria por MP4.
MARCAS_HEIC = (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftyphevc")

# Quantos bytes bastam para reconhecer qualquer um dos formatos aceitos.
BYTES_DE_ABERTURA = 32

# Como a foto vira JPG antes de ser guardada: no máximo 2000 px no lado maior e
# qualidade 85. É o suficiente para mostrar o problema sem guardar a foto
# original do celular, que costuma ter vários megabytes.
LADO_MAXIMO_DA_FOTO = 2000
QUALIDADE_DO_JPG = 85

# Teto de entrada antes de reduzir. A foto que passa do limite é reduzida em vez
# de recusada, mas nada justifica carregar para a memória um arquivo de dezenas
# de megabytes: a partir daqui a recusa é a resposta que protege o servidor.
TETO_DA_FOTO = 60 * 1024 * 1024


class ErroDeEvidencia(Exception):
    """O arquivo não pode ser guardado como evidência. A mensagem é humana."""


# -- Limites --------------------------------------------------------------------


def limite_da_foto() -> int:
    """Tamanho máximo aceito para uma foto, em bytes."""
    return int(getattr(settings, "DEMANDAS_MAX_FOTO_BYTES", 0) or 0)


def limite_do_video() -> int:
    """Tamanho máximo aceito para um vídeo curto, em bytes."""
    return int(getattr(settings, "DEMANDAS_MAX_VIDEO_BYTES", 0) or 0)


def limite_do_documento() -> int:
    """Tamanho máximo aceito para um documento (PDF), em bytes."""
    return int(getattr(settings, "DEMANDAS_MAX_DOCUMENTO_BYTES", 0) or 0)


def limite_de_evidencias() -> int:
    """Quantos arquivos podem acompanhar uma mesma atualização."""
    return int(getattr(settings, "DEMANDAS_MAX_EVIDENCIAS_POR_ENVIO", 0) or 0)


def limites_legiveis() -> dict:
    """Os limites em linguagem comum, para a tela dizer sem expor bytes."""
    return {
        "foto": formatar_tamanho(limite_da_foto()),
        "video": formatar_tamanho(limite_do_video()),
        "documento": formatar_tamanho(limite_do_documento()),
        "formatos": FORMATOS_ACEITOS,
        # A mesma lista que a borda confere, para a tela não oferecer um formato
        # que será recusado adiante.
        "aceite": ACEITE_DE_EVIDENCIA,
    }


# -- O arquivo ------------------------------------------------------------------


def tipo_do_video(conteudo: bytes) -> tuple[str, str] | None:
    """O tipo e a extensão do vídeo, lidos do próprio arquivo.

    Devolve ``None`` quando o conteúdo não é nenhum dos vídeos aceitos.
    """
    if conteudo[POSICAO_MP4 : POSICAO_MP4 + 4] == MARCA_MP4:
        return "video/mp4", "mp4"

    if conteudo.startswith(ASSINATURA_WEBM):
        return "video/webm", "webm"

    return None


def tipo_heic(conteudo: bytes) -> tuple[str, str] | None:
    """O tipo e a extensão da foto de celular, lidos do próprio arquivo.

    Devolve ``None`` quando o conteúdo não é HEIC nem HEIF. É reconhecido antes
    do vídeo de propósito: os dois começam com ``ftyp`` no quinto byte, e só a
    marca que vem depois diz de qual dos dois se trata.
    """
    if conteudo[POSICAO_MP4 : POSICAO_MP4 + 8] in MARCAS_HEIC:
        return "image/heic", "heic"

    return None


def _abertura(arquivo) -> bytes:
    """Lê só o começo do arquivo — o suficiente para reconhecer o formato.

    Um vídeo pode ter dezenas de megabytes; reconhecê-lo não justifica carregar
    tudo em memória duas vezes.
    """
    arquivo.seek(0)
    return arquivo.read(BYTES_DE_ABERTURA)


def _reduzir_para_jpg(conteudo: bytes) -> tuple[bytes, str, str]:
    """Abre a foto, respeita a orientação do EXIF, reduz e devolve o JPG.

    É o mesmo caminho para a foto de celular e para a foto grande: o que vive no
    bucket é sempre um JPG dentro do limite. ``exif_transpose`` é aplicado antes
    de salvar, senão a foto de celular aparece deitada depois da conversão.
    """
    try:
        with Image.open(io.BytesIO(conteudo)) as imagem:
            imagem = ImageOps.exif_transpose(imagem)
            if imagem.mode not in ("RGB", "L"):
                imagem = imagem.convert("RGB")
            imagem.thumbnail((LADO_MAXIMO_DA_FOTO, LADO_MAXIMO_DA_FOTO))
            saida = io.BytesIO()
            imagem.save(saida, format="JPEG", quality=QUALIDADE_DO_JPG)
    except ErroDeEvidencia:
        raise
    except Exception as exc:
        raise ErroDeEvidencia(
            "Não consegui preparar esta foto. "
            f"Envie uma foto em {FORMATOS_ACEITOS}."
        ) from exc

    return saida.getvalue(), "image/jpeg", "jpg"


def _dentro_do_limite(conteudo: bytes, mime: str, extensao: str):
    """Recusa a foto que, já reduzida, ainda passa do limite.

    A redução resolve o caso comum — a foto de celular com alguns megabytes a
    mais —, mas o limite continua valendo: o que não couber nem reduzido não
    vira arquivo.
    """
    limite = limite_da_foto()
    if limite and len(conteudo) > limite:
        raise ErroDeEvidencia(
            f"A foto, mesmo reduzida, ficou com {formatar_tamanho(len(conteudo))} "
            f"e passa do limite de {formatar_tamanho(limite)}."
        )
    return conteudo, mime, extensao


def validar_foto(arquivo) -> tuple[bytes, str, str]:
    """Confere a foto e devolve o que guardar — sempre um JPG dentro do limite.

    A foto de celular (HEIC/HEIF) é reconhecida pelo conteúdo e convertida para
    JPG, o único formato de foto que o banco conhece. A foto que passa do limite
    é reduzida em vez de recusada: perder a prova que mostra o problema por
    causa do tamanho do arquivo seria perder o que interessa. Só recusa quando,
    depois de reduzida, ela ainda passa do limite.
    """
    if arquivo is None:
        raise ErroDeEvidencia("Escolha a foto que sustenta este relato.")

    tamanho = int(getattr(arquivo, "size", 0) or 0)
    if tamanho <= 0:
        raise ErroDeEvidencia("A foto está vazia.")

    if tamanho > TETO_DA_FOTO:
        raise ErroDeEvidencia(
            f"A foto tem {formatar_tamanho(tamanho)} e é grande demais. "
            f"Envie uma foto em {FORMATOS_ACEITOS}."
        )

    conteudo = ler_conteudo(arquivo)

    # A foto de celular não é imagem que o banco guarde: vira JPG antes de medir
    # o limite, porque é o JPG que vai para o bucket.
    if tipo_heic(conteudo) is not None:
        return _dentro_do_limite(*_reduzir_para_jpg(conteudo))

    limite = limite_da_foto()
    if limite and len(conteudo) > limite:
        # Passou do limite: a régua compartilhada confere o formato antes de
        # tudo, e a redução cuida do tamanho.
        validar_imagem(
            arquivo,
            limite_em_bytes=0,
            erro=ErroDeEvidencia,
            sem_arquivo="Escolha a foto que sustenta este relato.",
            assunto="foto",
        )
        return _dentro_do_limite(*_reduzir_para_jpg(conteudo))

    # A foto dentro do limite segue a régua compartilhada, sem conversão.
    return validar_imagem(
        arquivo,
        limite_em_bytes=limite,
        erro=ErroDeEvidencia,
        sem_arquivo="Escolha a foto que sustenta este relato.",
        assunto="foto",
    )


def validar_video(arquivo) -> tuple[bytes, str, str]:
    """Confere o vídeo e devolve o conteúdo, o tipo real e a extensão.

    Vale o mesmo critério da foto: o que decide é o começo do arquivo. Quando o
    navegador diz uma coisa e o arquivo é outra, recusar é a única resposta
    honesta — aceitar seria guardar um arquivo com um tipo que ele não tem.
    """
    if arquivo is None:
        raise ErroDeEvidencia("Escolha o vídeo que sustenta este relato.")

    tamanho = int(getattr(arquivo, "size", 0) or 0)
    if tamanho <= 0:
        raise ErroDeEvidencia("O vídeo está vazio.")

    limite = limite_do_video()
    if limite and tamanho > limite:
        raise ErroDeEvidencia(
            f"O vídeo tem {formatar_tamanho(tamanho)} e passa do limite de "
            f"{formatar_tamanho(limite)}."
        )

    conteudo = ler_conteudo(arquivo)
    detectado = tipo_do_video(conteudo)
    if detectado is None:
        raise ErroDeEvidencia(
            "O arquivo não é um vídeo aceito. Envie um vídeo curto em MP4 ou WEBM."
        )
    mime, extensao = detectado

    informado = _tipo_informado(arquivo)
    if informado and informado != mime:
        raise ErroDeEvidencia(
            "O arquivo enviado não corresponde a um vídeo aceito. Envie um vídeo "
            "curto em MP4 ou WEBM."
        )

    return conteudo, mime, extensao


def validar_documento(arquivo) -> tuple[bytes, str, str]:
    """Confere o PDF e devolve o conteúdo, o tipo real e a extensão.

    Vale o mesmo critério das demais evidências: quem diz o que o arquivo é é o
    começo dele. Um ``.exe`` renomeado para ``.pdf`` não traz a assinatura
    ``%PDF-``, e recusar é a única resposta honesta.
    """
    if arquivo is None:
        raise ErroDeEvidencia("Escolha o documento que sustenta este relato.")

    tamanho = int(getattr(arquivo, "size", 0) or 0)
    if tamanho <= 0:
        raise ErroDeEvidencia("O documento está vazio.")

    limite = limite_do_documento()
    if limite and tamanho > limite:
        raise ErroDeEvidencia(
            f"O documento tem {formatar_tamanho(tamanho)} e passa do limite de "
            f"{formatar_tamanho(limite)}."
        )

    conteudo = ler_conteudo(arquivo)
    if not conteudo.startswith(ASSINATURA_PDF):
        raise ErroDeEvidencia(
            "O arquivo enviado não é um PDF. Envie a proposta, o laudo ou a nota "
            "em PDF."
        )

    informado = _tipo_informado(arquivo)
    if informado and informado != MIME_PDF:
        raise ErroDeEvidencia(
            "O arquivo enviado não é um PDF. Envie a proposta, o laudo ou a nota "
            "em PDF."
        )

    return conteudo, MIME_PDF, "pdf"


def _tipo_informado(arquivo) -> str:
    """O tipo que o navegador declarou, sem parâmetros e em minúsculas."""
    return (getattr(arquivo, "content_type", "") or "").split(";")[0].strip().lower()


def _como_chegou(arquivo) -> str:
    """Como o arquivo chegou — pelo nome que a pessoa deu, só para a conversa.

    A recusa diz o que chegou para que a pessoa reconheça o arquivo que enviou,
    e lista o que é aceito. O nome aqui é rótulo, como em todo o módulo: ele não
    decide nada, só dá nome ao que foi recusado.
    """
    nome = " ".join((getattr(arquivo, "name", "") or "").split())
    return f'O arquivo "{nome[:80]}"' if nome else "O arquivo enviado"


def validar_evidencia(arquivo) -> tuple[bytes, str, str, str]:
    """Confere o arquivo e diz o que ele é: foto, documento ou vídeo.

    Devolve o conteúdo, o tipo real, a extensão e o tipo de mídia. O que não
    passa daqui não vira nem registro: não houve anexo, então não há o que
    guardar nem o que contar no histórico.
    """
    if arquivo is None:
        raise ErroDeEvidencia(
            "Escolha a foto, o documento ou o vídeo que sustenta este relato."
        )

    abertura = _abertura(arquivo)

    # A foto de celular vem antes do vídeo: HEIC e MP4 começam com ``ftyp`` no
    # quinto byte, e só a marca que vem depois diz de qual dos dois se trata.
    if tipo_heic(abertura) is not None or tipo_da_imagem(abertura) is not None:
        conteudo, mime, extensao = validar_foto(arquivo)
        return conteudo, mime, extensao, TipoMidiaEvidencia.FOTO

    if tipo_do_video(abertura) is not None:
        conteudo, mime, extensao = validar_video(arquivo)
        return conteudo, mime, extensao, TipoMidiaEvidencia.VIDEO

    if abertura.startswith(ASSINATURA_PDF):
        conteudo, mime, extensao = validar_documento(arquivo)
        return conteudo, mime, extensao, TipoMidiaEvidencia.DOCUMENTO

    raise ErroDeEvidencia(
        f"{_como_chegou(arquivo)} não é uma evidência aceita. Envie uma foto, um "
        f"documento (PDF) ou um vídeo curto em {FORMATOS_ACEITOS}."
    )


def montar_chave(*, condominio, demanda, evidencia, extensao: str) -> str:
    """Caminho do objeto dentro do bucket privado.

    Só identificadores: o nome que a pessoa deu ao arquivo não entra aqui. O
    caminho não carrega dado pessoal, não depende do navegador e não permite
    escapar da pasta do condomínio.
    """
    return (
        f"demandas/{condominio.pk}/{demanda.pk}/evidencias/{evidencia.pk}.{extensao}"
    )


# -- Onde o arquivo fica --------------------------------------------------------


def provedor_das_evidencias():
    """O provedor no bucket privado das evidências de demanda.

    O bucket é do domínio: se ele não estiver definido no ambiente, o envio é
    recusado — não existe queda silenciosa para disco local.
    """
    return obter_provider(
        bucket=getattr(settings, "DEMANDAS_STORAGE_BUCKET", ""),
        nome_do_bucket="DEMANDAS_STORAGE_BUCKET",
    )


def acesso_temporario(evidencia, *, validade_segundos: int | None = None) -> str:
    """Devolve um acesso curto ao arquivo, gerado na hora — nunca guardado."""
    if not evidencia.storage_key:
        raise ErroDeStorage("O arquivo desta evidência não está disponível.")

    segundos = int(
        validade_segundos
        or getattr(settings, "DEMANDAS_URL_ACESSO_SEGUNDOS", 60)
        or 60
    )
    return provedor_das_evidencias().url_assinada(
        key=evidencia.storage_key, validade_segundos=segundos
    )


# -- Anexar ---------------------------------------------------------------------


@transaction.atomic
def anexar_evidencia(*, demanda, arquivo, usuario, evento) -> EvidenciaDemanda:
    """Guarda uma evidência presa à demanda e à movimentação que a trouxe.

    O objeto vai para o bucket antes de o registro existir, para que uma falha
    de armazenamento não deixe uma evidência registrada sem conteúdo guardado.
    Quem chama em um bloco próprio já garante que o conjunto — relato, situação
    e arquivos — entre ou não entre por inteiro.
    """
    bloqueada = (
        Demanda.objects.select_for_update()
        .select_related("condominio")
        .get(pk=demanda.pk)
    )

    if evento.demanda_id != bloqueada.pk:
        raise ErroDeEvidencia("A movimentação escolhida não é desta demanda.")

    conteudo, mime, extensao, tipo_midia = validar_evidencia(arquivo)
    sha256 = calcular_hash(conteudo)

    if EvidenciaDemanda.objects.filter(demanda=bloqueada, sha256=sha256).exists():
        raise ErroDeEvidencia("Este arquivo já está anexado a esta demanda.")

    evidencia = EvidenciaDemanda(
        demanda=bloqueada,
        evento=evento,
        tipo_midia=tipo_midia,
        nome_original=_nome_guardado(arquivo),
        mime_type=mime,
        tamanho_bytes=len(conteudo),
        sha256=sha256,
        storage_key="",  # provisório: a chave depende do identificador
        criado_por=usuario,
    )
    evidencia.storage_key = montar_chave(
        condominio=bloqueada.condominio,
        demanda=bloqueada,
        evidencia=evidencia,
        extensao=extensao,
    )

    # A conferência vem antes do envio: o que não passa no modelo não chega a
    # virar objeto no bucket.
    evidencia.full_clean()
    provider = provedor_das_evidencias()
    provider.enviar(key=evidencia.storage_key, conteudo=conteudo, content_type=mime)
    evidencia.storage_provider = provider.nome
    evidencia.storage_bucket = provider.bucket
    evidencia.save()

    return evidencia


def anexar_evidencias(*, demanda, arquivos, usuario, evento) -> list[EvidenciaDemanda]:
    """Guarda, na ordem, as evidências que acompanham uma mesma movimentação.

    O conjunto é recusado por inteiro quando passa do limite: o histórico não é
    lugar de galeria, e uma atualização com arquivos demais costuma ser engano
    de quem enviou, não intenção.

    O banco desfaz sozinho o que já tinha entrado quando um dos arquivos é
    recusado; o bucket, não. Por isso o que subiu para uma movimentação que não
    vingou é retirado de volta — um objeto sem dono não fica guardado.
    """
    lista = [arquivo for arquivo in (arquivos or []) if arquivo]

    if not lista:
        return []

    limite = limite_de_evidencias()
    if limite and len(lista) > limite:
        raise ErroDeEvidencia(
            f"Uma atualização aceita até {limite} arquivos. "
            "Envie os demais em outra atualização."
        )

    guardadas: list[EvidenciaDemanda] = []
    try:
        for arquivo in lista:
            guardadas.append(
                anexar_evidencia(
                    demanda=demanda, arquivo=arquivo, usuario=usuario, evento=evento
                )
            )
    except Exception:
        _descartar_do_bucket([item.storage_key for item in guardadas])
        raise

    return guardadas


def _descartar_do_bucket(chaves: list[str]) -> None:
    """Retira do bucket o que subiu para uma movimentação que não aconteceu."""
    if not chaves:
        return
    try:
        provider = provedor_das_evidencias()
    except (ErroDeStorage, ImproperlyConfigured):
        return
    for chave in chaves:
        try:
            provider.remover(key=chave)
        except (ErroDeStorage, ImproperlyConfigured):
            continue


def _nome_guardado(arquivo) -> str:
    """O nome que a pessoa deu ao arquivo, só para ela reconhecê-lo depois.

    É rótulo, não caminho: qualquer diretório que venha junto é descartado, e
    nada disso chega perto da chave do objeto no storage.
    """
    nome = (getattr(arquivo, "name", "") or "").replace("\\", "/")
    nome = nome.rsplit("/", 1)[-1]
    return " ".join(nome.split())[:255]
