"""Constantes do domínio de manutenções operacionais.

Manutenção é um serviço a ser feito em algo do condomínio: uma bomba parada, um
elevador com falha, uma dedetização programada. Diferente da demanda — que é um
pedido ou uma pendência a acompanhar —, a manutenção é a intervenção em si, com
início, serviço realizado e custo.

O peso da manutenção não é declarado aqui: é o mesmo vocabulário usado pelas
demandas, importado de ``apps.core.constants``.
"""
from django.db import models


class TipoManutencao(models.TextChoices):
    """Por que a manutenção existe.

    Corretiva resolve o que quebrou; preventiva evita que quebre. Duas palavras
    bastam para a gestão entender o que a lista está mostrando.
    """

    CORRETIVA = "corretiva", "Corretiva"
    PREVENTIVA = "preventiva", "Preventiva"


class StatusManutencao(models.TextChoices):
    """Onde a manutenção está agora.

    ``PROGRAMADA`` existe para o serviço que já tem data marcada e ainda não
    começou — não é atraso, é planejamento. ``AGUARDANDO`` existe para o que
    depende de terceiro: a peça que não chegou, o fornecedor que não veio.
    """

    ABERTA = "aberta", "Aberta"
    PROGRAMADA = "programada", "Programada"
    EM_ANDAMENTO = "em_andamento", "Em andamento"
    AGUARDANDO = "aguardando", "Aguardando"
    CONCLUIDA = "concluida", "Concluída"
    CANCELADA = "cancelada", "Cancelada"


class TipoEventoManutencao(models.TextChoices):
    """O que aconteceu com a manutenção, como ela é lembrada depois.

    O histórico registra decisão, não ruído técnico: quem responde por ela,
    quanto pesa, quando está prevista, em que situação está, o que foi editado
    de relevante e qual serviço foi realizado.
    """

    CRIADA = "criada", "Manutenção criada"
    STATUS = "status", "Situação alterada"
    RESPONSAVEL = "responsavel", "Responsável alterado"
    PRIORIDADE = "prioridade", "Prioridade alterada"
    PREVISTA = "previsao", "Data prevista alterada"
    EDICAO = "edicao", "Edição"
    SERVICO = "servico", "Serviço realizado"
    CONCLUIDA = "concluida", "Manutenção concluída"
    CANCELADA = "cancelada", "Manutenção cancelada"
