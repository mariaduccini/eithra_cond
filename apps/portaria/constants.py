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


class IntencaoPortaria(models.TextChoices):
    """O que uma fala pode querer dizer na portaria.

    Vocabulário da leitura, não estado do domínio: nada aqui é guardado. Serve
    para que "chegou alguém" e "alguém saiu" sejam coisas distintas antes de
    qualquer registro — e para que o que não deu para entender também tenha
    nome, em vez de virar um registro pela metade.
    """

    CHEGADA = "chegada", "Chegada"
    SAIDA = "saida", "Saída"
    AUTORIZACAO = "autorizacao", "Autorização"
    RELATO_DE_PROBLEMA = "relato_de_problema", "Relato de problema"
    INSUFICIENTE = "insuficiente", "Informação insuficiente"


class ContextoAcessoPortaria(models.TextChoices):
    """Para onde o acesso se dirige — o destino, antes de qualquer unidade.

    Nem todo acesso tem unidade. Um prestador pode estar atendendo uma unidade,
    o condomínio inteiro ou uma manutenção já agendada, e tratar tudo como
    visita obrigaria a inventar um destino que não existe. Vocabulário da
    leitura, como ``IntencaoPortaria``: nada aqui é guardado.
    """

    VISITANTE_DE_UNIDADE = "visitante_de_unidade", "Visita a unidade"
    PRESTADOR_DE_UNIDADE = "prestador_de_unidade", "Prestador de unidade"
    PRESTADOR_DO_CONDOMINIO = "prestador_do_condominio", "Prestador do condomínio"
    FORNECEDOR_DO_CONDOMINIO = "fornecedor_do_condominio", "Fornecedor do condomínio"
    OUTRO = "outro", "Outro acesso"


# O turno da portaria vai das 7h às 19h, mas horário de turno não é estado de
# presença: quem entrou e não teve saída registrada continua dentro depois das
# 19h, depois da meia-noite e no dia seguinte. Estas horas servem só para o
# Eithra reconhecer o que ficou pendente do turno anterior — nunca para
# encerrar nada.
HORA_DE_ABERTURA = 7
HORA_DE_FECHAMENTO = 19
