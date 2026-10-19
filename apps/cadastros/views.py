"""Telas dos cadastros básicos operacionais.

O cadastro do condomínio, quem tem acesso a ele, os lugares, os equipamentos e
os fornecedores. É o que dá nome às coisas que a operação vai citar depois — por
isso a tela é só cadastro: ver o que existe, corrigir, desativar o que saiu de
uso. Nada aqui é histórico.

A autorização é decidida no backend, sempre a partir do vínculo ativo da pessoa
com o condomínio. Quem não enxerga o módulo não é avisado de que a tela existe:
a resposta é 404. Quem enxerga e não responde pelo condomínio — a supervisão —
é recusado com 403 ao tentar alterar, porque a existência já é legítima para
essa pessoa.

Sobre qual condomínio a tela mostra: com um só vínculo, é ele. Com mais de um,
a tela oferece a escolha, e o endereço diz qual — sempre conferido contra a
lista autorizada. Não há condomínio guardado em sessão, cookie ou preferência.
"""
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.condominios.models import AcessoUsuarioCondominio

from . import services
from .forms import CondominioForm, EquipamentoForm, FornecedorForm, LocalForm
from .models import Equipamento, Fornecedor, LocalOperacional
from .permissoes import (
    condominios_para_gerenciar,
    condominios_para_ver,
    pode_gerenciar_cadastros,
)

# Quantidade de linhas mostradas por listagem. É um cadastro, não um arquivo:
# a lista serve para encontrar e corrigir, não para varrer o histórico inteiro.
LIMITE_DA_LISTA = 100


# -- Cadastro do condomínio -----------------------------------------------------


@login_required
def condominio(request):
    """O cadastro do condomínio em vista, para ler ou corrigir."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    pode_gerenciar = pode_gerenciar_cadastros(request.user, atual)

    if request.method == "POST":
        if not pode_gerenciar:
            raise PermissionDenied("Esta pessoa não mantém o cadastro deste condomínio.")

        formulario = CondominioForm(request.POST, instance=atual)
        if not formulario.is_valid():
            return _render_condominio(
                request, base, pode_gerenciar, formulario=formulario, status=400
            )

        services.salvar_condominio(condominio=atual, dados=formulario.cleaned_data)
        messages.success(request, "Cadastro do condomínio atualizado.")
        return redirect(_destino("cadastros:condominio", atual))

    return _render_condominio(request, base, pode_gerenciar)


def _render_condominio(request, base, pode_gerenciar, *, formulario=None, status=200):
    """Monta a tela do cadastro: em leitura, os campos vêm desabilitados."""
    contexto = {
        **base,
        "pode_gerenciar": pode_gerenciar,
        "formulario": formulario
        or CondominioForm(
            instance=base["condominio_atual"], somente_leitura=not pode_gerenciar
        ),
    }
    return render(request, "pages/cadastros/condominio.html", contexto, status=status)


# -- Usuários e acessos ---------------------------------------------------------


@login_required
def acessos(request):
    """Quem tem acesso a este condomínio e com qual papel.

    Nesta fase a tela é de leitura: o Eithra mostra o vínculo como ele está. Dar
    ou tirar acesso é uma decisão sobre pessoas, e será tratada com o cuidado
    que ela pede — não como um formulário solto de cadastro.
    """
    base = _base_da_pagina(request)
    vinculos = (
        AcessoUsuarioCondominio.objects.filter(condominio=base["condominio_atual"])
        .select_related("usuario")
        .order_by("usuario__full_name")[:LIMITE_DA_LISTA]
    )
    contexto = {**base, "acessos": [_acesso_para_tela(item) for item in vinculos]}
    return render(request, "pages/cadastros/acessos.html", contexto)


# -- Áreas e locais -------------------------------------------------------------


@login_required
def locais(request):
    """Os lugares cadastrados no condomínio em vista."""
    return _render_locais(request)


@login_required
@require_POST
def criar_local(request):
    """Registra um local novo. Só quem responde pelo condomínio cadastra."""
    atual = _condominio_para_gestao(request)
    formulario = LocalForm(request.POST)
    if not formulario.is_valid():
        return _render_locais(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    services.criar_local(
        condominio=atual, criado_por=request.user, dados=formulario.cleaned_data
    )
    messages.success(request, "Local cadastrado.")
    return redirect(_destino("cadastros:locais", atual))


@login_required
@require_POST
def editar_local(request, pk):
    """Corrige o cadastro de um local do condomínio em vista."""
    atual = _condominio_para_gestao(request)
    local = _obter_ou_404(LocalOperacional.objects.filter(condominio=atual), pk)
    formulario = LocalForm(request.POST, instance=local)
    if not formulario.is_valid():
        return _render_locais(
            request,
            formulario_edicao=formulario,
            item_edicao=local,
            modal="editar",
            status=400,
        )

    services.editar_local(local=local, dados=formulario.cleaned_data)
    messages.success(request, "Local atualizado.")
    return redirect(_destino("cadastros:locais", atual))


@login_required
@require_POST
def alternar_local(request, pk):
    """Ativa ou desativa um local, sem apagar o cadastro."""
    atual = _condominio_para_gestao(request)
    local = _obter_ou_404(LocalOperacional.objects.filter(condominio=atual), pk)
    return _alternar(request, local, destino="cadastros:locais", nome="Local")


def _render_locais(
    request,
    *,
    formulario_criacao=None,
    formulario_edicao=None,
    item_edicao=None,
    modal=None,
    status=200,
):
    """Monta a lista de locais com o formulário que a tela pediu."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    pode_gerenciar = pode_gerenciar_cadastros(request.user, atual)

    if item_edicao is None and pode_gerenciar:
        item_edicao = _item_em_edicao(request, LocalOperacional, base["condominios"])
    if item_edicao is None:
        if modal == "editar":
            modal = None
    elif modal is None:
        modal = "editar"

    contexto = {
        **base,
        "pode_gerenciar": pode_gerenciar,
        "locais": _locais_para_tela(atual),
        "formulario_local": formulario_criacao or LocalForm(),
        "formulario_edicao": formulario_edicao
        or (LocalForm(instance=item_edicao) if item_edicao else None),
        "item_edicao": item_edicao,
        "modal_aberto": modal,
    }
    return render(request, "pages/cadastros/locais.html", contexto, status=status)


# -- Equipamentos ---------------------------------------------------------------


@login_required
def equipamentos(request):
    """Os equipamentos cadastrados no condomínio em vista."""
    return _render_equipamentos(request)


@login_required
@require_POST
def criar_equipamento(request):
    """Registra um equipamento novo no condomínio em vista."""
    atual = _condominio_para_gestao(request)
    formulario = EquipamentoForm(request.POST, condominio=atual)
    if not formulario.is_valid():
        return _render_equipamentos(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    services.criar_equipamento(
        condominio=atual, criado_por=request.user, dados=formulario.cleaned_data
    )
    messages.success(request, "Equipamento cadastrado.")
    return redirect(_destino("cadastros:equipamentos", atual))


@login_required
@require_POST
def editar_equipamento(request, pk):
    """Corrige o cadastro de um equipamento do condomínio em vista."""
    atual = _condominio_para_gestao(request)
    equipamento = _obter_ou_404(Equipamento.objects.filter(condominio=atual), pk)
    formulario = EquipamentoForm(request.POST, instance=equipamento, condominio=atual)
    if not formulario.is_valid():
        return _render_equipamentos(
            request,
            formulario_edicao=formulario,
            item_edicao=equipamento,
            modal="editar",
            status=400,
        )

    services.editar_equipamento(
        equipamento=equipamento, dados=formulario.cleaned_data
    )
    messages.success(request, "Equipamento atualizado.")
    return redirect(_destino("cadastros:equipamentos", atual))


@login_required
@require_POST
def alternar_equipamento(request, pk):
    """Ativa ou desativa um equipamento, sem apagar o cadastro."""
    atual = _condominio_para_gestao(request)
    equipamento = _obter_ou_404(Equipamento.objects.filter(condominio=atual), pk)
    return _alternar(
        request, equipamento, destino="cadastros:equipamentos", nome="Equipamento"
    )


def _render_equipamentos(
    request,
    *,
    formulario_criacao=None,
    formulario_edicao=None,
    item_edicao=None,
    modal=None,
    status=200,
):
    """Monta a lista de equipamentos com o formulário que a tela pediu."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    pode_gerenciar = pode_gerenciar_cadastros(request.user, atual)

    if item_edicao is None and pode_gerenciar:
        item_edicao = _item_em_edicao(request, Equipamento, base["condominios"])
    if item_edicao is None:
        if modal == "editar":
            modal = None
    elif modal is None:
        modal = "editar"

    contexto = {
        **base,
        "pode_gerenciar": pode_gerenciar,
        "equipamentos": _equipamentos_para_tela(atual),
        "formulario_equipamento": formulario_criacao
        or EquipamentoForm(condominio=atual),
        "formulario_edicao": formulario_edicao
        or (
            EquipamentoForm(instance=item_edicao, condominio=atual)
            if item_edicao
            else None
        ),
        "item_edicao": item_edicao,
        "modal_aberto": modal,
    }
    return render(request, "pages/cadastros/equipamentos.html", contexto, status=status)


# -- Fornecedores ---------------------------------------------------------------


@login_required
def fornecedores(request):
    """Os fornecedores cadastrados no condomínio em vista."""
    return _render_fornecedores(request)


@login_required
@require_POST
def criar_fornecedor(request):
    """Registra um fornecedor novo no condomínio em vista."""
    atual = _condominio_para_gestao(request)
    formulario = FornecedorForm(request.POST, condominio=atual)
    if not formulario.is_valid():
        return _render_fornecedores(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    services.criar_fornecedor(
        condominio=atual, criado_por=request.user, dados=formulario.cleaned_data
    )
    messages.success(request, "Fornecedor cadastrado.")
    return redirect(_destino("cadastros:fornecedores", atual))


@login_required
@require_POST
def editar_fornecedor(request, pk):
    """Corrige o cadastro de um fornecedor do condomínio em vista."""
    atual = _condominio_para_gestao(request)
    fornecedor = _obter_ou_404(Fornecedor.objects.filter(condominio=atual), pk)
    formulario = FornecedorForm(request.POST, instance=fornecedor, condominio=atual)
    if not formulario.is_valid():
        return _render_fornecedores(
            request,
            formulario_edicao=formulario,
            item_edicao=fornecedor,
            modal="editar",
            status=400,
        )

    services.editar_fornecedor(fornecedor=fornecedor, dados=formulario.cleaned_data)
    messages.success(request, "Fornecedor atualizado.")
    return redirect(_destino("cadastros:fornecedores", atual))


@login_required
@require_POST
def alternar_fornecedor(request, pk):
    """Ativa ou desativa um fornecedor, sem apagar o cadastro."""
    atual = _condominio_para_gestao(request)
    fornecedor = _obter_ou_404(Fornecedor.objects.filter(condominio=atual), pk)
    return _alternar(
        request, fornecedor, destino="cadastros:fornecedores", nome="Fornecedor"
    )


def _render_fornecedores(
    request,
    *,
    formulario_criacao=None,
    formulario_edicao=None,
    item_edicao=None,
    modal=None,
    status=200,
):
    """Monta a lista de fornecedores com o formulário que a tela pediu."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    pode_gerenciar = pode_gerenciar_cadastros(request.user, atual)

    if item_edicao is None and pode_gerenciar:
        item_edicao = _item_em_edicao(request, Fornecedor, base["condominios"])
    if item_edicao is None:
        if modal == "editar":
            modal = None
    elif modal is None:
        modal = "editar"

    contexto = {
        **base,
        "pode_gerenciar": pode_gerenciar,
        "fornecedores": _fornecedores_para_tela(atual),
        "formulario_fornecedor": formulario_criacao or FornecedorForm(condominio=atual),
        "formulario_edicao": formulario_edicao
        or (FornecedorForm(instance=item_edicao, condominio=atual) if item_edicao else None),
        "item_edicao": item_edicao,
        "modal_aberto": modal,
    }
    return render(request, "pages/cadastros/fornecedores.html", contexto, status=status)


# -- Autorização e contexto -----------------------------------------------------
#
# Toda pergunta passa pelo domínio. Quem não enxerga o módulo recebe 404 — não
# descobre que a tela existe. Quem enxerga e não responde pelo condomínio recebe
# 403 ao tentar alterar. Esconder o botão na tela não substitui estas checagens.


def _condominios_para_ver(request):
    """Condomínios cujos cadastros esta pessoa consulta, ou 404."""
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


def _condominio_para_gestao(request):
    """O condomínio em que esta pessoa mantém cadastros agora.

    Quem não enxerga o módulo recebe 404 — não descobre que a tela existe. Quem
    enxerga e não responde pelo condomínio recebe 403 — a existência já é
    legítima para essa pessoa, a decisão sobre o cadastro não é.
    """
    visualizaveis = _condominios_para_ver(request)
    atual = _condominio_atual(request, visualizaveis)
    if not pode_gerenciar_cadastros(request.user, atual):
        raise PermissionDenied("Esta pessoa não mantém os cadastros deste condomínio.")
    return atual


def _base_da_pagina(request):
    """O que toda tela de cadastro precisa: os condomínios e qual está em vista."""
    condominios = _condominios_para_ver(request)
    return {
        "condominios": condominios,
        "condominio_atual": _condominio_atual(request, condominios),
        "mostrar_seletor": condominios.count() > 1,
    }


def _item_em_edicao(request, modelo, condominios):
    """O registro que o modal de edição deve abrir, quando a tela pede um."""
    if request.GET.get("abrir") != "editar":
        return None
    pk = request.GET.get("item")
    if not pk or not _eh_uuid(pk):
        return None
    return _obter_ou_404(modelo.objects.filter(condominio__in=condominios), pk)


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


def _destino(view_name, condominio) -> str:
    """Volta para a tela preservando o condomínio em vista."""
    return f"{reverse(view_name)}?condominio={condominio.pk}"


def _alternar(request, objeto, *, destino, nome):
    """Ativa ou desativa um cadastro e volta para a lista."""
    novo = not objeto.ativo
    services.definir_ativo(objeto=objeto, ativo=novo)
    if novo:
        messages.success(request, f"{nome} ativado.")
    else:
        messages.success(request, f"{nome} desativado.")
    return redirect(_destino(destino, objeto.condominio))


# -- Tradução para a tela -------------------------------------------------------


def _acesso_para_tela(vinculo) -> dict:
    """O vínculo como a lista de acessos precisa dele."""
    usuario = vinculo.usuario
    return {
        "nome": usuario.full_name or usuario.email,
        "email": usuario.email,
        "papel": vinculo.get_papel_display(),
        "situacao": "Ativo" if vinculo.ativo else "Inativo",
        "tom": "success" if vinculo.ativo else "secondary",
        "desde": vinculo.criado_em.strftime("%d/%m/%Y"),
    }


def _situacao_do_cadastro(ativo: bool) -> dict:
    """Como o estado de um cadastro deve ser lido e usado na lista."""
    return {
        "ativo": ativo,
        "texto": "Ativo" if ativo else "Inativo",
        "tom": "success" if ativo else "secondary",
    }


def _locais_para_tela(condominio) -> list[dict]:
    """Os locais do condomínio, como a lista precisa deles."""
    locais = LocalOperacional.objects.filter(condominio=condominio).order_by("nome")[
        :LIMITE_DA_LISTA
    ]
    return [
        {
            "pk": local.pk,
            "nome": local.nome,
            "tipo": local.get_tipo_display(),
            "descricao": local.descricao,
            **_situacao_do_cadastro(local.ativo),
        }
        for local in locais
    ]


def _equipamentos_para_tela(condominio) -> list[dict]:
    """Os equipamentos do condomínio, como a lista precisa deles."""
    equipamentos = (
        Equipamento.objects.filter(condominio=condominio)
        .select_related("local")
        .order_by("nome")[:LIMITE_DA_LISTA]
    )
    return [
        {
            "pk": equipamento.pk,
            "nome": equipamento.nome,
            "tipo": equipamento.tipo_texto,
            "local": equipamento.local.nome if equipamento.local else "",
            "fabricante": equipamento.fabricante,
            "modelo": equipamento.modelo,
            **_situacao_do_cadastro(equipamento.ativo),
        }
        for equipamento in equipamentos
    ]


def _fornecedores_para_tela(condominio) -> list[dict]:
    """Os fornecedores do condomínio, como a lista precisa deles."""
    fornecedores = Fornecedor.objects.filter(condominio=condominio).order_by(
        "nome_razao_social"
    )[:LIMITE_DA_LISTA]
    return [
        {
            "pk": fornecedor.pk,
            "nome": fornecedor.nome_razao_social,
            "fantasia": fornecedor.nome_fantasia,
            "documento": fornecedor.documento_formatado,
            "contato": fornecedor.contato,
            "telefone": fornecedor.telefone,
            **_situacao_do_cadastro(fornecedor.ativo),
        }
        for fornecedor in fornecedores
    ]
