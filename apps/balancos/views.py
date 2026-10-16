"""Telas de entrada do balanço.

A tela existe para uma coisa só: receber o arquivo que a administração enviou e
mostrar o que já chegou. Nenhum valor é interpretado aqui, nenhum mês passa a
valer. O que a gestão vê enquanto isso é o estado do recebimento.

A autorização é decidida no backend, sempre a partir do vínculo ativo da pessoa
com o condomínio. Quem não pode importar não é avisado de que o registro
existe: a resposta é 404.
"""
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.balancos.categorias import rotulo as rotulo_da_categoria
from apps.balancos.constants import LayoutBalanco, StatusImportacao
from apps.balancos.contratos import TipoInconsistencia, de_dicionario
from apps.balancos.forms import ImportacaoBalancoForm
from apps.balancos.models import ImportacaoBalanco
from apps.balancos.natureza_despesa import NaturezaDespesa
from apps.balancos.natureza_despesa import rotulo as rotulo_da_natureza
from apps.balancos.normalizadores import texto_para_decimal
from apps.balancos.permissoes import condominios_para_importar
from apps.balancos.services import (
    descartar_importacao,
    formatar_tamanho,
    gerar_acesso_temporario,
    limite_de_upload,
    receber_importacao,
)

# Quantidade de recebimentos recentes mostrados. A lista é de apoio: o que
# importa é o que acabou de chegar, não um arquivo morto.
LIMITE_DA_LISTA = 50


@login_required
def importacoes(request):
    """Arquivos de balanço recebidos, nos condomínios em que a pessoa pode importar."""
    condominios = _condominios_autorizados(request)

    return _render(request, condominios=condominios)


@login_required
def importar(request):
    """Recebe o PDF e registra a tentativa de importação.

    Nada de ``BalancoMensal`` nasce aqui: o balanço só existe depois que a
    gestão confirma a importação, em etapa futura.
    """
    if request.method != "POST":
        return redirect("balancos:importacoes")

    condominios = _condominios_autorizados(request)
    formulario = ImportacaoBalancoForm(
        request.POST, request.FILES, condominios=condominios
    )

    if not formulario.is_valid():
        return _render(request, condominios=condominios, formulario=formulario, status=400)

    resultado = receber_importacao(
        condominio=formulario.cleaned_data["condominio"],
        usuario=request.user,
        arquivo=formulario.cleaned_data["arquivo"],
    )

    if resultado.duplicada:
        messages.warning(
            request,
            "Este mesmo arquivo já havia sido recebido para este condomínio. "
            "Nada foi guardado de novo.",
        )
    elif resultado.importacao.status == StatusImportacao.ERRO:
        messages.error(
            request,
            "Não foi possível processar o arquivo automaticamente. A tentativa "
            "ficou registrada; tente enviar de novo.",
        )
    else:
        messages.success(request, "Arquivo recebido.")

    return redirect("balancos:importacoes")


@login_required
def detalhe(request, pk):
    """O que o Eithra entendeu do balanço, para a gestão conferir.

    A tela não oficializa nada: mostra os dados lidos e os pontos que merecem
    atenção. Confirmar o balanço é etapa futura, então aqui não há botão de
    confirmação — só o que foi lido e o que ainda falta esclarecer.
    """
    importacao = _importacao_autorizada(request, pk)
    return render(
        request,
        "pages/balancos/importacao_detalhe.html",
        _contexto_do_detalhe(importacao),
    )


@login_required
def acessar_arquivo(request, pk):
    """Abre o arquivo por um acesso curto, gerado na hora.

    O endereço do objeto nunca aparece como link fixo: cada visita gera um
    acesso temporário, e só depois de confirmado o vínculo com o condomínio.
    """
    importacao = _importacao_autorizada(request, pk)
    return redirect(gerar_acesso_temporario(importacao))


@login_required
@require_POST
def descartar(request, pk):
    """Recusa a importação, guardando a memória do descarte."""
    importacao = _importacao_autorizada(request, pk)
    descartar_importacao(importacao)
    messages.success(request, "Importação descartada.")
    return redirect("balancos:importacoes")


def _condominios_autorizados(request):
    """Condomínios em que esta pessoa pode importar, ou 404.

    404 e não 403: quem não pode importar também não deve descobrir, pela
    resposta, que a tela existe para outros.
    """
    condominios = condominios_para_importar(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomínio disponível para importar balanço.")
    return condominios


def _importacao_autorizada(request, pk):
    """Resolve a importação dentro dos condomínios autorizados, ou 404."""
    condominios = _condominios_autorizados(request)
    return get_object_or_404(
        ImportacaoBalanco.objects.select_related("condominio").filter(
            condominio__in=condominios
        ),
        pk=pk,
    )


def _render(request, *, condominios, formulario=None, status=200):
    """Monta a tela com o que já foi recebido e o formulário de entrada."""
    recebidas = (
        ImportacaoBalanco.objects.filter(condominio__in=condominios)
        .select_related("condominio")
        .order_by("-criado_em")[:LIMITE_DA_LISTA]
    )
    contexto = {
        "importacoes": recebidas,
        "formulario_importacao": formulario or ImportacaoBalancoForm(condominios=condominios),
        "limite_de_upload": formatar_tamanho(limite_de_upload()),
    }
    return render(request, "pages/balancos/importacoes.html", contexto, status=status)


# -- Apresentação do que foi lido ----------------------------------------------
#
# A tela fala a língua da gestão, não a do contrato: valores em reais, datas em
# dia/mês, categorias e naturezas com nome humano. Nada de código técnico, de
# score ou de detalhe de sistema aparece aqui — o contrato continua sendo o
# contrato, e esta tradução acontece só na borda.

TOM_POR_NATUREZA = {
    NaturezaDespesa.RECORRENTE_FIXA: "info",
    NaturezaDespesa.RECORRENTE_VARIAVEL: "info",
    NaturezaDespesa.EXTRAORDINARIA: "warning",
    NaturezaDespesa.NAO_DETERMINADA: "secondary",
}

# Tom do ponto de atenção pelo quanto pesa. O texto diz o que aconteceu; a cor
# só ajuda a separar o que pesa mais do que pesa menos.
TOM_POR_SEVERIDADE = {
    "alta": "danger",
    "media": "warning",
    "baixa": "secondary",
}

NOMES_DO_MES = (
    "",
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


def _contexto_do_detalhe(importacao):
    """Traduz o que está gravado num contexto que a tela mostra sem jargão."""
    balanco = de_dicionario(importacao.resultado_extraido)
    lido = bool(importacao.resultado_extraido)

    return {
        "importacao": importacao,
        "lido": lido,
        "competencia": _competencia_por_extenso(balanco.competencia) if lido else None,
        "receitas": [_receita_para_tela(item) for item in balanco.receitas],
        "despesas": [_despesa_para_tela(item) for item in balanco.despesas],
        "saldos": [_saldo_para_tela(item) for item in balanco.saldos],
        "categorias": [_categoria_para_tela(item) for item in balanco.categorias],
        "tem_a_confirmar": any(
            texto_para_decimal(item.nao_determinada) for item in balanco.categorias
        ),
        "resumo": _resumo_para_tela(balanco.resumo),
        "totais": {
            "receitas": _moeda(balanco.receitas_total) if lido else None,
            "despesas": _moeda(balanco.despesas_total) if lido else None,
            "saldo_anterior": _moeda(balanco.saldo_anterior) if lido else None,
            "saldo_final": _moeda(balanco.saldo_final) if lido else None,
        },
        "pendencias": [_pendencia_para_tela(item) for item in (importacao.inconsistencias or [])],
        "motivo_recusa": None if lido else _motivo_para_tela(importacao),
        "motivo_tom": "danger" if importacao.status == StatusImportacao.ERRO else "info",
    }


def _moeda(valor: str | None) -> str | None:
    """Escreve o valor em reais como a gestão lê: ``R$ 1.234,56``."""
    decimal = texto_para_decimal(valor) if valor else None
    if decimal is None:
        return None
    formatado = f"{decimal:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatado}"


def _competencia_por_extenso(iso: str | None) -> str | None:
    """Mês e ano por extenso: ``2026-08-01`` vira ``Agosto/2026``."""
    referencia = _data_iso(iso)
    if referencia is None:
        return None
    return f"{NOMES_DO_MES[referencia.month].capitalize()}/{referencia.year}"


def _dia(iso: str | None) -> str:
    """Dia e mês, como a gestão lê um lançamento: ``10/08``."""
    referencia = _data_iso(iso)
    return referencia.strftime("%d/%m") if referencia else ""


def _data_iso(iso: str | None) -> date | None:
    if not iso:
        return None
    try:
        return date.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def _receita_para_tela(item) -> dict:
    return {
        "data": _dia(item.data),
        "descricao": item.descricao_original,
        "origem": item.categoria_original or "",
        "valor": _moeda(item.valor),
    }


def _despesa_para_tela(item) -> dict:
    return {
        "data": _dia(item.data),
        "descricao": item.descricao_original,
        "categoria": rotulo_da_categoria(item.categoria_eithra),
        "categoria_original": item.categoria_original or "",
        "natureza": rotulo_da_natureza(item.natureza),
        "natureza_tom": TOM_POR_NATUREZA.get(item.natureza, "secondary"),
        "valor": _moeda(item.valor),
        "fornecedor": item.fornecedor_candidato or "",
        "documento": item.documento or "",
        "parcela": item.parcela or "",
    }


def _saldo_para_tela(item) -> dict:
    return {"identificacao": item.identificacao, "valor": _moeda(item.valor)}


def _categoria_para_tela(item) -> dict:
    return {
        "nome": rotulo_da_categoria(item.categoria),
        "recorrente": _moeda(item.recorrente),
        "extraordinaria": _moeda(item.extraordinaria),
        "a_confirmar": _moeda(item.nao_determinada),
        "total": _moeda(item.total),
        "quantidade": item.quantidade,
    }


def _resumo_para_tela(resumo) -> dict:
    return {
        "recorrente": _moeda(resumo.recorrente_total),
        "extraordinaria": _moeda(resumo.extraordinaria_total),
        "a_confirmar": _moeda(resumo.nao_determinada_total),
        "despesas": resumo.despesas_identificadas,
        "receitas": resumo.receitas_identificadas,
        "pontos": resumo.pontos_de_atencao,
        "principais": [rotulo_da_categoria(item) for item in resumo.principais_categorias],
    }


def _pendencia_para_tela(pendencia: dict) -> dict:
    """Traduz um ponto de atenção gravado para o que a tela mostra.

    O que foi lido vira frase para a gestão: o título nomeia o tipo de ponto e a
    mensagem já vem escrita em linguagem humana. Nem o código interno nem a
    página do documento aparecem aqui.
    """
    return {
        "titulo": _rotulo_da_inconsistencia(pendencia.get("tipo", "")),
        "mensagem": pendencia.get("mensagem", ""),
        "tom": TOM_POR_SEVERIDADE.get(pendencia.get("severidade"), "secondary"),
    }


def _rotulo_da_inconsistencia(tipo: str) -> str:
    """Nome humano do ponto de atenção; um rótulo neutro para o que não se conhece."""
    try:
        return TipoInconsistencia(tipo).label
    except ValueError:
        return "Ponto de atenção"


def _motivo_para_tela(importacao) -> str:
    """Por que este documento ainda não foi lido, em linguagem para a gestão."""
    if importacao.status == StatusImportacao.ERRO:
        return importacao.erro_processamento or (
            "Não foi possível ler este documento. Tente enviar de novo."
        )
    if importacao.layout_detectado == LayoutBalanco.LEGADO:
        return "Este formato será processado pela leitura de documentos legados."
    return "Ainda não foi possível reconhecer o formato deste documento."
