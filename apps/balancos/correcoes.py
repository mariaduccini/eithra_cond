"""Correção manual rastreável do balanço confirmado.

O Eithra lê o documento; a gestão responde por ele. Quando a leitura erra ou
não alcança um lançamento, quem responde precisa poder corrigir — sem apagar o
que a máquina leu e sem perder de vista o que foi decidido à mão.

Duas leituras convivem aqui, e a distinção é o coração deste módulo:

* o que foi **lido** — os campos base de cada lançamento, que nunca são
  reescritos, e ``dados_extraidos``, que fica intocado;
* o que foi **decidido** — o ``correcao`` ao lado de cada lançamento, mais o
  ``revisoes`` do balanço, que é a memória de quem corrigiu, quando, por quê e
  de que valor para qual.

Nada é sobrescrito em silêncio: cada correção acrescenta uma revisão ao
histórico, e os totais oficiais do documento continuam sendo os do documento.
O que se recalcula é o detalhamento — a soma dos lançamentos válidos, as
categorias, os agrupamentos e o resumo —, nunca o total declarado.
"""
from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal

from django.utils import timezone

from apps.balancos.categorias import CategoriaEithra
from apps.balancos.contratos import (
    BalancoExtraidoV1,
    DespesaExtraida,
    para_dicionario,
)
from apps.balancos.extrator_atual import (
    _agregar_categorias,
    _agregar_grupos,
    _resumo,
)
from apps.balancos.natureza_despesa import NaturezaDespesa
from apps.balancos.normalizadores import (
    chave_de_comparacao,
    decimal_para_texto,
    texto_para_decimal,
)

# Quanto de texto cabe numa justificativa. O suficiente para explicar a decisão,
# longe de virar um relatório paralelo dentro do balanço.
LIMITE_DA_JUSTIFICATIVA = 500

ORIGEM_EXTRAIDA = "extraida"
ORIGEM_MANUAL = "manual"
PREFIXO_MANUAL = "manual-"

# Cada campo corrigível sabe de onde ele foi lido. É o que permite comparar o
# valor atual com o lido e, quando a correção devolve o valor original, não
# deixar rastro de uma decisão que já não existe.
CAMPO_LIDO = {
    "descricao": "descricao_original",
    "valor": "valor",
    "categoria_eithra": "categoria_eithra",
    "natureza": "natureza",
    "grupo": "categoria_original",
}


class ErroDeCorrecao(Exception):
    """A correção não pode ser aplicada. A mensagem é para a gestão."""


# -- Identidade do lançamento ---------------------------------------------------


def identificar_despesas(despesas: list[dict]) -> None:
    """Dá a cada lançamento lido um identificador estável, uma única vez.

    O identificador vem do conteúdo — página, descrição, valor e agrupamento do
    documento —, e não da posição na lista: reordenar não troca a identidade.
    Linhas idênticas recebem um sufixo ordinal, porque aí a ordem é a única
    diferença que existe, e a correção precisa alcançar a linha certa.
    """
    vistos: dict[str, int] = {}
    for despesa in despesas:
        if despesa.get("id"):
            continue
        digest = _digest_da_despesa(despesa)
        vistos[digest] = vistos.get(digest, 0) + 1
        despesa["id"] = digest if vistos[digest] == 1 else f"{digest}-{vistos[digest]}"


def _digest_da_despesa(despesa: dict) -> str:
    pagina = (despesa.get("procedencia") or {}).get("pagina")
    base = "|".join(
        [
            str(pagina if pagina is not None else ""),
            despesa.get("descricao_normalizada") or "",
            despesa.get("valor") or "",
            despesa.get("categoria_original") or "",
        ]
    )
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def achar(leitura: dict, lancamento_id: str) -> dict | None:
    """O lançamento lido com este identificador, quando ele existir."""
    for despesa in leitura.get("despesas") or []:
        if despesa.get("id") == lancamento_id:
            return despesa
    return None


# -- Como o lançamento vale hoje ------------------------------------------------

# O que foi lido, mais a decisão humana por cima — campo a campo, sem misturar.
def _efetiva(despesa: dict) -> dict:
    correcao = despesa.get("correcao") or {}
    return {
        "id": despesa.get("id"),
        "descricao": correcao.get("descricao", despesa.get("descricao_original") or ""),
        "valor": correcao.get("valor", despesa.get("valor")),
        "categoria_eithra": correcao.get(
            "categoria_eithra", despesa.get("categoria_eithra")
        )
        or CategoriaEithra.NAO_CLASSIFICADO,
        "natureza": correcao.get("natureza", despesa.get("natureza"))
        or NaturezaDespesa.NAO_DETERMINADA,
        "grupo": correcao.get("grupo", despesa.get("categoria_original")),
        "data": despesa.get("data"),
        "fornecedor": despesa.get("fornecedor_candidato"),
        "documento": despesa.get("documento"),
        "parcela": despesa.get("parcela"),
        "origem": despesa.get("origem") or ORIGEM_EXTRAIDA,
        "ajustado": bool(correcao),
        "excluida": bool(despesa.get("excluida")),
        "manual": (despesa.get("origem") or ORIGEM_EXTRAIDA) == ORIGEM_MANUAL,
        # O outro lado da decisão: o que a máquina leu, sem tocar nele.
        "lido": {
            "descricao": despesa.get("descricao_original") or "",
            "valor": despesa.get("valor"),
            "categoria_eithra": despesa.get("categoria_eithra"),
            "natureza": despesa.get("natureza"),
            "grupo": despesa.get("categoria_original"),
        },
    }


def despesas_efetivas(
    leitura: dict, *, incluir_ignoradas: bool = False
) -> list[dict]:
    """Cada lançamento como ele vale agora: o lido com a correção por cima.

    Devolve valores crus — texto decimal e códigos de categoria e natureza —,
    porque quem formata é a borda. Por padrão, o que foi marcado como não sendo
    despesa sai da lista: continua no histórico, mas não pesa em nada.

    Os identificadores são garantidos aqui: quem mostra a lista precisa alcançar
    a linha certa para corrigi-la, e o identificador nasce sempre do conteúdo.
    """
    despesas = leitura.setdefault("despesas", [])
    identificar_despesas(despesas)

    resultado = []
    for despesa in despesas:
        efetiva = _efetiva(despesa)
        if not incluir_ignoradas and efetiva["excluida"]:
            continue
        resultado.append(efetiva)
    return resultado


def grupos_para_opcoes(leitura: dict) -> list[str]:
    """Os agrupamentos que o documento declarou, sem repetição e em ordem."""
    nomes = {grupo.get("nome") or "" for grupo in leitura.get("grupos") or []}
    nomes.discard("")
    return sorted(nomes)


def revisoes(leitura: dict) -> list[dict]:
    """O histórico de ajustes, na ordem em que aconteceram."""
    return list(leitura.get("revisoes") or [])


# -- Aplicar uma decisão --------------------------------------------------------


def aplicar_correcao(
    leitura: dict,
    *,
    lancamento_id: str,
    campos: dict,
    justificativa: str,
    usuario,
    quando=None,
) -> list[dict]:
    """Aplica uma correção a um lançamento, sem tocar no que foi lido.

    O valor lido continua onde está; o que muda é o ``correcao`` ao lado. A
    justificativa é obrigatória, e cada campo alterado fica registrado no
    histórico com o antes e o depois. Devolver um campo ao valor lido apaga a
    decisão daquele campo — o histórico, não.
    """
    despesas = leitura.setdefault("despesas", [])
    identificar_despesas(despesas)

    despesa = achar(leitura, lancamento_id)
    if despesa is None:
        raise ErroDeCorrecao("O lançamento não foi encontrado neste balanço.")

    justificativa = normalizar_justificativa(justificativa)
    antes = _efetiva(despesa)
    correcao = dict(despesa.get("correcao") or {})
    mudancas = []

    for campo, valor in campos.items():
        if valor is None:
            continue
        lido = despesa.get(CAMPO_LIDO[campo])
        efetivo = correcao.get(campo, lido)
        if _mesmo(efetivo, valor):
            continue
        mudancas.append({"campo": campo, "antes": efetivo, "depois": valor})
        if _mesmo(lido, valor):
            correcao.pop(campo, None)
        else:
            correcao[campo] = valor

    if not mudancas:
        raise ErroDeCorrecao("Nada mudou nesta correção. Ajuste um campo e salve.")

    if correcao:
        despesa["correcao"] = correcao
    else:
        despesa.pop("correcao", None)

    _registrar(
        leitura,
        tipo="correcao",
        lancamento_id=despesa["id"],
        lancamento=antes["descricao"],
        mudancas=mudancas,
        justificativa=justificativa,
        usuario=usuario,
        quando=quando,
    )
    return mudancas


def marcar_como_nao_despesa(
    leitura: dict,
    *,
    lancamento_id: str,
    justificativa: str,
    usuario,
    quando=None,
) -> None:
    """Tira um lançamento das despesas, sem apagar o que foi lido.

    O lançamento continua guardado e continua no histórico: sai dos agregados e
    da classificação, mas a decisão de quem o tirou e por quê permanece.
    """
    despesas = leitura.setdefault("despesas", [])
    identificar_despesas(despesas)

    despesa = achar(leitura, lancamento_id)
    if despesa is None:
        raise ErroDeCorrecao("O lançamento não foi encontrado neste balanço.")
    if despesa.get("excluida"):
        raise ErroDeCorrecao("Este lançamento já está marcado como não sendo despesa.")

    justificativa = normalizar_justificativa(justificativa)
    despesa["excluida"] = True

    _registrar(
        leitura,
        tipo="nao_despesa",
        lancamento_id=despesa["id"],
        lancamento=_efetiva(despesa)["descricao"],
        mudancas=[],
        justificativa=justificativa,
        usuario=usuario,
        quando=quando,
    )


def adicionar_lancamento(
    leitura: dict,
    *,
    descricao: str,
    valor: str,
    categoria_eithra: str,
    natureza: str,
    grupo: str,
    justificativa: str,
    usuario,
    quando=None,
) -> str:
    """Acrescenta um lançamento que o documento não trouxe.

    Ele nasce marcado como manual e sem procedência: o Eithra não finge que leu
    o que não leu. A identidade é própria, para que a correção seguinte alcance
    exatamente esta linha.
    """
    despesas = leitura.setdefault("despesas", [])
    identificar_despesas(despesas)

    descricao = " ".join((descricao or "").split())
    if not descricao:
        raise ErroDeCorrecao("Informe a descrição do lançamento.")
    justificativa = normalizar_justificativa(justificativa)

    novo = {
        "id": f"{PREFIXO_MANUAL}{uuid.uuid4().hex[:12]}",
        "origem": ORIGEM_MANUAL,
        "descricao_original": descricao,
        "descricao_normalizada": chave_de_comparacao(descricao),
        "valor": valor,
        "categoria_eithra": categoria_eithra,
        "natureza": natureza,
        "categoria_original": grupo or "",
    }
    despesas.append(novo)

    _registrar(
        leitura,
        tipo="inclusao",
        lancamento_id=novo["id"],
        lancamento=descricao,
        mudancas=[
            {"campo": "valor", "antes": None, "depois": valor},
            {"campo": "categoria_eithra", "antes": None, "depois": categoria_eithra},
            {"campo": "natureza", "antes": None, "depois": natureza},
            {"campo": "grupo", "antes": None, "depois": grupo or ""},
        ],
        justificativa=justificativa,
        usuario=usuario,
        quando=quando,
    )
    return novo["id"]


def _registrar(
    leitura: dict,
    *,
    tipo: str,
    lancamento_id: str,
    lancamento: str,
    mudancas: list[dict],
    justificativa: str,
    usuario,
    quando=None,
) -> None:
    """Acrescenta uma revisão ao histórico. O histórico só cresce."""
    leitura.setdefault("revisoes", []).append(
        {
            "quando": (quando or timezone.now()).isoformat(),
            "tipo": tipo,
            "lancamento_id": lancamento_id,
            "lancamento": lancamento,
            "mudancas": mudancas,
            "justificativa": justificativa,
            "autor": _nome_de(usuario),
            "autor_id": str(getattr(usuario, "pk", "") or ""),
        }
    )


def normalizar_justificativa(texto: str) -> str:
    """Deixa a justificativa limpa e recusa o vazio — correção sem porquê não entra."""
    justificativa = " ".join((texto or "").split())
    if not justificativa:
        raise ErroDeCorrecao("A correção precisa de uma justificativa.")
    if len(justificativa) > LIMITE_DA_JUSTIFICATIVA:
        raise ErroDeCorrecao(
            "A justificativa é longa demais. Resuma em poucas palavras."
        )
    return justificativa


# -- Recalcular o detalhamento --------------------------------------------------


def recalcular_agregados(leitura: dict, *, inconsistencias=()) -> None:
    """Recalcula o detalhamento a partir dos lançamentos válidos.

    O total oficial do documento não entra aqui: o que se recalcula é a soma
    dos lançamentos, as categorias, os agrupamentos e o resumo. A conferência de
    cada agrupamento continua sendo contra o subtotal que o documento declarou —
    nenhum subtotal é inventado para fazer um grupo fechar.
    """
    despesas = [
        _despesa_efetiva(item)
        for item in leitura.get("despesas") or []
        if not item.get("excluida")
    ]
    declarados = {
        grupo.get("nome") or "": grupo["total_declarado"]
        for grupo in leitura.get("grupos") or []
        if grupo.get("total_declarado")
    }

    categorias = _agregar_categorias(despesas)
    grupos = _agregar_grupos(despesas, declarados)
    resumo = _resumo(despesas, grupos, list(inconsistencias))

    serializado = para_dicionario(
        BalancoExtraidoV1(categorias=categorias, grupos=grupos, resumo=resumo)
    )
    leitura["categorias"] = serializado["categorias"]
    leitura["grupos"] = serializado["grupos"]
    leitura["resumo"] = serializado["resumo"]
    leitura["soma_despesas_extraida"] = decimal_para_texto(_soma(despesas))


def resumo_do_modelo(leitura: dict, validado, anterior: dict | None) -> dict:
    """O resumo curto do balanço, atualizado sem tocar nos totais oficiais.

    A listagem se apoia neste resumo; recalculá-lo é o que impede que a lista
    mostre um detalhamento antigo depois de uma correção. Receitas e despesas
    oficiais continuam exatamente como o documento as declarou.
    """
    resumo = dict(anterior or {})
    resumo.update(
        {
            "total_detalhado_identificado": validado.total_detalhado_identificado,
            "valor_nao_associado": validado.valor_nao_associado,
            "percentual_detalhamento": validado.percentual_detalhamento,
            "recorrente_total": leitura.get("resumo", {}).get("recorrente_total"),
            "extraordinaria_total": leitura.get("resumo", {}).get(
                "extraordinaria_total"
            ),
            "nao_determinada_total": leitura.get("resumo", {}).get(
                "nao_determinada_total"
            ),
            "despesas_identificadas": leitura.get("resumo", {}).get(
                "despesas_identificadas"
            ),
            "grupos_conferidos": leitura.get("resumo", {}).get("grupos_conferidos"),
            "grupos_com_diferenca": leitura.get("resumo", {}).get(
                "grupos_com_diferenca"
            ),
        }
    )
    return resumo


def _despesa_efetiva(item: dict) -> DespesaExtraida:
    """O lançamento efetivo como o contrato da leitura o entende.

    Reaproveitar o contrato e os agregados do extrator é o que garante que a
    releitura de um mês corrigido use exatamente as mesmas regras da leitura
    original — sem duplicar a regra de agregar, e sem alterar o extrator.
    """
    efetiva = _efetiva(item)
    return DespesaExtraida(
        descricao_original=efetiva["descricao"],
        descricao_normalizada=item.get("descricao_normalizada") or "",
        valor=efetiva["valor"] or "0.00",
        data=item.get("data"),
        fornecedor_candidato=item.get("fornecedor_candidato"),
        documento=item.get("documento"),
        parcela=item.get("parcela"),
        categoria_original=efetiva["grupo"] or None,
        categoria_eithra=efetiva["categoria_eithra"],
        natureza=efetiva["natureza"],
        confianca=item.get("confianca") or "media",
    )


def _soma(despesas) -> Decimal:
    """Soma decimal dos valores válidos, sem estimar o que não converte."""
    total = Decimal("0.00")
    for despesa in despesas:
        total += texto_para_decimal(despesa.valor) or Decimal("0.00")
    return total


# -- Apoio ----------------------------------------------------------------------


def _mesmo(a, b) -> bool:
    """Compara dois valores sem deixar ``None`` e vazio parecerem diferentes."""
    return ("" if a is None else str(a)) == ("" if b is None else str(b))


def _nome_de(usuario) -> str:
    """O nome humano de quem decidiu; nunca o identificador interno."""
    if usuario is None:
        return ""
    return getattr(usuario, "full_name", "") or getattr(usuario, "email", "") or ""
