"""A leitura do "Meu dia": o que a pessoa logada precisa fazer hoje.

Reúne três origens que já sabem se responder — a ocorrência de rotina, a
manutenção e a demanda — e as põe na ordem em que devem ser lidas: primeiro o
que atrasou, da data mais antiga para a mais recente; depois o que é de hoje.

Nenhuma regra de situação, de atraso ou de permissão nasce aqui: cada módulo
responde com o que já responde (``minhas()``, ``do_condominio()``,
``condominios_para_ver()``) e este módulo só junta as respostas. Não há modelo
novo nem campo guardado — o "meu dia" é lido na hora, como o resto do sistema.
"""
from dataclasses import dataclass
from datetime import date

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.condominios.acesso import condominios_com_papel
from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio
from apps.demandas.models import Demanda
from apps.demandas.permissoes import condominios_para_ver as demandas_que_vejo
from apps.manutencoes.models import Manutencao
from apps.manutencoes.permissoes import condominios_para_ver as manutencoes_que_vejo
from apps.rotinas.models import ExecucaoRotina
from apps.rotinas.permissoes import condominios_para_ver as rotinas_que_vejo

# Quem conduz o dia: a supervisão e quem executa — a operação e a portaria. A
# gestão não entra: ela tem o painel e a decisão; o "meu dia" é da ponta que
# faz acontecer.
PAPEIS_DO_MEU_DIA = frozenset(
    {Papel.SUPERVISORA, Papel.OPERACIONAL, Papel.PORTEIRO}
)

# Quem acompanha o dia da equipe: a administradora e a síndica. Elas respondem
# pelo condomínio e, por isso, enxergam o dia de quem executa — sem conduzir o
# próprio dia. Não é um "meu dia" delas: é a leitura do dia de outra pessoa.
PAPEIS_QUE_ACOMPANHAM = frozenset({Papel.ADMINISTRADORA, Papel.SINDICA})

TIPO_ROTINA = "rotina"
TIPO_MANUTENCAO = "manutencao"
TIPO_DEMANDA = "demanda"

# O ícone que o cartão mostra, por tipo de item. A tela não escolhe: lê daqui.
_ICONE = {
    TIPO_ROTINA: "bx-calendar-check",
    TIPO_MANUTENCAO: "bx-wrench",
    TIPO_DEMANDA: "bx-task",
}


@dataclass(frozen=True)
class ItemDoMeuDia:
    """Um item do dia, já apresentável: o que é, onde, quando e para onde ir."""

    tipo: str
    titulo: str
    local: str
    quando: str
    atrasado: bool
    referencia: date
    destino: str

    @property
    def icone(self) -> str:
        return _ICONE[self.tipo]


def condominios_do_meu_dia(usuario):
    """Os condomínios em que a pessoa tem o dia em vista, na ordem alfabética.

    Reúne quem conduz o próprio dia — supervisão, operação e portaria — e quem
    acompanha o dia da equipe — administradora e síndica. A leitura de cada dia
    é a mesma; muda quem está em vista.
    """
    return condominios_com_papel(
        usuario, PAPEIS_DO_MEU_DIA | PAPEIS_QUE_ACOMPANHAM
    ).order_by("nome")


def pode_ver_meu_dia(usuario) -> bool:
    """Diz se a pessoa tem o dia a conduzir ou a acompanhar em algum condomínio."""
    return condominios_do_meu_dia(usuario).exists()


def pode_acompanhar_o_dia(usuario, condominio) -> bool:
    """Diz se a pessoa acompanha o dia da equipe neste condomínio.

    É o recorte da administradora e da síndica: só quem responde pelo condomínio
    escolhe de quem é o dia que está vendo. Sem condomínio, a resposta é negar.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return False
    return condominios_com_papel(usuario, PAPEIS_QUE_ACOMPANHAM).filter(
        pk=condominio.pk
    ).exists()


def equipe_do_condominio(condominio) -> list[dict]:
    """Quem executa o dia neste condomínio, para a gestão acompanhar.

    São os vínculos ativos de supervisão, operação e portaria, ordenados por
    papel e nome. Cada pessoa traz o rótulo que o seletor mostra — o nome e o
    papel entre parênteses. Sem condomínio, a equipe é vazia.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return []

    acessos = (
        AcessoUsuarioCondominio.objects.filter(
            condominio=condominio,
            ativo=True,
            papel__in=PAPEIS_DO_MEU_DIA,
            usuario__is_active=True,
        )
        .select_related("usuario")
        .order_by("papel", "usuario__full_name")
    )
    return [
        {
            "id": str(acesso.usuario_id),
            "nome": acesso.usuario.full_name or str(acesso.usuario),
            "rotulo": f"{acesso.usuario} ({acesso.get_papel_display()})",
        }
        for acesso in acessos
    ]


def itens_do_meu_dia(usuario, condominio, hoje=None) -> list[ItemDoMeuDia]:
    """O que esta pessoa precisa fazer hoje neste condomínio, já ordenado.

    O condomínio é o que está em vista; sem ele, nada a mostrar. Cada origem
    só responde se a pessoa a enxerga naquele condomínio — a mesma permissão de
    leitura de cada módulo —, de modo que o "meu dia" nunca revela o que a
    tela de origem esconderia.
    """
    hoje = hoje or timezone.localdate()
    if condominio is None:
        return []

    itens = [
        *_das_rotinas(usuario, condominio, hoje),
        *_das_manutencoes(usuario, condominio, hoje),
        *_das_demandas(usuario, condominio, hoje),
    ]
    # Atrasados primeiro, do mais antigo para o mais recente; depois os de
    # hoje. A referência de cada item é a data que o faz aparecer aqui.
    itens.sort(key=lambda item: (0 if item.atrasado else 1, item.referencia))
    return itens


def _quando(referencia: date, hoje: date) -> str:
    """"Atrasado X dias" quando a data passou; "Hoje" quando é hoje ou depois."""
    dias = (hoje - referencia).days
    if dias > 0:
        return f"Atrasado {dias} dia" + ("s" if dias > 1 else "")
    return "Hoje"


def _item(tipo, titulo, local, referencia, hoje, destino) -> ItemDoMeuDia:
    return ItemDoMeuDia(
        tipo=tipo,
        titulo=titulo,
        local=local or "",
        quando=_quando(referencia, hoje),
        atrasado=referencia < hoje,
        referencia=referencia,
        destino=destino,
    )


def _das_rotinas(usuario, condominio, hoje) -> list[ItemDoMeuDia]:
    """As ocorrências de rotina pendentes, minhas, previstas até hoje."""
    if not rotinas_que_vejo(usuario).filter(pk=condominio.pk).exists():
        return []
    execucoes = (
        ExecucaoRotina.objects.do_condominio(condominio)
        .minhas(usuario)
        .filter(data_prevista__lte=hoje)
        .select_related("rotina", "rotina__local")
    )
    itens = []
    for execucao in execucoes:
        local = execucao.rotina.local
        itens.append(
            _item(
                TIPO_ROTINA,
                execucao.rotina.titulo,
                local.nome if local else "",
                execucao.data_prevista,
                hoje,
                reverse("rotinas:execucao", args=[execucao.pk]),
            )
        )
    return itens


def _das_manutencoes(usuario, condominio, hoje) -> list[ItemDoMeuDia]:
    """As manutenções em aberto sob minha responsabilidade.

    Sem data prevista, a manutenção é lida como de hoje: ela é minha e está em
    aberto, então pertence ao dia.
    """
    if not manutencoes_que_vejo(usuario).filter(pk=condominio.pk).exists():
        return []
    manutencoes = (
        Manutencao.objects.do_condominio(condominio)
        .minhas(usuario)
        .select_related("local")
    )
    itens = []
    for manutencao in manutencoes:
        itens.append(
            _item(
                TIPO_MANUTENCAO,
                manutencao.titulo,
                manutencao.local.nome if manutencao.local_id else "",
                manutencao.data_prevista or hoje,
                hoje,
                reverse("manutencoes:detalhe", args=[manutencao.pk]),
            )
        )
    return itens


def _das_demandas(usuario, condominio, hoje) -> list[ItemDoMeuDia]:
    """As demandas ativas em aberto que são minhas ou que eu acompanho.

    Entram as que têm prazo da próxima ação ou prazo já vencido hoje ou antes.
    A data de referência é a mais urgente das duas que já chegaram.
    """
    if not demandas_que_vejo(usuario).filter(pk=condominio.pk).exists():
        return []
    demandas = (
        Demanda.objects.filter(condominio=condominio, ativo=True)
        .abertas()
        .filter(Q(prazo_proxima_acao__lte=hoje) | Q(prazo__lte=hoje))
        .filter(Q(responsavel=usuario) | Q(responsavel_acompanhamento=usuario))
        .select_related("local")
    )
    itens = []
    for demanda in demandas:
        datas = [
            data
            for data in (demanda.prazo_proxima_acao, demanda.prazo)
            if data is not None
        ]
        referencia = min(datas) if datas else hoje
        itens.append(
            _item(
                TIPO_DEMANDA,
                demanda.titulo,
                demanda.local_para_exibir,
                referencia,
                hoje,
                reverse("demandas:detalhe", args=[demanda.pk]),
            )
        )
    return itens
