"""Operações de cadastro do domínio de cadastros básicos.

Cada operação roda em uma transação: ou o cadastro fica coerente, ou nada muda.
Nenhuma regra de tela mora aqui — a view traduz o formulário, e o domínio
grava. Quem entrou com o cadastro e em qual condomínio ele vive são decididos
pela view a partir do vínculo da pessoa, e nunca pelo navegador.

O que os formulários já validaram chega aqui como ``dados``, com os nomes dos
campos do cadastro. Assim não há duas listas de campos para manter em sincronia.
"""
from django.db import transaction

from .models import Condominio, Equipamento, Fornecedor, LocalOperacional


@transaction.atomic
def salvar_condominio(*, condominio: Condominio, dados: dict) -> Condominio:
    """Grava o cadastro do condomínio já validado."""
    return _aplicar(condominio, dados)


@transaction.atomic
def criar_local(
    *, condominio: Condominio, criado_por, dados: dict
) -> LocalOperacional:
    """Registra um local novo no condomínio."""
    return LocalOperacional.objects.create(
        condominio=condominio, criado_por=criado_por, **dados
    )


@transaction.atomic
def editar_local(*, local: LocalOperacional, dados: dict) -> LocalOperacional:
    """Atualiza o cadastro do local."""
    return _aplicar(local, dados)


@transaction.atomic
def criar_equipamento(
    *, condominio: Condominio, criado_por, dados: dict
) -> Equipamento:
    """Registra um equipamento novo no condomínio."""
    return Equipamento.objects.create(
        condominio=condominio, criado_por=criado_por, **dados
    )


@transaction.atomic
def editar_equipamento(*, equipamento: Equipamento, dados: dict) -> Equipamento:
    """Atualiza o cadastro do equipamento."""
    return _aplicar(equipamento, dados)


@transaction.atomic
def criar_fornecedor(
    *, condominio: Condominio, criado_por, dados: dict
) -> Fornecedor:
    """Registra um fornecedor novo no condomínio."""
    return Fornecedor.objects.create(
        condominio=condominio, criado_por=criado_por, **dados
    )


@transaction.atomic
def editar_fornecedor(*, fornecedor: Fornecedor, dados: dict) -> Fornecedor:
    """Atualiza o cadastro do fornecedor."""
    return _aplicar(fornecedor, dados)


@transaction.atomic
def definir_ativo(*, objeto, ativo: bool):
    """Ativa ou desativa um cadastro.

    Desativar é a forma de tirar algo de circulação sem apagar o cadastro: o que
    aponta para ele continua legível.
    """
    objeto.ativo = bool(ativo)
    objeto.save(update_fields=["ativo", "atualizado_em"])
    return objeto


def _aplicar(objeto, dados: dict):
    """Aplica ao cadastro o que o formulário já validou e grava uma vez só."""
    for campo, valor in dados.items():
        setattr(objeto, campo, valor)
    objeto.save()
    return objeto
