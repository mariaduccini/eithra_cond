"""Leitura do relato que registra a demanda.

Quem registra escreve o que aconteceu; o sistema lê o que está escrito e propõe
só o que consegue sustentar — assunto, prioridade, prazo e local —, sempre com o
motivo ao lado. O que não tem base não é preenchido: continua sendo pergunta.

São regras locais, determinísticas e legíveis, escritas com as palavras que a
operação de fato usa. Nada aqui chama serviço de fora nem completa lacuna por
conta própria: melhor não sugerir do que sugerir errado.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from unicodedata import combining, normalize

from apps.cadastros.models import LocalOperacional

from .constants import PrioridadeDemanda

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


@dataclass(frozen=True)
class Leitura:
    """O que deu para ler do relato — e, no motivo, por quê."""

    assunto: str = ""
    prioridade: str = PrioridadeDemanda.NORMAL
    motivo: str = ""
    prazo: date | None = None
    local: LocalOperacional | None = None

    @property
    def leu_alguma_coisa(self) -> bool:
        """Se a leitura sustenta alguma proposta, ou só devolveu o padrão."""
        return bool(self.assunto or self.motivo or self.local)


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


def _equipamento_mencionado(texto_normalizado: str) -> str:
    """O rótulo do equipamento essencial citado no relato, se algum foi."""
    for rotulo, variantes in EQUIPAMENTOS_ESSENCIAIS:
        if _contem(texto_normalizado, variantes):
            return rotulo
    return ""


def assunto_do_relato(texto: str | None, *, limite: int = LIMITE_DO_ASSUNTO) -> str:
    """A primeira ideia do relato, em uma linha.

    Quem escreve começa pelo que aconteceu, então a primeira frase já é o
    assunto. Se ela não couber, o corte é na última palavra inteira.

    O limite muda com a superfície que mostra o assunto: a linha da lista
    comporta mais do que o cabeçalho do detalhe. O corte é sempre o mesmo —
    na palavra inteira — e nunca toca no que está guardado.
    """
    linha = " ".join((texto or "").replace("\n", " ").split())
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

    Só nome inteiro conta. Entre dois cadastros que casam — "Garagem" e
    "Garagem do bloco B" —, vence o mais específico, que é o mais longo.
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
        (local, nome)
        for local, nome in nomeados
        if len(nome) >= MINIMO_DO_NOME_DE_LOCAL and _contem(lido, (nome,))
    ]
    if not candidatos:
        return None
    candidatos.sort(key=lambda par: len(par[1]), reverse=True)
    return candidatos[0][0]


def interpretar(relato: str | None, *, condominio=None, hoje=None) -> Leitura:
    """Lê o relato e devolve o que ele sustenta.

    Nesta fase, o relato não cria prazo automático. Sem data expressa ou outra
    base objetiva já aprovada no domínio, preencher prazo seria transformar
    heurística em regra de negócio.
    """
    texto = (relato or "").strip()
    if not texto:
        return Leitura()

    prioridade, motivo = prioridade_do_relato(texto)
    return Leitura(
        assunto=assunto_do_relato(texto),
        prioridade=prioridade,
        motivo=motivo,
        local=local_do_relato(texto, condominio),
    )
