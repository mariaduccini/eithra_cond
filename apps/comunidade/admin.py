"""Inspeção técnica da comunidade no Django Admin.

O Admin não é a experiência do domínio: existe apenas para conferir a estrutura
durante a fundação. Por isso não há ação em massa, e a exclusão é bloqueada —
uma unidade, uma pessoa ou um veículo sai de uso sendo desativado, e não
apagado, para que o que aponta para ele continue legível.

Os vínculos também não se apagam: uma ligação que chegou ao fim ganha data de
encerramento e continua contando o que aconteceu. O Admin mostra início e fim
lado a lado justamente para que se leia o histórico sem reescrevê-lo.
"""
from django.contrib import admin

from apps.comunidade.models import (
    Pessoa,
    Unidade,
    Veiculo,
    VinculoPessoaUnidade,
    VinculoVeiculoPessoa,
    VinculoVeiculoUnidade,
)


class SemExclusaoAdmin(admin.ModelAdmin):
    """Base dos cadastros: o registro sai de uso sendo desativado."""

    readonly_fields = ("id", "criado_em", "atualizado_em")

    def has_delete_permission(self, request, obj=None) -> bool:
        """Desativa-se o cadastro, não se apaga pela tela."""
        return False


class SemExclusaoDeVinculoAdmin(admin.ModelAdmin):
    """Base dos vínculos: o que terminou continua registrado."""

    readonly_fields = ("id", "criado_em", "atualizado_em")

    def has_delete_permission(self, request, obj=None) -> bool:
        """Fecha-se o vínculo com data, não se apaga o que houve."""
        return False


@admin.register(Unidade)
class UnidadeAdmin(SemExclusaoAdmin):
    list_display = ("identificador", "bloco_torre", "condominio", "ativa")
    list_filter = ("ativa", "condominio")
    search_fields = ("identificador", "bloco_torre", "complemento", "condominio__nome")
    autocomplete_fields = ("condominio", "criado_por")
    ordering = ("condominio__nome", "bloco_torre", "identificador")


@admin.register(Pessoa)
class PessoaAdmin(SemExclusaoAdmin):
    list_display = ("nome", "tipo_pessoa", "condominio", "ativa")
    list_filter = ("tipo_pessoa", "ativa", "condominio")
    search_fields = (
        "nome",
        "nome_social",
        "telefone",
        "whatsapp",
        "email",
        "condominio__nome",
    )
    autocomplete_fields = ("condominio", "usuario", "criado_por")
    ordering = ("condominio__nome", "nome")


@admin.register(Veiculo)
class VeiculoAdmin(SemExclusaoAdmin):
    list_display = ("placa", "marca", "modelo", "condominio", "ativo")
    list_filter = ("ativo", "condominio")
    search_fields = ("placa", "marca", "modelo", "cor", "condominio__nome")
    autocomplete_fields = ("condominio", "criado_por")
    ordering = ("condominio__nome", "placa")


@admin.register(VinculoPessoaUnidade)
class VinculoPessoaUnidadeAdmin(SemExclusaoDeVinculoAdmin):
    list_display = ("pessoa", "unidade", "tipo_vinculo", "data_inicio", "data_fim")
    list_filter = ("tipo_vinculo", "condominio")
    search_fields = ("pessoa__nome", "unidade__identificador", "condominio__nome")
    autocomplete_fields = ("condominio", "pessoa", "unidade", "criado_por")
    ordering = ("condominio__nome", "-data_inicio")


@admin.register(VinculoVeiculoPessoa)
class VinculoVeiculoPessoaAdmin(SemExclusaoDeVinculoAdmin):
    list_display = ("veiculo", "pessoa", "data_inicio", "data_fim")
    list_filter = ("condominio",)
    search_fields = ("veiculo__placa", "pessoa__nome", "condominio__nome")
    autocomplete_fields = ("condominio", "veiculo", "pessoa", "criado_por")
    ordering = ("condominio__nome", "-data_inicio")


@admin.register(VinculoVeiculoUnidade)
class VinculoVeiculoUnidadeAdmin(SemExclusaoDeVinculoAdmin):
    list_display = ("veiculo", "unidade", "data_inicio", "data_fim")
    list_filter = ("condominio",)
    search_fields = ("veiculo__placa", "unidade__identificador", "condominio__nome")
    autocomplete_fields = ("condominio", "veiculo", "unidade", "criado_por")
    ordering = ("condominio__nome", "-data_inicio")
