"""A foto que comprova uma chegada registrada na portaria.

A foto da portaria é evidência opcional de um acesso que já aconteceu: ela se
prende ao registro do acesso e serve à conferência de quem chegou. Não é
documento avulso nem acervo paralelo, e não decide nada no acesso — anexar a
foto não abre nem encerra a passagem, não muda o destino.

O que fica guardado é a referência do objeto no bucket privado — nunca o arquivo
em disco local nem um endereço público. O acesso é gerado sob demanda.

O formato é conferido pelo conteúdo real, e não pela extensão nem pelo que o
navegador informou: nenhum dos dois é fonte de confiança. A régua é a mesma
compartilhada em ``apps.core.imagem``.

A foto é SEMPRE opcional: registrar uma chegada sem foto continua funcionando, e
é por isso que ``guardar_foto`` não faz nada quando nada foi escolhido.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from apps.core.evidencia_foto import marcar_evidencia
from apps.core.imagem import validar_imagem
from apps.core.storage import ErroDeStorage, obter_provider

logger = logging.getLogger(__name__)

# Como a recusa se refere à imagem que era esperada.
ASSUNTO_DA_FOTO = "foto"

# Validade padrão do acesso temporário à foto, em segundos. Curta de propósito:
# é o suficiente para abrir a imagem, não para deixar um endereço circulando.
VALIDADE_PADRAO_SEGUNDOS = 60


class ErroDeFoto(Exception):
    """A foto não pode ser guardada. A mensagem é humana, sem detalhe técnico."""


# -- Limites --------------------------------------------------------------------


def limite_da_foto() -> int:
    """Tamanho máximo aceito para a foto de uma chegada, em bytes."""
    return int(getattr(settings, "PORTARIA_MAX_FOTO_BYTES", 0) or 0)


# -- O arquivo ------------------------------------------------------------------


def validar_foto(arquivo) -> tuple[bytes, str, str]:
    """Confere a foto pela régua compartilhada e devolve o que guardar.

    O que não passa daqui não vira arquivo: não é imagem aceita a que não traz a
    assinatura real, e o tipo informado pelo navegador não decide nada.
    """
    return validar_imagem(
        arquivo,
        limite_em_bytes=limite_da_foto(),
        erro=ErroDeFoto,
        sem_arquivo="Escolha a foto da chegada.",
        assunto=ASSUNTO_DA_FOTO,
    )


# -- Onde o arquivo fica --------------------------------------------------------


def provedor_das_fotos():
    """O provedor no bucket privado das fotos da portaria.

    O bucket é do domínio: se ele não estiver definido no ambiente, o envio é
    recusado — não existe queda silenciosa para disco local.
    """
    return obter_provider(
        bucket=getattr(settings, "PORTARIA_STORAGE_BUCKET", ""),
        nome_do_bucket="PORTARIA_STORAGE_BUCKET",
    )


def montar_chave(*, condominio, acesso, extensao: str) -> str:
    """Caminho do objeto dentro do bucket privado.

    Só identificadores: o nome que a pessoa deu ao arquivo não entra aqui. O
    caminho não carrega dado pessoal, não depende do navegador e não permite
    escapar da pasta do condomínio.
    """
    return f"portaria/{condominio.pk}/{acesso.pk}.{extensao}"


def acesso_temporario(acesso, *, validade_segundos: int | None = None) -> str:
    """Devolve um acesso curto à foto, gerado na hora — nunca guardado.

    Recusa quando a chegada não tem foto: sem referência, não há objeto a
    apontar. Uma falha do armazenamento ao gerar o endereço também vira recusa
    humana, para que a tela não fique com um detalhe técnico.
    """
    if not acesso.foto_storage_key:
        raise ErroDeFoto("Esta chegada não tem foto.")

    segundos = int(validade_segundos or VALIDADE_PADRAO_SEGUNDOS)
    try:
        return provedor_das_fotos().url_assinada(
            key=acesso.foto_storage_key, validade_segundos=segundos
        )
    except (ErroDeStorage, ImproperlyConfigured) as erro:
        raise ErroDeFoto(
            "Não foi possível abrir a foto desta chegada."
        ) from erro


# -- Guardar --------------------------------------------------------------------


def guardar_foto(*, acesso, arquivo) -> None:
    """Guarda a foto de uma chegada, presa ao acesso que já existe.

    A foto é sempre opcional: sem arquivo escolhido, nada acontece e nada é
    gravado. Com arquivo, ele é conferido pelo conteúdo real e enviado ao bucket
    antes de o acesso guardar a referência — uma falha de armazenamento não deixa
    o acesso apontando para um objeto que não existe.

    Não existe transação que cubra banco e bucket: se a gravação da referência
    falhar depois do envio, o objeto recém-criado é removido, para não ficar
    órfão no armazenamento.
    """
    if not arquivo:
        return

    conteudo, mime, extensao = validar_foto(arquivo)

    # A identificação visual é gravada na imagem antes de ela subir: leva o
    # condomínio, a hora do servidor, quem registrou e a referência da chegada.
    # É um reforço — se não for possível marcá-la, a foto sobe como veio.
    conteudo = marcar_evidencia(
        conteudo,
        mime_type=mime,
        condominio=getattr(acesso.condominio, "nome", ""),
        registrado_em=acesso.entrada_em,
        autor=_autor_do_acesso(acesso),
        referencia=acesso.nome,
    )

    chave = montar_chave(
        condominio=acesso.condominio, acesso=acesso, extensao=extensao
    )

    try:
        provider = provedor_das_fotos()
        provider.enviar(key=chave, conteudo=conteudo, content_type=mime)
    except (ErroDeStorage, ImproperlyConfigured) as erro:
        raise ErroDeFoto(
            "Não foi possível guardar a foto da chegada. Tente novamente."
        ) from erro

    acesso.foto_storage_provider = provider.nome
    acesso.foto_storage_bucket = provider.bucket
    acesso.foto_storage_key = chave
    acesso.foto_mime_type = mime
    acesso.foto_tamanho_bytes = len(conteudo)

    try:
        acesso.save(
            update_fields=[
                "foto_storage_provider",
                "foto_storage_bucket",
                "foto_storage_key",
                "foto_mime_type",
                "foto_tamanho_bytes",
            ]
        )
    except Exception:
        _desfazer_envio(provider, chave)
        raise


def _autor_do_acesso(acesso) -> str:
    """Quem registrou a chegada, pelo nome — a mesma autoria que o banco guarda.

    A foto pertence ao acesso, e a autoria mora em ``entrada_registrada_por``.
    Aqui só se lê o nome para escrevê-lo na imagem; nada é alterado.
    """
    usuario = getattr(acesso, "entrada_registrada_por", None)
    if usuario is None:
        return ""
    return (
        getattr(usuario, "full_name", "")
        or getattr(usuario, "email", "")
        or str(usuario)
    )


def _desfazer_envio(provider, chave: str) -> None:
    """Remove o objeto enviado quando a gravação falhou depois dele.

    A limpeza é o melhor esforço: se o próprio storage — ou a configuração —
    recusar a remoção, o objeto fica órfão no bucket e é recolhido depois. O que
    não pode acontecer é a falha de limpeza substituir a causa real: por isso o
    erro original segue adiante, e este registra apenas o que sobrou.
    """
    if not chave:
        return
    try:
        provider.remover(key=chave)
    except (ErroDeStorage, ImproperlyConfigured, OSError):
        logger.exception(
            "Não foi possível desfazer a foto de uma chegada que não chegou a ser "
            "registrada; o objeto pode ter ficado no armazenamento."
        )


__all__ = [
    "ErroDeFoto",
    "acesso_temporario",
    "guardar_foto",
    "limite_da_foto",
    "montar_chave",
    "provedor_das_fotos",
    "validar_foto",
]
