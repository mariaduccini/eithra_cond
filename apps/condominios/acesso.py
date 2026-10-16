"""Decisão de acesso a condomínios, em um único lugar.

Toda pergunta do tipo "esta pessoa pode acessar este condomínio?" passa por
aqui. A resposta vem sempre do banco de dados, e nunca do menu, do template ou
de um identificador trazido pelo navegador.

Regras desta camada:

- só vínculos ativos, em condomínios ativos, contam como acesso;
- nada de ``.first()``, identificador fixo ou nome de condomínio escrito no
  código: a verificação é feita por consulta;
- quando não é possível determinar o acesso com segurança, a resposta é
  negar. Não existe caminho que amplie acesso por omissão.
"""
from django.db.models import QuerySet

from apps.condominios.models import AcessoUsuarioCondominio, Condominio


def _conta_utilizavel(user) -> bool:
    """Diz se a pessoa pode, em princípio, ter acesso a alguma coisa.

    Exige sessão autenticada, registro existente e conta ativa. Não presume
    qual é a classe do usuário: apenas verifica o que estiver disponível.
    """
    return bool(
        user is not None
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_active", False)
        and getattr(user, "pk", None)
    )


def condominios_do_usuario(user) -> QuerySet[Condominio]:
    """Condomínios aos quais a pessoa tem acesso ativo agora.

    Devolve conjunto vazio quando não há pessoa utilizável ou nenhum vínculo
    ativo. Nunca devolve "todos" como atalho.
    """
    if not _conta_utilizavel(user):
        return Condominio.objects.none()

    vinculos = AcessoUsuarioCondominio.objects.filter(
        usuario=user,
        ativo=True,
        condominio__ativo=True,
    ).values_list("condominio_id", flat=True)

    return Condominio.objects.filter(pk__in=vinculos)


def tem_acesso_ativo(user, condominio) -> bool:
    """Diz se a pessoa pode acessar este condomínio agora.

    Recebe o condomínio resolvido (objeto), não um identificador solto. Se o
    que chegar não for um condomínio com registro, a resposta é negar.
    """
    if not _conta_utilizavel(user):
        return False

    if condominio is None or not getattr(condominio, "pk", None):
        return False

    if not getattr(condominio, "ativo", False):
        return False

    return AcessoUsuarioCondominio.objects.filter(
        usuario=user,
        condominio=condominio,
        ativo=True,
    ).exists()
