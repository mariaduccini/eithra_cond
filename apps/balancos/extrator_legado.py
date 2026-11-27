"""Leitura financeira do balancete antigo (Métrica Condominial) — fase 1.

O balancete da administradora anterior chega como PDF escaneado, sem camada
textual, e com um layout que não é o do modelo atual. Ler esse documento é o que
este módulo faz: reconhece o texto pela mesma leitura local já usada nos
comprovantes de compra (nada sai do servidor) e devolve o mesmo contrato
``BalancoExtraidoV1`` do modelo atual — para que o mês antigo e o mês novo vivam
no mesmo lugar, com a mesma conferência.

Quatro compromissos governam a leitura:

* **só o demonstrativo sintético.** A fase 1 lê a abertura do documento — saldo
  anterior, totais de receitas e de despesas, movimento do mês e saldo final, e
  as linhas de receitas e despesas por grupo. Não se tenta ler anexo, comprovante
  nem movimentação por unidade;
* **OCR é aquisição, não decisão.** O reconhecimento traz o texto; o que ele diz
  é lido por regras determinísticas. Nenhuma IA decide valor, categoria ou total;
* **totais oficiais, não somas.** O total de receitas e o de despesas vêm do que
  o demonstrativo declara. A soma das linhas é conferida ao lado, e a diferença
  entre as duas coisas aparece — nunca é escondida;
* **o que não mapeia vira "Outros".** A categoria do documento antigo não segue o
  catálogo do Eithra; o que não encontra regra vai para "Outros", em vez de ficar
  sem classificação e sumir dos agregados.

Nada aqui cria ``BalancoMensal``: a importação continua sendo o objeto
transitório, e o mês só passa a valer quando a gestão conferir e publicar.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from apps.balancos.categorias import CategoriaEithra, categorizar, sistema_para
from apps.balancos.constants import LayoutBalanco, StatusImportacao
from apps.balancos.contratos import (
    SCHEMA_VERSAO,
    BalancoExtraidoV1,
    Confianca,
    DespesaExtraida,
    ErroDeContrato,
    Inconsistencia,
    Periodo,
    Procedencia,
    ReceitaExtraida,
    ResumoExtraido,
    Severidade,
    TipoInconsistencia,
    para_dicionario,
    validar_dicionario,
)
from apps.balancos.detector import ErroDeDeteccaoDeLayout, _baixar_pdf_privado
from apps.balancos.extrator_atual import (
    ErroDeExtracao,
    ResultadoDaExtracao,
    _agregar_categorias,
    _agregar_grupos,
    _confianca,
    _inconsistencia_para_dicionario,
    _marcar_erro,
    _moeda,
    _resumo,
    _somar_valores,
)
from apps.balancos.natureza_despesa import classificar_natureza
from apps.balancos.normalizadores import (
    PADRAO_VALOR,
    chave_de_comparacao,
    decimal_para_texto,
    normalizar_espacos,
    texto_para_competencia,
    texto_para_decimal,
)
from apps.balancos.validacao_financeira import validar_balanco

# O demonstrativo sintético cabe na abertura do documento; os anexos por
# unidade vêm depois. A leitura vai página a página e para no saldo de
# fechamento — ou antes de uma página que já é listagem de unidades. O limite
# de seis páginas existe só para o caso de o documento não se fechar sozinho:
# ler o arquivo inteiro custaria caro e traria dado de morador para dentro do
# sistema sem servir à gestão.
PAGINAS_MAX_LEGADO = 6

# Valor com cara de dinheiro, na mesma regra do modelo atual: recusa data,
# percentual e número colado.
_RE_VALOR = re.compile(
    rf"(?<![\d.,/])(?P<valor>{PADRAO_VALOR})(?![\d/])(?![.,]\d)(?!\s*%)"
)

# A coluna de percentual nunca é valor — mesmo com ponto ("58.78%") ela é
# descartada da descrição e ignorada na leitura do lançamento.
_RE_PERCENTUAL = re.compile(r"\d+(?:[.,]\d+)?\s*%")

# O OCR troca a vírgula decimal por ponto. Quando o último separador da linha
# é um ponto seguido de exatamente dois dígitos no fim, ele é decimal:
# "152.60" é 152,60 e "11.070.00" é 11.070,00.
_RE_PONTO_DECIMAL_FINAL = re.compile(r"\.(\d{2})$")

# Código de agrupamento no começo da linha: "2.81 DESPESAS", "1.10AGUA".
_RE_CODIGO_INICIAL = re.compile(r"^\d+(?:[.,]\d+)*\s*")

# "Total de<X>": o subtotal de um grupo ou de uma seção. O OCR cola o rótulo ao
# nome ("Total deRECEITA", "Total deDESPESASBANCARIAS"), por isso a leitura é
# feita sobre a chave sem espaços.
_RE_TOTAL_DE = re.compile(r"^totalde([a-z]+)")

# "Saldo em 31/12/2023": a abertura e o fechamento do demonstrativo. O OCR
# costuma colar as palavras ("Saldoem31/12/2023"), por isso o \s* entre elas.
_RE_SALDO_EM = re.compile(
    r"saldo\s*em\s*(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{2,4})", re.IGNORECASE
)

# Linha que traz só um valor, sem descrição — é assim que o saldo costuma
# aparecer logo abaixo do rótulo.
_RE_SO_VALOR = re.compile(rf"^{PADRAO_VALOR}$")

# Linha de anexo por unidade ("apto 103"). Igual à regra da privacidade, mas
# local: aqui ela serve para a leitura parar antes do anexo, não para esconder.
_RE_UNIDADE = re.compile(r"\bap(?:to|artamento|art)?\.?\s*n?[º°o]?\.?\s*\d+")

# Rótulos com que o demonstrativo nomeia os campos do resumo. A comparação é
# feita sobre a chave normalizada (sem acento, em minúsculas), por isso os
# rótulos aqui já vêm sem acento. São termos de documento, não nomes próprios.
_ROTULOS: dict[str, tuple[str, ...]] = {
    "saldo_anterior": (
        "saldo anterior",
        "saldo do mes anterior",
        "saldo inicial",
        "saldo anterior do mes",
    ),
    "saldo_final": (
        "saldo final",
        "saldo atual",
        "saldo ao final",
        "saldo final do mes",
        "saldo a transportar",
        "saldo para o proximo mes",
    ),
    "receitas_total": (
        "total de receitas",
        "total de receita",
        "total das receitas",
        "total receitas",
        "total receita",
        "receitas total",
        "subtotal de receitas",
        "subtotal das receitas",
    ),
    "despesas_total": (
        "total de despesas",
        "total de despesa",
        "total das despesas",
        "total despesas",
        "total despesa",
        "despesas total",
        "subtotal de despesas",
        "subtotal das despesas",
    ),
    "movimento": (
        "movimento liquido",
        "movimento do mes",
        "movimento do periodo",
        "resultado do mes",
        "resultado do periodo",
        "resultado liquido",
    ),
}

# Cabeçalho de seção: a linha que só anuncia o bloco, sem valor. É ela que diz
# se a linha seguinte é receita ou despesa.
_SECOES = {
    "receitas": "receitas",
    "receita": "receitas",
    "despesas": "despesas",
    "despesa": "despesas",
}

METODO_LEGADO = "demonstrativo do balancete antigo (OCR local)"
METODO_CONFERENCIA = "conferencia da gestao"

# Diferença acima da qual um subtotal de grupo vira ponto de atenção. É mais
# folgada que a tolerância do centavo de propósito: aqui o OCR pode ter errado
# um dígito, e R$ 1,00 separa o ruído de leitura de uma falta real.
LIMITE_DO_SUBTOTAL = Decimal("1.00")


# -- Entrada pública ------------------------------------------------------------


def extrair_balanco_legado(
    importacao, *, conteudo: bytes | None = None, provider=None
) -> ResultadoDaExtracao:
    """Lê o balancete antigo e grava o resultado na importação.

    Só o layout legado é lido aqui. Qualquer outro devolve o motivo e deixa a
    importação como estava. Nenhum ``BalancoMensal`` nasce nesta função.
    """
    layout = importacao.layout_detectado
    if layout != LayoutBalanco.LEGADO:
        return ResultadoDaExtracao(
            processado=False, layout=layout, motivo=_motivo_de_recusa(layout)
        )

    importacao.status = StatusImportacao.PROCESSANDO
    importacao.erro_processamento = ""
    importacao.save(update_fields=["status", "erro_processamento", "atualizado_em"])

    try:
        if conteudo is None:
            conteudo = _obter_conteudo(importacao, provider)
        balanco, inconsistencias = ler_balanco_legado(
            conteudo, nome_arquivo=importacao.nome_original
        )
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
    importacao.inconsistencias = [
        _inconsistencia_para_dicionario(item) for item in inconsistencias
    ]
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


def ler_balanco_legado(
    conteudo: bytes,
    *,
    nome_arquivo: str | None = None,
) -> tuple[BalancoExtraidoV1, tuple[Inconsistencia, ...]]:
    """Lê um balancete escaneado e devolve o contrato já conferido.

    Função pura em relação ao banco: recebe bytes e não persiste nada — o que a
    torna exercitável com documentos sintéticos, sem storage. O nome do arquivo
    entra como pista de competência: é o segundo lugar onde o mês pode aparecer,
    depois do cabeçalho do próprio documento.
    """
    texto, paginas_lidas = _texto_do_documento(conteudo)
    if not texto.strip():
        raise ErroDeExtracao(
            "Não foi possível reconhecer texto no balancete enviado. Confira o "
            "arquivo e tente novamente."
        )
    return ler_balanco_legado_do_texto(
        texto, nome_arquivo=nome_arquivo, paginas_lidas=paginas_lidas
    )


def ler_balanco_legado_do_texto(
    texto: str,
    *,
    nome_arquivo: str | None = None,
    paginas_lidas: int | None = None,
) -> tuple[BalancoExtraidoV1, tuple[Inconsistencia, ...]]:
    """Lê o demonstrativo a partir do texto já reconhecido.

    Separada da leitura do PDF de propósito: o reconhecimento do texto é o único
    passo que depende do OCR, e o resto da leitura é determinístico e testável
    sem imagem.
    """
    varredura = _varrer(texto, nome_arquivo=nome_arquivo)

    receitas = tuple(varredura.receitas)
    despesas = tuple(varredura.despesas)
    grupos = _agregar_grupos(despesas, varredura.subtotais_grupos)
    soma_despesas = _somar_valores(item.valor for item in despesas)

    base = BalancoExtraidoV1(
        competencia=varredura.competencia,
        periodo=varredura.periodo,
        receitas_total=varredura.receitas_total,
        despesas_total=varredura.despesas_total,
        soma_despesas_extraida=decimal_para_texto(soma_despesas),
        saldo_anterior=varredura.saldo_anterior,
        saldo_final=varredura.saldo_final,
        categorias=_agregar_categorias(despesas),
        grupos=grupos,
        receitas=receitas,
        despesas=despesas,
        resumo=ResumoExtraido(),
        provenance={
            "metodo": METODO_LEGADO,
            "layout": "legado",
            "fase": 1,
            "paginas_lidas": (
                paginas_lidas if paginas_lidas is not None else PAGINAS_MAX_LEGADO
            ),
            "secoes_encontradas": list(varredura.secoes),
        },
        confianca=Confianca.MEDIA,
    )

    inconsistencias = tuple(
        [
            *varredura.inconsistencias,
            *_subtotais_divergentes(grupos),
            *validar_balanco(base),
        ]
    )
    return (
        replace(
            base,
            resumo=_resumo(despesas, grupos, inconsistencias),
            confianca=_confianca(varredura.competencia, despesas, inconsistencias),
        ),
        inconsistencias,
    )


def _texto_da_competencia(competencia) -> str | None:
    """A competência conferida como texto ISO do contrato.

    A tela monta a competência como ``date``; o contrato guarda texto. Data vira
    ``AAAA-MM-DD`` e o que não for data passa como veio — ``None`` segue ausente.
    """
    if isinstance(competencia, date):
        return competencia.isoformat()
    return competencia


def montar_balanco_da_conferencia(
    *,
    competencia: str | None,
    saldo_anterior: str | None,
    receitas_total: str | None,
    despesas_total: str | None,
    saldo_final: str | None,
    receitas: list[tuple[str, str]],
    despesas: list[tuple[str, str]],
) -> BalancoExtraidoV1:
    """Monta o contrato a partir do que a gestão conferiu na tela.

    Vale tanto para o balancete antigo conferido quanto para um mês lançado sem
    PDF: em ambos, os valores são os que a gestão decidiu, e as linhas são
    opcionais. A categoria segue a mesma regra da leitura — o que não mapeia vira
    "Outros".
    """
    receitas_extraidas = tuple(
        _montar_receita(descricao, valor, metodo=METODO_CONFERENCIA)
        for descricao, valor in receitas
        if (descricao or "").strip()
    )
    despesas_extraidas = tuple(
        _montar_despesa(descricao, valor, metodo=METODO_CONFERENCIA)
        for descricao, valor in despesas
        if (descricao or "").strip()
    )
    soma_despesas = _somar_valores(item.valor for item in despesas_extraidas)

    base = BalancoExtraidoV1(
        competencia=_texto_da_competencia(competencia),
        receitas_total=receitas_total,
        despesas_total=despesas_total,
        soma_despesas_extraida=decimal_para_texto(soma_despesas),
        saldo_anterior=saldo_anterior,
        saldo_final=saldo_final,
        categorias=_agregar_categorias(despesas_extraidas),
        receitas=receitas_extraidas,
        despesas=despesas_extraidas,
        provenance={"metodo": METODO_CONFERENCIA, "origem": "conferencia"},
    )
    inconsistencias = validar_balanco(base)
    return replace(
        base,
        resumo=_resumo(despesas_extraidas, (), inconsistencias),
        confianca=_confianca(competencia, despesas_extraidas, inconsistencias),
    )


# -- Acesso ao texto ------------------------------------------------------------


def _obter_conteudo(importacao, provider) -> bytes:
    """Traz o PDF privado pelo caminho já homologado do detector."""
    try:
        conteudo, _ = _baixar_pdf_privado(importacao, provider=provider)
    except ErroDeDeteccaoDeLayout as erro:
        raise ErroDeExtracao(str(erro)) from erro
    return conteudo


def _texto_do_documento(conteudo: bytes) -> tuple[str, int]:
    """Texto do balancete: a camada textual, quando existe; senão, o OCR local.

    Um balancete digitalizado não traz texto — o OCR reconhece as páginas do
    demonstrativo, uma a uma, parando no saldo de fechamento ou antes de um
    anexo por unidade. Um balancete que já venha com texto é lido direto, sem o
    custo do reconhecimento. Devolve o texto e quantas páginas foram lidas.
    """
    nativo, paginas_nativas = _texto_nativo(conteudo)
    if _texto_suficiente(nativo):
        return nativo, paginas_nativas
    return _paginas_por_ocr(
        conteudo,
        parar_pagina=_pagina_de_anexos,
        parar_texto=_saldo_de_fechamento_lido,
    )


def _texto_nativo(conteudo: bytes) -> tuple[str, int]:
    """Camada textual das primeiras páginas, quando o PDF tem uma."""
    try:
        leitor = PdfReader(io.BytesIO(conteudo))
    except (PdfReadError, ValueError) as erro:
        raise ErroDeExtracao("Não foi possível ler o PDF do balancete.") from erro

    if getattr(leitor, "is_encrypted", False):
        try:
            leitor.decrypt("")
        except Exception as erro:  # pragma: no cover - depende do PDF real
            raise ErroDeExtracao("O PDF está protegido e não pode ser lido.") from erro

    paginas = []
    for indice in range(min(PAGINAS_MAX_LEGADO, len(leitor.pages))):
        paginas.append(leitor.pages[indice].extract_text() or "")
    return "\n".join(paginas).strip(), len(paginas)


def _texto_suficiente(texto: str) -> bool:
    """Se a camada textual tem corpo de documento, e não um rodapé solto."""
    linhas_uteis = [linha for linha in (texto or "").splitlines() if linha.strip()]
    return len(texto.strip()) >= 40 and len(linhas_uteis) >= 3


def _pagina_de_anexos(pagina: str) -> bool:
    """Se a página já é listagem por unidade, e não o demonstrativo.

    O anexo por unidade vem depois do fechamento e só traz dado de morador: a
    leitura para antes dele, pelo mesmo motivo que a privacidade o esconde.
    """
    linhas = [linha for linha in (pagina or "").splitlines() if linha.strip()]
    if len(linhas) < 3:
        return False
    com_unidade = sum(1 for linha in linhas if _RE_UNIDADE.search(linha))
    return com_unidade * 2 > len(linhas)


def _saldo_de_fechamento_lido(texto: str) -> bool:
    """Se o texto acumulado já traz o saldo de fechamento do demonstrativo.

    A abertura traz um "Saldo em"; o fechamento traz o segundo. Achado o
    segundo, não há por que seguir lendo as páginas seguintes.
    """
    return len(_RE_SALDO_EM.findall(texto or "")) >= 2


def _paginas_por_ocr(
    conteudo: bytes, *, parar_pagina=None, parar_texto=None
) -> tuple[str, int]:
    """Texto e número real de páginas lidas pelo OCR do demonstrativo.

    A contagem é a das páginas efetivamente lidas, e não o limite: dizer "6
    páginas" quando o documento fechou na terceira seria inventar leitura na
    procedência.
    """
    return _ler_paginas_por_ocr(
        conteudo,
        maximo=PAGINAS_MAX_LEGADO,
        parar_pagina=parar_pagina,
        parar_texto=parar_texto,
    )


def _texto_por_ocr(
    conteudo: bytes,
    *,
    paginas: int = PAGINAS_MAX_LEGADO,
    parar_pagina=None,
    parar_texto=None,
) -> str:
    """Reaproveita o OCR local do estoque, página a página.

    A leitura do demonstrativo começa na primeira página e segue até o saldo de
    fechamento — ou para antes de um anexo por unidade. O detector de layout só
    precisa do rótulo do documento e pede uma página só, sem parada.
    """
    texto, _ = _ler_paginas_por_ocr(
        conteudo, maximo=paginas, parar_pagina=parar_pagina, parar_texto=parar_texto
    )
    return texto


def _ler_paginas_por_ocr(
    conteudo: bytes, *, maximo: int, parar_pagina=None, parar_texto=None
) -> tuple[str, int]:
    """O motor de OCR comum aos dois caminhos: texto e páginas lidas."""
    from apps.estoque import leitura_compra

    if not leitura_compra.ocr_disponivel():
        raise ErroDeExtracao(
            "O OCR local não está instalado neste ambiente, então o balancete "
            "escaneado não pôde ser lido."
        )

    import pypdfium2

    motor = leitura_compra._motor_ocr()
    documento = pypdfium2.PdfDocument(io.BytesIO(conteudo))
    try:
        paginas_lidas: list[str] = []
        total = min(len(documento), max(1, maximo))
        for indice in range(total):
            bitmap = documento[indice].render(scale=2)
            imagem = leitura_compra._ajustar_para_ocr(bitmap.to_pil().convert("RGB"))
            resultado, _confianca = motor(imagem)
            texto_pagina = leitura_compra._texto_de_blocos(
                leitura_compra._blocos_do_resultado(resultado)
            )
            if parar_pagina is not None and parar_pagina(texto_pagina):
                break
            paginas_lidas.append(texto_pagina)
            if parar_texto is not None and parar_texto("\n".join(paginas_lidas)):
                break
        return "\n".join(paginas_lidas).strip(), len(paginas_lidas)
    except ErroDeExtracao:
        raise
    except Exception as erro:  # pragma: no cover - depende do motor instalado
        raise ErroDeExtracao(
            "Não foi possível reconhecer o texto do balancete escaneado."
        ) from erro
    finally:
        documento.close()


# -- Varredura das linhas -------------------------------------------------------


@dataclass
class _VarreduraLegada:
    """O que a leitura das linhas acumulou, antes de virar contrato."""

    competencia: str | None = None
    periodo: Periodo = field(default_factory=Periodo)
    saldo_anterior: str | None = None
    saldo_final: str | None = None
    receitas_total: str | None = None
    despesas_total: str | None = None
    movimento: str | None = None
    receitas: list[ReceitaExtraida] = field(default_factory=list)
    despesas: list[DespesaExtraida] = field(default_factory=list)
    secoes: list[str] = field(default_factory=list)
    # Subtotal que cada agrupamento declara ("Total de<X>"), na chave do grupo —
    # o mesmo rótulo guardado como categoria original da despesa.
    subtotais_grupos: dict[str, str] = field(default_factory=dict)
    inconsistencias: list[Inconsistencia] = field(default_factory=list)


def _varrer(texto: str, *, nome_arquivo: str | None = None) -> _VarreduraLegada:
    """Percorre o demonstrativo, separando o resumo das linhas por grupo.

    A ordem das checagens importa. O fecho de um bloco ("Total de<X>") é
    reconhecido antes do cabeçalho de seção, senão a linha do total entraria
    como mais um lançamento e a soma seria contada duas vezes. E a seção
    ("2DESPESAS") é reconhecida antes do título de grupo, senão o número colado
    faria dela o nome de um agrupamento em vez de dizer que ali começam as
    despesas.
    """
    varredura = _VarreduraLegada()
    secao: str | None = None
    grupo: str | None = None
    grupos_por_colado: dict[str, str] = {}
    ultima_descricao: str | None = None
    pendente_de_saldo: str | None = None
    data_da_abertura: date | None = None

    _definir_competencia(
        varredura,
        cabecalho=_competencia_do_cabecalho(texto),
        do_nome=_competencia_do_nome(nome_arquivo),
    )

    for bruta in (texto or "").splitlines():
        linha = normalizar_espacos(bruta)
        if not linha:
            continue
        chave = chave_de_comparacao(linha)
        if not chave:
            continue
        colado = chave.replace(" ", "")
        valor = _valor_texto(linha)

        # O rótulo "Saldo em dd/mm/aaaa" costuma vir sem valor, e o número
        # aparece na linha seguinte, sozinho.
        if (
            pendente_de_saldo is not None
            and valor is not None
            and _RE_SO_VALOR.match(_normalizar_ponto_decimal(linha))
        ):
            _guardar_saldo(varredura, pendente_de_saldo, valor)
            pendente_de_saldo = None
            continue

        # A seção ("1RECEITA", "2DESPESAS") é reconhecida sempre, antes de
        # qualquer coisa que dependa de valor: o OCR cola o número ao nome, e o
        # "1" de "1RECEITA" não é valor nenhum — é o código da seção. É a seção
        # que diz se as linhas seguintes são receitas ou despesas.
        achada = _secao_da_linha(linha)
        if achada is not None:
            if achada not in varredura.secoes:
                varredura.secoes.append(achada)
            secao = achada
            grupo = None
            continue

        # "Total de<X>": fecha a seção (X é receita/despesa) ou o grupo X.
        total_de = _RE_TOTAL_DE.match(colado)
        if total_de:
            alvo = total_de.group(1)
            if alvo in _SECOES:
                if _SECOES[alvo] == "receitas":
                    varredura.receitas_total = varredura.receitas_total or valor
                else:
                    varredura.despesas_total = varredura.despesas_total or valor
                secao = None
            else:
                nome = grupos_por_colado.get(alvo)
                if nome is not None and valor is not None:
                    varredura.subtotais_grupos[nome] = valor
                grupo = None
            continue

        if _contem_rotulo(chave, colado, _ROTULOS["saldo_anterior"]):
            _guardar_saldo(varredura, "anterior", valor)
            continue
        if _contem_rotulo(chave, colado, _ROTULOS["saldo_final"]):
            _guardar_saldo(varredura, "final", valor)
            continue
        if _contem_rotulo(chave, colado, _ROTULOS["receitas_total"]):
            varredura.receitas_total = varredura.receitas_total or valor
            continue
        if _contem_rotulo(chave, colado, _ROTULOS["despesas_total"]):
            varredura.despesas_total = varredura.despesas_total or valor
            continue
        if _contem_rotulo(chave, colado, _ROTULOS["movimento"]):
            varredura.movimento = varredura.movimento or valor
            continue

        saldo_em = _RE_SALDO_EM.search(linha)
        if saldo_em:
            data_do_saldo = _data_do_saldo(saldo_em)
            if data_da_abertura is None and data_do_saldo is not None:
                data_da_abertura = data_do_saldo
            alvo = "anterior" if varredura.saldo_anterior is None else "final"
            if valor is not None:
                _guardar_saldo(varredura, alvo, valor)
            else:
                pendente_de_saldo = alvo
            continue

        if valor is None:
            # Uma linha sem valor com código numérico é o título do bloco que
            # vem a seguir: guardá-lo como agrupamento é o que permite dizer de
            # que grupo cada despesa veio.
            titulo = _nome_do_titulo_de_grupo(linha)
            if titulo is not None:
                grupo = titulo
                grupos_por_colado.setdefault(
                    chave_de_comparacao(titulo).replace(" ", ""), titulo
                )
            continue

        if secao is None:
            continue

        descricao = _descricao_da_linha(linha)
        if not descricao:
            if ultima_descricao is None:
                continue
            descricao = f"{ultima_descricao} (continuação)"
            varredura.inconsistencias.append(
                Inconsistencia(
                    tipo=TipoInconsistencia.LINHA_INCOMPLETA,
                    severidade=Severidade.MEDIA,
                    mensagem=(
                        "Uma linha veio sem descrição, só com percentual e valor; "
                        "foi lida como continuação da anterior — confira."
                    ),
                    referencia=ultima_descricao,
                )
            )
        ultima_descricao = descricao

        if secao == "receitas":
            varredura.receitas.append(
                _montar_receita(descricao, valor, metodo=METODO_LEGADO)
            )
        else:
            varredura.despesas.append(
                _montar_despesa(descricao, valor, grupo=grupo, metodo=METODO_LEGADO)
            )

    if varredura.competencia is None and data_da_abertura is not None:
        varredura.competencia = _mes_seguinte(data_da_abertura).isoformat()

    return varredura


def _definir_competencia(
    varredura: _VarreduraLegada, *, cabecalho: date | None, do_nome: date | None
) -> None:
    """Decide a competência na ordem do documento: cabeçalho, nome, saldo.

    Quando o cabeçalho e o nome do arquivo discordam, o documento manda — e a
    discordância vira ponto de atenção, para a gestão ver que houve dúvida.
    """
    if cabecalho is not None:
        varredura.competencia = cabecalho.isoformat()
        if do_nome is not None and do_nome != cabecalho:
            varredura.inconsistencias.append(
                Inconsistencia(
                    tipo=TipoInconsistencia.COMPETENCIA_CONFLITANTE,
                    severidade=Severidade.MEDIA,
                    mensagem=(
                        f"O cabeçalho do documento indica {cabecalho:%m/%Y} e o "
                        f"nome do arquivo indica {do_nome:%m/%Y}; usei a "
                        "competência do cabeçalho."
                    ),
                    referencia="competencia",
                )
            )
        return
    if do_nome is not None:
        varredura.competencia = do_nome.isoformat()


def _competencia_do_cabecalho(texto: str) -> date | None:
    """A competência declarada no alto do documento, quando há uma.

    Só as primeiras linhas valem, e linha com "venc." nunca decide: é o
    vencimento de uma parcela ("venc.12/2023"), não o mês do demonstrativo.
    """
    linhas = [linha for linha in (texto or "").splitlines() if linha.strip()][:5]
    for linha in linhas:
        if "venc" in linha.lower():
            continue
        competencia = texto_para_competencia(linha)
        if competencia is not None:
            return competencia
    return None


def _competencia_do_nome(nome_arquivo: str | None) -> date | None:
    """A competência que o nome do arquivo sugere, quando sugere alguma."""
    if not nome_arquivo:
        return None
    return texto_para_competencia(nome_arquivo)


def _mes_seguinte(data: date) -> date:
    """Primeiro dia do mês seguinte — a competência do saldo de abertura."""
    if data.month == 12:
        return date(data.year + 1, 1, 1)
    return date(data.year, data.month + 1, 1)


def _data_do_saldo(achado: re.Match) -> date | None:
    """A data lida em "Saldo em dd/mm/aaaa", quando ela é válida."""
    dia, mes, ano_texto = achado.group(1), achado.group(2), achado.group(3)
    ano = int(ano_texto)
    if ano < 100:
        ano += 2000
    try:
        return date(ano, int(mes), int(dia))
    except ValueError:
        return None


def _guardar_saldo(varredura: _VarreduraLegada, alvo: str, valor: str | None) -> None:
    """Guarda o saldo de abertura ou de fechamento, sem sobrescrever o lido."""
    if alvo == "anterior":
        varredura.saldo_anterior = varredura.saldo_anterior or valor
    else:
        varredura.saldo_final = varredura.saldo_final or valor


def _contem_rotulo(chave: str, colado: str, rotulos: tuple[str, ...]) -> bool:
    """Se a linha normalizada traz algum dos rótulos do resumo.

    A comparação é feita também sobre a forma sem espaços: o OCR cola as
    palavras ("Total deRECEITA", "Saldoem"), e só a chave colada reconhece isso.
    """
    return any(
        rotulo in chave or rotulo.replace(" ", "") in colado for rotulo in rotulos
    )


def _secao_da_linha(linha: str) -> str | None:
    """A seção quando a linha só anuncia o bloco, sem valor.

    O número colado ("1RECEITA", "2DESPESAS") é descartado antes da comparação.
    """
    sem_codigo = _RE_CODIGO_INICIAL.sub("", linha).strip()
    if not sem_codigo:
        return None
    return _SECOES.get(chave_de_comparacao(sem_codigo).replace(" ", ""))


def _nome_do_titulo_de_grupo(linha: str) -> str | None:
    """O nome do agrupamento quando a linha é um título, sem valor."""
    if not _parece_titulo_de_grupo(linha):
        return None
    return _RE_CODIGO_INICIAL.sub("", linha).strip() or None


def _normalizar_ponto_decimal(linha: str) -> str:
    """Troca por vírgula o ponto que o OCR usou como decimal.

    Quando o último separador da linha é um ponto seguido de exatamente dois
    dígitos no fim, ele é decimal: "152.60" é 152,60 e "11.070.00" é 11.070,00.
    """
    achado = _RE_PONTO_DECIMAL_FINAL.search(linha)
    if not achado:
        return linha
    return f"{linha[: achado.start()]},{achado.group(1)}"


def _valor_texto(linha: str) -> str | None:
    """Último valor em dinheiro da linha, no formato do contrato."""
    return decimal_para_texto(_valor_decimal(linha))


def _valor_decimal(linha: str) -> Decimal | None:
    """Último valor em dinheiro da linha, já como ``Decimal``."""
    achados = [
        achado.group("valor")
        for achado in _RE_VALOR.finditer(_normalizar_ponto_decimal(linha))
    ]
    if not achados:
        return None
    return texto_para_decimal(achados[-1])


def _descricao_da_linha(linha: str) -> str:
    """A linha sem o valor nem o percentual: é a descrição do lançamento.

    O código de agrupamento do começo ("1.10", "2.81.60") e a coluna de
    percentual não fazem parte do nome: sobram só as palavras, para que
    "Portaria e segurança 25.000,00" vire "Portaria e segurança".
    """
    normalizada = _normalizar_ponto_decimal(linha)
    achados = list(_RE_VALOR.finditer(normalizada))
    if achados:
        ultimo = achados[-1]
        normalizada = f"{normalizada[: ultimo.start()]} {normalizada[ultimo.end():]}"
    sem_percentual = _RE_PERCENTUAL.sub(" ", normalizada)
    sem_codigo = _RE_CODIGO_INICIAL.sub("", sem_percentual).strip()
    return normalizar_espacos(sem_codigo).strip(" -.:;·|")


def _parece_titulo_de_grupo(bruta: str) -> bool:
    """Diz se a linha é o título de um agrupamento, sem valor.

    O título de grupo do balancete antigo vem em maiúsculas e sozinho — sem
    valor ao lado — ou, quando o OCR cola o número, começa por um código
    numérico ("2.81 DESPESAS BANCARIAS"). É o que permite guardar de que bloco a
    despesa veio, sem confundir um lançamento com o seu cabeçalho.
    """
    texto = normalizar_espacos(bruta)
    if not any(caractere.isalpha() for caractere in texto):
        return False
    sem_codigo = _RE_CODIGO_INICIAL.sub("", texto).strip()
    if not sem_codigo:
        return False
    comeca_com_codigo = bool(_RE_CODIGO_INICIAL.match(texto))
    if texto.upper() != texto and not comeca_com_codigo:
        return False
    return 1 <= len(sem_codigo.split()) <= 8


def _subtotais_divergentes(grupos) -> tuple[Inconsistencia, ...]:
    """Aponta o grupo cujo subtotal declarado não fecha com as linhas lidas.

    A tolerância aqui é a do subtotal (R$ 1,00), e não a do centavo: um dígito
    trocado pelo OCR não deveria virar alarme, mas uma linha inteira faltando
    deveria. O apontamento não bloqueia nada — só pede a conferência da gestão.
    """
    avisos = []
    for grupo in grupos:
        declarado = (
            texto_para_decimal(grupo.total_declarado)
            if grupo.total_declarado
            else None
        )
        if declarado is None or not grupo.diferenca:
            continue
        diferenca = texto_para_decimal(grupo.diferenca)
        if diferenca is None or abs(diferenca) <= LIMITE_DO_SUBTOTAL:
            continue
        avisos.append(
            Inconsistencia(
                tipo=TipoInconsistencia.TOTAL_DIVERGENTE,
                severidade=Severidade.MEDIA,
                mensagem=(
                    f"O grupo “{grupo.nome}” declara {_moeda(declarado)} e a soma "
                    f"das linhas é {_moeda(texto_para_decimal(grupo.soma_extraida))}."
                ),
                referencia=grupo.nome,
            )
        )
    return tuple(avisos)


def _montar_receita(descricao: str, valor: str | None, *, metodo: str) -> ReceitaExtraida:
    """Uma linha de receita do demonstrativo, com a procedência do que a originou."""
    return ReceitaExtraida(
        descricao_original=descricao,
        descricao_normalizada=chave_de_comparacao(descricao),
        valor=valor or "0.00",
        origem="Demonstrativo do balancete",
        categoria_original=descricao,
        procedencia=Procedencia(
            secao="receitas", trecho=descricao, metodo=metodo
        ),
    )


def _montar_despesa(
    descricao: str,
    valor: str | None,
    *,
    metodo: str,
    grupo: str | None = None,
) -> DespesaExtraida:
    """Uma linha de despesa do demonstrativo, com a categoria do Eithra ao lado.

    A descrição decide primeiro e o grupo do documento é a pista de reserva: só
    quando a linha não diz nada por si. O que não encontra regra no catálogo vira
    "Outros": a pergunta "de que natureza é este gasto" continua sem resposta,
    mas o valor não desaparece dos agregados por falta de classificação.
    """
    categoria = categorizar(descricao, categoria_original=grupo)
    if categoria == CategoriaEithra.NAO_CLASSIFICADO:
        categoria = CategoriaEithra.OUTROS
    return DespesaExtraida(
        descricao_original=descricao,
        descricao_normalizada=chave_de_comparacao(descricao),
        valor=valor or "0.00",
        categoria_original=grupo or descricao,
        categoria_eithra=categoria,
        natureza=classificar_natureza(descricao, categoria_eithra=categoria),
        sistema=sistema_para(descricao),
        procedencia=Procedencia(
            secao="despesas", trecho=descricao, metodo=metodo
        ),
    )


def _motivo_de_recusa(layout: str) -> str:
    """Por que esta importação não é lida pelo leitor legado."""
    if layout == LayoutBalanco.ATUAL_CANONICO:
        return "Este documento é do modelo atual e é lido pela leitura do formato atual."
    return "Ainda não foi possível reconhecer o formato deste documento."
