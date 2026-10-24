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
from django.core.exceptions import (
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
)
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.accounts import avatar, primeiro_acesso
from apps.accounts.forms import DefinirSenhaPelaGestaoForm
from apps.comunidade.permissoes import (
    condominios_para_ver as condominios_da_comunidade_para_ver,
    pode_operar as pode_operar_comunidade,
)
from apps.condominios import services as servicos_de_acesso
from apps.condominios.models import AcessoUsuarioCondominio, Condominio
from apps.core.paginacao import paginar
from apps.core.storage import ErroDeStorage
from apps.governanca.permissoes import (
    pode_gerenciar_tipos as pode_gerenciar_tipos_de_governanca,
    pode_ver_governanca,
)

from . import services
from .forms import (
    AdicionarAcessoForm,
    CondominioForm,
    EditarAcessoForm,
    EquipamentoForm,
    EspecialidadeFornecedorForm,
    FornecedorForm,
    LocalForm,
    TipoEquipamentoForm,
    TipoLocalOperacionalForm,
)
from .models import (
    Equipamento,
    EspecialidadeFornecedor,
    Fornecedor,
    LocalOperacional,
    TipoEquipamento,
    TipoLocalOperacional,
)
from .permissoes import (
    condominios_para_ver,
    pode_criar_condominios,
    pode_gerenciar_cadastros,
)


# -- Cadastro do condomínio -----------------------------------------------------


@login_required
def condominios(request):
    """A carteira de condomínios cujos cadastros esta pessoa pode consultar."""
    return _render_condominios(request)


@login_required
def condominio(request):
    """Compatibilidade com o endereço antigo do cadastro do condomínio.

    O módulo agora abre a ficha em modal dentro da lista de condomínios, mas o
    endereço antigo continua apontando para esse mesmo fluxo.
    """
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    pode_gerenciar = pode_gerenciar_cadastros(request.user, atual)

    if request.method == "POST":
        if not pode_gerenciar:
            raise PermissionDenied("Esta pessoa não mantém o cadastro deste condomínio.")

        formulario = CondominioForm(request.POST, instance=atual)
        if not formulario.is_valid():
            return _render_condominios(
                request,
                formulario_edicao=formulario,
                item_edicao=atual,
                modal="editar",
                status=400,
            )

        services.salvar_condominio(condominio=atual, dados=formulario.cleaned_data)
        messages.success(request, "Cadastro do condomínio atualizado.")
        return redirect(reverse("cadastros:condominios"))

    return _render_condominios(
        request,
        item_edicao=atual,
        modal="editar",
    )


@login_required
@require_POST
def criar_condominio(request):
    """Registra um condomínio novo e já entrega à pessoa criadora o acesso inicial."""
    if not pode_criar_condominios(request.user):
        raise PermissionDenied("Esta pessoa não pode abrir novos condomínios.")

    formulario = CondominioForm(request.POST)
    if not formulario.is_valid():
        return _render_condominios(
            request,
            formulario_criacao=formulario,
            modal="criar",
            status=400,
        )

    try:
        condominio = services.criar_condominio(
            dados=formulario.cleaned_data,
            criado_por=request.user,
        )
    except services.ErroDeCadastro as erro:
        messages.error(request, str(erro))
        return _render_condominios(
            request,
            formulario_criacao=formulario,
            modal="criar",
            status=400,
        )

    messages.success(request, "Condomínio cadastrado.")
    return redirect(reverse("cadastros:condominios"))


@login_required
@require_POST
def editar_condominio(request, pk):
    """Corrige o cadastro de um condomínio já visível para esta pessoa."""
    condominios = _condominios_para_ver(request)
    condominio = _obter_ou_404(condominios, pk)
    if not pode_gerenciar_cadastros(request.user, condominio):
        raise PermissionDenied("Esta pessoa não mantém o cadastro deste condomínio.")

    formulario = CondominioForm(request.POST, instance=condominio)
    if not formulario.is_valid():
        return _render_condominios(
            request,
            formulario_edicao=formulario,
            item_edicao=condominio,
            modal="editar",
            status=400,
        )

    try:
        services.salvar_condominio(condominio=condominio, dados=formulario.cleaned_data)
    except services.ErroDeCadastro as erro:
        messages.error(request, str(erro))
        return _render_condominios(
            request,
            formulario_edicao=formulario,
            item_edicao=condominio,
            modal="editar",
            status=400,
        )

    messages.success(request, "Cadastro do condomínio atualizado.")
    return redirect(reverse("cadastros:condominios"))


@login_required
def pessoas_e_unidades(request):
    """Atalhos do contexto humano do condomínio: unidades, pessoas e veículos."""
    condominios = condominios_da_comunidade_para_ver(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomínio disponível.")

    atual = _condominio_atual(request, condominios)
    contexto = {
        "condominios": condominios,
        "condominio_atual": atual,
        "mostrar_seletor": condominios.count() > 1,
        "atalhos": [
            {
                "nome": "Unidades",
                "descricao": "Apartamentos, ocupação e vínculos operacionais.",
                "icone": "bx-building-house",
                "url": _destino("comunidade:unidades", atual),
            },
            {
                "nome": "Pessoas",
                "descricao": "Moradores, contatos e relações com as unidades.",
                "icone": "bx-user",
                "url": _destino("comunidade:pessoas", atual),
            },
            {
                "nome": "Veículos",
                "descricao": "Placas e vínculos com pessoas e unidades.",
                "icone": "bx-car",
                "url": _destino("comunidade:veiculos", atual),
            },
        ],
        "pode_operar": pode_operar_comunidade(request.user, atual),
    }
    return render(
        request,
        "pages/cadastros/pessoas_e_unidades.html",
        contexto,
    )


@login_required
def tipos_e_categorias(request):
    """Agrupa os catálogos auxiliares sem competir com as ações principais."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    pode_ver_governanca_no_condominio = pode_ver_governanca(request.user, atual)
    pode_gerenciar_governanca_no_condominio = pode_gerenciar_tipos_de_governanca(
        request.user, atual
    )

    contexto = {
        **base,
        "grupos": [
            {
                "titulo": "Cadastros estruturais",
                "itens": [
                    {
                        "nome": "Tipos de local",
                        "descricao": "Nomeiam os lugares usados em areas, equipamentos e manutencoes.",
                        "url": _destino("cadastros:tipos_de_local", atual),
                        "modo": (
                            "Pode gerenciar"
                            if pode_gerenciar_cadastros(request.user, atual)
                            else "Somente leitura"
                        ),
                    },
                    {
                        "nome": "Tipos de equipamento",
                        "descricao": "Classificam o que existe no condominio e precisa de manutencao.",
                        "url": _destino("cadastros:tipos_de_equipamento", atual),
                        "modo": (
                            "Pode gerenciar"
                            if pode_gerenciar_cadastros(request.user, atual)
                            else "Somente leitura"
                        ),
                    },
                    {
                        "nome": "Especialidades de fornecedor",
                        "descricao": "Organizam o que cada fornecedor atende.",
                        "url": _destino("cadastros:especialidades", atual),
                        "modo": (
                            "Pode gerenciar"
                            if pode_gerenciar_cadastros(request.user, atual)
                            else "Somente leitura"
                        ),
                    },
                ],
            },
            {
                "titulo": "Governanca documental",
                "itens": [
                    {
                        "nome": "Tipos de contrato",
                        "descricao": "Definem o vocabulario usado no cadastro de contratos.",
                        "url": _destino("governanca:tipos_de_contrato", atual),
                        "modo": (
                            "Pode gerenciar"
                            if pode_gerenciar_governanca_no_condominio
                            else "Somente leitura"
                        ),
                    },
                    {
                        "nome": "Tipos de documento",
                        "descricao": "Definem validade e alerta dos documentos operacionais.",
                        "url": _destino("governanca:tipos_de_documento", atual),
                        "modo": (
                            "Pode gerenciar"
                            if pode_gerenciar_governanca_no_condominio
                            else "Somente leitura"
                        ),
                    },
                ]
                if pode_ver_governanca_no_condominio
                else [],
            },
        ],
    }
    return render(
        request,
        "pages/cadastros/tipos_e_categorias.html",
        contexto,
    )


def _render_condominios(
    request,
    *,
    formulario_criacao=None,
    formulario_edicao=None,
    item_edicao=None,
    modal=None,
    status=200,
):
    """Monta a carteira de condomínios e, quando pedido, a ficha em modal."""
    base = _base_da_pagina(request)
    pode_criar = pode_criar_condominios(request.user)

    if item_edicao is None:
        item_edicao = _item_em_edicao(request, Condominio, base["condominios"])
    if item_edicao is None:
        if modal == "editar":
            modal = None
    elif modal is None:
        modal = "editar"

    pode_gerenciar_item = bool(
        item_edicao and pode_gerenciar_cadastros(request.user, item_edicao)
    )

    contexto = {
        **base,
        "condominios_lista": _condominios_para_tela(request, base["condominios"]),
        "pode_criar_condominio": pode_criar,
        "formulario_condominio": formulario_criacao or CondominioForm(),
        "formulario_edicao": formulario_edicao
        or (
            CondominioForm(
                instance=item_edicao,
                somente_leitura=not pode_gerenciar_item,
            )
            if item_edicao
            else None
        ),
        "item_edicao": item_edicao,
        "pode_gerenciar_item_edicao": pode_gerenciar_item,
        "modal_aberto": modal,
    }
    return render(request, "pages/cadastros/condominios.html", contexto, status=status)


# -- Usuários e acessos ---------------------------------------------------------
#
# Dar acesso é uma decisão sobre pessoas, e não um cadastro qualquer. A tela
# mostra quem tem vínculo com o condomínio e permite acrescentar, mudar o papel,
# ativar e desativar. Nada disso apaga: o vínculo é desativado, e o histórico de
# quem mudou o quê fica registrado. Quem responde pelo condomínio decide; a
# supervisão consulta. A regra — inclusive a do último administrador — vive no
# domínio de condomínios, não na tela.


@login_required
def acessos(request):
    """Quem tem acesso a este condomínio e com qual papel."""
    return _render_acessos(request)


@login_required
@require_POST
def adicionar_acesso(request):
    """Dá acesso a uma pessoa neste condomínio, com o papel escolhido.

    A senha inicial tem dois caminhos, e quem escolhe é a tela: a própria
    pessoa define a dela no primeiro acesso — o caminho de sempre —, ou a
    administração define uma agora, para quando não há e-mail que entregue o
    endereço. A foto é opcional e nunca desfaz o acesso: se o armazenamento
    recusar a imagem depois do vínculo criado, o que houve é um aviso, e a
    gestão corrige pelo próprio acesso.
    """
    atual = _condominio_para_gestao(request)
    formulario = AdicionarAcessoForm(
        request.POST, request.FILES, pode_definir_senha=request.user.is_staff
    )
    if not formulario.is_valid():
        return _render_acessos(
            request, formulario_acesso=formulario, modal="adicionar", status=400
        )

    try:
        vinculo = servicos_de_acesso.adicionar_acesso(
            condominio=atual,
            email=formulario.cleaned_data["email"],
            nome_completo=formulario.cleaned_data["nome_completo"],
            papel=formulario.cleaned_data["papel"],
            executor=request.user,
            senha=(
                formulario.cleaned_data["senha"]
                if formulario.senha_definida_aqui
                else ""
            ),
        )
    except servicos_de_acesso.ErroDeAcesso as erro:
        messages.error(request, str(erro))
        return _render_acessos(
            request, formulario_acesso=formulario, modal="adicionar", status=400
        )

    if primeiro_acesso.precisa_definir_senha(vinculo.usuario):
        try:
            primeiro_acesso.enviar_endereco_por_email(
                conta=vinculo.usuario,
                request=request,
                condominio_nome=atual.nome,
            )
        except primeiro_acesso.ErroDeEntregaDoPrimeiroAcesso:
            messages.warning(
                request,
                (
                    "Acesso concedido, mas não foi possível preparar as instruções "
                    "de senha agora. Use a ação 'Reenviar primeiro acesso' para "
                    "tentar novamente."
                ),
            )
        else:
            servicos_de_acesso.registrar_instrucoes_de_senha_enviadas(
                acesso=vinculo, executor=request.user, primeiro_acesso=True
            )
            messages.success(
                request,
                f"Acesso concedido. {primeiro_acesso.recado_de_entrega(vinculo.usuario.email)}",
            )
    else:
        messages.success(request, "Acesso concedido.")

    foto = formulario.cleaned_data["foto"]
    if foto:
        try:
            avatar.definir_foto(vinculo.usuario, foto)
        except (ErroDeStorage, ImproperlyConfigured):
            messages.warning(
                request,
                (
                    "Acesso concedido, mas não foi possível guardar a foto agora. "
                    "Use a ação 'Editar acesso' para tentar novamente."
                ),
            )
    return redirect(_destino("cadastros:acessos", atual))


@login_required
@require_POST
def gerar_link_primeiro_acesso(request, pk):
    """Pede que as instruções de senha sejam enviadas para a própria pessoa.

    A gestão não escolhe senha de ninguém: ela apenas dispara o envio, e a
    própria pessoa usa o endereço recebido para definir a primeira senha ou
    criar uma nova. A ação muda de nome conforme o estado da conta — reenviar o
    primeiro acesso ou enviar uma redefinição — mas o que ela faz é sempre a
    mesma coisa. O endereço não fica guardado: ele é calculado na hora.
    """
    atual = _condominio_para_gestao(request)
    vinculo = _obter_ou_404(
        AcessoUsuarioCondominio.objects.filter(condominio=atual), pk
    )
    primeira_senha = primeiro_acesso.precisa_definir_senha(vinculo.usuario)
    try:
        primeiro_acesso.enviar_endereco_por_email(
            conta=vinculo.usuario,
            request=request,
            condominio_nome=atual.nome,
        )
    except primeiro_acesso.ErroDeEntregaDoPrimeiroAcesso:
        messages.error(
            request,
            (
                "Não foi possível preparar as instruções agora. Confira a "
                "configuração de entrega e tente novamente."
            ),
        )
    else:
        servicos_de_acesso.registrar_instrucoes_de_senha_enviadas(
            acesso=vinculo, executor=request.user, primeiro_acesso=primeira_senha
        )
        messages.success(
            request, primeiro_acesso.recado_de_entrega(vinculo.usuario.email)
        )
    return redirect(_destino("cadastros:acessos", atual))


@login_required
@require_POST
def editar_acesso(request, pk):
    """Corrige o que a pessoa é neste condomínio e a foto que a representa.

    Duas coisas vivem aqui, e as duas são do vínculo, não da conta: o papel
    exercido neste condomínio — que não muda o que a pessoa é em outro — e a
    imagem que a identifica na lista. A senha não entra por esta porta: ela é
    escolha da própria pessoa, e a administração tem tela própria para isso.

    A imagem tem três respostas possíveis, e todas explícitas: manter a atual,
    substituí-la por outra, ou tirá-la. Tirar é declarado no formulário, nunca
    a ausência de arquivo — senão salvar sem escolher nada apagaria a foto de
    quem não pediu para apagá-la.

    Se o armazenamento recusar a imagem, o papel já valeu: o que houve é um
    aviso, e a gestão tenta de novo pelo próprio acesso.
    """
    atual = _condominio_para_gestao(request)
    vinculo = _obter_ou_404(
        AcessoUsuarioCondominio.objects.filter(condominio=atual), pk
    )
    formulario = EditarAcessoForm(request.POST, request.FILES)
    if not formulario.is_valid():
        return _render_acessos(
            request,
            formulario_edicao=formulario,
            item_edicao=vinculo,
            modal="editar",
            status=400,
        )

    try:
        servicos_de_acesso.alterar_papel(
            acesso=vinculo, papel=formulario.cleaned_data["papel"], executor=request.user
        )
    except servicos_de_acesso.ErroDeAcesso as erro:
        messages.error(request, str(erro))
        return _render_acessos(
            request,
            formulario_edicao=formulario,
            item_edicao=vinculo,
            modal="editar",
            status=400,
        )

    foto = formulario.cleaned_data["foto"]
    if foto:
        try:
            avatar.definir_foto(vinculo.usuario, foto)
        except (ErroDeStorage, ImproperlyConfigured):
            messages.warning(
                request,
                (
                    "O papel foi atualizado, mas não foi possível guardar a foto "
                    "agora. Tente novamente pela ação 'Editar acesso'."
                ),
            )
    elif formulario.cleaned_data.get("remover_foto"):
        avatar.remover_foto(vinculo.usuario)

    messages.success(request, "Acesso atualizado.")
    return redirect(_destino("cadastros:acessos", atual))


@login_required
@require_POST
def definir_senha_acesso(request, pk):
    """Define uma nova senha para a conta de uma pessoa deste condomínio.

    Esta é a única porta em que alguém escolhe a senha de outra pessoa, e ela
    existe para a administração técnica: sem ela, um ambiente sem entrega de
    e-mail não teria como devolver o acesso a quem perdeu a senha.

    Por isso a decisão é de quem tem acesso administrativo, e não de quem
    apenas responde pelo condomínio: a síndica vê a tela — a existência já lhe
    é legítima — mas não define senha de ninguém.

    A senha não é guardada em texto, não aparece na tela e não entra no
    histórico: quem a protege é o Django. O que fica registrado é quem definiu,
    para quem e quando.
    """
    atual = _condominio_para_gestao(request)
    if not request.user.is_staff:
        raise PermissionDenied("Esta pessoa não administra contas.")

    vinculo = _obter_ou_404(
        AcessoUsuarioCondominio.objects.filter(condominio=atual), pk
    )
    formulario = DefinirSenhaPelaGestaoForm(request.POST, conta=vinculo.usuario)
    if not formulario.is_valid():
        return _render_acessos(
            request,
            formulario_senha=formulario,
            item_edicao=vinculo,
            modal="senha",
            status=400,
        )

    servicos_de_acesso.definir_senha_de_acesso(
        acesso=vinculo,
        senha=formulario.cleaned_data["senha"],
        executor=request.user,
    )
    messages.success(request, "Senha atualizada com sucesso.")
    return redirect(_destino("cadastros:acessos", atual))


@login_required
@require_POST
def alternar_acesso(request, pk):
    """Retira ou devolve o acesso de uma pessoa, sem apagar o vínculo."""
    atual = _condominio_para_gestao(request)
    vinculo = _obter_ou_404(
        AcessoUsuarioCondominio.objects.filter(condominio=atual), pk
    )
    novo = not vinculo.ativo
    try:
        servicos_de_acesso.definir_ativo_acesso(
            acesso=vinculo, ativo=novo, executor=request.user
        )
    except servicos_de_acesso.ErroDeAcesso as erro:
        messages.error(request, str(erro))
        return redirect(_destino("cadastros:acessos", atual))

    if novo:
        messages.success(request, "Acesso reativado.")
    else:
        messages.success(request, "Acesso retirado.")
    return redirect(_destino("cadastros:acessos", atual))


def _render_acessos(
    request,
    *,
    formulario_acesso=None,
    formulario_edicao=None,
    formulario_senha=None,
    item_edicao=None,
    modal=None,
    status=200,
):
    """Monta a lista de acessos com o formulário que a tela pediu."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    pode_gerenciar = pode_gerenciar_cadastros(request.user, atual)
    pode_definir_senha = bool(request.user.is_staff and pode_gerenciar)

    if item_edicao is None and pode_gerenciar:
        item_edicao = _item_em_edicao(
            request,
            AcessoUsuarioCondominio,
            base["condominios"],
            abrir=("editar", "senha"),
        )
    if item_edicao is None:
        if modal in {"editar", "senha"}:
            modal = None
    elif modal is None:
        modal = "senha" if request.GET.get("abrir") == "senha" else "editar"

    vinculos = (
        AcessoUsuarioCondominio.objects.filter(condominio=atual)
        .select_related("usuario", "condominio")
        .order_by("usuario__full_name", "usuario__email")
    )
    if base["termo"]:
        # Quem tem acesso se procura por quem é: o nome da pessoa ou o e-mail
        # com que ela entra.
        vinculos = vinculos.filter(
            Q(usuario__full_name__icontains=base["termo"])
            | Q(usuario__email__icontains=base["termo"])
        )
    pagina_acessos = paginar(vinculos, request.GET.get("page"))
    pagina_acessos.itens = [
        _acesso_para_tela(item, pode_definir_senha=pode_definir_senha)
        for item in pagina_acessos
    ]

    contexto = {
        **base,
        "pode_gerenciar": pode_gerenciar,
        "acessos": pagina_acessos.itens,
        "pagina_acessos": pagina_acessos,
        "formulario_acesso": formulario_acesso
        or AdicionarAcessoForm(pode_definir_senha=request.user.is_staff),
        "formulario_edicao": formulario_edicao
        or (
            EditarAcessoForm(initial={"papel": item_edicao.papel})
            if item_edicao
            else None
        ),
        "formulario_senha": formulario_senha
        or (
            DefinirSenhaPelaGestaoForm(conta=item_edicao.usuario)
            if item_edicao and pode_definir_senha
            else None
        ),
        "item_edicao": item_edicao,
        "foto_em_edicao": _foto_do_acesso(item_edicao.usuario) if item_edicao else "",
        "modal_aberto": modal,
    }
    return render(request, "pages/cadastros/acessos.html", contexto, status=status)


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
    formulario = LocalForm(request.POST, condominio=atual)
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
    formulario = LocalForm(request.POST, instance=local, condominio=atual)
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

    pagina_locais = _locais_para_tela(atual, base["termo"], request.GET.get("page"))

    contexto = {
        **base,
        "pode_gerenciar": pode_gerenciar,
        "locais": pagina_locais.itens,
        "pagina_locais": pagina_locais,
        "formulario_local": formulario_criacao or LocalForm(condominio=atual),
        "formulario_edicao": formulario_edicao
        or (LocalForm(instance=item_edicao, condominio=atual) if item_edicao else None),
        "item_edicao": item_edicao,
        "modal_aberto": modal,
    }
    return render(request, "pages/cadastros/locais.html", contexto, status=status)


# -- Tipos de local -------------------------------------------------------------
#
# O catálogo continua ligado a quem o usa: os tipos de local nomeiam os locais,
# os de equipamento nomeiam os equipamentos, e as especialidades organizam os
# fornecedores. O que mudou foi a navegação: o catálogo agora entra pelo
# agrupador "Tipos e categorias", sem competir com a ação principal da lista.


@login_required
def tipos_de_local(request):
    """As classificações com que este condomínio nomeia os seus lugares."""
    return _render_tipos_de_local(request)


@login_required
@require_POST
def criar_tipo_local(request):
    """Registra um tipo de local novo. O catálogo é de quem responde pelo condomínio."""
    atual = _condominio_para_gestao(request)
    formulario = TipoLocalOperacionalForm(request.POST, condominio=atual)
    if not formulario.is_valid():
        return _render_tipos_de_local(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    try:
        services.criar_tipo_local(
            condominio=atual, criado_por=request.user, dados=formulario.cleaned_data
        )
    except services.ErroDeCadastro as erro:
        messages.error(request, str(erro))
        return _render_tipos_de_local(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    messages.success(request, "Tipo de local cadastrado.")
    return redirect(_destino("cadastros:tipos_de_local", atual))


@login_required
@require_POST
def editar_tipo_local(request, pk):
    """Corrige o nome e a descrição de um tipo de local."""
    atual = _condominio_para_gestao(request)
    tipo = _obter_ou_404(TipoLocalOperacional.objects.filter(condominio=atual), pk)
    formulario = TipoLocalOperacionalForm(request.POST, instance=tipo, condominio=atual)
    if not formulario.is_valid():
        return _render_tipos_de_local(
            request,
            formulario_edicao=formulario,
            item_edicao=tipo,
            modal="editar",
            status=400,
        )

    try:
        services.editar_tipo_local(tipo=tipo, dados=formulario.cleaned_data)
    except services.ErroDeCadastro as erro:
        messages.error(request, str(erro))
        return _render_tipos_de_local(
            request,
            formulario_edicao=formulario,
            item_edicao=tipo,
            modal="editar",
            status=400,
        )

    messages.success(request, "Tipo de local atualizado.")
    return redirect(_destino("cadastros:tipos_de_local", atual))


@login_required
@require_POST
def alternar_tipo_local(request, pk):
    """Tira um tipo de circulação, ou o devolve, sem apagar o que já o usa."""
    atual = _condominio_para_gestao(request)
    tipo = _obter_ou_404(TipoLocalOperacional.objects.filter(condominio=atual), pk)
    return _alternar(
        request, tipo, destino="cadastros:tipos_de_local", nome="Tipo de local"
    )


def _render_tipos_de_local(
    request,
    *,
    formulario_criacao=None,
    formulario_edicao=None,
    item_edicao=None,
    modal=None,
    status=200,
):
    """Monta a lista de tipos de local com o formulário que a tela pediu."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    pode_gerenciar = pode_gerenciar_cadastros(request.user, atual)

    if item_edicao is None and pode_gerenciar:
        item_edicao = _item_em_edicao(
            request, TipoLocalOperacional, base["condominios"]
        )
    if item_edicao is None:
        if modal == "editar":
            modal = None
    elif modal is None:
        modal = "editar"

    contexto = {
        **base,
        "pode_gerenciar": pode_gerenciar,
        "tipos": _tipos_para_tela(TipoLocalOperacional.objects.filter(condominio=atual)),
        "formulario_tipo": formulario_criacao
        or TipoLocalOperacionalForm(condominio=atual),
        "formulario_edicao": formulario_edicao
        or (
            TipoLocalOperacionalForm(instance=item_edicao, condominio=atual)
            if item_edicao
            else None
        ),
        "item_edicao": item_edicao,
        "modal_aberto": modal,
    }
    return render(
        request, "pages/cadastros/tipos_de_local.html", contexto, status=status
    )


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

    pagina_equipamentos = _equipamentos_para_tela(
        atual, base["termo"], request.GET.get("page")
    )

    contexto = {
        **base,
        "pode_gerenciar": pode_gerenciar,
        "equipamentos": pagina_equipamentos.itens,
        "pagina_equipamentos": pagina_equipamentos,
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


# -- Tipos de equipamento -------------------------------------------------------


@login_required
def tipos_de_equipamento(request):
    """As classificações com que este condomínio nomeia o que mantém."""
    return _render_tipos_de_equipamento(request)


@login_required
@require_POST
def criar_tipo_equipamento(request):
    """Registra um tipo de equipamento novo no condomínio."""
    atual = _condominio_para_gestao(request)
    formulario = TipoEquipamentoForm(request.POST, condominio=atual)
    if not formulario.is_valid():
        return _render_tipos_de_equipamento(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    try:
        services.criar_tipo_equipamento(
            condominio=atual, criado_por=request.user, dados=formulario.cleaned_data
        )
    except services.ErroDeCadastro as erro:
        messages.error(request, str(erro))
        return _render_tipos_de_equipamento(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    messages.success(request, "Tipo de equipamento cadastrado.")
    return redirect(_destino("cadastros:tipos_de_equipamento", atual))


@login_required
@require_POST
def editar_tipo_equipamento(request, pk):
    """Corrige o nome e a descrição de um tipo de equipamento."""
    atual = _condominio_para_gestao(request)
    tipo = _obter_ou_404(TipoEquipamento.objects.filter(condominio=atual), pk)
    formulario = TipoEquipamentoForm(request.POST, instance=tipo, condominio=atual)
    if not formulario.is_valid():
        return _render_tipos_de_equipamento(
            request,
            formulario_edicao=formulario,
            item_edicao=tipo,
            modal="editar",
            status=400,
        )

    try:
        services.editar_tipo_equipamento(tipo=tipo, dados=formulario.cleaned_data)
    except services.ErroDeCadastro as erro:
        messages.error(request, str(erro))
        return _render_tipos_de_equipamento(
            request,
            formulario_edicao=formulario,
            item_edicao=tipo,
            modal="editar",
            status=400,
        )

    messages.success(request, "Tipo de equipamento atualizado.")
    return redirect(_destino("cadastros:tipos_de_equipamento", atual))


@login_required
@require_POST
def alternar_tipo_equipamento(request, pk):
    """Tira um tipo de circulação, ou o devolve, sem apagar o que já o usa."""
    atual = _condominio_para_gestao(request)
    tipo = _obter_ou_404(TipoEquipamento.objects.filter(condominio=atual), pk)
    return _alternar(
        request, tipo, destino="cadastros:tipos_de_equipamento", nome="Tipo de equipamento"
    )


def _render_tipos_de_equipamento(
    request,
    *,
    formulario_criacao=None,
    formulario_edicao=None,
    item_edicao=None,
    modal=None,
    status=200,
):
    """Monta a lista de tipos de equipamento com o formulário que a tela pediu."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    pode_gerenciar = pode_gerenciar_cadastros(request.user, atual)

    if item_edicao is None and pode_gerenciar:
        item_edicao = _item_em_edicao(request, TipoEquipamento, base["condominios"])
    if item_edicao is None:
        if modal == "editar":
            modal = None
    elif modal is None:
        modal = "editar"

    contexto = {
        **base,
        "pode_gerenciar": pode_gerenciar,
        "tipos": _tipos_para_tela(TipoEquipamento.objects.filter(condominio=atual)),
        "formulario_tipo": formulario_criacao or TipoEquipamentoForm(condominio=atual),
        "formulario_edicao": formulario_edicao
        or (
            TipoEquipamentoForm(instance=item_edicao, condominio=atual)
            if item_edicao
            else None
        ),
        "item_edicao": item_edicao,
        "modal_aberto": modal,
    }
    return render(
        request, "pages/cadastros/tipos_de_equipamento.html", contexto, status=status
    )


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
        condominio=atual,
        criado_por=request.user,
        dados=_campos_do_modelo(Fornecedor, formulario.cleaned_data),
        especialidades=formulario.cleaned_data.get("especialidades", ()),
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

    services.editar_fornecedor(
        fornecedor=fornecedor,
        dados=_campos_do_modelo(Fornecedor, formulario.cleaned_data),
        especialidades=formulario.cleaned_data.get("especialidades", ()),
        # Quem está na tela responde pela liberação aos moradores, se ela
        # acontecer nesta gravação. A decisão é do domínio; a tela só diz quem.
        responsavel=request.user,
    )
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

    pagina_fornecedores = _fornecedores_para_tela(
        atual, base["termo"], request.GET.get("page")
    )

    contexto = {
        **base,
        "pode_gerenciar": pode_gerenciar,
        "fornecedores": pagina_fornecedores.itens,
        "pagina_fornecedores": pagina_fornecedores,
        "formulario_fornecedor": formulario_criacao or FornecedorForm(condominio=atual),
        "formulario_edicao": formulario_edicao
        or (FornecedorForm(instance=item_edicao, condominio=atual) if item_edicao else None),
        "item_edicao": item_edicao,
        "modal_aberto": modal,
    }
    return render(request, "pages/cadastros/fornecedores.html", contexto, status=status)


# -- Especialidades -------------------------------------------------------------


@login_required
def especialidades(request):
    """O que os fornecedores deste condomínio sabem fazer."""
    return _render_especialidades(request)


@login_required
@require_POST
def criar_especialidade(request):
    """Registra uma especialidade nova no condomínio."""
    atual = _condominio_para_gestao(request)
    formulario = EspecialidadeFornecedorForm(request.POST, condominio=atual)
    if not formulario.is_valid():
        return _render_especialidades(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    try:
        services.criar_especialidade(
            condominio=atual, criado_por=request.user, dados=formulario.cleaned_data
        )
    except services.ErroDeCadastro as erro:
        messages.error(request, str(erro))
        return _render_especialidades(
            request, formulario_criacao=formulario, modal="criar", status=400
        )

    messages.success(request, "Especialidade cadastrada.")
    return redirect(_destino("cadastros:especialidades", atual))


@login_required
@require_POST
def editar_especialidade(request, pk):
    """Corrige o nome e a descrição de uma especialidade."""
    atual = _condominio_para_gestao(request)
    especialidade = _obter_ou_404(
        EspecialidadeFornecedor.objects.filter(condominio=atual), pk
    )
    formulario = EspecialidadeFornecedorForm(
        request.POST, instance=especialidade, condominio=atual
    )
    if not formulario.is_valid():
        return _render_especialidades(
            request,
            formulario_edicao=formulario,
            item_edicao=especialidade,
            modal="editar",
            status=400,
        )

    try:
        services.editar_especialidade(
            especialidade=especialidade, dados=formulario.cleaned_data
        )
    except services.ErroDeCadastro as erro:
        messages.error(request, str(erro))
        return _render_especialidades(
            request,
            formulario_edicao=formulario,
            item_edicao=especialidade,
            modal="editar",
            status=400,
        )

    messages.success(request, "Especialidade atualizada.")
    return redirect(_destino("cadastros:especialidades", atual))


@login_required
@require_POST
def alternar_especialidade(request, pk):
    """Tira uma especialidade de circulação, ou a devolve, sem apagar o vínculo."""
    atual = _condominio_para_gestao(request)
    especialidade = _obter_ou_404(
        EspecialidadeFornecedor.objects.filter(condominio=atual), pk
    )
    return _alternar(
        request, especialidade, destino="cadastros:especialidades", nome="Especialidade"
    )


def _render_especialidades(
    request,
    *,
    formulario_criacao=None,
    formulario_edicao=None,
    item_edicao=None,
    modal=None,
    status=200,
):
    """Monta a lista de especialidades com o formulário que a tela pediu."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    pode_gerenciar = pode_gerenciar_cadastros(request.user, atual)

    if item_edicao is None and pode_gerenciar:
        item_edicao = _item_em_edicao(
            request, EspecialidadeFornecedor, base["condominios"]
        )
    if item_edicao is None:
        if modal == "editar":
            modal = None
    elif modal is None:
        modal = "editar"

    contexto = {
        **base,
        "pode_gerenciar": pode_gerenciar,
        "tipos": _tipos_para_tela(
            EspecialidadeFornecedor.objects.filter(condominio=atual)
        ),
        "formulario_tipo": formulario_criacao
        or EspecialidadeFornecedorForm(condominio=atual),
        "formulario_edicao": formulario_edicao
        or (
            EspecialidadeFornecedorForm(instance=item_edicao, condominio=atual)
            if item_edicao
            else None
        ),
        "item_edicao": item_edicao,
        "modal_aberto": modal,
    }
    return render(
        request, "pages/cadastros/especialidades.html", contexto, status=status
    )


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
        "termo": (request.GET.get("q") or "").strip(),
    }


def _item_em_edicao(request, modelo, condominios, abrir=("editar",)):
    """O registro que um modal deve abrir, quando a tela pede um.

    ``abrir`` diz quais pedidos esta tela reconhece. Quase todas reconhecem só
    o de edição; a lista de acessos reconhece também o de senha, porque lá os
    dois modais trabalham sobre o mesmo vínculo.
    """
    if request.GET.get("abrir") not in abrir:
        return None
    pk = request.GET.get("item")
    if not pk or not _eh_uuid(pk):
        return None
    if modelo is Condominio:
        return _obter_ou_404(condominios, pk)
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


def _endereco_de_acao(view_name, vinculo) -> str:
    """O endereço de uma ação sobre um vínculo, com o condomínio em vista.

    As ações da lista chegam por POST, e o POST não carrega o condomínio
    escolhido: sem ele no endereço, a ação cairia no primeiro condomínio da
    lista de quem tem mais de um.
    """
    return f"{reverse(view_name, args=[vinculo.pk])}?condominio={vinculo.condominio_id}"


def _alternar(request, objeto, *, destino, nome):
    """Ativa ou desativa um cadastro e volta para a lista."""
    novo = not objeto.ativo
    services.definir_ativo(objeto=objeto, ativo=novo)
    if novo:
        messages.success(request, f"{nome} ativado.")
    else:
        messages.success(request, f"{nome} desativado.")
    return redirect(_destino(destino, objeto.condominio))


def _campos_do_modelo(modelo, dados: dict) -> dict:
    """O que o formulário validou que é campo do modelo.

    O que o formulário devolve a mais — as especialidades de um fornecedor — não
    é coluna: vira linha de vínculo, e por isso é entregue ao domínio à parte,
    nunca como atributo do cadastro.
    """
    nomes = {campo.name for campo in modelo._meta.fields}
    return {campo: valor for campo, valor in dados.items() if campo in nomes}


# -- Tradução para a tela -------------------------------------------------------

def _acesso_para_tela(vinculo, *, pode_definir_senha=False) -> dict:
    """O vínculo como a lista de acessos precisa dele.

    As ações são montadas aqui, e não no template: é esta função que sabe que
    a senha continua sendo escolha da própria pessoa. A lista só desenha a ação
    que pede o envio das instruções, nomeada conforme a conta já tenha senha ou
    ainda espere a primeira.

    ``pode_definir_senha`` abre a única exceção: a administração técnica, em
    ambiente sem entrega de e-mail, define a nova senha diretamente. Para todo
    o resto, a lista nem sabe que essa ação existe.

    A foto é um retrato ao lado do nome, nunca um motivo para a lista não
    abrir: se o armazenamento recusar o acesso à imagem, o que se perde é o
    retrato — ficam o nome e as iniciais.
    """
    usuario = vinculo.usuario
    acoes = [
        {
            "label": "Editar acesso",
            "icon": "bx-edit",
            "url": f"?condominio={vinculo.condominio_id}&abrir=editar&item={vinculo.pk}",
            "post": False,
        },
    ]
    if pode_definir_senha:
        acoes.append(
            {
                "label": "Redefinir senha",
                "icon": "bx-lock-alt",
                "url": f"?condominio={vinculo.condominio_id}&abrir=senha&item={vinculo.pk}",
                "post": False,
            }
        )
    acoes.append(
        {
            "label": (
                "Reenviar primeiro acesso"
                if primeiro_acesso.precisa_definir_senha(usuario)
                else "Enviar redefinição de senha"
            ),
            "icon": "bx-envelope",
            "url": _endereco_de_acao(
                "cadastros:gerar_link_primeiro_acesso", vinculo
            ),
            "post": True,
        }
    )
    acoes.append(
        {
            "label": "Retirar acesso" if vinculo.ativo else "Devolver acesso",
            "icon": "bx-block" if vinculo.ativo else "bx-check-circle",
            "url": _endereco_de_acao("cadastros:alternar_acesso", vinculo),
            "post": True,
        }
    )
    return {
        "pk": vinculo.pk,
        "nome": usuario.full_name or usuario.email,
        "email": usuario.email,
        "iniciais": usuario.initials,
        "foto_url": _foto_do_acesso(usuario),
        "papel": vinculo.get_papel_display(),
        "condominio": vinculo.condominio.nome,
        "situacao": "Ativo" if vinculo.ativo else "Inativo",
        "tom": "success" if vinculo.ativo else "secondary",
        "acoes": acoes,
    }


def _foto_do_acesso(usuario) -> str:
    """O endereço temporário da foto, ou vazio quando não há foto ou acesso.

    A lista não depende do armazenamento para abrir: uma imagem que não se
    consegue mostrar vale menos do que a tela inteira fora do ar. Sem foto, ou
    sem armazenamento, a lista segue — e a pessoa aparece pelas iniciais.
    """
    if not usuario.tem_foto:
        return ""
    try:
        return avatar.acesso_temporario(usuario) or ""
    except (ErroDeStorage, ImproperlyConfigured):
        return ""


def _condominios_para_tela(request, condominios):
    """Traduz os condomínios autorizados para a grade inicial do módulo."""
    vinculos = (
        AcessoUsuarioCondominio.objects.filter(
            usuario=request.user,
            ativo=True,
            condominio__in=condominios,
        )
        .select_related("condominio")
        .order_by("condominio__nome")
    )
    return [_condominio_para_tela(item) for item in vinculos]


def _condominio_para_tela(vinculo) -> dict:
    """O condomínio como a grade de cadastros precisa ler."""
    condominio = vinculo.condominio
    localizacao = ", ".join(
        trecho
        for trecho in [condominio.cidade, condominio.uf]
        if trecho
    ) or "Localização não informada"
    return {
        "pk": condominio.pk,
        "nome": condominio.nome,
        "cnpj": condominio.cnpj or "",
        "localizacao": localizacao,
        "quantidade_unidades": condominio.quantidade_unidades,
        "papel": vinculo.get_papel_display(),
        "pode_gerenciar": pode_gerenciar_cadastros(vinculo.usuario, condominio),
        "texto": "Ativo" if condominio.ativo else "Inativo",
        "tom": "success" if condominio.ativo else "secondary",
        "acao_label": (
            "Editar"
            if pode_gerenciar_cadastros(vinculo.usuario, condominio)
            else "Visualizar"
        ),
        "acao_icone": (
            "bx-edit"
            if pode_gerenciar_cadastros(vinculo.usuario, condominio)
            else "bx-show"
        ),
        "url": f"{reverse('cadastros:condominios')}?abrir=editar&item={condominio.pk}",
    }


def _situacao_do_cadastro(ativo: bool) -> dict:
    """Como o estado de um cadastro deve ser lido e usado na lista."""
    return {
        "ativo": ativo,
        "texto": "Ativo" if ativo else "Inativo",
        "tom": "success" if ativo else "secondary",
    }


def _tipos_para_tela(tipos) -> list[dict]:
    """Um catálogo de tipos, como as listas de tipo precisam dele."""
    return [
        {
            "pk": tipo.pk,
            "nome": tipo.nome,
            "descricao": tipo.descricao,
            **_situacao_do_cadastro(tipo.ativo),
        }
        for tipo in tipos
    ]


def _locais_para_tela(condominio, termo="", numero=1):
    """A página de locais do condomínio, como a lista precisa dela.

    A leitura prefere a classificação nova. O texto antigo só aparece enquanto
    o local que veio da fase anterior não tiver sido classificado — nada é
    convertido no lugar dele.
    """
    locais = (
        LocalOperacional.objects.filter(condominio=condominio)
        .buscar(termo)
        .select_related("tipo_local")
        .order_by("nome")
    )
    pagina = paginar(locais, numero)
    pagina.itens = [
        {
            "pk": local.pk,
            "nome": local.nome,
            "tipo": (
                local.tipo_local.nome if local.tipo_local_id else local.get_tipo_display()
            ),
            "descricao": local.descricao,
            **_situacao_do_cadastro(local.ativo),
        }
        for local in pagina
    ]
    return pagina


def _equipamentos_para_tela(condominio, termo="", numero=1):
    """A página de equipamentos do condomínio, como a lista precisa dela.

    Como no local, a classificação nova vem primeiro e o texto antigo só
    permanece enquanto o equipamento não for classificado.
    """
    equipamentos = (
        Equipamento.objects.filter(condominio=condominio)
        .buscar(termo)
        .select_related("local", "tipo_equipamento")
        .order_by("nome")
    )
    pagina = paginar(equipamentos, numero)
    pagina.itens = [
        {
            "pk": equipamento.pk,
            "nome": equipamento.nome,
            "tipo": (
                equipamento.tipo_equipamento.nome
                if equipamento.tipo_equipamento_id
                else equipamento.tipo_texto
            ),
            "local": equipamento.local.nome if equipamento.local else "",
            "fabricante": equipamento.fabricante,
            "modelo": equipamento.modelo,
            **_situacao_do_cadastro(equipamento.ativo),
        }
        for equipamento in pagina
    ]
    return pagina


def _fornecedores_para_tela(condominio, termo="", numero=1):
    """A página de fornecedores do condomínio, como a lista precisa dela."""
    fornecedores = (
        Fornecedor.objects.filter(condominio=condominio)
        .buscar(termo)
        .prefetch_related("especialidades__especialidade")
        .order_by("nome_razao_social")
    )
    pagina = paginar(fornecedores, numero)
    pagina.itens = [
        {
            "pk": fornecedor.pk,
            "nome": fornecedor.nome_razao_social,
            "fantasia": fornecedor.nome_fantasia,
            "documento": fornecedor.documento_formatado,
            "contato": fornecedor.contato,
            "telefone": fornecedor.telefone,
            "especialidades": ", ".join(
                vinculo.especialidade.nome for vinculo in fornecedor.especialidades.all()
            ),
            # A disponibilização aos moradores é lida do próprio cadastro: não
            # há situação guardada para ficar desatualizada.
            "disponivel_para_moradores": fornecedor.disponivel_para_moradores,
            **_situacao_do_cadastro(fornecedor.ativo),
        }
        for fornecedor in pagina
    ]
    return pagina
