from decimal import Decimal, InvalidOperation
"""Telas de estoque e compras operacionais.

O estoque existe para responder a duas perguntas: quanto hÃ¡ de cada item e para
onde foi o que saiu. Por isso a lista mostra o saldo ao lado do que se espera
ter, e a pÃ¡gina do item mostra os movimentos que produziram esse saldo â€” o nÃºmero
na tela sempre tem explicaÃ§Ã£o.

As compras existem para repor o material: nascem como rascunho, ganham linhas com
destino, e sÃ³ ao serem confirmadas movimentam o estoque. Confirmar Ã© o momento em
que a compra passa a valer; cancelar sÃ³ vale enquanto ela nÃ£o valeu.

A autorizaÃ§Ã£o Ã© decidida no backend, sempre a partir do vÃ­nculo ativo da pessoa
com o condomÃ­nio. Quem nÃ£o enxerga um mÃ³dulo nÃ£o Ã© avisado de que ele existe: a
resposta Ã© 404. Quem enxerga e nÃ£o pode decidir Ã© recusado com 403.
"""
import uuid
from datetime import date

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import transaction
from django.db.models.functions import Lower
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import number_format
from django.views.decorators.http import require_POST

from apps.cadastros.constants import TipoPessoa
from apps.cadastros.models import Fornecedor, LocalOperacional
from apps.cadastros.services import ErroDeCadastro, criar_fornecedor
from apps.condominios.models import Condominio
from apps.core import audio
from apps.core.filtros import tem_filtros
from apps.core.paginacao import paginar_do_pedido
from apps.core.storage import ErroDeStorage, obter_provider
from apps.demandas.models import Demanda

from .constants import (
    DestinoDoItem,
    SituacaoDoSaldo,
    StatusLeituraDocumentoCompra,
    StatusCompra,
    StatusReembolso,
    TipoMovimentacaoEstoque,
)
from .forms import (
    CadastrarItemExtraidoForm,
    CompraForm,
    FiltroDeComprasForm,
    FiltroDeItensForm,
    ImportarCompraForm,
    ItemCompraForm,
    ItemEstoqueForm,
    MarcarReembolsoCompraForm,
    MovimentacaoEstoqueForm,
    ReembolsoCompraForm,
    SaidaParaManutencaoForm,
)
from .models import Compra, DocumentoCompra, ItemCompra, ItemEstoque
from .permissoes import (
    condominios_para_criar_compras,
    condominios_para_operar_estoque,
    condominios_para_ver_compras,
    condominios_para_ver_estoque,
    manutencoes_para_retirada,
    pode_cancelar_compra,
    pode_confirmar_compra,
    pode_editar_compra,
    pode_excluir_compra,
    pode_gerenciar_itens,
    pode_marcar_reembolso,
    pode_registrar_movimento,
    pode_registrar_reembolso,
    pode_registrar_saida_para_manutencao,
    pode_ver_estoque,
    tipos_de_movimento_permitidos,
)
from .services import (
    ErroDeEstoque,
    MOTIVO_DA_RETIRADA_RAPIDA,
    adicionar_item_compra,
    adicionar_documento_compra,
    cancelar_compra,
    cadastrar_item_extraido,
    chips_de_material,
    confirmar_compra,
    configurar_reembolso_compra,
    contar_estoque,
    criar_compra,
    criar_compra_com_documento,
    criar_item,
    definir_item_ativo,
    editar_compra,
    editar_item,
    editar_item_compra,
    historico_de_reposicao,
    incluir_item_extraido,
    marcar_reembolso_compra,
    marcar_reembolso_realizado,
    registrar_material_do_trabalho,
    registrar_movimentacao,
    registrar_saida_do_trabalho,
    registrar_saida_para_manutencao,
    remover_item_compra,
    reprocessar_documento_compra,
    saidas_do_item,
    selecionar_item_extraido,
    trazer_itens_identificados,
    ultimo_local_do_usuario,
)

# Quantos movimentos a pÃ¡gina do item mostra. A pÃ¡gina do item Ã© um resumo do
# saldo, nÃ£o o arquivo: o histÃ³rico inteiro fica onde sempre ficou.
LIMITE_DE_MOVIMENTOS_NA_FICHA = 50

# Tom de cada leitura do saldo e de cada situaÃ§Ã£o de compra no badge. Discreto:
# a lista inteira nÃ£o pode gritar.
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
    """Os itens com o saldo de agora, nos condomÃ­nios que esta pessoa acompanha."""
    condominios = _condominios_para_ver_estoque(request)
    return _render_itens(request, condominios)


@login_required
def criar_item_view(request):
    """Cadastra um item novo. O serviÃ§o sÃ³ nasce do que a gestÃ£o decidiu."""
    if request.method != "POST":
        return redirect("estoque:itens")

    condominios = condominios_para_operar_estoque(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomÃ­nio disponÃ­vel para cadastrar item.")

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
    """O item, quanto hÃ¡ dele agora e o que produziu esse saldo."""
    item = _item_autorizado(request, pk)
    return _render_item_detalhe(request, item, modal=request.GET.get("abrir"))


@login_required
@require_POST
def editar_item_view(request, pk):
    """Atualiza o cadastro do item. SÃ³ quem responde pelo condomÃ­nio edita."""
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
    """Registra um movimento feito Ã  mÃ£o: uso, perda, vencimento ou ajuste."""
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
    """Retira material deste item para uma manutenÃ§Ã£o que o consumiu.

    Quem responde pelo condomÃ­nio ou supervisiona retira para qualquer
    manutenÃ§Ã£o em uso; a operaÃ§Ã£o retira apenas para as suas. Ter saldo Ã©
    conferido pelo serviÃ§o, no momento da retirada.
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
        raise PermissionDenied("Esta pessoa nÃ£o retira material para esta manutenÃ§Ã£o.")

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


# -- Material junto do trabalho ------------------------------------------------


def _condominio_pedido(request):
    """O condomínio pedido pela tela, resolvido no backend. Sem certeza, nada.

    Nada aqui confia no identificador que vem do navegador: ele só é aceito se o
    condomínio existir e a pessoa puder consultar o estoque dele.
    """
    chave = request.GET.get("condominio") or request.POST.get("condominio")
    if not chave:
        return None
    try:
        return Condominio.objects.filter(pk=chave).first()
    except (ValidationError, ValueError, TypeError):
        return None


@login_required
def material_do_trabalho(request):
    """Os chips de material do trabalho, para o modal de conclusão.

    A tela manda o condomínio do registro (que vem do próprio registro, nunca
    digitado) e, quando há, o texto do que foi feito — dele sai a sugestão de
    quantidade. Sem acesso ao estoque naquele condomínio, a resposta é vazia: o
    modal simplesmente não oferece material.
    """
    condominio = _condominio_pedido(request)
    if condominio is None or not pode_ver_estoque(request.user, condominio):
        return JsonResponse({"chips": [], "itens": []})

    texto = request.GET.get("texto", "")
    itens = (
        ItemEstoque.objects.do_condominio(condominio)
        .ativos()
        .order_by(Lower("nome"))
        .values_list("id", "nome")
    )
    ultimo = ultimo_local_do_usuario(request.user, condominio)
    locais = (
        LocalOperacional.objects.filter(condominio=condominio, ativo=True)
        .order_by(Lower("nome"))
        .values_list("id", "nome")
    )
    return JsonResponse(
        {
            "chips": chips_de_material(condominio, texto=texto),
            "itens": [{"pk": str(pk), "nome": nome} for pk, nome in itens],
            "locais": [{"pk": str(pk), "nome": nome} for pk, nome in locais],
            "ultimo_local": str(ultimo.pk) if ultimo else "",
        }
    )


@login_required
def historico_do_item(request):
    """O que já aconteceu com um item, em números, para orientar a reposição."""
    condominio = _condominio_pedido(request)
    chave = request.GET.get("item", "")
    if condominio is None or not pode_ver_estoque(request.user, condominio):
        return JsonResponse({"historico": None})

    item = (
        ItemEstoque.objects.filter(pk=chave, condominio=condominio).first()
        if chave
        else None
    )
    if item is None:
        return JsonResponse({"historico": None})

    dados = historico_de_reposicao(item)
    return JsonResponse(
        {
            "historico": {
                "quantidade": dados.get("quantidade", ""),
                "data": dados["data"].strftime("%d/%m/%Y") if dados.get("data") else "",
                "saidas": dados.get("saidas", ""),
                "sem_destino": dados.get("sem_destino", ""),
                "saidas_url": reverse("estoque:saidas_do_item", args=[item.pk]),
            }
        }
    )


@login_required
def saidas_do_item_view(request, pk):
    """A lista curta das saídas de um item: para onde foi e quem levou."""
    item = _item_autorizado(request, pk)
    return render(
        request,
        "pages/estoque/_saidas_do_item.html",
        {"item": item, "saidas": saidas_do_item(item)[:LIMITE_DE_MOVIMENTOS_NA_FICHA]},
    )


@login_required
@require_POST
def retirar_rapido(request):
    """Registra que alguém pegou material do estoque, num passo só.

    É a retirada sem trabalho atrelado: sabe-se de onde saiu e quem levou, não
    para qual serviço. Diferente da retirada de gestão, aqui a saída **não**
    exige saldo — o material já saiu, e negá-lo apagaria o fato. Se o saldo
    ficar negativo, a contagem corrige depois.
    """
    condominio = _condominio_pedido(request)
    if condominio is None or not pode_ver_estoque(request.user, condominio):
        raise PermissionDenied("Esta pessoa não retira material deste condomínio.")

    local = None
    chave_local = request.POST.get("local", "")
    if chave_local:
        local = (
            LocalOperacional.objects.filter(
                pk=chave_local, condominio=condominio, ativo=True
            ).first()
            if chave_local
            else None
        )

    movimentos, negativo = registrar_material_do_trabalho(
        condominio=condominio,
        usuario=request.user,
        material=request.POST.get("material", ""),
        tipo=TipoMovimentacaoEstoque.SAIDA_USO,
        motivo=MOTIVO_DA_RETIRADA_RAPIDA,
        local=local,
    )

    if not movimentos:
        messages.error(request, "Marque ao menos um item para registrar a retirada.")
    elif negativo:
        messages.warning(
            request, "Retirada registrada. Estoque ficou negativo, confira na contagem."
        )
    else:
        messages.success(request, "Retirada registrada.")

    return redirect(_volta_da_retirada(request))


def _volta_da_retirada(request):
    """De volta para onde a pessoa estava — a tela que pediu a retirada."""
    destino = request.POST.get("volta", "")
    if destino and destino.startswith("/") and not destino.startswith("//"):
        return destino
    return reverse("core:meu_dia")


@login_required
@require_POST
def transcrever_retirada(request):
    """A fala vira texto no relato da retirada rápida — e para aí.

    Igual às outras portas de áudio do sistema: recebe o recado falado, devolve
    a transcrição fiel e descarta o arquivo. O texto cai no mesmo campo que a
    pessoa digitaria — o "o que você pegou", que sugere a quantidade dos chips —
    e daí em diante o caminho é o de sempre.
    """
    condominio = _condominio_pedido(request)
    if condominio is None or not pode_ver_estoque(request.user, condominio):
        raise PermissionDenied("Esta pessoa não retira material deste condomínio.")

    arquivo = request.FILES.get("audio")
    resultado = audio.transcrever(
        audio.PedidoDeAudio(
            conteudo=audio.ler_conteudo(arquivo),
            nome_do_arquivo=getattr(arquivo, "name", ""),
            content_type=getattr(arquivo, "content_type", ""),
            condominio=condominio,
            usuario=request.user,
        )
    )
    if resultado.obtida:
        return JsonResponse({"texto": resultado.texto})
    return JsonResponse({"mensagem": resultado.erro})


@login_required
def contagem_estoque(request):
    """A contagem do estoque: o saldo do sistema ao lado do que se contou.

    Entra-se nela para dizer quanto há de verdade. Onde a contagem difere do
    saldo, o sistema lança o ajuste com o motivo datado e o nome de quem contou.
    Campo deixado em branco é item não contado — e não vira ajuste nenhum.
    """
    condominios = _condominios_para_ver_estoque(request)
    gerenciáveis = condominios.filter(
        pk__in=condominios_para_operar_estoque(request.user).values("pk")
    )
    if not gerenciáveis.exists():
        raise PermissionDenied("Esta pessoa não faz a contagem do estoque.")

    condominio = _condominio_da_contagem(request, gerenciáveis)

    if request.method == "POST":
        if condominio is None:
            raise PermissionDenied("Esta pessoa não faz a contagem deste condomínio.")
        contagens = {
            linha.get("pk"): linha.get("quantidade")
            for linha in _linhas_de_contagem(request.POST)
        }
        resultado = contar_estoque(
            condominio=condominio,
            usuario=request.user,
            contagens=contagens,
        )
        if resultado["ajustes"]:
            messages.success(
                request,
                f"Contagem salva: {resultado['ajustes']} ajuste"
                f"{'s' if resultado['ajustes'] != 1 else ''} registrado"
                f"{'s' if resultado['ajustes'] != 1 else ''}.",
            )
        else:
            messages.info(request, "Contagem salva. Nenhum ajuste foi preciso.")
        if resultado["sem_destino"]:
            messages.warning(
                request,
                f"{resultado['sem_destino']} "
                f"{'itens têm' if resultado['sem_destino'] != 1 else 'item tem'} "
                "saída sem destino registrado.",
            )
        return redirect("estoque:contagem")

    return render(
        request,
        "pages/estoque/contagem.html",
        {
            "condominios": gerenciáveis,
            "condominio_atual": condominio,
            "mostrar_seletor": gerenciáveis.count() > 1,
            "itens": _itens_para_contagem(condominio),
        },
    )


def _linhas_de_contagem(dados) -> list[dict]:
    """Lê os pares item/quantidade digitados na contagem, na ordem em que vieram."""
    linhas = []
    for chave in dados.getlist("item"):
        linhas.append({"pk": chave, "quantidade": dados.get(f"quantidade-{chave}", "")})
    return linhas


def _itens_para_contagem(condominio):
    """Os itens do condomínio com o saldo atual, para a tela da contagem."""
    if condominio is None:
        return []
    return (
        ItemEstoque.objects.do_condominio(condominio)
        .ativos()
        .com_saldo()
        .order_by(Lower("nome"))
    )


def _condominio_da_contagem(request, condominios):
    """O condomínio em vista na contagem, conferido contra os autorizados."""
    escolhido = request.GET.get("condominio") or request.POST.get("condominio", "")
    if escolhido:
        return next((c for c in condominios if str(c.pk) == str(escolhido)), None)
    return condominios.first()


@login_required
@require_POST
def alternar_item(request, pk):
    """Retira o item da operaÃ§Ã£o, ou o devolve, sem apagar o que jÃ¡ aconteceu."""
    item = _item_para_gerenciar(request, pk)
    novo = not item.ativo
    definir_item_ativo(item=item, ativo=novo)
    if novo:
        messages.success(request, "Item devolvido Ã  operaÃ§Ã£o.")
    else:
        messages.success(request, "Item retirado da operaÃ§Ã£o.")
    return redirect("estoque:item_detalhe", pk=item.pk)


# -- Compras --------------------------------------------------------------------


@login_required
def compras(request):
    """As compras dos condomÃ­nios que esta pessoa acompanha."""
    condominios = _condominios_para_ver_compras(request)
    return _render_compras(request, condominios)


@login_required
def criar_compra_view(request):
    """Apresenta importaÃ§Ã£o como caminho principal e mantÃ©m cadastro manual."""
    condominios = condominios_para_criar_compras(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomÃ­nio disponÃ­vel para registrar compra.")

    if request.method != "POST":
        return _render_importar_compra(request, condominios)

    arquivo = request.FILES.get("arquivo")
    if arquivo is not None:
        formulario_importacao = ImportarCompraForm(
            request.POST,
            request.FILES,
            condominios=condominios,
        )
        if not formulario_importacao.is_valid():
            return _render_importar_compra(
                request, condominios, formulario=formulario_importacao, status=400
            )
        dados = formulario_importacao.cleaned_data
        try:
            compra, _documento = criar_compra_com_documento(
                condominio=dados["condominio"],
                criado_por=request.user,
                arquivo=arquivo,
                tipo_documento=dados["tipo_documento"],
            )
        except (ErroDeEstoque, ErroDeStorage, ImproperlyConfigured) as erro:
            messages.error(request, str(erro))
            return _render_importar_compra(
                request,
                condominios,
                formulario=formulario_importacao,
                status=400,
            )
        messages.success(
            request,
            "Documento armazenado. Revise as sugestÃµes antes de confirmar a compra.",
        )
        return redirect("compras:detalhe", pk=compra.pk)

    formulario = CompraForm(request.POST, condominios=condominios)
    if not formulario.is_valid():
        return _render_compras(
            request,
            _condominios_para_ver_compras(request),
            formulario=formulario,
            modal="manual",
            status=400,
        )

    dados = formulario.cleaned_data
    try:
        compra = criar_compra(
            condominio=dados["condominio"],
            criado_por=request.user,
            data_compra=dados["data_compra"],
            fornecedor=dados["fornecedor"],
            forma_pagamento=dados["forma_pagamento"],
            numero_documento=dados["numero_documento"],
            observacao=dados["observacao"],
            manutencao_origem=dados["manutencao_origem"],
            demanda_origem=dados["demanda_origem"],
        )
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
        return _render_compras(
            request,
            _condominios_para_ver_compras(request),
            formulario=formulario,
            modal="manual",
            status=400,
        )

    messages.success(request, "Compra aberta como rascunho. Registre os itens.")
    return redirect("compras:detalhe", pk=compra.pk)


@login_required
def compra_detalhe(request, pk):
    """A compra, suas linhas e o que jÃ¡ movimentou.

    Quando a pessoa abre uma linha para editar, a mesma pÃ¡gina volta com o
    formulÃ¡rio jÃ¡ preenchido â€” nÃ£o hÃ¡ tela separada sÃ³ para mudar uma linha.
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
def adicionar_documento_compra_view(request, pk):
    """Anexa outro documento ao rascunho autorizado.

    Responde em JSON quando o envio é automático — o arquivo é escolhido e
    enviado na mesma ação —, e redireciona quando o formulário é enviado sem
    JavaScript. O documento guardado e a leitura são idênticos nos dois caminhos:
    só muda como a resposta volta. Em qualquer caminho, uma falha não descarta o
    arquivo já recebido nem a compra em rascunho.
    """
    compra = _compra_para_editar(request, pk)
    formulario = ImportarCompraForm(
        request.POST,
        request.FILES,
        condominios=Condominio.objects.filter(pk=compra.condominio_id),
    )
    resposta_em_json = _quer_resposta_json(request)
    if not formulario.is_valid():
        return _responder_documento(
            request,
            compra,
            resposta_em_json,
            ok=False,
            recado="Escolha um PDF, recibo ou imagem válido.",
        )
    try:
        documento = adicionar_documento_compra(
            compra=compra,
            arquivo=formulario.cleaned_data["arquivo"],
            usuario=request.user,
            tipo_documento=formulario.cleaned_data["tipo_documento"],
        )
    except (ErroDeEstoque, ErroDeStorage, ImproperlyConfigured) as erro:
        return _responder_documento(
            request, compra, resposta_em_json, ok=False, recado=str(erro)
        )
    return _responder_documento(
        request,
        compra,
        resposta_em_json,
        ok=True,
        recado="Documento armazenado. Confira as sugestões de leitura.",
        documento=documento,
    )


def _quer_resposta_json(request) -> bool:
    """Se a tela pediu a resposta em JSON, e não a página redirecionada."""
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _responder_documento(
    request, compra, resposta_em_json, *, ok, recado, documento=None
):
    """Devolve o resultado do anexo no formato que o caminho pediu."""
    if not resposta_em_json:
        if ok:
            messages.success(request, recado)
        else:
            messages.error(request, recado)
        return redirect("compras:detalhe", pk=compra.pk)

    corpo = {"ok": ok}
    if ok:
        corpo["mensagem"] = recado
        if documento is not None:
            corpo["status_leitura"] = documento.get_status_leitura_display()
    else:
        corpo["erro"] = recado
    return JsonResponse(corpo, status=200 if ok else 400)


@login_required
@require_POST
def trazer_itens_identificados_view(request, pk, documento_pk):
    """Traz de uma vez os itens que o documento identificou para a compra."""
    compra = _compra_para_editar(request, pk)
    documento = _documento_da_compra(compra, documento_pk)
    try:
        linhas = trazer_itens_identificados(compra=compra, documento=documento)
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
    else:
        messages.success(
            request,
            f"{len(linhas)} item(ns) identificado(s) trazido(s) para a compra. "
            "Revise e ajuste o destino antes de confirmar.",
        )
    return redirect("compras:detalhe", pk=compra.pk)


@login_required
@require_POST
def reprocessar_documento_view(request, pk, documento_pk):
    """Relê o arquivo já guardado, sem duplicar documento, item nem compra."""
    compra = _compra_para_editar(request, pk)
    documento = _documento_da_compra(compra, documento_pk)
    try:
        reprocessar_documento_compra(documento=documento, usuario=request.user)
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
    else:
        messages.success(request, "Documento relido. Confira as sugestões atualizadas.")
    return redirect("compras:detalhe", pk=compra.pk)


@login_required
def baixar_documento_compra(request, pk, documento_pk):
    """Entrega acesso temporÃ¡rio sÃ³ depois de autorizar a compra."""
    compra = _compra_autorizada(request, pk)
    documento = _documento_da_compra(compra, documento_pk)
    try:
        provider = obter_provider(
            bucket=getattr(settings, "DOCUMENTOS_STORAGE_BUCKET", ""),
            nome_do_bucket="DOCUMENTOS_STORAGE_BUCKET",
        )
        validade = int(
            getattr(settings, "BALANCOS_URL_ACESSO_SEGUNDOS", 60) or 60
        )
        acesso = provider.url_assinada(
            key=documento.storage_key,
            validade_segundos=validade,
        )
        return redirect(acesso)
    except (ErroDeStorage, ImproperlyConfigured, OSError):
        messages.error(request, "O documento nÃ£o estÃ¡ disponÃ­vel no storage.")
        return redirect("compras:detalhe", pk=compra.pk)


@login_required
@require_POST
def selecionar_item_extraido_view(request, pk, documento_pk, indice):
    """Vincula uma proposta ao item canÃ´nico escolhido pela gestÃ£o."""
    compra = _compra_para_editar(request, pk)
    documento = _documento_da_compra(compra, documento_pk)
    item = _obter_ou_404(
        ItemEstoque.objects.filter(
            condominio_id=compra.condominio_id,
            ativo=True,
        ),
        request.POST.get("item_estoque"),
    )
    try:
        selecionar_item_extraido(
            documento=documento,
            indice=indice,
            item=item,
        )
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
    else:
        messages.success(request, "Item existente selecionado.")
    return redirect("compras:detalhe", pk=compra.pk)


@login_required
@require_POST
def cadastrar_item_extraido_view(request, pk, documento_pk, indice):
    """Cadastra um item canÃ´nico somente apÃ³s solicitaÃ§Ã£o explÃ­cita."""
    compra = _compra_para_editar(request, pk)
    if not pode_gerenciar_itens(request.user, compra.condominio):
        raise PermissionDenied("VocÃª nÃ£o pode cadastrar itens neste condomÃ­nio.")
    documento = _documento_da_compra(compra, documento_pk)
    propostas = (documento.dados_extraidos or {}).get("itens", [])
    proposta = propostas[indice] if 0 <= indice < len(propostas) else {}
    formulario = CadastrarItemExtraidoForm(
        request.POST,
        initial={
            "nome": proposta.get("descricao", ""),
            "categoria_texto": proposta.get("categoria_sugerida", ""),
        },
    )
    if not formulario.is_valid():
        messages.error(request, "Revise o nome e a categoria do item.")
        return redirect("compras:detalhe", pk=compra.pk)
    try:
        item = cadastrar_item_extraido(
            documento=documento,
            indice=indice,
            criado_por=request.user,
            nome=formulario.cleaned_data["nome"],
            categoria_texto=formulario.cleaned_data["categoria_texto"],
        )
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
    else:
        messages.success(request, f"Item Â«{item.nome}Â» cadastrado e vinculado.")
    return redirect("compras:detalhe", pk=compra.pk)


@login_required
@require_POST
def incluir_item_extraido_view(request, pk, documento_pk, indice):
    """Transforma uma proposta revisada em linha da Compra canÃ´nica."""
    compra = _compra_para_editar(request, pk)
    documento = _documento_da_compra(compra, documento_pk)
    formulario = ItemCompraForm(request.POST, condominio=compra.condominio)
    if not formulario.is_valid():
        messages.error(request, "Confira descriÃ§Ã£o, quantidade, valores e destino.")
        return redirect("compras:detalhe", pk=compra.pk)
    dados = formulario.cleaned_data
    try:
        incluir_item_extraido(
            compra=compra,
            documento=documento,
            indice=indice,
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
    else:
        messages.success(request, "Item revisado e incluÃ­do na compra.")
    return redirect("compras:detalhe", pk=compra.pk)


@login_required
@require_POST
def editar_compra_view(request, pk):
    """Atualiza o cabeÃ§alho da compra, enquanto ela ainda Ã© rascunho."""
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
            forma_pagamento=dados["forma_pagamento"],
            numero_documento=dados["numero_documento"],
            observacao=dados["observacao"],
            manutencao_origem=dados["manutencao_origem"],
            demanda_origem=dados["demanda_origem"],
        )
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
        return redirect("compras:detalhe", pk=compra.pk)

    messages.success(request, "Compra atualizada.")
    return redirect("compras:detalhe", pk=compra.pk)


@login_required
@require_POST
def adicionar_linha(request, pk):
    """Acrescenta uma linha Ã  compra, com o destino jÃ¡ dito."""
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

    messages.success(request, "Item acrescentado Ã  compra.")
    return redirect("compras:detalhe", pk=compra.pk)


@login_required
@require_POST
def editar_linha(request, pk, linha_pk):
    """Atualiza uma linha da compra, enquanto ela ainda Ã© rascunho."""
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
    dados_post = request.POST.copy()
    for campo in CompraForm.base_fields:
        nome_com_prefixo = f"confirmar-{campo}"
        if nome_com_prefixo not in dados_post and campo in dados_post:
            dados_post[nome_com_prefixo] = dados_post[campo]
    dados_post.setdefault("confirmar-condominio", str(compra.condominio_id))
    if "confirmar-data_compra" not in dados_post and compra.data_compra is not None:
        dados_post["confirmar-data_compra"] = compra.data_compra.isoformat()
    dados_post.setdefault("confirmar-fornecedor", str(compra.fornecedor_id or ""))
    dados_post.setdefault("confirmar-forma_pagamento", compra.forma_pagamento)
    dados_post.setdefault("confirmar-numero_documento", compra.numero_documento)
    dados_post.setdefault("confirmar-observacao", compra.observacao)
    dados_post.setdefault(
        "confirmar-manutencao_origem", str(compra.manutencao_origem_id or "")
    )
    formulario_confirmacao = CompraForm(
        dados_post,
        prefix="confirmar",
        condominio=compra.condominio,
        compra=compra,
    )
    formulario_reembolso = ReembolsoCompraForm(
        dados_post,
        condominio=compra.condominio,
        total=compra.total_calculado(),
    )
    if not formulario_confirmacao.is_valid() or not formulario_reembolso.is_valid():
        return _render_compra_detalhe(
            request,
            compra,
            formulario_confirmacao=formulario_confirmacao,
            formulario_reembolso=formulario_reembolso,
            modal="confirmar",
            status=400,
        )

    try:
        with transaction.atomic():
            campos = formulario_confirmacao.cleaned_data
            fornecedor = _fornecedor_da_confirmacao(
                request, compra, campos["fornecedor"]
            )
            _recusar_documento_repetido(
                compra, fornecedor, campos["numero_documento"]
            )
            editar_compra(
                compra=compra,
                data_compra=campos["data_compra"],
                fornecedor=fornecedor,
                forma_pagamento=campos["forma_pagamento"],
                numero_documento=campos["numero_documento"],
                observacao=campos["observacao"],
                manutencao_origem=campos["manutencao_origem"],
            )
            reembolso = formulario_reembolso.cleaned_data
            configurar_reembolso_compra(
                compra=compra,
                comprada_por_outra_pessoa=reembolso[
                    "comprada_por_outra_pessoa"
                ],
                precisa_reembolso=reembolso["precisa_reembolso"],
                reembolso_para=reembolso["reembolso_para"],
                valor_reembolso=reembolso["valor_reembolso"],
                observacao_reembolso=reembolso["observacao_reembolso"],
            )
            confirmar_compra(compra=compra, usuario=request.user)
    except (ErroDeEstoque, ErroDeCadastro, ValidationError) as erro:
        messages.error(request, str(erro))
        return redirect("compras:detalhe", pk=compra.pk)

    messages.success(request, "Compra confirmada.")
    return redirect("compras:detalhe", pk=compra.pk)


@login_required
@require_POST
def marcar_reembolso_realizado_view(request, pk):
    """Registra a devoluÃ§Ã£o sem criar outra compra ou uma despesa duplicada."""
    compra = _compra_autorizada(request, pk)
    if not pode_registrar_reembolso(request.user, compra):
        raise PermissionDenied("VocÃª nÃ£o pode registrar este reembolso.")
    try:
        marcar_reembolso_realizado(compra=compra)
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
    else:
        messages.success(request, "Reembolso marcado como realizado.")
    return redirect("compras:detalhe", pk=compra.pk)


@login_required
@require_POST
def cancelar(request, pk):
    """Cancela um rascunho que nÃ£o vai acontecer."""
    compra = _compra_para_cancelar(request, pk)
    try:
        cancelar_compra(compra=compra, usuario=request.user)
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
        return redirect("compras:detalhe", pk=compra.pk)

    messages.success(request, "Compra cancelada.")
    return redirect("compras:detalhe", pk=compra.pk)


@login_required
@require_POST
def excluir_rascunho(request, pk):
    """Apaga de vez um rascunho que ainda não virou nada."""
    compra = _compra_para_excluir(request, pk)
    compra.delete()
    messages.success(request, "Rascunho excluído.")
    return redirect("compras:listagem")


@login_required
@require_POST
def marcar_reembolso_compra_view(request, pk):
    """Marca a compra para reembolso direto da lista, sem passar pela edição."""
    compra = _compra_autorizada(request, pk)
    if not pode_marcar_reembolso(request.user, compra):
        raise PermissionDenied("Esta pessoa não pode marcar este reembolso.")

    formulario = MarcarReembolsoCompraForm(
        request.POST,
        condominio=compra.condominio,
        total=compra.total_calculado(),
    )
    if not formulario.is_valid():
        erros = " ".join(
            erro for lista in formulario.errors.values() for erro in lista
        )
        messages.error(request, erros or "Revise os dados do reembolso.")
        return redirect("compras:listagem")

    dados = formulario.cleaned_data
    try:
        marcar_reembolso_compra(
            compra=compra,
            reembolso_para=dados["reembolso_para"],
            valor_reembolso=dados["valor_reembolso"],
            observacao_reembolso=dados["observacao_reembolso"],
        )
    except ErroDeEstoque as erro:
        messages.error(request, str(erro))
    else:
        messages.success(request, "Reembolso marcado como pendente.")
    return redirect("compras:listagem")


# -- AutorizaÃ§Ã£o ----------------------------------------------------------------
#
# Toda pergunta passa pelo domÃ­nio. Quem nÃ£o enxerga o mÃ³dulo nÃ£o descobre que o
# registro existe: a resposta Ã© 404. Quem enxerga mas nÃ£o pode decidir Ã© recusado
# com 403 â€” a existÃªncia jÃ¡ Ã© legÃ­tima, a decisÃ£o nÃ£o Ã©.


def _condominios_para_ver_estoque(request):
    """CondomÃ­nios cujo estoque esta pessoa acompanha, ou 404."""
    condominios = condominios_para_ver_estoque(request.user)
    if not condominios.exists():
        raise Http404("Nenhum estoque disponÃ­vel para consulta.")
    return condominios


def _condominios_para_ver_compras(request):
    """CondomÃ­nios cujas compras esta pessoa acompanha, ou 404."""
    condominios = condominios_para_ver_compras(request.user)
    if not condominios.exists():
        raise Http404("Nenhuma compra disponÃ­vel para consulta.")
    return condominios


def _item_autorizado(request, pk):
    """Resolve o item dentro dos condomÃ­nios que esta pessoa acompanha."""
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
        raise PermissionDenied("Esta pessoa nÃ£o administra o estoque deste condomÃ­nio.")
    return item


def _item_para_movimentar(request, pk):
    """Resolve o item e exige, no backend, o direito de registrar movimento."""
    item = _item_autorizado(request, pk)
    if not pode_registrar_movimento(request.user, item):
        raise PermissionDenied("Esta pessoa nÃ£o registra movimentos neste item.")
    return item


def _compra_autorizada(request, pk):
    """Resolve a compra dentro dos condomÃ­nios que esta pessoa acompanha."""
    condominios = _condominios_para_ver_compras(request)
    return _obter_ou_404(
        Compra.objects.select_related(
            "condominio", "fornecedor", "manutencao_origem", "criado_por"
        ).filter(condominio__in=condominios),
        pk,
    )


def _compra_para_editar(request, pk):
    """Resolve a compra e exige, no backend, o direito de alterÃ¡-la."""
    compra = _compra_autorizada(request, pk)
    if not pode_editar_compra(request.user, compra):
        raise PermissionDenied("Esta pessoa nÃ£o pode alterar esta compra.")
    return compra


def _compra_para_confirmar(request, pk):
    """Resolve a compra e exige, no backend, o direito de confirmÃ¡-la."""
    compra = _compra_autorizada(request, pk)
    if not pode_confirmar_compra(request.user, compra):
        raise PermissionDenied("Esta pessoa nÃ£o pode confirmar esta compra.")
    return compra


def _compra_para_cancelar(request, pk):
    """Resolve a compra e exige, no backend, o direito de cancelÃ¡-la."""
    compra = _compra_autorizada(request, pk)
    if not pode_cancelar_compra(request.user, compra):
        raise PermissionDenied("Esta pessoa nÃ£o pode cancelar esta compra.")
    return compra


def _compra_para_excluir(request, pk):
    """Resolve a compra e exige, no backend, o direito de apagÃ¡-la."""
    compra = _compra_autorizada(request, pk)
    if not pode_excluir_compra(request.user, compra):
        raise PermissionDenied("Esta pessoa nÃ£o pode excluir esta compra.")
    return compra


def _linha_ou_404(compra, linha_pk):
    """Resolve uma linha dentro da prÃ³pria compra, ou 404."""
    return _obter_ou_404(ItemCompra.objects.filter(compra=compra), linha_pk)


def _obter_ou_404(queryset, pk):
    """Resolve um registro pela chave, ou 404 â€” inclusive se a chave vier torta."""
    try:
        return get_object_or_404(queryset, pk=pk)
    except (ValidationError, ValueError, TypeError):
        raise Http404("Registro nÃ£o encontrado.") from None


def _documento_da_compra(compra, documento_pk):
    """SÃ³ resolve documentos pertencentes Ã  compra jÃ¡ autorizada."""
    return _obter_ou_404(
        DocumentoCompra.objects.filter(compra_id=compra.pk),
        documento_pk,
    )


def _fornecedor_da_confirmacao(request, compra, fornecedor):
    """Decide o fornecedor da compra no momento de confirmar.

    A gestão pode vincular um fornecedor já cadastrado; se a nota apontou um que
    já existe — reconhecido pelo CNPJ —, ele é vinculado; se não existe e a
    gestão autorizar, o cadastro nasce aqui, nunca no upload do documento.
    """
    if fornecedor is not None:
        return fornecedor
    sugestoes = _sugestoes_da_compra(compra)
    identificado = sugestoes.get("fornecedor_identificado")
    if identificado is not None:
        return identificado
    if request.POST.get("autorizar_fornecedor_novo") != "on":
        return None
    novo = sugestoes.get("fornecedor_novo")
    if not novo:
        return None
    documento = "".join(
        caractere
        for caractere in str(novo.get("documento") or "")
        if caractere.isdigit()
    )
    return criar_fornecedor(
        condominio=compra.condominio,
        criado_por=request.user,
        dados={
            "nome_razao_social": novo.get("nome") or "Fornecedor da nota",
            "documento": documento,
            "tipo_pessoa": _tipo_de_pessoa_do_documento(documento),
        },
    )


def _tipo_de_pessoa_do_documento(documento: str) -> str:
    """O tipo de pessoa que o comprimento do documento revela."""
    if len(documento) == 14:
        return TipoPessoa.PJ
    if len(documento) == 11:
        return TipoPessoa.PF
    return TipoPessoa.NAO_INFORMADO


def _recusar_documento_repetido(compra, fornecedor, numero_documento) -> None:
    """Impede registrar duas vezes a mesma compra com o mesmo documento."""
    numero = " ".join(str(numero_documento or "").split())
    if not numero or fornecedor is None:
        return
    repetida = (
        Compra.objects.filter(
            condominio=compra.condominio,
            status=StatusCompra.CONFIRMADA,
            fornecedor=fornecedor,
            numero_documento=numero,
        )
        .exclude(pk=compra.pk)
        .exists()
    )
    if repetida:
        raise ErroDeEstoque(
            "Já existe uma compra confirmada com este documento para este "
            "fornecedor. Confira se esta compra não está sendo registrada duas vezes."
        )


def _sugestoes_da_compra(compra) -> dict:
    """A leitura mais recente da compra, pronta para o topo da tela.

    Enquanto a compra é rascunho, o topo mostra o que o documento sugeriu —
    fornecedor, data, total e itens — sem fingir que já é dado confirmado. Quando
    o CNPJ lido não corresponde a nenhum fornecedor do condomínio, a proposta de
    fornecedor novo também vem daqui: ela só vira cadastro na confirmação.
    """
    documento = compra.documentos.order_by("-criado_em").first()
    dados = dict((documento.dados_extraidos or {}) if documento else {})
    sugestoes = {
        "documento": documento,
        "fornecedor": str(dados.get("fornecedor") or "").strip(),
        "cnpj": dados.get("cnpj") or "",
        "data_compra": dados.get("data_compra") or "",
        "numero_documento": dados.get("numero_documento") or "",
        "forma_pagamento": dados.get("forma_pagamento") or "",
        "total_formatado": "",
        "itens": list(dados.get("itens", [])),
        "itens_pendentes": 0,
        "fornecedor_identificado": None,
        "fornecedor_novo": None,
    }
    total = dados.get("valor_total")
    if total not in (None, ""):
        try:
            sugestoes["total_formatado"] = _dinheiro(Decimal(str(total)))
        except (InvalidOperation, ValueError):
            sugestoes["total_formatado"] = ""
    sugestoes["itens_pendentes"] = sum(
        1 for proposta in sugestoes["itens"] if not proposta.get("item_compra_id")
    )

    cnpj = "".join(
        caractere for caractere in str(sugestoes["cnpj"]) if caractere.isdigit()
    )
    if cnpj:
        sugestoes["fornecedor_identificado"] = next(
            (
                candidato
                for candidato in Fornecedor.objects.filter(
                    condominio=compra.condominio, ativo=True
                )
                if candidato.documento == cnpj
            ),
            None,
        )
    if (
        compra.fornecedor_id is None
        and cnpj
        and sugestoes["fornecedor"]
        and sugestoes["fornecedor_identificado"] is None
    ):
        sugestoes["fornecedor_novo"] = {
            "nome": sugestoes["fornecedor"],
            "documento": cnpj,
        }
    return sugestoes


# -- Montagem das telas ---------------------------------------------------------


def _render_itens(request, condominios, *, formulario=None, modal=None, status=200):
    """Monta a lista de itens com os filtros aplicados e o formulÃ¡rio de cadastro."""
    filtros = FiltroDeItensForm(request.GET or None, condominios=condominios)
    pagina_itens = paginar_do_pedido(_itens_filtrados(condominios, filtros), request.GET)
    pode_criar = condominios_para_operar_estoque(request.user).exists()

    if formulario is None and pode_criar:
        formulario = ItemEstoqueForm(
            condominios=condominios_para_operar_estoque(request.user)
        )

    contexto = {
        "itens": [_item_para_linha(item) for item in pagina_itens],
        "pagina_itens": pagina_itens,
        "filtros": filtros,
        "filtros_ativos": tem_filtros(filtros),
        "total": pagina_itens.paginator.count,
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
    """Monta a pÃ¡gina do item com o que esta pessoa pode fazer com ele."""
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
        # A reposição se decide com o passado à vista: quanto veio na última
        # compra, quanto já saiu e quanto saiu sem que se saiba para onde foi.
        "historico": historico_de_reposicao(item),
        "saidas": saidas_do_item(item)[:LIMITE_DE_MOVIMENTOS_NA_FICHA],
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


def _render_importar_compra(request, condominios, *, formulario=None, status=200):
    """PÃ¡gina principal de captura, com alternativa manual preservada."""
    return render(
        request,
        "pages/compras/criar.html",
        {
            "formulario_importacao": formulario
            or ImportarCompraForm(condominios=condominios),
            "mostrar_condominio": condominios.count() > 1,
        },
        status=status,
    )


def _iniciais_compra_da_demanda(request):
    """O formulário de compra já aberto a partir do menu de uma demanda.

    Quando o pedido vem de ``?demanda=<pk>``, o condomínio, a observação e a
    demanda de origem chegam preenchidos — a compra nasce sabendo de onde veio.
    A demanda só vale se esta pessoa pode registrar compras no condomínio dela.
    """
    bruto = request.GET.get("demanda")
    if not bruto:
        return None
    try:
        chave = uuid.UUID(str(bruto))
    except (ValueError, TypeError, AttributeError):
        return None

    demanda = Demanda.objects.filter(
        pk=chave, condominio__in=condominios_para_criar_compras(request.user)
    ).first()
    if demanda is None:
        return None

    return {
        "condominio": demanda.condominio_id,
        "observacao": demanda.titulo,
        "demanda_origem": demanda.pk,
    }


def _render_compras(request, condominios, *, formulario=None, modal=None, status=200):
    """Monta a lista de compras com os filtros aplicados."""
    filtros = FiltroDeComprasForm(
        request.GET or None,
        condominios=condominios,
        fornecedores=Fornecedor.objects.filter(condominio__in=condominios, ativo=True),
    )
    chip = _chip_da_listagem(request)
    recorte = _compras_do_chip(_compras_filtradas(condominios, filtros), chip)
    pagina_compras = paginar_do_pedido(recorte, request.GET)
    pode_criar = condominios_para_criar_compras(request.user).exists()

    if formulario is None and pode_criar:
        formulario = CompraForm(
            condominios=condominios_para_criar_compras(request.user),
            initial=_iniciais_compra_da_demanda(request),
        )

    contexto = {
        "compras": [
            _compra_para_linha(compra, request.user) for compra in pagina_compras
        ],
        "pagina_compras": pagina_compras,
        "filtros": filtros,
        "filtros_ativos": tem_filtros(filtros),
        "total": pagina_compras.paginator.count,
        "pode_criar": pode_criar,
        "formulario_compra": formulario,
        "modal_aberto": modal or request.GET.get("abrir"),
        "mostrar_condominio": condominios.count() > 1,
        "chips": _chips_da_listagem(request, condominios, chip),
        "chip_ativo": chip,
        "pessoas_reembolso": (
            get_user_model()
            .objects.filter(
                is_active=True,
                acessos_condominio__condominio__in=condominios,
                acessos_condominio__ativo=True,
            )
            .distinct()
            .order_by("full_name", "email")
        ),
    }
    return render(request, "pages/compras/listagem.html", contexto, status=status)


def _render_compra_detalhe(
    request,
    compra,
    *,
    formulario_compra=None,
    formulario_confirmacao=None,
    formulario_linha=None,
    formulario_reembolso=None,
    linha_em_edicao=None,
    modal=None,
    status=200,
):
    """Monta a pÃ¡gina da compra com o que esta pessoa pode fazer com ela."""
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
        "documentos_compra": _documentos_para_tela(compra),
        "sugestoes_compra": _sugestoes_da_compra(compra),
        "itens_estoque_disponiveis": list(
            ItemEstoque.objects.do_condominio(compra.condominio).ativos()
        ),
        "movimentos": _movimentos_da_compra(compra),
        "pode_editar": pode_editar,
        "pode_confirmar": pode_confirmar,
        "pode_cancelar": pode_cancelar,
        "formulario_compra": formulario_compra
        or CompraForm(
            condominio=compra.condominio,
            compra=compra,
            initial=_iniciais_revisao_compra(compra),
        ),
        "formulario_confirmacao": formulario_confirmacao
        or CompraForm(
            condominio=compra.condominio,
            compra=compra,
            initial=_iniciais_revisao_compra(compra),
            prefix="confirmar",
        ),
        "formulario_reembolso": formulario_reembolso
        or ReembolsoCompraForm(
            condominio=compra.condominio,
            total=compra.total_calculado(),
            initial={
                "comprada_por_outra_pessoa": compra.comprada_por_outra_pessoa,
                "precisa_reembolso": compra.precisa_reembolso,
                "reembolso_para": compra.reembolso_para_id,
                "valor_reembolso": (
                    compra.valor_reembolso if compra.precisa_reembolso else ""
                ),
                "observacao_reembolso": compra.observacao_reembolso,
            },
        ),
        "total_compra_bruto": compra.total_calculado(),
        "formulario_documento": ImportarCompraForm(
            condominios=Condominio.objects.filter(pk=compra.condominio_id)
        ),
        "pode_registrar_reembolso": pode_registrar_reembolso(
            request.user, compra
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


def _iniciais_revisao_compra(compra):
    """Prioriza valores jÃ¡ revisados; senÃ£o oferece sugestÃµes sem gravÃ¡-las."""
    iniciais = _iniciais_da_compra(compra)
    documento = compra.documentos.order_by("-criado_em").first()
    sugestoes = (documento.dados_extraidos or {}) if documento else {}
    if compra.data_compra is None and sugestoes.get("data_compra"):
        iniciais["data_compra"] = sugestoes.get("data_compra") or ""
    if compra.fornecedor_id is None:
        cnpj = "".join(
            caractere
            for caractere in str(sugestoes.get("cnpj") or "")
            if caractere.isdigit()
        )
        fornecedor = next(
            (
                candidato
                for candidato in Fornecedor.objects.filter(
                    condominio=compra.condominio,
                    ativo=True,
                )
                if candidato.documento == cnpj and cnpj
            ),
            None,
        )
        if fornecedor:
            iniciais["fornecedor"] = fornecedor.pk
    if not iniciais["forma_pagamento"]:
        iniciais["forma_pagamento"] = sugestoes.get("forma_pagamento") or ""
    if not iniciais["numero_documento"] and sugestoes.get("numero_documento"):
        iniciais["numero_documento"] = sugestoes.get("numero_documento") or ""
    return iniciais


def _documentos_para_tela(compra):
    """Monta as sugestÃµes com opÃ§Ãµes reais do catÃ¡logo do mesmo condomÃ­nio."""
    documentos = []
    itens_catalogo = list(
        ItemEstoque.objects.do_condominio(compra.condominio).ativos()
    )
    for documento in compra.documentos.all():
        dados = documento.dados_extraidos or {}
        propostas = []
        for indice, proposta_original in enumerate(dados.get("itens", [])):
            proposta = dict(proposta_original)
            escolhido_id = proposta.get("item_estoque_sugerido")
            escolhido = next(
                (
                    item
                    for item in itens_catalogo
                    if str(item.pk) == str(escolhido_id)
                ),
                None,
            )
            candidatos_ids = set(proposta.get("item_estoque_candidatos", []))
            formulario_linha = ItemCompraForm(
                condominio=compra.condominio,
                initial={
                    "descricao": proposta.get("descricao", ""),
                    "quantidade": proposta.get("quantidade") or "1",
                    "valor_unitario": proposta.get("valor_unitario") or "",
                    "unidade": proposta.get("unidade") or "unidade",
                    "destino": DestinoDoItem.ESTOQUE,
                    "item_estoque": escolhido.pk if escolhido else "",
                },
            )
            propostas.append(
                {
                    **proposta,
                    "indice": indice,
                    "item_escolhido": escolhido,
                    "formulario_linha": formulario_linha,
                    "candidatos": [
                        item
                        for item in itens_catalogo
                        if str(item.pk) in candidatos_ids
                    ],
                    "quantidade_inicial": proposta.get("quantidade") or "1",
                    "unitario_inicial": proposta.get("valor_unitario") or "",
                    "ja_incluido": bool(proposta.get("item_compra_id")),
                }
            )
        total = dados.get("valor_total")
        documentos.append(
            {
                "registro": documento,
                "sugestoes": dados,
                "itens": propostas,
                "pendentes": sum(
                    1 for proposta in propostas if not proposta["ja_incluido"]
                ),
                "erro": documento.erro_leitura,
                "total_sugerido": (
                    _dinheiro(Decimal(str(total))) if total not in (None, "") else ""
                ),
            }
        )
    return documentos


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

    return queryset.filter(ativo=True).order_by("nome")


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

    return queryset.recentes()


# Os recortes que a gestão olha primeiro na lista de compras.
CHIPS_DE_COMPRAS = ("rascunhos", "reembolso", "confirmadas")


def _chip_da_listagem(request) -> str:
    """O chip ativo, só se ele existir. Um valor estranho não muda a lista."""
    chip = request.GET.get("chip")
    return chip if chip in CHIPS_DE_COMPRAS else ""


def _compras_do_chip(queryset, chip):
    """Aplica o recorte do chip escolhido sobre a lista já filtrada."""
    if chip == "rascunhos":
        return queryset.filter(status=StatusCompra.RASCUNHO)
    if chip == "reembolso":
        return queryset.filter(status_reembolso=StatusReembolso.PENDENTE)
    if chip == "confirmadas":
        hoje = timezone.localdate()
        return queryset.filter(
            status=StatusCompra.CONFIRMADA,
            data_compra__year=hoje.year,
            data_compra__month=hoje.month,
        )
    return queryset


def _chips_da_listagem(request, condominios, chip_ativo):
    """Os três recortes, com o total de cada um e o link que os aplica."""
    base = Compra.objects.filter(condominio__in=condominios)
    hoje = timezone.localdate()
    definicao = (
        (
            "rascunhos",
            "Rascunhos",
            "bx-edit",
            "muted",
            base.filter(status=StatusCompra.RASCUNHO).count(),
        ),
        (
            "reembolso",
            "Reembolso pendente",
            "bx-wallet",
            "warning",
            base.filter(status_reembolso=StatusReembolso.PENDENTE).count(),
        ),
        (
            "confirmadas",
            "Confirmadas no mês",
            "bx-check-circle",
            "success",
            base.filter(
                status=StatusCompra.CONFIRMADA,
                data_compra__year=hoje.year,
                data_compra__month=hoje.month,
            ).count(),
        ),
    )
    return [
        {
            "chave": chave,
            "titulo": titulo,
            "icone": icone,
            "tom": tom,
            "total": total,
            "ativo": chave == chip_ativo,
            "url": _url_do_chip(request, chave, chip_ativo),
        }
        for chave, titulo, icone, tom, total in definicao
    ]


def _url_do_chip(request, chave, chip_ativo) -> str:
    """O link do chip, preservando os filtros já aplicados na lista."""
    parametros = request.GET.copy()
    parametros.pop("page", None)
    if chave == chip_ativo:
        parametros.pop("chip", None)
    else:
        parametros["chip"] = chave
    consulta = parametros.urlencode()
    return f"?{consulta}" if consulta else "?"


def _pode_retirar_para_alguma(usuario, manutencoes, item) -> bool:
    """Diz se hÃ¡ alguma manutenÃ§Ã£o alcanÃ§Ã¡vel para a retirada deste item."""
    return any(
        pode_registrar_saida_para_manutencao(usuario, manutencao, item)
        for manutencao in manutencoes[:20]
    )


# -- TraduÃ§Ã£o para a tela -------------------------------------------------------


def _iniciais_do_item(item) -> dict:
    """O que o formulÃ¡rio jÃ¡ deve trazer quando o item Ã© aberto para ediÃ§Ã£o."""
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
    """O que o formulÃ¡rio jÃ¡ deve trazer quando a compra Ã© aberta para ediÃ§Ã£o."""
    return {
        "data_compra": compra.data_compra,
        "fornecedor": compra.fornecedor_id,
        "forma_pagamento": compra.forma_pagamento,
        "numero_documento": compra.numero_documento,
        "manutencao_origem": compra.manutencao_origem_id,
        "observacao": compra.observacao,
    }


def _iniciais_da_linha(linha) -> dict:
    """O que o formulÃ¡rio jÃ¡ deve trazer quando uma linha Ã© aberta para ediÃ§Ã£o."""
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
    """O nome pelo qual a pessoa Ã© reconhecida na tela."""
    return (
        getattr(usuario, "full_name", "")
        or getattr(usuario, "email", "")
        or "Sem responsÃ¡vel"
    )


def _dinheiro(valor) -> str:
    """O dinheiro como a gestÃ£o o lÃª, ou a ausÃªncia dele."""
    if valor is None:
        return "Sem valor"
    return f"R$ {number_format(valor, decimal_pos=2, force_grouping=True)}"


def _quantidade(valor, unidade: str = "") -> str:
    """A quantidade como a gestÃ£o a lÃª, sem zeros Ã  direita sem sentido."""
    if valor is None:
        return ""
    numero = number_format(valor, decimal_pos=3, force_grouping=False)
    numero = numero.rstrip("0").rstrip(",") if "," in numero else numero
    return f"{numero} {unidade}".strip()


def _data_por_extenso(valor) -> str:
    """A data como a gestÃ£o a lÃª, ou a ausÃªncia dela."""
    return valor.strftime("%d/%m/%Y") if valor else "Sem data"


def _momento_por_extenso(momento) -> str:
    """O momento como a gestÃ£o o lÃª: hoje, ontem ou a data, com a hora."""
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
    """A linha da compra confirmada mais recente que repÃ´s este item.

    Responde quanto se pagou da Ãºltima vez. Ã‰ leitura derivada: o item nÃ£o
    guarda preÃ§o, e a compra ainda rascunho nÃ£o conta como fato.
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
    """O item como a pÃ¡gina dele precisa: o cadastro, a leitura do saldo e o
    que se pagou por ele da Ãºltima vez."""
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
        .order_by("-criado_em")[:LIMITE_DE_MOVIMENTOS_NA_FICHA]
    )
    return [_movimento_para_tela(movimento, item) for movimento in movimentos]


def _movimento_para_tela(movimento, item) -> dict:
    """O movimento como a tela o lÃª: o que aconteceu, quanto e por quÃª."""
    origem = ""
    if movimento.manutencao_id:
        origem = f"ManutenÃ§Ã£o Â· {movimento.manutencao.titulo}"
    elif movimento.compra_item_id:
        origem = f"Compra Â· {_data_por_extenso(movimento.compra_item.compra.data_compra)}"

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


def _resumo_da_compra(compra) -> str:
    """O que a compra é, em uma linha: fornecedor, primeiro item ou observação."""
    if compra.fornecedor_id:
        return compra.fornecedor.nome_razao_social
    item = compra.itens.first()
    if item is not None:
        return item.descricao
    observacao = (compra.observacao or "").strip()
    if observacao:
        return observacao
    return "Rascunho sem dados"


def _compra_para_linha(compra, usuario=None) -> dict:
    """A compra como a linha da lista precisa dela."""
    total = getattr(compra, "total", None)
    if total is None:
        total = compra.total_calculado()
    return {
        "pk": compra.pk,
        "data": _data_por_extenso(compra.data_compra),
        "resumo": _resumo_da_compra(compra),
        "condominio": compra.condominio.nome,
        "fornecedor": (
            compra.fornecedor.nome_razao_social if compra.fornecedor else "Sem fornecedor"
        ),
        "documento": compra.numero_documento,
        "total": _dinheiro(total),
        "status": compra.get_status_display(),
        "status_tom": TOM_POR_STATUS_COMPRA.get(compra.status, "secondary"),
        "pode_cancelar": usuario is not None and pode_cancelar_compra(usuario, compra),
        "pode_excluir": usuario is not None and pode_excluir_compra(usuario, compra),
        "pode_marcar_reembolso": usuario is not None
        and pode_marcar_reembolso(usuario, compra),
        "pode_pagar_reembolso": usuario is not None
        and pode_registrar_reembolso(usuario, compra),
    }


def _compra_para_detalhe(compra) -> dict:
    """A compra como a pÃ¡gina dela precisa: o cabeÃ§alho e o total."""
    dados = _compra_para_linha(compra)
    dados.update(
        {
            "observacao": compra.observacao,
            "manutencao_origem": (
                compra.manutencao_origem.titulo if compra.manutencao_origem_id else ""
            ),
            "manutencao_origem_pk": compra.manutencao_origem_id,
            "demanda_origem": (
                compra.demanda_origem.titulo if compra.demanda_origem_id else ""
            ),
            "demanda_origem_pk": compra.demanda_origem_id,
            "confirmada_em": _momento_por_extenso(compra.confirmada_em),
            "criado_por": _nome_de(compra.criado_por),
            "criado_em": _momento_por_extenso(compra.criado_em),
            "forma_pagamento": compra.forma_pagamento,
            "comprada_por_outra_pessoa": compra.comprada_por_outra_pessoa,
            "precisa_reembolso": compra.precisa_reembolso,
            "reembolso_para": (
                _nome_de(compra.reembolso_para) if compra.reembolso_para_id else ""
            ),
            "valor_reembolso": (
                _dinheiro(compra.valor_reembolso) if compra.valor_reembolso else ""
            ),
            "observacao_reembolso": compra.observacao_reembolso,
            "status_reembolso": compra.get_status_reembolso_display()
            if compra.status_reembolso
            else "",
            "reembolsado_em": _momento_por_extenso(compra.reembolsado_em),
            "tem_fornecedor": compra.fornecedor_id is not None,
            "tem_data": compra.data_compra is not None,
            "tem_total": bool(compra.total_calculado()),
        }
    )
    return dados


def _linha_para_tela(linha) -> dict:
    """A linha da compra como a tela a lÃª: o que Ã©, quanto e para onde vai."""
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
    """O que esta compra movimentou, quando jÃ¡ foi confirmada."""
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

