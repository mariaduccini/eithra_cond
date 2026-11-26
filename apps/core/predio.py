"""O prédio visto por dentro: cada nível, o estado dele e o que há ali.

O desenho do prédio é um só, em ``templates/pages/predio/_predio.svg``, e ele
não conhece o banco: espera receber, por nível, um status — urgente, atenção ou
em dia. Quem responde isso é este módulo, com uma leitura agregada do que já
existe: os locais cadastrados, os equipamentos deles, o que está aberto nas
demandas, o que atrasou nas manutenções e o que vem por aí nos planos e nas
rotinas.

Uma leitura só, e não seis telas costuradas: as consultas são as mesmas para
todos os níveis e o agrupamento por nível acontece em memória. Nada aqui grava,
e nada aqui decide permissão — quem decide se a pessoa pode ver o prédio é a
view.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Equipamento, LocalOperacional
from apps.cadastros.niveis import NIVEIS, PREDIO, nivel_do_local
from apps.demandas.constants import PrioridadeDemanda
from apps.demandas.models import Demanda
from apps.manutencoes.constants import StatusManutencao
from apps.manutencoes.models import SITUACOES_ENCERRADAS, Manutencao, PlanoPreventivo
from apps.rotinas.models import RotinaRecorrente

# Os três estados que o desenho conhece.
URGENTE = "urgente"
ATENCAO = "atencao"
OK = "ok"

# A janela em que uma preventiva que se aproxima já merece atenção.
JANELA_DE_ATENCAO_DIAS = 15

# As prioridades que tornam urgente o nível onde a demanda está.
PRIORIDADES_URGENTES = (PrioridadeDemanda.ALTA, PrioridadeDemanda.URGENTE)

# O nome de cada nível como o desenho o escreve.
TITULOS = {
    "cobertura": "Cobertura",
    "duplex": "9º a 16º · dúplex",
    "baixos": "1º a 8º",
    "playground": "Playground",
    "g1": "G1",
    "g2": "G2",
    "g3": "G3",
    "g4": "G4",
    PREDIO: "Prédio inteiro",
}


@dataclass
class LeituraDoPredio:
    """O prédio lido: os níveis por chave, a mesma lista em ordem e o sem local."""

    niveis: dict
    lista: list
    sem_local: int = 0


def niveis_do_predio(condominio, hoje=None) -> LeituraDoPredio:
    """Lê o prédio inteiro de um condomínio em uma passada.

    Devolve, por nível, o status derivado e o que há dentro de cada local: os
    equipamentos, a próxima preventiva, a última manutenção concluída e as
    demandas abertas. A leitura faz seis consultas, independentemente de quantos
    níveis ou locais existam, e nunca uma por linha.
    """
    referencia = hoje or timezone.localdate()
    limite = referencia + timedelta(days=JANELA_DE_ATENCAO_DIAS)

    niveis = {chave: _nivel_novo(chave) for chave in NIVEIS}
    if condominio is None:
        return LeituraDoPredio(niveis=niveis, lista=list(niveis.values()))

    # 1) Os locais ativos são o esqueleto: dizem quais níveis têm o que mostrar.
    locais_por_id = {}
    for local in LocalOperacional.objects.filter(
        condominio=condominio, ativo=True
    ).only("pk", "nome"):
        nivel = nivel_do_local(local.nome)
        registro = _local_novo(local, nivel)
        niveis[nivel]["locais"].append(registro)
        locais_por_id[local.pk] = registro

    # 2) Os equipamentos ativos entram no local deles — e, sem local, no prédio.
    nivel_por_equipamento = {}
    for equipamento in Equipamento.objects.filter(
        condominio=condominio, ativo=True
    ).values("pk", "nome", "modelo", "local_id"):
        registro = locais_por_id.get(equipamento["local_id"])
        if registro is None:
            registro = _registro_avulso(niveis[PREDIO], equipamento["local_id"])
            locais_por_id[equipamento["local_id"]] = registro
        nivel_por_equipamento[equipamento["pk"]] = registro["_nivel"]
        registro["equipamentos"].append(
            {"nome": equipamento["nome"], "modelo": equipamento["modelo"]}
        )

    # 3) As demandas abertas: o que pesa decide o status do nível onde está.
    sem_local = 0
    demandas = (
        Demanda.objects.filter(condominio=condominio, ativo=True)
        .abertas()
        .values("pk", "titulo", "prioridade", "local_id", "equipamento_id")
    )
    for demanda in demandas:
        registro = locais_por_id.get(demanda["local_id"])
        if registro is not None:
            nivel = registro["_nivel"]
        elif demanda["local_id"] is not None:
            nivel = PREDIO
        else:
            sem_local += 1
            nivel = nivel_por_equipamento.get(demanda["equipamento_id"], PREDIO)
        item = {
            "pk": demanda["pk"],
            "titulo": demanda["titulo"],
            "prioridade": demanda["prioridade"],
            "urgente": demanda["prioridade"] in PRIORIDADES_URGENTES,
            "url": reverse("demandas:detalhe", args=[demanda["pk"]]),
        }
        niveis[nivel]["demandas"].append(item)
        if registro is not None:
            registro["demandas"].append(item)
        if item["urgente"]:
            niveis[nivel]["_urgente"] = True
        else:
            niveis[nivel]["_atencao"] = True

    # 4) As manutenções: a atrasada pesa como urgência; a concluída, como data.
    abertas = ~Q(status__in=SITUACOES_ENCERRADAS)
    manutencoes = (
        Manutencao.objects.filter(condominio=condominio, ativo=True)
        .filter(Q(status=StatusManutencao.CONCLUIDA) | (Q(data_prevista__lt=referencia) & abertas))
        .values("local_id", "equipamento_id", "status", "data_conclusao")
    )
    for manutencao in manutencoes:
        registro = locais_por_id.get(manutencao["local_id"])
        nivel = (
            registro["_nivel"]
            if registro is not None
            else nivel_por_equipamento.get(manutencao["equipamento_id"], PREDIO)
        )
        if manutencao["status"] == StatusManutencao.CONCLUIDA:
            conclusao = manutencao["data_conclusao"]
            if conclusao is not None and registro is not None:
                data = timezone.localtime(conclusao).date()
                registro["ultima_conclusao"] = _mais_recente(
                    registro["ultima_conclusao"], data
                )
        else:
            niveis[nivel]["_urgente"] = True

    # 5) Os planos preventivos: a data mais próxima vira a preventiva do local e,
    #    dentro da janela de atenção, acende o nível.
    planos = (
        PlanoPreventivo.objects.filter(condominio=condominio, ativo=True)
        .exclude(proxima_data_prevista__isnull=True)
        .values("local_id", "equipamento_id", "proxima_data_prevista")
    )
    for plano in planos:
        registro = locais_por_id.get(plano["local_id"])
        nivel = (
            registro["_nivel"]
            if registro is not None
            else nivel_por_equipamento.get(plano["equipamento_id"], PREDIO)
        )
        data = plano["proxima_data_prevista"]
        if registro is not None:
            registro["proxima_preventiva"] = _mais_proxima(
                registro["proxima_preventiva"], data
            )
        if data <= limite:
            niveis[nivel]["_atencao"] = True

    # 6) As rotinas: o mesmo lugar da preventiva, pela próxima execução.
    rotinas = (
        RotinaRecorrente.objects.filter(condominio=condominio, ativo=True)
        .exclude(proxima_execucao__isnull=True)
        .values("local_id", "equipamento_id", "proxima_execucao")
    )
    for rotina in rotinas:
        registro = locais_por_id.get(rotina["local_id"])
        nivel = (
            registro["_nivel"]
            if registro is not None
            else nivel_por_equipamento.get(rotina["equipamento_id"], PREDIO)
        )
        data = rotina["proxima_execucao"]
        if registro is not None:
            registro["proxima_preventiva"] = _mais_proxima(
                registro["proxima_preventiva"], data
            )
        if data <= limite:
            niveis[nivel]["_atencao"] = True

    for chave in NIVEIS:
        nivel = niveis[chave]
        if nivel.pop("_urgente"):
            nivel["status"] = URGENTE
        elif nivel.pop("_atencao"):
            nivel["status"] = ATENCAO
        else:
            nivel["status"] = OK
        nivel["abrir_demanda_url"] = _url_de_criacao(
            "demandas:listagem", condominio, nivel["locais"]
        )
        nivel["registrar_manutencao_url"] = _url_de_criacao(
            "manutencoes:listagem", condominio, nivel["locais"]
        )

    return LeituraDoPredio(
        niveis=niveis, lista=list(niveis.values()), sem_local=sem_local
    )


def _nivel_novo(chave) -> dict:
    """Um nível ainda sem nada dentro: o desenho já pode ser montado com ele."""
    return {
        "chave": chave,
        "titulo": TITULOS.get(chave, chave),
        "status": OK,
        "locais": [],
        "demandas": [],
        "_urgente": False,
        "_atencao": False,
    }


def _local_novo(local, nivel) -> dict:
    """Um local do cadastro, pronto para receber equipamentos e ocorrências."""
    return {
        "pk": local.pk,
        "nome": local.nome,
        "equipamentos": [],
        "demandas": [],
        "proxima_preventiva": None,
        "ultima_conclusao": None,
        "_nivel": nivel,
    }


def _registro_avulso(nivel, local_id) -> dict:
    """Reúne o que tem local inativo — ou nenhum — sem perder o registro.

    O que não tem cadastro ativo não pode sumir do prédio: sem isso, um
    equipamento órfão ficaria invisível, e o nível pareceria mais vazio do que é.
    """
    registro = {
        "pk": local_id,
        "nome": "Sem local definido" if local_id is None else "Outros",
        "equipamentos": [],
        "demandas": [],
        "proxima_preventiva": None,
        "ultima_conclusao": None,
        "_nivel": nivel["chave"],
    }
    nivel["locais"].append(registro)
    return registro


def _url_de_criacao(view, condominio, locais) -> str:
    """O endereço que abre o formulário já no condomínio — e no local, se único.

    Sem local — ou com mais de um no nível —, o formulário abre no condomínio e
    quem preenche escolhe o lugar; com um só, ele já vai escolhido.
    """
    url = f"{reverse(view)}?abrir=criar"
    if condominio is not None:
        url += f"&condominio={condominio.pk}"
    if len(locais) == 1 and locais[0]["pk"] is not None:
        url += f"&local={locais[0]['pk']}"
    return url


def _mais_proxima(atual, data):
    """A data que ainda não passou primeiro; empate fica com a que já estava."""
    if data is None:
        return atual
    if atual is None or data < atual:
        return data
    return atual


def _mais_recente(atual, data):
    """A data mais recente entre a que já se tinha e a nova."""
    if data is None:
        return atual
    if atual is None or data > atual:
        return data
    return atual
