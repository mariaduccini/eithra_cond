"""Recebimento do arquivo de balanço: do PDF ao registro transitório.

Este é o caminho por onde entra um documento. Nada aqui cria balanço: o que
nasce é uma ``ImportacaoBalanco`` — uma tentativa, ainda não confirmada. Só a
confirmação da gestão faz o mês valer, e ela também mora aqui: é este módulo
que transforma uma importação pronta no ``BalancoMensal`` do mês.

O upload não mora na view nem em ``save()``, e não há signal escondido: a
sequência é explícita e cada passo tem um nome. Quem chama é que decide o que
dizer ao usuário.

Duas coisas diferentes são guardadas em dois lugares diferentes:

- o banco guarda o registro e a referência do objeto;
- o bucket privado guarda o arquivo.

Não existe transação que cubra os dois. Se o arquivo for enviado e a gravação
seguinte falhar, o envio é desfeito na medida do possível; se o arquivo não for
enviado, a tentativa fica registrada como não recebida, e não como sucesso.

Já a confirmação é uma transação só, e é o único ponto do sistema onde um
``BalancoMensal`` nasce.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import DatabaseError, transaction
from django.utils import timezone

from apps.balancos.constants import (
    LayoutBalanco,
    ProcessamentoBalanco,
    StatusBalanco,
    StatusImportacao,
)
from apps.balancos.contratos import (
    SCHEMA_VERSAO,
    BalancoExtraidoV1,
    BalancoValidadoV1,
    ErroDeContrato,
    TipoInconsistencia,
    de_dicionario,
    para_dicionario_validado,
    validar_dicionario,
    validar_dicionario_validado,
)
from apps.balancos.detector import ErroDeDeteccaoDeLayout, detectar_layout_balanco
from apps.balancos.extrator_atual import ErroDeExtracao, extrair_balanco_atual
from apps.balancos.models import BalancoMensal, DocumentoBalanco, ImportacaoBalanco
from apps.balancos.normalizadores import decimal_para_texto, texto_para_decimal
from apps.balancos.storage import ErroDeStorage, obter_provider
from apps.balancos.validacao_financeira import TOLERANCIA

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
    com o hash do arquivo, a referência do objeto e — quando o documento é do
    modelo atual — os dados financeiros lidos da camada textual do PDF.
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

    if importacao.layout_detectado == LayoutBalanco.ATUAL_CANONICO:
        # O documento é do modelo atual: os dados financeiros são lidos agora,
        # enquanto o arquivo já está em mãos — sem um segundo acesso ao storage.
        # Legado e desconhecido seguem sem leitura: o motivo fica para a gestão.
        try:
            extrair_balanco_atual(importacao, conteudo=conteudo, provider=provider)
        except ErroDeExtracao:
            # A leitura falhou e já registrou o motivo na importação. O arquivo
            # continua no bucket: a tentativa pode ser refeita sem novo envio.
            importacao.refresh_from_db()
            return ResultadoDoRecebimento(importacao=importacao)

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
    registro e o objeto pode ser limpo depois. O descarte em si já está gravado.
    Nada disso vira erro para a gestão.
    """
    if not importacao.storage_key:
        return

    try:
        obter_provider().remover(key=importacao.storage_key)
    except (ErroDeStorage, ImproperlyConfigured):
        return


# -- Confirmação: a importação vira balanço ------------------------------------
#
# Confirmar não é exigir que todo lançamento tenha sido compreendido. O que o
# documento declara como total oficial continua sendo o valor oficial do mês, e
# o quanto desse total ficou sem detalhe associado aparece ao lado — não é
# escondido nem corrigido para o mês "fechar".


class ErroDeConfirmacao(Exception):
    """A importação não pode virar balanço agora. A mensagem é para a gestão."""


@dataclass(frozen=True)
class DetalhamentoDasDespesas:
    """Quanto do total declarado ficou preso ao detalhe que a leitura achou.

    ``valor_nao_associado`` é a parte do total declarado que não ficou presa a
    nenhum lançamento lido. Quando a leitura encontra mais do que o documento
    declara, ele fica negativo — e o sinal, em vez de escondido, é justamente o
    que diz que o detalhe passou do declarado.
    """

    total_declarado: str | None
    total_identificado: str
    valor_nao_associado: str
    percentual: str | None


def medir_detalhamento(balanco: BalancoExtraidoV1) -> DetalhamentoDasDespesas:
    """Mede a distância entre o total declarado e o detalhe identificado.

    Não soma nada ao total e não estima nada: só compara dois números que o
    documento e a leitura já deram, separadamente.
    """
    declarado = texto_para_decimal(balanco.despesas_total)
    identificado = texto_para_decimal(balanco.soma_despesas_extraida) or Decimal("0.00")

    if declarado is None or declarado <= 0:
        # Sem total declarado não há contra o que comparar; a ausência continua
        # sendo ausência, e não vira 0%.
        return DetalhamentoDasDespesas(
            total_declarado=decimal_para_texto(declarado),
            total_identificado=decimal_para_texto(identificado),
            valor_nao_associado="0.00",
            percentual=None,
        )

    percentual = (identificado / declarado * 100).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return DetalhamentoDasDespesas(
        total_declarado=decimal_para_texto(declarado),
        total_identificado=decimal_para_texto(identificado),
        valor_nao_associado=decimal_para_texto(declarado - identificado),
        percentual=decimal_para_texto(percentual),
    )


def pode_confirmar(importacao) -> tuple[bool, str]:
    """Diz se esta importação pode virar balanço, e por que não, quando não pode.

    A resposta é do domínio, não da tela: o botão só aparece quando esta função
    concorda, mas quem decide continua sendo ela.
    """
    if importacao.confirmado_em:
        return False, "Esta importação já foi confirmada."
    if importacao.descartado_em or importacao.status == StatusImportacao.DESCARTADO:
        return False, "Uma importação descartada não vira balanço."
    if importacao.status != StatusImportacao.PRONTO:
        return False, "A leitura deste documento ainda não terminou."
    if importacao.layout_detectado != LayoutBalanco.ATUAL_CANONICO:
        return False, "Este documento ainda não foi lido pelo Eithra."
    if not importacao.resultado_extraido:
        return False, "A leitura deste documento não produziu dados."

    try:
        validar_dicionario(importacao.resultado_extraido)
    except ErroDeContrato:
        return False, "A leitura deste documento não passou na conferência."

    balanco = de_dicionario(importacao.resultado_extraido)
    if not balanco.competencia:
        return False, "A competência do documento não foi identificada."
    if not balanco.receitas_total:
        return False, "O total de receitas do documento não foi identificado."
    if not balanco.despesas_total:
        return False, "O total de despesas do documento não foi identificado."

    return True, ""


def confirmar_importacao(importacao, *, usuario) -> BalancoMensal:
    """Transforma uma importação pronta no balanço do mês, em uma transação.

    É o único ponto do sistema onde um ``BalancoMensal`` nasce. Ou tudo
    acontece — balanço, documento e desfecho da importação —, ou nada fica:
    uma confirmação pela metade seria pior do que não confirmar.

    Confirmar duas vezes não cria um segundo balanço: a segunda chamada devolve
    o balanço que já existe. E uma competência que já tem versão em vigor não é
    sobrescrita — o sistema informa e espera uma decisão explícita sobre versão,
    que não é tomada aqui.
    """
    with transaction.atomic():
        bloqueada = (
            ImportacaoBalanco.objects.select_for_update()
            .select_related("condominio")
            .get(pk=importacao.pk)
        )

        if bloqueada.confirmado_em:
            existente = _versao_vigente(bloqueada.condominio, _competencia_da(bloqueada))
            if existente is None:
                raise ErroDeConfirmacao("Esta importação já foi confirmada.")
            return existente

        confirma, motivo = pode_confirmar(bloqueada)
        if not confirma:
            raise ErroDeConfirmacao(motivo)

        competencia = _competencia_da(bloqueada)
        if _versao_vigente(bloqueada.condominio, competencia) is not None:
            raise ErroDeConfirmacao(
                "Já existe balanço confirmado para esta competência."
            )

        agora = timezone.now()
        balanco = _criar_balanco(bloqueada, competencia, usuario=usuario, agora=agora)
        _criar_documento(balanco, bloqueada)

        bloqueada.status = StatusImportacao.CONFIRMADO
        bloqueada.confirmado_em = agora
        bloqueada.save(update_fields=["status", "confirmado_em", "atualizado_em"])

    return balanco


def _competencia_da(importacao) -> date | None:
    """A competência que a leitura identificou, quando ela existe."""
    return _data_iso((importacao.resultado_extraido or {}).get("competencia"))


def _data_iso(texto) -> date | None:
    if not texto:
        return None
    try:
        return date.fromisoformat(texto)
    except (TypeError, ValueError):
        return None


def _versao_vigente(condominio, competencia) -> BalancoMensal | None:
    """A versão em vigor daquela competência, se houver.

    Uma versão substituída não está em vigor: ela é memória, não o mês atual.
    """
    if competencia is None:
        return None
    return (
        BalancoMensal.objects.filter(condominio=condominio, competencia=competencia)
        .exclude(status=StatusBalanco.SUBSTITUIDO)
        .first()
    )


def _criar_balanco(importacao, competencia, *, usuario, agora) -> BalancoMensal:
    """Monta e grava o balanço do mês, com os totais oficiais do documento."""
    lido = de_dicionario(importacao.resultado_extraido)
    detalhamento = medir_detalhamento(lido)
    inconsistencias = list(importacao.inconsistencias or [])

    validado = BalancoValidadoV1(
        competencia=lido.competencia,
        receitas_total=lido.receitas_total,
        despesas_total=lido.despesas_total,
        saldo_anterior=lido.saldo_anterior,
        saldo_final=lido.saldo_final,
        total_declarado_despesas=detalhamento.total_declarado,
        total_detalhado_identificado=detalhamento.total_identificado,
        valor_nao_associado=detalhamento.valor_nao_associado,
        percentual_detalhamento=detalhamento.percentual,
        confirmado_com_ressalvas=_tem_ressalvas(detalhamento, inconsistencias),
        confirmado_em=agora.isoformat(),
        leitura=importacao.resultado_extraido,
    )
    dados_validados = para_dicionario_validado(validado)
    validar_dicionario_validado(dados_validados)

    balanco = BalancoMensal(
        condominio=importacao.condominio,
        competencia=competencia,
        versao=1,
        status=StatusBalanco.VALIDADO,
        status_processamento=ProcessamentoBalanco.CONCLUIDO,
        layout_detectado=LayoutBalanco.ATUAL_CANONICO,
        schema_versao=SCHEMA_VERSAO,
        dados_extraidos=importacao.resultado_extraido,
        dados_validados=dados_validados,
        inconsistencias=inconsistencias,
        resumo=_resumo_do_balanco(lido, validado, inconsistencias),
        criado_por=usuario,
        validado_por=usuario,
        validado_em=agora,
    )
    _gravar(balanco)
    return balanco


def _criar_documento(balanco, importacao) -> DocumentoBalanco:
    """Referencia o arquivo já recebido — sem enviar uma segunda cópia.

    O objeto no bucket é o mesmo da importação: guardar outro seria duplicar
    arquivo sem motivo. O que se acrescenta aqui é o vínculo com o balanço.
    """
    documento = DocumentoBalanco(
        balanco=balanco,
        nome_original=importacao.nome_original,
        storage_provider=importacao.storage_provider,
        storage_bucket=importacao.storage_bucket,
        storage_key=importacao.storage_key,
        mime_type=importacao.mime_type,
        tamanho_bytes=importacao.tamanho_bytes,
        sha256=importacao.sha256,
        enviado_por=importacao.criado_por,
        enviado_em=importacao.criado_em,
    )
    _gravar(documento)
    return documento


def _gravar(instancia) -> None:
    """Confere a instância e grava: o que não passa na conferência não entra."""
    try:
        instancia.full_clean()
    except ValidationError as erro:
        raise ErroDeConfirmacao(
            "O balanço não passou na conferência e não foi confirmado."
        ) from erro
    instancia.save()


def _tem_ressalvas(detalhamento: DetalhamentoDasDespesas, inconsistencias) -> bool:
    """Se o balanço passa a valer com algo ainda em aberto.

    A confirmação não remove inconsistência: ela assume o que ficou apontado. O
    resíduo entre o declarado e o identificado continua sendo dito, e a
    divergência de total apontada na leitura continua apontada.
    """
    diferenca = texto_para_decimal(detalhamento.valor_nao_associado) or Decimal("0.00")
    if abs(diferenca) > TOLERANCIA:
        return True
    return any(
        item.get("tipo") == TipoInconsistencia.TOTAL_DIVERGENTE
        for item in (inconsistencias or [])
    )


def _resumo_do_balanco(lido, validado: BalancoValidadoV1, inconsistencias) -> dict:
    """Agregados que a listagem e a comparação entre meses usam.

    É resumo, não o balanço: o estado oficial completo está em ``dados_validados``.
    """
    return {
        "competencia": validado.competencia,
        "versao": 1,
        "receitas_total": validado.receitas_total,
        "despesas_total": validado.despesas_total,
        "saldo_anterior": validado.saldo_anterior,
        "saldo_final": validado.saldo_final,
        "total_declarado_despesas": validado.total_declarado_despesas,
        "total_detalhado_identificado": validado.total_detalhado_identificado,
        "valor_nao_associado": validado.valor_nao_associado,
        "percentual_detalhamento": validado.percentual_detalhamento,
        "confirmado_com_ressalvas": validado.confirmado_com_ressalvas,
        "recorrente_total": lido.resumo.recorrente_total,
        "extraordinaria_total": lido.resumo.extraordinaria_total,
        "nao_determinada_total": lido.resumo.nao_determinada_total,
        "despesas_identificadas": lido.resumo.despesas_identificadas,
        "grupos_conferidos": lido.resumo.grupos_conferidos,
        "grupos_com_diferenca": lido.resumo.grupos_com_diferenca,
        "pontos_de_atencao": len(inconsistencias or []),
    }
