"""Apoio aos testes de rotinas recorrentes.

Reúne só o que os testes repetem: condomínios, pessoas, vínculos e os cadastros
que uma rotina pode citar. Nenhum dado real entra aqui — nomes, e-mails e
títulos são de teste.

As rotinas e as execuções não são montadas à mão pelos testes: elas nascem pelo
serviço, que é o mesmo caminho que a gestão usaria. Só os cadastros de apoio
aparecem neste arquivo.
"""
from django.contrib.auth import get_user_model

from apps.cadastros.models import Equipamento, Fornecedor, LocalOperacional
from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio, Condominio
from apps.rotinas.models import ItemModeloChecklist, ModeloChecklist


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
    """Um lugar do condomínio, para a rotina citar."""
    return LocalOperacional.objects.create(condominio=condominio, nome=nome)


def criar_equipamento(condominio, nome="Bomba de recalque", local=None):
    """Um equipamento do condomínio, opcionalmente preso a um lugar."""
    return Equipamento.objects.create(condominio=condominio, nome=nome, local=local)


def criar_fornecedor(condominio, nome="Elevadores de Teste"):
    """Um fornecedor do condomínio, para a rotina terceirizada."""
    return Fornecedor.objects.create(condominio=condominio, nome_razao_social=nome)


def criar_checklist(condominio, criado_por, nome="Checklist de limpeza"):
    """Um checklist modelo do condomínio, para a rotina citar."""
    return ModeloChecklist.objects.create(
        condominio=condominio, criado_por=criado_por, nome=nome
    )


def criar_item_do_checklist(
    checklist, descricao, ordem=0, obrigatorio=True, ativo=True
):
    """Uma tarefa do checklist modelo, na ordem em que deve ser lida."""
    return ItemModeloChecklist.objects.create(
        checklist=checklist,
        descricao=descricao,
        ordem=ordem,
        obrigatorio=obrigatorio,
        ativo=ativo,
    )

