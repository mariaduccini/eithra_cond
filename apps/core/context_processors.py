"""Contexto compartilhado por todas as telas.

O menu é declarado em um único lugar. Só entra aqui o que já existe de verdade
no sistema: nada de item que leve a uma página que ainda não existe.

Cada item pode declarar para quem ele existe. Isso mantém a decisão fora dos
templates — nada de condição de papel espalhada pelas telas — e, mais
importante, o menu **não** decide acesso: ele pergunta ao domínio, com a mesma
regra que o backend usa. A proteção continua na view.
"""
from apps.balancos.permissoes import pode_importar_em_algum_condominio

NAVIGATION = [
    {"label": "Painel", "view_name": "core:home", "icon": "bx-home-smile"},
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
    itens = [item for item in NAVIGATION if _aparece_para(item, usuario)]
    return {"navigation": itens}


def _aparece_para(item, usuario) -> bool:
    """Item sem exigência aparece sempre; com exigência, quem responde é o domínio."""
    exigencia = item.get("visivel_para")
    if exigencia is None:
        return True
    return bool(exigencia(usuario))
