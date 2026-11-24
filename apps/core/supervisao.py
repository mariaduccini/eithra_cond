"""O que precisa da supervisão agora — derivado, nunca guardado.

Quem supervisiona a operação do condomínio não deveria varrer o sistema para
descobrir o que fazer em seguida: a visita que já chegou, a ronda do dia que
ainda não foi feita, o serviço cujo encerramento ninguém registrou. Este módulo
lê o estado real dos registros que já existem — manutenções, ocorrências de
rotina e demandas — e deriva deles, de forma determinística, a ação que está
pedindo alguém.

Nada aqui é gravado. Não existe tabela de tarefa, de agenda nem de pendência da
supervisão: a ação é sempre calculada a partir do que já está persistido — a
data prevista, a execução, a situação, o próximo passo combinado. Guardar o que
pode ser derivado criaria uma segunda fonte de verdade, e a segunda fonte é
sempre a que mente primeiro.

O módulo é um agregador, não um domínio: ele reúne o que cada domínio já sabe e
traduz para a mesma leitura, sem substituir nenhum deles. Cada ação aponta para o
registro original, e é lá que qualquer mudança acontece — aqui só se lê.

A ordem de leitura é a autoridade única de ``PRECEDENCIA``: o que já deveria ter
sido feito antes do que é de hoje, e o que é de hoje antes do que apenas
continua aberto. Nenhuma tela repete esta ordem por conta própria.

Uma escolha merece ser dita em voz alta: a ocorrência de rotina já iniciada e
ainda não encerrada é lida como **pendência**, e não como ação imediata. "Agora"
guarda o que venceu ou o que falta fechar; "Pendências" guarda o ciclo que
ficou aberto. É a leitura que o próprio domínio sustenta — encerrar é o que
retira a ocorrência daqui.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db import models
from django.utils import timezone

from apps.condominios.acesso import condominios_com_papel
from apps.condominios.constants import Papel
from apps.demandas.constants import StatusDemanda
from apps.demandas.models import Demanda
from apps.manutencoes.constants import StatusManutencao
from apps.manutencoes.models import Manutencao
from apps.rotinas.constants import SITUACOES_PENDENTES, StatusExecucao
from apps.rotinas.models import ExecucaoRotina

# O papel que responde pela supervisão operacional do condomínio. É por ele que a
# Home sabe se deve mostrar a área — e é a partir dele que o recorte de
# condomínios é calculado.
PAPEIS_DA_SUPERVISAO = frozenset({Papel.SUPERVISORA})

# De onde veio a ação. A origem diz qual registro sustenta o item e para onde o
# clique leva; não é um segundo cadastro.
ORIGEM_MANUTENCAO = "manutencao"
ORIGEM_ROTINA = "rotina"
ORIGEM_DEMANDA = "demanda"

# Situações em que uma demanda está sendo conduzida e já tem um próximo passo
# combinado. Uma demanda recém-aberta ainda pode estar sendo triada.
SITUACOES_EM_CONDUCAO = frozenset(
    {StatusDemanda.EM_ANDAMENTO, StatusDemanda.AGUARDANDO}
)

# Sentinela para ordenar o que não tem data: vai para o fim dentro do seu bloco.
_SEM_DATA = 10**9


class BlocoDaSupervisao(models.TextChoices):
    """Os três agrupamentos da área, na ordem em que são lidos.

    ``AGORA`` guarda o que já deveria ter acontecido ou o que falta fechar;
    ``HOJE`` guarda o compromisso do dia; ``PENDENCIA`` guarda o ciclo que ficou
    aberto. Nada além disso — a área conduz o dia, não o resume em números.
    """

    AGORA = "agora", "Agora"
    HOJE = "hoje", "Ainda hoje"
    PENDENCIA = "pendencia", "Pendências"


class TipoAcao(models.TextChoices):
    """As poucas ações que a supervisão realmente distingue.

    Cada palavra responde a uma pergunta diferente de quem conduz: a visita
    atrasada, a visita de hoje, o serviço que já começou e não terminou, a
    atividade que não foi feita, a atividade do dia e o acompanhamento que segue
    aberto. Uma palavra nova só entra quando descreve uma ação humana diferente
    das que já existem.
    """

    VISITA_FECHAMENTO = "visita_fechamento", "Fechamento pendente"
    VISITA_ATRASADA = "visita_atrasada", "Visita atrasada"
    VISITA_HOJE = "visita_hoje", "Visita de hoje"
    SERVICO_EM_ANDAMENTO = "servico_em_andamento", "Serviço em andamento"
    ATIVIDADE_ATRASADA = "atividade_atrasada", "Atividade atrasada"
    ATIVIDADE_HOJE = "atividade_hoje", "Atividade de hoje"
    ATIVIDADE_EM_ANDAMENTO = "atividade_em_andamento", "Atividade em andamento"
    DEMANDA_ACAO_VENCIDA = "demanda_acao_vencida", "Próxima ação vencida"
    DEMANDA_ACOMPANHAMENTO = "demanda_acompanhamento", "Acompanhamento aberto"


# A ordem em que as ações são lidas: dentro de cada bloco, o que pesa mais
# primeiro. É a autoridade única da precedência — nenhuma tela repete esta ordem.
# Os blocos ficam contíguos por construção: ``AGORA`` ocupa o começo, ``HOJE`` o
# meio e ``PENDENCIA`` o fim.
PRECEDENCIA = {
    TipoAcao.VISITA_FECHAMENTO: 0,
    TipoAcao.VISITA_ATRASADA: 1,
    TipoAcao.ATIVIDADE_ATRASADA: 2,
    TipoAcao.DEMANDA_ACAO_VENCIDA: 3,
    TipoAcao.VISITA_HOJE: 4,
    TipoAcao.ATIVIDADE_HOJE: 5,
    TipoAcao.SERVICO_EM_ANDAMENTO: 6,
    TipoAcao.ATIVIDADE_EM_ANDAMENTO: 7,
    TipoAcao.DEMANDA_ACOMPANHAMENTO: 8,
}

# A que bloco cada ação pertence. Derivado da precedência acima para que bloco e
# ordem não possam divergir: quem reordena a lista move o bloco junto.
_BLOCO = {
    TipoAcao.VISITA_FECHAMENTO: BlocoDaSupervisao.AGORA,
    TipoAcao.VISITA_ATRASADA: BlocoDaSupervisao.AGORA,
    TipoAcao.ATIVIDADE_ATRASADA: BlocoDaSupervisao.AGORA,
    TipoAcao.DEMANDA_ACAO_VENCIDA: BlocoDaSupervisao.AGORA,
    TipoAcao.VISITA_HOJE: BlocoDaSupervisao.HOJE,
    TipoAcao.ATIVIDADE_HOJE: BlocoDaSupervisao.HOJE,
    TipoAcao.SERVICO_EM_ANDAMENTO: BlocoDaSupervisao.PENDENCIA,
    TipoAcao.ATIVIDADE_EM_ANDAMENTO: BlocoDaSupervisao.PENDENCIA,
    TipoAcao.DEMANDA_ACOMPANHAMENTO: BlocoDaSupervisao.PENDENCIA,
}

# A ação principal do item, em uma palavra. É conceito, não instrução técnica:
# quem apresenta decide como e onde o clique leva. O que ainda não começou se
# acompanha; o que já começou e não terminou se atualiza.
_ACAO_SUGERIDA = {
    TipoAcao.VISITA_FECHAMENTO: "Atualizar",
    TipoAcao.VISITA_ATRASADA: "Acompanhar visita",
    TipoAcao.VISITA_HOJE: "Acompanhar visita",
    TipoAcao.SERVICO_EM_ANDAMENTO: "Atualizar",
    TipoAcao.ATIVIDADE_ATRASADA: "Iniciar",
    TipoAcao.ATIVIDADE_HOJE: "Iniciar",
    TipoAcao.ATIVIDADE_EM_ANDAMENTO: "Atualizar",
    TipoAcao.DEMANDA_ACAO_VENCIDA: "Atualizar",
    TipoAcao.DEMANDA_ACOMPANHAMENTO: "Acompanhar",
}

# O peso visual de cada ação, no vocabulário de rótulo que as telas já usam.
# É conceito de leitura — o que exige decisão pesa mais que o que apenas segue
# aberto — não instrução de cor: quem apresenta escolhe o tom do rótulo.
_TOM = {
    TipoAcao.VISITA_FECHAMENTO: "warning",
    TipoAcao.VISITA_ATRASADA: "danger",
    TipoAcao.ATIVIDADE_ATRASADA: "danger",
    TipoAcao.DEMANDA_ACAO_VENCIDA: "danger",
    TipoAcao.VISITA_HOJE: "info",
    TipoAcao.ATIVIDADE_HOJE: "info",
    TipoAcao.SERVICO_EM_ANDAMENTO: "warning",
    TipoAcao.ATIVIDADE_EM_ANDAMENTO: "warning",
    TipoAcao.DEMANDA_ACOMPANHAMENTO: "secondary",
}


@dataclass(frozen=True)
class AcaoDaSupervisao:
    """A leitura derivada de um registro — fato e códigos, sem texto guardado.

    Guarda a origem, o tipo, o registro que sustenta a ação, a data de referência
    usada no cálculo e a data prevista do próprio registro. O nome, a mensagem e
    a ação sugerida são montados a partir daí, na hora de apresentar: o texto de
    interface não vira dado.
    """

    origem: str
    tipo: TipoAcao
    objeto: object
    data_referencia: date
    data_prevista: date | None = None
    # Positivo: a data prevista está à frente. Negativo: já passou. Nulo: sem data.
    dias_de_diferenca: int | None = None

    @property
    def ordem(self) -> int:
        """A severidade operacional: quanto menor, mais à frente na leitura."""
        return PRECEDENCIA[self.tipo]

    @property
    def bloco(self) -> BlocoDaSupervisao:
        """A que agrupamento da área a ação pertence."""
        return _BLOCO[self.tipo]

    @property
    def rotulo(self) -> str:
        """O nome curto do estado da ação."""
        return TipoAcao(self.tipo).label

    @property
    def identificador(self):
        """O identificador do registro original, para o clique."""
        return self.objeto.pk

    @property
    def nome(self) -> str:
        """O nome do registro, como a supervisão o reconhece."""
        if self.origem == ORIGEM_ROTINA:
            return self.objeto.rotina.titulo
        return self.objeto.titulo

    @property
    def acao_sugerida(self) -> str:
        """A ação principal que o item pede, em uma palavra."""
        return _ACAO_SUGERIDA[self.tipo]

    @property
    def tom(self) -> str:
        """O peso visual do item, no vocabulário de rótulo das telas."""
        return _TOM[self.tipo]

    @property
    def mensagem(self) -> str:
        """A frase operacional, composta pelos fatos do registro."""
        if self.tipo == TipoAcao.VISITA_FECHAMENTO:
            return "Falta registrar como terminou"
        if self.tipo == TipoAcao.SERVICO_EM_ANDAMENTO:
            return self._iniciado_em_frase("Este serviço continua aberto")
        if self.tipo == TipoAcao.ATIVIDADE_EM_ANDAMENTO:
            return self._iniciado_em_frase("Esta atividade continua aberta")
        if self.tipo in (TipoAcao.VISITA_ATRASADA, TipoAcao.ATIVIDADE_ATRASADA):
            return f"Estava prevista para {self._data_prevista_formatada()}"
        if self.tipo in (TipoAcao.VISITA_HOJE, TipoAcao.ATIVIDADE_HOJE):
            return "Prevista para hoje"
        if self.tipo == TipoAcao.DEMANDA_ACAO_VENCIDA:
            return f"Próxima ação vencida em {self._data_prevista_formatada()}"
        return "Tem uma próxima ação combinada"

    def _iniciado_em_frase(self, quando_sem_hora: str) -> str:
        """Diz quando o acompanhamento começou, ou o que ainda falta.

        O momento do início é o do servidor, gravado pelo domínio quando o
        serviço ou a atividade começou — a leitura só o mostra. Sem início
        registrado, resta a frase neutra: nunca se inventa um horário.
        """
        inicio = getattr(self.objeto, "data_inicio", None) or getattr(
            self.objeto, "iniciado_em", None
        )
        if inicio is None:
            return quando_sem_hora
        return f"Acompanhamento iniciado às {timezone.localtime(inicio):%H:%M}"

    def _data_prevista_formatada(self) -> str:
        if self.data_prevista is None:
            return "sem data"
        return self.data_prevista.strftime("%d/%m/%Y")


def condominios_da_supervisao(usuario) -> models.QuerySet:
    """Condomínios em que a pessoa atua como supervisão operacional agora.

    A pergunta "esta pessoa acessa este condomínio?" continua em
    ``apps.condominios.acesso``; aqui se diz apenas qual papel importa para a
    supervisão.
    """
    return condominios_com_papel(usuario, PAPEIS_DA_SUPERVISAO)


def pode_ver_acoes_da_supervisao(usuario) -> bool:
    """Diz se a área "O que precisa de você" faz sentido para esta pessoa."""
    return condominios_da_supervisao(usuario).exists()


def _ids_de_condominios(condominios) -> list:
    """Os identificadores dos condomínios, venha lista ou queryset."""
    if condominios is None:
        return []
    return [getattr(item, "pk", item) for item in condominios]


def _escopo(condominios, usuario):
    """O recorte de condomínios que esta leitura pode enxergar.

    A visibilidade é a que o papel de supervisão já concede — nunca uma matriz
    nova. Sem usuário nem lista, não há escopo: e sem escopo a resposta é vazia,
    porque a dúvida nunca autoriza.
    """
    if usuario is not None:
        visiveis = condominios_da_supervisao(usuario)
        if condominios is not None:
            visiveis = visiveis.filter(pk__in=_ids_de_condominios(condominios))
        return visiveis
    if condominios is not None:
        return _ids_de_condominios(condominios)
    return None


def _chave_de_ordenacao(item: AcaoDaSupervisao):
    """A ordem de leitura: o mais imediato primeiro, com desempate estável.

    Dentro do mesmo tipo, o atraso maior vem primeiro e o que vence antes também
    — as duas leituras caem na mesma conta, porque a diferença é negativa quando
    passou e positiva quando está por vir. Sem data fica no fim, e o nome com o
    identificador fecham o empate: nada depende da ordem física do banco.
    """
    dias = item.dias_de_diferenca
    return (
        item.ordem,
        dias if dias is not None else _SEM_DATA,
        item.nome.casefold(),
        str(item.identificador),
    )


def _acao(origem, tipo, objeto, *, hoje, data_prevista=None):
    """Monta a ação com a diferença de dias já calculada a partir de hoje."""
    dias = (data_prevista - hoje).days if data_prevista is not None else None
    return AcaoDaSupervisao(
        origem=origem,
        tipo=tipo,
        objeto=objeto,
        data_referencia=hoje,
        data_prevista=data_prevista,
        dias_de_diferenca=dias,
    )


def _acoes_de_manutencoes(escopo, hoje) -> list[AcaoDaSupervisao]:
    """O que as manutenções abertas pedem: fechar, acompanhar ou é hoje."""
    manutencoes = (
        Manutencao.objects.abertas()
        .filter(ativo=True)
        .filter(condominio__in=escopo)
        .select_related("condominio", "local", "fornecedor")
    )

    acoes = []
    for manutencao in manutencoes:
        prevista = manutencao.data_prevista

        if manutencao.data_execucao is not None:
            tipo = TipoAcao.VISITA_FECHAMENTO
        elif manutencao.status == StatusManutencao.EM_ANDAMENTO:
            tipo = TipoAcao.SERVICO_EM_ANDAMENTO
        elif prevista is not None and prevista < hoje:
            tipo = TipoAcao.VISITA_ATRASADA
        elif prevista is not None and prevista == hoje:
            tipo = TipoAcao.VISITA_HOJE
        else:
            continue

        acoes.append(
            _acao(
                ORIGEM_MANUTENCAO,
                tipo,
                manutencao,
                hoje=hoje,
                data_prevista=prevista,
            )
        )
    return acoes


def _acoes_de_rotinas(escopo, hoje) -> list[AcaoDaSupervisao]:
    """O que as ocorrências em aberto pedem: iniciar hoje, iniciar atrasada ou retomar."""
    execucoes = (
        ExecucaoRotina.objects.filter(status__in=SITUACOES_PENDENTES)
        .filter(condominio__in=escopo)
        .select_related("condominio", "rotina", "rotina__local", "responsavel")
    )

    acoes = []
    for execucao in execucoes:
        prevista = execucao.data_prevista

        if execucao.status == StatusExecucao.EM_ANDAMENTO:
            tipo = TipoAcao.ATIVIDADE_EM_ANDAMENTO
        elif prevista is not None and prevista < hoje:
            tipo = TipoAcao.ATIVIDADE_ATRASADA
        elif prevista is not None and prevista == hoje:
            tipo = TipoAcao.ATIVIDADE_HOJE
        else:
            continue

        acoes.append(
            _acao(
                ORIGEM_ROTINA,
                tipo,
                execucao,
                hoje=hoje,
                data_prevista=prevista,
            )
        )
    return acoes


def _acoes_de_demandas(escopo, hoje) -> list[AcaoDaSupervisao]:
    """O que as demandas em condução pedem: acompanhar o próximo passo combinado."""
    demandas = (
        Demanda.objects.abertas()
        .filter(ativo=True)
        .filter(condominio__in=escopo)
        .filter(status__in=SITUACOES_EM_CONDUCAO)
        .exclude(proxima_acao="")
        .select_related("condominio", "local", "equipamento", "responsavel")
    )

    acoes = []
    for demanda in demandas:
        prazo = demanda.prazo_proxima_acao
        if prazo is not None and prazo < hoje:
            tipo = TipoAcao.DEMANDA_ACAO_VENCIDA
        else:
            tipo = TipoAcao.DEMANDA_ACOMPANHAMENTO

        acoes.append(
            _acao(ORIGEM_DEMANDA, tipo, demanda, hoje=hoje, data_prevista=prazo)
        )
    return acoes


def obter_acoes_da_supervisao(
    *,
    condominios=None,
    usuario=None,
    hoje=None,
) -> list[AcaoDaSupervisao]:
    """As ações que pedem a supervisão, na ordem em que devem ser lidas.

    O recorte de condomínios vem do papel de supervisão do usuário (``usuario``),
    de uma lista explícita (``condominios``) ou da interseção dos dois. Sem
    recorte, a resposta é vazia.

    A data de referência é injetável (``hoje``) para que o cálculo não dependa do
    relógio. Uma leitura por origem, com os vínculos já resolvidos, evita uma
    consulta por item.
    """
    referencia = hoje or timezone.localdate()

    escopo = _escopo(condominios, usuario)
    if escopo is None:
        return []

    acoes = []
    acoes.extend(_acoes_de_manutencoes(escopo, referencia))
    acoes.extend(_acoes_de_rotinas(escopo, referencia))
    acoes.extend(_acoes_de_demandas(escopo, referencia))
    acoes.sort(key=_chave_de_ordenacao)
    return acoes
