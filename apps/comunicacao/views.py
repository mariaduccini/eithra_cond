"""Telas da capacidade transversal de comunicação.

A tela existe para escrever, revisar e gerar o card de um comunicado — e para
lembrar o que aconteceu com ele. Nada de regra de domínio mora aqui: a view
traduz o que a gestão decidiu, e o domínio (``services``) decide, aplica e
registra.

Toda ação que muda algo chega por POST. Só a leitura do card gerado é um GET: ela
não muda nada, apenas entrega o arquivo a quem pode ver o comunicado.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core import filtros as filtros_da_lista
from apps.core.paginacao import paginar_do_pedido
from apps.core.storage import ErroDeStorage
from apps.demandas.models import Demanda
from apps.manutencoes.models import Manutencao

from . import cartao
from .constants import ModeloComunicado, OrigemComunicado, StatusComunicacao
from .forms import ComunicacaoForm, FiltroDeComunicacoesForm
from .models import Comunicacao
from .permissoes import (
    condominios_para_criar,
    condominios_para_ver,
    pode_editar_comunicacao,
)
from .services import (
    acesso_temporario,
    criar_comunicacao,
    editar_comunicacao,
    gerar_card,
)

# O tom de cada situação no badge. Discreto: a lista inteira não pode gritar.
TOM_POR_STATUS = {
    StatusComunicacao.RASCUNHO: "secondary",
    StatusComunicacao.PRONTA: "success",
}


@login_required
def listagem(request):
    """Os comunicados dos condomínios que esta pessoa pode acompanhar."""
    condominios = _condominios_para_ver(request)
    return _render_listagem(request, condominios)


@login_required
def criar(request):
    """Escreve um comunicado novo — com ou sem uma origem.

    A origem chega pelo endereço (``?demanda=`` ou ``?manutencao=``), resolvida
    no backend dentro dos condomínios que esta pessoa pode comunicar. Ela é
    referência: a demanda ou a manutenção continua dona daqueles dados, e o texto
    do comunicado começa a partir do que a pessoa escrever.
    """
    condominios = condominios_para_criar(request.user)
    if not condominios.exists():
        raise Http404("Nenhum condomínio disponível para escrever comunicado.")

    origem = _origem_do_pedido(request, condominios)

    if request.method != "POST":
        return _render_formulario(request, condominios=condominios, origem=origem)

    condominio_da_origem = origem["condominio"] if origem else None
    formulario = ComunicacaoForm(
        request.POST, condominios=condominios, condominio=condominio_da_origem
    )
    if not formulario.is_valid():
        return _render_formulario(
            request, condominios=condominios, origem=origem, formulario=formulario, status=400
        )

    dados = formulario.cleaned_data
    condominio = dados.get("condominio") or condominio_da_origem
    if condominio is None:
        formulario.add_error("condominio", "Escolha o condomínio do comunicado.")
        return _render_formulario(
            request, condominios=condominios, origem=origem, formulario=formulario, status=400
        )

    comunicacao = criar_comunicacao(
        condominio=condominio,
        criado_por=request.user,
        titulo=dados["titulo"],
        mensagem=dados["mensagem"],
        local=dados["local"],
        periodo=dados["periodo"],
        publico=dados["publico"],
        publico_descricao=dados["publico_descricao"],
        modelo=dados["modelo"],
        orientacao=dados["orientacao"],
        origem_tipo=origem["origem_tipo"] if origem else OrigemComunicado.AVULSO,
        origem_demanda=origem.get("origem_demanda") if origem else None,
        origem_manutencao=origem.get("origem_manutencao") if origem else None,
    )
    messages.success(request, "Comunicado registrado. Revise o texto e gere o card.")
    return redirect("comunicacao:detalhe", pk=comunicacao.pk)


@login_required
def detalhe(request, pk):
    """O comunicado, o card que ele produz e o que já aconteceu com ele."""
    comunicacao = _comunicacao_autorizada(request, pk)
    return _render_detalhe(request, comunicacao)


@login_required
@require_POST
def editar(request, pk):
    """Revisa o texto do comunicado. Só quem responde pelo condomínio revisa."""
    comunicacao = _comunicacao_para_editar(request, pk)
    formulario = ComunicacaoForm(
        request.POST, condominio=comunicacao.condominio, instancia=comunicacao
    )
    if not formulario.is_valid():
        return _render_detalhe(request, comunicacao, formulario=formulario, status=400)

    dados = formulario.cleaned_data
    editar_comunicacao(
        comunicacao,
        usuario=request.user,
        titulo=dados["titulo"],
        mensagem=dados["mensagem"],
        local=dados["local"],
        periodo=dados["periodo"],
        publico=dados["publico"],
        publico_descricao=dados["publico_descricao"],
        modelo=dados["modelo"],
        orientacao=dados["orientacao"],
    )
    messages.success(request, "Comunicado revisado. Gere o card para atualizar a arte.")
    return redirect("comunicacao:detalhe", pk=comunicacao.pk)


@login_required
@require_POST
def gerar(request, pk):
    """Desenha e guarda o card do comunicado.

    Quem pode revisar é quem pode gerar: publicar o que foi escrito aos moradores
    pede a mesma responsabilidade de responder pelo condomínio. Uma falha de
    armazenamento vira aviso, e não um comunicado apontando para um card que não
    subiu.
    """
    comunicacao = _comunicacao_para_editar(request, pk)
    try:
        gerar_card(comunicacao, usuario=request.user)
    except (ErroDeStorage, ImproperlyConfigured):
        messages.error(
            request,
            "Não foi possível guardar o card agora. Tente novamente em instantes.",
        )
        return redirect("comunicacao:detalhe", pk=comunicacao.pk)

    messages.success(request, "Card gerado e pronto para compartilhar.")
    return redirect("comunicacao:detalhe", pk=comunicacao.pk)


@login_required
def arquivo(request, pk):
    """Entrega o card gerado a quem pode ver o comunicado.

    A autorização é a do próprio comunicado: quem não o acompanha não descobre
    que o card existe — a resposta é 404. O endereço é temporário, gerado na
    hora: nunca fica guardado nem circula.
    """
    comunicacao = _comunicacao_autorizada(request, pk)
    try:
        destino = acesso_temporario(comunicacao)
    except (ErroDeStorage, ImproperlyConfigured) as erro:
        raise Http404("O card deste comunicado não está disponível.") from erro
    return redirect(destino)


# -- Autorização ----------------------------------------------------------------
#
# Toda pergunta passa pelo domínio. Quem não enxerga o módulo não descobre que o
# comunicado existe: a resposta é 404. Quem enxerga mas não responde pelo
# condomínio é recusado com 403 — a existência já é legítima, a decisão não é.


def _condominios_para_ver(request):
    """Condomínios cujos comunicados esta pessoa acompanha, ou 404."""
    condominios = condominios_para_ver(request.user)
    if not condominios.exists():
        raise Http404("Nenhum comunicado disponível para consulta.")
    return condominios


def _comunicacao_autorizada(request, pk):
    """Resolve o comunicado dentro dos condomínios que esta pessoa acompanha."""
    condominios = _condominios_para_ver(request)
    return get_object_or_404(
        Comunicacao.objects.select_related(
            "condominio",
            "criado_por",
            "origem_demanda",
            "origem_manutencao",
        ).filter(condominio__in=condominios),
        pk=pk,
    )


def _comunicacao_para_editar(request, pk):
    """Resolve o comunicado e exige, no backend, o direito de revisá-lo."""
    comunicacao = _comunicacao_autorizada(request, pk)
    if not pode_editar_comunicacao(request.user, comunicacao):
        raise PermissionDenied("Esta pessoa não responde por este condomínio.")
    return comunicacao


def _origem_do_pedido(request, condominios):
    """Resolve a origem pedida para o comunicado, ou nada.

    A origem é a demanda ou a manutenção que motivou o aviso — resolvida dentro
    dos condomínios em que esta pessoa pode escrever. Um identificador que não
    pertence a eles é recusado com 404: quem não pode comunicar naquele
    condomínio não descobre que o registro de origem existe.
    """
    demanda_id = request.GET.get("demanda") or request.POST.get("demanda")
    if demanda_id:
        demanda = get_object_or_404(
            Demanda.objects.filter(condominio__in=condominios), pk=demanda_id
        )
        return {
            "origem_tipo": OrigemComunicado.DEMANDA,
            "origem_demanda": demanda,
            "condominio": demanda.condominio,
            "rotulo": f"Demanda · {demanda.titulo}",
            "titulo_sugerido": demanda.titulo,
        }

    manutencao_id = request.GET.get("manutencao") or request.POST.get("manutencao")
    if manutencao_id:
        manutencao = get_object_or_404(
            Manutencao.objects.filter(condominio__in=condominios), pk=manutencao_id
        )
        return {
            "origem_tipo": OrigemComunicado.MANUTENCAO,
            "origem_manutencao": manutencao,
            "condominio": manutencao.condominio,
            "rotulo": f"Manutenção · {manutencao.titulo}",
            "titulo_sugerido": manutencao.titulo,
        }

    return None


# -- Telas ----------------------------------------------------------------------


def _render_listagem(request, condominios, *, status=200):
    """Monta a lista de comunicados com os filtros aplicados."""
    filtros = FiltroDeComunicacoesForm(request.GET or None, condominios=condominios)
    pagina = paginar_do_pedido(
        _comunicacoes_filtradas(condominios, filtros), request.GET
    )
    pode_criar_condominios = condominios_para_criar(request.user)

    contexto = {
        "comunicacoes": [_comunicacao_para_lista(item) for item in pagina],
        "indicadores": _indicadores_da_listagem(condominios),
        "pagina_comunicacoes": pagina,
        "filtros": filtros,
        "filtros_ativos": filtros_da_lista.tem_filtros(filtros),
        "total": pagina.paginator.count,
        "pode_criar": pode_criar_condominios.exists(),
        "mostrar_condominio": condominios.count() > 1,
    }
    return render(request, "pages/comunicacao/listagem.html", contexto, status=status)


def _render_formulario(request, *, condominios, origem=None, formulario=None, status=200):
    """Monta a tela de escrita de um comunicado, com a prévia do card."""
    if formulario is None:
        formulario = ComunicacaoForm(
            condominios=condominios,
            condominio=origem["condominio"] if origem else None,
            initial=_iniciais_da_origem(origem),
        )

    contexto = {
        "formulario": formulario,
        "origem": origem,
        "previa": _previa_do_formulario(formulario),
    }
    return render(request, "pages/comunicacao/novo.html", contexto, status=status)


def _render_detalhe(request, comunicacao, *, formulario=None, status=200):
    """Monta a tela do comunicado com o que esta pessoa pode fazer com ele."""
    pode_editar = pode_editar_comunicacao(request.user, comunicacao)
    contexto = {
        "comunicacao": comunicacao,
        "previa": _previa_da_comunicacao(comunicacao),
        "historico": _historico_para_tela(comunicacao),
        "pode_editar": pode_editar,
        "formulario": formulario
        or ComunicacaoForm(
            condominio=comunicacao.condominio,
            instancia=comunicacao,
            initial=_iniciais_da_comunicacao(comunicacao),
        ),
    }
    return render(request, "pages/comunicacao/detalhe.html", contexto, status=status)


# -- Dados para a tela ----------------------------------------------------------


def _iniciais_da_origem(origem) -> dict:
    """O que a tela já deve trazer quando o comunicado parte de uma origem."""
    if not origem:
        return {}
    return {"titulo": origem.get("titulo_sugerido", "")}


def _iniciais_da_comunicacao(comunicacao) -> dict:
    """O que a tela de revisão já deve trazer quando é aberta."""
    return {
        "condominio": comunicacao.condominio_id,
        "modelo": comunicacao.modelo,
        "orientacao": comunicacao.orientacao,
        "titulo": comunicacao.titulo,
        "mensagem": comunicacao.mensagem,
        "local": comunicacao.local,
        "periodo": comunicacao.periodo,
        "publico": comunicacao.publico,
        "publico_descricao": comunicacao.publico_descricao,
    }


def _previa_do_formulario(formulario) -> dict:
    """A prévia do card a partir do que está na tela, gerada ou não o arquivo.

    Lê o que o formulário mostra hoje — inclusive o valor que a pessoa enviou —,
    para que a arte na tela acompanhe o texto antes de o card existir. A mesma
    paleta do PNG alimenta esta prévia, para que o que se vê antes de gerar seja
    o que sai depois.
    """
    modelo = formulario["modelo"].value() or ModeloComunicado.AVISO
    condominio = formulario.condominio_efetivo
    return {
        "cores": cartao.cores_do_modelo(modelo),
        "condominio": getattr(condominio, "nome", "") or "",
        "titulo": formulario["titulo"].value() or "Título do comunicado",
        "mensagem": formulario["mensagem"].value() or "",
        "local": formulario["local"].value() or "",
        "periodo": formulario["periodo"].value() or "",
        "modelo": ModeloComunicado(modelo).label,
        "orientacao": formulario["orientacao"].value() or "",
    }


def _previa_da_comunicacao(comunicacao) -> dict:
    """A prévia do card de um comunicado já escrito."""
    return {
        "cores": cartao.cores_do_modelo(comunicacao.modelo),
        "condominio": comunicacao.condominio.nome,
        "titulo": comunicacao.titulo,
        "mensagem": comunicacao.mensagem,
        "local": comunicacao.local,
        "periodo": comunicacao.periodo_por_extenso,
        "modelo": comunicacao.get_modelo_display(),
        "orientacao": comunicacao.orientacao,
    }


def _comunicacao_para_lista(comunicacao) -> dict:
    """O que a linha da lista precisa mostrar, já resolvido."""
    return {
        "id": comunicacao.pk,
        "titulo": comunicacao.titulo,
        "modelo": comunicacao.get_modelo_display(),
        "publico": comunicacao.publico_para_exibir,
        "local": comunicacao.local,
        "periodo": comunicacao.periodo_por_extenso,
        "origem": comunicacao.descricao_da_origem,
        "status": comunicacao.get_status_display(),
        "status_tom": TOM_POR_STATUS.get(comunicacao.status, "secondary"),
        "pronta": comunicacao.esta_pronta,
    }


def _historico_para_tela(comunicacao) -> list[dict]:
    """O histórico do comunicado, do mais recente para o mais antigo."""
    return [
        {
            "tipo": evento.get_tipo_evento_display(),
            "descricao": evento.descricao,
            "quem": _nome_de(evento.usuario),
            "momento": evento.data_hora.strftime("%d/%m/%Y %H:%M"),
        }
        for evento in comunicacao.historico.select_related("usuario")
    ]


def _indicadores_da_listagem(condominios) -> list[dict]:
    """Os números do topo da lista: quantos comunicados e em que ponto estão."""
    do_condominio = Comunicacao.objects.filter(
        condominio__in=condominios, ativo=True
    )
    return [
        {
            "titulo": "Comunicados",
            "valor": do_condominio.count(),
            "icone": "bx-megaphone",
            "tom": "primary",
        },
        {
            "titulo": "Rascunhos",
            "valor": do_condominio.filter(status=StatusComunicacao.RASCUNHO).count(),
            "icone": "bx-edit",
            "tom": "warning",
        },
        {
            "titulo": "Prontos",
            "valor": do_condominio.filter(status=StatusComunicacao.PRONTA).count(),
            "icone": "bx-check-circle",
            "tom": "success",
        },
    ]


def _comunicacoes_filtradas(condominios, filtros):
    """Aplica os filtros essenciais e devolve a lista mais recente primeiro."""
    queryset = Comunicacao.objects.filter(
        condominio__in=condominios, ativo=True
    ).select_related("condominio", "criado_por", "origem_demanda", "origem_manutencao")

    if not filtros.is_valid():
        return queryset.order_by("-criado_em")

    dados = filtros.cleaned_data
    if dados.get("condominio"):
        queryset = queryset.filter(condominio=dados["condominio"])
    if dados.get("status"):
        queryset = queryset.filter(status=dados["status"])
    if dados.get("modelo"):
        queryset = queryset.filter(modelo=dados["modelo"])
    if dados.get("publico"):
        queryset = queryset.filter(publico=dados["publico"])
    if dados.get("busca"):
        termo = dados["busca"]
        queryset = queryset.filter(
            Q(titulo__icontains=termo)
            | Q(mensagem__icontains=termo)
            | Q(local__icontains=termo)
        )
    return queryset.order_by("-criado_em")


def _nome_de(usuario) -> str:
    """O nome pelo qual a pessoa é reconhecida no histórico."""
    if usuario is None:
        return "Sem responsável"
    return (
        getattr(usuario, "full_name", "")
        or getattr(usuario, "short_name", "")
        or getattr(usuario, "email", "")
        or "Sem responsável"
    )
