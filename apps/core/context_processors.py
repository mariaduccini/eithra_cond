"""Contexto compartilhado por todas as telas.

O menu é declarado em um único lugar. Só entra aqui o que já existe de
verdade no sistema: nada de item que leve a uma página que ainda não existe.
"""

NAVIGATION = [
    {"label": "Painel", "view_name": "core:home", "icon": "bx-home-smile"},
    {"label": "Balanços", "view_name": "balancos:importacoes", "icon": "bx-wallet"},
]


def navigation(request):
    """Disponibiliza os itens do menu para a sidebar."""
    return {"navigation": NAVIGATION}
