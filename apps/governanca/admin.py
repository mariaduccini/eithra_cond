"""Inspeção técnica da governança no Django Admin.

O Admin não é a experiência do domínio: existe apenas para conferir a estrutura
durante a fundação. Por isso não há ação em massa, e a exclusão é bloqueada —
contrato, garantia e documento saem de circulação sendo desativados, e o vínculo
que deixa de existir é removido pelo domínio, não pela tela.

A situação de vigência não aparece como campo: ela é lida do prazo, na hora.
"""
from django.contrib import admin

from apps.governanca.models import (
    ArquivoDocumento,
    ContratoEquipamento,
    ContratoFornecedor,
    ContratoLocal,
    ContratoRotina,
    DocumentoContrato,
    DocumentoEquipamento,
    DocumentoFornecedor,
    DocumentoGarantia,
    DocumentoOperacional,
    Garantia,
    GarantiaEquipamento,
    GarantiaItemCompra,
    GarantiaManutencao,
    Projeto,
    ProjetoDemanda,
    ProjetoItem,
    TipoContrato,
    TipoDocumento,
)


class SemExclusaoAdmin(admin.ModelAdmin):
    """Base da governança: o registro sai de uso sendo desativado."""

    readonly_fields = ("id", "criado_em", "atualizado_em")

    def has_delete_permission(self, request, obj=None) -> bool:
        """Desativa-se o registro, não se apaga pela tela."""
        return False


@admin.register(TipoContrato)
class TipoContratoAdmin(SemExclusaoAdmin):
    list_display = ("nome", "condominio", "ativo")
    list_filter = ("ativo", "condominio")
    search_fields = ("nome", "descricao", "condominio__nome")
    autocomplete_fields = ("condominio", "criado_por")
    ordering = ("condominio__nome", "nome")


@admin.register(TipoDocumento)
class TipoDocumentoAdmin(SemExclusaoAdmin):
    list_display = ("nome", "condominio", "exige_validade", "ativo")
    list_filter = ("exige_validade", "ativo", "condominio")
    search_fields = ("nome", "descricao", "condominio__nome")
    autocomplete_fields = ("condominio", "criado_por")
    ordering = ("condominio__nome", "nome")


@admin.register(ContratoFornecedor)
class ContratoFornecedorAdmin(SemExclusaoAdmin):
    list_display = ("objeto", "condominio", "fornecedor", "data_inicio", "data_fim", "ativo")
    list_filter = ("ativo", "condominio", "tipo_contrato", "periodicidade_cobranca")
    search_fields = (
        "objeto",
        "numero_referencia",
        "descricao",
        "fornecedor__nome_razao_social",
        "fornecedor__nome_fantasia",
        "condominio__nome",
    )
    autocomplete_fields = (
        "condominio",
        "fornecedor",
        "tipo_contrato",
        "responsavel_interno",
        "criado_por",
    )
    ordering = ("condominio__nome", "-data_inicio")


@admin.register(Garantia)
class GarantiaAdmin(SemExclusaoAdmin):
    list_display = ("descricao", "condominio", "fornecedor", "data_inicio", "data_fim", "ativo")
    list_filter = ("ativo", "condominio")
    search_fields = (
        "descricao",
        "numero_referencia",
        "fornecedor__nome_razao_social",
        "fornecedor__nome_fantasia",
        "condominio__nome",
    )
    autocomplete_fields = ("condominio", "fornecedor", "criado_por")
    ordering = ("condominio__nome", "-data_inicio")


@admin.register(DocumentoOperacional)
class DocumentoOperacionalAdmin(SemExclusaoAdmin):
    list_display = ("titulo", "condominio", "tipo_documento", "data_validade", "ativo")
    list_filter = ("ativo", "condominio", "tipo_documento")
    search_fields = ("titulo", "numero_referencia", "emissor", "condominio__nome")
    autocomplete_fields = ("condominio", "tipo_documento", "criado_por")
    ordering = ("condominio__nome", "-criado_em")


@admin.register(ContratoEquipamento)
class ContratoEquipamentoAdmin(admin.ModelAdmin):
    list_display = ("contrato", "equipamento")
    search_fields = ("contrato__objeto", "equipamento__nome")
    autocomplete_fields = ("contrato", "equipamento")

    def has_delete_permission(self, request, obj=None) -> bool:
        """O vínculo é ajustado pelo domínio, não apagado pela tela."""
        return False


@admin.register(ContratoLocal)
class ContratoLocalAdmin(admin.ModelAdmin):
    list_display = ("contrato", "local")
    search_fields = ("contrato__objeto", "local__nome")
    autocomplete_fields = ("contrato", "local")

    def has_delete_permission(self, request, obj=None) -> bool:
        """O vínculo é ajustado pelo domínio, não apagado pela tela."""
        return False


@admin.register(ContratoRotina)
class ContratoRotinaAdmin(admin.ModelAdmin):
    list_display = ("contrato", "rotina")
    search_fields = ("contrato__objeto", "rotina__titulo")
    autocomplete_fields = ("contrato", "rotina")

    def has_delete_permission(self, request, obj=None) -> bool:
        """O vínculo é ajustado pelo domínio, não apagado pela tela."""
        return False


@admin.register(GarantiaEquipamento)
class GarantiaEquipamentoAdmin(admin.ModelAdmin):
    list_display = ("garantia", "equipamento")
    search_fields = ("garantia__descricao", "equipamento__nome")
    autocomplete_fields = ("garantia", "equipamento")

    def has_delete_permission(self, request, obj=None) -> bool:
        """O vínculo é ajustado pelo domínio, não apagado pela tela."""
        return False


@admin.register(GarantiaManutencao)
class GarantiaManutencaoAdmin(admin.ModelAdmin):
    list_display = ("garantia", "manutencao")
    search_fields = ("garantia__descricao", "manutencao__titulo")
    autocomplete_fields = ("garantia", "manutencao")

    def has_delete_permission(self, request, obj=None) -> bool:
        """O vínculo é ajustado pelo domínio, não apagado pela tela."""
        return False


@admin.register(GarantiaItemCompra)
class GarantiaItemCompraAdmin(admin.ModelAdmin):
    list_display = ("garantia", "item_compra")
    search_fields = ("garantia__descricao", "item_compra__descricao")
    autocomplete_fields = ("garantia", "item_compra")

    def has_delete_permission(self, request, obj=None) -> bool:
        """O vínculo é ajustado pelo domínio, não apagado pela tela."""
        return False


@admin.register(ArquivoDocumento)
class ArquivoDocumentoAdmin(admin.ModelAdmin):
    list_display = ("nome_original", "documento", "mime_type", "tamanho_bytes", "ativo")
    list_filter = ("ativo", "mime_type")
    search_fields = ("nome_original", "documento__titulo", "sha256")
    autocomplete_fields = ("documento", "enviado_por")
    readonly_fields = ("id", "enviado_em")

    def has_delete_permission(self, request, obj=None) -> bool:
        """O arquivo sai de circulação sendo desativado, não apagado pela tela."""
        return False


@admin.register(DocumentoFornecedor)
class DocumentoFornecedorAdmin(admin.ModelAdmin):
    list_display = ("documento", "fornecedor")
    search_fields = ("documento__titulo", "fornecedor__nome_razao_social")
    autocomplete_fields = ("documento", "fornecedor")

    def has_delete_permission(self, request, obj=None) -> bool:
        """O vínculo é ajustado pelo domínio, não apagado pela tela."""
        return False


@admin.register(DocumentoContrato)
class DocumentoContratoAdmin(admin.ModelAdmin):
    list_display = ("documento", "contrato")
    search_fields = ("documento__titulo", "contrato__objeto")
    autocomplete_fields = ("documento", "contrato")

    def has_delete_permission(self, request, obj=None) -> bool:
        """O vínculo é ajustado pelo domínio, não apagado pela tela."""
        return False


@admin.register(DocumentoEquipamento)
class DocumentoEquipamentoAdmin(admin.ModelAdmin):
    list_display = ("documento", "equipamento")
    search_fields = ("documento__titulo", "equipamento__nome")
    autocomplete_fields = ("documento", "equipamento")

    def has_delete_permission(self, request, obj=None) -> bool:
        """O vínculo é ajustado pelo domínio, não apagado pela tela."""
        return False


@admin.register(DocumentoGarantia)
class DocumentoGarantiaAdmin(admin.ModelAdmin):
    list_display = ("documento", "garantia")
    search_fields = ("documento__titulo", "garantia__descricao")
    autocomplete_fields = ("documento", "garantia")

    def has_delete_permission(self, request, obj=None) -> bool:
        """O vínculo é ajustado pelo domínio, não apagado pela tela."""
        return False


class ProjetoItemInline(admin.TabularInline):
    """Etapas e entregas do projeto, editadas junto com ele."""

    model = ProjetoItem
    extra = 0
    fields = ("parent", "tipo", "nome", "status", "ordem")
    autocomplete_fields = ("parent",)
    ordering = ("ordem", "nome")


@admin.register(Projeto)
class ProjetoAdmin(SemExclusaoAdmin):
    list_display = (
        "nome",
        "condominio",
        "status",
        "data_inicio",
        "data_prevista_fim",
        "ativo",
    )
    list_filter = ("ativo", "status", "condominio")
    search_fields = ("nome", "identificacao", "descricao", "condominio__nome")
    autocomplete_fields = ("condominio", "responsavel_interno", "criado_por")
    ordering = ("condominio__nome", "nome")
    inlines = (ProjetoItemInline,)


@admin.register(ProjetoItem)
class ProjetoItemAdmin(SemExclusaoAdmin):
    list_display = ("nome", "projeto", "tipo", "status", "ordem")
    list_filter = ("tipo", "status")
    search_fields = ("nome", "projeto__nome")
    autocomplete_fields = ("projeto", "parent")
    ordering = ("projeto__nome", "ordem", "nome")


@admin.register(ProjetoDemanda)
class ProjetoDemandaAdmin(admin.ModelAdmin):
    list_display = ("projeto", "demanda")
    search_fields = ("projeto__nome", "demanda__titulo")
    autocomplete_fields = ("projeto", "demanda")

    def has_delete_permission(self, request, obj=None) -> bool:
        """O vínculo é ajustado pelo domínio, não apagado pela tela."""
        return False
