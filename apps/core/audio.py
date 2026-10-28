"""Núcleo único de áudio: transforma fala em texto, sem interpretar.

O Eithra recebe áudio em contextos diferentes — o relato de uma demanda, a
atualização de um serviço, a chegada e a saída na portaria — e essas quatro
coisas não podem virar quatro transcrições. Este módulo é a única porta de
entrada de áudio do sistema, e ele para na camada 2:

    1. áudio original
    2. transcrição fiel      <- este módulo termina aqui
    3. interpretação         <- quem lê o texto é o domínio
    4. registro confirmado   <- só depois de confirmação humana

O que este módulo **não** faz, de propósito:

* não resume, não corrige, não troca palavra técnica por outra e não transforma
  inferência em fala. "O elevador de serviço voltou a parar no oitavo andar."
  chega igual, e a leitura — "falha recorrente no elevador de serviço no 8º
  andar" — é de quem interpreta, não de quem transcreve.
* não guarda o áudio. O arquivo vive na memória do pedido e morre com ele:
  transcrição feita, áudio descartado. Preservar áudio é decisão de outra
  rodada, com prazo, LGPD e auditoria definidos — e não efeito colateral de
  ouvir um recado.
* não conhece nenhum domínio. Demandas e Portaria consomem este núcleo; ele não
  sabe que eles existem.

Três compromissos, na mesma ordem de ``apps/core/llm.py``:

* **nada é obrigatório**: sem serviço configurado, ``disponivel()`` diz que não
  e a entrada por voz fica indisponível — o texto e o registro manual continuam
  funcionando, sem erro 500.
* **nada vaza**: a credencial vive no ambiente e nunca entra em log; o que se
  registra é o tipo de falha, a duração e o modelo, jamais o áudio ou o texto.
* **nada estoura**: arquivo errado, serviço fora do ar, demora acima do limite e
  resposta ilegível terminam em um resultado com mensagem humana, nunca em
  exceção que derrube a tela.

O contrato de leitura do texto está em ``apps/core/interpretacao.py``: este
módulo entrega a transcrição, não o sentido dela.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Limites
# ---------------------------------------------------------------------------

# O que o navegador e o celular realmente gravam hoje. A lista é curta de
# propósito: declarar suporte a um formato que o serviço não aceita seria
# prometer o que a implementação não cumpre.
FORMATOS_SUPORTADOS = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/vnd.wave": "wav",
}

EXTENSOES_SUPORTADAS = frozenset({"webm", "ogg", "mp3", "m4a", "wav"})

# 10 MB. É um recado curto, não uma reunião: o limite existe para recusar cedo
# o arquivo errado, não para apertar o uso legítimo.
LIMITE_DE_BYTES_PADRAO = 10 * 1024 * 1024

# Um recado operacional é curto, mas a transcrição é mais lenta que uma leitura
# de texto: o limite é maior que o da leitura semântica de propósito.
TIMEOUT_PADRAO = 30.0

IDIOMA_PADRAO = "pt"


# ---------------------------------------------------------------------------
# Mensagens para quem usa o sistema
# ---------------------------------------------------------------------------
# Frases prontas e humanas. Nada aqui descreve HTTP, timeout, MIME ou provider:
# quem está na portaria precisa saber o que fazer, não o que falhou por dentro.
MENSAGEM_SEM_PERMISSAO = "Você não pode enviar áudio por aqui."
MENSAGEM_INDISPONIVEL = "O envio por voz não está disponível agora."
MENSAGEM_VAZIO = "O áudio chegou vazio. Tente gravar de novo."
MENSAGEM_FORMATO = "Este formato de áudio não é aceito. Grave de novo por aqui."
MENSAGEM_GRANDE = "O áudio ficou longo demais. Grave um recado curto."
MENSAGEM_FALHA = "Não foi possível entender o áudio agora. Tente de novo ou escreva."
MENSAGEM_SEM_FALA = "Não deu para entender nenhuma fala no áudio. Tente de novo."


@dataclass(frozen=True)
class PedidoDeAudio:
    """O que o núcleo precisa para transcrever — e nada além disso.

    O áudio chega em memória: quem grava é a tela, e o arquivo não passa pelo
    disco da aplicação. ``condominio`` e ``usuario`` não vão para o serviço de
    transcrição — eles existem para o núcleo recusar áudio sem dono. Áudio não
    contorna permissão: quem não pode operar o módulo também não pode ditar para
    ele. Se a permissão do módulo em si é válida continua sendo decisão de quem
    chama, porque só o domínio conhece as suas próprias regras.
    """

    conteudo: bytes = b""
    nome_do_arquivo: str = ""
    content_type: str = ""
    condominio: object | None = None
    usuario: object | None = None
    contexto: str = ""
    idioma: str | None = None


@dataclass(frozen=True)
class Transcricao:
    """O que o núcleo devolve: o texto fiel e o que se sabe sobre ele.

    ``texto`` é o único campo que o domínio usa. ``erro`` só existe quando não
    houve transcrição, e traz uma frase pronta para a tela — nunca o motivo
    técnico. ``falha`` e ``referencia`` são de log: quem usa o sistema não os vê.
    """

    texto: str = ""
    idioma: str = ""
    # Nem todo serviço informa confiança. Vazio é "não sei", e não "baixa".
    confianca: str = ""
    duracao: float | None = None
    erro: str = ""
    falha: str = ""
    referencia: str = ""

    @property
    def obtida(self) -> bool:
        """Se há texto transcrito e nenhuma falha pelo caminho."""
        return bool(self.texto) and not self.erro


def disponivel() -> bool:
    """Se há um serviço de transcrição configurado.

    A credencial e o endereço são os mesmos da leitura semântica
    (``EITHRA_LLM_*``): é o mesmo provedor. O que o áudio precisa de próprio é o
    modelo de transcrição — sem ele não há para onde enviar, e isso não é erro:
    é o estado normal de um ambiente que ainda não contratou o serviço.
    """
    return bool(
        getattr(settings, "EITHRA_LLM_API_KEY", "").strip()
        and getattr(settings, "EITHRA_AUDIO_MODEL", "").strip()
    )


def transcrever(pedido: PedidoDeAudio) -> Transcricao:
    """Transforma o áudio em texto, sem interpretar e sem guardar.

    Nunca levanta: todo caminho de falha termina em ``Transcricao`` com uma
    mensagem humana em ``erro``. Quem chama decide o que fazer — e o texto
    manual continua sendo uma saída válida em qualquer um desses casos.
    """
    referencia = uuid.uuid4().hex[:12]

    if not _tem_dono(pedido):
        return Transcricao(
            erro=MENSAGEM_SEM_PERMISSAO, falha="sem_permissao", referencia=referencia
        )
    if not pedido.conteudo:
        return Transcricao(
            erro=MENSAGEM_VAZIO, falha="vazio", referencia=referencia
        )

    formato = _formato(pedido.content_type, pedido.nome_do_arquivo)
    if not formato:
        return Transcricao(
            erro=MENSAGEM_FORMATO, falha="formato", referencia=referencia
        )
    if len(pedido.conteudo) > _limite_de_bytes():
        return Transcricao(
            erro=MENSAGEM_GRANDE, falha="tamanho", referencia=referencia
        )
    if not disponivel():
        return Transcricao(
            erro=MENSAGEM_INDISPONIVEL, falha="nao_configurado", referencia=referencia
        )

    inicio = _agora()
    try:
        carga = _pedir_transcricao(pedido, formato=formato)
    except urllib.error.HTTPError as falha:
        # O corpo de uma resposta de erro pode ecoar o que foi enviado; fica fora.
        logger.warning(
            "Transcrição recusada pelo serviço (HTTP %s, ref %s).",
            falha.code,
            referencia,
        )
        return Transcricao(
            erro=MENSAGEM_FALHA, falha=f"http_{falha.code}", referencia=referencia
        )
    except Exception as falha:  # rede, tempo esgotado, resposta ilegível
        logger.warning(
            "Transcrição indisponível (%s, ref %s).",
            type(falha).__name__,
            referencia,
        )
        return Transcricao(
            erro=MENSAGEM_FALHA, falha=type(falha).__name__, referencia=referencia
        )

    # Observabilidade técnica: sucesso, duração e modelo. Nunca o texto.
    logger.info(
        "Transcrição concluída em %.2fs (modelo %s, ref %s).",
        _agora() - inicio,
        getattr(settings, "EITHRA_AUDIO_MODEL", ""),
        referencia,
    )
    return _da_resposta(carga, referencia=referencia)


def ler_conteudo(arquivo) -> bytes:
    """Lê o áudio recebido sem trazer mais para a memória do que o necessário.

    A leitura para um byte além do teto: se o arquivo for maior que o aceito, o
    núcleo recebe o suficiente para recusá-lo e o resto nunca é carregado. Quem
    decide o limite continua sendo este módulo — aqui só se evita segurar um
    arquivo inteiro na memória antes de ouvir a resposta dele.

    Existe para que cada tela que recebe áudio não repita a mesma leitura
    defensiva: quem dita entrega o arquivo do pedido e obtém os bytes.
    """
    if arquivo is None:
        return b""
    return arquivo.read(_limite_de_bytes() + 1)


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------


def _tem_dono(pedido: PedidoDeAudio) -> bool:
    """Se o áudio tem condomínio e uma pessoa autenticada por trás."""
    if pedido.condominio is None or not getattr(pedido.condominio, "pk", None):
        return False
    usuario = pedido.usuario
    if usuario is None or not getattr(usuario, "pk", None):
        return False
    return bool(getattr(usuario, "is_authenticated", False))


def _formato(content_type: str, nome_do_arquivo: str) -> str:
    """O formato do áudio, reconhecido pelo tipo declarado ou pela extensão.

    Navegadores mandam ``audio/webm;codecs=opus`` e às vezes um nome sem
    extensão ("blob"), então exigir as duas coisas juntas recusaria gravação
    legítima. Basta uma reconhecer o formato; se nenhuma reconhece, o núcleo não
    adivinha e recusa — é melhor pedir de novo do que mandar lixo ao serviço.
    """
    tipo = (content_type or "").split(";")[0].strip().lower()
    if tipo in FORMATOS_SUPORTADOS:
        return FORMATOS_SUPORTADOS[tipo]
    extensao = _extensao(nome_do_arquivo)
    if extensao in EXTENSOES_SUPORTADAS:
        return extensao
    return ""


def _extensao(nome_do_arquivo: str) -> str:
    nome = (nome_do_arquivo or "").strip().rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." not in nome:
        return ""
    return nome.rsplit(".", 1)[-1].lower()


def _limite_de_bytes() -> int:
    return int(getattr(settings, "EITHRA_AUDIO_MAX_BYTES", LIMITE_DE_BYTES_PADRAO))


# ---------------------------------------------------------------------------
# Chamada ao serviço
# ---------------------------------------------------------------------------


def _endereco() -> str:
    """O endereço da transcrição, a partir da base já configurada.

    Mesmo provedor e mesmo formato da leitura semântica: trocar de serviço é
    trocar variável de ambiente, não código.
    """
    base = getattr(settings, "EITHRA_LLM_BASE_URL", "").strip() or ""
    return f"{base.rstrip('/')}/audio/transcriptions"


def _pedir_transcricao(pedido: PedidoDeAudio, *, formato: str) -> dict:
    """Envia o áudio e devolve a resposta crua do serviço."""
    campos = {
        "model": getattr(settings, "EITHRA_AUDIO_MODEL", ""),
        # verbose_json é o que traz idioma e duração junto do texto.
        "response_format": "verbose_json",
        "language": pedido.idioma
        or getattr(settings, "EITHRA_AUDIO_IDIOMA", IDIOMA_PADRAO)
        or IDIOMA_PADRAO,
    }
    boundary = f"eithra{uuid.uuid4().hex}"
    tipo = (pedido.content_type or "").split(";")[0].strip() or f"audio/{formato}"
    corpo = _corpo_multipart(
        campos=campos,
        nome_do_arquivo=_nome_seguro(pedido.nome_do_arquivo, formato),
        conteudo=pedido.conteudo,
        tipo=tipo,
        boundary=boundary,
    )
    requisicao = urllib.request.Request(
        _endereco(),
        data=corpo,
        headers={
            # A credencial sai daqui e não é registrada em lugar nenhum.
            "Authorization": f"Bearer {settings.EITHRA_LLM_API_KEY}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    with urllib.request.urlopen(
        requisicao, timeout=getattr(settings, "EITHRA_AUDIO_TIMEOUT", TIMEOUT_PADRAO)
    ) as resposta:
        return json.loads(resposta.read().decode("utf-8"))


def _corpo_multipart(*, campos, nome_do_arquivo, conteudo, tipo, boundary) -> bytes:
    """Monta o formulário multipart à mão, sem dependência nova.

    O projeto não usa SDK de fornecedor de nuvem: fala HTTP direto com a
    biblioteca padrão. Uma transcrição não é motivo para trazer a primeira
    dependência externa de rede do sistema.
    """
    partes: list[bytes] = []
    for nome, valor in campos.items():
        partes.append(f"--{boundary}\r\n".encode("utf-8"))
        partes.append(
            f'Content-Disposition: form-data; name="{nome}"\r\n\r\n'.encode("utf-8")
        )
        partes.append(str(valor).encode("utf-8") + b"\r\n")
    partes.append(f"--{boundary}\r\n".encode("utf-8"))
    partes.append(
        f'Content-Disposition: form-data; name="file"; '
        f'filename="{nome_do_arquivo}"\r\n'.encode("utf-8")
    )
    partes.append(f"Content-Type: {tipo}\r\n\r\n".encode("utf-8"))
    partes.append(conteudo + b"\r\n")
    partes.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(partes)


def _nome_seguro(nome_do_arquivo: str, formato: str) -> str:
    """Um nome de arquivo inofensivo para o cabeçalho do formulário.

    O nome vem da tela, então é entrada não confiável: só o nome-base fica, sem
    caminho, sem aspas e sem quebra de linha. Sem extensão reconhecida, o nome
    passa a dizer o formato real — o serviço costuma usá-la para decidir como
    decodificar o áudio.
    """
    limpo = (nome_do_arquivo or "").replace("\\", "/").rsplit("/", 1)[-1]
    limpo = "".join(letra for letra in limpo if letra.isalnum() or letra in "._-")
    if "." not in limpo:
        return f"audio.{formato}"
    return limpo


# ---------------------------------------------------------------------------
# Saída
# ---------------------------------------------------------------------------


def _da_resposta(carga, *, referencia: str) -> Transcricao:
    """O texto fiel que veio do serviço, ou uma falha controlada."""
    if not isinstance(carga, dict):
        logger.warning(
            "Transcrição devolveu resposta fora do formato esperado (ref %s).",
            referencia,
        )
        return Transcricao(
            erro=MENSAGEM_FALHA, falha="resposta", referencia=referencia
        )
    texto = str(carga.get("text") or "").strip()
    if not texto:
        logger.warning("Transcrição devolveu resposta sem fala (ref %s).", referencia)
        return Transcricao(
            erro=MENSAGEM_SEM_FALA, falha="sem_fala", referencia=referencia
        )
    return Transcricao(
        texto=texto,
        idioma=str(carga.get("language") or ""),
        confianca=str(carga.get("confidence") or ""),
        duracao=_duracao(carga.get("duration")),
        referencia=referencia,
    )


def _duracao(valor) -> float | None:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _agora() -> float:
    """O instante atual, para medir a duração do processamento."""
    return time.monotonic()
