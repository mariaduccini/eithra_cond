"""Contrato da leitura financeira do balanço — versão 1.

O resultado da leitura não é um dicionário solto: é este contrato. Ele diz o
que o Eithra entendeu de um documento, campo por campo, e é independente do
layout visual de qualquer administradora. Nenhum nome de fornecedor, de
condomínio ou de arquivo real vira regra aqui.

Três compromissos estão embutidos na estrutura:

* **ausência é ausência.** Campo que o documento não traz fica ``None``. Não se
  estima, não se completa por suposição e não se converte inferência em fato;
* **dinheiro é texto decimal.** Valores viajam como ``Decimal`` convertido em
  texto — nunca ``float``, para que o valor volte idêntico do banco;
* **todo dado relevante tem procedência.** Página, seção, método e um trecho
  curto de origem. O trecho é curto de propósito: o contrato registra de onde
  veio, não copia o documento para dentro do banco.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from django.db import models

from apps.balancos.categorias import CategoriaEithra
from apps.balancos.natureza_despesa import NaturezaDespesa

# Versão do contrato. Quando o formato dos dados mudar de forma incompatível,
# este número muda e o resultado antigo continua legível pelo seu próprio
# contrato.
SCHEMA_VERSAO = 1

# Versão do contrato do balanço confirmado. É um contrato próprio, e não uma
# cópia do contrato da leitura: o balanço confirmado carrega, além do que foi
# lido, a decisão da gestão e o quanto do total declarado ficou sem detalhe
# associado.
SCHEMA_VALIDADO = 1

# Tamanho máximo do trecho de origem guardado na procedência. O suficiente para
# reconhecer a linha, longe de ser uma cópia do documento.
LIMITE_DO_TRECHO = 160


class ErroDeContrato(Exception):
    """O conteúdo lido não respeita o contrato desta versão."""


class Confianca(models.TextChoices):
    """Quanto se pode confiar num dado lido.

    É determinístico, não estatístico: vem de a linha ter sido lida por inteiro,
    parcialmente ou com ambiguidade. Serve para o sistema decidir o que mostrar
    como seguro e o que mostrar como ponto de atenção — nunca é exibido como
    número para a gestão.
    """

    ALTA = "alta", "Alta"
    MEDIA = "media", "Média"
    BAIXA = "baixa", "Baixa"


class Severidade(models.TextChoices):
    """Quanto um ponto de atenção pesa antes da confirmação da gestão."""

    ALTA = "alta", "Alta"
    MEDIA = "media", "Média"
    BAIXA = "baixa", "Baixa"


class StatusConferencia(models.TextChoices):
    """Se um grupo de despesa fecha com o subtotal que o documento declarou.

    É o resultado da conferência por grupo — não do lançamento. ``SEM_SUBTOTAL``
    é uma resposta legítima: o documento não declarou subtotal do grupo, então
    não há o que conferir, e inventar um total para fechar seria pior do que
    admitir a ausência.
    """

    CONFERE = "confere", "Confere"
    FALTA_IDENTIFICAR = "falta_identificar", "Falta identificar"
    SEM_SUBTOTAL = "sem_subtotal", "Sem subtotal no documento"


class TipoInconsistencia(models.TextChoices):
    """Os problemas que a leitura sabe reconhecer e nomear."""

    TOTAL_DIVERGENTE = "total_divergente", "Total divergente"
    COMPETENCIA_CONFLITANTE = "competencia_conflitante", "Competência conflitante"
    VALOR_INVALIDO = "valor_invalido", "Valor inválido"
    LINHA_INCOMPLETA = "linha_incompleta", "Linha incompleta"
    CATEGORIA_NAO_DETERMINADA = "categoria_nao_determinada", "Categoria a classificar"
    NATUREZA_NAO_DETERMINADA = "natureza_nao_determinada", "Natureza a confirmar"
    FORNECEDOR_AMBIGUO = "fornecedor_ambiguo", "Fornecedor ambíguo"
    POSSIVEL_DUPLICIDADE = "possivel_duplicidade", "Possível duplicidade"


def trecho_curto(texto: str, *, limite: int = LIMITE_DO_TRECHO) -> str:
    """Guarda o começo da linha de origem, sem copiar o documento inteiro."""
    limpo = " ".join((texto or "").split())
    if len(limpo) <= limite:
        return limpo
    return limpo[: limite - 1].rstrip() + "…"


@dataclass(frozen=True)
class Procedencia:
    """De onde cada dado veio: página, seção, método e um trecho curto."""

    pagina: int | None = None
    secao: str = ""
    trecho: str = ""
    metodo: str = ""
    confianca: str = Confianca.MEDIA

    def __post_init__(self):
        object.__setattr__(self, "trecho", trecho_curto(self.trecho))


@dataclass(frozen=True)
class Periodo:
    """Intervalo coberto pelo documento, quando ele declara um."""

    inicio: str | None = None
    fim: str | None = None


@dataclass(frozen=True)
class ReceitaExtraida:
    """Uma entrada de dinheiro lida do demonstrativo."""

    descricao_original: str
    descricao_normalizada: str
    valor: str
    data: str | None = None
    origem: str | None = None
    categoria_original: str | None = None
    confianca: str = Confianca.MEDIA
    procedencia: Procedencia = field(default_factory=Procedencia)


@dataclass(frozen=True)
class DespesaExtraida:
    """Uma saída de dinheiro lida do demonstrativo.

    A categoria original do documento nunca é descartada: a categoria do Eithra
    é acrescentada ao lado, não no lugar dela.
    """

    descricao_original: str
    descricao_normalizada: str
    valor: str
    data: str | None = None
    fornecedor_candidato: str | None = None
    documento: str | None = None
    parcela: str | None = None
    categoria_original: str | None = None
    categoria_eithra: str = CategoriaEithra.NAO_CLASSIFICADO
    natureza: str = NaturezaDespesa.NAO_DETERMINADA
    # O sistema do prédio que a despesa alcança, quando dá para dizer. É uma
    # lente a mais sobre a mesma despesa — nunca substitui a categoria nem toca
    # no valor.
    sistema: str | None = None
    confianca: str = Confianca.MEDIA
    procedencia: Procedencia = field(default_factory=Procedencia)


@dataclass(frozen=True)
class SaldoExtraido:
    """Um saldo por conta ou fundo, como o documento apresentar.

    ``valor`` é o saldo que a linha mostra, com a identificação escrita pelo
    próprio documento. Quando o documento separa a abertura do fechamento — uma
    tabela com saldo anterior e saldo atual —, os dois ficam guardados; sem essa
    separação eles ficam vazios, e o valor único continua sendo o que o
    documento disse, sem virar dois.
    """

    identificacao: str
    valor: str
    saldo_anterior: str | None = None
    saldo_final: str | None = None
    procedencia: Procedencia = field(default_factory=Procedencia)


@dataclass(frozen=True)
class InadimplenciaAgregada:
    """Inadimplência do mês, apenas em agregado.

    Nunca por unidade: o que a gestão acompanha aqui é o total do condomínio, e
    o contrato não deve carregar dado de morador.
    """

    valor: str | None = None
    percentual: str | None = None
    quantidade: int | None = None
    procedencia: Procedencia = field(default_factory=Procedencia)


@dataclass(frozen=True)
class CategoriaAgregada:
    """Quanto cada categoria representou no mês, separado por comportamento."""

    categoria: str
    recorrente: str
    extraordinaria: str
    nao_determinada: str
    total: str
    quantidade: int = 0


@dataclass(frozen=True)
class GrupoDeDespesa:
    """Um agrupamento de despesas do documento, conferido contra o seu subtotal.

    O total declarado e a soma dos lançamentos são conceitos diferentes e não se
    misturam: o primeiro é o que o documento afirma; o segundo é o que a leitura
    encontrou. A diferença entre os dois é o que a gestão precisa ver — nunca um
    lançamento inventado para fazer o grupo fechar.

    ``nome`` é o rótulo do próprio documento (a categoria original preservada) e
    fica vazio quando o documento não agrupou aquela despesa.
    """

    nome: str = ""
    categoria_eithra: str = CategoriaEithra.NAO_CLASSIFICADO
    total_declarado: str | None = None
    soma_extraida: str = "0.00"
    diferenca: str | None = None
    status: str = StatusConferencia.SEM_SUBTOTAL
    quantidade: int = 0
    recorrente: str = "0.00"
    extraordinaria: str = "0.00"
    nao_determinada: str = "0.00"


@dataclass(frozen=True)
class Inconsistencia:
    """Um ponto concreto que merece atenção da gestão.

    Não é erro de sistema: é o resultado honesto de uma leitura que encontrou
    algo duvidoso. É a base da conferência por exceção.
    """

    tipo: str
    severidade: str
    mensagem: str
    referencia: str = ""
    procedencia: Procedencia = field(default_factory=Procedencia)


@dataclass(frozen=True)
class ResumoExtraido:
    """Agregados que a tela mostra, derivados dos lançamentos lidos."""

    recorrente_total: str = "0.00"
    extraordinaria_total: str = "0.00"
    nao_determinada_total: str = "0.00"
    despesas_identificadas: int = 0
    receitas_identificadas: int = 0
    grupos_conferidos: int = 0
    grupos_com_diferenca: int = 0
    pontos_de_atencao: int = 0
    principais_categorias: tuple[str, ...] = ()


@dataclass(frozen=True)
class BalancoExtraidoV1:
    """Raiz da leitura de um balanço do modelo atual.

    ``despesas_total`` é o total **declarado** pelo documento, e
    ``soma_despesas_extraida`` é o que a leitura **identificou** lançamento a
    lançamento. São conceitos diferentes de propósito: a diferença entre eles é
    o valor que ainda não foi explicado, e some-la ao total seria esconder o
    problema em vez de mostrá-lo.
    """

    schema_versao: int = SCHEMA_VERSAO
    competencia: str | None = None
    periodo: Periodo = field(default_factory=Periodo)
    receitas_total: str | None = None
    despesas_total: str | None = None
    soma_despesas_extraida: str = "0.00"
    saldo_anterior: str | None = None
    saldo_final: str | None = None
    saldos: tuple[SaldoExtraido, ...] = ()
    categorias: tuple[CategoriaAgregada, ...] = ()
    grupos: tuple[GrupoDeDespesa, ...] = ()
    receitas: tuple[ReceitaExtraida, ...] = ()
    despesas: tuple[DespesaExtraida, ...] = ()
    inadimplencia_agregada: InadimplenciaAgregada | None = None
    resumo: ResumoExtraido = field(default_factory=ResumoExtraido)
    provenance: dict = field(default_factory=dict)
    confianca: str = Confianca.MEDIA


@dataclass(frozen=True)
class BalancoValidadoV1:
    """O balanço confirmado pela gestão — a leitura mais a decisão dela.

    Confirmar não é exigir que todo lançamento tenha sido compreendido. O que o
    documento **declara** como total oficial e o que a leitura **identificou**
    como detalhe são duas coisas diferentes, e a diferença entre elas continua
    visível aqui: ``valor_nao_associado`` é exatamente essa parte do total
    declarado que não ficou presa a nenhum lançamento lido.

    ``confirmado_com_ressalvas`` registra que o balanço passou a valer sem que
    essa diferença tenha desaparecido. A confirmação não remove inconsistência:
    ela assume o que ainda está aberto, e o que estava apontado continua
    apontado.

    ``leitura`` guarda o contrato da leitura inteiro — categorias, despesas,
    natureza, grupos, reconciliação, saldos e procedência — sob o seu próprio
    ``origem_schema_versao``: é o estado oficial do mês, não um resumo dele.
    """

    schema_versao: int = SCHEMA_VALIDADO
    origem_schema_versao: int = SCHEMA_VERSAO
    competencia: str | None = None
    receitas_total: str | None = None
    despesas_total: str | None = None
    saldo_anterior: str | None = None
    saldo_final: str | None = None
    total_declarado_despesas: str | None = None
    total_detalhado_identificado: str = "0.00"
    valor_nao_associado: str = "0.00"
    percentual_detalhamento: str | None = None
    confirmado_com_ressalvas: bool = False
    confirmado_em: str | None = None
    leitura: dict = field(default_factory=dict)


# -- Serialização ---------------------------------------------------------------


def para_dicionario(balanco: BalancoExtraidoV1) -> dict:
    """Prepara o contrato para ir ao banco.

    As tuplas viram listas e os enums continuam texto — o que sai daqui é JSON
    puro, sem objeto de Python pendurado no meio.
    """
    return _json_puro(asdict(balanco))


def _json_puro(valor):
    """Deixa o resultado pronto para JSON: ``asdict`` não converte tuplas.

    A conversão é recursiva porque o contrato tem listas dentro de listas —
    lançamentos, saldos, categorias e pontos de atenção.
    """
    if isinstance(valor, dict):
        return {chave: _json_puro(item) for chave, item in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_json_puro(item) for item in valor]
    return valor


def validar_dicionario(dados) -> None:
    """Confere a forma do contrato antes de confiar nele.

    Levanta ``ErroDeContrato`` em vez de tentar consertar: contrato inválido é
    sinal de que algo saiu errado antes, e adivinhar aqui esconderia o problema.
    """
    if not isinstance(dados, dict):
        raise ErroDeContrato("O resultado da leitura precisa ser um objeto.")

    versao = dados.get("schema_versao")
    if versao != SCHEMA_VERSAO:
        raise ErroDeContrato(
            f"Versão de contrato inesperada: {versao!r}. Esperada: {SCHEMA_VERSAO}."
        )

    for nome in ("saldos", "categorias", "grupos", "receitas", "despesas"):
        if not isinstance(dados.get(nome), list):
            raise ErroDeContrato(f"O campo '{nome}' precisa ser uma lista.")

    if not isinstance(dados.get("provenance"), dict):
        raise ErroDeContrato("O campo 'provenance' precisa ser um objeto.")

    competencia = dados.get("competencia")
    if competencia is not None:
        _conferir_competencia(competencia)


def _conferir_competencia(competencia: str) -> None:
    try:
        data = date.fromisoformat(competencia)
    except (TypeError, ValueError) as erro:
        raise ErroDeContrato("A competência precisa estar em formato ISO.") from erro

    if data.day != 1:
        raise ErroDeContrato("A competência representa o mês: use o primeiro dia.")


def _resumo_reconstruido(dados: dict | None) -> ResumoExtraido:
    """Reconstrói o resumo mantendo o tipo do contrato.

    As categorias principais viajam como lista no JSON e voltam como tupla: o
    contrato é imutável, e a reconstrução devolve o mesmo tipo que a leitura
    produziu — como já acontece com saldos, receitas, despesas e categorias.
    """
    dados = dict(dados or {})
    dados["principais_categorias"] = tuple(dados.get("principais_categorias") or ())
    return ResumoExtraido(**dados)


def de_dicionario(dados) -> BalancoExtraidoV1:
    """Reconstrói o contrato a partir do que está guardado.

    Tolerante com o que ainda não existe — uma importação recebida antes desta
    fase tem ``resultado_extraido`` vazio — e fiel ao que existe.
    """
    if not dados:
        return BalancoExtraidoV1()

    return BalancoExtraidoV1(
        schema_versao=int(dados.get("schema_versao", SCHEMA_VERSAO)),
        competencia=dados.get("competencia"),
        periodo=Periodo(**(dados.get("periodo") or {})),
        receitas_total=dados.get("receitas_total"),
        despesas_total=dados.get("despesas_total"),
        soma_despesas_extraida=dados.get("soma_despesas_extraida", "0.00"),
        saldo_anterior=dados.get("saldo_anterior"),
        saldo_final=dados.get("saldo_final"),
        saldos=tuple(
            SaldoExtraido(
                identificacao=item.get("identificacao", ""),
                valor=item.get("valor", ""),
                saldo_anterior=item.get("saldo_anterior"),
                saldo_final=item.get("saldo_final"),
                procedencia=Procedencia(**(item.get("procedencia") or {})),
            )
            for item in dados.get("saldos") or []
        ),
        categorias=tuple(
            CategoriaAgregada(
                categoria=item.get("categoria", ""),
                recorrente=item.get("recorrente", "0.00"),
                extraordinaria=item.get("extraordinaria", "0.00"),
                nao_determinada=item.get("nao_determinada", "0.00"),
                total=item.get("total", "0.00"),
                quantidade=int(item.get("quantidade", 0) or 0),
            )
            for item in dados.get("categorias") or []
        ),
        grupos=tuple(
            GrupoDeDespesa(
                nome=item.get("nome", ""),
                categoria_eithra=item.get(
                    "categoria_eithra", CategoriaEithra.NAO_CLASSIFICADO
                ),
                total_declarado=item.get("total_declarado"),
                soma_extraida=item.get("soma_extraida", "0.00"),
                diferenca=item.get("diferenca"),
                status=item.get("status", StatusConferencia.SEM_SUBTOTAL),
                quantidade=int(item.get("quantidade", 0) or 0),
                recorrente=item.get("recorrente", "0.00"),
                extraordinaria=item.get("extraordinaria", "0.00"),
                nao_determinada=item.get("nao_determinada", "0.00"),
            )
            for item in dados.get("grupos") or []
        ),
        receitas=tuple(
            ReceitaExtraida(
                descricao_original=item.get("descricao_original", ""),
                descricao_normalizada=item.get("descricao_normalizada", ""),
                valor=item.get("valor", ""),
                data=item.get("data"),
                origem=item.get("origem"),
                categoria_original=item.get("categoria_original"),
                confianca=item.get("confianca", Confianca.MEDIA),
                procedencia=Procedencia(**(item.get("procedencia") or {})),
            )
            for item in dados.get("receitas") or []
        ),
        despesas=tuple(
            DespesaExtraida(
                descricao_original=item.get("descricao_original", ""),
                descricao_normalizada=item.get("descricao_normalizada", ""),
                valor=item.get("valor", ""),
                data=item.get("data"),
                fornecedor_candidato=item.get("fornecedor_candidato"),
                documento=item.get("documento"),
                parcela=item.get("parcela"),
                categoria_original=item.get("categoria_original"),
                categoria_eithra=item.get(
                    "categoria_eithra", CategoriaEithra.NAO_CLASSIFICADO
                ),
                natureza=item.get("natureza", NaturezaDespesa.NAO_DETERMINADA),
                sistema=item.get("sistema"),
                confianca=item.get("confianca", Confianca.MEDIA),
                procedencia=Procedencia(**(item.get("procedencia") or {})),
            )
            for item in dados.get("despesas") or []
        ),
        inadimplencia_agregada=(
            InadimplenciaAgregada(**(dados.get("inadimplencia_agregada") or {}))
            if dados.get("inadimplencia_agregada")
            else None
        ),
        resumo=_resumo_reconstruido(dados.get("resumo")),
        provenance=dict(dados.get("provenance") or {}),
        confianca=dados.get("confianca", Confianca.MEDIA),
    )


# -- Contrato do balanço confirmado ---------------------------------------------


def para_dicionario_validado(validado: BalancoValidadoV1) -> dict:
    """Prepara o balanço confirmado para ir ao banco, como JSON puro."""
    return _json_puro(asdict(validado))


def validar_dicionario_validado(dados) -> None:
    """Confere a forma do balanço confirmado antes de confiar nele.

    A leitura embutida é conferida pelo contrato dela, sob a sua própria
    versão: um balanço confirmado cujo detalhe não respeita o contrato da
    leitura é sinal de que algo saiu errado antes, e adivinhar aqui esconderia
    o problema.
    """
    if not isinstance(dados, dict):
        raise ErroDeContrato("O balanço confirmado precisa ser um objeto.")

    versao = dados.get("schema_versao")
    if versao != SCHEMA_VALIDADO:
        raise ErroDeContrato(
            f"Versão do balanço confirmado inesperada: {versao!r}. "
            f"Esperada: {SCHEMA_VALIDADO}."
        )

    if int(dados.get("origem_schema_versao", 0) or 0) != SCHEMA_VERSAO:
        raise ErroDeContrato("A leitura embutida não é da versão esperada.")

    leitura = dados.get("leitura")
    if not isinstance(leitura, dict):
        raise ErroDeContrato("O campo 'leitura' precisa ser um objeto.")
    validar_dicionario(leitura)


def de_dicionario_validado(dados) -> BalancoValidadoV1:
    """Reconstrói o balanço confirmado a partir do que está guardado.

    Tolerante com o que ainda não existe, como o resto do contrato: quem lê um
    balanço gravado antes desta fase recebe o contrato vazio, e não um erro.
    """
    if not dados:
        return BalancoValidadoV1()

    leitura = dados.get("leitura") or {}
    return BalancoValidadoV1(
        schema_versao=int(dados.get("schema_versao", SCHEMA_VALIDADO) or SCHEMA_VALIDADO),
        origem_schema_versao=int(
            dados.get("origem_schema_versao", SCHEMA_VERSAO) or SCHEMA_VERSAO
        ),
        competencia=dados.get("competencia"),
        receitas_total=dados.get("receitas_total"),
        despesas_total=dados.get("despesas_total"),
        saldo_anterior=dados.get("saldo_anterior"),
        saldo_final=dados.get("saldo_final"),
        total_declarado_despesas=dados.get("total_declarado_despesas"),
        total_detalhado_identificado=dados.get("total_detalhado_identificado", "0.00"),
        valor_nao_associado=dados.get("valor_nao_associado", "0.00"),
        percentual_detalhamento=dados.get("percentual_detalhamento"),
        confirmado_com_ressalvas=bool(dados.get("confirmado_com_ressalvas", False)),
        confirmado_em=dados.get("confirmado_em"),
        leitura=dict(leitura),
    )
