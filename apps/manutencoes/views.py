"""Telas de manutenções operacionais.

A manutenção existe para virar acompanhamento: o que motivou, quem responde,
onde e em quê, quando está previsto, o que foi feito e quanto custou. A tela
mostra isso e o que já aconteceu — sem transformar o módulo em almoxarifado,
ordem de serviço ou fluxo de aprovação.

A autorização é decidida no backend, sempre a partir do vínculo ativo da pessoa
com o condomínio. Quem não enxerga o módulo não é avisado de que a manutenção
existe: a resposta é 404. Quem enxerga e não responde por ela é recusado com
403 — a existência já é legítima, a decisão não é.
"""
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import number_format
from django.views.decorators.http import require_POST

from apps.cadastros.models import Equipamento, Fornecedor, LocalOperacional
from apps.core.filtros import tem_filtros
from apps.core.paginacao import paginar
from apps.demandas.models import Demanda

from .constants import StatusManutencao
from .forms import (
    AlteracaoDeStatusManutencaoForm,
    CancelamentoDeManutencaoForm,
    ConclusaoDeManutencaoForm,
    FiltroDeManutencoesForm,
    ManutencaoForm,
)
from .models import Manutencao
from .permissoes import (
    condominios_para_criar,
    condominios_para_ver,
    pode_administrar_manutencao,
    pode_cancelar_manutencao,
    pode_concluir,
    pode_editar_manutencao,
    situacoes_para_mudar,
    usuarios_elegiveis_entre,
)
from .services import (
    ErroDeManutencao,
    alterar_status,
    cancelar_manutencao,
    concluir_manutencao,
    criar_manutencao,
    definir_ativo,
    editar_manutencao,
)

# Tom de cada situação e prioridade no badge. Discreto: nada de cor agressiva,
# porque a lista inteira não pode gritar.
TOM_POR_STATUS = {
    StatusManutencao.ABERTA: "secondary",
    StatusManutencao.PROGRAMADA: "info",
    StatusManutencao.EM_ANDAMENTO: "primary",
    StatusManutencao.AGUARDANDO: "warning",
    StatusManutencao.CONCLUIDA: "success",
    StatusManutencao.CANCELADA: "secondary",
}
TOM_POR_PRIORIDADE = {
    "baixa": "secondary",
    "normal": "secondary",
    "alta": "warning",
    "urgente": "danger",
}


@login_required
def listagem(request):
    """As manutenções dos condomínios que esta pessoa pode acompanhar."""
    condominios = _condominios_para_ver(request)
    return _render_listagem(request, condominios)


@login_required
def criar(request):
    """Registra uma manutenção nova. O serviço só nasce do que a gestão decidiu."""
    if request.method != "POST":
        return redirect("manutencoes:listagem")

    condominios = condominios_para_criar(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomínio disponível para registrar manutenção.")

    formulario = ManutencaoForm(request.POST, condominios=condominios)
    if not formulario.is_valid():
        return _render_listagem(
            request,
            _condominios_para_ver(request),
            formulario=formulario,
            modal="criar",
            status=400,
        )

    dados = formulario.cleaned_data
    try:
        manutencao = criar_manutencao(
            condominio=dados["condominio"],
            criado_por=request.user,
            titulo=dados["titulo"],
            tipo=dados["tipo"],
            descricao_problema=dados["descricao_problema"],
            prioridade=dados["prioridade"],
            local=dados["local"],
            equipamento=dados["equipamento"],
            fornecedor=dados["fornecedor"],
            responsavel=dados["responsavel"],
            data_prevista=dados["data_prevista"],
            custo_previsto=dados["custo_previsto"],
            demanda_origem=_demanda_de_origem(request.POST.get("demanda_origem"), dados["condominio"]),
        )
    except ErroDeManutencao as erro:
        messages.error(request, str(erro))
        return _render_listagem(
            request,
            _condominios_para_ver(request),
            formulario=formulario,
            modal="criar",
            status=400,
        )

    messages.success(request, "Manutenção registrada.")
    return redirect("manutencoes:detalhe", pk=manutencao.pk)


@login_required
def detalhe(request, pk):
    """A manutenção, o que ela é hoje e o que já aconteceu com ela."""
    manutencao = _manutencao_autorizada(request, pk)
    return _render_detalhe(request, manutencao, modal=request.GET.get("abrir"))


@login_required
@require_POST
def editar(request, pk):
    """Atualiza o conteúdo da manutenção. Só quem responde pelo condomínio edita."""
    manutencao = _manutencao_para_editar(request, pk)
    formulario = ManutencaoForm(
        request.POST, condominio=manutencao.condominio, manutencao=manutencao
    )
    if not formulario.is_valid():
        return _render_detalhe(
            request, manutencao, formulario=formulario, modal="editar", status=400
        )

    dados = formulario.cleaned_data
    try:
        editar_manutencao(
            manutencao=manutencao,
            usuario=request.user,
            tipo=dados["tipo"],
            titulo=dados["titulo"],
            descricao_problema=dados["descricao_problema"],
            descricao_servico=dados["descricao_servico"],
            prioridade=dados["prioridade"],
            local=dados["local"],
            equipamento=dados["equipamento"],
            fornecedor=dados["fornecedor"],
            responsavel=dados["responsavel"],
            data_prevista=dados["data_prevista"],
            custo_previsto=dados["custo_previsto"],
            custo_real=dados["custo_real"],
        )
    except ErroDeManutencao as erro:
        messages.error(request, str(erro))
        manutencao.refresh_from_db()
        return _render_detalhe(
            request, manutencao, formulario=formulario, modal="editar", status=400
        )

    messages.success(request, "Manutenção atualizada.")
    return redirect("manutencoes:detalhe", pk=manutencao.pk)


@login_required
@require_POST
def mudar_status(request, pk):
    """Move a manutenção para outra situação entre as que esta pessoa alcança."""
    manutencao = _manutencao_para_mudar_status(request, pk)
    permitidos = situacoes_para_mudar(request.user, manutencao)
    formulario = AlteracaoDeStatusManutencaoForm(request.POST, permitidos=permitidos)
    if not formulario.is_valid():
        return _render_detalhe(
            request, manutencao, formulario_status=formulario, modal="status", status=400
        )

    try:
        alterar_status(
            manutencao=manutencao,
            usuario=request.user,
            novo_status=formulario.cleaned_data["status"],
        )
    except ErroDeManutencao as erro:
        messages.error(request, str(erro))
        return redirect("manutencoes:detalhe", pk=manutencao.pk)

    messages.success(request, "Situação atualizada.")
    return redirect("manutencoes:detalhe", pk=manutencao.pk)


@login_required
@require_POST
def concluir(request, pk):
    """Conclui a manutenção dizendo o que foi feito."""
    manutencao = _manutencao_para_concluir(request, pk)
    formulario = ConclusaoDeManutencaoForm(request.POST)
    if not formulario.is_valid():
        return _render_detalhe(
            request,
            manutencao,
            formulario_conclusao=formulario,
            modal="concluir",
            status=400,
        )

    try:
        concluir_manutencao(
            manutencao=manutencao,
            usuario=request.user,
            descricao_servico=formulario.cleaned_data["descricao_servico"],
            custo_real=formulario.cleaned_data["custo_real"],
            data_conclusao=formulario.cleaned_data["data_conclusao"],
        )
    except ErroDeManutencao as erro:
        messages.error(request, str(erro))
        return redirect("manutencoes:detalhe", pk=manutencao.pk)

    messages.success(request, "Manutenção concluída.")
    return redirect("manutencoes:detalhe", pk=manutencao.pk)


@login_required
@require_POST
def cancelar(request, pk):
    """Encerra a manutenção sem que o serviço aconteça."""
    manutencao = _manutencao_para_cancelar(request, pk)
    formulario = CancelamentoDeManutencaoForm(request.POST)
    if not formulario.is_valid():
        return _render_detalhe(
            request,
            manutencao,
            formulario_cancelamento=formulario,
            modal="cancelar",
            status=400,
        )

    try:
        cancelar_manutencao(
            manutencao=manutencao,
            usuario=request.user,
            motivo=formulario.cleaned_data["motivo"],
        )
    except ErroDeManutencao as erro:
        messages.error(request, str(erro))
        return redirect("manutencoes:detalhe", pk=manutencao.pk)

    messages.success(request, "Manutenção cancelada.")
    return redirect("manutencoes:detalhe", pk=manutencao.pk)


@login_required
@require_POST
def alternar_ativo(request, pk):
    """Retira a manutenção da operação, ou a devolve, sem apagar nada."""
    manutencao = _manutencao_para_administrar(request, pk)
    novo = not manutencao.ativo
    definir_ativo(manutencao=manutencao, ativo=novo)
    if novo:
        messages.success(request, "Manutenção devolvida à operação.")
    else:
        messages.success(request, "Manutenção retirada da operação.")
    return redirect("manutencoes:detalhe", pk=manutencao.pk)


# -- Autorização ----------------------------------------------------------------
#
# Toda pergunta passa pelo domínio. Quem não enxerga o módulo não descobre que a
# manutenção existe: a resposta é 404. Quem enxerga mas não responde por ela é
# recusado com 403 — a existência já é legítima, a decisão não é.


def _condominios_para_ver(request):
    """Condomínios cujas manutenções esta pessoa acompanha, ou 404."""
    condominios = condominios_para_ver(request.user)
    if not condominios.exists():
        raise Http404("Nenhuma manutenção disponível para consulta.")
    return condominios


def _manutencao_autorizada(request, pk):
    """Resolve a manutenção dentro dos condomínios que esta pessoa acompanha."""
    condominios = _condominios_para_ver(request)
    return _obter_ou_404(
        Manutencao.objects.select_related(
            "condominio", "responsavel", "local", "equipamento", "fornecedor"
        ).filter(condominio__in=condominios),
        pk,
    )


def _manutencao_para_editar(request, pk):
    """Resolve a manutenção e exige, no backend, o direito de editar o conteúdo."""
    manutencao = _manutencao_autorizada(request, pk)
    if not pode_editar_manutencao(request.user, manutencao):
        raise PermissionDenied("Esta pessoa não responde por esta manutenção.")
    return manutencao


def _manutencao_para_mudar_status(request, pk):
    """Resolve a manutenção e exige, no backend, o direito de mover a situação."""
    manutencao = _manutencao_autorizada(request, pk)
    if not situacoes_para_mudar(request.user, manutencao):
        raise PermissionDenied("Esta pessoa não pode mudar a situação desta manutenção.")
    return manutencao


def _manutencao_para_concluir(request, pk):
    """Resolve a manutenção e exige, no backend, o direito de concluí-la."""
    manutencao = _manutencao_autorizada(request, pk)
    if not pode_concluir(request.user, manutencao):
        raise PermissionDenied("Esta pessoa não pode concluir esta manutenção.")
    return manutencao


def _manutencao_para_cancelar(request, pk):
    """Resolve a manutenção e exige, no backend, o direito de cancelá-la."""
    manutencao = _manutencao_autorizada(request, pk)
    if not pode_cancelar_manutencao(request.user, manutencao):
        raise PermissionDenied("Esta pessoa não pode cancelar esta manutenção.")
    return manutencao


def _manutencao_para_administrar(request, pk):
    """Resolve a manutenção e exige, no backend, o direito de retirá-la da operação."""
    manutencao = _manutencao_autorizada(request, pk)
    if not pode_administrar_manutencao(request.user, manutencao):
        raise PermissionDenied("Esta pessoa não administra esta manutenção.")
    return manutencao


def _demanda_de_origem(pk, condominio):
    """A demanda de onde a manutenção veio, quando a tela informou uma.

    A demanda precisa ser do mesmo condomínio da manutenção. O que o navegador
    enviou não decide nada, e uma procedência que não se confirma é recusada em
    vez de guardada pela metade.
    """
    if not pk:
        return None
    if not _eh_uuid(pk):
        raise Http404("Demanda não encontrada.")
    return _obter_ou_404(Demanda.objects.filter(condominio=condominio), pk)


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


# -- Montagem das telas ---------------------------------------------------------


def _cadastros_ativos_entre(modelo, condominios):
    """Os cadastros em uso nos condomínios que a pessoa enxerga, para filtrar.

    A lista deixa escolher local, equipamento e fornecedor de uma vez só, então
    as opções vêm dos condomínios visíveis — sempre em uso, nunca de outro
    condomínio. Sem condomínios, a resposta é vazia.
    """
    if condominios is None:
        return modelo.objects.none()
    return modelo.objects.filter(condominio__in=condominios, ativo=True)


def _render_listagem(request, condominios, *, formulario=None, modal=None, status=200):
    """Monta a lista com os filtros aplicados e o formulário de nova manutenção."""
    filtros = FiltroDeManutencoesForm(
        request.GET or None,
        condominios=condominios,
        responsaveis=usuarios_elegiveis_entre(condominios),
        locais=_cadastros_ativos_entre(LocalOperacional, condominios),
        equipamentos=_cadastros_ativos_entre(Equipamento, condominios),
        fornecedores=_cadastros_ativos_entre(Fornecedor, condominios),
    )
    pagina_manutencoes = paginar(
        _manutencoes_filtradas(request, condominios, filtros), request.GET.get("page")
    )
    pode_criar_condominios = condominios_para_criar(request.user)
    pode_criar = pode_criar_condominios.exists()
    origem = _origem_pedida(request, pode_criar_condominios) if pode_criar else None

    if formulario is None and pode_criar:
        if origem is not None:
            formulario = ManutencaoForm(
                condominio=origem.condominio, initial=_iniciais_da_demanda(origem)
            )
            # Veio da demanda: o formulário abre preenchido para revisão, sem
            # nada ser gravado antes de quem registra confirmar.
            modal = modal or "criar"
        else:
            formulario = ManutencaoForm(condominios=pode_criar_condominios)

    contexto = {
        "manutencoes": [
            _manutencao_para_tela(item, request.user) for item in pagina_manutencoes
        ],
        "pagina_manutencoes": pagina_manutencoes,
        "filtros": filtros,
        "filtros_ativos": tem_filtros(filtros),
        "total": pagina_manutencoes.paginator.count,
        "pode_criar": pode_criar,
        "formulario_manutencao": formulario,
        "demanda_origem": origem,
        "modal_aberto": modal,
        "mostrar_condominio": condominios.count() > 1,
    }
    return render(request, "pages/manutencoes/listagem.html", contexto, status=status)


def _origem_pedida(request, condominios):
    """A demanda que a tela pediu para originar uma manutenção, se houver.

    Só vale a demanda de um condomínio em que esta pessoa pode registrar
    manutenção — sempre conferida no backend, nunca aceita pelo que diz.
    """
    pk = request.GET.get("demanda")
    if not pk or not _eh_uuid(pk):
        return None
    return _obter_ou_404(Demanda.objects.filter(condominio__in=condominios), pk)


def _render_detalhe(
    request,
    manutencao,
    *,
    formulario=None,
    formulario_status=None,
    formulario_conclusao=None,
    formulario_cancelamento=None,
    modal=None,
    status=200,
):
    """Monta a tela da manutenção com o que esta pessoa pode fazer com ela."""
    permitidos = situacoes_para_mudar(request.user, manutencao)
    pode_editar = pode_editar_manutencao(request.user, manutencao)
    pode_concluir_agora = pode_concluir(request.user, manutencao)
    pode_cancelar_agora = pode_cancelar_manutencao(request.user, manutencao)

    if (
        (modal == "editar" and not pode_editar)
        or (modal == "status" and not permitidos)
        or (modal == "concluir" and not pode_concluir_agora)
        or (modal == "cancelar" and not pode_cancelar_agora)
    ):
        modal = None

    contexto = {
        "manutencao": _manutencao_para_detalhe(manutencao),
        "historico": _historico_para_tela(manutencao),
        "pode_editar": pode_editar,
        "pode_alterar_status": bool(permitidos),
        "pode_concluir": pode_concluir_agora,
        "pode_cancelar": pode_cancelar_agora,
        "pode_administrar": pode_administrar_manutencao(request.user, manutencao),
        "formulario_manutencao": formulario
        or ManutencaoForm(
            condominio=manutencao.condominio,
            manutencao=manutencao,
            initial=_iniciais_da_manutencao(manutencao),
        ),
        "formulario_status": formulario_status
        or AlteracaoDeStatusManutencaoForm(
            permitidos=permitidos, initial={"status": manutencao.status}
        ),
        "formulario_conclusao": formulario_conclusao or ConclusaoDeManutencaoForm(),
        "formulario_cancelamento": formulario_cancelamento
        or CancelamentoDeManutencaoForm(),
        "modal_aberto": modal,
    }
    return render(request, "pages/manutencoes/detalhe.html", contexto, status=status)


def _manutencoes_filtradas(request, condominios, filtros):
    """Aplica os filtros essenciais e devolve a lista na ordem de atenção."""
    queryset = Manutencao.objects.filter(
        condominio__in=condominios, ativo=True
    ).select_related("condominio", "responsavel", "local", "equipamento")

    if filtros.is_valid():
        dados = filtros.cleaned_data
        if dados["condominio"]:
            queryset = queryset.filter(condominio=dados["condominio"])
        if dados["status"]:
            queryset = queryset.filter(status=dados["status"])
        if dados["tipo"]:
            queryset = queryset.filter(tipo=dados["tipo"])
        if dados["prioridade"]:
            queryset = queryset.filter(prioridade=dados["prioridade"])
        if dados["responsavel"]:
            queryset = queryset.filter(responsavel=dados["responsavel"])
        if dados["local"]:
            queryset = queryset.filter(local=dados["local"])
        if dados["equipamento"]:
            queryset = queryset.filter(equipamento=dados["equipamento"])
        if dados["fornecedor"]:
            queryset = queryset.filter(fornecedor=dados["fornecedor"])

        previsao = dados["previsao"]
        if previsao == "atrasadas":
            queryset = queryset.atrasadas()
        elif previsao == "hoje":
            queryset = queryset.previstas_para_hoje()
        elif previsao == "proximas":
            queryset = queryset.proximas()
        elif previsao == "sem_previsao":
            queryset = queryset.sem_previsao()

        busca = dados["busca"]
        if busca:
            queryset = queryset.filter(
                Q(titulo__icontains=busca)
                | Q(descricao_problema__icontains=busca)
                | Q(descricao_servico__icontains=busca)
                | Q(local__nome__icontains=busca)
                | Q(equipamento__nome__icontains=busca)
            )

        if dados["minhas"]:
            queryset = queryset.minhas(request.user)

    return queryset.na_ordem_de_atencao()


# -- Tradução para a tela -------------------------------------------------------


def _iniciais_da_demanda(demanda) -> dict:
    """O que a manutenção já sabe quando ela nasce de uma demanda.

    É sugestão, não decisão: o formulário abre preenchido e quem registra revisa
    antes de confirmar. O lugar e o equipamento vêm junto porque a demanda já
    sabe onde o problema está — perguntar de novo seria refazer um trabalho que
    o registro do relato já fez.
    """
    return {
        "titulo": demanda.titulo,
        "descricao_problema": demanda.descricao,
        "prioridade": demanda.prioridade,
        "responsavel": demanda.responsavel_id,
        "data_prevista": demanda.prazo,
        "local": demanda.local_id,
        "equipamento": demanda.equipamento_id,
    }


def _iniciais_da_manutencao(manutencao) -> dict:
    """O que o formulário já deve trazer quando a manutenção é aberta para edição."""
    return {
        "tipo": manutencao.tipo,
        "titulo": manutencao.titulo,
        "descricao_problema": manutencao.descricao_problema,
        "descricao_servico": manutencao.descricao_servico,
        "prioridade": manutencao.prioridade,
        "local": manutencao.local_id,
        "equipamento": manutencao.equipamento_id,
        "fornecedor": manutencao.fornecedor_id,
        "responsavel": manutencao.responsavel_id,
        "data_prevista": manutencao.data_prevista,
        "custo_previsto": manutencao.custo_previsto,
        "custo_real": manutencao.custo_real,
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


def _data_por_extenso(valor) -> str:
    """A data como a gestão a lê, ou a ausência dela."""
    return valor.strftime("%d/%m/%Y") if valor else "Sem previsão"


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


def _previsao_situacao(data_prevista, encerrada: bool):
    """Como a previsão deve ser lida: atrasada, hoje, próxima ou nada."""
    if data_prevista is None or encerrada:
        return "", ""
    hoje = timezone.localdate()
    if data_prevista < hoje:
        return "Atrasada", "danger"
    if data_prevista == hoje:
        return "Hoje", "warning"
    if data_prevista <= hoje + timezone.timedelta(days=7):
        return "Próxima", "secondary"
    return "", ""


def _manutencao_para_tela(manutencao, usuario=None) -> dict:
    """A manutenção como a linha da lista precisa dela.

    As ações por linha saem da mesma regra do domínio que o resto do sistema: a
    interface só desenha o que a pessoa já poderia fazer.
    """
    situacao, tom = _previsao_situacao(manutencao.data_prevista, manutencao.esta_encerrada)
    return {
        "pk": manutencao.pk,
        "titulo": manutencao.titulo,
        "tipo": manutencao.get_tipo_display(),
        "condominio": manutencao.condominio.nome,
        "responsavel": _nome_de(manutencao.responsavel),
        "local": manutencao.local.nome if manutencao.local else "",
        "equipamento": manutencao.equipamento.nome if manutencao.equipamento else "",
        "previsao": _data_por_extenso(manutencao.data_prevista),
        "previsao_situacao": situacao,
        "previsao_tom": tom,
        "prioridade": manutencao.get_prioridade_display(),
        "prioridade_tom": TOM_POR_PRIORIDADE.get(manutencao.prioridade, "secondary"),
        "status": manutencao.get_status_display(),
        "status_valor": manutencao.status,
        "status_tom": TOM_POR_STATUS.get(manutencao.status, "secondary"),
        "encerrada": manutencao.esta_encerrada,
        "acoes": _acoes_da_linha(manutencao, usuario),
    }


def _acoes_da_linha(manutencao, usuario):
    """As ações oferecidas na linha, já decididas pelo domínio."""
    if usuario is None:
        return []

    destino = reverse("manutencoes:detalhe", args=[manutencao.pk])
    acoes = []
    if pode_editar_manutencao(usuario, manutencao):
        acoes.append({"label": "Editar", "icon": "bx-pencil", "url": f"{destino}?abrir=editar"})
    if situacoes_para_mudar(usuario, manutencao):
        acoes.append(
            {"label": "Mudar situação", "icon": "bx-transfer", "url": f"{destino}?abrir=status"}
        )
    return acoes


def _manutencao_para_detalhe(manutencao) -> dict:
    """A manutenção como a página dela precisa: os dados e o que dá para fazer."""
    dados = _manutencao_para_tela(manutencao)
    dados.update(
        {
            "problema": manutencao.descricao_problema,
            "servico": manutencao.descricao_servico,
            "fornecedor": (
                manutencao.fornecedor.nome_razao_social if manutencao.fornecedor else ""
            ),
            "custo_previsto": _dinheiro(manutencao.custo_previsto),
            "custo_real": _dinheiro(manutencao.custo_real),
            "inicio": _momento_por_extenso(manutencao.data_inicio),
            "conclusao": _momento_por_extenso(manutencao.data_conclusao),
            "criada_em": _momento_por_extenso(manutencao.criado_em),
            "criada_por": _nome_de(manutencao.criado_por),
            "demanda_origem": (
                manutencao.demanda_origem.titulo if manutencao.demanda_origem_id else ""
            ),
            "demanda_origem_pk": manutencao.demanda_origem_id,
            "ativo": manutencao.ativo,
        }
    )
    return dados


def _historico_para_tela(manutencao) -> list[dict]:
    """O que aconteceu com a manutenção, do começo para o fim."""
    return [
        {
            "momento": _momento_por_extenso(evento.data_hora),
            "tipo": evento.get_tipo_evento_display(),
            "descricao": evento.descricao,
            "quem": _nome_de(evento.usuario),
        }
        for evento in manutencao.historico.select_related("usuario").order_by("data_hora")
    ]
