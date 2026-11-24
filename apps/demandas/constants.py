"""Constantes do domínio de demandas operacionais.

Demanda é algo com consequência operacional que precisa de acompanhamento: um
vazamento, uma compra, um orçamento a pedir, uma reclamação que exige ação.
Não é conversa, comentário, informação sem ação, feed nem mensagem social.

Por isso os ciclos aqui são curtos e diretos. Não existe situação de revisão,
nem fila, nem etapas de atendimento: a demanda está aberta, andando, esperando,
concluída ou cancelada — e nada além disso.
"""
from django.db import models


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


# Como uma demanda sai de onde está: cada situação do ciclo tem uma ação com
# nome próprio, e é a ação — não uma lista de estados — que a alcança. "Iniciar"
# não é escolher "Em andamento": é dizer o que aconteceu com a demanda. Por isso
# o verbo, e não o rótulo do estado, é o que a tela oferece.
ACOES_DE_STATUS = (
    (StatusDemanda.EM_ANDAMENTO, "Iniciar"),
    (StatusDemanda.AGUARDANDO, "Aguardar"),
    (StatusDemanda.CONCLUIDA, "Concluir"),
    (StatusDemanda.CANCELADA, "Cancelar"),
)

# A demanda encerrada não volta a andar por movimento comum: a única ação é
# devolvê-la à operação. O verbo diz isso, e a situação alcançada é "Aberta".
ACAO_DE_REABERTURA = (StatusDemanda.ABERTA, "Reabrir")


class PrioridadeDemanda(models.TextChoices):
    """O peso operacional da demanda, na taxonomia de Demandas.

    Os valores gravados continuam os de sempre — ``baixa``, ``normal``,
    ``alta`` e ``urgente`` —, para que nada do que já está no banco precise ser
    reescrito nem o histórico já auditado mude de sentido. O que muda é a
    leitura operacional: ``normal`` passa a ser lido como "Média" e ``urgente``
    como "Crítica", como este domínio passou a nomear.

    Manutenções e Rotinas continuam com o vocabulário compartilhado de
    ``apps.core.constants.Prioridade``: alinhá-las à mesma taxonomia é uma etapa
    à parte, e mexer nelas agora arrastaria um escopo que não é desta entrega.
    """

    BAIXA = "baixa", "Baixa"
    NORMAL = "normal", "Média"
    ALTA = "alta", "Alta"
    URGENTE = "urgente", "Crítica"


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


class TipoProblema(models.TextChoices):
    """A natureza do que está acontecendo, na linguagem de quem lê o relato.

    É uma lista curta de propósito. Não é uma taxonomia de engenharia: é o
    suficiente para agrupar ocorrências semelhantes e reconhecer repetição. A
    ausência de classificação não vira um valor — ``None`` é "não identificado",
    que é diferente de "outro".
    """

    VAZAMENTO = "vazamento", "Vazamento"
    INFILTRACAO = "infiltracao", "Infiltração"
    HIDRAULICA = "hidraulica", "Falha hidráulica"
    ELETRICA = "eletrica", "Falha elétrica"
    ILUMINACAO = "iluminacao", "Iluminação"
    ACESSO = "acesso", "Falha de acesso ou automação"
    ESTRUTURA = "estrutura", "Estrutura ou alvenaria"
    LIMPEZA = "limpeza", "Limpeza ou conservação"
    RUIDO = "ruido", "Ruído"
    OUTRO = "outro", "Outro"


class ImpactoDemanda(models.TextChoices):
    """Até onde a consequência da demanda alcança."""

    UNIDADE = "unidade", "Uma unidade"
    AREA = "area", "Uma área do condomínio"
    EQUIPAMENTO = "equipamento", "Um equipamento"
    COLETIVO = "coletivo", "O condomínio inteiro"


class RiscoDemanda(models.TextChoices):
    """O risco identificado no relato, quando houver algum.

    ``SEM_RISCO`` é uma resposta declarada, e não a ausência de resposta:
    "sem risco identificado" é diferente de "não deu para avaliar". A ausência
    continua sendo ``None``.
    """

    SEM_RISCO = "sem_risco", "Sem risco identificado"
    SEGURANCA = "seguranca", "Risco à segurança"
    INCENDIO = "incendio", "Risco de incêndio ou explosão"
    ELETRICO = "eletrico", "Risco elétrico"
    ESTRUTURAL = "estrutural", "Risco estrutural"
    SAUDE = "saude", "Risco à saúde"


class RecorrenciaDemanda(models.TextChoices):
    """Se o que está sendo relatado já aconteceu antes.

    ``DECLARADA`` é o que o relato afirma; ``OBSERVADA`` é o que o histórico do
    condomínio mostra. São coisas diferentes e continuam separadas: uma é o que
    a pessoa disse, a outra é o que os registros sustentam.
    """

    PRIMEIRA = "primeira", "Primeira ocorrência"
    DECLARADA = "declarada", "O relato indica que já aconteceu antes"
    OBSERVADA = "observada", "Há ocorrência anterior no histórico"


class AguardandoTipo(models.TextChoices):
    """Por quem — ou pelo quê — a demanda espera agora.

    Isto não é situação da demanda. O ciclo continua o de sempre, e uma demanda
    pode estar "Em andamento" enquanto aguarda o fornecedor: são duas perguntas
    diferentes, e misturá-las encheria o ciclo de estados que não são ciclo.

    A lista é curta e fechada de propósito. O que a gestão precisa saber é a
    quem cobrar; o resto vai na descrição do que se espera.
    """

    NINGUEM = "", "Ninguém"
    FORNECEDOR = "fornecedor", "Fornecedor"
    MORADOR = "morador", "Morador"
    ADMINISTRACAO = "administracao", "Administração"
    APROVACAO = "aprovacao", "Aprovação"
    MATERIAL = "material", "Material"
    TERCEIRO = "terceiro", "Terceiro"
    OUTRO = "outro", "Outro"


class TipoMidiaEvidencia(models.TextChoices):
    """O que a evidência é, do ponto de vista de quem vai olhar.

    Foto e vídeo são a mesma coisa — prova do que se sabe — ditas em duas
    linguagens. O tipo existe porque a tela mostra cada uma do seu jeito:
    miniatura e ampliação para a foto, reprodução sob ação para o vídeo.
    """

    FOTO = "foto", "Foto"
    VIDEO = "video", "Vídeo"


# O que a evidência pode ser, no banco e na borda. A mesma lista serve à
# validação do upload e à restrição do modelo, para que aplicação e banco não
# divirjam sobre o que é uma evidência aceita.
MIMES_DE_EVIDENCIA = (
    "image/jpeg",
    "image/png",
    "image/webp",
    "video/mp4",
    "video/webm",
)


class TipoEventoDemanda(models.TextChoices):
    """O que aconteceu com a demanda, como ela é lembrada depois.

    O histórico registra decisão, não ruído técnico. Só entra aqui o que muda o
    entendimento da demanda: quem responde por ela, quanto pesa, quando vence,
    em que situação está, o que foi observado sobre ela, o que foi editado de
    relevante e a prova que passou a sustentá-la.
    """

    CRIADA = "criada", "Demanda criada"
    STATUS = "status", "Situação alterada"
    RESPONSAVEL = "responsavel", "Responsável alterado"
    PRIORIDADE = "prioridade", "Prioridade alterada"
    PRAZO = "prazo", "Prazo alterado"
    EDICAO = "edicao", "Edição"
    OBSERVACAO = "observacao", "Observação"
    EVIDENCIA = "evidencia", "Evidência anexada"
    ACOMPANHAMENTO = "acompanhamento", "Acompanhamento atualizado"
    CONCLUIDA = "concluida", "Demanda concluída"
    CANCELADA = "cancelada", "Demanda cancelada"
    # Um comunicado aos moradores foi gerado a partir desta demanda. É o único
    # evento que a demanda recebe de fora: o fato é da comunicação, e a demanda
    # apenas lembra que ele existiu, sem copiar o histórico de lá.
    COMUNICADO = "comunicado", "Comunicado gerado"
