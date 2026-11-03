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


class FrequenciaUnidade(models.TextChoices):
    """De quanto em quanto tempo um plano preventivo volta a acontecer.

    Três leituras bastam: a cada N dias, N meses ou N anos. É a palavra que a
    gestão escolhe, e não uma expressão de recorrência — a conta de calendário
    é feita em um lugar só, e nenhuma unidade nova entra sem que alguém saiba
    quem a calcula.
    """

    DIAS = "dias", "Dias"
    MESES = "meses", "Meses"
    ANOS = "anos", "Anos"


class StatusConferencia(models.TextChoices):
    """Onde está a conferência de uma ocorrência, separada da situação dela.

    A situação conta o serviço: aberto, programado, começado, esperando,
    concluído. Ela não conta se alguém olhou o que foi entregue — e, por isso,
    a conferência tem vocabulário próprio em vez de virar mais uma situação do
    ciclo. Não existe "aguardando vistoria" nem "reprovada pela supervisão": a
    manutenção continua sendo uma só, e a conferência é uma etapa dela.

    ``PENDENTE`` é a conferência devida e ainda não decidida. Ela nasce junto
    com a ocorrência que a exige, mesmo antes de alguém registrar a execução —
    o que muda é que ainda não há o que decidir, e é o serviço que impede
    decidir sem entrega registrada.

    ``NAO_EXIGIDA`` é a ocorrência que não passa por conferência. As duas
    palavras não convivem: exigir conferência e não estar devendo nenhuma é um
    estado que o banco recusa.
    """

    NAO_EXIGIDA = "nao_exigida", "Não exigida"
    PENDENTE = "pendente", "Aguardando conferência"
    APROVADA = "aprovada", "Aprovada"
    REPROVADA = "reprovada", "Reprovada"


class ResultadoCriterio(models.TextChoices):
    """O que a conferência respondeu sobre um item observável.

    Poucas palavras bastam. ``NAO_AVALIADO`` é o item que ninguém olhou ainda —
    e é aí que todo item começa. ``CONFORME`` é o que passou; ``NAO_CONFORME``,
    o que precisa de correção. ``NAO_SE_APLICA`` é a resposta honesta para o
    item que não cabe nesta execução — o equipamento foi trocado, o item não
    tem mais o que observar.

    Nada aqui afirma qualidade técnica: são leituras do que foi entregue. E o
    item obrigatório só libera a aprovação quando está ``CONFORME`` — as outras
    respostas continuam devendo uma decisão humana.
    """

    NAO_AVALIADO = "nao_avaliado", "Não avaliado"
    CONFORME = "conforme", "Conforme"
    NAO_CONFORME = "nao_conforme", "Não conforme"
    NAO_SE_APLICA = "nao_se_aplica", "Não se aplica"


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


# Como a manutenção anda: cada situação do movimento comum tem uma ação com nome
# próprio, e é a ação — não uma lista de estados — que a alcança. Concluir e
# cancelar não entram aqui: cada uma tem ação própria, porque pedem,
# respectivamente, o que foi feito e por quê. O movimento comum só faz a
# manutenção andar.
ACOES_DE_STATUS = (
    (StatusManutencao.EM_ANDAMENTO, "Iniciar"),
    (StatusManutencao.AGUARDANDO, "Aguardar"),
    (StatusManutencao.PROGRAMADA, "Programar"),
)

# A manutenção encerrada não volta a andar por movimento comum: a única ação é
# devolvê-la à operação. O verbo diz isso, e a situação alcançada é "Aberta".
ACAO_DE_REABERTURA = (StatusManutencao.ABERTA, "Reabrir")


class TipoEvidenciaManutencao(models.TextChoices):
    """O que a evidência é, do ponto de vista de quem vai olhar.

    Foto, vídeo e documento são a mesma coisa — prova do que se sabe sobre o
    serviço — ditas em três linguagens. O tipo existe porque a tela mostra cada
    uma do seu jeito: miniatura para a foto, reprodução sob ação para o vídeo,
    abertura para o documento. Nada aqui é acervo genérico: só entra o que
    sustenta a execução ou a conferência.
    """

    FOTO = "foto", "Foto"
    VIDEO = "video", "Vídeo"
    DOCUMENTO = "documento", "Documento"


# O que a evidência pode ser, no banco e na borda. A mesma lista serve à
# validação do upload e à restrição do modelo, para que aplicação e banco não
# divirjam sobre o que é uma evidência aceita. Não há suporte genérico a
# qualquer arquivo: são os formatos que a operação realmente produz.
MIMES_DE_EVIDENCIA_MANUTENCAO = (
    "image/jpeg",
    "image/png",
    "image/webp",
    "video/mp4",
    "video/webm",
    "application/pdf",
)


class OrigemCaptura(models.TextChoices):
    """De onde veio a data e a hora de captura de uma evidência.

    A origem é declarada para não se afirmar mais confiança do que existe. A
    data que a própria imagem traz é um metadado informado por ela — não uma
    prova absoluta do momento em que o fato aconteceu. Quando não há metadado
    confiável, a origem é ``NAO_IDENTIFICADA`` e a data de captura fica vazia:
    o Eithra não inventa horário.

    ``DISPOSITIVO`` e ``SISTEMA`` existem para quando houver uma fonte que os
    sustente — o relógio do próprio aparelho, ou uma captura feita pelo sistema.
    Nesta fase a escrita só produz ``EXIF`` ou ``NAO_IDENTIFICADA``.
    """

    EXIF = "exif", "Informada pela imagem"
    DISPOSITIVO = "dispositivo", "Informada pelo dispositivo"
    SISTEMA = "sistema", "Registrada pelo sistema"
    NAO_IDENTIFICADA = "nao_identificada", "Não identificada"


class ContextoEvidencia(models.TextChoices):
    """A que momento operacional a evidência serve.

    A evidência pertence sempre à manutenção; o contexto é só uma leitura do
    seu destino — o que sustenta a execução, o que apoia a conferência, ou o
    que é registro geral. Não é um ciclo nem uma sessão: é uma palavra curta,
    para que a tela futura mostre a prova no lugar certo sem criar estrutura
    paralela.
    """

    EXECUCAO = "execucao", "Execução"
    CONFERENCIA = "conferencia", "Conferência"
    GERAL = "geral", "Geral"


class TipoEventoManutencao(models.TextChoices):
    """O que aconteceu com a manutenção, como ela é lembrada depois.

    O histórico registra decisão, não ruído técnico: quem responde por ela,
    quanto pesa, quando está prevista, em que situação está, o que foi editado
    de relevante e qual serviço foi realizado.

    A execução e a conferência têm eventos próprios, e não se confundem com a
    conclusão. Registrar a execução diz que o serviço aconteceu; aprovar,
    reprovar ou não conseguir avaliar dizem o que a conferência decidiu — e
    nenhum deles conclui a manutenção, que segue pedindo uma decisão à parte.
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
    EXECUCAO = "execucao", "Execução registrada"
    CONFERENCIA_APROVADA = "conferencia_aprovada", "Conferência aprovada"
    CONFERENCIA_REPROVADA = "conferencia_reprovada", "Conferência reprovada"
    CONFERENCIA_NAO_AVALIADA = "conferencia_nao_avaliada", "Conferência não avaliada"
    EVIDENCIA_ADICIONADA = "evidencia_adicionada", "Evidência anexada"
