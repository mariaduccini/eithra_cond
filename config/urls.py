"""Roteamento raiz do Eithra."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from config import views

urlpatterns = [
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
    path("comunidade/", include("apps.comunidade.urls")),
    path("portaria/", include("apps.portaria.urls")),
    path("admin/", admin.site.urls),
]

# Páginas de erro próprias (usam o mesmo shell visual do sistema).
handler403 = views.permission_denied_view
handler404 = views.page_not_found_view
handler500 = views.server_error_view

# No desenvolvimento o provedor local guarda os arquivos sob MEDIA_ROOT, e é o
# próprio Django que os serve. Em produção quem serve é o provedor remoto, e
# esta rota nem existe.
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
