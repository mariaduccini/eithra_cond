"""Roteamento raiz do Eithra."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from config import views


def _endereco_do_admin() -> str:
    """O caminho do admin, com barra no fim e sem barra no começo.

    O valor vem de EITHRA_ADMIN_URL e é tratado como entrada do operador: com
    ou sem barras, ele vira uma rota válida — nunca uma rota quebrada por causa
    de uma barra a mais ou a menos.
    """
    return settings.EITHRA_ADMIN_URL.strip("/") + "/"


urlpatterns = [
    # O manifest do aplicativo instalável. Fica na raiz e sem exigir entrada:
    # o navegador do celular o busca ao "Adicionar à tela inicial".
    path("manifest.webmanifest", views.manifest, name="manifest"),
    path("", include("apps.core.urls")),
    path("conta/", include("apps.accounts.urls")),
    path("balancos/", include("apps.balancos.urls")),
    path("demandas/", include("apps.demandas.urls")),
    path("manutencoes/", include("apps.manutencoes.urls")),
    path("comunicacao/", include("apps.comunicacao.urls")),
    path("rotinas/", include("apps.rotinas.urls")),
    path("estoque/", include("apps.estoque.urls")),
    path("compras/", include("apps.estoque.urls_compras")),
    path("cadastros/", include("apps.cadastros.urls")),
    path("governanca/", include("apps.governanca.urls")),
    path("projetos/", include("apps.governanca.urls_projetos")),
    path("comunidade/", include("apps.comunidade.urls")),
    path("portaria/", include("apps.portaria.urls")),
    path(_endereco_do_admin(), admin.site.urls),
]

# Páginas de erro próprias (usam o mesmo shell visual do sistema).
handler403 = views.permission_denied_view
handler404 = views.page_not_found_view
handler500 = views.server_error_view

# No desenvolvimento o provedor local guarda os arquivos sob MEDIA_ROOT, e é o
# próprio Django que os serve. Quem também serve é o acesso remoto
# (config.settings.remoto): lá o armazenamento é local, mas a depuração está
# desligada — sem esta rota, as logos e as fotos não apareceriam no celular.
if settings.DEBUG or getattr(settings, "EITHRA_SERVIR_MEDIA", False):
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
