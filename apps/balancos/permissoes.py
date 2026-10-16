"""Quem pode mexer na entrada de balanços.

A resposta vem sempre do vínculo ativo da pessoa com o condomínio, e nunca do
menu, do formulário ou de um identificador trazido pelo navegador. O menu pode
esconder o que não serve — esconder não é autorizar.

Aqui mora apenas a regra do domínio: quais papéis importam balanço. A pergunta
"esta pessoa acessa este condomínio?" continua em ``apps.condominios.acesso``,
para não haver duas respostas para a mesma pergunta.
"""
from django.db.models import QuerySet

from apps.condominios.acesso import condominios_do_usuario
from apps.condominios.constants import Papel
from apps.condominios.models import Condominio

# Administração e síndica recebem o documento da administradora e respondem por
# ele. Supervisão, operação, portaria e morador não importam balanço.
PAPEIS_QUE_IMPORTAM = frozenset({Papel.ADMIN, Papel.SINDICA})


def condominios_para_importar(usuario) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa pode importar balanço agora.

    A exigência é dupla: vínculo ativo com o condomínio (a resposta de
    ``condominios_do_usuario``) e um papel que importa. Faltando qualquer uma
    das duas, o condomínio não entra na lista.
    """
    return (
        condominios_do_usuario(usuario)
        .filter(
            acessos__usuario_id=getattr(usuario, "pk", None),
            acessos__ativo=True,
            acessos__papel__in=PAPEIS_QUE_IMPORTAM,
        )
        .distinct()
    )


def pode_importar_balanco(usuario, condominio) -> bool:
    """Diz se a pessoa pode importar balanço neste condomínio agora.

    Recebe o condomínio resolvido, como o resto da autorização do sistema.
    Sem certeza, a resposta é negar.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_importar(usuario).filter(pk=condominio.pk).exists()
