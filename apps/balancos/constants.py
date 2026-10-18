"""Constantes do domínio de balanços mensais."""
from django.db import models


class StatusBalanco(models.TextChoices):
    """Situação funcional do balanço: o que a gestão vê e decide.

    Não há estado de rascunho, de revisão nem de erro aqui: o balanço passa a
    existir quando a gestão confirma a importação, e falha de leitura é assunto
    de processamento, não da situação financeira do mês.
    """

    VALIDADO = "validado", "Validado"
    PUBLICADO = "publicado", "Publicado aos moradores"
    SUBSTITUIDO = "substituido", "Substituído por versão corrigida"


class ProcessamentoBalanco(models.TextChoices):
    """Estado técnico da leitura do documento.

    É separado da situação funcional de propósito: a gestão não precisa saber
    que um arquivo está sendo lido, mas o sistema precisa saber se já pode
    mostrar o resultado.
    """

    PENDENTE = "pendente", "Pendente"
    PROCESSANDO = "processando", "Processando"
    CONCLUIDO = "concluido", "Concluído"
    ERRO = "erro", "Erro"


class StatusImportacao(models.TextChoices):
    """Situação de uma tentativa de importação ainda não confirmada.

    A importação é transitória: representa um arquivo recebido, que ainda não
    virou balanço. Só a confirmação da gestão cria o
    ``BalancoMensal``. Por isso este ciclo é diferente do ciclo funcional do
    balanço: aqui se acompanha o recebimento, não a situação financeira do mês.

    Agora o fluxo passa por ``PROCESSANDO`` para identificar o layout do PDF e
    termina em ``PRONTO`` quando o formato foi classificado, ainda sem extrair
    informação financeira nenhuma. ``ERRO`` descreve falha técnica; e
    ``CONFIRMADO`` e ``DESCARTADO`` são o desfecho da importação.
    """

    RECEBIDO = "recebido", "Recebido"
    PROCESSANDO = "processando", "Processando"
    PRONTO = "pronto", "Pronto para a próxima etapa"
    ERRO = "erro", "Não foi possível receber"
    CONFIRMADO = "confirmado", "Confirmado"
    DESCARTADO = "descartado", "Descartado"


class LayoutBalanco(models.TextChoices):
    """Formato do documento recebido.

    Registra o que veio, sem definir a estrutura dos dados: existe um único
    balanço, lido por caminhos diferentes. O formato legado pode exigir OCR e
    ter menos detalhamento — o modelo interno é o mesmo.
    """

    DESCONHECIDO = "desconhecido", "Desconhecido"
    ATUAL_CANONICO = "atual_canonico", "Atual canônico"
    LEGADO = "legado", "Legado"
