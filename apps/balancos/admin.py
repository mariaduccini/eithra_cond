"""Inspeção técnica dos balanços no Django Admin.

O Admin não é a experiência final do domínio: existe apenas para conferir a
estrutura durante a fundação. Por isso não há ação em massa, nem publicação, nem
substituição automática, e a exclusão é bloqueada para não perder histórico
financeiro.
"""
from django.contrib import admin

from apps.balancos.models import BalancoMensal, DocumentoBalanco


@admin.register(BalancoMensal)
class BalancoMensalAdmin(admin.ModelAdmin):
    list_display = (
        "competencia",
        "condominio",
        "versao",
        "status",
        "status_processamento",
        "layout_detectado",
    )
    list_filter = ("status", "status_processamento", "layout_detectado")
    search_fields = ("condominio__nome",)
    autocomplete_fields = ("condominio", "substitui", "criado_por", "validado_por")
    readonly_fields = ("id", "criado_em", "atualizado_em")
    ordering = ("-competencia", "-versao")

    def has_delete_permission(self, request, obj=None) -> bool:
        """Histórico financeiro não é apagado pela interface administrativa."""
        return False


@admin.register(DocumentoBalanco)
class DocumentoBalancoAdmin(admin.ModelAdmin):
    list_display = ("nome_original", "balanco", "tamanho_bytes", "ativo", "enviado_em")
    list_filter = ("ativo", "storage_provider")
    search_fields = ("nome_original", "sha256", "balanco__condominio__nome")
    autocomplete_fields = ("balanco", "enviado_por")
    readonly_fields = ("id", "enviado_em")

    def has_delete_permission(self, request, obj=None) -> bool:
        """O documento original é a fonte de verdade; não se apaga pela tela."""
        return False
