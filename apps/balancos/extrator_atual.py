"""Leitura financeira do balanço do modelo atual (ATUAL_CANONICO).

Este módulo transforma a camada textual de um balanço já classificado como
ATUAL_CANONICO no contrato ``BalancoExtraidoV1``. Nada aqui usa IA nem OCR: a
leitura é determinística, guiada pela estrutura do próprio documento, e o que
não tem evidência continua ausente.

Quatro compromissos governam a leitura:

* **só o modelo atual.** Um documento legado ou desconhecido não é lido por
  estas regras — devolve-se o motivo, sem tentar adivinhar o que não se sabe
  ler;
* **seções, não páginas.** O demonstrativo, as receitas, as despesas e o resumo
  ficam na abertura do balanço. A leitura localiza esses blocos e ignora os
  comprovantes anexos: ler cem comprovantes não melhora o resultado e custa
  caro;
* **totais oficiais, não somas.** O total de receitas e o de despesas vêm do que
  o documento declara. Não se soma linha a linha para derivar o total: as duas
  coisas são conceitos diferentes, e a diferença entre elas é justamente o que
  precisa aparecer;
* **despesas por grupo, receitas em resumo.** A leitura detalha as despesas pelo
  demonstrativo e as confere contra o subtotal que o documento declara para cada
  agrupamento. Recebimentos individuais não são lidos: o total de receitas vem
  do resumo do documento, e reproduzir centenas de lançamentos por unidade não
  serve à gestão e ainda traria dado de morador para dentro do sistema.

Onde o grupo não fecha, a diferença é mostrada — nunca se inventa um lançamento
para fazer o total bater. Ausência continua sendo ausência, e a dúvida vira
ponto de atenção.

Nada nesta fase cria ``BalancoMensal``: a importação continua sendo o objeto
transitório, e o mês só passa a valer quando a gestão confirmar.
"""
from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from decimal import Decimal

from django.conf import settings
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from apps.balancos.categorias import CategoriaEithra, categorizar, rotulo
from apps.balancos.constants import LayoutBalanco, StatusImportacao
from apps.balancos.contratos import (
    SCHEMA_VERSAO,
    BalancoExtraidoV1,
    CategoriaAgregada,
    Confianca,
    DespesaExtraida,
    ErroDeContrato,
    GrupoDeDespesa,
    InadimplenciaAgregada,
    Inconsistencia,
    Periodo,
    Procedencia,
    ResumoExtraido,
    SaldoExtraido,
    Severidade,
    StatusConferencia,
    TipoInconsistencia,
    para_dicionario,
    trecho_curto,
    validar_dicionario,
)
from apps.balancos.detector import (
    ErroDeDeteccaoDeLayout,
    _baixar_pdf_privado,
)
from apps.balancos.natureza_despesa import (
    NaturezaDespesa,
    classificar_natureza,
)
from apps.balancos.normalizadores import (
    PADRAO_VALOR,
    chave_de_comparacao,
    decimal_para_texto,
    normalizar_espacos,
    texto_para_competencia,
    texto_para_data,
    texto_para_decimal,
    texto_para_parcela,
)
from apps.balancos.validacao_financeira import TOLERANCIA, validar_balanco

# Quantas páginas de abertura a leitura varre. A extração precisa de mais
# páginas do que o detector, mas continua sem ler o arquivo inteiro: o
# demonstrativo estruturado está na abertura, e os anexos não entram na conta.
PAGINAS_PADRAO = 40

# Valor com cara de dinheiro em qualquer posição da linha: o demonstrativo
# escreve o valor antes ou depois da descrição e costuma repetir ao lado dele o
# acumulado e o percentual de rateio.
#
# O ``(?<![\d.,/])`` impede que o valor comece no meio de um número ou de uma
# data — sem ele, ``10/08/2026`` terminaria com o "valor" 2026. O que fecha o
# reconhecimento recusa o que não é dinheiro: um dígito ou uma barra colados
# (número maior, data), uma continuação decimal e o percentual, que é proporção
# e não lançamento.
_RE_VALOR = re.compile(
    rf"(?<![\d.,/])(?P<valor>{PADRAO_VALOR})(?![\d/])(?![.,]\d)(?!\s*%)"
)

# Rótulos que abrem um bloco. A ordem importa: "resumo" vem antes porque uma
# visão agregada não é o demonstrativo detalhado, e é do detalhado que saem os
# lançamentos. Nenhum rótulo é nome de empresa, de condomínio ou de valor: são
# as palavras com que qualquer balanço nomeia as próprias seções.
_MARCADORES_DE_SECAO: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "resumo",
        (
            "resumo financeiro",
            "resumo de contas",
            "resumo de receitas",
            "resumo de despesas",
            "resumo geral",
            "resumo",
        ),
    ),
    (
        "receitas",
        (
            "receitas detalhadas",
            "receita detalhada",
            "demonstrativo de receitas",
            "detalhamento de receitas",
        ),
    ),
    (
        "despesas",
        (
            "despesas detalhadas",
            "despesa detalhada",
            "demonstrativo de despesas",
            "detalhamento de despesas",
        ),
    ),
    ("saldos", ("saldo bancario", "saldo contabil", "saldos")),
    ("inadimplencia", ("inadimplencia",)),
    (
        "anexos",
        (
            "documentos anexos",
            "comprovantes anexos",
            "documentos comprovantes anexos",
            "extratos bancarios",
            "extrato bancario",
            "extratos",
            "extrato",
            "anexos",
            "anexo",
            "comprovantes",
            "comprovante",
            "realizado",
            "indice",
            "sumario",
        ),
    ),
)

# Um bloco também pode ser aberto por sua forma curta e isolada — "Receitas",
# "Despesas". Só a linha inteira vale: "despesa com pessoal" é agrupamento, não
# um bloco novo.
_MARCADORES_EXATOS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("receitas", ("receitas", "receita")),
    ("despesas", ("despesas", "despesa")),
)

# Bloco de detalhe, onde um valor no fim da linha é um lançamento e o texto
# solto logo abaixo completa a descrição do lançamento anterior. Receita
# individual não entra: no MVP as receitas vêm do resumo do documento.
SECOES_DE_LANCAMENTO = frozenset({"despesas"})

# -- Papel do bloco -------------------------------------------------------------
#
# O papel é da leitura, não do banco: diz o que se pode extrair de cada bloco.
# Um documento costuma trazer o resumo do mês antes do demonstrativo; somar os
# dois como se fossem lançamentos conta a mesma despesa duas vezes. Identificar
# o papel é o que impede isso.
PAPEL_RESUMO = "resumo"
PAPEL_DETALHE = "detalhe"
PAPEL_ANEXO = "anexo"
PAPEL_OUTRO = "outro"
# O bloco de saldo conta o que a conta tinha e o que sobrou — conta, fundo,
# movimento. Não descreve despesa do mês, e nada dele entra na soma.
PAPEL_SALDO = "saldo"

_PAPEL_DA_SECAO: dict[str, str] = {
    "resumo": PAPEL_RESUMO,
    "receitas": PAPEL_DETALHE,
    "despesas": PAPEL_DETALHE,
    "saldos": PAPEL_SALDO,
    "inadimplencia": PAPEL_OUTRO,
    "anexos": PAPEL_ANEXO,
}

# O resumo só vira lançamento quando o documento não detalha a despesa. Nesse
# caso a procedência diz de onde o valor veio — sem isso, resumo e detalhe
# ficariam indistinguíveis depois de gravados.
METODO_RESUMO = "resumo do documento, sem demonstrativo detalhado"


def _papel_da_secao(secao: str | None) -> str:
    """O papel do bloco corrente: o que se pode extrair dele."""
    return _PAPEL_DA_SECAO.get(secao or "", PAPEL_OUTRO)


# Vocabulário do bloco de saldo. São palavras do próprio documento — "saldo",
# "créditos", "débitos" —, nunca nome de banco, de conta ou de condomínio. O
# bloco é reconhecido pela estrutura que anuncia, e não pelo que ele contém.
_PALAVRAS_DE_SALDO = frozenset({"saldo", "saldos"})
_PALAVRAS_DE_ABERTURA = ("anterior", "inicial")
_PALAVRAS_DE_FECHAMENTO = ("atual", "final")


def _anuncia_saldo(chave: str) -> bool:
    """Diz se a linha é um título de bloco de saldo.

    O título não traz valor: nomeia o que vem abaixo — "saldo anterior", "saldo
    atual", "resumo financeiro contábil saldo anterior créditos débitos saldo
    atual". É esse rótulo que autoriza ler as linhas seguintes como saldo, e
    nunca o que elas contêm.
    """
    return bool(_PALAVRAS_DE_SALDO.intersection(chave.split()))


def _declara_movimentacao(chave: str) -> bool:
    """Diz se o título anuncia colunas de movimentação de conta.

    Uma tabela assim separa o saldo de abertura, os créditos, os débitos e o
    saldo de fechamento. É essa estrutura — e não o valor — que autoriza ler a
    primeira coluna como saldo anterior e a última como saldo atual. Sem ela,
    um valor no meio de uma linha continua sendo movimento, nunca saldo.
    """
    palavras = chave.split()
    if not (
        any(palavra.startswith("credito") for palavra in palavras)
        and any(palavra.startswith("debito") for palavra in palavras)
    ):
        return False
    abertura = _primeira_posicao(palavras, _PALAVRAS_DE_ABERTURA)
    fechamento = _primeira_posicao(palavras, _PALAVRAS_DE_FECHAMENTO)
    return (
        abertura is not None and fechamento is not None and abertura < fechamento
    )


def _primeira_posicao(palavras: list[str], alvos: tuple[str, ...]) -> int | None:
    """Posição da primeira palavra que está entre os alvos."""
    for indice, palavra in enumerate(palavras):
        if palavra in alvos:
            return indice
    return None


_RE_PERCENTUAL = re.compile(r"(\d{1,3}(?:[.,]\d{1,2})?)\s*%")
_RE_QUANTIDADE = re.compile(
    r"(\d{1,6})\s*(?:unidades|unidade|unid|casos|caso|inadimplentes|moradores)"
)
_RE_DATA_COMPLETA = re.compile(r"(?<![\d/])(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4})(?![\d/])")
_RE_SUFIXO_EMPRESA = re.compile(
    r"[\wÀ-ÿ&.,'\u2019\- ]{2,60}?\b(?:LTDA|LTD|S\.?\s?A\.?|ME|EPP|EIRELI|CIA|SOCIEDADE)\b\.?",
    re.IGNORECASE,
)
# Aceita "NF", "NFe" e "Nota Fiscal" sem depender da caixa: no documento real
# esses rótulos vêm escritos de todas as formas.
_RE_DOCUMENTO = re.compile(
    r"(?:nf(?:-?\s?e)?|nota fiscal|documento|doc|fatura|recibo|contrato)\D{0,12}(\d{3,})",
    re.IGNORECASE,
)
# Rótulo de comprovante citado no meio da descrição: sinaliza que a linha
# continua a anterior, e não que abre um agrupamento novo.
_RE_ROTULO_DOCUMENTO = re.compile(
    r"\b(?:nf(?:-?\s?e)?|nota fiscal|fatura|recibo)\b", re.IGNORECASE
)
# Linha que termina deixando o texto em aberto — vírgula, dois-pontos, hífen.
_RE_TERMINA_ABERTO = re.compile(r"[,;:\-]\s*$")
# Navegação de relatório: não é dado do mês nem agrupamento.
_RE_NAVEGACAO = re.compile(r"\bvoltar ao (?:indice|inicio|sumario)\b")

# Palavras com que um demonstrativo nomeia as próprias colunas. Uma linha feita
# só delas é cabeçalho de tabela — nunca um agrupamento de despesa.
_PALAVRAS_DE_COLUNA = frozenset(
    {
        "data",
        "datas",
        "historico",
        "descricao",
        "valor",
        "valores",
        "total",
        "documento",
        "doc",
        "credito",
        "debito",
    }
)

# Palavras com que um demonstrativo fecha o mês inteiro. Um rótulo de total
# feito só delas não nomeia agrupamento nenhum — é o total do documento escrito
# de outro jeito ("total da conta ordinária"). Sem essa distinção, o total do mês
# acabaria amarrado ao último agrupamento lido.
_PALAVRAS_DE_TOTAL_DO_MES = frozenset(
    {
        "total",
        "subtotal",
        "geral",
        "do",
        "das",
        "dos",
        "da",
        "de",
        "mes",
        "periodo",
        "conta",
        "contas",
        "ordinaria",
        "ordinario",
        "receita",
        "receitas",
        "despesa",
        "despesas",
    }
)

# Total do mês, e não subtotal de agrupamento. "Total de despesas" fecha o mês;
# "total da conta despesa com pessoal" fecha um agrupamento e não pode virar o
# total declarado do documento.
_RE_TOTAL_GERAL = re.compile(
    r"^(?:sub)?total"
    r"(?:\s+(?:geral|do\s+mes|do\s+periodo|de\s+contas|das\s+contas))?"
    r"(?:\s+(?:de|das|dos|da|do))?"
    r"\s*(?:receitas?|despesas?)?"
    r"(?:\s+(?:do\s+mes|do\s+periodo))?$"
)

# Rótulo de total em qualquer posição da linha. Quando a tabela traz data e
# colunas antes do rótulo, o texto não começa com "total" — e o fechamento do
# agrupamento acabaria lançado como despesa do mês. Reconhecer a palavra (e não
# só o começo da linha) é o que mantém subtotal fora dos lançamentos.
_RE_TEM_TOTAL = re.compile(r"\b(?:sub)?total\b")


class ErroDeExtracao(Exception):
    """Falha técnica ao tentar ler os dados financeiros do PDF.

    A mensagem é para a gestão: fala do que aconteceu, nunca de URL assinada,
    token, caminho de objeto ou resposta bruta do provedor.
    """


@dataclass(frozen=True)
class ResultadoDaExtracao:
    """O que a leitura conseguiu fazer com uma importação.

    ``processado`` é ``False`` quando o documento não é do modelo atual: nesse
    caso nada foi lido e o campo ``motivo`` diz o que será feito dele adiante.
    """

    processado: bool
    layout: str
    motivo: str = ""
    balanco: BalancoExtraidoV1 | None = None
    inconsistencias: tuple[Inconsistencia, ...] = ()


# -- Entrada pública ------------------------------------------------------------


def extrair_balanco_atual(
    importacao, *, conteudo: bytes | None = None, provider=None
) -> ResultadoDaExtracao:
    """Lê os dados financeiros do PDF privado e grava o resultado na importação.

    Só o modelo atual é lido. Um documento legado ou desconhecido devolve o
    motivo e deixa a importação como estava — nenhum dado é inventado para
    preencher a tela. Nenhum ``BalancoMensal`` nasce aqui.

    A leitura é idempotente: reexecutar sobre o mesmo arquivo produz o mesmo
    resultado e não cria objeto novo. Quando quem chama já tem o conteúdo em
    mãos — o caminho do upload tem —, ele pode ser passado e evita um segundo
    acesso ao storage.
    """
    layout = importacao.layout_detectado
    if layout != LayoutBalanco.ATUAL_CANONICO:
        return ResultadoDaExtracao(
            processado=False, layout=layout, motivo=_motivo_de_recusa(layout)
        )

    importacao.status = StatusImportacao.PROCESSANDO
    importacao.erro_processamento = ""
    importacao.save(update_fields=["status", "erro_processamento", "atualizado_em"])

    try:
        if conteudo is None:
            conteudo = _obter_conteudo(importacao, provider)
        balanco, inconsistencias = ler_balanco_atual(conteudo)
        dados = para_dicionario(balanco)
        validar_dicionario(dados)
    except ErroDeExtracao as erro:
        _marcar_erro(importacao, str(erro))
        raise
    except ErroDeContrato as erro:
        _marcar_erro(importacao, "A leitura não respeitou o contrato esperado.")
        raise ErroDeExtracao(
            "A leitura não respeitou o contrato esperado."
        ) from erro

    importacao.resultado_extraido = dados
    importacao.inconsistencias = [_inconsistencia_para_dicionario(i) for i in inconsistencias]
    importacao.schema_versao = SCHEMA_VERSAO
    importacao.status = StatusImportacao.PRONTO
    importacao.erro_processamento = ""
    importacao.save(
        update_fields=[
            "resultado_extraido",
            "inconsistencias",
            "schema_versao",
            "status",
            "erro_processamento",
            "atualizado_em",
        ]
    )

    return ResultadoDaExtracao(
        processado=True,
        layout=layout,
        balanco=balanco,
        inconsistencias=inconsistencias,
    )


def ler_balanco_atual(
    conteudo: bytes,
) -> tuple[BalancoExtraidoV1, tuple[Inconsistencia, ...]]:
    """Lê um PDF do modelo atual e devolve o contrato já conferido.

    Função pura: não toca em banco nem em storage. Existe separada para que a
    leitura possa ser exercitada com documentos sintéticos, sem rede.
    """
    linhas, total_paginas = _linhas_do_pdf(conteudo)

    competencia, inconsistencias = _competencia(linhas)
    periodo = _periodo(linhas)
    if competencia is None and not inconsistencias and periodo.inicio:
        # A competência representa o mês; derivá-la do início de um período
        # declarado é leitura, não invenção — o documento disse aquele mês.
        competencia = _primeiro_dia(periodo.inicio)

    varredura = _varrer(linhas)
    inconsistencias = tuple([*inconsistencias, *varredura.inconsistencias])

    grupos = _agregar_grupos(varredura.despesas, varredura.subtotais_grupos)
    soma_despesas = _somar_valores(despesa.valor for despesa in varredura.despesas)

    base = BalancoExtraidoV1(
        competencia=competencia,
        periodo=periodo,
        receitas_total=varredura.receitas_total,
        despesas_total=varredura.despesas_total,
        soma_despesas_extraida=decimal_para_texto(soma_despesas),
        saldo_anterior=varredura.saldo_anterior,
        saldo_final=varredura.saldo_final,
        saldos=tuple(varredura.saldos),
        categorias=_agregar_categorias(varredura.despesas),
        grupos=grupos,
        despesas=tuple(varredura.despesas),
        inadimplencia_agregada=varredura.inadimplencia,
        resumo=ResumoExtraido(),
        provenance={
            "metodo": "camada textual do PDF",
            "paginas_lidas": len({pagina for pagina, _ in linhas}),
            "total_paginas": total_paginas,
            "secoes_encontradas": list(varredura.secoes),
        },
        confianca=Confianca.MEDIA,
    )

    inconsistencias = tuple([*inconsistencias, *validar_balanco(base)])
    divergencia = _reconciliar_total_geral(grupos, varredura.despesas_total)
    if divergencia is not None:
        inconsistencias = tuple([*inconsistencias, divergencia])

    return replace(
        base,
        resumo=_resumo(varredura.despesas, grupos, inconsistencias),
        confianca=_confianca(competencia, varredura.despesas, inconsistencias),
    ), inconsistencias


# -- Acesso ao PDF --------------------------------------------------------------


def _obter_conteudo(importacao, provider) -> bytes:
    """Traz o PDF privado. O acesso assinado já existe e é do detector.

    Reaproveitar o caminho homologado evita uma segunda implementação da mesma
    leitura — e o detector segue intocado.
    """
    try:
        conteudo, _ = _baixar_pdf_privado(importacao, provider=provider)
    except ErroDeDeteccaoDeLayout as erro:
        raise ErroDeExtracao(str(erro)) from erro
    return conteudo


def _linhas_do_pdf(conteudo: bytes) -> tuple[list[tuple[int, str]], int]:
    """Devolve as linhas com texto, cada uma com a página de origem.

    Lê apenas a abertura do documento: é onde ficam o demonstrativo, as
    receitas, as despesas e o resumo. Um balanço com muitos comprovantes anexos
    é lido pelo essencial, e não página a página.

    A leitura preserva o layout da página. O demonstrativo é uma tabela em
    colunas, e sem isso a ordem das células se perde: a data, a descrição e o
    valor chegam embaralhados, e um valor pode acabar colado no texto do lado.
    """
    try:
        leitor = PdfReader(io.BytesIO(conteudo))
    except (PdfReadError, ValueError) as erro:
        raise ErroDeExtracao(
            "Não foi possível ler a camada textual do PDF."
        ) from erro

    if getattr(leitor, "is_encrypted", False):
        try:
            leitor.decrypt("")
        except Exception as erro:  # pragma: no cover - depende do PDF real
            raise ErroDeExtracao("O PDF está protegido e não pode ser lido.") from erro

    total_paginas = len(leitor.pages)
    if total_paginas <= 0:
        raise ErroDeExtracao("O PDF não possui páginas legíveis.")

    linhas: list[tuple[int, str]] = []
    for indice in range(min(_paginas_para_leitura(), total_paginas)):
        texto = leitor.pages[indice].extract_text(extraction_mode="layout") or ""
        for bruta in texto.splitlines():
            limpa = normalizar_espacos(bruta)
            if limpa:
                linhas.append((indice + 1, limpa))

    return linhas, total_paginas


def _paginas_para_leitura() -> int:
    limite = int(
        getattr(settings, "BALANCOS_EXTRACAO_MAX_PAGINAS", PAGINAS_PADRAO)
        or PAGINAS_PADRAO
    )
    return max(1, limite)


# -- Competência e período ------------------------------------------------------


def _competencia(
    linhas: list[tuple[int, str]],
) -> tuple[str | None, tuple[Inconsistencia, ...]]:
    """Descobre a competência, sem escolher em silêncio quando há conflito.

    Uma linha que rotula a competência ("Competência: Agosto/2026") vale mais do
    que uma menção solta, porque é onde o documento afirma o mês a que se
    refere. Só quando não há rótulo a leitura considera as menções soltas — e
    nunca em linhas que trazem valor, para que o mês de um lançamento não seja
    confundido com a competência do balanço.
    """
    rotuladas: list[date] = []
    soltas: list[date] = []

    for _, linha in linhas:
        achada = texto_para_competencia(linha)
        if achada is None:
            continue
        chave = chave_de_comparacao(linha)
        if "competencia" in chave or "mes de referencia" in chave:
            rotuladas.append(achada)
        elif _valor_da_linha(linha) is None:
            soltas.append(achada)

    candidatas = rotuladas or soltas
    distintas = sorted(set(candidatas))

    if len(distintas) == 1:
        return distintas[0].isoformat(), ()
    if len(distintas) > 1:
        return None, (
            Inconsistencia(
                tipo=TipoInconsistencia.COMPETENCIA_CONFLITANTE,
                severidade=Severidade.ALTA,
                mensagem=(
                    "O documento traz mais de uma competência possível ("
                    + ", ".join(f"{d.month:02d}/{d.year}" for d in distintas)
                    + "), então o mês ficou em aberto até a conferência."
                ),
                referencia="competencia",
            ),
        )
    return None, ()


def _periodo(linhas: list[tuple[int, str]]) -> Periodo:
    """Intervalo declarado pelo documento, quando ele escreve um."""
    for _, linha in linhas:
        if "periodo" not in chave_de_comparacao(linha):
            continue
        datas = _datas_completas(linha)
        if len(datas) >= 2:
            return Periodo(inicio=datas[0].isoformat(), fim=datas[1].isoformat())
    return Periodo()


def _datas_completas(linha: str) -> list[date]:
    datas: list[date] = []
    for achado in _RE_DATA_COMPLETA.finditer(linha):
        convertida = texto_para_data(achado.group(1))
        if convertida is not None:
            datas.append(convertida)
    return datas


def _primeiro_dia(iso: str) -> str | None:
    try:
        referencia = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    return date(referencia.year, referencia.month, 1).isoformat()


# -- Varredura das linhas -------------------------------------------------------


@dataclass
class _Varredura:
    """O que a leitura das linhas acumulou, antes de virar contrato."""

    despesas: list[DespesaExtraida] = field(default_factory=list)
    # Linhas de despesa lidas no resumo. Só valem como lançamento se o documento
    # não trouxer o demonstrativo detalhado; havendo os dois, elas são apenas
    # âncora de conferência do que o detalhe já disse.
    despesas_de_resumo: list[DespesaExtraida] = field(default_factory=list)
    saldos: list[SaldoExtraido] = field(default_factory=list)
    saldo_anterior: str | None = None
    saldo_final: str | None = None
    receitas_total: str | None = None
    despesas_total: str | None = None
    # Subtotal declarado de cada agrupamento, pelo rótulo do documento. É o que
    # permite conferir o grupo: compara-se o que ele afirma com o que se leu.
    subtotais_grupos: dict[str, str] = field(default_factory=dict)
    inadimplencia: InadimplenciaAgregada | None = None
    secoes: list[str] = field(default_factory=list)
    # Quantas linhas de bloco de saldo/movimentação deixaram de virar lançamento.
    # É a medida do que saiu da soma das despesas ao reconhecer o bloco.
    lancamentos_evitados: int = 0
    inconsistencias: list[Inconsistencia] = field(default_factory=list)


def _varrer(linhas: list[tuple[int, str]]) -> _Varredura:
    """Percorre as linhas, seguindo os blocos até os lançamentos."""
    varredura = _Varredura()
    repetidas = _linhas_repetidas(linhas)
    secao: str | None = None
    grupo: str | None = None
    # Uma continuação só vale para a linha imediatamente posterior a um
    # lançamento. Sem isso, o título do agrupamento seguinte seria absorvido
    # como resto da descrição anterior e o grupo se perderia.
    depois_de_lancamento = False
    # Bloco de saldo/movimentação aberto por um título: enquanto ele durar, o que
    # aparece com valor é conta, fundo ou movimento — nunca despesa do mês.
    em_bloco_de_saldo = False
    # A tabela separa abertura e fechamento? Só com essa estrutura a primeira
    # coluna vira saldo anterior e a última, saldo atual.
    colunas_de_movimento = False

    for pagina, linha in linhas:
        chave = chave_de_comparacao(linha)
        if not chave:
            continue
        if _RE_NAVEGACAO.search(chave):
            continue

        valores = _valores_da_linha(linha)
        token = valores[0] if valores else None
        completa_o_anterior = depois_de_lancamento
        depois_de_lancamento = False

        if token is None:
            nome = _secao_da_linha(chave)
            if nome is not None:
                secao = nome
                grupo = None
                em_bloco_de_saldo = False
                colunas_de_movimento = False
                if nome not in varredura.secoes:
                    varredura.secoes.append(nome)
                continue
            # Um título que anuncia saldos abre o bloco: daqui para baixo, o que
            # tem valor é conta, fundo ou movimento. Movimentação de conta não é
            # despesa do condomínio, e contá-la como despesa inflaria o mês.
            if _anuncia_saldo(chave) and _papel_da_secao(secao) in (
                PAPEL_DETALHE,
                PAPEL_RESUMO,
                PAPEL_SALDO,
            ):
                em_bloco_de_saldo = True
                colunas_de_movimento = colunas_de_movimento or _declara_movimentacao(
                    chave
                )
                grupo = None
                continue
            # O que se repete página a página é moldura do relatório — cabeçalho,
            # rodapé, nome das colunas —, nunca dado do mês nem agrupamento.
            if chave in repetidas or _e_cabecalho_de_colunas(chave):
                continue
            if em_bloco_de_saldo:
                # Texto solto fora da tabela de saldos: o bloco acabou antes
                # dele. Sem isso, as despesas seguintes seriam lidas como saldo.
                em_bloco_de_saldo = False
                colunas_de_movimento = False
            if (
                completa_o_anterior
                and secao in SECOES_DE_LANCAMENTO
                and _anexar_descricao(varredura, secao, linha)
            ):
                depois_de_lancamento = True
                continue
            if secao in ("despesas", "resumo") and _parece_rotulo_de_grupo(chave):
                grupo = linha
            continue

        descricao = _descricao_da_linha(linha)
        chave_desc = chave_de_comparacao(descricao)
        procedencia = _procedencia(pagina, secao, linha)

        # Dentro de um bloco de saldo o valor é de conta, de fundo ou de
        # movimento — nunca lançamento de despesa. A exceção é a linha que fecha
        # o mês e nomeia receitas ou despesas: ela é o total do documento, e
        # continua sendo lida como tal mesmo no meio do bloco.
        if (
            em_bloco_de_saldo or _papel_da_secao(secao) == PAPEL_SALDO
        ) and not _fecha_o_documento(chave_desc):
            _registrar_saldo_do_bloco(
                varredura,
                chave_desc,
                descricao,
                valores,
                colunas_de_movimento,
                procedencia,
            )
            continue

        if _RE_TEM_TOTAL.search(chave_desc):
            # O total do mês alimenta o declarado; o subtotal de um agrupamento
            # ("total da conta ...") fecha a categoria e não vira lançamento nem
            # sobrescreve o total do documento.
            if _RE_TOTAL_GERAL.match(chave_desc) or _fecha_o_mes(chave_desc):
                _registrar_total(varredura, chave_desc, token)
            elif grupo is not None:
                _registrar_subtotal_grupo(varredura, grupo, token)
            continue

        if "saldo" in chave_desc:
            _registrar_saldo(varredura, chave_desc, token, descricao, procedencia)
            continue

        if "inadimplencia" in chave_desc:
            _registrar_inadimplencia(varredura, token, linha, procedencia)
            continue

        if chave_desc in ("receitas", "receita"):
            _registrar_resumo_total(varredura, "receitas", token)
            continue
        if chave_desc in ("despesas", "despesa"):
            _registrar_resumo_total(varredura, "despesas", token)
            continue

        # Blocos em que uma linha com valor é dado do mês: o demonstrativo, que
        # detalha, e o resumo, que declara o que o detalhe soma. Nos demais —
        # anexos, saldos, inadimplência — o valor não é lançamento nem anomalia.
        papel = _papel_da_secao(secao)
        if papel not in (PAPEL_DETALHE, PAPEL_RESUMO):
            continue

        if len(valores) >= 3:
            # Três ou mais valores alinhados na mesma linha são uma tabela de
            # colunas — movimento, acumulado, saldo —, não um lançamento do mês.
            # Um lançamento traz um valor, no máximo com o acumulado e o
            # percentual ao lado.
            continue

        if not chave_desc:
            varredura.inconsistencias.append(
                Inconsistencia(
                    tipo=TipoInconsistencia.LINHA_INCOMPLETA,
                    severidade=Severidade.BAIXA,
                    mensagem="Apareceu um valor sem descrição na linha.",
                    referencia=trecho_curto(token),
                    procedencia=procedencia,
                )
            )
            continue

        valor = texto_para_decimal(token)
        if valor is None:
            varredura.inconsistencias.append(
                Inconsistencia(
                    tipo=TipoInconsistencia.VALOR_INVALIDO,
                    severidade=Severidade.BAIXA,
                    mensagem=(
                        f"Não foi possível ler o valor de “{trecho_curto(descricao)}”."
                    ),
                    referencia=chave_desc,
                    procedencia=procedencia,
                )
            )
            continue

        if secao == "receitas":
            # Recebimento individual não faz parte do MVP: o total de receitas
            # vem do resumo do documento.
            continue

        despesa = _criar_despesa(descricao, chave_desc, valor, grupo, procedencia)

        if papel == PAPEL_RESUMO:
            # O resumo entra na conferência, não na soma: se o documento trouxer
            # o demonstrativo detalhado, esta linha não vale como lançamento.
            varredura.despesas_de_resumo.append(despesa)
            continue

        varredura.despesas.append(despesa)
        depois_de_lancamento = True

    _aplicar_precedencia(varredura)
    return varredura


def _aplicar_precedencia(varredura: _Varredura) -> None:
    """Só o bloco detalhado gera lançamento quando o documento traz os dois.

    O resumo fecha o mês; o demonstrativo lista as despesas uma a uma. Ler os
    dois como lançamento conta a mesma despesa duas vezes, e é isso que faz a
    soma passar do total declarado. Quando há detalhamento, as linhas do resumo
    ficam de fora da soma — elas continuam servindo de âncora de conferência.

    Sem detalhamento, o resumo é tudo o que o documento oferece: ele passa a
    valer como leitura, e a procedência registra que o valor veio do resumo.
    """
    if varredura.despesas:
        varredura.despesas_de_resumo = []
        return
    varredura.despesas = [
        replace(
            despesa,
            procedencia=replace(despesa.procedencia, metodo=METODO_RESUMO),
        )
        for despesa in varredura.despesas_de_resumo
    ]
    varredura.despesas_de_resumo = []


def _criar_despesa(
    descricao: str,
    chave_desc: str,
    valor: Decimal,
    grupo: str | None,
    procedencia: Procedencia,
) -> DespesaExtraida:
    """Monta a despesa preservando o agrupamento do documento ao lado do Eithra."""
    categoria_eithra = categorizar(descricao, categoria_original=grupo)
    return DespesaExtraida(
        descricao_original=descricao,
        descricao_normalizada=chave_desc,
        valor=decimal_para_texto(valor),
        data=_data_do_lancamento(descricao),
        fornecedor_candidato=_fornecedor_candidato(descricao),
        documento=_documento_do_lancamento(descricao),
        parcela=texto_para_parcela(descricao),
        categoria_original=grupo,
        categoria_eithra=categoria_eithra,
        natureza=classificar_natureza(descricao, categoria_eithra=categoria_eithra),
        confianca=Confianca.ALTA,
        procedencia=procedencia,
    )


def _registrar_total(varredura: _Varredura, chave_desc: str, token: str) -> None:
    """Guarda o total declarado, seja de receitas ou de despesas."""
    valor = texto_para_decimal(token)
    if valor is None:
        return
    if "receita" in chave_desc:
        varredura.receitas_total = decimal_para_texto(valor)
    elif "despesa" in chave_desc:
        varredura.despesas_total = decimal_para_texto(valor)


def _registrar_resumo_total(varredura: _Varredura, qual: str, token: str) -> None:
    """Total declarado numa linha que é só o rótulo: "Receitas   12.345,67"."""
    valor = texto_para_decimal(token)
    if valor is None:
        return
    if qual == "receitas":
        varredura.receitas_total = decimal_para_texto(valor)
    else:
        varredura.despesas_total = decimal_para_texto(valor)


def _registrar_subtotal_grupo(varredura: _Varredura, grupo: str, token: str) -> None:
    """Guarda o subtotal que o documento declara para um agrupamento.

    O subtotal fecha o grupo, e não o mês: não vira lançamento nem substitui o
    total do documento. Guardá-lo é o que permite comparar, depois, o que o
    documento afirma com o que a leitura encontrou dentro do grupo — e mostrar a
    diferença em vez de escondê-la.
    """
    valor = texto_para_decimal(token)
    if valor is None:
        return
    varredura.subtotais_grupos[grupo] = decimal_para_texto(valor)


def _registrar_saldo(
    varredura: _Varredura,
    chave_desc: str,
    token: str,
    descricao: str,
    procedencia: Procedencia,
) -> None:
    """Guarda um saldo por conta, marcando início e fim quando o texto diz.

    O saldo entra na lista com a identificação que o documento escreveu — sem
    inventar nome de banco. Quando a linha declara ser o saldo anterior (ou
    inicial), ele alimenta também o campo de abertura; caso contrário, é o
    saldo final do período.
    """
    valor = texto_para_decimal(token)
    if valor is None:
        return
    texto = decimal_para_texto(valor)
    varredura.saldos.append(
        SaldoExtraido(
            identificacao=descricao or "Saldo",
            valor=texto,
            procedencia=procedencia,
        )
    )
    if "anterior" in chave_desc or "inicial" in chave_desc:
        varredura.saldo_anterior = texto
    else:
        varredura.saldo_final = texto


def _registrar_saldo_do_bloco(
    varredura: _Varredura,
    chave_desc: str,
    descricao: str,
    valores: list[str],
    colunas_de_movimento: bool,
    procedencia: Procedencia,
) -> None:
    """Lê uma linha de dentro do bloco de saldo/movimentação.

    Um bloco desses não conta despesa do mês: diz o que a conta tinha, o que
    entrou, o que saiu e o que sobrou. Nada daqui entra na soma das despesas, e
    nenhum valor vira lançamento.

    A leitura é conservadora de propósito. Uma linha com um valor só é o saldo
    de uma conta, com a identificação que o documento escreveu. Uma linha com
    vários valores só produz abertura e fechamento quando o título da tabela
    declarou essas colunas; sem esse rótulo, movimento continua sendo movimento
    e nenhum saldo é inferido.
    """
    if not valores:
        return

    if len(valores) < 3 and not _RE_TEM_TOTAL.search(chave_desc):
        # Linha que, sem o bloco, teria virado lançamento: é o que se conta como
        # movimentação de conta que saiu da soma das despesas.
        varredura.lancamentos_evitados += 1

    if len(valores) == 1:
        if "saldo" in chave_desc:
            # A própria linha diz o que é — "saldo anterior", "saldo atual".
            _registrar_saldo(varredura, chave_desc, valores[0], descricao, procedencia)
            return
        if _RE_TEM_TOTAL.search(chave_desc):
            # Fechamento de uma coluna só: é o total do bloco, e não uma conta.
            return
        valor = texto_para_decimal(valores[0])
        if valor is None:
            return
        varredura.saldos.append(
            SaldoExtraido(
                identificacao=descricao or "Saldo",
                valor=decimal_para_texto(valor),
                procedencia=procedencia,
            )
        )
        return

    if _RE_TEM_TOTAL.search(chave_desc):
        _registrar_total_do_bloco(varredura, valores, colunas_de_movimento)
        return

    if _datas_completas(descricao):
        # Linha datada é movimento do extrato: o que entrou e o que saiu da
        # conta num dia. Não é o que ela tinha nem o que sobrou, e movimento não
        # vira registro no MVP. É a data — marca estrutural, nunca o valor — que
        # separa o movimento do saldo.
        return

    if not colunas_de_movimento or len(valores) < 3:
        # Movimento sem estrutura declarada, ou linha de poucos valores: o que
        # seria saldo e o que seria acumulado não se distingue. Não se infere.
        return

    abertura = texto_para_decimal(valores[0])
    fechamento = texto_para_decimal(valores[-1])
    if abertura is None or fechamento is None:
        return
    varredura.saldos.append(
        SaldoExtraido(
            identificacao=descricao or "Saldo",
            valor=decimal_para_texto(fechamento),
            saldo_anterior=decimal_para_texto(abertura),
            saldo_final=decimal_para_texto(fechamento),
            procedencia=procedencia,
        )
    )


def _registrar_total_do_bloco(
    varredura: _Varredura, valores: list[str], colunas_de_movimento: bool
) -> None:
    """Guarda o saldo do documento a partir do total do bloco.

    O total de uma tabela que declara abertura e fechamento é o saldo do período
    inteiro: a primeira coluna soma as aberturas, a última, os saldos atuais. Só
    com essa estrutura — havendo várias contas, a equação simples do balanço não
    descreve o documento, e a conferência se cala em vez de acusar uma
    divergência que não existe.
    """
    if not colunas_de_movimento or len(valores) < 3:
        return
    abertura = texto_para_decimal(valores[0])
    fechamento = texto_para_decimal(valores[-1])
    if abertura is None or fechamento is None:
        return
    varredura.saldo_anterior = decimal_para_texto(abertura)
    varredura.saldo_final = decimal_para_texto(fechamento)


def _registrar_inadimplencia(
    varredura: _Varredura, token: str, linha: str, procedencia: Procedencia
) -> None:
    """Agrega a inadimplência do mês: valor, percentual e quantidade.

    Só o agregado do condomínio. Nenhum dado por unidade entra no contrato.
    """
    valor = texto_para_decimal(token)
    percentual = _percentual(linha)
    quantidade = _quantidade(linha)
    if valor is None and percentual is None and quantidade is None:
        return
    varredura.inadimplencia = InadimplenciaAgregada(
        valor=decimal_para_texto(valor),
        percentual=percentual,
        quantidade=quantidade,
        procedencia=procedencia,
    )


def _percentual(linha: str) -> str | None:
    """Percentual citado na linha, com a vírgula já convertida em ponto."""
    achado = _RE_PERCENTUAL.search(linha or "")
    if not achado:
        return None
    return achado.group(1).replace(",", ".")


def _quantidade(linha: str) -> int | None:
    """Quantidade citada na linha, quando o texto diz de que se trata."""
    achado = _RE_QUANTIDADE.search(linha or "")
    if not achado:
        return None
    return int(achado.group(1))


# -- Reconhecimento de linha ----------------------------------------------------


def _valores_da_linha(linha: str) -> list[str]:
    """Todos os valores com cara de dinheiro da linha, na ordem em que aparecem.

    O valor pode estar em qualquer posição: antes ou depois da descrição, às
    vezes com a data no meio. Quando a linha traz também o acumulado e o
    percentual, o primeiro dinheiro é o lançamento — o que vem depois é leitura
    do mês, não um lançamento novo. Saber quantos são é o que distingue um
    lançamento de uma linha de tabela com várias colunas de valores.
    """
    valores: list[str] = []
    for achado in _RE_VALOR.finditer(linha):
        token = _limpar_token(achado.group("valor"))
        if token and _parece_dinheiro(token):
            valores.append(token)
    return valores


def _valor_da_linha(linha: str) -> str | None:
    """O valor do lançamento da linha — ou ``None`` quando não há dinheiro."""
    valores = _valores_da_linha(linha)
    return valores[0] if valores else None


def _limpar_token(token: str) -> str | None:
    """Tira o que a captura pegou de enfeite e devolve só o valor.

    Um hífen solto antes dos dígitos é separador de frase, não sinal de valor:
    ``"manutenção - 1.234,56"`` não é despesa negativa. Só o hífen colado ao
    número vale como sinal.
    """
    token = (token or "").strip()
    if token.startswith("-") and not token[1:2].isdigit():
        token = token[1:].lstrip()
    return token or None


def _descricao_da_linha(linha: str) -> str:
    """O texto da linha sem os valores: a descrição do lançamento.

    Ao lado do lançamento o demonstrativo costuma repetir o acumulado do mês e
    o percentual de rateio. Deixar esses números no texto atrapalharia
    reconhecer o mesmo lançamento depois — e nenhum deles é descrição.
    """
    sem_percentual = _RE_PERCENTUAL.sub(" ", linha)
    partes: list[str] = []
    ultimo = 0
    for achado in _RE_VALOR.finditer(sem_percentual):
        token = _limpar_token(achado.group("valor"))
        if not token or not _parece_dinheiro(token):
            continue
        partes.append(sem_percentual[ultimo : achado.start()])
        ultimo = achado.end()
    partes.append(sem_percentual[ultimo:])
    return normalizar_espacos(" ".join(partes))


def _parece_dinheiro(token: str) -> bool:
    """Diz se o trecho é dinheiro, e não um número solto.

    O contexto — estar dentro de um bloco de lançamentos — já filtrou muito; o
    que resta recusar é o número sem nenhum sinal de dinheiro. Exigir vírgula,
    ponto, cifrão ou sinal de negativo é o que impede que um dia de vencimento
    ("dia 15") ou um código solto virem lançamento.
    """
    if not token or texto_para_decimal(token) is None:
        return False
    return any(sinal in token for sinal in (",", ".", "R$", "-", "(", ")"))


def _linhas_repetidas(linhas: list[tuple[int, str]]) -> frozenset[str]:
    """As linhas que aparecem em mais de uma página.

    Cabeçalho, rodapé e navegação se repetem página a página; um lançamento não.
    É o que separa a moldura do relatório do que ele diz sobre o mês.
    """
    paginas_por_chave: dict[str, set[int]] = {}
    for pagina, linha in linhas:
        chave = chave_de_comparacao(linha)
        if chave:
            paginas_por_chave.setdefault(chave, set()).add(pagina)
    return frozenset(
        chave for chave, paginas in paginas_por_chave.items() if len(paginas) > 1
    )


def _e_cabecalho_de_colunas(chave: str) -> bool:
    """Diz se a linha é só o nome das colunas da tabela."""
    palavras = chave.split()
    return bool(palavras) and set(palavras) <= _PALAVRAS_DE_COLUNA


def _anexar_descricao(varredura: _Varredura, secao: str, linha: str) -> bool:
    """Usa a linha para completar a descrição do lançamento anterior.

    O demonstrativo quebra a descrição em mais de uma linha — o rótulo do
    documento, o resto do nome da empresa. Como só vale a linha imediatamente
    posterior a um lançamento, uma linha de texto solta logo depois dele é
    continuação, e não um agrupamento novo. Só as despesas são detalhadas, então
    só elas têm descrição a completar.
    """
    if secao != "despesas":
        return False
    if not varredura.despesas:
        return False
    anterior = varredura.despesas[-1]
    valor = texto_para_decimal(anterior.valor)
    if valor is None:  # pragma: no cover - valor já conferido ao criar
        return False

    texto = normalizar_espacos(f"{anterior.descricao_original} {linha}")
    chave = chave_de_comparacao(texto)
    varredura.despesas[-1] = _criar_despesa(
        texto, chave, valor, anterior.categoria_original, anterior.procedencia
    )
    return True


def _secao_da_linha(chave: str) -> str | None:
    """Qual bloco a linha marca, se ela marca algum.

    O marcador precisa ser a linha inteira: um cabeçalho de seção é um título
    curto, e não uma frase que por acaso cita "despesas". Sem essa exigência,
    um agrupamento como "despesas com pessoal" trocaria o bloco no meio da
    leitura e os lançamentos seguintes perderiam o agrupamento.
    """
    if not chave:
        return None
    for nome, marcadores in _MARCADORES_DE_SECAO:
        if chave in marcadores:
            return nome
    for nome, marcadores in _MARCADORES_EXATOS:
        if chave in marcadores:
            return nome
    return None


def _parece_rotulo_de_grupo(chave: str) -> bool:
    """Diz se a linha é um agrupamento, e não um lançamento.

    Agrupamento é texto curto e sem cara de frase. Quem chama já sabe que a
    linha não traz valor; preservar o agrupamento do documento ao lado do
    Eithra é o que permite não perdê-lo na leitura.
    """
    if not chave or len(chave) > 60:
        return False
    return len(chave.split()) <= 8


def _fecha_o_mes(chave: str) -> bool:
    """Diz se o rótulo de total fecha o mês inteiro, e não um agrupamento.

    O demonstrativo rotula o total do mês de mais de um jeito — "total geral",
    "total das contas", "total da conta ordinária". Nenhum deles nomeia um
    agrupamento: tirando as palavras de fechamento, não sobra assunto nenhum.
    Tomá-los por subtotal de grupo amarraria o total do mês ao último
    agrupamento lido, e o mês inteiro apareceria como despesa de um setor só.
    """
    return not [
        palavra
        for palavra in chave.split()
        if palavra not in _PALAVRAS_DE_TOTAL_DO_MES
    ]


def _fecha_o_documento(chave: str) -> bool:
    """Diz se a linha fecha o mês declarando receitas ou despesas.

    Mesmo dentro de um bloco de saldo, uma linha assim é o total do documento —
    "total das despesas", "total de receitas" —, e não um saldo de conta. É o
    que mantém o total declarado do mês fora do bloco, sem confundi-lo com
    movimento bancário.
    """
    if "receita" not in chave and "despesa" not in chave:
        return False
    return bool(_RE_TOTAL_GERAL.match(chave)) or _fecha_o_mes(chave)


# -- Extração de campos do lançamento -------------------------------------------


def _procedencia(pagina: int | None, secao: str | None, linha: str) -> Procedencia:
    """De onde o dado veio: página, seção, trecho curto e método."""
    return Procedencia(
        pagina=pagina,
        secao=secao or "",
        trecho=linha,
        metodo="camada textual do PDF",
    )


def _data_do_lancamento(descricao: str) -> str | None:
    """Data do lançamento, quando a própria linha declara uma."""
    for achado in _RE_DATA_COMPLETA.finditer(descricao or ""):
        convertida = texto_para_data(achado.group(1))
        if convertida is not None:
            return convertida.isoformat()
    return None


def _fornecedor_candidato(descricao: str) -> str | None:
    """Nome de empresa no texto, quando há.

    É candidato: o Eithra ainda não cria fornecedor nesta fase. Guardar o
    indício é o que permitirá reconhecer a mesma empresa em meses seguintes.
    """
    achado = _RE_SUFIXO_EMPRESA.search(descricao or "")
    if not achado:
        return None
    nome = normalizar_espacos(achado.group(0)).strip(" .,-")
    return nome or None


def _documento_do_lancamento(descricao: str) -> str | None:
    """Número de nota ou documento citado na linha, quando há."""
    achado = _RE_DOCUMENTO.search(descricao or "")
    return achado.group(1) if achado else None


# -- Agregados ------------------------------------------------------------------


def _agregar_categorias(despesas) -> tuple[CategoriaAgregada, ...]:
    """Agrupa as despesas por categoria do Eithra, separando o comportamento.

    Só agrega o que foi lido: categoria sem lançamento não aparece. A ordenação
    é por valor, do maior para o menor — é a ordem em que a gestão olha.
    """
    acumulado: dict[str, dict] = {}
    for despesa in despesas:
        categoria = despesa.categoria_eithra or CategoriaEithra.NAO_CLASSIFICADO
        registro = acumulado.setdefault(
            categoria,
            {
                "recorrente": Decimal("0.00"),
                "extraordinaria": Decimal("0.00"),
                "nao_determinada": Decimal("0.00"),
                "total": Decimal("0.00"),
                "quantidade": 0,
            },
        )
        valor = texto_para_decimal(despesa.valor) or Decimal("0.00")
        registro["total"] += valor
        registro["quantidade"] += 1
        _acumular_por_natureza(registro, despesa.natureza, valor)

    return tuple(
        CategoriaAgregada(
            categoria=categoria,
            recorrente=decimal_para_texto(dados["recorrente"]),
            extraordinaria=decimal_para_texto(dados["extraordinaria"]),
            nao_determinada=decimal_para_texto(dados["nao_determinada"]),
            total=decimal_para_texto(dados["total"]),
            quantidade=dados["quantidade"],
        )
        for categoria, dados in sorted(
            acumulado.items(), key=lambda item: (-item[1]["total"], item[0])
        )
    )


def _acumular_por_natureza(registro: dict, natureza: str, valor: Decimal) -> None:
    """Soma o valor na coluna certa: recorrente, extraordinária ou a confirmar."""
    if natureza == NaturezaDespesa.EXTRAORDINARIA:
        registro["extraordinaria"] += valor
    elif natureza in (
        NaturezaDespesa.RECORRENTE_FIXA,
        NaturezaDespesa.RECORRENTE_VARIAVEL,
    ):
        registro["recorrente"] += valor
    else:
        registro["nao_determinada"] += valor


def _agregar_grupos(
    despesas, subtotais_grupos: dict[str, str]
) -> tuple[GrupoDeDespesa, ...]:
    """Confere cada agrupamento do documento contra o subtotal que ele declara.

    O agrupamento é o do próprio documento — a categoria original preservada —,
    e a categoria do Eithra entra ao lado, como classificação. O que o grupo
    afirma e o que a leitura encontrou ficam separados: a diferença entre os dois
    é o que a gestão precisa ver, e nenhum lançamento é inventado para fazê-la
    desaparecer.

    Um agrupamento que declara subtotal mas não rendeu lançamento aparece mesmo
    assim, com soma zero: é exatamente o caso em que "falta identificar" precisa
    ser dito.
    """
    acumulado: dict[str, dict] = {}
    for despesa in despesas:
        nome = despesa.categoria_original or ""
        registro = acumulado.setdefault(nome, _grupo_zerado())
        valor = texto_para_decimal(despesa.valor) or Decimal("0.00")
        registro["soma"] += valor
        registro["quantidade"] += 1
        _acumular_por_natureza(registro, despesa.natureza, valor)
        categoria = despesa.categoria_eithra or CategoriaEithra.NAO_CLASSIFICADO
        registro["por_categoria"][categoria] = (
            registro["por_categoria"].get(categoria, Decimal("0.00")) + valor
        )

    for nome in subtotais_grupos:
        acumulado.setdefault(nome, _grupo_zerado())

    grupos = tuple(
        _montar_grupo(nome, dados, subtotais_grupos.get(nome))
        for nome, dados in acumulado.items()
    )
    return tuple(
        sorted(
            grupos,
            key=lambda grupo: (-texto_para_decimal(grupo.soma_extraida), grupo.nome),
        )
    )


def _grupo_zerado() -> dict:
    """Um agrupamento recém-aberto, sem lançamento nenhum ainda."""
    return {
        "soma": Decimal("0.00"),
        "quantidade": 0,
        "recorrente": Decimal("0.00"),
        "extraordinaria": Decimal("0.00"),
        "nao_determinada": Decimal("0.00"),
        "por_categoria": {},
    }


def _montar_grupo(nome: str, dados: dict, declarado: str | None) -> GrupoDeDespesa:
    """Fecha um agrupamento: soma lida de um lado, subtotal declarado do outro.

    Quando o documento não declara subtotal, o estado é ``SEM_SUBTOTAL`` — não
    há o que conferir, e forjar um total seria pior do que admitir a ausência.
    """
    total_declarado = declarado
    diferenca: str | None = None
    status = StatusConferencia.SEM_SUBTOTAL

    valor_declarado = (
        texto_para_decimal(total_declarado) if total_declarado else None
    )
    if valor_declarado is not None:
        diferenca_valor = valor_declarado - dados["soma"]
        diferenca = decimal_para_texto(diferenca_valor)
        status = (
            StatusConferencia.CONFERE
            if abs(diferenca_valor) <= TOLERANCIA
            else StatusConferencia.FALTA_IDENTIFICAR
        )
    else:
        total_declarado = None

    return GrupoDeDespesa(
        nome=nome,
        categoria_eithra=_categoria_dominante(dados["por_categoria"]),
        total_declarado=total_declarado,
        soma_extraida=decimal_para_texto(dados["soma"]),
        diferenca=diferenca,
        status=status,
        quantidade=dados["quantidade"],
        recorrente=decimal_para_texto(dados["recorrente"]),
        extraordinaria=decimal_para_texto(dados["extraordinaria"]),
        nao_determinada=decimal_para_texto(dados["nao_determinada"]),
    )


def _categoria_dominante(por_categoria: dict[str, Decimal]) -> str:
    """A categoria do Eithra que mais pesou dentro do agrupamento."""
    if not por_categoria:
        return CategoriaEithra.NAO_CLASSIFICADO
    return min(por_categoria.items(), key=lambda item: (-item[1], item[0]))[0]


def _reconciliar_total_geral(
    grupos: tuple[GrupoDeDespesa, ...], despesas_total: str | None
) -> Inconsistencia | None:
    """Confere a soma dos subtotais declarados contra o total do documento.

    Só se aponta quando dá para afirmar: todos os agrupamentos precisam declarar
    o próprio subtotal. Se algum não declara, a soma não cobre o mês inteiro e
    comparar produziria um aviso falso — e silêncio continua sendo a resposta
    honesta.
    """
    total = texto_para_decimal(despesas_total) if despesas_total else None
    if total is None or not grupos:
        return None

    declarados = [texto_para_decimal(grupo.total_declarado) for grupo in grupos]
    if any(valor is None for valor in declarados):
        return None

    soma = sum(declarados, Decimal("0.00"))
    if abs(soma - total) <= TOLERANCIA:
        return None

    return Inconsistencia(
        tipo=TipoInconsistencia.TOTAL_DIVERGENTE,
        severidade=Severidade.MEDIA,
        mensagem=(
            f"A soma dos grupos declarados ({_moeda(soma)}) não confere com o "
            f"total do documento ({_moeda(total)})."
        ),
        referencia="grupos",
    )


def _somar_valores(valores) -> Decimal:
    """Soma decimal dos valores que a leitura conseguiu converter."""
    total = Decimal("0.00")
    for valor in valores:
        numero = texto_para_decimal(valor)
        if numero is not None:
            total += numero
    return total


def _moeda(valor: Decimal) -> str:
    """Formata um valor em reais para a mensagem exibida à gestão."""
    texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def _resumo(despesas, grupos, inconsistencias) -> ResumoExtraido:
    """Agregados que a tela mostra, derivados do que foi lido.

    Contagens e somas saem dos lançamentos — nada é estimado. As principais
    categorias aparecem com nome humano, nunca com o código técnico.
    """
    por_natureza = {
        "recorrente": Decimal("0.00"),
        "extraordinaria": Decimal("0.00"),
        "nao_determinada": Decimal("0.00"),
    }
    por_categoria: dict[str, Decimal] = {}

    for despesa in despesas:
        valor = texto_para_decimal(despesa.valor) or Decimal("0.00")
        registro = {
            "recorrente": por_natureza["recorrente"],
            "extraordinaria": por_natureza["extraordinaria"],
            "nao_determinada": por_natureza["nao_determinada"],
        }
        _acumular_por_natureza(registro, despesa.natureza, valor)
        por_natureza.update(registro)

        categoria = despesa.categoria_eithra or CategoriaEithra.NAO_CLASSIFICADO
        por_categoria[categoria] = por_categoria.get(categoria, Decimal("0.00")) + valor

    principais = tuple(
        rotulo(categoria)
        for categoria, _ in sorted(
            por_categoria.items(), key=lambda item: (-item[1], item[0])
        )[:3]
    )

    return ResumoExtraido(
        recorrente_total=decimal_para_texto(por_natureza["recorrente"]),
        extraordinaria_total=decimal_para_texto(por_natureza["extraordinaria"]),
        nao_determinada_total=decimal_para_texto(por_natureza["nao_determinada"]),
        despesas_identificadas=len(despesas),
        grupos_conferidos=sum(
            1 for grupo in grupos if grupo.status == StatusConferencia.CONFERE
        ),
        grupos_com_diferenca=sum(
            1 for grupo in grupos if grupo.status == StatusConferencia.FALTA_IDENTIFICAR
        ),
        pontos_de_atencao=len(inconsistencias),
        principais_categorias=principais,
    )


def _confianca(
    competencia: str | None, despesas, inconsistencias
) -> str:
    """Confiança determinística, tirada do que foi lido — nunca estimada.

    Um ponto de atenção de severidade alta derruba a confiança. Sem lançamento
    algum, a leitura é média: não se leu o bastante para confiar de todo, e não
    há erro que justifique desconfiar. Só é alta quando o documento entregou
    competência e lançamentos sem nada a apontar.
    """
    if any(item.severidade == Severidade.ALTA for item in inconsistencias):
        return Confianca.BAIXA
    if not despesas:
        return Confianca.MEDIA
    if competencia and not inconsistencias:
        return Confianca.ALTA
    return Confianca.MEDIA


# -- Gravação e mensagens -------------------------------------------------------


def _inconsistencia_para_dicionario(inconsistencia: Inconsistencia) -> dict:
    """Prepara o ponto de atenção para ir ao banco como JSON puro."""
    return asdict(inconsistencia)


def _motivo_de_recusa(layout: str) -> str:
    """Por que esta importação não é lida pelo modelo atual.

    A mensagem é para a gestão e diz o que vai acontecer, sem termo técnico.
    """
    if layout == LayoutBalanco.LEGADO:
        return "Este formato será processado pela leitura de documentos legados."
    return "Ainda não foi possível reconhecer o formato deste documento."


def _marcar_erro(importacao, mensagem: str) -> None:
    """Guarda a falha na importação, sem vazar segredo nem detalhe técnico."""
    importacao.status = StatusImportacao.ERRO
    importacao.erro_processamento = mensagem
    importacao.save(update_fields=["status", "erro_processamento", "atualizado_em"])
