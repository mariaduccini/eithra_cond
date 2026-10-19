"""Apoio aos testes de estoque e compras operacionais.

Reúne só o que os testes repetem: condomínios, pessoas, vínculos, os cadastros
que uma compra pode citar e as manutenções para as quais se retira material.
Nenhum dado real entra aqui — nomes, e-mails e itens são de teste.
"""
from django.contrib.auth import get_user_model

from apps.cadastros.models import Fornecedor, LocalOperacional
from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio, Condominio
from apps.estoque.models import Compra, ItemEstoque
from apps.estoque.services import criar_item
from apps.manutencoes.models import Manutencao
from apps.manutencoes.services import criar_manutencao


def criar_condominio(nome="Condomínio de Teste"):
    """Um condomínio de teste, sempre novo."""
    return Condominio.objects.create(nome=nome)


def criar_pessoa(email, nome="Pessoa de Teste"):
    """Uma pessoa de teste. Nunca usa dados reais."""
    return get_user_model().objects.create_user(
        email=email, password="senha-forte-123", full_name=nome
    )


def criar_vinculo(usuario, condominio, papel=Papel.SINDICA, ativo=True):
    """Liga uma pessoa a um condomínio, com o papel exercido lá."""
    return AcessoUsuarioCondominio.objects.create(
        usuario=usuario, condominio=condominio, papel=papel, ativo=ativo
    )


def criar_local(condominio, nome="Almoxarifado"):
    """Um lugar do condomínio, para o item citar."""
    return LocalOperacional.objects.create(condominio=condominio, nome=nome)


def criar_fornecedor(condominio, nome="Fornecedor de Teste"):
    """Um fornecedor do condomínio, para a compra citar."""
    return Fornecedor.objects.create(condominio=condominio, nome_razao_social=nome)


def criar_item_estoque(condominio, criado_por, nome="Lâmpada LED", **campos):
    """Um item do estoque, cadastrado pelo serviço como a gestão faria."""
    return criar_item(
        condominio=condominio, criado_por=criado_por, nome=nome, **campos
    )


def criar_manutencao_do_condominio(condominio, criado_por, titulo="Serviço de teste"):
    """Uma manutenção do condomínio, para retirar material."""
    return criar_manutencao(
        condominio=condominio, criado_por=criado_por, titulo=titulo
    )


__all__ = [
    "Compra",
    "ItemEstoque",
    "Manutencao",
    "criar_condominio",
    "criar_fornecedor",
    "criar_item_estoque",
    "criar_local",
    "criar_manutencao_do_condominio",
    "criar_pessoa",
    "criar_vinculo",
]
