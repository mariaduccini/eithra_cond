"""Telas de entrada do balanço.

A tela existe para uma coisa só: receber o arquivo que a administração enviou e
mostrar o que já chegou. Nenhum valor é interpretado aqui, nenhum mês passa a
valer enquanto a gestão não confirma. O que a gestão vê antes disso é o estado
do recebimento e o que o Eithra entendeu do documento.

Depois da confirmação, o mês passa a existir como balanço, e a tela passa a
mostrá-lo como gestão — não mais como pipeline.

A autorização é decidida no backend, sempre a partir do vínculo ativo da pessoa
com o condomínio. Quem não pode importar não é avisado de que o registro
existe: a resposta é 404.
"""
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.balancos.categorias import rotulo as rotulo_da_categoria
from apps.balancos.constants import LayoutBalanco, StatusBalanco, StatusImportacao
from apps.balancos.contratos import (
    StatusConferencia,
    TipoInconsistencia,
    de_dicionario,
    de_dicionario_validado,
)
from apps.balancos.forms import ImportacaoBalancoForm
from apps.balancos.models import BalancoMensal, ImportacaoBalanco
from apps.balancos.natureza_despesa import NaturezaDespesa
from apps.balancos.natureza_despesa import rotulo as rotulo_da_natureza
from apps.balancos.normalizadores import decimal_para_texto, texto_para_decimal
from apps.balancos.permissoes import condominios_para_importar
from apps.balancos.services import (
    ErroDeConfirmacao,
    confirmar_importacao,
    descartar_importacao,
    formatar_tamanho,
    gerar_acesso_temporario,
    limite_de_upload,
    medir_detalhamento,
    pode_confirmar,
    receber_importacao,
)
from apps.balancos.validacao_financeira import TOLERANCIA

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
    gestão confirma a importação.
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

    A tela ainda não oficializa nada: mostra os dados lidos e os pontos que
    merecem atenção. Confirmar é uma decisão da gestão, e por isso o botão só
    aparece quando o domínio concorda que esta importação pode virar balanço.
    """
    importacao = _importacao_autorizada(request, pk)
    return render(
        request,
        "pages/balancos/importacao_detalhe.html",
        _contexto_do_detalhe(importacao),
    )


@login_required
@require_POST
def confirmar(request, pk):
    """Confirma a importação: é aqui que o mês passa a valer.

    A decisão é da gestão, mas quem confere continua sendo o domínio: a
    confirmação acontece em uma transação só, e uma falha em qualquer passo não
    deixa balanço pela metade. Confirmar de novo não cria um segundo balanço.
    """
    importacao = _importacao_autorizada(request, pk)
    try:
        balanco = confirmar_importacao(importacao, usuario=request.user)
    except ErroDeConfirmacao as erro:
        messages.error(request, str(erro))
        return redirect("balancos:detalhe", pk=importacao.pk)

    messages.success(request, "Balanço confirmado.")
    return redirect("balancos:balanco", pk=balanco.pk)


@login_required
def balanco(request, pk):
    """O balanço confirmado do mês, como gestão — não como pipeline."""
    instancia = _balanco_autorizado(request, pk)
    return render(
        request,
        "pages/balancos/balanco_detalhe.html",
        _contexto_do_balanco(instancia),
    )


@login_required
def arquivo_do_balanco(request, pk):
    """Abre o PDF original do balanço confirmado, com acesso curto."""
    instancia = _balanco_autorizado(request, pk)
    original = _documento_original(instancia)
    if original is None or not original.storage_key:
        raise Http404("O documento original deste balanço não está disponível.")
    return redirect(gerar_acesso_temporario(original))


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


def _balanco_autorizado(request, pk):
    """Resolve o balanço dentro dos condomínios autorizados, ou 404."""
    condominios = _condominios_autorizados(request)
    return get_object_or_404(
        BalancoMensal.objects.select_related("condominio").filter(
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
    confirmados = (
        BalancoMensal.objects.filter(condominio__in=condominios)
        .select_related("condominio")
        .exclude(status=StatusBalanco.SUBSTITUIDO)
        .order_by("-competencia", "-versao")[:LIMITE_DA_LISTA]
    )
    _ligar_balancos_confirmados(recebidas)
    contexto = {
        "balancos_confirmados": [_balanco_da_lista(item) for item in confirmados],
        "importacoes": recebidas,
        "formulario_importacao": formulario or ImportacaoBalancoForm(condominios=condominios),
        "limite_de_upload": formatar_tamanho(limite_de_upload()),
    }
    return render(request, "pages/balancos/importacoes.html", contexto, status=status)


def _ligar_balancos_confirmados(importacoes) -> None:
    """Aponta, em cada importação confirmada, o balanço que nasceu dela.

    O vínculo é a competência: é ela que diz de qual mês o balanço é. A
    importação continua guardada como memória técnica do recebimento; o balanço
    é o mês em vigor. A busca é feita de uma vez só, para a lista não virar uma
    consulta por linha.
    """
    competencia_por_importacao = {}
    for importacao in importacoes:
        importacao.balanco_confirmado = None
        if not importacao.confirmado_em:
            continue
        competencia = _data_iso((importacao.resultado_extraido or {}).get("competencia"))
        if competencia is not None:
            competencia_por_importacao[importacao.pk] = (
                importacao.condominio_id,
                competencia,
            )

    if not competencia_por_importacao:
        return

    encontrados = {
        (item.condominio_id, item.competencia): item
        for item in BalancoMensal.objects.filter(
            condominio_id__in={par[0] for par in competencia_por_importacao.values()},
            competencia__in={par[1] for par in competencia_por_importacao.values()},
        ).exclude(status=StatusBalanco.SUBSTITUIDO)
    }
    for importacao in importacoes:
        encontrado = encontrados.get(competencia_por_importacao.get(importacao.pk))
        if encontrado is not None:
            importacao.balanco_confirmado = encontrado


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

# Tom da conferência do agrupamento. O texto já diz o que aconteceu; a cor só
# separa o que fecha do que ainda precisa de esclarecimento.
TOM_POR_CONFERENCIA = {
    StatusConferencia.CONFERE: "success",
    StatusConferencia.FALTA_IDENTIFICAR: "warning",
    StatusConferencia.SEM_SUBTOTAL: "secondary",
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
    """Traduz o que está gravado num contexto que a tela mostra sem jargão.

    As despesas aparecem pelo agrupamento do próprio documento — o grupo diz o
    que o documento declarou, o que a leitura encontrou e a diferença entre os
    dois. Recebimento individual não entra na tela: o total de receitas vem do
    resumo do documento, e reproduzir centenas de linhas por unidade não serve
    à gestão.
    """
    balanco = de_dicionario(importacao.resultado_extraido)
    lido = bool(importacao.resultado_extraido)
    lancamentos = _lancamentos_por_grupo(balanco.despesas)
    # Quem decide se esta importação pode virar balanço é o domínio; a tela só
    # mostra o botão quando ele concorda — e nunca oferece o que o backend
    # recusaria.
    confirma, motivo = pode_confirmar(importacao)

    return {
        "importacao": importacao,
        "lido": lido,
        "competencia": _competencia_por_extenso(balanco.competencia) if lido else None,
        "grupos": [
            _grupo_para_tela(item, lancamentos.get(item.nome or "", []))
            for item in balanco.grupos
        ],
        "saldos": [_saldo_para_tela(item) for item in balanco.saldos],
        "tem_a_confirmar": any(
            texto_para_decimal(item.nao_determinada) for item in balanco.grupos
        ),
        "resumo": _resumo_para_tela(balanco.resumo),
        "totais": {
            "receitas": _moeda(balanco.receitas_total) if lido else None,
            "despesas": _moeda(balanco.despesas_total) if lido else None,
            "residuo": (
                _residuo_do_total(balanco.despesas_total, balanco.soma_despesas_extraida)
                if lido
                else None
            ),
            "saldo_anterior": _moeda(balanco.saldo_anterior) if lido else None,
            "saldo_final": _moeda(balanco.saldo_final) if lido else None,
        },
        "detalhamento": _detalhamento_para_tela(balanco) if lido else None,
        "pode_confirmar": confirma,
        "motivo_para_nao_confirmar": motivo,
        "pendencias": [_pendencia_para_tela(item) for item in (importacao.inconsistencias or [])],
        "motivo_recusa": None if lido else _motivo_para_tela(importacao),
        "motivo_tom": "danger" if importacao.status == StatusImportacao.ERRO else "info",
    }


def _contexto_do_balanco(balanco) -> dict:
    """Traduz o balanço confirmado para a tela de gestão.

    O documento já virou mês: aqui não há pipeline, não há etapa, não há
    detalhe técnico. O que aparece é o que a administração declarou, o que a
    leitura detalhou e o quanto ficou sem detalhe associado — o resto continua
    guardado, mas não é assunto desta tela.
    """
    validado = de_dicionario_validado(balanco.dados_validados)
    lido = de_dicionario(validado.leitura)
    categorias = _categorias_para_tela(lido)
    grupos_em_atencao = _grupos_em_atencao_para_tela(lido.grupos)
    saldos = [_saldo_para_tela(item) for item in lido.saldos]
    detalhamento = _detalhamento_para_tela(lido)
    resumo = _resumo_para_tela(lido.resumo)
    original = _documento_original(balanco)
    saldo_rotulo, saldo_valor = _saldo_principal_para_tela(
        validado.saldo_final, saldos=saldos
    )

    return {
        "balanco": balanco,
        "competencia": _competencia_por_extenso(validado.competencia),
        "subtitulo": f"{balanco.condominio.nome} / {_competencia_por_extenso(validado.competencia)}",
        "categorias": categorias,
        "saldos": saldos,
        "grupos_em_atencao": grupos_em_atencao,
        "resumo": resumo,
        "totais": {
            "receitas": _moeda(validado.receitas_total),
            "despesas": _moeda(validado.despesas_total),
            "saldo_anterior": _moeda(validado.saldo_anterior),
            "saldo_final": saldo_valor,
            "saldo_rotulo": saldo_rotulo,
        },
        "detalhamento": detalhamento,
        "texto_detalhamento": _texto_do_detalhamento(detalhamento),
        "destaques": _destaques_para_tela(
            categorias=categorias,
            resumo=resumo,
            detalhamento=detalhamento,
        ),
        "texto_grupos_em_atencao": _texto_grupos_em_atencao(grupos_em_atencao),
        "nome_do_arquivo": original.nome_original if original else "",
        "tem_documento_original": bool(original),
        "pendencias": [
            _pendencia_para_tela(item) for item in (balanco.inconsistencias or [])
        ],
        "confirmado_em": balanco.validado_em,
    }


def _documento_original(balanco):
    """O PDF original ativo do balanço, quando existir."""
    return (
        balanco.documentos.filter(ativo=True).order_by("-enviado_em").first()
        or balanco.documentos.order_by("-enviado_em").first()
    )


def _balanco_da_lista(balanco) -> dict:
    """Resumo curto do balanço confirmado para a tela principal."""
    resumo = balanco.resumo or {}
    return {
        "pk": balanco.pk,
        "condominio": balanco.condominio.nome,
        "competencia": _competencia_por_extenso(balanco.competencia.isoformat()),
        "receitas": _moeda(resumo.get("receitas_total")),
        "despesas": _moeda(resumo.get("despesas_total")),
        "status": "Confirmado",
    }


def _detalhamento_para_tela(balanco) -> dict:
    """O quanto do total declarado ficou preso ao detalhe identificado.

    A distância entre o declarado e o identificado é dita, não escondida: é ela
    que mostra que o balanço vale pelos totais oficiais mesmo quando nem todo
    lançamento pôde ser associado.
    """
    medida = medir_detalhamento(balanco)
    nao_associado = texto_para_decimal(medida.valor_nao_associado) or Decimal("0.00")

    return {
        "total_declarado": _moeda(medida.total_declarado),
        "total_identificado": _moeda(medida.total_identificado),
        "valor_nao_associado": _moeda(medida.valor_nao_associado),
        "percentual": _percentual(medida.percentual),
        "tem_residual": abs(nao_associado) > TOLERANCIA,
        "mais_do_que_o_declarado": nao_associado < 0,
    }


def _percentual(valor: str | None) -> str | None:
    """Percentual com uma casa, como a gestão lê: ``91,2%``."""
    decimal = texto_para_decimal(valor) if valor else None
    if decimal is None:
        return None
    arredondado = decimal.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{arredondado:.1f}".replace(".", ",") + "%"


def _residuo_do_total(despesas_total: str | None, soma_extraida: str) -> dict | None:
    """A distância entre o total declarado e a soma do que foi lido.

    O total do documento e a soma do que foi lido são conceitos distintos: um é
    o que a administração afirma, o outro é o que a leitura encontrou. Quando a
    leitura encontra menos, falta identificar; quando encontra mais, o valor
    veio acima do declarado — e dizer o contrário seria afirmar o inverso do
    que os números mostram. Resíduo de arredondamento não vira frase.
    """
    total = texto_para_decimal(despesas_total)
    if total is None:
        return None
    diferenca = total - (texto_para_decimal(soma_extraida) or Decimal("0.00"))
    if abs(diferenca) <= TOLERANCIA:
        return None
    if diferenca > 0:
        return {
            "rotulo": "Não identificado",
            "valor": _moeda(decimal_para_texto(diferenca)),
        }
    return {
        "rotulo": "Acima do declarado",
        "valor": _moeda(decimal_para_texto(-diferenca)),
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


def _lancamentos_por_grupo(despesas) -> dict[str, list[dict]]:
    """Lançamentos de cada agrupamento, na ordem em que o documento os traz."""
    por_grupo: dict[str, list[dict]] = {}
    for despesa in despesas:
        por_grupo.setdefault(despesa.categoria_original or "", []).append(
            _despesa_para_tela(despesa)
        )
    return por_grupo


def _lancamentos_por_categoria(despesas) -> dict[str, list[dict]]:
    """Lançamentos por categoria do Eithra, na ordem em que vieram do documento."""
    por_categoria: dict[str, list[dict]] = {}
    for despesa in despesas:
        por_categoria.setdefault(despesa.categoria_eithra, []).append(
            _despesa_para_tela(despesa)
        )
    return por_categoria


def _despesa_para_tela(item) -> dict:
    return {
        "data": _dia(item.data),
        "descricao": item.descricao_original,
        "categoria": rotulo_da_categoria(item.categoria_eithra),
        "natureza": rotulo_da_natureza(item.natureza),
        "natureza_tom": TOM_POR_NATUREZA.get(item.natureza, "secondary"),
        "valor": _moeda(item.valor),
        "fornecedor": item.fornecedor_candidato or "",
        "documento": item.documento or "",
        "parcela": item.parcela or "",
        "grupo": item.categoria_original or "",
    }


def _categorias_para_tela(balanco) -> list[dict]:
    """Categorias agregadas do mês, ordenadas pelo total que mais pesou."""
    lancamentos = _lancamentos_por_categoria(balanco.despesas)
    categorias = [
        _categoria_para_tela(item, lancamentos.get(item.categoria, []))
        for item in balanco.categorias
        if (texto_para_decimal(item.total) or Decimal("0.00")) > Decimal("0.00")
    ]
    return sorted(categorias, key=lambda item: item["total_decimal"], reverse=True)


def _categoria_para_tela(categoria, lancamentos: list[dict]) -> dict:
    total = texto_para_decimal(categoria.total) or Decimal("0.00")
    return {
        "slug": re.sub(r"[^a-z0-9]+", "-", categoria.categoria.lower()).strip("-"),
        "nome": rotulo_da_categoria(categoria.categoria),
        "recorrente": _moeda(categoria.recorrente),
        "extra": _moeda(categoria.extraordinaria),
        "a_confirmar": _moeda(categoria.nao_determinada),
        "total": _moeda(categoria.total),
        "total_decimal": total,
        "lancamentos": lancamentos,
    }


def _grupos_em_atencao_para_tela(grupos) -> list[dict]:
    """Só os grupos que ainda têm diferença relevante a explicar."""
    grupos_em_atencao = []
    for grupo in grupos:
        if grupo.status != StatusConferencia.FALTA_IDENTIFICAR:
            continue
        diferenca = texto_para_decimal(grupo.diferenca) if grupo.diferenca else None
        if diferenca is None or abs(diferenca) <= TOLERANCIA:
            continue
        grupos_em_atencao.append(
            {
                "categoria": grupo.nome or "Despesas sem agrupamento",
                "declarado": _moeda(grupo.total_declarado),
                "identificado": _moeda(grupo.soma_extraida),
                "diferenca": _moeda(decimal_para_texto(abs(diferenca))),
            }
        )
    return grupos_em_atencao


def _grupo_para_tela(grupo, lancamentos: list[dict]) -> dict:
    """Traduz um agrupamento: o declarado, o identificado e a diferença.

    O agrupamento sem nome é o que o documento não rotulou — a leitura não
    inventa um nome para ele, apenas diz que não veio agrupado.
    """
    return {
        "nome": grupo.nome or "Despesas sem agrupamento",
        "categoria": rotulo_da_categoria(grupo.categoria_eithra),
        "quantidade": grupo.quantidade,
        "recorrente": _moeda(grupo.recorrente),
        "extraordinaria": _moeda(grupo.extraordinaria),
        "a_confirmar": _moeda(grupo.nao_determinada),
        "identificado": _moeda(grupo.soma_extraida),
        "total_declarado": _moeda(grupo.total_declarado) if grupo.total_declarado else "",
        "conferencia": _rotulo_da_conferencia(grupo.status),
        "tom_conferencia": TOM_POR_CONFERENCIA.get(grupo.status, "secondary"),
        "diferenca": _diferenca_para_tela(grupo.diferenca, grupo.status),
        "lancamentos": lancamentos,
    }


def _diferenca_para_tela(diferenca: str | None, status: str) -> str:
    """A frase da diferença do agrupamento — ou nada, quando não há o que dizer.

    A diferença nunca é escondida nem arredondada para zero: o que faltou
    identificar é dito, e o que veio acima do declarado também.
    """
    valor = texto_para_decimal(diferenca) if diferenca else None
    if valor is None or status != StatusConferencia.FALTA_IDENTIFICAR:
        return ""
    if valor > 0:
        return f"Falta identificar {_moeda(decimal_para_texto(valor))}"
    return f"Identificado {_moeda(decimal_para_texto(-valor))} acima do declarado"


def _rotulo_da_conferencia(status: str) -> str:
    """Nome humano da conferência; um rótulo honesto para o que não se conhece."""
    try:
        return StatusConferencia(status).label
    except ValueError:
        return "Não conferido"


def _saldo_para_tela(item) -> dict:
    return {
        "identificacao": _identificacao_de_saldo(item.identificacao),
        "valor": _moeda(item.valor),
        "saldo_anterior": _moeda(item.saldo_anterior),
        "saldo_atual": _moeda(item.saldo_final or item.valor),
    }


def _saldo_principal_para_tela(saldo_final: str | None, *, saldos: list[dict]) -> tuple[str, str]:
    """Escolhe o que cabe na faixa principal sem inventar consolidação."""
    saldo = _moeda(saldo_final)
    if saldo:
        return ("Saldo", saldo)
    if len(saldos) == 1 and saldos[0]["saldo_atual"]:
        return ("Saldo", saldos[0]["saldo_atual"])
    return ("Saldos", "Saldos por conta")


def _texto_do_detalhamento(detalhamento: dict) -> str | None:
    if not detalhamento.get("percentual"):
        return None
    return f"Detalhamento identificado: {detalhamento['percentual']}"


def _destaques_para_tela(*, categorias: list[dict], resumo: dict, detalhamento: dict) -> list[dict]:
    """No máximo três destaques, só quando trazem contexto útil."""
    destaques = []
    if categorias:
        maior = categorias[0]
        destaques.append(
            {
                "titulo": "Maior categoria",
                "rotulo": maior["nome"],
                "valor": maior["total"],
            }
        )

    extra = resumo.get("extraordinaria")
    if extra and extra != "R$ 0,00":
        destaques.append(
            {
                "titulo": "Despesas extras",
                "rotulo": "Gastos fora da rotina do mês",
                "valor": extra,
            }
        )

    if detalhamento.get("tem_residual"):
        destaques.append(
            {
                "titulo": "Não associado",
                "rotulo": "Parte das despesas ainda sem lançamento associado",
                "valor": detalhamento["valor_nao_associado"],
            }
        )

    return destaques[:3]


def _texto_grupos_em_atencao(grupos_em_atencao: list[dict]) -> str | None:
    quantidade = len(grupos_em_atencao)
    if not quantidade:
        return None
    if quantidade == 1:
        return "1 grupo precisa de atenção."
    return f"{quantidade} grupos precisam de atenção."


def _resumo_para_tela(resumo) -> dict:
    return {
        "recorrente": _moeda(resumo.recorrente_total),
        "extraordinaria": _moeda(resumo.extraordinaria_total),
        "a_confirmar": _moeda(resumo.nao_determinada_total),
        "despesas": resumo.despesas_identificadas,
        "pontos": resumo.pontos_de_atencao,
        "conferidos": resumo.grupos_conferidos,
        "com_diferenca": resumo.grupos_com_diferenca,
        "principais": [rotulo_da_categoria(item) for item in resumo.principais_categorias],
    }


def _identificacao_de_saldo(texto: str) -> str:
    """Preserva o rótulo útil do saldo sem expor número de conta desnecessário."""
    limpo = re.sub(r"\b\d[\d.\-]{3,}\b", "", texto or "")
    limpo = re.sub(r"\s{2,}", " ", limpo).strip(" -/")
    return limpo or "Conta/Fundo"


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
