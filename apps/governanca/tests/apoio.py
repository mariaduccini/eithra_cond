"""Apoio aos testes da governança.

Reúne só o que os testes repetem: tipos, contratos, garantias, documentos e os
objetos cobertos. Nenhum dado real entra aqui — nomes, números e valores são de
teste.
"""
from datetime import date, timedelta

from apps.cadastros.services import criar_equipamento, criar_fornecedor, criar_local
from apps.estoque.constants import DestinoDoItem
from apps.estoque.models import Compra, ItemCompra
from apps.governanca.models import (
    ContratoFornecedor,
    DocumentoOperacional,
    Garantia,
    TipoContrato,
    TipoDocumento,
)
from apps.manutencoes.models import Manutencao

HOJE = date(2026, 6, 15)


def criar_tipo_contrato(condominio, nome="Manutenção", **campos):
    """Um tipo de contrato de teste no condomínio informado."""
    return TipoContrato.objects.create(condominio=condominio, nome=nome, **campos)


def criar_tipo_documento(condominio, nome="Laudo", **campos):
    """Um tipo de documento de teste no condomínio informado."""
    return TipoDocumento.objects.create(condominio=condominio, nome=nome, **campos)


def criar_contrato(condominio, criado_por, **campos):
    """Um contrato de teste com prazo padrão, no condomínio informado."""
    fornecedor = campos.pop("fornecedor", None) or criar_fornecedor(
        condominio=condominio,
        criado_por=criado_por,
        dados={"nome_razao_social": "Silva Serviços"},
    )
    padrao = {
        "fornecedor": fornecedor,
        "objeto": "Manutenção de elevadores",
        "data_inicio": HOJE - timedelta(days=30),
        "data_fim": HOJE + timedelta(days=365),
    }
    return ContratoFornecedor.objects.create(
        condominio=condominio, criado_por=criado_por, **{**padrao, **campos}
    )


def criar_garantia(condominio, criado_por, **campos):
    """Uma garantia de teste com prazo padrão, no condomínio informado."""
    padrao = {
        "descricao": "Garantia da bomba de recalque",
        "data_inicio": HOJE - timedelta(days=30),
        "data_fim": HOJE + timedelta(days=365),
    }
    return Garantia.objects.create(
        condominio=condominio, criado_por=criado_por, **{**padrao, **campos}
    )


def criar_documento(condominio, criado_por, **campos):
    """Um documento de teste, com o tipo informado ou um novo."""
    tipo = campos.pop("tipo_documento", None) or criar_tipo_documento(condominio)
    padrao = {"titulo": "Laudo do elevador", "tipo_documento": tipo}
    return DocumentoOperacional.objects.create(
        condominio=condominio, criado_por=criado_por, **{**padrao, **campos}
    )


def criar_manutencao(condominio, criado_por, **campos):
    """Uma manutenção de teste no condomínio informado."""
    return Manutencao.objects.create(
        condominio=condominio,
        criado_por=criado_por,
        titulo=campos.pop("titulo", "Troca do selo mecânico"),
        **campos,
    )


def criar_item_de_compra(condominio, criado_por, **campos):
    """Uma linha de compra de teste, já com o destino coerente com a quantidade."""
    compra = campos.pop("compra", None) or Compra.objects.create(
        condominio=condominio, data_compra=HOJE, criado_por=criado_por
    )
    quantidade = campos.pop("quantidade", 2)
    padrao = {
        "compra": compra,
        "descricao": "Selo mecânico",
        "quantidade": quantidade,
        "destino": DestinoDoItem.USO_IMEDIATO,
        "quantidade_uso_imediato": quantidade,
    }
    return ItemCompra.objects.create(**{**padrao, **campos})


def criar_equipamento_do(condominio, criado_por, nome="Bomba de recalque", **campos):
    """Um equipamento de teste, criado pelo serviço de cadastros."""
    return criar_equipamento(
        condominio=condominio,
        criado_por=criado_por,
        dados={"nome": nome, **campos},
    )


def criar_local_do(condominio, criado_por, nome="Casa de máquinas", **campos):
    """Um local de teste, criado pelo serviço de cadastros."""
    return criar_local(
        condominio=condominio, criado_por=criado_por, dados={"nome": nome, **campos}
    )
