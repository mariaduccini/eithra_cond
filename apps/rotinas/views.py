"""Telas de rotinas recorrentes.

Uma rotina é a regra que faz uma atividade voltar a acontecer sem depender de
alguém lembrar; a execução é a ocorrência concreta daquela regra. A tela da
rotina mostra o combinado — o ritmo, o contexto e a próxima data — e as
ocorrências que já caíram. A tela das execuções é a lista de trabalho: o que
está atrasado, o que cai hoje e o que vem por aí.

A autorização é decidida no backend, sempre a partir do vínculo ativo da pessoa
com o condomínio. Quem não enxerga o módulo não descobre que a rotina existe: a
resposta é 404. Quem enxerga mas não responde por ela é recusado com 403 — a
existência já é legítima, a decisão não é. A operação não alcança o cadastro das
rotinas: o que ela vê são as ocorrências atribuídas a ela, na mesma rota, com o
conteúdo filtrado.
"""
from itertools import groupby

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.cadastros.models import Equipamento, LocalOperacional
from apps.core import audio
from apps.core.filtros import tem_filtros
from apps.core.paginacao import paginar_do_pedido

from .biblioteca import (
    ErroDeBiblioteca,
    ativar_modelo,
    marcar_nao_aplica,
    modelos_para_condominio,
    reabrir_modelo,
)
from .constants import Periodicidade, StatusExecucao, StatusItemChecklist
from .forms import (
    AtivacaoModeloForm,
    ChecklistForm,
    ConclusaoDeExecucaoForm,
    DemandaDaExecucaoForm,
    FiltroDeExecucoesForm,
    FiltroDeRotinasForm,
    InicioDeExecucaoForm,
    ItemDoChecklistForm,
    JustificativaDaExecucaoForm,
    ManutencaoDaExecucaoForm,
    RotinaForm,
    TratamentoDeItemForm,
)
from .models import (
    DecisaoModeloAtividade,
    ExecucaoRotina,
    EstadoModeloAtividade,
    ItemExecucaoChecklist,
    ItemModeloChecklist,
    ModeloAtividadeProgramada,
    RotinaRecorrente,
)
from .permissoes import (
    checklists_para_ver,
    condominios_para_checklist,
    condominios_para_criar,
    condominios_para_ver,
    execucoes_para_ver,
    pode_administrar_checklist,
    pode_administrar_rotina,
    pode_cancelar_execucao,
    pode_concluir,
    pode_criar_em_algum_condominio,
    pode_editar_checklist,
    pode_editar_item_do_checklist,
    pode_editar_rotina,
    pode_gerar_demanda,
    pode_gerar_execucao,
    pode_gerar_manutencao,
    pode_ignorar,
    pode_iniciar,
    pode_tratar_item,
    rotinas_para_ver,
    usuarios_elegiveis_entre,
)
from .services import (
    ErroDeRotina,
    abrir_demanda_da_execucao,
    cancelar_execucao,
    concluir_execucao,
    criar_checklist,
    criar_item_do_checklist,
    criar_manutencao_da_execucao,
    criar_rotina,
    definir_ativo,
    definir_checklist_ativo,
    definir_item_do_checklist_ativo,
    editar_checklist,
    editar_item_do_checklist,
    editar_rotina,
    gerar_proxima_execucao,
    ignorar_execucao,
    iniciar_execucao,
    tratar_item_checklist,
)

# Quantas ocorrências recentes a ficha da rotina mostra. A ficha é um resumo,
# não um arquivo: o histórico inteiro vive na lista de ocorrências.
LIMITE_DE_OCORRENCIAS_NA_FICHA = 50
RESUMO_DA_ROTINA = (
    ("titulo", "Atividade"),
    ("descricao", "Descrição"),
    ("tipo", "Tipo"),
    ("prioridade", "Prioridade"),
    ("periodicidade_tipo", "Periodicidade"),
    ("intervalo", "A cada"),
    ("dia_semana", "Dia da semana"),
    ("dia_mes", "Dia do mês"),
    ("mes_ano", "Mês"),
    ("data_inicio", "Início"),
    ("data_fim", "Fim"),
    ("responsavel", "Responsável"),
    ("local", "Local"),
    ("equipamento", "Equipamento"),
    ("fornecedor", "Fornecedor"),
    ("checklist_modelo", "Checklist"),
    ("gera_manutencao", "Manutenção preventiva"),
)

# Tom de cada situação de execução no badge. Discreto: a lista inteira não
# pode gritar.
TOM_POR_STATUS = {
    StatusExecucao.PENDENTE: "secondary",
    StatusExecucao.EM_ANDAMENTO: "primary",
    StatusExecucao.CONCLUIDA: "success",
    StatusExecucao.IGNORADA: "warning",
    StatusExecucao.CANCELADA: "secondary",
}

# Tom de cada situação de uma tarefa do checklist. Discreto também: a lista
# inteira não pode gritar.
TOM_DO_STATUS_DO_ITEM = {
    StatusItemChecklist.PENDENTE: "secondary",
    StatusItemChecklist.CONCLUIDO: "success",
    StatusItemChecklist.NAO_APLICAVEL: "secondary",
}


@login_required
def listagem(request):
    """As rotinas dos condomínios desta pessoa, ou as ocorrências dela.

    Quem responde ou supervisiona chega ao cadastro das rotinas. A operação não
    alcança o cadastro: a mesma rota recebe o conteúdo filtrado, e o que aparece
    são as ocorrências atribuídas a ela.
    """
    condominios = _condominios_para_ver(request)
    if not pode_criar_em_algum_condominio(request.user):
        return _render_execucoes(request, condominios)
    return _render_listagem(request, condominios)


@login_required
def criar(request):
    """Registra uma rotina nova. A primeira ocorrência nasce junto."""
    if request.method != "POST":
        return redirect("rotinas:listagem")

    condominios = condominios_para_criar(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomínio disponível para registrar rotina.")

    formulario = RotinaForm(request.POST, condominios=condominios)
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
        rotina = criar_rotina(
            condominio=dados["condominio"],
            criado_por=request.user,
            titulo=dados["titulo"],
            tipo=dados["tipo"],
            descricao=dados["descricao"],
            periodicidade_tipo=dados["periodicidade_tipo"],
            intervalo=dados["intervalo"],
            dia_semana=dados["dia_semana"],
            dia_mes=dados["dia_mes"],
            mes_ano=dados["mes_ano"],
            data_inicio=dados["data_inicio"],
            data_fim=dados["data_fim"],
            responsavel=dados["responsavel"],
            local=dados["local"],
            equipamento=dados["equipamento"],
            fornecedor=dados["fornecedor"],
            checklist_modelo=dados["checklist_modelo"],
            prioridade=dados["prioridade"],
            gera_manutencao=dados["gera_manutencao"],
        )
    except ErroDeRotina as erro:
        messages.error(request, str(erro))
        return _render_listagem(
            request,
            _condominios_para_ver(request),
            formulario=formulario,
            modal="criar",
            status=400,
        )

    messages.success(request, "Rotina registrada.")
    return redirect("rotinas:detalhe", pk=rotina.pk)


@login_required
def detalhe(request, pk):
    """A rotina como ela é hoje e as ocorrências que já caíram."""
    rotina = _rotina_autorizada(request, pk)
    return _render_detalhe(request, rotina, modal=request.GET.get("abrir"))


@login_required
@require_POST
def editar(request, pk):
    """Atualiza a regra da rotina. O que muda vale para o que vem depois."""
    rotina = _rotina_para_editar(request, pk)
    formulario = RotinaForm(
        request.POST, condominio=rotina.condominio, rotina=rotina
    )
    if not formulario.is_valid():
        return _render_detalhe(
            request, rotina, formulario=formulario, modal="editar", status=400
        )

    dados = formulario.cleaned_data
    try:
        editar_rotina(
            rotina=rotina,
            titulo=dados["titulo"],
            tipo=dados["tipo"],
            descricao=dados["descricao"],
            periodicidade_tipo=dados["periodicidade_tipo"],
            intervalo=dados["intervalo"],
            dia_semana=dados["dia_semana"],
            dia_mes=dados["dia_mes"],
            mes_ano=dados["mes_ano"],
            data_inicio=dados["data_inicio"],
            data_fim=dados["data_fim"],
            responsavel=dados["responsavel"],
            local=dados["local"],
            equipamento=dados["equipamento"],
            fornecedor=dados["fornecedor"],
            checklist_modelo=dados["checklist_modelo"],
            prioridade=dados["prioridade"],
            gera_manutencao=dados["gera_manutencao"],
        )
    except ErroDeRotina as erro:
        messages.error(request, str(erro))
        rotina.refresh_from_db()
        return _render_detalhe(
            request, rotina, formulario=formulario, modal="editar", status=400
        )

    messages.success(request, "Rotina atualizada.")
    return redirect("rotinas:detalhe", pk=rotina.pk)


@login_required
@require_POST
def alternar_ativo(request, pk):
    """Retira a rotina da operação, ou a devolve, sem apagar nada."""
    rotina = _rotina_para_administrar(request, pk)
    novo = not rotina.ativo
    definir_ativo(rotina=rotina, ativo=novo)
    if novo:
        messages.success(request, "Rotina devolvida à operação.")
    else:
        messages.success(request, "Rotina retirada da operação.")
    return redirect("rotinas:detalhe", pk=rotina.pk)


# -- Modelos de checklist -------------------------------------------------------
#
# O checklist é contexto da Rotina, não um módulo à parte: monta-se a lista aqui
# e ela é citada ao registrar a rotina. Não há item de menu próprio — o acesso
# sai da tela de Rotinas. Montar a lista é a mesma responsabilidade de criar a
# rotina; tirá-la de circulação é de quem responde pelo condomínio. Quem não
# alcança o cadastro recebe 404, e não descobre que o checklist existe.


@login_required
def checklists(request):
    """Os modelos de checklist dos condomínios que esta pessoa monta."""
    condominios = condominios_para_checklist(request.user)
    if not condominios.exists():
        raise Http404("Nenhum checklist disponível para consulta.")
    return _render_checklists(request, condominios)


@login_required
@require_POST
def checklist_criar(request):
    """Registra um modelo de checklist. As tarefas entram depois, uma a uma."""
    condominios = condominios_para_checklist(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomínio disponível para criar checklist.")

    formulario = ChecklistForm(request.POST, condominios=condominios)
    if not formulario.is_valid():
        return _render_checklists(
            request, condominios, formulario=formulario, modal="criar", status=400
        )

    dados = formulario.cleaned_data
    try:
        checklist = criar_checklist(
            condominio=dados["condominio"],
            criado_por=request.user,
            nome=dados["nome"],
            descricao=dados["descricao"],
        )
    except ErroDeRotina as erro:
        messages.error(request, str(erro))
        return _render_checklists(
            request, condominios, formulario=formulario, modal="criar", status=400
        )

    messages.success(request, "Checklist criado. Agora acrescente as tarefas.")
    return redirect("rotinas:checklist", pk=checklist.pk)


@login_required
def checklist_detalhe(request, pk):
    """O modelo por dentro: as tarefas, na ordem, e as rotinas que o citam."""
    checklist = _checklist_autorizado(request, pk)
    return _render_checklist_detalhe(
        request, checklist, modal=request.GET.get("abrir")
    )


@login_required
@require_POST
def checklist_editar(request, pk):
    """Atualiza o nome e a descrição do modelo. Vale para o que vem depois."""
    checklist = _checklist_para_editar(request, pk)
    formulario = ChecklistForm(
        request.POST, condominio=checklist.condominio, initial=_iniciais_do_checklist(checklist)
    )
    if not formulario.is_valid():
        return _render_checklist_detalhe(
            request, checklist, formulario=formulario, modal="editar", status=400
        )

    try:
        editar_checklist(
            checklist=checklist,
            nome=formulario.cleaned_data["nome"],
            descricao=formulario.cleaned_data["descricao"],
        )
    except ErroDeRotina as erro:
        messages.error(request, str(erro))
        checklist.refresh_from_db()
        return _render_checklist_detalhe(
            request, checklist, formulario=formulario, modal="editar", status=400
        )

    messages.success(request, "Checklist atualizado.")
    return redirect("rotinas:checklist", pk=checklist.pk)


@login_required
@require_POST
def checklist_alternar(request, pk):
    """Retira o modelo das próximas rotinas, ou o devolve, sem apagar nada."""
    checklist = _checklist_para_administrar(request, pk)
    novo = not checklist.ativo
    definir_checklist_ativo(checklist=checklist, ativo=novo)
    if novo:
        messages.success(request, "Checklist devolvido às rotinas.")
    else:
        messages.success(request, "Checklist retirado das próximas rotinas.")
    return redirect("rotinas:checklist", pk=checklist.pk)


@login_required
@require_POST
def checklist_item_criar(request, pk):
    """Acrescenta uma tarefa ao modelo, no fim da lista quando a ordem não vem."""
    checklist = _checklist_para_editar(request, pk)
    formulario = ItemDoChecklistForm(request.POST)
    if not formulario.is_valid():
        messages.error(request, _primeiro_erro(formulario))
        return redirect("rotinas:checklist", pk=checklist.pk)

    try:
        criar_item_do_checklist(
            checklist=checklist,
            descricao=formulario.cleaned_data["descricao"],
            ordem=formulario.cleaned_data["ordem"],
            obrigatorio=formulario.cleaned_data["obrigatorio"],
        )
    except ErroDeRotina as erro:
        messages.error(request, str(erro))
        return redirect("rotinas:checklist", pk=checklist.pk)

    messages.success(request, "Tarefa acrescentada.")
    return redirect("rotinas:checklist", pk=checklist.pk)


@login_required
@require_POST
def checklist_item_editar(request, pk, item_pk):
    """Ajusta a tarefa do modelo: o que fazer, em que posição e se é obrigatória."""
    checklist = _checklist_para_editar(request, pk)
    item = _item_do_modelo_autorizado(request, checklist, item_pk)

    formulario = ItemDoChecklistForm(request.POST)
    if not formulario.is_valid():
        messages.error(request, _primeiro_erro(formulario))
        return redirect("rotinas:checklist", pk=checklist.pk)

    try:
        editar_item_do_checklist(
            item=item,
            descricao=formulario.cleaned_data["descricao"],
            ordem=formulario.cleaned_data["ordem"] or 0,
            obrigatorio=formulario.cleaned_data["obrigatorio"],
        )
    except ErroDeRotina as erro:
        messages.error(request, str(erro))
        return redirect("rotinas:checklist", pk=checklist.pk)

    messages.success(request, "Tarefa atualizada.")
    return redirect("rotinas:checklist", pk=checklist.pk)


@login_required
@require_POST
def checklist_item_alternar(request, pk, item_pk):
    """Tira a tarefa das próximas ocorrências, ou a devolve, sem apagar nada."""
    checklist = _checklist_para_editar(request, pk)
    item = _item_do_modelo_autorizado(request, checklist, item_pk)

    novo = not item.ativo
    definir_item_do_checklist_ativo(item=item, ativo=novo)
    if novo:
        messages.success(request, "Tarefa devolvida ao checklist.")
    else:
        messages.success(request, "Tarefa retirada das próximas ocorrências.")
    return redirect("rotinas:checklist", pk=checklist.pk)


@login_required
@require_POST
def executar_geracao(request, pk):
    """Adianta a próxima ocorrência da rotina, se houver uma por preparar."""
    rotina = _rotina_para_gerar(request, pk)
    execucao = gerar_proxima_execucao(rotina)
    if execucao is None:
        messages.info(
            request,
            "A rotina não tem nova ocorrência a preparar agora.",
        )
    else:
        messages.success(request, "Próxima ocorrência preparada.")
    return redirect("rotinas:detalhe", pk=rotina.pk)


@login_required
def execucoes(request):
    """A lista de trabalho: o que está atrasado, o que cai hoje e o que vem."""
    return _render_execucoes(request, _condominios_para_ver(request))


@login_required
def execucao_detalhe(request, pk):
    """A ocorrência por dentro: o contexto do dia e o checklist que a acompanha."""
    execucao = _execucao_autorizada(request, pk)
    return _render_execucao(request, execucao)


@login_required
@require_POST
def tratar_item(request, execucao_pk, item_pk):
    """Registra o que aconteceu com uma tarefa do checklist.

    Uma ação rápida diz como a tarefa ficou — concluída ou não aplicável —, e a
    anotação diz o que vale lembrar. Uma tarefa só é tratada dentro da ocorrência
    aberta, e só por quem pode movimentá-la; encerrada a ocorrência, o retrato
    não se mexe mais.
    """
    execucao = _execucao_autorizada(request, execucao_pk)
    if not pode_tratar_item(request.user, execucao):
        raise PermissionDenied("Esta pessoa não registra tarefas desta ocorrência.")

    item = _obter_ou_404(
        ItemExecucaoChecklist.objects.filter(execucao=execucao), item_pk
    )

    formulario = TratamentoDeItemForm(request.POST)
    if not formulario.is_valid():
        messages.error(request, _primeiro_erro(formulario))
        return redirect("rotinas:execucao", pk=execucao.pk)

    try:
        tratar_item_checklist(
            item=item,
            usuario=request.user,
            status=formulario.cleaned_data["status"] or None,
            observacao=formulario.cleaned_data["observacao"],
        )
    except ErroDeRotina as erro:
        messages.error(request, str(erro))
        return redirect("rotinas:execucao", pk=execucao.pk)

    messages.success(request, "Tarefa registrada.")
    return redirect("rotinas:execucao", pk=execucao.pk)


@login_required
@require_POST
def iniciar(request, pk):
    """Começa uma ocorrência, guardando quando isso aconteceu."""
    execucao = _execucao_para_iniciar(request, pk)
    formulario = InicioDeExecucaoForm(request.POST)
    if not formulario.is_valid():
        messages.error(request, "Não foi possível iniciar esta ocorrência.")
        return redirect("rotinas:execucoes")

    try:
        iniciar_execucao(execucao=execucao, usuario=request.user)
    except ErroDeRotina as erro:
        messages.error(request, str(erro))
        return redirect("rotinas:execucoes")

    messages.success(request, "Ocorrência iniciada.")
    return redirect("rotinas:execucoes")


@login_required
@require_POST
def concluir(request, pk):
    """Conclui a ocorrência dizendo o que se encontrou."""
    execucao = _execucao_para_concluir(request, pk)
    formulario = ConclusaoDeExecucaoForm(request.POST)
    if not formulario.is_valid():
        messages.error(request, _primeiro_erro(formulario))
        return redirect("rotinas:execucoes")

    try:
        concluir_execucao(
            execucao=execucao,
            usuario=request.user,
            resultado=formulario.cleaned_data["resultado"],
            observacao=formulario.cleaned_data["observacao"],
            data_execucao=formulario.cleaned_data["data_execucao"],
        )
    except ErroDeRotina as erro:
        messages.error(request, str(erro))
        return redirect("rotinas:execucoes")

    messages.success(request, "Ocorrência concluída.")
    return redirect("rotinas:execucoes")


@login_required
@require_POST
def ignorar(request, pk):
    """Registra que a atividade não se aplicava naquele período."""
    execucao = _execucao_para_ignorar(request, pk)
    formulario = JustificativaDaExecucaoForm(request.POST)
    if not formulario.is_valid():
        messages.error(request, _primeiro_erro(formulario))
        return redirect("rotinas:execucoes")

    try:
        ignorar_execucao(
            execucao=execucao,
            usuario=request.user,
            justificativa=formulario.cleaned_data["justificativa"],
        )
    except ErroDeRotina as erro:
        messages.error(request, str(erro))
        return redirect("rotinas:execucoes")

    messages.success(request, "Ocorrência ignorada. A rotina segue adiante.")
    return redirect("rotinas:execucoes")


@login_required
@require_POST
def cancelar(request, pk):
    """Encerra uma ocorrência que não vai acontecer, sem cancelar a rotina."""
    execucao = _execucao_para_cancelar(request, pk)
    formulario = JustificativaDaExecucaoForm(request.POST)
    if not formulario.is_valid():
        messages.error(request, _primeiro_erro(formulario))
        return redirect("rotinas:execucoes")

    try:
        cancelar_execucao(
            execucao=execucao,
            usuario=request.user,
            justificativa=formulario.cleaned_data["justificativa"],
        )
    except ErroDeRotina as erro:
        messages.error(request, str(erro))
        return redirect("rotinas:execucoes")

    messages.success(request, "Ocorrência cancelada. A rotina segue adiante.")
    return redirect("rotinas:execucoes")


@login_required
@require_POST
def demanda(request, pk):
    """Abre uma demanda a partir do que a ocorrência encontrou."""
    execucao = _execucao_para_demanda(request, pk)
    formulario = DemandaDaExecucaoForm(request.POST)
    if not formulario.is_valid():
        messages.error(request, _primeiro_erro(formulario))
        return redirect("rotinas:execucoes")

    dados = formulario.cleaned_data
    try:
        abrir_demanda_da_execucao(
            execucao=execucao,
            usuario=request.user,
            titulo=dados["titulo"],
            descricao=dados["descricao"],
            prioridade=dados["prioridade"],
            prazo=dados["prazo"],
        )
    except ErroDeRotina as erro:
        messages.error(request, str(erro))
        return redirect("rotinas:execucoes")

    messages.success(request, "Demanda aberta a partir desta ocorrência.")
    return redirect("demandas:detalhe", pk=execucao.demanda_gerada_id)


@login_required
@require_POST
def manutencao(request, pk):
    """Cria uma manutenção preventiva a partir da ocorrência."""
    execucao = _execucao_para_manutencao(request, pk)
    formulario = ManutencaoDaExecucaoForm(request.POST)
    if not formulario.is_valid():
        messages.error(request, _primeiro_erro(formulario))
        return redirect("rotinas:execucoes")

    dados = formulario.cleaned_data
    try:
        criar_manutencao_da_execucao(
            execucao=execucao,
            usuario=request.user,
            titulo=dados["titulo"],
            descricao_problema=dados["descricao_problema"],
            data_prevista=dados["data_prevista"],
        )
    except ErroDeRotina as erro:
        messages.error(request, str(erro))
        return redirect("rotinas:execucoes")

    messages.success(request, "Manutenção preventiva criada a partir desta ocorrência.")
    return redirect("manutencoes:detalhe", pk=execucao.manutencao_gerada_id)


# -- Autorização ----------------------------------------------------------------
#
# Toda pergunta passa pelo domínio. Quem não enxerga o módulo não descobre que a
# rotina existe: a resposta é 404. Quem enxerga mas não responde por ela é
# recusado com 403 — a existência já é legítima, a decisão não é.


def _condominios_para_ver(request):
    """Condomínios cujas rotinas esta pessoa acompanha, ou 404."""
    condominios = condominios_para_ver(request.user)
    if not condominios.exists():
        raise Http404("Nenhuma rotina disponível para consulta.")
    return condominios


def _rotina_autorizada(request, pk):
    """Resolve a rotina dentro dos condomínios que esta pessoa acompanha."""
    return _obter_ou_404(
        rotinas_para_ver(request.user).select_related(
            "condominio", "responsavel", "local", "equipamento", "fornecedor"
        ),
        pk,
    )


def _rotina_para_editar(request, pk):
    """Resolve a rotina e exige, no backend, o direito de editar a regra."""
    rotina = _rotina_autorizada(request, pk)
    if not pode_editar_rotina(request.user, rotina):
        raise PermissionDenied("Esta pessoa não responde por esta rotina.")
    return rotina


def _rotina_para_administrar(request, pk):
    """Resolve a rotina e exige, no backend, o direito de ativá-la ou inativá-la."""
    rotina = _rotina_autorizada(request, pk)
    if not pode_administrar_rotina(request.user, rotina):
        raise PermissionDenied("Esta pessoa não administra esta rotina.")
    return rotina


def _rotina_para_gerar(request, pk):
    """Resolve a rotina e exige, no backend, o direito de gerar a ocorrência."""
    rotina = _rotina_autorizada(request, pk)
    if not pode_gerar_execucao(request.user, rotina):
        raise PermissionDenied("Esta pessoa não gera ocorrências desta rotina.")
    return rotina


def _execucao_autorizada(request, pk):
    """Resolve a execução dentro do que esta pessoa alcança agora."""
    return _obter_ou_404(
        execucoes_para_ver(request.user).select_related(
            "condominio",
            "responsavel",
            "rotina",
            "rotina__local",
            "rotina__equipamento",
            "rotina__fornecedor",
        ),
        pk,
    )


def _execucao_para_iniciar(request, pk):
    """Resolve a execução e exige, no backend, o direito de começar."""
    execucao = _execucao_autorizada(request, pk)
    if not pode_iniciar(request.user, execucao):
        raise PermissionDenied("Esta pessoa não pode iniciar esta ocorrência.")
    return execucao


def _execucao_para_concluir(request, pk):
    """Resolve a execução e exige, no backend, o direito de concluir."""
    execucao = _execucao_autorizada(request, pk)
    if not pode_concluir(request.user, execucao):
        raise PermissionDenied("Esta pessoa não pode concluir esta ocorrência.")
    return execucao


def _execucao_para_ignorar(request, pk):
    """Resolve a execução e exige, no backend, o direito de ignorá-la."""
    execucao = _execucao_autorizada(request, pk)
    if not pode_ignorar(request.user, execucao):
        raise PermissionDenied("Esta pessoa não pode ignorar esta ocorrência.")
    return execucao


def _execucao_para_cancelar(request, pk):
    """Resolve a execução e exige, no backend, o direito de cancelá-la."""
    execucao = _execucao_autorizada(request, pk)
    if not pode_cancelar_execucao(request.user, execucao):
        raise PermissionDenied("Esta pessoa não pode cancelar esta ocorrência.")
    return execucao


def _execucao_para_demanda(request, pk):
    """Resolve a execução e exige, no backend, o direito de abrir a demanda."""
    execucao = _execucao_autorizada(request, pk)
    if not pode_gerar_demanda(request.user, execucao):
        raise PermissionDenied("Esta pessoa não abre demanda neste condomínio.")
    return execucao


def _execucao_para_manutencao(request, pk):
    """Resolve a execução e exige, no backend, o direito de criar a manutenção."""
    execucao = _execucao_autorizada(request, pk)
    if not pode_gerar_manutencao(request.user, execucao):
        raise PermissionDenied("Esta pessoa não cria manutenção neste condomínio.")
    return execucao


def _obter_ou_404(queryset, pk):
    """Resolve um registro pela chave, ou 404 — inclusive se a chave vier torta."""
    try:
        return get_object_or_404(queryset, pk=pk)
    except (ValidationError, ValueError, TypeError):
        raise Http404("Registro não encontrado.") from None


def _checklist_autorizado(request, pk):
    """Resolve o checklist dentro do que esta pessoa alcança agora."""
    return _obter_ou_404(
        checklists_para_ver(request.user).select_related("condominio", "criado_por"),
        pk,
    )


def _checklist_para_editar(request, pk):
    """Resolve o checklist e exige, no backend, o direito de montar a lista.

    Montar a lista é a mesma responsabilidade de criar a rotina: administração,
    síndica e supervisão.
    """
    checklist = _checklist_autorizado(request, pk)
    if not pode_editar_checklist(request.user, checklist):
        raise PermissionDenied("Esta pessoa não monta este checklist.")
    return checklist


def _checklist_para_administrar(request, pk):
    """Resolve o checklist e exige o direito de ativá-lo ou inativá-lo.

    Tirar o modelo de circulação é decisão de quem responde pelo condomínio, e
    não de quem apenas supervisiona — como acontece com a rotina.
    """
    checklist = _checklist_autorizado(request, pk)
    if not pode_administrar_checklist(request.user, checklist):
        raise PermissionDenied("Esta pessoa não administra este checklist.")
    return checklist


def _item_do_modelo_autorizado(request, checklist, item_pk):
    """Resolve uma tarefa do modelo e exige, no backend, o direito de mexer nela."""
    item = _obter_ou_404(
        ItemModeloChecklist.objects.filter(checklist=checklist), item_pk
    )
    if not pode_editar_item_do_checklist(request.user, item):
        raise PermissionDenied("Esta pessoa não edita as tarefas deste checklist.")
    return item


# -- Montagem das telas ---------------------------------------------------------


def _cadastros_ativos_entre(modelo, condominios):
    """Os cadastros em uso nos condomínios que a pessoa enxerga, para filtrar."""
    if condominios is None:
        return modelo.objects.none()
    return modelo.objects.filter(condominio__in=condominios, ativo=True)


def _render_listagem(request, condominios, *, formulario=None, modal=None, status=200):
    """Monta a lista de rotinas com os filtros aplicados e o formulário de nova."""
    filtros = FiltroDeRotinasForm(
        request.GET or None,
        condominios=condominios,
        responsaveis=usuarios_elegiveis_entre(condominios),
        locais=_cadastros_ativos_entre(LocalOperacional, condominios),
        equipamentos=_cadastros_ativos_entre(Equipamento, condominios),
    )
    pagina_rotinas = paginar_do_pedido(
        _rotinas_filtradas(request, condominios, filtros), request.GET
    )
    pode_criar_condominios = condominios_para_criar(request.user)
    pode_criar = pode_criar_condominios.exists()

    if formulario is None and pode_criar:
        formulario = RotinaForm(condominios=pode_criar_condominios)

    contexto = {
        "rotinas": [_rotina_para_tela(item, request.user) for item in pagina_rotinas],
        "pagina_rotinas": pagina_rotinas,
        "filtros": filtros,
        "filtros_ativos": tem_filtros(filtros),
        "total": pagina_rotinas.paginator.count,
        "pode_criar": pode_criar,
        "formulario_rotina": formulario,
        "modal_aberto": modal,
        "mostrar_condominio": condominios.count() > 1,
        "resumo_da_rotina": RESUMO_DA_ROTINA,
        # A gravação usa o mesmo endpoint autorizado de relato por voz: os
        # papéis que criam rotinas também podem criar demandas.
        "voz_disponivel": audio.disponivel() and pode_criar,
    }
    return render(request, "pages/rotinas/listagem.html", contexto, status=status)


def _condominio_da_biblioteca(request, condominios):
    """Resolve só o condomínio que a pessoa pode operar."""
    fonte = request.POST if request.method == "POST" else request.GET
    informado = fonte.get("condominio")
    if not informado:
        elegiveis = list(condominios[:2])
        return elegiveis[0] if len(elegiveis) == 1 else None
    return _obter_ou_404(condominios, informado)


def _categorias_da_biblioteca(itens):
    """Agrupa cartões preservando a ordem do catálogo."""
    categorias = []
    for categoria, grupo in groupby(itens, key=lambda item: item["modelo"].categoria):
        categorias.append({"nome": categoria, "itens": list(grupo)})
    return categorias


@login_required
def biblioteca_preventiva(request):
    """Mostra modelos sugeridos e modelos disponíveis para revisão manual."""
    condominios = condominios_para_criar(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomínio disponível para revisar modelos.")
    condominio = _condominio_da_biblioteca(request, condominios)
    sugestoes, demais = modelos_para_condominio(condominio) if condominio else ([], [])
    return render(
        request,
        "pages/rotinas/biblioteca_preventiva.html",
        {
            "condominios": condominios,
            "condominio": condominio,
            "categorias_sugeridas": _categorias_da_biblioteca(sugestoes),
            "categorias_demais": _categorias_da_biblioteca(demais),
            "total_sugeridos": len(sugestoes),
            "estado_disponivel": EstadoModeloAtividade.DISPONIVEL,
            "estado_ativado": EstadoModeloAtividade.ATIVADA,
            "estado_nao_aplica": EstadoModeloAtividade.NAO_APLICA,
        },
    )


@login_required
def revisar_modelo_preventivo(request, chave):
    """Exige a revisão da atividade e da frequência antes de qualquer gravação."""
    condominios = condominios_para_criar(request.user)
    condominio = _condominio_da_biblioteca(request, condominios)
    if condominio is None:
        raise Http404("Escolha um condomínio autorizado.")
    modelo = get_object_or_404(
        ModeloAtividadeProgramada.objects.ativos(), chave=chave
    )
    sugestoes, demais = modelos_para_condominio(condominio)
    item = next(
        (
            candidato
            for candidato in (*sugestoes, *demais)
            if candidato["modelo"].pk == modelo.pk
        ),
        None,
    )
    if item is None:
        raise Http404("Modelo não encontrado.")

    equipamentos = list(
        condominio.equipamentos.ativos()
        .select_related("tipo_equipamento", "local")
        .order_by("nome")
    )
    locais = list(
        condominio.locais.ativos().select_related("tipo_local").order_by("nome")
    )
    from .biblioteca import _equipamentos_compativeis, _locais_compativeis

    equipamentos_sugeridos = _equipamentos_compativeis(
        modelo, equipamentos
    ) if modelo.gatilhos_equipamento else []
    locais_sugeridos = _locais_compativeis(modelo, locais) if modelo.gatilhos_local else []
    initial = {
        "titulo": modelo.titulo,
        "descricao": modelo.descricao or modelo.observacoes,
        "tipo": modelo.tipo,
        "periodicidade_tipo": modelo.periodicidade_tipo,
        "intervalo": modelo.periodicidade_intervalo or 1,
        "dia_semana": timezone.localdate().weekday(),
        "dia_mes": timezone.localdate().day,
        "mes_ano": timezone.localdate().month,
        "data_inicio": timezone.localdate(),
        "prioridade": modelo.prioridade,
        "equipamento": (
            equipamentos_sugeridos[0].pk if len(equipamentos_sugeridos) == 1 else None
        ),
        "local": locais_sugeridos[0].pk if len(locais_sugeridos) == 1 else None,
    }
    formulario = AtivacaoModeloForm(
        request.POST or None,
        condominios=condominios,
        condominio=condominio,
        initial=initial,
        modelo=modelo,
    )
    if request.method == "POST" and formulario.is_valid():
        try:
            rotina = ativar_modelo(
                modelo=modelo,
                condominio=condominio,
                usuario=request.user,
                dados=formulario.cleaned_data,
            )
        except ErroDeBiblioteca as erro:
            formulario.add_error(None, str(erro))
        else:
            messages.success(
                request, "Atividade programada criada e vinculada ao modelo."
            )
            return redirect("rotinas:detalhe", pk=rotina.pk)

    return render(
        request,
        "pages/rotinas/revisar_modelo_preventivo.html",
        {
            "modelo": modelo,
            "condominio": condominio,
            "formulario": formulario,
            "evidencias": item["evidencias"],
            "total_de_tarefas": len(modelo.checklist_sugerido),
        },
        status=400 if request.method == "POST" else 200,
    )


@login_required
@require_POST
def dispensar_modelo_preventivo(request, chave):
    condominios = condominios_para_criar(request.user)
    condominio = _condominio_da_biblioteca(request, condominios)
    if condominio is None:
        raise Http404("Escolha um condomínio autorizado.")
    modelo = get_object_or_404(
        ModeloAtividadeProgramada.objects.ativos(), chave=chave
    )
    observacao = (request.POST.get("observacao") or "").strip()
    if not observacao:
        messages.error(request, "Diga por que este modelo não se aplica.")
    else:
        try:
            marcar_nao_aplica(
                modelo=modelo,
                condominio=condominio,
                usuario=request.user,
                observacao=observacao,
            )
        except ErroDeBiblioteca as erro:
            messages.error(request, str(erro))
        else:
            messages.success(request, "Modelo marcado como não aplicável.")
    return redirect(f"{reverse('rotinas:biblioteca_preventiva')}?condominio={condominio.pk}")


@login_required
@require_POST
def reabrir_modelo_preventivo(request, chave):
    condominios = condominios_para_criar(request.user)
    condominio = _condominio_da_biblioteca(request, condominios)
    if condominio is None:
        raise Http404("Escolha um condomínio autorizado.")
    modelo = get_object_or_404(
        ModeloAtividadeProgramada.objects.ativos(), chave=chave
    )
    try:
        reabrir_modelo(modelo=modelo, condominio=condominio, usuario=request.user)
    except ErroDeBiblioteca as erro:
        messages.error(request, str(erro))
    else:
        messages.success(request, "Modelo disponível novamente para revisão.")
    return redirect(f"{reverse('rotinas:biblioteca_preventiva')}?condominio={condominio.pk}")


def _render_detalhe(request, rotina, *, formulario=None, modal=None, status=200):
    """Monta a tela da rotina com o que esta pessoa pode fazer com ela."""
    pode_editar = pode_editar_rotina(request.user, rotina)
    if modal == "editar" and not pode_editar:
        modal = None

    contexto = {
        "rotina": _rotina_para_detalhe(rotina),
        "execucoes": _execucoes_da_rotina(rotina),
        "pode_editar": pode_editar,
        "pode_gerar": pode_gerar_execucao(request.user, rotina),
        "pode_administrar": pode_administrar_rotina(request.user, rotina),
        "formulario_rotina": formulario
        or RotinaForm(
            condominio=rotina.condominio,
            rotina=rotina,
            initial=_iniciais_da_rotina(rotina),
        ),
        "modal_aberto": modal,
    }
    return render(request, "pages/rotinas/detalhe.html", contexto, status=status)


def _render_execucoes(request, condominios, *, status=200):
    """Monta a lista de ocorrências, aberta no recorte que merece atenção."""
    # Sem recorte informado, a lista abre no que ainda depende de alguém. O
    # recorte entra nos dados do formulário para que a escolha apareça na tela.
    dados = request.GET.copy()
    if "recorte" not in dados:
        dados["recorte"] = "pendentes"

    filtros = FiltroDeExecucoesForm(
        dados,
        condominios=condominios,
        responsaveis=usuarios_elegiveis_entre(condominios),
    )
    pagina_execucoes = paginar_do_pedido(
        _execucoes_filtradas(request, condominios, filtros), request.GET
    )
    linhas = [_execucao_para_tela(item, request.user) for item in pagina_execucoes]

    # Cada ação da lista abre o mesmo modal, e o modal recebe o endereço da
    # ocorrência no momento em que abre. Assim os modais são poucos — um por
    # ação —, e não um por linha.
    acoes_disponiveis = {
        acao["chave"] for linha in linhas for acao in linha["acoes"]
    }

    contexto = {
        "execucoes": linhas,
        "pagina_execucoes": pagina_execucoes,
        "filtros": filtros,
        "total": pagina_execucoes.paginator.count,
        "mostrar_condominio": condominios.count() > 1,
        "filtros_ativos": bool(request.GET),
        # Quem responde ou supervisiona chega ao cadastro das rotinas; a
        # operação entra pela lista de ocorrências dela e não volta para lá.
        "pode_ver_cadastro": pode_criar_em_algum_condominio(request.user),
        "acoes_disponiveis": acoes_disponiveis,
        "formulario_conclusao": (
            ConclusaoDeExecucaoForm(auto_id="concluir_%s")
            if "concluir" in acoes_disponiveis
            else None
        ),
        "formulario_ignorar": (
            JustificativaDaExecucaoForm(auto_id="ignorar_%s")
            if "ignorar" in acoes_disponiveis
            else None
        ),
        "formulario_cancelar": (
            JustificativaDaExecucaoForm(auto_id="cancelar_%s")
            if "cancelar" in acoes_disponiveis
            else None
        ),
        "formulario_demanda": (
            DemandaDaExecucaoForm(auto_id="demanda_%s")
            if "demanda" in acoes_disponiveis
            else None
        ),
        "formulario_manutencao": (
            ManutencaoDaExecucaoForm(auto_id="manutencao_%s")
            if "manutencao" in acoes_disponiveis
            else None
        ),
    }
    return render(request, "pages/rotinas/execucoes.html", contexto, status=status)


def _rotinas_filtradas(request, condominios, filtros):
    """Aplica os filtros essenciais e devolve as rotinas na ordem de atenção."""
    queryset = (
        rotinas_para_ver(request.user)
        .filter(condominio__in=condominios)
        .select_related("condominio", "responsavel", "local", "equipamento")
    )

    if filtros.is_valid():
        dados = filtros.cleaned_data
        if dados["condominio"]:
            queryset = queryset.filter(condominio=dados["condominio"])
        if dados["tipo"]:
            queryset = queryset.filter(tipo=dados["tipo"])
        if dados["responsavel"]:
            queryset = queryset.filter(responsavel=dados["responsavel"])
        if dados["local"]:
            queryset = queryset.filter(local=dados["local"])
        if dados["equipamento"]:
            queryset = queryset.filter(equipamento=dados["equipamento"])

        situacao = dados["situacao"]
        if situacao == "ativas":
            queryset = queryset.ativas()
        elif situacao == "vencidas":
            queryset = queryset.vencidas()
        elif situacao == "proximas":
            queryset = queryset.proximas()
        elif situacao == "sem_proxima":
            queryset = queryset.ativas().filter(proxima_execucao__isnull=True)
        elif situacao == "inativas":
            queryset = queryset.filter(ativo=False)

        if dados["busca"]:
            queryset = queryset.buscar(dados["busca"])

    return queryset.na_ordem_de_atencao()


def _execucoes_filtradas(request, condominios, filtros):
    """Aplica os filtros essenciais e devolve as ocorrências na ordem de atenção."""
    queryset = execucoes_para_ver(request.user).filter(
        condominio__in=condominios
    ).select_related("condominio", "responsavel", "rotina").prefetch_related("itens")

    if filtros.is_valid():
        dados = filtros.cleaned_data
        if dados["condominio"]:
            queryset = queryset.filter(condominio=dados["condominio"])

        recorte = dados["recorte"]
        if recorte == "pendentes":
            queryset = queryset.pendentes()
        elif recorte == "atrasadas":
            queryset = queryset.atrasadas()
        elif recorte == "hoje":
            queryset = queryset.de_hoje()
        elif recorte == "proximas":
            queryset = queryset.proximas()
        elif recorte == "com_checklist_pendente":
            queryset = queryset.com_checklist_pendente()
        elif recorte == "concluidas":
            queryset = queryset.concluidas()

        if dados["responsavel"]:
            queryset = queryset.filter(responsavel=dados["responsavel"])
        if dados["busca"]:
            queryset = queryset.filter(rotina__titulo__icontains=dados["busca"])

    return queryset.na_ordem_de_atencao()


# -- Tradução para a tela -------------------------------------------------------


def _nome_de(usuario) -> str:
    """O nome pelo qual a pessoa é reconhecida na tela."""
    return (
        getattr(usuario, "full_name", "")
        or getattr(usuario, "email", "")
        or "Sem responsável"
    )


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


def _primeiro_erro(formulario) -> str:
    """A primeira frase humana de um erro de formulário."""
    for erros in formulario.errors.values():
        if erros:
            return erros[0]
    return "Não foi possível concluir a ação."


def _periodicidade_por_extenso(rotina) -> str:
    """A periodicidade dita em palavras simples: 'toda semana', 'a cada 2 meses'."""
    tipo = rotina.periodicidade_tipo
    intervalo = rotina.intervalo or 1

    if tipo in (Periodicidade.DIARIA, Periodicidade.INTERVALO_DIAS):
        return "Todo dia" if intervalo == 1 else f"A cada {intervalo} dias"

    if tipo == Periodicidade.SEMANAL:
        base = "Toda semana" if intervalo == 1 else f"A cada {intervalo} semanas"
        dia = rotina.get_dia_semana_display()
        return f"{base} · {dia}" if dia else base

    if tipo == Periodicidade.MENSAL:
        base = "Todo mês" if intervalo == 1 else f"A cada {intervalo} meses"
        return f"{base} · dia {rotina.dia_mes}" if rotina.dia_mes else base

    if tipo == Periodicidade.ANUAL:
        base = "Todo ano" if intervalo == 1 else f"A cada {intervalo} anos"
        if rotina.dia_mes and rotina.mes_ano:
            return f"{base} · {rotina.dia_mes:02d}/{rotina.mes_ano:02d}"
        return base

    return rotina.get_periodicidade_tipo_display()


def _situacao_da_rotina(rotina):
    """Como a rotina deve ser lida agora. Derivada, nunca persistida.

    A situação sai de duas coisas que já existem — estar ativa e ter uma próxima
    execução marcada —, e não de um campo à parte que precisaria ser mantido.
    """
    if not rotina.ativo:
        return "Inativa", "secondary"
    if rotina.proxima_execucao is None:
        return "Sem próxima", "warning"
    if rotina.proxima_execucao < timezone.localdate():
        return "Vencida", "danger"
    return "Ativa", "success"


def _iniciais_da_rotina(rotina) -> dict:
    """O que o formulário já deve trazer quando a rotina é aberta para edição."""
    return {
        "titulo": rotina.titulo,
        "descricao": rotina.descricao,
        "tipo": rotina.tipo,
        "periodicidade_tipo": rotina.periodicidade_tipo,
        "intervalo": rotina.intervalo,
        "dia_semana": rotina.dia_semana,
        "dia_mes": rotina.dia_mes,
        "mes_ano": rotina.mes_ano,
        "data_inicio": rotina.data_inicio,
        "data_fim": rotina.data_fim,
        "prioridade": rotina.prioridade,
        "local": rotina.local_id,
        "equipamento": rotina.equipamento_id,
        "fornecedor": rotina.fornecedor_id,
        "responsavel": rotina.responsavel_id,
        "checklist_modelo": rotina.checklist_modelo_id,
        "gera_manutencao": rotina.gera_manutencao,
    }


def _acoes_da_rotina(rotina, usuario):
    """As ações oferecidas na linha da rotina, já decididas pelo domínio."""
    if usuario is None:
        return []

    destino = reverse("rotinas:detalhe", args=[rotina.pk])
    acoes = []
    if pode_editar_rotina(usuario, rotina):
        acoes.append(
            {"label": "Editar", "icon": "bx-pencil", "url": f"{destino}?abrir=editar", "post": False}
        )
    if pode_gerar_execucao(usuario, rotina):
        acoes.append(
            {
                "label": "Gerar próxima ocorrência",
                "icon": "bx-calendar-plus",
                "url": reverse("rotinas:executar_geracao", args=[rotina.pk]),
                "post": True,
            }
        )
    if pode_administrar_rotina(usuario, rotina):
        acoes.append(
            {
                "label": "Desativar" if rotina.ativo else "Ativar",
                "icon": "bx-block" if rotina.ativo else "bx-check-circle",
                "url": reverse("rotinas:alternar_ativo", args=[rotina.pk]),
                "post": True,
            }
        )
    return acoes


def _rotina_para_tela(rotina, usuario=None) -> dict:
    """A rotina como a linha da lista precisa dela."""
    situacao, tom = _situacao_da_rotina(rotina)
    return {
        "pk": rotina.pk,
        "titulo": rotina.titulo,
        "tipo": rotina.get_tipo_display(),
        "periodicidade": _periodicidade_por_extenso(rotina),
        "condominio": rotina.condominio.nome,
        "responsavel": _nome_de(rotina.responsavel),
        "local": rotina.local.nome if rotina.local else "",
        "equipamento": rotina.equipamento.nome if rotina.equipamento else "",
        "proxima": _data_por_extenso(rotina.proxima_execucao),
        "situacao": situacao,
        "situacao_tom": tom,
        "ativo": rotina.ativo,
        "acoes": _acoes_da_rotina(rotina, usuario),
    }


def _rotina_para_detalhe(rotina) -> dict:
    """A rotina como a página dela precisa: a regra, o contexto e a vigência."""
    dados = _rotina_para_tela(rotina)
    dados.update(
        {
            "descricao": rotina.descricao,
            "fornecedor": (
                rotina.fornecedor.nome_razao_social if rotina.fornecedor else ""
            ),
            "prioridade": rotina.get_prioridade_display(),
            "gera_manutencao": rotina.gera_manutencao,
            "checklist": (
                rotina.checklist_modelo.nome if rotina.checklist_modelo_id else ""
            ),
            "inicio": _data_por_extenso(rotina.data_inicio),
            "fim": _data_por_extenso(rotina.data_fim) if rotina.data_fim else "Sem fim",
            "criada_em": _momento_por_extenso(rotina.criado_em),
            "criada_por": _nome_de(rotina.criado_por),
            "proxima_iso": rotina.proxima_execucao,
        }
    )
    return dados


def _execucoes_da_rotina(rotina) -> list[dict]:
    """As ocorrências mais recentes da rotina, do que já caiu para o que vem."""
    recentes = (
        rotina.execucoes.select_related("responsavel")
        .prefetch_related("itens")
        .order_by("-data_prevista")[:LIMITE_DE_OCORRENCIAS_NA_FICHA]
    )
    return [_execucao_para_tela(item) for item in recentes]


def _acoes_da_execucao(execucao, usuario) -> list[dict]:
    """As ações oferecidas na linha da ocorrência, já decididas pelo domínio.

    Cada ação traz o endereço que confirma a decisão. A lista abre um modal
    curto e a decisão só sai por POST — a linha não muda nada sozinha.
    """

    def acao(chave, label, icon):
        return {
            "chave": chave,
            "label": label,
            "icon": icon,
            "url": reverse(f"rotinas:{chave}", args=[execucao.pk]),
        }

    if usuario is None:
        return []

    acoes = []
    if pode_iniciar(usuario, execucao):
        acoes.append(acao("iniciar", "Iniciar", "bx-play-circle"))
    if pode_concluir(usuario, execucao):
        acoes.append(acao("concluir", "Concluir", "bx-check-circle"))
    if pode_ignorar(usuario, execucao):
        acoes.append(acao("ignorar", "Ignorar", "bx-slash-circle"))
    if pode_cancelar_execucao(usuario, execucao):
        acoes.append(acao("cancelar", "Cancelar ocorrência", "bx-x-circle"))
    if pode_gerar_demanda(usuario, execucao) and not execucao.demanda_gerada_id:
        acoes.append(acao("demanda", "Abrir demanda", "bx-list-plus"))
    if pode_gerar_manutencao(usuario, execucao) and not execucao.manutencao_gerada_id:
        acoes.append(acao("manutencao", "Criar manutenção", "bx-wrench"))
    return acoes


def _execucao_para_tela(execucao, usuario=None) -> dict:
    """A ocorrência como a linha da lista precisa dela."""
    progresso = execucao.progresso_do_checklist
    return {
        "pk": execucao.pk,
        "rotina": execucao.rotina.titulo,
        "rotina_pk": execucao.rotina_id,
        "condominio": execucao.condominio.nome,
        "responsavel": _nome_de(execucao.responsavel),
        "data_prevista": _data_por_extenso(execucao.data_prevista),
        "situacao": execucao.get_status_display(),
        "situacao_valor": execucao.status,
        "situacao_tom": TOM_POR_STATUS.get(execucao.status, "secondary"),
        "resultado": execucao.get_resultado_display() if execucao.resultado else "",
        "atrasada": execucao.esta_atrasada,
        "encerrada": execucao.esta_encerrada,
        "concluida_em": _momento_por_extenso(execucao.concluido_em),
        "observacao": execucao.observacao_execucao,
        "tem_checklist": progresso["total"] > 0,
        "checklist_percentual": progresso["percentual"],
        "checklist_obrigatorio_pendente": execucao.tem_item_obrigatorio_pendente,
        "demanda_gerada": (
            execucao.demanda_gerada.titulo if execucao.demanda_gerada_id else ""
        ),
        "demanda_gerada_pk": execucao.demanda_gerada_id,
        "manutencao_gerada": (
            execucao.manutencao_gerada.titulo if execucao.manutencao_gerada_id else ""
        ),
        "manutencao_gerada_pk": execucao.manutencao_gerada_id,
        "acoes": _acoes_da_execucao(execucao, usuario),
    }


def _item_para_tela(item) -> dict:
    """Uma tarefa do checklist como a tela da ocorrência precisa dela."""
    return {
        "pk": item.pk,
        "descricao": item.descricao,
        "obrigatorio": item.obrigatorio,
        "situacao": item.get_status_display(),
        "situacao_valor": item.status,
        "situacao_tom": TOM_DO_STATUS_DO_ITEM.get(item.status, "secondary"),
        "pendente": not item.esta_tratado,
        "observacao": item.observacao,
        "tratado_por": _nome_de(item.concluido_por) if item.concluido_por_id else "",
        "tratado_em": _momento_por_extenso(item.concluido_em),
    }


def _execucao_para_detalhe(execucao) -> dict:
    """A ocorrência como a página dela precisa: o dia, o contexto e o desfecho."""
    dados = _execucao_para_tela(execucao)
    rotina = execucao.rotina
    dados.update(
        {
            "iniciada_em": _momento_por_extenso(execucao.iniciado_em),
            "prioridade": rotina.get_prioridade_display(),
            "descricao_rotina": rotina.descricao,
            "local": rotina.local.nome if rotina.local_id else "",
            "equipamento": rotina.equipamento.nome if rotina.equipamento_id else "",
            "fornecedor": (
                rotina.fornecedor.nome_razao_social if rotina.fornecedor_id else ""
            ),
        }
    )
    return dados


def _render_execucao(request, execucao, *, status=200):
    """Monta a tela da ocorrência com o checklist e as ações que restam nela."""
    progresso = execucao.progresso_do_checklist
    itens = [_item_para_tela(item) for item in execucao.itens.all()]
    rotina = execucao.rotina

    contexto = {
        "execucao": _execucao_para_detalhe(execucao),
        "itens": itens,
        "progresso": progresso,
        "tem_checklist": progresso["total"] > 0,
        "pode_tratar": pode_tratar_item(request.user, execucao),
        "formulario_item": TratamentoDeItemForm(auto_id="item_%s_%s"),
        "pode_iniciar": pode_iniciar(request.user, execucao),
        "pode_concluir": pode_concluir(request.user, execucao),
        "pode_ignorar": pode_ignorar(request.user, execucao),
        "pode_cancelar": pode_cancelar_execucao(request.user, execucao),
        "pode_demanda": (
            pode_gerar_demanda(request.user, execucao) and not execucao.demanda_gerada_id
        ),
        "pode_manutencao": (
            pode_gerar_manutencao(request.user, execucao)
            and not execucao.manutencao_gerada_id
        ),
        "formulario_conclusao": ConclusaoDeExecucaoForm(auto_id="concluir_%s"),
        "formulario_ignorar": JustificativaDaExecucaoForm(auto_id="ignorar_%s"),
        "formulario_cancelar": JustificativaDaExecucaoForm(auto_id="cancelar_%s"),
        "formulario_demanda": DemandaDaExecucaoForm(
            auto_id="demanda_%s",
            initial={"titulo": rotina.titulo, "descricao": rotina.descricao},
        ),
        "formulario_manutencao": ManutencaoDaExecucaoForm(
            auto_id="manutencao_%s",
            initial={
                "titulo": rotina.titulo,
                "descricao_problema": rotina.descricao,
                "data_prevista": execucao.data_prevista,
            },
        ),
    }
    return render(request, "pages/rotinas/execucao_detalhe.html", contexto, status=status)


# -- Tradução do checklist para a tela ------------------------------------------


def _iniciais_do_checklist(checklist) -> dict:
    """O que o formulário já deve trazer quando o modelo é aberto para edição."""
    return {"nome": checklist.nome, "descricao": checklist.descricao}


def _acoes_do_checklist(checklist, usuario) -> list[dict]:
    """As ações oferecidas na linha do checklist, já decididas pelo domínio."""
    if usuario is None:
        return []

    destino = reverse("rotinas:checklist", args=[checklist.pk])
    acoes = []
    if pode_editar_checklist(usuario, checklist):
        acoes.append(
            {
                "label": "Renomear",
                "icon": "bx-pencil",
                "url": f"{destino}?abrir=editar",
                "post": False,
            }
        )
    if pode_administrar_checklist(usuario, checklist):
        acoes.append(
            {
                "label": "Desativar" if checklist.ativo else "Ativar",
                "icon": "bx-block" if checklist.ativo else "bx-check-circle",
                "url": reverse("rotinas:checklist_alternar", args=[checklist.pk]),
                "post": True,
            }
        )
    return acoes


def _checklist_para_tela(checklist, usuario=None) -> dict:
    """O checklist como a linha da lista precisa dele."""
    return {
        "pk": checklist.pk,
        "nome": checklist.nome,
        "titulo": checklist.nome,
        "descricao": checklist.descricao,
        "condominio": checklist.condominio.nome,
        "ativo": checklist.ativo,
        "situacao": "Ativo" if checklist.ativo else "Inativo",
        "situacao_tom": "success" if checklist.ativo else "secondary",
        "total_de_itens": getattr(checklist, "itens_ativos", checklist.itens.count()),
        "rotinas": getattr(checklist, "rotinas_vinculadas", 0),
        "acoes": _acoes_do_checklist(checklist, usuario),
    }


def _checklist_para_detalhe(checklist) -> dict:
    """O modelo como a página dele precisa: o nome, a origem e a situação."""
    dados = _checklist_para_tela(checklist)
    dados.update(
        {
            "criado_em": _momento_por_extenso(checklist.criado_em),
            "criado_por": _nome_de(checklist.criado_por),
        }
    )
    return dados


def _acoes_do_item_do_modelo(item, usuario) -> list[dict]:
    """As ações de uma tarefa do modelo: editar e tirar de circulação."""
    if not pode_editar_item_do_checklist(usuario, item):
        return []

    destino = reverse("rotinas:checklist", args=[item.checklist_id])
    return [
        {
            "label": "Editar tarefa",
            "icon": "bx-pencil",
            "url": f"{destino}?abrir=item-{item.pk}",
            "post": False,
        },
        {
            "label": "Desativar" if item.ativo else "Ativar",
            "icon": "bx-block" if item.ativo else "bx-check-circle",
            "url": reverse(
                "rotinas:checklist_item_alternar", args=[item.checklist_id, item.pk]
            ),
            "post": True,
        },
    ]


def _item_do_modelo_para_tela(item, usuario) -> dict:
    """Uma tarefa do modelo como a lista da página do checklist precisa dela."""
    return {
        "pk": item.pk,
        "descricao": item.descricao,
        "titulo": item.descricao,
        "ordem": item.ordem,
        "obrigatorio": item.obrigatorio,
        "ativo": item.ativo,
        "situacao": "Ativa" if item.ativo else "Inativa",
        "situacao_tom": "success" if item.ativo else "secondary",
        "acoes": _acoes_do_item_do_modelo(item, usuario),
    }


def _rotinas_que_usam(checklist, usuario) -> list[dict]:
    """As rotinas que citam este checklist, as ativas primeiro."""
    rotinas = (
        rotinas_para_ver(usuario)
        .filter(checklist_modelo=checklist)
        .select_related("condominio", "responsavel")
        .order_by("-ativo", "titulo")
    )
    return [
        {
            "pk": rotina.pk,
            "titulo": rotina.titulo,
            "situacao": "Ativa" if rotina.ativo else "Inativa",
            "situacao_tom": "success" if rotina.ativo else "secondary",
        }
        for rotina in rotinas
    ]


def _render_checklists(request, condominios, *, formulario=None, modal=None, status=200):
    """Monta a lista de modelos de checklist com o formulário de novo."""
    if formulario is None:
        formulario = ChecklistForm(condominios=condominios)

    modelos = (
        checklists_para_ver(request.user)
        .filter(condominio__in=condominios)
        .select_related("condominio")
        .annotate(
            itens_ativos=Count("itens", filter=Q(itens__ativo=True), distinct=True),
            rotinas_vinculadas=Count(
                "rotinas", filter=Q(rotinas__ativo=True), distinct=True
            ),
        )
        .order_by("condominio__nome", "nome")
    )
    linhas = [_checklist_para_tela(modelo, request.user) for modelo in modelos]

    contexto = {
        "checklists": linhas,
        "total": len(linhas),
        "mostrar_condominio": condominios.count() > 1,
        "formulario_checklist": formulario,
        "modal_aberto": modal,
    }
    return render(request, "pages/rotinas/checklists.html", contexto, status=status)


def _render_checklist_detalhe(request, checklist, *, formulario=None, modal=None, status=200):
    """Monta a tela do modelo: as tarefas, na ordem, e as rotinas que o citam."""
    itens = [
        _item_do_modelo_para_tela(item, request.user) for item in checklist.itens.all()
    ]

    item_em_edicao = None
    if modal and modal.startswith("item-"):
        try:
            item_em_edicao = checklist.itens.filter(pk=modal[5:]).first()
        except (ValidationError, ValueError, TypeError):
            item_em_edicao = None
        if item_em_edicao is None:
            modal = None

    contexto = {
        "checklist": _checklist_para_detalhe(checklist),
        "itens": itens,
        "total_de_itens": len(itens),
        "rotinas_que_usam": _rotinas_que_usam(checklist, request.user),
        "pode_editar": pode_editar_checklist(request.user, checklist),
        "pode_administrar": pode_administrar_checklist(request.user, checklist),
        "formulario_checklist": formulario
        or ChecklistForm(
            condominio=checklist.condominio,
            initial=_iniciais_do_checklist(checklist),
        ),
        "formulario_item": ItemDoChecklistForm(),
        "item_em_edicao": item_em_edicao,
        "modal_aberto": modal,
    }
    return render(
        request, "pages/rotinas/checklist_detalhe.html", contexto, status=status
    )
