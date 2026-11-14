"""Quem responde pela administração de um condomínio.

Há perguntas que não são de acesso. "Esta pessoa pode entrar?" já tem resposta em
``apps.condominios.acesso``. A pergunta daqui é outra: *quem administra este
condomínio?* — e ela existe porque algumas rotinas precisam de um nome fixo sem
que ninguém o digite na tela.

A resposta vem do vínculo ativo de **Administradora** do condomínio — é a
função operacional que responde pela administração dele. ``Papel.SUPERVISORA``,
``Papel.SINDICA`` e a administração do sistema (``is_staff``/``is_superuser``)
**não** entram nesta resolução: não há substituto, nem fallback para o primeiro
vínculo encontrado. Quem pode executar uma ação não se confunde com quem
acompanha a preventiva.

A regra desta camada é deliberadamente estreita: só há Administradora quando o
condomínio tem **exatamente um** vínculo ativo de Administradora. Nenhum, ou
mais de um, e a resposta não existe — o sistema recusa, em vez de escolher por
conta própria. Tomar "o primeiro" seria inventar uma precedência que ninguém
combinou, e é justamente o que não se faz aqui.
"""
from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio


class ErroDeGestao(Exception):
    """Uma impossibilidade de resolver quem administra, que a gestão precisa saber."""


def administradora_do_condominio(condominio):
    """A pessoa que responde pela administração deste condomínio.

    A resposta é o vínculo ativo de Administradora com conta utilizável. Só há
    resposta quando existe exatamente um. Sem vínculo, ou com mais de um, a
    função recusa dizendo o motivo: quem chamou decide o que mostrar, e ninguém
    passa a ser escolhido ao acaso.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        raise ErroDeGestao(
            "Escolha o condomínio antes de definir quem acompanha."
        )

    pessoas = [
        vinculo.usuario
        for vinculo in (
            AcessoUsuarioCondominio.objects.filter(
                condominio_id=condominio.pk,
                ativo=True,
                papel=Papel.ADMINISTRADORA,
                usuario__is_active=True,
            )
            .select_related("usuario")
            .order_by("pk")
        )
    ]

    if not pessoas:
        raise ErroDeGestao(
            "Este condomínio ainda não possui uma administradora definida."
        )

    if len(pessoas) > 1:
        raise ErroDeGestao(
            "Há mais de uma administradora ativa para este condomínio. "
            "Regularize o cadastro antes de continuar."
        )

    return pessoas[0]
