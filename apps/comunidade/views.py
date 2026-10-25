"""Telas do cadastro de comunidade: unidades, pessoas e veículos.

É o retrato de quem vive e circula pelo condomínio, e o que liga uma coisa à
outra: a pessoa à unidade, o veículo à pessoa e à unidade. Por isso a tela não
é só cadastro — cada ficha mostra, na hora, quem está ligado a ela agora e o que
já passou.

A autorização é decidida no backend, sempre a partir do vínculo ativo da pessoa
com o condomínio. Quem não enxerga o módulo não é avisado de que a tela existe:
a resposta é 404. Esconder o botão na tela não substitui esta checagem.

Sobre qual condomínio a tela mostra: com um só vínculo, é ele. Com mais de um,
a tela oferece a escolha, e o endereço diz qual — sempre conferido contra a
lista autorizada. Não há condomínio guardado em sessão, cookie ou preferência.
"""
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import F, Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.condominios import logo
from apps.core.paginacao import paginar

from . import services
from .forms import (
    PessoaForm,
    TransferirUnidadeForm,
    UnidadeForm,
    VeiculoForm,
    VinculoPessoaUnidadeForm,
    VinculoVeiculoPessoaForm,
    VinculoVeiculoUnidadeForm,
)
from .models import (
    Pessoa,
    Unidade,
    Veiculo,
    VinculoPessoaUnidade,
    VinculoVeiculoPessoa,
    VinculoVeiculoUnidade,
)
from .permissoes import condominios_para_ver, pode_operar


# -- Unidades -------------------------------------------------------------------


@login_required
def unidades(request):
    """As unidades do condomínio em vista."""
    return _render_unidades(request)


@login_required
def unidade(request, pk):
    """Uma unidade: os dados, quem está ligado a ela e o que já passou."""
    base = _base_da_pagina(request)
    registro = _obter_ou_404(
        Unidade.objects.filter(condominio=base["condominio_atual"]), pk
    )
    return _render_unidade(request, base, registro)


@login_required
@require_POST
def criar_unidade(request):
    """Registra uma unidade nova. Só quem opera o cadastro cadastra."""
    atual = _condominio_para_operar(request)
    formulario = UnidadeForm(request.POST, condominio=atual)
    if not formulario.is_valid():
        return _render_unidades(request, formulario_criacao=formulario, modal="criar", status=400)

    services.criar_unidade(
        condominio=atual, criado_por=request.user, dados=formulario.cleaned_data
    )
    messages.success(request, "Unidade cadastrada.")
    return redirect(_destino("comunidade:unidades", atual))


@login_required
@require_POST
def editar_unidade(request, pk):
    """Corrige os dados de uma unidade do condomínio em vista."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    _exigir_operacao(request, atual)
    registro = _obter_ou_404(Unidade.objects.filter(condominio=atual), pk)
    formulario = UnidadeForm(request.POST, instance=registro, condominio=atual)
    if not formulario.is_valid():
        return _render_unidade(
            request, base, registro, formulario_unidade=formulario, modal="editar", status=400
        )

    services.editar_unidade(unidade=registro, dados=formulario.cleaned_data)
    messages.success(request, "Unidade atualizada.")
    return redirect(_destino("comunidade:unidade", atual, pk=registro.pk))


@login_required
@require_POST
def alternar_unidade(request, pk):
    """Ativa ou desativa uma unidade, sem apagar o cadastro."""
    atual = _condominio_para_operar(request)
    registro = _obter_ou_404(Unidade.objects.filter(condominio=atual), pk)
    try:
        if registro.ativa:
            services.inativar_unidade(unidade=registro)
            messages.success(request, "Unidade desativada.")
        else:
            services.reativar_unidade(unidade=registro)
            messages.success(request, "Unidade ativada.")
    except services.ErroDeComunidade as erro:
        messages.error(request, str(erro))
    return redirect(_destino("comunidade:unidades", atual))


@login_required
@require_POST
def vincular_pessoa(request, pk):
    """Liga uma pessoa a esta unidade."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    _exigir_operacao(request, atual)
    registro = _obter_ou_404(Unidade.objects.filter(condominio=atual), pk)
    formulario = VinculoPessoaUnidadeForm(request.POST, condominio=atual, unidade=registro)
    if not formulario.is_valid():
        return _render_unidade(
            request,
            base,
            registro,
            formulario_pessoa=formulario,
            modal="vincular-pessoa",
            status=400,
        )

    try:
        # A ponta conhecida já vem no formulário — fixada e não editável —, então
        # o vínculo é criado inteiramente a partir do que foi validado.
        services.criar_vinculo_pessoa_unidade(
            condominio=atual, criado_por=request.user, **formulario.cleaned_data
        )
    except services.ErroDeComunidade as erro:
        formulario.add_error(None, str(erro))
        return _render_unidade(
            request,
            base,
            registro,
            formulario_pessoa=formulario,
            modal="vincular-pessoa",
            status=400,
        )
    messages.success(request, "Pessoa vinculada à unidade.")
    return redirect(_destino("comunidade:unidade", atual, pk=registro.pk))


@login_required
@require_POST
def vincular_veiculo(request, pk):
    """Relaciona um veículo a esta unidade."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    _exigir_operacao(request, atual)
    registro = _obter_ou_404(Unidade.objects.filter(condominio=atual), pk)
    formulario = VinculoVeiculoUnidadeForm(request.POST, condominio=atual, unidade=registro)
    if not formulario.is_valid():
        return _render_unidade(
            request,
            base,
            registro,
            formulario_veiculo=formulario,
            modal="vincular-veiculo",
            status=400,
        )

    try:
        services.vincular_veiculo_unidade(
            condominio=atual,
            criado_por=request.user,
            **formulario.cleaned_data,
        )
    except services.ErroDeComunidade as erro:
        formulario.add_error(None, str(erro))
        return _render_unidade(
            request,
            base,
            registro,
            formulario_veiculo=formulario,
            modal="vincular-veiculo",
            status=400,
        )
    messages.success(request, "Veículo relacionado à unidade.")
    return redirect(_destino("comunidade:unidade", atual, pk=registro.pk))


def _render_unidades(
    request, *, formulario_criacao=None, item_edicao=None, modal=None, status=200
):
    """Monta a lista de unidades com o formulário que a tela pediu."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    operar = pode_operar(request.user, atual)
    pagina_unidades = _unidades_para_tela(
        atual, base.get("termo", ""), request.GET.get("page")
    )

    contexto = {
        **base,
        "pode_operar": operar,
        "unidades": pagina_unidades.itens,
        "pagina_unidades": pagina_unidades,
        "formulario_unidade": formulario_criacao or UnidadeForm(condominio=atual),
        "modal_aberto": modal,
    }
    return render(request, "pages/comunidade/unidades.html", contexto, status=status)


def _render_unidade(
    request,
    base,
    registro,
    *,
    formulario_unidade=None,
    formulario_pessoa=None,
    formulario_veiculo=None,
    modal=None,
    status=200,
):
    """Monta a ficha de uma unidade: dados, pessoas, histórico e veículos."""
    atual = base["condominio_atual"]
    operar = pode_operar(request.user, atual)
    vinculos = list(
        VinculoPessoaUnidade.objects.da_unidade(registro)
        .select_related("pessoa")
        .order_by(F("data_inicio").desc(nulls_last=True), "-criado_em")
    )
    contexto = {
        **base,
        "pode_operar": operar,
        "unidade": _unidade_para_tela(registro),
        "vinculos_atuais": [
            _vinculo_pessoa_unidade_para_tela(vinculo)
            for vinculo in vinculos
            if vinculo.vigente
        ],
        "vinculos_encerrados": [
            _vinculo_pessoa_unidade_para_tela(vinculo)
            for vinculo in vinculos
            if vinculo.encerrado
        ],
        "veiculos_relacionados": _vinculos_veiculo_unidade_para_tela(registro),
        "formulario_unidade": formulario_unidade
        or UnidadeForm(instance=registro, condominio=atual),
        "formulario_pessoa": formulario_pessoa
        or VinculoPessoaUnidadeForm(condominio=atual, unidade=registro),
        "formulario_veiculo": formulario_veiculo
        or VinculoVeiculoUnidadeForm(condominio=atual, unidade=registro),
        "modal_aberto": modal,
    }
    return render(request, "pages/comunidade/unidade.html", contexto, status=status)


# -- Pessoas --------------------------------------------------------------------


@login_required
def pessoas(request):
    """As pessoas do condomínio em vista."""
    return _render_pessoas(request)


@login_required
def pessoa(request, pk):
    """Uma pessoa: os dados e as unidades com que ela se relaciona."""
    base = _base_da_pagina(request)
    registro = _obter_ou_404(
        Pessoa.objects.filter(condominio=base["condominio_atual"]), pk
    )
    return _render_pessoa(request, base, registro)


@login_required
@require_POST
def criar_pessoa(request):
    """Registra uma pessoa nova. Só quem opera o cadastro cadastra."""
    atual = _condominio_para_operar(request)
    formulario = PessoaForm(request.POST, condominio=atual)
    if not formulario.is_valid():
        return _render_pessoas(request, formulario_criacao=formulario, modal="criar", status=400)

    services.criar_pessoa(
        condominio=atual, criado_por=request.user, dados=formulario.cleaned_data
    )
    messages.success(request, "Pessoa cadastrada.")
    return redirect(_destino("comunidade:pessoas", atual))


@login_required
@require_POST
def editar_pessoa(request, pk):
    """Corrige os dados de uma pessoa do condomínio em vista."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    _exigir_operacao(request, atual)
    registro = _obter_ou_404(Pessoa.objects.filter(condominio=atual), pk)
    formulario = PessoaForm(request.POST, instance=registro, condominio=atual)
    if not formulario.is_valid():
        return _render_pessoa(
            request, base, registro, formulario_pessoa=formulario, modal="editar", status=400
        )

    services.editar_pessoa(pessoa=registro, dados=formulario.cleaned_data)
    messages.success(request, "Pessoa atualizada.")
    return redirect(_destino("comunidade:pessoa", atual, pk=registro.pk))


@login_required
@require_POST
def alternar_pessoa(request, pk):
    """Ativa ou desativa uma pessoa, sem apagar o cadastro."""
    atual = _condominio_para_operar(request)
    registro = _obter_ou_404(Pessoa.objects.filter(condominio=atual), pk)
    try:
        if registro.ativa:
            services.inativar_pessoa(pessoa=registro)
            messages.success(request, "Pessoa desativada.")
        else:
            services.reativar_pessoa(pessoa=registro)
            messages.success(request, "Pessoa ativada.")
    except services.ErroDeComunidade as erro:
        messages.error(request, str(erro))
    return redirect(_destino("comunidade:pessoas", atual))


@login_required
@require_POST
def vincular_unidade(request, pk):
    """Liga esta pessoa a uma unidade."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    _exigir_operacao(request, atual)
    registro = _obter_ou_404(Pessoa.objects.filter(condominio=atual), pk)
    formulario = VinculoPessoaUnidadeForm(request.POST, condominio=atual, pessoa=registro)
    if not formulario.is_valid():
        return _render_pessoa(
            request,
            base,
            registro,
            formulario_unidade=formulario,
            modal="vincular-unidade",
            status=400,
        )

    try:
        services.criar_vinculo_pessoa_unidade(
            condominio=atual,
            criado_por=request.user,
            **formulario.cleaned_data,
        )
    except services.ErroDeComunidade as erro:
        formulario.add_error(None, str(erro))
        return _render_pessoa(
            request,
            base,
            registro,
            formulario_unidade=formulario,
            modal="vincular-unidade",
            status=400,
        )
    messages.success(request, "Pessoa vinculada à unidade.")
    return redirect(_destino("comunidade:pessoa", atual, pk=registro.pk))


@login_required
@require_POST
def transferir_unidade(request, pk):
    """Muda esta pessoa de unidade a partir de um vínculo em vigor.

    A ação parte do vínculo, e não da pessoa: quem tem mais de um papel na
    mesma unidade muda um deles, e os outros ficam onde estão.
    """
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    _exigir_operacao(request, atual)
    vinculo = _obter_ou_404(
        VinculoPessoaUnidade.objects.filter(condominio=atual).select_related(
            "pessoa", "unidade"
        ),
        pk,
    )
    formulario = TransferirUnidadeForm(
        request.POST, condominio=atual, vinculo=vinculo
    )
    if not formulario.is_valid():
        return _render_pessoa(
            request,
            base,
            vinculo.pessoa,
            formulario_transferencia=formulario,
            modal="alterar-unidade",
            status=400,
        )

    try:
        services.transferir_pessoa_unidade(
            vinculo=vinculo,
            para_unidade=formulario.cleaned_data["unidade"],
            criado_por=request.user,
        )
    except services.ErroDeComunidade as erro:
        formulario.add_error(None, str(erro))
        return _render_pessoa(
            request,
            base,
            vinculo.pessoa,
            formulario_transferencia=formulario,
            modal="alterar-unidade",
            status=400,
        )
    messages.success(
        request, "Unidade alterada. O vínculo anterior ficou no histórico."
    )
    return redirect(_destino("comunidade:pessoa", atual, pk=vinculo.pessoa_id))


def _render_pessoas(request, *, formulario_criacao=None, modal=None, status=200):
    """Monta a lista de pessoas com o formulário que a tela pediu."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    pagina_pessoas = _pessoas_para_tela(
        atual, base.get("termo", ""), request.GET.get("page")
    )
    contexto = {
        **base,
        "pode_operar": pode_operar(request.user, atual),
        "pessoas": pagina_pessoas.itens,
        "pagina_pessoas": pagina_pessoas,
        "formulario_pessoa": formulario_criacao or PessoaForm(condominio=atual),
        "modal_aberto": modal,
    }
    return render(request, "pages/comunidade/pessoas.html", contexto, status=status)


def _render_pessoa(
    request,
    base,
    registro,
    *,
    formulario_pessoa=None,
    formulario_unidade=None,
    formulario_transferencia=None,
    modal=None,
    status=200,
):
    """Monta a ficha de uma pessoa: dados, unidades e veículos ligados a ela."""
    atual = base["condominio_atual"]
    vinculos = list(
        VinculoPessoaUnidade.objects.da_pessoa(registro)
        .select_related("unidade")
        .order_by(F("data_inicio").desc(nulls_last=True), "-criado_em")
    )
    vinculos_veiculo = list(
        VinculoVeiculoPessoa.objects.da_pessoa(registro)
        .select_related("veiculo")
        .order_by("-data_inicio", "-criado_em")
    )
    contexto = {
        **base,
        "pode_operar": pode_operar(request.user, atual),
        "pessoa": _pessoa_para_tela(registro),
        "vinculos_atuais": [
            _vinculo_pessoa_unidade_para_tela(vinculo)
            for vinculo in vinculos
            if vinculo.vigente
        ],
        "vinculos_encerrados": [
            _vinculo_pessoa_unidade_para_tela(vinculo)
            for vinculo in vinculos
            if vinculo.encerrado
        ],
        "veiculos_atuais": [
            _vinculo_veiculo_pessoa_para_tela(vinculo)
            for vinculo in vinculos_veiculo
            if vinculo.vigente
        ],
        "veiculos_encerrados": [
            _vinculo_veiculo_pessoa_para_tela(vinculo)
            for vinculo in vinculos_veiculo
            if vinculo.encerrado
        ],
        "formulario_pessoa": formulario_pessoa
        or PessoaForm(instance=registro, condominio=atual),
        "formulario_unidade": formulario_unidade
        or VinculoPessoaUnidadeForm(condominio=atual, pessoa=registro),
        "formulario_transferencia": formulario_transferencia
        or TransferirUnidadeForm(condominio=atual),
        "modal_aberto": modal,
    }
    return render(request, "pages/comunidade/pessoa.html", contexto, status=status)


# -- Veículos -------------------------------------------------------------------


@login_required
def veiculos(request):
    """Os veículos do condomínio em vista."""
    return _render_veiculos(request)


@login_required
def veiculo(request, pk):
    """Um veículo: quem o utiliza e a qual unidade ele se relaciona."""
    base = _base_da_pagina(request)
    registro = _obter_ou_404(
        Veiculo.objects.filter(condominio=base["condominio_atual"]), pk
    )
    return _render_veiculo(request, base, registro)


@login_required
@require_POST
def criar_veiculo(request):
    """Registra um veículo novo. Só quem opera o cadastro cadastra."""
    atual = _condominio_para_operar(request)
    formulario = VeiculoForm(request.POST, condominio=atual)
    if not formulario.is_valid():
        return _render_veiculos(request, formulario_criacao=formulario, modal="criar", status=400)

    services.criar_veiculo(
        condominio=atual, criado_por=request.user, dados=formulario.cleaned_data
    )
    messages.success(request, "Veículo cadastrado.")
    return redirect(_destino("comunidade:veiculos", atual))


@login_required
@require_POST
def editar_veiculo(request, pk):
    """Corrige os dados de um veículo do condomínio em vista."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    _exigir_operacao(request, atual)
    registro = _obter_ou_404(Veiculo.objects.filter(condominio=atual), pk)
    formulario = VeiculoForm(request.POST, instance=registro, condominio=atual)
    if not formulario.is_valid():
        return _render_veiculo(
            request, base, registro, formulario_veiculo=formulario, modal="editar", status=400
        )

    services.editar_veiculo(veiculo=registro, dados=formulario.cleaned_data)
    messages.success(request, "Veículo atualizado.")
    return redirect(_destino("comunidade:veiculo", atual, pk=registro.pk))


@login_required
@require_POST
def alternar_veiculo(request, pk):
    """Ativa ou desativa um veículo, sem apagar o cadastro."""
    atual = _condominio_para_operar(request)
    registro = _obter_ou_404(Veiculo.objects.filter(condominio=atual), pk)
    try:
        if registro.ativo:
            services.inativar_veiculo(veiculo=registro)
            messages.success(request, "Veículo desativado.")
        else:
            services.reativar_veiculo(veiculo=registro)
            messages.success(request, "Veículo ativado.")
    except services.ErroDeComunidade as erro:
        messages.error(request, str(erro))
    return redirect(_destino("comunidade:veiculos", atual))


@login_required
@require_POST
def vincular_pessoa_veiculo(request, pk):
    """Registra quem utiliza este veículo."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    _exigir_operacao(request, atual)
    registro = _obter_ou_404(Veiculo.objects.filter(condominio=atual), pk)
    formulario = VinculoVeiculoPessoaForm(request.POST, condominio=atual, veiculo=registro)
    if not formulario.is_valid():
        return _render_veiculo(
            request,
            base,
            registro,
            formulario_pessoa=formulario,
            modal="vincular-pessoa",
            status=400,
        )

    try:
        services.vincular_veiculo_pessoa(
            condominio=atual,
            criado_por=request.user,
            **formulario.cleaned_data,
        )
    except services.ErroDeComunidade as erro:
        formulario.add_error(None, str(erro))
        return _render_veiculo(
            request,
            base,
            registro,
            formulario_pessoa=formulario,
            modal="vincular-pessoa",
            status=400,
        )
    messages.success(request, "Pessoa vinculada ao veículo.")
    return redirect(_destino("comunidade:veiculo", atual, pk=registro.pk))


@login_required
@require_POST
def vincular_unidade_veiculo(request, pk):
    """Relaciona este veículo a uma unidade."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    _exigir_operacao(request, atual)
    registro = _obter_ou_404(Veiculo.objects.filter(condominio=atual), pk)
    formulario = VinculoVeiculoUnidadeForm(request.POST, condominio=atual, veiculo=registro)
    if not formulario.is_valid():
        return _render_veiculo(
            request,
            base,
            registro,
            formulario_unidade=formulario,
            modal="vincular-unidade",
            status=400,
        )

    try:
        services.vincular_veiculo_unidade(
            condominio=atual,
            criado_por=request.user,
            **formulario.cleaned_data,
        )
    except services.ErroDeComunidade as erro:
        formulario.add_error(None, str(erro))
        return _render_veiculo(
            request,
            base,
            registro,
            formulario_unidade=formulario,
            modal="vincular-unidade",
            status=400,
        )
    messages.success(request, "Veículo relacionado à unidade.")
    return redirect(_destino("comunidade:veiculo", atual, pk=registro.pk))


def _render_veiculos(request, *, formulario_criacao=None, modal=None, status=200):
    """Monta a lista de veículos com o formulário que a tela pediu."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    pagina_veiculos = _veiculos_para_tela(
        atual, base.get("termo", ""), request.GET.get("page")
    )
    contexto = {
        **base,
        "pode_operar": pode_operar(request.user, atual),
        "veiculos": pagina_veiculos.itens,
        "pagina_veiculos": pagina_veiculos,
        "formulario_veiculo": formulario_criacao or VeiculoForm(condominio=atual),
        "modal_aberto": modal,
    }
    return render(request, "pages/comunidade/veiculos.html", contexto, status=status)


def _render_veiculo(
    request,
    base,
    registro,
    *,
    formulario_veiculo=None,
    formulario_pessoa=None,
    formulario_unidade=None,
    modal=None,
    status=200,
):
    """Monta a ficha de um veículo: quem o utiliza e a que unidade se relaciona."""
    atual = base["condominio_atual"]
    vinculos_pessoa = list(
        VinculoVeiculoPessoa.objects.do_veiculo(registro)
        .select_related("pessoa")
        .order_by("-data_inicio", "-criado_em")
    )
    vinculos_unidade = list(
        VinculoVeiculoUnidade.objects.do_veiculo(registro)
        .select_related("unidade")
        .order_by("-data_inicio", "-criado_em")
    )
    contexto = {
        **base,
        "pode_operar": pode_operar(request.user, atual),
        "veiculo": _veiculo_para_tela(registro),
        "pessoas_atuais": [
            _vinculo_veiculo_pessoa_para_tela(vinculo)
            for vinculo in vinculos_pessoa
            if vinculo.vigente
        ],
        "pessoas_encerradas": [
            _vinculo_veiculo_pessoa_para_tela(vinculo)
            for vinculo in vinculos_pessoa
            if vinculo.encerrado
        ],
        "unidades_atuais": [
            _vinculo_veiculo_unidade_para_tela_linha(vinculo)
            for vinculo in vinculos_unidade
            if vinculo.vigente
        ],
        "unidades_encerradas": [
            _vinculo_veiculo_unidade_para_tela_linha(vinculo)
            for vinculo in vinculos_unidade
            if vinculo.encerrado
        ],
        "formulario_veiculo": formulario_veiculo
        or VeiculoForm(instance=registro, condominio=atual),
        "formulario_pessoa": formulario_pessoa
        or VinculoVeiculoPessoaForm(condominio=atual, veiculo=registro),
        "formulario_unidade": formulario_unidade
        or VinculoVeiculoUnidadeForm(condominio=atual, veiculo=registro),
        "modal_aberto": modal,
    }
    return render(request, "pages/comunidade/veiculo.html", contexto, status=status)


# -- Encerrar vínculos ----------------------------------------------------------


@login_required
@require_POST
def encerrar_vinculo_pessoa_unidade(request, pk):
    """Marca o fim de um vínculo entre pessoa e unidade."""
    atual = _condominio_para_operar(request)
    vinculo = _obter_ou_404(
        VinculoPessoaUnidade.objects.filter(condominio=atual).select_related(
            "pessoa", "unidade"
        ),
        pk,
    )
    try:
        services.encerrar_vinculo_pessoa_unidade(vinculo=vinculo)
        messages.success(request, "Vínculo encerrado.")
    except services.ErroDeComunidade as erro:
        messages.error(request, str(erro))
    return redirect(
        _destino_do_pai(
            request,
            vinculo.condominio_id,
            {
                "unidade": ("comunidade:unidade", vinculo.unidade_id),
                "pessoa": ("comunidade:pessoa", vinculo.pessoa_id),
            },
        )
    )


@login_required
@require_POST
def encerrar_vinculo_veiculo_pessoa(request, pk):
    """Marca o fim do uso de um veículo por uma pessoa."""
    atual = _condominio_para_operar(request)
    vinculo = _obter_ou_404(
        VinculoVeiculoPessoa.objects.filter(condominio=atual).select_related(
            "veiculo", "pessoa"
        ),
        pk,
    )
    try:
        services.encerrar_vinculo_veiculo_pessoa(vinculo=vinculo)
        messages.success(request, "Vínculo encerrado.")
    except services.ErroDeComunidade as erro:
        messages.error(request, str(erro))
    return redirect(
        _destino_do_pai(
            request,
            vinculo.condominio_id,
            {
                "veiculo": ("comunidade:veiculo", vinculo.veiculo_id),
                "pessoa": ("comunidade:pessoa", vinculo.pessoa_id),
            },
        )
    )


@login_required
@require_POST
def encerrar_vinculo_veiculo_unidade(request, pk):
    """Marca o fim da relação de um veículo com uma unidade."""
    atual = _condominio_para_operar(request)
    vinculo = _obter_ou_404(
        VinculoVeiculoUnidade.objects.filter(condominio=atual).select_related(
            "veiculo", "unidade"
        ),
        pk,
    )
    try:
        services.encerrar_vinculo_veiculo_unidade(vinculo=vinculo)
        messages.success(request, "Vínculo encerrado.")
    except services.ErroDeComunidade as erro:
        messages.error(request, str(erro))
    return redirect(
        _destino_do_pai(
            request,
            vinculo.condominio_id,
            {
                "veiculo": ("comunidade:veiculo", vinculo.veiculo_id),
                "unidade": ("comunidade:unidade", vinculo.unidade_id),
            },
        )
    )


# -- Autorização e contexto -----------------------------------------------------
#
# Toda pergunta passa pelo domínio. Quem não enxerga o módulo recebe 404 — não
# descobre que a tela existe. Quem enxerga e não opera recebe 403 ao tentar
# alterar. Esconder o botão na tela não substitui estas checagens.


def _condominios_para_ver(request):
    """Condomínios cuja comunidade esta pessoa consulta, ou 404."""
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
    """O condomínio em que esta pessoa mantém o cadastro de comunidade agora.

    Quem não enxerga o módulo recebe 404 — não descobre que a tela existe. Quem
    enxerga e não opera recebe 403.
    """
    visualizaveis = _condominios_para_ver(request)
    atual = _condominio_atual(request, visualizaveis)
    _exigir_operacao(request, atual)
    return atual


def _exigir_operacao(request, condominio):
    """Recusa, com 403, uma alteração de quem só enxerga o módulo."""
    if not pode_operar(request.user, condominio):
        raise PermissionDenied(
            "Esta pessoa não mantém o cadastro de comunidade deste condomínio."
        )


def _base_da_pagina(request):
    """O que toda tela de comunidade precisa: os condomínios e qual está em vista."""
    condominios = _condominios_para_ver(request)
    atual = _condominio_atual(request, condominios)
    return {
        "condominios": condominios,
        "condominio_atual": atual,
        "logo_do_condominio": logo.endereco(atual),
        "mostrar_seletor": condominios.count() > 1,
        "termo": (request.GET.get("q") or "").strip(),
    }


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


def _destino(view_name, condominio, *, pk=None) -> str:
    """Volta para uma tela preservando o condomínio em vista."""
    url = reverse(view_name, args=[pk]) if pk is not None else reverse(view_name)
    return f"{url}?condominio={condominio.pk}"


def _destino_do_pai(request, condominio_id, opcoes: dict) -> str:
    """Para onde voltar depois de mexer num vínculo: o pai que a tela indicou.

    ``opcoes`` liga o valor de "voltar" a (nome da view, chave). Sem indicação
    reconhecida, volta para a primeira opção — nunca para um endereço trazido
    pelo navegador.
    """
    escolhido = request.POST.get("voltar")
    if escolhido not in opcoes:
        escolhido = next(iter(opcoes))
    view_name, pk = opcoes[escolhido]
    return f"{reverse(view_name, args=[pk])}?condominio={condominio_id}"


# -- Tradução para a tela -------------------------------------------------------


def _situacao(ativo: bool, *, ativo_texto="Ativo", inativo_texto="Inativo") -> dict:
    """Como o estado de um cadastro deve ser lido e usado na lista."""
    return {
        "ativo": ativo,
        "texto": ativo_texto if ativo else inativo_texto,
        "tom": "success" if ativo else "secondary",
    }


def _resumo_de_pessoas(quantidade: int) -> str:
    """Como a lista conta quem está ligado a uma unidade ou a um veículo hoje."""
    if not quantidade:
        return "Ninguém vinculado no momento"
    if quantidade == 1:
        return "1 pessoa vinculada"
    return f"{quantidade} pessoas vinculadas"


def _resumo_de_unidades(rotulos: set[str]) -> str:
    """A que unidades uma pessoa ou um veículo se ligam hoje.

    Nomes ordenados para que a frase não mude de ordem entre uma carga e outra.
    """
    ordenadas = sorted(rotulos)
    if not ordenadas:
        return "Sem unidade no momento"
    if len(ordenadas) == 1:
        return f"Unidade {ordenadas[0]}"
    if len(ordenadas) == 2:
        return f"Unidades {ordenadas[0]} e {ordenadas[1]}"
    return f"Unidades {ordenadas[0]}, {ordenadas[1]} e mais {len(ordenadas) - 2}"


def _unidades_para_tela(condominio, termo="", numero=1):
    """A página de unidades do condomínio, como a lista precisa dela.

    Quem está ligado à unidade hoje vem no mesmo carregamento da lista — é uma
    conta derivada dos vínculos vigentes, não uma coluna guardada.
    """
    unidades = (
        Unidade.objects.filter(condominio=condominio)
        .buscar(termo)
        .prefetch_related(
            Prefetch(
                "vinculos_pessoa",
                queryset=VinculoPessoaUnidade.objects.vigentes().select_related(
                    "pessoa"
                ),
            )
        )
        .order_by("bloco_torre", "identificador")
    )
    pagina = paginar(unidades, numero)
    pagina.itens = [
        {
            "pk": unidade.pk,
            "identificador": unidade.identificador,
            "bloco_torre": unidade.bloco_torre,
            "andar": unidade.andar,
            "rotulo": unidade.rotulo,
            "pessoas_resumo": _resumo_de_pessoas(
                len({vinculo.pessoa_id for vinculo in unidade.vinculos_pessoa.all()})
            ),
            **_situacao(unidade.ativa, ativo_texto="Ativa", inativo_texto="Inativa"),
        }
        for unidade in pagina
    ]
    return pagina


def _unidade_para_tela(unidade) -> dict:
    """Uma unidade, como a ficha precisa dela."""
    return {
        "pk": unidade.pk,
        "identificador": unidade.identificador,
        "bloco_torre": unidade.bloco_torre,
        "andar": unidade.andar,
        "complemento": unidade.complemento,
        "rotulo": unidade.rotulo,
        "observacao_interna": unidade.observacao_interna,
        "origem": unidade.get_origem_display(),
        "criada_em": unidade.criado_em,
        "atualizada_em": unidade.atualizado_em,
        **_situacao(unidade.ativa, ativo_texto="Ativa", inativo_texto="Inativa"),
    }


def _pessoas_para_tela(condominio, termo="", numero=1):
    """A página de pessoas do condomínio, como a lista precisa dela.

    O documento não aparece aqui: ele não identifica a pessoa na lista, e é
    dado protegido.
    """
    pessoas = (
        Pessoa.objects.filter(condominio=condominio)
        .buscar(termo)
        .prefetch_related(
            Prefetch(
                "vinculos_unidade",
                queryset=VinculoPessoaUnidade.objects.vigentes().select_related(
                    "unidade"
                ),
            )
        )
        .order_by("nome")
    )
    pagina = paginar(pessoas, numero)
    pagina.itens = [
        {
            "pk": pessoa.pk,
            "nome": pessoa.nome,
            "chamado": pessoa.nome_social or pessoa.nome,
            "tipo": pessoa.get_tipo_pessoa_display(),
            "telefone": pessoa.telefone,
            "whatsapp": pessoa.whatsapp,
            "email": pessoa.email,
            "unidades_resumo": _resumo_de_unidades(
                {vinculo.unidade.rotulo for vinculo in pessoa.vinculos_unidade.all()}
            ),
            **_situacao(pessoa.ativa, ativo_texto="Ativa", inativo_texto="Inativa"),
        }
        for pessoa in pagina
    ]
    return pagina


def _pessoa_para_tela(pessoa) -> dict:
    """Uma pessoa, como a ficha precisa dela."""
    return {
        "pk": pessoa.pk,
        "nome": pessoa.nome,
        "nome_social": pessoa.nome_social,
        "chamado": pessoa.nome_social or pessoa.nome,
        "tipo": pessoa.get_tipo_pessoa_display(),
        "telefone": pessoa.telefone,
        "whatsapp": pessoa.whatsapp,
        "email": pessoa.email,
        "documento": pessoa.documento_formatado,
        "observacao_interna": pessoa.observacao_interna,
        "origem": pessoa.get_origem_display(),
        "criada_em": pessoa.criado_em,
        "atualizada_em": pessoa.atualizado_em,
        **_situacao(pessoa.ativa, ativo_texto="Ativa", inativo_texto="Inativa"),
    }


def _veiculos_para_tela(condominio, termo="", numero=1):
    """A página de veículos do condomínio, como a lista precisa deles.

    Quem usa o veículo e a que unidade ele se relaciona hoje vêm no mesmo
    carregamento — são vínculos vigentes, derivados na hora.
    """
    veiculos = (
        Veiculo.objects.filter(condominio=condominio)
        .buscar(termo)
        .prefetch_related(
            Prefetch(
                "vinculos_pessoa",
                queryset=VinculoVeiculoPessoa.objects.vigentes().select_related(
                    "pessoa"
                ),
            ),
            Prefetch(
                "vinculos_unidade",
                queryset=VinculoVeiculoUnidade.objects.vigentes().select_related(
                    "unidade"
                ),
            ),
        )
        .order_by("placa")
    )
    pagina = paginar(veiculos, numero)
    pagina.itens = [
        {
            **_veiculo_para_tela(veiculo),
            "pessoas_resumo": _resumo_de_pessoas(
                len({vinculo.pessoa_id for vinculo in veiculo.vinculos_pessoa.all()})
            ),
            "unidades_resumo": _resumo_de_unidades(
                {vinculo.unidade.rotulo for vinculo in veiculo.vinculos_unidade.all()}
            ),
        }
        for veiculo in pagina
    ]
    return pagina


def _veiculo_para_tela(veiculo) -> dict:
    """Um veículo, como a lista e a ficha precisam dele."""
    return {
        "pk": veiculo.pk,
        "placa": veiculo.placa,
        "marca": veiculo.marca,
        "modelo": veiculo.modelo,
        "cor": veiculo.cor,
        "descricao": " · ".join(p for p in (veiculo.marca, veiculo.modelo) if p),
        "observacao_interna": veiculo.observacao_interna,
        "origem": veiculo.get_origem_display(),
        "criado_em": veiculo.criado_em,
        "atualizado_em": veiculo.atualizado_em,
        **_situacao(veiculo.ativo),
    }


def _vinculo_pessoa_unidade_para_tela(vinculo) -> dict:
    """Um vínculo entre pessoa e unidade, como as fichas precisam dele."""
    return {
        "pk": vinculo.pk,
        "pessoa": vinculo.pessoa.nome_social or vinculo.pessoa.nome,
        "pessoa_pk": vinculo.pessoa_id,
        "unidade": vinculo.unidade.rotulo,
        "unidade_pk": vinculo.unidade_id,
        "papel": vinculo.get_tipo_vinculo_display(),
        "inicio": vinculo.data_inicio,
        "fim": vinculo.data_fim,
        "vigente": vinculo.vigente,
        "observacao_interna": vinculo.observacao_interna,
    }


def _vinculo_veiculo_pessoa_para_tela(vinculo) -> dict:
    """Um vínculo entre veículo e pessoa, como as fichas precisam dele."""
    return {
        "pk": vinculo.pk,
        "pessoa": vinculo.pessoa.nome_social or vinculo.pessoa.nome,
        "pessoa_pk": vinculo.pessoa_id,
        "veiculo": vinculo.veiculo.placa,
        "veiculo_pk": vinculo.veiculo_id,
        "inicio": vinculo.data_inicio,
        "fim": vinculo.data_fim,
        "vigente": vinculo.vigente,
        "observacao": vinculo.observacao,
    }


def _vinculo_veiculo_unidade_para_tela(vinculo) -> dict:
    """Um vínculo entre veículo e unidade, para a ficha da unidade."""
    return {
        "pk": vinculo.pk,
        "veiculo": vinculo.veiculo.placa,
        "veiculo_pk": vinculo.veiculo_id,
        "marca": vinculo.veiculo.marca,
        "modelo": vinculo.veiculo.modelo,
        "inicio": vinculo.data_inicio,
        "fim": vinculo.data_fim,
        "vigente": vinculo.vigente,
        "observacao": vinculo.observacao,
    }


def _vinculo_veiculo_unidade_para_tela_linha(vinculo) -> dict:
    """Um vínculo entre veículo e unidade, para a ficha do veículo."""
    linha = _vinculo_veiculo_unidade_para_tela(vinculo)
    linha["unidade"] = vinculo.unidade.rotulo
    linha["unidade_pk"] = vinculo.unidade_id
    return linha


def _vinculos_veiculo_unidade_para_tela(unidade) -> list[dict]:
    """Os veículos relacionados hoje a esta unidade."""
    vinculos = (
        VinculoVeiculoUnidade.objects.da_unidade(unidade)
        .vigentes()
        .select_related("veiculo")
        .order_by("veiculo__placa")
    )
    return [_vinculo_veiculo_unidade_para_tela(vinculo) for vinculo in vinculos]
