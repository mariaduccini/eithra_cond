"""O que merece atenção nas manutenções — derivado, nunca guardado.

A administradora não deveria precisar lembrar de olhar o que vence, perceber o
atraso, cobrar a execução que não foi registrada ou conferir o que já foi
entregue. Este módulo lê o estado real da manutenção e deriva dele, de forma
determinística, um item de atenção.

Nada aqui é gravado. Não existe tabela de alerta, de notificação, de tarefa nem
de pendência: a atenção é sempre calculada a partir do que já está persistido —
a data prevista, a execução, a situação, a conferência, o responsável e o
estado ativo. Guardar o que pode ser derivado criaria uma segunda fonte de
verdade, e a segunda fonte é sempre a que mente primeiro.

Uma manutenção acumula fatos ao mesmo tempo — está atrasada e sem responsável,
por exemplo. O módulo não transforma isso em alertas redundantes: escolhe UMA
atenção principal pela precedência de ``PRECEDENCIA`` e compõe os demais fatos
na mesma mensagem. A pessoa lê uma frase operacional, não quatro avisos sobre a
mesma coisa.

O que o sistema não adivinha: sem execução registrada significa exatamente
isso. Não significa que o fornecedor não veio — a ausência do registro pode ser
atraso do fornecedor, esquecimento de quem registra ou reagendamento, e o
sistema não escolhe uma dessas histórias por conta própria.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.db import models
from django.utils import timezone

from .constants import StatusConferencia
from .models import Manutencao
from .permissoes import condominios_para_ver

# Com quantos dias de antecedência a preventiva mais próxima ainda aparece.
# Serve só quando nada foi configurado; é leitura, não regra de negócio.
ANTECEDENCIA_PADRAO_DIAS = 3

# Sentinela para ordenar o que não tem data: vai para o fim dentro do seu tipo.
_SEM_DATA = 10**9


class TipoAtencao(models.TextChoices):
    """Os poucos estados de atenção que a operação realmente distingue.

    Cada palavra responde a uma pergunta diferente de quem acompanha: o que
    precisa ser corrigido, o que precisa ser conferido, o que já foi executado
    e só falta encerrar, o que passou da hora, o que vence hoje, o que está sem
    dono e o que ainda está por vir. Não há variações decorativas — uma palavra
    nova só entra quando descreve uma ação humana diferente das que já existem.

    ``AGUARDANDO_CONCLUSAO`` é o serviço já executado que ainda não foi
    encerrado: sem conferência devida, ou com a conferência já aprovada. O
    próximo passo não é prazo nem conferência — é fechar o registro. Sem esta
    palavra, uma manutenção executada continuaria sendo lida como atraso de
    execução, o que deixou de ser verdade.
    """

    AGUARDANDO_CORRECAO = "aguardando_correcao", "Aguardando correção"
    AGUARDANDO_CONFERENCIA = "aguardando_conferencia", "Aguardando conferência"
    AGUARDANDO_CONCLUSAO = "aguardando_conclusao", "Aguardando conclusão"
    ATRASADA = "atrasada", "Atrasada"
    HOJE = "hoje", "Hoje"
    SEM_RESPONSAVEL = "sem_responsavel", "Sem responsável"
    PROXIMA = "proxima", "Próxima"


# A ordem em que a atenção é lida e ordenada: o que exige ação mais imediata
# primeiro. É a autoridade única da precedência — nenhuma tela repete esta
# ordem por conta própria.
#
# A correção e a conferência vêm antes da conclusão porque dependem de uma
# decisão sobre o que foi entregue; a conclusão, por sua vez, vem antes do
# atraso porque o serviço já aconteceu — o prazo deixou de ser o próximo passo.
# "Sem responsável" fica depois do prazo: quem assume é importante, mas o que
# está vencido ou já executado pesa mais.
PRECEDENCIA = {
    TipoAtencao.AGUARDANDO_CORRECAO: 0,
    TipoAtencao.AGUARDANDO_CONFERENCIA: 1,
    TipoAtencao.AGUARDANDO_CONCLUSAO: 2,
    TipoAtencao.ATRASADA: 3,
    TipoAtencao.HOJE: 4,
    TipoAtencao.SEM_RESPONSAVEL: 5,
    TipoAtencao.PROXIMA: 6,
}

# A ação que a atenção sugere, em uma linha. É conceito, não instrução técnica:
# quem apresenta decide como e onde mostrar.
_ACAO_SUGERIDA = {
    TipoAtencao.AGUARDANDO_CORRECAO: "Orientar a correção do que foi reprovado",
    TipoAtencao.AGUARDANDO_CONFERENCIA: "Conferir o serviço entregue",
    TipoAtencao.AGUARDANDO_CONCLUSAO: "Concluir a manutenção",
    TipoAtencao.ATRASADA: "Verificar por que o serviço não foi executado",
    TipoAtencao.HOJE: "Executar o serviço previsto para hoje",
    TipoAtencao.SEM_RESPONSAVEL: "Definir quem responde pela manutenção",
    TipoAtencao.PROXIMA: "Preparar a execução prevista",
}


@dataclass(frozen=True)
class AtencaoManutencao:
    """A leitura derivada de uma manutenção — fato e códigos, sem texto guardado.

    Guarda a manutenção, o tipo de atenção, a data de referência usada no
    cálculo e os fatos que compõem a leitura. Título, mensagem e ação sugerida
    são montados a partir daí, na hora de apresentar: o texto de interface não
    vira dado.
    """

    manutencao: Manutencao
    tipo: TipoAtencao
    data_referencia: date
    # Positivo: a previsão está à frente. Negativo: já passou. Zero: hoje.
    # Nulo: a manutenção não tem data prevista.
    dias_de_diferenca: int | None = None
    sem_responsavel: bool = False
    execucao_nao_registrada: bool = False

    @property
    def ordem(self) -> int:
        """A severidade operacional: quanto menor, mais à frente na leitura."""
        return PRECEDENCIA[self.tipo]

    @property
    def titulo(self) -> str:
        """O nome curto do estado de atenção."""
        return TipoAtencao(self.tipo).label

    @property
    def acao_sugerida(self) -> str:
        """O próximo passo conceitual que a atenção sugere."""
        return _ACAO_SUGERIDA[self.tipo]

    @property
    def mensagem(self) -> str:
        """A frase operacional, composta pelos fatos da mesma manutenção."""
        partes = [self._frase_principal()]
        if (
            self.sem_responsavel
            and self.tipo != TipoAtencao.SEM_RESPONSAVEL
        ):
            partes.append("sem responsável")
        return " · ".join(partes)

    def _frase_principal(self) -> str:
        if self.tipo == TipoAtencao.AGUARDANDO_CORRECAO:
            return "Correção pendente"
        if self.tipo == TipoAtencao.AGUARDANDO_CONFERENCIA:
            return "Aguardando conferência"
        if self.tipo == TipoAtencao.AGUARDANDO_CONCLUSAO:
            return "Execução registrada · falta concluir a manutenção"
        if self.tipo == TipoAtencao.HOJE:
            return "Vence hoje"
        if self.tipo == TipoAtencao.SEM_RESPONSAVEL:
            return "Sem responsável"
        if self.tipo == TipoAtencao.PROXIMA:
            return f"Prevista para {self._data_prevista_formatada()}"
        return self._frase_de_atraso()

    def _frase_de_atraso(self) -> str:
        dias = abs(self.dias_de_diferenca or 0)
        rotulo = "dia" if dias == 1 else "dias"
        return f"Atrasada há {dias} {rotulo} · execução não registrada"

    def _data_prevista_formatada(self) -> str:
        return self.manutencao.data_prevista.strftime("%d/%m/%Y")


@dataclass(frozen=True)
class ResumoDeAtencoes:
    """Contagens derivadas de um conjunto de atenções — sem tabela de contador."""

    atencao: tuple[AtencaoManutencao, ...] = ()

    @classmethod
    def de(cls, atencao) -> "ResumoDeAtencoes":
        """Resume uma coleção de atenções já derivadas."""
        return cls(tuple(atencao))

    def _contar(self, tipo: TipoAtencao) -> int:
        return sum(1 for item in self.atencao if item.tipo == tipo)

    @property
    def total(self) -> int:
        return len(self.atencao)

    @property
    def atrasadas(self) -> int:
        return self._contar(TipoAtencao.ATRASADA)

    @property
    def hoje(self) -> int:
        return self._contar(TipoAtencao.HOJE)

    @property
    def aguardando_conferencia(self) -> int:
        return self._contar(TipoAtencao.AGUARDANDO_CONFERENCIA)

    @property
    def aguardando_conclusao(self) -> int:
        return self._contar(TipoAtencao.AGUARDANDO_CONCLUSAO)

    @property
    def aguardando_correcao(self) -> int:
        return self._contar(TipoAtencao.AGUARDANDO_CORRECAO)

    @property
    def proximas(self) -> int:
        return self._contar(TipoAtencao.PROXIMA)


def _dias_de_antecedencia(valor=None) -> int:
    """Quantos dias à frente ainda contam como "próxima". Nunca negativo."""
    if valor is None:
        valor = getattr(
            settings,
            "MANUTENCOES_ALERTA_ANTECEDENCIA_DIAS",
            ANTECEDENCIA_PADRAO_DIAS,
        )
    return max(0, int(valor))


def _ids_de_condominios(condominios) -> list:
    """Os identificadores dos condomínios, venha lista ou queryset."""
    if condominios is None:
        return []
    return [getattr(item, "pk", item) for item in condominios]


def _escopo(condominios, usuario):
    """O recorte de condomínios que esta consulta pode enxergar.

    Com um usuário, a visibilidade é a que as permissões do domínio já
    concedem — nunca uma matriz nova. Com uma lista de condomínios, é esse o
    recorte. Sem um nem outro, não há escopo: e sem escopo a resposta é vazia,
    porque a dúvida nunca autoriza.
    """
    if usuario is not None:
        visiveis = condominios_para_ver(usuario)
        if condominios is not None:
            visiveis = visiveis.filter(pk__in=_ids_de_condominios(condominios))
        return visiveis
    if condominios is not None:
        return _ids_de_condominios(condominios)
    return None


def _chave_de_ordenacao(item: AtencaoManutencao):
    """A ordem de leitura: ação mais imediata primeiro, com desempate estável.

    Dentro do mesmo tipo, a previsão mais distante para trás vem primeiro (o
    atraso maior) e a mais próxima para a frente vem primeiro (o que vence
    antes) — as duas leituras caem na mesma conta, porque a diferença é
    negativa quando passou e positiva quando está por vir. Sem data fica no
    fim, e o título com o identificador fecham o empate: nada depende da ordem
    física do banco.
    """
    dias = item.dias_de_diferenca
    return (
        item.ordem,
        dias if dias is not None else _SEM_DATA,
        item.manutencao.titulo.casefold(),
        str(item.manutencao.pk),
    )


def _atencao_da_manutencao(manutencao, *, hoje, dias_antecedencia):
    """Deriva a atenção de uma manutenção, ou ``None`` quando não há nenhuma.

    A decisão segue a precedência, e nenhum fato vira dois itens: os fatos
    secundários — sem responsável, execução não registrada — compõem a mesma
    atenção em vez de gerar avisos paralelos.
    """
    prevista = manutencao.data_prevista
    executada = manutencao.data_execucao is not None
    sem_responsavel = manutencao.responsavel_id is None
    conferencia = manutencao.status_conferencia
    dias = (prevista - hoje).days if prevista is not None else None

    def atencao(tipo, *, execucao_nao_registrada=False):
        return AtencaoManutencao(
            manutencao=manutencao,
            tipo=tipo,
            data_referencia=hoje,
            dias_de_diferenca=dias,
            sem_responsavel=sem_responsavel,
            execucao_nao_registrada=execucao_nao_registrada,
        )

    # A reprovação é a pendência mais específica: pede correção, não prazo.
    if conferencia == StatusConferencia.REPROVADA:
        return atencao(TipoAtencao.AGUARDANDO_CORRECAO)

    # Executada e devendo conferência: o que falta é a conferência, mesmo que a
    # data prevista já tenha passado. Não se mostra "atrasada" aqui.
    if (
        manutencao.exige_conferencia
        and executada
        and conferencia == StatusConferencia.PENDENTE
    ):
        return atencao(TipoAtencao.AGUARDANDO_CONFERENCIA)

    # Executada, sem conferência devida ou com a conferência já aprovada: o
    # serviço aconteceu e só falta encerrar o registro. O prazo deixou de ser o
    # próximo passo, e "execução não registrada" seria falso.
    if executada:
        return atencao(TipoAtencao.AGUARDANDO_CONCLUSAO)

    # Passou da data prevista, sem execução registrada, e continua aberta.
    if prevista is not None and prevista < hoje:
        return atencao(TipoAtencao.ATRASADA, execucao_nao_registrada=True)

    # Vence hoje e a execução ainda não foi registrada: ainda há o que fazer.
    if prevista is not None and prevista == hoje:
        return atencao(TipoAtencao.HOJE, execucao_nao_registrada=True)

    # Aberta e sem responsável: alguém precisa assumir, mesmo sem data marcada.
    if sem_responsavel:
        return atencao(TipoAtencao.SEM_RESPONSAVEL)

    # Dentro da janela de antecedência: entra na leitura antes de vencer.
    if (
        prevista is not None
        and 0 < (prevista - hoje).days <= dias_antecedencia
    ):
        return atencao(TipoAtencao.PROXIMA)

    return None


def obter_atencoes_de_manutencoes(
    *,
    condominios=None,
    usuario=None,
    responsavel=None,
    hoje=None,
    dias_antecedencia=None,
) -> list[AtencaoManutencao]:
    """As manutenções que merecem atenção, na ordem em que devem ser lidas.

    O recorte de condomínios é obrigatório por natureza: vem da visibilidade do
    usuário (``usuario``), de uma lista explícita (``condominios``) ou da
    interseção dos dois. Sem recorte, a resposta é vazia.

    A data de referência é injetável (``hoje``) para que o cálculo não dependa
    do relógio — o mesmo motivo pelo qual a janela de antecedência também pode
    ser informada. Uma leitura única, com os vínculos já resolvidos, evita uma
    consulta por manutenção.
    """
    referencia = hoje or timezone.localdate()
    antecedencia = _dias_de_antecedencia(dias_antecedencia)

    escopo = _escopo(condominios, usuario)
    if escopo is None:
        return []

    manutencoes = (
        Manutencao.objects.abertas()
        .filter(ativo=True)
        .filter(condominio__in=escopo)
        .select_related("condominio", "responsavel", "local", "equipamento")
    )

    if responsavel is not None:
        manutencoes = manutencoes.filter(
            responsavel_id=getattr(responsavel, "pk", responsavel)
        )

    atencao = [
        item
        for manutencao in manutencoes
        if (
            item := _atencao_da_manutencao(
                manutencao, hoje=referencia, dias_antecedencia=antecedencia
            )
        )
        is not None
    ]
    atencao.sort(key=_chave_de_ordenacao)
    return atencao
