"""Deteccao deterministica do layout do PDF de balanco.

Nesta fase o objetivo e apenas reconhecer o formato do documento recebido. O
detector nao extrai receitas, despesas, saldos ou qualquer dado financeiro; ele
so reusa o PDF privado, coleta evidencias tecnicas e grava o layout encontrado.

O desenho segue tres principios:

* sem IA e sem OCR;
* custo local, usando apenas a camada textual do PDF;
* resultado auditavel, baseado em evidencias curtas e deterministicas.

A classificacao olha apenas para o conteudo estrutural do comeco do documento:
marcadores textuais, presenca de indice, secoes financeiras, densidade textual
por pagina com texto e a proporcao de paginas com texto. **A quantidade de
paginas do PDF nao e caracteristica de layout**: um mesmo modelo pode vir curto,
longo, com parte do balancete digitalizada ou com muitos comprovantes anexados.
Por isso o total de paginas tem peso zero na decisao e a amostragem se concentra
na abertura estrutural, em vez de se espalhar pelo arquivo inteiro — anexos e
comprovantes nao podem determinar o formato.
"""
from __future__ import annotations

import io
import time
import unicodedata
from dataclasses import dataclass

from django.conf import settings
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from apps.balancos.constants import LayoutBalanco, StatusImportacao
from apps.balancos.storage import ErroDeStorage, obter_provider

PAGINAS_PADRAO = 15

# O que fica registrado quando o balancete veio todo escaneado e o ambiente nao
# tem OCR local: sem ele, a camada textual nao existe e nao ha o que ler.
AVISO_SEM_OCR = "PDF escaneado: OCR local não instalado"

# Densidade textual, medida em caracteres por pagina COM texto. E o sinal que
# substitui a contagem absoluta de caracteres: um PDF longo com muitos anexos
# escaneados nao deve parecer "pobre em texto" so porque a amostra e grande, nem
# um PDF curto deve parecer "rico" pelo mesmo motivo. Medir por pagina com texto
# tira o tamanho do arquivo da conta.
LIMIAR_DENSIDADE_ATUAL = 200
LIMIAR_ESCASSEZ_LEGADO = 120

PESOS_ATUAL_CANONICO = {
    "indice": 3,
    "demonstrativo de contas": 3,
    "resumo de contas": 2,
    "receitas": 1,
    "despesas": 1,
    "resumo financeiro": 2,
    "saldo bancario": 2,
    "saldo contabil": 2,
    "extratos": 1,
    "realizado": 1,
    "receitas detalhadas": 3,
    "inadimplencia": 2,
    "despesas detalhadas": 3,
    # Peso baixo de proposito: indicar anexos e diferente de o layout ser
    # determinado pelos anexos. Uma secao "documentos anexos" ajuda a reconhecer
    # o modelo, mas nao pode ser o que decide o formato.
    "documentos anexos": 1,
}

VARIANTES_ATUAL_CANONICO = {
    "indice": ("indice",),
    "demonstrativo de contas": ("demonstrativo de contas",),
    "resumo de contas": ("resumo de contas",),
    "receitas": ("receitas",),
    "despesas": ("despesas",),
    "resumo financeiro": ("resumo financeiro",),
    "saldo bancario": ("saldo bancario",),
    "saldo contabil": ("saldo contabil",),
    "extratos": ("extratos", "extrato bancario", "extratos bancarios"),
    "realizado": ("realizado",),
    "receitas detalhadas": ("receitas detalhadas",),
    "inadimplencia": ("inadimplencia",),
    "despesas detalhadas": ("despesas detalhadas",),
    "documentos anexos": (
        "documentos anexos",
        "comprovantes anexos",
        "documentos comprovantes anexos",
    ),
}

# Marcadores textuais do balancete antigo. "Balancete" cobre o quadro
# historico generico; "Metrica" e o nome da administradora anterior, e
# "demonstrativo" o rotulo do resumo sintetico que ela emite. A normalizacao ja
# remove acento, por isso a comparacao e feita sem eles. Reconhecer o rotulo nao
# basta para classificar: sozinho ele nao decide nada — o conjunto de sinais e
# que classifica, como no modelo atual.
VARIANTES_LEGADO = {
    "balancete": ("balancete",),
    "metrica": ("metrica",),
    "demonstrativo": ("demonstrativo",),
}

PESOS_LEGADO = {
    "balancete": 4,
    "metrica": 4,
    "demonstrativo": 4,
    "texto escasso": 2,
    "poucas paginas com texto": 1,
    "predominio visual": 2,
    "sem sinais fortes do atual": 1,
}

# Frase de auditoria de cada marcador textual legado, para a lista de evidencias.
_DESCRICAO_DE_MARCADOR_LEGADO = {
    "balancete": "Marcador historico de balancete encontrado.",
    "metrica": "Marcador da administradora anterior (Metrica) encontrado.",
    "demonstrativo": "Rotulo de demonstrativo sintetico encontrado.",
}


class ErroDeDeteccaoDeLayout(Exception):
    """Falha tecnica ao tentar identificar o layout do PDF."""


@dataclass(frozen=True)
class ResultadoDeteccaoLayout:
    """Resultado tecnico e auditavel da deteccao."""

    layout: str
    confidence: float
    evidencias: list[str]
    paginas_analisadas: int
    total_paginas: int
    tempo_download_ms: int
    tempo_leitura_ms: int
    tempo_total_ms: int
    aviso: str = ""


@dataclass(frozen=True)
class EvidenciasColetadas:
    """Resumo do que foi visto nas paginas inspecionadas."""

    texto_normalizado: str
    total_paginas: int
    paginas_analisadas: int
    paginas_com_texto: int
    caracteres_extraidos: int
    imagens_detectadas: int
    marcadores_atual: list[str]
    marcadores_legado: list[str]
    tempo_leitura_ms: int
    aviso: str = ""

    @property
    def proporcao_paginas_com_texto(self) -> float:
        if not self.paginas_analisadas:
            return 0.0
        return self.paginas_com_texto / self.paginas_analisadas

    @property
    def densidade_textual(self) -> float:
        """Caracteres por pagina que tem texto.

        Mede o quanto o documento e textual sem deixar o tamanho do arquivo
        influenciar: paginas de anexo, sem camada textual, ficam de fora da
        conta em vez de diluir a densidade.
        """
        if not self.paginas_com_texto:
            return 0.0
        return self.caracteres_extraidos / self.paginas_com_texto


def detectar_layout_balanco(
    importacao, *, provider=None, conteudo: bytes | None = None
) -> ResultadoDeteccaoLayout:
    """Le o PDF privado e grava o layout detectado na importacao.

    A deteccao e idempotente: reexecutar sobre o mesmo arquivo produz o mesmo
    resultado, sem criar objeto novo e sem tocar em ``BalancoMensal``. O
    ``conteudo`` pode ser passado pronto por quem ja o tem em maos, para nao
    baixar o mesmo arquivo duas vezes.
    """
    importacao.status = StatusImportacao.PROCESSANDO
    importacao.erro_processamento = ""
    importacao.save(update_fields=["status", "erro_processamento", "atualizado_em"])

    inicio_total = time.monotonic()
    try:
        if conteudo is None:
            conteudo, tempo_download_ms = _baixar_pdf_privado(
                importacao, provider=provider
            )
        else:
            tempo_download_ms = 0
        resultado = detectar_layout_do_pdf(
            conteudo,
            tempo_download_ms=tempo_download_ms,
        )
    except (ErroDeStorage, ErroDeDeteccaoDeLayout) as erro:
        importacao.status = StatusImportacao.ERRO
        importacao.erro_processamento = str(erro)
        importacao.save(update_fields=["status", "erro_processamento", "atualizado_em"])
        raise

    importacao.layout_detectado = resultado.layout
    importacao.status = StatusImportacao.PRONTO
    # O aviso (por exemplo, balancete escaneado sem OCR local) fica no motivo do
    # processamento: nao e falha de recebimento, mas diz por que nao ha leitura.
    importacao.erro_processamento = resultado.aviso
    importacao.save(
        update_fields=[
            "layout_detectado",
            "status",
            "erro_processamento",
            "atualizado_em",
        ]
    )

    tempo_total_ms = int((time.monotonic() - inicio_total) * 1000)
    return ResultadoDeteccaoLayout(
        layout=resultado.layout,
        confidence=resultado.confidence,
        evidencias=resultado.evidencias,
        paginas_analisadas=resultado.paginas_analisadas,
        total_paginas=resultado.total_paginas,
        tempo_download_ms=resultado.tempo_download_ms,
        tempo_leitura_ms=resultado.tempo_leitura_ms,
        tempo_total_ms=tempo_total_ms,
        aviso=resultado.aviso,
    )


def detectar_layout_do_pdf(
    conteudo: bytes,
    *,
    tempo_download_ms: int = 0,
) -> ResultadoDeteccaoLayout:
    """Classifica o PDF em layout atual, legado ou desconhecido."""
    evidencias = _coletar_evidencias(conteudo)
    layout, confidence, itens = _classificar(evidencias)
    return ResultadoDeteccaoLayout(
        layout=layout,
        confidence=confidence,
        evidencias=itens,
        paginas_analisadas=evidencias.paginas_analisadas,
        total_paginas=evidencias.total_paginas,
        tempo_download_ms=tempo_download_ms,
        tempo_leitura_ms=evidencias.tempo_leitura_ms,
        tempo_total_ms=tempo_download_ms + evidencias.tempo_leitura_ms,
        aviso=evidencias.aviso,
    )


def _texto_de_ocr_da_abertura(conteudo: bytes) -> tuple[str, str]:
    """Texto da primeira pagina pelo OCR local, ou o motivo de nao haver texto.

    So e chamada quando o PDF nao tem camada textual nenhuma e ha imagens: e o
    caso do balancete escaneado. Reaproveita o caminho de OCR do extrator
    legado, limitado a uma pagina — o detector so precisa reconhecer o rotulo do
    documento, nao le-lo. Devolve o texto e, quando nao houver como reconhecer,
    o aviso que fica registrado na importacao.
    """
    if not conteudo:
        return "", ""
    from apps.estoque import leitura_compra

    if not leitura_compra.ocr_disponivel():
        return "", AVISO_SEM_OCR

    # Import tardio: o extrator legado importa este modulo no topo, e um import
    # direto aqui fecharia o ciclo.
    from apps.balancos.extrator_legado import ErroDeExtracao, _texto_por_ocr

    try:
        return _texto_por_ocr(conteudo, paginas=1), ""
    except ErroDeExtracao:
        return "", ""


def _baixar_pdf_privado(importacao, *, provider=None) -> tuple[bytes, int]:
    """Traz o PDF do storage privado pela abstracao do provedor.

    O conteudo e resolvido por ``provider.baixar`` — o provedor local le do
    disco da propria aplicacao (``MEDIA_ROOT``) e o remoto baixa por HTTP —, e
    nao por um endereco tratado como rede. Em ambiente local ``url_assinada``
    devolve um caminho de midia, que nunca foi endereco de rede: abri-lo como
    HTTP era o que fazia a leitura falhar mesmo com o arquivo no disco.
    """
    if not importacao.storage_key:
        raise ErroDeDeteccaoDeLayout("O arquivo desta importacao nao esta disponivel.")

    provider = provider or obter_provider()
    inicio = time.monotonic()
    try:
        conteudo = provider.baixar(key=importacao.storage_key)
    except ErroDeStorage as erro:
        raise ErroDeDeteccaoDeLayout(
            "Nao foi possivel acessar o PDF desta importacao."
        ) from erro

    if not conteudo:
        raise ErroDeDeteccaoDeLayout("O PDF desta importacao voltou vazio.")

    return conteudo, int((time.monotonic() - inicio) * 1000)


def _coletar_evidencias(conteudo: bytes) -> EvidenciasColetadas:
    """Extrai o minimo necessario do PDF para classificar o layout."""
    inicio = time.monotonic()
    try:
        leitor = PdfReader(io.BytesIO(conteudo))
    except (PdfReadError, ValueError) as erro:
        raise ErroDeDeteccaoDeLayout("Nao foi possivel ler a camada textual do PDF.") from erro

    if getattr(leitor, "is_encrypted", False):
        try:
            leitor.decrypt("")
        except Exception as erro:  # pragma: no cover - depende do PDF real
            raise ErroDeDeteccaoDeLayout("O PDF esta protegido e nao pode ser lido.") from erro

    total_paginas = len(leitor.pages)
    if total_paginas <= 0:
        raise ErroDeDeteccaoDeLayout("O PDF nao possui paginas legiveis.")

    texto_paginas: list[str] = []
    paginas_com_texto = 0
    imagens_detectadas = 0
    indices = _paginas_para_inspecao(total_paginas)

    for indice in indices:
        pagina = leitor.pages[indice]
        texto = pagina.extract_text() or ""
        texto_normalizado = _normalizar(texto)
        texto_paginas.append(texto_normalizado)
        if len(texto_normalizado.replace(" ", "")) >= 20:
            paginas_com_texto += 1
        imagens_detectadas += _contar_imagens(pagina)

    texto_total = " ".join(parte for parte in texto_paginas if parte).strip()
    aviso = ""
    if not texto_total and imagens_detectadas:
        # PDF so de imagem: nao ha camada textual para ler. O OCR local da
        # abertura devolve o rotulo do documento — e, sem OCR instalado, o aviso
        # que explica por que ele segue desconhecido.
        texto_ocr, aviso = _texto_de_ocr_da_abertura(conteudo)
        if texto_ocr:
            texto_total = _normalizar(texto_ocr)

    marcadores_atual = _marcadores_encontrados(texto_total, VARIANTES_ATUAL_CANONICO)
    marcadores_legado = _marcadores_encontrados(texto_total, VARIANTES_LEGADO)
    # "Demonstrativo de contas" e o titulo de uma secao do modelo atual, e a
    # busca por substring o confundiria com o rotulo do balancete antigo. Quando
    # a frase completa do modelo atual aparece, o "demonstrativo" solto nao conta
    # como sinal legado — quem o escreveu nao esta falando de layout antigo.
    if "demonstrativo de contas" in texto_total:
        marcadores_legado = [m for m in marcadores_legado if m != "demonstrativo"]

    return EvidenciasColetadas(
        texto_normalizado=texto_total,
        total_paginas=total_paginas,
        paginas_analisadas=len(indices),
        paginas_com_texto=paginas_com_texto,
        caracteres_extraidos=len(texto_total.replace(" ", "")),
        imagens_detectadas=imagens_detectadas,
        marcadores_atual=marcadores_atual,
        marcadores_legado=marcadores_legado,
        tempo_leitura_ms=int((time.monotonic() - inicio) * 1000),
        aviso=aviso,
    )


def _classificar(evidencias: EvidenciasColetadas) -> tuple[str, float, list[str]]:
    """Decide o layout usando um conjunto pequeno de sinais estruturais."""
    score_atual, evidencias_atual = _pontuar_atual(evidencias)
    score_legado, evidencias_legado = _pontuar_legado(evidencias)

    conflito = (
        score_atual >= 7
        and score_legado >= 6
        and abs(score_atual - score_legado) <= 2
    )
    if conflito:
        return (
            LayoutBalanco.DESCONHECIDO,
            _confidence_desconhecido(score_atual, score_legado),
            [
                "Indicadores conflitantes entre layout atual e legado.",
                *_evidencias_basicas(evidencias),
            ],
        )

    if score_atual >= 9 and len(evidencias.marcadores_atual) >= 4 and score_atual >= score_legado + 2:
        return (
            LayoutBalanco.ATUAL_CANONICO,
            _confidence(score_atual, 18),
            [*evidencias_atual, *_evidencias_basicas(evidencias)],
        )

    if (
        score_legado >= 7
        and evidencias.marcadores_legado
        and score_legado >= score_atual + 1
    ):
        return (
            LayoutBalanco.LEGADO,
            _confidence(score_legado, 10),
            [*evidencias_legado, *_evidencias_basicas(evidencias)],
        )

    itens = _evidencias_basicas(evidencias)
    if not evidencias.marcadores_atual and not evidencias.marcadores_legado:
        itens.insert(0, "Nao houve evidencia suficiente para classificar com seguranca.")
    else:
        itens.insert(0, "As evidencias nao bastaram para classificacao segura.")
    return (
        LayoutBalanco.DESCONHECIDO,
        _confidence_desconhecido(score_atual, score_legado),
        itens,
    )


def _pontuar_atual(evidencias: EvidenciasColetadas) -> tuple[int, list[str]]:
    score = 0
    itens: list[str] = []

    for marcador in evidencias.marcadores_atual:
        score += PESOS_ATUAL_CANONICO[marcador]
    if evidencias.marcadores_atual:
        itens.append(
            "Marcadores do modelo atual encontrados: "
            + ", ".join(evidencias.marcadores_atual[:6])
            + "."
        )

    if evidencias.densidade_textual >= LIMIAR_DENSIDADE_ATUAL:
        score += 2
        itens.append("Camada textual densa nos trechos com texto.")
    if evidencias.proporcao_paginas_com_texto >= 0.6:
        score += 2
        itens.append("Maioria das paginas analisadas contem texto legivel.")

    return score, itens


def _pontuar_legado(evidencias: EvidenciasColetadas) -> tuple[int, list[str]]:
    score = 0
    itens: list[str] = []

    for marcador in evidencias.marcadores_legado:
        score += PESOS_LEGADO[marcador]
        itens.append(_DESCRICAO_DE_MARCADOR_LEGADO.get(marcador, "Marcador historico encontrado."))
    if evidencias.densidade_textual <= LIMIAR_ESCASSEZ_LEGADO:
        score += PESOS_LEGADO["texto escasso"]
        itens.append("Pouco texto extraivel por pagina com texto.")
    if evidencias.proporcao_paginas_com_texto <= 0.4:
        score += PESOS_LEGADO["poucas paginas com texto"]
        itens.append("Poucas paginas com texto legivel.")
    if evidencias.imagens_detectadas >= max(1, evidencias.paginas_analisadas // 2):
        score += PESOS_LEGADO["predominio visual"]
        itens.append("Sinais de predominio visual no PDF.")
    if len(evidencias.marcadores_atual) <= 1:
        score += PESOS_LEGADO["sem sinais fortes do atual"]
        itens.append("Nao ha sinais fortes do modelo atual.")

    return score, itens


def _evidencias_basicas(evidencias: EvidenciasColetadas) -> list[str]:
    proporcao = int(round(evidencias.proporcao_paginas_com_texto * 100))
    densidade = int(round(evidencias.densidade_textual))
    return [
        f"{evidencias.paginas_analisadas} de {evidencias.total_paginas} paginas foram analisadas.",
        f"{proporcao}% das paginas analisadas tem texto legivel.",
        f"{densidade} caracteres por pagina com texto foram extraidos localmente.",
    ]


def _paginas_para_inspecao(total_paginas: int) -> list[int]:
    """Escolhe as paginas a ler: a abertura estrutural, nunca o PDF inteiro.

    O indice, o resumo e as secoes financeiras ficam no comeco do balanco. E ali
    que estao os sinais do formato, entao e ali que a amostra se concentra. Ler
    paginas espalhadas pelo documento misturaria anexos e comprovantes — que nao
    dizem nada sobre o layout — e faria o resultado depender do tamanho do
    arquivo. Por isso a amostra e simplesmente a abertura, e a quantidade de
    paginas do PDF deixa de influenciar a decisao.

    Um PDF parcial, com poucas paginas, e lido por inteiro: se a abertura traz
    evidencia estrutural suficiente, ele e classificado normalmente.
    """
    limite = int(
        getattr(settings, "BALANCOS_DETECTOR_MAX_PAGINAS", PAGINAS_PADRAO)
        or PAGINAS_PADRAO
    )
    limite = max(1, limite)
    return list(range(min(limite, total_paginas)))


def _marcadores_encontrados(texto: str, variantes: dict[str, tuple[str, ...]]) -> list[str]:
    encontrados: list[str] = []
    for nome, opcoes in variantes.items():
        if any(opcao in texto for opcao in opcoes):
            encontrados.append(nome)
    return encontrados


def _contar_imagens(pagina) -> int:
    """Conta xobjects de imagem de forma defensiva.

    O objetivo nao e medir com precisao tipografica, mas ter um sinal simples de
    que o PDF e mais visual do que textual.
    """
    try:
        recursos = pagina.get("/Resources")
        if recursos is None:
            return 0
        recursos = recursos.get_object()
        xobjects = recursos.get("/XObject")
        if xobjects is None:
            return 0
        xobjects = xobjects.get_object()
    except Exception:
        return 0

    total = 0
    for item in xobjects.values():
        try:
            objeto = item.get_object()
        except Exception:
            continue
        if objeto.get("/Subtype") == "/Image":
            total += 1
    return total


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(caractere for caractere in texto if not unicodedata.combining(caractere))
    texto = texto.lower()
    permitido = []
    for caractere in texto:
        if caractere.isalnum() or caractere.isspace():
            permitido.append(caractere)
        else:
            permitido.append(" ")
    return " ".join("".join(permitido).split())


def _confidence(score: int, maximo: int) -> float:
    if maximo <= 0:
        return 0.5
    proporcao = min(1.0, max(score, 0) / maximo)
    return round(0.5 + (proporcao * 0.49), 2)


def _confidence_desconhecido(score_atual: int, score_legado: int) -> float:
    base = max(score_atual, score_legado)
    return round(min(0.79, 0.45 + (base * 0.03)), 2)
