"""Constantes do domínio de demandas operacionais.

Demanda é algo com consequência operacional que precisa de acompanhamento: um
vazamento, uma compra, um orçamento a pedir, uma reclamação que exige ação.
Não é conversa, comentário, informação sem ação, feed nem mensagem social.

Por isso os ciclos aqui são curtos e diretos. Não existe situação de revisão,
nem fila, nem etapas de atendimento: a demanda está aberta, andando, esperando,
concluída ou cancelada — e nada além disso.
"""
from django.db import models

from apps.core.constants import Prioridade


class StatusDemanda(models.TextChoices):
    """Onde a demanda está agora.

    Cinco situações bastam para acompanhar trabalho de verdade. ``AGUARDANDO``
    existe para o que depende de terceiro ou de uma decisão e não deve ser lido
    como atraso: o prazo continua sendo prazo.
    """

    ABERTA = "aberta", "Aberta"
    EM_ANDAMENTO = "em_andamento", "Em andamento"
    AGUARDANDO = "aguardando", "Aguardando"
    CONCLUIDA = "concluida", "Concluída"
    CANCELADA = "cancelada", "Cancelada"


# O peso da demanda é o mesmo vocabulário usado por outros módulos do Eithra.
# O nome antigo continua valendo para quem já o importa daqui, e os valores e
# rótulos são exatamente os de sempre — nada muda no que já está gravado.
PrioridadeDemanda = Prioridade


class OrigemDemanda(models.TextChoices):
    """De onde a demanda veio.

    Nesta fase a criação é sempre manual. Os demais valores existem para
    registrar a procedência quando houver integração — portaria, morador,
    e-mail, WhatsApp —, e nenhuma delas é ingerida automaticamente ainda.
    """

    MANUAL = "manual", "Registrada pela gestão"
    MORADOR = "morador", "Morador"
    PORTARIA = "portaria", "Portaria"
    EMAIL = "email", "E-mail"
    WHATSAPP = "whatsapp", "WhatsApp"
    SISTEMA = "sistema", "Sistema"
    OUTRA = "outra", "Outra"


class TipoEventoDemanda(models.TextChoices):
    """O que aconteceu com a demanda, como ela é lembrada depois.

    O histórico registra decisão, não ruído técnico. Só entra aqui o que muda o
    entendimento da demanda: quem responde por ela, quanto pesa, quando vence,
    em que situação está, o que foi observado sobre ela e o que foi editado de
    relevante.
    """

    CRIADA = "criada", "Demanda criada"
    STATUS = "status", "Situação alterada"
    RESPONSAVEL = "responsavel", "Responsável alterado"
    PRIORIDADE = "prioridade", "Prioridade alterada"
    PRAZO = "prazo", "Prazo alterado"
    EDICAO = "edicao", "Edição"
    OBSERVACAO = "observacao", "Observação"
    CONCLUIDA = "concluida", "Demanda concluída"
    CANCELADA = "cancelada", "Demanda cancelada"
