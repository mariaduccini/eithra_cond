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


def administra_o_sistema(user) -> bool:
    """Diz se a pessoa administra o Eithra por inteiro.

    A administração do sistema é global e vive em ``is_superuser``, do Django —
    não é papel de condomínio. Quem a exerce responde pelo sistema todo e, por
    isso, enxerga todos os condomínios. Mas enxergar não é ser: o administrador
    global **não** passa a ser Administradora, síndico, supervisão nem
    responsável por nada. Ele ganha visão, e só.

    ``is_staff`` sozinho **não** basta: quem entra na administração técnica sem
    ser superusuário não recebe escopo global. Sem certeza, a resposta é negar.
    """
    return bool(
        _conta_utilizavel(user) and getattr(user, "is_superuser", False)
    )


def condominios_do_usuario(user) -> QuerySet[Condominio]:
    """Condomínios aos quais a pessoa tem acesso ativo agora.

    Devolve conjunto vazio quando não há pessoa utilizável ou nenhum vínculo
    ativo. Nunca devolve "todos" como atalho — a exceção é o administrador do
    sistema, que responde pelo Eithra inteiro e por isso vê todos os
    condomínios ativos, mesmo sem vínculo. A exceção mora em um único lugar:
    aqui, e em ``condominios_com_papel``.
    """
    if not _conta_utilizavel(user):
        return Condominio.objects.none()

    if administra_o_sistema(user):
        return Condominio.objects.filter(ativo=True)

    vinculos = AcessoUsuarioCondominio.objects.filter(
        usuario=user,
        ativo=True,
        condominio__ativo=True,
    ).values_list("condominio_id", flat=True)

    return Condominio.objects.filter(pk__in=vinculos)


def condominios_com_papel(user, papeis: frozenset[str]) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa atua com algum destes papéis.

    É o mecanismo único do escopo global: o administrador do sistema enxerga
    todos os condomínios ativos, sem precisar de vínculo e sem ganhar papel
    nenhum de condomínio. As demais pessoas continuam limitadas ao vínculo ativo
    com um dos papéis pedidos — vínculo nenhum, nenhum condomínio.
    """
    if administra_o_sistema(user):
        return Condominio.objects.filter(ativo=True)

    return (
        condominios_do_usuario(user)
        .filter(
            acessos__usuario_id=getattr(user, "pk", None),
            acessos__ativo=True,
            acessos__papel__in=papeis,
        )
        .distinct()
    )


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
