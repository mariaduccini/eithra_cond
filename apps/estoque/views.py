"""Telas de estoque e compras operacionais.

O estoque existe para responder a duas perguntas: quanto há de cada item e para
onde foi o que saiu. Por isso a lista mostra o saldo ao lado do que se espera
ter, e a página do item mostra os movimentos que produziram esse saldo — o número
na tela sempre tem explicação.

As compras existem para repor o material: nascem como rascunho, ganham linhas com
destino, e só ao serem confirmadas movimentam o estoque. Confirmar é o momento em
que a compra passa a valer; cancelar só vale enquanto ela não valeu.

A autorização é decidida no backend, sempre a partir do vínculo ativo da pessoa
com o condomínio. Quem não enxerga um módulo não é avisado de que ele existe: a
resposta é 404. Quem enxerga e não pode decidir é recusado com 403.
"""
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.formats import number_format
from django.views.decorators.http import require_POST

from apps.cadastros.models import Fornecedor

from .constants import (
    DestinoDoItem,
    SituacaoDoSaldo,
    StatusCompra,
)
from .forms import (
    CompraForm,
    FiltroDeComprasForm,
    FiltroDeItensForm,
    ItemCompraForm,
    ItemEstoqueForm,
    MovimentacaoEstoqueForm,
    SaidaParaManutencaoForm,
)
from .models import Compra, ItemCompra, ItemEstoque
from .permissoes import (
    condominios_para_criar_compras,
    condominios_para_operar_estoque,
    condominios_para_ver_compras,
    condominios_para_ver_estoque,
    manutencoes_para_retirada,
    pode_cancelar_compra,
    pode_confirmar_compra,
    pode_editar_compra,
    pode_gerenciar_itens,
    pode_registrar_movimento,
    pode_registrar_saida_para_manutencao,
    tipos_de_movimento_permitidos,
)
from .services import (
    ErroDeEstoque,
    adicionar_item_compra,
    cancelar_compra,
    confirmar_compra,
    criar_compra,
    criar_item,
    definir_item_ativo,
    editar_compra,
    editar_item,
    editar_item_compra,
    registrar_movimentacao,
    registrar_saida_para_manutencao,
    remover_item_compra,
)

# Quantidade de itens e compras mostrados. A lista é de trabalho, não de arquivo.
LIMITE_DA_LISTA = 50

# Tom de cada leitura do saldo e de cada situação de compra no badge. Discreto:
# a lista inteira não pode gritar.
TOM_POR_SALDO = {
    SituacaoDoSaldo.NORMAL: "success",
    SituacaoDoSaldo.ABAIXO_DO_MINIMO: "warning",
    SituacaoDoSaldo.ZERADO: "danger",
}
TOM_POR_STATUS_COMPRA = {
    StatusCompra.RASCUNHO: "secondary",
    StatusCompra.CONFIRMADA: "success",
    StatusCompra.CANCELADA: "secondary",
}
TOM_POR_DESTINO = {
    DestinoDoItem.USO_IMEDIATO: "info",
    DestinoDoItem.ESTOQUE: "primary",
    DestinoDoItem.MISTO: "warning",
}


# -- Estoque: itens -------------------------------------------------------------


@login_required
def itens(request):
    """Os itens com o saldo de agora, nos condomínios que esta pessoa acompanha."""
    condominios = _condominios_para_ver_estoque(request)
    return _render_itens(request, condominios)


@login_required
def criar_item_view(request):
    """Cadastra um item novo. O serviço só nasce do que a gestão decidiu."""
    if request.method != "POST":
        return redirect("estoque:itens")

    condominios = condominios_para_operar_estoque(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomínio disponível para cadastrar item.")

    formulario = ItemEstoqueForm(request.POST, condominios=condominios)
    if not formulario.is_valid():
        return _render_itens(
            request,
            _condominios_para_ver_estoque(request),
            formulario=formulario,
            modal="criar",
            status=400,
        )

    dados = formulario.cleaned_data
    try:
        item = criar_item(
            condominio=dados["condominio"],
            criado_por=request.user,
            nome=dados["nome"],
            descricao=dados["descricao"],
            categoria_texto=dados["categoria_texto"],
            unidade=dados["unidade"],
            local_armazenamento=dados["local_armazenamento"],
            estoque_minimo=dados["estoque_minimo"],
            item_critico=dados["item_critico"],
            controla_validade=dados["controla_validade"],
        )
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
        return _render_itens(
            request,
            _condominios_para_ver_estoque(request),
            formulario=formulario,
            modal="criar",
            status=400,
        )

    messages.success(request, "Item cadastrado.")
    return redirect("estoque:item_detalhe", pk=item.pk)


@login_required
def item_detalhe(request, pk):
    """O item, quanto há dele agora e o que produziu esse saldo."""
    item = _item_autorizado(request, pk)
    return _render_item_detalhe(request, item, modal=request.GET.get("abrir"))


@login_required
@require_POST
def editar_item_view(request, pk):
    """Atualiza o cadastro do item. Só quem responde pelo condomínio edita."""
    item = _item_para_gerenciar(request, pk)
    formulario = ItemEstoqueForm(
        request.POST, condominio=item.condominio, item=item
    )
    if not formulario.is_valid():
        return _render_item_detalhe(
            request, item, formulario_item=formulario, modal="editar", status=400
        )

    dados = formulario.cleaned_data
    try:
        editar_item(
            item=item,
            nome=dados["nome"],
            descricao=dados["descricao"],
            categoria_texto=dados["categoria_texto"],
            unidade=dados["unidade"],
            local_armazenamento=dados["local_armazenamento"],
            estoque_minimo=dados["estoque_minimo"],
            item_critico=dados["item_critico"],
            controla_validade=dados["controla_validade"],
        )
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
        return redirect("estoque:item_detalhe", pk=item.pk)

    messages.success(request, "Item atualizado.")
    return redirect("estoque:item_detalhe", pk=item.pk)


@login_required
@require_POST
def movimentar_item(request, pk):
    """Registra um movimento feito à mão: uso, perda, vencimento ou ajuste."""
    item = _item_para_movimentar(request, pk)
    permitidos = tipos_de_movimento_permitidos(request.user, item)
    formulario = MovimentacaoEstoqueForm(request.POST, permitidos=permitidos)
    if not formulario.is_valid():
        return _render_item_detalhe(
            request, item, formulario_movimento=formulario, modal="movimentar", status=400
        )

    try:
        registrar_movimentacao(
            condominio=item.condominio,
            item=item,
            usuario=request.user,
            tipo=formulario.cleaned_data["tipo"],
            quantidade=formulario.cleaned_data["quantidade"],
            motivo=formulario.cleaned_data["motivo"],
        )
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
        return redirect("estoque:item_detalhe", pk=item.pk)

    messages.success(request, "Movimento registrado.")
    return redirect("estoque:item_detalhe", pk=item.pk)


@login_required
@require_POST
def retirar_para_manutencao(request, pk):
    """Retira material deste item para uma manutenção que o consumiu.

    Quem responde pelo condomínio ou supervisiona retira para qualquer
    manutenção em uso; a operação retira apenas para as suas. Ter saldo é
    conferido pelo serviço, no momento da retirada.
    """
    item = _item_autorizado(request, pk)
    formulario = SaidaParaManutencaoForm(
        request.POST,
        manutencoes=manutencoes_para_retirada(request.user).filter(
            condominio=item.condominio
        ),
        itens=ItemEstoque.objects.filter(pk=item.pk),
    )
    if not formulario.is_valid():
        return _render_item_detalhe(
            request, item, formulario_retirada=formulario, modal="retirar", status=400
        )

    manutencao = formulario.cleaned_data["manutencao"]
    if not pode_registrar_saida_para_manutencao(request.user, manutencao, item):
        raise PermissionDenied("Esta pessoa não retira material para esta manutenção.")

    try:
        registrar_saida_para_manutencao(
            manutencao=manutencao,
            item=item,
            usuario=request.user,
            quantidade=formulario.cleaned_data["quantidade"],
            motivo=formulario.cleaned_data["motivo"],
        )
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
        return redirect("estoque:item_detalhe", pk=item.pk)

    messages.success(request, "Retirada registrada.")
    return redirect("estoque:item_detalhe", pk=item.pk)


@login_required
@require_POST
def alternar_item(request, pk):
    """Retira o item da operação, ou o devolve, sem apagar o que já aconteceu."""
    item = _item_para_gerenciar(request, pk)
    novo = not item.ativo
    definir_item_ativo(item=item, ativo=novo)
    if novo:
        messages.success(request, "Item devolvido à operação.")
    else:
        messages.success(request, "Item retirado da operação.")
    return redirect("estoque:item_detalhe", pk=item.pk)


# -- Compras --------------------------------------------------------------------


@login_required
def compras(request):
    """As compras dos condomínios que esta pessoa acompanha."""
    condominios = _condominios_para_ver_compras(request)
    return _render_compras(request, condominios)


@login_required
def criar_compra_view(request):
    """Abre uma compra nova, como rascunho."""
    if request.method != "POST":
        return redirect("compras:listagem")

    condominios = condominios_para_criar_compras(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomínio disponível para registrar compra.")

    formulario = CompraForm(request.POST, condominios=condominios)
    if not formulario.is_valid():
        return _render_compras(
            request,
            _condominios_para_ver_compras(request),
            formulario=formulario,
            modal="criar",
            status=400,
        )

    dados = formulario.cleaned_data
    try:
        compra = criar_compra(
            condominio=dados["condominio"],
            criado_por=request.user,
            data_compra=dados["data_compra"],
            fornecedor=dados["fornecedor"],
            numero_documento=dados["numero_documento"],
            observacao=dados["observacao"],
            manutencao_origem=dados["manutencao_origem"],
        )
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
        return _render_compras(
            request,
            _condominios_para_ver_compras(request),
            formulario=formulario,
            modal="criar",
            status=400,
        )

    messages.success(request, "Compra aberta como rascunho. Registre os itens.")
    return redirect("compras:detalhe", pk=compra.pk)


@login_required
def compra_detalhe(request, pk):
    """A compra, suas linhas e o que já movimentou.

    Quando a pessoa abre uma linha para editar, a mesma página volta com o
    formulário já preenchido — não há tela separada só para mudar uma linha.
    """
    compra = _compra_autorizada(request, pk)
    linha_pk = request.GET.get("linha")
    linha = _linha_ou_404(compra, linha_pk) if linha_pk else None
    return _render_compra_detalhe(
        request,
        compra,
        linha_em_edicao=linha,
        modal=request.GET.get("abrir"),
    )


@login_required
@require_POST
def editar_compra_view(request, pk):
    """Atualiza o cabeçalho da compra, enquanto ela ainda é rascunho."""
    compra = _compra_para_editar(request, pk)
    formulario = CompraForm(request.POST, condominio=compra.condominio, compra=compra)
    if not formulario.is_valid():
        return _render_compra_detalhe(
            request, compra, formulario_compra=formulario, modal="editar", status=400
        )

    dados = formulario.cleaned_data
    try:
        editar_compra(
            compra=compra,
            data_compra=dados["data_compra"],
            fornecedor=dados["fornecedor"],
            numero_documento=dados["numero_documento"],
            observacao=dados["observacao"],
            manutencao_origem=dados["manutencao_origem"],
        )
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
        return redirect("compras:detalhe", pk=compra.pk)

    messages.success(request, "Compra atualizada.")
    return redirect("compras:detalhe", pk=compra.pk)


@login_required
@require_POST
def adicionar_linha(request, pk):
    """Acrescenta uma linha à compra, com o destino já dito."""
    compra = _compra_para_editar(request, pk)
    formulario = ItemCompraForm(request.POST, condominio=compra.condominio)
    if not formulario.is_valid():
        return _render_compra_detalhe(
            request, compra, formulario_linha=formulario, modal="linha", status=400
        )

    dados = formulario.cleaned_data
    try:
        adicionar_item_compra(
            compra=compra,
            descricao=dados["descricao"],
            quantidade=dados["quantidade"],
            valor_unitario=dados["valor_unitario"],
            unidade=dados["unidade"],
            destino=dados["destino"],
            item_estoque=dados["item_estoque"],
            quantidade_uso_imediato=dados["quantidade_uso_imediato"],
            manutencao=dados["manutencao"],
            validade=dados["validade"],
        )
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
        return redirect("compras:detalhe", pk=compra.pk)

    messages.success(request, "Item acrescentado à compra.")
    return redirect("compras:detalhe", pk=compra.pk)


@login_required
@require_POST
def editar_linha(request, pk, linha_pk):
    """Atualiza uma linha da compra, enquanto ela ainda é rascunho."""
    compra = _compra_para_editar(request, pk)
    linha = _linha_ou_404(compra, linha_pk)
    formulario = ItemCompraForm(
        request.POST, condominio=compra.condominio, linha=linha
    )
    if not formulario.is_valid():
        return _render_compra_detalhe(
            request,
            compra,
            formulario_linha=formulario,
            linha_em_edicao=linha,
            modal="linha",
            status=400,
        )

    dados = formulario.cleaned_data
    try:
        editar_item_compra(
            linha=linha,
            descricao=dados["descricao"],
            quantidade=dados["quantidade"],
            valor_unitario=dados["valor_unitario"],
            unidade=dados["unidade"],
            destino=dados["destino"],
            item_estoque=dados["item_estoque"],
            quantidade_uso_imediato=dados["quantidade_uso_imediato"],
            manutencao=dados["manutencao"],
            validade=dados["validade"],
        )
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
        return redirect("compras:detalhe", pk=compra.pk)

    messages.success(request, "Item da compra atualizado.")
    return redirect("compras:detalhe", pk=compra.pk)


@login_required
@require_POST
def remover_linha(request, pk, linha_pk):
    """Tira uma linha do rascunho."""
    compra = _compra_para_editar(request, pk)
    linha = _linha_ou_404(compra, linha_pk)
    try:
        remover_item_compra(linha=linha)
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
        return redirect("compras:detalhe", pk=compra.pk)

    messages.success(request, "Item retirado da compra.")
    return redirect("compras:detalhe", pk=compra.pk)


@login_required
@require_POST
def confirmar(request, pk):
    """Faz a compra valer: aplica o destino das linhas e movimenta o estoque."""
    compra = _compra_para_confirmar(request, pk)
    try:
        confirmar_compra(compra=compra, usuario=request.user)
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
        return redirect("compras:detalhe", pk=compra.pk)

    messages.success(request, "Compra confirmada.")
    return redirect("compras:detalhe", pk=compra.pk)


@login_required
@require_POST
def cancelar(request, pk):
    """Cancela um rascunho que não vai acontecer."""
    compra = _compra_para_cancelar(request, pk)
    try:
        cancelar_compra(compra=compra, usuario=request.user)
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
        return redirect("compras:detalhe", pk=compra.pk)

    messages.success(request, "Compra cancelada.")
    return redirect("compras:detalhe", pk=compra.pk)


# -- Autorização ----------------------------------------------------------------
#
# Toda pergunta passa pelo domínio. Quem não enxerga o módulo não descobre que o
# registro existe: a resposta é 404. Quem enxerga mas não pode decidir é recusado
# com 403 — a existência já é legítima, a decisão não é.


def _condominios_para_ver_estoque(request):
    """Condomínios cujo estoque esta pessoa acompanha, ou 404."""
    condominios = condominios_para_ver_estoque(request.user)
    if not condominios.exists():
        raise Http404("Nenhum estoque disponível para consulta.")
    return condominios


def _condominios_para_ver_compras(request):
    """Condomínios cujas compras esta pessoa acompanha, ou 404."""
    condominios = condominios_para_ver_compras(request.user)
    if not condominios.exists():
        raise Http404("Nenhuma compra disponível para consulta.")
    return condominios


def _item_autorizado(request, pk):
    """Resolve o item dentro dos condomínios que esta pessoa acompanha."""
    condominios = _condominios_para_ver_estoque(request)
    return _obter_ou_404(
        ItemEstoque.objects.com_saldo()
        .select_related("condominio", "local_armazenamento")
        .filter(condominio__in=condominios),
        pk,
    )


def _item_para_gerenciar(request, pk):
    """Resolve o item e exige, no backend, o direito de alterar o cadastro."""
    item = _item_autorizado(request, pk)
    if not pode_gerenciar_itens(request.user, item.condominio):
        raise PermissionDenied("Esta pessoa não administra o estoque deste condomínio.")
    return item


def _item_para_movimentar(request, pk):
    """Resolve o item e exige, no backend, o direito de registrar movimento."""
    item = _item_autorizado(request, pk)
    if not pode_registrar_movimento(request.user, item):
        raise PermissionDenied("Esta pessoa não registra movimentos neste item.")
    return item


def _compra_autorizada(request, pk):
    """Resolve a compra dentro dos condomínios que esta pessoa acompanha."""
    condominios = _condominios_para_ver_compras(request)
    return _obter_ou_404(
        Compra.objects.select_related(
            "condominio", "fornecedor", "manutencao_origem", "criado_por"
        ).filter(condominio__in=condominios),
        pk,
    )


def _compra_para_editar(request, pk):
    """Resolve a compra e exige, no backend, o direito de alterá-la."""
    compra = _compra_autorizada(request, pk)
    if not pode_editar_compra(request.user, compra):
        raise PermissionDenied("Esta pessoa não pode alterar esta compra.")
    return compra


def _compra_para_confirmar(request, pk):
    """Resolve a compra e exige, no backend, o direito de confirmá-la."""
    compra = _compra_autorizada(request, pk)
    if not pode_confirmar_compra(request.user, compra):
        raise PermissionDenied("Esta pessoa não pode confirmar esta compra.")
    return compra


def _compra_para_cancelar(request, pk):
    """Resolve a compra e exige, no backend, o direito de cancelá-la."""
    compra = _compra_autorizada(request, pk)
    if not pode_cancelar_compra(request.user, compra):
        raise PermissionDenied("Esta pessoa não pode cancelar esta compra.")
    return compra


def _linha_ou_404(compra, linha_pk):
    """Resolve uma linha dentro da própria compra, ou 404."""
    return _obter_ou_404(ItemCompra.objects.filter(compra=compra), linha_pk)


def _obter_ou_404(queryset, pk):
    """Resolve um registro pela chave, ou 404 — inclusive se a chave vier torta."""
    try:
        return get_object_or_404(queryset, pk=pk)
    except (ValidationError, ValueError, TypeError):
        raise Http404("Registro não encontrado.") from None


# -- Montagem das telas ---------------------------------------------------------


def _render_itens(request, condominios, *, formulario=None, modal=None, status=200):
    """Monta a lista de itens com os filtros aplicados e o formulário de cadastro."""
    filtros = FiltroDeItensForm(request.GET or None, condominios=condominios)
    itens = _itens_filtrados(condominios, filtros)
    pode_criar = condominios_para_operar_estoque(request.user).exists()

    if formulario is None and pode_criar:
        formulario = ItemEstoqueForm(
            condominios=condominios_para_operar_estoque(request.user)
        )

    contexto = {
        "itens": [_item_para_linha(item) for item in itens],
        "filtros": filtros,
        "total": len(itens),
        "pode_criar": pode_criar,
        "formulario_item": formulario,
        "modal_aberto": modal,
        "mostrar_condominio": condominios.count() > 1,
    }
    return render(request, "pages/estoque/itens.html", contexto, status=status)


def _render_item_detalhe(
    request,
    item,
    *,
    formulario_item=None,
    formulario_movimento=None,
    formulario_retirada=None,
    modal=None,
    status=200,
):
    """Monta a página do item com o que esta pessoa pode fazer com ele."""
    pode_gerenciar = pode_gerenciar_itens(request.user, item.condominio)
    tipos_permitidos = tipos_de_movimento_permitidos(request.user, item)
    manutencoes = manutencoes_para_retirada(request.user).filter(
        condominio=item.condominio
    )
    pode_retirar = _pode_retirar_para_alguma(request.user, manutencoes, item)

    if (
        (modal == "editar" and not pode_gerenciar)
        or (modal == "movimentar" and not tipos_permitidos)
        or (modal == "retirar" and not pode_retirar)
    ):
        modal = None

    contexto = {
        "item": _item_para_detalhe(item),
        "movimentos": _movimentos_para_tela(item),
        "pode_gerenciar": pode_gerenciar,
        "pode_movimentar": bool(tipos_permitidos),
        "pode_retirar": pode_retirar,
        "formulario_item": formulario_item
        or ItemEstoqueForm(
            condominio=item.condominio, item=item, initial=_iniciais_do_item(item)
        ),
        "formulario_movimento": formulario_movimento
        or MovimentacaoEstoqueForm(permitidos=tipos_permitidos),
        "formulario_retirada": formulario_retirada
        or SaidaParaManutencaoForm(
            manutencoes=manutencoes,
            itens=ItemEstoque.objects.filter(pk=item.pk),
        ),
        "modal_aberto": modal,
    }
    return render(request, "pages/estoque/item_detalhe.html", contexto, status=status)


def _render_compras(request, condominios, *, formulario=None, modal=None, status=200):
    """Monta a lista de compras com os filtros aplicados."""
    filtros = FiltroDeComprasForm(
        request.GET or None,
        condominios=condominios,
        fornecedores=Fornecedor.objects.filter(condominio__in=condominios, ativo=True),
    )
    compras = _compras_filtradas(condominios, filtros)
    pode_criar = condominios_para_criar_compras(request.user).exists()

    if formulario is None and pode_criar:
        formulario = CompraForm(condominios=condominios_para_criar_compras(request.user))

    contexto = {
        "compras": [_compra_para_linha(compra) for compra in compras],
        "filtros": filtros,
        "total": len(compras),
        "pode_criar": pode_criar,
        "formulario_compra": formulario,
        "modal_aberto": modal,
        "mostrar_condominio": condominios.count() > 1,
    }
    return render(request, "pages/compras/listagem.html", contexto, status=status)


def _render_compra_detalhe(
    request,
    compra,
    *,
    formulario_compra=None,
    formulario_linha=None,
    linha_em_edicao=None,
    modal=None,
    status=200,
):
    """Monta a página da compra com o que esta pessoa pode fazer com ela."""
    pode_editar = pode_editar_compra(request.user, compra)
    pode_confirmar = pode_confirmar_compra(request.user, compra)
    pode_cancelar = pode_cancelar_compra(request.user, compra)

    if (
        (modal == "editar" and not pode_editar)
        or (modal == "linha" and not pode_editar)
        or (modal == "confirmar" and not pode_confirmar)
        or (modal == "cancelar" and not pode_cancelar)
    ):
        modal = None

    contexto = {
        "compra": _compra_para_detalhe(compra),
        "linhas": [_linha_para_tela(linha) for linha in compra.itens.all()],
        "movimentos": _movimentos_da_compra(compra),
        "pode_editar": pode_editar,
        "pode_confirmar": pode_confirmar,
        "pode_cancelar": pode_cancelar,
        "formulario_compra": formulario_compra
        or CompraForm(
            condominio=compra.condominio,
            compra=compra,
            initial=_iniciais_da_compra(compra),
        ),
        "formulario_linha": formulario_linha
        or ItemCompraForm(
            condominio=compra.condominio,
            linha=linha_em_edicao,
            initial=_iniciais_da_linha(linha_em_edicao),
        ),
        "linha_em_edicao": linha_em_edicao,
        "modal_aberto": modal,
    }
    return render(request, "pages/compras/detalhe.html", contexto, status=status)


def _itens_filtrados(condominios, filtros):
    """Aplica os filtros essenciais e devolve os itens com o saldo anotado."""
    queryset = ItemEstoque.objects.filter(condominio__in=condominios).com_saldo()

    if filtros.is_valid():
        dados = filtros.cleaned_data
        if dados["condominio"]:
            queryset = queryset.filter(condominio=dados["condominio"])
        if dados["busca"]:
            queryset = queryset.buscar(dados["busca"])

        situacao = dados["situacao"]
        if situacao == "abaixo":
            queryset = queryset.abaixo_do_minimo()
        elif situacao == "zerados":
            queryset = queryset.zerados()
        elif situacao == "criticos":
            queryset = queryset.criticos()

    return queryset.filter(ativo=True).order_by("nome")[:LIMITE_DA_LISTA]


def _compras_filtradas(condominios, filtros):
    """Aplica os filtros essenciais e devolve as compras com o total anotado."""
    queryset = Compra.objects.filter(condominio__in=condominios).com_total()

    if filtros.is_valid():
        dados = filtros.cleaned_data
        if dados["condominio"]:
            queryset = queryset.filter(condominio=dados["condominio"])
        if dados["status"]:
            queryset = queryset.filter(status=dados["status"])
        if dados["fornecedor"]:
            queryset = queryset.filter(fornecedor=dados["fornecedor"])
        if dados["busca"]:
            queryset = queryset.buscar(dados["busca"])

    return queryset.recentes()[:LIMITE_DA_LISTA]


def _pode_retirar_para_alguma(usuario, manutencoes, item) -> bool:
    """Diz se há alguma manutenção alcançável para a retirada deste item."""
    return any(
        pode_registrar_saida_para_manutencao(usuario, manutencao, item)
        for manutencao in manutencoes[:20]
    )


# -- Tradução para a tela -------------------------------------------------------


def _iniciais_do_item(item) -> dict:
    """O que o formulário já deve trazer quando o item é aberto para edição."""
    return {
        "nome": item.nome,
        "categoria_texto": item.categoria_texto,
        "unidade": item.unidade,
        "local_armazenamento": item.local_armazenamento_id,
        "estoque_minimo": item.estoque_minimo,
        "item_critico": item.item_critico,
        "controla_validade": item.controla_validade,
        "descricao": item.descricao,
    }


def _iniciais_da_compra(compra) -> dict:
    """O que o formulário já deve trazer quando a compra é aberta para edição."""
    return {
        "data_compra": compra.data_compra,
        "fornecedor": compra.fornecedor_id,
        "numero_documento": compra.numero_documento,
        "manutencao_origem": compra.manutencao_origem_id,
        "observacao": compra.observacao,
    }


def _iniciais_da_linha(linha) -> dict:
    """O que o formulário já deve trazer quando uma linha é aberta para edição."""
    if linha is None:
        return {}
    return {
        "descricao": linha.descricao,
        "unidade": linha.unidade,
        "quantidade": linha.quantidade,
        "valor_unitario": linha.valor_unitario,
        "destino": linha.destino,
        "item_estoque": linha.item_estoque_id,
        "quantidade_uso_imediato": linha.quantidade_uso_imediato,
        "manutencao": linha.manutencao_id,
        "validade": linha.validade,
    }


def _nome_de(usuario) -> str:
    """O nome pelo qual a pessoa é reconhecida na tela."""
    return (
        getattr(usuario, "full_name", "")
        or getattr(usuario, "email", "")
        or "Sem responsável"
    )


def _dinheiro(valor) -> str:
    """O dinheiro como a gestão o lê, ou a ausência dele."""
    if valor is None:
        return "Sem valor"
    return f"R$ {number_format(valor, decimal_pos=2, force_grouping=True)}"


def _quantidade(valor, unidade: str = "") -> str:
    """A quantidade como a gestão a lê, sem zeros à direita sem sentido."""
    if valor is None:
        return ""
    numero = number_format(valor, decimal_pos=3, force_grouping=False)
    numero = numero.rstrip("0").rstrip(",") if "," in numero else numero
    return f"{numero} {unidade}".strip()


def _data_por_extenso(valor) -> str:
    """A data como a gestão a lê, ou a ausência dela."""
    return valor.strftime("%d/%m/%Y") if valor else "Sem data"


def _momento_por_extenso(momento) -> str:
    """O momento como a gestão o lê: hoje, ontem ou a data, com a hora."""
    if momento is None:
        return ""
    local = timezone.localtime(momento)
    hoje = timezone.localdate()
    dia = local.date()
    if dia == hoje:
        prefixo = "Hoje"
    elif dia == hoje - timezone.timedelta(days=1):
        prefixo = "Ontem"
    else:
        prefixo = local.strftime("%d/%m/%Y")
    return f"{prefixo} {local.strftime('%H:%M')}"


def _item_para_linha(item) -> dict:
    """O item como a linha da lista precisa dele: ao lado do que se espera ter."""
    return {
        "pk": item.pk,
        "nome": item.nome,
        "condominio": item.condominio.nome,
        "categoria": item.categoria_texto,
        "local": item.local_armazenamento.nome if item.local_armazenamento else "",
        "saldo": _quantidade(item.saldo, item.get_unidade_display().lower()),
        "saldo_bruto": item.saldo,
        "minimo": _quantidade(item.estoque_minimo, "") if item.estoque_minimo else "",
        "minimo_bruto": item.estoque_minimo,
        "situacao": SituacaoDoSaldo(item.situacao_do_saldo).label
        if item.situacao_do_saldo
        else "",
        "situacao_tom": TOM_POR_SALDO.get(item.situacao_do_saldo, "secondary"),
        "critico": item.item_critico,
        "unidade": item.get_unidade_display(),
        "ativo": item.ativo,
    }


def _ultima_compra_confirmada(item):
    """A linha da compra confirmada mais recente que repôs este item.

    Responde quanto se pagou da última vez. É leitura derivada: o item não
    guarda preço, e a compra ainda rascunho não conta como fato.
    """
    return (
        ItemCompra.objects.filter(
            item_estoque=item, compra__status=StatusCompra.CONFIRMADA
        )
        .select_related("compra", "compra__fornecedor")
        .order_by("-compra__data_compra", "-criado_em")
        .first()
    )


def _item_para_detalhe(item) -> dict:
    """O item como a página dele precisa: o cadastro, a leitura do saldo e o
    que se pagou por ele da última vez."""
    dados = _item_para_linha(item)
    ultima = _ultima_compra_confirmada(item)
    dados.update(
        {
            "descricao": item.descricao,
            "controla_validade": item.controla_validade,
            "criado_por": _nome_de(item.criado_por),
            "criado_em": _momento_por_extenso(item.criado_em),
            "ultima_compra": _data_por_extenso(ultima.compra.data_compra)
            if ultima
            else "",
            "ultimo_fornecedor": (
                ultima.compra.fornecedor.nome_razao_social
                if ultima and ultima.compra.fornecedor
                else ""
            ),
            "ultimo_custo": _dinheiro(ultima.valor_unitario) if ultima else "",
        }
    )
    return dados


def _movimentos_para_tela(item) -> list[dict]:
    """Os movimentos do item, do mais recente para o mais antigo."""
    movimentos = (
        item.movimentacoes.select_related("usuario", "manutencao", "compra_item__compra")
        .order_by("-criado_em")[:LIMITE_DA_LISTA]
    )
    return [_movimento_para_tela(movimento, item) for movimento in movimentos]


def _movimento_para_tela(movimento, item) -> dict:
    """O movimento como a tela o lê: o que aconteceu, quanto e por quê."""
    origem = ""
    if movimento.manutencao_id:
        origem = f"Manutenção · {movimento.manutencao.titulo}"
    elif movimento.compra_item_id:
        origem = f"Compra · {_data_por_extenso(movimento.compra_item.compra.data_compra)}"

    return {
        "quando": _momento_por_extenso(movimento.criado_em),
        "tipo": movimento.get_tipo_display(),
        "entrada": movimento.soma_ao_saldo,
        "quantidade": _quantidade(
            movimento.quantidade, item.get_unidade_display().lower()
        ),
        "origem": origem,
        "motivo": movimento.motivo,
        "quem": _nome_de(movimento.usuario),
    }


def _compra_para_linha(compra) -> dict:
    """A compra como a linha da lista precisa dela."""
    total = getattr(compra, "total", None)
    if total is None:
        total = compra.total_calculado()
    return {
        "pk": compra.pk,
        "data": _data_por_extenso(compra.data_compra),
        "condominio": compra.condominio.nome,
        "fornecedor": (
            compra.fornecedor.nome_razao_social if compra.fornecedor else "Sem fornecedor"
        ),
        "documento": compra.numero_documento,
        "total": _dinheiro(total),
        "status": compra.get_status_display(),
        "status_tom": TOM_POR_STATUS_COMPRA.get(compra.status, "secondary"),
    }


def _compra_para_detalhe(compra) -> dict:
    """A compra como a página dela precisa: o cabeçalho e o total."""
    dados = _compra_para_linha(compra)
    dados.update(
        {
            "observacao": compra.observacao,
            "manutencao_origem": (
                compra.manutencao_origem.titulo if compra.manutencao_origem_id else ""
            ),
            "manutencao_origem_pk": compra.manutencao_origem_id,
            "confirmada_em": _momento_por_extenso(compra.confirmada_em),
            "criado_por": _nome_de(compra.criado_por),
            "criado_em": _momento_por_extenso(compra.criado_em),
        }
    )
    return dados


def _linha_para_tela(linha) -> dict:
    """A linha da compra como a tela a lê: o que é, quanto e para onde vai."""
    return {
        "pk": linha.pk,
        "descricao": linha.descricao,
        "item": linha.item_estoque.nome if linha.item_estoque else "",
        "unidade": linha.get_unidade_display(),
        "quantidade": _quantidade(linha.quantidade),
        "valor_unitario": _dinheiro(linha.valor_unitario),
        "valor_total": _dinheiro(linha.valor_total),
        "destino": linha.get_destino_display(),
        "destino_tom": TOM_POR_DESTINO.get(linha.destino, "secondary"),
        "vai_para_estoque": _quantidade(linha.quantidade_que_vai_para_o_estoque),
        "consumo_imediato": _quantidade(linha.quantidade_uso_imediato),
        "manutencao": linha.manutencao.titulo if linha.manutencao_id else "",
        "validade": _data_por_extenso(linha.validade) if linha.validade else "",
    }


def _movimentos_da_compra(compra) -> list[dict]:
    """O que esta compra movimentou, quando já foi confirmada."""
    movimentos = []
    for linha in compra.itens.select_related("item_estoque"):
        for movimento in linha.movimentacoes.select_related("usuario", "item"):
            movimentos.append(
                {
                    "quando": _momento_por_extenso(movimento.criado_em),
                    "item": movimento.item.nome,
                    "quantidade": _quantidade(
                        movimento.quantidade,
                        movimento.item.get_unidade_display().lower(),
                    ),
                    "quem": _nome_de(movimento.usuario),
                }
            )
    return movimentos
