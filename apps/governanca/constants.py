"""Vocabulário do domínio de governança.

Contratos, garantias e documentos são obrigações com prazo. O que muda entre
eles é o que vence e o que cobre; o que eles têm em comum é a pergunta "isto
ainda vale?". Por isso as situações de vigência são declaradas uma vez só, e
lidas pelos três domínios com as mesmas palavras.
"""
from django.db import models


class PeriodicidadeCobranca(models.TextChoices):
    """De quanto em quanto tempo o contrato é cobrado.

    É informação operacional — serve para a gestão reconhecer a cobrança que
    chega — e não substitui a contabilidade.
    """

    MENSAL = "mensal", "Mensal"
    BIMESTRAL = "bimestral", "Bimestral"
    TRIMESTRAL = "trimestral", "Trimestral"
    SEMESTRAL = "semestral", "Semestral"
    ANUAL = "anual", "Anual"
    UNICA = "unica", "Cobrança única"
    OUTRO = "outro", "Outro"


class SituacaoVigencia(models.TextChoices):
    """A situação de um prazo, lida do próprio dado e nunca guardada.

    Nada disso é persistido: a situação de hoje é calculada do vencimento e da
    data em que se pergunta. Um campo de status precisaria ser atualizado por
    alguém, todos os dias, e ficaria errado no dia em que ninguém atualizasse.
    """

    INATIVO = "inativo", "Inativo"
    FUTURO = "futuro", "Ainda não começou"
    VIGENTE = "vigente", "Em vigor"
    VENCENDO = "vencendo", "Vence em breve"
    VENCIDO = "vencido", "Vencido"
    SEM_VALIDADE = "sem_validade", "Sem validade"


# Como cada situação aparece na tela. Um vencimento próximo pede atenção sem
# alarme; um vencimento passado é um fato consumado.
TOM_POR_SITUACAO = {
    SituacaoVigencia.INATIVO: "secondary",
    SituacaoVigencia.FUTURO: "info",
    SituacaoVigencia.VIGENTE: "success",
    SituacaoVigencia.VENCENDO: "warning",
    SituacaoVigencia.VENCIDO: "danger",
    SituacaoVigencia.SEM_VALIDADE: "secondary",
}
