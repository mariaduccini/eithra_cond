from django.db import models


class TipoAcessoPortaria(models.TextChoices):
    VISITANTE = "visitante", "Visitante"
    PRESTADOR = "prestador", "Prestador"
    FORNECEDOR = "fornecedor", "Fornecedor"
    TECNICO = "tecnico", "Técnico"
    DELIVERY = "delivery", "Delivery"
    CORREIOS = "correios", "Correios"


# Todos os tipos compartilham o mesmo fluxo: identificar quem chegou, registrar
# o contexto e dizer o resultado da autorização. O que muda é o vocabulário que
# a tela usa para pedir os dados certos, não o domínio. Delivery e Correios não
# são pessoas: são entregas com destino e uma instrução de como proceder, e por
# isso pedem campos próprios em vez de forçar o entregador a virar visitante.
ESCOLHAS_DE_TIPO_EM_USO = [
    (TipoAcessoPortaria.VISITANTE.value, TipoAcessoPortaria.VISITANTE.label),
    (TipoAcessoPortaria.PRESTADOR.value, TipoAcessoPortaria.PRESTADOR.label),
    (TipoAcessoPortaria.FORNECEDOR.value, TipoAcessoPortaria.FORNECEDOR.label),
    (TipoAcessoPortaria.TECNICO.value, TipoAcessoPortaria.TECNICO.label),
    (TipoAcessoPortaria.DELIVERY.value, TipoAcessoPortaria.DELIVERY.label),
    (TipoAcessoPortaria.CORREIOS.value, TipoAcessoPortaria.CORREIOS.label),
]

# As entradas que não são pessoas: trazem um destino e uma instrução de como
# proceder, sem exigir que o entregador seja cadastrado como visitante.
TIPOS_DE_ENTREGA = frozenset(
    {TipoAcessoPortaria.DELIVERY, TipoAcessoPortaria.CORREIOS}
)

# As transportadoras que a tela da encomenda oferece em botões. É uma lista de
# atalhos, não um catálogo: "Outra" existe para o caso que não está aqui, e o
# que se grava quando ela é escolhida é a ausência de transportadora — melhor
# nada do que um nome inventado.
TRANSPORTADORA_CORREIOS = "Correios"
TRANSPORTADORA_OUTRA = "Outra"
TRANSPORTADORAS_DE_ENTREGA = (
    (TRANSPORTADORA_CORREIOS, TRANSPORTADORA_CORREIOS),
    ("Mercado Livre", "Mercado Livre"),
    ("Amazon", "Amazon"),
    ("Shopee", "Shopee"),
    ("iFood/Delivery", "iFood/Delivery"),
    (TRANSPORTADORA_OUTRA, TRANSPORTADORA_OUTRA),
)


class NaturezaObjetoCorreios(models.TextChoices):
    """O que os Correios trouxeram, quando isso for informado.

    Vocabulário operacional da entrega, e não do domínio da presença: nem toda
    correspondência precisa ser classificada, e por isso o campo é opcional.
    """

    CARTA = "carta", "Carta"
    ENCOMENDA = "encomenda", "Encomenda"
    REGISTRADO = "registrado", "Registrado"
    OUTRO = "outro", "Outro"


class InstrucaoEntrega(models.TextChoices):
    """Como a entrega deve ser tratada no destino.

    Não é uma autorização nem um estado de presença: é a instrução combinada no
    momento da chegada — quem recebe desce, a entrega sobe ou fica aguardando
    na portaria. Fica registrada para que a decisão não dependa de memória.
    """

    MORADOR_DESCE = "morador_desce", "Morador desce para receber"
    AUTORIZADO_SUBIR = "autorizado_subir", "Autorizado a subir"
    DEIXAR_NA_PORTARIA = "deixar_na_portaria", "Deixar na portaria"


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
    # Uma autorização que chegou por e-mail não deixa de ser uma autorização
    # registrada pela portaria: a origem só diz de onde veio o aviso, para que
    # quem audita consiga separar o que foi digitado do que foi lido.
    EMAIL = "email", "E-mail"


class EventoAcessoPortaria(models.TextChoices):
    """O que aconteceu com um acesso depois de registrado.

    Vocabulário do histórico do acesso — linha acrescentada, nunca reescrita.
    Hoje o único evento é o aviso de encomenda por WhatsApp: a portaria
    registra que abriu a conversa com o morador, para que o fato não dependa
    da memória de quem estava no turno.
    """

    AVISO_WHATSAPP = "aviso_whatsapp", "Aviso por WhatsApp"


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
