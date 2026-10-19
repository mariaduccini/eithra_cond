"""Inspeção técnica das manutenções no Django Admin.

O Admin não é a experiência do domínio: existe apenas para conferir a estrutura
durante a fundação. Por isso não há ação em massa, e a exclusão é bloqueada —
a manutenção e o que aconteceu com ela são histórico operacional do condomínio.
"""
from django.contrib import admin

from apps.manutencoes.models import HistoricoManutencao, Manutencao


@admin.register(Manutencao)
class ManutencaoAdmin(admin.ModelAdmin):
    list_display = (
        "titulo",
        "condominio",
        "tipo",
        "status",
        "prioridade",
        "responsavel",
        "data_prevista",
        "ativo",
    )
    list_filter = ("tipo", "status", "prioridade", "ativo")
    search_fields = ("titulo", "descricao_problema", "descricao_servico", "condominio__nome")
    autocomplete_fields = (
        "condominio",
        "demanda_origem",
        "local",
        "equipamento",
        "fornecedor",
        "responsavel",
        "criado_por",
    )
    readonly_fields = ("id", "criado_em", "atualizado_em")
    ordering = ("-criado_em",)

    def has_delete_permission(self, request, obj=None) -> bool:
        """Manutenção é histórico operacional; desativa-se, não se apaga pela tela."""
        return False


@admin.register(HistoricoManutencao)
class HistoricoManutencaoAdmin(admin.ModelAdmin):
    list_display = ("manutencao", "tipo_evento", "usuario", "data_hora")
    list_filter = ("tipo_evento",)
    search_fields = ("descricao", "manutencao__titulo")
    autocomplete_fields = ("manutencao", "usuario")
    readonly_fields = ("id", "data_hora")
    ordering = ("-data_hora",)

    def has_delete_permission(self, request, obj=None) -> bool:
        """O histórico é append-only: não se apaga pela interface administrativa."""
        return False
