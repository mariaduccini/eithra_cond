"""Telas da governança.

Contrato, garantia e documento são obrigações com prazo. A tela responde a três
perguntas: o que o condomínio assumiu, o que disso está para vencer e o que cada
obrigação cobre. Nada é guardado como estado — vencido, vencendo e em vigor são
lidos do prazo na hora, e por isso a lista nunca mostra uma situação atrasada.

Os catálogos de tipos continuam ligados a quem os usa, mas a navegação até eles
agora passa por "Tipos e categorias". O tipo não ganhou vida própria; ele só
deixou de disputar atenção com a ação principal das listas de contratos e
documentos.

A autorização é decidida no backend, sempre a partir do vínculo ativo da pessoa
com o condomínio. Quem não enxerga o módulo não é avisado de que a tela existe:
a resposta é 404. Quem enxerga e não responde pelo cadastro é recusado com 403 —
a existência já é legítima para essa pessoa, a decisão não é. A regra da
garantia que precisa dizer o que cobre é do domínio, e a tela apenas mostra, em
linguagem humana, o motivo da recusa.
"""
import uuid
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.formats import number_format
from django.views.decorators.http import require_POST

from apps.cadastros.models import Fornecedor
from apps.condominios import logo
from apps.condominios.acesso import condominios_do_usuario
from apps.core.paginacao import paginar_do_pedido
from apps.core.storage import ErroDeStorage

from . import arquivos, services
from .constants import SituacaoVigencia, StatusProjeto, TipoItemProjeto
from .forms import (
    ContratoForm,
    DocumentoForm,
    GarantiaForm,
    TipoContratoForm,
    TipoDocumentoForm,
)
from .models import (
    ContratoFornecedor,
    DocumentoOperacional,
    Garantia,
    Projeto,
    ProjetoItem,
    TipoContrato,
    TipoDocumento,
)
from .permissoes import (
    condominios_para_ver,
    pode_gerenciar_tipos,
    pode_operar,
    pode_ver_governanca,
)
from .vencimentos import agrupar_por_faixa, coletar_vencimentos

# Como cada situação de vigência aparece escrita na tela.
ROTULO_POR_SITUACAO = dict(SituacaoVigencia.choices)

# Como cada situação de projeto aparece na tela: o tom do selo e o ícone do
# toque que faz a entrega andar.
TOM_POR_STATUS_PROJETO = {
    StatusProjeto.EM_ESTUDO: "info",
    StatusProjeto.EM_ANDAMENTO: "primary",
    StatusProjeto.AGUARDANDO: "warning",
    StatusProjeto.CONCLUIDO: "success",
    StatusProjeto.CANCELADO: "secondary",
}
ICONE_POR_STATUS_PROJETO = {
    StatusProjeto.AGUARDANDO: "bx-time",
    StatusProjeto.EM_ESTUDO: "bx-search",
    StatusProjeto.EM_ANDAMENTO: "bx-loader-circle",
    StatusProjeto.CONCLUIDO: "bx-check-circle",
    StatusProjeto.CANCELADO: "bx-x-circle",
}


# -- Contratos ------------------------------------------------------------------


@login_required
def contratos(request):
    """Os contratos do condomínio em vista, do prazo mais próximo ao mais distante."""
    return _render_contratos(request)


@login_required
@require_POST
def criar_contrato(request):
    """Registra um contrato e o que ele cobre. Só quem opera o módulo cadastra."""
    atual = _condominio_para_operar(request)
    formulario = ContratoForm(request.POST, condominio=atual)
    if not formulario.is_valid():
        return _render_contratos(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    dados = formulario.cleaned_data
    try:
        services.criar_contrato(
            condominio=atual,
            criado_por=request.user,
            dados=_campos_do_modelo(ContratoFornecedor, dados),
            equipamentos=dados.get("equipamentos", ()),
            locais=dados.get("locais", ()),
            rotinas=dados.get("rotinas", ()),
        )
    except services.ErroDeGovernanca as erro:
        messages.error(request, str(erro))
        return _render_contratos(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    messages.success(request, "Contrato registrado.")
    return redirect(_destino("governanca:contratos", atual))


@login_required
@require_POST
def editar_contrato(request, pk):
    """Corrige um contrato e os objetos que ele cobre."""
    atual = _condominio_para_operar(request)
    contrato = _obter_ou_404(ContratoFornecedor.objects.filter(condominio=atual), pk)
    formulario = ContratoForm(request.POST, instance=contrato, condominio=atual)
    if not formulario.is_valid():
        return _render_contratos(
            request,
            formulario_edicao=formulario,
            item_edicao=contrato,
            modal="editar",
            status=400,
        )

    dados = formulario.cleaned_data
    try:
        services.editar_contrato(
            contrato=contrato,
            dados=_campos_do_modelo(ContratoFornecedor, dados),
            equipamentos=dados.get("equipamentos", ()),
            locais=dados.get("locais", ()),
            rotinas=dados.get("rotinas", ()),
        )
    except services.ErroDeGovernanca as erro:
        messages.error(request, str(erro))
        return _render_contratos(
            request,
            formulario_edicao=formulario,
            item_edicao=contrato,
            modal="editar",
            status=400,
        )

    messages.success(request, "Contrato atualizado.")
    return redirect(_destino("governanca:contratos", atual))


@login_required
@require_POST
def alternar_contrato(request, pk):
    """Encerra a vigência de um contrato, ou a devolve, sem apagar o registro."""
    atual = _condominio_para_operar(request)
    contrato = _obter_ou_404(ContratoFornecedor.objects.filter(condominio=atual), pk)
    novo = not contrato.ativo
    services.definir_ativo_contrato(contrato=contrato, ativo=novo)
    messages.success(request, "Contrato ativado." if novo else "Contrato desativado.")
    return redirect(_destino("governanca:contratos", atual))


def _render_contratos(
    request,
    *,
    formulario_criacao=None,
    formulario_edicao=None,
    item_edicao=None,
    modal=None,
    status=200,
):
    """Monta a lista de contratos com o formulário que a tela pediu."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    pode_operar_aqui = pode_operar(request.user, atual)

    if item_edicao is None and pode_operar_aqui:
        item_edicao = _item_em_edicao(request, ContratoFornecedor, base["condominios"])
    if item_edicao is None:
        if modal == "editar":
            modal = None
    elif modal is None:
        modal = "editar"

    pagina_contratos = paginar_do_pedido(
        _contratos_do_condominio(atual, base["termo"]), request.GET
    )

    contexto = {
        **base,
        "pode_operar": pode_operar_aqui,
        "contratos": [_contrato_para_tela(item) for item in pagina_contratos],
        "pagina_contratos": pagina_contratos,
        "formulario_contrato": formulario_criacao or ContratoForm(condominio=atual),
        "formulario_edicao": formulario_edicao
        or (ContratoForm(instance=item_edicao, condominio=atual) if item_edicao else None),
        "item_edicao": item_edicao,
        "modal_aberto": modal,
    }
    return render(request, "pages/governanca/contratos.html", contexto, status=status)


# -- Tipos de contrato ----------------------------------------------------------


@login_required
def tipos_de_contrato(request):
    """O vocabulário com que o condomínio classifica seus contratos."""
    return _render_tipos_de_contrato(request)


@login_required
@require_POST
def criar_tipo_contrato(request):
    """Registra um tipo novo. O catálogo é de quem responde pelo condomínio."""
    atual = _condominio_para_gerenciar(request)
    formulario = TipoContratoForm(request.POST)
    if not formulario.is_valid():
        return _render_tipos_de_contrato(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    try:
        services.criar_tipo_contrato(
            condominio=atual, criado_por=request.user, dados=formulario.cleaned_data
        )
    except services.ErroDeGovernanca as erro:
        messages.error(request, str(erro))
        return _render_tipos_de_contrato(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    messages.success(request, "Tipo de contrato cadastrado.")
    return redirect(_destino("governanca:tipos_de_contrato", atual))


@login_required
@require_POST
def editar_tipo_contrato(request, pk):
    """Corrige o nome e a descrição de um tipo de contrato."""
    atual = _condominio_para_gerenciar(request)
    tipo = _obter_ou_404(TipoContrato.objects.filter(condominio=atual), pk)
    formulario = TipoContratoForm(request.POST, instance=tipo)
    if not formulario.is_valid():
        return _render_tipos_de_contrato(
            request,
            formulario_edicao=formulario,
            item_edicao=tipo,
            modal="editar",
            status=400,
        )

    try:
        services.editar_tipo_contrato(tipo=tipo, dados=formulario.cleaned_data)
    except services.ErroDeGovernanca as erro:
        messages.error(request, str(erro))
        return _render_tipos_de_contrato(
            request,
            formulario_edicao=formulario,
            item_edicao=tipo,
            modal="editar",
            status=400,
        )

    messages.success(request, "Tipo de contrato atualizado.")
    return redirect(_destino("governanca:tipos_de_contrato", atual))


@login_required
@require_POST
def alternar_tipo_contrato(request, pk):
    """Tira um tipo de circulação, ou o devolve, sem apagar o que já o usa."""
    atual = _condominio_para_gerenciar(request)
    tipo = _obter_ou_404(TipoContrato.objects.filter(condominio=atual), pk)
    return _alternar(
        request, tipo, destino="governanca:tipos_de_contrato", nome="Tipo de contrato"
    )


def _render_tipos_de_contrato(
    request,
    *,
    formulario_criacao=None,
    formulario_edicao=None,
    item_edicao=None,
    modal=None,
    status=200,
):
    """Monta a lista de tipos com o formulário que a tela pediu."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    pode_gerenciar = pode_gerenciar_tipos(request.user, atual)

    if item_edicao is None and pode_gerenciar:
        item_edicao = _item_em_edicao(request, TipoContrato, base["condominios"])
    if item_edicao is None:
        if modal == "editar":
            modal = None
    elif modal is None:
        modal = "editar"

    contexto = {
        **base,
        "pode_gerenciar": pode_gerenciar,
        "tipos": _tipos_para_tela(TipoContrato.objects.do_condominio(atual)),
        "formulario_tipo": formulario_criacao or TipoContratoForm(),
        "formulario_edicao": formulario_edicao
        or (TipoContratoForm(instance=item_edicao) if item_edicao else None),
        "item_edicao": item_edicao,
        "modal_aberto": modal,
    }
    return render(
        request, "pages/governanca/tipos_de_contrato.html", contexto, status=status
    )


# -- Garantias ------------------------------------------------------------------


@login_required
def garantias(request):
    """As garantias do condomínio em vista, do prazo mais próximo ao mais distante."""
    return _render_garantias(request)


@login_required
@require_POST
def criar_garantia(request):
    """Registra uma garantia e o que ela cobre, numa transação só."""
    atual = _condominio_para_operar(request)
    formulario = GarantiaForm(request.POST, condominio=atual)
    if not formulario.is_valid():
        return _render_garantias(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    dados = formulario.cleaned_data
    try:
        services.criar_garantia(
            condominio=atual,
            criado_por=request.user,
            dados=_campos_do_modelo(Garantia, dados),
            equipamentos=dados.get("equipamentos", ()),
            manutencoes=dados.get("manutencoes", ()),
            itens_de_compra=dados.get("itens_de_compra", ()),
        )
    except services.ErroDeGovernanca as erro:
        messages.error(request, str(erro))
        return _render_garantias(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    messages.success(request, "Garantia registrada.")
    return redirect(_destino("governanca:garantias", atual))


@login_required
@require_POST
def editar_garantia(request, pk):
    """Corrige uma garantia e o que ela cobre."""
    atual = _condominio_para_operar(request)
    garantia = _obter_ou_404(Garantia.objects.filter(condominio=atual), pk)
    formulario = GarantiaForm(request.POST, instance=garantia, condominio=atual)
    if not formulario.is_valid():
        return _render_garantias(
            request,
            formulario_edicao=formulario,
            item_edicao=garantia,
            modal="editar",
            status=400,
        )

    dados = formulario.cleaned_data
    try:
        services.editar_garantia(
            garantia=garantia,
            dados=_campos_do_modelo(Garantia, dados),
            equipamentos=dados.get("equipamentos", ()),
            manutencoes=dados.get("manutencoes", ()),
            itens_de_compra=dados.get("itens_de_compra", ()),
        )
    except services.ErroDeGovernanca as erro:
        messages.error(request, str(erro))
        return _render_garantias(
            request,
            formulario_edicao=formulario,
            item_edicao=garantia,
            modal="editar",
            status=400,
        )

    messages.success(request, "Garantia atualizada.")
    return redirect(_destino("governanca:garantias", atual))


@login_required
@require_POST
def alternar_garantia(request, pk):
    """Devolve a garantia ao uso, ou a retira — desde que ela diga o que cobre."""
    atual = _condominio_para_operar(request)
    garantia = _obter_ou_404(Garantia.objects.filter(condominio=atual), pk)
    novo = not garantia.ativo
    try:
        services.definir_ativo_garantia(garantia=garantia, ativo=novo)
    except services.ErroDeGovernanca as erro:
        messages.error(request, str(erro))
        return redirect(_destino("governanca:garantias", atual))

    messages.success(request, "Garantia ativada." if novo else "Garantia desativada.")
    return redirect(_destino("governanca:garantias", atual))


def _render_garantias(
    request,
    *,
    formulario_criacao=None,
    formulario_edicao=None,
    item_edicao=None,
    modal=None,
    status=200,
):
    """Monta a lista de garantias com o formulário que a tela pediu."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    pode_operar_aqui = pode_operar(request.user, atual)

    if item_edicao is None and pode_operar_aqui:
        item_edicao = _item_em_edicao(request, Garantia, base["condominios"])
    if item_edicao is None:
        if modal == "editar":
            modal = None
    elif modal is None:
        modal = "editar"

    pagina_garantias = paginar_do_pedido(
        _garantias_do_condominio(atual, base["termo"]), request.GET
    )

    contexto = {
        **base,
        "pode_operar": pode_operar_aqui,
        "garantias": [_garantia_para_tela(item) for item in pagina_garantias],
        "pagina_garantias": pagina_garantias,
        "formulario_garantia": formulario_criacao or GarantiaForm(condominio=atual),
        "formulario_edicao": formulario_edicao
        or (GarantiaForm(instance=item_edicao, condominio=atual) if item_edicao else None),
        "item_edicao": item_edicao,
        "modal_aberto": modal,
    }
    return render(request, "pages/governanca/garantias.html", contexto, status=status)


# -- Documentos -----------------------------------------------------------------


@login_required
def documentos(request):
    """Os documentos do condomínio em vista, do que vence antes ao que não vence."""
    return _render_documentos(request)


@login_required
@require_POST
def criar_documento(request):
    """Registra um documento e os contextos em que ele vale."""
    atual = _condominio_para_operar(request)
    formulario = DocumentoForm(request.POST, condominio=atual)
    if not formulario.is_valid():
        return _render_documentos(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    dados = formulario.cleaned_data
    try:
        services.criar_documento(
            condominio=atual,
            criado_por=request.user,
            dados=_campos_do_modelo(DocumentoOperacional, dados),
            fornecedores=dados.get("fornecedores", ()),
            contratos=dados.get("contratos", ()),
            equipamentos=dados.get("equipamentos", ()),
            garantias=dados.get("garantias", ()),
        )
    except services.ErroDeGovernanca as erro:
        messages.error(request, str(erro))
        return _render_documentos(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    messages.success(request, "Documento registrado.")
    return redirect(_destino("governanca:documentos", atual))


@login_required
@require_POST
def editar_documento(request, pk):
    """Corrige um documento e os contextos em que ele vale."""
    atual = _condominio_para_operar(request)
    documento = _obter_ou_404(DocumentoOperacional.objects.filter(condominio=atual), pk)
    formulario = DocumentoForm(request.POST, instance=documento, condominio=atual)
    if not formulario.is_valid():
        return _render_documentos(
            request,
            formulario_edicao=formulario,
            item_edicao=documento,
            modal="editar",
            status=400,
        )

    dados = formulario.cleaned_data
    try:
        services.editar_documento(
            documento=documento,
            dados=_campos_do_modelo(DocumentoOperacional, dados),
            fornecedores=dados.get("fornecedores", ()),
            contratos=dados.get("contratos", ()),
            equipamentos=dados.get("equipamentos", ()),
            garantias=dados.get("garantias", ()),
        )
    except services.ErroDeGovernanca as erro:
        messages.error(request, str(erro))
        return _render_documentos(
            request,
            formulario_edicao=formulario,
            item_edicao=documento,
            modal="editar",
            status=400,
        )

    messages.success(request, "Documento atualizado.")
    return redirect(_destino("governanca:documentos", atual))


@login_required
@require_POST
def alternar_documento(request, pk):
    """Tira um documento de circulação, ou o devolve, sem apagar o registro."""
    atual = _condominio_para_operar(request)
    documento = _obter_ou_404(DocumentoOperacional.objects.filter(condominio=atual), pk)
    return _alternar(
        request, documento, destino="governanca:documentos", nome="Documento"
    )


@login_required
@require_POST
def anexar_arquivo(request, pk):
    """Guarda o arquivo que comprova um documento deste condomínio.

    O arquivo não altera o documento: ele sustenta o que já está registrado. Por
    isso o erro do domínio volta como frase, no próprio modal, e o documento
    continua como a gestão o deixou.
    """
    atual = _condominio_para_operar(request)
    documento = _obter_ou_404(DocumentoOperacional.objects.filter(condominio=atual), pk)

    try:
        arquivos.anexar_arquivo(
            documento=documento,
            arquivo=request.FILES.get("arquivo"),
            usuario=request.user,
        )
    except (arquivos.ErroDeArquivo, ErroDeStorage) as erro:
        return _render_documentos(
            request,
            item_arquivos=documento,
            erro_arquivo=str(erro),
            modal="arquivos",
            status=400,
        )

    messages.success(request, "Arquivo anexado.")
    return redirect(_destino_dos_arquivos(atual, documento))


def _render_documentos(
    request,
    *,
    formulario_criacao=None,
    formulario_edicao=None,
    item_edicao=None,
    item_arquivos=None,
    erro_arquivo="",
    modal=None,
    status=200,
):
    """Monta a lista de documentos com o formulário que a tela pediu."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    pode_operar_aqui = pode_operar(request.user, atual)

    if item_edicao is None and pode_operar_aqui:
        item_edicao = _item_pedido(
            request, DocumentoOperacional, base["condominios"], abrir="editar"
        )
    if item_edicao is None:
        if modal == "editar":
            modal = None
    elif modal is None:
        modal = "editar"

    if item_arquivos is None and pode_operar_aqui:
        item_arquivos = _item_pedido(
            request, DocumentoOperacional, base["condominios"], abrir="arquivos"
        )
    if item_arquivos is None:
        if modal == "arquivos":
            modal = None
    elif modal is None:
        modal = "arquivos"

    pagina_documentos = paginar_do_pedido(
        _documentos_do_condominio(atual, base["termo"]), request.GET
    )

    contexto = {
        **base,
        "pode_operar": pode_operar_aqui,
        "documentos": [_documento_para_tela(item) for item in pagina_documentos],
        "pagina_documentos": pagina_documentos,
        "formulario_documento": formulario_criacao or DocumentoForm(condominio=atual),
        "formulario_edicao": formulario_edicao
        or (DocumentoForm(instance=item_edicao, condominio=atual) if item_edicao else None),
        "item_edicao": item_edicao,
        "item_arquivos": item_arquivos,
        "arquivos_do_documento": (
            [arquivos.resumo_do_arquivo(item) for item in item_arquivos.arquivos.all()]
            if item_arquivos
            else []
        ),
        "limite_de_arquivo": arquivos.limite_legivel(),
        "erro_arquivo": erro_arquivo,
        "modal_aberto": modal,
    }
    return render(request, "pages/governanca/documentos.html", contexto, status=status)


# -- Tipos de documento ---------------------------------------------------------


@login_required
def tipos_de_documento(request):
    """O vocabulário dos documentos, com a regra de validade de cada tipo."""
    return _render_tipos_de_documento(request)


@login_required
@require_POST
def criar_tipo_documento(request):
    """Registra um tipo de documento novo no condomínio."""
    atual = _condominio_para_gerenciar(request)
    formulario = TipoDocumentoForm(request.POST)
    if not formulario.is_valid():
        return _render_tipos_de_documento(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    try:
        services.criar_tipo_documento(
            condominio=atual, criado_por=request.user, dados=formulario.cleaned_data
        )
    except services.ErroDeGovernanca as erro:
        messages.error(request, str(erro))
        return _render_tipos_de_documento(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    messages.success(request, "Tipo de documento cadastrado.")
    return redirect(_destino("governanca:tipos_de_documento", atual))


@login_required
@require_POST
def editar_tipo_documento(request, pk):
    """Corrige um tipo de documento, inclusive a sua regra de validade."""
    atual = _condominio_para_gerenciar(request)
    tipo = _obter_ou_404(TipoDocumento.objects.filter(condominio=atual), pk)
    formulario = TipoDocumentoForm(request.POST, instance=tipo)
    if not formulario.is_valid():
        return _render_tipos_de_documento(
            request,
            formulario_edicao=formulario,
            item_edicao=tipo,
            modal="editar",
            status=400,
        )

    try:
        services.editar_tipo_documento(tipo=tipo, dados=formulario.cleaned_data)
    except services.ErroDeGovernanca as erro:
        messages.error(request, str(erro))
        return _render_tipos_de_documento(
            request,
            formulario_edicao=formulario,
            item_edicao=tipo,
            modal="editar",
            status=400,
        )

    messages.success(request, "Tipo de documento atualizado.")
    return redirect(_destino("governanca:tipos_de_documento", atual))


@login_required
@require_POST
def alternar_tipo_documento(request, pk):
    """Tira um tipo de circulação, ou o devolve, sem apagar o que já o usa."""
    atual = _condominio_para_gerenciar(request)
    tipo = _obter_ou_404(TipoDocumento.objects.filter(condominio=atual), pk)
    return _alternar(
        request, tipo, destino="governanca:tipos_de_documento", nome="Tipo de documento"
    )


def _render_tipos_de_documento(
    request,
    *,
    formulario_criacao=None,
    formulario_edicao=None,
    item_edicao=None,
    modal=None,
    status=200,
):
    """Monta a lista de tipos de documento com o formulário que a tela pediu."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    pode_gerenciar = pode_gerenciar_tipos(request.user, atual)

    if item_edicao is None and pode_gerenciar:
        item_edicao = _item_em_edicao(request, TipoDocumento, base["condominios"])
    if item_edicao is None:
        if modal == "editar":
            modal = None
    elif modal is None:
        modal = "editar"

    tipos = TipoDocumento.objects.do_condominio(atual)
    contexto = {
        **base,
        "pode_gerenciar": pode_gerenciar,
        "tipos": [
            {
                "pk": tipo.pk,
                "nome": tipo.nome,
                "exige_validade": tipo.exige_validade,
                "antecedencia": tipo.antecedencia_alerta_padrao_dias,
                "descricao": tipo.descricao,
                **_situacao_do_cadastro(tipo.ativo),
            }
            for tipo in tipos
        ],
        "formulario_tipo": formulario_criacao or TipoDocumentoForm(),
        "formulario_edicao": formulario_edicao
        or (TipoDocumentoForm(instance=item_edicao) if item_edicao else None),
        "item_edicao": item_edicao,
        "modal_aberto": modal,
    }
    return render(
        request, "pages/governanca/tipos_de_documento.html", contexto, status=status
    )


# -- Vencimentos ----------------------------------------------------------------


@login_required
def vencimentos(request):
    """Tudo o que vence neste condomínio, contrato, garantia e documento juntos.

    A tela não decide a urgência: pede ao domínio de vencimentos a leitura já
    classificada e apenas apresenta. Quem enxerga a governança consulta — a
    leitura é a mesma para quem responde e para quem supervisiona.
    """
    base = _base_da_pagina(request)
    itens = coletar_vencimentos(base["condominio_atual"])
    contexto = {
        **base,
        "faixas": agrupar_por_faixa(itens),
        "total": len(itens),
    }
    return render(request, "pages/governanca/vencimentos.html", contexto)


# -- Projetos -------------------------------------------------------------------
#
# A obra tocada aos poucos: o AVCB, a troca de bombas, a reforma do hall. A tela
# responde a duas perguntas — quanto já se pensou gastar e quanto falta —, e
# deixa a linha da entrega ser corrigida no lugar onde ela é lida. O progresso e
# os totais não são guardados: são lidos do que as entregas dizem agora.


@login_required
def projetos(request):
    """Os projetos do condomínio em vista."""
    visiveis = _condominios_dos_projetos(request)
    atual = _condominio_atual(request, visiveis)
    return _render_projetos(request, visiveis, atual)


@login_required
def projeto(request, pk):
    """Um projeto, suas etapas, as entregas de cada uma e as demandas ligadas."""
    visiveis = _condominios_dos_projetos(request)
    alvo = _obter_ou_404(Projeto.objects.filter(condominio__in=visiveis), pk)
    return _render_projeto(request, visiveis, alvo)


@login_required
@require_POST
def avancar_item(request, pk, item_pk):
    """O toque que faz a entrega andar um passo no ciclo do andamento."""
    alvo, item = _item_para_operar(request, pk, item_pk)
    services.avancar_status_item(item=item)
    messages.success(request, "Situação da entrega atualizada.")
    return redirect("projetos:detalhe", pk=alvo.pk)


@login_required
@require_POST
def cancelar_item(request, pk, item_pk):
    """Tira a entrega do ciclo, sem apagar a linha."""
    alvo, item = _item_para_operar(request, pk, item_pk)
    services.cancelar_item(item=item)
    messages.success(request, "Entrega cancelada.")
    return redirect("projetos:detalhe", pk=alvo.pk)


@login_required
@require_POST
def editar_valores_item(request, pk, item_pk):
    """Grava, na própria linha, o previsto, o contratado e o fornecedor."""
    alvo, item = _item_para_operar(request, pk, item_pk)
    try:
        services.definir_valores_item(
            item=item,
            valor_previsto=_decimal_ou_none(request.POST.get("valor_previsto")),
            valor_contratado=_decimal_ou_none(request.POST.get("valor_contratado")),
            fornecedor=_fornecedor_do_projeto(request.POST.get("fornecedor"), alvo),
        )
    except services.ErroDeGovernanca as erro:
        messages.error(request, str(erro))
        return redirect("projetos:detalhe", pk=alvo.pk)
    messages.success(request, "Valores da entrega atualizados.")
    return redirect("projetos:detalhe", pk=alvo.pk)


@login_required
@require_POST
def abrir_demanda(request, pk, item_pk):
    """Abre, nesta etapa, uma demanda já ligada ao projeto."""
    alvo, item = _item_para_operar(request, pk, item_pk)
    try:
        demanda = services.abrir_demanda_na_etapa(
            projeto=alvo,
            etapa=item,
            criado_por=request.user,
            titulo=request.POST.get("titulo", ""),
        )
    except services.ErroDeGovernanca as erro:
        messages.error(request, str(erro))
        return redirect("projetos:detalhe", pk=alvo.pk)
    messages.success(request, "Demanda aberta e ligada ao projeto.")
    return redirect("demandas:detalhe", pk=demanda.pk)


def _render_projetos(request, visiveis, atual):
    """A lista: nome, situação, progresso e os dois totais de cada projeto."""
    linhas = [
        _projeto_para_tela(projeto)
        for projeto in Projeto.objects.do_condominio(atual).ativos()
    ]
    contexto = {**_base_dos_projetos(visiveis, atual), "projetos": linhas}
    return render(request, "pages/governanca/projetos.html", contexto)


def _render_projeto(request, visiveis, alvo):
    """O detalhe: o topo com os totais, as etapas e as demandas ligadas."""
    contexto = {
        **_base_dos_projetos(visiveis, alvo.condominio),
        "projeto": _projeto_para_tela(alvo),
        "etapas": _etapas_para_tela(alvo),
        "demandas": _demandas_do_projeto(alvo),
        "fornecedores": Fornecedor.objects.filter(
            condominio=alvo.condominio
        ).order_by("nome_razao_social"),
    }
    return render(request, "pages/governanca/projeto.html", contexto)


def _base_dos_projetos(visiveis, atual) -> dict:
    """O que as telas de projeto precisam: o condomínio em vista e o seletor."""
    return {
        "condominios": visiveis,
        "condominio_atual": atual,
        "logo_do_condominio": logo.endereco(atual),
        "mostrar_seletor": visiveis.count() > 1,
    }


# -- Autorização e contexto -----------------------------------------------------
#
# Toda pergunta passa pelo domínio. Quem não enxerga o módulo recebe 404 — não
# descobre que a tela existe. Quem enxerga e não responde pelo cadastro recebe
# 403 ao tentar alterar. Esconder o botão na tela não substitui estas checagens.


def _condominios_para_ver(request):
    """Condomínios cujas obrigações esta pessoa consulta, ou 404."""
    condominios = condominios_para_ver(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomínio disponível.")
    return condominios


def _condominio_atual(request, condominios):
    """Qual condomínio a tela mostra agora.

    Com um só, é ele. Com vários, vale o escolhido no endereço — sempre
    conferido contra a lista autorizada, nunca aceito pelo que diz. Sem escolha,
    o primeiro da lista, e a tela oferece o seletor para trocar.
    """
    escolhido = request.POST.get("condominio") or request.GET.get("condominio")
    if not escolhido:
        return condominios.first()
    if not _eh_uuid(escolhido):
        raise Http404("Condomínio não encontrado.")
    return _obter_ou_404(condominios, escolhido)


def _condominio_para_operar(request):
    """O condomínio em que esta pessoa mantém obrigações agora.

    Quem não enxerga o módulo recebe 404 — não descobre que a tela existe. Quem
    enxerga e não responde pelo registro recebe 403 — a existência já é
    legítima para essa pessoa, a decisão não é.
    """
    visualizaveis = _condominios_para_ver(request)
    atual = _condominio_atual(request, visualizaveis)
    if not pode_operar(request.user, atual):
        raise PermissionDenied(
            "Esta pessoa não mantém contratos, garantias ou documentos deste condomínio."
        )
    return atual


def _condominio_para_gerenciar(request):
    """O condomínio cujo catálogo de tipos esta pessoa mantém agora."""
    visualizaveis = _condominios_para_ver(request)
    atual = _condominio_atual(request, visualizaveis)
    if not pode_gerenciar_tipos(request.user, atual):
        raise PermissionDenied(
            "Esta pessoa não mantém o catálogo de tipos deste condomínio."
        )
    return atual


def _base_da_pagina(request):
    """O que toda tela da governança precisa: os condomínios, qual está em vista
    e o termo já procurado, quando houver."""
    condominios = _condominios_para_ver(request)
    atual = _condominio_atual(request, condominios)
    return {
        "condominios": condominios,
        "condominio_atual": atual,
        "logo_do_condominio": logo.endereco(atual),
        "mostrar_seletor": condominios.count() > 1,
        "termo": (request.GET.get("q") or "").strip(),
    }


def _item_pedido(request, modelo, condominios, *, abrir: str):
    """O registro que um modal deve abrir, quando a tela pede um."""
    if request.GET.get("abrir") != abrir:
        return None
    pk = request.GET.get("item")
    if not pk or not _eh_uuid(pk):
        return None
    return _obter_ou_404(modelo.objects.filter(condominio__in=condominios), pk)


def _item_em_edicao(request, modelo, condominios):
    """O registro que o modal de edição deve abrir, quando a tela pede um."""
    return _item_pedido(request, modelo, condominios, abrir="editar")


def _obter_ou_404(queryset, pk):
    """Resolve um registro pela chave, ou 404 — inclusive se a chave vier torta."""
    try:
        return get_object_or_404(queryset, pk=pk)
    except (ValidationError, ValueError, TypeError):
        raise Http404("Registro não encontrado.") from None


def _eh_uuid(valor) -> bool:
    """Diz se o valor recebido pode ser uma chave de registro."""
    try:
        uuid.UUID(str(valor))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def _condominios_dos_projetos(request):
    """Os condomínios cujos projetos esta pessoa acompanha, ou a recusa devida.

    Quem responde pelo condomínio e quem supervisiona acompanham a obra; o
    morador acessa o prédio, mas não os projetos. Por isso a fronteira aqui não
    é o acesso: quem acessa o condomínio e não responde por ele recebe 403 — a
    tela existe para essa pessoa, a decisão não é dela. Quem não acessa
    condomínio nenhum recebe 404.
    """
    if not condominios_do_usuario(request.user).exists():
        raise Http404("Nenhum condomínio disponível.")
    visiveis = condominios_para_ver(request.user)
    if not visiveis.exists():
        raise PermissionDenied(
            "Esta pessoa não acompanha os projetos deste condomínio."
        )
    return visiveis


def _projeto_para_operar(request, pk):
    """O projeto que esta pessoa mantém agora, ou a recusa devida."""
    visiveis = _condominios_dos_projetos(request)
    alvo = _obter_ou_404(Projeto.objects.filter(condominio__in=visiveis), pk)
    if not pode_operar(request.user, alvo.condominio):
        raise PermissionDenied("Esta pessoa não mantém os projetos deste condomínio.")
    return alvo


def _item_para_operar(request, pk, item_pk):
    """A entrega ou etapa que esta pessoa mantém, dentro de um projeto que mantém."""
    alvo = _projeto_para_operar(request, pk)
    item = _obter_ou_404(ProjetoItem.objects.filter(projeto=alvo), item_pk)
    return alvo, item


def _decimal_ou_none(valor):
    """Lê um valor digitado na linha. Vazio é vazio; a vírgula vira ponto."""
    texto = (valor or "").strip()
    if not texto:
        return None
    normalizado = texto.replace(".", "").replace(",", ".") if "," in texto else texto
    try:
        return Decimal(normalizado)
    except (InvalidOperation, ValueError) as erro:
        raise services.ErroDeGovernanca("O valor informado não é um número.") from erro


def _fornecedor_do_projeto(valor, projeto):
    """O fornecedor escolhido na linha, se houver; recusa o de outro prédio."""
    if not valor:
        return None
    fornecedor = None
    if _eh_uuid(valor):
        fornecedor = Fornecedor.objects.filter(
            condominio=projeto.condominio, pk=valor
        ).first()
    if fornecedor is None:
        raise services.ErroDeGovernanca(
            "O fornecedor escolhido não é deste condomínio."
        )
    return fornecedor


def _destino(view_name, condominio) -> str:
    """Volta para a tela preservando o condomínio em vista."""
    return f"{reverse(view_name)}?condominio={condominio.pk}"


def _destino_dos_arquivos(condominio, documento) -> str:
    """Volta para a lista com os arquivos deste documento abertos."""
    return (
        f"{_destino('governanca:documentos', condominio)}"
        f"&abrir=arquivos&item={documento.pk}"
    )


def _alternar(request, objeto, *, destino, nome):
    """Ativa ou desativa um cadastro e volta para a lista."""
    novo = not objeto.ativo
    services.definir_ativo_tipo(tipo=objeto, ativo=novo)
    messages.success(request, f"{nome} ativado." if novo else f"{nome} desativado.")
    return redirect(_destino(destino, objeto.condominio))


def _campos_do_modelo(modelo, dados: dict) -> dict:
    """O que o formulário validou que é campo do modelo.

    Os campos de escolha múltipla — equipamentos cobertos, contextos do
    documento — não são colunas: eles viram linhas de vínculo, e por isso são
    entregues ao domínio à parte, nunca como atributo do registro.
    """
    nomes = {campo.name for campo in modelo._meta.fields}
    return {campo: valor for campo, valor in dados.items() if campo in nomes}


# -- Tradução para a tela -------------------------------------------------------


def _situacao_do_cadastro(ativo: bool) -> dict:
    """Como o estado de um cadastro deve ser lido e usado na lista."""
    return {
        "ativo": ativo,
        "texto": "Ativo" if ativo else "Inativo",
        "tom": "success" if ativo else "secondary",
    }


def _situacao_para_tela(objeto) -> dict:
    """A vigência do registro em palavras humanas, com o tom do badge."""
    situacao = objeto.situacao
    return {
        "situacao": situacao,
        "texto": ROTULO_POR_SITUACAO.get(situacao, ""),
        "tom": objeto.tom,
    }


def _periodo(inicio, fim) -> str:
    """O prazo do registro como a lista o mostra, em uma linha.

    Prazo indeterminado é uma resposta legítima, e não um dado faltando: a
    lista diz que não há término informado em vez de deixar a célula pela
    metade.
    """
    if not inicio:
        return ""
    if fim is None:
        return f"Desde {inicio.strftime('%d/%m/%Y')} · sem término informado"
    return f"{inicio.strftime('%d/%m/%Y')} → {fim.strftime('%d/%m/%Y')}"


def _moeda(valor) -> str:
    """O valor operacional como a lista o mostra. Sem valor, nada aparece."""
    if valor is None:
        return ""
    return f"R$ {number_format(valor, decimal_pos=2, force_grouping=True)}"


# -- Projetos na tela -----------------------------------------------------------


def _projeto_para_tela(projeto) -> dict:
    """O projeto como a lista e o topo do detalhe o leem."""
    return {
        "pk": projeto.pk,
        "nome": projeto.nome,
        "identificacao": projeto.identificacao,
        "descricao": projeto.descricao,
        "status": projeto.get_status_display(),
        "status_valor": projeto.status,
        "status_tom": TOM_POR_STATUS_PROJETO.get(projeto.status, "secondary"),
        "progresso": projeto.progresso,
        "previsto_total": _moeda(projeto.previsto_total),
        "contratado_total": _moeda(projeto.contratado_total),
        "entregas_concluidas": projeto.entregas_concluidas,
        "total_de_entregas": projeto.total_de_entregas,
    }


def _soma_de_valores(itens, campo) -> Decimal:
    """Soma um valor nas linhas, ignorando o que ainda não tem resposta."""
    return sum(
        (getattr(item, campo) or Decimal("0") for item in itens), Decimal("0")
    )


def _valor_editavel(valor) -> str:
    """O valor como o campo de edição o recebe. Vazio continua vazio."""
    if valor is None:
        return ""
    return f"{valor:.2f}"


def _entrega_para_tela(entrega) -> dict:
    """Uma entrega: o nome, o selo do andamento e o que se edita na linha."""
    return {
        "pk": entrega.pk,
        "nome": entrega.nome,
        "descricao": entrega.descricao,
        "status": entrega.get_status_display(),
        "status_valor": entrega.status,
        "icone": ICONE_POR_STATUS_PROJETO.get(entrega.status, "bx-circle"),
        "tom": TOM_POR_STATUS_PROJETO.get(entrega.status, "secondary"),
        "valor_previsto": _valor_editavel(entrega.valor_previsto),
        "valor_contratado": _valor_editavel(entrega.valor_contratado),
        "fornecedor": entrega.fornecedor_id,
        "cancelada": entrega.status == StatusProjeto.CANCELADO,
        "projeto_pk": entrega.projeto_id,
        "url_avancar": reverse(
            "projetos:avancar_item", args=[entrega.projeto_id, entrega.pk]
        ),
        "url_cancelar": reverse(
            "projetos:cancelar_item", args=[entrega.projeto_id, entrega.pk]
        ),
        "url_valores": reverse(
            "projetos:editar_valores_item", args=[entrega.projeto_id, entrega.pk]
        ),
    }


def _etapa_para_tela(etapa, entregas) -> dict:
    """Uma etapa: o nome, o subtotal dos valores e as suas entregas."""
    concluidas = sum(1 for e in entregas if e.status == StatusProjeto.CONCLUIDO)
    previsto = _soma_de_valores(entregas, "valor_previsto") + (
        etapa.valor_previsto or Decimal("0")
    )
    contratado = _soma_de_valores(entregas, "valor_contratado") + (
        etapa.valor_contratado or Decimal("0")
    )
    return {
        "pk": etapa.pk,
        "nome": etapa.nome,
        "descricao": etapa.descricao,
        "entregas": [_entrega_para_tela(entrega) for entrega in entregas],
        "total": len(entregas),
        "concluidas": concluidas,
        "previsto_subtotal": _moeda(previsto),
        "contratado_subtotal": _moeda(contratado),
        "url_demanda": reverse(
            "projetos:abrir_demanda", args=[etapa.projeto_id, etapa.pk]
        ),
    }


def _etapas_para_tela(projeto) -> list[dict]:
    """As etapas do projeto, cada uma com as suas entregas e o subtotal."""
    itens = list(
        projeto.itens.select_related("fornecedor").order_by("ordem", "nome")
    )
    entregas_por_etapa = {}
    etapas = []
    for item in itens:
        if item.tipo == TipoItemProjeto.ETAPA:
            etapas.append(item)
        elif item.parent_id:
            entregas_por_etapa.setdefault(item.parent_id, []).append(item)
    return [
        _etapa_para_tela(etapa, entregas_por_etapa.get(etapa.pk, []))
        for etapa in etapas
    ]


def _demandas_do_projeto(projeto) -> list[dict]:
    """As demandas ligadas ao projeto, com o link para cada uma."""
    return [
        {
            "pk": vinculo.demanda_id,
            "titulo": vinculo.demanda.titulo,
            "status": vinculo.demanda.get_status_display(),
            "url": reverse("demandas:detalhe", args=[vinculo.demanda_id]),
        }
        for vinculo in projeto.demandas_vinculadas.select_related("demanda")
    ]


def _classificacao_do_contrato(contrato) -> str:
    """Como o contrato se identifica além do objeto: tipo e número, se houver.

    Os dois são informação de apoio da linha, e não colunas: o tipo sozinho
    raramente decide algo na lista, e o número serve para achar o documento
    quando alguém já sabe qual procura.
    """
    partes = []
    if contrato.tipo_contrato_id:
        partes.append(contrato.tipo_contrato.nome)
    if contrato.numero_referencia:
        partes.append(f"Ref. {contrato.numero_referencia}")
    return " · ".join(partes)


def _contagem(quantidade: int, singular: str, plural: str) -> str:
    """Uma contagem em linguagem humana: "2 equipamentos", "1 manutenção"."""
    return f"{quantidade} {singular if quantidade == 1 else plural}"


def _descricao_da_cobertura(equipamentos: int, locais: int, rotinas: int) -> str:
    """O que uma obrigação cobre, em uma linha. Vazio quando não cobre nada."""
    partes = []
    if equipamentos:
        partes.append(_contagem(equipamentos, "equipamento", "equipamentos"))
    if locais:
        partes.append(_contagem(locais, "local", "locais"))
    if rotinas:
        partes.append(_contagem(rotinas, "preventiva", "preventivas"))
    return ", ".join(partes)


def _contratos_do_condominio(condominio, termo=""):
    """Os contratos do condomínio, na ordem em que a atenção é pedida.

    O termo estreita o conjunto antes de ordenar e paginar: o que a pessoa
    procurou vale para o que ela vê, e não para uma página já recortada.
    """
    return (
        ContratoFornecedor.objects.filter(condominio=condominio)
        .buscar(termo)
        .select_related("fornecedor", "tipo_contrato")
        .prefetch_related(
            "equipamentos_vinculados", "locais_vinculados", "rotinas_vinculadas"
        )
        .na_ordem_de_atencao()
    )


def _contrato_para_tela(contrato) -> dict:
    """O contrato como a lista o lê: o que foi contratado, com quem e até quando."""
    return {
        "pk": contrato.pk,
        "objeto": contrato.objeto,
        "fornecedor": contrato.fornecedor.nome_razao_social,
        "classificacao": _classificacao_do_contrato(contrato),
        "vigencia": _periodo(contrato.data_inicio, contrato.data_fim),
        "valor": _moeda(contrato.valor),
        "periodicidade": (
            contrato.get_periodicidade_cobranca_display()
            if contrato.periodicidade_cobranca
            else ""
        ),
        "cobre": _descricao_da_cobertura(
            len(contrato.equipamentos_vinculados.all()),
            len(contrato.locais_vinculados.all()),
            len(contrato.rotinas_vinculadas.all()),
        ),
        "decisao_proxima": contrato.prazo_de_decisao_proximo,
        "ativo": contrato.ativo,
        **_situacao_para_tela(contrato),
    }


def _garantias_do_condominio(condominio, termo=""):
    """As garantias do condomínio, da que vence antes à que vence depois."""
    return (
        Garantia.objects.filter(condominio=condominio)
        .buscar(termo)
        .select_related("fornecedor")
        .prefetch_related("equipamentos", "manutencoes", "itens_de_compra")
        .order_by("data_fim")
    )


def _garantia_para_tela(garantia) -> dict:
    """A garantia como a lista a lê: o que ela assegura e por quanto tempo."""
    return {
        "pk": garantia.pk,
        "descricao": garantia.descricao,
        "fornecedor": (
            garantia.fornecedor.nome_razao_social
            if garantia.fornecedor_id
            else "Sem fornecedor definido"
        ),
        "vigencia": _periodo(garantia.data_inicio, garantia.data_fim),
        "cobre": _cobertura_da_garantia_para_tela(garantia),
        "ativo": garantia.ativo,
        **_situacao_para_tela(garantia),
    }


def _cobertura_da_garantia_para_tela(garantia) -> str:
    """O que a garantia cobre, somando os três tipos de vínculo."""
    equipamentos = len(garantia.equipamentos.all())
    manutencoes = len(garantia.manutencoes.all())
    itens = len(garantia.itens_de_compra.all())

    partes = []
    if equipamentos:
        partes.append(_contagem(equipamentos, "equipamento", "equipamentos"))
    if manutencoes:
        partes.append(_contagem(manutencoes, "manutenção", "manutenções"))
    if itens:
        partes.append(_contagem(itens, "item de compra", "itens de compra"))
    if not partes:
        return "Ainda não diz o que cobre"
    return ", ".join(partes)


def _documentos_do_condominio(condominio, termo=""):
    """Os documentos do condomínio, do que vence antes ao que não vence."""
    return (
        DocumentoOperacional.objects.filter(condominio=condominio)
        .buscar(termo)
        .select_related("tipo_documento")
        .prefetch_related(
            "contratos_vinculados",
            "equipamentos_vinculados",
            "garantias_vinculadas",
            "fornecedores_vinculados",
        )
        .order_by("data_validade")
    )


def _documento_para_tela(documento) -> dict:
    """O documento como a lista o lê: o que é, de que tipo e até quando vale."""
    return {
        "pk": documento.pk,
        "titulo": documento.titulo,
        "tipo": documento.tipo_documento.nome,
        "relacionado": documento.relacionado_a,
        "validade": (
            documento.data_validade.strftime("%d/%m/%Y")
            if documento.data_validade
            else "Sem validade"
        ),
        "ativo": documento.ativo,
        **_situacao_para_tela(documento),
    }


def _tipos_para_tela(tipos) -> list[dict]:
    """O vocabulário de um catálogo, como a lista precisa dele."""
    return [
        {
            "pk": tipo.pk,
            "nome": tipo.nome,
            "descricao": tipo.descricao,
            **_situacao_do_cadastro(tipo.ativo),
        }
        for tipo in tipos
    ]
