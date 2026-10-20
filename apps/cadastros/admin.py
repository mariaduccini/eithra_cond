"""Inspeção técnica dos cadastros no Django Admin.

O Admin não é a experiência do domínio: existe apenas para conferir a estrutura
durante a fundação. Por isso não há ação em massa, e a exclusão é bloqueada —
um cadastro sai de uso sendo desativado, e não apagado, para que o que aponta
para ele continue legível.

O tipo antigo do local e do equipamento continua listável e filtrável enquanto
a base da fase anterior não for classificada: é por ele que se enxerga o que
ainda falta migrar, sem que a leitura nova dependa dele.
"""
from django.contrib import admin

from apps.cadastros.models import (
    Equipamento,
    EspecialidadeFornecedor,
    Fornecedor,
    FornecedorEspecialidade,
    LocalOperacional,
    TipoEquipamento,
    TipoLocalOperacional,
)


class SemExclusaoAdmin(admin.ModelAdmin):
    """Base dos cadastros: o registro sai de uso sendo desativado."""

    readonly_fields = ("id", "criado_em", "atualizado_em")

    def has_delete_permission(self, request, obj=None) -> bool:
        """Desativa-se o cadastro, não se apaga pela tela."""
        return False


@admin.register(TipoLocalOperacional)
class TipoLocalOperacionalAdmin(SemExclusaoAdmin):
    list_display = ("nome", "condominio", "ativo")
    list_filter = ("ativo", "condominio")
    search_fields = ("nome", "descricao", "condominio__nome")
    autocomplete_fields = ("condominio", "criado_por")
    ordering = ("condominio__nome", "nome")


@admin.register(TipoEquipamento)
class TipoEquipamentoAdmin(SemExclusaoAdmin):
    list_display = ("nome", "condominio", "ativo")
    list_filter = ("ativo", "condominio")
    search_fields = ("nome", "descricao", "condominio__nome")
    autocomplete_fields = ("condominio", "criado_por")
    ordering = ("condominio__nome", "nome")


@admin.register(EspecialidadeFornecedor)
class EspecialidadeFornecedorAdmin(SemExclusaoAdmin):
    list_display = ("nome", "condominio", "ativo")
    list_filter = ("ativo", "condominio")
    search_fields = ("nome", "descricao", "condominio__nome")
    autocomplete_fields = ("condominio", "criado_por")
    ordering = ("condominio__nome", "nome")


@admin.register(LocalOperacional)
class LocalOperacionalAdmin(SemExclusaoAdmin):
    list_display = ("nome", "condominio", "tipo_local", "tipo", "ativo")
    list_filter = ("tipo_local", "tipo", "ativo", "condominio")
    search_fields = ("nome", "descricao", "condominio__nome")
    autocomplete_fields = ("condominio", "tipo_local", "criado_por")
    ordering = ("condominio__nome", "nome")


@admin.register(Equipamento)
class EquipamentoAdmin(SemExclusaoAdmin):
    list_display = ("nome", "condominio", "local", "tipo_equipamento", "ativo")
    list_filter = ("tipo_equipamento", "ativo", "condominio", "local")
    search_fields = (
        "nome",
        "tipo_texto",
        "fabricante",
        "modelo",
        "numero_serie",
        "patrimonio",
        "condominio__nome",
    )
    autocomplete_fields = ("condominio", "local", "tipo_equipamento", "criado_por")
    ordering = ("condominio__nome", "nome")


@admin.register(Fornecedor)
class FornecedorAdmin(SemExclusaoAdmin):
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
    ordering = ("condominio__nome", "nome_razao_social")


@admin.register(FornecedorEspecialidade)
class FornecedorEspecialidadeAdmin(admin.ModelAdmin):
    list_display = ("fornecedor", "especialidade")
    search_fields = (
        "fornecedor__nome_razao_social",
        "especialidade__nome",
    )
    autocomplete_fields = ("fornecedor", "especialidade")

    def has_delete_permission(self, request, obj=None) -> bool:
        """O vínculo é ajustado pelo domínio, não apagado pela tela."""
        return False
