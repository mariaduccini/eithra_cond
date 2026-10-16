"""Páginas de erro no nível do projeto.

Cada página reaproveita o shell visual do Eithra para manter a identidade
mesmo quando algo dá errado. As mensagens são humanas e não expõem detalhes
técnicos ao usuário final.
"""
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def permission_denied_view(
    request: HttpRequest, exception: Exception | None = None
) -> HttpResponse:
    """403 — o acesso existe, mas não é permitido para este usuário."""
    return render(request, "errors/403.html", status=403)


def page_not_found_view(
    request: HttpRequest, exception: Exception | None = None
) -> HttpResponse:
    """404 — o conteúdo não existe ou não é acessível a este usuário."""
    return render(request, "errors/404.html", status=404)


def server_error_view(request: HttpRequest) -> HttpResponse:
    """500 — falha inesperada na aplicação."""
    return render(request, "errors/500.html", status=500)
