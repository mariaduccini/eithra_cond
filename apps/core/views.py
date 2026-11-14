"""Views do núcleo: o painel e a decisão sobre as mensagens lidas.

O painel responde à pergunta que abre o sistema: *o que precisa da minha atenção
agora?* A resposta tem duas origens que não se misturam: as manutenções cujo
estado real derivou uma atenção — lidas por ``apps.manutencoes.atencao`` e aqui
apenas agrupadas, nunca recalculadas — e as mensagens que a leitura identificou
e não pôde seguir sozinha. Não existe caixa de entrada paralela, nem menu de
e-mail.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.core import email_acoes
from apps.core.email_interpretacao import Consequencia
from apps.manutencoes.atencao import (
    ResumoDeAtencoes,
    TipoAtencao,
    obter_atencoes_de_manutencoes,
)

# Quantas manutenções a Home mostra antes de oferecer "Ver todas". A lista
# inteira continua a um clique; a Home não vira painel.
LIMITE_DE_ATENCOES_NA_HOME = 5

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
    """Ponto de entrada do sistema depois do acesso.

    A seção operacional responde à mesma pergunta do painel — *o que precisa da
    minha atenção agora?* — reutilizando a leitura derivada das manutenções. A
    view não decide o que é atraso, conferência ou conclusão: recebe a lista
    pronta, corta o que é só "próxima" para um bloco próprio e limita o que
    cabe na tela. A regra mora em ``apps.manutencoes.atencao`` — aqui ela não se
    repete.
    """
    atencao = obter_atencoes_de_manutencoes(usuario=request.user)
    resumo = ResumoDeAtencoes.de(atencao)

    # "Hoje" reúne o que exige ação; "Próximos dias" guarda o que ainda está por
    # vir. O único corte é o de "próxima" — precedência e texto vêm do domínio.
    do_agora = [item for item in atencao if item.tipo != TipoAtencao.PROXIMA]
    proximas = [item for item in atencao if item.tipo == TipoAtencao.PROXIMA]

    return render(
        request,
        "pages/home.html",
        {
            "pendencias": email_acoes.pendencias_para(request.user),
            "atencao_de_hoje": [
                _linha_de_atencao(item)
                for item in do_agora[:LIMITE_DE_ATENCOES_NA_HOME]
            ],
            # Tudo o que merece atenção menos o que é apenas futuro.
            "total_de_hoje": resumo.total - resumo.proximas,
            "tem_mais_atencao": len(do_agora) > LIMITE_DE_ATENCOES_NA_HOME,
            "atencao_proxima": [_linha_de_atencao(item) for item in proximas],
        },
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


def _linha_de_atencao(item) -> dict:
    """O pouco que a Home mostra de uma atenção, sem repetir o que a frase já diz.

    O título e a mensagem vêm derivados do domínio; o contexto situa a
    manutenção — onde e com quem — e só acrescenta a data quando a própria
    mensagem não a informa, como ocorre com o que ainda está por vir. É
    apresentação: nenhuma regra de atenção é lida aqui.
    """
    manutencao = item.manutencao
    contexto = []
    if manutencao.local_id:
        contexto.append(manutencao.local.nome)
    if manutencao.fornecedor_id:
        contexto.append(manutencao.fornecedor.nome_razao_social)
    if manutencao.data_prevista and item.tipo != TipoAtencao.PROXIMA:
        contexto.append(f"previsto para {manutencao.data_prevista:%d/%m/%Y}")

    return {
        "id": manutencao.pk,
        "titulo": manutencao.titulo,
        "mensagem": item.mensagem,
        "contexto": " · ".join(contexto),
    }


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
