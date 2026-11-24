"""Relatório da rodada de importação definitiva do RVE.

A importação grava; este módulo confere. Ele lê o resultado da rodada linha a
linha — o que a matriz mandava fazer e o que a rodada fez — e devolve três
artefatos, na forma que as fases de validação pedem:

A simulação grava os artefatos com o prefixo ``dry_run``; a carga definitiva
grava os seus com o prefixo ``pos_importacao`` — e o arquivo de detalhes passa a
ser a própria reconciliação pós-carga:

* ``…_resumo.txt`` — o balanço por tipo e por desfecho, seguido das conferências
  obrigatórias da carga;
* ``…_detalhes.csv`` / ``pos_importacao_rve_reconciliacao.csv`` — uma linha por
  identidade da matriz, com a decisão e o registro alcançado, para conferência
  item a item;
* ``…_conflitos.csv`` — o que impede a rodada de ser aceita: falhas de escrita e
  identidades penduradas em item superior que não existe.

Na carga definitiva o relatório ainda relê o banco e confere que cada destino
prometido pela rodada existe de fato — é a leitura que fecha a reconciliação:
nenhum item da matriz fica sem explicação.

Duas regras valem aqui inteiras:

* **conferir contra a fonte, não contra a expectativa** — as conferências contam
  a própria matriz, e divergência é relatada com os identificadores que a
  explicam, em vez de arredondada para "aproximado";
* **nada é inferido** — um número que a especificação não declara fica sem
  expectativa, e a conferência apenas o apresenta.
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from apps.core import rve_importacao
from apps.core.rve_importacao import DesfechoDaLinha, ResumoDaImportacao

ARQUIVO_RESUMO = "dry_run_importacao_rve_resumo.txt"
ARQUIVO_DETALHES = "dry_run_importacao_rve_detalhes.csv"
ARQUIVO_CONFLITOS = "dry_run_importacao_rve_conflitos.csv"

# A carga definitiva não escreve por cima dos artefatos da simulação: ela tem os
# seus próprios nomes, e o de detalhes é o que a especificação chama de
# reconciliação pós-carga.
ARQUIVO_RESUMO_CARGA = "pos_importacao_rve_resumo.txt"
ARQUIVO_RECONCILIACAO = "pos_importacao_rve_reconciliacao.csv"
ARQUIVO_CONFLITOS_CARGA = "pos_importacao_rve_conflitos.csv"


def _nomes_dos_artefatos(*, simulado: bool) -> dict[str, str]:
    """Os três nomes de arquivo da rodada, conforme ela simule ou grave."""
    if simulado:
        return {
            "resumo": ARQUIVO_RESUMO,
            "detalhes": ARQUIVO_DETALHES,
            "conflitos": ARQUIVO_CONFLITOS,
        }
    return {
        "resumo": ARQUIVO_RESUMO_CARGA,
        "detalhes": ARQUIVO_RECONCILIACAO,
        "conflitos": ARQUIVO_CONFLITOS_CARGA,
    }


COLUNAS_DETALHES = (
    "external_id",
    "familia",
    "tipo_entidade",
    "titulo",
    "parent_external_id",
    "acao_importacao",
    "desfecho",
    "destino",
    "revisao_necessaria",
    "motivo",
)
COLUNAS_CONFLITOS = ("external_id", "familia", "tipo_entidade", "motivo")

# Os desfechos na ordem em que o relatório os lê, com o nome que a especificação
# usa para cada um. É a única tradução de vocabulário deste módulo.
DESFECHOS = (
    (rve_importacao.DESFECHO_CRIADO, "CREATE"),
    (rve_importacao.DESFECHO_VINCULADO_EXISTENTE, "VINCULAR_EXISTENTE"),
    (rve_importacao.DESFECHO_VINCULADO_JORNADA, "VINCULAR_JORNADA"),
    (rve_importacao.DESFECHO_REVISAR, "REVISAR"),
    (rve_importacao.DESFECHO_ATUALIZADO, "UPDATE"),
    (rve_importacao.DESFECHO_CONSOLIDADO, "CONSOLIDATE"),
    (rve_importacao.DESFECHO_PENDENTE, "LINK (pendente de par)"),
    (rve_importacao.DESFECHO_HISTORICO, "HISTORY"),
    (rve_importacao.DESFECHO_PLANEJAMENTO, "PLANNING_ONLY"),
    (rve_importacao.DESFECHO_REVISAO, "REVIEW"),
    (rve_importacao.DESFECHO_IGNORADO, "SKIP / DO_NOT_CREATE"),
    (rve_importacao.DESFECHO_FALHA, "ERROR"),
)


@dataclass(frozen=True)
class Conferencia:
    """Uma das conferências obrigatórias, com o seu resultado.

    ``esperado`` é ``None`` quando a especificação não declara número: nesse
    caso a conferência só apresenta o que encontrou.
    """

    nome: str
    encontrado: int
    esperado: int | None = None
    explicacao: str = ""
    divergentes: tuple[str, ...] = ()

    @property
    def confirmada(self) -> bool:
        return self.esperado is None or self.encontrado == self.esperado


@dataclass
class Relatorio:
    """O relatório de uma rodada, antes de virar arquivo."""

    resumo: ResumoDaImportacao
    conferencias: list[Conferencia] = field(default_factory=list)
    conflitos: list[tuple[str, str, str, str]] = field(default_factory=list)
    inventario: dict | None = None

    @property
    def tudo_confirmado(self) -> bool:
        return all(item.confirmada for item in self.conferencias)


# ---------------------------------------------------------------------------
# As conferências obrigatórias
# ---------------------------------------------------------------------------


def conferir(resumo: ResumoDaImportacao) -> list[Conferencia]:
    """Confere a matriz contra o que a especificação manda validar."""
    por_tipo = defaultdict(list)
    por_familia = defaultdict(list)
    for linha in resumo.detalhes:
        por_tipo[linha.tipo_entidade].append(linha)
        por_familia[linha.familia].append(linha)

    def identificadores(linhas):
        return tuple(linha.external_id for linha in linhas)

    def contagem(nome, linhas, esperado=None, explicacao=""):
        return Conferencia(
            nome=nome,
            encontrado=len(linhas),
            esperado=esperado,
            explicacao=explicacao,
            divergentes=() if esperado is None or len(linhas) == esperado else identificadores(linhas),
        )

    def sem_falha(nome, linhas, esperado=None, explicacao=""):
        base = contagem(nome, linhas, esperado, explicacao)
        falhas = tuple(
            linha.external_id
            for linha in linhas
            if linha.desfecho == rve_importacao.DESFECHO_FALHA
        )
        if not falhas:
            return base
        return Conferencia(
            nome=base.nome,
            encontrado=base.encontrado,
            esperado=base.esperado,
            explicacao=base.explicacao,
            divergentes=tuple(dict.fromkeys(base.divergentes + falhas)),
        )

    # A hierarquia do AVCB: o projeto, as etapas penduradas nele e as entregas
    # penduradas nas etapas. É a única conferência que não é uma contagem.
    avcb_etapas = [linha for linha in resumo.detalhes if linha.familia == "AVCB-E"]
    avcb_entregas = [linha for linha in resumo.detalhes if linha.familia == "AVCB-T"]
    pais = {linha.external_id: linha.tipo_entidade for linha in resumo.detalhes}
    quebradas = tuple(
        linha.external_id
        for linha in avcb_etapas + avcb_entregas
        if not linha.parent or pais.get(linha.parent) not in ("projeto", "etapa_projeto")
    )

    return [
        contagem(
            "Demandas históricas sem duplicação",
            por_tipo["demanda"],
            77,
            "as demandas do acervo, vindas de RVE-HIST e RVE-N",
        ),
        contagem(
            "Candidatas do Lote 5 reconciliadas",
            por_tipo["demanda (candidata)"],
            7,
            "familia RVE-L5, preservadas como candidatas",
        ),
        contagem(
            "Cobranças e retornos",
            por_tipo["cobranca/retorno"],
            22,
            "familia RET",
        ),
        contagem(
            "Ativos e equipamentos",
            por_tipo["ativo/equipamento"],
            18,
            "familia EQ",
        ),
        contagem(
            "Subitens de garantia",
            por_tipo["subitem_garantia"],
            5,
            "familia PISC-G",
        ),
        sem_falha(
            "Ocorrências de segurança (GES) reconciliadas",
            por_familia["GES"],
            None,
            "familia GES, sem falha de escrita",
        ),
        sem_falha(
            "Planejamento 2027 separado",
            por_familia["PL27"],
            None,
            "familia PL27, que não entra no domínio nesta rodada",
        ),
        contagem(
            "Projetos identificados",
            por_tipo["projeto"],
            3,
            "PRJ e PRJ-AVCB",
        ),
        Conferencia(
            nome="AVCB preservado hierarquicamente",
            encontrado=len(avcb_etapas) + len(avcb_entregas),
            esperado=None,
            explicacao=(
                f"{len(avcb_etapas)} etapa(s) e {len(avcb_entregas)} entrega(s) "
                "penduradas em item superior da própria hierarquia"
            ),
            divergentes=quebradas,
        ),
    ]


# ---------------------------------------------------------------------------
# Os conflitos
# ---------------------------------------------------------------------------


def conflitos(resumo: ResumoDaImportacao) -> list[tuple[str, str, str, str]]:
    """O que impede a rodada de ser aceita, com o identificador que o explica."""
    por_id = {linha.external_id: linha for linha in resumo.detalhes}
    encontrados: list[tuple[str, str, str, str]] = []

    for identificador, motivo in resumo.falhas:
        linha = por_id.get(identificador)
        encontrados.append(
            (
                identificador,
                linha.familia if linha else "",
                linha.tipo_entidade if linha else "",
                motivo,
            )
        )

    # Uma etapa precisa de projeto; uma entrega precisa de etapa. Sem isso, a
    # linha não tem onde ser pendurada, e a rodada não pode avançar sozinha.
    esperado = {"etapa_projeto": "projeto", "entrega_projeto": "etapa_projeto"}
    for linha in resumo.detalhes:
        alvo = esperado.get(linha.tipo_entidade)
        if alvo is None or not linha.parent:
            continue
        superior = por_id.get(linha.parent)
        if superior is None:
            encontrados.append(
                (
                    linha.external_id,
                    linha.familia,
                    linha.tipo_entidade,
                    f"O item superior {linha.parent} não existe na matriz.",
                )
            )
        elif superior.tipo_entidade != alvo:
            encontrados.append(
                (
                    linha.external_id,
                    linha.familia,
                    linha.tipo_entidade,
                    f"O item superior {linha.parent} é {superior.tipo_entidade}, "
                    f"e não {alvo}.",
                )
            )
    return encontrados


# ---------------------------------------------------------------------------
# O relatório
# ---------------------------------------------------------------------------


def montar(resumo: ResumoDaImportacao, *, inventario=None) -> Relatorio:
    """Reúne conferências e conflitos de uma rodada.

    O inventário do banco é opcional porque só a carga definitiva relê o banco;
    a simulação não tem nada para reler.
    """
    return Relatorio(
        resumo=resumo,
        conferencias=conferir(resumo),
        conflitos=conflitos(resumo),
        inventario=inventario,
    )


def inventario_do_banco(resumo: ResumoDaImportacao) -> dict:
    """Relê o banco e confere que cada destino prometido pela rodada existe.

    A matriz diz o que devia virar; a rodada diz o que virou e guarda o destino
    alcançado em cada linha. Aqui o banco é consultado de novo: se um destino que
    a rodada prometeu não estiver lá, o item fica sem explicação — e isto é o que
    a reconciliação pós-carga precisa pegar.

    Devolve a contagem alcançada por entidade e os identificadores ausentes.
    """
    from collections import defaultdict

    from apps.cadastros.models import Equipamento
    from apps.core.constants import EntidadeImportacaoHistorica
    from apps.demandas.models import Demanda
    from apps.governanca.models import Projeto, ProjetoItem

    modelos = {
        EntidadeImportacaoHistorica.DEMANDA: Demanda,
        EntidadeImportacaoHistorica.PROJETO: Projeto,
        EntidadeImportacaoHistorica.PROJETO_ITEM: ProjetoItem,
        EntidadeImportacaoHistorica.EQUIPAMENTO: Equipamento,
    }

    prometidos: dict[str, set[str]] = defaultdict(set)
    for linha in resumo.detalhes:
        entidade = rve_importacao.ENTIDADE_POR_TIPO.get(linha.tipo_entidade)
        if entidade is None or not linha.destino:
            continue
        prometidos[entidade].add(linha.destino)

    por_entidade: dict[str, dict[str, int]] = {}
    ausentes: list[str] = []
    for entidade, identificadores in prometidos.items():
        modelo = modelos.get(entidade)
        if modelo is None:
            continue
        no_banco = {
            str(pk)
            for pk in modelo.objects.filter(pk__in=list(identificadores)).values_list(
                "pk", flat=True
            )
        }
        por_entidade[entidade] = {
            "esperados": len(identificadores),
            "no_banco": len(no_banco),
        }
        ausentes.extend(sorted(identificadores - no_banco))

    return {"por_entidade": por_entidade, "ausentes": ausentes}


def texto(relatorio: Relatorio) -> str:
    """O relatório em texto, na ordem em que é lido."""
    resumo = relatorio.resumo
    linhas: list[str] = []
    escrever = linhas.append

    titulo = "IMPORTAÇÃO DEFINITIVA RVE — RELATÓRIO"
    titulo += " DE DRY-RUN" if resumo.simulado else " DA CARGA EXECUTADA"
    escrever(titulo)
    escrever("=" * len(titulo))
    escrever(f"Condomínio: {resumo.condominio.nome} ({resumo.condominio.pk})")
    escrever(f"Matriz: {resumo.matriz}")
    escrever(f"Lote: {resumo.lote.pk if resumo.lote else 'não criado'}")
    escrever(f"Simulação: {'sim' if resumo.simulado else 'não'}")
    escrever(f"Linhas na matriz: {resumo.lidas}")
    escrever(f"Linhas selecionadas: {resumo.selecionadas}")
    escrever(
        f"Itens da fonte: {resumo.itens} "
        f"(novos {resumo.itens_novos}, já existentes {resumo.itens_existentes})"
    )
    escrever("")

    por_tipo = defaultdict(Counter)
    for linha in resumo.detalhes:
        por_tipo[linha.tipo_entidade][linha.desfecho] += 1

    colunas = [nome for _, nome in DESFECHOS]
    largura = max([len("tipo"), *(len(tipo) for tipo in por_tipo)])
    escrever("POR TIPO")
    escrever(f"{'tipo'.ljust(largura)}  {'encon.':>6}  " + "  ".join(colunas))
    for tipo in sorted(por_tipo):
        contagem = por_tipo[tipo]
        totais = "  ".join(str(contagem[nome]).rjust(len(nome)) for nome in colunas)
        escrever(f"{tipo.ljust(largura)}  {sum(contagem.values()):>6}  {totais}")
    escrever("")

    escrever("TOTAIS POR DESFECHO")
    for desfecho, nome in DESFECHOS:
        escrever(f"{nome}: {resumo.contar_desfecho(desfecho)}")
    escrever("")

    escrever("CONFERÊNCIAS OBRIGATÓRIAS")
    for item in relatorio.conferencias:
        marca = "OK " if item.confirmada else "DIVERGE"
        esperado = f" (esperado {item.esperado})" if item.esperado is not None else ""
        escrever(f"[{marca}] {item.nome}: {item.encontrado}{esperado}")
        if item.explicacao:
            escrever(f"        {item.explicacao}")
        if item.divergentes:
            escrever(f"        a explicar por ID: {', '.join(item.divergentes)}")
    escrever("")

    if relatorio.inventario is not None:
        escrever("INVENTÁRIO DO BANCO (destinos prometidos pela rodada)")
        por_entidade = relatorio.inventario.get("por_entidade", {})
        if por_entidade:
            for entidade in sorted(por_entidade):
                contagem = por_entidade[entidade]
                escrever(
                    f"{entidade}: {contagem['no_banco']} de "
                    f"{contagem['esperados']} no banco"
                )
        else:
            escrever("Nenhum destino de domínio foi prometido pela rodada.")
        ausentes = relatorio.inventario.get("ausentes", [])
        escrever(f"Destinos ausentes no banco: {len(ausentes)}")
        for identificador in ausentes:
            escrever(f"- {identificador}")
        escrever("")

    escrever("CONFLITOS")
    if relatorio.conflitos:
        for identificador, familia, tipo, motivo in relatorio.conflitos:
            escrever(f"- {identificador} [{familia}/{tipo}]: {motivo}")
    else:
        escrever("Nenhum.")
    escrever("")

    escrever(f"FALHAS DE ESCRITA: {len(resumo.falhas)}")
    for identificador, motivo in resumo.falhas:
        escrever(f"- {identificador}: {motivo}")
    return "\n".join(linhas) + "\n"


def escrever(relatorio: Relatorio, *, diretorio) -> dict[str, Path]:
    """Grava os três artefatos do relatório e devolve os caminhos escritos.

    Os nomes seguem a rodada: a simulação grava ``dry_run_*`` e a carga grava
    ``pos_importacao_*``, para que a segunda não escreva por cima da primeira.
    """
    diretorio = Path(diretorio)
    diretorio.mkdir(parents=True, exist_ok=True)

    nomes = _nomes_dos_artefatos(simulado=relatorio.resumo.simulado)

    caminho_resumo = diretorio / nomes["resumo"]
    caminho_resumo.write_text(texto(relatorio), encoding="utf-8")

    caminho_detalhes = diretorio / nomes["detalhes"]
    with caminho_detalhes.open("w", encoding="utf-8-sig", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=COLUNAS_DETALHES)
        escritor.writeheader()
        for linha in relatorio.resumo.detalhes:
            escritor.writerow(_detalhe(linha))

    caminho_conflitos = diretorio / nomes["conflitos"]
    with caminho_conflitos.open("w", encoding="utf-8-sig", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=COLUNAS_CONFLITOS)
        escritor.writeheader()
        for identificador, familia, tipo, motivo in relatorio.conflitos:
            escritor.writerow(
                {
                    "external_id": identificador,
                    "familia": familia,
                    "tipo_entidade": tipo,
                    "motivo": motivo,
                }
            )

    return {
        "resumo": caminho_resumo,
        "detalhes": caminho_detalhes,
        "conflitos": caminho_conflitos,
    }


def _detalhe(linha: DesfechoDaLinha) -> dict[str, str]:
    """Uma linha da matriz como ela aparece no CSV de detalhes."""
    return {
        "external_id": linha.external_id,
        "familia": linha.familia,
        "tipo_entidade": linha.tipo_entidade,
        "titulo": linha.titulo,
        "parent_external_id": linha.parent,
        "acao_importacao": linha.acao,
        "desfecho": linha.desfecho,
        "destino": linha.destino,
        "revisao_necessaria": "sim" if linha.revisao else "nao",
        "motivo": linha.motivo,
    }


__all__ = [
    "ARQUIVO_CONFLITOS",
    "ARQUIVO_CONFLITOS_CARGA",
    "ARQUIVO_DETALHES",
    "ARQUIVO_RECONCILIACAO",
    "ARQUIVO_RESUMO",
    "ARQUIVO_RESUMO_CARGA",
    "Conferencia",
    "Relatorio",
    "conferir",
    "conflitos",
    "escrever",
    "inventario_do_banco",
    "montar",
    "texto",
]
