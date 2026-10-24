from django.db import models


class TipoAcessoPortaria(models.TextChoices):
    VISITANTE = "visitante", "Visitante"
    PRESTADOR = "prestador", "Prestador"
    FORNECEDOR = "fornecedor", "Fornecedor"
    TECNICO = "tecnico", "Técnico"


# Os quatro tipos compartilham o mesmo fluxo: identificar quem chegou, registrar
# o contexto e dizer o resultado da autorização. O que muda é o vocabulário que
# a tela usa para pedir os dados certos, não o domínio.
ESCOLHAS_DE_TIPO_EM_USO = [
    (TipoAcessoPortaria.VISITANTE.value, TipoAcessoPortaria.VISITANTE.label),
    (TipoAcessoPortaria.PRESTADOR.value, TipoAcessoPortaria.PRESTADOR.label),
    (TipoAcessoPortaria.FORNECEDOR.value, TipoAcessoPortaria.FORNECEDOR.label),
    (TipoAcessoPortaria.TECNICO.value, TipoAcessoPortaria.TECNICO.label),
]


class StatusAutorizacaoPortaria(models.TextChoices):
    AGUARDANDO = "aguardando_confirmacao", "Aguardando confirmação"
    AUTORIZADO = "autorizado", "Autorizado"
    NAO_AUTORIZADO = "nao_autorizado", "Não autorizado"
    SEM_RESPOSTA = "sem_resposta", "Sem resposta"


class MeioAutorizacaoPortaria(models.TextChoices):
    INTERFONE = "interfone", "Interfone"
    TELEFONE = "telefone", "Telefone"
    PRESENCIAL = "presencial", "Presencial"


class OrigemPortaria(models.TextChoices):
    PORTARIA_MANUAL = "portaria_manual", "Portaria manual"
    CHEGADA_NAO_PREVISTA = "chegada_nao_prevista", "Chegada não prevista"


# O turno da portaria vai das 7h às 19h, mas horário de turno não é estado de
# presença: quem entrou e não teve saída registrada continua dentro depois das
# 19h, depois da meia-noite e no dia seguinte. Estas horas servem só para o
# Eithra reconhecer o que ficou pendente do turno anterior — nunca para
# encerrar nada.
HORA_DE_ABERTURA = 7
HORA_DE_FECHAMENTO = 19
