"""Leitura local da etiqueta de uma encomenda na Portaria.

O porteiro fotografa a encomenda e o Eithra propõe a unidade e o morador a
partir do que está escrito na etiqueta. A leitura usa o mesmo motor OCR local
das compras do estoque — nada é enviado para fora e nenhum modelo de linguagem
participa: o texto é reconhecido e casado com o cadastro do condomínio por
regras determinísticas.

O que sai daqui é sempre uma sugestão para o porteiro conferir, nunca um
registro: a leitura não grava nada e nunca decide sozinha para quem a encomenda
vai. Quando o documento não permite concluir com segurança, o campo volta vazio
e a confiança é baixa — melhor nada do que um destino inventado.
"""
from __future__ import annotations

import difflib
import io
import re
import unicodedata

from apps.comunidade.models import Unidade, VinculoPessoaUnidade
from apps.estoque.leitura_compra import (
    _ajustar_para_ocr,
    _blocos_do_resultado,
    _motor_ocr,
    _texto_de_blocos,
    ocr_disponivel,
)

from .forms import TIPOS_DE_RESIDENTE

# Acima disto dois nomes são a mesma pessoa para a portaria. O limiar é alto de
# propósito: casar "João" com "Joana" mandaria a encomenda para a unidade
# errada, e a sugestão existe para poupar digitação, não para adivinhar.
LIMIAR_DE_NOME = 0.75

# As transportadoras que aparecem com mais frequência em etiqueta no Brasil.
# Não é catálogo fechado: o que não estiver aqui simplesmente não é reconhecido,
# e o porteiro segue digitando o que precisar.
TRANSPORTADORAS = (
    "Correios",
    "Mercado Livre",
    "Amazon",
    "Shopee",
    "Magalu",
    "Jadlog",
    "Loggi",
    "Total Express",
    "Sequoia",
    "J&T",
)

# O número da unidade vem precedido de um rótulo. A alternância vai do mais
# longo para o mais curto para "apartamento" não ser lido como "ap".
_RE_UNIDADE = re.compile(
    r"(?i)\b(?:apartamento|apto|apt|unidade|unid|casa|ap)\b\.?\s*"
    r"(?:n[º°o]?\.?|numero)?\s*[:#\-]?\s*(?P<numero>\d{1,5})"
)
# Bloco ou torre, quando a etiqueta distingue prédios: "Bloco A", "Torre 2".
_RE_BLOCO = re.compile(
    r"(?i)\b(?:bloco|torre|bl)\b\.?\s*[:#\-]?\s*(?P<bloco>[A-Za-z0-9]{1,6})"
)
# Rótulos que anunciam o destinatário; o valor é o que vem depois, na mesma
# linha ou na linha seguinte.
_ROTULOS_DE_DESTINATARIO = (
    "destinatario",
    "destinataria",
    "dest",
    "para",
    "entregar a",
    "nome do destinatario",
)


def _normalizar(texto: str | None) -> str:
    """Compara sem acento e sem caixa — o OCR erra acento e quem lê não vê."""
    decomposto = unicodedata.normalize("NFKD", texto or "")
    sem_acentos = "".join(c for c in decomposto if not unicodedata.combining(c))
    return " ".join(sem_acentos.casefold().split())


def _digitos(texto: str | None) -> str:
    return re.sub(r"\D", "", texto or "")


def _linhas(texto: str) -> list[str]:
    return [linha.strip() for linha in (texto or "").splitlines() if linha.strip()]


def _texto_da_foto(conteudo: bytes) -> str:
    """Texto reconhecido por OCR local — o único ponto que fala com o motor.

    É a costura que os testes substituem: o resto da leitura é determinístico.
    """
    from PIL import Image

    with Image.open(io.BytesIO(conteudo)) as imagem:
        resultado, _confianca = _motor_ocr()(_ajustar_para_ocr(imagem.convert("RGB")))
    return _texto_de_blocos(_blocos_do_resultado(resultado))


def _numero_e_bloco(texto: str) -> tuple[str, str]:
    """O número da unidade e o bloco/torre, como estiverem escritos na etiqueta."""
    achado_unidade = _RE_UNIDADE.search(texto or "")
    achado_bloco = _RE_BLOCO.search(texto or "")
    numero = achado_unidade.group("numero") if achado_unidade else ""
    bloco = achado_bloco.group("bloco") if achado_bloco else ""
    return numero, bloco


def _nome_do_destinatario(linhas: list[str]) -> str:
    """O destinatário: pelo rótulo e, sem ele, pela linha em maiúsculas mais longa."""
    for indice, linha in enumerate(linhas):
        compacta = _normalizar(linha).replace(" ", "")
        for rotulo in _ROTULOS_DE_DESTINATARIO:
            chave = rotulo.replace(" ", "")
            if not compacta.startswith(chave):
                continue
            achado = re.match(
                rf"(?i)^\s*{re.escape(rotulo)}\s*[:#\-]?\s*(?P<valor>.*)$",
                linha,
            )
            valor = (achado.group("valor") if achado else "").strip(" :#-|")
            if valor:
                return valor
            if indice + 1 < len(linhas):
                return linhas[indice + 1]
    return _linha_em_maiusculas(linhas)


def _linha_em_maiusculas(linhas: list[str]) -> str:
    """A linha mais longa escrita só em maiúsculas — o nome costuma vir assim."""
    candidatas = []
    for linha in linhas:
        letras = [c for c in linha if c.isalpha()]
        if len(letras) < 4:
            continue
        if not all(c.isupper() for c in letras):
            continue
        # Uma linha só de dígitos e símbolos não é nome; exige ao menos uma
        # sequência de letras que pareça uma palavra.
        if not re.search(r"[A-ZÀ-Ý]{2,}", linha):
            continue
        candidatas.append(linha)
    if not candidatas:
        return ""
    return max(candidatas, key=len).strip()


def _transportadora(texto: str) -> str:
    """A transportadora, quando uma das conhecidas aparece na etiqueta."""
    plano = _normalizar(texto)
    for nome in TRANSPORTADORAS:
        if _normalizar(nome) in plano:
            return nome
    return ""


def _bloco_normalizado(valor: str | None) -> str:
    """O bloco/torre sem o rótulo: "Bloco A" e "A" são o mesmo bloco."""
    limpo = re.sub(r"(?i)\b(?:bloco|torre|bl)\b\.?", " ", valor or "")
    return re.sub(r"[^0-9a-z]", "", _normalizar(limpo))


def _casar_unidade(numero: str, bloco: str, condominio):
    """A unidade do cadastro que corresponde ao que foi lido, se houver uma só."""
    if not numero and not bloco:
        return None
    unidades = list(Unidade.objects.do_condominio(condominio).ativas())

    def exata(unidade) -> bool:
        return bool(numero) and _digitos(unidade.identificador) == numero

    def contem(unidade) -> bool:
        return bool(numero) and numero in _digitos(unidade.identificador)

    bloco_lido = _bloco_normalizado(bloco)

    def confere_bloco(unidade) -> bool:
        if not bloco_lido:
            return True
        return _bloco_normalizado(unidade.bloco_torre) == bloco_lido

    for filtro in (exata, contem):
        candidatas = [u for u in unidades if filtro(u) and confere_bloco(u)]
        if candidatas:
            return candidatas[0]
    return None


def _casar_pessoa(nome: str, unidade, condominio):
    """O morador cujo nome mais se parece com o destinatário lido."""
    if not nome:
        return None
    vinculos = (
        VinculoPessoaUnidade.objects.do_condominio(condominio)
        .vigentes()
        .filter(tipo_vinculo__in=TIPOS_DE_RESIDENTE)
        .select_related("pessoa")
    )
    if unidade is not None:
        vinculos = vinculos.filter(unidade=unidade)

    alvo = _normalizar(nome)
    melhor, melhor_ratio = None, 0.0
    for vinculo in vinculos:
        pessoa = vinculo.pessoa
        if not pessoa.ativa:
            continue
        ratio = difflib.SequenceMatcher(None, alvo, _normalizar(str(pessoa))).ratio()
        if ratio > melhor_ratio:
            melhor, melhor_ratio = pessoa, ratio
    if melhor is not None and melhor_ratio >= LIMIAR_DE_NOME:
        return melhor
    return None


def _contrato_vazio() -> dict:
    return {
        "unidade_id": "",
        "unidade_rotulo": "",
        "pessoa_id": "",
        "nome": "",
        "transportadora": "",
        "confianca": "baixa",
    }


def ler_etiqueta(conteudo: bytes, condominio) -> dict:
    """Lê a etiqueta e propõe unidade, morador e transportadora — sem gravar nada.

    Devolve sempre o mesmo contrato; o que não foi reconhecido volta vazio e a
    confiança diz o quanto se pode confiar: alta quando unidade e morador foram
    encontrados, média quando só um deles, baixa quando nada casou.
    """
    texto = _texto_da_foto(conteudo)
    if not texto.strip():
        return _contrato_vazio()

    numero, bloco = _numero_e_bloco(texto)
    unidade = _casar_unidade(numero, bloco, condominio)
    nome_lido = _nome_do_destinatario(_linhas(texto))
    pessoa = _casar_pessoa(nome_lido, unidade, condominio)
    transportadora = _transportadora(texto)

    dados = _contrato_vazio()
    dados["transportadora"] = transportadora
    if unidade is not None:
        dados["unidade_id"] = str(unidade.pk)
        dados["unidade_rotulo"] = unidade.rotulo
    if pessoa is not None:
        dados["pessoa_id"] = str(pessoa.pk)
        dados["nome"] = str(pessoa)
    elif nome_lido:
        dados["nome"] = nome_lido

    if unidade is not None and pessoa is not None:
        dados["confianca"] = "alta"
    elif unidade is not None or pessoa is not None:
        dados["confianca"] = "media"
    return dados


__all__ = ["TRANSPORTADORAS", "ler_etiqueta", "ocr_disponivel"]
