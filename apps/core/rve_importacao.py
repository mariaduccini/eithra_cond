"""Importação definitiva dos dados reais do RVE a partir da matriz reconciliada.

O acervo do Condomínio Rio Vermelho Exclusive foi lido, reconciliado e resumido
em uma matriz de identidades — a saída das fases de consolidação. Esta camada
consome essa matriz, e só ela: re-derivar das planilhas e caixas originais seria
construir um segundo importador paralelo, com duas versões da mesma decisão.

O que a rodada faz, na ordem:

1. lê a reconciliação declarada, quando ela existe — o artefato que diz o que
   **já está materializado** no domínio, para que a carga não crie um segundo
   fato de negócio com outro identificador técnico;
2. lê a matriz de reconciliação, linha a linha;
3. registra cada linha como item da carga histórica — a identidade externa é o
   `external_id` da matriz, e reprocessar reencontra a mesma linha em vez de
   criar outra;
4. escreve o **núcleo operacional** que a matriz mandou criar: demandas,
   projeto/etapa/entrega e ativos/equipamentos. O restante entra como linhagem:
   o item da fonte fica registrado, com o vínculo e o seu desfecho;
5. relata o que foi feito, sem inventar dado nenhum que a fonte não sustente.

Três decisões atravessam tudo o que segue:

* **idempotência por identidade externa** — a chave é
  `condominio + origem + conta declarada + external_id`, e o vínculo guarda o
  destino já criado. Uma segunda execução não cria nada de novo;
* **nada de dado inventado** — prioridade, responsável e prazo só entram quando
  a matriz os declara canônicos; instituição não vira usuário, e texto de papel
  não vira responsável;
* **a linhagem é a memória** — mesmo o que não vira registro de domínio fica
  registrado como item da fonte, para que a decisão de não criar seja auditável.

A reconciliação declarada é o que separa a identidade da fonte da identidade do
fato de negócio: só evidência escrita — vínculo já existente, identificador
documental comprovado, decisão humana validada — muda o CREATE em
reconhecimento, nunca semelhança de título.

A execução definitiva exige quem responda pela carga. A simulação lê, decide e
relata sem gravar nada — nem lote, nem item, nem vínculo, nem domínio.
"""
from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from django.db import transaction

from apps.core.constants import (
    EntidadeImportacaoHistorica,
    EstadoProcessamentoImportacao,
    OrigemImportacaoHistorica,
    ResultadoImportacaoHistorica,
)
from apps.core.models import (
    ItemImportacaoHistorica,
    LoteImportacaoHistorica,
    VinculoImportacaoHistorica,
)

# A conta declarada desta carga: faz parte da identidade do item, e por isso é
# fixa. Trocar o nome do arquivo da matriz não pode duplicar linhagem.
CONTA_ORIGEM = "RVE"

LIMITE_DO_TITULO = 200
LIMITE_DO_NOME = 255

# Como cada linha terminou. É o vocabulário do relatório da rodada, e é o mesmo
# em simulação e em execução: na simulação, "CREATE" é o que a rodada faria.
DESFECHO_CRIADO = "CREATE"
DESFECHO_ATUALIZADO = "UPDATE"
DESFECHO_CONSOLIDADO = "CONSOLIDATE"
DESFECHO_PENDENTE = "PENDENTE"
DESFECHO_HISTORICO = "HISTORY"
DESFECHO_PLANEJAMENTO = "PLANNING_ONLY"
DESFECHO_REVISAO = "REVIEW"
DESFECHO_IGNORADO = "SKIP"
DESFECHO_FALHA = "ERROR"

# Os desfechos da reconciliação declarada: quando o artefato já sabe que o fato
# de negócio existe no domínio, a rodada não cria outro — ela reconhece o que
# já está lá. É o que impede a carga histórica de duplicar o que uma rodada
# anterior já materializou com outro identificador técnico.
DESFECHO_VINCULADO_EXISTENTE = "VINCULAR_EXISTENTE"
DESFECHO_VINCULADO_JORNADA = "VINCULAR_JORNADA"
DESFECHO_REVISAR = "REVISAR"

# O vocabulário do artefato de reconciliação. A decisão vem declarada em dados,
# com a evidência que a sustenta — nunca deduzida de título parecido, score
# fuzzy ou equipamento igual. A identidade da fonte não é a identidade do fato
# de negócio; quem as liga é a evidência, não a semelhança.
DECISAO_VINCULAR_EXISTENTE = "VINCULAR_EXISTENTE"
DECISAO_VINCULAR_JORNADA = "VINCULAR_JORNADA"
DECISAO_CRIAR = "CREATE"
DECISAO_REVISAR = "REVISAR"

# As decisões que tiram a linha do fluxo normal da matriz: em vez de seguir a
# ação declarada (criar, vincular ou preservar), a rodada obedece ao artefato.
DECISOES_QUE_MUDAM_O_DESTINO = frozenset(
    {DECISAO_VINCULAR_EXISTENTE, DECISAO_VINCULAR_JORNADA, DECISAO_REVISAR}
)

# O nome do artefato de reconciliação, quando ele mora ao lado da matriz.
ARQUIVO_RECONCILIACAO = "reconciliacao_rve_decisoes.csv"

# A ação da matriz que não escreve nada por decisão dela própria: a linha vira
# linhagem, e o desfecho diz qual foi a decisão.
_DESFECHO_POR_ACAO = {
    "DO_NOT_CREATE": DESFECHO_IGNORADO,
    "PRESERVE_AS_HISTORY": DESFECHO_HISTORICO,
    "PLANNING_ONLY": DESFECHO_PLANEJAMENTO,
    "REVIEW": DESFECHO_REVISAO,
}


class ErroDaImportacaoRVE(Exception):
    """Uma inconsistência da matriz que impede gravar com segurança."""


# ---------------------------------------------------------------------------
# Vocabulário da matriz
# ---------------------------------------------------------------------------

# De que entidade do Eithra cada tipo da matriz fala. `None` significa que a
# linha é metadado de rastreabilidade da própria fonte — não há destino de
# domínio, e portanto não há vínculo a registrar.
ENTIDADE_POR_TIPO = {
    "demanda": EntidadeImportacaoHistorica.DEMANDA,
    "demanda (verificacao AVCB)": EntidadeImportacaoHistorica.DEMANDA,
    "demanda (candidata)": EntidadeImportacaoHistorica.DEMANDA,
    "atividade AVCB": EntidadeImportacaoHistorica.DEMANDA,
    "atividade/pendencia": EntidadeImportacaoHistorica.DEMANDA,
    "pendencia": EntidadeImportacaoHistorica.DEMANDA,
    "atendimento": EntidadeImportacaoHistorica.DEMANDA,
    "ocorrencia/seguranca": EntidadeImportacaoHistorica.DEMANDA,
    "reserva/area comum": EntidadeImportacaoHistorica.DEMANDA,
    "consumo individualizado": EntidadeImportacaoHistorica.DEMANDA,
    "cobranca/retorno": EntidadeImportacaoHistorica.DEMANDA,
    "inadimplencia/acordo": EntidadeImportacaoHistorica.DEMANDA,
    "planejamento": EntidadeImportacaoHistorica.DEMANDA,
    "projeto": EntidadeImportacaoHistorica.PROJETO,
    "etapa_projeto": EntidadeImportacaoHistorica.PROJETO_ITEM,
    "entrega_projeto": EntidadeImportacaoHistorica.PROJETO_ITEM,
    "ativo/equipamento": EntidadeImportacaoHistorica.EQUIPAMENTO,
    "documento": EntidadeImportacaoHistorica.DOCUMENTO_OPERACIONAL,
    "documento financeiro/proposta": EntidadeImportacaoHistorica.DOCUMENTO_OPERACIONAL,
    "financeiro_projeto": EntidadeImportacaoHistorica.DOCUMENTO_OPERACIONAL,
    "conta bancaria/aplicacao": EntidadeImportacaoHistorica.DOCUMENTO_OPERACIONAL,
    "contrato/obrigacao": EntidadeImportacaoHistorica.CONTRATO_FORNECEDOR,
    "cadastro/obrigacao": EntidadeImportacaoHistorica.CONTRATO_FORNECEDOR,
    "subitem_garantia": EntidadeImportacaoHistorica.GARANTIA,
    "linhagem": None,
}

# Os tipos que esta primeira rodada escreve no domínio. O resto da matriz é
# preservado como linhagem e revisão — a decisão de escopo, não um esquecimento.
TIPOS_DE_DEMANDA = frozenset(
    {"demanda", "demanda (verificacao AVCB)", "atividade AVCB", "ocorrencia/seguranca"}
)
TIPOS_ESCRITOS = TIPOS_DE_DEMANDA | {
    "projeto",
    "etapa_projeto",
    "entrega_projeto",
    "ativo/equipamento",
}

# A ordem em que os tipos são processados: o pai antes do filho, e o que cria
# antes do que apenas vincula. Sem isso, uma entrega procuraria a etapa que
# ainda não existe.
_ORDEM_DE_TIPOS = (
    "projeto",
    "etapa_projeto",
    "entrega_projeto",
    "ativo/equipamento",
    "demanda",
    "demanda (verificacao AVCB)",
    "atividade AVCB",
    "ocorrencia/seguranca",
    "atendimento",
    "atividade/pendencia",
    "pendencia",
    "demanda (candidata)",
    "cobranca/retorno",
    "inadimplencia/acordo",
    "reserva/area comum",
    "consumo individualizado",
    "planejamento",
    "documento",
    "documento financeiro/proposta",
    "financeiro_projeto",
    "conta bancaria/aplicacao",
    "contrato/obrigacao",
    "cadastro/obrigacao",
    "subitem_garantia",
    "linhagem",
)
_RANK = {tipo: posicao for posicao, tipo in enumerate(_ORDEM_DE_TIPOS)}

# Tradução do que a matriz propõe para o vocabulário do domínio. A chave é
# sempre normalizada em minúsculas; o que não está no mapa fica de fora, em vez
# de virar um palpite.
_PRIORIDADE = {
    "baixa": "baixa",
    "média": "normal",
    "media": "normal",
    "alta": "alta",
    "crítica": "urgente",
    "critica": "urgente",
}
_STATUS_DEMANDA = {
    "aberta": "aberta",
    "em andamento": "em_andamento",
    "aguardando": "aguardando",
    "concluída": "concluida",
    "concluida": "concluida",
    "cancelada": "cancelada",
}
_STATUS_PROJETO = {
    "em estudo": "em_estudo",
    "em andamento": "em_andamento",
    "aguardando": "aguardando",
    "concluído": "concluido",
    "concluida": "concluida",
    "concluido": "concluido",
    "cancelado": "cancelado",
}


def _texto(valor: str | None) -> str:
    """Colapsa espaços repetidos e devolve string vazia quando não há conteúdo."""
    return " ".join((valor or "").split())


def _chave(valor: str | None) -> str:
    """A forma comparável de um rótulo da matriz."""
    return _texto(valor).casefold()


# ---------------------------------------------------------------------------
# A matriz
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LinhaDaMatriz:
    """Uma identidade consolidada da matriz de reconciliação."""

    external_id: str = ""
    familia: str = ""
    tipo_entidade: str = ""
    titulo: str = ""
    parent_external_id: str = ""
    status_fonte: str = ""
    status_canonico: str = ""
    prioridade_canonica: str = ""
    responsavel_canonico: str = ""
    local: str = ""
    ativo: str = ""
    fornecedor: str = ""
    evidencia: str = ""
    fonte_primaria: str = ""
    acao: str = ""
    motivo: str = ""
    revisao_necessaria: bool = False
    linha_fonte_primaria: str = ""

    @classmethod
    def de_dicionario(cls, registro: dict) -> "LinhaDaMatriz":
        """Lê uma linha do CSV, tolerando coluna ausente ou vazia."""
        return cls(
            external_id=_texto(registro.get("external_id")),
            familia=_texto(registro.get("familia")),
            tipo_entidade=_texto(registro.get("tipo_entidade")),
            titulo=_texto(registro.get("titulo_canonico")),
            parent_external_id=_texto(registro.get("parent_external_id")),
            status_fonte=_texto(registro.get("status_fonte")),
            status_canonico=_texto(registro.get("status_canonico_proposto")),
            prioridade_canonica=_texto(registro.get("prioridade_canonica")),
            responsavel_canonico=_texto(registro.get("responsavel_canonico")),
            local=_texto(registro.get("local")),
            ativo=_texto(registro.get("ativo")),
            fornecedor=_texto(registro.get("fornecedor")),
            evidencia=_texto(registro.get("evidencia")),
            fonte_primaria=_texto(registro.get("fonte_primaria")),
            acao=_texto(registro.get("acao_importacao")),
            motivo=_texto(registro.get("motivo")),
            revisao_necessaria=_chave(registro.get("revisao_necessaria")) == "sim",
            linha_fonte_primaria=_texto(registro.get("linha_fonte_primaria")),
        )

    @property
    def entidade(self) -> str | None:
        """A que entidade do Eithra esta linha corresponde, quando houver."""
        return ENTIDADE_POR_TIPO.get(self.tipo_entidade)

    @property
    def chave_de_titulo(self) -> str:
        """O título como o domínio o guardará, para casar vínculos por título."""
        return self.titulo[:LIMITE_DO_TITULO].casefold()


def ler_matriz(caminho: str | Path) -> list[LinhaDaMatriz]:
    """Lê a matriz de reconciliação inteira, na ordem em que ela está escrita."""
    caminho = Path(caminho)
    with caminho.open(encoding="utf-8-sig", newline="") as arquivo:
        return [LinhaDaMatriz.de_dicionario(registro) for registro in csv.DictReader(arquivo)]


# ---------------------------------------------------------------------------
# A reconciliação declarada
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisaoDeReconciliacao:
    """O que já se sabe, por evidência declarada, sobre uma identidade da fonte.

    A matriz diz o que a fonte é; este artefato diz o que já existe no domínio.
    Ele existe porque a identidade técnica da fonte não é a identidade do fato
    de negócio: parte do acervo já foi materializada por outra rodada, com outro
    identificador, e o importador precisa reconhecê-la antes de decidir criar.

    O que a decisão carrega é a evidência, não um palpite — vínculo já existente,
    identificador documental comprovado, reconciliação declarada à mão. Sem
    evidência, a linha não entra aqui.
    """

    external_id: str = ""
    entidade_destino: str = ""
    destino_id: str = ""
    decisao: str = ""
    observacao: str = ""

    @classmethod
    def de_dicionario(cls, registro: dict) -> "DecisaoDeReconciliacao":
        """Lê uma linha do artefato, tolerando coluna ausente ou vazia."""
        return cls(
            external_id=_texto(registro.get("external_id_historico")),
            entidade_destino=_chave(registro.get("entidade_destino")),
            destino_id=_texto(registro.get("destino_id")),
            decisao=_texto(registro.get("decisao")).upper(),
            observacao=_texto(registro.get("observacao")),
        )

    @property
    def muda_o_destino(self) -> bool:
        """A decisão substitui a ação da matriz, em vez de deixá-la seguir."""
        return self.decisao in DECISOES_QUE_MUDAM_O_DESTINO


def ler_reconciliacao(caminho: str | Path) -> dict[str, DecisaoDeReconciliacao]:
    """Lê o artefato de reconciliação, indexado pelo identificador da fonte."""
    caminho = Path(caminho)
    with caminho.open(encoding="utf-8-sig", newline="") as arquivo:
        decisoes = [
            DecisaoDeReconciliacao.de_dicionario(registro)
            for registro in csv.DictReader(arquivo)
        ]
    return {decisao.external_id: decisao for decisao in decisoes if decisao.external_id}


def _carregar_reconciliacao(caminho) -> dict[str, DecisaoDeReconciliacao]:
    """Carrega a reconciliação declarada, quando ela foi informada.

    Ausência de artefato é ausência de decisão — a rodada segue a matriz. Mas um
    caminho informado que não existe é erro, não omissão: silenciar isso faria a
    carga esquecer, sem avisar, que havia equivalência comprovada a respeitar.
    """
    if caminho in (None, ""):
        return {}
    caminho = Path(caminho)
    if not caminho.is_file():
        raise ErroDaImportacaoRVE(
            f"A reconciliação declarada não foi encontrada: {caminho}"
        )
    return ler_reconciliacao(caminho)


@dataclass(frozen=True)
class DesfechoDaLinha:
    """O que a rodada decidiu e fez com uma linha da matriz.

    Guarda o suficiente para o relatório ser conferido linha a linha sem reabrir
    a fonte: de onde veio, o que a matriz mandava fazer, o que a rodada fez e a
    que registro do domínio isso corresponde.
    """

    external_id: str
    familia: str
    tipo_entidade: str
    titulo: str
    parent: str
    acao: str
    desfecho: str
    destino: str
    motivo: str
    revisao: bool


def _desfecho_da_linha(linha: LinhaDaMatriz, *, desfecho: str, destino) -> DesfechoDaLinha:
    """Monta o registro do desfecho de uma linha, já pronto para o relatório."""
    return DesfechoDaLinha(
        external_id=linha.external_id,
        familia=linha.familia,
        tipo_entidade=linha.tipo_entidade,
        titulo=linha.titulo,
        parent=linha.parent_external_id,
        acao=linha.acao,
        desfecho=desfecho,
        destino="" if destino in (None, "") else str(destino),
        motivo=linha.motivo,
        revisao=linha.revisao_necessaria,
    )


# ---------------------------------------------------------------------------
# O relato da rodada
# ---------------------------------------------------------------------------


@dataclass
class ResumoDaImportacao:
    """O que a rodada fez — ou apenas decidiu, na simulação."""

    condominio: object = None
    matriz: Path | None = None
    lote: object | None = None
    simulado: bool = True
    lidas: int = 0
    selecionadas: int = 0
    itens: int = 0
    itens_novos: int = 0
    itens_existentes: int = 0
    criados: dict[str, int] = field(default_factory=dict)
    vinculados: int = 0
    pulados: int = 0
    ignorados: int = 0
    pendentes: int = 0
    revisao: int = 0
    falhas: list[tuple[str, str]] = field(default_factory=list)
    detalhes: list[DesfechoDaLinha] = field(default_factory=list)

    @property
    def total_criado(self) -> int:
        return sum(self.criados.values())

    def contar_criado(self, tipo: str) -> None:
        self.criados[tipo] = self.criados.get(tipo, 0) + 1

    def contar_desfecho(self, desfecho: str) -> int:
        """Quantas linhas terminaram de uma certa maneira."""
        return sum(1 for linha in self.detalhes if linha.desfecho == desfecho)

    def ids_com_desfecho(self, desfecho: str) -> list[str]:
        """Os identificadores das linhas que terminaram de uma certa maneira."""
        return [linha.external_id for linha in self.detalhes if linha.desfecho == desfecho]


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------


def importar(
    *,
    condominio,
    matriz: str | Path,
    usuario=None,
    simulado: bool = True,
    ids=None,
    limite: int | None = None,
    reconciliacao: str | Path | None = None,
) -> ResumoDaImportacao:
    """Lê a matriz, registra a linhagem e escreve o núcleo operacional.

    ``usuario`` é quem responde pela carga e assina o que for criado. Na
    simulação ele não é exigido, porque nada é gravado.

    ``reconciliacao`` é o artefato que declara o que já existe no domínio. Ele é
    lido antes de qualquer escrita: uma equivalência comprovada nunca chega a
    virar CREATE, e um artefato informado que não existe interrompe a rodada
    antes que ela grave qualquer coisa.
    """
    matriz = Path(matriz)
    decisoes = _carregar_reconciliacao(reconciliacao)
    linhas = ler_matriz(matriz)
    selecionadas = _selecionar(linhas, ids=ids, limite=limite)

    resumo = ResumoDaImportacao(
        condominio=condominio,
        matriz=matriz,
        simulado=simulado,
        lidas=len(linhas),
        selecionadas=len(selecionadas),
    )

    if not simulado:
        resumo.lote = LoteImportacaoHistorica.objects.create(
            condominio=condominio,
            origem=OrigemImportacaoHistorica.IMPORTACAO,
            conta_origem=CONTA_ORIGEM,
            nome_original=matriz.name,
            hash_fonte=_hash_do_arquivo(matriz),
            observacao="Importação definitiva RVE a partir da matriz de reconciliação.",
        )

    itens = _registrar_itens(
        selecionadas,
        lote=resumo.lote,
        condominio=condominio,
        usuario=usuario,
        resumo=resumo,
        simulado=simulado,
    )

    escritos: dict[str, object] = {}
    for linha in _em_ordem(selecionadas):
        _processar(
            linha,
            item=itens[linha.external_id],
            escritos=escritos,
            condominio=condominio,
            usuario=usuario,
            resumo=resumo,
            simulado=simulado,
            decisoes=decisoes,
        )

    return resumo


def _selecionar(linhas, *, ids=None, limite=None) -> list[LinhaDaMatriz]:
    """Aplica os recortes pedidos, sem mudar a ordem da matriz."""
    if ids:
        desejados = {valor.strip() for valor in ids if valor.strip()}
        linhas = [linha for linha in linhas if linha.external_id in desejados]
    if limite is not None:
        linhas = linhas[:limite]
    return list(linhas)


def _em_ordem(linhas) -> list[LinhaDaMatriz]:
    """Ordena por fase: o que cria antes do que vincula, o pai antes do filho."""
    return sorted(
        linhas,
        key=lambda linha: (
            _RANK.get(linha.tipo_entidade, len(_ORDEM_DE_TIPOS)),
            0 if linha.acao == "CREATE" else 1,
            linha.external_id,
        ),
    )


def _registrar_itens(
    linhas, *, lote, condominio, usuario, resumo: ResumoDaImportacao, simulado: bool
) -> dict[str, object]:
    """Registra cada linha como item da fonte — ou reencontra o já registrado."""
    itens: dict[str, object] = {}
    for linha in linhas:
        resumo.itens += 1
        if simulado:
            item = _item_existente(linha, condominio=condominio)
            itens[linha.external_id] = item
            if item is None:
                resumo.itens_novos += 1
            else:
                resumo.itens_existentes += 1
            continue

        item, criado = ItemImportacaoHistorica.objects.registrar_item(
            lote=lote,
            identificador_externo=linha.external_id,
            conta_origem=CONTA_ORIGEM,
            nome_original=linha.fonte_primaria[:LIMITE_DO_NOME],
            assunto=linha.titulo[:LIMITE_DO_NOME],
            criado_por=usuario,
        )
        itens[linha.external_id] = item
        if criado:
            resumo.itens_novos += 1
        else:
            resumo.itens_existentes += 1
    return itens


def _processar(
    linha: LinhaDaMatriz,
    *,
    item,
    escritos: dict,
    condominio,
    usuario,
    resumo: ResumoDaImportacao,
    simulado: bool,
    decisoes: dict | None = None,
) -> None:
    """Decide e executa o destino de uma linha, sem deixar uma falha parar o resto."""
    entidade = linha.entidade
    # A revisão é da linha, não do desfecho: a matriz já disse que esta
    # identidade precisa de olhar humano, e isso vale para qualquer destino.
    if linha.revisao_necessaria:
        resumo.revisao += 1

    desfecho, destino = DESFECHO_IGNORADO, ""
    try:
        decisao = (decisoes or {}).get(linha.external_id)
        if decisao is not None and decisao.muda_o_destino:
            # A reconciliação declarada vem antes de tudo: reconhecer o que já
            # existe não pode depender de a matriz ter pedido CREATE.
            desfecho, destino = _aplicar_reconciliacao(
                linha,
                decisao=decisao,
                item=item,
                entidade=entidade,
                escritos=escritos,
                usuario=usuario,
                resumo=resumo,
                simulado=simulado,
            )
        elif entidade is None:
            # Metadado de rastreabilidade da própria fonte: nada de domínio.
            resumo.ignorados += 1
        elif linha.acao == "CREATE" and linha.tipo_entidade in TIPOS_ESCRITOS:
            desfecho, destino = _escrever(
                linha,
                item=item,
                escritos=escritos,
                condominio=condominio,
                usuario=usuario,
                resumo=resumo,
                simulado=simulado,
            )
        elif linha.acao == "LINK":
            desfecho, destino = _consolidar(
                linha,
                item=item,
                entidade=entidade,
                condominio=condominio,
                usuario=usuario,
                resumo=resumo,
                simulado=simulado,
            )
        else:
            desfecho = _DESFECHO_POR_ACAO.get(linha.acao, DESFECHO_IGNORADO)
            _registrar_vinculo(
                item,
                linha,
                entidade,
                resultado=ResultadoImportacaoHistorica.IGNORADO,
                revisao=linha.revisao_necessaria,
                usuario=usuario,
                simulado=simulado,
            )
            resumo.ignorados += 1
    except ErroDaImportacaoRVE as erro:
        desfecho = DESFECHO_FALHA
        resumo.falhas.append((linha.external_id, str(erro)))
    except Exception as erro:  # noqa: BLE001 - a rodada relata, não derruba tudo
        desfecho = DESFECHO_FALHA
        resumo.falhas.append((linha.external_id, type(erro).__name__))

    resumo.detalhes.append(
        _desfecho_da_linha(linha, desfecho=desfecho, destino=destino)
    )

    if not simulado and item is not None:
        _processar_item(item)


def _aplicar_reconciliacao(
    linha: LinhaDaMatriz,
    *,
    decisao: DecisaoDeReconciliacao,
    item,
    entidade,
    escritos: dict,
    usuario,
    resumo: ResumoDaImportacao,
    simulado: bool,
) -> tuple[str, str]:
    """Obedece à decisão já declarada, em vez de criar um segundo fato de negócio.

    A equivalência não é deduzida aqui — ela vem escrita no artefato, com a
    evidência que a sustenta. Esta função só a respeita:

    * ``VINCULAR_EXISTENTE`` aponta para o registro que já existe e não cria;
    * ``VINCULAR_JORNADA`` registra que a linha pertence à mesma jornada, sem
      fundir nem excluir nada — o vínculo fica como revisão documentada;
    * ``REVISAR`` não cria e deixa a linha para decisão humana.

    Nenhum dos três é destrutivo: a rodada nunca apaga nem mescla o domínio
    atual. O pior caso é não criar.
    """
    entidade_declarada = decisao.entidade_destino or entidade
    if (
        decisao.entidade_destino
        and entidade is not None
        and decisao.entidade_destino != entidade
    ):
        raise ErroDaImportacaoRVE(
            f"A reconciliação de {linha.external_id} aponta para "
            f"{decisao.entidade_destino}, mas a matriz diz {entidade}."
        )

    if decisao.decisao == DECISAO_REVISAR:
        _registrar_vinculo(
            item,
            linha,
            entidade_declarada,
            resultado=ResultadoImportacaoHistorica.PENDENTE,
            revisao=True,
            usuario=usuario,
            simulado=simulado,
            observacao=decisao.observacao or linha.motivo,
        )
        return DESFECHO_REVISAR, ""

    if decisao.decisao == DECISAO_VINCULAR_JORNADA:
        _registrar_vinculo(
            item,
            linha,
            entidade_declarada,
            resultado=ResultadoImportacaoHistorica.PENDENTE,
            revisao=True,
            usuario=usuario,
            simulado=simulado,
            observacao=decisao.observacao
            or "Jornada relacionada: pertence ao mesmo problema, sem fusão automática.",
        )
        return DESFECHO_VINCULADO_JORNADA, ""

    # VINCULAR_EXISTENTE — a evidência aponta para um registro que já existe.
    if not decisao.destino_id:
        raise ErroDaImportacaoRVE(
            f"A reconciliação de {linha.external_id} diz VINCULAR_EXISTENTE "
            "sem declarar o destino."
        )
    if not _destino_do_modelo_existe(entidade_declarada, decisao.destino_id):
        raise ErroDaImportacaoRVE(
            f"O destino declarado para {linha.external_id} "
            f"({decisao.destino_id}) não existe no domínio."
        )

    escritos[linha.external_id] = decisao.destino_id
    _registrar_vinculo(
        item,
        linha,
        entidade_declarada,
        destino_id=decisao.destino_id,
        resultado=ResultadoImportacaoHistorica.CRIADO,
        revisao=linha.revisao_necessaria,
        usuario=usuario,
        simulado=simulado,
        observacao=decisao.observacao or linha.motivo,
    )
    return DESFECHO_VINCULADO_EXISTENTE, decisao.destino_id


def _escrever(
    linha: LinhaDaMatriz,
    *,
    item,
    escritos: dict,
    condominio,
    usuario,
    resumo: ResumoDaImportacao,
    simulado: bool,
) -> tuple[str, str]:
    """Escreve o registro de domínio, ou reconhece que ele já existe.

    Devolve o desfecho da linha e o registro de domínio alcançado, quando houver.
    """
    entidade = linha.entidade
    existente = _destino_existente(item, entidade, linha.external_id)
    if existente is not None:
        escritos[linha.external_id] = existente
        resumo.pulados += 1
        _registrar_vinculo(
            item,
            linha,
            entidade,
            destino_id=existente,
            resultado=ResultadoImportacaoHistorica.ATUALIZADO,
            revisao=linha.revisao_necessaria,
            usuario=usuario,
            simulado=simulado,
        )
        return DESFECHO_ATUALIZADO, str(existente)

    if simulado:
        resumo.contar_criado(linha.tipo_entidade)
        return DESFECHO_CRIADO, ""

    with transaction.atomic():
        registro = _criar_registro(
            linha, escritos=escritos, condominio=condominio, usuario=usuario
        )
        _registrar_vinculo(
            item,
            linha,
            entidade,
            destino_id=registro.pk,
            resultado=ResultadoImportacaoHistorica.CRIADO,
            revisao=linha.revisao_necessaria,
            usuario=usuario,
            simulado=simulado,
        )

    escritos[linha.external_id] = registro.pk
    resumo.contar_criado(linha.tipo_entidade)
    return DESFECHO_CRIADO, str(registro.pk)


def _consolidar(
    linha: LinhaDaMatriz,
    *,
    item,
    entidade,
    condominio,
    usuario,
    resumo: ResumoDaImportacao,
    simulado: bool,
) -> tuple[str, str]:
    """Vincula a linha a um registro que já existe, quando o título o identifica.

    "Consolidar por título" é a decisão desta rodada para as linhas que a fonte
    manda vincular e não criar: quando o título casa com uma demanda do
    condomínio, o vínculo aponta para ela. Sem casar, fica pendente de revisão —
    nunca se cria uma segunda demanda para "resolver" o vínculo.
    """
    if entidade != EntidadeImportacaoHistorica.DEMANDA:
        _registrar_vinculo(
            item,
            linha,
            entidade,
            resultado=ResultadoImportacaoHistorica.PENDENTE,
            revisao=True,
            usuario=usuario,
            simulado=simulado,
        )
        resumo.pendentes += 1
        return DESFECHO_PENDENTE, ""

    demanda = _demanda_por_titulo(linha, condominio=condominio)
    if demanda is None:
        _registrar_vinculo(
            item,
            linha,
            entidade,
            resultado=ResultadoImportacaoHistorica.PENDENTE,
            revisao=True,
            usuario=usuario,
            simulado=simulado,
        )
        resumo.pendentes += 1
        return DESFECHO_PENDENTE, ""

    if not simulado:
        _registrar_vinculo(
            item,
            linha,
            entidade,
            destino_id=demanda.pk,
            resultado=ResultadoImportacaoHistorica.CRIADO,
            revisao=False,
            usuario=usuario,
            simulado=simulado,
        )
    resumo.vinculados += 1
    return DESFECHO_CONSOLIDADO, str(demanda.pk)


# ---------------------------------------------------------------------------
# Escrita no domínio
# ---------------------------------------------------------------------------


def _criar_registro(linha: LinhaDaMatriz, *, escritos, condominio, usuario):
    """Cria o registro de domínio do tipo da linha, pelo service do próprio domínio."""
    tipo = linha.tipo_entidade
    if tipo == "projeto":
        return _criar_projeto(linha, condominio=condominio, usuario=usuario)
    if tipo in ("etapa_projeto", "entrega_projeto"):
        return _criar_item_de_projeto(linha, escritos=escritos, condominio=condominio)
    if tipo == "ativo/equipamento":
        return _criar_equipamento(linha, condominio=condominio, usuario=usuario)
    return _criar_demanda(
        linha, escritos=escritos, condominio=condominio, usuario=usuario
    )


def _criar_projeto(linha: LinhaDaMatriz, *, condominio, usuario):
    """Registra o projeto com o que a matriz declara, sem inventar prazo."""
    from apps.governanca.models import Projeto

    return Projeto.objects.create(
        condominio=condominio,
        nome=linha.titulo[:LIMITE_DO_TITULO],
        identificacao=linha.external_id,
        status=_status_projeto(linha.status_canonico),
        observacao=_observacao_da_linha(linha),
        criado_por=usuario,
    )


def _criar_item_de_projeto(linha: LinhaDaMatriz, *, escritos, condominio):
    """Registra a etapa sob o projeto, ou a entrega sob a etapa."""
    from apps.governanca.constants import TipoItemProjeto
    from apps.governanca.models import Projeto, ProjetoItem

    if linha.tipo_entidade == "etapa_projeto":
        projeto = _resolver(
            linha.parent_external_id,
            EntidadeImportacaoHistorica.PROJETO,
            modelo=Projeto,
            escritos=escritos,
            condominio=condominio,
        )
        parent = None
        tipo = TipoItemProjeto.ETAPA
    else:
        parent = _resolver(
            linha.parent_external_id,
            EntidadeImportacaoHistorica.PROJETO_ITEM,
            modelo=ProjetoItem,
            escritos=escritos,
            condominio=condominio,
        )
        projeto = parent.projeto if parent is not None else None
        tipo = TipoItemProjeto.ENTREGA

    if projeto is None:
        raise ErroDaImportacaoRVE(
            f"Sem o item superior ({linha.parent_external_id}) não há onde "
            f"pendurar {linha.external_id}."
        )

    return ProjetoItem.objects.create(
        projeto=projeto,
        parent=parent,
        tipo=tipo,
        nome=linha.titulo[:LIMITE_DO_TITULO],
        status=_status_projeto(linha.status_canonico),
        ordem=_ordem(linha.external_id),
        observacao=_observacao_da_linha(linha),
    )


def _criar_equipamento(linha: LinhaDaMatriz, *, condominio, usuario):
    """Registra o ativo pelo cadastro do condomínio, uma única vez."""
    from apps.cadastros.services import criar_equipamento

    nome = (linha.ativo or linha.titulo)[:150]
    return criar_equipamento(
        condominio=condominio,
        criado_por=usuario,
        dados={"nome": nome, "observacao": _observacao_da_linha(linha)},
    )


def _criar_demanda(linha: LinhaDaMatriz, *, escritos, condominio, usuario):
    """Registra a demanda pelo service do domínio — nunca por atribuição direta.

    Só entra o que a fonte sustenta: título, descrição factual, prioridade
    canônica e situação declarada. Responsável e prazo não são escolhidos aqui —
    papel institucional não vira pessoa, e prazo não se inventa.
    """
    from apps.demandas.constants import OrigemDemanda, StatusDemanda
    from apps.demandas.services import alterar_status, criar_demanda

    demanda = criar_demanda(
        condominio=condominio,
        criado_por=usuario,
        titulo=linha.titulo[:LIMITE_DO_TITULO],
        descricao=_descricao_da_linha(linha),
        origem=OrigemDemanda.SISTEMA,
        prioridade=_prioridade(linha.prioridade_canonica),
    )

    status = _status_demanda(linha.status_canonico)
    if status and status != StatusDemanda.ABERTA:
        alterar_status(demanda=demanda, usuario=usuario, novo_status=status)

    _vincular_ao_projeto(demanda, linha, escritos=escritos, condominio=condominio)
    return demanda


def _vincular_ao_projeto(demanda, linha: LinhaDaMatriz, *, escritos, condominio) -> None:
    """Liga a demanda ao projeto quando o item superior da fonte é um projeto."""
    from apps.governanca.models import Projeto, ProjetoDemanda

    if not linha.parent_external_id:
        return
    projeto = _resolver(
        linha.parent_external_id,
        EntidadeImportacaoHistorica.PROJETO,
        modelo=Projeto,
        escritos=escritos,
        condominio=condominio,
    )
    if projeto is None:
        return
    ProjetoDemanda.objects.get_or_create(
        projeto=projeto, demanda=demanda, defaults={"observacao": linha.external_id[:120]}
    )


# ---------------------------------------------------------------------------
# Identidade, vínculo e apoio
# ---------------------------------------------------------------------------


def _item_existente(linha: LinhaDaMatriz, *, condominio):
    """O item da fonte já registrado para esta linha, quando existir."""
    return (
        ItemImportacaoHistorica.objects.filter(
            condominio=condominio,
            origem=OrigemImportacaoHistorica.IMPORTACAO,
            conta_origem=CONTA_ORIGEM,
            identificador_externo=linha.external_id,
        )
        .first()
    )


def _destino_existente(item, entidade, identificador):
    """O destino já criado para este item — é o que torna a segunda rodada inócua."""
    if item is None:
        return None
    vinculo = (
        VinculoImportacaoHistorica.objects.filter(
            item=item, entidade_destino=entidade, identificador_destino=identificador
        )
        .exclude(destino_id=None)
        .first()
    )
    if vinculo is None:
        return None

    if not _destino_do_modelo_existe(entidade, vinculo.destino_id):
        return None
    return vinculo.destino_id


def _destino_do_modelo_existe(entidade, destino_id) -> bool:
    """Confere que o destino declarado existe de fato no domínio.

    Quando a entidade não tem modelo conhecido aqui, não há como conferir — e a
    ausência de conferência não é ausência de destino.
    """
    if not destino_id:
        return False
    modelo = _MODELO_POR_ENTIDADE.get(entidade)
    if modelo is None:
        return True
    return modelo.objects.filter(pk=destino_id).exists()


def _resolver(external_id, entidade, *, modelo, escritos, condominio):
    """Encontra o registro já escrito com esse `external_id`, nesta rodada ou antes."""
    if not external_id:
        return None
    destino_id = escritos.get(external_id)
    if destino_id is None:
        item = (
            ItemImportacaoHistorica.objects.filter(
                condominio=condominio,
                origem=OrigemImportacaoHistorica.IMPORTACAO,
                conta_origem=CONTA_ORIGEM,
                identificador_externo=external_id,
            )
            .first()
        )
        destino_id = _destino_existente(item, entidade, external_id)
    if destino_id is None:
        return None
    return modelo.objects.filter(pk=destino_id).first()


def _registrar_vinculo(
    item,
    linha: LinhaDaMatriz,
    entidade,
    *,
    resultado,
    revisao: bool,
    usuario,
    simulado: bool,
    destino_id=None,
    observacao: str | None = None,
) -> None:
    """Grava o vínculo entre a fonte e o destino, com o desfecho da tentativa.

    A observação é a da linha por omissão; a reconciliação declarada passa a
    sua, para que a evidência que sustentou a decisão fique auditável no vínculo.
    """
    if simulado or item is None:
        return
    VinculoImportacaoHistorica.objects.registrar_vinculo(
        item=item,
        entidade_destino=entidade,
        identificador_destino=linha.external_id,
        destino_id=destino_id,
        resultado=resultado,
        revisao_manual=revisao,
        observacao=linha.motivo if observacao is None else observacao,
        criado_por=usuario,
    )


def _processar_item(item) -> None:
    """Fecha o item como processado, passando pelo ciclo válido de transições."""
    objetos = type(item).objects
    if item.estado_processamento == EstadoProcessamentoImportacao.PROCESSADO:
        return
    item = objetos.alterar_estado_processamento(
        item=item,
        novo_estado=EstadoProcessamentoImportacao.PROCESSANDO,
        reprocessamento_explicito=True,
    )
    objetos.alterar_estado_processamento(
        item=item, novo_estado=EstadoProcessamentoImportacao.PROCESSADO
    )


def _demanda_por_titulo(linha: LinhaDaMatriz, *, condominio):
    """A demanda do condomínio cujo título casa com o da linha — e só uma."""
    from apps.demandas.models import Demanda

    if not linha.chave_de_titulo:
        return None
    candidatas = Demanda.objects.filter(
        condominio=condominio, titulo__iexact=linha.titulo[:LIMITE_DO_TITULO]
    )
    if candidatas.count() != 1:
        return None
    return candidatas.first()


def _modelos_por_entidade() -> dict:
    """O modelo de domínio de cada entidade de destino, para conferir existência."""
    from apps.cadastros.models import Equipamento
    from apps.demandas.models import Demanda
    from apps.governanca.models import Projeto, ProjetoItem

    return {
        EntidadeImportacaoHistorica.DEMANDA: Demanda,
        EntidadeImportacaoHistorica.PROJETO: Projeto,
        EntidadeImportacaoHistorica.PROJETO_ITEM: ProjetoItem,
        EntidadeImportacaoHistorica.EQUIPAMENTO: Equipamento,
    }


def _hash_do_arquivo(caminho: Path) -> str:
    """O SHA-256 da matriz: identifica a versão exata da fonte desta rodada."""
    return hashlib.sha256(Path(caminho).read_bytes()).hexdigest()


def _ordem(external_id: str) -> int:
    """A posição do item na sequência da fonte, lida do sufixo do identificador."""
    digitos = re.search(r"(\d+)$", external_id or "")
    return int(digitos.group(1)) if digitos else 0


def _prioridade(valor: str | None) -> str:
    """A prioridade canônica da matriz no vocabulário de Demandas."""
    from apps.demandas.constants import PrioridadeDemanda

    return _PRIORIDADE.get(_chave(valor), PrioridadeDemanda.NORMAL)


def _status_demanda(valor: str | None) -> str:
    """A situação declarada da demanda, ou vazio quando a matriz não a normalizou."""
    return _STATUS_DEMANDA.get(_chave(valor), "")


def _status_projeto(valor: str | None) -> str:
    """A situação do projeto ou do item, com `Em estudo` como ponto de partida."""
    from apps.governanca.constants import StatusProjeto

    return _STATUS_PROJETO.get(_chave(valor), StatusProjeto.EM_ESTUDO)


def _descricao_da_linha(linha: LinhaDaMatriz) -> str:
    """A descrição factual da demanda: o que a matriz registra sobre a origem."""
    partes = [f"Importado da matriz de reconciliação RVE ({linha.external_id})."]
    if linha.linha_fonte_primaria:
        partes.append(f"Fonte: {linha.linha_fonte_primaria}.")
    if linha.parent_external_id:
        partes.append(f"Item superior na origem: {linha.parent_external_id}.")
    if linha.evidencia:
        partes.append(f"Evidência: {linha.evidencia}.")
    if linha.status_fonte:
        partes.append(f"Situação na fonte: {linha.status_fonte}.")
    return " ".join(partes)


def _observacao_da_linha(linha: LinhaDaMatriz) -> str:
    """O que se sabe sobre o registro e não cabe em campo próprio."""
    partes = []
    if linha.responsavel_canonico:
        partes.append(f"Responsável na fonte: {linha.responsavel_canonico}.")
    if linha.linha_fonte_primaria:
        partes.append(f"Fonte: {linha.linha_fonte_primaria}.")
    return " ".join(partes)


_MODELO_POR_ENTIDADE = _modelos_por_entidade()


__all__ = [
    "ARQUIVO_RECONCILIACAO",
    "CONTA_ORIGEM",
    "DECISAO_CRIAR",
    "DECISAO_REVISAR",
    "DECISAO_VINCULAR_EXISTENTE",
    "DECISAO_VINCULAR_JORNADA",
    "DESFECHO_CONSOLIDADO",
    "DESFECHO_CRIADO",
    "DESFECHO_ATUALIZADO",
    "DESFECHO_FALHA",
    "DESFECHO_HISTORICO",
    "DESFECHO_IGNORADO",
    "DESFECHO_PENDENTE",
    "DESFECHO_PLANEJAMENTO",
    "DESFECHO_REVISAR",
    "DESFECHO_REVISAO",
    "DESFECHO_VINCULADO_EXISTENTE",
    "DESFECHO_VINCULADO_JORNADA",
    "DecisaoDeReconciliacao",
    "DesfechoDaLinha",
    "ENTIDADE_POR_TIPO",
    "ErroDaImportacaoRVE",
    "LinhaDaMatriz",
    "ResumoDaImportacao",
    "TIPOS_ESCRITOS",
    "importar",
    "ler_matriz",
    "ler_reconciliacao",
]
