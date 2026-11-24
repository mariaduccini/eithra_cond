"""Constantes que valem para mais de um domínio do Eithra.

O que mora aqui é vocabulário comum, não regra: o mesmo conjunto de palavras
usado por módulos diferentes. Cada domínio continua com as suas próprias
constantes quando elas só dizem respeito a ele — o que se evita é a mesma
escolha ser declarada duas vezes, com valores que podem divergir depois.
"""
from django.db import models


class Prioridade(models.TextChoices):
    """O quanto uma pendência pesa, na leitura de quem acompanha o condomínio.

    Não há matriz de impacto e urgência: quatro degraus, com ``NORMAL`` como
    padrão, evitam que tudo vire urgente e mantêm a lista utilizável. Demandas e
    manutenções leem o peso com as mesmas palavras — quem compara as duas listas
    está falando do mesmo assunto.
    """

    BAIXA = "baixa", "Baixa"
    NORMAL = "normal", "Normal"
    ALTA = "alta", "Alta"
    URGENTE = "urgente", "Urgente"


class OrigemImportacaoHistorica(models.TextChoices):
    """De qual fonte externa veio um lote de carga histórica.

    Gmail e Drive são fontes de migração e rastreabilidade, não dependências
    permanentes do Eithra. Depois que o dado entra, o sistema continua a viver
    sem elas.
    """

    GMAIL = "gmail", "Gmail"
    DRIVE = "drive", "Google Drive"
    IMPORTACAO = "importacao", "Importação"


class EntidadeImportacaoHistorica(models.TextChoices):
    """Que tipo de registro do Eithra um item da carga pretende atingir."""

    UNIDADE = "unidade", "Unidade"
    PESSOA = "pessoa", "Pessoa"
    VINCULO_PESSOA_UNIDADE = "vinculo_pessoa_unidade", "Vínculo pessoa x unidade"
    VEICULO = "veiculo", "Veículo"
    VINCULO_VEICULO_PESSOA = "vinculo_veiculo_pessoa", "Vínculo veículo x pessoa"
    VINCULO_VEICULO_UNIDADE = (
        "vinculo_veiculo_unidade",
        "Vínculo veículo x unidade",
    )
    VAGA_GARAGEM = "vaga_garagem", "Vaga de garagem"
    VINCULO_VAGA_UNIDADE = "vinculo_vaga_unidade", "Vínculo vaga x unidade"
    FORNECEDOR = "fornecedor", "Fornecedor"
    LOCAL_OPERACIONAL = "local_operacional", "Local operacional"
    EQUIPAMENTO = "equipamento", "Equipamento"
    CONTRATO_FORNECEDOR = "contrato_fornecedor", "Contrato"
    GARANTIA = "garantia", "Garantia"
    DOCUMENTO_OPERACIONAL = "documento_operacional", "Documento operacional"
    ROTINA = "rotina", "Rotina"
    MANUTENCAO = "manutencao", "Manutenção"
    DEMANDA = "demanda", "Demanda"
    PROJETO = "projeto", "Projeto"
    PROJETO_ITEM = "projeto_item", "Item de projeto"
    AUTORIZACAO_PORTARIA = "autorizacao_portaria", "Autorização de portaria"


class ResultadoImportacaoHistorica(models.TextChoices):
    """Como terminou a tentativa de registrar um item da carga histórica."""

    PENDENTE = "pendente", "Pendente"
    CRIADO = "criado", "Criado"
    ATUALIZADO = "atualizado", "Atualizado"
    IGNORADO = "ignorado", "Ignorado"
    ERRO = "erro", "Erro"


class EstadoProcessamentoImportacao(models.TextChoices):
    """Em que ponto do ciclo operacional este item de fonte está.

    É o estado do próprio item da fonte — não da consequência no domínio. Um
    item pode já ter sido processado uma vez e voltar a ``PROCESSANDO`` por
    reprocessamento explícito, preservando a identidade externa e os vínculos
    que já foram descobertos.
    """

    PENDENTE = "pendente", "Pendente"
    PROCESSANDO = "processando", "Processando"
    AGUARDANDO_CONFIRMACAO = (
        "aguardando_confirmacao",
        "Aguardando confirmação",
    )
    PROCESSADO = "processado", "Processado"
    ERRO = "erro", "Erro"
