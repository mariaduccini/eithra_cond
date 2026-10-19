"""Apoio aos testes dos cadastros básicos operacionais.

Reúne só o que os testes repetem: condomínios, pessoas, vínculos e cadastros.
Nenhum dado real entra aqui — nomes, e-mails e documentos são de teste.
"""
from django.contrib.auth import get_user_model

from apps.cadastros.models import Equipamento, Fornecedor, LocalOperacional
from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio, Condominio

SENHA = "senha-forte-123"


def criar_condominio(nome="Condomínio de Teste", **campos):
    """Um condomínio de teste, sempre novo."""
    return Condominio.objects.create(nome=nome, **campos)


def criar_pessoa(email, nome="Pessoa de Teste"):
    """Uma pessoa de teste. Nunca usa dados reais."""
    return get_user_model().objects.create_user(
        email=email, password=SENHA, full_name=nome
    )


def criar_vinculo(usuario, condominio, papel=Papel.SINDICA, ativo=True):
    """Liga uma pessoa a um condomínio, com o papel exercido lá."""
    return AcessoUsuarioCondominio.objects.create(
        usuario=usuario, condominio=condominio, papel=papel, ativo=ativo
    )


def entrar_como(cliente, papel, condominio, email="pessoa@exemplo.test"):
    """Cria pessoa e vínculo com este papel e entra no sistema com ela."""
    pessoa = criar_pessoa(email, papel)
    criar_vinculo(pessoa, condominio, papel=papel)
    cliente.force_login(pessoa)
    return pessoa


def criar_local(condominio, nome="Bloco A", **campos):
    """Um local de teste no condomínio informado."""
    return LocalOperacional.objects.create(condominio=condominio, nome=nome, **campos)


def criar_equipamento(condominio, nome="Bomba de recalque", **campos):
    """Um equipamento de teste no condomínio informado."""
    return Equipamento.objects.create(condominio=condominio, nome=nome, **campos)


def criar_fornecedor(condominio, nome_razao_social="Elevadores Silva Ltda.", **campos):
    """Um fornecedor de teste no condomínio informado."""
    return Fornecedor.objects.create(
        condominio=condominio, nome_razao_social=nome_razao_social, **campos
    )
