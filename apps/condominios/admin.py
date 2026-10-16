"""Administração de condomínios e acessos."""
from django.contrib import admin

from apps.condominios.models import AcessoUsuarioCondominio, Condominio


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
