"""Administração de condomínios e acessos."""
from django.contrib import admin

from apps.condominios.models import (
    AcessoUsuarioCondominio,
    Condominio,
    HistoricoAcessoCondominio,
)


@admin.register(Condominio)
class CondominioAdmin(admin.ModelAdmin):
    list_display = ("nome", "ativo", "criado_em")
    list_filter = ("ativo",)
    search_fields = ("nome",)


@admin.register(AcessoUsuarioCondominio)
class AcessoUsuarioCondominioAdmin(admin.ModelAdmin):
    list_display = ("usuario", "condominio", "papel", "ativo")
    list_filter = ("papel", "ativo", "condominio")
    search_fields = ("usuario__full_name", "usuario__email", "condominio__nome")
    autocomplete_fields = ("usuario", "condominio")


@admin.register(HistoricoAcessoCondominio)
class HistoricoAcessoCondominioAdmin(admin.ModelAdmin):
    """O histórico é append-only: consulta-se aqui, não se edita nem se apaga."""

    list_display = ("data_hora", "evento", "acesso", "usuario_executor")
    list_filter = ("evento",)
    search_fields = (
        "acesso__usuario__full_name",
        "acesso__usuario__email",
        "acesso__condominio__nome",
        "usuario_executor__email",
    )
    readonly_fields = (
        "acesso",
        "usuario_executor",
        "evento",
        "dados_anteriores",
        "dados_novos",
        "data_hora",
    )

    def has_add_permission(self, request) -> bool:
        """A linha do histórico nasce da operação, nunca da tela."""
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        """Uma linha do histórico não é reescrita."""
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        """Uma linha do histórico não é apagada."""
        return False

