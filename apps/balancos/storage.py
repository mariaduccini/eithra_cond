"""Armazenamento externo privado dos arquivos de balanço.

O PDF de um balanço nunca fica no sistema de arquivos da aplicação: ele vive em
um bucket privado, e o Eithra guarda apenas a referência do objeto.

Este módulo existe para que o domínio não conheça o provedor. O domínio fala
com um contrato pequeno — enviar, remover, gerar acesso temporário — e o
provedor concreto fica atrás dele. Trocar de provedor no futuro não deve exigir
mexer no domínio.

Duas decisões de operação estão fixadas aqui:

* **Tempo limite.** Toda conversa com o provedor passa por um único ponto, onde
  o tempo máximo é aplicado. Não existe chamada sem limite.
* **Sem repetição automática.** Não há retry genérico: repetir um envio sem
  garantia de idempotência poderia criar um segundo objeto do mesmo arquivo, e
  isso é pior do que falhar. A única repetição admitida é do caso já conhecido
  como idempotente — o provedor dizer que o objeto existe, quando então o envio
  é refeito substituindo. Qualquer outra falha é reportada de forma explícita.

A credencial do provedor é usada somente aqui, no backend: não é registrada em
log, não é gravada em banco e nunca chega ao navegador. As mensagens de erro
falam do que aconteceu, nunca de cabeçalho, token, URL assinada ou corpo de
resposta do provedor.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

# Tempo máximo de espera ao conversar com o provedor. Curto de propósito: uma
# espera longa aqui segura a resposta ao usuário sem necessidade.
TEMPO_LIMITE_SEGUNDOS = 30

PROVEDOR_PADRAO = "supabase"


class ErroDeStorage(Exception):
    """Falha controlada ao conversar com o armazenamento externo.

    A mensagem é para a gestão. Nunca inclui segredo, URL assinada, token nem
    corpo de resposta do provedor.
    """


class StorageProvider(ABC):
    """Contrato mínimo de armazenamento externo privado."""

    nome: str = "generico"

    def __init__(self, *, bucket: str):
        self.bucket = bucket

    @abstractmethod
    def enviar(self, *, key: str, conteudo: bytes, content_type: str) -> None:
        """Guarda o arquivo no bucket privado, sob a chave informada."""

    @abstractmethod
    def remover(self, *, key: str) -> None:
        """Remove o objeto. Não falha se ele já não existir."""

    @abstractmethod
    def url_assinada(self, *, key: str, validade_segundos: int) -> str:
        """Devolve um acesso temporário ao objeto, gerado sob demanda."""


class SupabaseStorageProvider(StorageProvider):
    """Supabase Storage em bucket privado, por HTTP, sem SDK.

    Usa apenas a biblioteca padrão: uma dependência a menos e o mesmo contrato.
    A service role key fica só no backend.
    """

    nome = "supabase"

    def __init__(self, *, url: str, service_role_key: str, bucket: str):
        super().__init__(bucket=bucket)
        self._url = url.rstrip("/")
        self._chave = service_role_key

    # -- Endereços e cabeçalhos --------------------------------------------
    def _endpoint(self, caminho: str) -> str:
        return f"{self._url}/storage/v1/{caminho}"

    def _objeto_url(self, key: str) -> str:
        seguro = urllib.parse.quote(key, safe="/")
        return self._endpoint(f"object/{self.bucket}/{seguro}")

    def _cabecalhos(self, *, content_type: str | None = None, extra: dict | None = None) -> dict:
        cabecalhos = {
            "Authorization": f"Bearer {self._chave}",
            "apikey": self._chave,
        }
        if content_type:
            cabecalhos["Content-Type"] = content_type
        if extra:
            cabecalhos.update(extra)
        return cabecalhos

    @staticmethod
    def _abrir(requisicao: urllib.request.Request):
        """Abre a conversa com o provedor.

        Existe para que o tempo limite não dependa de quem escreve a chamada:
        ele é aplicado em um único lugar, e não existe conversa sem limite.
        """
        return urllib.request.urlopen(requisicao, timeout=TEMPO_LIMITE_SEGUNDOS)

    @staticmethod
    def _falha(mensagem: str) -> ErroDeStorage:
        """Falha controlada, com a mensagem que a gestão lê.

        O motivo original fica encadeado na exceção (``from``), para quem estiver
        depurando, e não dentro da mensagem: é assim que cabeçalho, token ou
        corpo do provedor ficam de fora do que é mostrado e registrado.
        """
        return ErroDeStorage(mensagem)

    def _enviar_com(self, *, metodo: str, key: str, conteudo: bytes, content_type: str, extra: dict | None = None) -> None:
        requisicao = urllib.request.Request(
            self._objeto_url(key),
            data=conteudo,
            method=metodo,
            headers=self._cabecalhos(content_type=content_type, extra=extra),
        )
        try:
            with self._abrir(requisicao):
                return
        except urllib.error.HTTPError as erro:
            raise self._falha(
                "Não foi possível guardar o arquivo no armazenamento."
            ) from erro
        except (urllib.error.URLError, TimeoutError, OSError) as erro:
            raise self._falha("Não foi possível falar com o armazenamento.") from erro

    # -- Contrato -----------------------------------------------------------
    def enviar(self, *, key: str, conteudo: bytes, content_type: str) -> None:
        try:
            self._enviar_com(
                metodo="POST", key=key, conteudo=conteudo, content_type=content_type
            )
        except ErroDeStorage as erro:
            # 409 significa que o objeto já existe. Reenvia substituindo, para
            # que uma repetição do mesmo envio não crie dois objetos.
            if not self._conflito(erro):
                raise
            self._enviar_com(
                metodo="PUT",
                key=key,
                conteudo=conteudo,
                content_type=content_type,
                extra={"x-upsert": "true"},
            )

    @staticmethod
    def _conflito(erro: ErroDeStorage) -> bool:
        causa = erro.__cause__
        return isinstance(causa, urllib.error.HTTPError) and causa.code == 409

    def remover(self, *, key: str) -> None:
        requisicao = urllib.request.Request(
            self._objeto_url(key),
            method="DELETE",
            headers=self._cabecalhos(),
        )
        try:
            with self._abrir(requisicao):
                return
        except urllib.error.HTTPError as erro:
            if erro.code == 404:
                return
            raise self._falha(
                "Não foi possível remover o arquivo do armazenamento."
            ) from erro
        except (urllib.error.URLError, TimeoutError, OSError) as erro:
            raise self._falha("Não foi possível falar com o armazenamento.") from erro

    def url_assinada(self, *, key: str, validade_segundos: int) -> str:
        seguro = urllib.parse.quote(key, safe="/")
        requisicao = urllib.request.Request(
            self._endpoint(f"object/sign/{self.bucket}/{seguro}"),
            data=json.dumps({"expiresIn": int(validade_segundos)}).encode("utf-8"),
            method="POST",
            headers=self._cabecalhos(content_type="application/json"),
        )
        try:
            with self._abrir(requisicao) as resposta:
                corpo = json.loads(resposta.read() or b"{}")
        except urllib.error.HTTPError as erro:
            raise self._falha(
                "Não foi possível gerar o acesso temporário ao arquivo."
            ) from erro
        except (urllib.error.URLError, TimeoutError, OSError) as erro:
            raise self._falha("Não foi possível falar com o armazenamento.") from erro
        except json.JSONDecodeError as erro:
            raise ErroDeStorage(
                "O armazenamento respondeu de forma inesperada."
            ) from erro

        caminho = (corpo or {}).get("signedURL") or (corpo or {}).get("signedUrl")
        if not caminho:
            raise ErroDeStorage("O armazenamento não devolveu um acesso temporário.")
        if caminho.startswith(("http://", "https://")):
            return caminho
        # O Supabase devolve o caminho relativo a /storage/v1.
        return f"{self._url}/storage/v1{caminho}"


def obter_provider() -> StorageProvider:
    """Devolve o provedor configurado, ou falha dizendo exatamente o que falta.

    Não existe queda para disco local. Se o storage está configurado e as
    variáveis obrigatórias não estão no ambiente, o envio é recusado — e não
    desviado, em silêncio, para um lugar inseguro.
    """
    nome = (getattr(settings, "BALANCOS_STORAGE_PROVIDER", "") or "").strip().lower()

    if nome in ("", PROVEDOR_PADRAO):
        return _supabase_configurado()

    raise ImproperlyConfigured(
        f"Provedor de armazenamento não suportado: {nome!r}. "
        "Use 'supabase' ou ajuste BALANCOS_STORAGE_PROVIDER."
    )


def _supabase_configurado() -> SupabaseStorageProvider:
    """Monta o provedor do Supabase, exigindo as variáveis obrigatórias."""
    url = (getattr(settings, "SUPABASE_URL", "") or "").strip()
    chave = (getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "") or "").strip()
    bucket = (getattr(settings, "BALANCOS_STORAGE_BUCKET", "") or "").strip()

    faltando = [
        nome_da_variavel
        for nome_da_variavel, valor in (
            ("SUPABASE_URL", url),
            ("SUPABASE_SERVICE_ROLE_KEY", chave),
            ("BALANCOS_STORAGE_BUCKET", bucket),
        )
        if not valor
    ]
    if faltando:
        raise ImproperlyConfigured(
            "Armazenamento privado dos balanços não está configurado. "
            "Defina no ambiente: " + ", ".join(faltando) + ". "
            "Nenhum arquivo é guardado em disco local."
        )

    return SupabaseStorageProvider(url=url, service_role_key=chave, bucket=bucket)
