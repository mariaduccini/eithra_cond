"""Inspeção técnica das demandas no Django Admin.

O Admin não é a experiência do domínio: existe apenas para conferir a estrutura
durante a fundação. Por isso não há ação em massa, e a exclusão é bloqueada —
a demanda e o que aconteceu com ela são histórico operacional do condomínio.
"""
from django.contrib import admin

from apps.demandas.models import Demanda, HistoricoDemanda


@admin.register(Demanda)
class DemandaAdmin(admin.ModelAdmin):
    list_display = (
        "titulo",
        "condominio",
        "status",
        "prioridade",
        "responsavel",
        "prazo",
        "ativo",
    )
    list_filter = ("status", "prioridade", "origem", "ativo")
    search_fields = ("titulo", "descricao", "condominio__nome")
    autocomplete_fields = ("condominio", "responsavel", "criado_por")
    readonly_fields = ("id", "criado_em", "atualizado_em")
    ordering = ("-criado_em",)

    def has_delete_permission(self, request, obj=None) -> bool:
        """Demanda é histórico operacional; desativa-se, não se apaga pela tela."""
        return False


@admin.register(HistoricoDemanda)
class HistoricoDemandaAdmin(admin.ModelAdmin):
    list_display = ("demanda", "tipo_evento", "usuario", "data_hora")
    list_filter = ("tipo_evento",)
    search_fields = ("descricao", "demanda__titulo")
    autocomplete_fields = ("demanda", "usuario")
    readonly_fields = ("id", "data_hora")
    ordering = ("-data_hora",)

    def has_delete_permission(self, request, obj=None) -> bool:
        """O histórico é append-only: não se apaga pela interface administrativa."""
        return False
