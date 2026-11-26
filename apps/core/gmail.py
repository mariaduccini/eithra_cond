"""Leitura do Gmail como fonte externa — sem interpretar e sem guardar.

Este módulo é a primeira camada da integração recorrente com o Gmail: ele fala
com a API, normaliza o que volta e para aí.

    Gmail API → provider → MensagemGmail normalizada

O que este módulo **não** faz, de propósito:

* não interpreta o conteúdo, não chama nenhum serviço de linguagem e não decide
  o que a mensagem significa;
* não cria ``ItemImportacaoHistorica``, não mexe em estado de processamento, não
  reconcilia fornecedor, não procura demanda nem manutenção, não cria documento,
  não grava anexo e não cria vínculo. Nenhum domínio operacional é chamado aqui;
* não persiste nada — nem o corpo, nem o anexo, nem um cursor de sincronização.
  Ele não tem model, migration, scheduler ou management command.

A leitura é **somente leitura** (``gmail.readonly``): este módulo não marca
mensagem como lida, não arquiva, não exclui, não responde e não envia. O que a
mensagem provoca no domínio é decisão da camada de cima — e essa decisão não
mora aqui.

Três compromissos, na mesma ordem de ``apps/core/audio.py``:

* **nada é obrigatório**: sem credencial configurada, ``configurado()`` diz que
  não e o provedor recusa com ``GmailNaoConfigurado`` — o resto do sistema
  continua funcionando, sem erro 500.
* **nada vaza**: o *client secret*, o *refresh token* e o token de acesso vivem
  no ambiente, são usados só no backend e nunca entram em log; o que se registra
  é a operação e o tipo de falha, jamais o corpo da mensagem, o conteúdo do
  anexo ou a credencial.
* **nada se confunde**: a falha chega à camada de cima como exceção de domínio
  — não configurado, credencial inválida, indisponível, mensagem inexistente,
  anexo inexistente, resposta inválida —, e não como erro bruto da rede.

O provedor se apoia na biblioteca padrão, como o resto do núcleo: não há SDK de
fornecedor de nuvem aqui. A API do Gmail e o endpoint de token são HTTP + JSON,
o mesmo contrato de ``apps/core/storage.py`` e ``apps/core/audio.py``.

Autenticação: o provedor usa o fluxo *refresh token* (servidor a servidor). O
*refresh token* é obtido uma única vez, fora do Eithra, por consentimento do
responsável pela conta — o Eithra não abre tela de consentimento nesta fase.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

from django.conf import settings

from apps.core.texto import normalizar_endereco_email

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Limites e endereços
# ---------------------------------------------------------------------------

# O menor escopo que resolve a leitura. Não se pede ``gmail.modify``: esta
# integração não marca, não arquiva, não exclui, não responde e não envia.
ESCOPO_LEITURA = "https://www.googleapis.com/auth/gmail.readonly"

ENDERECO_TOKEN = "https://oauth2.googleapis.com/token"
ENDERECO_API = "https://gmail.googleapis.com/gmail/v1/users/me"

# Tempo máximo de espera ao conversar com a API. Curto de propósito: uma espera
# longa aqui segura quem estiver lendo a caixa.
TEMPO_LIMITE_PADRAO = 30.0

QUANTIDADE_PADRAO = 100
# A própria API do Gmail limita ``maxResults`` a 500.
QUANTIDADE_MAXIMA = 500


# ---------------------------------------------------------------------------
# Falhas de domínio
# ---------------------------------------------------------------------------
# A camada de cima distingue "não configurado" de "falha temporária" e de
# "credencial inválida" por aqui — nunca inspecionando a exceção bruta da rede.
# A mensagem é curta e técnica; nenhuma delas carrega token, segredo, corpo ou
# anexo.


class ErroGmail(Exception):
    """Falha controlada ao conversar com o Gmail."""


class GmailNaoConfigurado(ErroGmail):
    """O acesso ao Gmail não está configurado no ambiente."""

    def __init__(self, mensagem: str = "O acesso ao Gmail não está configurado."):
        super().__init__(mensagem)


class GmailCredencialInvalida(ErroGmail):
    """A credencial foi recusada pelo Gmail."""

    def __init__(self, mensagem: str = "O acesso ao Gmail foi recusado."):
        super().__init__(mensagem)


class GmailIndisponivel(ErroGmail):
    """O Gmail não respondeu, ou está limitando o acesso agora."""

    def __init__(self, mensagem: str = "O Gmail não respondeu."):
        super().__init__(mensagem)


class GmailRespostaInvalida(ErroGmail):
    """O Gmail respondeu de uma forma que o provider não reconhece."""

    def __init__(self, mensagem: str = "O Gmail respondeu de forma inesperada."):
        super().__init__(mensagem)


class GmailMensagemInexistente(ErroGmail):
    """A mensagem pedida não existe — ou já não está acessível à conta."""

    def __init__(self, mensagem: str = "A mensagem não foi encontrada no Gmail."):
        super().__init__(mensagem)


class GmailAnexoInexistente(ErroGmail):
    """O anexo pedido não existe na mensagem."""

    def __init__(self, mensagem: str = "O anexo não foi encontrado no Gmail."):
        super().__init__(mensagem)


# ---------------------------------------------------------------------------
# Contratos de dados (em memória, nunca persistidos)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnexoGmail:
    """Um anexo identificado, ainda **sem** os bytes.

    ``attachment_id`` é o que se usa em :meth:`ProvedorGmail.baixar_anexo`. Os
    bytes não vêm junto de propósito: baixar todos os anexos de todas as
    mensagens seria caro e desnecessário para quem só quer saber o que chegou.
    """

    attachment_id: str = ""
    nome: str = ""
    mime_type: str = ""
    # Em bytes. Zero quando a API não informa o tamanho.
    tamanho: int = 0


@dataclass(frozen=True)
class MensagemGmail:
    """Uma mensagem normalizada — o dado externo em memória.

    Existe só durante a leitura: não é gravada em banco, não é registrada em
    log. ``remetente`` e ``destinatarios`` já vêm canônicos (só o endereço, sem
    o nome de exibição). ``history_id`` é metadado técnico da API, guardado aqui
    para que a sincronização incremental futura não precise reabrir este
    contrato — nesta fase ele não é persistido em lugar nenhum.
    """

    message_id: str = ""
    thread_id: str = ""
    remetente: str = ""
    destinatarios: tuple[str, ...] = ()
    assunto: str = ""
    momento: datetime | None = None
    texto: str = ""
    anexos: tuple[AnexoGmail, ...] = ()
    history_id: str = ""


@dataclass(frozen=True)
class ReferenciaMensagem:
    """A identificação de uma mensagem na listagem, sem baixar o conteúdo."""

    message_id: str = ""
    thread_id: str = ""


@dataclass(frozen=True)
class PaginaDeMensagens:
    """Uma página de referências, com o cursor da próxima."""

    itens: tuple[ReferenciaMensagem, ...] = ()
    proxima_pagina: str = ""


@dataclass(frozen=True)
class CredenciaisGmail:
    """O que o provider precisa para autenticar — e nada além disso."""

    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""
    conta: str = ""


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------


def credenciais_do_ambiente() -> CredenciaisGmail | None:
    """As credenciais do Gmail declaradas no ambiente, ou ``None``.

    Sem as três variáveis obrigatórias não há como autenticar: devolver ``None``
    é o "não configurado", e não um erro. A ausência da conta declarada não
    impede a leitura — ela é apenas rótulo humano.
    """
    client_id = (getattr(settings, "EITHRA_GMAIL_CLIENT_ID", "") or "").strip()
    client_secret = (getattr(settings, "EITHRA_GMAIL_CLIENT_SECRET", "") or "").strip()
    refresh_token = (getattr(settings, "EITHRA_GMAIL_REFRESH_TOKEN", "") or "").strip()
    conta = (getattr(settings, "EITHRA_GMAIL_CONTA", "") or "").strip()

    if not (client_id and client_secret and refresh_token):
        return None
    return CredenciaisGmail(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
        conta=conta,
    )


def ativo() -> bool:
    """Se a integração com o Gmail está ligada neste ambiente.

    É o interruptor de cima: desligado (o padrão), nada da integração acontece —
    o comando não roda, as telas e os blocos de e-mail somem e nenhuma leitura é
    feita. Estar ligado não basta por si: ainda é preciso haver credencial para
    falar com a API, e por isso ``configurado()`` continua sendo a pergunta sobre
    a credencial.
    """
    return bool(getattr(settings, "EITHRA_GMAIL_ATIVO", False))


def configurado() -> bool:
    """Se há credencial suficiente para falar com o Gmail.

    Quem chama usa isto para decidir se oferece a leitura — a ausência de
    configuração é o estado normal de um ambiente que ainda não conectou a
    conta, e não motivo de erro.
    """
    return credenciais_do_ambiente() is not None


def obter_provedor() -> "ProvedorGmail":
    """Monta o provedor com as credenciais do ambiente.

    Recusa cedo, com ``GmailNaoConfigurado``, quando falta credencial — em vez
    de deixar a falha aparecer como erro de rede na primeira chamada.
    """
    credenciais = credenciais_do_ambiente()
    if credenciais is None:
        raise GmailNaoConfigurado()
    return ProvedorGmail(credenciais=credenciais)


# ---------------------------------------------------------------------------
# Provedor
# ---------------------------------------------------------------------------


class ProvedorGmail:
    """Lê mensagens e anexos do Gmail e devolve dados normalizados.

    O token de acesso é obtido sob demanda, a partir do *refresh token*, e
    reaproveitado enquanto estiver válido — sem gravar nada em disco, banco ou
    log.
    """

    def __init__(self, *, credenciais: CredenciaisGmail | None = None):
        self._credenciais_dadas = credenciais
        self._token = ""
        self._token_expira_em = 0.0

    # -- Interface pública --------------------------------------------------

    def listar_mensagens(
        self,
        *,
        consulta: str = "",
        quantidade: int = QUANTIDADE_PADRAO,
        pagina_token: str = "",
    ) -> PaginaDeMensagens:
        """Lista referências de mensagens, uma página por chamada.

        ``consulta`` é a busca do Gmail (``q``) — ex.: ``in:inbox newer_than:7d``.
        A paginação é do Gmail: passe ``proxima_pagina`` de volta como
        ``pagina_token`` na chamada seguinte.
        """
        carga = self._requisitar(
            "messages",
            params={
                "maxResults": _quantidade_valida(quantidade),
                "q": (consulta or "").strip(),
                "pageToken": (pagina_token or "").strip(),
            },
        )
        if not isinstance(carga, dict):
            raise GmailRespostaInvalida()

        itens = tuple(
            ReferenciaMensagem(
                message_id=str(item.get("id") or ""),
                thread_id=str(item.get("threadId") or ""),
            )
            for item in (carga.get("messages") or [])
            if isinstance(item, dict) and item.get("id")
        )
        return PaginaDeMensagens(
            itens=itens,
            proxima_pagina=str(carga.get("nextPageToken") or ""),
        )

    def obter_mensagem(self, message_id: str) -> MensagemGmail:
        """Devolve a mensagem normalizada: cabeçalhos, texto e anexos.

        O texto é o ``text/plain`` quando existe; se só houver HTML, ele é
        convertido para texto. Não há limpeza semântica aqui — assinatura,
        rodapé e histórico da conversa continuam no texto e são assunto de quem
        interpreta, não desta camada.
        """
        if not (message_id or "").strip():
            raise GmailMensagemInexistente("A mensagem não foi informada.")
        carga = self._requisitar(
            f"messages/{message_id}",
            params={"format": "full"},
            ausente_em_404=GmailMensagemInexistente,
        )
        return _mensagem_do_json(carga)

    def baixar_anexo(self, message_id: str, attachment_id: str) -> bytes:
        """Baixa os bytes de um anexo, só quando eles são realmente necessários.

        É a única operação que traz conteúdo binário. Nada é gravado: os bytes
        voltam em memória para quem chamou decidir o que fazer.
        """
        if not (attachment_id or "").strip():
            raise GmailAnexoInexistente("O anexo não foi informado.")
        carga = self._requisitar(
            f"messages/{message_id}/attachments/{attachment_id}",
            ausente_em_404=GmailAnexoInexistente,
        )
        dados = carga.get("data") if isinstance(carga, dict) else None
        if not dados:
            raise GmailAnexoInexistente("O Gmail não devolveu o conteúdo do anexo.")
        try:
            return base64.urlsafe_b64decode(_com_preenchimento(str(dados)))
        except (binascii.Error, ValueError) as erro:
            logger.warning("Gmail devolveu anexo ilegível (message_id %s).", message_id)
            raise GmailRespostaInvalida("O anexo chegou em formato inesperado.") from erro

    # -- Autenticação -------------------------------------------------------

    def _credenciais_ativas(self) -> CredenciaisGmail:
        if self._credenciais_dadas is not None:
            return self._credenciais_dadas
        credenciais = credenciais_do_ambiente()
        if credenciais is None:
            raise GmailNaoConfigurado()
        return credenciais

    def _token_de_acesso(self) -> str:
        """O token de acesso, renovado pelo *refresh token* quando necessário."""
        agora = time.monotonic()
        if self._token and agora < self._token_expira_em:
            return self._token

        credenciais = self._credenciais_ativas()
        corpo = urllib.parse.urlencode(
            {
                "client_id": credenciais.client_id,
                "client_secret": credenciais.client_secret,
                "refresh_token": credenciais.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
        requisicao = urllib.request.Request(
            ENDERECO_TOKEN,
            data=corpo,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        try:
            with urllib.request.urlopen(
                requisicao, timeout=self._tempo_limite()
            ) as resposta:
                carga = json.loads(resposta.read() or b"{}")
        except urllib.error.HTTPError as erro:
            # O corpo do erro do endpoint de token não é registrado: ele pode
            # ecoar o que foi enviado.
            logger.warning("Autenticação do Gmail recusada (HTTP %s).", erro.code)
            raise GmailCredencialInvalida() from erro
        except (urllib.error.URLError, TimeoutError, OSError) as erro:
            logger.warning(
                "Gmail indisponível ao autenticar (%s).", type(erro).__name__
            )
            raise GmailIndisponivel("Não foi possível autenticar no Gmail agora.") from erro
        except (json.JSONDecodeError, UnicodeDecodeError) as erro:
            logger.warning("Autenticação do Gmail devolveu resposta ilegível.")
            raise GmailRespostaInvalida() from erro

        token = str((carga or {}).get("access_token") or "") if isinstance(carga, dict) else ""
        if not token:
            logger.warning("Autenticação do Gmail não devolveu token de acesso.")
            raise GmailCredencialInvalida("A credencial do Gmail não devolveu acesso.")

        self._token = token
        validade = _validade_em_segundos(carga.get("expires_in"))
        # Renova um pouco antes de expirar, para não usar um token na virada.
        self._token_expira_em = agora + max(0.0, validade - 60)
        return token

    # -- Conversa com a API -------------------------------------------------

    def _requisitar(self, caminho: str, *, params=None, ausente_em_404=None):
        """GET autenticado, devolvendo o JSON decodificado."""
        url = f"{ENDERECO_API}/{caminho}"
        limpos = {
            chave: valor
            for chave, valor in (params or {}).items()
            if valor not in (None, "")
        }
        if limpos:
            url = f"{url}?{urllib.parse.urlencode(limpos)}"
        requisicao = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self._token_de_acesso()}"},
            method="GET",
        )

        try:
            with urllib.request.urlopen(
                requisicao, timeout=self._tempo_limite()
            ) as resposta:
                bruto = resposta.read()
        except urllib.error.HTTPError as erro:
            logger.warning("Gmail recusou a operação %s (HTTP %s).", caminho, erro.code)
            raise self._erro_de_http(erro, ausente_em_404=ausente_em_404) from erro
        except (urllib.error.URLError, TimeoutError, OSError) as erro:
            logger.warning(
                "Gmail indisponível na operação %s (%s).", caminho, type(erro).__name__
            )
            raise GmailIndisponivel() from erro

        try:
            return json.loads(bruto or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as erro:
            logger.warning("Gmail devolveu resposta ilegível na operação %s.", caminho)
            raise GmailRespostaInvalida() from erro

    @staticmethod
    def _erro_de_http(erro: urllib.error.HTTPError, *, ausente_em_404=None) -> ErroGmail:
        """Traduz a resposta HTTP em falha de domínio."""
        if erro.code == 404 and ausente_em_404 is not None:
            return ausente_em_404()
        if erro.code == 401:
            return GmailCredencialInvalida()
        if erro.code == 403:
            if _motivo_do_erro(erro) in {
                "rateLimitExceeded",
                "userRateLimitExceeded",
                "quotaExceeded",
            }:
                return GmailIndisponivel("O Gmail está limitando o acesso agora.")
            return GmailCredencialInvalida()
        if erro.code >= 500:
            return GmailIndisponivel()
        if erro.code == 404:
            return GmailMensagemInexistente()
        return GmailRespostaInvalida()

    def _tempo_limite(self) -> float:
        try:
            return float(
                getattr(settings, "EITHRA_GMAIL_TIMEOUT", TEMPO_LIMITE_PADRAO)
            )
        except (TypeError, ValueError):
            return TEMPO_LIMITE_PADRAO


# ---------------------------------------------------------------------------
# Normalização da resposta
# ---------------------------------------------------------------------------


def _mensagem_do_json(carga) -> MensagemGmail:
    """Converte o recurso Message do Gmail no contrato normalizado."""
    if not isinstance(carga, dict):
        raise GmailRespostaInvalida()

    payload = carga.get("payload") or {}
    cabecalhos = _cabecalhos(payload)
    anexos: list[AnexoGmail] = []
    texto = _corpo_texto(payload, anexos=anexos)

    return MensagemGmail(
        message_id=str(carga.get("id") or ""),
        thread_id=str(carga.get("threadId") or ""),
        history_id=str(carga.get("historyId") or ""),
        remetente=normalizar_endereco_email(cabecalhos.get("from", "")),
        destinatarios=_enderecos(cabecalhos.get("to", "")),
        assunto=" ".join((cabecalhos.get("subject") or "").split()),
        momento=_momento(cabecalhos, carga.get("internalDate")),
        texto=texto,
        anexos=tuple(anexos),
    )


def _cabecalhos(payload) -> dict[str, str]:
    resultado: dict[str, str] = {}
    for cabecalho in payload.get("headers") or []:
        if not isinstance(cabecalho, dict):
            continue
        nome = str(cabecalho.get("name") or "").lower()
        if nome and nome not in resultado:
            resultado[nome] = str(cabecalho.get("value") or "")
    return resultado


def _enderecos(valor: str) -> tuple[str, ...]:
    return tuple(
        endereco
        for _, bruto in getaddresses([valor or ""])
        if (endereco := normalizar_endereco_email(bruto))
    )


def _momento(cabecalhos: dict[str, str], internal_date) -> datetime | None:
    """A data/hora da mensagem, sempre com fuso.

    Prefere o cabeçalho ``Date`` — o que o remetente declarou — e só cai no
    ``internalDate`` da API quando aquele falta ou é ilegível.
    """
    bruto = cabecalhos.get("date") or ""
    if bruto:
        try:
            momento = parsedate_to_datetime(bruto)
        except (TypeError, ValueError):
            momento = None
        if momento is not None:
            if momento.tzinfo is None:
                return momento.replace(tzinfo=timezone.utc)
            return momento
    return _de_internal_date(internal_date)


def _de_internal_date(valor) -> datetime | None:
    try:
        milissegundos = int(valor)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(milissegundos / 1000, tz=timezone.utc)


def _corpo_texto(payload, *, anexos: list[AnexoGmail]) -> str:
    """O texto legível do corpo, separando o que é corpo do que é anexo.

    Prioridade: ``text/plain``; só na falta dele o HTML é convertido. Arquivo
    anexado nunca é tratado como corpo.
    """
    planos: list[str] = []
    htmls: list[str] = []
    _percorrer(payload, planos=planos, htmls=htmls, anexos=anexos)

    if planos:
        return "\n\n".join(planos).strip()
    if htmls:
        return html_para_texto("\n\n".join(htmls)).strip()
    return ""


def _percorrer(parte, *, planos: list[str], htmls: list[str], anexos: list[AnexoGmail]) -> None:
    if not isinstance(parte, dict):
        return

    nome = str(parte.get("filename") or "")
    corpo = parte.get("body") or {}
    anexo_id = str(corpo.get("attachmentId") or "")
    tipo = str(parte.get("mimeType") or "").lower()

    # Um arquivo anexado não é corpo, mesmo quando é texto.
    if nome or anexo_id:
        anexos.append(
            AnexoGmail(
                attachment_id=anexo_id,
                nome=nome,
                mime_type=str(parte.get("mimeType") or ""),
                tamanho=_inteiro(corpo.get("size")),
            )
        )
        return

    if tipo == "text/plain":
        conteudo = _decodificar(corpo.get("data"))
        if conteudo:
            planos.append(conteudo)
    elif tipo == "text/html":
        conteudo = _decodificar(corpo.get("data"))
        if conteudo:
            htmls.append(conteudo)

    for filho in parte.get("parts") or []:
        _percorrer(filho, planos=planos, htmls=htmls, anexos=anexos)


def _decodificar(dados) -> str:
    """Decodifica o corpo base64url de uma parte, sem estourar com lixo."""
    if not dados:
        return ""
    try:
        bruto = base64.urlsafe_b64decode(_com_preenchimento(str(dados)))
    except (binascii.Error, ValueError):
        return ""
    return bruto.decode("utf-8", errors="replace")


def _com_preenchimento(valor: str) -> str:
    """Completa o base64url até um múltiplo de quatro, como a API exige."""
    faltando = len(valor) % 4
    if faltando:
        return valor + "=" * (4 - faltando)
    return valor


class _ExtratorDeTexto(HTMLParser):
    """Extrai texto de HTML descartando o que não é conteúdo legível."""

    _QUEBRA = frozenset({"br", "p", "div", "li", "tr", "h1", "h2", "h3", "table"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._pedacos: list[str] = []
        self._ignorar = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self._ignorar += 1
        elif tag in self._QUEBRA:
            self._pedacos.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self._ignorar:
            self._ignorar -= 1

    def handle_data(self, dados):
        if not self._ignorar:
            self._pedacos.append(dados)

    def texto(self) -> str:
        return "".join(self._pedacos)


def html_para_texto(bruto: str) -> str:
    """Converte HTML em texto de forma mínima e segura — sem interpretar.

    Não remove assinatura, rodapé nem histórico: isso é limpeza semântica, e
    pertence a quem interpreta. Aqui só se tira o marcador e o script.
    """
    if not bruto:
        return ""
    extrator = _ExtratorDeTexto()
    try:
        extrator.feed(bruto)
        extrator.close()
    except Exception:  # HTML malformado não pode derrubar a leitura
        logger.warning("HTML da mensagem não pôde ser totalmente lido.")
    linhas = [" ".join(linha.split()) for linha in extrator.texto().splitlines()]
    return "\n".join(linha for linha in linhas if linha)


def _inteiro(valor) -> int:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 0


def _quantidade_valida(quantidade) -> int:
    try:
        numero = int(quantidade)
    except (TypeError, ValueError):
        return QUANTIDADE_PADRAO
    return max(1, min(numero, QUANTIDADE_MAXIMA))


def _validade_em_segundos(valor) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 3600.0


def _motivo_do_erro(erro: urllib.error.HTTPError) -> str:
    """Lê o ``reason`` da resposta de erro, sem registrar o corpo."""
    try:
        carga = json.loads(erro.read() or b"{}")
    except Exception:
        return ""
    erros = ((carga or {}).get("error") or {}).get("errors") or []
    if erros and isinstance(erros[0], dict):
        return str(erros[0].get("reason") or "")
    return ""
