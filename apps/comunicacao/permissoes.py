"""Quem pode criar, revisar e gerar um comunicado.

A resposta vem sempre do vínculo ativo da pessoa com o condomínio, e nunca do
menu, do formulário ou de um identificador trazido pelo navegador. O menu pode
esconder o que não serve — esconder não é autorizar.

Comunicar com os moradores é um ato de quem responde pelo condomínio. Por isso
criar e revisar pedem o mesmo vínculo de gestão das demandas, e a pergunta
"esta pessoa acessa este condomínio?" continua em ``apps.condominios.acesso``,
para não haver duas respostas para a mesma pergunta.
"""
from django.db.models import QuerySet

from apps.condominios.acesso import condominios_com_papel
from apps.condominios.constants import Papel
from apps.condominios.models import Condominio

# Administradora e síndica respondem pelo condomínio: criam, revisam e geram o
# card.
PAPEIS_QUE_GERENCIAM = frozenset({Papel.ADMINISTRADORA, Papel.SINDICA})

# A supervisão acompanha e ajuda a escrever, sem responder pelo condomínio.
PAPEIS_QUE_CRIAM = PAPEIS_QUE_GERENCIAM | {Papel.SUPERVISORA}

# A operação participa do dia a dia: enxerga o que foi comunicado, e é dela que
# costuma nascer o que precisa ser avisado.
PAPEIS_QUE_OPERAM = frozenset({Papel.OPERACIONAL})

# Quem enxerga o módulo. Portaria e morador não entram nesta fase.
PAPEIS_QUE_VEEM = PAPEIS_QUE_CRIAM | PAPEIS_QUE_OPERAM


def _com_papel(usuario, papeis: frozenset[str]) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa atua com algum destes papéis.

    A regra — vínculo ativo com um dos papéis, ou o escopo global do
    administrador do sistema — vem de ``apps.condominios.acesso``, em um lugar
    só. Aqui se dizem apenas quais papéis importam para a comunicação.
    """
    return condominios_com_papel(usuario, papeis)


def condominios_para_ver(usuario) -> QuerySet[Condominio]:
    """Condomínios cujos comunicados a pessoa pode consultar agora."""
    return _com_papel(usuario, PAPEIS_QUE_VEEM)


def condominios_para_criar(usuario) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa pode escrever comunicado agora."""
    return _com_papel(usuario, PAPEIS_QUE_CRIAM)


def pode_ver_comunicacao_em_algum(usuario) -> bool:
    """Diz se a pessoa enxerga o módulo em algum condomínio agora.

    É a mesma pergunta que o menu faz, com a mesma regra — quem pergunta é a
    navegação, mas a resposta continua sendo do domínio.
    """
    return condominios_para_ver(usuario).exists()


def pode_criar_comunicacao(usuario, condominio) -> bool:
    """Diz se a pessoa pode escrever comunicado neste condomínio agora."""
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_criar(usuario).filter(pk=condominio.pk).exists()


def pode_ver_comunicacao(usuario, comunicacao) -> bool:
    """Diz se a pessoa pode consultar este comunicado agora.

    A pergunta é feita ao condomínio do comunicado, resolvido no backend. Sem
    certeza, a resposta é negar.
    """
    if comunicacao is None or not getattr(comunicacao, "pk", None):
        return False

    condominio = getattr(comunicacao, "condominio", None)
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_ver(usuario).filter(pk=condominio.pk).exists()


def _pode_gerenciar(usuario, condominio) -> bool:
    """Diz se a pessoa responde pelo condomínio — ponto comum de revisar e gerar."""
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return _com_papel(usuario, PAPEIS_QUE_GERENCIAM).filter(pk=condominio.pk).exists()


def pode_editar_comunicacao(usuario, comunicacao) -> bool:
    """Diz se a pessoa pode revisar o texto e gerar o card deste comunicado.

    Gerar o card é publicar o que foi escrito aos moradores: pede a mesma
    responsabilidade de responder pelo condomínio. Sem certeza, a resposta é
    negar.
    """
    if comunicacao is None or not getattr(comunicacao, "pk", None):
        return False

    return _pode_gerenciar(usuario, comunicacao.condominio)
