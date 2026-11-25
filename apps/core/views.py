"""Views do núcleo: o painel e a decisão sobre as mensagens lidas.

O painel responde à pergunta que abre o sistema: *o que precisa da minha atenção
agora?* A resposta reúne origens que não se misturam: as manutenções cujo estado
real derivou uma atenção — lidas por ``apps.manutencoes.atencao`` e aqui apenas
agrupadas, nunca recalculadas; as demandas em aberto, lidas pelo próprio domínio
(``DemandaQuerySet.na_ordem_de_atencao``); e as mensagens que a leitura
identificou e não pôde seguir sozinha. Não existe caixa de entrada paralela, nem
menu de e-mail.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.balancos.constants import StatusBalanco
from apps.balancos.models import BalancoMensal
from apps.balancos.permissoes import (
    condominios_para_ler,
    condominios_para_ler_publicados,
)
from apps.condominios import logo
from apps.core import email_acoes
from apps.core.acompanhamento import (
    GESTO_ATUALIZAR,
    GESTO_ENCERRAR,
    GESTO_INICIAR,
    encerrar,
    iniciar,
    obter_acompanhamento,
    registrar_atualizacao,
)
from apps.core.email_interpretacao import Consequencia
from apps.core.meu_dia import condominios_do_meu_dia, itens_do_meu_dia
from apps.core.supervisao import (
    ORIGEM_DEMANDA,
    ORIGEM_MANUTENCAO,
    ORIGEM_ROTINA,
    BlocoDaSupervisao,
    obter_acoes_da_supervisao,
    pode_ver_acoes_da_supervisao,
)
from apps.demandas.excecoes import obter_excecoes_de_demandas
from apps.demandas.models import Demanda
from apps.demandas.permissoes import condominios_para_ver, pode_gerenciar_em_algum_condominio
from apps.demandas.services import ErroDeDemanda
from apps.governanca.constants import PeriodicidadeCobranca
from apps.governanca.models import ContratoFornecedor
from apps.governanca.permissoes import condominios_para_gerenciar
from apps.governanca.vencimentos import vencimentos_proximos
from apps.manutencoes.atencao import (
    ResumoDeAtencoes,
    TipoAtencao,
    obter_atencoes_de_manutencoes,
)
from apps.manutencoes.services import ErroDeManutencao
from apps.rotinas.services import ErroDeRotina

# Quantas manutenções a Home mostra antes de oferecer "Ver todas". A lista
# inteira continua a um clique; a Home não vira painel.
LIMITE_DE_ATENCOES_NA_HOME = 5

# Mesmo corte para as demandas em aberto. A Home apresenta o topo da ordem de
# atenção; a lista inteira continua a um clique.
LIMITE_DE_DEMANDAS_NA_HOME = 5

# Mesmo corte para as exceções que pedem decisão de quem responde pelo
# condomínio. A Home mostra o que é mais imediato; o resto continua a um clique.
LIMITE_DE_EXCECOES_NA_HOME = 5

# O mesmo corte para os vencimentos que a gestão precisa lembrar de renovar.
LIMITE_DE_VENCIMENTOS_NA_HOME = 5

# O "Meu dia" na Home é só um aperitivo: os três primeiros itens e o atalho
# para a tela. A lista inteira continua a um clique.
LIMITE_DO_MEU_DIA_NA_HOME = 3

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
    "nome": "Nome",
    "placa": "Placa",
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

    As demandas seguem a mesma ideia: a Home não decide prioridade nem prazo —
    pede ao domínio a lista em aberto já na ordem de atenção, no escopo que a
    pessoa enxerga, e mostra só o topo. Nada é copiado para outra tabela.

    Quem responde pelo condomínio ganha ainda a leitura que pede uma decisão:
    as exceções derivadas das demandas em aberto (urgência, risco, aprovação,
    prazo vencido, falta de próximo passo). A derivação mora em
    ``apps.demandas.excecoes`` — a Home só apresenta. A supervisão e a operação
    não recebem este bloco: elas conduzem, quem decide é a gestão.

    Quem responde pela supervisão operacional ganha a área "O que precisa de
    você": as ações que a operação do dia pede, reunidas de manutenções, de
    ocorrências de rotina e de demandas com próximo passo combinado. A leitura
    derivada mora em ``apps.core.supervisao`` — a Home só agrupa por bloco e
    apresenta. Quem não tem o papel não recebe a área.
    """
    condominios_financeiros = list(
        (condominios_para_ler(request.user) | condominios_para_ler_publicados(
            request.user
        )).order_by("nome")
    )
    condominio_home = _condominio_da_home(request, condominios_financeiros)
    financeiro = _resumo_financeiro_da_home(request.user, condominio_home)
    experiencia_morador = bool(financeiro.get("morador"))

    atencao = obter_atencoes_de_manutencoes(usuario=request.user)
    resumo = ResumoDeAtencoes.de(atencao)

    # "Hoje" reúne o que exige ação; "Próximos dias" guarda o que ainda está por
    # vir. O único corte é o de "próxima" — precedência e texto vêm do domínio.
    do_agora = [item for item in atencao if item.tipo != TipoAtencao.PROXIMA]
    proximas = [item for item in atencao if item.tipo == TipoAtencao.PROXIMA]

    # A ordem é do domínio; aqui só se busca um item a mais para saber se há
    # excedente, sem uma contagem à parte.
    demandas = (
        Demanda.objects.filter(
            condominio__in=condominios_para_ver(request.user), ativo=True
        )
        .abertas()
        .na_ordem_de_atencao()
        .select_related("condominio", "local")
    )
    linhas_de_demanda = [
        _linha_de_demanda(item)
        for item in demandas[: LIMITE_DE_DEMANDAS_NA_HOME + 1]
    ]

    # As exceções só fazem sentido para quem decide. A derivação é do domínio;
    # busca-se um item a mais apenas para saber se há excedente.
    excecoes = []
    if pode_gerenciar_em_algum_condominio(request.user):
        excecoes = [
            _linha_de_excecao(item)
            for item in obter_excecoes_de_demandas(usuario=request.user)[
                : LIMITE_DE_EXCECOES_NA_HOME + 1
            ]
        ]

    # A área da supervisão operacional reúne o que pede a operação do dia. A
    # leitura é derivada em apps.core.supervisao; aqui só se agrupa por bloco.
    # Sem o papel, `None` — a área não existe para quem não a conduz.
    blocos_da_supervisao = _blocos_da_supervisao(request.user)

    # Os vencimentos que pedem uma decisão de renovação. Só quem responde pelo
    # condomínio recebe o bloco — a supervisão acompanha os registros, mas a
    # decisão de renovar não é dela. A leitura mora em apps.governanca.vencimentos;
    # aqui só se resolve o condomínio em vista e se corta o topo.
    vencimentos_de_hoje = []
    tem_mais_vencimentos = False
    condominios_geridos = list(condominios_para_gerenciar(request.user).order_by("nome"))
    if condominios_geridos:
        proximos_vencimentos = vencimentos_proximos(
            _condominio_da_home(request, condominios_geridos)
        )
        vencimentos_de_hoje = proximos_vencimentos[:LIMITE_DE_VENCIMENTOS_NA_HOME]
        tem_mais_vencimentos = len(proximos_vencimentos) > LIMITE_DE_VENCIMENTOS_NA_HOME

    # O "Meu dia" na Home é o aperitivo de quem conduz a operação do dia: os
    # três primeiros itens do condomínio em vista. A leitura é a mesma da tela
    # (apps.core.meu_dia); aqui só se corta o topo. Quem não conduz o dia não
    # recebe o bloco — a leitura devolve vazio e nada é desenhado.
    meu_dia_condominios = list(condominios_do_meu_dia(request.user))
    meu_dia_itens = []
    meu_dia_total = 0
    tem_mais_meu_dia = False
    if meu_dia_condominios:
        itens_do_dia = itens_do_meu_dia(
            request.user, _condominio_da_home(request, meu_dia_condominios)
        )
        meu_dia_itens = itens_do_dia[:LIMITE_DO_MEU_DIA_NA_HOME]
        meu_dia_total = len(itens_do_dia)
        tem_mais_meu_dia = len(itens_do_dia) > LIMITE_DO_MEU_DIA_NA_HOME

    # O marcador do topo conta o que precisa de atenção. Para a supervisão, esse
    # número é o das ações dela; para os demais, segue a leitura das manutenções.
    total_de_hoje = resumo.total - resumo.proximas
    if blocos_da_supervisao is not None:
        total_de_hoje = sum(len(bloco["itens"]) for bloco in blocos_da_supervisao)

    return render(
        request,
        "pages/home.html",
        {
            "home_condominio": condominio_home,
            "condominios_financeiros": condominios_financeiros,
            "financeiro": financeiro,
            "experiencia_morador": experiencia_morador,
            "pendencias": (
                email_acoes.pendencias_para(request.user)
                if not experiencia_morador
                else []
            ),
            "atencao_de_hoje": [
                _linha_de_atencao(item)
                for item in do_agora[:LIMITE_DE_ATENCOES_NA_HOME]
            ],
            # Tudo o que merece atenção menos o que é apenas futuro.
            "total_de_hoje": total_de_hoje,
            "tem_mais_atencao": len(do_agora) > LIMITE_DE_ATENCOES_NA_HOME,
            "atencao_proxima": [_linha_de_atencao(item) for item in proximas],
            "blocos_da_supervisao": blocos_da_supervisao or [],
            "demandas_de_hoje": linhas_de_demanda[:LIMITE_DE_DEMANDAS_NA_HOME],
            "tem_mais_demandas": len(linhas_de_demanda) > LIMITE_DE_DEMANDAS_NA_HOME,
            "excecoes": excecoes[:LIMITE_DE_EXCECOES_NA_HOME],
            "tem_mais_excecoes": len(excecoes) > LIMITE_DE_EXCECOES_NA_HOME,
            "vencimentos_de_hoje": vencimentos_de_hoje,
            "tem_mais_vencimentos": tem_mais_vencimentos,
            "meu_dia_itens": meu_dia_itens,
            "meu_dia_total": meu_dia_total,
            "tem_mais_meu_dia": tem_mais_meu_dia,
        },
    )


@login_required
def meu_dia(request):
    """O que a pessoa logada precisa fazer hoje, num só lugar.

    A tela não decide o que conta como atraso nem o que é "meu": cada módulo
    responde com o que já responde e a leitura reúne (``apps.core.meu_dia``).
    Aqui só se resolve o condomínio em vista — o mesmo do resto do sistema, com
    a escolha do endereço conferida contra a lista autorizada — e se apresenta.
    """
    condominios = list(condominios_do_meu_dia(request.user))
    condominio = _condominio_da_home(request, condominios)
    itens = itens_do_meu_dia(request.user, condominio)
    return render(
        request,
        "pages/core/meu_dia.html",
        {
            "condominios": condominios,
            "condominio_atual": condominio,
            "logo_do_condominio": logo.endereco(condominio),
            "mostrar_seletor": len(condominios) > 1,
            "itens": itens,
            "total": len(itens),
            "saudacao": _saudacao_do_dia(),
        },
    )


def _saudacao_do_dia() -> str:
    """A saudação do topo, pelo horário do servidor."""
    hora = timezone.localtime().hour
    if hora < 12:
        return "Bom dia"
    if hora < 18:
        return "Boa tarde"
    return "Boa noite"


@login_required
def transparencia(request, pk):
    """Transparência agregada, sem arquivos nem detalhes administrativos."""
    balanco = _balanco_para_transparencia(request.user, pk)
    competencia_solicitada = (request.GET.get("periodo") or "").strip()
    if competencia_solicitada:
        selecionado = _balanco_para_transparencia(
            request.user, competencia_solicitada
        )
        if selecionado.condominio_id != balanco.condominio_id:
            raise Http404
        return redirect("core:transparencia", pk=selecionado.pk)

    dados_validados = _dados_validados_do_balanco(balanco)
    leitura = dados_validados.get("leitura")
    leitura = leitura if isinstance(leitura, dict) else {}
    categorias = _categorias_publicas(leitura)
    valores_das_categorias = [
        _decimal_financeiro(item.get("total"))
        for item in leitura.get("categorias") or []
        if isinstance(item, dict)
    ]
    total_categorias = (
        sum(valores_das_categorias, start=Decimal("0.00"))
        if all(valor is not None for valor in valores_das_categorias)
        else None
    )
    pode_ver_lancamentos = condominios_para_ler(request.user).filter(
        pk=balanco.condominio_id
    ).exists()
    lancamentos = (
        _lancamentos_publicos(leitura) if pode_ver_lancamentos else []
    )
    categorias_disponiveis = sorted(
        {
            (item["categoria"], item["categoria_nome"])
            for item in lancamentos
        },
        key=lambda item: item[1].casefold(),
    )
    categoria_selecionada = (request.GET.get("categoria") or "").strip()
    fornecedor_busca = (request.GET.get("fornecedor") or "").strip()[:80]
    lancamentos_filtrados = [
        item
        for item in lancamentos
        if (
            not categoria_selecionada
            or item["categoria"] == categoria_selecionada
        )
        and (
            not fornecedor_busca
            or fornecedor_busca.casefold() in item["fornecedor"].casefold()
        )
    ]
    lancamentos_filtrados.sort(
        key=lambda item: item["_valor"], reverse=True
    )
    lancamentos_identificados = sum(
        (item["_valor"] for item in lancamentos), start=Decimal("0.00")
    )
    total_declarado = _decimal_financeiro(
        dados_validados.get("despesas_total")
    )
    diferenca_categorias = (
        total_declarado - total_categorias
        if total_declarado is not None and total_categorias is not None
        else None
    )
    diferenca_detalhamento = (
        total_declarado - lancamentos_identificados
        if total_declarado is not None
        else None
    )
    balancos_visiveis = _balancos_visiveis(
        request.user, balanco.condominio
    )
    periodos = list(
        balancos_visiveis.order_by("-competencia", "-versao")[:12]
    )
    if all(periodo.pk != balanco.pk for periodo in periodos):
        periodos.append(balanco)
        periodos.sort(key=lambda periodo: (periodo.competencia, periodo.versao))
        periodos.reverse()
    evolucao = _evolucao_financeira(balancos_visiveis, balanco)
    anterior = _balanco_do_mes_anterior(request.user, balanco)
    atual_receitas = _decimal_financeiro(
        dados_validados.get("receitas_total")
    )
    atual_despesas = _decimal_financeiro(
        dados_validados.get("despesas_total")
    )
    despesas_anteriores = (
        _decimal_financeiro(
            _dados_validados_do_balanco(anterior).get("despesas_total")
        )
        if anterior
        else None
    )
    # Compromisso, fornecedores, natureza e receitas são outras leituras do mesmo
    # balanço — nenhuma delas contabiliza o período de novo. O compromisso vem
    # dos contratos com cobrança mensal em vigor, que são obrigação assumida, não
    # pagamento; por isso aparece ao lado do saldo, sem entrar nele.
    compromisso = _contratos_mensais_vigentes(balanco.condominio)
    fornecedores = (
        _fornecedores_do_periodo(lancamentos) if pode_ver_lancamentos else []
    )
    naturezas = _naturezas_agregadas(leitura)
    receitas_por_tipo = _receitas_por_tipo(leitura)
    gasto_por_unidade = _gasto_por_unidade(atual_despesas, balanco.condominio)

    variacao = None
    if atual_despesas is not None and despesas_anteriores is not None:
        diferenca = atual_despesas - despesas_anteriores
        variacao = {
            "diferenca": _moeda_da_home(abs(diferenca)),
            "direcao": (
                "aumentaram"
                if diferenca > 0
                else "diminuíram"
                if diferenca < 0
                else "permaneceram iguais"
            ),
            "percentual": (
                _decimal_para_tela((abs(diferenca) / despesas_anteriores) * 100)
                if despesas_anteriores and diferenca
                else None
            ),
        }

    return render(
        request,
        "pages/core/transparencia.html",
        {
            "balanco": balanco,
            "pode_ver_lancamentos": pode_ver_lancamentos,
            "competencia": _competencia_para_tela(balanco.competencia),
            "receitas": _moeda_da_home(
                atual_receitas
            ),
            "despesas": _moeda_da_home(atual_despesas),
            "saldo_periodo": _moeda_da_home(
                atual_receitas - atual_despesas
                if atual_receitas is not None and atual_despesas is not None
                else None
            ),
            "categorias": categorias,
            "total_categorias": _moeda_da_home(total_categorias),
            "diferenca_categorias": _moeda_da_home(
                abs(diferenca_categorias)
                if diferenca_categorias is not None
                else None
            ),
            "categorias_incompletas": (
                diferenca_categorias is not None and diferenca_categorias != 0
            ),
            "categorias_excedem_total": (
                diferenca_categorias is not None and diferenca_categorias < 0
            ),
            "periodos": periodos,
            "evolucao": evolucao,
            "categorias_disponiveis": categorias_disponiveis,
            "categoria_selecionada": categoria_selecionada,
            "fornecedor_busca": fornecedor_busca,
            "lancamentos": [
                {key: value for key, value in item.items() if key != "_valor"}
                for item in lancamentos_filtrados
            ],
            "principais_gastos": [
                {key: value for key, value in item.items() if key != "_valor"}
                for item in lancamentos_filtrados[:3]
            ],
            "total_detalhado": _moeda_da_home(lancamentos_identificados),
            "diferenca_detalhamento": _moeda_da_home(
                abs(diferenca_detalhamento)
                if diferenca_detalhamento is not None
                else None
            ),
            "detalhamento_incompleto": (
                diferenca_detalhamento is not None
                and diferenca_detalhamento != 0
            ),
            "detalhamento_excede_total": (
                diferenca_detalhamento is not None
                and diferenca_detalhamento < 0
            ),
            "balanco_anterior": anterior,
            "competencia_anterior": (
                _competencia_para_tela(anterior.competencia) if anterior else ""
            ),
            "despesas_anteriores": _moeda_da_home(despesas_anteriores),
            "variacao": variacao,
            "compromisso": compromisso,
            "fornecedores": fornecedores,
            "naturezas": naturezas,
            "receitas_por_tipo": receitas_por_tipo,
            "gasto_por_unidade": gasto_por_unidade,
        },
    )


def _condominio_da_home(request, condominios):
    """Resolve o condomínio pedido dentro do escopo financeiro da pessoa."""
    escolhido = (request.GET.get("condominio") or "").strip()
    try:
        escolhido = UUID(escolhido)
    except (ValueError, TypeError, AttributeError):
        escolhido = None
    if escolhido:
        return next(
            (condominio for condominio in condominios if condominio.pk == escolhido),
            None,
        ) or (condominios[0] if condominios else None)
    return condominios[0] if condominios else None


def _resumo_financeiro_da_home(usuario, condominio) -> dict:
    """Lê totais apenas de balanço oficial e permitido para este perfil."""
    vazio = {"disponivel": False, "morador": False}
    if condominio is None:
        return vazio

    if condominios_para_ler(usuario).filter(pk=condominio.pk).exists():
        balancos = BalancoMensal.objects.filter(
            condominio=condominio,
            status__in=(StatusBalanco.VALIDADO, StatusBalanco.PUBLICADO),
        )
        morador = False
    elif condominios_para_ler_publicados(usuario).filter(pk=condominio.pk).exists():
        balancos = BalancoMensal.objects.filter(
            condominio=condominio, status=StatusBalanco.PUBLICADO
        )
        morador = True
    else:
        return vazio

    balanco = balancos.order_by("-competencia", "-versao").first()
    compromissos = _contratos_mensais_vigentes(condominio)
    if balanco is None:
        return {
            "disponivel": True,
            "morador": morador,
            "sem_balanco": True,
            "condominio": condominio,
            "contratos_mensais": compromissos,
        }

    validado = _dados_validados_do_balanco(balanco)
    receitas = _decimal_financeiro(validado.get("receitas_total"))
    despesas = _decimal_financeiro(validado.get("despesas_total"))
    saldo_periodo = receitas - despesas if receitas is not None and despesas is not None else None
    return {
        "disponivel": True,
        "morador": morador,
        "sem_balanco": False,
        "balanco": balanco,
        "competencia": _competencia_para_tela(balanco.competencia),
        "receitas": _moeda_da_home(receitas),
        "despesas": _moeda_da_home(despesas),
        "saldo_periodo": _moeda_da_home(saldo_periodo),
        "contratos_mensais": compromissos,
    }


def _contratos_mensais_vigentes(condominio) -> dict | None:
    """Resume obrigações de contrato sem tratá-las como pagamento pendente."""
    contratos = ContratoFornecedor.objects.filter(
        condominio=condominio,
        periodicidade_cobranca=PeriodicidadeCobranca.MENSAL,
    ).vigentes()
    agregado = contratos.aggregate(
        quantidade=Count("pk"),
        sem_valor=Count("pk", filter=Q(valor__isnull=True)),
        total=Sum("valor"),
    )
    if not agregado["quantidade"]:
        return None
    return {
        "quantidade": agregado["quantidade"],
        "valor": (
            _moeda_da_home(agregado["total"])
            if not agregado["sem_valor"] and agregado["total"] is not None
            else None
        ),
    }


def _dados_validados_do_balanco(balanco) -> dict:
    dados = balanco.dados_validados
    return dados if isinstance(dados, dict) else {}


def _balanco_para_transparencia(usuario, pk):
    """Autoriza gestão em dados confirmados e moradores só no que foi publicado."""
    return get_object_or_404(_balancos_visiveis(usuario), pk=pk)


def _balancos_visiveis(usuario, condominio=None):
    """Queryset de balanços no escopo financeiro permitido à pessoa."""
    gerenciaveis = condominios_para_ler(usuario)
    publicos = condominios_para_ler_publicados(usuario)
    balancos = BalancoMensal.objects.select_related("condominio").filter(
        Q(
            condominio__in=gerenciaveis,
            status__in=(StatusBalanco.VALIDADO, StatusBalanco.PUBLICADO),
        )
        | Q(condominio__in=publicos, status=StatusBalanco.PUBLICADO)
    )
    if condominio is not None:
        balancos = balancos.filter(condominio=condominio)
    return balancos


def _balanco_do_mes_anterior(usuario, balanco):
    """Retorna apenas a competência imediatamente anterior, no mesmo acesso."""
    competencia = balanco.competencia
    anterior = date(
        competencia.year - (competencia.month == 1),
        12 if competencia.month == 1 else competencia.month - 1,
        1,
    )
    return (
        _balancos_visiveis(usuario, balanco.condominio)
        .filter(competencia=anterior)
        .order_by("-versao")
        .first()
    )


def _lancamentos_publicos(leitura) -> list[dict]:
    """Prepara detalhes do demonstrativo sem expor o fluxo técnico do PDF."""
    from apps.balancos import correcoes
    from apps.balancos.categorias import rotulo

    efetivas = correcoes.despesas_efetivas(leitura)
    originais = {
        item.get("id"): item
        for item in leitura.get("despesas") or []
        if isinstance(item, dict)
    }
    resultado = []
    for item in efetivas:
        valor = _decimal_financeiro(item.get("valor"))
        if valor is None:
            continue
        original = originais.get(item.get("id"), {})
        pagina = ((original.get("procedencia") or {}).get("pagina"))
        data = item.get("data") or ""
        try:
            data = date.fromisoformat(data).strftime("%d/%m/%Y") if data else ""
        except (TypeError, ValueError):
            data = ""
        categoria = item.get("categoria_eithra") or "nao_classificado"
        resultado.append(
            {
                "descricao": item.get("descricao") or "Despesa sem descrição",
                "valor": _moeda_da_home(valor),
                "data": data,
                "fornecedor": item.get("fornecedor") or "",
                "categoria": categoria,
                "categoria_nome": rotulo(categoria),
                "origem": (
                    "Incluído pela gestão"
                    if item.get("manual")
                    else "Demonstrativo mensal"
                ),
                "pagina": pagina if isinstance(pagina, int) else None,
                "_valor": valor,
            }
        )
    return resultado


def _evolucao_financeira(balancos, balanco_atual) -> list[dict]:
    """Desenha até seis meses existentes; meses ausentes não são preenchidos."""
    meses = list(
        balancos.filter(competencia__lte=balanco_atual.competencia)
        .order_by("-competencia", "-versao")[:6]
    )
    meses.reverse()
    valores = []
    for mes in meses:
        dados = _dados_validados_do_balanco(mes)
        receitas = _decimal_financeiro(dados.get("receitas_total"))
        despesas = _decimal_financeiro(dados.get("despesas_total"))
        if receitas is None or despesas is None:
            continue
        valores.append((mes, receitas, despesas))
    maximo = max(
        (max(receitas, despesas) for _, receitas, despesas in valores),
        default=Decimal("0"),
    )
    return [
        {
            "competencia": _competencia_para_tela(mes.competencia),
            "receitas": _moeda_da_home(receitas),
            "despesas": _moeda_da_home(despesas),
            "largura_receitas": (
                int(receitas / maximo * 100) if maximo > 0 else 0
            ),
            "largura_despesas": (
                int(despesas / maximo * 100) if maximo > 0 else 0
            ),
        }
        for mes, receitas, despesas in valores
    ]


def _categorias_publicas(leitura) -> list[dict]:
    """Mostra categorias agregadas, sem revelar lançamentos ou documentos."""
    from apps.balancos.categorias import rotulo

    categorias = []
    for item in leitura.get("categorias") or []:
        valor = _decimal_financeiro(item.get("total"))
        if valor is None or valor <= 0:
            continue
        categorias.append(
            {
                "codigo": item.get("categoria", ""),
                "nome": rotulo(item.get("categoria", "")),
                "_valor": valor,
            }
        )
    categorias.sort(key=lambda item: item["_valor"], reverse=True)
    return [
        {
            "codigo": item["codigo"],
            "nome": item["nome"],
            "valor": _moeda_da_home(item["_valor"]),
        }
        for item in categorias[:6]
    ]


def _fornecedores_do_periodo(lancamentos: list[dict]) -> list[dict]:
    """Outro ângulo sobre as mesmas despesas: com quem mais se gastou.

    Não é uma segunda contabilização — é o mesmo conjunto lido por fornecedor,
    por isso não entra em total nenhum. Fornecedor sem nome no demonstrativo não
    vira linha: inventar um rótulo seria pior do que omitir.
    """
    acumulado: dict[str, Decimal] = {}
    for item in lancamentos:
        nome = (item.get("fornecedor") or "").strip()
        if not nome:
            continue
        acumulado[nome] = acumulado.get(nome, Decimal("0.00")) + item["_valor"]
    ordenados = sorted(
        acumulado.items(), key=lambda par: (-par[1], par[0].casefold())
    )
    return [
        {"nome": nome, "valor": _moeda_da_home(valor)}
        for nome, valor in ordenados[:5]
    ]


def _naturezas_agregadas(leitura) -> list[dict]:
    """Separa as despesas entre 'do dia a dia' e 'extraordinárias'.

    Lê a separação que a leitura já produziu por categoria — nada é
    reclassificado aqui. Sem valor classificado, a seção não aparece: é
    preferível à classificação inventada.
    """
    totais = {
        "dia_a_dia": Decimal("0.00"),
        "extraordinaria": Decimal("0.00"),
        "a_classificar": Decimal("0.00"),
    }
    for item in leitura.get("categorias") or []:
        if not isinstance(item, dict):
            continue
        for chave, destino in (
            ("recorrente", "dia_a_dia"),
            ("extraordinaria", "extraordinaria"),
            ("nao_determinada", "a_classificar"),
        ):
            valor = _decimal_financeiro(item.get(chave))
            if valor:
                totais[destino] += valor
    rotulos = (
        ("dia_a_dia", "Despesas do dia a dia"),
        ("extraordinaria", "Obras e gastos extraordinários"),
        ("a_classificar", "Ainda sem classificação"),
    )
    return [
        {"rotulo": rotulo, "valor": _moeda_da_home(totais[chave])}
        for chave, rotulo in rotulos
        if totais[chave] > 0
    ]


def _receitas_por_tipo(leitura) -> list[dict]:
    """Agrupa as entradas pelo agrupamento do próprio demonstrativo.

    Reaproveita o que o documento já separou; o que não vem agrupado entra como
    'Outras receitas'. Não há classificador de receita no domínio, e inventar um
    seria criar regra onde não existe fonte.
    """
    acumulado: dict[str, Decimal] = {}
    for item in leitura.get("receitas") or []:
        if not isinstance(item, dict):
            continue
        valor = _decimal_financeiro(item.get("valor"))
        if valor is None:
            continue
        nome = (item.get("categoria_original") or "").strip() or "Outras receitas"
        acumulado[nome] = acumulado.get(nome, Decimal("0.00")) + valor
    ordenados = sorted(
        acumulado.items(), key=lambda par: (-par[1], par[0].casefold())
    )
    return [
        {"nome": nome, "valor": _moeda_da_home(valor)}
        for nome, valor in ordenados[:6]
        if valor > 0
    ]


def _gasto_por_unidade(despesas: Decimal | None, condominio) -> str | None:
    """Média simples por unidade — nunca rateio nem fração ideal.

    O condomínio tem N unidades; dividir o total por N é uma leitura grosseira, e
    a tela diz isso. Sem o número de unidades cadastrado, não se estima.
    """
    unidades = getattr(condominio, "quantidade_unidades", None)
    if despesas is None or not unidades or unidades <= 0:
        return None
    return _moeda_da_home(despesas / Decimal(unidades))


def _decimal_financeiro(valor) -> Decimal | None:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return numero if numero.is_finite() else None


def _moeda_da_home(valor: Decimal | None) -> str | None:
    if valor is None:
        return None
    formatado = f"{valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {formatado}"


def _decimal_para_tela(valor: Decimal) -> str:
    return f"{valor.quantize(Decimal('0.1')):f}".replace(".", ",")


def _competencia_para_tela(competencia: date) -> str:
    meses = (
        "janeiro", "fevereiro", "março", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
    )
    return f"{meses[competencia.month - 1].capitalize()} de {competencia.year}"


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


@login_required
def acompanhamento(request, origem, pk):
    """A visita aberta: o registro canônico e os gestos da jornada.

    A tela não cadastra nada novo — mostra o registro original (a manutenção, a
    ocorrência, a demanda) e oferece começar, atualizar e encerrar, sempre pelo
    serviço do próprio domínio. Um identificador fora do escopo da pessoa não
    chega até aqui: a leitura já o recusa como inexistente.
    """
    registro = obter_acompanhamento(origem=origem, pk=pk, usuario=request.user)
    return render(
        request,
        "pages/core/acompanhamento.html",
        {"acompanhamento": registro},
    )


@login_required
@require_POST
def acompanhar(request, origem, pk):
    """Executa o gesto escolhido — sempre revalidando tudo no backend.

    O gesto e o registro são reconferidos aqui: o que veio do navegador não é
    autoridade nem sobre a permissão nem sobre o que mudar. O momento do início
    é o do servidor, gravado pelo serviço do domínio — nunca o relógio da tela.
    """
    registro = obter_acompanhamento(origem=origem, pk=pk, usuario=request.user)
    gesto = (request.POST.get("gesto") or "").strip()
    texto = (request.POST.get("texto") or "").strip()
    resultado = (request.POST.get("resultado") or "").strip()

    try:
        if gesto == GESTO_INICIAR:
            iniciar(registro, usuario=request.user)
            messages.success(request, "Acompanhamento iniciado.")
        elif gesto == GESTO_ATUALIZAR:
            registrar_atualizacao(registro, usuario=request.user, texto=texto)
            messages.success(request, "Atualização registrada.")
        elif gesto == GESTO_ENCERRAR:
            encerrar(
                registro, usuario=request.user, texto=texto, resultado=resultado
            )
            messages.success(request, "Acompanhamento encerrado.")
        else:
            raise Http404("Gesto de acompanhamento desconhecido.")
    except (ErroDeManutencao, ErroDeRotina, ErroDeDemanda) as erro:
        messages.error(request, str(erro))
    except PermissionDenied as erro:
        messages.error(request, str(erro) or "Você não pode fazer isto.")

    return redirect("core:acompanhamento", origem=origem, pk=pk)


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


def _linha_de_demanda(demanda) -> dict:
    """O pouco que a Home mostra de uma demanda em aberto.

    A ordem e o corte vêm do domínio; aqui só se apresenta o que a linha
    precisa — o título e, em uma linha, onde a demanda acontece e o prazo,
    quando há. Nenhuma regra de prioridade ou de prazo é recalculada aqui: o
    prazo aparece como a data registrada, sem juízo de atraso.
    """
    contexto = [demanda.condominio.nome]
    local = demanda.local_para_exibir
    if local:
        contexto.append(local)
    if demanda.prazo:
        contexto.append(f"prazo {demanda.prazo:%d/%m/%Y}")

    return {
        "id": demanda.pk,
        "titulo": demanda.titulo,
        "contexto": " · ".join(contexto),
    }


def _linha_de_excecao(item) -> dict:
    """O pouco que a Home mostra de uma exceção que pede decisão.

    O motivo e a ação vêm do domínio (``apps.demandas.excecoes``); aqui só se
    monta a linha — o título da demanda, o motivo, onde ela acontece e o que
    fazer. Nada aqui promete um botão que o domínio ainda não oferece: o único
    destino é o contexto da demanda, onde a decisão acontece.
    """
    demanda = item.demanda
    contexto = [demanda.condominio.nome]
    local = demanda.local_para_exibir
    if local:
        contexto.append(local)

    return {
        "id": demanda.pk,
        "titulo": demanda.titulo,
        "motivo": item.titulo,
        "acao": item.acao_sugerida,
        "tom": item.tom,
        "contexto": " · ".join(contexto),
    }


# Para onde o clique da área leva: a tela enxuta de acompanhamento, que resolve
# o registro canônico de cada origem e oferece ali os gestos do domínio. É o
# único destino da área — a leitura derivada diz o que fazer, não para onde ir.
_DESTINO_DA_ACAO = "core:acompanhamento"


def _local_da_acao(item) -> str:
    """Onde a ação acontece, quando o registro sabe dizer.

    Cada origem guarda o lugar de um jeito — a manutenção e a demanda têm um
    local próprio, a ocorrência de rotina herda o da rotina. Aqui só se lê, sem
    inventar um lugar que o registro não tem.
    """
    if item.origem == ORIGEM_MANUTENCAO:
        return item.objeto.local.nome if item.objeto.local_id else ""
    if item.origem == ORIGEM_ROTINA:
        local = item.objeto.rotina.local
        return local.nome if local else ""
    return item.objeto.local_para_exibir


def _linha_da_acao(item) -> dict:
    """O pouco que a Home mostra de uma ação da supervisão.

    O nome, o rótulo, a mensagem e o tom vêm da leitura derivada
    (``apps.core.supervisao``); aqui só se monta a linha — onde acontece e para
    onde o clique leva. Nenhuma regra de bloco ou de ordem é recalculada aqui.
    """
    condominio = getattr(item.objeto, "condominio", None)
    contexto = [condominio.nome] if condominio is not None else []
    local = _local_da_acao(item)
    if local:
        contexto.append(local)

    return {
        "titulo": item.nome,
        "rotulo": item.rotulo,
        "mensagem": item.mensagem,
        "acao": item.acao_sugerida,
        "tom": item.tom,
        "bloco": item.bloco,
        "contexto": " · ".join(contexto),
        "url": reverse(_DESTINO_DA_ACAO, args=[item.origem, item.identificador]),
    }


def _blocos_da_supervisao(usuario):
    """Agrupa as ações da supervisão nos blocos da área, ou ``None`` sem papel.

    A ordem e o bloco de cada item vêm de ``apps.core.supervisao`` — aqui só se
    agrupa o que já veio pronto. Sem o papel de supervisão, devolve ``None``:
    isso distingue "não é para você" de "não há nada", e a área não aparece.
    """
    if not pode_ver_acoes_da_supervisao(usuario):
        return None

    acoes = [
        _linha_da_acao(item)
        for item in obter_acoes_da_supervisao(usuario=usuario)
    ]
    blocos = []
    for bloco in BlocoDaSupervisao:
        itens = [acao for acao in acoes if acao["bloco"] == bloco]
        if itens:
            blocos.append({"rotulo": bloco.label, "itens": itens})
    return blocos


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
