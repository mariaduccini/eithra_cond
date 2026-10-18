"""Evidências visuais do balanço.

Uma foto explica algo do mês: uma melhoria feita, um gasto fora da rotina, o
antes e o depois de um reparo. Ela é complementar — não substitui lançamento,
documento, nota fiscal nem justificativa — e por isso nunca existe solta: toda
evidência está presa a um lançamento, a uma categoria do balanço ou ao destaque
do mês. Sem contexto financeiro, a imagem não entra.

O arquivo vai para o mesmo bucket privado dos documentos, e aqui fica apenas a
referência do objeto. A imagem é conferida pelo conteúdo real, e não pela
extensão nem pelo que o navegador informou: nenhum dos dois é fonte de
confiança. O acesso é temporário e gerado na hora, como o do PDF original.

Duas decisões de convivência com a correção manual:

* o vínculo com um lançamento usa o identificador estável da Fase 1H — o mesmo
  que a tela já mostra —, e o identificador é conferido contra o balanço: o que
  vem do navegador é candidato, não é fato;
* anexar foto **não** grava nada no detalhamento do mês. A evidência é um
  registro próprio, com ciclo de vida próprio, e o balanço continua sendo o que
  a gestão confirmou e corrigiu.
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from apps.balancos import correcoes
from apps.balancos.categorias import CategoriaEithra
from apps.balancos.constants import StatusBalanco, TipoVinculoEvidencia
from apps.balancos.contratos import de_dicionario_validado
from apps.balancos.models import BalancoMensal, EvidenciaVisualBalanco
from apps.balancos.services import calcular_hash, formatar_tamanho, ler_conteudo
from apps.balancos.storage import ErroDeStorage, obter_provider

# A evidência ilustra a despesa; não vira álbum. Três fotos contam a história de
# uma melhoria — a quarta já é acervo.
LIMITE_DE_FOTOS_POR_VINCULO = 3

# O destaque do mês é uma coisa só: no máximo uma foto nesta primeira versão.
LIMITE_DE_FOTOS_NO_DESTAQUE = 1

# A legenda é uma frase sobre a imagem, não um relatório ao lado dela.
LIMITE_DA_LEGENDA = 200

# Assinatura real de cada imagem aceita. Extensão e tipo informado pelo
# navegador não provam nada: o que vale é o começo do arquivo.
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


class ErroDeEvidencia(Exception):
    """A evidência não pode ser guardada. A mensagem é para a gestão."""


# -- O arquivo ------------------------------------------------------------------


def limite_da_foto() -> int:
    """Tamanho máximo aceito para uma foto de evidência, em bytes."""
    return int(getattr(settings, "BALANCOS_MAX_FOTO_BYTES", 0) or 0)


def tipo_da_imagem(conteudo: bytes) -> tuple[str, str] | None:
    """O tipo e a extensão da imagem, lidos do próprio arquivo.

    Devolve ``None`` quando o conteúdo não é nenhuma das imagens aceitas — o que
    inclui o PDF, que nesta funcionalidade é recusado.
    """
    for mime, assinatura, extensao in ASSINATURAS:
        if conteudo.startswith(assinatura):
            return mime, extensao

    if conteudo[:4] == MARCA_RIFF and conteudo[8:12] == MARCA_WEBP:
        return MIME_WEBP, EXTENSAO_WEBP

    return None


def validar_foto(arquivo) -> tuple[bytes, str, str]:
    """Confere a foto e devolve o que dela precisa ser guardado.

    Devolve o conteúdo, o tipo real e a extensão. Um arquivo que não passa daqui
    não vira nem registro: não houve anexo, então não há o que guardar.
    """
    if arquivo is None:
        raise ErroDeEvidencia("Escolha a foto que explica este gasto.")

    tamanho = int(getattr(arquivo, "size", 0) or 0)
    if tamanho <= 0:
        raise ErroDeEvidencia("A foto está vazia.")

    limite = limite_da_foto()
    if limite and tamanho > limite:
        raise ErroDeEvidencia(
            f"A foto tem {formatar_tamanho(tamanho)} e passa do limite de "
            f"{formatar_tamanho(limite)}."
        )

    conteudo = ler_conteudo(arquivo)
    detectado = tipo_da_imagem(conteudo)
    if detectado is None:
        raise ErroDeEvidencia(
            "O arquivo não é uma imagem aceita. Envie uma foto JPG, PNG ou WEBP."
        )
    mime, extensao = detectado

    informado = (
        (getattr(arquivo, "content_type", "") or "").split(";")[0].strip().lower()
    )
    if informado and informado != mime:
        # O navegador disse uma coisa e o arquivo é outra. Recusar é a única
        # resposta honesta: repassar o tipo informado seria guardar um arquivo
        # com um tipo que ele não tem.
        raise ErroDeEvidencia(
            "O arquivo enviado não corresponde a uma imagem aceita. Envie uma "
            "foto JPG, PNG ou WEBP."
        )

    return conteudo, mime, extensao


def montar_chave(*, condominio, balanco, evidencia, extensao: str) -> str:
    """Caminho do objeto dentro do bucket privado.

    Só identificadores: o nome que a pessoa deu ao arquivo não entra aqui. O
    caminho não carrega dado pessoal e não depende do que o navegador enviou.
    """
    return (
        f"balancos/{condominio.pk}/{balanco.pk}/evidencias/{evidencia.pk}.{extensao}"
    )


def acesso_temporario(evidencia, *, validade_segundos: int | None = None) -> str:
    """Devolve um acesso curto à foto, gerado na hora — nunca guardado."""
    if not evidencia.storage_key:
        raise ErroDeStorage("A foto desta evidência não está disponível.")

    segundos = int(
        validade_segundos
        or getattr(settings, "BALANCOS_URL_ACESSO_SEGUNDOS", 60)
        or 60
    )
    return obter_provider().url_assinada(
        key=evidencia.storage_key, validade_segundos=segundos
    )


# -- Anexar, editar, remover ----------------------------------------------------


def anexar_evidencia(
    balanco,
    *,
    tipo_vinculo: str,
    referencia: str,
    arquivo,
    legenda: str = "",
    exibir_aos_moradores: bool = False,
    usuario,
) -> EvidenciaVisualBalanco:
    """Guarda uma foto presa a um contexto do mês, em uma transação.

    O vínculo é conferido contra o balanço — o identificador que a tela mandou é
    candidato, e só vale se existir aqui. A foto é conferida pelo conteúdo. O
    objeto vai para o bucket antes de o registro existir, para que uma falha de
    armazenamento não deixe uma evidência sem imagem.

    ``exibir_aos_moradores`` não publica nada: apenas marca que a gestão assume a
    imagem como elegível para uma transparência futura.
    """
    with transaction.atomic():
        bloqueado = (
            BalancoMensal.objects.select_for_update()
            .select_related("condominio")
            .get(pk=balanco.pk)
        )
        _recusar_se_nao_gerenciavel(bloqueado)

        referencia = _vinculo_conferido(
            bloqueado, tipo_vinculo=tipo_vinculo, referencia=referencia
        )

        conteudo, mime, extensao = validar_foto(arquivo)
        sha256 = calcular_hash(conteudo)

        _recusar_repeticao(
            bloqueado, tipo_vinculo=tipo_vinculo, referencia=referencia, sha256=sha256
        )
        _recusar_excesso(
            bloqueado, tipo_vinculo=tipo_vinculo, referencia=referencia
        )

        evidencia = EvidenciaVisualBalanco(
            balanco=bloqueado,
            tipo_vinculo=tipo_vinculo,
            referencia=referencia,
            storage_key="",  # provisório: a chave depende do identificador
            mime_type=mime,
            tamanho_bytes=len(conteudo),
            sha256=sha256,
            legenda=normalizar_legenda(legenda),
            exibir_aos_moradores=bool(exibir_aos_moradores),
            criado_por=usuario,
        )
        evidencia.storage_key = montar_chave(
            condominio=bloqueado.condominio,
            balanco=bloqueado,
            evidencia=evidencia,
            extensao=extensao,
        )

        # A conferência vem antes do envio: o que não passa no modelo não chega
        # a virar objeto no bucket.
        evidencia.full_clean()
        provider = obter_provider()
        provider.enviar(key=evidencia.storage_key, conteudo=conteudo, content_type=mime)
        evidencia.storage_provider = provider.nome
        evidencia.storage_bucket = provider.bucket
        evidencia.save()

    return evidencia


def editar_evidencia(
    balanco,
    *,
    evidencia_id,
    legenda: str,
    exibir_aos_moradores: bool,
) -> EvidenciaVisualBalanco:
    """Muda a legenda ou a decisão de exibir. A foto em si não é substituída.

    Para trocar a imagem, a gestão anexa outra e remove a anterior: versionar
    evidência é complexidade sem uso nesta fase.
    """
    with transaction.atomic():
        bloqueado = _bloqueado(balanco)
        evidencia = _ativa(bloqueado, evidencia_id)

        evidencia.legenda = normalizar_legenda(legenda)
        evidencia.exibir_aos_moradores = bool(exibir_aos_moradores)
        evidencia.full_clean()
        evidencia.save(
            update_fields=["legenda", "exibir_aos_moradores", "atualizado_em"]
        )

    return evidencia


def remover_evidencia(balanco, *, evidencia_id) -> None:
    """Tira a foto do mês sem apagar o registro do que já existiu.

    A evidência fica inativa e com a data da remoção: o histórico do mês não
    perde a explicação que um dia esteve lá. O objeto é removido do bucket pela
    mesma política do descarte de importação — se o armazenamento falhar, a
    remoção continua valendo e o objeto pode ser limpo depois; o que não pode é
    a gestão ficar presa a uma foto que decidiu tirar.
    """
    with transaction.atomic():
        bloqueado = _bloqueado(balanco)
        evidencia = _ativa(bloqueado, evidencia_id)

        _remover_objeto(evidencia)
        evidencia.ativo = False
        evidencia.removido_em = timezone.now()
        evidencia.save(update_fields=["ativo", "removido_em", "atualizado_em"])


def normalizar_legenda(texto: str) -> str:
    """Deixa a legenda limpa. Curta e opcional — não é campo de relatório."""
    legenda = " ".join((texto or "").split())
    if len(legenda) > LIMITE_DA_LEGENDA:
        raise ErroDeEvidencia(
            f"A legenda é longa demais. Resuma em até {LIMITE_DA_LEGENDA} caracteres."
        )
    return legenda


# -- Conferências ---------------------------------------------------------------


def _bloqueado(balanco) -> BalancoMensal:
    """O balanço travado durante a transação, para a contagem não correr solta."""
    bloqueado = (
        BalancoMensal.objects.select_for_update()
        .select_related("condominio")
        .get(pk=balanco.pk)
    )
    _recusar_se_nao_gerenciavel(bloqueado)
    return bloqueado


def _recusar_se_nao_gerenciavel(balanco) -> None:
    """Só o mês confirmado e em vigor recebe evidência."""
    if balanco.status != StatusBalanco.VALIDADO:
        raise ErroDeEvidencia(
            "Só um balanço confirmado e em vigor pode receber fotos."
        )


def _vinculo_conferido(balanco, *, tipo_vinculo: str, referencia: str) -> str:
    """Confere o vínculo contra o balanço e devolve a referência que vale.

    O destaque do mês não tem referência: ele é o próprio mês. Os demais vínculos
    precisam alcançar alguma coisa que exista aqui dentro — um lançamento do
    detalhamento ou uma categoria do catálogo do Eithra.
    """
    if tipo_vinculo not in set(TipoVinculoEvidencia.values):
        raise ErroDeEvidencia("Escolha a que esta foto está ligada.")

    referencia = (referencia or "").strip()

    if tipo_vinculo == TipoVinculoEvidencia.DESTAQUE:
        if referencia:
            raise ErroDeEvidencia(
                "O destaque do mês não é um lançamento: não há referência a guardar."
            )
        return ""

    if not referencia:
        raise ErroDeEvidencia("Escolha a que esta foto está ligada.")

    if tipo_vinculo == TipoVinculoEvidencia.CATEGORIA:
        if referencia not in set(CategoriaEithra.values):
            raise ErroDeEvidencia("Esta categoria não faz parte do balanço.")
        return referencia

    if not _lancamento_existe(balanco, referencia):
        raise ErroDeEvidencia("Este lançamento não faz parte do balanço.")

    return referencia


def _lancamento_existe(balanco, identificador: str) -> bool:
    """Se o lançamento está no detalhamento válido deste balanço.

    O identificador é o mesmo que a tela mostra, e é reconstruído do conteúdo do
    lançamento. Um identificador inventado não encontra nada e não autoriza
    vínculo nenhum.
    """
    leitura = de_dicionario_validado(balanco.dados_validados).leitura or {}
    return any(
        item["id"] == identificador for item in correcoes.despesas_efetivas(leitura)
    )


def _recusar_repeticao(balanco, *, tipo_vinculo, referencia, sha256) -> None:
    """A mesma imagem não entra duas vezes no mesmo vínculo.

    O mesmo arquivo em contextos diferentes é permitido: a mesma foto pode
    explicar duas despesas distintas sem virar erro.
    """
    repetida = EvidenciaVisualBalanco.objects.filter(
        balanco=balanco,
        tipo_vinculo=tipo_vinculo,
        referencia=referencia,
        sha256=sha256,
        ativo=True,
    ).exists()
    if repetida:
        raise ErroDeEvidencia("Esta foto já está anexada aqui.")


def _recusar_excesso(balanco, *, tipo_vinculo, referencia) -> None:
    """Segura o limite de fotos por vínculo antes de gravar qualquer coisa."""
    limite = (
        LIMITE_DE_FOTOS_NO_DESTAQUE
        if tipo_vinculo == TipoVinculoEvidencia.DESTAQUE
        else LIMITE_DE_FOTOS_POR_VINCULO
    )
    ativas = EvidenciaVisualBalanco.objects.filter(
        balanco=balanco,
        tipo_vinculo=tipo_vinculo,
        referencia=referencia,
        ativo=True,
    ).count()
    if ativas >= limite:
        if limite == 1:
            raise ErroDeEvidencia("O destaque do mês já tem uma foto.")
        raise ErroDeEvidencia(
            f"Cada vínculo aceita até {limite} fotos. Remova uma antes de "
            "anexar outra."
        )


def _ativa(balanco, evidencia_id) -> EvidenciaVisualBalanco:
    """A evidência ativa deste balanço, ou a recusa em linguagem humana."""
    evidencia = EvidenciaVisualBalanco.objects.filter(
        balanco=balanco, pk=evidencia_id, ativo=True
    ).first()
    if evidencia is None:
        raise ErroDeEvidencia("Esta foto já não está no balanço.")
    return evidencia


def _remover_objeto(evidencia) -> None:
    """Remove a foto do bucket sem tornar a operação refém do armazenamento."""
    if not evidencia.storage_key:
        return

    try:
        obter_provider().remover(key=evidencia.storage_key)
    except (ErroDeStorage, ImproperlyConfigured):
        return
