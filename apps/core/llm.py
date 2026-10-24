"""Leitura semântica do relato, quando houver com quem contar.

O Eithra lê o que está escrito com regras próprias — e isso já basta para
registrar uma demanda. Este módulo existe para o que as palavras exatas não
alcançam: a cena descrita sem o nome técnico, o equipamento chamado pelo jeito
de falar e não pelo nome do cadastro.

Três compromissos, nesta ordem:

* **nada é obrigatório.** Sem credencial configurada, ``disponivel()`` responde
  que não, e o sistema segue determinístico: nenhuma tela quebra e nenhum
  registro fica pela metade por causa disso.
* **nada é afirmado aqui.** O serviço devolve uma proposta; conferir se ela
  cabe no vocabulário do domínio e no cadastro do condomínio é de quem chama,
  não deste módulo.
* **nada vaza.** A credencial vive no ambiente e nunca entra em log; o que se
  registra é o tipo de falha, jamais o conteúdo enviado ou recebido.

Este módulo é transporte e nada mais: monta a chamada, devolve o objeto JSON e
desaparece. Quem lê o relato é ``apps/demandas/interpretacao.py``.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)


def disponivel() -> bool:
    """Se há um serviço de leitura configurado para consultar.

    Sem chave ou sem modelo não há leitura semântica — e não há erro nisso: é
    o estado normal de um ambiente que ainda não contratou o serviço.
    """
    return bool(
        getattr(settings, "EITHRA_LLM_API_KEY", "").strip()
        and getattr(settings, "EITHRA_LLM_MODEL", "").strip()
    )


def _endereco() -> str:
    """O endereço da chamada de conversa, a partir da base configurada.

    O endereço é o mesmo formato usado pela API de chat da OpenAI, o que
    mantém a escolha de provedor fora do código: trocar de serviço é trocar
    a variável de ambiente.
    """
    base = getattr(settings, "EITHRA_LLM_BASE_URL", "").strip() or ""
    return f"{base.rstrip('/')}/chat/completions"


def pedir_json(*, sistema: str, pedido: str) -> dict | None:
    """Pede uma leitura ao serviço e devolve o objeto JSON, ou ``None``.

    Qualquer coisa que dê errado — falta de configuração, serviço fora do ar,
    demora acima do limite, resposta ilegível — termina em ``None`` e uma linha
    de log com o tipo da falha. Nada é levantado: quem chama já sabe o que
    fazer sem esta resposta, e uma sugestão a menos nunca pode impedir o
    registro de uma demanda.
    """
    if not disponivel():
        return None

    corpo = {
        "model": settings.EITHRA_LLM_MODEL,
        # Duas leituras do mesmo relato devem concordar entre si.
        "temperature": 0,
        "messages": [
            {"role": "system", "content": sistema},
            {"role": "user", "content": pedido},
        ],
    }
    requisicao = urllib.request.Request(
        _endereco(),
        data=json.dumps(corpo).encode("utf-8"),
        headers={
            # A credencial sai daqui e não é registrada em nenhum lugar.
            "Authorization": f"Bearer {settings.EITHRA_LLM_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            requisicao, timeout=getattr(settings, "EITHRA_LLM_TIMEOUT", 6.0)
        ) as resposta:
            carga = json.loads(resposta.read().decode("utf-8"))
    except urllib.error.HTTPError as falha:
        # O corpo da resposta de erro pode conter o que foi enviado; fica fora.
        logger.warning("Leitura semântica recusada (HTTP %s).", falha.code)
        return None
    except Exception as falha:  # rede, tempo esgotado, resposta ilegível
        logger.warning("Leitura semântica indisponível (%s).", type(falha).__name__)
        return None

    return _objeto_da_resposta(carga)


def _objeto_da_resposta(carga) -> dict | None:
    """O objeto JSON que veio dentro da resposta, se veio algum.

    O conteúdo é procurado entre a primeira chave e a última: alguns serviços
    embrulham o JSON em texto ou em bloco de código, e recusar por causa disso
    seria perder uma leitura boa por um detalhe de formatação.
    """
    try:
        conteudo = carga["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        logger.warning("Leitura semântica devolveu resposta fora do formato esperado.")
        return None
    if not isinstance(conteudo, str):
        return None

    inicio, fim = conteudo.find("{"), conteudo.rfind("}")
    if inicio < 0 or fim <= inicio:
        logger.warning("Leitura semântica devolveu resposta sem JSON.")
        return None
    try:
        objeto = json.loads(conteudo[inicio : fim + 1])
    except json.JSONDecodeError:
        logger.warning("Leitura semântica devolveu JSON ilegível.")
        return None
    return objeto if isinstance(objeto, dict) else None
