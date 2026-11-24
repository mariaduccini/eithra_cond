"""Leitura local de comprovantes anexados a uma compra.

O leitor não chama serviços externos nem grava dados na compra. Ele produz
propostas com procedência explícita; a gestão revisa e confirma cada dado.

Três caminhos chegam até aqui e todos terminam no mesmo contrato de sugestões:

* PDF com camada textual — lido diretamente pelo ``pypdf``, sem custo de OCR;
* imagem (foto de cupom, recibo, nota fotografada) — reconhecida por OCR local;
* PDF sem texto (escaneado) — rasterizado página a página e então reconhecido.

O OCR roda no próprio servidor, com modelos embutidos no pacote: nada é enviado
para fora e nada é inventado. O que não for reconhecido com segurança continua em
branco e fica para a revisão humana. Cupom fiscal, nota fiscal e recibo usam
layouts muito diferentes, então o texto é lido por rótulos e por posição na
linha, tolerando o que o OCR costuma fazer: colar palavras, quebrar o rótulo do
valor em duas linhas e perder acentos.

A leitura nunca confunde subtotal, desconto, valor pago ou troco com o total da
compra: só rótulos de total explícitos alimentam o ``valor_total``.
"""
from __future__ import annotations

import io
import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .constants import StatusLeituraDocumentoCompra, UnidadeMedida
from .models import DocumentoCompra, ItemEstoque

logger = logging.getLogger(__name__)

VERSAO_CONTRATO = 2
MAX_PAGINAS = 10
# Cada página reconhecida por OCR custa segundos; três bastam para um comprovante.
MAX_PAGINAS_OCR = 3
# Abaixo disso a foto perde o espaço entre as palavras; acima, só custa tempo.
LADO_MINIMO_OCR = 1400
LADO_MAXIMO_OCR = 2600
# Até este tamanho o texto nativo do PDF é tratado como insuficiente (escaneado).
# Um rodapé escaneado pode passar de doze caracteres e ainda assim não ser corpo
# de documento: por isso o tamanho só conta junto com o número de linhas úteis.
MINIMO_TEXTO_NATIVO = 12
MINIMO_LINHAS_NATIVAS = 3
MAX_LINHAS_ANALISADAS = 400

MIME_PDF = "application/pdf"
MIMES_DE_IMAGEM = ("image/jpeg", "image/png")

_PADRAO_DINHEIRO = r"(?:R\$\s*)?(-?\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|-?\d+(?:[,.]\d{1,2})?)"
_PADRAO_DATA = r"(?P<dia>\d{2})[/-](?P<mes>\d{2})[/-](?P<ano>\d{4})"
_RE_SOMENTE_DINHEIRO = re.compile(rf"^\s*{_PADRAO_DINHEIRO}\s*$")
_RE_CNPJ = re.compile(r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}")
_RE_CPF = re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}")
_RE_DATA_EM_TEXTO = re.compile(r"\d{2}/\d{2}/\d{4}")
_RE_DATA_SOLTA = re.compile(
    rf"^\s*{_PADRAO_DATA}(?:\s+\d{{2}}:\d{{2}}(?::\d{{2}})?)?\s*$"
)
_RE_UNIDADE_NA_LINHA = r"UN|UND|UNID|UNIDADE|PC|P[CÇ]|KG|L|M|M2|M3|CX|CXA|PCT|RL|PAR|JG"

_UNIDADES_LIDAS = {
    "un": UnidadeMedida.UNIDADE,
    "und": UnidadeMedida.UNIDADE,
    "unid": UnidadeMedida.UNIDADE,
    "pc": UnidadeMedida.UNIDADE,
    "pç": UnidadeMedida.UNIDADE,
    "kg": UnidadeMedida.QUILO,
    "l": UnidadeMedida.LITRO,
    "m": UnidadeMedida.METRO,
    "unidade": UnidadeMedida.UNIDADE,
    "caixa": UnidadeMedida.CAIXA,
    "cxa": UnidadeMedida.CAIXA,
    "pacote": UnidadeMedida.PACOTE,
    "pct": UnidadeMedida.PACOTE,
    "metro": UnidadeMedida.METRO,
    "litro": UnidadeMedida.LITRO,
    "quilo": UnidadeMedida.QUILO,
    "rolo": UnidadeMedida.ROLO,
    "rl": UnidadeMedida.ROLO,
    "par": UnidadeMedida.PAR,
    "jogo": UnidadeMedida.JOGO,
    "outra": UnidadeMedida.OUTRA,
}

# Rótulos de total, do mais específico para o mais genérico. A âncora de início
# de linha é o que impede "SUBTOTAL" e "VALOR PAGO" de virarem o total da compra.
_ROTULOS_DE_TOTAL = (
    "VALOR TOTAL DA NOTA",
    "VALOR TOTAL DO DOCUMENTO",
    "VALOR TOTAL A PAGAR",
    "VALOR TOTAL DA COMPRA",
    "VALOR TOTAL",
    "TOTAL A PAGAR",
    "TOTAL GERAL",
    "TOTAL DA COMPRA",
    "TOTAL",
)
_ROTULOS_DE_DATA = (
    "DATA DE EMISSAO",
    "DATA DA EMISSAO",
    "DATA DA COMPRA",
    "DATA DO DOCUMENTO",
    "DATA DO PEDIDO",
    "EMISSAO",
    "DATA",
)
_ROTULOS_DE_FORNECEDOR = (
    "RAZAO SOCIAL",
    "FORNECEDOR",
    "EMITENTE",
    "NOME FANTASIA",
)
_ROTULOS_DE_PAGAMENTO = (
    "FORMA DE PAGAMENTO",
    "FORMA DE PGTO",
    "FORMA PAGAMENTO",
    "PAGAMENTO",
)
_ROTULOS_DE_DOCUMENTO = (
    "NUMERO DO DOCUMENTO",
    "NUMERO DA NOTA",
    "NUMERO DO CUPOM",
    "NUMERO DA NF",
    "NOTA FISCAL",
    "NF-E",
    "NFE",
    "NFC-E",
    "NFCE",
    "CUPOM FISCAL",
    "COO",
    "CCF",
    "NUMERO",
    "NRO",
    "N.º",
)

# Palavras que anunciam um resumo de valores, não um item. Sem esta lista, o OCR
# que separa rótulo e valor em duas linhas transformaria "SUBTOTAL" em produto.
_PALAVRAS_QUE_NAO_SAO_ITEM = (
    "subtotal",
    "desconto",
    "acrescimo",
    "troco",
    "total",
    "valortotal",
    "valorpago",
    "valorpagamento",
    "valorapagar",
    "valorrecebido",
    "pagamento",
    "formapagamento",
    "dinheiro",
    "cartao",
    "credito",
    "debito",
    "pix",
    "cnpj",
    "cpf",
    "inscricao",
    "cep",
    "endereco",
    "telefone",
    "data",
    "hora",
    "cupom",
    "nfce",
    "nfe",
    "notafiscal",
    "documentoauxiliar",
    "chave",
    "acesso",
    "protocolo",
    "autorizacao",
    "sat",
    "ecf",
    "coo",
    "ccf",
    "serie",
    "operador",
    "supervisor",
    "consumidor",
    "tributos",
    "obrigado",
    "volte",
    "quantidade",
    "valor",
    "unitario",
    "vlr",
    "qtd",
)

# Cada letra aceita também a forma acentuada: o OCR erra o acento com frequência.
_LETRAS_TOLERANTES = {
    "a": "[aàáâãäª]",
    "e": "[eèéêë]",
    "i": "[iìíîï]",
    "o": "[oòóôõöº°]",
    "u": "[uùúûü]",
    "c": "[cç]",
    "n": "[nñ]",
}


def _normalizar(texto: str) -> str:
    """Normaliza apenas para comparação exata, sem aproximação fuzzy."""
    decomposto = unicodedata.normalize("NFKD", texto or "")
    sem_acentos = "".join(char for char in decomposto if not unicodedata.combining(char))
    return " ".join(sem_acentos.casefold().split())


def _dinheiro(texto: str | None) -> str | None:
    """Converte um valor explícito do documento em texto decimal canônico."""
    if not texto:
        return None
    bruto = texto.replace("R$", "").replace(" ", "").strip()
    if "," in bruto:
        bruto = bruto.replace(".", "").replace(",", ".")
    try:
        valor = Decimal(bruto).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None
    if valor < 0:
        return None
    return format(valor, ".2f")


def _valor_monetario(texto: str | None) -> str | None:
    """O primeiro valor em dinheiro de um trecho, já em forma canônica."""
    achado = re.search(_PADRAO_DINHEIRO, texto or "")
    return _dinheiro(achado.group(1)) if achado else None


def _quantidade(texto: str | None) -> str | None:
    if not texto:
        return None
    try:
        valor = Decimal(texto.strip().replace(",", ".")).quantize(
            Decimal("0.001")
        )
    except (InvalidOperation, ValueError):
        return None
    if valor <= 0:
        return None
    representacao = format(valor, "f")
    if "." in representacao:
        return representacao.rstrip("0").rstrip(".")
    return representacao


def _unidade(texto: str | None) -> str | None:
    """Converte abreviações do documento para unidades já existentes no estoque."""
    return _UNIDADES_LIDAS.get(_normalizar(texto)) if texto else None


def _padrao_rotulo(rotulo: str) -> str:
    """Rótulo tolerante: aceita espaços (ou nenhum) e acentos entre as letras.

    O OCR cola palavras — "VALOR TOTAL DA NOTA" chega como
    "VALORTOTALDANOTA". Exigir o espaço exato seria perder o campo.
    """
    base = _normalizar(rotulo).replace(" ", "")
    return "".join(
        f"{_LETRAS_TOLERANTES.get(caractere, re.escape(caractere))}\\s*"
        for caractere in base
    )


def _linhas(texto: str) -> list[str]:
    """Linhas úteis do documento, já sem espaços nas pontas."""
    return [
        linha.strip()
        for linha in (texto or "").splitlines()
    ][:MAX_LINHAS_ANALISADAS]


def _valor_apos_rotulo(linhas: list[str], rotulos: tuple[str, ...]) -> str | None:
    """Valor que acompanha o rótulo, na mesma linha ou logo abaixo dela.

    Muitos cupons imprimem "SUBTOTAL" e o número na linha seguinte; o mesmo
    acontece com o OCR quando o valor fica longe do rótulo.
    """
    for rotulo in rotulos:
        padrao = _padrao_rotulo(rotulo)
        for indice, linha in enumerate(linhas):
            achado = re.match(
                rf"\s*{padrao}\s*[:#\-]?\s*(?P<valor>.*?)\s*$",
                linha,
                re.IGNORECASE,
            )
            if not achado:
                continue
            valor = achado.group("valor").strip(" :#-|")
            if valor:
                return valor
            if indice + 1 < len(linhas) and linhas[indice + 1]:
                return linhas[indice + 1]
    return None


def _data(linhas: list[str]) -> str | None:
    """A data do documento: pelo rótulo e, sem ele, pela linha só de data.

    Cupons e recibos impressos quase nunca rotulam a data, mas imprimem
    "15/11/2026" sozinho na linha — às vezes com a hora ao lado. A leitura por
    posição só aceita a linha que é *apenas* a data, para não capturar, por
    exemplo, um vencimento rotulado.
    """
    valor = _valor_apos_rotulo(linhas, _ROTULOS_DE_DATA)
    achado = re.search(_PADRAO_DATA, valor) if valor else _data_solta(linhas)
    if not achado:
        return None
    try:
        return date(
            int(achado.group("ano")),
            int(achado.group("mes")),
            int(achado.group("dia")),
        ).isoformat()
    except ValueError:
        return None


def _data_solta(linhas: list[str]):
    """A primeira linha que é somente uma data, com hora opcional ao lado."""
    for linha in linhas:
        achado = _RE_DATA_SOLTA.match(linha)
        if achado:
            return achado
    return None


def _documento_da_nota(texto: str) -> str | None:
    """Número do documento: só dígitos, sem confundir CNPJ, CPF, data ou valor."""
    linhas = _linhas(texto)
    for rotulo in _ROTULOS_DE_DOCUMENTO:
        padrao = _padrao_rotulo(rotulo)
        for linha in linhas:
            achado = re.match(
                rf"\s*{padrao}\s*[:#\-]?\s*(?P<valor>.+)$",
                linha,
                re.IGNORECASE,
            )
            if not achado:
                continue
            numero = _primeiro_numero_de_documento(achado.group("valor"))
            if numero:
                return numero
    return None


def _primeiro_numero_de_documento(texto: str) -> str | None:
    """O primeiro número que pode ser um número de documento — e não outra coisa."""
    for achado in re.finditer(r"\d[\d.\-/]{2,26}", texto or ""):
        bruto = achado.group(0)
        if _RE_CNPJ.fullmatch(bruto) or _RE_CPF.fullmatch(bruto):
            continue
        if _RE_DATA_EM_TEXTO.fullmatch(bruto):
            continue
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", bruto):
            continue
        digitos = re.sub(r"\D", "", bruto)
        if 1 <= len(digitos) <= 20:
            return digitos
    return None


def _documento_fiscal(texto: str) -> str | None:
    """CNPJ ou CPF do emitente, com ou sem máscara. Guardado só em dígitos."""
    linhas = _linhas(texto)
    for linha in linhas:
        compacta = _normalizar(linha).replace(" ", "")
        if "cnpj" not in compacta and "cpf" not in compacta:
            continue
        achado = _RE_CNPJ.search(linha) or _RE_CPF.search(linha)
        if achado:
            return re.sub(r"\D", "", achado.group(0))
    achado = _RE_CNPJ.search(texto) or _RE_CPF.search(texto)
    return re.sub(r"\D", "", achado.group(0)) if achado else None


def _fornecedor(linhas: list[str], texto: str) -> str | None:
    """O emitente: pelo rótulo e, quando ele falta, pela linha acima do CNPJ.

    Cupons impressos quase nunca rotulam o emitente, mas imprimem o nome
    imediatamente antes do CNPJ. É uma leitura de posição, não um palpite: se a
    linha não parecer uma razão social, nada é sugerido.
    """
    valor = _valor_apos_rotulo(linhas, _ROTULOS_DE_FORNECEDOR)
    if valor:
        limpo = " ".join(valor.split())[:200]
        if limpo and not _RE_CNPJ.search(limpo) and not _RE_CPF.search(limpo):
            return limpo

    for indice, linha in enumerate(linhas):
        if not (_RE_CNPJ.search(linha) or _RE_CPF.search(linha)):
            continue
        if indice == 0:
            break
        candidato = linhas[indice - 1]
        if _parece_razao_social(candidato):
            return candidato[:200]
        break
    return None


def _parece_razao_social(texto: str) -> bool:
    """Se a linha pode ser o nome do emitente — e não um cabeçalho ou um valor."""
    limpo = (texto or "").strip()
    if len(limpo) < 4 or len(re.findall(r"[A-Za-zÀ-ÿ]", limpo)) < 4:
        return False
    compacta = _normalizar(limpo).replace(" ", "")
    return not any(compacta.startswith(palavra) for palavra in _PALAVRAS_QUE_NAO_SAO_ITEM)


def _item_rotulado(linha: str) -> dict | None:
    """Linha de item com campos explícitos separados por barra."""
    achado = re.search(
        r"(?i)^\s*(?:ITEM|PRODUTO)\s*:\s*(?P<descricao>[^|]+)"
        r"(?:\|\s*(?:QTD|QUANTIDADE)\s*:\s*(?P<quantidade>[^|]+))?"
        r"(?:\|\s*(?:UNIDADE|UN|UND)\s*:\s*(?P<unidade>[^|]+))?"
        r"(?:\|\s*(?:VALOR\s*UNIT[ÁA]RIO|UNIT[ÁA]RIO)\s*:\s*(?P<unitario>[^|]+))?"
        r"(?:\|\s*(?:TOTAL)\s*:\s*(?P<total>[^|]+))?"
        r"(?:\|\s*(?:CATEGORIA)\s*:\s*(?P<categoria>[^|]+))?",
        linha,
    )
    if not achado:
        return None
    campos = achado.groupdict()
    descricao = " ".join((campos["descricao"] or "").split())
    if not descricao:
        return None
    return {
        "descricao": descricao[:200],
        "quantidade": _quantidade(campos["quantidade"]),
        "unidade": _unidade(campos["unidade"]),
        "valor_unitario": _dinheiro(campos["unitario"]),
        "valor_total": _dinheiro(campos["total"]),
        "categoria_sugerida": " ".join((campos["categoria"] or "").split())[:60],
    }


def _item_de_medida(linha: str, descricao: str) -> dict | None:
    """Linha de cupom: "2 UN x 32,90", com total opcional logo depois."""
    achado = re.match(
        rf"^\s*(?P<quantidade>\d+(?:[,.]\d{{1,3}})?)\s*"
        rf"(?P<unidade>{_RE_UNIDADE_NA_LINHA})?\s*[xX×]\s*"
        rf"(?P<unitario>{_PADRAO_DINHEIRO})"
        rf"(?:\s+(?P<total>{_PADRAO_DINHEIRO}))?\s*$",
        linha,
        re.IGNORECASE,
    )
    if not achado:
        return None
    return {
        "descricao": descricao[:200],
        "quantidade": _quantidade(achado.group("quantidade")),
        "unidade": _unidade(achado.group("unidade")),
        "valor_unitario": _dinheiro(achado.group("unitario")),
        "valor_total": _dinheiro(achado.group("total")),
        "categoria_sugerida": "",
    }


def _item_tabular(linha: str, descricao_pendente: str = "") -> dict | None:
    """Linha em que descrição, quantidade, unidade, unitário e total vêm juntos.

    O OCR separa cada célula da tabela em seu próprio bloco e a reconstrução da
    linha volta a colar as células por um espaço; por isso a quantidade aceita
    vir encostada na unidade ("2UN") quando o espaço entre as colunas se perde.
    Uma descrição quebrada em duas linhas chega aqui como o resto da linha
    anterior, que é recolado antes de ler os valores.
    """
    achado = re.search(
        rf"^\s*(?:\d{{1,5}}\s+)?(?P<descricao>.+?)\s+"
        rf"(?P<quantidade>\d+(?:[,.]\d{{1,3}})?)\s*"
        rf"(?P<unidade>{_RE_UNIDADE_NA_LINHA})\s+"
        rf"(?P<unitario>{_PADRAO_DINHEIRO})\s+"
        rf"(?P<total>{_PADRAO_DINHEIRO})\s*$",
        linha,
        re.IGNORECASE,
    )
    if not achado:
        return None
    descricao = " ".join(achado.group("descricao").split())
    if descricao_pendente:
        descricao = f"{descricao_pendente} {descricao}".strip()
    if not descricao or not _parece_item(descricao):
        return None
    item = {
        "descricao": descricao[:200],
        "quantidade": _quantidade(achado.group("quantidade")),
        "unidade": _unidade(achado.group("unidade")),
        "valor_unitario": _dinheiro(achado.group("unitario")),
        "valor_total": _dinheiro(achado.group("total")),
        "categoria_sugerida": "",
    }
    item["revisar"] = _associacao_incerta(item)
    return item


def _associacao_incerta(item: dict) -> bool:
    """Se a linha não fecha, pede revisão em vez de aceitar a coluna errada.

    Quando quantidade × unitário não bate com o total, algum valor pode ter
    caído na coluna errada durante a reconstrução da tabela. O dado continua
    visível — só não é dado como certo.
    """
    total = item.get("valor_total")
    if total is None:
        return True
    quantidade = item.get("quantidade")
    unitario = item.get("valor_unitario")
    if not (quantidade and unitario):
        return False
    try:
        esperado = (Decimal(quantidade) * Decimal(unitario)).quantize(
            Decimal("0.01")
        )
    except (InvalidOperation, ValueError):
        return True
    return abs(esperado - Decimal(total)) > Decimal("0.02")


def _parece_cabecalho_de_itens(linha: str) -> bool:
    """Se a linha é o cabeçalho de uma tabela de itens — e não um item.

    Um cabeçalho reúne o vocabulário das colunas (descrição, quantidade,
    unitário e total) e não traz valor em dinheiro. O rótulo da descrição é o
    que separa o cabeçalho de uma linha de item rotulada ("ITEM: ... | QTD: 2 |
    UNIDADE: UN | TOTAL: 541,00"): sem ele, colunas de números bastariam para
    descartar um produto. Por isso a descrição é obrigatória aqui.
    """
    compacta = _normalizar(linha).replace(" ", "")
    if not compacta or _RE_SOMENTE_DINHEIRO.match(linha):
        return False
    if not any(
        palavra in compacta
        for palavra in ("descricao", "discriminacao", "produto")
    ):
        return False
    grupos = (
        "qtd" in compacta or "quant" in compacta,
        "unit" in compacta,
        "total" in compacta,
    )
    return sum(grupos) >= 2


def _tem_cabecalho_de_itens(linhas: list[str]) -> bool:
    """Se alguma linha do documento é o cabeçalho da tabela de itens."""
    return any(_parece_cabecalho_de_itens(linha) for linha in linhas)


def _parece_item(texto: str) -> bool:
    """Uma linha só descreve um item se não for rótulo de resumo nem número."""
    limpo = (texto or "").strip()
    if len(limpo) < 2 or len(re.findall(r"[A-Za-zÀ-ÿ]", limpo)) < 2:
        return False
    compacta = _normalizar(limpo).replace(" ", "")
    return not any(compacta.startswith(palavra) for palavra in _PALAVRAS_QUE_NAO_SAO_ITEM)


def _limpar_descricao(texto: str) -> str:
    """Tira o marcador de item do começo e o excesso de espaços."""
    limpo = re.sub(
        r"(?i)^\s*(?:ITEM|PRODUTO|DESCRI[CÇ][AÃ]O)\s*[:#\-]?\s*\d*\s*",
        "",
        texto,
    )
    return " ".join(limpo.split())[:200]


def _itens(linhas: list[str]) -> list[dict]:
    """Lê itens de cupom, de nota e de recibo, na ordem em que aparecem.

    Três formatos convivem: campos rotulados ("ITEM: ... | QTD: ..."), a linha de
    cupom com a descrição acima e o valor abaixo, e a tabela de uma linha só.
    """
    itens: list[dict] = []
    descricao_pendente = ""

    for linha in linhas:
        if not linha:
            continue

        # O cabeçalho da tabela não é item nem descrição: zerar a pendência aqui
        # evita que ele seja colado na descrição do primeiro produto.
        if _parece_cabecalho_de_itens(linha):
            descricao_pendente = ""
            continue

        rotulado = _item_rotulado(linha)
        if rotulado is not None:
            itens.append(rotulado)
            descricao_pendente = ""
            continue

        medida = _item_de_medida(linha, descricao_pendente)
        if medida is not None:
            itens.append(medida)
            descricao_pendente = ""
            continue

        tabela = _item_tabular(linha, descricao_pendente)
        if tabela is not None:
            itens.append(tabela)
            descricao_pendente = ""
            continue

        # O total do item costuma cair sozinho na linha seguinte.
        if itens and not itens[-1]["valor_total"] and _RE_SOMENTE_DINHEIRO.match(linha):
            itens[-1]["valor_total"] = _valor_monetario(linha)
            continue

        if _parece_item(linha):
            descricao_pendente = _limpar_descricao(linha)
        else:
            descricao_pendente = ""

    return [item for item in itens if item.get("descricao")]


def processar_texto(texto: str) -> dict:
    """Extrai os campos reconhecíveis de um texto já obtido — puro e testável.

    Nenhuma chamada de rede, nenhum acesso ao banco: entra texto, saem propostas.
    Dois rótulos podem apontar a mesma coisa ("RAZÃO SOCIAL" e "Fornecedor"); a
    ordem das tuplas de rótulo é a ordem de preferência.
    """
    linhas = _linhas(texto)
    itens = _itens(linhas)
    fornecedor = _fornecedor(linhas, texto)
    total = _valor_monetario(_valor_apos_rotulo(linhas, _ROTULOS_DE_TOTAL))
    return {
        "fornecedor": fornecedor,
        "cnpj": _documento_fiscal(texto),
        "data_compra": _data(linhas),
        "numero_documento": _documento_da_nota(texto),
        "valor_total": total,
        "forma_pagamento": (
            " ".join((_valor_apos_rotulo(linhas, _ROTULOS_DE_PAGAMENTO) or "").split())[:60]
            or None
        ),
        "itens": itens,
        # O cabeçalho foi reconhecido, mas nenhuma linha de item foi associada:
        # o rascunho precisa dizer isso em vez de parecer vazio por descuido.
        "tabela_de_itens_sem_linhas": _tem_cabecalho_de_itens(linhas) and not itens,
    }


# --------------------------------------------------------------------------- #
# Aquisição do texto: PDF nativo, imagem e PDF escaneado
# --------------------------------------------------------------------------- #

_MOTOR_OCR = None


def ocr_disponivel() -> bool:
    """Se o OCR local está instalado neste ambiente (não carrega os modelos)."""
    try:
        import pypdfium2  # noqa: F401
        from rapidocr_onnxruntime import RapidOCR  # noqa: F401
    except ImportError:
        return False
    return True


def _motor_ocr():
    """Carrega o motor uma única vez: os modelos custam segundos para subir."""
    global _MOTOR_OCR
    if _MOTOR_OCR is None:
        from rapidocr_onnxruntime import RapidOCR

        _MOTOR_OCR = RapidOCR()
    return _MOTOR_OCR


def _texto_do_pdf(conteudo: bytes) -> str:
    """Extrai texto da camada textual do PDF; nunca tenta OCR aqui."""
    leitor = PdfReader(io.BytesIO(conteudo), strict=False)
    if getattr(leitor, "is_encrypted", False):
        raise ValueError("O PDF está protegido e não permite leitura local.")
    return "\n".join(
        (pagina.extract_text() or "")
        for pagina in leitor.pages[:MAX_PAGINAS]
    ).strip()


def _ajustar_para_ocr(imagem):
    """Redimensiona para o tamanho em que o OCR acerta o espaçamento das palavras."""
    from PIL import Image

    maior = max(imagem.size)
    if maior <= 0:
        return imagem
    fator = 1.0
    if maior < LADO_MINIMO_OCR:
        fator = min(3.0, LADO_MINIMO_OCR / maior)
    elif maior > LADO_MAXIMO_OCR:
        fator = LADO_MAXIMO_OCR / maior
    if fator == 1.0:
        return imagem
    return imagem.resize(
        (max(1, int(imagem.width * fator)), max(1, int(imagem.height * fator))),
        Image.LANCZOS,
    )


@dataclass(frozen=True)
class BlocoDeTexto:
    """Um trecho reconhecido e a caixa onde ele estava na página.

    O RapidOCR devolve uma lista de blocos e, em uma tabela, cada célula vem como
    um bloco próprio. Guardar a caixa é o que permite reconstruir a linha: blocos
    com o mesmo centro vertical pertencem à mesma linha, e a ordem horizontal diz
    qual coluna é qual.
    """

    texto: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def centro_y(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def altura(self) -> float:
        return max(0.0, self.y1 - self.y0)


def _blocos_do_resultado(resultado) -> list[BlocoDeTexto]:
    """Converte a saída crua do motor em blocos com coordenadas.

    A caixa chega como quatro pontos; basta o retângulo que os contém. Blocos sem
    texto ou sem caixa são descartados — não há o que associar a eles.
    """
    blocos: list[BlocoDeTexto] = []
    for linha in resultado or []:
        if len(linha) < 2 or not linha[1]:
            continue
        pontos = [ponto for ponto in (linha[0] or []) if len(ponto) >= 2]
        if not pontos:
            continue
        xs = [ponto[0] for ponto in pontos]
        ys = [ponto[1] for ponto in pontos]
        texto = str(linha[1]).strip()
        if texto:
            blocos.append(
                BlocoDeTexto(
                    texto=texto,
                    x0=min(xs),
                    y0=min(ys),
                    x1=max(xs),
                    y1=max(ys),
                )
            )
    return blocos


def _blocos_em_linhas(blocos: list[BlocoDeTexto]) -> list[list[BlocoDeTexto]]:
    """Agrupa blocos que dividem a mesma linha visual e ordena cada linha por x.

    Dois blocos estão na mesma linha quando os centros verticais ficam próximos em
    relação à altura típica do texto. É a coordenada que reconecta colunas soltas
    ("2UN" ao lado de "Bomba submersa") sem depender da ordem em que o motor
    devolveu os blocos.
    """
    if not blocos:
        return []
    alturas = sorted(bloco.altura for bloco in blocos if bloco.altura > 0)
    referencia = alturas[len(alturas) // 2] if alturas else 0.0
    tolerancia = max(referencia * 0.6, 1.0)

    linhas: list[list[BlocoDeTexto]] = []
    for bloco in sorted(blocos, key=lambda item: (item.centro_y, item.x0)):
        for linha in linhas:
            centro = sum(item.centro_y for item in linha) / len(linha)
            if abs(bloco.centro_y - centro) <= tolerancia:
                linha.append(bloco)
                break
        else:
            linhas.append([bloco])
    return [sorted(linha, key=lambda item: item.x0) for linha in linhas]


def _texto_de_blocos(blocos: list[BlocoDeTexto]) -> str:
    """Texto na ordem de leitura, com cada linha da tabela remontada.

    As células de uma mesma linha voltam a ser unidas por um espaço, na ordem
    horizontal: é assim que descrição, quantidade, unidade, unitário e total
    retornam à mesma linha, que é o que o parser entende como um item.
    """
    linhas = _blocos_em_linhas(blocos)
    return "\n".join(
        " ".join(bloco.texto for bloco in linha) for linha in linhas
    )


def _texto_de_imagem(imagem) -> str:
    """Reconhece o texto de uma imagem preservando as coordenadas dos blocos."""
    resultado, _confianca = _motor_ocr()(_ajustar_para_ocr(imagem.convert("RGB")))
    return _texto_de_blocos(_blocos_do_resultado(resultado))


def _texto_de_pdf_escaneado(conteudo: bytes) -> str:
    """Rasteriza as primeiras páginas do PDF e reconhece cada uma."""
    import pypdfium2

    documento = pypdfium2.PdfDocument(io.BytesIO(conteudo))
    try:
        paginas = []
        total = min(len(documento), MAX_PAGINAS_OCR)
        for indice in range(total):
            bitmap = documento[indice].render(scale=2)
            paginas.append(_texto_de_imagem(bitmap.to_pil()))
        return "\n".join(paginas).strip()
    finally:
        documento.close()


def _texto_por_ocr(conteudo: bytes, mime_type: str) -> str:
    """Texto obtido por OCR local — o único ponto que fala com o motor.

    É o costura que os testes substituem: o resto da leitura é determinístico.
    """
    if mime_type == MIME_PDF:
        return _texto_de_pdf_escaneado(conteudo)
    from PIL import Image

    with Image.open(io.BytesIO(conteudo)) as imagem:
        return _texto_de_imagem(imagem)


def _procedencia(origem: str) -> str:
    return f"{origem}; requer conferência humana."


def _tem_proposta(dados: dict) -> bool:
    """Se a leitura produziu algo que valha a pena mostrar para revisão."""
    return any(
        (
            dados.get("fornecedor"),
            dados.get("cnpj"),
            dados.get("data_compra"),
            dados.get("numero_documento"),
            dados.get("valor_total"),
            dados.get("forma_pagamento"),
            dados.get("itens"),
            # Só o cabeçalho lido já é algo para mostrar: o rascunho diz que os
            # itens ficaram pendentes em vez de parecer que nada foi reconhecido.
            dados.get("tabela_de_itens_sem_linhas"),
        )
    )


def _contrato_vazio() -> dict:
    """O contrato de sugestões, ainda sem nenhum campo reconhecido."""
    return {
        "versao_contrato": VERSAO_CONTRATO,
        "procedencia": "",
        "fornecedor": None,
        "cnpj": None,
        "data_compra": None,
        "numero_documento": None,
        "valor_total": None,
        "forma_pagamento": None,
        "itens": [],
        "tabela_de_itens_sem_linhas": False,
        "aviso_itens": "",
    }


def _propostas(texto: str, origem: str) -> tuple[str, dict, str]:
    """Fecha a leitura: junta o que foi reconhecido e diz se dá para revisar."""
    dados = _contrato_vazio()
    dados.update(processar_texto(texto))
    dados["procedencia"] = _procedencia(origem)
    if dados.get("tabela_de_itens_sem_linhas"):
        dados["aviso_itens"] = (
            "O cabeçalho da tabela de itens foi reconhecido, mas as linhas de "
            "produto não puderam ser associadas com segurança. Confira o "
            "documento e informe os itens manualmente."
        )
    if not _tem_proposta(dados):
        return (
            StatusLeituraDocumentoCompra.REVISAO_MANUAL,
            dados,
            "O documento foi lido, mas não reconhecemos campos com segurança. "
            "Confira os dados manualmente.",
        )
    return StatusLeituraDocumentoCompra.LIDO, dados, ""


def _texto_nativo_suficiente(texto: str) -> bool:
    """Se a camada textual do PDF é corpo de documento e não um rodapé solto.

    Um PDF escaneado pode trazer uma linha de texto na imagem — um rodapé. Se
    bastasse contar caracteres, esse arquivo iria para o parser textual e a
    tabela, que está na imagem, ficaria sem OCR. Por isso a camada textual só
    serve quando tem tamanho e linhas úteis de um documento.
    """
    linhas_uteis = [linha for linha in (texto or "").splitlines() if linha.strip()]
    return (
        len(texto.strip()) >= MINIMO_TEXTO_NATIVO
        and len(linhas_uteis) >= MINIMO_LINHAS_NATIVAS
    )


def sugerir_dados(*, conteudo: bytes, mime_type: str) -> tuple[str, dict, str]:
    """Devolve situação, contrato de sugestões e explicação, sem persistir Compra."""
    if mime_type == MIME_PDF:
        try:
            texto = _texto_do_pdf(conteudo)
        except (PdfReadError, ValueError, OSError):
            return (
                StatusLeituraDocumentoCompra.ERRO,
                _contrato_vazio(),
                "O PDF foi armazenado, mas não pôde ser lido localmente. "
                "Confira os dados manualmente.",
            )
        if _texto_nativo_suficiente(texto):
            return _propostas(texto, "Texto extraído localmente do PDF")
    elif mime_type not in MIMES_DE_IMAGEM:
        return (
            StatusLeituraDocumentoCompra.REVISAO_MANUAL,
            _contrato_vazio(),
            "O arquivo foi armazenado, mas este formato não tem leitura "
            "automática. Confira os dados manualmente.",
        )

    # Daqui para baixo o texto só pode vir do OCR local.
    try:
        texto = _texto_por_ocr(conteudo, mime_type)
    except ImportError:
        return (
            StatusLeituraDocumentoCompra.REVISAO_MANUAL,
            _contrato_vazio(),
            "O arquivo foi armazenado, mas o OCR local não está instalado "
            "neste ambiente. Confira os dados manualmente.",
        )
    except Exception:
        logger.exception("Falha inesperada no OCR local do documento da compra.")
        return (
            StatusLeituraDocumentoCompra.ERRO,
            _contrato_vazio(),
            "O arquivo foi armazenado, mas a leitura automática não pôde ser "
            "concluída. Confira os dados manualmente ou tente novamente.",
        )

    if not texto.strip():
        return (
            StatusLeituraDocumentoCompra.REVISAO_MANUAL,
            _contrato_vazio(),
            "O arquivo foi armazenado, mas não foi possível reconhecer texto "
            "nele. Confira os dados manualmente ou tente uma foto mais nítida.",
        )
    return _propostas(texto, "Texto reconhecido por OCR local")


def associar_itens_existentes(documento: DocumentoCompra) -> dict:
    """Anota correspondências exatas do catálogo no contrato de sugestões."""
    dados = dict(documento.dados_extraidos or {})
    candidatos = list(
        ItemEstoque.objects.filter(
            condominio_id=documento.compra.condominio_id,
            ativo=True,
        )
    )
    itens = []
    for proposta in dados.get("itens", []):
        linha = dict(proposta)
        nome = _normalizar(linha.get("descricao", ""))
        exatos = [item for item in candidatos if _normalizar(item.nome) == nome]
        linha["item_estoque_sugerido"] = str(exatos[0].pk) if len(exatos) == 1 else ""
        semelhantes = []
        if not exatos and nome:
            tokens = set(nome.split())
            ranqueados = []
            for item in candidatos:
                nome_candidato = _normalizar(item.nome)
                intersecao = tokens.intersection(nome_candidato.split())
                pontuacao = len(intersecao) / max(
                    len(tokens), len(nome_candidato.split()), 1
                )
                if nome in nome_candidato or nome_candidato in nome or pontuacao >= 0.5:
                    ranqueados.append((pontuacao, item))
            semelhantes = [
                str(item.pk)
                for _pontuacao, item in sorted(
                    ranqueados,
                    key=lambda par: (-par[0], _normalizar(par[1].nome)),
                )[:5]
            ]
        linha["item_estoque_candidatos"] = semelhantes
        if exatos:
            linha["categoria_sugerida"] = exatos[0].categoria_texto
        itens.append(linha)
    dados["itens"] = itens
    return dados


__all__ = [
    "associar_itens_existentes",
    "ocr_disponivel",
    "processar_texto",
    "sugerir_dados",
]
