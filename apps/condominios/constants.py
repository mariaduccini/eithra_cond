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
