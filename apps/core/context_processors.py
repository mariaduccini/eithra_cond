"""Contexto compartilhado por todas as telas.

O menu é declarado em um único lugar. Só entra aqui o que já existe de verdade
no sistema: nada de item que leve a uma página que ainda não existe.

Cada item pode declarar para quem ele existe. Isso mantém a decisão fora dos
templates — nada de condição de papel espalhada pelas telas — e, mais
importante, o menu **não** decide acesso: ele pergunta ao domínio, com a mesma
regra que o backend usa. A proteção continua na view.

Um item pode reunir outros em ``filhos``. O grupo existe enquanto existir ao
menos um filho visível, e o destaque da tela aberta sobe do filho para o grupo.

Além do menu, este módulo responde à outra pergunta do shell: **em qual
condomínio a pessoa está operando agora**. É essa a marca do alto do menu — a
logo do condomínio em vista, ou as iniciais dele quando não há logo.
"""
import uuid

from apps.balancos.permissoes import pode_importar_em_algum_condominio
from apps.cadastros.permissoes import pode_ver_cadastros_em_algum
from apps.comunicacao.permissoes import pode_ver_comunicacao_em_algum
from apps.comunidade.permissoes import pode_ver_comunidade_em_algum
from apps.condominios import logo
from apps.demandas.permissoes import pode_ver_alguma_demanda
from apps.estoque.permissoes import pode_ver_compras_em_algum, pode_ver_estoque_em_algum
from apps.governanca.permissoes import pode_ver_governanca_em_algum
from apps.manutencoes.permissoes import pode_ver_alguma_manutencao
from apps.portaria.permissoes import condominios_para_ver, pode_ver_portaria_em_algum
from apps.rotinas.permissoes import pode_ver_alguma_rotina

NAVIGATION = [
    {"label": "Painel", "view_name": "core:home", "icon": "bx-home"},
    {
        "label": "Portaria",
        "view_name": "portaria:painel",
        "icon": "bx-door-open",
        "visivel_para": pode_ver_portaria_em_algum,
    },
    {
        "label": "Demandas",
        "view_name": "demandas:listagem",
        "icon": "bx-list-check",
        # Módulo de acompanhamento operacional: quem vê a demanda em algum
        # condomínio enxerga o item. O menu não autoriza — a view é que decide.
        "visivel_para": pode_ver_alguma_demanda,
    },
    {
        "label": "Manutenções",
        "view_name": "manutencoes:listagem",
        "icon": "bx-wrench",
        # Módulo de quem executa e acompanha o serviço: quem vê a manutenção em
        # algum condomínio enxerga o item. O menu não autoriza — a view decide.
        "visivel_para": pode_ver_alguma_manutencao,
    },
    {
        "label": "Comunicação",
        "view_name": "comunicacao:listagem",
        "icon": "bx-megaphone",
        # Capacidade transversal: o comunicado nasce de uma demanda, de uma
        # manutenção ou de nada. Quem acompanha a comunicação em algum
        # condomínio enxerga o item. O menu não autoriza — a view é que decide.
        "visivel_para": pode_ver_comunicacao_em_algum,
    },
    {
        # O que se repete sem depender de alguém lembrar. O rótulo fala de
        # "atividades programadas" porque quem opera não precisa saber a
        # diferença técnica entre rotina e preventiva: as duas chegam aqui como
        # o que está marcado para acontecer. O endereço e o nome interno seguem
        # sendo "rotinas" — o que mudou foi só como a porta se chama.
        "label": "Atividades programadas",
        "view_name": "rotinas:listagem",
        "icon": "bx-repeat",
        # Quem responde, supervisiona ou executa enxerga o item: a operação
        # entra pela lista de ocorrências dela, com o conteúdo filtrado. O menu
        # não autoriza — a view é que decide.
        "visivel_para": pode_ver_alguma_rotina,
    },
    {
        "label": "Estoque",
        "view_name": "estoque:itens",
        "icon": "bx-package",
        # Quem responde pelo condomínio, supervisiona ou executa a manutenção
        # enxerga o estoque — a operação entra para retirar material do serviço
        # que assume. O menu não autoriza: a view é que decide.
        "visivel_para": pode_ver_estoque_em_algum,
    },
    {
        "label": "Compras",
        "view_name": "compras:listagem",
        "icon": "bx-cart",
        # Compras são de quem responde pela gestão. A operação não entra nesta
        # fase, e por isso não recebe o item. O menu não autoriza: a view decide.
        "visivel_para": pode_ver_compras_em_algum,
    },
    {
        "label": "Cadastros",
        "icon": "bx-folder",
        # Módulo de quem responde pelo condomínio, em leitura para a supervisão.
        # Quem não enxerga os cadastros em nenhum condomínio não recebe o grupo.
        "visivel_para": pode_ver_cadastros_em_algum,
        "filhos": [
            {
                "label": "Condomínios",
                "view_name": "cadastros:condominios",
                "active_view_names": {
                    "cadastros:condominios",
                    "cadastros:condominio",
                    "cadastros:criar_condominio",
                    "cadastros:editar_condominio",
                },
            },
            {
                "label": "Pessoas e unidades",
                "view_name": "cadastros:pessoas_e_unidades",
                "visivel_para": pode_ver_comunidade_em_algum,
                "active_prefixes": ("comunidade:", "cadastros:pessoas_e_unidades"),
            },
            {"label": "Usuários e acessos", "view_name": "cadastros:acessos"},
            {"label": "Áreas e locais", "view_name": "cadastros:locais"},
            {"label": "Equipamentos", "view_name": "cadastros:equipamentos"},
            {"label": "Fornecedores", "view_name": "cadastros:fornecedores"},
            {
                "label": "Contratos",
                "view_name": "governanca:contratos",
                "visivel_para": pode_ver_governanca_em_algum,
            },
            {
                "label": "Garantias",
                "view_name": "governanca:garantias",
                "visivel_para": pode_ver_governanca_em_algum,
            },
            {
                "label": "Documentos",
                "view_name": "governanca:documentos",
                "visivel_para": pode_ver_governanca_em_algum,
            },
            {
                "label": "Tipos e categorias",
                "view_name": "cadastros:tipos_e_categorias",
                "active_prefixes": (
                    "cadastros:tipos_e_categorias",
                    "cadastros:tipos_de_local",
                    "cadastros:criar_tipo_local",
                    "cadastros:editar_tipo_local",
                    "cadastros:alternar_tipo_local",
                    "cadastros:tipos_de_equipamento",
                    "cadastros:criar_tipo_equipamento",
                    "cadastros:editar_tipo_equipamento",
                    "cadastros:alternar_tipo_equipamento",
                    "cadastros:especialidades",
                    "cadastros:criar_especialidade",
                    "cadastros:editar_especialidade",
                    "cadastros:alternar_especialidade",
                    "governanca:tipos_de_contrato",
                    "governanca:criar_tipo_contrato",
                    "governanca:editar_tipo_contrato",
                    "governanca:alternar_tipo_contrato",
                    "governanca:tipos_de_documento",
                    "governanca:criar_tipo_documento",
                    "governanca:editar_tipo_documento",
                    "governanca:alternar_tipo_documento",
                ),
            },
        ],
    },
    {
        "label": "Balanços",
        "view_name": "balancos:importacoes",
        "icon": "bx-wallet",
        # Módulo administrativo: só quem responde pelo documento. O morador
        # terá uma experiência própria de balanços publicados, em outra fase —
        # esta rota não é dele e não deve ser oferecida a ele.
        "visivel_para": pode_importar_em_algum_condominio,
    },
]


def navigation(request):
    """Disponibiliza apenas os itens de menu que fazem sentido para quem está vendo."""
    usuario = getattr(request, "user", None)
    aberta = getattr(getattr(request, "resolver_match", None), "view_name", None)
    itens = [
        _preparar(item, usuario, aberta)
        for item in NAVIGATION
        if _aparece_para(item, usuario)
    ]
    return {"navigation": itens}


def _preparar(item, usuario, aberta):
    """Copia o item com os filhos visíveis e marca o que corresponde à tela aberta.

    A cópia existe para que o destaque de hoje não fique gravado no menu de
    amanhã: ``NAVIGATION`` é declarado uma vez e compartilhado por todas as
    requisições.
    """
    filhos = [
        {**filho, "ativo": _esta_ativo(filho, aberta)}
        for filho in item.get("filhos", ())
        if _aparece_para(filho, usuario)
    ]
    ativo = _esta_ativo(item, aberta) or any(filho["ativo"] for filho in filhos)
    return {**item, "filhos": filhos, "ativo": ativo}


def _aparece_para(item, usuario) -> bool:
    """Item sem exigência aparece sempre; com exigência, quem responde é o domínio."""
    exigencia = item.get("visivel_para")
    if exigencia is None:
        return True
    return bool(exigencia(usuario))


def _esta_ativo(item, aberta) -> bool:
    """Diz se o item corresponde a esta tela aberta.

    Alguns itens de navegação representam um contexto maior que uma única rota.
    A pergunta continua simples: a tela em vista pertence a este contexto?
    """
    if not aberta:
        return False
    if item.get("view_name") == aberta:
        return True
    if aberta in item.get("active_view_names", ()):
        return True
    return any(aberta.startswith(prefixo) for prefixo in item.get("active_prefixes", ()))


# -- A marca do condomínio no alto do menu --------------------------------------
#
# O alto do menu diz em qual condomínio a pessoa está operando: a logo dele
# quando existe, as iniciais do nome quando não. É a mesma identidade que as
# telas mostram, resolvida pela mesma pergunta, para o shell nunca exibir a
# marca de um condomínio diferente do que está aberto.
#
# Duas coisas não acontecem aqui: não se monta endereço nem se fala com o
# armazenamento — quem guarda e assina a logo é ``apps.condominios.logo`` — e
# nada derruba a página. Sem condomínio, sem logo ou com o armazenamento fora do
# ar, a resposta é simplesmente menos: nenhuma marca, ou as iniciais, ou nada.


def marca_do_condominio(request):
    """A marca do condomínio em vista, para o alto do menu."""
    atual = _condominio_em_vista(request)
    return {
        "condominio_da_marca": atual,
        "logo_da_marca": logo.endereco(atual) if atual else "",
    }


def _condominio_em_vista(request):
    """Qual condomínio o shell mostra agora.

    Mesma regra das telas: com vários, vale o escolhido no endereço — sempre
    conferido contra a lista autorizada, nunca aceito pelo que diz —; sem
    escolha, o primeiro. Diferente das telas, nada é recusado aqui: um
    identificador estranho, ou de um condomínio sem acesso, não interrompe a
    página — apenas devolve o primeiro.
    """
    condominios = condominios_para_ver(getattr(request, "user", None))
    escolhido = request.POST.get("condominio") or request.GET.get("condominio")
    if escolhido and _eh_uuid(escolhido):
        return condominios.filter(pk=escolhido).first() or condominios.first()
    return condominios.first()


def _eh_uuid(valor) -> bool:
    """Diz se o valor recebido pode ser uma chave de registro."""
    try:
        uuid.UUID(str(valor))
    except (ValueError, TypeError, AttributeError):
        return False
    return True
