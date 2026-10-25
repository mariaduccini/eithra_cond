"""A foto de quem usa o Eithra.

Um retrato é identificação, não acervo: a foto é opcional, e quem não tem uma
aparece pelas iniciais do próprio nome. Ela existe para a pessoa ser reconhecida
na lista de acessos — pequena, ao lado do nome —, e não para virar galeria.

O arquivo vai para o mesmo armazenamento privado dos demais domínios, em um
bucket próprio: a imagem de uma pessoa não se mistura com o arquivo de outro
assunto. Aqui fica apenas a referência do objeto; a imagem é conferida pelo
conteúdo real, e não pela extensão nem pelo que o navegador informou — nenhum
dos dois é fonte de confiança. O acesso é temporário e gerado na hora, nunca
uma URL pública guardada.
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from apps.core.imagem import validar_imagem
from apps.core.storage import ErroDeStorage, obter_provider

# A variável de ambiente que diz em qual bucket privado a foto vive. O nome é
# citado na recusa do provedor: sem ele, a falta da variável seria reportada
# com o nome do bucket de outro domínio.
NOME_DO_BUCKET = "EITHRA_AVATAR_BUCKET"


class ErroDeFoto(Exception):
    """A foto não pode ser guardada. A mensagem é para a gestão."""


# -- O arquivo ------------------------------------------------------------------


def limite_da_foto() -> int:
    """Tamanho máximo aceito para a foto, em bytes."""
    return int(getattr(settings, "EITHRA_AVATAR_MAX_BYTES", 0) or 0)


def validar_foto(arquivo) -> tuple[bytes, str, str]:
    """Confere a imagem e devolve o que dela precisa ser guardado.

    Devolve o conteúdo, o tipo real e a extensão. A régua é a mesma para toda
    imagem do sistema (``apps/core/imagem.py``); o que é próprio da foto é o
    limite e o jeito de dizer a recusa.
    """
    return validar_imagem(
        arquivo,
        limite_em_bytes=limite_da_foto(),
        erro=ErroDeFoto,
        sem_arquivo="Escolha a imagem que vai representar esta pessoa.",
        assunto="foto",
    )


def montar_chave(*, conta, extensao: str) -> str:
    """Caminho do objeto dentro do bucket privado.

    Só o identificador da conta e a extensão real: o nome que a pessoa deu ao
    arquivo não entra aqui, e o caminho não carrega dado pessoal.
    """
    return f"avatares/{conta.pk}.{extensao}"


# -- Guardar, trocar, tirar -----------------------------------------------------


def tem_foto(conta) -> bool:
    """Diz se a pessoa tem uma foto guardada."""
    return bool((getattr(conta, "avatar_storage_key", "") or "").strip())


def definir_foto(conta, arquivo) -> None:
    """Guarda a foto da pessoa, substituindo a que houver.

    A imagem vai para o bucket antes de a referência existir no banco: uma falha
    de armazenamento não deixa uma pessoa apontando para uma imagem que não
    subiu. O objeto antigo é removido quando o formato muda e, com ele, o
    caminho — o mesmo formato é substituído no lugar.
    """
    conteudo, mime, extensao = validar_foto(arquivo)
    chave = montar_chave(conta=conta, extensao=extensao)
    anterior = (getattr(conta, "avatar_storage_key", "") or "").strip()

    provedor = _provedor()
    provedor.enviar(key=chave, conteudo=conteudo, content_type=mime)
    if anterior and anterior != chave:
        _remover_sem_falhar(provedor, anterior)

    with transaction.atomic():
        conta.avatar_storage_provider = provedor.nome
        conta.avatar_storage_bucket = provedor.bucket
        conta.avatar_storage_key = chave
        conta.save(
            update_fields=[
                "avatar_storage_provider",
                "avatar_storage_bucket",
                "avatar_storage_key",
                "updated_at",
            ]
        )


def remover_foto(conta) -> None:
    """Tira a foto da pessoa.

    O objeto é removido do bucket, e o que não pode acontecer é a gestão ficar
    presa a uma imagem que decidiu tirar: se o armazenamento falhar, a
    referência é limpa do mesmo jeito, e o objeto pode ser recolhido depois.
    """
    chave = (getattr(conta, "avatar_storage_key", "") or "").strip()
    if chave:
        try:
            _remover_sem_falhar(_provedor(), chave)
        except ImproperlyConfigured:
            pass

    with transaction.atomic():
        conta.avatar_storage_provider = ""
        conta.avatar_storage_bucket = ""
        conta.avatar_storage_key = ""
        conta.save(
            update_fields=[
                "avatar_storage_provider",
                "avatar_storage_bucket",
                "avatar_storage_key",
                "updated_at",
            ]
        )


def acesso_temporario(conta, *, validade_segundos: int | None = None) -> str | None:
    """Devolve um acesso curto à foto, gerado na hora — nunca guardado.

    Sem foto, ou com o armazenamento fora do ar, a resposta é nada: a tela cai
    nas iniciais, e a lista continua legível. Uma imagem que não abre não pode
    derrubar a página inteira.
    """
    chave = (getattr(conta, "avatar_storage_key", "") or "").strip()
    if not chave:
        return None

    segundos = int(
        validade_segundos
        or getattr(settings, "BALANCOS_URL_ACESSO_SEGUNDOS", 60)
        or 60
    )
    try:
        return _provedor().url_assinada(key=chave, validade_segundos=segundos)
    except (ErroDeStorage, ImproperlyConfigured):
        return None


# -- Provedor -------------------------------------------------------------------


def _provedor():
    """O provedor do bucket de fotos, ou a recusa citando a variável certa.

    Nunca cai no bucket de outro domínio: o bucket de fotos é próprio, e sem ele
    configurado a guarda é recusada — não desviada em silêncio.
    """
    return obter_provider(
        bucket=(getattr(settings, NOME_DO_BUCKET, "") or ""),
        nome_do_bucket=NOME_DO_BUCKET,
    )


def _remover_sem_falhar(provedor, chave: str) -> None:
    """Remove o objeto do bucket sem tornar a operação refém do armazenamento."""
    try:
        provedor.remover(key=chave)
    except ErroDeStorage:
        return
