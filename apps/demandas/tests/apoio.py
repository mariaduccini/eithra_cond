"""Apoio aos testes de demandas operacionais.

Reúne só o que os testes repetem: condomínios, pessoas, vínculos e demandas.
Nenhum dado real entra aqui — nomes, e-mails e títulos são de teste.
"""
from django.contrib.auth import get_user_model

from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio, Condominio


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


def montar_condominio_com(papel, email="pessoa@exemplo.test", nome="Pessoa"):
    """Cria condomínio, pessoa e vínculo de uma vez, devolvendo a pessoa.

    Devolve ``(usuario, condominio)`` na ordem em que os testes costumam usar.
    """
    condominio = criar_condominio()
    usuario = criar_pessoa(email, nome)
    criar_vinculo(usuario, condominio, papel=papel)
    return usuario, condominio
