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
* **dinheiro só com contexto.** Um número solto não é valor. Só vira lançamento
  a linha que traz valor com cara de dinheiro **dentro** de um bloco de receitas
  ou de despesas;
* **ausência é ausência.** O que o documento não declara fica vazio, e a dúvida
  vira ponto de atenção — nunca certeza inventada.

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
    InadimplenciaAgregada,
    Inconsistencia,
    Periodo,
    Procedencia,
    ReceitaExtraida,
    ResumoExtraido,
    SaldoExtraido,
    Severidade,
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
from apps.balancos.validacao_financeira import validar_balanco

# Quantas páginas de abertura a leitura varre. A extração precisa de mais
# páginas do que o detector, mas continua sem ler o arquivo inteiro: o
# demonstrativo estruturado está na abertura, e os anexos não entram na conta.
PAGINAS_PADRAO = 40

# Valor com cara de dinheiro no fim da linha: descrição à esquerda, valor à
# direita. É o formato de praticamente todo lançamento de demonstrativo.
# O ``(?<![\d.,/])`` impede que o valor comece no meio de um número ou de uma
# data — sem ele, ``10/08/2026`` terminaria com o "valor" 026.
_RE_VALOR_NO_FIM = re.compile(rf"(?<![\d.,/])(?P<valor>{PADRAO_VALOR})\s*$")

# Marcadores que identificam o começo de um bloco. A ordem importa: o específico
# vem antes do genérico ("receitas detalhadas" antes de "receitas").
SECOES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("receitas", ("receitas detalhadas", "receita detalhada", "receitas", "receita")),
    ("despesas", ("despesas detalhadas", "despesa detalhada", "despesas", "despesa")),
    (
        "resumo",
        ("resumo financeiro", "resumo de contas", "demonstrativo de contas"),
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
            "realizado",
            "indice",
        ),
    ),
)

# Blocos que a leitura deliberadamente não percorre.
SECOES_IGNORADAS = frozenset({"anexos"})

# Blocos de detalhe, onde um valor no fim da linha é um lançamento.
SECOES_DE_LANCAMENTO = frozenset({"receitas", "despesas"})

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

    base = BalancoExtraidoV1(
        competencia=competencia,
        periodo=periodo,
        receitas_total=varredura.receitas_total,
        despesas_total=varredura.despesas_total,
        saldo_anterior=varredura.saldo_anterior,
        saldo_final=varredura.saldo_final,
        saldos=tuple(varredura.saldos),
        categorias=_agregar_categorias(varredura.despesas),
        receitas=tuple(varredura.receitas),
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
    return replace(
        base,
        resumo=_resumo(varredura.receitas, varredura.despesas, inconsistencias),
        confianca=_confianca(competencia, varredura.receitas, varredura.despesas, inconsistencias),
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
        texto = leitor.pages[indice].extract_text() or ""
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
        elif _valor_no_fim(linha) is None:
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

    receitas: list[ReceitaExtraida] = field(default_factory=list)
    despesas: list[DespesaExtraida] = field(default_factory=list)
    saldos: list[SaldoExtraido] = field(default_factory=list)
    saldo_anterior: str | None = None
    saldo_final: str | None = None
    receitas_total: str | None = None
    despesas_total: str | None = None
    inadimplencia: InadimplenciaAgregada | None = None
    secoes: list[str] = field(default_factory=list)
    inconsistencias: list[Inconsistencia] = field(default_factory=list)


def _varrer(linhas: list[tuple[int, str]]) -> _Varredura:
    """Percorre as linhas, seguindo os blocos até os lançamentos."""
    varredura = _Varredura()
    secao: str | None = None
    grupo: str | None = None

    for pagina, linha in linhas:
        chave = chave_de_comparacao(linha)
        if not chave:
            continue

        token = _valor_no_fim(linha)
        dinheiro = token is not None and _parece_dinheiro(token)

        if not dinheiro:
            nome = _secao_da_linha(chave)
            if nome is not None:
                secao = nome
                grupo = None
                if nome not in varredura.secoes:
                    varredura.secoes.append(nome)
                continue
            if secao in SECOES_DE_LANCAMENTO and _parece_rotulo_de_grupo(linha, chave):
                grupo = linha
            continue

        descricao = normalizar_espacos(linha[: linha.rfind(token)]).strip()
        chave_desc = chave_de_comparacao(descricao)
        procedencia = _procedencia(pagina, secao, linha)

        if chave_desc.startswith(("total", "subtotal")):
            _registrar_total(varredura, chave_desc, token)
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

        if secao not in SECOES_DE_LANCAMENTO or secao in SECOES_IGNORADAS:
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
            varredura.receitas.append(
                _criar_receita(descricao, chave_desc, valor, procedencia)
            )
        else:
            varredura.despesas.append(
                _criar_despesa(descricao, chave_desc, valor, grupo, procedencia)
            )

    return varredura


def _criar_receita(
    descricao: str, chave_desc: str, valor: Decimal, procedencia: Procedencia
) -> ReceitaExtraida:
    return ReceitaExtraida(
        descricao_original=descricao,
        descricao_normalizada=chave_desc,
        valor=decimal_para_texto(valor),
        data=_data_do_lancamento(descricao),
        origem=None,
        categoria_original=None,
        confianca=Confianca.ALTA,
        procedencia=procedencia,
    )


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


def _valor_no_fim(linha: str) -> str | None:
    """O trecho com cara de valor no fim da linha, já limpo — ou ``None``.

    Um hífen solto antes dos dígitos é separador de frase, não sinal de valor:
    ``"manutenção - 1.234,56"`` não é despesa negativa. Só o hífen colado ao
    número vale como sinal.
    """
    achado = _RE_VALOR_NO_FIM.search(linha)
    if not achado:
        return None

    token = achado.group("valor").strip()
    if token.startswith("-") and not token[1:2].isdigit():
        token = token[1:].lstrip()
    return token or None


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


def _secao_da_linha(chave: str) -> str | None:
    """Qual bloco a linha marca, se ela marca algum.

    O marcador precisa ser a linha inteira: um cabeçalho de seção é um título
    curto, e não uma frase que por acaso cita "despesas". Sem essa exigência,
    um agrupamento como "despesas com pessoal" trocaria o bloco no meio da
    leitura e os lançamentos seguintes perderiam o agrupamento.
    """
    if not chave:
        return None
    for nome, marcadores in SECOES:
        if chave in marcadores:
            return nome
    return None


def _parece_rotulo_de_grupo(linha: str, chave: str) -> bool:
    """Diz se a linha é um agrupamento, e não um lançamento.

    Agrupamento é texto curto, sem valor no fim e sem cara de frase. Preservá-lo
    permite guardar o agrupamento do documento ao lado do Eithra.
    """
    if not chave or len(chave) > 60:
        return False
    if len(chave.split()) > 8:
        return False
    return _valor_no_fim(linha) is None


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


def _resumo(
    receitas, despesas, inconsistencias
) -> ResumoExtraido:
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
        receitas_identificadas=len(receitas),
        pontos_de_atencao=len(inconsistencias),
        principais_categorias=principais,
    )


def _confianca(
    competencia: str | None, receitas, despesas, inconsistencias
) -> str:
    """Confiança determinística, tirada do que foi lido — nunca estimada.

    Um ponto de atenção de severidade alta derruba a confiança. Sem lançamento
    algum, a leitura é média: não se leu o bastante para confiar de todo, e não
    há erro que justifique desconfiar. Só é alta quando o documento entregou
    competência e lançamentos sem nada a apontar.
    """
    if any(item.severidade == Severidade.ALTA for item in inconsistencias):
        return Confianca.BAIXA
    if not receitas and not despesas:
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
