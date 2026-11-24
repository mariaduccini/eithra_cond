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
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import number_format
from django.views.decorators.http import require_POST

from apps.cadastros.models import Equipamento, Fornecedor, LocalOperacional
from apps.comunicacao.permissoes import pode_criar_comunicacao
from apps.core import audio
from apps.core.filtros import tem_filtros
from apps.core.paginacao import paginar_do_pedido
from apps.core.storage import ErroDeStorage
from apps.demandas.models import Demanda

from .atencao import _atencao_da_manutencao, _dias_de_antecedencia
from .constants import (
    ContextoEvidencia,
    FrequenciaUnidade,
    ResultadoCriterio,
    StatusConferencia,
    StatusManutencao,
)
from .evidencias import ErroDeEvidencia, acesso_temporario, adicionar_evidencia
from .forms import (
    ESCOLHA_DE_FREQUENCIA,
    RESULTADOS_DA_CONFERENCIA,
    AcaoDeStatusForm,
    CancelamentoDeManutencaoForm,
    ConferenciaDeManutencaoForm,
    ConclusaoDeManutencaoForm,
    EvidenciaDaExecucaoForm,
    ExecucaoDeManutencaoForm,
    FiltroDeManutencoesForm,
    ManutencaoForm,
    ModeloDeConferenciaForm,
    PlanoPreventivoForm,
    acoes_de_status,
    avaliacoes_da_conferencia_no_envio,
    criterios_do_modelo_no_envio,
    escolha_de_frequencia,
)
from .models import (
    EvidenciaManutencao,
    Manutencao,
    ModeloConferenciaPreventiva,
    PlanoPreventivo,
)
from .permissoes import (
    condominios_para_administrar_modelos,
    condominios_para_criar,
    condominios_para_ver,
    pode_administrar_manutencao,
    pode_anexar_evidencia,
    pode_cancelar_manutencao,
    pode_concluir,
    pode_conferir,
    pode_criar_manutencao,
    pode_editar_manutencao,
    pode_registrar_execucao,
    situacoes_para_mudar,
    usuarios_elegiveis_entre,
)
from .services import (
    ErroDeManutencao,
    acompanhamento_do_plano,
    alterar_status,
    aplicar_modelo_de_conferencia,
    aprovar_conferencia,
    cancelar_manutencao,
    concluir_manutencao,
    criar_manutencao,
    criar_modelo_conferencia,
    criar_plano_preventivo,
    definir_ativo,
    definir_ativo_do_modelo,
    editar_manutencao,
    editar_modelo_conferencia,
    editar_plano_preventivo,
    nao_avaliar_conferencia,
    registrar_execucao,
    reprovar_conferencia,
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

# O estado da conferência e o resultado de cada item são vocabulários próprios,
# separados da situação. O tom só ajuda a ler; a palavra é a do domínio.
TOM_POR_CONFERENCIA = {
    StatusConferencia.NAO_EXIGIDA: "secondary",
    StatusConferencia.PENDENTE: "warning",
    StatusConferencia.APROVADA: "success",
    StatusConferencia.REPROVADA: "danger",
}
TOM_POR_RESULTADO = {
    ResultadoCriterio.NAO_AVALIADO: "secondary",
    ResultadoCriterio.CONFORME: "success",
    ResultadoCriterio.NAO_CONFORME: "danger",
    ResultadoCriterio.NAO_SE_APLICA: "secondary",
}

# O que dizer depois de cada decisão da conferência, em uma linha.
RECADO_DA_CONFERENCIA = {
    "aprovar": "Conferência aprovada.",
    "reprovar": "Conferência reprovada. A correção ficou registrada.",
    "nao_avaliar": "Conferência registrada como não avaliada.",
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
    """Registra a ação que move a manutenção entre as que esta pessoa alcança."""
    manutencao = _manutencao_para_mudar_status(request, pk)
    permitidos = situacoes_para_mudar(request.user, manutencao)
    formulario = AcaoDeStatusForm(
        request.POST, permitidos=permitidos, manutencao=manutencao
    )
    acao = formulario.cleaned_data.get("acao") if formulario.is_valid() else ""
    if not formulario.is_valid() or not acao:
        if formulario.is_valid():
            formulario.add_error("acao", "Diga o que aconteceu com a manutenção.")
        return _render_detalhe(
            request, manutencao, formulario_status=formulario, modal="status", status=400
        )

    try:
        alterar_status(
            manutencao=manutencao,
            usuario=request.user,
            novo_status=acao,
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


# -- O ciclo da ocorrência ------------------------------------------------------
#
# Executar, anexar e conferir são passos da mesma ocorrência, e nenhum deles
# conclui a manutenção: a conclusão é decisão à parte, e o domínio a cobra. Cada
# passo tem uma porta própria, e todas escrevem pelo serviço oficial — a view só
# organiza o que a pessoa respondeu. A tela oferece a ação do estado em que a
# ocorrência está; quem decide de verdade é o domínio.


@login_required
@require_POST
def executar(request, pk):
    """Registra que o serviço foi executado, sem concluir a manutenção."""
    manutencao = _manutencao_para_registrar_execucao(request, pk)
    formulario = ExecucaoDeManutencaoForm(request.POST)
    if not formulario.is_valid():
        return _render_detalhe(
            request,
            manutencao,
            formulario_execucao=formulario,
            modal="execucao",
            status=400,
        )

    try:
        registrar_execucao(
            manutencao=manutencao,
            usuario=request.user,
            descricao_servico=formulario.cleaned_data["descricao_servico"],
            data_execucao=formulario.cleaned_data["data_execucao"],
        )
    except ErroDeManutencao as erro:
        messages.error(request, str(erro))
        return redirect("manutencoes:detalhe", pk=manutencao.pk)

    messages.success(request, "Execução registrada.")
    return redirect("manutencoes:detalhe", pk=manutencao.pk)


@login_required
@require_POST
def conferir(request, pk):
    """Decide a conferência de uma execução: aprovar, reprovar ou não avaliar."""
    manutencao = _manutencao_para_conferir(request, pk)
    formulario = ConferenciaDeManutencaoForm(request.POST)
    if not formulario.is_valid():
        return _render_detalhe(
            request,
            manutencao,
            formulario_conferencia=formulario,
            modal="conferir",
            status=400,
        )

    decisao = formulario.cleaned_data["decisao"]
    avaliacoes = avaliacoes_da_conferencia_no_envio(request.POST)
    try:
        if decisao == "aprovar":
            aprovar_conferencia(
                manutencao=manutencao,
                usuario=request.user,
                avaliacoes=avaliacoes,
                observacao=formulario.cleaned_data["observacao"],
            )
        elif decisao == "reprovar":
            reprovar_conferencia(
                manutencao=manutencao,
                usuario=request.user,
                motivo=formulario.cleaned_data["motivo"],
                criterios_nao_conformes=[
                    avaliacao
                    for avaliacao in avaliacoes
                    if avaliacao["resultado"] == ResultadoCriterio.NAO_CONFORME
                ],
            )
        else:
            nao_avaliar_conferencia(
                manutencao=manutencao,
                usuario=request.user,
                observacao=formulario.cleaned_data["observacao"],
            )
    except ErroDeManutencao as erro:
        messages.error(request, str(erro))
        return redirect("manutencoes:detalhe", pk=manutencao.pk)

    messages.success(request, RECADO_DA_CONFERENCIA[decisao])
    return redirect("manutencoes:detalhe", pk=manutencao.pk)


@login_required
@require_POST
def anexar_evidencia(request, pk):
    """Anexa provas à ocorrência sem mexer na situação nem na conferência.

    É a mesma evidência da execução, por outra porta: quem só quer guardar a
    prova não precisa registrar execução para isso. Cada arquivo entra pelo
    serviço oficial, que o confere, guarda no bucket privado e anuncia no
    histórico; anexar não conclui, não aprova e não reprova nada.
    """
    manutencao = _manutencao_para_anexar_evidencia(request, pk)
    formulario = EvidenciaDaExecucaoForm(request.POST, request.FILES)
    if not formulario.is_valid():
        return _render_detalhe(
            request,
            manutencao,
            formulario_evidencia=formulario,
            modal="evidencia",
            status=400,
        )

    arquivos = formulario.arquivos_enviados()
    try:
        for arquivo in arquivos:
            adicionar_evidencia(
                manutencao=manutencao,
                arquivo=arquivo,
                usuario=request.user,
                contexto=ContextoEvidencia.EXECUCAO,
            )
    except (ErroDeEvidencia, ErroDeStorage) as erro:
        formulario.add_error("evidencias", str(erro))
        return _render_detalhe(
            request,
            manutencao,
            formulario_evidencia=formulario,
            modal="evidencia",
            status=400,
        )

    recado = "Evidência anexada." if len(arquivos) == 1 else "Evidências anexadas."
    messages.success(request, recado)
    return redirect("manutencoes:detalhe", pk=manutencao.pk)


@login_required
def evidencia_arquivo(request, pk, evidencia_id):
    """Entrega o arquivo de uma evidência a quem pode acompanhar a manutenção.

    A autorização é a da própria manutenção: quem não a acompanha não descobre
    que a evidência existe — a resposta é 404, e não uma negativa que
    confirmaria o arquivo. O endereço devolvido é temporário, gerado na hora:
    nunca fica guardado nem circula.
    """
    manutencao = _manutencao_autorizada(request, pk)
    evidencia = get_object_or_404(
        EvidenciaManutencao, pk=evidencia_id, manutencao=manutencao
    )
    try:
        destino = acesso_temporario(evidencia)
    except (ErroDeStorage, ImproperlyConfigured) as erro:
        raise Http404("O arquivo desta evidência não está disponível.") from erro
    return redirect(destino)


# -- Planos preventivos ---------------------------------------------------------
#
# A preventiva que volta sempre é uma regra, e a tela de cadastro pergunta só o
# que a gestão decide: o que fazer, onde, com quem, de quanto em quanto e quando
# volta. Quem acompanha e a conferência não são perguntas: o domínio as fixa ao
# gravar, a partir do vínculo de administração do condomínio. Não existe campo
# para nenhuma das duas, e é por isso que ninguém as escolhe por engano.


@login_required
def planos(request):
    """As preventivas que voltam sempre, nos condomínios que esta pessoa acompanha."""
    condominios = _condominios_para_ver(request)
    if request.GET.get("abrir") == "editar":
        plano = _plano_pedido_para_editar(request)
        if plano is not None:
            return _render_planos(request, condominios, plano=plano, modal="editar")
    return _render_planos(request, condominios)


@login_required
def criar_plano(request):
    """Registra a regra de uma preventiva. Quem acompanha e a conferência vêm do domínio."""
    if request.method != "POST":
        return redirect("manutencoes:planos")

    condominios = condominios_para_criar(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomínio disponível para registrar plano preventivo.")

    formulario = PlanoPreventivoForm(request.POST, condominios=condominios)
    if not formulario.is_valid():
        return _render_planos(
            request,
            _condominios_para_ver(request),
            formulario=formulario,
            modal="criar",
            status=400,
        )

    dados = formulario.cleaned_data
    try:
        with transaction.atomic():
            plano = criar_plano_preventivo(
                condominio=dados["condominio"],
                criado_por=request.user,
                titulo=dados["titulo"],
                local=dados["local"],
                equipamento=dados["equipamento"],
                fornecedor=dados["fornecedor"],
                frequencia_unidade=dados["frequencia_unidade"],
                frequencia_intervalo=dados["frequencia_intervalo"],
                proxima_data_prevista=dados["proxima_data_prevista"],
            )
            # O modelo é aplicado pelo serviço oficial: os critérios são
            # copiados por valor para o plano, e nada liga os dois depois.
            _aplicar_modelo_escolhido(
                plano=plano, dados=dados, usuario=request.user
            )
    except ErroDeManutencao as erro:
        messages.error(request, str(erro))
        return _render_planos(
            request,
            _condominios_para_ver(request),
            formulario=formulario,
            modal="criar",
            status=400,
        )

    messages.success(request, "Plano preventivo registrado.")
    return redirect("manutencoes:planos")


@login_required
@require_POST
def editar_plano(request, pk):
    """Atualiza a regra da preventiva. Só quem responde pelo condomínio edita."""
    plano = _plano_para_editar(request, pk)
    formulario = PlanoPreventivoForm(
        request.POST, condominio=plano.condominio, plano=plano
    )
    if not formulario.is_valid():
        return _render_planos(
            request,
            _condominios_para_ver(request),
            formulario=formulario,
            plano=plano,
            modal="editar",
            status=400,
        )

    dados = formulario.cleaned_data
    try:
        with transaction.atomic():
            editar_plano_preventivo(
                plano=plano,
                titulo=dados["titulo"],
                local=dados["local"],
                equipamento=dados["equipamento"],
                fornecedor=dados["fornecedor"],
                frequencia_unidade=dados["frequencia_unidade"],
                frequencia_intervalo=dados["frequencia_intervalo"],
                proxima_data_prevista=dados["proxima_data_prevista"],
            )
            # Trocar a lista do plano nunca é silencioso: quando já há itens, o
            # serviço só substitui com a confirmação explícita da tela.
            _aplicar_modelo_escolhido(
                plano=plano, dados=dados, usuario=request.user
            )
    except ErroDeManutencao as erro:
        messages.error(request, str(erro))
        return _render_planos(
            request,
            _condominios_para_ver(request),
            formulario=formulario,
            plano=plano,
            modal="editar",
            status=400,
        )

    messages.success(request, "Plano preventivo atualizado.")
    return redirect("manutencoes:planos")


def _aplicar_modelo_escolhido(*, plano, dados, usuario) -> None:
    """Aplica ao plano o modelo escolhido na tela, quando houve escolha.

    A decisão de substituir é da tela, e chega como confirmação explícita: sem
    ela, o serviço recusa trocar uma lista que já existe. Nada de critérios é
    copiado aqui — quem copia é o serviço.
    """
    modelo = dados.get("modelo_de_conferencia")
    if modelo is None or not dados.get("usar_modelo_de_conferencia"):
        return
    aplicar_modelo_de_conferencia(
        plano=plano,
        modelo=modelo,
        usuario=usuario,
        substituir=bool(dados.get("substituir_conferencia")),
    )


# -- Modelos de conferência -----------------------------------------------------
#
# A biblioteca de modelos reutilizáveis vive dentro das preventivas, e é
# administrada pela Administradora do condomínio — a mesma autorização dos
# modelos, e nunca ``is_staff``. Quem não administra não enxerga a tela: a
# resposta é 404, para não anunciar o que não é dela.


@login_required
def modelos_de_conferencia(request):
    """A biblioteca de modelos de conferência dos condomínios desta Administradora."""
    condominios = _condominios_para_administrar_modelos(request)
    if request.GET.get("abrir") == "editar":
        modelo = _modelo_pedido_para_editar(request, condominios)
        if modelo is not None:
            return _render_modelos(request, condominios, modelo=modelo, modal="editar")
    return _render_modelos(request, condominios)


@login_required
def novo_modelo(request):
    """Registra um modelo reutilizável de conferência. Só a Administradora."""
    if request.method != "POST":
        return redirect("manutencoes:modelos_conferencia")

    condominios = condominios_para_administrar_modelos(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomínio disponível para administrar modelos.")

    formulario = ModeloDeConferenciaForm(request.POST, condominios=condominios)
    if not formulario.is_valid():
        return _render_modelos(
            request,
            _condominios_para_administrar_modelos(request),
            formulario=formulario,
            modal="criar",
            status=400,
        )

    dados = formulario.cleaned_data
    try:
        criar_modelo_conferencia(
            condominio=dados["condominio"],
            criado_por=request.user,
            nome=dados["nome"],
            descricao=dados["descricao"],
            criterios=criterios_do_modelo_no_envio(request.POST),
        )
    except ErroDeManutencao as erro:
        messages.error(request, str(erro))
        return _render_modelos(
            request,
            _condominios_para_administrar_modelos(request),
            formulario=formulario,
            modal="criar",
            status=400,
        )

    messages.success(request, "Modelo de conferência registrado.")
    return redirect("manutencoes:modelos_conferencia")


@login_required
@require_POST
def editar_modelo(request, pk):
    """Atualiza o roteiro reutilizável, sem alcançar o que já foi copiado dele."""
    modelo = _modelo_autorizado(request, pk)
    formulario = ModeloDeConferenciaForm(
        request.POST, condominio=modelo.condominio, modelo=modelo
    )
    if not formulario.is_valid():
        return _render_modelos(
            request,
            _condominios_para_administrar_modelos(request),
            formulario=formulario,
            modelo=modelo,
            modal="editar",
            status=400,
        )

    dados = formulario.cleaned_data
    try:
        editar_modelo_conferencia(
            modelo=modelo,
            usuario=request.user,
            nome=dados["nome"],
            descricao=dados["descricao"],
            criterios=criterios_do_modelo_no_envio(request.POST),
        )
    except ErroDeManutencao as erro:
        messages.error(request, str(erro))
        return _render_modelos(
            request,
            _condominios_para_administrar_modelos(request),
            formulario=formulario,
            modelo=modelo,
            modal="editar",
            status=400,
        )

    messages.success(request, "Modelo de conferência atualizado.")
    return redirect("manutencoes:modelos_conferencia")


@login_required
@require_POST
def alternar_modelo(request, pk):
    """Retira o modelo das próximas aplicações, ou o devolve, sem apagar nada."""
    modelo = _modelo_autorizado(request, pk)
    novo = not modelo.ativo
    definir_ativo_do_modelo(modelo=modelo, usuario=request.user, ativo=novo)
    if novo:
        messages.success(request, "Modelo de conferência reativado.")
    else:
        messages.success(request, "Modelo de conferência desativado.")
    return redirect("manutencoes:modelos_conferencia")


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


def _pode_conferir_agora(usuario, manutencao) -> bool:
    """Diz se a conferência pode ser decidida neste momento.

    Quem confere é decidido pelo domínio; quando, pelo estado da ocorrência. Uma
    ocorrência nasce com a conferência pendente mesmo antes de o serviço ser
    executado, então pendente sozinho não basta: a conferência pressupõe uma
    execução registrada, porque é ela que se olha. ``data_execucao`` é o mesmo
    sinal que o domínio usa para recusar — sem ele não há entrega para conferir.
    Depois de aprovada o caminho é concluir, e depois de reprovada é registrar a
    nova execução. Esta é a leitura compartilhada pela tela e pela porta da
    view; a recusa de verdade continua sendo do serviço.
    """
    return (
        pode_conferir(usuario, manutencao)
        and manutencao.data_execucao is not None
        and manutencao.status_conferencia == StatusConferencia.PENDENTE
    )


def _manutencao_para_registrar_execucao(request, pk):
    """Resolve a manutenção e exige, no backend, o direito de registrar execução."""
    manutencao = _manutencao_autorizada(request, pk)
    if not pode_registrar_execucao(request.user, manutencao):
        raise PermissionDenied(
            "Esta pessoa não pode registrar execução desta manutenção."
        )
    return manutencao


def _manutencao_para_conferir(request, pk):
    """Resolve a manutenção e exige, no backend, o direito de conferi-la agora.

    A mesma leitura que a tela usa governa o acesso direto: quem não pode
    conferir neste momento — por não ter o direito, por não haver execução
    registrada ou por a conferência já estar decidida — é recusado aqui, sem
    depender de a tela ter escondido a ação.
    """
    manutencao = _manutencao_autorizada(request, pk)
    if not _pode_conferir_agora(request.user, manutencao):
        raise PermissionDenied(
            "Esta pessoa não pode conferir esta manutenção agora."
        )
    return manutencao


def _manutencao_para_anexar_evidencia(request, pk):
    """Resolve a manutenção e exige, no backend, o direito de anexar prova."""
    manutencao = _manutencao_autorizada(request, pk)
    if not pode_anexar_evidencia(request.user, manutencao):
        raise PermissionDenied(
            "Esta pessoa não pode anexar evidência a esta manutenção."
        )
    return manutencao


def _plano_autorizado(request, pk):
    """Resolve o plano dentro dos condomínios que esta pessoa acompanha."""
    condominios = _condominios_para_ver(request)
    return _obter_ou_404(
        PlanoPreventivo.objects.select_related(
            "condominio", "responsavel", "local", "equipamento", "fornecedor"
        ).filter(condominio__in=condominios),
        pk,
    )


def _pode_editar_plano(usuario, plano) -> bool:
    """Diz se a pessoa responde pelo condomínio deste plano."""
    if plano is None or not getattr(plano, "pk", None):
        return False
    return condominios_para_criar(usuario).filter(pk=plano.condominio_id).exists()


def _plano_para_editar(request, pk):
    """Resolve o plano e exige, no backend, o direito de editar a regra."""
    plano = _plano_autorizado(request, pk)
    if not _pode_editar_plano(request.user, plano):
        raise PermissionDenied("Esta pessoa não responde por este condomínio.")
    return plano


def _plano_pedido_para_editar(request):
    """O plano que a tela pediu para editar, quando esta pessoa pode editá-lo.

    Serve à abertura do formulário por endereço. O que a pessoa não pode editar
    simplesmente não abre — a lista aparece sem o modal, em vez de recusar a
    tela inteira por causa de um parâmetro de endereço.
    """
    pk = request.GET.get("item")
    if not pk or not _eh_uuid(pk):
        return None
    plano = _plano_autorizado(request, pk)
    return plano if _pode_editar_plano(request.user, plano) else None


def _condominios_para_administrar_modelos(request):
    """Condomínios cujos modelos esta pessoa administra, ou 404.

    Só a Administradora chega aqui. Quem não administra nenhum condomínio não é
    avisado de que a biblioteca existe: a resposta é 404.
    """
    condominios = condominios_para_administrar_modelos(request.user)
    if not condominios.exists():
        raise Http404("Nenhum modelo de conferência disponível.")
    return condominios


def _modelo_autorizado(request, pk):
    """Resolve o modelo dentro dos condomínios que esta pessoa administra."""
    condominios = _condominios_para_administrar_modelos(request)
    return _obter_ou_404(
        ModeloConferenciaPreventiva.objects.select_related("condominio").filter(
            condominio__in=condominios
        ),
        pk,
    )


def _modelo_pedido_para_editar(request, condominios):
    """O modelo que a tela pediu para editar, quando esta pessoa pode editá-lo.

    Serve à abertura do formulário por endereço. O que a pessoa não pode editar
    simplesmente não abre — a lista aparece sem o modal, em vez de recusar a tela
    inteira por causa de um parâmetro de endereço.
    """
    pk = request.GET.get("item")
    if not pk or not _eh_uuid(pk):
        return None
    return _obter_ou_404(
        ModeloConferenciaPreventiva.objects.select_related("condominio").filter(
            condominio__in=condominios
        ),
        pk,
    )


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
    pagina_manutencoes = paginar_do_pedido(
        _manutencoes_filtradas(request, condominios, filtros), request.GET
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
        # Ditar é escrever: o microfone só se oferece onde a pessoa poderia
        # registrar manutenção — a mesma permissão que o serviço de transcrição
        # confere no backend. O botão nunca leva a um lugar sem saída.
        "voz_disponivel": audio.disponivel() and pode_criar,
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
    formulario_execucao=None,
    formulario_conferencia=None,
    formulario_evidencia=None,
    modal=None,
    status=200,
):
    """Monta a tela da manutenção com o que esta pessoa pode fazer com ela."""
    permitidos = situacoes_para_mudar(request.user, manutencao)
    acoes = acoes_de_status(permitidos, manutencao)
    pode_editar = pode_editar_manutencao(request.user, manutencao)
    pode_concluir_agora = pode_concluir(request.user, manutencao)
    pode_cancelar_agora = pode_cancelar_manutencao(request.user, manutencao)
    pode_executar_agora = pode_registrar_execucao(request.user, manutencao)
    pode_conferir_agora = _pode_conferir_agora(request.user, manutencao)
    pode_anexar_agora = pode_anexar_evidencia(request.user, manutencao)

    if (
        (modal == "editar" and not pode_editar)
        or (modal == "status" and not acoes)
        or (modal == "concluir" and not pode_concluir_agora)
        or (modal == "cancelar" and not pode_cancelar_agora)
        or (modal == "execucao" and not pode_executar_agora)
        or (modal == "conferir" and not pode_conferir_agora)
        or (modal == "evidencia" and not pode_anexar_agora)
    ):
        modal = None

    contexto = {
        "manutencao": _manutencao_para_detalhe(manutencao),
        "historico": _historico_para_tela(manutencao),
        "pode_editar": pode_editar,
        "pode_alterar_status": bool(acoes),
        "pode_concluir": pode_concluir_agora,
        "pode_cancelar": pode_cancelar_agora,
        "pode_administrar": pode_administrar_manutencao(request.user, manutencao),
        "pode_registrar_execucao": pode_executar_agora,
        "pode_conferir": pode_conferir_agora,
        "pode_anexar_evidencia": pode_anexar_agora,
        # Comunicar aos moradores é uma capacidade transversal: a manutenção é a
        # origem do aviso, e quem responde pelo condomínio é quem o escreve.
        "pode_gerar_comunicado": pode_criar_comunicacao(
            request.user, manutencao.condominio
        ),
        "formulario_manutencao": formulario
        or ManutencaoForm(
            condominio=manutencao.condominio,
            manutencao=manutencao,
            initial=_iniciais_da_manutencao(manutencao),
        ),
        "formulario_status": formulario_status
        or AcaoDeStatusForm(permitidos=permitidos, manutencao=manutencao),
        "formulario_conclusao": formulario_conclusao or ConclusaoDeManutencaoForm(),
        "formulario_cancelamento": formulario_cancelamento
        or CancelamentoDeManutencaoForm(),
        "formulario_execucao": formulario_execucao or ExecucaoDeManutencaoForm(),
        "formulario_conferencia": formulario_conferencia
        or ConferenciaDeManutencaoForm(),
        "formulario_evidencia": formulario_evidencia or EvidenciaDaExecucaoForm(),
        "resultados_da_conferencia": RESULTADOS_DA_CONFERENCIA,
        "modal_aberto": modal,
        # Ditar é escrever: o microfone aparece onde a pessoa pode escrever o
        # texto livre desta manutenção — quem a registra neste condomínio ou
        # quem executa o serviço sob sua responsabilidade. É a mesma permissão
        # que o serviço de transcrição confere no backend, para o botão nunca
        # dar em nada.
        "voz_disponivel": audio.disponivel()
        and (
            pode_criar_manutencao(request.user, manutencao.condominio)
            or pode_executar_agora
        ),
    }
    return render(request, "pages/manutencoes/detalhe.html", contexto, status=status)


def _render_planos(
    request, condominios, *, formulario=None, plano=None, modal=None, status=200
):
    """Monta a tela dos planos com o formulário que estiver em uso.

    Os planos da lista vêm dos condomínios que esta pessoa acompanha. O
    formulário — de criação ou de edição — é único na tela, e é ele que decide
    para qual condomínio o vínculo de administração será lido.
    """
    consulta = (
        PlanoPreventivo.objects.filter(condominio__in=condominios)
        .select_related(
            "condominio", "responsavel", "local", "equipamento", "fornecedor"
        )
        .prefetch_related("criterios_conferencia")
        .order_by("proxima_data_prevista", "titulo")
    )
    pagina_planos = paginar_do_pedido(consulta, request.GET)
    condominios_criaveis = condominios_para_criar(request.user)
    pode_criar = condominios_criaveis.exists()

    formulario_criacao = None
    formulario_edicao = None
    if modal == "editar" and plano is not None:
        formulario_edicao = formulario or PlanoPreventivoForm(
            condominio=plano.condominio,
            plano=plano,
            initial=_iniciais_do_plano(plano),
        )
    elif formulario is not None:
        formulario_criacao = formulario
    elif pode_criar:
        formulario_criacao = PlanoPreventivoForm(condominios=condominios_criaveis)

    condominio_do_formulario = getattr(
        formulario_edicao or formulario_criacao, "condominio_efetivo", None
    )
    acompanhamento, erro_acompanhamento = _acompanhamento_para_tela(
        condominio_do_formulario
    )

    contexto = {
        "planos": [_plano_para_tela(item, request.user) for item in pagina_planos],
        "pagina_planos": pagina_planos,
        "total": pagina_planos.paginator.count,
        "pode_criar": pode_criar,
        "formulario_criacao": formulario_criacao,
        "formulario_edicao": formulario_edicao,
        "plano_em_edicao": plano,
        "modal_aberto": modal,
        "mostrar_condominio": condominios.count() > 1,
        "acompanhamento": acompanhamento,
        "erro_acompanhamento": erro_acompanhamento,
        "pode_administrar_modelos": condominios_para_administrar_modelos(
            request.user
        ).exists(),
        "modelos_disponiveis": _modelos_do_condominio(condominio_do_formulario),
        "modelos_previa": _previa_dos_modelos(condominio_do_formulario),
        "criterios_do_plano": _criterios_do_plano_para_tela(plano),
    }
    return render(request, "pages/manutencoes/planos.html", contexto, status=status)


def _modelos_do_condominio(condominio):
    """Os modelos ativos que podem ser aplicados a um plano deste condomínio."""
    if condominio is None:
        return ModeloConferenciaPreventiva.objects.none()
    return (
        ModeloConferenciaPreventiva.objects.ativos()
        .do_condominio(condominio)
        .prefetch_related("criterios")
    )


def _previa_dos_modelos(condominio) -> list[dict]:
    """Os critérios de cada modelo ativo, como a tela os mostra em prévia.

    É o mesmo conteúdo que será copiado ao aplicar: a prévia não inventa nada
    além do que o modelo já tem.
    """
    return [
        {
            "id": str(modelo.pk),
            "nome": modelo.nome,
            "criterios": [
                {"descricao": criterio.descricao, "obrigatorio": criterio.obrigatorio}
                for criterio in modelo.criterios.all()
            ],
        }
        for modelo in _modelos_do_condominio(condominio)
    ]


def _criterios_do_plano_para_tela(plano) -> list[dict]:
    """Os critérios que o plano já tem, quando há plano em edição."""
    if plano is None:
        return []
    return [
        {"descricao": criterio.descricao, "obrigatorio": criterio.obrigatorio}
        for criterio in plano.criterios_conferencia.all()
    ]


def _render_modelos(
    request, condominios, *, formulario=None, modelo=None, modal=None, status=200
):
    """Monta a biblioteca de modelos com o formulário que estiver em uso.

    Os modelos vêm dos condomínios que esta Administradora responde. O
    formulário — de criação ou de edição — é único na tela, e é ele que decide
    para qual condomínio o vínculo de administração será lido.
    """
    consulta = (
        ModeloConferenciaPreventiva.objects.filter(condominio__in=condominios)
        .select_related("condominio")
        .prefetch_related("criterios")
        .order_by("condominio__nome", "nome")
    )
    modelos = list(consulta)

    formulario_criacao = None
    formulario_edicao = None
    if modal == "editar" and modelo is not None:
        formulario_edicao = formulario or ModeloDeConferenciaForm(
            condominio=modelo.condominio,
            modelo=modelo,
            initial=_iniciais_do_modelo(modelo),
        )
    elif formulario is not None:
        formulario_criacao = formulario
    else:
        formulario_criacao = ModeloDeConferenciaForm(condominios=condominios)

    contexto = {
        "modelos": [_modelo_para_tela(item) for item in modelos],
        "total": len(modelos),
        "formulario_criacao": formulario_criacao,
        "formulario_edicao": formulario_edicao,
        "modelo_em_edicao": modelo,
        "criterios_do_modelo": _criterios_para_tela(modelo),
        "modal_aberto": modal,
        "mostrar_condominio": condominios.count() > 1,
    }
    return render(
        request, "pages/manutencoes/modelos_conferencia.html", contexto, status=status
    )


def _criterios_para_tela(modelo) -> list[dict]:
    """Os critérios do modelo como as linhas do editor precisam deles."""
    if modelo is None:
        return []
    return [
        {"descricao": criterio.descricao, "obrigatorio": criterio.obrigatorio}
        for criterio in modelo.criterios.all()
    ]


def _iniciais_do_modelo(modelo) -> dict:
    """O que o formulário já deve trazer quando o modelo é aberto para edição."""
    return {
        "nome": modelo.nome,
        "descricao": modelo.descricao,
    }


def _modelo_para_tela(modelo) -> dict:
    """O modelo como a linha da biblioteca precisa dele."""
    return {
        "pk": modelo.pk,
        "nome": modelo.nome,
        "descricao": modelo.descricao,
        "condominio": modelo.condominio.nome,
        "criterios": len(modelo.criterios.all()),
        "ativo": modelo.ativo,
        "abrir_edicao": (
            f"{reverse('manutencoes:modelos_conferencia')}"
            f"?abrir=editar&item={modelo.pk}"
        ),
    }


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


def _referencia_da_manutencao(manutencao) -> str:
    """O número curto pelo qual a ocorrência é citada na tela.

    Não é um protocolo guardado — é a leitura curta do próprio identificador,
    derivada e nunca persistida. Serve para a gestão citar a ocorrência sem
    decorar o identificador inteiro.
    """
    return str(manutencao.pk).split("-")[0].upper()


def _evidencias_para_tela(manutencao) -> list[dict]:
    """As provas anexadas, como a lista compacta precisa delas.

    Tipo, nome e data bastam para reconhecer o arquivo; o endereço é o da
    própria evidência, que gera um acesso curto na hora de abrir. Nada aqui
    afirma nada sobre a conferência: a evidência é prova, não decisão.
    """
    return [
        {
            "pk": evidencia.pk,
            "tipo": evidencia.get_tipo_midia_display(),
            "nome": evidencia.nome_original or "Arquivo",
            "momento": _momento_por_extenso(evidencia.enviada_em),
            "enviada_por": _nome_de(evidencia.enviada_por),
            "url": reverse(
                "manutencoes:evidencia_arquivo", args=[manutencao.pk, evidencia.pk]
            ),
        }
        for evidencia in manutencao.evidencias.select_related("enviada_por").order_by(
            "enviada_em"
        )
    ]


def _criterios_da_ocorrencia_para_tela(manutencao) -> list[dict]:
    """Os itens observáveis da ocorrência, como a conferência os lê.

    A lista é o retrato copiado do plano quando a ocorrência nasceu — nunca
    uma busca ao plano de agora. Cada item traz o que já foi respondido, se for
    o caso, para a tela reabrir a conferência com o que ficou registrado.
    """
    return [
        {
            "pk": criterio.pk,
            "descricao": criterio.descricao,
            "obrigatorio": criterio.obrigatorio,
            "resultado": criterio.resultado,
            "resultado_rotulo": criterio.get_resultado_display(),
            "resultado_tom": TOM_POR_RESULTADO.get(criterio.resultado, "secondary"),
            "observacao": criterio.observacao,
            "nao_conforme": criterio.resultado == ResultadoCriterio.NAO_CONFORME,
            # Ninguém respondeu ainda quando o item continua no ponto de partida;
            # a tela usa isto para não escolher uma resposta por conta própria.
            "respondido": criterio.resultado != ResultadoCriterio.NAO_AVALIADO,
        }
        for criterio in manutencao.criterios_conferencia.all()
    ]


def _atencao_para_tela(manutencao):
    """A atenção derivada da ocorrência, ou nada quando não há o que sinalizar.

    A atenção não é guardada: é lida do estado real, pela mesma autoridade do
    resto do sistema. Manutenção encerrada ou fora da operação não pede atenção
    — do mesmo modo que a leitura de conjunto só devolve o que está aberto.
    """
    if manutencao.esta_encerrada or not manutencao.ativo:
        return None

    atencao = _atencao_da_manutencao(
        manutencao,
        hoje=timezone.localdate(),
        dias_antecedencia=_dias_de_antecedencia(None),
    )
    if atencao is None:
        return None
    return {
        "titulo": atencao.titulo,
        "mensagem": atencao.mensagem,
        "acao": atencao.acao_sugerida,
    }


def _manutencao_para_detalhe(manutencao) -> dict:
    """A manutenção como a página dela precisa: os dados e o que dá para fazer."""
    dados = _manutencao_para_tela(manutencao)
    dados.update(
        {
            "referencia": _referencia_da_manutencao(manutencao),
            "problema": manutencao.descricao_problema,
            "servico": manutencao.descricao_servico,
            "fornecedor": (
                manutencao.fornecedor.nome_razao_social if manutencao.fornecedor else ""
            ),
            "custo_previsto": _dinheiro(manutencao.custo_previsto),
            "custo_real": _dinheiro(manutencao.custo_real),
            "inicio": _momento_por_extenso(manutencao.data_inicio),
            "conclusao": _momento_por_extenso(manutencao.data_conclusao),
            "execucao": _momento_por_extenso(manutencao.data_execucao),
            "tem_execucao": manutencao.data_execucao is not None,
            "criada_em": _momento_por_extenso(manutencao.criado_em),
            "criada_por": _nome_de(manutencao.criado_por),
            "demanda_origem": (
                manutencao.demanda_origem.titulo if manutencao.demanda_origem_id else ""
            ),
            "demanda_origem_pk": manutencao.demanda_origem_id,
            "ativo": manutencao.ativo,
            # A conferência é um vocabulário à parte da situação, e a tela lê as
            # três palavras do estado para dizer onde ela está e o que vem agora.
            "exige_conferencia": manutencao.exige_conferencia,
            "conferencia": manutencao.get_status_conferencia_display(),
            "conferencia_valor": manutencao.status_conferencia,
            "conferencia_tom": TOM_POR_CONFERENCIA.get(
                manutencao.status_conferencia, "secondary"
            ),
            "conferencia_pendente": (
                manutencao.status_conferencia == StatusConferencia.PENDENTE
            ),
            "conferencia_aprovada": (
                manutencao.status_conferencia == StatusConferencia.APROVADA
            ),
            "conferencia_reprovada": (
                manutencao.status_conferencia == StatusConferencia.REPROVADA
            ),
            "criterios": _criterios_da_ocorrencia_para_tela(manutencao),
            "evidencias": _evidencias_para_tela(manutencao),
            "atencao": _atencao_para_tela(manutencao),
        }
    )
    dados["tem_criterios"] = bool(dados["criterios"])
    dados["nao_conformes"] = [
        criterio["descricao"] for criterio in dados["criterios"] if criterio["nao_conforme"]
    ]
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


# As respostas prontas de frequência, como a tela as mostra. "Personalizar" não
# entra: ele não é uma regra, é o caminho para dizer o número e a unidade.
ROTULO_DE_FREQUENCIA = dict(ESCOLHA_DE_FREQUENCIA)


def _frequencia_por_extenso(plano) -> str:
    """A frequência do plano na linguagem de quem cuida do condomínio."""
    escolha = escolha_de_frequencia(plano.frequencia_unidade, plano.frequencia_intervalo)
    if escolha != "personalizar":
        return ROTULO_DE_FREQUENCIA[escolha]
    unidade = FrequenciaUnidade(plano.frequencia_unidade).label.lower()
    return f"A cada {plano.frequencia_intervalo} {unidade}"


def _acompanhamento_para_tela(condominio):
    """Quem acompanha o plano, para a tela mostrar — ou o motivo de não haver.

    A resposta é a mesma que o cadastro vai gravar: a Administradora do
    condomínio, lida do vínculo. Quando o domínio não consegue responder sem
    adivinhar, a tela recebe o motivo em vez de uma pessoa inventada.
    """
    if condominio is None:
        return None, ""
    try:
        return acompanhamento_do_plano(condominio), ""
    except ErroDeManutencao as erro:
        return None, str(erro)


def _iniciais_do_plano(plano) -> dict:
    """O que o formulário já deve trazer quando o plano é aberto para edição.

    A frequência volta à pergunta que a tela faz: uma regra guardada como
    "todo mês" reaparece como "Todo mês", e uma regra sem resposta pronta volta
    como "Personalizar" com o número e a unidade já preenchidos.
    """
    return {
        "titulo": plano.titulo,
        "local": plano.local_id,
        "equipamento": plano.equipamento_id,
        "fornecedor": plano.fornecedor_id,
        "frequencia": escolha_de_frequencia(
            plano.frequencia_unidade, plano.frequencia_intervalo
        ),
        "frequencia_intervalo_personalizado": plano.frequencia_intervalo,
        "frequencia_unidade_personalizada": plano.frequencia_unidade,
        "proxima_data_prevista": plano.proxima_data_prevista,
    }


def _plano_para_tela(plano, usuario=None) -> dict:
    """O plano como a linha da lista precisa dele."""
    return {
        "pk": plano.pk,
        "titulo": plano.titulo,
        "condominio": plano.condominio.nome,
        "local": plano.local.nome if plano.local else "",
        "equipamento": plano.equipamento.nome if plano.equipamento else "",
        "fornecedor": plano.fornecedor.nome_razao_social if plano.fornecedor else "",
        "responsavel": _nome_de(plano.responsavel),
        "frequencia": _frequencia_por_extenso(plano),
        "proxima": _data_por_extenso(plano.proxima_data_prevista),
        "ativo": plano.ativo,
        "exige_conferencia": plano.exige_conferencia,
        # A conferência é sempre exigida; o que pode faltar é o que conferir.
        # Esta é uma leitura derivada do próprio plano, sem novo estado.
        "tem_criterios_conferencia": bool(plano.criterios_conferencia.all()),
        "pode_editar": bool(usuario) and _pode_editar_plano(usuario, plano),
        "abrir_edicao": f"{reverse('manutencoes:planos')}?abrir=editar&item={plano.pk}",
    }
