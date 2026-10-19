"""Apoio aos testes de manutenções operacionais.

Reúne só o que os testes repetem: condomínios, pessoas, vínculos, os cadastros
que uma manutenção pode citar e uma demanda de origem. Nenhum dado real entra
aqui — nomes, e-mails e títulos são de teste.
"""
from django.contrib.auth import get_user_model

from apps.cadastros.models import Equipamento, Fornecedor, LocalOperacional
from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio, Condominio
from apps.demandas.models import Demanda


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


def criar_local(condominio, nome="Casa de máquinas"):
    """Um lugar do condomínio, para a manutenção citar."""
    return LocalOperacional.objects.create(condominio=condominio, nome=nome)


def criar_equipamento(condominio, nome="Bomba de recalque", local=None):
    """Um equipamento do condomínio, opcionalmente preso a um lugar."""
    return Equipamento.objects.create(condominio=condominio, nome=nome, local=local)


def criar_fornecedor(condominio, nome="Elevadores de Teste"):
    """Um fornecedor do condomínio, para o serviço terceirizado."""
    return Fornecedor.objects.create(
        condominio=condominio, nome_razao_social=nome
    )


def criar_demanda(condominio, criado_por, titulo="Demanda de teste", **campos):
    """Uma demanda do condomínio, de onde uma manutenção pode nascer."""
    return Demanda.objects.create(
        condominio=condominio, criado_por=criado_por, titulo=titulo, **campos
    )
