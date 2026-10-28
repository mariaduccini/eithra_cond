"""Carga histórica de Demandas a partir da caixa de e-mail da administração.

O acervo já existe: mensagens trocadas com fornecedores e entre a gestão, antes
de o Eithra existir. Esta camada relê esse acervo uma vez, com regras
conservadoras, para devolver ao sistema as demandas que já aconteceram.

O princípio de sempre vale aqui inteiro: entra uma informação nova, e a pergunta
é se ela muda alguma coisa que mereça atenção. Só a mensagem que anuncia uma
ocorrência operacional sustenta uma demanda; conclusão só vale quando declarada
com todas as letras; e o que não se sustenta fica de fora — silêncio é
resultado, não lacuna a preencher.

Três regras organizam tudo o que segue:

* **agrupar antes de decidir** — a mesma ocorrência costuma chegar em várias
  mensagens, e o fio da conversa é o primeiro sinal de que são a mesma coisa;
* **separar na dúvida** — conflito claro de equipamento, de local ou de
  natureza separa; dúvida sobre duplicidade cria registro novo, nunca funde;
* **não inventar** — local e equipamento só entram quando o cadastro do
  condomínio confirma o nome citado; prioridade, responsável e prazo não são
  escolhidos aqui.

Nada aqui grava corpo de mensagem, resposta de leitura ou texto integral de
e-mail: o item da fonte guarda a identidade da mensagem, e o vínculo guarda a
que demanda ela corresponde.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from django.db import models, transaction

from apps.core.constants import (
    EntidadeImportacaoHistorica,
    EstadoProcessamentoImportacao,
    OrigemImportacaoHistorica,
    ResultadoImportacaoHistorica,
)
from apps.core.email_interpretacao import (
    Consequencia,
    LeituraIndisponivel,
    interpretar,
)
from apps.core.models import VinculoImportacaoHistorica
from apps.core.texto import normalizar, normalizar_endereco_email

logger = logging.getLogger(__name__)

# A chave de proveniência desta carga: a demanda que a mensagem gerou. Renomear
# a mensagem já ligada encontra a mesma linha de vínculo, em vez de criar uma
# segunda — é o que torna uma segunda passagem inofensiva.
CHAVE_DA_DEMANDA = "demanda_historica"

# Duas mensagens sem fio em comum só são a mesma conversa quando assunto,
# remetente e intervalo de tempo dizem o mesmo. A janela é curta de propósito:
# separar demais é recuperável, fundir demais não é.
JANELA_DE_PROXIMIDADE = timedelta(days=7)

# O título cabe em uma linha de lista, com folga para não virar parágrafo.
LIMITE_DO_TITULO = 120

# Marca de ordenação para a mensagem que chegou sem data: ela vai para o
# começo, e nunca se agrupa por proximidade com ninguém.
_SEM_MOMENTO = datetime.min.replace(tzinfo=timezone.utc)


class NaturezaDaMensagem(models.TextChoices):
    """O que a mensagem é, para os fins desta carga."""

    OCORRENCIA = "ocorrencia", "Ocorrência nova"
    CONCLUSAO = "conclusao", "Conclusão declarada"
    SEM_OCORRENCIA = "sem_ocorrencia", "Sem ocorrência"


class DecisaoDaCarga(models.TextChoices):
    """O que a carga faz com um grupo de mensagens da mesma ocorrência."""

    CRIAR = "criar", "Registrar demanda"
    RELACIONAR_EXISTENTE = "relacionar_existente", "Relacionar a demanda existente"
    IGNORAR = "ignorar", "Nada a registrar"


# Só duas leituras sustentam alguma decisão: anunciar uma ocorrência e declarar
# uma conclusão. Atualização, cobrança, agendamento, documento e leitura
# ambígua continuam valendo como contexto da ocorrência do grupo — nunca como
# ocorrência por si mesmos, porque sozinhos não sustentam registro novo.
_NATUREZA_POR_CONSEQUENCIA = {
    Consequencia.NOVA_OCORRENCIA: NaturezaDaMensagem.OCORRENCIA,
    Consequencia.CONCLUSAO_DECLARADA: NaturezaDaMensagem.CONCLUSAO,
}


class ErroDaCargaHistorica(Exception):
    """Falha controlada da carga histórica de demandas."""


class CargaSemResponsavel(ErroDaCargaHistorica):
    """A carga não tem quem responda por ela — e sem isso nada é gravado."""


def natureza_da(consequencia: str) -> str:
    """O que a consequência lida significa para esta carga."""
    return _NATUREZA_POR_CONSEQUENCIA.get(
        consequencia, NaturezaDaMensagem.SEM_OCORRENCIA
    )


def texto_da_mensagem(mensagem) -> str:
    """Assunto e corpo juntos, como o relato é lido no resto do sistema."""
    partes = [
        getattr(mensagem, "assunto", "") or "",
        getattr(mensagem, "texto", "") or "",
    ]
    return "\n".join(parte for parte in partes if parte.strip())


# ---------------------------------------------------------------------------
# Contratos (em memória, nunca persistidos)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntradaDaCarga:
    """Uma mensagem da caixa e o item de origem que a representa.

    ``item`` é o registro durável da mensagem na carga histórica. Ele falta na
    simulação, que lê e decide sem gravar nada.
    """

    mensagem: object
    item: object | None = None

    @property
    def message_id(self) -> str:
        return (getattr(self.mensagem, "message_id", "") or "").strip()


@dataclass(frozen=True)
class LeituraDaMensagem:
    """A mensagem já lida: o que ela sustenta e a que ocorrência pertence."""

    entrada: EntradaDaCarga
    natureza: str
    local: object | None = None
    equipamento: object | None = None
    tipo_de_problema: str = ""

    @property
    def mensagem(self):
        return self.entrada.mensagem

    @property
    def item(self):
        return self.entrada.item

    @property
    def message_id(self) -> str:
        return self.entrada.message_id

    @property
    def thread_id(self) -> str:
        return (getattr(self.mensagem, "thread_id", "") or "").strip()

    @property
    def remetente(self) -> str:
        return normalizar_endereco_email(getattr(self.mensagem, "remetente", ""))

    @property
    def assunto(self) -> str:
        return " ".join((getattr(self.mensagem, "assunto", "") or "").split())

    @property
    def assunto_normalizado(self) -> str:
        return normalizar(self.assunto)

    @property
    def momento(self):
        return getattr(self.mensagem, "momento", None)

    @property
    def texto(self) -> str:
        return texto_da_mensagem(self.mensagem)

    @property
    def ocorrencia(self) -> bool:
        return self.natureza == NaturezaDaMensagem.OCORRENCIA

    @property
    def conclusao(self) -> bool:
        return self.natureza == NaturezaDaMensagem.CONCLUSAO


@dataclass
class GrupoDaOcorrencia:
    """Mensagens que falam da mesma ocorrência, na ordem em que chegaram."""

    chave: str
    leituras: list[LeituraDaMensagem] = field(default_factory=list)

    @property
    def mensagem_de_ocorrencia(self) -> LeituraDaMensagem | None:
        """A mensagem que anunciou a ocorrência — a primeira que a declarou."""
        for leitura in self.leituras:
            if leitura.ocorrencia:
                return leitura
        return None

    @property
    def itens(self) -> tuple:
        return tuple(leitura.item for leitura in self.leituras if leitura.item is not None)

    @property
    def concluida(self) -> bool:
        """Se alguma mensagem do grupo declarou a conclusão com todas as letras."""
        return any(leitura.conclusao for leitura in self.leituras)


@dataclass(frozen=True)
class PlanoDaOcorrencia:
    """O que a carga pretende fazer com um grupo, e por quê."""

    grupo: GrupoDaOcorrencia
    decisao: str
    motivo: str = ""
    titulo: str = ""
    descricao: str = ""
    local: object | None = None
    equipamento: object | None = None
    concluida: bool = False
    demanda: object | None = None

    @property
    def chave(self) -> str:
        return self.grupo.chave

    @property
    def itens(self) -> tuple:
        return self.grupo.itens


@dataclass(frozen=True)
class ResultadoDoGrupo:
    """O plano do grupo e o que de fato aconteceu com ele."""

    plano: PlanoDaOcorrencia
    executado: bool = False
    demanda: object | None = None

    @property
    def decisao(self) -> str:
        return self.plano.decisao

    @property
    def chave(self) -> str:
        return self.plano.chave

    @property
    def motivo(self) -> str:
        return self.plano.motivo

    @property
    def titulo(self) -> str:
        return self.plano.titulo

    @property
    def itens(self) -> tuple:
        return self.plano.itens


@dataclass
class ResumoDaCarga:
    """O que a passagem pela caixa produziu — ou apenas decidiu, na simulação."""

    condominio: object = None
    simulado: bool = False
    mensagens: int = 0
    grupos: list[ResultadoDoGrupo] = field(default_factory=list)
    falhas: list[tuple[str, str]] = field(default_factory=list)

    def por_decisao(self, decisao: str) -> tuple[ResultadoDoGrupo, ...]:
        return tuple(r for r in self.grupos if r.decisao == decisao)

    @property
    def criadas(self) -> tuple[ResultadoDoGrupo, ...]:
        return self.por_decisao(DecisaoDaCarga.CRIAR)

    @property
    def relacionadas(self) -> tuple[ResultadoDoGrupo, ...]:
        return self.por_decisao(DecisaoDaCarga.RELACIONAR_EXISTENTE)

    @property
    def ignoradas(self) -> tuple[ResultadoDoGrupo, ...]:
        return self.por_decisao(DecisaoDaCarga.IGNORAR)

    @property
    def demandas(self) -> tuple:
        """As demandas tocadas por esta passagem: criadas e relacionadas."""
        vistas: list = []
        for resultado in self.grupos:
            if resultado.demanda is None:
                continue
            if resultado.demanda.pk not in [d.pk for d in vistas]:
                vistas.append(resultado.demanda)
        return tuple(vistas)


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def ler_mensagem(entrada: EntradaDaCarga, *, condominio, interpretador=None):
    """Lê uma mensagem e reduz o resultado ao que esta carga usa.

    A leitura é a mesma do resto do sistema: ``interpretar`` é chamada como na
    confirmação de e-mail. O que muda é o destino dela — aqui não se guarda
    proposta nem pergunta, e local e equipamento só valem quando o cadastro do
    condomínio confirma o nome citado.
    """
    from apps.demandas import derivacao

    ler = interpretador or interpretar
    mensagem = entrada.mensagem
    leitura = ler(mensagem, condominio=condominio)
    texto = texto_da_mensagem(mensagem)

    return LeituraDaMensagem(
        entrada=entrada,
        natureza=natureza_da(leitura.tipo_de_consequencia),
        local=derivacao.local_do_relato(texto, condominio),
        equipamento=derivacao.equipamento_do_relato(texto, condominio)[0],
        tipo_de_problema=_tipo_declarado(texto),
    )


def _tipo_declarado(texto: str) -> str:
    """A natureza do problema, e só quando o próprio relato a nomeia.

    A cena descrita também sugere natureza, mas isso já é interpretação — e a
    descrição da demanda diz fatos, não leituras.
    """
    from apps.demandas import derivacao

    sugestao = derivacao.tipo_de_problema_do_relato(texto)
    if not sugestao.existe:
        return ""
    if sugestao.origem != derivacao.OrigemSugestao.RELATO:
        return ""
    return sugestao.valor


# ---------------------------------------------------------------------------
# Agrupamento
# ---------------------------------------------------------------------------


def agrupar(leituras) -> list[GrupoDaOcorrencia]:
    """Junta as mensagens que falam da mesma ocorrência.

    A ordem das perguntas é a ordem da confiança: primeiro o fio da conversa,
    que é a resposta declarada; depois assunto, remetente e proximidade no
    tempo, que é o que sobra quando não há fio. Conflito claro de equipamento
    ou de local, entre duas mensagens que anunciam ocorrência, separa — o mesmo
    assunto pode contar dois problemas.
    """
    grupos: list[GrupoDaOcorrencia] = []
    for leitura in sorted(leituras, key=_ordem):
        for grupo in grupos:
            if _mesma_ocorrencia(leitura, grupo):
                grupo.leituras.append(leitura)
                break
        else:
            grupos.append(
                GrupoDaOcorrencia(chave=_chave_do_grupo(leitura, grupos), leituras=[leitura])
            )
    return grupos


def _ordem(leitura: LeituraDaMensagem):
    """A ordem de chegada, com o que veio sem data no começo."""
    momento = leitura.momento
    return (momento is not None, momento or _SEM_MOMENTO, leitura.message_id)


def _chave_do_grupo(leitura: LeituraDaMensagem, grupos) -> str:
    """Como o grupo se identifica no relatório — pelo fio, ou pelo assunto."""
    if leitura.thread_id:
        base = f"fio:{leitura.thread_id}"
    else:
        base = f"assunto:{leitura.assunto_normalizado or leitura.message_id}|{leitura.remetente}"
    if any(grupo.chave == base for grupo in grupos):
        return f"{base}#{len(grupos)}"
    return base


def _mesma_ocorrencia(leitura: LeituraDaMensagem, grupo: GrupoDaOcorrencia) -> bool:
    """Se esta mensagem pertence ao grupo que já existe."""
    fio = grupo.leituras[0].thread_id
    if fio and leitura.thread_id:
        return leitura.thread_id == fio and not _conflito(leitura, _ancora(grupo))
    if fio or leitura.thread_id:
        return False
    return _mesmo_assunto(leitura, grupo) and not _conflito(leitura, _ancora(grupo))


def _ancora(grupo: GrupoDaOcorrencia) -> LeituraDaMensagem:
    """A mensagem com que o grupo se compara: a da ocorrência, se já houver."""
    return grupo.mensagem_de_ocorrencia or grupo.leituras[0]


def _mesmo_assunto(leitura: LeituraDaMensagem, grupo: GrupoDaOcorrencia) -> bool:
    """Sem fio em comum, assunto e remetente ainda podem identificar a conversa."""
    primeira = grupo.leituras[0]
    assunto = leitura.assunto_normalizado
    if not assunto or assunto != primeira.assunto_normalizado:
        return False
    if not leitura.remetente or leitura.remetente != primeira.remetente:
        return False
    return _proximo(leitura, grupo)


def _proximo(leitura: LeituraDaMensagem, grupo: GrupoDaOcorrencia) -> bool:
    """Se a mensagem cai dentro da janela de todas as que já estão no grupo."""
    momento = leitura.momento
    if momento is None:
        return False
    momentos = [item.momento for item in grupo.leituras if item.momento is not None]
    if not momentos:
        return False
    return all(
        abs(momento - referencia) <= JANELA_DE_PROXIMIDADE for referencia in momentos
    )


def _conflito(leitura: LeituraDaMensagem, anterior: LeituraDaMensagem) -> bool:
    """Conflito claro entre duas mensagens que anunciam ocorrência.

    Só o que o cadastro confirmou conta: dois equipamentos, ou dois locais,
    diferentes são duas ocorrências — não uma. Nome não confirmado não separa,
    porque não sustenta nada.
    """
    if not (leitura.ocorrencia and anterior.ocorrencia):
        return False
    return _divergem(leitura.equipamento, anterior.equipamento) or _divergem(
        leitura.local, anterior.local
    )


def _divergem(um, outro) -> bool:
    """Se dois cadastros confirmados são registros diferentes."""
    if um is None or outro is None:
        return False
    return getattr(um, "pk", None) != getattr(outro, "pk", None)


# ---------------------------------------------------------------------------
# Decisão
# ---------------------------------------------------------------------------


def planejar(grupo: GrupoDaOcorrencia, *, condominio) -> PlanoDaOcorrencia:
    """Decide o destino do grupo: registrar, relacionar ou nada.

    A ordem é conservadora. Primeiro o que já existe — vínculo registrado para
    estas mensagens, ou fio já ligado a uma única demanda. Depois a evidência:
    sem mensagem que anuncie ocorrência, não há o que registrar. Só então se
    cria, e criar é sempre preferível a fundir por semelhança.
    """
    existente = demanda_ja_vinculada(grupo, condominio=condominio)
    if existente is not None:
        return PlanoDaOcorrencia(
            grupo=grupo,
            decisao=DecisaoDaCarga.RELACIONAR_EXISTENTE,
            motivo="Esta conversa já está ligada a uma demanda do condomínio.",
            demanda=existente,
        )

    ocorrencia = grupo.mensagem_de_ocorrencia
    if ocorrencia is None:
        return PlanoDaOcorrencia(
            grupo=grupo,
            decisao=DecisaoDaCarga.IGNORAR,
            motivo="A conversa não anuncia ocorrência operacional.",
        )

    titulo = titulo_do_grupo(grupo)
    if not titulo:
        return PlanoDaOcorrencia(
            grupo=grupo,
            decisao=DecisaoDaCarga.IGNORAR,
            motivo="Sem um assunto que sustente o título da demanda.",
        )

    return PlanoDaOcorrencia(
        grupo=grupo,
        decisao=DecisaoDaCarga.CRIAR,
        motivo="Ocorrência operacional relatada na mensagem.",
        titulo=titulo,
        descricao=descricao_do_grupo(grupo),
        local=ocorrencia.local,
        equipamento=ocorrencia.equipamento,
        concluida=grupo.concluida,
    )


def titulo_do_grupo(grupo: GrupoDaOcorrencia) -> str:
    """O título da demanda: o assunto da mensagem que anunciou a ocorrência.

    Quando o assunto está vazio, a primeira frase do relato faz esse papel —
    é o que o registro manual já faz, com o mesmo corte.
    """
    from apps.demandas import derivacao

    ocorrencia = grupo.mensagem_de_ocorrencia
    if ocorrencia is None:
        return ""
    if ocorrencia.assunto:
        return derivacao.assunto_do_relato(ocorrencia.assunto, limite=LIMITE_DO_TITULO)
    return derivacao.assunto_do_relato(ocorrencia.texto, limite=LIMITE_DO_TITULO)


def descricao_do_grupo(grupo: GrupoDaOcorrencia) -> str:
    """A descrição factual da ocorrência — sem corpo de e-mail, sem prompt.

    Diz o que aconteceu na linguagem do domínio: o registro veio por e-mail e,
    quando o relato sustenta, a natureza do problema e o lugar ou o equipamento
    que o cadastro confirmou. O que não se sustenta não entra.
    """
    partes = ["Registro recebido por e-mail."]
    ocorrencia = grupo.mensagem_de_ocorrencia
    if ocorrencia is not None:
        partes.extend(fatos_da_ocorrencia(ocorrencia))
    return " ".join(partes)


def fatos_da_ocorrencia(ocorrencia: LeituraDaMensagem) -> list[str]:
    """O que a mensagem sustenta, em frases curtas e humanas."""
    from apps.demandas.constants import TipoProblema

    fatos: list[str] = []
    if ocorrencia.tipo_de_problema:
        fatos.append(f"Natureza: {TipoProblema(ocorrencia.tipo_de_problema).label}.")
    if ocorrencia.local is not None:
        fatos.append(f"Local: {ocorrencia.local.nome}.")
    if ocorrencia.equipamento is not None:
        fatos.append(f"Equipamento: {ocorrencia.equipamento.nome}.")
    return fatos


def demanda_ja_vinculada(grupo: GrupoDaOcorrencia, *, condominio):
    """A demanda que este grupo já alcançou, quando existe uma só.

    Duas portas, ambas registradas: um vínculo já gravado para alguma das
    mensagens do grupo, ou o fio da conversa já ligado a uma única demanda em
    outra passagem da carga. Havendo mais de uma candidata, não há segurança —
    e a resposta é não relacionar.
    """
    from apps.demandas.models import Demanda

    destinos = _destinos_do_grupo(grupo, condominio=condominio)
    if not destinos:
        return None

    demandas = Demanda.objects.filter(pk__in=destinos, condominio=condominio)
    if demandas.count() != 1:
        return None
    return demandas.first()


def _destinos_do_grupo(grupo: GrupoDaOcorrencia, *, condominio) -> set:
    """A que demandas as mensagens deste grupo, ou o seu fio, já apontam."""
    destinos: set = set()
    for item in grupo.itens:
        destinos |= _destinos_das_mensagens(item=item)

    fio = grupo.leituras[0].thread_id if grupo.leituras else ""
    if fio and condominio is not None:
        destinos |= _destinos_das_mensagens(
            condominio=condominio,
            origem=OrigemImportacaoHistorica.GMAIL,
            thread_id=fio,
        )
    return destinos


def _destinos_das_mensagens(*, item=None, condominio=None, origem="", thread_id="") -> set:
    """Os destinos já vinculados às mensagens que casam com o filtro."""
    filtros = {"entidade_destino": EntidadeImportacaoHistorica.DEMANDA}
    if item is not None:
        filtros["item"] = item
    else:
        filtros["condominio"] = condominio
        filtros["item__origem"] = origem
        filtros["item__thread_id"] = thread_id
        filtros["item__condominio"] = condominio

    return set(
        VinculoImportacaoHistorica.objects.filter(**filtros)
        .exclude(destino_id=None)
        .values_list("destino_id", flat=True)
    )


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------


def importar(
    entradas,
    *,
    condominio,
    usuario=None,
    interpretador=None,
    simulado=False,
) -> ResumoDaCarga:
    """Lê, agrupa, decide e registra — ou apenas decide, na simulação.

    ``usuario`` é quem responde pela carga e assina o que for criado. Sem ele
    não há execução: demanda nasce com autor, e autor não se inventa. Na
    simulação ninguém assina, porque nada é gravado.
    """
    entradas = list(entradas)
    resumo = ResumoDaCarga(
        condominio=condominio, simulado=simulado, mensagens=len(entradas)
    )
    _conferir_entradas(entradas, condominio=condominio, simulado=simulado)
    if not simulado:
        exigir_responsavel(usuario, condominio)

    leituras, falhas = _ler(
        entradas, condominio=condominio, interpretador=interpretador, simulado=simulado
    )
    resumo.falhas.extend(falhas)

    for grupo in agrupar(leituras):
        plano = planejar(grupo, condominio=condominio)
        if simulado:
            resumo.grupos.append(ResultadoDoGrupo(plano=plano))
            continue
        resumo.grupos.append(_executar(plano, condominio=condominio, usuario=usuario))

    return resumo


def _conferir_entradas(entradas, *, condominio, simulado: bool) -> None:
    """Recusa lotes misturados e execução sem o registro de origem."""
    for entrada in entradas:
        if entrada.item is None:
            if not simulado:
                raise ErroDaCargaHistorica(
                    "Cada mensagem precisa do seu registro de origem para ser importada."
                )
            continue
        if getattr(condominio, "pk", None) != entrada.item.condominio_id:
            raise ErroDaCargaHistorica("Esta carga não mistura condomínios.")


def exigir_responsavel(usuario, condominio) -> None:
    """Confere quem responde pela carga, pelo mesmo critério de agir no domínio."""
    from apps.condominios.acesso import tem_acesso_ativo
    from apps.demandas.permissoes import pode_criar_demanda

    if usuario is None or not getattr(usuario, "pk", None):
        raise CargaSemResponsavel(
            "Informe quem responde por esta carga antes de gravar qualquer coisa."
        )
    if not tem_acesso_ativo(usuario, condominio):
        raise CargaSemResponsavel("Quem responde por esta carga não acessa este condomínio.")
    if not pode_criar_demanda(usuario, condominio):
        raise CargaSemResponsavel(
            "Quem responde por esta carga não registra demandas neste condomínio."
        )


def _executar(plano: PlanoDaOcorrencia, *, condominio, usuario) -> ResultadoDoGrupo:
    """Faz o que o plano decidiu, com a demanda e os itens no mesmo passo."""
    grupo = plano.grupo

    if plano.decisao == DecisaoDaCarga.IGNORAR:
        _encerrar(grupo)
        return ResultadoDoGrupo(plano=plano, executado=True)

    if plano.decisao == DecisaoDaCarga.RELACIONAR_EXISTENTE:
        with transaction.atomic():
            _vincular(grupo, plano.demanda, usuario=usuario)
            _encerrar(grupo)
        return ResultadoDoGrupo(plano=plano, executado=True, demanda=plano.demanda)

    return _registrar_demanda(plano, condominio=condominio, usuario=usuario)


def _registrar_demanda(plano: PlanoDaOcorrencia, *, condominio, usuario) -> ResultadoDoGrupo:
    """Cria a demanda pelo service do domínio — nunca por atribuição direta.

    Só entra o que a mensagem sustenta: título e descrição factuais, e o local e
    o equipamento que o cadastro confirmou. Prioridade, responsável e prazo não
    são escolhidos aqui. A conclusão declarada é aplicada pelo mecanismo
    oficial de situação, e não escrita à mão.
    """
    from apps.demandas.constants import OrigemDemanda, StatusDemanda
    from apps.demandas.services import alterar_status, criar_demanda

    grupo = plano.grupo
    with transaction.atomic():
        demanda = criar_demanda(
            condominio=condominio,
            criado_por=usuario,
            titulo=plano.titulo,
            descricao=plano.descricao,
            origem=OrigemDemanda.EMAIL,
            local=plano.local,
            equipamento=plano.equipamento,
        )
        _vincular(grupo, demanda, usuario=usuario)
        if plano.concluida:
            alterar_status(
                demanda=demanda, usuario=usuario, novo_status=StatusDemanda.CONCLUIDA
            )
        _encerrar(grupo)

    return ResultadoDoGrupo(plano=plano, executado=True, demanda=demanda)


def _vincular(grupo: GrupoDaOcorrencia, demanda, *, usuario) -> None:
    """Grava a proveniência: de qual mensagem esta demanda veio."""
    for item in grupo.itens:
        VinculoImportacaoHistorica.objects.registrar_vinculo(
            item=item,
            entidade_destino=EntidadeImportacaoHistorica.DEMANDA,
            identificador_destino=CHAVE_DA_DEMANDA,
            destino_id=demanda.pk,
            resultado=ResultadoImportacaoHistorica.CRIADO,
            revisao_manual=True,
            criado_por=usuario,
        )


def _encerrar(grupo: GrupoDaOcorrencia) -> None:
    """Leva cada item do grupo ao estado processado, pelo ciclo válido."""
    for item in grupo.itens:
        _processar_item(item)


def _ler(entradas, *, condominio, interpretador, simulado: bool):
    """Lê o que der para ler; o que não der vira falha auditável, não silêncio."""
    leituras: list[LeituraDaMensagem] = []
    falhas: list[tuple[str, str]] = []

    for entrada in entradas:
        try:
            leituras.append(
                ler_mensagem(entrada, condominio=condominio, interpretador=interpretador)
            )
        except LeituraIndisponivel:
            logger.warning("Mensagem %s: a leitura está indisponível.", entrada.message_id)
            falhas.append((entrada.message_id, LeituraIndisponivel.__name__))
            if not simulado and entrada.item is not None:
                _marcar_erro(entrada.item)

    return leituras, falhas


def _processar_item(item):
    """Fecha o item como processado, passando pelo ciclo válido de transições."""
    objetos = type(item).objects
    if item.estado_processamento == EstadoProcessamentoImportacao.PROCESSADO:
        return item

    item = objetos.alterar_estado_processamento(
        item=item,
        novo_estado=EstadoProcessamentoImportacao.PROCESSANDO,
        reprocessamento_explicito=True,
    )
    return objetos.alterar_estado_processamento(
        item=item, novo_estado=EstadoProcessamentoImportacao.PROCESSADO
    )


def _marcar_erro(item):
    """Leva o item a erro pelo caminho de sempre, sem pular etapa."""
    objetos = type(item).objects
    if item.estado_processamento != EstadoProcessamentoImportacao.PROCESSANDO:
        item = objetos.alterar_estado_processamento(
            item=item,
            novo_estado=EstadoProcessamentoImportacao.PROCESSANDO,
            reprocessamento_explicito=True,
        )
    return objetos.alterar_estado_processamento(
        item=item, novo_estado=EstadoProcessamentoImportacao.ERRO
    )


__all__ = [
    "CHAVE_DA_DEMANDA",
    "CargaSemResponsavel",
    "DecisaoDaCarga",
    "EntradaDaCarga",
    "ErroDaCargaHistorica",
    "GrupoDaOcorrencia",
    "JANELA_DE_PROXIMIDADE",
    "LeituraDaMensagem",
    "NaturezaDaMensagem",
    "PlanoDaOcorrencia",
    "ResumoDaCarga",
    "ResultadoDoGrupo",
    "agrupar",
    "demanda_ja_vinculada",
    "descricao_do_grupo",
    "exigir_responsavel",
    "importar",
    "ler_mensagem",
    "natureza_da",
    "planejar",
    "texto_da_mensagem",
    "titulo_do_grupo",
]
