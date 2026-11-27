"""Telas de entrada do balanço.

A tela existe para uma coisa só: receber o arquivo que a administração enviou e
mostrar o que já chegou. Nenhum valor é interpretado aqui, nenhum mês passa a
valer enquanto a gestão não confirma. O que a gestão vê antes disso é o estado
do recebimento e o que o Eithra entendeu do documento.

Depois da confirmação, o mês passa a existir como balanço, e a tela passa a
mostrá-lo como gestão — não mais como pipeline.

A autorização é decidida no backend, sempre a partir do vínculo ativo da pessoa
com o condomínio. Quem não pode importar não é avisado de que o registro
existe: a resposta é 404.
"""
import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.balancos import classificacao, correcoes, evidencias
from apps.balancos.categorias import CategoriaEithra
from apps.balancos.categorias import rotulo as rotulo_da_categoria
from apps.balancos.constants import (
    LayoutBalanco,
    StatusBalanco,
    StatusImportacao,
    TipoVinculoEvidencia,
)
from apps.balancos.contratos import (
    StatusConferencia,
    TipoInconsistencia,
    de_dicionario,
    de_dicionario_validado,
)
from apps.balancos.correcoes import ErroDeCorrecao
from apps.balancos.detector import ErroDeDeteccaoDeLayout
from apps.balancos.evidencias import ErroDeEvidencia
from apps.balancos.extrator_legado import (
    montar_balanco_da_conferencia,
)
from apps.balancos.forms import (
    ClassificarDespesaForm,
    ConferenciaDeBalancoForm,
    CorrecaoDeLancamentoForm,
    EdicaoDeEvidenciaForm,
    EvidenciaVisualForm,
    ImportacaoBalancoForm,
    LancamentoManualForm,
    MarcarNaoDespesaForm,
)
from apps.balancos.models import BalancoMensal, EvidenciaVisualBalanco, ImportacaoBalanco
from apps.balancos.natureza_despesa import NaturezaDespesa
from apps.balancos.natureza_despesa import rotulo as rotulo_da_natureza
from apps.balancos.normalizadores import decimal_para_texto, texto_para_decimal
from apps.balancos.permissoes import (
    condominios_para_importar,
    condominios_para_ler,
    pode_corrigir_balanco,
    pode_gerenciar_evidencias,
)
from apps.balancos.services import (
    ErroDeConfirmacao,
    ErroDeExclusao,
    ErroDeReleitura,
    confirmar_importacao,
    corrigir_balanco,
    excluir_importacao,
    formatar_tamanho,
    gerar_acesso_temporario,
    limite_de_upload,
    medir_detalhamento,
    pode_confirmar,
    publicar_balanco_da_conferencia,
    receber_importacao,
    reler_importacao,
)
from apps.balancos.validacao_financeira import TOLERANCIA, validar_balanco

# Quantidade de recebimentos recentes mostrados. A lista é de apoio: o que
# importa é o que acabou de chegar, não um arquivo morto.
LIMITE_DA_LISTA = 50


@login_required
def importacoes(request):
    """Arquivos de balanço recebidos, nos condomínios em que a pessoa pode importar."""
    condominios = _condominios_autorizados(request)

    return _render(request, condominios=condominios)


@login_required
def importar(request):
    """Recebe o PDF e registra a tentativa de importação.

    Nada de ``BalancoMensal`` nasce aqui: o balanço só existe depois que a
    gestão confirma a importação.
    """
    if request.method != "POST":
        return redirect("balancos:importacoes")

    condominios = _condominios_autorizados(request)
    formulario = ImportacaoBalancoForm(
        request.POST, request.FILES, condominios=condominios
    )

    if not formulario.is_valid():
        return _render(request, condominios=condominios, formulario=formulario, status=400)

    resultado = receber_importacao(
        condominio=formulario.cleaned_data["condominio"],
        usuario=request.user,
        arquivo=formulario.cleaned_data["arquivo"],
    )

    if resultado.duplicada:
        messages.warning(
            request,
            "Este mesmo arquivo já havia sido recebido para este condomínio. "
            "Nada foi guardado de novo.",
        )
    elif resultado.importacao.status == StatusImportacao.ERRO:
        messages.error(
            request,
            "Não foi possível processar o arquivo automaticamente. A tentativa "
            "ficou registrada; tente enviar de novo.",
        )
    else:
        messages.success(request, "Arquivo recebido.")

    return redirect("balancos:importacoes")


@login_required
def detalhe(request, pk):
    """O que o Eithra entendeu do balanço, para a gestão conferir.

    A tela ainda não oficializa nada: mostra os dados lidos e os pontos que
    merecem atenção. Confirmar é uma decisão da gestão, e por isso o botão só
    aparece quando o domínio concorda que esta importação pode virar balanço.
    """
    importacao = _importacao_autorizada(request, pk)
    return render(
        request,
        "pages/balancos/importacao_detalhe.html",
        _contexto_do_detalhe(importacao),
    )


@login_required
@require_POST
def reler(request, pk):
    """Repete a detecção e a leitura sobre o arquivo já guardado.

    Nada é reenviado: o PDF continua no storage e só é lido de novo. Serve para
    a importação que ficou sem leitura. Um mês já confirmado não é relido.
    """
    importacao = _importacao_autorizada(request, pk)
    try:
        reler_importacao(importacao)
    except ErroDeReleitura as erro:
        messages.warning(request, str(erro))
    except ErroDeDeteccaoDeLayout:
        messages.error(
            request,
            "Não foi possível ler o arquivo desta importação. O motivo ficou "
            "registrado na própria importação.",
        )
    else:
        importacao.refresh_from_db()
        if importacao.resultado_extraido:
            messages.success(request, "A leitura foi refeita a partir do arquivo recebido.")
        else:
            messages.warning(
                request,
                "A leitura foi tentada de novo, mas o documento continua sem "
                "leitura automática.",
            )
    return redirect("balancos:importacoes")


@login_required
@require_POST
def confirmar(request, pk):
    """Confirma a importação: é aqui que o mês passa a valer.

    A decisão é da gestão, mas quem confere continua sendo o domínio: a
    confirmação acontece em uma transação só, e uma falha em qualquer passo não
    deixa balanço pela metade. Confirmar de novo não cria um segundo balanço.
    """
    importacao = _importacao_autorizada(request, pk)
    try:
        balanco = confirmar_importacao(importacao, usuario=request.user)
    except ErroDeConfirmacao as erro:
        messages.error(request, str(erro))
        return redirect("balancos:detalhe", pk=importacao.pk)

    messages.success(request, "Balanço confirmado.")
    return redirect("balancos:balanco", pk=balanco.pk)


@login_required
def conferencia(request, pk=None):
    """A tela única de conferência, antes de o mês passar a valer.

    Sem ``pk``, a tela abre vazia: é o mês lançado sem PDF. Com ``pk``, ela abre
    com o que a leitura do balancete antigo propôs — e tudo continua editável.
    As duas conferências automáticas avisam, mas quem publica é a gestão.
    """
    condominios = _condominios_autorizados(request)
    importacao = _importacao_autorizada(request, pk) if pk else None
    return _render_conferencia(request, condominios=condominios, importacao=importacao)


@login_required
@require_POST
def publicar_conferencia(request, pk=None):
    """Publica o mês conferido: é aqui que ele passa a valer."""
    condominios = _condominios_autorizados(request)
    importacao = _importacao_autorizada(request, pk) if pk else None
    linhas = _linhas_do_formulario(request)
    formulario = ConferenciaDeBalancoForm(request.POST, condominios=condominios)

    if not formulario.is_valid():
        return _render_conferencia(
            request,
            condominios=condominios,
            importacao=importacao,
            formulario=formulario,
            linhas=linhas,
            status=400,
        )

    dados = formulario.cleaned_data
    try:
        balanco = publicar_balanco_da_conferencia(
            condominio=dados["condominio"],
            usuario=request.user,
            competencia=date(dados["ano"], dados["mes"], 1),
            saldo_anterior=_valor_em_texto(dados["saldo_anterior"]),
            receitas_total=_valor_em_texto(dados["receitas_total"]),
            despesas_total=_valor_em_texto(dados["despesas_total"]),
            saldo_final=_valor_em_texto(dados["saldo_final"]),
            receitas=linhas["receitas"],
            despesas=linhas["despesas"],
            importacao=importacao,
        )
    except ErroDeConfirmacao as erro:
        messages.error(request, str(erro))
        return _render_conferencia(
            request,
            condominios=condominios,
            importacao=importacao,
            formulario=formulario,
            linhas=linhas,
            status=400,
        )

    messages.success(request, "Balanço publicado.")
    return redirect("balancos:balanco", pk=balanco.pk)


@login_required
def balanco(request, pk):
    """O balanço confirmado do mês, como gestão — não como pipeline.

    Ler o mês confirmado é mais amplo do que importá-lo: a supervisão acompanha
    sem responder por ele. Corrigir continua sendo de quem responde — por isso a
    pergunta é feita aqui, uma vez, e a tela só mostra o que o backend aceitaria.
    """
    instancia = _balanco_autorizado(request, pk)
    return render(
        request,
        "pages/balancos/balanco_detalhe.html",
        _contexto_do_balanco(
            instancia,
            pode_corrigir=pode_corrigir_balanco(request.user, instancia.condominio),
            pode_gerenciar_evidencias=pode_gerenciar_evidencias(
                request.user, instancia.condominio
            ),
        ),
    )


@login_required
@require_POST
def corrigir_lancamento(request, pk):
    """Corrige um lançamento do detalhamento, guardando quem decidiu e por quê."""
    instancia = _balanco_para_corrigir(request, pk)
    formulario = CorrecaoDeLancamentoForm(request.POST, grupos=_grupos_do_balanco(instancia))
    if not formulario.is_valid():
        return _voltar_ao_balanco(request, instancia, _primeiro_erro(formulario))
    dados = formulario.cleaned_data
    return _executar_correcao(
        request,
        instancia,
        acao="corrigir",
        campos=_campos_do_lancamento(dados),
        lancamento_id=dados["lancamento_id"],
        justificativa=dados["justificativa"],
        versao=dados["versao"],
    )


@login_required
@require_POST
def marcar_nao_despesa(request, pk):
    """Tira um lançamento das despesas — sem apagar o que o Eithra leu."""
    instancia = _balanco_para_corrigir(request, pk)
    formulario = MarcarNaoDespesaForm(request.POST)
    if not formulario.is_valid():
        return _voltar_ao_balanco(request, instancia, _primeiro_erro(formulario))
    dados = formulario.cleaned_data
    return _executar_correcao(
        request,
        instancia,
        acao="nao_despesa",
        lancamento_id=dados["lancamento_id"],
        justificativa=dados["justificativa"],
        versao=dados["versao"],
    )


@login_required
@require_POST
def adicionar_lancamento(request, pk):
    """Acrescenta ao detalhamento um lançamento que o documento não trouxe."""
    instancia = _balanco_para_corrigir(request, pk)
    formulario = LancamentoManualForm(request.POST, grupos=_grupos_do_balanco(instancia))
    if not formulario.is_valid():
        return _voltar_ao_balanco(request, instancia, _primeiro_erro(formulario))
    dados = formulario.cleaned_data
    return _executar_correcao(
        request,
        instancia,
        acao="adicionar",
        campos=_campos_do_lancamento(dados),
        justificativa=dados["justificativa"],
        versao=dados["versao"],
    )


# -- Despesas a classificar -----------------------------------------------------
#
# A leitura classifica o que as palavras alcançam; o que sobra é a fila curta
# desta tela. A sugestão é só uma proposta — a confirmação é que decide, e é ela
# que vira regra. Nenhum valor é tocado: a linha continua valendo o que valia.

JUSTIFICATIVA_DA_CLASSIFICACAO = (
    "Classificação confirmada na lista de despesas a classificar."
)


@login_required
def classificar_despesas(request, pk):
    """A fila curta do que ficou em A classificar ou Outros, com sugestões.

    Quem responde pelo balanço é quem classifica: a mesma permissão da correção.
    A sugestão vem marcada como sugestão, nunca como decisão tomada.
    """
    instancia = _balanco_para_corrigir(request, pk)
    validado = de_dicionario_validado(instancia.dados_validados)
    leitura = validado.leitura or {}
    linhas = classificacao.linhas_a_classificar(
        leitura, condominio=instancia.condominio
    )
    versao = instancia.atualizado_em.isoformat() if instancia.atualizado_em else ""
    return render(
        request,
        "pages/balancos/classificar.html",
        {
            "balanco": instancia,
            "competencia": _competencia_por_extenso(validado.competencia),
            "subtitulo": (
                f"{instancia.condominio.nome} / "
                f"{_competencia_por_extenso(validado.competencia)}"
            ),
            "linhas": linhas,
            "categorias": [
                {"codigo": categoria.value, "rotulo": categoria.label}
                for categoria in CategoriaEithra
                if categoria not in classificacao.CATEGORIAS_EM_ABERTO
            ],
            "versao": versao,
        },
    )


@login_required
@require_POST
def confirmar_classificacao(request, pk):
    """Confirma a categoria de uma linha e transforma a decisão em regra.

    A confirmação é uma correção como outra qualquer: passa pelo mesmo caminho,
    guarda quem decidiu e regrava o detalhamento. A diferença é que aqui a
    categoria muda e, com isso, a regra nasce — o mês seguinte já nasce
    classificado. Nenhum valor é alterado.
    """
    instancia = _balanco_para_corrigir(request, pk)
    formulario = ClassificarDespesaForm(request.POST)
    if not formulario.is_valid():
        return _voltar_para_classificacao(request, instancia, _primeiro_erro(formulario))
    dados = formulario.cleaned_data
    try:
        corrigir_balanco(
            instancia,
            acao="corrigir",
            usuario=request.user,
            campos={"categoria_eithra": dados["categoria"]},
            lancamento_id=dados["lancamento_id"],
            justificativa=JUSTIFICATIVA_DA_CLASSIFICACAO,
            versao=dados["versao"],
        )
    except ErroDeCorrecao as erro:
        return _voltar_para_classificacao(request, instancia, str(erro))

    messages.success(
        request, "Despesa classificada. A regra foi guardada para os próximos meses."
    )
    return redirect("balancos:classificar_despesas", pk=instancia.pk)


def _voltar_para_classificacao(request, instancia, aviso: str):
    """Recusa a classificação sem mudar nada e volta para a fila, dizendo por quê."""
    messages.error(request, aviso)
    return redirect("balancos:classificar_despesas", pk=instancia.pk)


@login_required
@require_POST
def adicionar_evidencia(request, pk):
    """Anexa uma foto a um lançamento, a uma categoria ou ao destaque do mês.

    A foto é complementar: ela explica o número, não o substitui. Quem decide
    se ela entra é o domínio — e o vínculo que veio da tela é conferido contra o
    balanço antes de qualquer coisa ser guardada.
    """
    instancia = _balanco_para_gerenciar_evidencias(request, pk)
    formulario = EvidenciaVisualForm(request.POST, request.FILES)
    if not formulario.is_valid():
        return _voltar_ao_balanco(request, instancia, _primeiro_erro(formulario))
    dados = formulario.cleaned_data
    return _executar_evidencia(
        request,
        instancia,
        acao="adicionar",
        tipo_vinculo=dados["tipo_vinculo"],
        referencia=dados["referencia"],
        arquivo=dados["imagem"],
        legenda=dados["legenda"],
        exibir_aos_moradores=dados["exibir_aos_moradores"],
    )


@login_required
@require_POST
def editar_evidencia(request, pk, evidencia_id):
    """Muda a legenda ou a decisão de exibir. A imagem continua a mesma.

    Trocar a foto não é edição: a gestão anexa outra e remove a anterior.
    """
    instancia = _balanco_para_gerenciar_evidencias(request, pk)
    formulario = EdicaoDeEvidenciaForm(request.POST)
    if not formulario.is_valid():
        return _voltar_ao_balanco(request, instancia, _primeiro_erro(formulario))
    dados = formulario.cleaned_data
    return _executar_evidencia(
        request,
        instancia,
        acao="editar",
        evidencia_id=evidencia_id,
        legenda=dados["legenda"],
        exibir_aos_moradores=dados["exibir_aos_moradores"],
    )


@login_required
@require_POST
def remover_evidencia(request, pk, evidencia_id):
    """Tira a foto do mês sem apagar o registro de que ela existiu."""
    instancia = _balanco_para_gerenciar_evidencias(request, pk)
    return _executar_evidencia(
        request, instancia, acao="remover", evidencia_id=evidencia_id
    )


@login_required
def imagem_da_evidencia(request, pk, evidencia_id):
    """Abre a foto por um acesso curto, gerado na hora.

    O vínculo com o balanço é conferido antes: quem não lê o mês não descobre
    que a foto existe, e o endereço do objeto nunca vira link fixo.
    """
    instancia = _balanco_autorizado(request, pk)
    evidencia = get_object_or_404(
        EvidenciaVisualBalanco, pk=evidencia_id, balanco=instancia, ativo=True
    )
    if not evidencia.storage_key:
        raise Http404("A foto desta evidência não está disponível.")
    return redirect(evidencias.acesso_temporario(evidencia))


@login_required
def arquivo_do_balanco(request, pk):
    """Abre o PDF original do balanço confirmado, com acesso curto."""
    instancia = _balanco_autorizado(request, pk)
    original = _documento_original(instancia)
    if original is None or not original.storage_key:
        raise Http404("O documento original deste balanço não está disponível.")
    return redirect(gerar_acesso_temporario(original))


@login_required
def acessar_arquivo(request, pk):
    """Abre o arquivo por um acesso curto, gerado na hora.

    O endereço do objeto nunca aparece como link fixo: cada visita gera um
    acesso temporário, e só depois de confirmado o vínculo com o condomínio.
    """
    importacao = _importacao_autorizada(request, pk)
    return redirect(gerar_acesso_temporario(importacao))


@login_required
@require_POST
def excluir(request, pk):
    """Exclui a importação: o registro sai e o arquivo é liberado do bucket."""
    importacao = _importacao_autorizada(request, pk)
    try:
        excluir_importacao(importacao)
    except ErroDeExclusao as erro:
        messages.error(request, str(erro))
        return redirect("balancos:importacoes")

    messages.success(request, "Importação excluída.")
    return redirect("balancos:importacoes")


def _condominios_autorizados(request):
    """Condomínios em que esta pessoa pode importar, ou 404.

    404 e não 403: quem não pode importar também não deve descobrir, pela
    resposta, que a tela existe para outros.
    """
    condominios = condominios_para_importar(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomínio disponível para importar balanço.")
    return condominios


def _importacao_autorizada(request, pk):
    """Resolve a importação dentro dos condomínios autorizados, ou 404."""
    condominios = _condominios_autorizados(request)
    return get_object_or_404(
        ImportacaoBalanco.objects.select_related("condominio").filter(
            condominio__in=condominios
        ),
        pk=pk,
    )


def _balanco_autorizado(request, pk):
    """Resolve o balanço dentro dos condomínios que esta pessoa pode ler, ou 404.

    Ler o mês confirmado alcança mais papéis do que importá-lo, e o conjunto de
    condomínios vem do domínio — nunca do endereço nem do que a tela mostrou.
    """
    condominios = condominios_para_ler(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomínio disponível para consultar balanço.")
    return get_object_or_404(
        BalancoMensal.objects.select_related("condominio").filter(
            condominio__in=condominios
        ),
        pk=pk,
    )


def _balanco_para_corrigir(request, pk):
    """Resolve o balanço e exige, no backend, o direito de corrigir.

    Quem não pode ler o mês não descobre que ele existe: a resposta é 404. Quem
    lê e não responde por ele é recusado com 403 — a existência do balanço já é
    legítima para essa pessoa, a decisão sobre ele não é. Esconder a ação na
    tela não substitui esta checagem.
    """
    instancia = _balanco_autorizado(request, pk)
    if not pode_corrigir_balanco(request.user, instancia.condominio):
        raise PermissionDenied("Esta pessoa não responde por este balanço.")
    return instancia


def _balanco_para_gerenciar_evidencias(request, pk):
    """Resolve o balanço e exige, no backend, o direito de mexer nas fotos.

    Mesma distinção da correção: quem não lê o mês recebe 404 — não descobre que
    ele existe; quem lê e não responde por ele recebe 403 — a existência já é
    legítima, a decisão sobre as fotos não é. A supervisão vê as imagens nesta
    tela, mas não as acrescenta nem decide exibi-las.
    """
    instancia = _balanco_autorizado(request, pk)
    if not pode_gerenciar_evidencias(request.user, instancia.condominio):
        raise PermissionDenied("Esta pessoa não responde pelas fotos deste balanço.")
    return instancia


# -- Correção do detalhamento ---------------------------------------------------
#
# Corrigir é uma decisão, não uma edição de tela: a view traduz o formulário para
# o que o domínio entende, o domínio decide e grava em uma transação, e o erro do
# domínio volta para a gestão como frase. Nenhuma regra de negócio mora aqui.

AVISO_DA_CORRECAO = {
    "corrigir": "Correção registrada.",
    "nao_despesa": "Lançamento marcado como não sendo despesa.",
    "adicionar": "Lançamento adicionado.",
}


def _leitura_do_balanco(balanco) -> dict:
    """O estado validado do mês, como está gravado agora."""
    return de_dicionario_validado(balanco.dados_validados).leitura or {}


def _grupos_do_balanco(balanco) -> list[str]:
    """Os agrupamentos que o próprio documento declarou, para os campos de escolha."""
    return correcoes.grupos_para_opcoes(_leitura_do_balanco(balanco))


def _campos_do_lancamento(dados: dict) -> dict:
    """Traduz o formulário para os campos que a correção entende.

    A categoria vem do catálogo do Eithra e o valor sai daqui como texto decimal:
    dinheiro não atravessa o contrato como número de ponto flutuante.
    """
    return {
        "descricao": dados["descricao"],
        "valor": decimal_para_texto(dados["valor"]),
        "categoria_eithra": dados["categoria"],
        "natureza": dados["natureza"],
        "grupo": dados["grupo"] or "",
    }


def _primeiro_erro(formulario) -> str:
    """A primeira coisa que impediu a correção, em uma frase para a gestão."""
    for erros in formulario.errors.values():
        if erros:
            return str(erros[0])
    return "Não foi possível entender a correção."


def _voltar_ao_balanco(request, instancia, aviso: str):
    """Recusa a correção sem mudar nada e volta para o mês, dizendo por quê."""
    messages.error(request, aviso)
    return redirect("balancos:balanco", pk=instancia.pk)


def _executar_correcao(request, instancia, *, acao: str, **decisao):
    """Aplica a decisão em uma transação e volta para o mês.

    Ou tudo acontece — a decisão, o histórico e o recálculo —, ou nada fica: o
    domínio é quem garante isso. Aqui só se decide o que dizer à gestão.
    """
    try:
        corrigir_balanco(instancia, acao=acao, usuario=request.user, **decisao)
    except ErroDeCorrecao as erro:
        return _voltar_ao_balanco(request, instancia, str(erro))

    messages.success(request, AVISO_DA_CORRECAO[acao])
    return redirect("balancos:balanco", pk=instancia.pk)


# -- Evidências visuais ---------------------------------------------------------
#
# A foto não altera o detalhamento do mês: ela explica o que já está lá. Por
# isso anexar, editar ou remover não mexe em valor nenhum — e o erro do domínio
# volta para a gestão como frase, do mesmo jeito que na correção.

AVISO_DA_EVIDENCIA = {
    "adicionar": "Foto anexada.",
    "editar": "Foto atualizada.",
    "remover": "Foto removida.",
}


def _executar_evidencia(request, instancia, *, acao: str, **decisao):
    """Aplica a decisão sobre a foto em uma transação e volta para o mês."""
    try:
        if acao == "adicionar":
            evidencias.anexar_evidencia(instancia, usuario=request.user, **decisao)
        elif acao == "editar":
            evidencias.editar_evidencia(instancia, **decisao)
        else:
            evidencias.remover_evidencia(instancia, **decisao)
    except ErroDeEvidencia as erro:
        return _voltar_ao_balanco(request, instancia, str(erro))

    messages.success(request, AVISO_DA_EVIDENCIA[acao])
    return redirect("balancos:balanco", pk=instancia.pk)


def _render(request, *, condominios, formulario=None, status=200):
    """Monta a tela com o que já foi recebido e o formulário de entrada."""
    recebidas = (
        ImportacaoBalanco.objects.filter(condominio__in=condominios)
        .select_related("condominio")
        .order_by("-criado_em")[:LIMITE_DA_LISTA]
    )
    confirmados = (
        BalancoMensal.objects.filter(condominio__in=condominios)
        .select_related("condominio")
        .exclude(status=StatusBalanco.SUBSTITUIDO)
        .order_by("-competencia", "-versao")[:LIMITE_DA_LISTA]
    )
    _ligar_balancos_confirmados(recebidas)
    contexto = {
        "balancos_confirmados": [_balanco_da_lista(item) for item in confirmados],
        "importacoes": recebidas,
        "formulario_importacao": formulario or ImportacaoBalancoForm(condominios=condominios),
        "limite_de_upload": formatar_tamanho(limite_de_upload()),
    }
    return render(request, "pages/balancos/importacoes.html", contexto, status=status)


def _ligar_balancos_confirmados(importacoes) -> None:
    """Aponta, em cada importação confirmada, o balanço que nasceu dela.

    O vínculo é a competência: é ela que diz de qual mês o balanço é. A
    importação continua guardada como memória técnica do recebimento; o balanço
    é o mês em vigor. A busca é feita de uma vez só, para a lista não virar uma
    consulta por linha.
    """
    competencia_por_importacao = {}
    for importacao in importacoes:
        importacao.balanco_confirmado = None
        if not importacao.confirmado_em:
            continue
        competencia = _data_iso((importacao.resultado_extraido or {}).get("competencia"))
        if competencia is not None:
            competencia_por_importacao[importacao.pk] = (
                importacao.condominio_id,
                competencia,
            )

    if not competencia_por_importacao:
        return

    encontrados = {
        (item.condominio_id, item.competencia): item
        for item in BalancoMensal.objects.filter(
            condominio_id__in={par[0] for par in competencia_por_importacao.values()},
            competencia__in={par[1] for par in competencia_por_importacao.values()},
        ).exclude(status=StatusBalanco.SUBSTITUIDO)
    }
    for importacao in importacoes:
        encontrado = encontrados.get(competencia_por_importacao.get(importacao.pk))
        if encontrado is not None:
            importacao.balanco_confirmado = encontrado


# -- Conferência antes de publicar ---------------------------------------------
#
# A tela única reúne o que a leitura propôs — ou o que a gestão digita, no mês
# sem PDF — e as duas conferências automáticas. Nada aqui decide pelo domínio:
# publicar é uma chamada só, e quem responde por ela é o serviço.

CAMPOS_DA_CONFERENCIA = (
    "saldo_anterior",
    "receitas_total",
    "despesas_total",
    "saldo_final",
)


def _render_conferencia(
    request, *, condominios, importacao, formulario=None, linhas=None, status=200
):
    """Monta a tela de conferência com o proposto ou com o que foi digitado."""
    proposta = _proposta_da_conferencia(importacao)
    if formulario is None:
        formulario = ConferenciaDeBalancoForm(
            initial=proposta["inicial"], condominios=condominios
        )

    receitas = linhas["receitas"] if linhas else proposta["receitas"]
    despesas = linhas["despesas"] if linhas else proposta["despesas"]
    checagens = _checagens_da_conferencia(
        valores=_valores_da_conferencia(formulario, proposta),
        receitas=receitas,
        despesas=despesas,
    )

    return render(
        request,
        "pages/balancos/conferencia.html",
        {
            "formulario": formulario,
            "importacao": importacao,
            "competencia": _competencia_da_conferencia(formulario, proposta),
            "receitas": _linhas_para_tela(receitas),
            "despesas": _linhas_para_tela(despesas),
            "checagens": checagens,
            "tem_divergencia": any(item["estado"] == "diverge" for item in checagens),
        },
        status=status,
    )


def _proposta_da_conferencia(importacao) -> dict:
    """O que a leitura propôs para a tela — ou uma tela vazia, sem importação."""
    if importacao is None or not importacao.resultado_extraido:
        return {
            "inicial": {"ano": timezone.localdate().year},
            "receitas": [],
            "despesas": [],
        }

    lido = de_dicionario(importacao.resultado_extraido)
    competencia = _data_iso(lido.competencia)
    return {
        "inicial": {
            "condominio": importacao.condominio_id,
            "mes": competencia.month if competencia else None,
            "ano": competencia.year if competencia else timezone.localdate().year,
            "saldo_anterior": _numero_para_editar(lido.saldo_anterior),
            "receitas_total": _numero_para_editar(lido.receitas_total),
            "despesas_total": _numero_para_editar(lido.despesas_total),
            "saldo_final": _numero_para_editar(lido.saldo_final),
        },
        "receitas": [(item.descricao_original, item.valor) for item in lido.receitas],
        "despesas": [(item.descricao_original, item.valor) for item in lido.despesas],
    }


def _valores_da_conferencia(formulario, proposta) -> dict:
    """Os quatro valores da conferência em texto, digitados ou propostos."""
    valores = {}
    for nome in CAMPOS_DA_CONFERENCIA:
        bruto = (
            formulario.data.get(nome)
            if formulario.is_bound
            else proposta["inicial"].get(nome)
        )
        valores[nome] = _valor_em_texto(texto_para_decimal(bruto))
    return valores


def _competencia_da_conferencia(formulario, proposta) -> str | None:
    """Mês e ano por extenso do que está na tela, para o cabeçalho."""
    if formulario.is_bound:
        mes, ano = formulario.data.get("mes"), formulario.data.get("ano")
    else:
        mes, ano = proposta["inicial"].get("mes"), proposta["inicial"].get("ano")
    try:
        return f"{NOMES_DO_MES[int(mes)].capitalize()}/{int(ano)}"
    except (TypeError, ValueError, IndexError):
        return None


def _linhas_para_tela(linhas) -> list[dict]:
    """As linhas editáveis da tela: a descrição e o valor como se digita."""
    return [
        {"descricao": descricao, "valor": _numero_para_editar(valor)}
        for descricao, valor in linhas
    ]


def _valor_em_texto(valor) -> str | None:
    """O ``Decimal`` conferido como texto do contrato, ou ausência."""
    return decimal_para_texto(valor) if valor is not None else None


def _linhas_do_formulario(request) -> dict:
    """Pareia descrição e valor de cada linha digitada, ignorando as vazias."""
    return {
        "receitas": _linhas_de(
            request.POST.getlist("receita_descricao"),
            request.POST.getlist("receita_valor"),
        ),
        "despesas": _linhas_de(
            request.POST.getlist("despesa_descricao"),
            request.POST.getlist("despesa_valor"),
        ),
    }


def _linhas_de(descricoes, valores) -> list[tuple[str, str]]:
    """Cada par descrição/valor válido, no texto decimal do contrato."""
    linhas = []
    for descricao, valor in zip(descricoes, valores):
        nome = " ".join((descricao or "").split())
        numero = texto_para_decimal(valor)
        if not nome or numero is None or numero <= Decimal("0.00"):
            continue
        linhas.append((nome, decimal_para_texto(numero)))
    return linhas


def _checagens_da_conferencia(*, valores, receitas, despesas) -> list[dict]:
    """As duas conferências automáticas: verdes, vermelhas — ou sem o que conferir.

    A resposta vem da mesma validação que o domínio usa: monta-se o contrato com
    o que está na tela e pede-se a ele que aponte o que não fecha. A referência
    de cada apontamento diz a qual das duas conferências ele pertence.
    """
    lido = montar_balanco_da_conferencia(
        competencia=None,
        saldo_anterior=valores["saldo_anterior"],
        receitas_total=valores["receitas_total"],
        despesas_total=valores["despesas_total"],
        saldo_final=valores["saldo_final"],
        receitas=receitas,
        despesas=despesas,
    )
    por_referencia: dict[str, list[str]] = {}
    for item in validar_balanco(lido):
        por_referencia.setdefault(item.referencia, []).append(item.mensagem)

    tem_linhas = bool(receitas) or bool(despesas)
    return [
        _checagem(
            "Saldos fecham",
            "Saldo anterior + receitas − despesas = saldo final",
            tem=all(valores[nome] is not None for nome in CAMPOS_DA_CONFERENCIA),
            mensagens=por_referencia.get("saldo", []),
        ),
        _checagem(
            "Linhas batem com os totais",
            "A soma das linhas conferidas é igual aos totais informados",
            tem=tem_linhas,
            mensagens=(
                por_referencia.get("receitas", []) + por_referencia.get("despesas", [])
            ),
        ),
    ]


def _checagem(titulo: str, descricao: str, *, tem: bool, mensagens: list[str]) -> dict:
    """Um resultado de conferência: verde, vermelho ou sem o que conferir."""
    if not tem:
        return {
            "titulo": titulo,
            "descricao": descricao,
            "estado": "sem_dados",
            "icone": "bx-info-circle",
            "tom": "secondary",
            "mensagens": [],
        }
    if mensagens:
        return {
            "titulo": titulo,
            "descricao": descricao,
            "estado": "diverge",
            "icone": "bx-error-circle",
            "tom": "danger",
            "mensagens": mensagens,
        }
    return {
        "titulo": titulo,
        "descricao": descricao,
        "estado": "confere",
        "icone": "bx-check-circle",
        "tom": "success",
        "mensagens": [],
    }


# -- Apresentação do que foi lido ----------------------------------------------
#
# A tela fala a língua da gestão, não a do contrato: valores em reais, datas em
# dia/mês, categorias e naturezas com nome humano. Nada de código técnico, de
# score ou de detalhe de sistema aparece aqui — o contrato continua sendo o
# contrato, e esta tradução acontece só na borda.

TOM_POR_NATUREZA = {
    NaturezaDespesa.RECORRENTE_FIXA: "info",
    NaturezaDespesa.RECORRENTE_VARIAVEL: "info",
    NaturezaDespesa.EXTRAORDINARIA: "warning",
    NaturezaDespesa.NAO_DETERMINADA: "secondary",
}

# Tom da conferência do agrupamento. O texto já diz o que aconteceu; a cor só
# separa o que fecha do que ainda precisa de esclarecimento.
TOM_POR_CONFERENCIA = {
    StatusConferencia.CONFERE: "success",
    StatusConferencia.FALTA_IDENTIFICAR: "warning",
    StatusConferencia.SEM_SUBTOTAL: "secondary",
}

# Tom do ponto de atenção pelo quanto pesa. O texto diz o que aconteceu; a cor
# só ajuda a separar o que pesa mais do que pesa menos.
TOM_POR_SEVERIDADE = {
    "alta": "danger",
    "media": "warning",
    "baixa": "secondary",
}

NOMES_DO_MES = (
    "",
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


def _contexto_do_detalhe(importacao):
    """Traduz o que está gravado num contexto que a tela mostra sem jargão.

    As despesas aparecem pelo agrupamento do próprio documento — o grupo diz o
    que o documento declarou, o que a leitura encontrou e a diferença entre os
    dois. Recebimento individual não entra na tela: o total de receitas vem do
    resumo do documento, e reproduzir centenas de linhas por unidade não serve
    à gestão.
    """
    balanco = de_dicionario(importacao.resultado_extraido)
    lido = bool(importacao.resultado_extraido)
    lancamentos = _lancamentos_por_grupo(balanco.despesas)
    # Quem decide se esta importação pode virar balanço é o domínio; a tela só
    # mostra o botão quando ele concorda — e nunca oferece o que o backend
    # recusaria.
    confirma, motivo = pode_confirmar(importacao)

    return {
        "importacao": importacao,
        "lido": lido,
        "competencia": _competencia_por_extenso(balanco.competencia) if lido else None,
        "grupos": [
            _grupo_para_tela(item, lancamentos.get(item.nome or "", []))
            for item in balanco.grupos
        ],
        "saldos": [_saldo_para_tela(item) for item in balanco.saldos],
        "tem_a_confirmar": any(
            texto_para_decimal(item.nao_determinada) for item in balanco.grupos
        ),
        "resumo": _resumo_para_tela(balanco.resumo),
        "totais": {
            "receitas": _moeda(balanco.receitas_total) if lido else None,
            "despesas": _moeda(balanco.despesas_total) if lido else None,
            "residuo": (
                _residuo_do_total(balanco.despesas_total, balanco.soma_despesas_extraida)
                if lido
                else None
            ),
            "saldo_anterior": _moeda(balanco.saldo_anterior) if lido else None,
            "saldo_final": _moeda(balanco.saldo_final) if lido else None,
        },
        "detalhamento": _detalhamento_para_tela(balanco) if lido else None,
        "pode_confirmar": confirma,
        "layout_legado": importacao.layout_detectado == LayoutBalanco.LEGADO,
        "motivo_para_nao_confirmar": motivo,
        "pendencias": [_pendencia_para_tela(item) for item in (importacao.inconsistencias or [])],
        "motivo_recusa": None if lido else _motivo_para_tela(importacao),
        "motivo_tom": "danger" if importacao.status == StatusImportacao.ERRO else "info",
    }


def _contexto_do_balanco(
    balanco, *, pode_corrigir: bool = False, pode_gerenciar_evidencias: bool = False
) -> dict:
    """Traduz o balanço confirmado para a tela de gestão.

    O documento já virou mês: aqui não há pipeline, não há etapa, não há
    detalhe técnico. O que aparece é o que a administração declarou, o que a
    leitura detalhou — já com as decisões da gestão por cima — e o quanto ficou
    sem detalhe associado. O resto continua guardado, mas não é assunto desta
    tela.

    As fotos entram como apoio do número: cada lançamento, categoria e o
    destaque do mês trazem as suas, e o que não tem foto simplesmente não
    mostra nada. A imagem não substitui valor, documento nem justificativa.
    """
    validado = de_dicionario_validado(balanco.dados_validados)
    leitura = validado.leitura or {}
    lido = de_dicionario(leitura)
    lancamentos = _lancamentos_por_categoria(leitura)
    categorias = _categorias_para_tela(lido, lancamentos)
    evidencias_por_vinculo = _evidencias_ativas(balanco)
    _distribuir_evidencias(categorias, evidencias_por_vinculo)
    destaques_da_evidencia = evidencias_por_vinculo.get(
        (TipoVinculoEvidencia.DESTAQUE, ""), []
    )
    grupos_em_atencao = _grupos_em_atencao_para_tela(lido.grupos)
    saldos = [_saldo_para_tela(item) for item in lido.saldos]
    detalhamento = _detalhamento_para_tela(lido)
    resumo = _resumo_para_tela(lido.resumo)
    original = _documento_original(balanco)
    saldo_rotulo, saldo_valor = _saldo_principal_para_tela(
        validado.saldo_final, saldos=saldos
    )
    revisoes = _revisoes_para_tela(leitura)
    # A versão do mês viaja no formulário: corrigir sobre um estado que já mudou
    # seria pior do que pedir para recarregar.
    versao = balanco.atualizado_em.isoformat() if balanco.atualizado_em else ""
    grupos = correcoes.grupos_para_opcoes(leitura)
    ignorados = [
        _lancamento_para_tela(item)
        for item in correcoes.despesas_efetivas(leitura, incluir_ignoradas=True)
        if item["excluida"]
    ]

    return {
        "balanco": balanco,
        "competencia": _competencia_por_extenso(validado.competencia),
        "subtitulo": f"{balanco.condominio.nome} / {_competencia_por_extenso(validado.competencia)}",
        "categorias": categorias,
        "saldos": saldos,
        "grupos_em_atencao": grupos_em_atencao,
        "resumo": resumo,
        "totais": {
            "receitas": _moeda(validado.receitas_total),
            "despesas": _moeda(validado.despesas_total),
            "saldo_anterior": _moeda(validado.saldo_anterior),
            "saldo_final": saldo_valor,
            "saldo_rotulo": saldo_rotulo,
        },
        "detalhamento": detalhamento,
        "texto_detalhamento": _texto_do_detalhamento(detalhamento),
        "destaques": _destaques_para_tela(categorias=categorias, resumo=resumo),
        "texto_grupos_em_atencao": _texto_grupos_em_atencao(grupos_em_atencao),
        "nome_do_arquivo": original.nome_original if original else "",
        "tem_documento_original": bool(original),
        "pendencias": [
            _pendencia_para_tela(item) for item in (balanco.inconsistencias or [])
        ],
        "confirmado_em": balanco.validado_em,
        "pode_corrigir": pode_corrigir,
        "pode_gerenciar_evidencias": pode_gerenciar_evidencias,
        "evidencia_destaque": destaques_da_evidencia[0] if destaques_da_evidencia else None,
        "tem_evidencias": bool(evidencias_por_vinculo),
        "form_evidencia": EvidenciaVisualForm(auto_id="evidencia-%s"),
        "limite_de_fotos": evidencias.LIMITE_DE_FOTOS_POR_VINCULO,
        "limite_da_foto": formatar_tamanho(evidencias.limite_da_foto()),
        "ignorados": ignorados,
        "revisoes": revisoes,
        "form_correcao": CorrecaoDeLancamentoForm(
            auto_id="corrigir-%s", grupos=grupos, initial={"versao": versao}
        ),
        "form_adicionar": LancamentoManualForm(
            auto_id="adicionar-%s", grupos=grupos, initial={"versao": versao}
        ),
    }


def _documento_original(balanco):
    """O PDF original ativo do balanço, quando existir."""
    return (
        balanco.documentos.filter(ativo=True).order_by("-enviado_em").first()
        or balanco.documentos.order_by("-enviado_em").first()
    )


# -- Evidências: o que a tela mostra --------------------------------------------
#
# A foto é apoio do número. Ela aparece pequena, ao lado do que explica, e nunca
# vira card próprio nem galeria. O endereço que a tela usa é o da própria tela,
# que gera um acesso curto na hora — o objeto do bucket nunca vira link fixo.


def _evidencias_ativas(balanco) -> dict[tuple[str, str], list[dict]]:
    """As fotos ativas do mês, agrupadas pelo vínculo a que pertencem.

    Uma consulta só para o mês inteiro: a tela mostra cada foto no lugar certo
    sem perguntar ao banco por lançamento.
    """
    por_vinculo: dict[tuple[str, str], list[dict]] = {}
    evidencias_ativas = EvidenciaVisualBalanco.objects.filter(
        balanco=balanco, ativo=True
    ).order_by("criado_em")
    for evidencia in evidencias_ativas:
        por_vinculo.setdefault(
            (evidencia.tipo_vinculo, evidencia.referencia), []
        ).append(_evidencia_para_tela(balanco, evidencia))
    return por_vinculo


def _evidencia_para_tela(balanco, evidencia) -> dict:
    """Uma foto pronta para a tela: legenda, decisão de exibir e endereços.

    Nenhum endereço de storage aparece aqui: o que a tela usa é a rota do
    próprio Eithra, que confere o vínculo e gera o acesso curto na hora.
    """
    return {
        "id": str(evidencia.pk),
        "legenda": evidencia.legenda,
        "exibir_aos_moradores": evidencia.exibir_aos_moradores,
        "url": reverse(
            "balancos:evidencia_imagem", args=[balanco.pk, evidencia.pk]
        ),
        "editar_url": reverse(
            "balancos:evidencia_editar", args=[balanco.pk, evidencia.pk]
        ),
        "remover_url": reverse(
            "balancos:evidencia_remover", args=[balanco.pk, evidencia.pk]
        ),
    }


def _distribuir_evidencias(categorias: list[dict], por_vinculo: dict) -> None:
    """Põe cada foto ao lado do que ela explica: a categoria e o lançamento."""
    for categoria in categorias:
        categoria["evidencias"] = por_vinculo.get(
            (TipoVinculoEvidencia.CATEGORIA, categoria["codigo"]), []
        )
        for lancamento in categoria["lancamentos"]:
            lancamento["evidencias"] = por_vinculo.get(
                (TipoVinculoEvidencia.LANCAMENTO, lancamento["id"]), []
            )


def _balanco_da_lista(balanco) -> dict:
    """Resumo curto do balanço confirmado para a tela principal."""
    resumo = balanco.resumo or {}
    return {
        "pk": balanco.pk,
        "condominio": balanco.condominio.nome,
        "competencia": _competencia_por_extenso(balanco.competencia.isoformat()),
        "receitas": _moeda(resumo.get("receitas_total")),
        "despesas": _moeda(resumo.get("despesas_total")),
        "status": "Confirmado",
    }


def _detalhamento_para_tela(balanco) -> dict:
    """O quanto do total declarado ficou preso ao detalhe identificado.

    A distância entre o declarado e o identificado é dita, não escondida: é ela
    que mostra que o balanço vale pelos totais oficiais mesmo quando nem todo
    lançamento pôde ser associado.
    """
    medida = medir_detalhamento(balanco)
    nao_associado = texto_para_decimal(medida.valor_nao_associado) or Decimal("0.00")

    return {
        "total_declarado": _moeda(medida.total_declarado),
        "total_identificado": _moeda(medida.total_identificado),
        "valor_nao_associado": _moeda(medida.valor_nao_associado),
        "percentual": _percentual(medida.percentual),
        "tem_residual": abs(nao_associado) > TOLERANCIA,
        "mais_do_que_o_declarado": nao_associado < 0,
    }


def _percentual(valor: str | None) -> str | None:
    """Percentual com uma casa, como a gestão lê: ``91,2%``."""
    decimal = texto_para_decimal(valor) if valor else None
    if decimal is None:
        return None
    arredondado = decimal.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{arredondado:.1f}".replace(".", ",") + "%"


def _residuo_do_total(despesas_total: str | None, soma_extraida: str) -> dict | None:
    """A distância entre o total declarado e a soma do que foi lido.

    O total do documento e a soma do que foi lido são conceitos distintos: um é
    o que a administração afirma, o outro é o que a leitura encontrou. Quando a
    leitura encontra menos, falta identificar; quando encontra mais, o valor
    veio acima do declarado — e dizer o contrário seria afirmar o inverso do
    que os números mostram. Resíduo de arredondamento não vira frase.
    """
    total = texto_para_decimal(despesas_total)
    if total is None:
        return None
    diferenca = total - (texto_para_decimal(soma_extraida) or Decimal("0.00"))
    if abs(diferenca) <= TOLERANCIA:
        return None
    if diferenca > 0:
        return {
            "rotulo": "Não identificado",
            "valor": _moeda(decimal_para_texto(diferenca)),
        }
    return {
        "rotulo": "Acima do declarado",
        "valor": _moeda(decimal_para_texto(-diferenca)),
    }


def _moeda(valor: str | None) -> str | None:
    """Escreve o valor em reais como a gestão lê: ``R$ 1.234,56``."""
    decimal = texto_para_decimal(valor) if valor else None
    if decimal is None:
        return None
    formatado = f"{decimal:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatado}"


def _competencia_por_extenso(iso: str | None) -> str | None:
    """Mês e ano por extenso: ``2026-08-01`` vira ``Agosto/2026``."""
    referencia = _data_iso(iso)
    if referencia is None:
        return None
    return f"{NOMES_DO_MES[referencia.month].capitalize()}/{referencia.year}"


def _dia(iso: str | None) -> str:
    """Dia e mês, como a gestão lê um lançamento: ``10/08``."""
    referencia = _data_iso(iso)
    return referencia.strftime("%d/%m") if referencia else ""


def _data_iso(iso: str | None) -> date | None:
    if not iso:
        return None
    try:
        return date.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def _lancamentos_por_grupo(despesas) -> dict[str, list[dict]]:
    """Lançamentos de cada agrupamento, na ordem em que o documento os traz."""
    por_grupo: dict[str, list[dict]] = {}
    for despesa in despesas:
        por_grupo.setdefault(despesa.categoria_original or "", []).append(
            _despesa_para_tela(despesa)
        )
    return por_grupo


def _lancamentos_por_categoria(leitura: dict) -> dict[str, list[dict]]:
    """Lançamentos válidos por categoria do Eithra, como eles valem hoje.

    A lista vem do estado validado — o lido com as correções por cima —, e não
    da leitura original: corrigir precisa mudar o que a tela mostra, e o que a
    máquina leu continua guardado para quem quiser comparar.
    """
    por_categoria: dict[str, list[dict]] = {}
    for item in correcoes.despesas_efetivas(leitura):
        por_categoria.setdefault(item["categoria_eithra"], []).append(
            _lancamento_para_tela(item)
        )
    return por_categoria


def _lancamento_para_tela(item: dict) -> dict:
    """Um lançamento para a tela: o valor de hoje ao lado do que foi lido.

    O lido viaja junto porque corrigir exige ver o que o Eithra havia entendido;
    os indicadores dizem, sem badge forte, o que a gestão ajustou à mão.
    """
    return {
        "id": item["id"],
        "descricao": item["descricao"],
        "descricao_lida": item["lido"]["descricao"],
        "valor": _moeda(item["valor"]),
        "valor_editavel": _numero_para_editar(item["valor"]),
        "valor_lido": _moeda(item["lido"]["valor"]),
        "categoria": rotulo_da_categoria(item["categoria_eithra"]),
        "categoria_codigo": item["categoria_eithra"],
        "natureza": rotulo_da_natureza(item["natureza"]),
        "natureza_codigo": item["natureza"],
        "natureza_tom": TOM_POR_NATUREZA.get(item["natureza"], "secondary"),
        "grupo": item["grupo"] or "",
        "data": _dia(item["data"]),
        "ajustado": item["ajustado"],
        "manual": item["manual"],
        "evidencias": [],
    }


def _numero_para_editar(valor: str | None) -> str:
    """O valor como a gestão o digita: ``1.234,56``, sem cifrão e sem código."""
    formatado = _moeda(valor)
    if not formatado:
        return ""
    return formatado.removeprefix("R$ ")


def _despesa_para_tela(item) -> dict:
    return {
        "data": _dia(item.data),
        "descricao": item.descricao_original,
        "categoria": rotulo_da_categoria(item.categoria_eithra),
        "natureza": rotulo_da_natureza(item.natureza),
        "natureza_tom": TOM_POR_NATUREZA.get(item.natureza, "secondary"),
        "valor": _moeda(item.valor),
        "fornecedor": item.fornecedor_candidato or "",
        "documento": item.documento or "",
        "parcela": item.parcela or "",
        "grupo": item.categoria_original or "",
    }


def _categorias_para_tela(balanco, lancamentos: dict[str, list[dict]]) -> list[dict]:
    """Categorias agregadas do mês, ordenadas pelo total que mais pesou."""
    categorias = [
        _categoria_para_tela(item, lancamentos.get(item.categoria, []))
        for item in balanco.categorias
        if (texto_para_decimal(item.total) or Decimal("0.00")) > Decimal("0.00")
    ]
    return sorted(categorias, key=lambda item: item["total_decimal"], reverse=True)


def _categoria_para_tela(categoria, lancamentos: list[dict]) -> dict:
    total = texto_para_decimal(categoria.total) or Decimal("0.00")
    return {
        # O código é a identidade da categoria no balanço: é ele que a foto usa
        # como referência, e não o rótulo que a tela mostra.
        "codigo": categoria.categoria,
        "slug": re.sub(r"[^a-z0-9]+", "-", categoria.categoria.lower()).strip("-"),
        "nome": rotulo_da_categoria(categoria.categoria),
        "recorrente": _moeda(categoria.recorrente),
        "extra": _moeda(categoria.extraordinaria),
        "a_confirmar": _moeda(categoria.nao_determinada),
        "total": _moeda(categoria.total),
        "total_decimal": total,
        "lancamentos": lancamentos,
        "evidencias": [],
    }


def _grupos_em_atencao_para_tela(grupos) -> list[dict]:
    """Só os grupos que ainda têm diferença relevante a explicar."""
    grupos_em_atencao = []
    for grupo in grupos:
        if grupo.status != StatusConferencia.FALTA_IDENTIFICAR:
            continue
        diferenca = texto_para_decimal(grupo.diferenca) if grupo.diferenca else None
        if diferenca is None or abs(diferenca) <= TOLERANCIA:
            continue
        grupos_em_atencao.append(
            {
                "categoria": grupo.nome or "Despesas sem agrupamento",
                "declarado": _moeda(grupo.total_declarado),
                "identificado": _moeda(grupo.soma_extraida),
                "diferenca": _moeda(decimal_para_texto(abs(diferenca))),
            }
        )
    return grupos_em_atencao


def _grupo_para_tela(grupo, lancamentos: list[dict]) -> dict:
    """Traduz um agrupamento: o declarado, o identificado e a diferença.

    O agrupamento sem nome é o que o documento não rotulou — a leitura não
    inventa um nome para ele, apenas diz que não veio agrupado.
    """
    return {
        "nome": grupo.nome or "Despesas sem agrupamento",
        "categoria": rotulo_da_categoria(grupo.categoria_eithra),
        "quantidade": grupo.quantidade,
        "recorrente": _moeda(grupo.recorrente),
        "extraordinaria": _moeda(grupo.extraordinaria),
        "a_confirmar": _moeda(grupo.nao_determinada),
        "identificado": _moeda(grupo.soma_extraida),
        "total_declarado": _moeda(grupo.total_declarado) if grupo.total_declarado else "",
        "conferencia": _rotulo_da_conferencia(grupo.status),
        "tom_conferencia": TOM_POR_CONFERENCIA.get(grupo.status, "secondary"),
        "diferenca": _diferenca_para_tela(grupo.diferenca, grupo.status),
        "lancamentos": lancamentos,
    }


def _diferenca_para_tela(diferenca: str | None, status: str) -> str:
    """A frase da diferença do agrupamento — ou nada, quando não há o que dizer.

    A diferença nunca é escondida nem arredondada para zero: o que faltou
    identificar é dito, e o que veio acima do declarado também.
    """
    valor = texto_para_decimal(diferenca) if diferenca else None
    if valor is None or status != StatusConferencia.FALTA_IDENTIFICAR:
        return ""
    if valor > 0:
        return f"Falta identificar {_moeda(decimal_para_texto(valor))}"
    return f"Identificado {_moeda(decimal_para_texto(-valor))} acima do declarado"


def _rotulo_da_conferencia(status: str) -> str:
    """Nome humano da conferência; um rótulo honesto para o que não se conhece."""
    try:
        return StatusConferencia(status).label
    except ValueError:
        return "Não conferido"


def _saldo_para_tela(item) -> dict:
    return {
        "identificacao": _identificacao_de_saldo(item.identificacao),
        "valor": _moeda(item.valor),
        "saldo_anterior": _moeda(item.saldo_anterior),
        "saldo_atual": _moeda(item.saldo_final or item.valor),
    }


def _saldo_principal_para_tela(saldo_final: str | None, *, saldos: list[dict]) -> tuple[str, str]:
    """Escolhe o que cabe na faixa principal sem inventar consolidação."""
    saldo = _moeda(saldo_final)
    if saldo:
        return ("Saldo", saldo)
    if len(saldos) == 1 and saldos[0]["saldo_atual"]:
        return ("Saldo", saldos[0]["saldo_atual"])
    return ("Saldos", "Saldos por conta")


def _texto_do_detalhamento(detalhamento: dict) -> str | None:
    if not detalhamento.get("percentual"):
        return None
    return f"Detalhamento identificado: {detalhamento['percentual']}"


def _destaques_para_tela(*, categorias: list[dict], resumo: dict) -> list[dict]:
    """No máximo dois destaques, só quando trazem contexto útil.

    O residual não está aqui de propósito: ele já aparece no card de
    detalhamento e no bloco de completude. Repetir o mesmo número em três
    lugares não informa mais — só aumenta a densidade da tela.
    """
    destaques = []
    if categorias:
        maior = categorias[0]
        destaques.append(
            {
                "titulo": "Maior categoria",
                "rotulo": maior["nome"],
                "valor": maior["total"],
            }
        )

    extra = resumo.get("extraordinaria")
    if extra and extra != "R$ 0,00":
        destaques.append(
            {
                "titulo": "Despesas extras",
                "rotulo": "Gastos fora da rotina do mês",
                "valor": extra,
            }
        )

    return destaques[:2]


# -- Histórico de ajustes -------------------------------------------------------
#
# O histórico é a memória de quem decidiu: quando, o que mudou, de que valor
# para qual e por quê. O que se mostra é a decisão em linguagem humana — nunca o
# que ficou guardado, nem o identificador interno do lançamento.

ACOES_DA_REVISAO = {
    "correcao": "Lançamento ajustado",
    "nao_despesa": "Marcado como não despesa",
    "inclusao": "Lançamento adicionado",
}

ROTULO_DO_CAMPO = {
    "descricao": "Descrição",
    "valor": "Valor",
    "categoria_eithra": "Categoria",
    "natureza": "Natureza",
    "grupo": "Grupo",
}


def _revisoes_para_tela(leitura: dict) -> list[dict]:
    """O histórico de ajustes, do mais recente ao mais antigo, como a gestão o lê."""
    return [
        {
            "quando": _momento_por_extenso(revisao.get("quando")),
            "acao": ACOES_DA_REVISAO.get(revisao.get("tipo"), "Ajuste"),
            "lancamento": revisao.get("lancamento") or "",
            "mudancas": [
                _mudanca_para_tela(item) for item in revisao.get("mudancas") or []
            ],
            "justificativa": revisao.get("justificativa") or "",
            "autor": revisao.get("autor") or "",
        }
        for revisao in reversed(correcoes.revisoes(leitura))
    ]


def _mudanca_para_tela(mudanca: dict) -> dict:
    """Um campo alterado, com o antes e o depois já escritos para leitura."""
    campo = mudanca.get("campo") or ""
    return {
        "campo": ROTULO_DO_CAMPO.get(campo, "Campo"),
        "antes": _valor_da_mudanca(campo, mudanca.get("antes")),
        "depois": _valor_da_mudanca(campo, mudanca.get("depois")),
    }


def _valor_da_mudanca(campo: str, valor) -> str:
    """Escreve o valor de um campo alterado do jeito que ele aparece na tela."""
    if valor is None or valor == "":
        return "—"
    texto = str(valor)
    if campo == "valor":
        return _moeda(texto) or texto
    if campo == "categoria_eithra":
        return rotulo_da_categoria(texto)
    if campo == "natureza":
        return rotulo_da_natureza(texto)
    return texto


def _momento_por_extenso(iso: str | None) -> str:
    """Quando o ajuste aconteceu, na hora de quem lê."""
    if not iso:
        return ""
    try:
        momento = datetime.fromisoformat(str(iso))
    except ValueError:
        return ""
    if timezone.is_aware(momento):
        momento = timezone.localtime(momento)
    return momento.strftime("%d/%m/%Y às %H:%M")


def _texto_grupos_em_atencao(grupos_em_atencao: list[dict]) -> str | None:
    quantidade = len(grupos_em_atencao)
    if not quantidade:
        return None
    if quantidade == 1:
        return "1 grupo precisa de atenção."
    return f"{quantidade} grupos precisam de atenção."


def _resumo_para_tela(resumo) -> dict:
    return {
        "recorrente": _moeda(resumo.recorrente_total),
        "extraordinaria": _moeda(resumo.extraordinaria_total),
        "a_confirmar": _moeda(resumo.nao_determinada_total),
        "despesas": resumo.despesas_identificadas,
        "pontos": resumo.pontos_de_atencao,
        "conferidos": resumo.grupos_conferidos,
        "com_diferenca": resumo.grupos_com_diferenca,
        "principais": [rotulo_da_categoria(item) for item in resumo.principais_categorias],
    }


def _identificacao_de_saldo(texto: str) -> str:
    """Preserva o rótulo útil do saldo sem expor número de conta desnecessário."""
    limpo = re.sub(r"\b\d[\d.\-]{3,}\b", "", texto or "")
    limpo = re.sub(r"\s{2,}", " ", limpo).strip(" -/")
    return limpo or "Conta/Fundo"


def _pendencia_para_tela(pendencia: dict) -> dict:
    """Traduz um ponto de atenção gravado para o que a tela mostra.

    O que foi lido vira frase para a gestão: o título nomeia o tipo de ponto e a
    mensagem já vem escrita em linguagem humana. Nem o código interno nem a
    página do documento aparecem aqui.
    """
    return {
        "titulo": _rotulo_da_inconsistencia(pendencia.get("tipo", "")),
        "mensagem": pendencia.get("mensagem", ""),
        "tom": TOM_POR_SEVERIDADE.get(pendencia.get("severidade"), "secondary"),
    }


def _rotulo_da_inconsistencia(tipo: str) -> str:
    """Nome humano do ponto de atenção; um rótulo neutro para o que não se conhece."""
    try:
        return TipoInconsistencia(tipo).label
    except ValueError:
        return "Ponto de atenção"


def _motivo_para_tela(importacao) -> str:
    """Por que este documento ainda não foi lido, em linguagem para a gestão."""
    if importacao.status == StatusImportacao.ERRO:
        return importacao.erro_processamento or (
            "Não foi possível ler este documento. Tente enviar de novo."
        )
    if importacao.layout_detectado == LayoutBalanco.LEGADO:
        return "Este é um balancete antigo: quem o lê é a leitura de documentos legados."
    return "Ainda não foi possível reconhecer o formato deste documento."
