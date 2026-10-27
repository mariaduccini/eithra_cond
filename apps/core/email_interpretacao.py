"""Leitura semântica de um e-mail e reconciliação com o que o Eithra já sabe.

A pergunta desta camada é a mesma que atravessa o sistema inteiro:

    Entrou uma informação nova. Isso muda alguma coisa que mereça atenção?

O caminho é curto e termina antes de qualquer gravação operacional:

    ItemImportacaoHistorica
    → recuperar MensagemGmail        (provider, somente leitura)
    → extrair FATOS                  (o que a mensagem diz, com todas as letras)
    → buscar CONTEXTO                (cadastro, fio, demanda, manutenção, documento)
    → gerar INFERÊNCIAS              (o que o Eithra conclui do contexto)
    → produzir PROPOSTA              (o que faria sentido fazer)
    → decidir                        (sem ação / pede confirmação / falha)

Três coisas nunca se misturam aqui, e é essa separação que sustenta o resto:

* **FATO** é o que está na mensagem — o remetente, a data, o número citado, o
  anexo, a frase de conclusão que o fornecedor escreveu.
* **INFERÊNCIA** é o que o Eithra conclui ao comparar o fato com o cadastro e o
  histórico — "esta mensagem provavelmente se refere à manutenção do elevador
  social". É hipótese, e continua rotulada como tal.
* **PROPOSTA** é o que faria sentido fazer com isso. Nesta fase, **nenhuma
  proposta é executada**: relacionar, concluir, criar demanda, registrar
  documento — tudo isso depende de confirmação humana e de uma fase seguinte.

O que este módulo **não** faz, de propósito: não cria demanda, não atualiza
demanda, não cria manutenção, não conclui manutenção, não cria documento, não
grava anexo, não altera status nem prioridade e não grava vínculo operacional.
A única coisa que ele muda no banco é o **estado de processamento** do item da
fonte — e nada mais.

O corpo da mensagem não é persistido em lugar nenhum: ele é lido do provider a
cada análise, usado em memória e descartado. O que fica registrado é o fato de a
leitura ter acontecido, não o texto que a originou.

Quem lê é :func:`interpretar`; quem cruza com o cadastro é :func:`reconciliar`;
quem conduz o item pelo ciclo é :func:`processar_item_gmail`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core import gmail, llm
from apps.core.constants import (
    EntidadeImportacaoHistorica,
    EstadoProcessamentoImportacao,
    OrigemImportacaoHistorica,
)
from apps.core.interpretacao import Confianca, Estado
from apps.core.models import ItemImportacaoHistorica
from apps.core.texto import contem, normalizar, normalizar_endereco_email

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vocabulário da leitura
# ---------------------------------------------------------------------------
# Poucas categorias, e todas reconhecíveis por quem opera o condomínio. Não é
# taxonomia de engenharia: é o suficiente para separar o que não muda nada, o
# que atualiza algo e o que traz uma ocorrência nova.

PALAVRAS_DE_RECEBIMENTO = (
    "obrigado",
    "obrigada",
    "recebido",
    "recebida",
    "recebemos",
    "ciente",
    "de acordo",
    "ok",
    "otimo",
    "otima",
    "perfeito",
)

PALAVRAS_DE_PEDIDO = (
    "solicito",
    "solicitamos",
    "pedimos",
    "peco",
    "precisamos",
    "favor",
    "gostariamos",
    "necessitamos",
    "aguardamos",
)

PALAVRAS_DE_CONCLUSAO = (
    "concluido",
    "concluida",
    "concluimos",
    "finalizado",
    "finalizada",
    "servico realizado",
    "realizado",
    "executado",
    "executada",
    "terminado",
)

PALAVRAS_DE_COBRANCA = (
    "cobranca",
    "boleto",
    "fatura",
    "pagamento",
    "nota fiscal",
    "vencimento",
    "pix",
    "deposito",
)

PALAVRAS_DE_AGENDAMENTO = (
    "agendado",
    "agendada",
    "agendamento",
    "agendamos",
    "visita",
    "iremos",
    "compareceremos",
    "marcado",
    "marcada",
    "amanha",
)

# Um recado de recebimento é curto por natureza. Quando a mensagem é longa, ela
# está dizendo mais do que "ok, recebido" — e "mais" pode ser consequência.
LIMITE_DE_RECADO_CURTO = 14

# O corpo entra no pedido de leitura sem limite próprio; este teto é higiene de
# transporte, não regra de negócio.
LIMITE_DO_CORPO = 4000

# Uma pergunta longa deixa de ser pergunta. O rótulo vem do título do registro,
# que já é curto; este corte é rede de segurança.
LIMITE_DA_PERGUNTA = 120


class Consequencia(models.TextChoices):
    """O que a mensagem muda, dito em poucas palavras.

    ``SEM_ACAO`` é um resultado legítimo e frequente: agradecimento, aviso de
    recebimento, confirmação de leitura. Nada é criado só para "dar destino" a
    esse tipo de mensagem.
    """

    SEM_ACAO = "sem_acao", "Sem ação"
    ATUALIZACAO = "atualizacao", "Atualização"
    NOVA_OCORRENCIA = "nova_ocorrencia", "Nova ocorrência"
    DOCUMENTO = "documento", "Documento"
    AGENDAMENTO = "agendamento", "Agendamento"
    CONCLUSAO_DECLARADA = "conclusao_declarada", "Conclusão declarada"
    COBRANCA = "cobranca", "Cobrança"
    AMBIGUO = "ambiguo", "Ambíguo"


class AcaoProposta(models.TextChoices):
    """O que a leitura sugere fazer — nunca o que ela fez.

    Nesta fase, todas as ações abaixo existem apenas como proposta: elas
    descrevem o próximo passo possível, e quem decide é a pessoa.
    """

    NENHUMA = "nenhuma", "Nada a fazer"
    RELACIONAR_DEMANDA = "relacionar_demanda", "Relacionar a uma demanda"
    RELACIONAR_MANUTENCAO = "relacionar_manutencao", "Relacionar a uma manutenção"
    AVALIAR_CONCLUSAO = "avaliar_conclusao", "Avaliar a conclusão declarada"
    REGISTRAR_DOCUMENTO = "registrar_documento", "Considerar documento"
    REGISTRAR_AGENDAMENTO = "registrar_agendamento", "Considerar agendamento"
    REGISTRAR_COBRANCA = "registrar_cobranca", "Considerar cobrança"
    CRIAR_NOVA_DEMANDA = "criar_nova_demanda", "Considerar nova demanda"
    CONFIRMAR_CONTEXTO = "confirmar_contexto", "Confirmar o contexto"


class TipoCandidato(models.TextChoices):
    """A que registro do Eithra a mensagem pode estar se referindo."""

    THREAD = "thread", "Fio da conversa"
    FORNECEDOR = "fornecedor", "Fornecedor"
    CONTRATO = "contrato", "Contrato"
    EQUIPAMENTO = "equipamento", "Equipamento"
    LOCAL = "local", "Local"
    DEMANDA = "demanda", "Demanda"
    MANUTENCAO = "manutencao", "Manutenção"
    DOCUMENTO = "documento", "Documento"


class OrigemCandidato(models.TextChoices):
    """De onde o candidato foi encontrado — para que a tela possa dizer."""

    THREAD = "thread", "Do fio da conversa"
    REMETENTE = "remetente", "Do remetente"
    IDENTIFICADOR = "identificador", "De um identificador citado"
    EQUIPAMENTO = "equipamento", "Do equipamento citado"
    LOCAL = "local", "Do local citado"
    HISTORICO = "historico", "Do histórico recente"


# ---------------------------------------------------------------------------
# Contratos (em memória, nunca persistidos)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fato:
    """Um dado que a mensagem sustenta — e onde ele se sustenta.

    ``estado`` é o que separa "o cadastro diz" de "a mensagem disse": quando um
    cadastro do condomínio confirma o que a mensagem citou, o fato passa a
    ``CONFIRMADA`` e ``objeto`` guarda o registro que o confirma.
    """

    campo: str = ""
    valor: str = ""
    rotulo: str = ""
    estado: str = Estado.DECLARADA
    objeto: object | None = None
    motivo: str = ""

    @property
    def existe(self) -> bool:
        return bool(self.valor or self.rotulo)

    @property
    def texto(self) -> str:
        """Como o fato aparece para quem confirma."""
        return self.rotulo or self.valor

    @property
    def confirmado(self) -> bool:
        """Se o cadastro sustenta isto, e não apenas a mensagem."""
        return self.estado == Estado.CONFIRMADA


@dataclass(frozen=True)
class Inferencia:
    """O que o Eithra conclui — e continua sendo hipótese.

    Nunca é gravada como fato. Existe para ser mostrada, com o motivo do lado,
    e descartada se quem lê discordar.
    """

    campo: str = ""
    valor: str = ""
    rotulo: str = ""
    confianca: str = Confianca.BAIXA
    objeto: object | None = None
    motivo: str = ""


@dataclass(frozen=True)
class Candidato:
    """Um registro do Eithra que a mensagem pode estar explicando.

    ``objeto`` é o registro real — resolvido pelo backend, nunca escolhido por
    um serviço de linguagem. ``rotulo`` é o que a pessoa lê; identificador
    técnico não sai daqui.
    """

    tipo: str = ""
    rotulo: str = ""
    origem: str = OrigemCandidato.THREAD
    confianca: str = Confianca.BAIXA
    objeto: object | None = None
    motivo: str = ""


@dataclass(frozen=True)
class Proposta:
    """O que a leitura sugere fazer, com o porquê ao lado.

    Não executa nada. Existe para que a confirmação humana tenha sobre o que
    decidir — e para que o silêncio, quando é a resposta, também fique dito.
    """

    acao: str = AcaoProposta.NENHUMA
    confianca: str = Confianca.BAIXA
    rotulo: str = ""
    motivo: str = ""
    candidato: Candidato | None = None

    @property
    def existe(self) -> bool:
        """Se há algo a decidir. ``NENHUMA`` é o silêncio, e é resposta."""
        return self.acao != AcaoProposta.NENHUMA


@dataclass(frozen=True)
class LeituraEmail:
    """O que deu para ler de uma mensagem, antes e depois do contexto.

    Sai de :func:`interpretar` só com fatos e consequência; :func:`reconciliar`
    acrescenta candidatos, proposta e a decisão sobre precisar ou não de
    confirmação.
    """

    fatos: tuple[Fato, ...] = ()
    tipo_de_consequencia: str = Consequencia.AMBIGUO
    entidades_extraidas: dict[str, Fato] = field(default_factory=dict)
    inferencias: tuple[Inferencia, ...] = ()
    candidatos: tuple[Candidato, ...] = ()
    proposta: Proposta = field(default_factory=Proposta)
    necessita_confirmacao: bool = True
    pergunta: str = ""

    def fato(self, campo: str) -> Fato | None:
        return self.entidades_extraidas.get(campo)

    def candidatos_de(self, tipo: str) -> tuple[Candidato, ...]:
        return tuple(c for c in self.candidatos if c.tipo == tipo)


@dataclass(frozen=True)
class ResultadoProcessamentoEmail:
    """O desfecho de uma leitura: o que se entendeu e onde o item parou.

    Vive só em memória. Nada aqui é gravado automaticamente nesta fase — nem a
    proposta, nem os candidatos, nem a resposta da leitura.
    """

    item: object = None
    estado_final: str = EstadoProcessamentoImportacao.PENDENTE
    tipo_de_consequencia: str = ""
    fatos: tuple[Fato, ...] = ()
    inferencias: tuple[Inferencia, ...] = ()
    candidatos: tuple[Candidato, ...] = ()
    proposta: Proposta = field(default_factory=Proposta)
    pergunta: str = ""
    necessita_confirmacao: bool = False
    falha: str = ""

    @property
    def concluiu(self) -> bool:
        """Se a leitura terminou sem deixar decisão pendente."""
        return self.estado_final == EstadoProcessamentoImportacao.PROCESSADO


class ErroLeituraEmail(Exception):
    """Falha controlada da leitura semântica de e-mail."""


class OrigemNaoSuportada(ErroLeituraEmail):
    """O item não é uma mensagem do Gmail — esta leitura não se aplica."""


class LeituraIndisponivel(ErroLeituraEmail):
    """Não houve leitura utilizável: serviço fora do ar ou resposta incompatível."""


# ---------------------------------------------------------------------------
# Pedido de leitura
# ---------------------------------------------------------------------------

INSTRUCAO = (
    "Você ajuda a ler uma mensagem de e-mail recebida pela administração de um "
    "condomínio, para uso interno do sistema Eithra.\n"
    "\n"
    "Responda SOMENTE com um objeto JSON, sem texto em volta, com exatamente "
    "estas chaves:\n"
    '{"consequencia": "", "equipamento": "", "local": "", "resumo": "", '
    '"pergunta": ""}\n'
    "\n"
    "Valores aceitos em consequencia: "
    "sem_acao = nada a fazer; atualizacao = atualiza algo já em andamento; "
    "nova_ocorrencia = algo novo aconteceu; documento = a mensagem traz "
    "documento; agendamento = visita ou serviço marcado; "
    "conclusao_declarada = alguém declarou serviço concluído; "
    "cobranca = pedido de pagamento; ambiguo = não deu para dizer.\n"
    "\n"
    "Regras, sem exceção:\n"
    "- Sem certeza, use ambiguo e deixe as demais strings vazias.\n"
    "- Nunca invente nome de equipamento, de local ou número de documento: "
    "copie exatamente como aparece na mensagem.\n"
    "- resumo é uma frase curta, em português, do que a mensagem quer dizer.\n"
    "- pergunta é, no máximo, uma pergunta útil a quem lê. Sem pergunta, use "
    "string vazia.\n"
    "- Não explique, não comente, não numere, não use bloco de código."
)

# Identificadores que a mensagem pode citar. Cada um exige ao menos um dígito,
# para que "os moradores" não vire número de ordem de serviço.
PADROES_DE_IDENTIFICADOR = (
    (
        "ordem_de_servico",
        re.compile(
            r"\b(?:o\.?\s?s\.?|os|ordem de servi[cç]o)\s*(?:n[ºo.]*)?\s*[:\-]?\s*"
            r"([a-z0-9][a-z0-9\-/]{2,})",
            re.IGNORECASE,
        ),
    ),
    (
        "orcamento",
        re.compile(
            r"\bor[cç]amento\s*(?:n[ºo.]*)?\s*[:\-]?\s*([a-z0-9][a-z0-9\-/]{2,})",
            re.IGNORECASE,
        ),
    ),
    (
        "contrato",
        re.compile(
            r"\bcontrato\s*(?:n[ºo.]*)?\s*[:\-]?\s*([a-z0-9][a-z0-9\-/]{2,})",
            re.IGNORECASE,
        ),
    ),
    (
        "documento",
        re.compile(
            r"\b(?:documento|doc\.?|laudo|ata)\s*(?:n[ºo.]*)?\s*[:\-]?\s*"
            r"([a-z0-9][a-z0-9\-/]{2,})",
            re.IGNORECASE,
        ),
    ),
)

PADRAO_DE_VALOR = re.compile(r"R\$\s?\d{1,3}(?:\.\d{3})*(?:,\d{2})?", re.IGNORECASE)
PADRAO_DE_DATA = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
PADRAO_DE_UNIDADE = re.compile(
    r"\b(bloco|torre|apto|apt|apartamento|unidade|casa)\s*[:\-]?\s*"
    r"([0-9][a-z0-9\-]{0,9})\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Leitura: fatos e consequência
# ---------------------------------------------------------------------------


def interpretar(mensagem, *, condominio=None, pedir_json=None) -> LeituraEmail:
    """Lê a mensagem e devolve o que ela sustenta, com o apoio que houver.

    A leitura determinística vem primeiro e é sempre ela que decide o que é
    ``SEM_ACAO`` ou conclusão declarada — casos em que pedir ajuda a um serviço
    de linguagem não acrescentaria nada. Fora desses dois, a leitura semântica
    é consultada; quando ela não responde, ou responde fora do combinado, a
    leitura não aconteceu e :class:`LeituraIndisponivel` sobe para quem chamou.

    ``pedir_json`` existe para que a leitura seja testável sem rede.
    """
    texto = _texto_da_mensagem(mensagem)
    fatos = _extrair_fatos(mensagem, texto=texto)
    consequencia = _consequencia_deterministica(fatos, texto=texto)

    if consequencia in {Consequencia.SEM_ACAO, Consequencia.CONCLUSAO_DECLARADA}:
        return _montar(fatos=fatos, consequencia=consequencia)

    consultar = pedir_json or llm.pedir_json
    resposta = consultar(
        sistema=INSTRUCAO, pedido=_pedido(mensagem, fatos, condominio=condominio)
    )
    if not isinstance(resposta, dict):
        raise LeituraIndisponivel("A leitura da mensagem não respondeu.")

    return _montar(
        fatos=fatos, consequencia=consequencia, resposta=resposta, mensagem=mensagem
    )


def _texto_da_mensagem(mensagem) -> str:
    """O que a leitura enxerga: assunto e corpo, um depois do outro."""
    partes = [getattr(mensagem, "assunto", "") or "", getattr(mensagem, "texto", "") or ""]
    return "\n".join(parte for parte in partes if parte.strip())


def _extrair_fatos(mensagem, *, texto: str) -> list[Fato]:
    """Os dados que a mensagem sustenta — e só eles.

    O que não está escrito não é inventado: ausência vira ausência, nunca um
    campo preenchido por dedução.
    """
    fatos: list[Fato] = []

    remetente = normalizar_endereco_email(getattr(mensagem, "remetente", ""))
    if remetente:
        fatos.append(Fato(campo="remetente", valor=remetente, motivo="Remetente da mensagem."))

    assunto = " ".join((getattr(mensagem, "assunto", "") or "").split())
    if assunto:
        fatos.append(Fato(campo="assunto", valor=assunto, motivo="Assunto da mensagem."))

    momento = getattr(mensagem, "momento", None)
    if momento is not None:
        fatos.append(
            Fato(
                campo="momento",
                valor=momento.isoformat(),
                rotulo=momento.strftime("%d/%m/%Y %H:%M"),
                motivo="Data e hora declaradas na mensagem.",
            )
        )

    for anexo in getattr(mensagem, "anexos", ()) or ():
        if anexo.nome:
            fatos.append(
                Fato(
                    campo="anexo",
                    valor=anexo.nome,
                    rotulo=anexo.nome,
                    motivo="A mensagem traz este arquivo anexado.",
                )
            )

    for campo, padrao in PADROES_DE_IDENTIFICADOR:
        valor = _com_digito(padrao, texto)
        if valor and not any(fato.campo == campo for fato in fatos):
            fatos.append(
                Fato(
                    campo=campo,
                    valor=valor,
                    motivo=f"A mensagem cita este {campo.replace('_', ' ')}.",
                )
            )

    valor = PADRAO_DE_VALOR.search(texto)
    if valor:
        fatos.append(
            Fato(campo="valor", valor=valor.group(0), motivo="Valor citado na mensagem.")
        )

    for encontrada in PADRAO_DE_DATA.findall(texto)[:3]:
        fatos.append(
            Fato(campo="data_citada", valor=encontrada, motivo="Data citada na mensagem.")
        )

    unidade = PADRAO_DE_UNIDADE.search(texto)
    if unidade:
        fatos.append(
            Fato(
                campo="unidade",
                valor=" ".join(parte for parte in unidade.groups() if parte),
                motivo="Unidade citada na mensagem.",
            )
        )

    return fatos


def _com_digito(padrao: re.Pattern, texto: str) -> str:
    """O valor capturado por um padrão, desde que contenha algum dígito."""
    encontrado = padrao.search(texto or "")
    if not encontrado:
        return ""
    valor = encontrado.group(1)
    return valor if any(caractere.isdigit() for caractere in valor) else ""


def _consequencia_deterministica(fatos, *, texto: str) -> str:
    """O que as regras locais já conseguem afirmar sobre a mensagem.

    A ordem importa: o recado curto de recebimento vem antes da conclusão, a
    conclusão antes do problema, e a ocorrência antes do agendamento — cada
    leitura é a mais específica que o texto sustenta.
    """
    lido = normalizar(texto)
    if not lido:
        return Consequencia.AMBIGUO

    if _sem_consequencia(fatos, lido=lido, texto=texto):
        return Consequencia.SEM_ACAO
    if contem(lido, PALAVRAS_DE_CONCLUSAO):
        return Consequencia.CONCLUSAO_DECLARADA
    if contem(lido, PALAVRAS_DE_COBRANCA):
        return Consequencia.COBRANCA
    if _tipo_de_problema(texto).existe:
        return Consequencia.NOVA_OCORRENCIA
    if contem(lido, PALAVRAS_DE_AGENDAMENTO):
        return Consequencia.AGENDAMENTO
    if any(fato.campo == "anexo" for fato in fatos):
        return Consequencia.DOCUMENTO
    return Consequencia.AMBIGUO


def _sem_consequencia(fatos, *, lido: str, texto: str) -> bool:
    """Se a mensagem é inequivocamente um recado, sem nada a fazer.

    Não basta conter "obrigado": a mensagem precisa ser curta, não trazer
    anexo, não citar identificador, não pedir nada e não descrever problema.
    Qualquer um desses sinais desliga o silêncio — e a leitura segue.
    """
    if any(fato.campo == "anexo" for fato in fatos):
        return False
    if any(fato.campo in {"ordem_de_servico", "orcamento", "contrato", "documento"} for fato in fatos):
        return False
    if contem(lido, PALAVRAS_DE_PEDIDO):
        return False
    if contem(lido, PALAVRAS_DE_CONCLUSAO) or contem(lido, PALAVRAS_DE_COBRANCA):
        return False
    if contem(lido, PALAVRAS_DE_AGENDAMENTO):
        return False
    if _tipo_de_problema(texto).existe:
        return False
    return contem(lido, PALAVRAS_DE_RECEBIMENTO) and len(lido.split()) <= LIMITE_DE_RECADO_CURTO


def _tipo_de_problema(texto: str):
    """A natureza do problema que o texto descreve, pelas regras de Demandas."""
    from apps.demandas import derivacao

    return derivacao.tipo_de_problema_do_relato(texto)


def _pedido(mensagem, fatos, *, condominio) -> str:
    """O que se pede ao serviço: a mensagem e o cadastro do condomínio.

    O cadastro vai junto para que o serviço reconheça o equipamento chamado
    pelo jeito de falar — e para que ele não tenha como inventar um nome que
    não existe ali.
    """
    linhas = [f"Assunto: {(getattr(mensagem, 'assunto', '') or '').strip()}"]
    linhas.append(f"Corpo:\n{_texto_da_mensagem(mensagem)[:LIMITE_DO_CORPO]}")

    nomes = [fato.texto for fato in fatos if fato.campo in {"anexo", "orcamento", "contrato"}]
    linhas.append("Já identificado na mensagem: " + (", ".join(nomes) if nomes else "nada"))

    locais, equipamentos = _cadastro(condominio)
    linhas.append(
        "Locais cadastrados neste condomínio: " + (", ".join(locais) if locais else "nenhum")
    )
    linhas.append(
        "Equipamentos cadastrados neste condomínio: "
        + (", ".join(equipamentos) if equipamentos else "nenhum")
    )
    return "\n".join(linhas)


def _cadastro(condominio) -> tuple[list[str], list[str]]:
    """Os nomes de locais e equipamentos deste condomínio, quando houver."""
    from apps.cadastros.models import Equipamento, LocalOperacional

    if condominio is None or not getattr(condominio, "pk", None):
        return [], []
    locais = [
        local.nome for local in LocalOperacional.objects.do_condominio(condominio).ativos()
    ]
    equipamentos = [
        equipamento.nome
        for equipamento in Equipamento.objects.do_condominio(condominio).ativos()
    ]
    return locais, equipamentos


def _montar(*, fatos, consequencia: str, resposta=None, mensagem=None) -> LeituraEmail:
    """Junta a leitura determinística com o que o serviço acrescentou.

    O que o serviço devolve é conferido contra o vocabulário antes de valer:
    consequência fora da lista, quando as regras não decidiram nada, significa
    resposta incompatível — e isso não é uma leitura, é uma falha.
    """
    inferencias: list[Inferencia] = []
    pergunta = ""
    if resposta:
        do_servico = _texto(resposta, "consequencia")
        if do_servico in Consequencia.values:
            consequencia = do_servico
        elif consequencia == Consequencia.AMBIGUO:
            raise LeituraIndisponivel("A leitura devolveu uma consequência incompatível.")

        resumo = _texto(resposta, "resumo")
        if resumo:
            inferencias.append(
                Inferencia(
                    campo="resumo",
                    rotulo=resumo,
                    confianca=Confianca.MEDIA,
                    motivo="Leitura da mensagem.",
                )
            )
        for campo in ("equipamento", "local"):
            valor = _texto(resposta, campo)
            if valor and not any(fato.campo == campo for fato in fatos):
                fatos.append(
                    Fato(
                        campo=campo,
                        valor=valor,
                        motivo="Citado na mensagem, ainda a confirmar no cadastro.",
                    )
                )
        pergunta = _texto(resposta, "pergunta")[:LIMITE_DA_PERGUNTA]

    return LeituraEmail(
        fatos=tuple(fatos),
        tipo_de_consequencia=consequencia,
        entidades_extraidas={fato.campo: fato for fato in fatos if fato.campo},
        inferencias=tuple(inferencias),
        necessita_confirmacao=consequencia != Consequencia.SEM_ACAO,
        pergunta=pergunta,
    )


def _texto(resposta, campo: str) -> str:
    valor = resposta.get(campo) if isinstance(resposta, dict) else None
    return " ".join(valor.split()) if isinstance(valor, str) else ""


# ---------------------------------------------------------------------------
# Reconciliação: contexto existente, na ordem em que ele pesa
# ---------------------------------------------------------------------------


def reconciliar(leitura: LeituraEmail, *, mensagem, item, condominio) -> LeituraEmail:
    """Cruza a leitura com o que o condomínio já tem, na ordem obrigatória.

    A ordem não é decorativa: o fio da conversa responde primeiro, depois o
    remetente, depois os identificadores citados, e só então o cadastro
    (equipamento, local) e o que está aberto (demanda, manutenção, documento,
    histórico). Nova ocorrência é a última hipótese, nunca a primeira.

    Nada aqui escreve no domínio: só se leem registros do condomínio atual.
    """
    texto = _texto_da_mensagem(mensagem)
    fatos = list(leitura.fatos)
    inferencias = list(leitura.inferencias)
    candidatos: list[Candidato] = []

    # 1. fio da conversa — sinal forte, mas não verdade absoluta.
    do_fio, conflito_de_fio = _candidatos_do_fio(
        item=item, condominio=condominio, leitura=leitura
    )
    candidatos.extend(do_fio)
    for candidato in do_fio:
        inferencias.append(
            Inferencia(
                campo="fio",
                valor=candidato.rotulo,
                rotulo=candidato.rotulo,
                confianca=candidato.confianca,
                objeto=candidato.objeto,
                motivo=candidato.motivo,
            )
        )

    # 2. remetente → fornecedor cadastrado neste condomínio.
    fornecedores, fornecedor_ambiguo = _fornecedores_do_remetente(mensagem, condominio)
    for fornecedor in fornecedores:
        candidatos.append(
            Candidato(
                tipo=TipoCandidato.FORNECEDOR,
                rotulo=fornecedor.nome_fantasia or fornecedor.nome_razao_social,
                origem=OrigemCandidato.REMETENTE,
                confianca=Confianca.ALTA,
                objeto=fornecedor,
                motivo="O remetente é um fornecedor cadastrado neste condomínio.",
            )
        )
    if len(fornecedores) == 1:
        fatos.append(
            Fato(
                campo="fornecedor",
                valor=fornecedores[0].nome_razao_social,
                rotulo=fornecedores[0].nome_fantasia or fornecedores[0].nome_razao_social,
                estado=Estado.CONFIRMADA,
                objeto=fornecedores[0],
                motivo="O remetente corresponde a este fornecedor no cadastro.",
            )
        )

    # 3. identificadores explícitos (contrato é o que existe no domínio hoje).
    for candidato in _candidatos_por_identificador(fatos, condominio):
        candidatos.append(candidato)

    # 4. equipamento — resolvido no cadastro, nunca inventado.
    equipamento, equipamento_ambiguo, mencao_equipamento = _equipamento_do_texto(
        texto, condominio
    )
    if equipamento is not None:
        candidatos.append(
            Candidato(
                tipo=TipoCandidato.EQUIPAMENTO,
                rotulo=equipamento.nome,
                origem=OrigemCandidato.EQUIPAMENTO,
                confianca=Confianca.ALTA,
                objeto=equipamento,
                motivo="O texto aponta este equipamento do cadastro.",
            )
        )
        fatos.append(
            Fato(
                campo="equipamento",
                valor=equipamento.nome,
                rotulo=equipamento.nome,
                estado=Estado.CONFIRMADA,
                objeto=equipamento,
                motivo="Confere com um equipamento cadastrado neste condomínio.",
            )
        )
    elif mencao_equipamento:
        # Citado sem corresponder a um cadastro: continua sendo o que a mensagem
        # disse, e não vira equipamento nenhum.
        fatos.append(
            Fato(
                campo="equipamento",
                valor=mencao_equipamento,
                motivo="Citado na mensagem; não há equipamento com esse nome no cadastro.",
            )
        )

    # 5. local — mesma regra, no mesmo condomínio.
    local = _local_do_texto(texto, condominio)
    if local is not None:
        candidatos.append(
            Candidato(
                tipo=TipoCandidato.LOCAL,
                rotulo=local.nome,
                origem=OrigemCandidato.LOCAL,
                confianca=Confianca.ALTA,
                objeto=local,
                motivo="O texto aponta este local do cadastro.",
            )
        )
        fatos.append(
            Fato(
                campo="local",
                valor=local.nome,
                rotulo=local.nome,
                estado=Estado.CONFIRMADA,
                objeto=local,
                motivo="Confere com um local cadastrado neste condomínio.",
            )
        )

    # 6. demanda aberta — só do condomínio atual.
    demandas = _demandas_abertas(condominio, equipamento=equipamento, local=local)
    for demanda in demandas:
        candidatos.append(
            Candidato(
                tipo=TipoCandidato.DEMANDA,
                rotulo=demanda.titulo,
                origem=OrigemCandidato.EQUIPAMENTO if equipamento else OrigemCandidato.LOCAL,
                confianca=Confianca.MEDIA,
                objeto=demanda,
                motivo="Há uma demanda aberta neste ponto do condomínio.",
            )
        )

    # 7. manutenção aberta — idem, e ainda mais provável quando é o fornecedor.
    manutencoes = _manutencoes_abertas(
        condominio, equipamento=equipamento, local=local, fornecedores=fornecedores
    )
    for manutencao in manutencoes:
        candidatos.append(
            Candidato(
                tipo=TipoCandidato.MANUTENCAO,
                rotulo=manutencao.titulo,
                origem=OrigemCandidato.REMETENTE
                if fornecedores
                else OrigemCandidato.EQUIPAMENTO,
                confianca=Confianca.MEDIA,
                objeto=manutencao,
                motivo="Há uma manutenção aberta compatível com esta mensagem.",
            )
        )

    # 8. documento relacionado — pelo nome do anexo ou por número citado.
    for documento in _documentos_candidatos(fatos, condominio):
        candidatos.append(
            Candidato(
                tipo=TipoCandidato.DOCUMENTO,
                rotulo=documento.titulo,
                origem=OrigemCandidato.IDENTIFICADOR,
                confianca=Confianca.MEDIA,
                objeto=documento,
                motivo="Já existe um documento com esta referência no condomínio.",
            )
        )

    # 9. histórico recente — quando o que aconteceu já aconteceu antes.
    inferencia_historico = _historico(condominio, texto=texto, equipamento=equipamento, local=local)
    if inferencia_historico is not None:
        inferencias.append(inferencia_historico)

    # 10. só então nova ocorrência — e ela não cria nada, apenas propõe.
    proposta, necessita_confirmacao, pergunta = _decidir(
        leitura,
        candidatos=candidatos,
        demandas=demandas,
        manutencoes=manutencoes,
        fornecedor_ambiguo=fornecedor_ambiguo,
        equipamento_ambiguo=equipamento_ambiguo,
        conflito_de_fio=conflito_de_fio,
        texto=texto,
        condominio=condominio,
    )

    return LeituraEmail(
        fatos=tuple(fatos),
        tipo_de_consequencia=leitura.tipo_de_consequencia,
        entidades_extraidas={fato.campo: fato for fato in fatos if fato.campo},
        inferencias=tuple(inferencias),
        candidatos=tuple(candidatos),
        proposta=proposta,
        necessita_confirmacao=necessita_confirmacao,
        pergunta=pergunta,
    )


def _candidatos_do_fio(*, item, condominio, leitura) -> tuple[list[Candidato], bool]:
    """O que o fio da conversa já explicava — e se ele mudou de assunto.

    Quando a mensagem fala de ocorrência nova e o fio aponta para algo já
    encerrado, o fio não decide nada: ele vira um candidato desacreditado e a
    decisão passa a ser humana.
    """
    if not getattr(item, "thread_id", ""):
        return [], False

    anteriores = (
        ItemImportacaoHistorica.objects.do_condominio(condominio)
        .filter(
            origem=OrigemImportacaoHistorica.GMAIL,
            thread_id=item.thread_id,
        )
        .exclude(pk=item.pk)
        .prefetch_related("vinculos")
    )

    candidatos: list[Candidato] = []
    conflito = False
    vistos: set = set()
    for anterior in anteriores:
        for vinculo in anterior.vinculos.all():
            destino = _objeto_do_vinculo(vinculo)
            if destino is None or destino.pk in vistos:
                continue
            vistos.add(destino.pk)
            encerrado = _encerrado(destino)
            mudou_de_assunto = (
                encerrado and leitura.tipo_de_consequencia == Consequencia.NOVA_OCORRENCIA
            )
            conflito = conflito or mudou_de_assunto
            candidatos.append(
                Candidato(
                    tipo=TipoCandidato.THREAD,
                    rotulo=_rotulo_do(vinculo.entidade_destino, destino),
                    origem=OrigemCandidato.THREAD,
                    confianca=Confianca.BAIXA if mudou_de_assunto else Confianca.ALTA,
                    objeto=destino,
                    motivo=(
                        "O fio já apontava para isto, mas parece ter mudado de assunto."
                        if mudou_de_assunto
                        else "O mesmo fio da conversa já foi relacionado a isto."
                    ),
                )
            )
    return candidatos, conflito


def _objeto_do_vinculo(vinculo):
    """O registro de domínio apontado por um vínculo, dentro do condomínio."""
    if not getattr(vinculo, "destino_id", None):
        return None
    modelos = {
        EntidadeImportacaoHistorica.DEMANDA: _modelo_demanda,
        EntidadeImportacaoHistorica.MANUTENCAO: _modelo_manutencao,
    }
    obter = modelos.get(vinculo.entidade_destino)
    if obter is None:
        return None
    modelo = obter()
    return (
        modelo.objects.filter(pk=vinculo.destino_id, condominio_id=vinculo.condominio_id)
        .first()
    )


def _modelo_demanda():
    from apps.demandas.models import Demanda

    return Demanda


def _modelo_manutencao():
    from apps.manutencoes.models import Manutencao

    return Manutencao


def _rotulo_do(entidade_destino: str, destino) -> str:
    if entidade_destino == EntidadeImportacaoHistorica.MANUTENCAO:
        return f"Manutenção: {destino.titulo}"
    return f"Demanda: {destino.titulo}"


def _encerrado(destino) -> bool:
    """Se o registro apontado pelo fio já saiu da operação.

    ``esta_encerrada`` é propriedade em Demanda e em Manutenção — o acesso já
    devolve o booleano. O ramo chamável fica porque um domínio futuro pode
    expor isto como método, e a leitura não deve depender da forma.
    """
    esta_encerrada = getattr(destino, "esta_encerrada", None)
    if esta_encerrada is None:
        return False
    if callable(esta_encerrada):
        return bool(esta_encerrada())
    return bool(esta_encerrada)


def _fornecedores_do_remetente(mensagem, condominio) -> tuple[list, bool]:
    """O fornecedor cadastrado com o endereço do remetente, quando é um só.

    Endereço canônico, comparação exata: o nome de exibição não decide nada, e
    um endereço que não está no cadastro não cria fornecedor nenhum.
    """
    from apps.cadastros.models import Fornecedor

    endereco = normalizar_endereco_email(getattr(mensagem, "remetente", ""))
    if not endereco or condominio is None or not getattr(condominio, "pk", None):
        return [], False
    achados = list(
        Fornecedor.objects.do_condominio(condominio).filter(email=endereco).order_by("nome_razao_social")
    )
    if len(achados) > 1:
        # Ambiguidade declarada: os candidatos vão, mas a decisão é humana.
        return achados, True
    return achados, False


def _candidatos_por_identificador(fatos, condominio) -> list[Candidato]:
    """Contrato citado na mensagem, quando ele existe neste condomínio."""
    numero = next(
        (fato.valor for fato in fatos if fato.campo == "contrato" and fato.valor), ""
    )
    if not numero or condominio is None or not getattr(condominio, "pk", None):
        return []

    from apps.governanca.models import ContratoFornecedor

    contratos = (
        ContratoFornecedor.objects.do_condominio(condominio)
        .filter(numero_referencia__iexact=numero)
        .order_by("-criado_em")[:3]
    )
    return [
        Candidato(
            tipo=TipoCandidato.CONTRATO,
            rotulo=contrato.numero_referencia or str(contrato.pk),
            origem=OrigemCandidato.IDENTIFICADOR,
            confianca=Confianca.ALTA,
            objeto=contrato,
            motivo="O número citado corresponde a um contrato deste condomínio.",
        )
        for contrato in contratos
    ]


def _equipamento_do_texto(texto: str, condominio) -> tuple[object | None, bool, str]:
    """O equipamento que o texto aponta, se o cadastro decidir por um só.

    Devolve o equipamento, se há ambiguidade de cadastro, e a menção textual
    quando o texto cita um equipamento que não existe no cadastro.
    """
    from apps.demandas import derivacao

    equipamento, sugestao = derivacao.equipamento_do_relato(texto, condominio)
    if equipamento is not None:
        return equipamento, False, ""
    if sugestao.motivo:
        # O cadastro não respondeu por um só: ou há vários, ou o texto citou o
        # tipo sem dizer qual. Nos dois casos, quem decide é a pessoa.
        return None, True, ""
    return None, False, ""


def _local_do_texto(texto: str, condominio):
    from apps.demandas import derivacao

    return derivacao.local_do_relato(texto, condominio)


def _demandas_abertas(condominio, *, equipamento, local) -> list:
    """As demandas abertas do condomínio no mesmo ponto da mensagem."""
    from apps.demandas.models import Demanda

    if condominio is None or not getattr(condominio, "pk", None):
        return []
    filtro = Q()
    if equipamento is not None:
        filtro |= Q(equipamento_id=equipamento.pk)
    if local is not None:
        filtro |= Q(local_id=local.pk)
    if not filtro:
        return []
    return list(
        Demanda.objects.filter(condominio_id=condominio.pk)
        .abertas()
        .filter(filtro)
        .order_by("-criado_em")[:5]
    )


def _manutencoes_abertas(condominio, *, equipamento, local, fornecedores) -> list:
    """As manutenções abertas do condomínio compatíveis com a mensagem."""
    from apps.manutencoes.models import Manutencao

    if condominio is None or not getattr(condominio, "pk", None):
        return []
    filtro = Q()
    if equipamento is not None:
        filtro |= Q(equipamento_id=equipamento.pk)
    if local is not None:
        filtro |= Q(local_id=local.pk)
    if fornecedores:
        filtro |= Q(fornecedor_id__in=[fornecedor.pk for fornecedor in fornecedores])
    if not filtro:
        return []
    return list(
        Manutencao.objects.do_condominio(condominio)
        .abertas()
        .filter(filtro)
        .order_by("-criado_em")[:5]
    )


def _documentos_candidatos(fatos, condominio) -> list:
    """Documentos do condomínio que a mensagem parece estar citando."""
    from apps.governanca.models import DocumentoOperacional

    if condominio is None or not getattr(condominio, "pk", None):
        return []
    filtro = Q()
    for fato in fatos:
        if fato.campo == "documento" and fato.valor:
            filtro |= Q(numero_referencia__iexact=fato.valor)
        elif fato.campo == "anexo" and fato.valor:
            radical = _radical_do_arquivo(fato.valor)
            if len(radical) >= 4:
                filtro |= Q(titulo__icontains=radical)
    if not filtro:
        return []
    return list(
        DocumentoOperacional.objects.filter(condominio_id=condominio.pk, ativo=True)
        .filter(filtro)
        .order_by("-criado_em")[:5]
    )


def _radical_do_arquivo(nome: str) -> str:
    """O nome do arquivo sem a extensão — o que dá para casar com um título."""
    return (nome or "").rsplit(".", 1)[0].strip()


def _historico(condominio, *, texto, equipamento, local):
    """Se o mesmo ponto já teve ocorrência parecida, dentro da janela recente."""
    from apps.demandas import derivacao

    sugestao = derivacao.recorrencia_do_relato(
        texto, condominio, local=local, equipamento=equipamento
    )
    if not sugestao.existe:
        return None
    return Inferencia(
        campo="historico",
        valor=sugestao.valor,
        confianca=sugestao.confianca,
        motivo=sugestao.motivo,
    )


# ---------------------------------------------------------------------------
# Decisão
# ---------------------------------------------------------------------------


def _decidir(
    leitura,
    *,
    candidatos,
    demandas,
    manutencoes,
    fornecedor_ambiguo,
    equipamento_ambiguo,
    conflito_de_fio,
    texto,
    condominio,
):
    """A proposta e a necessidade (ou não) de confirmação humana.

    O silêncio só é resposta quando a leitura é inequívoca: mensagem sem
    consequência e sem ambiguidade nenhuma. Todo o resto exige decisão humana,
    porque nesta fase nada pode ser gravado sem ela.
    """
    consequencia = leitura.tipo_de_consequencia

    if consequencia == Consequencia.SEM_ACAO:
        return (
            Proposta(
                acao=AcaoProposta.NENHUMA,
                confianca=Confianca.ALTA,
                rotulo="Nada a fazer com esta mensagem",
                motivo="A mensagem não pede ação e não muda nada no condomínio.",
            ),
            False,
            "",
        )

    if fornecedor_ambiguo or equipamento_ambiguo or conflito_de_fio:
        return (
            Proposta(
                acao=AcaoProposta.CONFIRMAR_CONTEXTO,
                confianca=Confianca.BAIXA,
                rotulo="Confirmar a que isto se refere",
                motivo=_motivo_da_ambiguidade(
                    fornecedor_ambiguo=fornecedor_ambiguo,
                    equipamento_ambiguo=equipamento_ambiguo,
                    conflito_de_fio=conflito_de_fio,
                ),
            ),
            True,
            _pergunta_da_ambiguidade(
                fornecedor_ambiguo=fornecedor_ambiguo,
                equipamento_ambiguo=equipamento_ambiguo,
                conflito_de_fio=conflito_de_fio,
                texto=texto,
                condominio=condominio,
                leitura=leitura,
            ),
        )

    if consequencia == Consequencia.CONCLUSAO_DECLARADA:
        alvo = (manutencoes or demandas or [None])[0]
        return (
            Proposta(
                acao=AcaoProposta.AVALIAR_CONCLUSAO,
                confianca=Confianca.MEDIA,
                rotulo="Avaliar a conclusão declarada",
                motivo=(
                    "O fornecedor declarou a conclusão do serviço. Declarar não é "
                    "concluir: quem encerra é a gestão."
                ),
                candidato=_candidato_de(alvo, manutencoes, demandas),
            ),
            True,
            "" if alvo is not None else "A qual serviço esta conclusão se refere?",
        )

    if consequencia in {Consequencia.DOCUMENTO, Consequencia.COBRANCA, Consequencia.AGENDAMENTO}:
        acao, rotulo = {
            Consequencia.DOCUMENTO: (
                AcaoProposta.REGISTRAR_DOCUMENTO,
                "Considerar o documento recebido",
            ),
            Consequencia.COBRANCA: (AcaoProposta.REGISTRAR_COBRANCA, "Considerar a cobrança"),
            Consequencia.AGENDAMENTO: (
                AcaoProposta.REGISTRAR_AGENDAMENTO,
                "Considerar o agendamento declarado",
            ),
        }[consequencia]
        return (
            Proposta(
                acao=acao,
                confianca=Confianca.MEDIA,
                rotulo=rotulo,
                motivo="A mensagem traz informação que costuma gerar acompanhamento.",
            ),
            True,
            "",
        )

    # Ocorrência nova ou atualização: o contexto existente vem antes de criar.
    alvo_manutencao = _candidato_de(manutencoes[0] if manutencoes else None, manutencoes, demandas)
    alvo_demanda = _candidato_de(demandas[0] if demandas else None, manutencoes, demandas)

    if len(manutencoes) == 1 and not demandas:
        return (
            Proposta(
                acao=AcaoProposta.RELACIONAR_MANUTENCAO,
                confianca=Confianca.MEDIA,
                rotulo="Relacionar à manutenção em andamento",
                motivo="Há uma manutenção aberta compatível com esta mensagem.",
                candidato=alvo_manutencao,
            ),
            True,
            "",
        )
    if len(demandas) == 1 and not manutencoes:
        return (
            Proposta(
                acao=AcaoProposta.RELACIONAR_DEMANDA,
                confianca=Confianca.MEDIA,
                rotulo="Relacionar à demanda em aberto",
                motivo="Há uma demanda aberta no mesmo ponto desta mensagem.",
                candidato=alvo_demanda,
            ),
            True,
            "",
        )
    if demandas or manutencoes:
        return (
            Proposta(
                acao=AcaoProposta.CONFIRMAR_CONTEXTO,
                confianca=Confianca.BAIXA,
                rotulo="Escolher a que isto se refere",
                motivo="Há mais de um registro aberto compatível com esta mensagem.",
            ),
            True,
            "Há mais de um registro aberto compatível. A qual deles esta mensagem se refere?",
        )

    if consequencia == Consequencia.NOVA_OCORRENCIA:
        return (
            Proposta(
                acao=AcaoProposta.CRIAR_NOVA_DEMANDA,
                confianca=Confianca.MEDIA,
                rotulo="Considerar uma nova demanda",
                motivo="A mensagem descreve algo novo, sem registro aberto correspondente.",
            ),
            True,
            "",
        )

    return (
        Proposta(
            acao=AcaoProposta.CONFIRMAR_CONTEXTO,
            confianca=Confianca.BAIXA,
            rotulo="Confirmar o que esta mensagem muda",
            motivo="A mensagem foi lida, mas não ficou claro o que ela muda agora.",
        ),
        True,
        "",
    )


def _candidato_de(alvo, manutencoes, demandas) -> Candidato | None:
    """O candidato correspondente a um registro, para a proposta carregar junto."""
    if alvo is None:
        return None
    tipo = TipoCandidato.MANUTENCAO if alvo in manutencoes else TipoCandidato.DEMANDA
    return Candidato(tipo=tipo, rotulo=alvo.titulo, objeto=alvo)


def _motivo_da_ambiguidade(*, fornecedor_ambiguo, equipamento_ambiguo, conflito_de_fio) -> str:
    if conflito_de_fio:
        return "O fio da conversa aponta para algo já encerrado, e a mensagem fala de algo novo."
    if fornecedor_ambiguo:
        return "Há mais de um fornecedor cadastrado com este endereço."
    return "Há mais de um equipamento com esse nome no condomínio."


def _pergunta_da_ambiguidade(
    *, fornecedor_ambiguo, equipamento_ambiguo, conflito_de_fio, texto, condominio, leitura
) -> str:
    """A única pergunta que vale a pena fazer agora.

    Não se pergunta para preencher campo: pergunta-se o que muda a decisão.
    """
    if conflito_de_fio:
        return "Esta mensagem é sobre um assunto novo ou continua o anterior?"
    if fornecedor_ambiguo:
        return "Há mais de um fornecedor com este endereço. Qual deles enviou a mensagem?"
    from apps.demandas import derivacao

    tipo_problema = _tipo_de_problema(texto)
    pergunta = derivacao.pergunta_do_relato(
        texto, condominio, equipamento=None, tipo_problema=tipo_problema.valor
    )
    return pergunta[:LIMITE_DA_PERGUNTA]


# ---------------------------------------------------------------------------
# Processamento do item
# ---------------------------------------------------------------------------


def processar_item_gmail(item, *, provedor=None, interpretador=None) -> ResultadoProcessamentoEmail:
    """Conduz um item do Gmail pela leitura e para antes de qualquer gravação.

    A função faz oito coisas, nesta ordem: confere que o item é do Gmail; move
    o item para ``PROCESSANDO``; recupera a mensagem no provider; interpreta os
    fatos; busca o contexto; reconcilia os candidatos; monta o resultado; e
    fecha o item em ``PROCESSADO``, ``AGUARDANDO_CONFIRMACAO`` ou ``ERRO``.

    ``PROCESSADO`` só acontece quando não há nada a decidir. Qualquer proposta
    com efeito operacional deixa o item em ``AGUARDANDO_CONFIRMACAO`` — nada é
    gravado no domínio nesta fase.

    Provedor e interpretador são recebidos de fora para que a leitura seja
    testável sem rede e sem serviço de linguagem.
    """
    if item is None or not getattr(item, "pk", None):
        raise ValidationError({"item": "Diga qual item da carga deve ser lido."})
    if item.origem != OrigemImportacaoHistorica.GMAIL:
        raise OrigemNaoSuportada("Esta leitura só se aplica a mensagens do Gmail.")
    if not (item.message_id or "").strip():
        raise OrigemNaoSuportada("O item do Gmail está sem o identificador da mensagem.")

    # O provedor é montado antes de mexer no item: falta de configuração não é
    # falha da mensagem, e não deve marcar item nenhum como erro.
    leitor = provedor if provedor is not None else gmail.obter_provedor()
    ler = interpretador or interpretar
    objetos = type(item).objects

    item = objetos.alterar_estado_processamento(
        item=item,
        novo_estado=EstadoProcessamentoImportacao.PROCESSANDO,
        reprocessamento_explicito=True,
    )

    try:
        mensagem = leitor.obter_mensagem(item.message_id)
    except gmail.ErroGmail as erro:
        return _encerrar_com_falha(item, falha=type(erro).__name__)

    try:
        leitura = ler(mensagem, condominio=item.condominio)
    except LeituraIndisponivel as erro:
        logger.warning("Leitura indisponível para o item %s: %s.", item.pk, erro)
        return _encerrar_com_falha(item, falha=LeituraIndisponivel.__name__)

    leitura = reconciliar(leitura, mensagem=mensagem, item=item, condominio=item.condominio)

    estado = (
        EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO
        if leitura.necessita_confirmacao
        else EstadoProcessamentoImportacao.PROCESSADO
    )
    item = objetos.alterar_estado_processamento(item=item, novo_estado=estado)
    logger.info(
        "Item %s lido: %s (%s).",
        item.pk,
        leitura.tipo_de_consequencia,
        estado,
    )

    return ResultadoProcessamentoEmail(
        item=item,
        estado_final=estado,
        tipo_de_consequencia=leitura.tipo_de_consequencia,
        fatos=leitura.fatos,
        inferencias=leitura.inferencias,
        candidatos=leitura.candidatos,
        proposta=leitura.proposta,
        pergunta=leitura.pergunta,
        necessita_confirmacao=leitura.necessita_confirmacao,
    )


def _encerrar_com_falha(item, *, falha: str) -> ResultadoProcessamentoEmail:
    """Fecha o item em ``ERRO`` sem criar consequência nenhuma.

    A identidade e a proveniência continuam intactas: só o estado muda, e o
    retry usa o mesmo caminho de sempre.
    """
    item = type(item).objects.alterar_estado_processamento(
        item=item, novo_estado=EstadoProcessamentoImportacao.ERRO
    )
    logger.warning("Leitura do item %s terminou em erro (%s).", item.pk, falha)
    return ResultadoProcessamentoEmail(
        item=item,
        estado_final=EstadoProcessamentoImportacao.ERRO,
        falha=falha,
    )
