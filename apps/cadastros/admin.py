"""Inspeção técnica dos cadastros no Django Admin.

O Admin não é a experiência do domínio: existe apenas para conferir a estrutura
durante a fundação. Por isso não há ação em massa, e a exclusão é bloqueada —
um cadastro sai de uso sendo desativado, e não apagado, para que o que aponta
para ele continue legível.
"""
from django.contrib import admin

from apps.cadastros.models import Equipamento, Fornecedor, LocalOperacional


@admin.register(LocalOperacional)
class LocalOperacionalAdmin(admin.ModelAdmin):
    list_display = ("nome", "condominio", "tipo", "ativo")
    list_filter = ("tipo", "ativo", "condominio")
    search_fields = ("nome", "descricao", "condominio__nome")
    autocomplete_fields = ("condominio", "criado_por")
    readonly_fields = ("id", "criado_em", "atualizado_em")
    ordering = ("condominio__nome", "nome")

    def has_delete_permission(self, request, obj=None) -> bool:
        """Desativa-se o local, não se apaga pela tela."""
        return False


@admin.register(Equipamento)
class EquipamentoAdmin(admin.ModelAdmin):
    list_display = ("nome", "condominio", "local", "tipo_texto", "ativo")
    list_filter = ("ativo", "condominio", "local")
    search_fields = (
        "nome",
        "tipo_texto",
        "fabricante",
        "modelo",
        "numero_serie",
        "patrimonio",
        "condominio__nome",
    )
    autocomplete_fields = ("condominio", "local", "criado_por")
    readonly_fields = ("id", "criado_em", "atualizado_em")
    ordering = ("condominio__nome", "nome")

    def has_delete_permission(self, request, obj=None) -> bool:
        """Desativa-se o equipamento, não se apaga pela tela."""
        return False


@admin.register(Fornecedor)
class FornecedorAdmin(admin.ModelAdmin):
    list_display = ("nome_razao_social", "condominio", "tipo_pessoa", "ativo")
    list_filter = ("tipo_pessoa", "ativo", "condominio")
    search_fields = (
        "nome_razao_social",
        "nome_fantasia",
        "documento",
        "email",
        "contato",
        "condominio__nome",
    )
    autocomplete_fields = ("condominio", "criado_por")
    readonly_fields = ("id", "criado_em", "atualizado_em")
    ordering = ("condominio__nome", "nome_razao_social")

    def has_delete_permission(self, request, obj=None) -> bool:
        """Desativa-se o fornecedor, não se apaga pela tela."""
        return False
