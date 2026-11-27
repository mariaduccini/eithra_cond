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
    para_dicionario,
    validar_dicionario,
)
from apps.balancos.detector import ErroDeDeteccaoDeLayout, _baixar_pdf_privado
from apps.balancos.extrator_atual import (
    ErroDeExtracao,
    ResultadoDaExtracao,
    _agregar_categorias,
    _confianca,
    _inconsistencia_para_dicionario,
    _marcar_erro,
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

# A fase 1 lê apenas a abertura, onde está o demonstrativo sintético. Ler o
# documento inteiro (e os anexos por unidade) custa caro e traria dado de morador
# para dentro do sistema sem servir à gestão.
PAGINAS_DA_FASE_1 = 2

# Valor com cara de dinheiro, na mesma regra do modelo atual: recusa data,
# percentual e número colado.
_RE_VALOR = re.compile(
    rf"(?<![\d.,/])(?P<valor>{PADRAO_VALOR})(?![\d/])(?![.,]\d)(?!\s*%)"
)

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
        "total das receitas",
        "total receitas",
        "receitas total",
        "subtotal de receitas",
        "subtotal das receitas",
    ),
    "despesas_total": (
        "total de despesas",
        "total das despesas",
        "total despesas",
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
        balanco, inconsistencias = ler_balanco_legado(conteudo)
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
) -> tuple[BalancoExtraidoV1, tuple[Inconsistencia, ...]]:
    """Lê um balancete escaneado e devolve o contrato já conferido.

    Função pura em relação ao banco: recebe bytes e não persiste nada — o que a
    torna exercitável com documentos sintéticos, sem storage.
    """
    texto = _texto_do_documento(conteudo)
    if not texto.strip():
        raise ErroDeExtracao(
            "Não foi possível reconhecer texto no balancete enviado. Confira o "
            "arquivo e tente novamente."
        )
    return ler_balanco_legado_do_texto(texto)


def ler_balanco_legado_do_texto(
    texto: str,
) -> tuple[BalancoExtraidoV1, tuple[Inconsistencia, ...]]:
    """Lê o demonstrativo a partir do texto já reconhecido.

    Separada da leitura do PDF de propósito: o reconhecimento do texto é o único
    passo que depende do OCR, e o resto da leitura é determinístico e testável
    sem imagem.
    """
    varredura = _varrer(texto)

    receitas = tuple(varredura.receitas)
    despesas = tuple(varredura.despesas)
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
        receitas=receitas,
        despesas=despesas,
        resumo=ResumoExtraido(),
        provenance={
            "metodo": METODO_LEGADO,
            "layout": "legado",
            "fase": 1,
            "paginas_lidas": PAGINAS_DA_FASE_1,
            "secoes_encontradas": list(varredura.secoes),
        },
        confianca=Confianca.MEDIA,
    )

    inconsistencias = tuple([*varredura.inconsistencias, *validar_balanco(base)])
    return (
        replace(
            base,
            resumo=_resumo(despesas, (), inconsistencias),
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


def _texto_do_documento(conteudo: bytes) -> str:
    """Texto do balancete: a camada textual, quando existe; senão, o OCR local.

    Um balancete digitalizado não traz texto — o OCR reconhece as duas primeiras
    páginas. Um balancete que já venha com texto é lido direto, sem o custo do
    reconhecimento.
    """
    nativo = _texto_nativo(conteudo)
    if _texto_suficiente(nativo):
        return nativo
    return _texto_por_ocr(conteudo)


def _texto_nativo(conteudo: bytes) -> str:
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
    for indice in range(min(PAGINAS_DA_FASE_1, len(leitor.pages))):
        paginas.append(leitor.pages[indice].extract_text() or "")
    return "\n".join(paginas).strip()


def _texto_suficiente(texto: str) -> bool:
    """Se a camada textual tem corpo de documento, e não um rodapé solto."""
    linhas_uteis = [linha for linha in (texto or "").splitlines() if linha.strip()]
    return len(texto.strip()) >= 40 and len(linhas_uteis) >= 3


def _texto_por_ocr(conteudo: bytes, *, paginas: int = PAGINAS_DA_FASE_1) -> str:
    """Reaproveita o OCR local do estoque, limitado às páginas pedidas.

    O extrator lê o demonstrativo inteiro (as páginas da fase 1); o detector de
    layout só precisa do rótulo do documento e pede uma página só.
    """
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
        total = min(len(documento), max(1, paginas))
        for indice in range(total):
            bitmap = documento[indice].render(scale=2)
            imagem = leitura_compra._ajustar_para_ocr(bitmap.to_pil().convert("RGB"))
            resultado, _confianca = motor(imagem)
            paginas_lidas.append(
                leitura_compra._texto_de_blocos(
                    leitura_compra._blocos_do_resultado(resultado)
                )
            )
        return "\n".join(paginas_lidas).strip()
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
    inconsistencias: list[Inconsistencia] = field(default_factory=list)


def _varrer(texto: str) -> _VarreduraLegada:
    """Percorre o demonstrativo, separando o resumo das linhas por grupo.

    A ordem das checagens importa: um rótulo de total ("Total de receitas") é
    reconhecido antes do cabeçalho de seção ("Receitas"), senão a linha do total
    entraria como mais um lançamento e a soma seria contada duas vezes.
    """
    varredura = _VarreduraLegada()
    secao: str | None = None
    grupo: str | None = None

    for bruta in (texto or "").splitlines():
        linha = normalizar_espacos(bruta)
        if not linha:
            continue
        chave = chave_de_comparacao(linha)
        if not chave:
            continue

        if varredura.competencia is None:
            competencia = texto_para_competencia(linha)
            if competencia is not None:
                varredura.competencia = competencia.isoformat()

        if _contem_rotulo(chave, _ROTULOS["saldo_anterior"]):
            varredura.saldo_anterior = varredura.saldo_anterior or _valor_texto(linha)
            continue
        if _contem_rotulo(chave, _ROTULOS["saldo_final"]):
            varredura.saldo_final = varredura.saldo_final or _valor_texto(linha)
            continue
        if _contem_rotulo(chave, _ROTULOS["receitas_total"]):
            varredura.receitas_total = varredura.receitas_total or _valor_texto(linha)
            continue
        if _contem_rotulo(chave, _ROTULOS["despesas_total"]):
            varredura.despesas_total = varredura.despesas_total or _valor_texto(linha)
            continue
        if _contem_rotulo(chave, _ROTULOS["movimento"]):
            varredura.movimento = varredura.movimento or _valor_texto(linha)
            continue

        valor = _valor_texto(linha)
        if valor is None and chave in _SECOES:
            nome = _SECOES[chave]
            if nome not in varredura.secoes:
                varredura.secoes.append(nome)
            secao = nome
            grupo = None
            continue

        if valor is None:
            # Uma linha sem valor em maiúsculas é o título do bloco que vem a
            # seguir: guardá-lo como agrupamento é o que permite dizer de que
            # grupo cada despesa veio — sem deixar o grupo decidir sozinho o que
            # a descrição já diz.
            if _parece_titulo_de_grupo(bruta):
                grupo = linha
            continue

        if secao is None:
            continue

        descricao = _descricao_da_linha(linha)
        if not descricao:
            continue
        if secao == "receitas":
            varredura.receitas.append(
                _montar_receita(descricao, valor, metodo=METODO_LEGADO)
            )
        else:
            varredura.despesas.append(
                _montar_despesa(descricao, valor, grupo=grupo, metodo=METODO_LEGADO)
            )

    return varredura


def _contem_rotulo(chave: str, rotulos: tuple[str, ...]) -> bool:
    """Se a linha normalizada traz algum dos rótulos do resumo."""
    return any(rotulo in chave for rotulo in rotulos)


def _valor_texto(linha: str) -> str | None:
    """Último valor em dinheiro da linha, no formato do contrato."""
    achados = [achado.group("valor") for achado in _RE_VALOR.finditer(linha)]
    if not achados:
        return None
    return decimal_para_texto(texto_para_decimal(achados[-1]))


def _valor_decimal(linha: str) -> Decimal | None:
    achados = [achado.group("valor") for achado in _RE_VALOR.finditer(linha)]
    if not achados:
        return None
    return texto_para_decimal(achados[-1])


def _descricao_da_linha(linha: str) -> str:
    """A linha sem o valor: é a descrição do grupo.

    Remove o último valor reconhecido e limpa os separadores que o cercam, para
    que "Portaria e segurança 25.000,00" vire "Portaria e segurança".
    """
    achados = list(_RE_VALOR.finditer(linha))
    if not achados:
        return linha.strip()
    ultimo = achados[-1]
    resto = f"{linha[: ultimo.start()]} {linha[ultimo.end():]}"
    return normalizar_espacos(resto).strip(" -.:;·|")


def _parece_titulo_de_grupo(bruta: str) -> bool:
    """Diz se a linha é o título de um agrupamento, sem valor.

    O título de grupo do balancete antigo vem em maiúsculas e sozinho — sem
    valor ao lado. É o que permite guardar de que bloco a despesa veio, sem
    confundir um lançamento com o seu cabeçalho.
    """
    texto = normalizar_espacos(bruta)
    letras = [caractere for caractere in texto if caractere.isalpha()]
    if not letras:
        return False
    if texto.upper() != texto:
        return False
    return 1 <= len(texto.split()) <= 8


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
