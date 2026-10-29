"""Leitura do relato que registra a demanda.

Quem registra escreve o que aconteceu; o sistema lê o que está escrito e propõe
só o que consegue sustentar — assunto, prioridade, prazo, local, equipamento,
tipo de problema, impacto, risco e recorrência —, sempre com a origem, o motivo
e o quanto aquilo é firme ao lado. O que não tem base não é preenchido:
continua sendo pergunta, e uma pergunta de cada vez.

São regras locais, determinísticas e legíveis, escritas com as palavras que a
operação de fato usa. Nada aqui chama serviço de fora nem completa lacuna por
conta própria: melhor não sugerir do que sugerir errado.

Três fontes entram nesta leitura, e cada uma responde por uma parte diferente:

* **o relato** diz o que aconteceu, com as palavras de quem viu;
* **o cadastro do condomínio** confirma a que local e a que equipamento aquilo
  pertence — e é ele que decide quando o nome citado não é ambíguo;
* **o histórico** mostra o que já aconteceu naquele ponto antes.

A confiança sai daí, e é determinística: não é probabilidade, é o quanto a
evidência sustenta. Ela decide se a tela preenche direto ou se pergunta, e nunca
aparece como número para quem usa o sistema.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from unicodedata import combining, normalize

from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.cadastros.constants import TipoLocal
from apps.cadastros.models import Equipamento, LocalOperacional

from . import prazos
from .constants import (
    ImpactoDemanda,
    PrioridadeDemanda,
    RecorrenciaDemanda,
    RiscoDemanda,
    TipoProblema,
)

# O assunto cabe em uma linha de lista, com folga para não virar parágrafo.
LIMITE_DO_ASSUNTO = 120

# O cabeçalho do detalhe é lido de perto: o assunto ocupa duas linhas no
# celular e uma só no desktop.
#
# Este limite é de apresentação, não de domínio. Cortar um texto aqui não
# produz um assunto: quando o corte aparece, o que está na tela é o começo de
# um relato, não um assunto lido dele. O assunto de verdade é o que a pessoa
# escreveu no campo de assunto; a descrição original é o relato inteiro; e o
# corte é o terceiro caso — registro antigo que guardou o relato no lugar do
# assunto. Dívida controlada: demandas antigas podem possuir relato completo
# salvo no campo de assunto. Nada disso é reescrito automaticamente; a limpeza
# será uma rodada assistida (descrição original → sugestão de assunto curto →
# revisão humana → atualização).
LIMITE_DO_ASSUNTO_NO_CABECALHO = 64

# Nome de lugar curto demais casaria por acidente dentro de qualquer frase.
# Vale para a busca por dentro do texto; um nome curto que o cadastro confirma
# continua sendo reconhecido, mas por palavra inteira (ver ``_menciona``).
MINIMO_DO_NOME_DE_LOCAL = 3

# O que pode machucar alguém ou comprometer o prédio: não espera.
PALAVRAS_DE_RISCO = (
    "risco",
    "perigo",
    "incendio",
    "fumaca",
    "choque",
    "curto-circuito",
    "curto circuito",
    "explosao",
    "desabamento",
    "arrombamento",
    "invasao",
    "vazamento de gas",
    "cheiro de gas",
)

# O problema que volta: quem já tentou resolver sabe que o caso é outro.
PALAVRAS_DE_RECORRENCIA = (
    "novamente",
    "de novo",
    "outra vez",
    "mais uma vez",
    "nova falha",
    "novo problema",
    "recorrente",
    "reincidente",
    "voltou a",
    "mesmo problema",
)

# O que está fora de operação, dito como a portaria e a administração dizem.
PALAVRAS_DE_FALHA = (
    "falha",
    "falhou",
    "defeito",
    "quebrou",
    "quebrado",
    "parou",
    "parado",
    "travou",
    "travado",
    "nao funciona",
    "nao esta funcionando",
    "sem funcionar",
    "inoperante",
    "danificado",
    "estragou",
    "vazando",
    "vazamento",
    "entupido",
    "entupiu",
    "nao abre",
    "nao fecha",
    "nao sobe",
    "nao desce",
    "sem agua",
    "sem energia",
    "fora do ar",
    "queimado",
    "avaria",
    "problema",
)

# Equipamentos em que a falha deixa de ser incômodo e passa a ser o prédio
# parado. O rótulo é o que aparece no motivo; as variantes, o que se escreve.
EQUIPAMENTOS_ESSENCIAIS = (
    ("portão", ("portao", "portoes")),
    ("elevador", ("elevador", "elevadores")),
    ("bomba", ("bomba", "bombas")),
    ("interfone", ("interfone", "interfones")),
    ("catraca", ("catraca", "catracas")),
    ("gerador", ("gerador", "geradores")),
    ("cerca elétrica", ("cerca eletrica",)),
    ("câmera", ("camera", "cameras")),
    ("CFTV", ("cftv",)),
    ("pressurizador", ("pressurizador",)),
    ("guarita", ("guarita",)),
    ("poço", ("poco", "pocos")),
)

# O que a própria pessoa já disse que pode esperar.
PALAVRAS_SEM_PRESSA = (
    "quando possivel",
    "sem urgencia",
    "sem pressa",
    "pode aguardar",
    "nao e urgente",
    "nao urgente",
    "aos poucos",
)

# Até onde o histórico olha para trás quando procura ocorrência parecida. Um
# problema de manutenção que volta depois de um ano é outra história — e tratá-lo
# como o mesmo seria afirmar mais do que os registros sustentam.
DIAS_DE_HISTORICO = 180


class Confianca(models.TextChoices):
    """O quanto a evidência sustenta uma classificação.

    É determinística, não estatística: vem de o cadastro ter casado um nome só,
    de o relato ter dito a palavra ou de o histórico ter mostrado ocorrência
    anterior. Não é probabilidade e não vira número na tela — serve para o
    sistema decidir entre preencher, sugerir e perguntar.
    """

    ALTA = "alta", "Alta"
    MEDIA = "media", "Média"
    BAIXA = "baixa", "Baixa"


class OrigemSugestao(models.TextChoices):
    """De onde cada informação veio, para que a tela possa dizer.

    Não é rótulo decorativo: é a resposta para "de onde o sistema tirou isso?".
    A tela mostra só o que interessa no momento, mas a origem existe sempre.
    """

    RELATO = "relato", "Identificado no relato"
    EITHRA = "eithra", "Identificado pelo EITHRA"
    LOCAL = "local", "Derivado do Local"
    EQUIPAMENTO = "equipamento", "Derivado do equipamento"
    HISTORICO = "historico", "Encontrado no histórico"
    GESTAO = "gestao", "Informado pela gestão"


@dataclass(frozen=True)
class Sugestao:
    """Uma classificação proposta, com a origem e o porquê.

    ``confianca`` alta preenche direto; média aparece como sugestão a confirmar;
    baixa não escolhe nada. ``motivo`` é a frase humana que explica a proposta —
    e é ela que a tela mostra, nunca a confiança.
    """

    valor: str = ""
    origem: str = OrigemSugestao.EITHRA
    confianca: str = Confianca.BAIXA
    motivo: str = ""

    @property
    def existe(self) -> bool:
        return bool(self.valor)


# O tipo de problema em duas passadas. Primeiro o que o relato diz com todas as
# letras; depois a cena que ele descreve, quando o nome técnico não aparece.
#
# A ordem decide o empate, e por isso vai do mais concreto para o mais genérico:
# "vazamento" ganha de "cano", que poderia estar em qualquer frase.
TIPOS_DE_PROBLEMA_DECLARADOS = (
    (TipoProblema.VAZAMENTO, "Vazamento declarado no relato", (
        "vazamento", "vazando", "vazou", "pingando", "gotejando", "transbordando",
    )),
    (TipoProblema.INFILTRACAO, "Infiltração declarada no relato", (
        "infiltracao", "infiltrado", "infiltrando", "umidade", "mofo", "bolor",
        "parede molhada", "tinta descascando",
    )),
    (TipoProblema.ESTRUTURA, "Sinal em estrutura ou alvenaria", (
        "rachadura", "trinca", "fissura", "reboco", "desabamento", "laje",
        "pilar", "viga", "telhado", "telha",
    )),
    (TipoProblema.ELETRICA, "Sinal de falha elétrica", (
        "sem energia", "falta de energia", "curto-circuito", "curto circuito",
        "curto", "disjuntor", "fiacao", "tomada", "quadro de energia",
        "queimado", "energia",
    )),
    (TipoProblema.ILUMINACAO, "Sinal em iluminação", (
        "lampada", "luminaria", "refletor", "poste", "iluminacao",
        "luz queimada",
    )),
    (TipoProblema.HIDRAULICA, "Sinal de falha hidráulica", (
        "sem agua", "falta de agua", "entupido", "entupiu", "esgoto",
        "encanamento", "cano", "torneira", "descarga", "registro", "pressao",
        "bomba",
    )),
    (TipoProblema.ACESSO, "Sinal de falha de acesso ou automação", (
        "portao", "cancela", "interfone", "fechadura", "catraca", "biometria",
        "automatizacao", "elevador", "porta",
    )),
    (TipoProblema.LIMPEZA, "Sinal em limpeza ou conservação", (
        "sujeira", "lixo", "entulho", "limpeza", "mau cheiro", "cheiro ruim",
        "caixa de gordura", "pragas", "ratos", "baratas", "dedetizacao",
    )),
    (TipoProblema.RUIDO, "Sinal de ruído", (
        "barulho", "ruido", "estrondo", "vibracao", "rangendo", "ranger",
    )),
)

# A cena descrita, para quando a palavra exata não veio. Duas listas: a primeira
# precisa aparecer, a segunda confirma o contexto. Exige as duas juntas porque
# "parede" sozinha não é infiltração, e "água" sozinha não é nada.
CENAS_DE_PROBLEMA = (
    (
        TipoProblema.INFILTRACAO,
        "Água em parede ou teto indica infiltração",
        ("agua",),
        ("parede", "teto", "forro", "laje", "piso"),
    ),
    (
        TipoProblema.ACESSO,
        "O relato descreve algo que não responde ao comando",
        ("nao responde", "nao funciona", "nao obedece"),
        ("controle", "botao", "interfone", "fechadura", "portao", "cancela"),
    ),
)

# O risco, na ordem em que ele pesa. O que pode matar vem antes do que pode
# incomodar: fumaça ganha de barulho, sempre.
RISCOS_DECLARADOS = (
    (RiscoDemanda.INCENDIO, "Risco de incêndio ou explosão", (
        "incendio", "fumaca", "explosao", "fogo", "vazamento de gas",
        "cheiro de gas",
    )),
    (RiscoDemanda.ELETRICO, "Risco elétrico", (
        "choque", "curto-circuito", "curto circuito", "curto",
        "fiacao exposta", "energia exposta",
    )),
    (RiscoDemanda.ESTRUTURAL, "Risco estrutural", (
        "desabamento", "desabou", "risco de queda", "estrutura comprometida",
        "laje comprometida",
    )),
    (RiscoDemanda.SEGURANCA, "Risco à segurança", (
        "risco", "perigo", "arrombamento", "invasao", "assalto",
    )),
    (RiscoDemanda.SAUDE, "Risco à saúde", (
        "esgoto", "contaminacao", "agua contaminada", "pragas", "ratos",
        "baratas", "falta de agua",
    )),
)

# O risco que ninguém escreveu, mas a cena deixa entrever. É hipótese, e não
# declaração — por isso vai como sugestão a confirmar, nunca como fato.
CENAS_DE_RISCO = (
    (
        RiscoDemanda.SEGURANCA,
        "Acesso liberado ou fora de comando deixa o condomínio exposto",
        ("ficou aberto", "deixou aberto", "portao aberto", "cancela aberta"),
        (),
    ),
)

# Quando o próprio relato afirma que não há risco. É uma resposta declarada, e
# não a ausência de resposta: só entra aqui quando a pessoa disse isso.
PALAVRAS_SEM_RISCO = (
    "sem risco",
    "nenhum risco",
    "nao ha risco",
    "sem perigo",
    "nao oferece risco",
)

# Até onde a consequência alcança, quando o relato diz.
PALAVRAS_DE_IMPACTO_COLETIVO = (
    "todo o predio", "todos os moradores", "predio inteiro", "condominio inteiro",
    "bloco inteiro", "torre inteira", "para todos", "toda a garagem",
    "todo o condominio", "coletivo", "geral",
)
PALAVRAS_DE_IMPACTO_NA_UNIDADE = (
    "meu apartamento", "minha unidade", "meu banheiro", "minha cozinha",
    "minha sala", "meu quarto", "minha varanda", "dentro do apartamento",
)

# O que faz entender que o relato está falando de um equipamento sem nomeá-lo,
# ou nomeando um que não está no cadastro. Sem esse sinal, o sistema não sai
# perguntando por equipamento em relato que não menciona nenhum.
SINAIS_DE_EQUIPAMENTO = (
    "equipamento",
    "maquina",
    "motor",
    "aparelho",
    "quadro",
    "controlador",
)

# A saudação abre o relato e não é o fato: quem cumprimenta antes de contar o
# que aconteceu não está falando do problema. Estas palavras, quando abrem o
# relato, saem da frente antes de o assunto ser lido — nada mais é reescrito.
SAUDACOES = (
    "ola",
    "oi",
    "bom dia",
    "boa tarde",
    "boa noite",
    "boa madrugada",
    "tudo bem",
    "tudo bom",
    "por favor",
    "por gentileza",
    "obrigado",
    "obrigada",
)


@dataclass(frozen=True)
class Leitura:
    """O que deu para ler do relato — e, em cada proposta, por quê.

    Além do que a lista já usava, guarda a classificação que o relato e o
    cadastro sustentam: equipamento, área, tipo de problema, impacto, risco e
    recorrência. Cada uma vem como ``Sugestao``, com origem e confiança, para
    que a tela saiba o que preencher, o que apenas sugerir e o que perguntar.
    """

    assunto: str = ""
    prioridade: str = PrioridadeDemanda.NORMAL
    motivo: str = ""
    prazo: date | None = None
    # Por que o prazo é o que é. Vazio quando não há política configurada — e a
    # data só existe quando existe regra; a leitura do relato nunca a inventa.
    motivo_do_prazo: str = ""
    local: LocalOperacional | None = None
    # De onde o local veio. Quase sempre é o próprio relato, que nomeia o
    # lugar; quando a leitura semântica é quem o reconhece, a origem muda para
    # que a rastreabilidade continue dizendo a verdade.
    origem_do_local: str = OrigemSugestao.RELATO
    equipamento: Equipamento | None = None
    area: Sugestao = field(default_factory=Sugestao)
    tipo_problema: Sugestao = field(default_factory=Sugestao)
    impacto: Sugestao = field(default_factory=Sugestao)
    risco: Sugestao = field(default_factory=Sugestao)
    recorrencia: Sugestao = field(default_factory=Sugestao)
    # A pergunta que o relato deixou em aberto. No máximo uma, e só quando
    # responder aquilo muda a classificação.
    pergunta: str = ""

    @property
    def leu_alguma_coisa(self) -> bool:
        """Se a leitura sustenta alguma proposta, ou só devolveu o padrão."""
        if self.assunto or self.motivo or self.local or self.equipamento:
            return True
        return any(
            sugestao.existe
            for sugestao in (
                self.area,
                self.tipo_problema,
                self.impacto,
                self.risco,
                self.recorrencia,
            )
        )

    @property
    def sugestoes(self) -> dict[str, Sugestao]:
        """A classificação proposta, campo a campo, com origem e confiança.

        É esta a memória que o registro guarda: o valor sugerido, a origem e a
        confiança de cada campo. Comparar isso com o que foi confirmado é o que
        permitirá o EITHRA aprender — por ora, só se preserva.
        """
        return {
            "assunto": Sugestao(
                valor=self.assunto,
                origem=OrigemSugestao.RELATO,
                confianca=Confianca.ALTA,
                motivo="É o começo do próprio relato." if self.assunto else "",
            ),
            "local": Sugestao(
                valor=self.local.nome if self.local else "",
                origem=self.origem_do_local,
                confianca=Confianca.ALTA if self.local else Confianca.BAIXA,
                motivo="Confere com um local cadastrado neste condomínio."
                if self.local
                else "",
            ),
            "prioridade": Sugestao(
                valor=self.prioridade if self.motivo else "",
                origem=OrigemSugestao.EITHRA,
                confianca=Confianca.ALTA if self.motivo else Confianca.BAIXA,
                motivo=self.motivo,
            ),
            "equipamento": Sugestao(
                valor=self.equipamento.nome if self.equipamento else "",
                origem=OrigemSugestao.EITHRA,
                confianca=Confianca.ALTA if self.equipamento else Confianca.BAIXA,
                motivo="Confere com o equipamento cadastrado neste condomínio."
                if self.equipamento
                else "",
            ),
            "area": self.area,
            "tipo_problema": self.tipo_problema,
            "impacto": self.impacto,
            "risco": self.risco,
            "recorrencia": self.recorrencia,
        }

    def registro(self) -> dict:
        """O que a leitura propôs, em forma de registro, para guardar a memória.

        Só o que a leitura sustentou entra: campo sem proposta não vira linha
        vazia. Cada linha preserva valor, origem e confiança.
        """
        return {
            campo: {
                "valor": str(sugestao.valor),
                "origem": str(sugestao.origem),
                "confianca": str(sugestao.confianca),
            }
            for campo, sugestao in self.sugestoes.items()
            if sugestao.existe
        }


def _normalizado(texto: str | None) -> str:
    """O texto em minúsculas, sem acento e sem espaço sobrando."""
    if not texto:
        return ""
    sem_acento = "".join(
        letra for letra in normalize("NFD", texto) if not combining(letra)
    )
    return " ".join(sem_acento.lower().split())


def _contem(texto_normalizado: str, palavras) -> bool:
    """Se alguma das palavras aparece inteira no texto.

    Inteira, e não como pedaço: "sala" não pode casar dentro de "salão".
    """
    return any(
        re.search(rf"\b{re.escape(palavra)}s?\b", texto_normalizado)
        for palavra in palavras
    )


def _palavras(texto_normalizado: str) -> list[str]:
    """As palavras do texto, sem a pontuação que veio colada nelas.

    A comparação de um nome curto é por palavra inteira, então "no G2." precisa
    valer como "g2": a pontuação do entorno sai antes de comparar, e o que fica
    é a unidade lexical dita na frase.
    """
    return [re.sub(r"^\W+|\W+$", "", palavra) for palavra in texto_normalizado.split()]


def _menciona(texto_normalizado: str, nome: str) -> bool:
    """Se o nome do lugar aparece no relato como unidade lexical.

    Nome com tamanho de sobra é procurado inteiro no texto — a busca de sempre.
    Nome curto — "G2", "P1", "12" — é curto demais para essa busca, porque a
    sequência de caracteres casaria dentro de "G20", "AG2" e "G2X". Ele só
    entra quando o cadastro o confirma, e entra por palavra inteira: existe de
    fato como lugar do condomínio, então pode ser dito no relato; e é palavra
    inteira, então não casa por dentro de outra. Nada é escolhido por
    semelhança frouxa — quem autoriza o nome curto é o cadastro.
    """
    if len(nome) >= MINIMO_DO_NOME_DE_LOCAL:
        return _contem(texto_normalizado, (nome,))
    return nome in _palavras(texto_normalizado)


def _equipamento_mencionado(texto_normalizado: str) -> str:
    """O rótulo do equipamento essencial citado no relato, se algum foi."""
    for rotulo, variantes in EQUIPAMENTOS_ESSENCIAIS:
        if _contem(texto_normalizado, variantes):
            return rotulo
    return ""


def _sem_saudacao(linha: str) -> str:
    """O relato sem a saudação da frente, para que o assunto seja o fato.

    Só a abertura sai, e só quando ela é exatamente um cumprimento: "boa tarde
    para todos" continua sendo o relato, porque não é saudação. O que sobra é
    devolvido como estava — a limpeza é de abertura, não uma reescrita.

    Quando só havia cumprimento, o resultado é vazio: não há assunto, e a
    etapa da descrição continua aberta esperando o fato.
    """
    restante = linha
    while True:
        partes = re.split(r"\s*[,;:.!?]\s*", restante, maxsplit=1)
        if _normalizado(partes[0]) not in SAUDACOES:
            return restante
        if len(partes) < 2:
            return ""
        restante = partes[1].strip()


def assunto_do_relato(texto: str | None, *, limite: int = LIMITE_DO_ASSUNTO) -> str:
    """A primeira ideia do relato, em uma linha.

    Quem escreve começa pelo que aconteceu — e a saudação, quando abre o
    relato, não conta: o assunto é o fato, não o cumprimento. Se a frase não
    couber, o corte é na última palavra inteira.

    O limite muda com a superfície que mostra o assunto: a linha da lista
    comporta mais do que o cabeçalho do detalhe. O corte é sempre o mesmo —
    na palavra inteira — e nunca toca no que está guardado.
    """
    linha = " ".join((texto or "").replace("\n", " ").split())
    if not linha:
        return ""
    linha = _sem_saudacao(linha)
    if not linha:
        return ""
    for corte in (". ", "! ", "? ", ";"):
        if corte in linha:
            linha = linha.split(corte)[0]
            break
    linha = linha.strip().strip(".,;:!? ")
    if len(linha) <= limite:
        return linha
    return linha[:limite].rsplit(" ", 1)[0].rstrip(" ,;:")


def sustenta_registro(relato: str | None) -> bool:
    """Se o relato já sustenta seguir para as informações.

    É a mesma régua do registro: sem assunto não há demanda. Serve à fala, que
    precisa saber se pode levar a tela adiante sem mais um clique — e a
    resposta é do domínio, não do navegador.
    """
    return bool(assunto_do_relato(relato))


def prioridade_do_relato(texto: str | None) -> tuple[str, str]:
    """A prioridade que o relato sustenta, com o motivo em uma frase.

    Devolve ``Normal`` sem motivo quando não há regra que sustente outra coisa:
    o padrão da demanda é a resposta honesta para o que não se sabe ler.
    """
    lido = _normalizado(texto)
    if not lido:
        return PrioridadeDemanda.NORMAL, ""

    if _contem(lido, PALAVRAS_DE_RISCO):
        return PrioridadeDemanda.URGENTE, "Risco à segurança"

    equipamento = _equipamento_mencionado(lido)
    if equipamento and _contem(lido, PALAVRAS_DE_RECORRENCIA):
        return PrioridadeDemanda.ALTA, f"Falha recorrente em {equipamento}"
    if _contem(lido, PALAVRAS_DE_RECORRENCIA):
        return PrioridadeDemanda.ALTA, "O problema voltou a acontecer"
    if equipamento and _contem(lido, PALAVRAS_DE_FALHA):
        return PrioridadeDemanda.ALTA, f"{equipamento} fora de operação"

    if _contem(lido, PALAVRAS_SEM_PRESSA):
        return PrioridadeDemanda.BAIXA, "Sem urgência declarada"

    return PrioridadeDemanda.NORMAL, ""


def local_do_relato(texto: str | None, condominio) -> LocalOperacional | None:
    """O lugar cadastrado que o relato cita, quando cita algum.

    Só nome inteiro conta, e só cadastro ativo deste condomínio. Entre dois
    cadastros que casam — "Garagem" e "Garagem do bloco B" —, vence o mais
    específico, que é o mais longo. Nome curto, como "G2", também é
    reconhecido desde que exista no cadastro: ele não é procurado por dentro
    do texto, e sim como palavra inteira do relato (ver ``_menciona``).

    Empatados na especificidade, não há escolha: dois lugares igualmente
    prováveis seriam uma aposta, e o silêncio é a resposta honesta.
    """
    lido = _normalizado(texto)
    if not lido or condominio is None or not getattr(condominio, "pk", None):
        return None

    locais = LocalOperacional.objects.do_condominio(condominio).ativos()
    nomeados = [
        (local, _normalizado(local.nome))
        for local in locais
        if _normalizado(local.nome)
    ]
    for local, nome in nomeados:
        if nome == lido:
            return local

    candidatos = [
        (local, nome) for local, nome in nomeados if _menciona(lido, nome)
    ]
    if not candidatos:
        return None
    candidatos.sort(key=lambda par: len(par[1]), reverse=True)
    if len(candidatos) > 1 and len(candidatos[0][1]) == len(candidatos[1][1]):
        return None
    return candidatos[0][0]


def _cena_casa(lido: str, exige, confirma) -> bool:
    """Se a cena descrita aparece: o que é obrigatório e o que só confirma."""
    if not _contem(lido, exige):
        return False
    return not confirma or _contem(lido, confirma)


def _tipo_do_local(local) -> str:
    """Como o condomínio classifica este lugar, quando ele está classificado."""
    if local is None or not getattr(local, "pk", None):
        return ""
    tipo_local = getattr(local, "tipo_local", None)
    if tipo_local is not None:
        return tipo_local.nome
    if local.tipo and local.tipo != TipoLocal.OUTRO:
        return local.get_tipo_display()
    return ""


def _nomeia_outro_lugar(nome: str, local) -> bool:
    """Se o rótulo do tipo é, na verdade, o nome de outro lugar do condomínio.

    O tipo do lugar existe para agrupar lugares parecidos — "Área comum",
    "Fachada", "Garagem". Quando o rótulo coincide com o nome de um lugar
    cadastrado, ele não agrupa nada: nomeia um lugar do mesmo nível. Aí dizer
    "o local G2 pertence à área G1" seria contradizer o cadastro, que já disse
    onde G2 fica. Nada é reclassificado nem inventado: a área simplesmente não
    se sustenta, e o silêncio é a resposta.
    """
    condominio = getattr(local, "condominio", None)
    if condominio is None or not getattr(condominio, "pk", None):
        return False
    alvo = _normalizado(nome)
    if not alvo:
        return False

    outros = LocalOperacional.objects.do_condominio(condominio).ativos()
    if getattr(local, "pk", None):
        outros = outros.exclude(pk=local.pk)
    return any(_normalizado(item.nome) == alvo for item in outros)


def _area_do_local(local) -> str:
    """O rótulo do tipo do lugar, quando ele de fato agrupa o lugar.

    A área é derivada do relacionamento real do cadastro e de mais nada: a
    informação explícita e a inferência semântica não entram aqui, porque uma
    área não pode contrariar o lugar que o cadastro já confirmou.
    """
    nome = _tipo_do_local(local)
    if not nome or _nomeia_outro_lugar(nome, local):
        return ""
    return nome


def _equipamento_confirmado(equipamento) -> Sugestao:
    """A proposta para um equipamento que o próprio cadastro confirmou."""
    return Sugestao(
        valor=equipamento.nome,
        origem=OrigemSugestao.EITHRA,
        confianca=Confianca.ALTA,
        motivo="Confere com o equipamento cadastrado neste condomínio.",
    )


def equipamento_do_relato(
    texto: str | None, condominio
) -> tuple[Equipamento | None, Sugestao]:
    """O equipamento cadastrado que o relato aponta, e o que sustenta a escolha.

    Casar o nome citado com o cadastro é o que decide a confiança: um nome só
    responde; dois nomes, ou nenhum, viram pergunta. Nada é escolhido por
    semelhança frouxa — a escolha é sempre entre cadastros reais do condomínio.
    """
    lido = _normalizado(texto)
    if not lido or condominio is None or not getattr(condominio, "pk", None):
        return None, Sugestao()

    cadastrados = [
        (equipamento, _normalizado(equipamento.nome))
        for equipamento in Equipamento.objects.do_condominio(condominio).ativos()
        if _normalizado(equipamento.nome)
    ]
    for equipamento, nome in cadastrados:
        if nome == lido:
            return equipamento, _equipamento_confirmado(equipamento)

    candidatos = [
        (equipamento, nome)
        for equipamento, nome in cadastrados
        if len(nome) >= MINIMO_DO_NOME_DE_LOCAL and _contem(lido, (nome,))
    ]
    if len(candidatos) == 1:
        return candidatos[0][0], _equipamento_confirmado(candidatos[0][0])
    if len(candidatos) > 1:
        return None, Sugestao(
            origem=OrigemSugestao.EITHRA,
            confianca=Confianca.BAIXA,
            motivo="Há mais de um equipamento com esse nome no condomínio.",
        )

    # O relato citou o equipamento pelo tipo, sem dizer qual deles é.
    rotulo = _equipamento_mencionado(lido)
    if not rotulo:
        return None, Sugestao()

    # O rótulo é o nome que a tela mostra, com acento; a comparação é entre
    # textos já normalizados, senão "portão" nunca casaria com "portao social".
    procurado = _normalizado(rotulo)
    por_tipo = [
        equipamento
        for equipamento, _ in cadastrados
        if procurado in _normalizado(f"{equipamento.nome} {equipamento.tipo_texto}")
    ]
    if len(por_tipo) == 1:
        return por_tipo[0], _equipamento_confirmado(por_tipo[0])
    return None, Sugestao(
        origem=OrigemSugestao.EITHRA,
        confianca=Confianca.BAIXA,
        motivo=f"O relato cita {rotulo} pelo tipo, sem dizer qual é.",
    )


def area_do_contexto(local, equipamento) -> Sugestao:
    """A área a que o fato pertence, derivada do cadastro.

    Vem do tipo do local quando ele está classificado; e, quando só o
    equipamento se sabe, do local onde ele fica. É derivada, não declarada —
    por isso a origem é o cadastro, e não o relato. Quando o vínculo do
    cadastro não sustenta uma área — porque o rótulo nomeia outro lugar —,
    não há área a mostrar: a área nunca contradiz o lugar confirmado.
    """
    nome = _area_do_local(local)
    if nome:
        return Sugestao(
            valor=nome,
            origem=OrigemSugestao.LOCAL,
            confianca=Confianca.ALTA,
            motivo=f"O local faz parte de {nome} no cadastro do condomínio.",
        )

    if equipamento is not None and getattr(equipamento, "local_id", None):
        nome = _area_do_local(equipamento.local)
        if nome:
            return Sugestao(
                valor=nome,
                origem=OrigemSugestao.EQUIPAMENTO,
                confianca=Confianca.MEDIA,
                motivo=f"O equipamento fica em {equipamento.local.nome}.",
            )
    return Sugestao()


def tipo_de_problema_do_relato(texto: str | None) -> Sugestao:
    """A natureza do problema, primeiro pelo nome e depois pela cena.

    A palavra técnica, quando vem, vale como declaração. Quando não vem, a
    cena descrita pode dizer a mesma coisa — água na parede é infiltração mesmo
    sem a palavra —, mas aí já é interpretação, e vai como sugestão a
    confirmar.
    """
    lido = _normalizado(texto)
    if not lido:
        return Sugestao()

    for tipo, motivo, palavras in TIPOS_DE_PROBLEMA_DECLARADOS:
        if _contem(lido, palavras):
            return Sugestao(
                valor=tipo,
                origem=OrigemSugestao.RELATO,
                confianca=Confianca.MEDIA,
                motivo=motivo,
            )

    for tipo, motivo, exige, confirma in CENAS_DE_PROBLEMA:
        if _cena_casa(lido, exige, confirma):
            return Sugestao(
                valor=tipo,
                origem=OrigemSugestao.EITHRA,
                confianca=Confianca.MEDIA,
                motivo=motivo,
            )
    return Sugestao()


def impacto_do_contexto(texto: str | None, local, equipamento) -> Sugestao:
    """Até onde o fato alcança, quando o relato ou o cadastro já dizem.

    Declarado ganha de derivado: se a pessoa escreveu que atingiu o prédio
    inteiro, é isso. Sem declaração, o contexto responde — um equipamento ou
    um local são um alcance, e não o condomínio todo.
    """
    lido = _normalizado(texto)
    if _contem(lido, PALAVRAS_DE_IMPACTO_COLETIVO):
        return Sugestao(
            valor=ImpactoDemanda.COLETIVO,
            origem=OrigemSugestao.RELATO,
            confianca=Confianca.ALTA,
            motivo="O relato diz que alcança o condomínio inteiro.",
        )
    if _contem(lido, PALAVRAS_DE_IMPACTO_NA_UNIDADE):
        return Sugestao(
            valor=ImpactoDemanda.UNIDADE,
            origem=OrigemSugestao.RELATO,
            confianca=Confianca.ALTA,
            motivo="O relato diz que é dentro de uma unidade.",
        )
    if equipamento is not None and getattr(equipamento, "pk", None):
        return Sugestao(
            valor=ImpactoDemanda.EQUIPAMENTO,
            origem=OrigemSugestao.EQUIPAMENTO,
            confianca=Confianca.MEDIA,
            motivo=f"O relato aponta um equipamento: {equipamento.nome}.",
        )
    if local is not None and getattr(local, "pk", None):
        return Sugestao(
            valor=ImpactoDemanda.AREA,
            origem=OrigemSugestao.LOCAL,
            confianca=Confianca.MEDIA,
            motivo=f"O relato aponta uma área: {local.nome}.",
        )
    return Sugestao()


def risco_do_relato(texto: str | None) -> Sugestao:
    """O risco que o relato declara — e o que só a cena deixa entrever.

    O que a pessoa escreveu vale como declaração. O que se conclui da cena é
    hipótese, e vai como sugestão a confirmar. Sem nada disso, o risco fica
    sem classificação: ``None`` não é o mesmo que "sem risco".
    """
    lido = _normalizado(texto)
    if not lido:
        return Sugestao()

    if _contem(lido, PALAVRAS_SEM_RISCO):
        return Sugestao(
            valor=RiscoDemanda.SEM_RISCO,
            origem=OrigemSugestao.RELATO,
            confianca=Confianca.ALTA,
            motivo="O relato informa que não há risco.",
        )

    for risco, motivo, palavras in RISCOS_DECLARADOS:
        if _contem(lido, palavras):
            return Sugestao(
                valor=risco,
                origem=OrigemSugestao.RELATO,
                confianca=Confianca.ALTA,
                motivo=motivo,
            )

    for risco, motivo, exige, confirma in CENAS_DE_RISCO:
        if _cena_casa(lido, exige, confirma):
            return Sugestao(
                valor=risco,
                origem=OrigemSugestao.EITHRA,
                confianca=Confianca.MEDIA,
                motivo=motivo,
            )
    return Sugestao()


def _data_do_registro(momento) -> str:
    """A data de um registro, no fuso de quem lê."""
    if timezone.is_aware(momento):
        momento = timezone.localtime(momento)
    return momento.strftime("%d/%m/%Y")


def _inicio_da_janela(referencia):
    """O começo da janela do histórico, como data e hora consciente do fuso."""
    if isinstance(referencia, datetime):
        momento = referencia
        if timezone.is_naive(momento):
            momento = timezone.make_aware(momento)
    else:
        momento = timezone.make_aware(datetime.combine(referencia, time.min))
    return momento - timedelta(days=DIAS_DE_HISTORICO)


def _ocorrencia_anterior(
    condominio, *, local=None, equipamento=None, hoje=None, ignorar=None
):
    """A demanda mais recente no mesmo ponto, dentro da janela do histórico.

    O ponto é o equipamento quando ele se sabe; senão, o local. Sem nenhum dos
    dois não há o que comparar — e o silêncio é a resposta.
    """
    from .models import Demanda

    if condominio is None or not getattr(condominio, "pk", None):
        return None

    if equipamento is not None and getattr(equipamento, "pk", None):
        ponto = Q(equipamento_id=equipamento.pk)
    elif local is not None and getattr(local, "pk", None):
        ponto = Q(local_id=local.pk)
    else:
        return None

    consulta = Demanda.objects.filter(
        ponto,
        condominio_id=condominio.pk,
        criado_em__gte=_inicio_da_janela(hoje or timezone.localdate()),
    )
    # A demanda que está sendo revista não é histórico dela mesma.
    if ignorar is not None and getattr(ignorar, "pk", None):
        consulta = consulta.exclude(pk=ignorar.pk)
    return consulta.order_by("-criado_em").first()


def recorrencia_do_relato(
    texto: str | None, condominio, *, local=None, equipamento=None, hoje=None, ignorar=None
) -> Sugestao:
    """Se isto já aconteceu antes — pelo que o relato diz e pelo que os registros mostram.

    São duas coisas diferentes e continuam separadas: a declaração de quem
    relata não é o mesmo que o histórico sustentar ocorrência anterior.
    """
    if _contem(_normalizado(texto), PALAVRAS_DE_RECORRENCIA):
        return Sugestao(
            valor=RecorrenciaDemanda.DECLARADA,
            origem=OrigemSugestao.RELATO,
            confianca=Confianca.ALTA,
            motivo="O relato indica que o problema já aconteceu antes.",
        )

    anterior = _ocorrencia_anterior(
        condominio, local=local, equipamento=equipamento, hoje=hoje, ignorar=ignorar
    )
    if anterior is None:
        return Sugestao()
    return Sugestao(
        valor=RecorrenciaDemanda.OBSERVADA,
        origem=OrigemSugestao.HISTORICO,
        confianca=Confianca.MEDIA,
        motivo=f"Já houve registro parecido em {_data_do_registro(anterior.criado_em)}.",
    )


def _tem_mais_de_um_equipamento(condominio) -> bool:
    """Se há mais de um equipamento cadastrado — quando o nome dele importa."""
    if condominio is None or not getattr(condominio, "pk", None):
        return False
    return Equipamento.objects.do_condominio(condominio).ativos().count() > 1


def pergunta_do_relato(
    texto: str | None, condominio, *, equipamento=None, tipo_problema: str = ""
) -> str:
    """A única pergunta que vale a pena fazer agora, quando houver uma.

    Pergunta-se só o que o relato deixou em aberto e o cadastro não resolveu.
    Quando nada está ambíguo, o silêncio é a resposta: não se pergunta para
    preencher campo.
    """
    if equipamento is not None:
        return ""

    lido = _normalizado(texto)
    rotulo = _equipamento_mencionado(lido)
    if rotulo:
        return f"Qual {rotulo} apresentou o problema?"
    if _contem(lido, SINAIS_DE_EQUIPAMENTO):
        return "De qual equipamento se trata?"
    if tipo_problema == TipoProblema.RUIDO and _tem_mais_de_um_equipamento(condominio):
        return "Qual equipamento está fazendo o barulho?"
    return ""


def interpretar(
    relato: str | None, *, condominio=None, hoje=None, ignorar=None
) -> Leitura:
    """Lê o relato e devolve o que ele sustenta.

    O prazo não sai do relato: ele sai da política configurada, aplicada ao
    peso que o relato sustenta (ver ``.prazos``). Sem regra configurada, não há
    data — o relato nunca inventa uma. O que a pessoa informar na tela vale
    mais do que a derivação, e é o formulário que faz essa parte valer.

    ``ignorar`` é a demanda que já existe e está sendo revista: ela não conta
    como histórico dela mesma.
    """
    texto = (relato or "").strip()
    if not texto:
        return Leitura()

    prioridade, motivo = prioridade_do_relato(texto)
    local = local_do_relato(texto, condominio)
    equipamento, _ = equipamento_do_relato(texto, condominio)
    tipo_problema = tipo_de_problema_do_relato(texto)
    return Leitura(
        assunto=assunto_do_relato(texto),
        prioridade=prioridade,
        motivo=motivo,
        prazo=prazos.prazo_do_registro(prioridade, base=hoje),
        motivo_do_prazo=prazos.motivo_do_prazo(prioridade),
        local=local,
        equipamento=equipamento,
        area=area_do_contexto(local, equipamento),
        tipo_problema=tipo_problema,
        impacto=impacto_do_contexto(texto, local, equipamento),
        risco=risco_do_relato(texto),
        recorrencia=recorrencia_do_relato(
            texto,
            condominio,
            local=local,
            equipamento=equipamento,
            hoje=hoje,
            ignorar=ignorar,
        ),
        pergunta=pergunta_do_relato(
            texto,
            condominio,
            equipamento=equipamento,
            tipo_problema=tipo_problema.valor,
        ),
    )
