"""Quem pode ver e manter os cadastros básicos operacionais.

A resposta vem sempre do vínculo ativo da pessoa com o condomínio, e nunca do
menu, do formulário ou de um identificador trazido pelo navegador. O menu pode
esconder o que não serve — esconder não é autorizar.

Administração e síndica mantêm o cadastro do condomínio, os locais, os
equipamentos e os fornecedores. A supervisão enxerga tudo isso em modo de
leitura, sem alterar. Operação, portaria e morador não entram nesta fase — e
quem não entra não é avisado de que a tela existe.

Aqui mora apenas a regra do domínio. A pergunta "esta pessoa acessa este
condomínio?" continua em ``apps.condominios.acesso``, para não haver duas
respostas para a mesma pergunta.
"""
from django.db.models import QuerySet

from apps.condominios.acesso import condominios_do_usuario
from apps.condominios.constants import Papel
from apps.condominios.models import Condominio

# Administração e síndica respondem pelo condomínio: mantêm o cadastro dele e o
# dos seus locais, equipamentos e fornecedores.
PAPEIS_QUE_GERENCIAM = frozenset({Papel.ADMIN, Papel.SINDICA})

# A supervisão acompanha o cadastro sem respondê-lo: vê, não altera.
PAPEIS_QUE_VEEM = PAPEIS_QUE_GERENCIAM | {Papel.SUPERVISORA}

# Disponibilização de um fornecedor aos moradores: as quatro decisões — editar
# o que o morador lê, indicar, liberar e revalidar — são a mesma responsabilidade
# de responder pelo condomínio, e por isso vivem todas em PAPEIS_QUE_GERENCIAM.
# A supervisão vê os dados públicos junto com o cadastro, mas não decide sobre
# eles: mostrar ao morador é decisão de quem responde pelo condomínio.


def _com_papel(usuario, papeis: frozenset[str]) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa tem vínculo ativo com algum destes papéis."""
    return (
        condominios_do_usuario(usuario)
        .filter(
            acessos__usuario_id=getattr(usuario, "pk", None),
            acessos__ativo=True,
            acessos__papel__in=papeis,
        )
        .distinct()
    )


def condominios_para_gerenciar(usuario) -> QuerySet[Condominio]:
    """Condomínios cujos cadastros a pessoa pode manter agora."""
    return _com_papel(usuario, PAPEIS_QUE_GERENCIAM)


def condominios_para_ver(usuario) -> QuerySet[Condominio]:
    """Condomínios cujos cadastros a pessoa pode consultar agora."""
    return _com_papel(usuario, PAPEIS_QUE_VEEM)


def pode_ver_cadastros(usuario, condominio) -> bool:
    """Diz se a pessoa pode consultar os cadastros deste condomínio agora.

    Recebe o condomínio resolvido. Sem certeza, a resposta é negar.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_ver(usuario).filter(pk=condominio.pk).exists()


def pode_ver_cadastros_em_algum(usuario) -> bool:
    """Diz se a pessoa enxerga o módulo em algum condomínio agora.

    É a mesma pergunta que o menu faz, com a mesma regra — quem pergunta é a
    navegação, mas a resposta continua sendo do domínio.
    """
    return condominios_para_ver(usuario).exists()


def pode_gerenciar_cadastros(usuario, condominio) -> bool:
    """Diz se a pessoa pode manter os cadastros deste condomínio agora.

    É a mesma responsabilidade de responder pelo condomínio. Sem certeza, a
    resposta é negar.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_gerenciar(usuario).filter(pk=condominio.pk).exists()
