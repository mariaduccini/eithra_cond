"""Quem acessa e opera a Portaria."""
from django.db.models import QuerySet

from apps.condominios.acesso import condominios_com_papel
from apps.condominios.constants import Papel
from apps.condominios.models import Condominio

PAPEIS_QUE_OPERAM = frozenset(
    {Papel.ADMINISTRADORA, Papel.SINDICA, Papel.SUPERVISORA, Papel.PORTEIRO}
)
PAPEIS_QUE_VEEM = PAPEIS_QUE_OPERAM


def _com_papel(usuario, papeis: frozenset[str]) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa atua com algum destes papéis.

    A regra — vínculo ativo com um dos papéis, ou o escopo global do
    administrador do sistema — vem de ``apps.condominios.acesso``, em um lugar
    só. Aqui se dizem apenas quais papéis importam para a Portaria.
    """
    return condominios_com_papel(usuario, papeis)


def condominios_para_ver(usuario) -> QuerySet[Condominio]:
    return _com_papel(usuario, PAPEIS_QUE_VEEM)


def condominios_para_operar(usuario) -> QuerySet[Condominio]:
    return _com_papel(usuario, PAPEIS_QUE_OPERAM)


def pode_ver_portaria(usuario, condominio) -> bool:
    if condominio is None or not getattr(condominio, "pk", None):
        return False
    return condominios_para_ver(usuario).filter(pk=condominio.pk).exists()


def pode_operar_portaria(usuario, condominio) -> bool:
    if condominio is None or not getattr(condominio, "pk", None):
        return False
    return condominios_para_operar(usuario).filter(pk=condominio.pk).exists()


def pode_ver_portaria_em_algum(usuario) -> bool:
    return condominios_para_ver(usuario).exists()

