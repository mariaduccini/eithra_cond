"""Telas de demandas operacionais.

A demanda existe para virar acompanhamento: quem responde, até quando, quanto
pesa e em que pé está. A tela mostra isso e o que já aconteceu com ela — sem
transformar o módulo em sistema de chamados, com fila, SLA ou etapas.

A autorização é decidida no backend, sempre a partir do vínculo ativo da pessoa
com o condomínio. Quem não enxerga o módulo não é avisado de que a demanda
existe: a resposta é 404.
"""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.core.filtros import tem_filtros
from apps.core.paginacao import paginar
from apps.manutencoes.permissoes import pode_criar_manutencao

from .constants import PrioridadeDemanda, StatusDemanda
from .forms import (
    AlteracaoDeStatusForm,
    DemandaForm,
    FiltroDeDemandasForm,
    ObservacaoDaDemandaForm,
)
from .models import Demanda
from .permissoes import (
    condominios_para_criar,
    condominios_para_ver,
    pode_adicionar_observacao,
    pode_alterar_status,
    pode_editar_demanda,
    status_permitidos_para,
    usuarios_elegiveis_entre,
)
from .services import (
    ErroDeDemanda,
    alterar_status,
    criar_demanda,
    editar_demanda,
    registrar_observacao,
)

# Tom de cada situação e prioridade no badge. Discreto: nada de cor agressiva,
# porque a lista inteira não pode gritar.
TOM_POR_STATUS = {
    StatusDemanda.ABERTA: "secondary",
    StatusDemanda.EM_ANDAMENTO: "info",
    StatusDemanda.AGUARDANDO: "warning",
    StatusDemanda.CONCLUIDA: "success",
    StatusDemanda.CANCELADA: "secondary",
}
TOM_POR_PRIORIDADE = {
    PrioridadeDemanda.BAIXA: "secondary",
    PrioridadeDemanda.NORMAL: "secondary",
    PrioridadeDemanda.ALTA: "warning",
    PrioridadeDemanda.URGENTE: "danger",
}


@login_required
def listagem(request):
    """As demandas dos condomínios que esta pessoa pode acompanhar."""
    condominios = _condominios_para_ver(request)
    return _render_listagem(request, condominios)


@login_required
def criar(request):
    """Registra uma demanda nova. O balanço oficial só nasce da decisão da gestão."""
    if request.method != "POST":
        return redirect("demandas:listagem")

    condominios = condominios_para_criar(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomínio disponível para registrar demanda.")

    formulario = DemandaForm(request.POST, condominios=condominios)
    if not formulario.is_valid():
        return _render_listagem(
            request,
            _condominios_para_ver(request),
            formulario=formulario,
            modal="criar",
            status=400,
        )

    dados = formulario.cleaned_data
    demanda = criar_demanda(
        condominio=dados["condominio"],
        criado_por=request.user,
        titulo=dados["titulo"],
        descricao=dados["descricao"],
        prioridade=dados["prioridade"],
        responsavel=dados["responsavel"],
        prazo=dados["prazo"],
        local=dados["local"],
    )
    messages.success(request, "Demanda registrada.")
    return redirect("demandas:detalhe", pk=demanda.pk)


@login_required
def detalhe(request, pk):
    """A demanda, o que ela é hoje e o que já aconteceu com ela."""
    demanda = _demanda_autorizada(request, pk)
    return _render_detalhe(request, demanda, modal=request.GET.get("abrir"))


@login_required
@require_POST
def editar(request, pk):
    """Atualiza o conteúdo da demanda. Só quem responde pelo condomínio edita."""
    demanda = _demanda_para_editar(request, pk)
    formulario = DemandaForm(request.POST, condominio=demanda.condominio)
    if not formulario.is_valid():
        return _render_detalhe(
            request, demanda, formulario=formulario, modal="editar", status=400
        )

    dados = formulario.cleaned_data
    editar_demanda(
        demanda=demanda,
        usuario=request.user,
        titulo=dados["titulo"],
        descricao=dados["descricao"],
        prioridade=dados["prioridade"],
        responsavel=dados["responsavel"],
        prazo=dados["prazo"],
        local=dados["local"],
    )
    messages.success(request, "Demanda atualizada.")
    return redirect("demandas:detalhe", pk=demanda.pk)


@login_required
@require_POST
def mudar_status(request, pk):
    """Move a demanda para outra situação entre as que esta pessoa alcança."""
    demanda = _demanda_para_mudar_status(request, pk)
    permitidos = status_permitidos_para(request.user, demanda)
    formulario = AlteracaoDeStatusForm(request.POST, permitidos=permitidos)
    if not formulario.is_valid():
        return _render_detalhe(
            request, demanda, formulario_status=formulario, modal="status", status=400
        )

    try:
        alterar_status(
            demanda=demanda,
            usuario=request.user,
            novo_status=formulario.cleaned_data["status"],
        )
    except ErroDeDemanda as erro:
        messages.error(request, str(erro))
        return redirect("demandas:detalhe", pk=demanda.pk)

    messages.success(request, "Situação atualizada.")
    return redirect("demandas:detalhe", pk=demanda.pk)


@login_required
@require_POST
def observar(request, pk):
    """Registra o que se sabe sobre a demanda, sem mexer nos campos dela."""
    demanda = _demanda_para_observar(request, pk)
    formulario = ObservacaoDaDemandaForm(request.POST)
    if not formulario.is_valid():
        return _render_detalhe(
            request,
            demanda,
            formulario_observacao=formulario,
            modal="observacao",
            status=400,
        )

    try:
        registrar_observacao(
            demanda=demanda,
            usuario=request.user,
            texto=formulario.cleaned_data["texto"],
        )
    except ErroDeDemanda as erro:
        messages.error(request, str(erro))
        return redirect("demandas:detalhe", pk=demanda.pk)

    messages.success(request, "Observação registrada.")
    return redirect("demandas:detalhe", pk=demanda.pk)


# -- Autorização ----------------------------------------------------------------
#
# Toda pergunta passa pelo domínio. Quem não enxerga o módulo não descobre que a
# demanda existe: a resposta é 404. Quem enxerga mas não responde por ela é
# recusado com 403 — a existência já é legítima, a decisão não é.


def _condominios_para_ver(request):
    """Condomínios cujas demandas esta pessoa acompanha, ou 404."""
    condominios = condominios_para_ver(request.user)
    if not condominios.exists():
        raise Http404("Nenhuma demanda disponível para consulta.")
    return condominios


def _demanda_autorizada(request, pk):
    """Resolve a demanda dentro dos condomínios que esta pessoa acompanha, ou 404."""
    condominios = _condominios_para_ver(request)
    return get_object_or_404(
        Demanda.objects.select_related(
            "condominio", "responsavel", "criado_por", "local"
        ).filter(condominio__in=condominios),
        pk=pk,
    )


def _demanda_para_editar(request, pk):
    """Resolve a demanda e exige, no backend, o direito de editar o conteúdo."""
    demanda = _demanda_autorizada(request, pk)
    if not pode_editar_demanda(request.user, demanda):
        raise PermissionDenied("Esta pessoa não responde por esta demanda.")
    return demanda


def _demanda_para_mudar_status(request, pk):
    """Resolve a demanda e exige, no backend, o direito de mover a situação."""
    demanda = _demanda_autorizada(request, pk)
    if not pode_alterar_status(request.user, demanda):
        raise PermissionDenied("Esta pessoa não pode mudar a situação desta demanda.")
    return demanda


def _demanda_para_observar(request, pk):
    """Resolve a demanda e exige, no backend, o direito de observar sobre ela."""
    demanda = _demanda_autorizada(request, pk)
    if not pode_adicionar_observacao(request.user, demanda):
        raise PermissionDenied("Esta pessoa não acompanha esta demanda.")
    return demanda


# -- Montagem das telas ---------------------------------------------------------


def _render_listagem(request, condominios, *, formulario=None, modal=None, status=200):
    """Monta a lista com os filtros aplicados e o formulário de nova demanda."""
    filtros = FiltroDeDemandasForm(
        request.GET or None,
        condominios=condominios,
        responsaveis=usuarios_elegiveis_entre(condominios),
    )
    pagina_demandas = paginar(
        _demandas_filtradas(request, condominios, filtros), request.GET.get("page")
    )
    pode_criar_condominios = condominios_para_criar(request.user)
    pode_criar = pode_criar_condominios.exists()

    contexto = {
        "demandas": [
            _demanda_para_tela(item, request.user) for item in pagina_demandas
        ],
        "pagina_demandas": pagina_demandas,
        "filtros": filtros,
        "filtros_ativos": tem_filtros(filtros),
        "total": pagina_demandas.paginator.count,
        "pode_criar": pode_criar,
        "formulario_demanda": formulario
        or (DemandaForm(condominios=pode_criar_condominios) if pode_criar else None),
        "modal_aberto": modal,
        "mostrar_condominio": condominios.count() > 1,
    }
    return render(request, "pages/demandas/listagem.html", contexto, status=status)


def _render_detalhe(
    request,
    demanda,
    *,
    formulario=None,
    formulario_status=None,
    formulario_observacao=None,
    modal=None,
    status=200,
):
    """Monta a tela da demanda com o que esta pessoa pode fazer com ela."""
    permitidos = status_permitidos_para(request.user, demanda)
    pode_editar = pode_editar_demanda(request.user, demanda)
    pode_observar = pode_adicionar_observacao(request.user, demanda)

    if (modal == "editar" and not pode_editar) or (
        modal == "status" and not permitidos
    ) or (modal == "observacao" and not pode_observar):
        modal = None

    contexto = {
        "demanda": _demanda_para_detalhe(demanda),
        "historico": _historico_para_tela(demanda),
        "pode_editar": pode_editar,
        "pode_observar": pode_observar,
        "pode_alterar_status": bool(permitidos),
        # A oferta de criar manutenção a partir desta demanda é do domínio de
        # manutenções. A demanda só pergunta, com a mesma regra que a outra tela
        # usaria — e quem não pode registrar não recebe a ação.
        "pode_criar_manutencao": pode_criar_manutencao(request.user, demanda.condominio),
        "formulario_demanda": formulario
        or DemandaForm(condominio=demanda.condominio, initial=_iniciais_da_demanda(demanda)),
        "formulario_status": formulario_status
        or AlteracaoDeStatusForm(
            permitidos=permitidos, initial={"status": demanda.status}
        ),
        "formulario_observacao": formulario_observacao or ObservacaoDaDemandaForm(),
        "modal_aberto": modal,
    }
    return render(request, "pages/demandas/detalhe.html", contexto, status=status)


def _demandas_filtradas(request, condominios, filtros):
    """Aplica os filtros essenciais e devolve a lista na ordem de atenção."""
    queryset = Demanda.objects.filter(
        condominio__in=condominios, ativo=True
    ).select_related("condominio", "responsavel", "local")

    if filtros.is_valid():
        dados = filtros.cleaned_data
        if dados["condominio"]:
            queryset = queryset.filter(condominio=dados["condominio"])
        if dados["status"]:
            queryset = queryset.filter(status=dados["status"])
        if dados["prioridade"]:
            queryset = queryset.filter(prioridade=dados["prioridade"])
        if dados["responsavel"]:
            queryset = queryset.filter(responsavel=dados["responsavel"])

        prazo = dados["prazo"]
        if prazo == "vencidas":
            queryset = queryset.vencidas()
        elif prazo == "hoje":
            queryset = queryset.vencendo_hoje()
        elif prazo == "proximas":
            queryset = queryset.proximas()
        elif prazo == "sem_prazo":
            queryset = queryset.sem_prazo()

        busca = dados["busca"]
        if busca:
            queryset = queryset.filter(
                Q(titulo__icontains=busca)
                | Q(descricao__icontains=busca)
                | Q(local__nome__icontains=busca)
                | Q(local_contexto__icontains=busca)
            )

        if dados["minhas"]:
            queryset = queryset.minhas(request.user)

    return queryset.na_ordem_de_atencao()


# -- Tradução para a tela -------------------------------------------------------


def _iniciais_da_demanda(demanda):
    """O que o formulário já deve trazer quando a demanda é aberta para edição."""
    return {
        "titulo": demanda.titulo,
        "descricao": demanda.descricao,
        "prioridade": demanda.prioridade,
        "responsavel": demanda.responsavel_id,
        "prazo": demanda.prazo,
        "local": demanda.local_id,
    }


def _nome_de(usuario) -> str:
    """O nome pelo qual a pessoa é reconhecida na tela."""
    return (
        getattr(usuario, "full_name", "")
        or getattr(usuario, "email", "")
        or "Sem responsável"
    )


def _prazo_por_extenso(prazo) -> str:
    return prazo.strftime("%d/%m/%Y") if prazo else "Sem prazo"


def _situacao_do_prazo(prazo, encerrada: bool, hoje):
    """Como o prazo deve ser lido: vencida, vence hoje, próxima ou nada."""
    if prazo is None or encerrada:
        return "", ""
    if prazo < hoje:
        return "Vencida", "danger"
    if prazo == hoje:
        return "Vence hoje", "warning"
    if prazo <= hoje + timedelta(days=7):
        return "Próxima", "secondary"
    return "", ""


def _momento_por_extenso(momento, hoje) -> str:
    """O momento como a gestão o lê: hoje, ontem ou a data, com a hora."""
    local = timezone.localtime(momento)
    dia = local.date()
    if dia == hoje:
        prefixo = "Hoje"
    elif dia == hoje - timedelta(days=1):
        prefixo = "Ontem"
    else:
        prefixo = local.strftime("%d/%m/%Y")
    return f"{prefixo} {local.strftime('%H:%M')}"


def _demanda_para_tela(demanda, usuario=None):
    """A demanda como a linha da lista precisa dela.

    As ações por linha saem da mesma regra do domínio que o resto do sistema:
    a interface só desenha o que a pessoa já poderia fazer.
    """
    hoje = timezone.localdate()
    situacao, tom = _situacao_do_prazo(demanda.prazo, demanda.esta_encerrada, hoje)
    return {
        "pk": demanda.pk,
        "titulo": demanda.titulo,
        "condominio": demanda.condominio.nome,
        "responsavel": _nome_de(demanda.responsavel),
        "prazo": _prazo_por_extenso(demanda.prazo),
        "prazo_situacao": situacao,
        "prazo_tom": tom,
        "prioridade": demanda.get_prioridade_display(),
        "prioridade_tom": TOM_POR_PRIORIDADE.get(demanda.prioridade, "secondary"),
        "status": demanda.get_status_display(),
        "status_tom": TOM_POR_STATUS.get(demanda.status, "secondary"),
        "encerrada": demanda.esta_encerrada,
        "local": demanda.local_para_exibir,
        "acoes": _acoes_da_linha(demanda, usuario),
    }


def _acoes_da_linha(demanda, usuario):
    """As ações oferecidas na linha, já decididas pelo domínio."""
    if usuario is None:
        return []

    destino = reverse("demandas:detalhe", args=[demanda.pk])
    acoes = []
    if pode_editar_demanda(usuario, demanda):
        acoes.append(
            {
                "label": "Editar",
                "icon": "bx-pencil",
                "url": f"{destino}?abrir=editar",
            }
        )
    if pode_alterar_status(usuario, demanda):
        acoes.append(
            {
                "label": "Mudar situação",
                "icon": "bx-transfer",
                "url": f"{destino}?abrir=status",
            }
        )
    return acoes


def _demanda_para_detalhe(demanda):
    """A demanda como a página dela precisa: os dados e o que dá para fazer."""
    dados = _demanda_para_tela(demanda)
    dados.update(
        {
            "descricao": demanda.descricao,
            "criada_em": _momento_por_extenso(demanda.criado_em, timezone.localdate()),
        }
    )
    return dados


def _historico_para_tela(demanda):
    """O que aconteceu com a demanda, do começo para o fim."""
    hoje = timezone.localdate()
    return [
        {
            "momento": _momento_por_extenso(evento.data_hora, hoje),
            "tipo": evento.get_tipo_evento_display(),
            "descricao": evento.descricao,
            "quem": _nome_de(evento.usuario),
        }
        for evento in demanda.historico.select_related("usuario").order_by("data_hora")
    ]
