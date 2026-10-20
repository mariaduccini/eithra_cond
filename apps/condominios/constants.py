"""Constantes de domínio do app de condomínios."""
from django.db import models


class Papel(models.TextChoices):
    """O que a pessoa faz dentro de um condomínio.

    O papel pertence ao vínculo com o condomínio, não à pessoa: a mesma
    pessoa pode ser síndica em um condomínio e moradora em outro.
    """

    ADMIN = "admin", "Administração"
    SINDICA = "sindica", "Síndico(a)"
    SUPERVISORA = "supervisora", "Supervisão"
    OPERACIONAL = "operacional", "Operacional"
    PORTEIRO = "porteiro", "Portaria"
    MORADOR = "morador", "Morador(a)"


# Papéis que respondem pela gestão do condomínio. Um condomínio não pode ficar
# sem pelo menos um vínculo ativo destes: sem ninguém que administre, ninguém
# consegue sequer entrar para corrigir.
PAPEIS_DE_GESTAO = frozenset({Papel.ADMIN, Papel.SINDICA})


class EventoAcesso(models.TextChoices):
    """O que aconteceu com o acesso de uma pessoa a um condomínio.

    Cada evento é um fato do passado: o vínculo foi criado, o papel mudou, o
    acesso foi ativado ou inativado. O histórico é append-only — nada é
    reescrito nem apagado.
    """

    CRIADO = "criado", "Acesso criado"
    PAPEL_ALTERADO = "papel_alterado", "Papel alterado"
    ATIVADO = "ativado", "Acesso ativado"
    INATIVADO = "inativado", "Acesso inativado"
