"""Views do núcleo: o painel e a decisão sobre as mensagens lidas.

O painel responde à pergunta que abre o sistema: *entrou alguma informação que
muda algo que mereça atenção?* Quando há mensagens aguardando decisão, elas
aparecem ali — não existe caixa de entrada paralela, nem menu de e-mail.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core import email_acoes
from apps.core.email_interpretacao import Consequencia

# O que a tela mostra das leituras: só o que tem nome humano. Remetente,
# assunto e momento já estão no cabeçalho; o resto vira linha rotulada.
ROTULO_DO_FATO = {
    "anexo": "Arquivo recebido",
    "ordem_de_servico": "Ordem de serviço",
    "orcamento": "Orçamento",
    "contrato": "Contrato",
    "documento": "Documento",
    "valor": "Valor citado",
    "data_citada": "Data citada",
    "unidade": "Unidade",
    "equipamento": "Equipamento",
    "local": "Local",
    "fornecedor": "Fornecedor",
}


@login_required
def home(request):
    """Ponto de entrada do sistema depois do acesso."""
    return render(
        request,
        "pages/home.html",
        {"pendencias": email_acoes.pendencias_para(request.user)},
    )


@login_required
def confirmacao(request, pk):
    """A leitura de uma mensagem, para decisão humana.

    A leitura é refeita a cada visita: o que a tela mostra é o que o sistema
    entende agora, não o que entendia quando alguém abriu a mesma página ontem.
    """
    item = _item_para_decidir(request, pk)
    leitura, falha = email_acoes.leitura_atual(item)
    acao = email_acoes.acao_disponivel(leitura) if leitura is not None else ""
    return render(
        request,
        "pages/core/confirmacao.html",
        {
            "item": item,
            "leitura": leitura,
            "falha_de_leitura": bool(falha),
            "consequencia": _rotulo_da_consequencia(leitura),
            "fatos": _fatos_da_leitura(leitura),
            "contexto": _contexto_da_leitura(leitura),
            "acao": acao,
            "rotulo_da_acao": email_acoes.rotulo_da_acao(acao),
        },
    )


@login_required
@require_POST
def confirmar(request, pk):
    """Executa a ação escolhida — sempre por POST, sempre reconstruindo tudo."""
    item = _item_para_decidir(request, pk)
    acao = (request.POST.get("acao") or "").strip()

    try:
        resultado = email_acoes.confirmar_acao_email(item, acao, usuario=request.user)
    except email_acoes.PermissaoNegada:
        raise Http404("Esta confirmação não está disponível.")
    except email_acoes.ErroConfirmacaoEmail:
        messages.error(request, "Não conseguimos confirmar esta ação agora.")
        return redirect("core:home")

    if resultado.contexto_mudou:
        # Nada foi executado: a leitura nova volta para ser revista.
        messages.warning(request, resultado.mensagem)
        return redirect("core:confirmacao", pk=item.pk)

    if resultado.executado:
        messages.success(request, resultado.mensagem)
    elif resultado.falha:
        messages.error(request, resultado.mensagem)
    else:
        messages.info(request, resultado.mensagem)
    return redirect("core:home")


# ---------------------------------------------------------------------------
# Apoio da tela
# ---------------------------------------------------------------------------


def _item_para_decidir(request, pk):
    """O item só é alcançável por quem decide sobre ele — senão, 404.

    É o mesmo critério que monta a lista de pendências: nem o identificador
    vindo da tela nem o condomínio do item são aceitos como verdade.
    """
    return get_object_or_404(email_acoes.pendencias_para(request.user), pk=pk)


def _rotulo_da_consequencia(leitura) -> str:
    if leitura is None:
        return ""
    for valor, rotulo in Consequencia.choices:
        if valor == leitura.tipo_de_consequencia:
            return rotulo
    return ""


def _fatos_da_leitura(leitura) -> list[dict]:
    if leitura is None:
        return []
    return [
        {
            "rotulo": ROTULO_DO_FATO[fato.campo],
            "valor": fato.texto,
            "do_cadastro": fato.confirmado,
        }
        for fato in leitura.fatos
        if fato.campo in ROTULO_DO_FATO and fato.existe
    ]


def _contexto_da_leitura(leitura) -> str:
    if leitura is None or leitura.proposta.candidato is None:
        return ""
    return leitura.proposta.candidato.rotulo
