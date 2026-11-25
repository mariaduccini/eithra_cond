"""Vencimentos reunidos: contratos, garantias e documentos em uma só leitura.

Contrato, garantia e documento são obrigações com prazo, e cada um tem a sua
lista. A pergunta que a síndica faz antes de perder um prazo legal, porém, não
distingue a origem: *o que vence agora?* Esta leitura junta as três fontes ativas
do condomínio e as classifica por urgência, sem guardar nada — assim como a
situação de cada registro, a faixa é lida do vencimento na hora.

A leitura não decide autorização: ela recebe o condomínio já resolvido. Quem
pode ver a governança enxerga os vencimentos; a decisão continua na view.
"""
from datetime import date

from django.urls import reverse

from .models import ContratoFornecedor, DocumentoOperacional, Garantia

# As faixas de urgência, na ordem em que a tela as apresenta. O nome é o do
# cabeçalho e o tom é o da cor de apoio (o vocabulário de tom já usado nos selos).
FAIXAS = (
    ("vencido", "Vencido", "danger"),
    ("ate_30", "Até 30 dias", "warning"),
    ("ate_90", "Até 90 dias", "info"),
    ("em_dia", "Em dia", "success"),
)

# Até quantos dias um vencimento ainda conta como próximo.
DIAS_ATE_30 = 30
DIAS_ATE_90 = 90

# O tom de cada faixa, para a linha usar a mesma cor do cabeçalho.
TOM_POR_FAIXA = {chave: tom for chave, _, tom in FAIXAS}


def coletar_vencimentos(condominio, hoje=None):
    """Tudo o que vence neste condomínio, do mais urgente ao mais distante.

    Traz as três fontes ativas — contrato, garantia e documento — que tenham
    data de término. Registro sem data não vence e não entra: prazo
    indeterminado é uma resposta, não um dado faltando.
    """
    referencia = hoje or date.today()
    itens = [
        *_vencimentos_de_contratos(condominio, referencia),
        *_vencimentos_de_garantias(condominio, referencia),
        *_vencimentos_de_documentos(condominio, referencia),
    ]
    # A data mais antiga primeiro: o que está vencido há mais tempo abre a lista.
    itens.sort(key=lambda item: (item["data"], item["tipo"], item["titulo"]))
    return itens


def agrupar_por_faixa(itens):
    """As mesmas linhas, separadas nas faixas de urgência, na ordem fixa."""
    grupos = {chave: [] for chave, _, _ in FAIXAS}
    for item in itens:
        grupos[item["faixa"]].append(item)
    return [
        {
            "chave": chave,
            "titulo": titulo,
            "tom": tom,
            "itens": grupos[chave],
            "resumo": _contagem(len(grupos[chave])),
        }
        for chave, titulo, tom in FAIXAS
    ]


def vencimentos_proximos(condominio, hoje=None):
    """Os vencidos e o que vence em até 30 dias, do mais urgente ao mais distante.

    É o recorte que o painel mostra; a tela completa acrescenta o resto.
    """
    return [
        item
        for item in coletar_vencimentos(condominio, hoje)
        if item["faixa"] in ("vencido", "ate_30")
    ]


def _vencimentos_de_contratos(condominio, referencia):
    """Os contratos ativos com fim marcado, na leitura de vencimento."""
    contratos = (
        ContratoFornecedor.objects.do_condominio(condominio)
        .ativos()
        .filter(data_fim__isnull=False)
        .select_related("fornecedor")
    )
    for contrato in contratos:
        yield _item(
            tipo="Contrato",
            titulo=contrato.objeto,
            origem=contrato.fornecedor.nome_razao_social,
            data=contrato.data_fim,
            referencia=referencia,
            destino=_destino_do_registro(
                "governanca:contratos", contrato.pk, condominio
            ),
        )


def _vencimentos_de_garantias(condominio, referencia):
    """As garantias ativas com fim marcado, na leitura de vencimento."""
    garantias = (
        Garantia.objects.do_condominio(condominio)
        .ativos()
        .filter(data_fim__isnull=False)
        .select_related("fornecedor")
    )
    for garantia in garantias:
        yield _item(
            tipo="Garantia",
            titulo=garantia.descricao,
            origem=(
                garantia.fornecedor.nome_razao_social
                if garantia.fornecedor_id
                else "Sem fornecedor definido"
            ),
            data=garantia.data_fim,
            referencia=referencia,
            destino=_destino_do_registro(
                "governanca:garantias", garantia.pk, condominio
            ),
        )


def _vencimentos_de_documentos(condominio, referencia):
    """Os documentos ativos com validade marcada, na leitura de vencimento."""
    documentos = (
        DocumentoOperacional.objects.do_condominio(condominio)
        .ativos()
        .filter(data_validade__isnull=False)
    )
    for documento in documentos:
        yield _item(
            tipo="Documento",
            titulo=documento.titulo,
            origem=documento.emissor or documento.tipo_documento.nome,
            data=documento.data_validade,
            referencia=referencia,
            destino=_destino_do_registro(
                "governanca:documentos", documento.pk, condominio
            ),
        )


def _item(*, tipo, titulo, origem, data, referencia, destino):
    """Uma linha de vencimento, já lida para a tela."""
    dias = (data - referencia).days
    faixa = _faixa(dias)
    return {
        "tipo": tipo,
        "titulo": titulo,
        "origem": origem,
        "data": data,
        "data_texto": data.strftime("%d/%m/%Y"),
        "dias": dias,
        "quando": _quando(dias),
        "faixa": faixa,
        "tom": TOM_POR_FAIXA[faixa],
        "destino": destino,
    }


def _contagem(quantidade: int) -> str:
    """A quantidade de linhas de uma faixa, em linguagem humana."""
    return f"{quantidade} {'item' if quantidade == 1 else 'itens'}"


def _quando(dias) -> str:
    """O prazo em linguagem humana: "vence em 12 dias", "venceu há 3 dias"."""
    if dias == 0:
        return "vence hoje"
    if dias == 1:
        return "vence amanhã"
    if dias > 1:
        return f"vence em {dias} dias"
    if dias == -1:
        return "venceu ontem"
    return f"venceu há {abs(dias)} dias"


def _faixa(dias) -> str:
    """Em que faixa de urgência o vencimento cai, a partir dos dias restantes."""
    if dias < 0:
        return "vencido"
    if dias <= DIAS_ATE_30:
        return "ate_30"
    if dias <= DIAS_ATE_90:
        return "ate_90"
    return "em_dia"


def _destino_do_registro(view_name, pk, condominio) -> str:
    """O endereço da tela do registro, preservando o condomínio em vista."""
    return f"{reverse(view_name)}?condominio={condominio.pk}&abrir=editar&item={pk}"
