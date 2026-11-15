"""Armazenamento privado dos arquivos do Eithra.

Em produção o disco é efêmero: o arquivo vive em um bucket privado remoto, e o
banco guarda apenas a referência do objeto. No desenvolvimento, o mesmo
contrato é atendido pelo disco da própria aplicação (``MEDIA_ROOT``), para que
trabalhar localmente não dependa de serviço externo nenhum.

Este módulo existe para que o domínio não conheça o provedor. O domínio fala
com um contrato pequeno — enviar, remover, gerar acesso temporário — e o
provedor concreto fica atrás dele. Trocar de provedor no futuro não deve exigir
mexer no domínio.

Ele nasceu no domínio de balanços e passou a ser compartilhado na Fase 2E, para
que documentos operacionais usem a mesma infraestrutura sem depender de
balanços. O que era específico de um domínio é apenas o bucket: cada domínio
aponta o seu (``BALANCOS_STORAGE_BUCKET``, ``DOCUMENTOS_STORAGE_BUCKET``),
enquanto a credencial do provedor continua global — ela é do projeto, não de um
domínio.

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
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.validators import RegexValidator

# Tempo máximo de espera ao conversar com o provedor. Curto de propósito: uma
# espera longa aqui segura a resposta ao usuário sem necessidade.
TEMPO_LIMITE_SEGUNDOS = 30

# SHA-256 em hexadecimal minúsculo. Fixar a caixa evita que o mesmo arquivo
# apareça como dois hashes diferentes na detecção de duplicidade.
FORMATO_SHA256 = RegexValidator(
    regex=r"^[0-9a-f]{64}$",
    message="Informe o hash SHA-256 em hexadecimal minúsculo (64 caracteres).",
)

# Campos que guardam a referência do objeto em storage externo privado.
CAMPOS_DE_REFERENCIA_DE_STORAGE = ("storage_provider", "storage_bucket", "storage_key")


def erros_de_referencia_de_storage(instancia) -> dict:
    """Recusa URL pública ou assinada guardada no lugar da referência.

    O que se persiste é provider, bucket e chave do objeto. URL pública ou
    assinada é gerada sob demanda e nunca fica gravada: guardá-la seria expor o
    arquivo, e ela ainda expiraria, deixando o registro inválido.

    Vive aqui, e não no domínio, porque vale para todo arquivo guardado em
    storage privado — balanços e documentos operacionais seguem a mesma regra.
    """
    erros: dict[str, str] = {}
    for campo in CAMPOS_DE_REFERENCIA_DE_STORAGE:
        valor = (getattr(instancia, campo) or "").strip().lower()
        if valor.startswith(("http://", "https://")):
            erros[campo] = (
                "Guarde apenas a referência do objeto. URL pública ou "
                "assinada é gerada sob demanda, nunca persistida."
            )
    return erros

PROVEDOR_PADRAO = "local"


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

    @abstractmethod
    def baixar(self, *, key: str) -> bytes:
        """Traz o conteúdo do objeto guardado sob a chave.

        Existe para quem precisa desenhar o arquivo, e não apenas apontá-lo a um
        navegador — como o card de um comunicado, que embute a logo do
        condomínio. É leitura pura: não altera o objeto nem o bucket.
        """


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

    def baixar(self, *, key: str) -> bytes:
        requisicao = urllib.request.Request(
            self._objeto_url(key),
            method="GET",
            headers=self._cabecalhos(),
        )
        try:
            with self._abrir(requisicao) as resposta:
                return resposta.read()
        except urllib.error.HTTPError as erro:
            raise self._falha(
                "Não foi possível ler o arquivo do armazenamento."
            ) from erro
        except (urllib.error.URLError, TimeoutError, OSError) as erro:
            raise self._falha("Não foi possível falar com o armazenamento.") from erro


class LocalStorageProvider(StorageProvider):
    """O sistema de arquivos da própria aplicação, para o desenvolvimento.

    É o provedor que permite trabalhar sem serviço externo: o arquivo fica sob
    ``MEDIA_ROOT`` e o Django serve a mídia enquanto ``DEBUG`` estiver ligado.
    Não há bucket — ele fica vazio —, e por isso a referência guardada é o
    provedor e a chave, sem mais nada.

    O endereço devolvido é o da própria mídia: no disco local não existe
    assinatura nem validade. O contrato é o mesmo dos demais provedores, então
    quem pede o acesso não precisa saber onde o arquivo está.
    """

    nome = "local"

    def __init__(self):
        super().__init__(bucket="")
        # A raiz e o endereço são lidos a cada provedor montado, e não guardados
        # de uma vez: assim trocar o ambiente — como os testes fazem — muda de
        # verdade onde o arquivo é gravado.
        self._armazenamento = FileSystemStorage(
            location=str(getattr(settings, "MEDIA_ROOT", "") or ""),
            base_url=(getattr(settings, "MEDIA_URL", "") or "/"),
        )

    def enviar(self, *, key: str, conteudo: bytes, content_type: str) -> None:
        """Grava o arquivo sob a chave, substituindo o que estiver no lugar.

        O que existir é removido antes: sem isso, gravar duas vezes a mesma
        logo deixaria um segundo arquivo ao lado, com um sufixo que ninguém
        pediu. O tipo informado não é usado — o arquivo já chegou conferido pelo
        conteúdo real, e é a extensão da chave que o descreve.
        """
        if self._armazenamento.exists(key):
            self._armazenamento.delete(key)
        self._armazenamento.save(key, ContentFile(conteudo))

    def remover(self, *, key: str) -> None:
        """Remove o arquivo. Não falha se ele já não existir."""
        if self._armazenamento.exists(key):
            self._armazenamento.delete(key)

    def url_assinada(self, *, key: str, validade_segundos: int) -> str:
        """O endereço da mídia — sem assinatura, que só existe no remoto."""
        return self._armazenamento.url(key)

    def baixar(self, *, key: str) -> bytes:
        """Lê o arquivo do disco. Recusa quando ele não existe."""
        if not self._armazenamento.exists(key):
            raise ErroDeStorage("O arquivo não está no armazenamento.")
        with self._armazenamento.open(key) as arquivo:
            return arquivo.read()


def obter_provider(
    bucket: str | None = None, *, nome_do_bucket: str | None = None
) -> StorageProvider:
    """Devolve o provedor configurado, ou falha dizendo exatamente o que falta.

    O bucket é do domínio: quem chama diz qual é — e ele só importa no provedor
    remoto. Sem bucket informado, vale o bucket dos balanços, que foi o primeiro
    domínio a usar este armazenamento, para que os chamadores existentes
    continuem funcionando sem mudança.

    ``nome_do_bucket`` só existe para a recusa falar o nome certo da variável
    quando o domínio pede um bucket próprio (por exemplo, a foto do usuário ou
    a logo do condomínio): sem ele, a recusa citaria a variável do domínio que
    estreou o provedor.

    Não existe troca silenciosa de provedor: escolhido o remoto e faltando
    alguma variável obrigatória, o envio é recusado — e não desviado, em
    silêncio, para outro lugar.
    """
    nome = _provedor_escolhido()

    if nome == "local":
        return LocalStorageProvider()
    if nome == "supabase":
        return _supabase_configurado(bucket, nome_do_bucket=nome_do_bucket)

    raise ImproperlyConfigured(
        f"Provedor de armazenamento não suportado: {nome!r}. "
        "Use 'local' ou 'supabase'."
    )


def _provedor_escolhido() -> str:
    """Qual provedor guarda os arquivos privados.

    ``EITHRA_STORAGE_PROVIDER`` decide. ``BALANCOS_STORAGE_PROVIDER`` continua
    sendo lido porque foi o primeiro nome dado a essa escolha. Sem nenhum dos
    dois, o ambiente vale como local: trabalhar no desenvolvimento não deve
    depender de serviço externo para funcionar.
    """
    for variavel in ("EITHRA_STORAGE_PROVIDER", "BALANCOS_STORAGE_PROVIDER"):
        nome = (getattr(settings, variavel, "") or "").strip().lower()
        if nome:
            return nome
    return PROVEDOR_PADRAO


def _supabase_configurado(
    bucket: str | None = None, *, nome_do_bucket: str | None = None
) -> SupabaseStorageProvider:
    """Monta o provedor do Supabase, exigindo as variáveis obrigatórias."""
    url = (getattr(settings, "SUPABASE_URL", "") or "").strip()
    chave = (getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "") or "").strip()

    if bucket is None:
        bucket = (getattr(settings, "BALANCOS_STORAGE_BUCKET", "") or "").strip()
        nome_do_bucket = nome_do_bucket or "BALANCOS_STORAGE_BUCKET"
    else:
        bucket = (bucket or "").strip()
        nome_do_bucket = nome_do_bucket or "DOCUMENTOS_STORAGE_BUCKET"

    faltando = [
        nome_da_variavel
        for nome_da_variavel, valor in (
            ("SUPABASE_URL", url),
            ("SUPABASE_SERVICE_ROLE_KEY", chave),
            (nome_do_bucket, bucket),
        )
        if not valor
    ]
    if faltando:
        raise ImproperlyConfigured(
            "Armazenamento privado não está configurado. "
            "Defina no ambiente: " + ", ".join(faltando) + ". "
            "Nenhum arquivo é guardado em disco local."
        )

    return SupabaseStorageProvider(url=url, service_role_key=chave, bucket=bucket)
