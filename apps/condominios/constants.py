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
    # A gestão pediu que as instruções de senha fossem entregues à pessoa. O
    # fato registrado é o pedido, e não a senha: nenhuma senha, endereço ou
    # token entra no histórico.
    INSTRUCOES_DE_SENHA_ENVIADAS = (
        "instrucoes_enviadas",
        "Instruções de senha enviadas",
    )
    # A administração técnica definiu uma nova senha para a conta. O fato
    # registrado é a definição, e não a senha: nenhum valor, endereço ou token
    # entra no histórico.
    SENHA_DEFINIDA_PELA_GESTAO = (
        "senha_definida",
        "Senha definida pela gestão",
    )
