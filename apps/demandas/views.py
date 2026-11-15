"""Telas de demandas operacionais.

O domínio já sabe criar, editar, mover situação, observar e relacionar
manutenção. Esta camada organiza isso na interface aprovada: tabela
operacional, wizard curto de registro, detalhe com abas e atualização sem
reinventar o backend.
"""
import uuid

from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.comunicacao.permissoes import pode_criar_comunicacao
from apps.core import audio
from apps.core.filtros import tem_filtros
from apps.core.paginacao import paginar_do_pedido
from apps.core.storage import ErroDeStorage
from apps.manutencoes.permissoes import pode_criar_manutencao

from . import movimentacao, prazos
from .constants import (
    AguardandoTipo,
    ImpactoDemanda,
    OrigemDemanda,
    PrioridadeDemanda,
    RiscoDemanda,
    StatusDemanda,
    TipoMidiaEvidencia,
    TipoProblema,
)
from .derivacao import (
    LIMITE_DO_ASSUNTO_NO_CABECALHO,
    OrigemSugestao,
    area_do_contexto,
    assunto_do_relato,
    prioridade_do_relato,
    sustenta_registro,
)
from .evidencias import (
    ErroDeEvidencia,
    acesso_temporario,
    anexar_evidencias,
    limites_legiveis,
)
from .forms import (
    AcompanhamentoDaDemandaForm,
    AcaoDeStatusForm,
    DemandaForm,
    EvidenciaDaDemandaForm,
    FiltroDeDemandasForm,
    ObservacaoDaDemandaForm,
)
from .models import Demanda, EvidenciaDemanda
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
    atualizar_acompanhamento,
    contexto_de_governanca,
    criar_demanda,
    editar_demanda,
    registrar_evidencia_anexada,
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

# Onde a sessão guarda as confirmações que já produziram movimentação. Existe
# para uma coisa só: o mesmo formulário não escrever duas vezes no histórico.
CHAVE_DAS_CONFIRMACOES = "demandas_confirmacoes"
LIMITE_DAS_CONFIRMACOES = 20


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
@require_POST
def transcrever_audio(request):
    """Transforma a fala em texto do relato — e para aí.

    Esta é a única porta de áudio das Demandas, e ela termina no texto. Recebe
    o recado falado, devolve a transcrição fiel e descarta o arquivo: não
    interpreta, não sugere local nem prioridade, não cria nada. O texto cai no
    mesmo campo que a pessoa digitaria, e daí em diante o caminho é
    exatamente o do relato escrito — as etapas de informação, revisão e a
    criação pelo mesmo serviço de sempre.

    Falar é uma forma de escrever, não um caminho paralelo: é por isso que não
    existe aqui nenhuma regra de demanda. O arquivo não é gravado em lugar
    nenhum — vive na memória deste pedido e morre com ele. Nem o áudio nem a
    transcrição entram em log.

    O mesmo endereço serve às duas situações, porque as duas são a mesma coisa:
    escrever um relato. No registro de uma demanda nova o texto vai para o campo
    do relato; na atualização de uma demanda existente ele é a atualização dela,
    e a demanda vem indicada no próprio pedido.
    """
    condominio = _condominio_para_ditar(request)
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
    # Um recado que não pôde ser transcrito não é falha do sistema: é uma
    # situação comum, com frase pronta para a tela. A resposta é sempre a
    # mesma forma — ou vem o texto, ou vem o que dizer sobre ele.
    if resultado.obtida:
        return JsonResponse(
            {
                "texto": resultado.texto,
                # A fala bastou para registrar? Quem responde é o domínio, não o
                # navegador: é isso que deixa a tela seguir para as informações
                # sem exigir mais um clique. Fala vazia ou sem fato operacional
                # devolve ``False``, e a etapa da descrição continua aberta para
                # ser completada.
                "avancar": sustenta_registro(resultado.texto),
            }
        )
    return JsonResponse({"mensagem": resultado.erro})


def _condominio_para_ditar(request):
    """O condomínio em que esta pessoa pode escrever agora.

    Ditar é escrever o relato, então quem dita é quem registra: a permissão é a
    mesma de ``criar``, sem papel novo e sem regra nova. Condomínio fora da
    lista autorizada não existe para esta pessoa, e a resposta é 404 — esconder
    o botão na tela nunca substituiu esta checagem.

    Quando o pedido aponta uma demanda, o relato ditado é a atualização dela, e
    a permissão é a de quem acompanha aquela demanda. Aí o condomínio não é
    escolhido pelo navegador: ele sai da demanda autorizada.
    """
    identificador_da_demanda = (
        request.POST.get("demanda") or request.GET.get("demanda") or ""
    ).strip()
    if identificador_da_demanda:
        return _demanda_para_observar(request, identificador_da_demanda).condominio

    autorizados = condominios_para_criar(request.user)
    if not autorizados.exists():
        raise Http404("Nenhum condomínio disponível para registrar demanda.")

    identificador = (request.POST.get("condominio") or "").strip()
    if not identificador:
        return autorizados.first()

    try:
        condominio = autorizados.filter(pk=identificador).first()
    except (ValidationError, ValueError, TypeError):
        condominio = None
    if condominio is None:
        raise Http404("Condomínio não autorizado.")
    return condominio


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
    """Registra o que aconteceu com a demanda — por texto ou por fala.

    O relato é a entrada; a movimentação é a saída. Quem registra conta o que
    aconteceu, a leitura do próprio relato propõe a consequência que ele
    sustenta, e a etapa de conclusão mostra isso antes de tocar em coisa
    alguma. Nenhuma situação muda sem a confirmação de quem registrou: o
    movimento passa pelo serviço oficial do domínio, que grava situação e
    histórico na mesma transação.

    O caminho é o mesmo para o relato digitado e para o relato ditado — a fala
    vira texto antes de chegar aqui, e daí em diante não há duas rotas.
    """
    demanda = _demanda_autorizada(request, pk)
    permitidos = status_permitidos_para(request.user, demanda)
    pode_observar = pode_adicionar_observacao(request.user, demanda)
    if not permitidos and not pode_observar:
        raise PermissionDenied("Esta pessoa não pode atualizar esta demanda.")

    etapa = request.POST.get("etapa_atualizacao") or "informacoes"
    confirmacao = (request.POST.get("confirmacao") or "").strip()
    formulario_status = AcaoDeStatusForm(
        request.POST, permitidos=permitidos, demanda=demanda
    )
    texto = " ".join((request.POST.get("texto") or "").split())
    # A foto e o vídeo acompanham o relato: são a prova do que ele diz. Vêm
    # como arquivos, e não como campo do formulário, porque um envio pode
    # trazer vários sob a mesma chave.
    arquivos = [arquivo for arquivo in request.FILES.getlist("evidencias") if arquivo]
    tem_arquivos = bool(arquivos)
    precisa_validar_observacao = bool(texto) or tem_arquivos
    if precisa_validar_observacao and not pode_observar:
        raise PermissionDenied("Esta pessoa não pode registrar observação nesta demanda.")

    formulario_observacao = (
        ObservacaoDaDemandaForm(request.POST)
        if bool(texto)
        else ObservacaoDaDemandaForm(initial={"texto": texto})
    )

    invalido = bool(permitidos) and not formulario_status.is_valid()
    invalido = invalido or (bool(texto) and not formulario_observacao.is_valid())
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

    # O que o próprio relato sustenta. É leitura, não decisão: nada daqui toca
    # na demanda antes da confirmação.
    leitura = movimentacao.ler(texto, demanda=demanda)

    # A tela diz o que aconteceu; a situação é consequência. A ação escolhida
    # manda; quando ninguém escolheu nenhuma, quem ainda pode mover a demanda é
    # o próprio relato — e só até onde esta pessoa alcança.
    proximo_status = demanda.status
    if permitidos and formulario_status.is_valid():
        proximo_status = formulario_status.cleaned_data.get("acao") or demanda.status
    if (
        proximo_status == demanda.status
        and leitura.conclusao
        and StatusDemanda.CONCLUIDA in permitidos
    ):
        proximo_status = StatusDemanda.CONCLUIDA
    resumo = _resumo_da_atualizacao(demanda, proximo_status, texto, leitura=leitura)

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
            confirmacao=confirmacao or uuid.uuid4().hex,
        )

    if etapa == "corrigir":
        return _render_detalhe(
            request,
            demanda,
            formulario_status=formulario_status,
            formulario_observacao=formulario_observacao,
            modal="atualizar",
            aba=request.GET.get("aba") or "visao_geral",
            etapa_atualizacao="informacoes",
            confirmacao=confirmacao or uuid.uuid4().hex,
        )

    mudou_status = proximo_status != demanda.status
    if not mudou_status and not texto and not tem_arquivos:
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

    # Uma confirmação vale uma movimentação: o toque repetido no botão, ou a
    # página recarregada, reenviam o mesmo formulário. O histórico é a memória
    # da demanda — ele não ganha a mesma atualização duas vezes por acidente.
    if confirmacao and _confirmacao_ja_usada(request, confirmacao):
        messages.info(request, "Esta atualização já tinha sido registrada.")
        return redirect(f"{reverse('demandas:detalhe', args=[demanda.pk])}?aba=historico")

    # Relato, situação, acompanhamento e arquivos são uma coisa só: ou tudo
    # entra, ou nada entra. Sem a transação, um arquivo recusado deixaria a
    # situação já mudada e o relato já escrito — uma movimentação pela metade.
    try:
        with transaction.atomic():
            evento = None
            if mudou_status:
                evento = alterar_status(
                    demanda=demanda, usuario=request.user, novo_status=proximo_status
                )
            if texto:
                evento = registrar_observacao(
                    demanda=demanda, usuario=request.user, texto=texto
                )
            # O relato também pode dizer por quem a demanda passa a esperar, o
            # que vem depois e o que deixou de travar. Só o que ele sustenta é
            # proposto — e só agora, depois da confirmação, isso é aplicado ao
            # acompanhamento. Nada aqui move a situação da demanda.
            proposta = leitura.acompanhamento_proposto()
            if proposta:
                atualizar_acompanhamento(
                    demanda=demanda, usuario=request.user, **proposta
                )
            if tem_arquivos:
                # Sem relato, a evidência precisa de um lugar próprio na linha
                # do tempo: o evento que diz que ela passou a existir.
                evento = evento or registrar_evidencia_anexada(
                    demanda=demanda, usuario=request.user
                )
                anexar_evidencias(
                    demanda=demanda,
                    arquivos=arquivos,
                    usuario=request.user,
                    evento=evento,
                )
    except (ErroDeDemanda, ErroDeEvidencia, ErroDeStorage) as erro:
        if isinstance(erro, ErroDeDemanda):
            messages.error(request, str(erro))
            return redirect(
                f"{reverse('demandas:detalhe', args=[demanda.pk])}?aba=historico"
            )
        # O arquivo recusado volta para o lugar onde ele foi escolhido, com o
        # relato e a situação ainda escritos: a atualização não ficou pela
        # metade, e ninguém precisa digitar de novo o que já tinha dito.
        formulario_evidencia = EvidenciaDaDemandaForm(request.POST, request.FILES)
        formulario_evidencia.is_valid()
        formulario_evidencia.add_error("evidencias", str(erro))
        return _render_detalhe(
            request,
            demanda,
            formulario_status=formulario_status,
            formulario_observacao=formulario_observacao,
            formulario_evidencia=formulario_evidencia,
            modal="atualizar",
            aba=request.GET.get("aba") or "visao_geral",
            etapa_atualizacao="conclusao",
            resumo_atualizacao=resumo,
            confirmacao=confirmacao or uuid.uuid4().hex,
            status=400,
        )

    if confirmacao:
        _lembrar_confirmacao(request, confirmacao)

    messages.success(request, "Demanda atualizada.")
    return redirect(f"{reverse('demandas:detalhe', args=[demanda.pk])}?aba=historico")


def _confirmacao_ja_usada(request, confirmacao: str) -> bool:
    """Se esta confirmação já produziu movimentação nesta sessão.

    A chave nasce quando a tela mostra o que vai acontecer e volta no envio;
    guardá-la é o que permite recusar o segundo envio do mesmo formulário sem
    inventar tabela nem mexer no domínio.
    """
    return confirmacao in (request.session.get(CHAVE_DAS_CONFIRMACOES) or [])


def _lembrar_confirmacao(request, confirmacao: str) -> None:
    """Lembra a confirmação executada, guardando só as últimas."""
    registradas = [
        chave
        for chave in (request.session.get(CHAVE_DAS_CONFIRMACOES) or [])
        if chave != confirmacao
    ]
    request.session[CHAVE_DAS_CONFIRMACOES] = (registradas + [confirmacao])[
        -LIMITE_DAS_CONFIRMACOES:
    ]


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


@login_required
@require_POST
def acompanhar(request, pk):
    """Atualiza o acompanhamento operacional, sem tocar na situação.

    Aqui se responde a outra pergunta, diferente do ciclo da demanda: por quem
    ela espera, qual é o próximo passo, para quando foi prometida, o que a trava
    e quem a segue de perto. Salvar isto não move a demanda — previsão vencida
    não conclui, espera pelo fornecedor não altera situação, e tirar o
    impedimento não encerra nada. Quem acompanha a demanda é quem diz o que sabe
    sobre ela, e é essa a autorização que vale aqui.
    """
    demanda = _demanda_para_acompanhar(request, pk)
    formulario = AcompanhamentoDaDemandaForm(request.POST, demanda=demanda)
    if not formulario.is_valid():
        return _render_detalhe(
            request,
            demanda,
            formulario_acompanhamento=formulario,
            modal="acompanhamento",
            status=400,
        )

    try:
        atualizar_acompanhamento(
            demanda=demanda,
            usuario=request.user,
            **formulario.mudancas(),
        )
    except ErroDeDemanda as erro:
        messages.error(request, str(erro))
        return redirect("demandas:detalhe", pk=demanda.pk)

    messages.success(request, "Acompanhamento atualizado.")
    return redirect("demandas:detalhe", pk=demanda.pk)


@login_required
@require_POST
def adicionar_evidencia(request, pk):
    """Anexa foto ou vídeo à demanda sem mexer na situação dela.

    É a mesma evidência da atualização, por outra porta: quem só quer guardar a
    prova do que já sabe não precisa mover a demanda para isso. O arquivo ganha
    um evento próprio no histórico — o que diz que ele passou a existir — e
    nunca aparece solto, sem quando nem por quem. Anexar não conclui, não
    cancela e não cria demanda.
    """
    demanda = _demanda_para_observar(request, pk)
    formulario = EvidenciaDaDemandaForm(request.POST, request.FILES)
    if not formulario.is_valid():
        return _render_detalhe(
            request,
            demanda,
            formulario_evidencia=formulario,
            modal="evidencia",
            aba="historico",
            status=400,
        )

    # O arquivo e o evento que o anuncia entram juntos: se o arquivo for
    # recusado, o evento também não fica.
    try:
        with transaction.atomic():
            evento = registrar_evidencia_anexada(demanda=demanda, usuario=request.user)
            anexar_evidencias(
                demanda=demanda,
                arquivos=formulario.arquivos_enviados(),
                usuario=request.user,
                evento=evento,
            )
    except (ErroDeEvidencia, ErroDeStorage) as erro:
        formulario.add_error("evidencias", str(erro))
        return _render_detalhe(
            request,
            demanda,
            formulario_evidencia=formulario,
            modal="evidencia",
            aba="historico",
            status=400,
        )

    messages.success(request, "Evidência anexada.")
    return redirect(f"{reverse('demandas:detalhe', args=[demanda.pk])}?aba=historico")


@login_required
def evidencia_arquivo(request, pk, evidencia_id):
    """Entrega o arquivo de uma evidência a quem pode acompanhar a demanda.

    A autorização é a da própria demanda: quem não a acompanha não descobre que
    a evidência existe — a resposta é 404, e não uma negativa que confirmaria o
    arquivo. O endereço devolvido é temporário, gerado na hora: nunca fica
    guardado nem circula.
    """
    demanda = _demanda_autorizada(request, pk)
    evidencia = get_object_or_404(EvidenciaDemanda, pk=evidencia_id, demanda=demanda)
    try:
        destino = acesso_temporario(evidencia)
    except (ErroDeStorage, ImproperlyConfigured) as erro:
        raise Http404("O arquivo desta evidência não está disponível.") from erro
    return redirect(destino)


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
            "responsavel_acompanhamento",
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


def _demanda_para_acompanhar(request, pk):
    """Resolve a demanda e exige o direito de dizer o que se sabe sobre ela.

    Acompanhar é a mesma autorização de observar: quem enxerga a demanda diz o
    que sabe dela. Quem não a acompanha não descobre que ela existe — a resposta
    é 404, e não uma negativa que confirmaria o registro.
    """
    return _demanda_para_observar(request, pk)


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
    pagina_demandas = paginar_do_pedido(
        _demandas_filtradas(request, condominios, filtros), request.GET
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
        # O registro não guarda rascunho: cada etapa é a resposta do envio
        # anterior. Fechar uma etapa descarta o envio, e o lugar de onde a tela
        # volta a viver é a leitura limpa dela mesma.
        "descartar_url": reverse("demandas:listagem"),
        "registro_etapa": registro_etapa,
        "etapas_do_registro": _etapas_para_tela(ETAPAS_DO_REGISTRO, registro_etapa),
        "sugestoes": sugestoes,
        "revisao": revisao,
        "mostrar_condominio": condominios.count() > 1,
        # Falar é outra forma de preencher o relato. Sem serviço de
        # transcrição configurado, o botão não é oferecido e o registro por
        # texto continua exatamente como era.
        "voz_disponivel": audio.disponivel(),
    }
    return render(request, "pages/demandas/listagem.html", contexto, status=status)


def _render_detalhe(
    request,
    demanda,
    *,
    formulario=None,
    formulario_status=None,
    formulario_observacao=None,
    formulario_evidencia=None,
    formulario_acompanhamento=None,
    modal=None,
    aba=None,
    etapa_atualizacao="informacoes",
    resumo_atualizacao=None,
    confirmacao=None,
    status=200,
):
    """Monta a tela da demanda com o que esta pessoa pode fazer com ela."""
    permitidos = status_permitidos_para(request.user, demanda)
    pode_editar = pode_editar_demanda(request.user, demanda)
    pode_observar = pode_adicionar_observacao(request.user, demanda)
    # Acompanhar é a mesma autorização de observar: quem enxerga a demanda diz o
    # que sabe dela — por quem espera, o próximo passo, o que a trava.
    pode_acompanhar = pode_observar
    manutencao = _manutencao_da_demanda(demanda)
    aba_atual = _aba_da_demanda(aba)
    historico = _historico_para_tela(demanda)

    if (modal == "editar" and not pode_editar) or (
        modal == "status" and not permitidos
    ) or (modal == "observacao" and not pode_observar) or (
        modal == "evidencia" and not pode_observar
    ) or (modal == "acompanhamento" and not pode_acompanhar) or (
        modal == "atualizar" and not (permitidos or pode_observar)
    ):
        modal = None

    provas = [item for evento in historico for item in evento["evidencias"]]
    contexto = {
        "demanda": _demanda_para_detalhe(demanda),
        "aba": aba_atual,
        "abas": _abas_para_tela(aba_atual),
        "historico": historico,
        # As janelas de ampliação só existem quando há o que ampliar: sem prova
        # no histórico, nenhuma delas ocupa a página.
        "tem_evidencias": bool(provas),
        "tem_videos": any(item["eh_video"] for item in provas),
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
        # Acompanhar é dizer o que se sabe sobre a demanda — por quem ela espera,
        # o próximo passo, o que a trava. Não move a situação dela.
        "pode_acompanhar": pode_acompanhar,
        # Anexar uma prova é observar a demanda, não movê-la: quem acompanha
        # pode guardar a foto ou o vídeo sem mexer na situação.
        "pode_adicionar_evidencia": pode_observar,
        "pode_concluir": StatusDemanda.CONCLUIDA in permitidos and not demanda.esta_encerrada,
        "proximo_passo": _proximo_passo(demanda, request.user, manutencao),
        "mais_acoes": _mais_acoes_da_demanda(demanda, request.user),
        "formulario_demanda": formulario
        or DemandaForm(condominio=demanda.condominio, initial=_iniciais_da_demanda(demanda)),
        "formulario_status": formulario_status
        or AcaoDeStatusForm(permitidos=permitidos, demanda=demanda),
        "formulario_observacao": formulario_observacao or ObservacaoDaDemandaForm(),
        "formulario_evidencia": formulario_evidencia or EvidenciaDaDemandaForm(),
        "formulario_acompanhamento": formulario_acompanhamento
        or AcompanhamentoDaDemandaForm(demanda=demanda),
        # O que a tela pode dizer sobre o arquivo sem expor bytes nem termos
        # técnicos: os formatos aceitos e o tamanho que cada um pode ter.
        "limites_evidencia": limites_legiveis(),
        "modal_aberto": modal,
        "etapa_atualizacao": etapa_atualizacao,
        "etapas_da_atualizacao": _etapas_para_tela(
            ETAPAS_DA_ATUALIZACAO, etapa_atualizacao
        ),
        "resumo_atualizacao": resumo_atualizacao,
        # A marca que acompanha o formulário de atualização entre a revisão e a
        # confirmação. É o que impede o mesmo envio de virar duas movimentações.
        "confirmacao": confirmacao,
        "voz_disponivel": audio.disponivel(),
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


def _origem_do_prazo(prazo, leitura) -> str:
    """De onde veio a data prevista, na linguagem humana que a tela mostra.

    A data que é a mesma que a política derivou é regra; qualquer outra que
    esteja preenchida foi decidida por quem registrou. Não há terceiro caso: a
    leitura do relato não cria data.
    """
    if not prazo:
        return "Não identificado"
    if prazo == leitura.prazo:
        return "Derivado por regra"
    return "Definido por quem registrou"


def _sugestoes_para_tela(formulario, usuario):
    """O que a etapa de informações apresenta, já em linguagem humana.

    A etapa existe para revisar e corrigir, não para classificar: por isso ela
    traz o resultado da leitura montado — assunto, lugar e tipo de problema,
    peso e prazo, responsável —, e o ajuste fica atrás de uma ação discreta.
    Cada campo guarda o motivo do domínio, para quem quiser saber de onde
    aquilo veio; a tela mostra o motivo onde ele ajuda, e não em cada linha.
    """
    leitura = formulario.leitura
    dados = formulario.cleaned_data
    responsavel = dados.get("responsavel") or formulario.responsavel_sugerido(usuario)
    prioridade = dados.get("prioridade") or ""
    propostas = leitura.sugestoes
    return {
        "assunto": {"valor": dados.get("titulo") or leitura.assunto},
        "local": {
            "valor": leitura.local.nome if leitura.local else dados.get("local_texto") or "",
        },
        # A área não é campo: é a leitura do tipo do lugar onde o problema está.
        # Vem pronta do backend, e some quando o cadastro não a sustenta.
        "area": _proposta_para_tela(propostas["area"]),
        "equipamento": _proposta_para_tela(propostas["equipamento"]),
        "tipo_problema": _proposta_para_tela(propostas["tipo_problema"], TipoProblema),
        "impacto": _proposta_para_tela(propostas["impacto"], ImpactoDemanda),
        "risco": _proposta_para_tela(propostas["risco"], RiscoDemanda),
        "prioridade": {
            "valor": PrioridadeDemanda(prioridade).label if prioridade else "",
            "motivo": leitura.motivo,
        },
        "prazo": {
            "valor": _prazo_por_extenso(dados.get("prazo")) if dados.get("prazo") else "",
            "texto": _prazo_da_sintese(dados.get("prazo"), leitura),
            "origem": _origem_do_prazo(dados.get("prazo"), leitura),
            "motivo": leitura.motivo_do_prazo if dados.get("prazo") == leitura.prazo else "",
        },
        "responsavel": {"valor": _nome_de(responsavel)},
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
            "origem": (
                _origem_do_prazo(dados.get("prazo"), leitura)
                if dados.get("prazo")
                else "Sem prazo definido"
            ),
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
    """Ações secundárias do detalhe, preservando permissões do backend.

    Registrar uma atualização não entra aqui: ela é a ação principal da tela e
    já aparece à vista. Repetir no menu o mesmo destino não é atalho — é ruído.
    """
    acoes = []
    destino = reverse("demandas:detalhe", args=[demanda.pk])
    if pode_editar_demanda(usuario, demanda):
        acoes.append({"label": "Editar demanda", "url": f"{destino}?abrir=editar"})
    # Comunicar aos moradores é uma capacidade transversal: a demanda é a origem,
    # e quem responde pelo condomínio é quem escreve o aviso. O comunicado nasce
    # apontando para esta demanda, e os dados dela continuam sendo dela.
    if pode_criar_comunicacao(usuario, demanda.condominio):
        acoes.append(
            {
                "label": "Gerar comunicado",
                "url": f"{reverse('comunicacao:criar')}?demanda={demanda.pk}",
            }
        )
    return acoes


def _sinal_do_acompanhamento(demanda):
    """O sinal compacto do acompanhamento na lista, ou nada quando não há.

    A grade não é o lugar do acompanhamento inteiro: o que aparece aqui é o que
    muda uma decisão de quem olha a lista — o que está parado à espera de
    alguém, ou uma data prometida que já passou. Sem nada relevante, nenhum
    sinal ocupa a linha.
    """
    if demanda.esta_encerrada or not demanda.tem_acompanhamento:
        return "", ""
    if demanda.previsao_vencida:
        return "Previsão vencida", "danger"
    if demanda.aguardando_tipo:
        return f"Aguardando {demanda.get_aguardando_tipo_display().lower()}", "warning"
    if demanda.aguardando_descricao:
        return f"Aguardando {demanda.aguardando_descricao}", "warning"
    return "", ""


def _acompanhamento_para_tela(demanda):
    """O acompanhamento como o bloco compacto do detalhe o lê.

    Só o que existe vira linha: campo sem resposta não aparece vazio. A previsão
    vem por extenso e, quando já passou, marcada como vencida — um sinal para
    quem acompanha, sem que isso mude nada na demanda.
    """
    linhas = []
    if demanda.aguardando_tipo or demanda.aguardando_descricao:
        linhas.append(
            {"rotulo": "Aguardando", "valor": demanda.aguardando_para_exibir, "tom": ""}
        )
    if demanda.proxima_acao:
        linhas.append({"rotulo": "Próxima ação", "valor": demanda.proxima_acao, "tom": ""})
    if demanda.previsao:
        vencida = demanda.previsao_vencida
        linhas.append(
            {
                "rotulo": "Previsão",
                "valor": _prazo_por_extenso(demanda.previsao),
                "nota": "Vencida" if vencida else "",
                "tom": "danger" if vencida else "",
            }
        )
    if demanda.impedimento:
        linhas.append(
            {"rotulo": "Impedimento", "valor": demanda.impedimento, "tom": "warning"}
        )
    if demanda.responsavel_acompanhamento_id:
        linhas.append(
            {
                "rotulo": "Quem acompanha",
                "valor": _nome_de(demanda.responsavel_acompanhamento),
                "tom": "",
            }
        )
    return linhas


def _nome_de(usuario) -> str:
    """O nome pelo qual a pessoa é reconhecida na tela."""
    return (
        getattr(usuario, "full_name", "")
        or getattr(usuario, "email", "")
        or "Sem responsável"
    )


def _prazo_por_extenso(prazo) -> str:
    return prazo.strftime("%d/%m/%Y") if prazo else "Sem prazo"


def _prazo_da_sintese(prazo, leitura) -> str:
    """O prazo como a síntese do registro o diz.

    Quando a data vem da política, ela aparece com os dias que a regra
    concedeu e o dia em que vence — é o que quem registra precisa saber, e o
    número sai da própria política, não de uma cópia. Quando não há data
    nenhuma, a resposta é a ausência dita em linguagem humana: o registro
    segue sem prazo, e ninguém precisa escolher uma data para continuar.
    """
    if not prazo:
        return "Sem prazo definido"

    data = _prazo_por_extenso(prazo)
    if prazo != leitura.prazo:
        return f"Prazo {data}"

    dias = prazos.dias_do_prazo(leitura.prioridade)
    if not dias:
        return f"Prazo {data}"
    return f"Prazo {'1 dia' if dias == 1 else f'{dias} dias'} · {data}"


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


def _momento_da_ultima_movimentacao(demanda) -> str:
    """Quando a demanda se moveu pela última vez, como a gestão lê.

    A fonte é o histórico — a memória do que mudou. Nunca o carimbo de
    atualização, que anda por qualquer gravação e por isso não responde a esta
    pergunta.
    """
    evento = demanda.ultima_movimentacao
    if evento is None:
        return ""
    return _momento_por_extenso(evento.data_hora, timezone.localdate())


def _demanda_para_tela(demanda, usuario=None):
    """A demanda como a linha da lista precisa dela.

    As ações por linha saem da mesma regra do domínio que o resto do sistema:
    a interface só desenha o que a pessoa já poderia fazer.
    """
    hoje = timezone.localdate()
    situacao, tom = _situacao_do_prazo(demanda.prazo, demanda.esta_encerrada, hoje)
    sinal = _sinal_do_acompanhamento(demanda)
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
        # O acompanhamento não ocupa coluna própria na lista: o que muda uma
        # decisão de quem olha a grade cabe em um sinal discreto ao lado da
        # situação. O resto vive no detalhe da demanda.
        "sinal_acompanhamento": sinal[0],
        "sinal_acompanhamento_tom": sinal[1],
        "acoes": _acoes_da_linha(demanda, usuario),
    }


def _acoes_da_linha(demanda, usuario):
    """As ações oferecidas no menu da linha, já decididas pelo domínio.

    Abrir a demanda não é ação: a linha inteira leva até ela. O que sobra vai
    para o menu de três pontos, e cada entrada abre a mesma janela que o
    detalhe já oferece — a lista não inventa caminho novo, só encurta o que
    existe. As permissões são as do detalhe: quem acompanha pode registrar
    atualização, atualizar o acompanhamento e anexar evidência; editar o
    conteúdo continua sendo de quem responde pelo condomínio.
    """
    if usuario is None:
        return []

    destino = reverse("demandas:detalhe", args=[demanda.pk])
    acoes = []
    if pode_adicionar_observacao(usuario, demanda):
        acoes.append(
            {
                "label": "Registrar atualização",
                "icon": "bx-refresh",
                "url": f"{destino}?abrir=atualizar&aba=visao_geral",
            }
        )
        acoes.append(
            {
                "label": "Atualizar acompanhamento",
                "icon": "bx-time-five",
                "url": f"{destino}?abrir=acompanhamento&aba=visao_geral",
            }
        )
        acoes.append(
            {
                "label": "Adicionar evidência",
                "icon": "bx-image-add",
                "url": f"{destino}?abrir=evidencia&aba=historico",
            }
        )
    if pode_editar_demanda(usuario, demanda):
        acoes.append(
            {
                "label": "Editar",
                "icon": "bx-pencil",
                "url": f"{destino}?abrir=editar&aba=visao_geral",
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
            # O acompanhamento operacional: outra pergunta que não a situação.
            # Só o que existe vira linha no bloco compacto da tela.
            "acompanhamento": _acompanhamento_para_tela(demanda),
            "ultima_movimentacao": _momento_da_ultima_movimentacao(demanda),
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
    # O acompanhamento também aparece com o antes e o depois: por quem se
    # espera, o próximo passo, a data prometida, o que trava e quem acompanha.
    ("aguardando_tipo", "Aguardando"),
    ("aguardando_descricao", "O que se espera"),
    ("proxima_acao", "Próxima ação"),
    ("previsao", "Previsão"),
    ("impedimento", "Impedimento"),
)

ESCOLHAS_DO_HISTORICO = {
    "status": StatusDemanda,
    "prioridade": PrioridadeDemanda,
    "tipo_problema": TipoProblema,
    "impacto": ImpactoDemanda,
    "risco": RiscoDemanda,
    "aguardando_tipo": AguardandoTipo,
}


def _valor_do_historico(campo, valor) -> str:
    """Um valor guardado, como a gestão o lê agora — ou a ausência dele."""
    if campo == "aguardando_tipo":
        return _rotulo_de(AguardandoTipo, valor) or "ninguém"
    if not valor:
        return "não informado"
    if campo in ("prazo", "previsao"):
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
    campo com vocabulário próprio, com o antes e o depois já traduzidos. As
    evidências que aquele evento trouxe aparecem presas a ele: a foto fica onde
    o relato ficou, e não numa galeria à parte.
    """
    hoje = timezone.localdate()
    eventos = (
        demanda.historico.select_related("usuario")
        .prefetch_related("evidencias")
        .order_by("-data_hora")
    )
    return [
        {
            "momento": _momento_por_extenso(evento.data_hora, hoje),
            "tipo": evento.get_tipo_evento_display(),
            "descricao": evento.descricao,
            "quem": _nome_de(evento.usuario),
            "mudancas": _mudancas_do_evento(evento),
            "evidencias": [
                _evidencia_para_tela(item) for item in evento.evidencias.all()
            ],
        }
        for evento in eventos
    ]


def _evidencia_para_tela(evidencia):
    """O mínimo para a tela: o que é e como pedir o arquivo.

    O endereço do arquivo não é resolvido aqui — quem o entrega é a view que
    confere a autorização. Guardar o endereço temporário no contexto só criaria
    uma URL assinada a mais para expirar sem uso.
    """
    return {
        "pk": evidencia.pk,
        "eh_video": evidencia.tipo_midia == TipoMidiaEvidencia.VIDEO,
        "tipo": evidencia.get_tipo_midia_display(),
        "nome": evidencia.nome_original,
    }


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


def _resumo_da_atualizacao(demanda, proximo_status, texto, *, leitura=None):
    """O que a tela mostra entre o relato e a confirmação.

    É um retrato do que aconteceria se esta pessoa confirmasse: o que o próprio
    relato sustentou e a consequência que ele teria. Nada aqui foi aplicado à
    demanda — a leitura propõe, a tela mostra, e só a confirmação chama o
    serviço oficial.
    """
    mudou_status = proximo_status != demanda.status
    situacao = StatusDemanda(proximo_status).label
    conclui = mudou_status and proximo_status == StatusDemanda.CONCLUIDA

    if not mudou_status:
        consequencia, tom = "A demanda continua na mesma situação.", "secondary"
    elif conclui:
        consequencia, tom = "A demanda será concluída.", "success"
    else:
        consequencia, tom = f"A demanda passará para {situacao}.", "info"

    return {
        "situacao": situacao,
        "mudou_status": mudou_status,
        "observacao": texto or "Sem observação adicional.",
        "identificado": list(leitura.identificado) if leitura else [],
        "consequencia": consequencia,
        "consequencia_tom": tom,
        "propoe_conclusao": conclui,
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
