"""Leitura do que a fala quer dizer na portaria.

Camada 3 do núcleo de áudio, do lado da Portaria. A transcrição já chegou fiel
(``apps/core/audio.py``); aqui ela vira intenção operacional — chegou alguém,
alguém saiu, houve autorização, alguém relatou um problema, ou não deu para
entender.

Nada é registrado aqui: as operações continuam sendo as de ``services.py``, e só
são chamadas depois que a portaria confirma. Este módulo também não transcreve e
não classifica demanda — para demanda, quem lê é
``apps/demandas/interpretacao.py``, que já existia antes deste núcleo. Duplicar
aquilo aqui seria criar uma segunda verdade sobre o mesmo relato.

Duas coisas sustentam a leitura, nesta ordem:

* **a fala** diz o que aconteceu, com as palavras de quem está na portaria;
* **o condomínio** confirma a quem aquilo se refere — unidade, pessoa, presença
  aberta. É o cadastro que transforma "902" em uma unidade de verdade, e é ele
  que decide quando a fala é ambígua.

A confiança sai daí. Unidade, pessoa e a ação de saída são dados críticos: sem
cadastro que os confirme, não viram fato — viram pergunta. Quando a dúvida é
sobre qual unidade ou qual presença, o Eithra pergunta; não escolhe.

E antes de perguntar qualquer coisa, o contexto é procurado nesta ordem, porque
unidade não é o único destino possível de um acesso:

1. existe visita ou prestador **esperado** para agora?
2. existe **serviço do condomínio** compatível — manutenção aberta, chamado da
   síndica ou da supervisão?
3. a empresa citada é **fornecedor conhecido** desta portaria?
4. a fala cita **unidade ou morador**?
5. só então se pergunta o que faltou — e apenas o que faltou.

Sem esses passos, "chegou o rapaz da Atlas" viraria uma visita sem unidade, e a
portaria seria obrigada a inventar um destino para um prestador que veio ao
condomínio inteiro.
"""
from __future__ import annotations

import re

from django.db.models import Q

from apps.cadastros.models import Equipamento, Fornecedor, LocalOperacional
from apps.comunidade.models import Pessoa, Unidade
from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio
from apps.core import texto
from apps.core.interpretacao import Confianca, Entidade, Estado, Intencao
from apps.demandas.models import Demanda
from apps.manutencoes.models import Manutencao

from .constants import (
    TIPOS_DE_ENTREGA,
    TRANSPORTADORA_CORREIOS,
    ContextoAcessoPortaria,
    IntencaoPortaria,
    MeioAutorizacaoPortaria,
    StatusAutorizacaoPortaria,
    TipoAcessoPortaria,
)
from .models import AcessoPortaria, AutorizacaoPortaria
from .services import empresas_conhecidas

# ---------------------------------------------------------------------------
# Vocabulário da leitura
# ---------------------------------------------------------------------------
# Palavras escritas como a portaria fala — sem acento de propósito: a
# normalização de apps/core/texto.py tira o acento dos dois lados, e manter as
# listas na forma crua evitaria o casamento por um detalhe de digitação.
PALAVRAS_DE_CHEGADA = (
    "chegou",
    "chegaram",
    "chegando",
    "acabou de chegar",
    "acabaram de chegar",
    "esta aqui",
    "ta aqui",
    "esta na portaria",
    "ta na portaria",
    "aqui embaixo",
    "quer subir",
    "quer entrar",
    "veio ver",
    "veio para",
    "veio pra",
    "tem uma visita",
    "tem um visitante",
    "tem um entregador",
    "subindo",
)

PALAVRAS_DE_SAIDA = (
    "saiu",
    "sairam",
    "saindo",
    "foi embora",
    "foram embora",
    "deixou o predio",
    "deixaram o predio",
    "acabou de sair",
)

PALAVRAS_DE_AUTORIZACAO = (
    "autorizou",
    "autorizado",
    "autorizaram",
    "liberou",
    "liberado",
    "liberaram",
    "pode entrar",
    "confirmou a entrada",
)

PALAVRAS_DE_RECUSA = (
    "nao autorizou",
    "nao liberou",
    "recusou",
    "negou",
    "nao pode entrar",
    "nao deixou entrar",
)

PALAVRAS_DE_SEM_RESPOSTA = (
    "sem resposta",
    "nao respondeu",
    "nao atendeu",
    "nao atende",
    "ninguem respondeu",
    "ninguem atendeu",
)

PALAVRAS_DE_PROBLEMA = (
    "problema",
    "defeito",
    "quebrou",
    "quebrado",
    "avariado",
    "danificado",
    "parou",
    "parado",
    "travou",
    "vazamento",
    "vazando",
    "barulho",
    "ruido",
    "nao funciona",
    "nao esta funcionando",
    "fora de servico",
)

PALAVRAS_DE_PRESTADOR = (
    "prestador",
    "tecnico",
    "fornecedor",
    "manutencao",
    "instalador",
    "eletricista",
    "encanador",
    "pintor",
    "mecanico",
    "montador",
)

PALAVRAS_DE_VISITANTE = (
    "visita",
    "visitante",
    "convidado",
    "amigo",
    "familiar",
    "parente",
)

# Quem chega trazendo algo, e não visitando: entregador e transportadora. Quem
# chega assim não tem nome de pessoa — o nome é a transportadora, quando dita.
PALAVRAS_DE_ENTREGA = (
    "entregador",
    "entrega",
    "delivery",
    "ifood",
    "rappi",
    "motoboy",
    "correios",
    "sedex",
    "mercado livre",
    "shopee",
    "amazon",
    "pacote",
    "encomenda",
)

# "correios" e "sedex" não são só entrega: são a transportadora Correios.
PALAVRAS_DE_CORREIOS = ("correios", "sedex")

# O que a entrega dizia sem nomear quem entrega. A transportadora (iFood,
# Correios) continua nomeável: ela é o nome de quem chega na entrega.
PALAVRAS_DE_ENTREGA_GENERICAS = ("entrega", "delivery", "motoboy", "pacote", "encomenda")

# Palavras que já nomeiam quem chega: visita, serviço ou entrega. Com uma delas
# — ou com uma unidade — a fala é chegada mesmo sem o verbo "chegou" (§D1).
PALAVRAS_DE_TIPO_DE_ACESSO = (
    frozenset(PALAVRAS_DE_VISITANTE)
    | frozenset(PALAVRAS_DE_PRESTADOR)
    | frozenset(PALAVRAS_DE_ENTREGA)
)

# Palavras que dizem que há um serviço, mas não qual: nomeiam a natureza do
# trabalho ou o papel de quem o faz, nunca o objeto dele. Numa correspondência
# de serviço elas não contam como evidência — do contrário, "chegou para uma
# manutenção" casaria com toda demanda chamada "Manutenção ...". É a lista de
# §8: "problema", "manutenção" e "técnico" não relacionam nada sozinhos.
PALAVRAS_GENERICAS_DE_SERVICO = (
    frozenset(PALAVRAS_DE_PRESTADOR)
    | frozenset(PALAVRAS_DE_PROBLEMA)
    | frozenset(
        {"servico", "servicos", "reparo", "reparos", "conserto", "consertos", "consertar"}
    )
)

# Quem vem prestar serviço, e não visitar. São estes que podem ter o condomínio
# inteiro como destino — e é por isso que não se pergunta unidade a eles.
TIPOS_DE_SERVICO = frozenset(
    {
        TipoAcessoPortaria.PRESTADOR.value,
        TipoAcessoPortaria.FORNECEDOR.value,
        TipoAcessoPortaria.TECNICO.value,
    }
)

# Quem pediu o serviço, quando a fala diz. A administradora, a síndica e a
# supervisão chamam prestador para o condomínio inteiro: é o que dispensa
# perguntar unidade.
PALAVRAS_DE_SOLICITANTE = (
    "sindica",
    "sindico",
    "supervisora",
    "supervisor",
    "administracao",
)

# O papel de cadastro que responde por cada palavra acima. Serve para checar se
# o condomínio tem mesmo alguém neste papel — nunca para dar a informação por
# confirmada: quem chamou foi quem falou, e isso continua sendo declarado.
PAPEIS_DO_SOLICITANTE = {
    "sindica": (Papel.SINDICA, "Síndica"),
    "sindico": (Papel.SINDICA, "Síndico"),
    "supervisora": (Papel.SUPERVISORA, "Supervisão"),
    "supervisor": (Papel.SUPERVISORA, "Supervisão"),
    "administracao": (Papel.ADMINISTRADORA, "Administradora"),
}

# "da Atlas", "do fornecedor X", "da empresa X": a empresa vem depois de uma
# ligação, e estas são as ligações que a portaria usa.
CONECTORES_DE_EMPRESA = frozenset({"da", "do", "de", "das", "dos"})

# Palavras que podem aparecer entre a ligação e o nome da empresa. "do
# fornecedor Atlas" nomeia a mesma Atlas que "da Atlas".
LIGACOES_DE_EMPRESA = frozenset({"fornecedor", "empresa", "firma", "prestador"})

# Onde o nome termina. Sem isto, a leitura de "chegou o eletricista que a
# síndica chamou" continuaria até achar alguém — e acharia a síndica.
LIMITES_DO_NOME = frozenset(
    {"para", "pra", "e", "que", "disse", "falou", "avisou", "com", "no", "na", "em"}
)

# Aliases operacionais (§24). Não é cadastro de apelidos: é uma tabela curta,
# escrita à mão, que traduz o jeito de falar para o valor que o domínio guarda.
# Um cadastro de aliases por condomínio é decisão de outra rodada; o que este
# mecanismo precisa provar agora é que a tradução tem um lugar definido.
ALIASES_DE_MEIO = (
    (
        MeioAutorizacaoPortaria.INTERFONE,
        ("interfone", "no interfone", "pelo interfone"),
    ),
    (
        MeioAutorizacaoPortaria.TELEFONE,
        ("telefone", "no telefone", "por telefone", "ligou", "celular", "whatsapp"),
    ),
    (
        MeioAutorizacaoPortaria.PRESENCIAL,
        ("presencial", "pessoalmente", "na portaria", "de frente", "aqui mesmo"),
    ),
)

# Como o meio aparece no resumo de confirmação. "pelo presencial" não é português.
FRASE_DO_MEIO = {
    MeioAutorizacaoPortaria.INTERFONE.value: "pelo interfone",
    MeioAutorizacaoPortaria.TELEFONE.value: "pelo telefone",
    MeioAutorizacaoPortaria.PRESENCIAL.value: "pessoalmente",
}

# Ações sugeridas ao domínio. Nomes estáveis: a tela que consome isto precisa de
# um contrato, e a operação em si continua sendo chamada só depois da confirmação.
ACAO_DE_CHEGADA = "registrar_chegada"
ACAO_DE_SAIDA = "registrar_saida"
ACAO_DE_AUTORIZACAO = "registrar_autorizacao"
ACAO_DE_OCORRENCIA = "registrar_ocorrencia"

# O que nunca é nome de pessoa: artigos, preposições, o vocabulário do próprio
# domínio — inclusive os verbos que disparam a leitura — e os substantivos com
# que a portaria se refere a alguém sem nomear ("o rapaz da Atlas"). Sem esta
# lista, "pelo interfone" viraria o nome do autorizador e "técnico" viraria o
# nome de quem chegou.
PALAVRAS_QUE_NAO_SAO_NOME = (
    frozenset(
        {
            "o", "a", "os", "as", "um", "uma", "do", "da", "dos", "das", "de",
            "no", "na", "nos", "nas", "ao", "aos", "para", "pra", "com", "e",
            "que", "por", "pelo", "pela", "sr", "sra", "seu", "sua", "dona",
            "esta", "estao", "sao", "mas", "ainda", "ja", "so", "la", "aqui",
            "entao", "hoje", "agora", "veio", "vai", "disse", "falou", "avisou",
            "nao", "sim", "ninguem", "nunca",
            "morador", "moradora", "portaria", "predio", "condominio",
            "apartamento", "apto", "ap", "unidade", "bloco", "casa", "loja",
            "sala", "servico", "reparo",
            "interfone", "telefone", "celular", "whatsapp",
            "presencial", "pessoalmente", "chegou", "chegaram", "chegando",
            "saiu", "sairam", "saindo", "autorizou", "autorizaram",
            "autorizado", "liberou", "liberaram", "liberado", "entrada",
            "saida", "chegada",
            "rapaz", "moco", "moca", "homem", "mulher", "senhor", "senhora",
            "garoto", "garota", "motorista", "entregador",
            "oi", "ola", "bom", "boa", "dia", "tarde", "noite", "olha", "ei",
            "tipo", "ta", "ai", "embaixo", "subir", "entrar", "ver", "quer",
            "tem", "pro", "pra", "visita", "aqui", "ali", "gente", "pessoal",
            "ok",
        }
    )
    | frozenset(PALAVRAS_DE_PRESTADOR)
    | frozenset(PALAVRAS_DE_VISITANTE)
    | frozenset(PALAVRAS_DE_ENTREGA_GENERICAS)
    | frozenset(PAPEIS_DO_SOLICITANTE)
)

# Números que podem ser unidade. Dois dígitos já bastam ("sala 12"); um só não,
# porque casaria com andar, hora ou quantidade dita de passagem.
PADRAO_DE_NUMERO = re.compile(r"\b([0-9]{2,4}[a-z]?)\b")

# Tokens de um texto, preservando a caixa original — é dela que sai o nome.
PADRAO_DE_TOKEN = re.compile(r"[0-9A-Za-zÀ-ÿ]+")


def interpretar(texto_falado: str | None, *, condominio=None) -> Intencao:
    """Lê a fala e devolve a intenção — sem registrar nada.

    Devolve sempre uma ``Intencao``: quando nada foi reconhecido, o tipo é
    "informação insuficiente" e vem a pergunta mínima. Silêncio aqui não é
    opção, porque quem está na portaria acabou de falar e espera uma resposta.
    """
    lido = texto.normalizar(texto_falado)
    if not lido:
        return Intencao(
            tipo=IntencaoPortaria.INSUFICIENTE,
            confianca=Confianca.BAIXA,
            perguntas=("O que aconteceu?",),
        )

    sinais = _sinais_do_texto(lido)
    if not sinais:
        return Intencao(
            tipo=IntencaoPortaria.INSUFICIENTE,
            confianca=Confianca.BAIXA,
            perguntas=("Chegou alguém, alguém saiu, ou é um problema no condomínio?",),
        )

    principal, *resto = sinais
    # Numa chegada ou saída, a autorização não é outra consequência: faz parte
    # dela. Só o relato de problema sobrevive como consequência separada — é
    # ele que pede uma segunda ação, e é o exemplo de "uma fala, duas coisas".
    secundarias = tuple(
        sinal
        for sinal in resto
        if not (
            principal in (IntencaoPortaria.CHEGADA, IntencaoPortaria.SAIDA)
            and sinal == IntencaoPortaria.AUTORIZACAO
        )
    )
    original = texto_falado or ""

    if principal == IntencaoPortaria.CHEGADA:
        return _ler_chegada(lido, original, condominio, secundarias)
    if principal == IntencaoPortaria.SAIDA:
        return _ler_saida(lido, original, condominio, secundarias)
    if principal == IntencaoPortaria.AUTORIZACAO:
        return _ler_autorizacao(lido, original, condominio, secundarias)
    return _ler_problema(secundarias)


def corrigir_tipo(intencao: Intencao, tipo: str) -> None:
    """Troca o tipo de acesso lido pelo que a portaria corrigiu nos chips.

    Quem vê quem está na frente é a portaria; a leitura só ouve a fala. A
    correção muda apenas o tipo: o resto da leitura continua sendo o de agora.
    Um valor fora do vocabulário do domínio não muda nada.
    """
    if not tipo:
        return
    try:
        rotulo = TipoAcessoPortaria(tipo).label
    except ValueError:
        return
    intencao.entidades["tipo_acesso"] = Entidade(
        campo="tipo_acesso",
        valor=tipo,
        rotulo=rotulo,
        confianca=Confianca.ALTA,
        estado=Estado.DECLARADA,
        motivo="A portaria corrigiu o tipo de quem chegou.",
    )


# ---------------------------------------------------------------------------
# Sinais
# ---------------------------------------------------------------------------


def _sinais_do_texto(lido: str) -> list[str]:
    """As intenções que a fala contém, na ordem em que a portaria as lê."""
    sinais: list[str] = []
    if texto.contem(lido, PALAVRAS_DE_CHEGADA):
        sinais.append(IntencaoPortaria.CHEGADA)
    if texto.contem(lido, PALAVRAS_DE_SAIDA):
        sinais.append(IntencaoPortaria.SAIDA)
    if _status_da_fala(lido).existe:
        sinais.append(IntencaoPortaria.AUTORIZACAO)
    if texto.contem(lido, PALAVRAS_DE_PROBLEMA):
        sinais.append(IntencaoPortaria.RELATO_DE_PROBLEMA)
    if not sinais and _parece_chegada(lido):
        sinais.append(IntencaoPortaria.CHEGADA)
    return sinais


def _parece_chegada(lido: str) -> bool:
    """Chegada sem o verbo: uma unidade ou um tipo de quem chega já basta.

    O porteiro fala "Visita pro 302, Ana" sem dizer "chegou". Se a fala não
    indica saída nem problema, e nomeia quem chega (visita, serviço, entrega)
    ou traz um número que pode ser unidade, ela é uma chegada.
    """
    if texto.contem(lido, PALAVRAS_DE_SAIDA) or texto.contem(lido, PALAVRAS_DE_PROBLEMA):
        return False
    if texto.contem(lido, PALAVRAS_DE_TIPO_DE_ACESSO):
        return True
    return bool(PADRAO_DE_NUMERO.search(lido))


def _status_da_fala(lido: str) -> Entidade:
    """O resultado da autorização, quando a fala disse qual foi.

    A recusa vem antes da autorização de propósito: "não autorizou" contém
    "autorizou", e ler pela ordem ingênua inverteria o sentido da frase.

    Quem disse o resultado foi quem falou, e é isso que a entidade registra: a
    fala é clara, mas continua sendo declaração — não há cadastro que confirme
    um "autorizou" dito na portaria.
    """
    tabela = (
        (StatusAutorizacaoPortaria.NAO_AUTORIZADO, PALAVRAS_DE_RECUSA),
        (StatusAutorizacaoPortaria.SEM_RESPOSTA, PALAVRAS_DE_SEM_RESPOSTA),
        (StatusAutorizacaoPortaria.AUTORIZADO, PALAVRAS_DE_AUTORIZACAO),
    )
    for status, palavras in tabela:
        if texto.contem(lido, palavras):
            return Entidade(
                campo="status",
                valor=status.value,
                rotulo=status.label,
                confianca=Confianca.ALTA,
                estado=Estado.DECLARADA,
                motivo="A fala disse o resultado da autorização.",
            )
    return Entidade()


def _tipo_de_acesso(lido: str, empresa: Entidade, servico: Entidade) -> Entidade:
    """Visita, serviço ou entrega — sempre com um valor, nunca vazio.

    O que a fala diz vem primeiro: "técnico", "visita" ou "entregador" têm
    nome, e nomeá-los é declarar. A entrega vem depois da visita: quem entrega
    não visita. Sem isso, o cadastro ajuda a decidir — um serviço do condomínio
    ou uma empresa conhecida são serviço, não visita. É inferência, e a origem
    diz isso: o cadastro sustenta, a fala não disse.
    """
    if texto.contem(lido, PALAVRAS_DE_PRESTADOR):
        return Entidade(
            campo="tipo_acesso",
            valor=TipoAcessoPortaria.PRESTADOR.value,
            rotulo=TipoAcessoPortaria.PRESTADOR.label,
            confianca=Confianca.ALTA,
            estado=Estado.DECLARADA,
            motivo="A fala indicou serviço, empresa ou manutenção.",
        )
    if texto.contem(lido, PALAVRAS_DE_VISITANTE):
        return Entidade(
            campo="tipo_acesso",
            valor=TipoAcessoPortaria.VISITANTE.value,
            rotulo=TipoAcessoPortaria.VISITANTE.label,
            confianca=Confianca.ALTA,
            estado=Estado.DECLARADA,
            motivo="A fala indicou visita.",
        )
    if texto.contem(lido, PALAVRAS_DE_ENTREGA):
        entrega = (
            TipoAcessoPortaria.CORREIOS
            if texto.contem(lido, PALAVRAS_DE_CORREIOS)
            else TipoAcessoPortaria.DELIVERY
        )
        return Entidade(
            campo="tipo_acesso",
            valor=entrega.value,
            rotulo=entrega.label,
            confianca=Confianca.ALTA,
            estado=Estado.DECLARADA,
            motivo="A fala indicou entrega.",
        )
    if servico.existe:
        return Entidade(
            campo="tipo_acesso",
            valor=TipoAcessoPortaria.PRESTADOR.value,
            rotulo=TipoAcessoPortaria.PRESTADOR.label,
            confianca=Confianca.MEDIA,
            estado=Estado.INFERIDA,
            motivo="A fala citou um serviço do condomínio.",
        )
    if empresa.confirmada:
        return Entidade(
            campo="tipo_acesso",
            valor=TipoAcessoPortaria.FORNECEDOR.value,
            rotulo=TipoAcessoPortaria.FORNECEDOR.label,
            confianca=Confianca.MEDIA,
            estado=Estado.INFERIDA,
            motivo="A empresa citada é conhecida desta portaria.",
        )
    return Entidade(
        campo="tipo_acesso",
        valor=TipoAcessoPortaria.VISITANTE.value,
        rotulo=TipoAcessoPortaria.VISITANTE.label,
        confianca=Confianca.MEDIA,
        estado=Estado.INFERIDA,
        motivo="Não foi dito; a portaria trata como visita até alguém corrigir.",
    )


def _meio_da_fala(lido: str) -> Entidade:
    for meio, apelidos in ALIASES_DE_MEIO:
        if texto.contem(lido, apelidos):
            return Entidade(
                campo="meio",
                valor=meio.value,
                rotulo=meio.label,
                confianca=Confianca.ALTA,
                estado=Estado.DECLARADA,
                motivo="A fala nomeou como a autorização aconteceu.",
            )
    return Entidade()


# ---------------------------------------------------------------------------
# Entidades do cadastro
# ---------------------------------------------------------------------------


def _unidade_da_fala(lido: str, condominio) -> tuple[Entidade, tuple[Unidade, ...]]:
    """A unidade citada e, quando houver mais de uma, as candidatas.

    Número solto não é unidade: vira unidade só depois de conferir o cadastro.
    Se o mesmo número responder por mais de uma unidade — "101" existe no bloco
    A e no bloco B —, a leitura para e devolve as duas, para que a portaria
    pergunte em vez de escolher.
    """
    citacoes = tuple(dict.fromkeys(PADRAO_DE_NUMERO.findall(lido)))
    if not citacoes:
        return Entidade(), ()

    achadas = [
        unidade
        for unidade in _unidades_do_condominio(condominio)
        if _unidade_casa(unidade.identificador, citacoes)
    ]
    if len(achadas) == 1:
        return _entidade_de_unidade(achadas[0]), ()
    if len(achadas) > 1:
        return (
            Entidade(
                campo="unidade",
                valor=citacoes[0],
                rotulo=citacoes[0],
                confianca=Confianca.BAIXA,
                estado=Estado.AMBIGUA,
                motivo="Mais de uma unidade cadastrada responde por este número.",
            ),
            tuple(achadas),
        )
    return (
        Entidade(
            campo="unidade",
            valor=citacoes[0],
            rotulo=citacoes[0],
            confianca=Confianca.MEDIA,
            estado=Estado.DECLARADA,
            motivo="A fala informou este número; não há cadastro que o confirme.",
        ),
        (),
    )


def _entidade_de_unidade(unidade: Unidade | None) -> Entidade:
    """A unidade do cadastro como entidade — sempre confirmada por ele."""
    if unidade is None:
        return Entidade()
    return Entidade(
        campo="unidade",
        valor=unidade.identificador,
        rotulo=unidade.rotulo,
        objeto=unidade,
        confianca=Confianca.ALTA,
        estado=Estado.CONFIRMADA,
        motivo="Confere com uma unidade cadastrada neste condomínio.",
    )


def _unidades_do_condominio(condominio) -> list[Unidade]:
    if condominio is None or not getattr(condominio, "pk", None):
        return []
    return list(Unidade.objects.do_condominio(condominio).ativas())


def _unidade_casa(identificador: str, citacoes) -> bool:
    """Se algum pedaço do identificador é um dos números ditos.

    Comparar pedaço a pedaço faz "A-101", "Casa 03" e "101" responderem ao
    número que a portaria fala, sem exigir que o cadastro use a mesma forma.
    """
    pedacos = [
        pedaco
        for pedaco in re.split(r"[^0-9a-z]+", texto.normalizar(identificador))
        if pedaco
    ]
    return any(citacao in pedacos for citacao in citacoes)


def unidade_citada(identificacao: str, condominio) -> Unidade | None:
    """A unidade do cadastro que responde por este número — ou nenhuma.

    É a mesma conferência que a portaria faz da fala, exposta para quem chega
    por outro canal: um e-mail que cita "apto 902" só vira unidade de verdade
    quando o cadastro confirma uma só. Havendo mais de uma — o número existe no
    bloco A e no B — ou nenhuma, devolve ``None``: quem decide é a pessoa, e
    quem lê não escolhe no lugar dela.
    """
    entidade, _ = _unidade_da_fala(texto.normalizar(identificacao), condominio)
    if entidade.estado == Estado.CONFIRMADA and entidade.objeto is not None:
        return entidade.objeto
    return None


def _pessoa_da_fala(lido: str, original: str, condominio, verbos) -> Entidade:
    """A pessoa nomeada em volta de um verbo — confirmada ou apenas declarada."""
    nome = _nome_em_volta_do_verbo(original, verbos)
    if not nome:
        return Entidade()

    conhecida = _pessoa_do_cadastro(nome, condominio)
    if conhecida is not None:
        return _entidade_de_pessoa(conhecida)
    return Entidade(
        campo="pessoa",
        valor=nome,
        rotulo=nome,
        confianca=Confianca.MEDIA,
        estado=Estado.DECLARADA,
        motivo="A fala nomeou esta pessoa; não há cadastro que a confirme.",
    )


def _entidade_de_pessoa(pessoa) -> Entidade:
    """A pessoa do cadastro como entidade — sempre confirmada por ele."""
    if pessoa is None or not getattr(pessoa, "pk", None):
        return Entidade()
    rotulo = pessoa.nome_social or pessoa.nome
    return Entidade(
        campo="pessoa",
        valor=rotulo,
        rotulo=rotulo,
        objeto=pessoa,
        confianca=Confianca.ALTA,
        estado=Estado.CONFIRMADA,
        motivo="Confere com uma pessoa cadastrada neste condomínio.",
    )


def _pessoa_do_cadastro(nome: str, condominio):
    """A pessoa do condomínio com exatamente este nome — ou nenhuma.

    Só o nome inteiro confirma. Casar por pedaço faria "Ana" encontrar toda
    "Ana" do condomínio e transformaria uma declaração em fato por acidente.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return None
    alvo = texto.normalizar(nome)
    if not alvo:
        return None
    for pessoa in Pessoa.objects.do_condominio(condominio).ativas():
        for candidato in (pessoa.nome, pessoa.nome_social):
            if texto.normalizar(candidato) == alvo:
                return pessoa
    return None


def _presencas_compativeis(*, pessoa: Entidade, unidade: Entidade, condominio) -> list:
    """As presenças abertas que podem ser a de quem a fala diz que saiu."""
    if condominio is None or not getattr(condominio, "pk", None):
        return []
    filtros = Q()
    if unidade.objeto is not None:
        filtros &= Q(unidade=unidade.objeto)
    if pessoa.existe:
        nome = pessoa.valor
        filtros &= (
            Q(nome__icontains=nome)
            | Q(pessoa__nome__icontains=nome)
            | Q(pessoa__nome_social__icontains=nome)
            | Q(empresa__icontains=nome)
        )
    if not filtros:
        return []
    return list(
        _acessos_abertos(condominio).filter(filtros).order_by("entrada_em")
    )


def _acessos_abertos(condominio):
    return AcessoPortaria.objects.do_condominio(condominio).abertas()


# ---------------------------------------------------------------------------
# Contexto do acesso: onde a fala diz que a pessoa vai
# ---------------------------------------------------------------------------
# Um acesso pode terminar em uma unidade, no condomínio inteiro, em um serviço
# do condomínio ou em nada disso — e a unidade é só um desses destinos. Estas
# leituras vêm antes de qualquer pergunta de propósito: só se pergunta o que
# continua faltando depois que elas não acharam nada.


def _empresa_da_fala(original: str) -> str:
    """O nome da empresa que a fala traz depois de uma ligação.

    "da Atlas", "do fornecedor X", "da empresa X": a portaria diz a empresa
    logo depois de uma ligação, e é isso que se lê. Não é uma janela de tantos
    tokens — a empresa pode estar logo depois do verbo ou a três palavras dele,
    e o que decide é a ligação, não a distância.
    """
    tokens = _tokens(original)
    for indice, token in enumerate(tokens):
        if texto.normalizar(token) not in CONECTORES_DE_EMPRESA:
            continue
        nome = _nome_da_empresa(tokens, indice + 1)
        if nome:
            return nome
    return ""


def _nome_da_empresa(tokens: list[str], inicio: int) -> str:
    """O nome depois da ligação, saltando "fornecedor" e "empresa".

    A ligação sozinha não nomeia nada: "chegou o rapaz da Atlas" nomeia, e
    "chegou a visita do 902" não — o número que vem depois da ligação não é
    empresa, e por isso não há nome a devolver.
    """
    for token in tokens[inicio : inicio + 2]:
        if texto.normalizar(token) in LIGACOES_DE_EMPRESA:
            continue
        return token if _serve_como_empresa(token) else ""
    return ""


def _serve_como_empresa(token: str) -> bool:
    """Se este token pode ser o nome de uma empresa dito na fala."""
    normalizado = texto.normalizar(token)
    if normalizado in LIMITES_DO_NOME:
        return False
    return _serve_como_nome(token)


def _empresa_do_acesso(original: str, pessoa: Entidade, condominio) -> Entidade:
    """A empresa desta fala — a que a portaria reconhece, e nenhuma outra.

    A empresa aparece de duas formas: depois de uma ligação ("da Atlas") ou
    como o nome que a fala deu a quem chegou ("A Atlas chegou"). Em qualquer
    delas vale a mesma regra: só entra o nome que a portaria conhece. Um nome
    solto, sem cadastro e sem histórico, continua sendo uma pessoa declarada —
    transformá-lo em empresa seria inventar o que a fala não disse.
    """
    citada = _empresa_conhecida(_empresa_da_fala(original), condominio)
    if citada.existe and (citada.confirmada or not pessoa.existe):
        return citada
    if pessoa.existe and not pessoa.confirmada:
        pelo_nome = _empresa_conhecida(pessoa.valor, condominio)
        if pelo_nome.confirmada:
            return pelo_nome
    return Entidade()


def _empresa_conhecida(nome: str, condominio) -> Entidade:
    """A empresa citada — confirmada pelo cadastro, ou apenas declarada.

    O cadastro de fornecedores confirma: aquela empresa é conhecida daqui, e
    isso é fato. Um nome que só a fala trouxe não é — e a origem diz qual dos
    dois é, para que ninguém leia uma declaração como certeza.
    """
    if not nome:
        return Entidade()

    fornecedor = _fornecedor_do_cadastro(nome, condominio)
    if fornecedor is not None:
        rotulo = str(fornecedor)
        return Entidade(
            campo="empresa",
            valor=rotulo,
            rotulo=rotulo,
            objeto=fornecedor,
            confianca=Confianca.ALTA,
            estado=Estado.CONFIRMADA,
            motivo="Confere com um fornecedor cadastrado neste condomínio.",
        )
    if nome.casefold() in _nomes_conhecidos(condominio):
        return Entidade(
            campo="empresa",
            valor=nome,
            rotulo=nome,
            confianca=Confianca.MEDIA,
            estado=Estado.CONFIRMADA,
            motivo="Esta empresa já passou por esta portaria antes.",
        )
    return Entidade(
        campo="empresa",
        valor=nome,
        rotulo=nome,
        confianca=Confianca.MEDIA,
        estado=Estado.DECLARADA,
        motivo="A fala nomeou esta empresa; não há cadastro que a confirme.",
    )


def _nomes_conhecidos(condominio) -> set[str]:
    """As empresas que a portaria já viu, comparáveis sem depender da grafia."""
    return {nome.casefold() for nome in empresas_conhecidas(condominio)}


def _fornecedor_do_cadastro(nome: str, condominio):
    """O fornecedor do condomínio que responde por este nome — ou nenhum."""
    if condominio is None or not getattr(condominio, "pk", None):
        return None
    achados = [
        fornecedor
        for fornecedor in Fornecedor.objects.do_condominio(condominio).ativos()
        if _casa_com_a_fala(nome, fornecedor.nome_fantasia)
        or _casa_com_a_fala(nome, fornecedor.nome_razao_social)
    ]
    if len(achados) == 1:
        return achados[0]
    return None


def _servico_do_condominio(lido: str, condominio) -> Entidade:
    """O serviço do condomínio que a fala cita — equipamento, local, trabalho.

    É isto que dispensa perguntar unidade: quem vem olhar o elevador vem ao
    condomínio, e o elevador já diz onde. Só o cadastro confirma o serviço — a
    fala sozinha continua sendo declaração, e por isso ela não aparece aqui.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return Entidade()

    achados = [
        ("equipamento", equipamento.nome, equipamento)
        for equipamento in Equipamento.objects.do_condominio(condominio).ativos()
        if _casa_com_a_fala(lido, equipamento.nome)
    ]
    achados += [
        ("local", local.nome, local)
        for local in LocalOperacional.objects.do_condominio(condominio).ativos()
        if _casa_com_a_fala(lido, local.nome)
    ]
    achados += [
        ("manutenção", manutencao.titulo, manutencao)
        for manutencao in Manutencao.objects.do_condominio(condominio).abertas()
        if _casa_com_a_fala(lido, manutencao.titulo)
    ]
    achados += [
        ("demanda", demanda.titulo, demanda)
        for demanda in Demanda.objects.filter(condominio=condominio).abertas()
        if _casa_com_a_fala(lido, demanda.titulo)
    ]
    if not achados:
        return Entidade()

    tipo, nome, objeto = achados[0]
    if len({achado[1] for achado in achados}) > 1:
        return Entidade(
            campo="servico",
            valor=nome,
            rotulo=nome,
            confianca=Confianca.MEDIA,
            estado=Estado.AMBIGUA,
            motivo="Mais de um serviço do condomínio responde ao que a fala disse.",
        )
    return Entidade(
        campo="servico",
        valor=nome,
        rotulo=nome,
        objeto=objeto,
        confianca=Confianca.ALTA,
        estado=Estado.CONFIRMADA,
        motivo=f"Corresponde a {tipo} cadastrado neste condomínio.",
    )


def _casa_com_a_fala(lido: str, nome: str | None) -> bool:
    """Se o nome do cadastro aparece na fala, por palavra que identifica.

    "olhar o elevador" responde a "Elevador de serviço": o que casa é uma
    palavra do nome do cadastro, não o nome inteiro. Palavras curtas ficam de
    fora para que "de" e "do" não façam todo cadastro casar com toda fala. As
    genéricas também: "manutenção", "problema" e "técnico" dizem que há um
    serviço, mas não qual — casá-las faria toda demanda de manutenção responder
    a qualquer chegada. Sem uma palavra que identifique, não há correspondência.
    """
    if not nome:
        return False
    ditas = set(re.split(r"[^0-9a-z]+", lido))
    return any(
        palavra
        for palavra in re.split(r"[^0-9a-z]+", texto.normalizar(nome))
        if len(palavra) >= 4
        and palavra not in PALAVRAS_GENERICAS_DE_SERVICO
        and palavra in ditas
    )


def _solicitante_da_fala(lido: str, condominio) -> Entidade:
    """Quem a fala diz que chamou o prestador — sempre declarado.

    A síndica chamou: quem disse foi quem falou na portaria, e isso continua
    sendo declaração. O cadastro entra só para conferir que existe mesmo alguém
    neste papel no condomínio — o que não confirma que tenha sido essa pessoa
    quem chamou.
    """
    for palavra in PALAVRAS_DE_SOLICITANTE:
        if not texto.contem(lido, (palavra,)):
            continue
        papel, rotulo = PAPEIS_DO_SOLICITANTE[palavra]
        motivo = "A fala disse quem chamou o prestador."
        if _condominio_tem_papel(condominio, papel):
            motivo += " Há alguém neste papel no condomínio."
        return Entidade(
            campo="solicitante",
            valor=papel.value,
            rotulo=rotulo,
            confianca=Confianca.MEDIA,
            estado=Estado.DECLARADA,
            motivo=motivo,
        )
    return Entidade()


def _condominio_tem_papel(condominio, papel) -> bool:
    """Se o condomínio tem alguém ativo no papel — e não quem é essa pessoa."""
    if condominio is None or not getattr(condominio, "pk", None):
        return False
    return AcessoUsuarioCondominio.objects.filter(
        condominio=condominio, papel=papel, ativo=True
    ).exists()


def _esperado_agora(*, pessoa: Entidade, empresa: Entidade, unidade: Entidade, condominio):
    """A visita que a portaria já espera para agora, quando há exatamente uma.

    Uma autorização é uma espera com hora: se o nome, a empresa ou a unidade
    dela conferem com a fala, a chegada não é surpresa — é o cumprimento do
    que a portaria já tinha. Com mais de uma compatível, a leitura não escolhe:
    devolve as duas para que quem está na portaria diga.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return Entidade(), ()

    filtros = Q()
    if unidade.objeto is not None:
        filtros &= Q(unidade=unidade.objeto)
    if pessoa.existe:
        nome = pessoa.valor
        filtros &= (
            Q(nome__icontains=nome)
            | Q(pessoa__nome__icontains=nome)
            | Q(pessoa__nome_social__icontains=nome)
            | Q(empresa__icontains=nome)
        )
    if empresa.existe:
        nome = empresa.valor
        filtros &= Q(empresa__icontains=nome) | Q(nome__icontains=nome)
    if not filtros:
        return Entidade(), ()

    candidatas = list(
        AutorizacaoPortaria.objects.do_condominio(condominio)
        .esperadas_agora()
        .filter(filtros)
        .order_by("inicio_previsto")
    )
    if not candidatas:
        return Entidade(), ()
    if len(candidatas) == 1:
        esperada = candidatas[0]
        rotulo = _rotulo_da_autorizacao(esperada)
        return (
            Entidade(
                campo="esperado",
                valor=str(esperada.pk),
                rotulo=rotulo,
                objeto=esperada,
                confianca=Confianca.ALTA,
                estado=Estado.CONFIRMADA,
                motivo="A portaria já esperava esta visita para agora.",
            ),
            (),
        )
    return (
        Entidade(
            campo="esperado",
            valor=_rotulo_da_autorizacao(candidatas[0]),
            rotulo=_rotulo_da_autorizacao(candidatas[0]),
            confianca=Confianca.BAIXA,
            estado=Estado.AMBIGUA,
            motivo="Mais de uma visita esperada corresponde à fala.",
        ),
        tuple(candidatas),
    )


def _rotulo_da_autorizacao(autorizacao) -> str:
    return f"{autorizacao.nome_para_exibir} · {autorizacao.destino_para_exibir}"


def _completar_pelo_esperado(esperado, *, unidade, pessoa) -> tuple[Entidade, Entidade]:
    """O que a visita esperada já sabia e a fala não repetiu.

    Quem autorizou a visita já disse quem vem e para onde; quando a fala chega
    sem isso, é o esperado que responde — e responde com o cadastro, não com um
    palpite. Só o que faltava é preenchido: o que a fala disse continua sendo o
    que a fala disse.
    """
    autorizacao = esperado.objeto if esperado.confirmada else None
    if autorizacao is None:
        return unidade, pessoa
    if not unidade.existe and getattr(autorizacao, "unidade_id", None):
        unidade = _entidade_de_unidade(autorizacao.unidade)
    if not pessoa.existe and getattr(autorizacao, "pessoa_id", None):
        pessoa = _entidade_de_pessoa(autorizacao.pessoa)
    elif pessoa.existe and not pessoa.confirmada:
        # O primeiro nome basta para reconhecer quem a portaria já esperava:
        # "Mariana chegou" confirma a visita esperada de "Mariana Lopes".
        confirmada = _pessoa_do_esperado(autorizacao, pessoa.valor)
        if confirmada.existe:
            pessoa = confirmada
    return unidade, pessoa


def _pessoa_do_esperado(autorizacao, dita: str) -> Entidade:
    """Quem a visita esperada nomeia, quando o primeiro nome confere.

    Só o primeiro nome é comparado: é assim que a portaria fala. O esperado é
    uma visita de hoje, com hora — ele confirma a pessoa, e não o contrário.
    """
    if not dita:
        return Entidade()
    esperado_primeiro = _primeiro_nome(autorizacao.nome_para_exibir)
    if not esperado_primeiro or esperado_primeiro != _primeiro_nome(dita):
        return Entidade()
    if getattr(autorizacao, "pessoa_id", None):
        return _entidade_de_pessoa(autorizacao.pessoa)
    rotulo = autorizacao.nome_para_exibir
    return Entidade(
        campo="pessoa",
        valor=rotulo,
        rotulo=rotulo,
        confianca=Confianca.ALTA,
        estado=Estado.CONFIRMADA,
        motivo="Confere com a visita que a portaria já esperava.",
    )


def _primeiro_nome(nome: str) -> str:
    """O primeiro nome, normalizado — como a portaria chama alguém."""
    partes = texto.normalizar(nome or "").split()
    return partes[0] if partes else ""


def _contexto_da_chegada(
    *, unidade, esperado, servico, empresa, solicitante, tipo
) -> Entidade:
    """Onde esta chegada termina: na unidade, no condomínio, ou em lugar nenhum.

    A unidade decide quando a fala a disse — aí o destino é ela, com serviço ou
    sem ele. Sem unidade citada, o condomínio inteiro é o destino assim que
    houver uma espera, um serviço, uma empresa conhecida ou um chamado da
    gestão; e é isso, não um palpite, que dispensa a pergunta da unidade.
    """
    if unidade.existe:
        if tipo.valor == TipoAcessoPortaria.VISITANTE.value:
            return _contexto(
                ContextoAcessoPortaria.VISITANTE_DE_UNIDADE,
                "A fala citou a unidade para onde a visita vai.",
            )
        return _contexto(
            ContextoAcessoPortaria.PRESTADOR_DE_UNIDADE,
            "A fala citou a unidade onde o serviço será feito.",
        )
    if esperado.existe:
        return _contexto_do_esperado(esperado)
    if servico.existe or solicitante.existe:
        return _contexto(
            ContextoAcessoPortaria.PRESTADOR_DO_CONDOMINIO,
            "O serviço é do condomínio, e não de uma unidade.",
        )
    if empresa.existe:
        if empresa.objeto is not None:
            return _contexto(
                ContextoAcessoPortaria.FORNECEDOR_DO_CONDOMINIO,
                "A empresa citada é fornecedora deste condomínio.",
            )
        return _contexto(
            ContextoAcessoPortaria.PRESTADOR_DO_CONDOMINIO,
            "A fala trouxe uma empresa, e não uma unidade.",
        )
    if tipo.valor in TIPOS_DE_SERVICO:
        return _contexto(
            ContextoAcessoPortaria.PRESTADOR_DO_CONDOMINIO,
            "Quem chegou vem prestar serviço; ainda não se sabe para onde.",
        )
    return _contexto(
        ContextoAcessoPortaria.OUTRO,
        "A fala não disse para onde esta chegada vai.",
    )


def _contexto_do_esperado(esperado: Entidade) -> Entidade:
    """O destino do que era esperado — é ele que a portaria já tinha anotado."""
    autorizada = esperado.objeto
    tipo = getattr(autorizada, "tipo_acesso", "")
    unidade = getattr(autorizada, "unidade_id", None)
    if unidade:
        membro = (
            ContextoAcessoPortaria.VISITANTE_DE_UNIDADE
            if tipo == TipoAcessoPortaria.VISITANTE.value
            else ContextoAcessoPortaria.PRESTADOR_DE_UNIDADE
        )
        return _contexto(membro, "A visita esperada era para uma unidade.")
    if tipo == TipoAcessoPortaria.FORNECEDOR.value:
        return _contexto(
            ContextoAcessoPortaria.FORNECEDOR_DO_CONDOMINIO,
            "A visita esperada era de um fornecedor do condomínio.",
        )
    return _contexto(
        ContextoAcessoPortaria.PRESTADOR_DO_CONDOMINIO,
        "A visita esperada era para o condomínio, e não para uma unidade.",
    )


def _contexto(membro, motivo: str) -> Entidade:
    """O contexto como entidade — sempre leitura, nunca cadastro."""
    return Entidade(
        campo="contexto",
        valor=membro.value,
        rotulo=membro.label,
        confianca=Confianca.ALTA,
        estado=Estado.INFERIDA,
        motivo=motivo,
    )


def _vai_para_o_condominio(contexto: Entidade) -> bool:
    """Se o destino é o condomínio inteiro — e não uma unidade."""
    return contexto.valor in (
        ContextoAcessoPortaria.PRESTADOR_DO_CONDOMINIO.value,
        ContextoAcessoPortaria.FORNECEDOR_DO_CONDOMINIO.value,
    )


# ---------------------------------------------------------------------------
# Leitura de cada intenção
# ---------------------------------------------------------------------------


def _ler_chegada(lido, original, condominio, secundarias) -> Intencao:
    entidades: dict[str, Entidade] = {}
    faltantes: list[str] = []

    pessoa = _pessoa_da_fala(lido, original, condominio, PALAVRAS_DE_CHEGADA)
    empresa = _empresa_do_acesso(original, pessoa, condominio)
    if empresa.confirmada:
        # O nome que a fala deu era da empresa, e não de uma pessoa do cadastro.
        pessoa = Entidade()

    unidade, ambiguas = _unidade_da_fala(lido, condominio)
    servico = _servico_do_condominio(lido, condominio)
    solicitante = _solicitante_da_fala(lido, condominio)
    tipo = _tipo_de_acesso(lido, empresa, servico)
    entrega = tipo.valor in TIPOS_DE_ENTREGA
    if entrega:
        # Quem entrega não tem nome de pessoa: o nome é a transportadora, se
        # dita. Sem pessoa, a entrega continua sendo uma leitura de chegada.
        pessoa = Entidade()
        if not empresa.existe and tipo.valor == TipoAcessoPortaria.CORREIOS.value:
            # "Sedex" é Correios: a transportadora é o nome de quem chega.
            empresa = Entidade(
                campo="empresa",
                valor=TRANSPORTADORA_CORREIOS,
                rotulo=TRANSPORTADORA_CORREIOS,
                confianca=Confianca.MEDIA,
                estado=Estado.DECLARADA,
                motivo="A fala indicou encomenda dos Correios.",
            )
    esperado, _ = _esperado_agora(
        pessoa=pessoa, empresa=empresa, unidade=unidade, condominio=condominio
    )
    # O que a portaria já esperava completa o que a fala não repetiu: quem
    # autorizou a visita já disse quem vem e para onde.
    unidade, pessoa = _completar_pelo_esperado(
        esperado, unidade=unidade, pessoa=pessoa
    )

    contexto = _contexto_da_chegada(
        unidade=unidade,
        esperado=esperado,
        servico=servico,
        empresa=empresa,
        solicitante=solicitante,
        tipo=tipo,
    )

    if not entrega and not pessoa.existe and not empresa.existe:
        faltantes.append("pessoa")
    if not unidade.existe and not _vai_para_o_condominio(contexto):
        faltantes.append("unidade")
    if _vai_para_o_condominio(contexto) and not servico.existe and not empresa.existe:
        faltantes.append("servico")

    if pessoa.existe:
        entidades["pessoa"] = pessoa
    if empresa.existe:
        entidades["empresa"] = empresa
    if unidade.existe:
        entidades["unidade"] = unidade
    if servico.existe:
        entidades["servico"] = servico
    if solicitante.existe:
        entidades["solicitante"] = solicitante
    if esperado.existe:
        entidades["esperado"] = esperado
    entidades["contexto"] = contexto
    entidades["tipo_acesso"] = tipo

    autorizador = _pessoa_da_fala(lido, original, condominio, PALAVRAS_DE_AUTORIZACAO)
    if autorizador.existe:
        entidades["autorizador"] = autorizador

    meio = _meio_da_fala(lido)
    if meio.existe:
        entidades["meio"] = meio

    status = _status_da_fala(lido)
    if status.existe:
        entidades["status"] = status

    acoes = [ACAO_DE_CHEGADA]
    if IntencaoPortaria.RELATO_DE_PROBLEMA in secundarias:
        acoes.append(ACAO_DE_OCORRENCIA)

    return Intencao(
        tipo=IntencaoPortaria.CHEGADA,
        confianca=_confianca_do_critico(
            entidades,
            faltantes,
            _criticos_da_chegada(contexto, pessoa, empresa, servico, solicitante, tipo),
        ),
        resumo=_resumo_da_chegada(entidades),
        entidades=entidades,
        secundarias=secundarias,
        faltantes=tuple(faltantes),
        perguntas=_perguntas_da_chegada(
            ambiguas=ambiguas,
            faltantes=faltantes,
            servico=servico,
            contexto=contexto,
        ),
        acoes=tuple(acoes),
    )


def _criticos_da_chegada(contexto, pessoa, empresa, servico, solicitante, tipo) -> tuple:
    """O que não pode ficar em dúvida nesta chegada (§22).

    São duas coisas: para onde a chegada vai — a unidade, e só quando a fala
    apontou uma — e quem a portaria consegue nomear. Quando o destino é o
    condomínio inteiro não há unidade a exigir: o registro se sustenta na
    empresa, em quem chegou, no serviço ou em quem chamou, e exigir unidade aí
    seria inventar um caminho que a fala não deu.

    Na entrega, quem nomeia é a transportadora, se dita: quem entrega não tem
    nome de pessoa, e cobrar um seria cobrar o que a fala nunca teria.

    Quem nomeia vem em ordem de firmeza: a empresa tem cadastro, a pessoa
    também, e o serviço e o chamado dizem menos de quem chegou do que os dois
    primeiros.
    """
    onde = () if _vai_para_o_condominio(contexto) else ("unidade",)
    if tipo.valor in TIPOS_DE_ENTREGA:
        return onde + (("empresa",) if empresa.existe else ())
    if empresa.existe:
        quem = ("empresa",)
    elif pessoa.existe:
        quem = ("pessoa",)
    elif servico.existe:
        quem = ("servico",)
    elif solicitante.existe:
        quem = ("solicitante",)
    else:
        quem = ("pessoa",)
    return onde + quem


def _confianca_do_critico(entidades, faltantes, criticos) -> str:
    """A firmeza da leitura a partir do que não pode ficar em dúvida (§22).

    Unidade, pessoa e autorizador sustentam o registro: faltar um deles ou
    tê-lo ambíguo dá no mesmo — a leitura não decide sozinha e vira pergunta.
    Só quando todos vêm confirmados pelo cadastro a leitura é alta; o que a
    fala apenas declarou fica no meio.
    """
    for campo in criticos:
        if campo in faltantes:
            return Confianca.BAIXA
        if entidades.get(campo, Entidade()).confianca == Confianca.BAIXA:
            return Confianca.BAIXA
    if all(entidades.get(campo, Entidade()).confirmada for campo in criticos):
        return Confianca.ALTA
    return Confianca.MEDIA


def _perguntas_da_chegada(
    *, ambiguas, faltantes, servico, contexto
) -> tuple[str, ...]:
    """No máximo uma pergunta — e a que mais muda o registro.

    Desfazer a ambiguidade vem primeiro: se o número dito responde a duas
    unidades, nenhuma outra resposta muda o registro antes disso.

    Depois o destino decide. Quem vem ao condomínio inteiro não precisa de
    unidade — precisava de serviço, e é ele que a portaria anota; só se pergunta
    unidade quando a fala apontou para uma e ela não veio.

    E por último quem chegou: sem nome nenhum, saber quem é resolve mais do que
    saber para onde vai.
    """
    if len(ambiguas) > 1:
        rotulos = " ou ".join(unidade_candidata.rotulo for unidade_candidata in ambiguas)
        return (f"Qual unidade: {rotulos}?",)
    if _vai_para_o_condominio(contexto):
        if "servico" in faltantes or servico.estado == Estado.AMBIGUA:
            return ("Ele veio para qual serviço?",)
        return ("Quem chegou?",) if "pessoa" in faltantes else ()
    if "pessoa" in faltantes:
        return ("Quem chegou?",)
    if "unidade" in faltantes:
        return ("Para qual unidade?",)
    return ()


def _resumo_da_chegada(entidades) -> str:
    """O resumo curto que a portaria confirma em uma linha (§21)."""
    pedacos: list[str] = []

    pessoa = entidades.get("pessoa")
    empresa = entidades.get("empresa")
    unidade = entidades.get("unidade")
    servico = entidades.get("servico")
    solicitante = entidades.get("solicitante")
    tipo = entidades.get("tipo_acesso")
    contexto = entidades.get("contexto")
    autorizador = entidades.get("autorizador")
    meio = entidades.get("meio")
    status = entidades.get("status")

    if pessoa and pessoa.existe:
        pedacos.append(pessoa.texto)
    if empresa and empresa.existe:
        pedacos.append(empresa.texto)

    if tipo and tipo.existe and unidade and unidade.existe:
        pedacos.append(f"{tipo.texto.lower()} da unidade {unidade.texto}")
    elif tipo and tipo.existe and contexto and _vai_para_o_condominio(contexto):
        pedacos.append(f"{tipo.texto.lower()} do condomínio")
    elif unidade and unidade.existe:
        pedacos.append(f"unidade {unidade.texto}")

    if servico and servico.existe:
        pedacos.append(f"serviço {servico.texto}")
    if solicitante and solicitante.existe:
        pedacos.append(f"chamado por {solicitante.texto}")

    if autorizador and autorizador.existe:
        frase = f"autorizado por {autorizador.texto}"
        if meio and meio.existe:
            frase += f" {_frase_do_meio(meio)}"
        pedacos.append(frase)
    elif meio and meio.existe:
        pedacos.append(_frase_do_meio(meio))

    if status and status.existe and status.valor != StatusAutorizacaoPortaria.AUTORIZADO:
        pedacos.append(status.texto.lower())

    return ", ".join(pedacos)


def _frase_do_meio(meio: Entidade) -> str:
    return FRASE_DO_MEIO.get(meio.valor) or f"pelo {meio.texto.lower()}"


def _ler_saida(lido, original, condominio, secundarias) -> Intencao:
    entidades: dict[str, Entidade] = {}
    faltantes: list[str] = []

    pessoa = _pessoa_da_fala(lido, original, condominio, PALAVRAS_DE_SAIDA)
    if pessoa.existe:
        entidades["pessoa"] = pessoa

    unidade, ambiguas = _unidade_da_fala(lido, condominio)
    if unidade.existe:
        entidades["unidade"] = unidade

    candidatos = _presencas_compativeis(
        pessoa=pessoa, unidade=unidade, condominio=condominio
    )
    if len(candidatos) == 1:
        acesso = candidatos[0]
        entidades["acesso"] = Entidade(
            campo="acesso",
            valor=str(acesso.pk),
            rotulo=_rotulo_do_acesso(acesso),
            objeto=acesso,
            confianca=Confianca.ALTA,
            estado=Estado.CONFIRMADA,
            motivo="Há uma presença aberta que corresponde a esta fala.",
        )
        acoes = (ACAO_DE_SAIDA,)
    else:
        # Encerrar a presença errada tira alguém de dentro do condomínio no
        # papel. Sem uma correspondência única, a leitura não escolhe.
        faltantes.append("acesso")
        acoes = ()

    return Intencao(
        tipo=IntencaoPortaria.SAIDA,
        confianca=Confianca.ALTA if len(candidatos) == 1 else Confianca.BAIXA,
        resumo=_resumo_da_saida(entidades),
        entidades=entidades,
        secundarias=secundarias,
        faltantes=tuple(faltantes),
        perguntas=_perguntas_da_saida(pessoa, unidade, ambiguas, candidatos),
        acoes=acoes,
    )


def _perguntas_da_saida(pessoa, unidade, ambiguas, candidatos) -> tuple[str, ...]:
    if len(candidatos) == 1:
        # Uma presença só corresponde à fala: não há o que perguntar.
        return ()
    if len(ambiguas) > 1:
        rotulos = " ou ".join(
            unidade_candidata.rotulo for unidade_candidata in ambiguas
        )
        return (f"Qual unidade: {rotulos}?",)
    if len(candidatos) > 1:
        rotulos = " ou ".join(_rotulo_do_acesso(acesso) for acesso in candidatos)
        return (f"Quem saiu: {rotulos}?",)
    if not pessoa.existe and not unidade.existe:
        return ("Quem saiu?",)
    return ("Não encontrei ninguém com essa descrição na portaria. Quem saiu?",)


def _resumo_da_saida(entidades) -> str:
    pessoa = entidades.get("pessoa")
    unidade = entidades.get("unidade")
    if pessoa and pessoa.existe and unidade and unidade.existe:
        return f"{pessoa.texto}, da unidade {unidade.texto}, saiu"
    if pessoa and pessoa.existe:
        return f"{pessoa.texto} saiu"
    if unidade and unidade.existe:
        return f"Saída da unidade {unidade.texto}"
    acesso = entidades.get("acesso")
    if acesso and acesso.existe:
        return f"{acesso.rotulo} saiu"
    return ""


def _rotulo_do_acesso(acesso) -> str:
    return f"{acesso.nome_para_exibir} · {acesso.destino_para_exibir}"


def _ler_autorizacao(lido, original, condominio, secundarias) -> Intencao:
    entidades: dict[str, Entidade] = {}
    faltantes: list[str] = []

    autorizador = _pessoa_da_fala(lido, original, condominio, PALAVRAS_DE_AUTORIZACAO)
    if autorizador.existe:
        entidades["autorizador"] = autorizador
    else:
        faltantes.append("autorizador")

    unidade, ambiguas = _unidade_da_fala(lido, condominio)
    if unidade.existe:
        entidades["unidade"] = unidade
    else:
        faltantes.append("unidade")

    status = _status_da_fala(lido)
    if status.existe:
        entidades["status"] = status
    meio = _meio_da_fala(lido)
    if meio.existe:
        entidades["meio"] = meio

    return Intencao(
        tipo=IntencaoPortaria.AUTORIZACAO,
        confianca=_confianca_do_critico(
            entidades, faltantes, ("unidade", "autorizador")
        ),
        resumo=_resumo_da_autorizacao(entidades),
        entidades=entidades,
        secundarias=secundarias,
        faltantes=tuple(faltantes),
        perguntas=_perguntas_da_autorizacao(autorizador, unidade, ambiguas),
        acoes=(ACAO_DE_AUTORIZACAO,),
    )


def _perguntas_da_autorizacao(autorizador, unidade, ambiguas) -> tuple[str, ...]:
    if len(ambiguas) > 1:
        rotulos = " ou ".join(
            unidade_candidata.rotulo for unidade_candidata in ambiguas
        )
        return (f"Qual unidade: {rotulos}?",)
    if not autorizador.existe:
        return ("Quem autorizou?",)
    if not unidade.existe:
        return ("Para qual unidade?",)
    return ()


def _resumo_da_autorizacao(entidades) -> str:
    pedacos: list[str] = []
    autorizador = entidades.get("autorizador")
    unidade = entidades.get("unidade")
    status = entidades.get("status")
    meio = entidades.get("meio")
    if unidade and unidade.existe:
        pedacos.append(f"unidade {unidade.texto}")
    if status and status.existe:
        pedacos.append(status.texto.lower())
    if autorizador and autorizador.existe:
        frase = f"por {autorizador.texto}"
        if meio and meio.existe:
            frase += f" {_frase_do_meio(meio)}"
        pedacos.append(frase)
    return ", ".join(pedacos)


def _ler_problema(secundarias) -> Intencao:
    """Um problema relatado não é operação da portaria: é uma demanda.

    A leitura para na intenção. Onde está o problema, que equipamento é e com
    que urgência é assunto de ``apps/demandas``, que já sabe ler isso — repetir
    aqui seria manter duas respostas para a mesma pergunta.
    """
    return Intencao(
        tipo=IntencaoPortaria.RELATO_DE_PROBLEMA,
        confianca=Confianca.MEDIA,
        secundarias=secundarias,
        acoes=(ACAO_DE_OCORRENCIA,),
    )


# ---------------------------------------------------------------------------
# Nome declarado
# ---------------------------------------------------------------------------


# Até onde vai um nome de pessoa: "Ana Paula Souza" tem três palavras, e uma
# quarta já é frase. Passado o limite, o que vem não é mais o nome de quem
# chegou.
PALAVRAS_DO_NOME = 3


def _nome_em_volta_do_verbo(original: str, verbos) -> str:
    """O nome que a fala coloca em volta do verbo que o anuncia — ou junto dele.

    "Chegou Carlos" traz o nome depois; "Carlos do 902 saiu" traz antes. As
    duas formas são a mesma coisa dita em ordens diferentes, e por isso as duas
    são procuradas — com o "antes" primeiro, que é onde o português costuma
    pôr o sujeito. Quando a fala não tem verbo ("visita pro 501, Carlos"), o
    nome ainda está junto da palavra que anuncia a chegada, e é ali que a
    leitura procura.
    """
    palavras = _palavras_do_verbo(verbos)
    nome = _nome_antes_do_verbo(original, palavras) or _nome_depois_do_verbo(
        original, palavras
    )
    return nome or _nome_junto_do_tipo(original)


def _palavras_do_verbo(verbos) -> frozenset:
    """As palavras dos verbos, soltas: "está aqui" conta como "está" e "aqui"."""
    palavras = set()
    for verbo in verbos:
        for palavra in str(verbo).split():
            palavras.add(texto.normalizar(palavra))
    return frozenset(palavras)


def _nome_depois_do_verbo(original: str, verbos) -> str:
    """O nome que vem depois do verbo, até três palavras seguidas.

    "Chegou Carlos" nomeia logo depois do verbo; "chegou Ana Paula Souza" nomeia
    inteiro. A leitura avança enquanto a fala não chegou a um limite ("para",
    "que", "com") nem à ligação de uma empresa, e para no que já não é nome.
    """
    tokens = _tokens(original)
    for indice, token in enumerate(tokens):
        if texto.normalizar(token) not in verbos:
            continue
        return _nome_a_frente(tokens, indice + 1)
    return ""


def _nome_antes_do_verbo(original: str, verbos) -> str:
    """O nome antes do verbo, até três palavras, na ordem em que se escreve."""
    tokens = _tokens(original)
    for indice, token in enumerate(tokens):
        if texto.normalizar(token) not in verbos:
            continue
        colhidas: list[str] = []
        for anterior in reversed(tokens[:indice]):
            if _serve_como_nome(anterior):
                colhidas.append(anterior)
                if len(colhidas) == PALAVRAS_DO_NOME:
                    break
            elif colhidas:
                break
        return " ".join(reversed(colhidas))
    return ""


def _nome_junto_do_tipo(original: str) -> str:
    """O nome que a fala dá depois de anunciar quem chega, sem verbo.

    "visita pro 501, Carlos" diz o tipo primeiro e o nome no fim. Depois da
    palavra que anuncia a chegada — visita, visitante, convidado — a leitura
    segue até o primeiro nome; depois do artigo ("o Carlos"), o nome tem de vir
    colado. Em ambos os casos a inicial maiúscula é o sinal de que ali começa
    um nome próprio: "o elevador" não nomeia ninguém.
    """
    tokens = _tokens(original)
    for indice, token in enumerate(tokens):
        normalizado = texto.normalizar(token)
        if normalizado in PALAVRAS_DE_VISITANTE:
            nome = _primeiro_nome_adiante(tokens, indice + 1)
            if nome:
                return nome
        elif normalizado in ("o", "a"):
            nome = _nome_imediatamente(tokens, indice + 1)
            if nome:
                return nome
    return ""


def _primeiro_nome_adiante(tokens: list[str], inicio: int) -> str:
    """O primeiro nome próprio adiante, saltando o que não é nome."""
    for indice in range(inicio, len(tokens)):
        if _serve_como_nome_proprio(tokens[indice]):
            return _nome_a_frente(tokens, indice)
    return ""


def _nome_imediatamente(tokens: list[str], inicio: int) -> str:
    """O nome próprio que começa exatamente ali, sem saltar nada."""
    if inicio < len(tokens) and _serve_como_nome_proprio(tokens[inicio]):
        return _nome_a_frente(tokens, inicio)
    return ""


def _serve_como_nome_proprio(token: str) -> bool:
    """Um nome de pessoa de verdade — com inicial maiúscula."""
    return token[:1].isupper() and _serve_como_nome(token)


def _nome_a_frente(tokens: list[str], inicio: int) -> str:
    """O nome a partir de um ponto, até três palavras seguidas.

    Salta o que não é nome antes de começar ("chegou o Carlos"), mas para no
    limite da frase e na ligação de uma empresa — ali não vem mais um nome.
    """
    colhidas: list[str] = []
    for seguinte in tokens[inicio:]:
        normalizado = texto.normalizar(seguinte)
        if _serve_como_nome(seguinte):
            colhidas.append(seguinte)
            if len(colhidas) == PALAVRAS_DO_NOME:
                break
        elif colhidas:
            break
        elif normalizado in LIMITES_DO_NOME or normalizado in CONECTORES_DE_EMPRESA:
            break
    return " ".join(colhidas)


def _tokens(original: str) -> list[str]:
    return PADRAO_DE_TOKEN.findall(original or "")


def _serve_como_nome(token: str) -> bool:
    normalizado = texto.normalizar(token)
    if not normalizado or normalizado.isdigit():
        return False
    return normalizado not in PALAVRAS_QUE_NAO_SAO_NOME
