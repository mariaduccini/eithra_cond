"""O que exige decisão de quem responde pelo condomínio — derivado, nunca guardado.

Quem responde pelo condomínio não deveria varrer o sistema para descobrir onde é
preciso decidir. Este módulo lê o estado real das demandas em aberto e deriva
dele, de forma determinística, a exceção que pede uma decisão.

Nada aqui é gravado. Não existe tabela de aprovação, de escalonamento nem de
pendência: a exceção é sempre calculada a partir do que já está persistido —
prioridade, risco, prazo, situação, o que se espera e o próximo passo. Guardar o
que pode ser derivado criaria uma segunda fonte de verdade, e a segunda fonte é
sempre a que mente primeiro.

Uma demanda pode caber em mais de uma exceção — está fora do prazo e tem risco
alto. O módulo não produz avisos redundantes: escolhe UMA exceção principal pela
precedência de ``PRECEDENCIA``. A pessoa lê um motivo, não quatro.

O que o módulo não inventa: hoje não existe conceito de aprovação formal, de
decisão financeira nem de escalonamento. "Aguardando aprovação" é lido do
próprio domínio (``AguardandoTipo.APROVACAO``) — a demanda diz que espera
aprovação, e isso basta para a exceção. A reincidência observada não entra: o
campo que a guardava foi removido, e reconstruí-la a partir do histórico seria
outra conta, não esta leitura. Nada além do que existe é suposto.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db import models
from django.utils import timezone

from .constants import (
    AguardandoTipo,
    PrioridadeDemanda,
    RiscoDemanda,
    StatusDemanda,
)
from .models import Demanda
from .permissoes import condominios_para_ver

# Riscos que fazem a demanda pedir decisão. ``SEM_RISCO`` é resposta declarada —
# "não há risco" — e por isso fica de fora; só o risco identificado pesa.
RISCOS_RELEVANTES = frozenset(
    {
        RiscoDemanda.SEGURANCA,
        RiscoDemanda.INCENDIO,
        RiscoDemanda.ELETRICO,
        RiscoDemanda.ESTRUTURAL,
        RiscoDemanda.SAUDE,
    }
)

# Situações em que "sem próxima ação" é uma lacuna, e não o começo normal de uma
# demanda recém-aberta: quem já está conduzindo ou esperando deveria saber o
# próximo passo. Uma demanda apenas aberta ainda pode estar sendo triada.
SITUACOES_DE_TRABALHO = frozenset({StatusDemanda.EM_ANDAMENTO, StatusDemanda.AGUARDANDO})


class TipoExcecao(models.TextChoices):
    """As poucas exceções que pedem a decisão de quem responde pelo condomínio.

    Cada palavra responde a uma pergunta diferente: o que é urgente, o que traz
    risco, o que espera aprovação, o que passou do prazo e o que ficou sem
    próximo passo. Uma palavra nova só entra quando descreve uma decisão humana
    diferente das que já existem.
    """

    URGENTE = "urgente", "Urgente"
    RISCO_ALTO = "risco_alto", "Risco identificado"
    AGUARDANDO_APROVACAO = "aguardando_aprovacao", "Aguardando aprovação"
    VENCIDA = "vencida", "Fora do prazo"
    SEM_PROXIMA_ACAO = "sem_proxima_acao", "Sem próxima ação"


# A ordem em que a exceção é lida: o que exige decisão mais imediata primeiro.
# É a autoridade única da precedência — nenhuma tela repete esta ordem.
PRECEDENCIA = {
    TipoExcecao.URGENTE: 0,
    TipoExcecao.RISCO_ALTO: 1,
    TipoExcecao.AGUARDANDO_APROVACAO: 2,
    TipoExcecao.VENCIDA: 3,
    TipoExcecao.SEM_PROXIMA_ACAO: 4,
}

# A ação que a exceção sugere, em uma linha. É conceito, não instrução técnica:
# quem apresenta decide como e onde mostrar. Nada aqui promete um botão que o
# domínio ainda não oferece — hoje só existe "ver o contexto".
_ACAO_SUGERIDA = {
    TipoExcecao.URGENTE: "Ver o contexto e decidir",
    TipoExcecao.RISCO_ALTO: "Ver o contexto e decidir",
    TipoExcecao.AGUARDANDO_APROVACAO: "Decidir a aprovação",
    TipoExcecao.VENCIDA: "Cobrar quem responde",
    TipoExcecao.SEM_PROXIMA_ACAO: "Definir a próxima ação",
}

# O tom com que a exceção pesa na tela. Sempre acompanhado do texto: o estado
# nunca depende só da cor.
_TOM = {
    TipoExcecao.URGENTE: "danger",
    TipoExcecao.RISCO_ALTO: "danger",
    TipoExcecao.AGUARDANDO_APROVACAO: "warning",
    TipoExcecao.VENCIDA: "warning",
    TipoExcecao.SEM_PROXIMA_ACAO: "info",
}

# Sentinela para ordenar o que não tem atraso: fica depois dentro do seu tipo.
_SEM_ATRASO = 0


@dataclass(frozen=True)
class ExcecaoDemanda:
    """A leitura derivada de uma demanda — fato e códigos, sem texto guardado.

    Guarda a demanda, o tipo de exceção, a data de referência e quantos dias a
    demanda está fora do prazo. Título, ação sugerida e tom são montados a partir
    daí, na hora de apresentar: o texto de interface não vira dado.
    """

    demanda: Demanda
    tipo: TipoExcecao
    data_referencia: date
    # Dias além do prazo. Zero quando a exceção não é de atraso.
    dias_de_atraso: int = 0

    @property
    def ordem(self) -> int:
        """A severidade da decisão: quanto menor, mais à frente na leitura."""
        return PRECEDENCIA[self.tipo]

    @property
    def titulo(self) -> str:
        """O nome curto do motivo da exceção."""
        return TipoExcecao(self.tipo).label

    @property
    def acao_sugerida(self) -> str:
        """O próximo passo conceitual que a exceção sugere."""
        return _ACAO_SUGERIDA[self.tipo]

    @property
    def tom(self) -> str:
        """O peso da exceção na tela."""
        return _TOM[self.tipo]


def _dias_de_atraso(prazo, hoje) -> int:
    """Quantos dias a demanda está fora do prazo. Zero quando não está."""
    if prazo is None or prazo >= hoje:
        return 0
    return (hoje - prazo).days


def _excecao_da_demanda(demanda, *, hoje) -> ExcecaoDemanda | None:
    """Deriva a exceção de uma demanda, ou ``None`` quando não há nenhuma.

    A decisão segue a precedência, e a demanda gera no máximo uma exceção: o
    motivo principal é o que pesa mais, e os demais fatos continuam onde sempre
    estiveram — na própria demanda e no histórico dela.
    """

    def excecao(tipo, *, atraso=0):
        return ExcecaoDemanda(
            demanda=demanda,
            tipo=tipo,
            data_referencia=hoje,
            dias_de_atraso=atraso,
        )

    if demanda.prioridade == PrioridadeDemanda.URGENTE:
        return excecao(TipoExcecao.URGENTE)

    if demanda.risco in RISCOS_RELEVANTES:
        return excecao(TipoExcecao.RISCO_ALTO)

    if demanda.aguardando_tipo == AguardandoTipo.APROVACAO:
        return excecao(TipoExcecao.AGUARDANDO_APROVACAO)

    if demanda.prazo is not None and demanda.prazo < hoje:
        return excecao(
            TipoExcecao.VENCIDA, atraso=_dias_de_atraso(demanda.prazo, hoje)
        )

    if demanda.status in SITUACOES_DE_TRABALHO and not demanda.proxima_acao:
        return excecao(TipoExcecao.SEM_PROXIMA_ACAO)

    return None


def _ids_de_condominios(condominios) -> list:
    """Os identificadores dos condomínios, venha lista ou queryset."""
    if condominios is None:
        return []
    return [getattr(item, "pk", item) for item in condominios]


def _escopo(condominios, usuario):
    """O recorte de condomínios que esta consulta pode enxergar.

    Com um usuário, a visibilidade é a que as permissões do domínio já concedem
    — nunca uma matriz nova. Com uma lista de condomínios, é esse o recorte. Sem
    um nem outro, não há escopo: e sem escopo a resposta é vazia, porque a dúvida
    nunca autoriza.
    """
    if usuario is not None:
        visiveis = condominios_para_ver(usuario)
        if condominios is not None:
            visiveis = visiveis.filter(pk__in=_ids_de_condominios(condominios))
        return visiveis
    if condominios is not None:
        return _ids_de_condominios(condominios)
    return None


def _chave_de_ordenacao(item: ExcecaoDemanda):
    """A ordem de leitura: decisão mais imediata primeiro, desempate estável.

    Dentro do mesmo tipo, o maior atraso vem primeiro. Sem atraso, o título e o
    identificador fecham o empate: nada depende da ordem física do banco.
    """
    return (
        item.ordem,
        -item.dias_de_atraso if item.dias_de_atraso else _SEM_ATRASO,
        item.demanda.titulo.casefold(),
        str(item.demanda.pk),
    )


def obter_excecoes_de_demandas(
    *,
    condominios=None,
    usuario=None,
    hoje=None,
) -> list[ExcecaoDemanda]:
    """As demandas em aberto que pedem uma decisão, na ordem em que se lê.

    O recorte de condomínios vem da visibilidade do usuário (``usuario``), de
    uma lista explícita (``condominios``) ou da interseção dos dois. Sem
    recorte, a resposta é vazia.

    A data de referência é injetável (``hoje``) para que o cálculo não dependa
    do relógio. Uma leitura única, com os vínculos já resolvidos, evita uma
    consulta por demanda.
    """
    referencia = hoje or timezone.localdate()

    escopo = _escopo(condominios, usuario)
    if escopo is None:
        return []

    demandas = (
        Demanda.objects.abertas()
        .filter(ativo=True)
        .filter(condominio__in=escopo)
        .select_related("condominio", "local", "responsavel")
    )

    excecoes = [
        item
        for demanda in demandas
        if (item := _excecao_da_demanda(demanda, hoje=referencia)) is not None
    ]
    excecoes.sort(key=_chave_de_ordenacao)
    return excecoes
