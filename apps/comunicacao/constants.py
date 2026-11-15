"""Constantes da capacidade transversal de comunicação.

Comunicação é o comunicado que a gestão faz ao morador — um aviso de manutenção,
uma interdição, um informativo. Não é conversa, comentário, feed nem mensagem
social: é um card visual, com autoria, modelo, público e arquivo gerado, para ser
compartilhado.

A capacidade é transversal de propósito: ela não pertence a um domínio. Demanda e
manutenção são as origens desta fase; portaria, rotinas e risco ambiental entram
depois, sem que a comunicação precise conhecer cada um deles por dentro.
"""
from django.db import models


class ModeloComunicado(models.TextChoices):
    """O tom e o desenho do card.

    Cinco modelos bastam para a gestão escolher sem pensar muito. Nenhum deles é
    um editor: o modelo decide a cor e o rótulo do card, e o texto continua sendo
    de quem escreve.
    """

    AVISO = "aviso", "Aviso"
    MANUTENCAO = "manutencao", "Manutenção"
    URGENTE = "urgente", "Urgente"
    INFORMATIVO = "informativo", "Informativo"
    ALERTA = "alerta", "Alerta"


class OrientacaoDoCard(models.TextChoices):
    """Como o card se apresenta.

    Vertical é o formato natural do celular e do WhatsApp; horizontal serve a
    quadros e telas. Duas leituras bastam, e nenhuma delas deforma o conteúdo.
    """

    VERTICAL = "vertical", "Vertical"
    HORIZONTAL = "horizontal", "Horizontal"


class PublicoComunicado(models.TextChoices):
    """A quem o comunicado se destina.

    É dado estruturado, mesmo sem canal de envio nesta fase: quem escreve diz o
    alcance agora, e o envio futuro lê isso em vez de adivinhar.
    """

    TODOS = "todos", "Todos os moradores"
    BLOCO = "bloco", "Um bloco ou torre"
    UNIDADES = "unidades", "Unidades específicas"
    EQUIPE = "equipe", "Equipe interna"


class OrigemComunicado(models.TextChoices):
    """De onde o comunicado partiu.

    A origem é referência, não cópia: a comunicação aponta para a demanda ou a
    manutenção que a motivou, e quem responde pelos dados continua sendo o
    domínio de origem. ``AVULSO`` é o comunicado que nasce sem origem.
    """

    DEMANDA = "demanda", "Demanda"
    MANUTENCAO = "manutencao", "Manutenção"
    PORTARIA = "portaria", "Portaria"
    ROTINA = "rotina", "Rotina"
    AMBIENTAL = "ambiental", "Risco ambiental"
    AVULSO = "avulso", "Sem origem"


# Origens que, nesta fase, já podem ser apontadas por uma comunicação. As demais
# existem no vocabulário para o futuro e não têm chave estrangeira ainda — usá-las
# seria prometer um vínculo que o sistema não guarda.
ORIGENS_COM_REFERENCIA = frozenset(
    {OrigemComunicado.DEMANDA, OrigemComunicado.MANUTENCAO}
)


class StatusComunicacao(models.TextChoices):
    """Onde está a comunicação.

    Rascunho é a comunicação escrita e ainda sem card; pronta é a que já tem a
    imagem gerada e guardada. Não existe "enviada" nesta fase — o envio por canal
    externo é etapa posterior.
    """

    RASCUNHO = "rascunho", "Rascunho"
    PRONTA = "pronta", "Pronta"


class TipoEventoComunicacao(models.TextChoices):
    """O que aconteceu com a comunicação, na memória dela.

    São fatos da própria comunicação — criada, revisada, imagem gerada —, e não
    do domínio que a originou. Por isso vivem aqui, em histórico próprio.
    """

    CRIADA = "criada", "Comunicado criado"
    EDITADA = "editada", "Comunicado revisado"
    IMAGEM_GERADA = "imagem_gerada", "Imagem gerada"


# O que o arquivo gerado é, no banco e na borda. O card sai sempre em PNG.
MIMES_DE_COMUNICADO = ("image/png",)

# Limites de texto do card. Existem para a arte continuar legível: texto que não
# cabe vira arte ilegível, e arte ilegível não comunica nada.
LIMITE_DO_TITULO = 120
LIMITE_DA_MENSAGEM = 600
LIMITE_DO_LOCAL = 150
LIMITE_DO_PUBLICO = 200
