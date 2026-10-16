"""Telas de entrada do balanço.

A tela existe para uma coisa só: receber o arquivo que a administração enviou e
mostrar o que já chegou. Nenhum valor é interpretado aqui, nenhum mês passa a
valer. O que a gestão vê enquanto isso é o estado do recebimento.

A autorização é decidida no backend, sempre a partir do vínculo ativo da pessoa
com o condomínio. Quem não pode importar não é avisado de que o registro
existe: a resposta é 404.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.balancos.constants import StatusImportacao
from apps.balancos.forms import ImportacaoBalancoForm
from apps.balancos.models import ImportacaoBalanco
from apps.balancos.permissoes import condominios_para_importar
from apps.balancos.services import (
    descartar_importacao,
    formatar_tamanho,
    gerar_acesso_temporario,
    limite_de_upload,
    receber_importacao,
)

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
    gestão confirma a importação, em etapa futura.
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
            "Não foi possível receber o arquivo. A tentativa ficou registrada; "
            "tente enviar de novo.",
        )
    else:
        messages.success(request, "Arquivo recebido.")

    return redirect("balancos:importacoes")


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
def descartar(request, pk):
    """Recusa a importação, guardando a memória do descarte."""
    importacao = _importacao_autorizada(request, pk)
    descartar_importacao(importacao)
    messages.success(request, "Importação descartada.")
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


def _render(request, *, condominios, formulario=None, status=200):
    """Monta a tela com o que já foi recebido e o formulário de entrada."""
    recebidas = (
        ImportacaoBalanco.objects.filter(condominio__in=condominios)
        .select_related("condominio")
        .order_by("-criado_em")[:LIMITE_DA_LISTA]
    )
    contexto = {
        "importacoes": recebidas,
        "formulario_importacao": formulario or ImportacaoBalancoForm(condominios=condominios),
        "limite_de_upload": formatar_tamanho(limite_de_upload()),
    }
    return render(request, "pages/balancos/importacoes.html", contexto, status=status)
