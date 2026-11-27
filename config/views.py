"""Páginas de erro e o manifest no nível do projeto.

Cada página de erro reaproveita o shell visual do Eithra para manter a
identidade mesmo quando algo dá errado. As mensagens são humanas e não expõem
detalhes técnicos ao usuário final.

Além delas, este módulo serve o manifest que o celular lê para "Adicionar à tela
inicial": é o que faz o Eithra abrir como aplicativo, e não como uma aba.
"""
from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render


def manifest(request: HttpRequest) -> JsonResponse:
    """O manifest do aplicativo instalável no celular.

    Sem service worker nesta etapa: o objetivo é o ícone abrir em tela cheia,
    com o nome e as cores do sistema. O endereço do ícone é montado a partir de
    STATIC_URL, e não pelo armazenamento de estáticos: assim o manifest não
    depende de o collectstatic ter rodado para ser montado.
    """
    return JsonResponse(
        {
            "name": "Eithra",
            "short_name": "Eithra",
            "description": "Eithra — gestão condominial",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#f7f6f9",
            "theme_color": "#36545b",
            "icons": [
                {
                    "src": f"{settings.STATIC_URL}img/logorvemenu.png",
                    "sizes": "1024x1024",
                    "type": "image/png",
                    "purpose": "any",
                },
            ],
        },
        content_type="application/manifest+json",
    )


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
