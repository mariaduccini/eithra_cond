"""Inspeção técnica do estoque e das compras no Django Admin.

O Admin não é a experiência do domínio: existe apenas para conferir a estrutura
durante a fundação. Por isso não há ação em massa e a exclusão é bloqueada — o
item, o movimento e a compra são histórico operacional do condomínio. O item se
desativa em vez de sumir; o movimento não se apaga, porque é a trilha do saldo.
"""
from django.contrib import admin

from apps.estoque.models import (
    Compra,
    DocumentoCompra,
    ItemCompra,
    ItemEstoque,
    MovimentacaoEstoque,
)


@admin.register(ItemEstoque)
class ItemEstoqueAdmin(admin.ModelAdmin):
    list_display = (
        "nome",
        "condominio",
        "categoria_texto",
        "unidade",
        "estoque_minimo",
        "item_critico",
        "ativo",
    )
    list_filter = ("unidade", "item_critico", "controla_validade", "ativo")
    search_fields = ("nome", "descricao", "categoria_texto", "condominio__nome")
    autocomplete_fields = ("condominio", "local_armazenamento", "criado_por")
    readonly_fields = ("id", "criado_em", "atualizado_em")
    ordering = ("nome",)

    def has_delete_permission(self, request, obj=None) -> bool:
        """O item tem histórico; desativa-se, não se apaga pela tela."""
        return False


@admin.register(MovimentacaoEstoque)
class MovimentacaoEstoqueAdmin(admin.ModelAdmin):
    list_display = ("item", "tipo", "quantidade", "condominio", "usuario", "criado_em")
    list_filter = ("tipo",)
    search_fields = ("item__nome", "motivo", "condominio__nome")
    autocomplete_fields = ("condominio", "item", "compra_item", "manutencao", "usuario")
    readonly_fields = ("id", "criado_em")
    ordering = ("-criado_em",)

    def has_delete_permission(self, request, obj=None) -> bool:
        """O movimento é a trilha do saldo: não se apaga pela interface."""
        return False


@admin.register(Compra)
class CompraAdmin(admin.ModelAdmin):
    list_display = (
        "data_compra",
        "condominio",
        "fornecedor",
        "numero_documento",
        "status",
        "confirmada_em",
    )
    list_filter = ("status",)
    search_fields = ("numero_documento", "observacao", "condominio__nome")
    autocomplete_fields = ("condominio", "fornecedor", "manutencao_origem", "criado_por")
    readonly_fields = ("id", "criado_em", "atualizado_em", "confirmada_em")
    ordering = ("-data_compra", "-criado_em")

    def has_delete_permission(self, request, obj=None) -> bool:
        """A compra é um fato do condomínio; cancela-se, não se apaga pela tela."""
        return False


@admin.register(DocumentoCompra)
class DocumentoCompraAdmin(admin.ModelAdmin):
    list_display = (
        "nome_original",
        "compra",
        "tipo_documento",
        "status_leitura",
        "criado_por",
        "criado_em",
    )
    list_filter = ("tipo_documento", "status_leitura")
    search_fields = ("nome_original", "compra__numero_documento")
    autocomplete_fields = ("compra", "criado_por")
    readonly_fields = (
        "id",
        "storage_provider",
        "storage_bucket",
        "storage_key",
        "mime_type",
        "tamanho_bytes",
        "sha256",
        "criado_em",
    )
    ordering = ("-criado_em",)

    def has_delete_permission(self, request, obj=None) -> bool:
        """O documento integra a trilha da compra e não é apagado pelo Admin."""
        return False


@admin.register(ItemCompra)
class ItemCompraAdmin(admin.ModelAdmin):
    list_display = (
        "descricao",
        "compra",
        "item_estoque",
        "quantidade",
        "destino",
        "valor_total",
    )
    list_filter = ("destino", "unidade")
    search_fields = ("descricao", "compra__numero_documento")
    autocomplete_fields = ("compra", "item_estoque", "manutencao")
    readonly_fields = ("id", "valor_total", "criado_em", "atualizado_em")
    ordering = ("criado_em",)

    def has_delete_permission(self, request, obj=None) -> bool:
        """A linha pertence ao histórico da compra: não se apaga pela tela."""
        return False
