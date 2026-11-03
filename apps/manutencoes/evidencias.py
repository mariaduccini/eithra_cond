"""As provas que sustentam o que aconteceu numa manutenção.

A evidência não é documento avulso nem acervo paralelo: ela se prende à
manutenção que já existe e serve à execução e à conferência. O que fica guardado
é a referência do objeto no bucket privado — nunca o arquivo em disco local nem
um endereço público. O acesso é gerado sob demanda.

O formato é conferido pelo conteúdo real, e não pela extensão nem pelo que o
navegador informou: nenhum dos dois é fonte de confiança. A foto usa a régua
compartilhada (``apps.core.imagem``); o vídeo, a mesma leitura de assinatura de
contêiner; o documento, a assinatura do PDF. Só entra o que a operação
realmente produz — não há suporte genérico a qualquer arquivo.

Duas datas, dois significados. ``enviada_em`` é quando o Eithra recebeu o
arquivo, escrita pelo servidor — é a referência confiável. ``capturada_em`` é
quando o fato foi registrado na origem, e só existe quando há uma fonte que o
sustente: a data que a própria imagem traz, lida de um EXIF mínimo. Sem esse
metadado, a captura fica vazia e a origem é declarada não identificada — o
Eithra não inventa horário.

Nada aqui decide o destino da manutenção. Anexar uma prova não inicia, não
conclui, não aprova nem reprova: quem decide é o serviço oficial do domínio.
"""
from __future__ import annotations

import logging
from datetime import datetime

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from apps.core.arquivos import (
    ASSINATURA_PDF,
    MIME_PDF,
    calcular_hash,
    formatar_tamanho,
    ler_conteudo,
)
from apps.core.imagem import tipo_da_imagem, validar_imagem
from apps.core.storage import ErroDeStorage, obter_provider

from .constants import (
    ContextoEvidencia,
    MIMES_DE_EVIDENCIA_MANUTENCAO,
    OrigemCaptura,
    TipoEvidenciaManutencao,
    TipoEventoManutencao,
)
from .models import EvidenciaManutencao, Manutencao
from .permissoes import pode_anexar_evidencia
from .services import _registrar

logger = logging.getLogger(__name__)

# O que a tela oferece no seletor de arquivo. É só a primeira triagem do
# navegador — o que decide é o conteúdo real, conferido logo abaixo.
ACEITE_DE_EVIDENCIA = ",".join(MIMES_DE_EVIDENCIA_MANUTENCAO)

# O que a recusa diz quando o arquivo não é uma prova aceita.
FORMATOS_ACEITOS = "JPG, PNG, WEBP, MP4, WEBM ou PDF"

# Assinatura real dos vídeos aceitos. MP4 é um contêiner ISO e traz a marca
# ``ftyp`` no quinto byte; WEBM é Matroska e começa pela assinatura própria.
MARCA_MP4 = b"ftyp"
POSICAO_MP4 = 4
ASSINATURA_WEBM = b"\x1a\x45\xdf\xa3"

# Quantos bytes bastam para reconhecer qualquer um dos formatos aceitos.
BYTES_DE_ABERTURA = 32


class ErroDeEvidencia(Exception):
    """A prova não pode ser guardada. A mensagem é humana."""


# -- Limites --------------------------------------------------------------------


def limite_da_foto() -> int:
    """Tamanho máximo aceito para uma foto, em bytes."""
    return int(getattr(settings, "MANUTENCOES_MAX_FOTO_BYTES", 0) or 0)


def limite_do_video() -> int:
    """Tamanho máximo aceito para um vídeo curto, em bytes."""
    return int(getattr(settings, "MANUTENCOES_MAX_VIDEO_BYTES", 0) or 0)


def limite_do_documento() -> int:
    """Tamanho máximo aceito para um documento, em bytes."""
    return int(getattr(settings, "MANUTENCOES_MAX_DOCUMENTO_BYTES", 0) or 0)


def limite_de_evidencias() -> int:
    """Quantos arquivos podem ser anexados numa mesma vez."""
    return int(getattr(settings, "MANUTENCOES_MAX_EVIDENCIAS_POR_ENVIO", 0) or 0)


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


def _abertura(arquivo) -> bytes:
    """Lê só o começo do arquivo — o suficiente para reconhecer o formato.

    Um vídeo pode ter dezenas de megabytes; reconhecê-lo não justifica carregar
    tudo em memória duas vezes.
    """
    arquivo.seek(0)
    return arquivo.read(BYTES_DE_ABERTURA)


def _tipo_informado(arquivo) -> str:
    """O tipo que o navegador declarou, sem parâmetros e em minúsculas."""
    return (getattr(arquivo, "content_type", "") or "").split(";")[0].strip().lower()


def validar_foto(arquivo) -> tuple[bytes, str, str]:
    """Confere a foto pela régua compartilhada e devolve o que guardar."""
    return validar_imagem(
        arquivo,
        limite_em_bytes=limite_da_foto(),
        erro=ErroDeEvidencia,
        sem_arquivo="Escolha a foto que sustenta este serviço.",
        assunto="foto",
    )


def validar_video(arquivo) -> tuple[bytes, str, str]:
    """Confere o vídeo e devolve o conteúdo, o tipo real e a extensão.

    Vale o mesmo critério da foto: o que decide é o começo do arquivo. Quando o
    navegador diz uma coisa e o arquivo é outra, recusar é a única resposta
    honesta — aceitar seria guardar um arquivo com um tipo que ele não tem.
    """
    if arquivo is None:
        raise ErroDeEvidencia("Escolha o vídeo que sustenta este serviço.")

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
    """Confere o documento e devolve o conteúdo, o tipo real e a extensão.

    Um relatório técnico ou uma ordem de serviço chega em PDF. A assinatura do
    arquivo é o que decide; a extensão e o tipo informado não decidem nada.
    """
    if arquivo is None:
        raise ErroDeEvidencia("Escolha o documento que sustenta este serviço.")

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
            "O arquivo não é um documento PDF aceito. Envie o relatório ou a "
            "ordem de serviço em PDF."
        )

    informado = _tipo_informado(arquivo)
    if informado and informado != MIME_PDF:
        raise ErroDeEvidencia(
            "O arquivo enviado não corresponde a um documento PDF."
        )

    return conteudo, MIME_PDF, "pdf"


def validar_evidencia(arquivo) -> tuple[bytes, str, str, str]:
    """Confere o arquivo e diz o que ele é: foto, vídeo ou documento.

    Devolve o conteúdo, o tipo real, a extensão e o tipo de mídia. O que não
    passa daqui não vira nem registro: não houve anexo, então não há o que
    guardar nem o que contar no histórico.
    """
    if arquivo is None:
        raise ErroDeEvidencia(
            "Escolha a foto, o vídeo ou o documento que sustenta este serviço."
        )

    abertura = _abertura(arquivo)

    if tipo_da_imagem(abertura) is not None:
        conteudo, mime, extensao = validar_foto(arquivo)
        return conteudo, mime, extensao, TipoEvidenciaManutencao.FOTO

    if tipo_do_video(abertura) is not None:
        conteudo, mime, extensao = validar_video(arquivo)
        return conteudo, mime, extensao, TipoEvidenciaManutencao.VIDEO

    if abertura.startswith(ASSINATURA_PDF):
        conteudo, mime, extensao = validar_documento(arquivo)
        return conteudo, mime, extensao, TipoEvidenciaManutencao.DOCUMENTO

    raise ErroDeEvidencia(
        "O arquivo não é uma evidência aceita. Envie uma foto, um vídeo curto "
        f"ou um documento em {FORMATOS_ACEITOS}."
    )


# -- A data informada pela imagem -----------------------------------------------


def data_da_captura(conteudo: bytes) -> datetime | None:
    """A data e a hora de captura que a imagem informa, quando ela as traz.

    Lê apenas o ``DateTimeOriginal`` do EXIF de um JPEG — o metadado que diz
    quando a foto foi tirada. Nada além disso é extraído: não há dump de EXIF,
    não há GPS, não há localização inferida. A data vem com a imagem e é tratada
    como o que é: informação declarada por ela, não prova absoluta. Quando não
    há metadado legível, a resposta é ``None`` — e o Eithra não inventa horário.
    """
    bloco = _bloco_exif(conteudo)
    if bloco is None:
        return None
    return _data_do_tiff(bloco)


def _bloco_exif(conteudo: bytes) -> bytes | None:
    """Encontra o segmento APP1 com EXIF num JPEG e devolve o bloco TIFF.

    Percorre os segmentos do arquivo pelo próprio tamanho declarado, sem varrer
    o conteúdo. Para no começo dos dados comprimidos: a partir dali não há mais
    cabeçalho a ler.
    """
    if not conteudo.startswith(b"\xff\xd8"):
        return None

    posicao = 2
    total = len(conteudo)
    while posicao + 4 <= total:
        if conteudo[posicao] != 0xFF:
            return None
        marcador = conteudo[posicao + 1]

        # Preenchimento: um 0xFF extra entre segmentos.
        if marcador == 0xFF:
            posicao += 1
            continue
        # Marcadores sem conteúdo: início de imagem e reinícios.
        if marcador == 0x01 or 0xD0 <= marcador <= 0xD9:
            posicao += 2
            continue
        # Começo dos dados comprimidos: o cabeçalho acabou.
        if marcador == 0xDA:
            return None

        tamanho = int.from_bytes(conteudo[posicao + 2 : posicao + 4], "big")
        if tamanho < 2:
            return None
        fim = posicao + 2 + tamanho
        if fim > total:
            return None

        if marcador == 0xE1:
            carga = conteudo[posicao + 4 : fim]
            if carga.startswith(b"Exif\x00\x00"):
                return carga[6:]
            # Outro APP1 (XMP, por exemplo): continua procurando.

        posicao = fim

    return None


def _data_do_tiff(bloco: bytes) -> datetime | None:
    """Lê o ``DateTimeOriginal`` do bloco TIFF, quando ele está lá."""
    if len(bloco) < 8:
        return None

    ordem = bloco[:2]
    if ordem == b"II":
        leitura = "little"
    elif ordem == b"MM":
        leitura = "big"
    else:
        return None

    if int.from_bytes(bloco[2:4], leitura) != 42:
        return None

    ifd0 = int.from_bytes(bloco[4:8], leitura)
    ifd_exif = _deslocamento_da_tag(bloco, ifd0, 0x8769, leitura)
    if ifd_exif is None:
        return None

    return _texto_da_tag(bloco, ifd_exif, 0x9003, leitura)


def _deslocamento_da_tag(bloco: bytes, inicio: int, alvo: int, leitura: str) -> int | None:
    """Onde está o valor de uma tag cujo conteúdo é um deslocamento."""
    if inicio <= 0 or inicio + 2 > len(bloco):
        return None

    quantidade = int.from_bytes(bloco[inicio : inicio + 2], leitura)
    posicao = inicio + 2
    for _ in range(quantidade):
        if posicao + 12 > len(bloco):
            return None
        tag = int.from_bytes(bloco[posicao : posicao + 2], leitura)
        if tag == alvo:
            return int.from_bytes(bloco[posicao + 8 : posicao + 12], leitura)
        posicao += 12

    return None


def _texto_da_tag(bloco: bytes, inicio: int, alvo: int, leitura: str) -> datetime | None:
    """O valor de uma tag de texto, lido como data e hora, quando legível."""
    if inicio <= 0 or inicio + 2 > len(bloco):
        return None

    quantidade = int.from_bytes(bloco[inicio : inicio + 2], leitura)
    posicao = inicio + 2
    for _ in range(quantidade):
        if posicao + 12 > len(bloco):
            return None
        tag = int.from_bytes(bloco[posicao : posicao + 2], leitura)
        tipo = int.from_bytes(bloco[posicao + 2 : posicao + 4], leitura)
        contagem = int.from_bytes(bloco[posicao + 4 : posicao + 8], leitura)

        # Tipo 2 é ASCII. O valor cabe nos quatro bytes quando é curto; fora
        # disso, esses quatro bytes são o deslocamento até o texto.
        if tag == alvo and tipo == 2:
            if contagem <= 4:
                bruto = bloco[posicao + 8 : posicao + 8 + contagem]
            else:
                deslocamento = int.from_bytes(bloco[posicao + 8 : posicao + 12], leitura)
                if deslocamento <= 0 or deslocamento + contagem > len(bloco):
                    return None
                bruto = bloco[deslocamento : deslocamento + contagem]
            return _momento_do_exif(bruto)

        posicao += 12

    return None


def _momento_do_exif(bruto: bytes) -> datetime | None:
    """Converte o texto "AAAA:MM:DD HH:MM:SS" numa data e hora do sistema.

    O EXIF não carrega fuso horário: a data é lida no fuso do projeto, como as
    demais datas informadas do domínio. Um texto que não é uma data válida não
    vira horário nenhum.
    """
    texto = bytes(bruto).split(b"\x00", 1)[0].decode("ascii", "ignore").strip()
    if not texto:
        return None
    try:
        ausente = datetime.strptime(texto, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    return timezone.make_aware(ausente)


def _metadados(conteudo: bytes, mime: str) -> tuple[datetime | None, str]:
    """A data de captura e a sua origem, a partir do que a evidência permite.

    Só a imagem pode informar quando o fato aconteceu. Quando ela não traz o
    metadado — porque o arquivo perdeu o EXIF ao passar por um aplicativo ou
    por uma exportação — a captura fica vazia e a origem é não identificada:
    perder o metadado não recusa a prova.
    """
    if not mime.startswith("image/"):
        return None, OrigemCaptura.NAO_IDENTIFICADA

    momento = data_da_captura(conteudo)
    if momento is None:
        return None, OrigemCaptura.NAO_IDENTIFICADA
    return momento, OrigemCaptura.EXIF


# -- Onde o arquivo fica --------------------------------------------------------


def montar_chave(*, condominio, manutencao, evidencia, extensao: str) -> str:
    """Caminho do objeto dentro do bucket privado.

    Só identificadores: o nome que a pessoa deu ao arquivo não entra aqui. O
    caminho não carrega dado pessoal, não depende do navegador e não permite
    escapar da pasta do condomínio.
    """
    return (
        f"manutencoes/{condominio.pk}/{manutencao.pk}/evidencias/"
        f"{evidencia.pk}.{extensao}"
    )


def provedor_das_evidencias():
    """O provedor no bucket privado das evidências de manutenção.

    O bucket é do domínio: se ele não estiver definido no ambiente, o envio é
    recusado — não existe queda silenciosa para disco local.
    """
    return obter_provider(
        bucket=getattr(settings, "MANUTENCOES_STORAGE_BUCKET", ""),
        nome_do_bucket="MANUTENCOES_STORAGE_BUCKET",
    )


def acesso_temporario(evidencia, *, validade_segundos: int | None = None) -> str:
    """Devolve um acesso curto ao arquivo, gerado na hora — nunca guardado."""
    if not evidencia.storage_key:
        raise ErroDeStorage("O arquivo desta evidência não está disponível.")

    segundos = int(
        validade_segundos
        or getattr(settings, "MANUTENCOES_URL_ACESSO_SEGUNDOS", 60)
        or 60
    )
    return provedor_das_evidencias().url_assinada(
        key=evidencia.storage_key, validade_segundos=segundos
    )


# -- Anexar ---------------------------------------------------------------------


@transaction.atomic
def adicionar_evidencia(
    *,
    manutencao,
    arquivo,
    usuario,
    contexto: str = ContextoEvidencia.GERAL,
) -> EvidenciaManutencao:
    """Guarda uma prova presa à manutenção, de forma idempotente.

    Este é o portal único de escrita da evidência: a autorização, a conferência
    do arquivo, o cálculo do tamanho e do resumo, a leitura da data informada
    pela imagem, a guarda no bucket, a gravação do registro e o evento no
    histórico acontecem aqui — e em nenhum outro lugar. Nenhuma view grava
    evidência por conta própria.

    O arquivo já conferido vai para o bucket antes de o registro existir, para
    que uma falha de armazenamento não deixe uma evidência registrada sem
    conteúdo guardado; e a conferência do modelo vem antes do envio, para que o
    que não passa não chegue a virar objeto no bucket. Se a gravação falhar
    depois do envio, o objeto recém-criado é removido — o banco não guarda
    registro parcial e o bucket não guarda arquivo sem dono.

    Reenviar o mesmo arquivo à mesma manutenção não cria uma segunda prova: a
    que já existe é devolvida, e nada novo é gravado nem contado.
    """
    bloqueada = (
        Manutencao.objects.select_for_update()
        .select_related("condominio")
        .get(pk=manutencao.pk)
    )

    if not pode_anexar_evidencia(usuario, bloqueada):
        raise ErroDeEvidencia("Você não pode anexar evidência a esta manutenção.")

    conteudo, mime, extensao, tipo_midia = validar_evidencia(arquivo)
    sha256 = calcular_hash(conteudo)

    existente = EvidenciaManutencao.objects.filter(
        manutencao=bloqueada, sha256=sha256
    ).first()
    if existente is not None:
        return existente

    capturada_em, origem_captura = _metadados(conteudo, mime)

    evidencia = EvidenciaManutencao(
        manutencao=bloqueada,
        contexto=contexto,
        tipo_midia=tipo_midia,
        nome_original=_nome_guardado(arquivo),
        mime_type=mime,
        tamanho_bytes=len(conteudo),
        sha256=sha256,
        capturada_em=capturada_em,
        origem_captura=origem_captura,
        storage_key="",  # provisório: a chave depende do identificador
        enviada_por=usuario,
    )
    evidencia.storage_key = montar_chave(
        condominio=bloqueada.condominio,
        manutencao=bloqueada,
        evidencia=evidencia,
        extensao=extensao,
    )

    # A conferência vem antes do envio: o que não passa no modelo não chega a
    # virar objeto no bucket.
    evidencia.full_clean()
    provider = provedor_das_evidencias()
    provider.enviar(key=evidencia.storage_key, conteudo=conteudo, content_type=mime)

    # Daqui em diante o objeto já está no bucket. Não existe transação que cubra
    # banco e storage: se a gravação do registro ou do histórico falhar, a
    # transação volta atrás e o arquivo recém-enviado é desfeito junto, para não
    # ficar órfão. Só o objeto criado por esta tentativa é removido — quem já
    # existia por idempotência saiu antes, no retorno acima.
    try:
        evidencia.storage_provider = provider.nome
        evidencia.storage_bucket = provider.bucket
        evidencia.save()
        _registrar(
            bloqueada,
            usuario,
            TipoEventoManutencao.EVIDENCIA_ADICIONADA,
            _frase_do_evento(evidencia),
            novos=_resumo_para_o_historico(evidencia),
        )
    except Exception:
        _desfazer_envio(provider, evidencia.storage_key)
        raise

    return evidencia


def _desfazer_envio(provider, chave: str) -> None:
    """Remove o objeto enviado quando a gravação falhou depois dele.

    A limpeza é o melhor esforço: se o próprio storage — ou a configuração —
    recusar a remoção, o objeto fica órfão no bucket e é recolhido depois. O que
    não pode acontecer é a falha de limpeza substituir a causa real: por isso o
    erro original segue adiante, e este registra apenas o que sobrou.
    """
    if not chave:
        return
    try:
        provider.remover(key=chave)
    except (ErroDeStorage, ImproperlyConfigured, OSError):
        logger.exception(
            "Não foi possível desfazer o arquivo de uma evidência que não chegou "
            "a ser registrada; o objeto pode ter ficado no armazenamento."
        )


def _nome_guardado(arquivo) -> str:
    """O nome que a pessoa deu ao arquivo, só para ela reconhecê-lo depois.

    É rótulo, não caminho: qualquer diretório que venha junto é descartado, e
    nada disso chega perto da chave do objeto no storage.
    """
    nome = (getattr(arquivo, "name", "") or "").replace("\\", "/")
    nome = nome.rsplit("/", 1)[-1]
    return " ".join(nome.split())[:255]


def _frase_do_evento(evidencia) -> str:
    """Como o anexo é lembrado no histórico, em linguagem humana."""
    nome = evidencia.nome_original or "arquivo"
    return f"Evidência anexada: {nome}"


def _resumo_para_o_historico(evidencia) -> dict:
    """Só os metadados úteis da prova — sem identificador técnico.

    O resumo do conteúdo fica aqui porque o histórico é interno: ele serve à
    auditoria e ao reconhecimento do mesmo arquivo, e nunca é mostrado como
    identificador.
    """
    return {
        "nome": evidencia.nome_original,
        "tipo": evidencia.tipo_midia,
        "mime": evidencia.mime_type,
        "tamanho": evidencia.tamanho_bytes,
        "sha256": evidencia.sha256,
        "capturada_em": (
            evidencia.capturada_em.isoformat() if evidencia.capturada_em else None
        ),
        "origem_captura": evidencia.origem_captura,
        "contexto": evidencia.contexto,
    }


__all__ = [
    "ErroDeEvidencia",
    "ACEITE_DE_EVIDENCIA",
    "FORMATOS_ACEITOS",
    "adicionar_evidencia",
    "acesso_temporario",
    "data_da_captura",
    "limite_da_foto",
    "limite_do_documento",
    "limite_do_video",
    "limite_de_evidencias",
    "limites_legiveis",
    "montar_chave",
    "provedor_das_evidencias",
    "tipo_do_video",
    "validar_evidencia",
]
