"""Tela operacional da Portaria."""
import uuid
from datetime import timedelta
from urllib.parse import quote, urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import NON_FIELD_ERRORS, PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.cadastros.models import Fornecedor, LocalOperacional
from apps.comunidade.models import (
    Pessoa,
    Unidade,
    Veiculo,
    VinculoPessoaUnidade,
)
from apps.condominios import logo
from apps.core import audio
from apps.core.paginacao import paginar_do_pedido
from apps.estoque.permissoes import pode_ver_estoque

from . import etiqueta, interpretacao, numero_falado
from .constants import (
    HORA_DE_ABERTURA,
    HORA_DE_FECHAMENTO,
    TIPOS_DE_ENTREGA,
    TRANSPORTADORA_CORREIOS,
    TRANSPORTADORA_OUTRA,
    TRANSPORTADORAS_DE_ENTREGA,
    ContextoAcessoPortaria,
    EventoAcessoPortaria,
    InstrucaoEntrega,
    IntencaoPortaria,
    MeioAutorizacaoPortaria,
    StatusAutorizacaoPortaria,
    TipoAcessoPortaria,
)
from .forms import (
    TIPOS_DE_RESIDENTE,
    AutorizacaoPortariaForm,
    ChegadaNaoPrevistaForm,
    VerificarAutorizacaoForm,
)
from .fotos import ErroDeFoto, guardar_foto, validar_foto
from .models import AcessoPortaria, AutorizacaoPortaria, HistoricoAcessoPortaria
from .permissoes import condominios_para_ver, pode_operar_portaria
from .services import (
    ErroDePortaria,
    empresas_conhecidas,
    registrar_aviso_whatsapp,
    registrar_chegada,
    registrar_chegada_com_resultado,
    registrar_esperado,
    registrar_saida,
    verificar_autorizacao,
)


@login_required
def painel(request):
    return _render_painel(request)


@login_required
def buscar_autorizador(request):
    """Quem pode autorizar uma entrada, procurado pelo nome ou pelo apartamento.

    A portaria não escolhe entre procurar pessoa ou unidade: quem responde por
    uma unidade responde pelo apartamento dela, e é o mesmo par nos dois
    sentidos. A tela só precisa de uma pergunta.
    """
    condominio = _condominio_para_operar(request)
    termo = (request.GET.get("q") or "").strip()
    unidades, pessoas, sugestao = _autorizadores(condominio, termo)
    return render(
        request,
        "pages/portaria/_autorizador.html",
        {
            "termo": termo,
            "unidades": unidades,
            "pessoas": pessoas,
            "sugestao": sugestao,
        },
    )


@login_required
def buscar_portaria(request):
    """A busca da portaria: o condomínio responde numa lista só.

    Quem opera não escolhe entre procurar pessoa, apartamento, placa ou
    empresa: digita o que sabe e a tela diz o que existe. É a mesma pergunta do
    registro de chegada, com tudo o que o condomínio guarda como resposta.
    """
    condominio = _condominio_para_operar(request)
    termo = (request.GET.get("q") or "").strip()
    return render(
        request,
        "pages/portaria/_busca.html",
        {"termo": termo, "resultados": _resultados_da_portaria(condominio, termo)},
    )


@login_required
@require_POST
def criar_esperado(request):
    condominio = _condominio_para_operar(request)
    formulario = AutorizacaoPortariaForm(request.POST, condominio=condominio)
    if not formulario.is_valid():
        return _render_painel(
            request,
            formulario_esperado=formulario,
            modal="esperado",
            status=400,
        )
    try:
        registrar_esperado(
            condominio=condominio,
            usuario=request.user,
            dados=formulario.cleaned_data,
        )
    except ErroDePortaria as erro:
        formulario.add_error(None, str(erro))
        return _render_painel(
            request,
            formulario_esperado=formulario,
            modal="esperado",
            status=400,
        )
    messages.success(request, "Visita esperada registrada.")
    return redirect(_destino("portaria:painel", condominio))


@login_required
@require_POST
def criar_chegada_nao_prevista(request):
    condominio = _condominio_para_operar(request)
    formulario = ChegadaNaoPrevistaForm(request.POST, condominio=condominio)
    if not formulario.is_valid():
        return _render_painel(
            request,
            formulario_chegada=formulario,
            modal="chegada",
            status=400,
        )
    try:
        autorizacao, acesso = registrar_chegada_com_resultado(
            condominio=condominio,
            usuario=request.user,
            dados=formulario.cleaned_data,
        )
    except ErroDePortaria as erro:
        formulario.add_error(None, str(erro))
        return _render_painel(
            request,
            formulario_chegada=formulario,
            modal="chegada",
            status=400,
        )
    # A foto é evidência opcional da chegada, e a chegada já está registrada:
    # uma foto que o armazenamento recusar não desfaz o acesso nem trava a
    # portaria — a pessoa é avisada e o registro continua de pé.
    try:
        guardar_foto(acesso=acesso, arquivo=request.FILES.get("foto"))
    except ErroDeFoto as erro:
        messages.warning(request, str(erro))
    messages.success(request, _recado_do_resultado(autorizacao, acesso) + _recado_do_aviso(acesso))
    return redirect(_destino("portaria:painel", condominio))


@login_required
@require_POST
def chegou_encomenda(request):
    """Registra a encomenda em uma etapa só: a entrega, não a visita.

    O caminho curto da entrega: a etiqueta sugere o destino, o porteiro confere,
    e a chegada nasce como Correios ou Delivery conforme a transportadora — a
    autorização é presencial, porque quem entrega está no balcão agora. Só a
    unidade é obrigatória; o resto é atalho.

    Registrada a encomenda, a tela oferece o aviso ao morador: com um único
    destinatário elegível, o painel já volta pronto para abrir a conversa; com
    mais de um, a lista fica na linha da entrega; sem ninguém, a mensagem diz.
    """
    condominio = _condominio_para_operar(request)
    unidade = _unidade_da_encomenda(request, condominio)
    if unidade is None:
        return _render_painel(
            request,
            modal="chegou-encomenda",
            erro_encomenda="Escolha a unidade que recebe a encomenda.",
            status=400,
        )
    transportadora = _transportadora_da_encomenda(request)
    dados = {
        "unidade": unidade,
        "tipo_acesso": (
            TipoAcessoPortaria.CORREIOS
            if transportadora == TRANSPORTADORA_CORREIOS
            else TipoAcessoPortaria.DELIVERY
        ),
        "nome": _nome_da_encomenda(transportadora),
        # "Outra" não é transportadora: é a ausência dela. Guardar o rótulo
        # faria o recado ao morador dizer "Outra" como se fosse uma empresa.
        "empresa": "" if transportadora == TRANSPORTADORA_OUTRA else transportadora,
        "status": StatusAutorizacaoPortaria.AUTORIZADO,
        # O meio é o mesmo que a chegada não prevista já usa: a portaria é o
        # lugar onde o resultado foi obtido, e a entrega não inventa um meio
        # próprio só para caber no registro.
        "meio_autorizacao": MeioAutorizacaoPortaria.INTERFONE,
        "instrucao_entrega": _instrucao_da_encomenda(request),
    }
    try:
        _autorizacao, acesso = registrar_chegada_com_resultado(
            condominio=condominio, usuario=request.user, dados=dados
        )
    except ErroDePortaria as erro:
        return _render_painel(
            request,
            modal="chegou-encomenda",
            erro_encomenda=str(erro),
            status=400,
        )
    try:
        guardar_foto(acesso=acesso, arquivo=request.FILES.get("foto"))
    except ErroDeFoto as erro:
        messages.warning(request, str(erro))

    avisos, _sem_whatsapp = _avisos_da_entrega(acesso)
    if not avisos:
        messages.success(
            request, "Encomenda registrada. Sem WhatsApp autorizado nesta unidade."
        )
        return redirect(_destino("portaria:painel", condominio))
    if len(avisos) > 1:
        nomes = ", ".join(aviso["primeiro_nome"] for aviso in avisos)
        messages.success(
            request, f"Encomenda registrada. Avisar por WhatsApp: {nomes}."
        )
        return redirect(_destino("portaria:painel", condominio))
    aviso = avisos[0]
    messages.success(
        request,
        f"Encomenda registrada. Avisando {aviso['primeiro_nome']} pelo WhatsApp.",
    )
    return redirect(
        f"{_destino('portaria:painel', condominio)}"
        f"&avisar={acesso.pk}&pessoa={aviso['pessoa_id']}"
    )


def _unidade_da_encomenda(request, condominio):
    """A unidade escolhida para receber a encomenda, se for deste condomínio."""
    valor = (request.POST.get("unidade") or "").strip()
    if not _eh_uuid(valor):
        return None
    return Unidade.objects.filter(pk=valor, condominio=condominio).first()


def _transportadora_da_encomenda(request) -> str:
    """A transportadora escolhida nos botões, ou "Outra" quando não for uma delas."""
    escolhida = (request.POST.get("transportadora") or "").strip()
    for nome, _rotulo in TRANSPORTADORAS_DE_ENTREGA:
        if nome.casefold() == escolhida.casefold():
            return nome
    return TRANSPORTADORA_OUTRA


def _instrucao_da_encomenda(request) -> str:
    """O que fazer com a encomenda; sem escolha, ela fica na portaria."""
    escolhida = (request.POST.get("instrucao_entrega") or "").strip()
    if escolhida in InstrucaoEntrega.values:
        return escolhida
    return InstrucaoEntrega.DEIXAR_NA_PORTARIA


def _nome_da_encomenda(transportadora: str) -> str:
    """Como a encomenda aparece na lista: pela transportadora que a trouxe."""
    if transportadora == TRANSPORTADORA_OUTRA:
        return "Encomenda"
    return f"Entrega {transportadora}"


@login_required
@require_POST
def verificar(request, pk):
    condominio = _condominio_para_operar(request)
    autorizacao = _obter_ou_404(
        AutorizacaoPortaria.objects.select_related(
            "condominio", "pessoa", "unidade", "local", "veiculo", "autorizador_pessoa"
        ).filter(condominio=condominio),
        pk,
    )
    formulario = VerificarAutorizacaoForm(request.POST, condominio=condominio)
    if not formulario.is_valid():
        return _render_painel(
            request,
            formulario_verificacao=formulario,
            item_verificacao=autorizacao,
            modal="verificar",
            status=400,
        )
    try:
        autorizacao, acesso = verificar_autorizacao(
            autorizacao=autorizacao,
            usuario=request.user,
            dados=formulario.cleaned_data,
        )
    except ErroDePortaria as erro:
        formulario.add_error(None, str(erro))
        return _render_painel(
            request,
            formulario_verificacao=formulario,
            item_verificacao=autorizacao,
            modal="verificar",
            status=400,
        )
    messages.success(request, _recado_do_resultado(autorizacao, acesso))
    return redirect(_destino("portaria:painel", condominio))


@login_required
@require_POST
def chegou(request, pk):
    condominio = _condominio_para_operar(request)
    autorizacao = _obter_ou_404(
        AutorizacaoPortaria.objects.select_related(
            "condominio", "pessoa", "unidade", "local", "veiculo"
        ).filter(condominio=condominio),
        pk,
    )
    try:
        acesso = registrar_chegada(autorizacao=autorizacao, usuario=request.user)
        messages.success(request, _recado_de_entrada(acesso))
    except ErroDePortaria as erro:
        messages.error(request, str(erro))
    return redirect(_destino("portaria:painel", condominio))


@login_required
@require_POST
def saiu(request, pk):
    condominio = _condominio_para_operar(request)
    acesso = _obter_ou_404(
        AcessoPortaria.objects.select_related("condominio", "pessoa").filter(
            condominio=condominio
        ),
        pk,
    )
    try:
        acesso = registrar_saida(acesso=acesso, usuario=request.user)
        messages.success(request, _recado_de_saida(acesso))
    except ErroDePortaria as erro:
        messages.error(request, str(erro))
    return redirect(_destino("portaria:painel", condominio))


@login_required
@require_POST
def registrar_aviso(request, pk):
    """Anota que a portaria abriu o aviso de encomenda no WhatsApp do morador.

    O Eithra não envia nada: quem toca abre a conversa no próprio aparelho. O
    que se guarda é o fato de a portaria ter avisado — e quem avisou —, preso ao
    acesso, para que "o morador foi avisado?" tenha resposta depois.
    """
    condominio = _condominio_para_operar(request)
    acesso = _obter_ou_404(
        AcessoPortaria.objects.do_condominio(condominio), pk
    )
    pessoa = _obter_ou_404(
        Pessoa.objects.filter(condominio=condominio), request.POST.get("pessoa")
    )
    evento = registrar_aviso_whatsapp(
        acesso=acesso, usuario=request.user, nome=str(pessoa)
    )
    return JsonResponse(
        {"ok": True, "avisado_em": timezone.localtime(evento.data_hora).strftime("%H:%M")}
    )


@login_required
@require_POST
def ler_etiqueta(request):
    """Lê a etiqueta fotografada e propõe unidade, morador e transportadora.

    Não grava nada: devolve a sugestão para o porteiro conferir e preencher o
    registro de chegada. A leitura é local e determinística — nada é enviado
    para fora e nenhum modelo de linguagem participa.
    """
    condominio = _condominio_para_operar(request)
    try:
        conteudo, _mime, _extensao = validar_foto(request.FILES.get("foto"))
    except ErroDeFoto as erro:
        return JsonResponse({"erro": str(erro)}, status=400)

    if not etiqueta.ocr_disponivel():
        return JsonResponse(
            {"erro": "A leitura de etiquetas não está disponível neste servidor."},
            status=400,
        )
    try:
        dados = etiqueta.ler_etiqueta(conteudo, condominio)
    except Exception:  # noqa: BLE001 - a leitura é um limite: falha vira recusa humana
        return JsonResponse(
            {"erro": "Não foi possível ler a etiqueta. Digite os dados."}, status=400
        )
    return JsonResponse(dados)


@login_required
@require_POST
def interpretar_relato(request):
    """Lê o que a portaria escreveu e devolve a proposta — sem registrar nada.

    O relato é a única informação que vem do formulário. O condomínio continua
    sendo resolvido aqui, pela mesma permissão da Portaria inteira: quem não
    opera este condomínio não chega nem a esta leitura.
    """
    condominio = _condominio_para_operar(request)
    relato = _relato_do_pedido(request)
    return _render_painel(
        request,
        relato=relato,
        proposta=_proposta_do_relato(relato, condominio),
    )


@login_required
@require_POST
def confirmar_relato(request):
    """Executa a proposta que a portaria confirmou.

    Nada do que a tela mostrou é levado a crédito: o relato é reinterpretado
    aqui e a ação só acontece se continuar sendo a mesma leitura de agora. Só
    depois disso os services da Portaria são chamados.
    """
    condominio = _condominio_para_operar(request)
    relato = _relato_do_pedido(request)
    escolha = (request.POST.get("acao") or "").strip()
    intencao = interpretacao.interpretar(relato, condominio=condominio)

    if escolha not in ROTULOS_DE_ACAO or _escolha_executavel(intencao) != escolha:
        messages.error(
            request, "O relato mudou desde a leitura. Diga de novo o que aconteceu."
        )
        return _render_painel(
            request, relato=relato, proposta=_proposta(intencao, relato)
        )

    try:
        recado = _executar_a_intencao(
            intencao,
            escolha,
            condominio,
            request.user,
            relato=relato,
        )
    except ErroDePortaria as erro:
        messages.error(request, str(erro))
        return _render_painel(
            request, relato=relato, proposta=_proposta(intencao, relato)
        )

    messages.success(request, recado)
    return redirect(_destino("portaria:painel", condominio))


@login_required
@require_POST
def transcrever_audio(request):
    """Transforma a fala da portaria em texto — e para aí.

    Esta é a única porta de entrada de áudio da Portaria, e ela não sabe nada
    sobre chegada, saída, autorização ou presença: recebe o recado, devolve a
    transcrição e descarta o áudio. Interpretar continua sendo o passo seguinte,
    no mesmo caminho de texto de sempre — e quem decide quando é a pessoa,
    revendo o que foi transcrito e tocando em "Entender". O áudio não executa
    nada por conta própria.

    O arquivo não é gravado em lugar nenhum: vive na memória deste pedido e morre
    com ele. Nem o áudio nem a transcrição entram em log.
    """
    condominio = _condominio_para_operar(request)
    arquivo = request.FILES.get("audio")
    resultado = audio.transcrever(
        audio.PedidoDeAudio(
            conteudo=_conteudo_do_audio(arquivo),
            nome_do_arquivo=getattr(arquivo, "name", ""),
            content_type=getattr(arquivo, "content_type", ""),
            condominio=condominio,
            usuario=request.user,
        )
    )
    # Um recado que não pôde ser transcrito não é falha do sistema: é uma
    # situação comum, com frase pronta para a tela. Por isso a resposta é
    # sempre a mesma — ou vem o texto, ou vem o que dizer sobre ele.
    if resultado.obtida:
        return JsonResponse({"texto": resultado.texto})
    return JsonResponse({"mensagem": resultado.erro})


def _conteudo_do_audio(arquivo) -> bytes:
    """Lê o áudio sem trazer mais para a memória do que o necessário.

    A leitura para um byte além do teto: se o arquivo for maior que o aceito, o
    núcleo recebe o suficiente para recusá-lo e o resto nunca é carregado. Quem
    decide o limite continua sendo o núcleo de áudio — aqui só se evita segurar
    um arquivo inteiro na memória antes de ouvir a resposta dele.
    """
    if arquivo is None:
        return b""
    teto = int(
        getattr(settings, "EITHRA_AUDIO_MAX_BYTES", audio.LIMITE_DE_BYTES_PADRAO)
    )
    return arquivo.read(teto + 1)


# ---------------------------------------------------------------------------
# Relato conversacional
# ---------------------------------------------------------------------------

# O relato é texto livre; o espaço vazio dele convida a dizer o que aconteceu.
PLACEHOLDER_DO_RELATO = "Conte o que está acontecendo... Ex.: o elevador parou, há água na garagem ou chegou um visitante para o 902."

# O que a proposta pode executar nesta rodada. Cada chave é a ação conceitual
# que o núcleo de interpretação já devolve; nenhuma delas é inventada aqui.
CHAVES_DE_ACAO = {
    "chegada": interpretacao.ACAO_DE_CHEGADA,
    "saida": interpretacao.ACAO_DE_SAIDA,
    "ocorrencia": interpretacao.ACAO_DE_OCORRENCIA,
}

ROTULOS_DE_ACAO = {
    "chegada": "Registrar chegada",
    "saida": "Registrar saída",
    "ocorrencia": "Criar demanda",
}


def _relato_do_pedido(request) -> str:
    """O relato desta rodada, já com a resposta curta da pergunta anterior.

    Quem responde "902" a "Para qual unidade?" não reescreve o relato: a
    resposta é acrescentada à frente do texto original, porque é a mesma frase
    dita em dois tempos. Nada é guardado entre um envio e outro.
    """
    relato = (request.POST.get("relato") or "").strip()
    respondendo = (request.POST.get("respondendo") or "").strip()
    if not respondendo:
        return relato
    if not relato:
        return respondendo
    return f"{relato} {respondendo}"


def _proposta_do_relato(relato, condominio):
    return _proposta(interpretacao.interpretar(relato, condominio=condominio), relato)


def _proposta(intencao, relato) -> dict:
    """O que a portaria escreveu, dito de volta de forma humana.

    A tela mostra a leitura e, quando ela já sustenta um registro, a única ação
    que a resolve. Não há estado, confiança nem identificador aqui: quem lê é
    quem está na portaria, e o que ele precisa saber é o que o Eithra entendeu.
    """
    pergunta = intencao.perguntas[0] if intencao.perguntas else ""
    escolha = _escolha_executavel(intencao)
    proposta = {
        "entendeu": intencao.tipo != IntencaoPortaria.INSUFICIENTE,
        "titulo": _titulo_da_leitura(intencao),
        "contexto": _contexto_da_leitura(intencao),
        "detalhe": _detalhe_da_leitura(intencao),
        "pergunta": pergunta,
        "nota": _nota_da_leitura(intencao),
        "aviso": _aviso_da_leitura(intencao, escolha),
        "acao": None,
        "campo": relato,
        "placeholder": PLACEHOLDER_DO_RELATO,
        "respondendo": "",
    }
    if escolha is not None:
        proposta["acao"] = {"chave": escolha, "rotulo": ROTULOS_DE_ACAO[escolha]}
    elif pergunta:
        # Enquanto falta informação, o campo se esvazia e passa a perguntar: a
        # resposta volta completando o relato original, sem histórico.
        proposta["campo"] = ""
        proposta["placeholder"] = pergunta
        proposta["respondendo"] = relato
    return proposta


def _escolha_executavel(intencao):
    """Qual ação desta leitura já pode ser registrada — e nenhuma no escuro.

    Chegada e saída são as únicas consequências desta rodada. Uma chegada só é
    executável quando a portaria consegue nomear quem chegou e dizer para onde
    ele vai; sem um dos dois, o registro seria um chute.
    """
    if (
        intencao.tipo == IntencaoPortaria.RELATO_DE_PROBLEMA
        and interpretacao.ACAO_DE_OCORRENCIA in intencao.acoes
    ):
        return "ocorrencia"

    if interpretacao.ACAO_DE_SAIDA in intencao.acoes:
        acesso = intencao.entidade("acesso")
        if acesso is not None and acesso.confirmada and acesso.objeto is not None:
            return "saida"
        return None

    if interpretacao.ACAO_DE_CHEGADA not in intencao.acoes:
        return None

    esperado = intencao.entidade("esperado")
    if esperado is not None and esperado.existe and not esperado.confirmada:
        # Duas visitas esperadas respondem à mesma fala: quem escolhe é a
        # portaria, pela lista de esperados.
        return None
    if esperado is not None and esperado.confirmada and esperado.objeto is not None:
        return "chegada"

    pessoa = intencao.entidade("pessoa")
    empresa = intencao.entidade("empresa")
    if not ((pessoa is not None and pessoa.existe) or (empresa is not None and empresa.existe)):
        return None

    unidade = intencao.entidade("unidade")
    if unidade is not None and unidade.confirmada and unidade.objeto is not None:
        return "chegada"
    if _local_da_chegada(intencao) is not None:
        return "chegada"
    return None


def _titulo_da_leitura(intencao) -> str:
    pessoa = intencao.entidade("pessoa")
    empresa = intencao.entidade("empresa")
    acesso = intencao.entidade("acesso")
    if pessoa is not None and pessoa.existe:
        return pessoa.texto
    if empresa is not None and empresa.existe:
        return empresa.texto
    if acesso is not None and acesso.existe:
        return acesso.rotulo
    if intencao.tipo == IntencaoPortaria.RELATO_DE_PROBLEMA:
        return "Problema relatado"
    if intencao.tipo == IntencaoPortaria.AUTORIZACAO:
        return "Autorização"
    return "Não entendi"


def _contexto_da_leitura(intencao) -> str:
    pedacos: list[str] = []
    tipo = intencao.entidade("tipo_acesso")
    if tipo is not None and tipo.existe:
        pedacos.append(tipo.texto)
    destino = _destino_da_leitura(intencao)
    if destino:
        pedacos.append(destino)
    servico = intencao.entidade("servico")
    if servico is not None and servico.existe:
        pedacos.append(f"serviço {servico.texto}")
    return " · ".join(pedacos)


def _destino_da_leitura(intencao) -> str:
    unidade = intencao.entidade("unidade")
    if unidade is not None and unidade.existe:
        rotulo = getattr(unidade.objeto, "rotulo", "") if unidade.objeto else ""
        return rotulo or f"Unidade {unidade.texto}"
    esperado = intencao.entidade("esperado")
    if esperado is not None and esperado.confirmada and esperado.objeto is not None:
        return esperado.objeto.destino_para_exibir
    contexto = intencao.entidade("contexto")
    if contexto is not None and contexto.valor in (
        ContextoAcessoPortaria.PRESTADOR_DO_CONDOMINIO.value,
        ContextoAcessoPortaria.FORNECEDOR_DO_CONDOMINIO.value,
    ):
        return "Condomínio"
    return ""


def _detalhe_da_leitura(intencao) -> str:
    pedacos: list[str] = []
    esperado = intencao.entidade("esperado")
    if esperado is not None and esperado.confirmada and esperado.objeto is not None:
        pedacos.append("Já esperado na portaria")

    autorizador = intencao.entidade("autorizador")
    meio = intencao.entidade("meio")
    if autorizador is not None and autorizador.existe:
        frase = f"Autorizado por {autorizador.texto}"
        if meio is not None and meio.existe:
            frase += f" · {meio.texto}"
        pedacos.append(frase)

    status = intencao.entidade("status")
    if status is not None and status.existe:
        pedacos.append(status.texto)
    return " · ".join(pedacos)


def _nota_da_leitura(intencao) -> str:
    """O que a fala trouxe além da operação desta rodada."""
    if interpretacao.ACAO_DE_OCORRENCIA not in intencao.acoes:
        return ""
    if intencao.tipo == IntencaoPortaria.RELATO_DE_PROBLEMA:
        return "Registrar ocorrência ainda não está disponível por aqui."
    return (
        "Há também um problema relatado nesta fala. "
        "Registrar ocorrência ainda não está disponível por aqui."
    )


def _aviso_da_leitura(intencao, escolha) -> str:
    if escolha is not None or intencao.perguntas:
        return ""
    if intencao.tipo == IntencaoPortaria.AUTORIZACAO:
        return "Registrar autorização ainda não está disponível por aqui."
    if intencao.tipo == IntencaoPortaria.RELATO_DE_PROBLEMA:
        return ""
    esperado = intencao.entidade("esperado")
    if esperado is not None and esperado.existe and not esperado.confirmada:
        return "Mais de uma visita esperada corresponde a esta fala."
    if intencao.tipo == IntencaoPortaria.CHEGADA:
        return "Para registrar, diga a unidade ou a área comum desta chegada."
    return ""


def _local_da_chegada(intencao):
    """A área comum do condomínio que o serviço citado aponta, quando há uma."""
    servico = intencao.entidade("servico")
    if servico is None or not servico.existe:
        return None
    objeto = servico.objeto
    if isinstance(objeto, LocalOperacional):
        return objeto
    local = getattr(objeto, "local", None)
    if local is not None and getattr(local, "pk", None):
        return local
    return None


def _executar_a_intencao(
    intencao, escolha, condominio, usuario, *, relato=""
) -> str:
    if escolha == "saida":
        return _recado_de_saida(
            registrar_saida(acesso=intencao.entidade("acesso").objeto, usuario=usuario)
        )
    if escolha == "ocorrencia":
        return _executar_demanda_da_portaria(
            relato=relato,
            condominio=condominio,
            usuario=usuario,
        )
    return _executar_chegada(intencao, condominio, usuario)


def _executar_demanda_da_portaria(*, relato, condominio, usuario) -> str:
    """Cria pelo domínio de Demandas o problema confirmado na Portaria.

    A Portaria apenas reconhece que o relato é um problema. Título,
    classificação, prioridade, local e equipamento continuam sendo lidos
    pelo domínio canônico de Demandas.
    """
    from apps.demandas import interpretacao as interpretacao_demanda
    from apps.demandas.constants import OrigemDemanda
    from apps.demandas.services import criar_demanda

    leitura = interpretacao_demanda.interpretar(
        relato,
        condominio=condominio,
    )

    titulo = (getattr(leitura, "assunto", "") or relato).strip()[:150]
    if not titulo:
        raise ErroDePortaria(
            "Não consegui identificar o problema para criar a demanda."
        )

    local = getattr(leitura, "local", None)

    equipamento = getattr(leitura, "equipamento", None)
    if equipamento is not None and not getattr(equipamento, "pk", None):
        equipamento = getattr(equipamento, "objeto", None)
    if equipamento is not None and not getattr(equipamento, "pk", None):
        equipamento = None

    tipo_problema = getattr(
        getattr(leitura, "tipo_problema", None), "valor", None
    )
    impacto = getattr(
        getattr(leitura, "impacto", None), "valor", None
    )
    risco = getattr(
        getattr(leitura, "risco", None), "valor", None
    )

    registro = getattr(leitura, "registro", None)
    classificacao_sugerida = (
        registro() if callable(registro) else None
    )

    demanda = criar_demanda(
        condominio=condominio,
        criado_por=usuario,
        titulo=titulo,
        descricao=relato,
        origem=OrigemDemanda.PORTARIA,
        prioridade=getattr(leitura, "prioridade", None) or "normal",
        local=local,
        equipamento=equipamento,
        tipo_problema=tipo_problema,
        impacto=impacto,
        risco=risco,
        classificacao_sugerida=classificacao_sugerida,
    )

    return f"Demanda criada pela Portaria: {demanda.titulo}."


def _executar_chegada(intencao, condominio, usuario) -> str:
    esperado = intencao.entidade("esperado")
    if esperado is not None and esperado.confirmada and esperado.objeto is not None:
        # A visita já estava autorizada: a chegada cumpre o que a portaria
        # esperava, pela mesma operação que a lista de esperados usa.
        return _recado_de_entrada(
            registrar_chegada(autorizacao=esperado.objeto, usuario=usuario)
        )
    autorizacao, acesso = registrar_chegada_com_resultado(
        condominio=condominio,
        usuario=usuario,
        dados=_dados_da_chegada(intencao),
    )
    return _recado_do_resultado(autorizacao, acesso)


def _dados_da_chegada(intencao) -> dict:
    """O que a leitura já sustenta, no formato que a Portaria sempre aceitou."""
    pessoa = intencao.entidade("pessoa")
    empresa = intencao.entidade("empresa")
    unidade = intencao.entidade("unidade")
    tipo = intencao.entidade("tipo_acesso")
    autorizador = intencao.entidade("autorizador")
    meio = intencao.entidade("meio")
    status = intencao.entidade("status")

    nome = ""
    if pessoa is not None and pessoa.existe:
        nome = pessoa.texto
    elif empresa is not None and empresa.existe:
        nome = empresa.texto

    dados = {
        "nome": nome,
        "tipo_acesso": (
            tipo.valor
            if tipo is not None and tipo.existe
            else TipoAcessoPortaria.VISITANTE.value
        ),
        "empresa": empresa.texto if empresa is not None and empresa.existe else "",
        "meio_autorizacao": meio.valor if meio is not None and meio.existe else "",
        "unidade": unidade.objeto if unidade is not None and unidade.confirmada else None,
        "local": _local_da_chegada(intencao),
        # Confirmar a entrada é o próprio ato de autorizar quem está na frente
        # do porteiro; só um resultado já dito em contrário muda isso.
        "status": StatusAutorizacaoPortaria.AUTORIZADO,
    }
    if autorizador is not None and autorizador.existe:
        if autorizador.confirmada and autorizador.objeto is not None:
            dados["autorizador_pessoa"] = autorizador.objeto
        else:
            dados["autorizador_nome"] = autorizador.texto
    if status is not None and status.existe:
        dados["status"] = status.valor
    return dados


ABAS_DA_PORTARIA = ("painel", "historico")

# Quantos dias ainda contam como "próximas" antes de a autorização sair da fila
# de espera: a diferença entre o que se prepara nesta semana e a agenda distante.
DIAS_DE_PROXIMAS = 7

# O ícone que acompanha o tipo de quem chega. Quem diz o que é continua sendo o
# texto; a marca só ajuda o olho a reconhecer a linha de longe.
ICONES_DE_TIPO = {
    TipoAcessoPortaria.VISITANTE: "bx-user",
    TipoAcessoPortaria.PRESTADOR: "bx-wrench",
    TipoAcessoPortaria.FORNECEDOR: "bx-buildings",
    TipoAcessoPortaria.TECNICO: "bx-cog",
}


def _aba(request) -> str:
    escolhida = (request.GET.get("aba") or "").strip()
    return escolhida if escolhida in ABAS_DA_PORTARIA else "painel"


def _render_painel(
    request,
    *,
    formulario_esperado=None,
    formulario_chegada=None,
    formulario_verificacao=None,
    item_verificacao=None,
    modal=None,
    relato="",
    proposta=None,
    erro_encomenda="",
    status=200,
):
    """Monta a Portaria operacional, com histórico separado."""
    base = _base_da_pagina(request)
    atual = base["condominio_atual"]
    if item_verificacao is None and modal != "verificar":
        item_verificacao = _autorizacao_em_verificacao(request, atual)
        if item_verificacao is not None:
            modal = "verificar"
    # Enquanto a leitura espera uma resposta, o campo volta vazio e pergunta:
    # é a própria pergunta que ocupa o lugar do relato, sem histórico.
    campo_do_relato = relato
    placeholder_do_relato = PLACEHOLDER_DO_RELATO
    respondendo = ""
    if proposta is not None:
        campo_do_relato = proposta["campo"]
        placeholder_do_relato = proposta["placeholder"]
        respondendo = proposta["respondendo"]
    contexto = {
        **base,
        "formulario_esperado": formulario_esperado
        or AutorizacaoPortariaForm(condominio=atual),
        "formulario_chegada": formulario_chegada
        or ChegadaNaoPrevistaForm(condominio=atual),
        "passo_chegada": _passo_da_chegada(formulario_chegada),
        "passo_esperado": _passo_do_esperado(formulario_esperado),
        "formulario_verificacao": formulario_verificacao
        or (
            VerificarAutorizacaoForm(
                condominio=atual,
                initial=_iniciais_da_verificacao(item_verificacao),
            )
            if item_verificacao
            else None
        ),
        "item_verificacao": item_verificacao,
        "autorizador_unidade": _unidade_escolhida(formulario_chegada, atual),
        "autorizador_unidade_esperado": _unidade_escolhida(formulario_esperado, atual),
        "empresas_conhecidas": empresas_conhecidas(atual),
        # A planta do condomínio para escolher o destino por clique. Sai do
        # cadastro de unidades que já existe e nunca atravessa condomínio.
        "estrutura_unidades": _estrutura_de_unidades(atual),
        # Quem responde por cada unidade, para a autorização ser um toque no
        # nome de quem atendeu o interfone. Mesma origem do cadastro real: sem
        # morador vinculado, a unidade não oferece ninguém para autorizar.
        "moradores_por_unidade": _moradores_por_unidade(atual),
        # As áreas comuns que existem no condomínio. São destino de acesso, como
        # a unidade — e nunca uma lista inventada pela tela.
        "areas_comuns": _areas_comuns(atual),
        "modal_aberto": modal,
        "relato": relato,
        "proposta": proposta,
        # Os tipos que são entrega — e não pessoa. A tela usa esta lista para
        # saber quando oferecer a leitura da etiqueta; a origem é única.
        "tipos_de_entrega": TIPOS_DE_ENTREGA,
        # A encomenda em uma etapa: as transportadoras em botões e as instruções
        # de entrega vêm do domínio, não de uma lista escrita na tela.
        "transportadoras": TRANSPORTADORAS_DE_ENTREGA,
        "instrucoes_de_entrega": InstrucaoEntrega.choices,
        "erro_encomenda": erro_encomenda,
        # A encomenda acabou de ser registrada e há um morador a avisar: o
        # painel volta com o toque pronto. O JS abre o mesmo aviso da linha da
        # entrega, para que o histórico registre quem foi avisado.
        "aviso_a_abrir": _aviso_a_abrir(request),
        "campo_do_relato": campo_do_relato,
        "placeholder_do_relato": placeholder_do_relato,
        "respondendo": respondendo,
        # Voz é atalho, não caminho: sem serviço de transcrição configurado o
        # microfone nem aparece, e o relato por texto segue inteiro.
        "voz_disponivel": audio.disponivel(),
        # Um endereço só de transcrição viaja com a tela: o relato, o destino
        # da chegada e o nome de quem chegou ditam no mesmo serviço, pelo mesmo
        # controlador. Não há um segundo motor de voz para a Portaria.
        #
        # O nome é "url_da_transcricao" e não "voz_url" de propósito: o campo
        # compartilhado (``cadastros/_campo.html``) liga o microfone a partir de
        # um ``voz_url`` recebido na inclusão. Como a inclusão herda o contexto
        # da página, um ``voz_url`` solto aqui poria microfone em todo campo da
        # tela. Com outro nome, só dita o campo que pedir a voz explicitamente.
        "url_da_transcricao": reverse("portaria:transcrever")
        if audio.disponivel()
        else "",
        # A retirada rápida só aparece para quem alcança o estoque deste
        # condomínio — a Portaria é onde o material sai com mais frequência.
        "pode_retirar_material": pode_ver_estoque(request.user, atual),
    }
    if base["aba"] == "historico":
        contexto.update(_historico(request, atual))
    else:
        contexto.update(_painel_operacional(atual, base["agora"]))
    return render(request, "pages/portaria/painel.html", contexto, status=status)


def _chave_natural(valor):
    """Ordena "1, 2, 10" como o condomínio lê, não como o texto é escrito.

    Andar e bloco são texto livre: quando o que está escrito é número, ele
    ordena como número; o resto ordena alfabeticamente. Sem isso, o andar 10
    apareceria antes do andar 2.
    """
    texto = (valor or "").strip()
    if texto.isdigit():
        return (0, int(texto), "")
    return (1, 0, texto.lower())


def _chave_do_andar(valor):
    """O prédio se lê de cima para baixo: 16º, 15º, …, 2º, 1º.

    Andar sem número — o que o cadastro ainda não informou — vai para o fim e
    sem rótulo próprio: o mapa não inventa andar nenhum nem escreve o que não
    está no cadastro.
    """
    texto = (valor or "").strip()
    if texto.isdigit():
        return (0, -int(texto), "")
    return (1, 0, texto.lower())


def _rotulo_do_andar(valor):
    """Como o andar aparece no prédio: "9º" — e nada quando não há andar."""
    texto = (valor or "").strip()
    if not texto:
        return ""
    return f"{texto}º" if texto.isdigit() else texto


def _estrutura_de_unidades(condominio):
    """A planta do condomínio para escolher o destino por clique.

    Bloco/Torre → Andar → Unidade, montada do cadastro que já existe
    (bloco_torre, andar, identificador). É o prédio de pé: os andares reais na
    ordem em que se sobe — 16º, 15º, …, 1º — e as unidades de cada andar lado a
    lado, como as plantas as mostram. Só a unidade ativa do próprio condomínio
    aparece.

    Condomínio sem bloco/torre não ganha um agrupamento inventado: a lista sai
    com um bloco só, sem nome, e a tela mostra direto os andares.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return []
    unidades = (
        Unidade.objects.do_condominio(condominio)
        .filter(ativa=True)
        .order_by("bloco_torre", "andar", "identificador")
    )
    blocos = {}
    for unidade in unidades:
        blocos.setdefault(unidade.bloco_torre or "", {}).setdefault(
            unidade.andar or "", []
        ).append(unidade)
    return [
        {
            "nome": bloco,
            "andares": [
                {
                    "nome": andar,
                    "rotulo": _rotulo_do_andar(andar),
                    "unidades": [
                        {
                            "pk": str(unidade.pk),
                            "identificador": unidade.identificador,
                            "rotulo": unidade.rotulo,
                        }
                        for unidade in lista
                    ],
                }
                for andar, lista in sorted(
                    andares.items(), key=lambda item: _chave_do_andar(item[0])
                )
            ],
        }
        for bloco, andares in sorted(
            blocos.items(), key=lambda item: _chave_natural(item[0])
        )
    ]


def _moradores_por_unidade(condominio):
    """Quem responde por cada unidade hoje, indexado pela unidade.

    A autorização da portaria acontece no interfone: quem atende é quem mora
    ali. Esta é a lista que a tela oferece como "Autorizado por", e ela sai do
    vínculo vigente — nunca de um cadastro paralelo. Unidade sem morador
    vinculado simplesmente não oferece ninguém, e a portaria escreve o nome à
    mão quando precisar.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return {}
    vinculos = (
        VinculoPessoaUnidade.objects.do_condominio(condominio)
        .vigentes()
        .filter(tipo_vinculo__in=TIPOS_DE_RESIDENTE)
        .select_related("pessoa")
        .order_by("pessoa__nome")
    )
    moradores: dict[str, list[dict]] = {}
    vistos: set[tuple[str, str]] = set()
    for vinculo in vinculos:
        chave = (str(vinculo.unidade_id), str(vinculo.pessoa_id))
        if chave in vistos:
            continue
        vistos.add(chave)
        moradores.setdefault(str(vinculo.unidade_id), []).append(
            {"pk": str(vinculo.pessoa_id), "nome": str(vinculo.pessoa)}
        )
    return moradores


def _areas_comuns(condominio):
    """As áreas comuns do condomínio, como destino de um acesso.

    Piscina, salão, academia, garagem: o que a gestão cadastrou como local
    operacional. A tela mostra exatamente isto — nem um local a mais.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return []
    return [
        {"pk": str(local.pk), "nome": local.nome}
        for local in LocalOperacional.objects.do_condominio(condominio)
        .ativos()
        .order_by("nome")
    ]


def _painel_operacional(condominio, agora):
    """O que a portaria decide agora: quem chega e quem sai.

    São duas decisões, e cada uma tem seu lugar: "Chegando" reúne quem ainda
    não entrou e oferece o toque que registra a entrada; "Aqui agora" reúne a
    presença aberta e oferece o toque que registra a saída. Nada mais compete
    com isso — quem procura alguém usa a busca, e o que já aconteceu é assunto
    do histórico.
    """
    autorizacoes = _autorizacoes_operacionais(condominio, agora)
    acessos_abertos = list(_acessos_operacionais(condominio).order_by("-entrada_em"))
    inicio_do_turno = _inicio_do_turno(agora)

    chegando = [
        _linha_de_autorizacao(autorizacao, agora) for autorizacao in autorizacoes
    ]
    avisados = _avisos_registrados(acessos_abertos)
    aqui_agora = []
    for acesso in acessos_abertos:
        # Quem entrou antes do turno de hoje continua dentro: a lista de saída
        # precisa saber disso para a presença antiga não passar por chegada.
        _descrever_presenca(acesso, agora, inicio_do_turno)
        aqui_agora.append(_linha_de_acesso(acesso, avisados.get(acesso.pk, "")))

    acessos_do_dia = AcessoPortaria.objects.do_condominio(condominio).filter(
        entrada_em__date=agora.date()
    ).count()
    return {
        "chegando": chegando,
        "aqui_agora": aqui_agora,
        # Indicador é leitura de relance, não painel: os três números cabem em
        # blocos compactos, com ícone e cor de apoio, sem disputar espaço com a
        # operação. Chegando e Aqui agora usam o teal estrutural; Hoje fica no
        # lilás de detalhe, como nas referências.
        "resumo": [
            {
                "rotulo": "Chegando",
                "valor": len(chegando),
                "icone": "bx-user-plus",
                "tom": "teal",
            },
            {
                "rotulo": "Aqui agora",
                "valor": len(aqui_agora),
                "icone": "bx-group",
                "tom": "teal",
            },
            {
                "rotulo": "Hoje",
                "valor": acessos_do_dia,
                "icone": "bx-calendar",
                "tom": "lilas",
            },
        ],
    }


def _autorizacoes_operacionais(condominio, agora):
    """Autorizações ainda relevantes para a operação de agora."""
    return (
        AutorizacaoPortaria.objects.do_condominio(condominio)
        .select_related(
            "pessoa",
            "unidade",
            "local",
            "veiculo",
            "autorizador_pessoa",
        )
        .exclude(acessos__id__isnull=False)
        .filter(
            Q(status=StatusAutorizacaoPortaria.AUTORIZADO, fim_previsto__date__gte=agora.date())
            | Q(
                status__in=[
                    StatusAutorizacaoPortaria.AGUARDANDO,
                    StatusAutorizacaoPortaria.NAO_AUTORIZADO,
                    StatusAutorizacaoPortaria.SEM_RESPOSTA,
                ],
                criado_em__date=agora.date(),
            )
        )
        .order_by("-resultado_registrado_em", "-inicio_previsto", "-criado_em")
    )


def _acessos_operacionais(condominio):
    """Presenças que continuam dentro agora."""
    return (
        AcessoPortaria.objects.do_condominio(condominio)
        .abertas()
        .select_related(
            "condominio", "pessoa", "unidade", "local", "veiculo", "autorizacao"
        )
    )


def _linha_de_autorizacao(autorizacao, agora):
    situacao = autorizacao.situacao_para_exibir
    tom = {
        "Aguardando confirmação": "warning",
        "Autorizado": "success",
        "Não autorizado": "danger",
        "Sem resposta": "secondary",
    }.get(situacao, "secondary")
    autorizacao.iniciais = _iniciais(autorizacao.nome_para_exibir)
    momento = (
        autorizacao.resultado_registrado_em
        or autorizacao.inicio_previsto
        or autorizacao.criado_em
    )
    contexto = autorizacao.destino_para_exibir
    if autorizacao.autorizador_para_exibir:
        contexto = f"{contexto} · {autorizacao.autorizador_para_exibir}"
    return {
        "kind": "autorizacao",
        "pk": autorizacao.pk,
        "momento": momento,
        "iniciais": autorizacao.iniciais,
        "nome": autorizacao.nome_para_exibir,
        "tipo": autorizacao.get_tipo_acesso_display(),
        "destino": autorizacao.destino_para_exibir,
        "horario": timezone.localtime(momento).strftime("%H:%M"),
        "situacao": situacao,
        "tom": tom,
        "contexto": contexto,
        "contexto_secundario": _contexto_da_autorizacao(autorizacao, agora),
        "acao_label": "Chegou"
        if autorizacao.status == StatusAutorizacaoPortaria.AUTORIZADO
        else "Verificar",
        "acao_url": reverse("portaria:chegou", args=[autorizacao.pk])
        if autorizacao.status == StatusAutorizacaoPortaria.AUTORIZADO
        else f"{reverse('portaria:painel')}?condominio={autorizacao.condominio_id}&abrir=verificar&item={autorizacao.pk}",
        "acao_post": autorizacao.status == StatusAutorizacaoPortaria.AUTORIZADO,
    }


def _primeiro_nome(nome: str) -> str:
    """Como a portaria chama a pessoa: só o primeiro nome."""
    partes = [parte for parte in (nome or "").split() if parte]
    return partes[0] if partes else ""


def _moradores_a_avisar(acesso):
    """Quem, nesta unidade, autorizou ser avisado por WhatsApp.

    Só entra quem tem número E autorização: sem qualquer um dos dois, a portaria
    não oferece o aviso — uma encomenda não vira uma mensagem que ninguém pediu.
    """
    if not acesso.unidade_id:
        return []
    return [
        pessoa
        for pessoa in acesso.unidade.pessoas_atuais()
        if pessoa.ativa and pessoa.aceita_avisos_whatsapp and pessoa.whatsapp
    ]


def _mensagem_do_aviso(acesso, pessoa) -> str:
    """O recado que a portaria manda ao morador quando a encomenda chega."""
    primeiro = _primeiro_nome(str(pessoa))
    hora = timezone.localtime(acesso.entrada_em).strftime("%H:%M")
    texto = (
        f"📦 Olá, {primeiro}! Chegou uma encomenda para o apto "
        f"{acesso.destino_para_exibir}"
    )
    transportadora = (acesso.empresa or "").strip()
    if transportadora:
        texto += f", {transportadora}"
    texto += f" às {hora}."
    if acesso.instrucao_entrega:
        texto += f" {acesso.get_instrucao_entrega_display()}."
    return f"{texto} Portaria {acesso.condominio.nome}."


def _link_do_aviso(acesso, pessoa) -> str:
    """O endereço wa.me que abre a conversa com a mensagem já escrita."""
    texto = quote(_mensagem_do_aviso(acesso, pessoa), safe="")
    return f"https://wa.me/55{pessoa.whatsapp}?text={texto}"


def _avisos_da_entrega(acesso):
    """O que a linha da entrega oferece: quem avisar e se ninguém pode receber."""
    if acesso.tipo_acesso not in TIPOS_DE_ENTREGA:
        return [], False
    pessoas = _moradores_a_avisar(acesso)
    if not pessoas:
        return [], bool(acesso.unidade_id)
    avisos = [
        {
            "pessoa_id": str(pessoa.pk),
            "nome": str(pessoa),
            "primeiro_nome": _primeiro_nome(str(pessoa)),
            "link": _link_do_aviso(acesso, pessoa),
        }
        for pessoa in pessoas
    ]
    return avisos, False


def _aviso_a_abrir(request):
    """O aviso que o painel deve abrir sozinho depois de registrar a encomenda.

    A tela só volta com esta marca quando a encomenda acabou de ser registrada
    e há um morador elegível: o resto do tempo o painel é só o painel, e abrir
    uma conversa de WhatsApp por conta própria seria um susto, não um atalho.
    """
    acesso = (request.GET.get("avisar") or "").strip()
    pessoa = (request.GET.get("pessoa") or "").strip()
    if not _eh_uuid(acesso) or not _eh_uuid(pessoa):
        return None
    return {"acesso": acesso, "pessoa": pessoa}


def _avisos_registrados(acessos) -> dict:
    """Quando cada acesso teve o aviso aberto, para a linha dizer "avisado às"."""
    ids = [acesso.pk for acesso in acessos]
    if not ids:
        return {}
    eventos = HistoricoAcessoPortaria.objects.filter(
        acesso_id__in=ids, evento=EventoAcessoPortaria.AVISO_WHATSAPP
    ).order_by("data_hora")
    ultimos = {}
    for evento in eventos:
        ultimos[evento.acesso_id] = timezone.localtime(evento.data_hora).strftime(
            "%H:%M"
        )
    return ultimos


def _linha_de_acesso(acesso, avisado_em=""):
    avisos, sem_whatsapp = _avisos_da_entrega(acesso)
    return {
        "kind": "acesso",
        "pk": acesso.pk,
        "momento": acesso.entrada_em,
        "iniciais": acesso.iniciais,
        "nome": acesso.nome_para_exibir,
        "tipo": acesso.get_tipo_acesso_display(),
        "destino": acesso.destino_para_exibir,
        "horario": timezone.localtime(acesso.entrada_em).strftime("%H:%M"),
        "situacao": "Dentro",
        "tom": "success",
        "contexto": acesso.destino_para_exibir,
        "contexto_secundario": acesso.entrada_humana,
        # Quem entrou antes do turno de hoje continua dentro e não é novidade
        # nenhuma: a tela precisa dizer isso, senão a presença antiga passa por
        # chegada recente.
        "turno_anterior": getattr(acesso, "do_turno_anterior", False),
        # A entrega oferece o aviso por WhatsApp ao morador que o autorizou; sem
        # ninguém elegível, a linha diz isso em vez de esconder o fato.
        "avisos": avisos,
        "sem_whatsapp": sem_whatsapp,
        "avisado_em": avisado_em,
        "acao_label": "Saiu",
        "acao_url": reverse("portaria:saiu", args=[acesso.pk]),
        "acao_post": True,
    }


def _contexto_da_autorizacao(autorizacao, agora):
    if autorizacao.status == StatusAutorizacaoPortaria.AUTORIZADO:
        return _quando(
            autorizacao.resultado_registrado_em or autorizacao.criado_em,
            agora,
            "autorizado",
        )
    if autorizacao.status == StatusAutorizacaoPortaria.NAO_AUTORIZADO:
        return _quando(
            autorizacao.resultado_registrado_em or autorizacao.criado_em,
            agora,
            "negado",
        )
    if autorizacao.status == StatusAutorizacaoPortaria.SEM_RESPOSTA:
        return _quando(
            autorizacao.resultado_registrado_em or autorizacao.criado_em,
            agora,
            "sem resposta",
        )
    return _quando(autorizacao.criado_em, agora, "aguardando desde")


def _historico(request, condominio):
    """O que já aconteceu, para consulta — não para operação.

    Entram os acessos encerrados: enquanto alguém continua dentro, ele é
    assunto da aba Agora. Aqui a pergunta é outra — "quem passou por aqui
    naquele dia?" —, e a resposta se procura por nome, unidade, placa ou data.
    """
    termo = (request.GET.get("q") or "").strip()
    de = parse_date((request.GET.get("de") or "").strip())
    ate = parse_date((request.GET.get("ate") or "").strip())
    registros = (
        AcessoPortaria.objects.do_condominio(condominio)
        .exclude(saida_em__isnull=True)
        .select_related("pessoa", "unidade", "local", "veiculo", "autorizacao")
    )
    if termo:
        registros = registros.buscar(termo)
    if de:
        registros = registros.filter(entrada_em__date__gte=de)
    if ate:
        registros = registros.filter(entrada_em__date__lte=ate)

    pagina = paginar_do_pedido(registros.order_by("-entrada_em"), request.GET)
    agora = timezone.localtime()
    for acesso in pagina:
        acesso.iniciais = _iniciais(acesso.nome_para_exibir)
        acesso.entrada_humana = _quando(acesso.entrada_em, agora, "entrou")
        acesso.saida_humana = _quando(acesso.saida_em, agora, "saiu")
        acesso.autorizador_humano = ""
        acesso.meio_humano = ""
        if acesso.autorizacao_id:
            acesso.autorizador_humano = acesso.autorizacao.autorizador_para_exibir
            acesso.meio_humano = acesso.autorizacao.get_meio_autorizacao_display()

    return {
        "historico": pagina,
        "historico_de": request.GET.get("de") or "",
        "historico_ate": request.GET.get("ate") or "",
        "historico_filtrado": bool(termo or de or ate),
    }


def _descrever_presenca(acesso, agora, inicio_do_turno):
    """Como esta presença se conta na tela, em linguagem de portaria."""
    acesso.iniciais = _iniciais(acesso.nome_para_exibir)
    acesso.entrada_humana = _quando(acesso.entrada_em, agora, "entrou")
    acesso.ha_quanto = _ha_quanto(acesso.entrada_em, agora)
    acesso.do_turno_anterior = (
        timezone.localtime(acesso.entrada_em) < inicio_do_turno
    )


def _inicio_do_turno(agora):
    """Quando o turno de hoje começou, no relógio da portaria."""
    return agora.replace(
        hour=HORA_DE_ABERTURA, minute=0, second=0, microsecond=0
    )


def _ha_quanto(momento, agora) -> str:
    """Há quanto tempo, dito como se fala no balcão."""
    if momento is None:
        return ""
    minutos = int((agora - timezone.localtime(momento)).total_seconds() // 60)
    if minutos < 1:
        return "agora mesmo"
    if minutos < 60:
        return f"há {minutos} min"
    horas, resto = divmod(minutos, 60)
    if horas < 24:
        return f"há {horas}h" if not resto else f"há {horas}h{resto:02d}"
    dias = horas // 24
    return "há 1 dia" if dias == 1 else f"há {dias} dias"


def _quando(momento, agora, verbo="entrou") -> str:
    """O momento na fala do dia: hoje, ontem, ou com a data.

    Serve para entrada e saída — o que muda é o verbo e o momento contado.
    """
    if momento is None:
        return ""
    local = timezone.localtime(momento)
    if local.date() == agora.date():
        return f"{verbo} às {local:%H:%M}"
    if local.date() == (agora - timedelta(days=1)).date():
        return f"{verbo} ontem às {local:%H:%M}"
    return f"{verbo} em {local:%d/%m} às {local:%H:%M}"


def _iniciais(nome) -> str:
    """As letras que cabem no avatar: a primeira e a última do nome."""
    partes = [parte for parte in (nome or "").split() if parte]
    if not partes:
        return "?"
    if len(partes) == 1:
        return partes[0][:1].upper()
    return (partes[0][:1] + partes[-1][:1]).upper()


def _como_chamar(registro) -> str:
    """O nome pelo qual a portaria chama esta pessoa, ou nada se não houver."""
    if registro.pessoa_id or (registro.nome or "").strip():
        return registro.nome_para_exibir
    return ""


def _recado_de_entrada(acesso) -> str:
    """O que a portaria acabou de registrar, dito como se conta a alguém."""
    nome = _como_chamar(acesso)
    if not nome:
        return "Entrada registrada."
    return f"{nome} entrou às {timezone.localtime(acesso.entrada_em):%H:%M}."


def _recado_de_saida(acesso) -> str:
    nome = _como_chamar(acesso)
    if not nome:
        return "Saída registrada."
    return f"Saída de {nome} registrada."


def _recado_do_resultado(autorizacao, acesso) -> str:
    """O resultado humano do contato feito pela Portaria."""
    if acesso is not None:
        return _recado_de_entrada(acesso)
    nome = _como_chamar(autorizacao)
    situacao = autorizacao.situacao_para_exibir.casefold()
    if not nome:
        return f"Resultado registrado: {situacao}."
    return f"{nome}: {situacao}."


def _recado_do_aviso(acesso) -> str:
    """O que a mensagem de sucesso acrescenta sobre o aviso por WhatsApp.

    A mensagem de sucesso é texto: o ícone do aviso vive na linha da entrega, em
    "Aqui agora". Aqui a portaria é avisada, por escrito, de quem pode receber o
    recado — ou de que ninguém autorizou.
    """
    if acesso is None or acesso.tipo_acesso not in TIPOS_DE_ENTREGA:
        return ""
    pessoas = _moradores_a_avisar(acesso)
    if pessoas:
        nomes = ", ".join(_primeiro_nome(str(pessoa)) for pessoa in pessoas)
        return f" Aviso por WhatsApp disponível para {nomes}."
    if acesso.unidade_id:
        return " Sem WhatsApp autorizado nesta unidade."
    return ""


# A busca de autorizador não é catálogo: quem foi encontrado responde agora.
# Pouca coisa já basta para agir, e uma lista longa só empurra a escolha certa
# para baixo da dobra.
QUANTOS_AUTORIZADORES = 6


def _autorizadores(condominio, termo):
    """Quem pode autorizar, encontrado pelo apartamento ou pelo nome.

    Uma busca, dois caminhos: quem digita "301" vê quem responde por aquela
    unidade; quem digita "Maria" vê a unidade de Maria. Nos dois casos o que
    sai é o mesmo par — quem autoriza e a unidade que é o destino.

    Quando a fala traz um número — "902", "novecentos e dois", "nove zero
    dois" —, ele é lido e comparado só com as unidades reais e ativas deste
    condomínio. Havendo correspondência exata, é ela o destino. Não havendo, o
    Eithra não assume nada: devolve a sugestão segura, se houver, e o resto
    continua como sempre foi.
    """
    if not termo:
        return [], [], ""

    ativas = Unidade.objects.do_condominio(condominio).ativas()
    numero = numero_falado.em_digitos(termo)

    if numero:
        unidades = [
            unidade
            for unidade in ativas
            if numero in numero_falado.digitos(unidade.identificador)
        ]
        sugestao = (
            ""
            if unidades
            else numero_falado.sugestao(
                numero, [unidade.identificador for unidade in ativas]
            )
        )
    else:
        unidades = list(ativas.buscar(termo))
        sugestao = ""

    unidades.sort(key=lambda unidade: _proximidade(unidade.identificador, termo))
    unidades = unidades[:QUANTOS_AUTORIZADORES]

    encontradas = _pessoas_que_autorizam(condominio, termo)
    por_unidade = _pessoas_por_unidade(condominio, unidades)
    por_pessoa = _unidades_por_pessoa(condominio, encontradas)

    return (
        [
            {
                "id": unidade.pk,
                "rotulo": unidade.rotulo,
                "pessoas": por_unidade.get(unidade.pk, []),
            }
            for unidade in unidades
        ],
        [
            opcao
            for pessoa in encontradas
            for opcao in por_pessoa.get(pessoa.pk, [])
        ],
        sugestao,
    )


def _quem_autoriza(vinculo):
    """Uma linha da busca: quem responde, com que papel e por qual unidade."""
    nome = str(vinculo.pessoa)
    return {
        "pessoa_id": vinculo.pessoa_id,
        "nome": nome,
        "iniciais": _iniciais(nome),
        "vinculo": vinculo.get_tipo_vinculo_display(),
        "unidade_id": vinculo.unidade_id,
        "unidade_rotulo": vinculo.unidade.rotulo,
    }


def _proximidade(valor, termo) -> int:
    """Quem casa exatamente com o que foi digitado vem antes do resto."""
    return 0 if (valor or "").casefold() == termo.casefold() else 1


def _pessoas_que_autorizam(condominio, termo):
    """Quem foi encontrado pelo nome e responde hoje por alguma unidade.

    Sem vínculo vigente não há destino, e sem destino não há o que autorizar:
    quem só tem cadastro não entra na lista de quem pode liberar a entrada.
    """
    candidatas = list(
        Pessoa.objects.do_condominio(condominio).ativas().buscar(termo)
    )
    if not candidatas:
        return []
    respondem = set(
        VinculoPessoaUnidade.objects.do_condominio(condominio)
        .vigentes()
        .filter(tipo_vinculo__in=TIPOS_DE_RESIDENTE)
        .filter(pessoa__in=candidatas)
        .values_list("pessoa_id", flat=True)
    )
    encontradas = [pessoa for pessoa in candidatas if pessoa.pk in respondem]
    encontradas.sort(key=lambda pessoa: _proximidade(pessoa.nome, termo))
    return encontradas[:QUANTOS_AUTORIZADORES]


def _pessoas_por_unidade(condominio, unidades):
    """Quem responde hoje por cada unidade procurada."""
    if not unidades:
        return {}
    vinculos = (
        VinculoPessoaUnidade.objects.do_condominio(condominio)
        .vigentes()
        .filter(tipo_vinculo__in=TIPOS_DE_RESIDENTE)
        .filter(unidade__in=unidades)
        .select_related("pessoa", "unidade")
        .order_by("pessoa__nome")
    )
    moradores = {}
    for vinculo in vinculos:
        moradores.setdefault(vinculo.unidade_id, []).append(_quem_autoriza(vinculo))
    return moradores


def _unidades_por_pessoa(condominio, pessoas):
    """As unidades por onde cada pessoa procurada responde hoje."""
    if not pessoas:
        return {}
    vinculos = (
        VinculoPessoaUnidade.objects.do_condominio(condominio)
        .vigentes()
        .filter(tipo_vinculo__in=TIPOS_DE_RESIDENTE)
        .filter(pessoa__in=pessoas)
        .select_related("pessoa", "unidade")
        .order_by("unidade__bloco_torre", "unidade__identificador")
    )
    unidades = {}
    for vinculo in vinculos:
        unidades.setdefault(vinculo.pessoa_id, []).append(_quem_autoriza(vinculo))
    return unidades


# Onde cada campo do registro de chegada é perguntado. Serve para reabrir o
# fluxo no passo em que o erro precisa ser visto.
#
# Os passos são as decisões da portaria, nesta ordem: para onde vai, quem
# chegou, quem autorizou. O campo "local" é destino, como a unidade, e por isso
# mora no primeiro passo.
CAMPOS_DO_PASSO_DA_CHEGADA = {
    1: {"unidade", "local"},
    2: {"nome", "tipo_acesso", "empresa", "motivo", "placa", "natureza_objeto", "instrucao_entrega"},
    3: {"status", "meio_autorizacao", "autorizador_pessoa", "autorizador_nome", "observacao"},
}


def _passo_da_chegada(formulario) -> int:
    """Em que passo o registro de chegada deve reabrir depois de um erro.

    O wizard volta no passo em que a pessoa estava. Se o erro ficou em outro
    passo, ela veria o modal reabrir sem explicação — então é o erro quem
    decide onde a tela volta a abrir.
    """
    informado = 1
    if formulario is None or not formulario.is_bound:
        return informado
    try:
        informado = min(max(int(formulario.data.get("passo") or 1), 1, 3), 3)
    except (TypeError, ValueError):
        informado = 1
    for passo, campos in CAMPOS_DO_PASSO_DA_CHEGADA.items():
        if campos & set(formulario.errors):
            return passo
    if NON_FIELD_ERRORS in formulario.errors:
        return 1
    return informado


# Onde cada campo do registro de esperado é perguntado. É o mesmo fluxo de
# decisões do registro de chegada — destino, quem é esperado, quem autorizou —
# e por isso o mapa de reabertura é o mesmo, trocando só o que a chegada tem de
# próprio (o resultado da autorização) pelo que o esperado tem (o autorizador).
CAMPOS_DO_PASSO_DO_ESPERADO = {
    1: {"unidade", "local"},
    2: {
        "nome",
        "tipo_acesso",
        "empresa",
        "motivo",
        "placa",
        "natureza_objeto",
        "instrucao_entrega",
    },
    3: {"autorizador_pessoa", "autorizador_nome", "observacao"},
}


def _passo_do_esperado(formulario) -> int:
    """Em que passo o registro de esperado deve reabrir depois de um erro.

    Mesma regra do registro de chegada: quem decide onde o modal reabre é o
    erro, não o passo em que a pessoa parou. Sem isso, a correção apareceria
    num passo sem explicação na tela.
    """
    informado = 1
    if formulario is None or not formulario.is_bound:
        return informado
    try:
        informado = min(max(int(formulario.data.get("passo") or 1), 1, 3), 3)
    except (TypeError, ValueError):
        informado = 1
    for passo, campos in CAMPOS_DO_PASSO_DO_ESPERADO.items():
        if campos & set(formulario.errors):
            return passo
    if NON_FIELD_ERRORS in formulario.errors:
        return 1
    return informado


def _unidade_escolhida(formulario, condominio):
    """A unidade que o formulário já trazia, para a tela não perder a escolha.

    Quando a portaria reenvia o formulário por causa de outro erro, quem vai
    autorizar continua escolhido: a tela só precisa voltar a dizer qual é a
    unidade, em vez de pedir a mesma informação outra vez.
    """
    if formulario is None or not formulario.is_bound:
        return None
    valor = formulario.data.get("unidade")
    if not valor or not _eh_uuid(valor):
        return None
    return Unidade.objects.do_condominio(condominio).filter(pk=valor).first()


def _resultado_da_busca(condominio, termo):
    if not termo:
        return None
    pessoas = (
        Pessoa.objects.do_condominio(condominio)
        .ativas()
        .buscar(termo)
        .order_by("nome")[:5]
    )
    unidades = list(
        Unidade.objects.do_condominio(condominio)
        .ativas()
        .buscar(termo)
        .order_by("bloco_torre", "identificador")[:5]
    )
    veiculos = (
        Veiculo.objects.do_condominio(condominio)
        .ativos()
        .buscar(termo)
        .order_by("placa")[:5]
    )
    fornecedores = (
        Fornecedor.objects.do_condominio(condominio)
        .ativos()
        .buscar(termo)
        .order_by("nome_razao_social")[:5]
    )
    esperados = (
        AutorizacaoPortaria.objects.do_condominio(condominio)
        .select_related("pessoa", "unidade", "local", "veiculo", "autorizador_pessoa")
        .buscar(termo)
        .order_by("-resultado_registrado_em", "-inicio_previsto")[:5]
    )
    presencas = (
        AcessoPortaria.objects.do_condominio(condominio)
        .abertas()
        .select_related("pessoa", "unidade", "local", "veiculo")
        .buscar(termo)
        .order_by("-entrada_em")[:5]
    )
    historico = (
        AcessoPortaria.objects.do_condominio(condominio)
        .select_related("pessoa", "unidade", "local", "veiculo")
        .buscar(termo)
        .order_by("-entrada_em")[:5]
    )
    unidades_resumo = [
        {
            "registro": unidade,
            "pessoas": list(unidade.pessoas_atuais()),
            "veiculos": list(unidade.veiculos_relacionados()),
            "autorizacoes": list(
                AutorizacaoPortaria.objects.do_condominio(condominio)
                .filter(unidade=unidade)
                .select_related("pessoa")
                .order_by("-inicio_previsto")[:3]
            ),
        }
        for unidade in unidades
    ]
    # Quem mora não é recebido como visita: ao ser buscado, a pessoa entra como
    # contexto — a unidade por onde responde e o que foi autorizado para ela.
    pessoas_resumo = [
        {
            "registro": pessoa,
            "unidades": list(pessoa.unidades_atuais()),
            "veiculos": list(pessoa.veiculos_atuais()),
            "autorizacoes": list(
                AutorizacaoPortaria.objects.do_condominio(condominio)
                .filter(unidade__in=pessoa.unidades_atuais())
                .select_related("pessoa")
                .order_by("-inicio_previsto")[:3]
            ),
        }
        for pessoa in pessoas
    ]
    return {
        "pessoas": pessoas_resumo,
        "unidades": unidades_resumo,
        "veiculos": veiculos,
        "fornecedores": fornecedores,
        "esperados": esperados,
        "presencas": presencas,
        "historico": historico,
    }


def _resultados_da_portaria(condominio, termo):
    """A busca da portaria numa lista só, na ordem em que a portaria olha.

    A busca já sabe achar cada coisa — pessoa, unidade, veículo, fornecedor,
    espera e passagem. O que faltava era dizer tudo junto: primeiro quem está
    dentro agora, depois quem se espera, e então o cadastro. Nada aparece duas
    vezes, e nada de outro condomínio entra.

    A busca localiza contexto, não opera: cada item que tem para onde ir leva o
    endereço de uma tela que já existe. Quem não tem — hoje, o fornecedor — vem
    sem destino, e a tela o apresenta como informação, não como ação.
    """
    achados = _resultado_da_busca(condominio, termo)
    if not achados:
        return []

    agora = timezone.localtime()
    ligacoes = _vinculos_das_pessoas(
        condominio, [resumo["registro"] for resumo in achados["pessoas"]]
    )
    itens = []
    vistos = set()

    def acrescentar(chave, nome, contexto, destino=None):
        if not nome or chave in vistos:
            return
        vistos.add(chave)
        itens.append(
            {
                "nome": nome,
                "contexto": contexto,
                "iniciais": _iniciais(nome),
                "url": destino,
            }
        )

    for acesso in achados["presencas"]:
        # Quem está dentro se procura no histórico pelo nome: a saída continua
        # sendo ato da operação, e a busca não a executa por ninguém.
        acrescentar(
            ("acesso", acesso.pk),
            acesso.nome_para_exibir,
            "Dentro agora · %s · %s"
            % (acesso.get_tipo_acesso_display().lower(), acesso.destino_para_exibir),
            _destino_do_historico(condominio, acesso.nome_para_exibir),
        )
    for esperado in achados["esperados"]:
        # A revisão da autorização já existe na tela: o clique só a abre.
        acrescentar(
            ("autorizacao", esperado.pk),
            esperado.nome_para_exibir,
            "%s · %s"
            % (esperado.situacao_para_exibir, esperado.destino_para_exibir),
            _destino_da_verificacao(condominio, esperado),
        )
    for resumo in achados["pessoas"]:
        pessoa = resumo["registro"]
        papeis = ligacoes.get(pessoa.pk, [])
        if papeis:
            contexto = "%s · %s" % (
                papeis[0].get_tipo_vinculo_display(),
                ", ".join(vinculo.unidade.rotulo for vinculo in papeis),
            )
        else:
            contexto = "Sem unidade ativa"
        acrescentar(
            ("pessoa", pessoa.pk),
            str(pessoa),
            contexto,
            _destino_do_historico(condominio, str(pessoa)),
        )
    for resumo in achados["unidades"]:
        registro = resumo["registro"]
        moradores = ", ".join(str(morador) for morador in resumo["pessoas"])
        acrescentar(
            ("unidade", registro.pk),
            registro.rotulo,
            moradores or "Sem moradores vinculados hoje",
            # O histórico procura pela unidade pelo identificador, e não pelo
            # rótulo com bloco: é o identificador que o filtro entende.
            _destino_do_historico(condominio, registro.identificador),
        )
    for veiculo in achados["veiculos"]:
        condutores = ", ".join(str(pessoa) for pessoa in veiculo.pessoas_atuais())
        acrescentar(
            ("veiculo", veiculo.pk),
            veiculo.placa,
            condutores or "Veículo cadastrado no condomínio",
            # O histórico já aceita placa na busca; o veículo leva para lá.
            _destino_do_historico(condominio, veiculo.placa),
        )
    for fornecedor in achados["fornecedores"]:
        # Sem tela própria de fornecedor, ele apenas se apresenta.
        acrescentar(("fornecedor", fornecedor.pk), str(fornecedor), "Fornecedor")
    for acesso in achados["historico"]:
        acrescentar(
            ("acesso", acesso.pk),
            acesso.nome_para_exibir,
            "%s · %s"
            % (acesso.destino_para_exibir, _quando(acesso.entrada_em, agora)),
            _destino_do_historico(condominio, acesso.nome_para_exibir),
        )
    return itens


def _endereco_do_painel(parametros):
    """Um endereço da própria Portaria, com o condomínio viajando junto."""
    return "%s?%s" % (reverse("portaria:painel"), urlencode(parametros))


def _destino_do_historico(condominio, termo):
    """O histórico já procura por nome, unidade e placa: a busca só aponta."""
    return _endereco_do_painel(
        {"aba": "historico", "q": termo, "condominio": condominio.pk}
    )


def _destino_da_verificacao(condominio, autorizacao):
    """A revisão da autorização já existe: o clique abre, não registra."""
    return _endereco_do_painel(
        {
            "abrir": "verificar",
            "item": str(autorizacao.pk),
            "condominio": condominio.pk,
        }
    )


def _vinculos_das_pessoas(condominio, pessoas):
    """Por onde cada pessoa responde hoje, e com que papel.

    Uma consulta para todas: o papel é do vínculo, e sem ele a busca diria só
    um nome solto, sem dizer de que unidade ele é.
    """
    if not pessoas:
        return {}
    vinculos = (
        VinculoPessoaUnidade.objects.do_condominio(condominio)
        .vigentes()
        .filter(pessoa__in=pessoas)
        .select_related("unidade")
        .order_by("unidade__bloco_torre", "unidade__identificador")
    )
    ligacoes = {}
    for vinculo in vinculos:
        ligacoes.setdefault(vinculo.pessoa_id, []).append(vinculo)
    return ligacoes


def _autorizacao_em_verificacao(request, condominio):
    if request.GET.get("abrir") != "verificar":
        return None
    pk = request.GET.get("item")
    if not pk or not _eh_uuid(pk):
        return None
    return (
        AutorizacaoPortaria.objects.do_condominio(condominio)
        .select_related("unidade", "local", "autorizador_pessoa")
        .filter(pk=pk)
        .first()
    )


def _iniciais_da_verificacao(autorizacao):
    if autorizacao is None:
        return {}
    return {
        "autorizador_pessoa": autorizacao.autorizador_pessoa_id,
        "autorizador_nome": autorizacao.autorizador_para_exibir,
        "status": autorizacao.status,
        "meio_autorizacao": autorizacao.meio_autorizacao,
        "observacao": autorizacao.observacao,
    }


def _condominios_para_ver(request):
    condominios = condominios_para_ver(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomínio disponível.")
    return condominios


def _condominio_atual(request, condominios):
    escolhido = request.POST.get("condominio") or request.GET.get("condominio")
    if not escolhido:
        return condominios.first()
    if not _eh_uuid(escolhido):
        raise Http404("Condomínio não encontrado.")
    return _obter_ou_404(condominios, escolhido)


def _condominio_para_operar(request):
    visualizaveis = _condominios_para_ver(request)
    atual = _condominio_atual(request, visualizaveis)
    if not pode_operar_portaria(request.user, atual):
        raise PermissionDenied("Esta pessoa não opera a portaria deste condomínio.")
    return atual


def _base_da_pagina(request):
    condominios = _condominios_para_ver(request)
    atual = _condominio_atual(request, condominios)
    return {
        "condominios": condominios,
        "condominio_atual": atual,
        "logo_do_condominio": logo.endereco(atual),
        "mostrar_seletor": condominios.count() > 1,
        "aba": _aba(request),
        "termo": (request.GET.get("q") or "").strip(),
        "agora": timezone.localtime(),
    }


def _obter_ou_404(queryset, pk):
    try:
        return get_object_or_404(queryset, pk=pk)
    except (ValidationError, ValueError, TypeError):
        raise Http404("Registro não encontrado.") from None


def _eh_uuid(valor):
    try:
        uuid.UUID(str(valor))
    except (ValueError, TypeError, AttributeError):
        return False
    return True


def _destino(view_name, condominio):
    return f"{reverse(view_name)}?condominio={condominio.pk}"
