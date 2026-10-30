"""Constantes do domínio de rotinas recorrentes.

Uma rotina é a regra de algo que precisa acontecer de tempos em tempos,
independentemente de alguém lembrar: a limpeza que se repete, o teste mensal da
iluminação de emergência, a inspeção da casa de máquinas, a revisão preventiva
do gerador. A execução é a ocorrência concreta dessa regra, com data, quem
responde e o que realmente aconteceu.

As palavras aqui são poucas de propósito. Não há cron, não há expressão de
recorrência exposta e não há situação de rotina persistida: o que existe é o
tipo do trabalho, o ritmo com que ele volta, em que ponto a ocorrência está e o
que se encontrou quando ela foi feita.

O peso de uma rotina é o mesmo vocabulário usado por demandas e manutenções,
importado de ``apps.core.constants`` — não há um terceiro degrau de urgência.

O ritmo também não é só desta casa: ``Periodicidade`` vive na camada neutra de
recorrência, junto da conta que o interpreta, e é daqui que o domínio o lê.
"""
from django.db import models

from apps.core.recorrencia import Periodicidade


class TipoRotina(models.TextChoices):
    """Que espécie de trabalho a rotina organiza."""

    OPERACIONAL = "operacional", "Operacional"
    INSPECAO = "inspecao", "Inspeção"
    LIMPEZA = "limpeza", "Limpeza"
    PREVENTIVA = "preventiva", "Preventiva"
    ADMINISTRATIVA = "administrativa", "Administrativa"
    SEGURANCA = "seguranca", "Segurança"
    OUTRA = "outra", "Outra"


class DiaSemana(models.IntegerChoices):
    """O dia da semana de uma rotina semanal.

    O número é o que o calendário usa — segunda é zero —, e não o nome escrito
    de um idioma: assim o cálculo não depende de tradução para acertar.
    """

    SEGUNDA = 0, "Segunda-feira"
    TERCA = 1, "Terça-feira"
    QUARTA = 2, "Quarta-feira"
    QUINTA = 3, "Quinta-feira"
    SEXTA = 4, "Sexta-feira"
    SABADO = 5, "Sábado"
    DOMINGO = 6, "Domingo"


class StatusExecucao(models.TextChoices):
    """Onde uma ocorrência está agora.

    ``IGNORADA`` existe para a atividade que não se aplicava naquele período —
    e, mesmo assim, a rotina segue em frente. ``CANCELADA`` existe para a
    ocorrência que não vai acontecer por decisão de quem responde pelo
    condomínio. As duas são desfechos, e nenhuma delas apaga o que houve.
    """

    PENDENTE = "pendente", "Pendente"
    EM_ANDAMENTO = "em_andamento", "Em andamento"
    CONCLUIDA = "concluida", "Concluída"
    IGNORADA = "ignorada", "Ignorada"
    CANCELADA = "cancelada", "Cancelada"


class ResultadoExecucao(models.TextChoices):
    """O que se encontrou ao fazer a atividade.

    Existe para registrar o meio-termo que a conclusão sozinha não conta:
    "a atividade foi feita, mas encontrou um problema". É a partir daqui que se
    oferece abrir uma demanda — nunca automaticamente.
    """

    OK = "ok", "Tudo certo"
    ATENCAO = "atencao", "Atenção"
    PROBLEMA = "problema", "Problema"
    NAO_APLICAVEL = "nao_aplicavel", "Não aplicável"


class StatusItemChecklist(models.TextChoices):
    """Onde uma tarefa do checklist está.

    Três estados bastam: a tarefa ainda não foi tratada, foi feita, ou não se
    aplicava naquela ocorrência. Não há estado intermediário — a tarefa ou está
    por tratar, ou foi resolvida de alguma forma.
    """

    PENDENTE = "pendente", "Pendente"
    CONCLUIDO = "concluido", "Concluído"
    NAO_APLICAVEL = "nao_aplicavel", "Não aplicável"


# Situações em que a ocorrência ainda depende de alguém.
SITUACOES_PENDENTES = (StatusExecucao.PENDENTE, StatusExecucao.EM_ANDAMENTO)

# Estados que dão por tratada uma tarefa do checklist. Uma tarefa pendente com
# esta marca bloqueia a conclusão da ocorrência; as demais, não.
STATUS_QUE_TRATAM_O_ITEM = (
    StatusItemChecklist.CONCLUIDO,
    StatusItemChecklist.NAO_APLICAVEL,
)

# Situações que encerram a ocorrência: depois delas ela não muda mais.
SITUACOES_ENCERRADAS = (
    StatusExecucao.CONCLUIDA,
    StatusExecucao.IGNORADA,
    StatusExecucao.CANCELADA,
)

# Resultados em que faz sentido oferecer a abertura de uma demanda.
RESULTADOS_QUE_PEDEM_ATENCAO = (
    ResultadoExecucao.ATENCAO,
    ResultadoExecucao.PROBLEMA,
)
