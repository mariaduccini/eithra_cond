"""Telas de demandas operacionais.

O domínio já sabe criar, editar, mover situação, observar e relacionar
manutenção. Esta camada organiza isso na interface aprovada: tabela
operacional, wizard curto de registro, detalhe com abas e atualização sem
reinventar o backend.
"""
from datetime import date, timedelta

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

from .constants import (
    ImpactoDemanda,
    OrigemDemanda,
    PrioridadeDemanda,
    RiscoDemanda,
    StatusDemanda,
    TipoProblema,
)
from .derivacao import (
    LIMITE_DO_ASSUNTO_NO_CABECALHO,
    OrigemSugestao,
    area_do_contexto,
    assunto_do_relato,
    prioridade_do_relato,
)
from .forms import (
    AcaoDeStatusForm,
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
    contexto_de_governanca,
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
ABAS_DA_DEMANDA = (
    ("visao_geral", "Visão geral", False),
    ("historico", "Histórico", False),
    ("anexos", "Anexos", True),
    ("relacionadas", "Relacionadas", False),
)
ETAPAS_DO_REGISTRO = (
    ("descricao", "Descrição", False),
    ("informacoes", "Informações", False),
    ("revisao", "Revisão", False),
)
ETAPAS_DA_ATUALIZACAO = (
    ("informacoes", "Informações", False),
    ("conclusao", "Conclusão", False),
)


@login_required
def listagem(request):
    """As demandas dos condomínios que esta pessoa pode acompanhar."""
    condominios = _condominios_para_ver(request)
    return _render_listagem(request, condominios)


@login_required
def criar(request):
    """Registra uma demanda nova a partir do que aconteceu.

    O primeiro envio traz só o relato. O que a leitura dele sustentar volta
    pronto para conferência — e só o segundo envio registra. Quem manda o
    formulário inteiro de uma vez, como fazem as integrações, continua sendo
    atendido no mesmo passo de sempre.
    """
    if request.method != "POST":
        return redirect("demandas:listagem")

    condominios = condominios_para_criar(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomínio disponível para registrar demanda.")

    formulario = DemandaForm(request.POST, condominios=condominios)
    etapa = request.POST.get("wizard", "")
    if not formulario.is_valid():
        return _render_listagem(
            request,
            _condominios_para_ver(request),
            formulario=formulario,
            modal="criar",
            registro_etapa=_etapa_do_registro_invalida(etapa),
            status=400,
        )

    if etapa == "descricao":
        return _render_listagem(
            request,
            _condominios_para_ver(request),
            formulario=_formulario_da_informacao(formulario, condominios, request.user),
            modal="criar",
            registro_etapa="informacoes",
            sugestoes=_sugestoes_para_tela(formulario, request.user),
        )

    if etapa == "informacoes":
        return _render_listagem(
            request,
            _condominios_para_ver(request),
            formulario=_formulario_da_revisao(formulario, condominios, request.user),
            modal="criar",
            registro_etapa="revisao",
            revisao=_resumo_do_registro(formulario, request.user),
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
        local_contexto=dados.get("local_contexto") or "",
        equipamento=dados.get("equipamento"),
        tipo_problema=dados.get("tipo_problema"),
        impacto=dados.get("impacto"),
        risco=dados.get("risco"),
        classificacao_sugerida=dados.get("classificacao_sugerida"),
    )
    messages.success(request, "Demanda registrada.")
    return redirect("demandas:detalhe", pk=demanda.pk)


@login_required
def detalhe(request, pk):
    """A demanda, o que ela é hoje e o que já aconteceu com ela."""
    demanda = _demanda_autorizada(request, pk)
    return _render_detalhe(
        request,
        demanda,
        modal=request.GET.get("abrir"),
        aba=request.GET.get("aba"),
        # Sem "etapa" na URL, o Atualizar abre no começo do fluxo. Sem o
        # "or", o None que vem do GET anularia o padrão de _render_detalhe e a
        # revisão apareceria como primeira tela.
        etapa_atualizacao=request.GET.get("etapa") or "informacoes",
    )


@login_required
@require_POST
def editar(request, pk):
    """Atualiza o conteúdo da demanda. Só quem responde pelo condomínio edita."""
    demanda = _demanda_para_editar(request, pk)
    formulario = DemandaForm(
        request.POST, condominio=demanda.condominio, instancia=demanda
    )
    if not formulario.is_valid():
        return _render_detalhe(
            request, demanda, formulario=formulario, modal="editar", status=400
        )

    dados = formulario.cleaned_data
    # Só o que a tela perguntou pode mudar. Quando o envio não fala de um campo —
    # o caso das integrações e das telas antigas —, ele fica como está: quem não
    # falou do equipamento não o apagou, e o histórico não registra mudança que
    # ninguém pediu.
    classificacao = {
        campo: dados.get(campo)
        for campo in ("equipamento", "tipo_problema", "impacto", "risco")
        if campo in request.POST
    }
    editar_demanda(
        demanda=demanda,
        usuario=request.user,
        titulo=dados["titulo"],
        descricao=dados["descricao"],
        prioridade=dados["prioridade"],
        responsavel=dados["responsavel"],
        prazo=dados["prazo"],
        local=dados["local"],
        local_contexto=dados.get("local_contexto"),
        **classificacao,
    )
    messages.success(request, "Demanda atualizada.")
    return redirect("demandas:detalhe", pk=demanda.pk)


@login_required
@require_POST
def atualizar(request, pk):
    """Atualiza o andamento operacional sem reabrir a edição estrutural."""
    demanda = _demanda_autorizada(request, pk)
    permitidos = status_permitidos_para(request.user, demanda)
    pode_observar = pode_adicionar_observacao(request.user, demanda)
    if not permitidos and not pode_observar:
        raise PermissionDenied("Esta pessoa não pode atualizar esta demanda.")

    etapa = request.POST.get("etapa_atualizacao") or "informacoes"
    formulario_status = AcaoDeStatusForm(
        request.POST, permitidos=permitidos, demanda=demanda
    )
    texto = " ".join((request.POST.get("texto") or "").split())
    precisa_validar_observacao = bool(texto)
    if precisa_validar_observacao and not pode_observar:
        raise PermissionDenied("Esta pessoa não pode registrar observação nesta demanda.")

    formulario_observacao = (
        ObservacaoDaDemandaForm(request.POST)
        if precisa_validar_observacao
        else ObservacaoDaDemandaForm(initial={"texto": texto})
    )

    invalido = bool(permitidos) and not formulario_status.is_valid()
    invalido = invalido or (precisa_validar_observacao and not formulario_observacao.is_valid())
    if invalido:
        return _render_detalhe(
            request,
            demanda,
            formulario_status=formulario_status,
            formulario_observacao=formulario_observacao,
            modal="atualizar",
            aba=request.GET.get("aba") or "visao_geral",
            etapa_atualizacao="informacoes",
            status=400,
        )

    # A tela diz o que aconteceu; a situação é consequência. Sem ação escolhida,
    # a demanda fica onde está — atualizar também serve só para observar.
    proximo_status = demanda.status
    if permitidos and formulario_status.is_valid():
        proximo_status = formulario_status.cleaned_data.get("acao") or demanda.status
    resumo = _resumo_da_atualizacao(demanda, proximo_status, texto)

    if etapa == "informacoes":
        return _render_detalhe(
            request,
            demanda,
            formulario_status=formulario_status,
            formulario_observacao=formulario_observacao,
            modal="atualizar",
            aba=request.GET.get("aba") or "visao_geral",
            etapa_atualizacao="conclusao",
            resumo_atualizacao=resumo,
        )

    mudou_status = proximo_status != demanda.status
    if not mudou_status and not texto:
        messages.error(request, "Informe uma atualização antes de salvar.")
        return _render_detalhe(
            request,
            demanda,
            formulario_status=formulario_status,
            formulario_observacao=formulario_observacao,
            modal="atualizar",
            aba=request.GET.get("aba") or "visao_geral",
            etapa_atualizacao="informacoes",
            status=400,
        )

    try:
        if mudou_status:
            alterar_status(demanda=demanda, usuario=request.user, novo_status=proximo_status)
        if texto:
            registrar_observacao(demanda=demanda, usuario=request.user, texto=texto)
    except ErroDeDemanda as erro:
        messages.error(request, str(erro))
        return redirect(f"{reverse('demandas:detalhe', args=[demanda.pk])}?aba=historico")

    messages.success(request, "Demanda atualizada.")
    return redirect(f"{reverse('demandas:detalhe', args=[demanda.pk])}?aba=historico")


@login_required
@require_POST
def mudar_status(request, pk):
    """Registra a ação que move a demanda entre as que esta pessoa alcança."""
    demanda = _demanda_para_mudar_status(request, pk)
    permitidos = status_permitidos_para(request.user, demanda)
    formulario = AcaoDeStatusForm(request.POST, permitidos=permitidos, demanda=demanda)
    acao = formulario.cleaned_data.get("acao") if formulario.is_valid() else ""
    if not formulario.is_valid() or not acao:
        if formulario.is_valid():
            formulario.add_error("acao", "Diga o que aconteceu com a demanda.")
        return _render_detalhe(
            request, demanda, formulario_status=formulario, modal="status", status=400
        )

    try:
        alterar_status(
            demanda=demanda,
            usuario=request.user,
            novo_status=acao,
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
            "condominio",
            "responsavel",
            "criado_por",
            "local",
            "local__tipo_local",
            # A área sai do tipo do lugar onde o equipamento fica: trazer esse
            # caminho junto evita uma consulta por demanda só para ler o rótulo.
            "equipamento",
            "equipamento__local",
            "equipamento__local__tipo_local",
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


def _render_listagem(
    request,
    condominios,
    *,
    formulario=None,
    modal=None,
    registro_etapa="descricao",
    sugestoes=None,
    revisao=None,
    status=200,
):
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
        "indicadores": _indicadores_da_listagem(condominios),
        "pagina_demandas": pagina_demandas,
        "filtros": filtros,
        "filtros_ativos": tem_filtros(filtros),
        "total": pagina_demandas.paginator.count,
        "pode_criar": pode_criar,
        "formulario_demanda": formulario
        or (DemandaForm(condominios=pode_criar_condominios) if pode_criar else None),
        "modal_aberto": modal,
        "registro_etapa": registro_etapa,
        "etapas_do_registro": _etapas_para_tela(ETAPAS_DO_REGISTRO, registro_etapa),
        "sugestoes": sugestoes,
        "revisao": revisao,
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
    aba=None,
    etapa_atualizacao="informacoes",
    resumo_atualizacao=None,
    status=200,
):
    """Monta a tela da demanda com o que esta pessoa pode fazer com ela."""
    permitidos = status_permitidos_para(request.user, demanda)
    pode_editar = pode_editar_demanda(request.user, demanda)
    pode_observar = pode_adicionar_observacao(request.user, demanda)
    manutencao = _manutencao_da_demanda(demanda)
    aba_atual = _aba_da_demanda(aba)

    if (modal == "editar" and not pode_editar) or (
        modal == "status" and not permitidos
    ) or (modal == "observacao" and not pode_observar) or (
        modal == "atualizar" and not (permitidos or pode_observar)
    ):
        modal = None

    contexto = {
        "demanda": _demanda_para_detalhe(demanda),
        "aba": aba_atual,
        "abas": _abas_para_tela(aba_atual),
        "historico": _historico_para_tela(demanda),
        # O que a Governança já sabe sobre o lugar e o equipamento desta demanda:
        # contrato vigente e garantia, quando existem. Vem do domínio de
        # Governança por consulta, sem criar vínculo novo aqui.
        "contexto_governanca": contexto_de_governanca(
            demanda=demanda, usuario=request.user
        ),
        "relacionadas": _relacionadas_para_tela(demanda, manutencao),
        "motivo_prioridade": _motivo_da_prioridade(demanda),
        "pode_editar": pode_editar,
        "pode_observar": pode_observar,
        "pode_atualizar": bool(permitidos or pode_observar),
        "pode_concluir": StatusDemanda.CONCLUIDA in permitidos and not demanda.esta_encerrada,
        "proximo_passo": _proximo_passo(demanda, request.user, manutencao),
        "mais_acoes": _mais_acoes_da_demanda(demanda, request.user),
        "formulario_demanda": formulario
        or DemandaForm(condominio=demanda.condominio, initial=_iniciais_da_demanda(demanda)),
        "formulario_status": formulario_status
        or AcaoDeStatusForm(permitidos=permitidos, demanda=demanda),
        "formulario_observacao": formulario_observacao or ObservacaoDaDemandaForm(),
        "modal_aberto": modal,
        "etapa_atualizacao": etapa_atualizacao,
        "etapas_da_atualizacao": _etapas_para_tela(
            ETAPAS_DA_ATUALIZACAO, etapa_atualizacao
        ),
        "resumo_atualizacao": resumo_atualizacao,
    }
    return render(request, "pages/demandas/detalhe.html", contexto, status=status)


def _demandas_filtradas(request, condominios, filtros):
    """Aplica os filtros essenciais e devolve a lista na ordem de atenção."""
    queryset = Demanda.objects.filter(
        condominio__in=condominios, ativo=True
    ).select_related("condominio", "responsavel", "local", "equipamento")

    if filtros.is_valid():
        dados = filtros.cleaned_data
        if dados["condominio"]:
            queryset = queryset.filter(condominio=dados["condominio"])
        if dados["status"]:
            queryset = queryset.filter(status=dados["status"])
        if dados["prioridade"]:
            queryset = queryset.filter(prioridade=dados["prioridade"])
        if dados["local"]:
            queryset = queryset.filter(local=dados["local"])
        if dados["responsavel"]:
            queryset = queryset.filter(responsavel=dados["responsavel"])
        # A classificação entra como filtro pela mesma razão que o local: é o que
        # permite olhar só o que interessa naquele momento — o que está sem
        # equipamento, o que é infiltração, o que tem risco de segurança.
        if dados["equipamento"]:
            queryset = queryset.filter(equipamento=dados["equipamento"])
        if dados["tipo_problema"]:
            queryset = queryset.filter(tipo_problema=dados["tipo_problema"])
        if dados["impacto"]:
            queryset = queryset.filter(impacto=dados["impacto"])
        if dados["risco"]:
            queryset = queryset.filter(risco=dados["risco"])

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
        "relato": demanda.descricao,
        "titulo": demanda.titulo,
        "descricao": demanda.descricao,
        "responsavel": demanda.responsavel_id,
        "prazo": demanda.prazo,
        "local": demanda.local_id,
        # O lugar é perguntado como busca: aparece o nome do cadastro e, quando
        # é tudo o que se sabe, o texto que ficou guardado do lugar.
        "local_texto": demanda.local_para_exibir,
        "equipamento": demanda.equipamento_id,
        "tipo_problema": demanda.tipo_problema or "",
        "impacto": demanda.impacto or "",
        "risco": demanda.risco or "",
    }


def _etapa_do_registro_invalida(etapa: str) -> str:
    if etapa == "informacoes":
        return "informacoes"
    return "descricao"


def _formulario_da_informacao(formulario, condominios, usuario):
    """Monta a etapa de informações com o que o relato já sustentou."""
    dados = formulario.cleaned_data
    leitura = formulario.leitura

    responsavel = dados.get("responsavel") or formulario.responsavel_sugerido(usuario)
    local = dados.get("local") or leitura.local
    local_texto = dados.get("local_texto") or (local.nome if local else "")

    return DemandaForm(
        condominios=condominios,
        initial={
            "condominio": dados["condominio"].pk,
            "relato": dados.get("relato") or "",
            "titulo": dados["titulo"],
            "descricao": dados.get("descricao") or "",
            "responsavel": getattr(responsavel, "pk", None),
            "prazo": dados.get("prazo"),
            "local": getattr(local, "pk", None),
            "local_texto": local_texto,
            # O que a leitura sustentou já vem escolhido, para a gestão conferir
            # em vez de preencher: o equipamento quando o cadastro o confirmou e
            # a classificação quando o relato a sustentou. O que ficou em aberto
            # continua em branco.
            "equipamento": getattr(dados.get("equipamento"), "pk", None),
            "tipo_problema": dados.get("tipo_problema") or "",
            "impacto": dados.get("impacto") or "",
            "risco": dados.get("risco") or "",
        },
    )


def _resumo_da_revisao(formulario):
    """Mantido por compatibilidade com leituras antigas de contexto."""
    return _resumo_do_registro(formulario, None)


def _formulario_da_revisao(formulario, condominios, usuario):
    """Congela os dados finais para a etapa de revisão."""
    dados = formulario.cleaned_data
    leitura = formulario.leitura
    responsavel = dados.get("responsavel") or formulario.responsavel_sugerido(usuario)
    local = dados.get("local") or leitura.local
    local_texto = dados.get("local_texto") or (local.nome if local else "")
    return DemandaForm(
        condominios=condominios,
        initial={
            "condominio": dados["condominio"].pk,
            "relato": dados.get("relato") or "",
            "titulo": dados.get("titulo") or leitura.assunto,
            "descricao": dados.get("descricao") or "",
            "responsavel": getattr(responsavel, "pk", None),
            "prazo": dados.get("prazo"),
            "local": getattr(local, "pk", None),
            "local_texto": local_texto,
            # A revisão não reabre a classificação: ela mostra o que foi
            # confirmado e o leva adiante sem perguntar de novo.
            "equipamento": getattr(dados.get("equipamento"), "pk", None),
            "tipo_problema": dados.get("tipo_problema") or "",
            "impacto": dados.get("impacto") or "",
            "risco": dados.get("risco") or "",
        },
    )


# A classificação que a leitura sustenta e que a tela mostra como proposta, com
# o rótulo humano do campo e o vocabulário que o traduz. A área não entra aqui:
# ela é derivada do tipo do local e aparece junto do lugar, onde é lida. A
# recorrência também não: é leitura do histórico, não classificação da demanda.
CLASSIFICACAO_PARA_TELA = (
    ("equipamento", "Equipamento", None),
    ("tipo_problema", "Tipo de problema", TipoProblema),
    ("impacto", "Impacto", ImpactoDemanda),
    ("risco", "Risco", RiscoDemanda),
)


def _proposta_para_tela(proposta, escolhas=None, sem_proposta: str = "") -> dict:
    """Uma proposta da leitura como ela aparece ao lado do campo que sugeriu.

    Quando a leitura não sustentou nada, o que se mostra é a ausência — dita em
    linguagem humana —, e não um valor inventado.
    """
    if not proposta.existe:
        return {"valor": "", "origem": sem_proposta, "motivo": ""}
    return {
        "valor": escolhas(proposta.valor).label if escolhas else proposta.valor,
        "origem": OrigemSugestao(proposta.origem).label,
        "motivo": proposta.motivo,
    }


def _classificacao_identificada(leitura):
    """O que a leitura do relato identificou, com a origem de cada campo.

    Só entra o que ela sustentou de fato: campo sem proposta não vira linha
    vazia, e a tela não mostra o que não há. A origem acompanha cada item
    porque é ela que diz se aquilo veio do relato, do cadastro ou do histórico.
    """
    sugestoes = leitura.sugestoes
    itens = []
    for campo, rotulo, escolhas in CLASSIFICACAO_PARA_TELA:
        proposta = sugestoes[campo]
        if not proposta.existe:
            continue
        itens.append(
            {
                "rotulo": rotulo,
                "valor": escolhas(proposta.valor).label if escolhas else proposta.valor,
                "origem": OrigemSugestao(proposta.origem).label,
                "motivo": proposta.motivo,
            }
        )
    return itens


def _sugestoes_para_tela(formulario, usuario):
    """As sugestões da etapa 2 com origem e explicação humana."""
    leitura = formulario.leitura
    dados = formulario.cleaned_data
    responsavel = dados.get("responsavel") or formulario.responsavel_sugerido(usuario)
    prioridade = dados.get("prioridade") or ""
    propostas = leitura.sugestoes
    return {
        "assunto": {
            "valor": dados.get("titulo") or leitura.assunto,
            "origem": "Identificado no relato" if leitura.assunto else "Não identificado",
            "motivo": "Pode ser ajustado antes do registro." if leitura.assunto else "",
        },
        "local": {
            "valor": leitura.local.nome if leitura.local else dados.get("local_texto") or "",
            "origem": "Identificado no relato" if leitura.local else "Não identificado",
            "motivo": "Usa o cadastro do condomínio quando o local é reconhecido."
            if leitura.local
            else "",
        },
        # A área não é campo: é a leitura do tipo do lugar onde o problema está.
        # Aparece junto de onde as coisas acontecem porque é ali que ela explica.
        "area": _proposta_para_tela(
            propostas["area"], None, "Não identificada pelo lugar."
        ),
        "equipamento": _proposta_para_tela(
            propostas["equipamento"], None, "Nenhum equipamento identificado no relato."
        ),
        "tipo_problema": _proposta_para_tela(
            propostas["tipo_problema"],
            TipoProblema,
            "Não identificado no relato.",
        ),
        "impacto": _proposta_para_tela(
            propostas["impacto"], ImpactoDemanda, "Não avaliado."
        ),
        "risco": _proposta_para_tela(propostas["risco"], RiscoDemanda, "Não avaliado."),
        "prioridade": {
            "valor": PrioridadeDemanda(prioridade).label if prioridade else "",
            "origem": (
                "Sugerida pelo EITHRA"
                if leitura.motivo
                else "Não identificada"
            ),
            "motivo": leitura.motivo,
        },
        "prazo": {
            "valor": _prazo_por_extenso(dados.get("prazo")) if dados.get("prazo") else "",
            "origem": "Informado manualmente" if dados.get("prazo") else "Não identificado",
            "motivo": "",
        },
        "responsavel": {
            "valor": _nome_de(responsavel),
            "origem": (
                "Sugerido pelo contexto"
                if responsavel is not None and getattr(responsavel, "pk", None) == getattr(usuario, "pk", None)
                else "Não identificado"
            ),
            "motivo": "Quem registrou também pode acompanhar esta demanda."
            if responsavel is not None and getattr(responsavel, "pk", None) == getattr(usuario, "pk", None)
            else "",
        },
        # A classificação, campo a campo, e a pergunta que o relato deixou em
        # aberto — no máximo uma. Quando nada ficou ambíguo, ela vem vazia e a
        # tela não pergunta nada.
        "identificados": _classificacao_identificada(leitura),
        "pergunta": leitura.pergunta,
    }


def _resumo_do_registro(formulario, usuario):
    """Resumo final do registro antes de persistir a demanda."""
    dados = formulario.cleaned_data
    leitura = formulario.leitura
    responsavel = dados.get("responsavel") or formulario.responsavel_sugerido(usuario)
    prioridade = dados.get("prioridade") or ""
    return {
        "referencia": _referencia_visual_temporaria(dados["condominio"], dados.get("titulo") or leitura.assunto),
        "assunto": dados.get("titulo") or leitura.assunto,
        "descricao_original": dados.get("descricao") or dados.get("relato") or "",
        "local": dados.get("local").nome if dados.get("local") else dados.get("local_texto") or "Sem local definido",
        # O peso não se escolhe: ele é o que o relato sustenta, e a origem diz
        # exatamente isso — não há mais um caso "informada pela gestão".
        "prioridade": {
            "valor": PrioridadeDemanda(prioridade).label if prioridade else "Não identificada",
            "origem": "Sugerida pelo EITHRA" if leitura.motivo else "Não identificada",
            "motivo": leitura.motivo,
        },
        "prazo": {
            "valor": _prazo_por_extenso(dados.get("prazo")),
            "origem": "Informado manualmente" if dados.get("prazo") else "Sem prazo definido",
        },
        # A mesma classificação que a etapa anterior mostrou: quem revisa
        # confere o que o EITHRA identificou antes de registrar, e não descobre
        # depois. Campo sem proposta não vira linha vazia.
        "identificados": _classificacao_identificada(leitura),
        "responsavel": _nome_de(responsavel),
        "situacao_inicial": StatusDemanda.ABERTA.label,
        "origem": OrigemDemanda.MANUAL.label,
        "registrado_por": _nome_de(usuario),
        "registrado_em": _momento_por_extenso(timezone.now(), timezone.localdate()),
    }


def _manutencao_da_demanda(demanda):
    """A manutenção em andamento gerada por esta demanda, se houver alguma.

    Só a que continua aberta interessa ao próximo passo: a que já terminou é
    passado da demanda, e o histórico dela já conta essa parte.
    """
    return demanda.manutencoes_geradas.abertas().order_by("-criado_em").first()


def _motivo_da_prioridade(demanda) -> str:
    """Por que esta demanda pesa o que pesa, quando o relato sustenta a razão.

    A explicação só aparece quando a leitura do relato chega ao mesmo peso que
    a demanda tem hoje. Se alguém mudou o peso à mão, ou se o relato não diz
    nada sobre isso, não há explicação — e o silêncio é a resposta honesta.
    """
    prioridade, motivo = prioridade_do_relato(demanda.descricao or demanda.titulo)
    return motivo if prioridade == demanda.prioridade else ""


def _proximo_passo(demanda, usuario, manutencao):
    """A próxima ação que faz sentido para esta demanda, uma só.

    Quando já existe manutenção em andamento, o passo é acompanhar o que foi
    criado. Quando não existe e esta pessoa pode registrar manutenção, o passo é
    criá-la — pela mesma porta que o domínio de manutenções já oferece. Encerrada
    ou sem passo possível, não há o que oferecer.
    """
    if demanda.esta_encerrada:
        return None

    if manutencao is not None:
        return {
            "label": "Acompanhar manutenção",
            "detalhe": manutencao.get_status_display(),
            "icon": "bx-wrench",
            "url": reverse("manutencoes:detalhe", args=[manutencao.pk]),
        }

    if pode_criar_manutencao(usuario, demanda.condominio):
        return {
            "label": "Criar manutenção",
            "detalhe": "Leva o problema para quem executa o serviço.",
            "icon": "bx-wrench",
            "url": f"{reverse('manutencoes:listagem')}?demanda={demanda.pk}",
        }

    return None


def _mais_acoes_da_demanda(demanda, usuario):
    """Ações secundárias do detalhe, preservando permissões do backend."""
    acoes = []
    destino = reverse("demandas:detalhe", args=[demanda.pk])
    if pode_adicionar_observacao(usuario, demanda) or status_permitidos_para(usuario, demanda):
        acoes.append({"label": "Atualizar demanda", "url": f"{destino}?abrir=atualizar"})
    if pode_editar_demanda(usuario, demanda):
        acoes.append({"label": "Editar demanda", "url": f"{destino}?abrir=editar"})
    return acoes


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
        "referencia": _referencia_curta(demanda),
        "titulo": demanda.titulo,
        "descricao_curta": _contexto_curto(demanda.descricao),
        # O corte acima é de exibição: a lista mostra uma linha, e o texto
        # completo fica disponível no title. O que está guardado não muda.
        "descricao_completa": demanda.descricao,
        "condominio": demanda.condominio.nome,
        "responsavel": _nome_de(demanda.responsavel),
        "prazo": _prazo_por_extenso(demanda.prazo),
        "prazo_situacao": situacao,
        "prazo_tom": tom,
        # O que merece parada no olho: prazo vencido ou vencendo hoje. O resto
        # já está dito pela própria data.
        "prazo_destaque": situacao if tom in ("danger", "warning") else "",
        "prioridade": demanda.get_prioridade_display(),
        "prioridade_tom": TOM_POR_PRIORIDADE.get(demanda.prioridade, "secondary"),
        # Peso só aparece quando pesa: demanda comum não precisa se anunciar.
        "prioridade_em_destaque": (
            demanda.prioridade in (PrioridadeDemanda.ALTA, PrioridadeDemanda.URGENTE)
            and not demanda.esta_encerrada
        ),
        "status": demanda.get_status_display(),
        "status_tom": TOM_POR_STATUS.get(demanda.status, "secondary"),
        "encerrada": demanda.esta_encerrada,
        "local": demanda.local_para_exibir or "Sem local",
        "acoes": _acoes_da_linha(demanda, usuario),
    }


def _acoes_da_linha(demanda, usuario):
    """As ações oferecidas na linha, já decididas pelo domínio.

    Só o apoio: abrir a demanda é a própria linha, e o que muda a demanda
    acontece dentro dela, por ação, não por escolha de situação na lista.
    """
    if usuario is None:
        return []

    destino = reverse("demandas:detalhe", args=[demanda.pk])
    acoes = [
        {
            "label": "Abrir demanda",
            "icon": "bx-show",
            "url": destino,
        }
    ]
    if pode_editar_demanda(usuario, demanda):
        acoes.append(
            {
                "label": "Editar",
                "icon": "bx-pencil",
                "url": f"{destino}?abrir=editar",
            }
        )
    return acoes


def _demanda_para_detalhe(demanda):
    """A demanda como a página dela precisa: os dados e o que dá para fazer.

    Além do que a lista mostra, entra o contexto que qualifica o problema — onde
    fica, sobre o que é e o que ele significa. Só o que existe aparece: campo
    sem resposta não vira linha vazia na tela.
    """
    area = area_do_contexto(demanda.local, demanda.equipamento)
    dados = _demanda_para_tela(demanda)
    dados.update(
        {
            "descricao": demanda.descricao,
            "assunto_curto": _assunto_do_cabecalho(demanda.titulo),
            "origem": demanda.get_origem_display(),
            "criado_por": _nome_de(demanda.criado_por),
            "criada_em": _momento_por_extenso(demanda.criado_em, timezone.localdate()),
            # Derivada, não guardada: a área sai do tipo do lugar cadastrado e,
            # quando só o equipamento se sabe, do lugar onde ele fica.
            "area": area.valor if area.existe else "",
            "area_motivo": area.motivo if area.existe else "",
            "equipamento": demanda.equipamento.nome if demanda.equipamento_id else "",
            "tipo_problema": _rotulo_de(TipoProblema, demanda.tipo_problema),
            "impacto": _rotulo_de(ImpactoDemanda, demanda.impacto),
            "risco": _rotulo_de(RiscoDemanda, demanda.risco),
        }
    )
    return dados


def _rotulo_de(escolhas, valor) -> str:
    """O rótulo humano de um valor guardado, ou nada quando não há valor."""
    if not valor:
        return ""
    try:
        return escolhas(valor).label
    except ValueError:
        return valor


# Os campos do histórico que a gestão lê melhor como dois valores lado a lado.
# O que fica de fora — local, equipamento, responsável — aparece pelo nome na
# própria frase do evento: identificador de cadastro não sobe para a tela.
ROTULOS_DO_HISTORICO = (
    ("status", "Situação"),
    ("prioridade", "Prioridade"),
    ("tipo_problema", "Tipo de problema"),
    ("impacto", "Impacto"),
    ("risco", "Risco"),
    ("prazo", "Prazo"),
)

ESCOLHAS_DO_HISTORICO = {
    "status": StatusDemanda,
    "prioridade": PrioridadeDemanda,
    "tipo_problema": TipoProblema,
    "impacto": ImpactoDemanda,
    "risco": RiscoDemanda,
}


def _valor_do_historico(campo, valor) -> str:
    """Um valor guardado, como a gestão o lê agora — ou a ausência dele."""
    if not valor:
        return "não informado"
    if campo == "prazo":
        try:
            return _prazo_por_extenso(date.fromisoformat(valor))
        except (TypeError, ValueError):
            return str(valor)
    escolhas = ESCOLHAS_DO_HISTORICO.get(campo)
    return _rotulo_de(escolhas, valor) if escolhas else str(valor)


def _mudancas_do_evento(evento):
    """O antes e o depois de cada campo, na língua da tela."""
    anteriores = evento.dados_anteriores or {}
    novos = evento.dados_novos or {}
    mudancas = []
    for campo, rotulo in ROTULOS_DO_HISTORICO:
        if campo not in anteriores and campo not in novos:
            continue
        mudancas.append(
            {
                "rotulo": rotulo,
                "antes": _valor_do_historico(campo, anteriores.get(campo)),
                "depois": _valor_do_historico(campo, novos.get(campo)),
            }
        )
    return mudancas


def _historico_para_tela(demanda):
    """O que aconteceu com a demanda, do começo para o fim.

    Cada evento vem com a frase que o descreve e, quando a mudança foi de um
    campo com vocabulário próprio, com o antes e o depois já traduzidos.
    """
    hoje = timezone.localdate()
    return [
        {
            "momento": _momento_por_extenso(evento.data_hora, hoje),
            "tipo": evento.get_tipo_evento_display(),
            "descricao": evento.descricao,
            "quem": _nome_de(evento.usuario),
            "mudancas": _mudancas_do_evento(evento),
        }
        for evento in demanda.historico.select_related("usuario").order_by("-data_hora")
    ]


def _indicadores_da_listagem(condominios):
    """Indicadores estáveis do universo visível na operação."""
    hoje = timezone.localdate()
    base = Demanda.objects.filter(condominio__in=condominios, ativo=True)
    inicio_mes = hoje.replace(day=1)
    return [
        {
            "titulo": "Em aberto",
            "valor": base.abertas().count(),
            "icone": "bx-folder-open",
            "tom": "primary",
        },
        {
            "titulo": "Vencidas",
            "valor": base.vencidas(hoje).count(),
            "icone": "bx-error-circle",
            "tom": "danger",
        },
        {
            "titulo": "Em andamento",
            "valor": base.filter(status=StatusDemanda.EM_ANDAMENTO).count(),
            "icone": "bx-time-five",
            "tom": "warning",
        },
        {
            "titulo": "Concluídas no mês",
            "valor": base.filter(
                status=StatusDemanda.CONCLUIDA,
                concluido_em__date__gte=inicio_mes,
                concluido_em__date__lte=hoje,
            ).count(),
            "icone": "bx-check-circle",
            "tom": "success",
        },
    ]


def _etapas_para_tela(etapas, atual):
    """Etapas com estado visual para o wizard."""
    indice_atual = next(
        (indice for indice, (chave, _, inativo) in enumerate(etapas) if chave == atual and not inativo),
        0,
    )
    return [
        {
            "chave": chave,
            "titulo": titulo,
            "numero": indice + 1,
            "ativa": chave == atual,
            "concluida": indice < indice_atual,
            "inativa": inativo,
        }
        for indice, (chave, titulo, inativo) in enumerate(etapas)
    ]


def _aba_da_demanda(aba):
    disponiveis = {chave for chave, _, inativa in ABAS_DA_DEMANDA if not inativa}
    return aba if aba in disponiveis else "visao_geral"


def _abas_para_tela(atual):
    return [
        {
            "chave": chave,
            "titulo": titulo,
            "ativa": chave == atual,
            "inativa": inativa,
        }
        for chave, titulo, inativa in ABAS_DA_DEMANDA
    ]


def _relacionadas_para_tela(demanda, manutencao):
    relacionadas = []
    if manutencao is not None:
        relacionadas.append(
            {
                "titulo": manutencao.titulo,
                "tipo": "Manutenção vinculada",
                "status": manutencao.get_status_display(),
                "url": reverse("manutencoes:detalhe", args=[manutencao.pk]),
            }
        )
    return relacionadas


def _resumo_da_atualizacao(demanda, proximo_status, texto):
    """Resumo da atualização operacional antes de salvar."""
    mudou_status = proximo_status != demanda.status
    return {
        "situacao": StatusDemanda(proximo_status).label,
        "mudou_status": mudou_status,
        "observacao": texto or "Sem observação adicional.",
    }


def _assunto_do_cabecalho(titulo):
    """O que o cabeçalho mostra quando o assunto guardado é um relato inteiro.

    Três coisas diferentes convivem aqui e não podem ser confundidas: o assunto
    real, escrito no campo de assunto; a descrição original, que é o relato
    inteiro; e este caso — registro antigo que guardou o relato no lugar do
    assunto. O que sai daqui é o terceiro: um começo de relato cortado para o
    cabeçalho não virar parágrafo. O corte é de apresentação e não produz
    assunto; não é derivação de assunto.

    É só leitura: o texto guardado continua íntegro e aparece por inteiro no
    título da aba e na descrição logo abaixo.

    Dívida controlada: demandas antigas podem possuir relato completo salvo no
    campo de assunto. Nada é reescrito automaticamente — sem limpeza em massa e
    sem migration de dados. A correção será uma rodada assistida: descrição
    original → sugestão de assunto curto → revisão humana → atualização.
    """
    return assunto_do_relato(titulo, limite=LIMITE_DO_ASSUNTO_NO_CABECALHO)


def _referencia_curta(demanda):
    """Identificador visual estável derivado do UUID, sem persistir novo número."""
    return f"D-{str(demanda.pk).split('-')[0].upper()}"


def _referencia_visual_temporaria(condominio, assunto):
    """Referência temporária da revisão, sem inventar número de domínio."""
    base = (assunto or str(condominio.pk)).encode("utf-8", errors="ignore")
    return f"Prévia {base.hex()[:6].upper()}"


def _contexto_curto(texto):
    """Trecho curto para a linha secundária da tabela."""
    linha = " ".join((texto or "").replace("\n", " ").split())
    if not linha:
        return "Sem contexto adicional."
    if len(linha) <= 72:
        return linha
    return f"{linha[:69].rstrip()}..."
