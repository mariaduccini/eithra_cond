"""Inspeção técnica das rotinas e das execuções no Django Admin.

O Admin não é a experiência do domínio: existe apenas para conferir a estrutura
durante a fundação. Por isso não há ação em massa, e a exclusão é bloqueada — a
rotina é o combinado que o condomínio assumiu, e a execução é o que realmente
aconteceu. Não há um modelo de histórico à parte para inspecionar: a lista de
execuções já é o histórico.

Os checklists entram pela mesma porta: o modelo é a lista reutilizável, e os
itens da ocorrência são o retrato dela. Nenhum deles é apagado pela tela — o
retrato de uma ocorrência já feita não se desfaz.
"""
from django.contrib import admin

from apps.rotinas.models import (
    ExecucaoRotina,
    ItemExecucaoChecklist,
    ItemModeloChecklist,
    ModeloChecklist,
    RotinaRecorrente,
)


@admin.register(RotinaRecorrente)
class RotinaRecorrenteAdmin(admin.ModelAdmin):
    list_display = (
        "titulo",
        "condominio",
        "tipo",
        "periodicidade_tipo",
        "responsavel",
        "proxima_execucao",
        "ativo",
    )
    list_filter = ("tipo", "periodicidade_tipo", "ativo")
    search_fields = ("titulo", "descricao", "condominio__nome")
    autocomplete_fields = (
        "condominio",
        "responsavel",
        "local",
        "equipamento",
        "fornecedor",
        "criado_por",
    )
    readonly_fields = ("id", "proxima_execucao", "criado_em", "atualizado_em")
    ordering = ("-criado_em",)

    def has_delete_permission(self, request, obj=None) -> bool:
        """A rotina é história do condomínio; retira-se da operação, não se apaga."""
        return False


@admin.register(ExecucaoRotina)
class ExecucaoRotinaAdmin(admin.ModelAdmin):
    list_display = (
        "rotina",
        "condominio",
        "data_prevista",
        "status",
        "responsavel",
        "resultado",
    )
    list_filter = ("status", "resultado")
    search_fields = ("rotina__titulo", "observacao_execucao", "condominio__nome")
    autocomplete_fields = (
        "rotina",
        "condominio",
        "responsavel",
        "demanda_gerada",
        "manutencao_gerada",
    )
    readonly_fields = ("id", "criado_em", "atualizado_em")
    ordering = ("-data_prevista",)

    def has_delete_permission(self, request, obj=None) -> bool:
        """A execução registra o que aconteceu; o histórico não se apaga pela tela."""
        return False


@admin.register(ModeloChecklist)
class ModeloChecklistAdmin(admin.ModelAdmin):
    list_display = ("nome", "condominio", "ativo", "criado_por", "criado_em")
    list_filter = ("ativo",)
    search_fields = ("nome", "descricao", "condominio__nome")
    autocomplete_fields = ("condominio", "criado_por")
    readonly_fields = ("id", "criado_em", "atualizado_em")
    ordering = ("-criado_em",)

    def has_delete_permission(self, request, obj=None) -> bool:
        """O checklist é estrutura do condomínio; desativa-se, não se apaga."""
        return False


@admin.register(ItemModeloChecklist)
class ItemModeloChecklistAdmin(admin.ModelAdmin):
    list_display = ("descricao", "checklist", "ordem", "obrigatorio", "ativo")
    list_filter = ("obrigatorio", "ativo")
    search_fields = ("descricao", "checklist__nome")
    autocomplete_fields = ("checklist",)
    readonly_fields = ("id", "criado_em", "atualizado_em")
    ordering = ("checklist", "ordem")

    def has_delete_permission(self, request, obj=None) -> bool:
        """A tarefa do modelo é combinado; desativa-se, não se apaga."""
        return False


@admin.register(ItemExecucaoChecklist)
class ItemExecucaoChecklistAdmin(admin.ModelAdmin):
    list_display = (
        "descricao",
        "execucao",
        "ordem",
        "obrigatorio",
        "status",
        "concluido_por",
    )
    list_filter = ("status", "obrigatorio")
    search_fields = ("descricao", "execucao__rotina__titulo")
    autocomplete_fields = ("execucao", "item_modelo", "concluido_por")
    readonly_fields = ("id", "criado_em", "atualizado_em")
    ordering = ("-criado_em",)

    def has_delete_permission(self, request, obj=None) -> bool:
        """A tarefa da ocorrência é retrato do que aconteceu; não se apaga."""
        return False
