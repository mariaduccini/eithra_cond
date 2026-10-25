"""A logo de um condomínio.

A logo é identificação de contexto, não acervo: ela existe para a pessoa saber
em qual condomínio está operando quando o nome sozinho não basta. Por isso é
opcional, vive pequena ao lado do nome, e quem não tem uma é identificado pelas
iniciais — nunca por uma imagem quebrada.

O arquivo segue o mesmo caminho de qualquer imagem do sistema: vai para o
armazenamento privado, em um espaço próprio — a marca de um condomínio não se
mistura com a foto de uma pessoa nem com o documento de outro assunto —, e aqui
fica apenas a referência do objeto. O conteúdo é conferido pelo que o arquivo
realmente é, e não pela extensão nem pelo que o navegador informou.

Onde o arquivo fica é decisão do provedor configurado: no desenvolvimento, o
disco da aplicação; em produção, um bucket privado remoto. O acesso é pedido ao
provedor na hora — assinado e temporário onde isso existe, o endereço da própria
mídia onde não existe — e nunca uma URL pública guardada.

A régua da imagem é a mesma de todo o Eithra (``apps/core/imagem.py``); o que é
próprio da logo é o limite e o jeito de dizer a recusa.
"""
from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from apps.core.imagem import validar_imagem
from apps.core.storage import ErroDeStorage, obter_provider

# A variável de ambiente que diz em qual bucket privado a logo vive. O nome é
# citado na recusa do provedor: sem ele, a falta da variável seria reportada
# com o nome do bucket de outro domínio.
NOME_DO_BUCKET = "EITHRA_CONDOMINIO_LOGO_BUCKET"


class ErroDeLogo(Exception):
    """A logo não pode ser guardada. A mensagem é para a gestão."""


# -- O arquivo ------------------------------------------------------------------


def limite_da_logo() -> int:
    """Tamanho máximo aceito para a logo, em bytes."""
    return int(getattr(settings, "EITHRA_CONDOMINIO_LOGO_MAX_BYTES", 0) or 0)


def validar_logo(arquivo) -> tuple[bytes, str, str]:
    """Confere a imagem e devolve o que dela precisa ser guardado.

    Devolve o conteúdo, o tipo real e a extensão.
    """
    return validar_imagem(
        arquivo,
        limite_em_bytes=limite_da_logo(),
        erro=ErroDeLogo,
        sem_arquivo="Escolha a imagem que vai identificar este condomínio.",
        assunto="logo",
    )


def montar_chave(*, condominio, extensao: str) -> str:
    """Caminho do objeto dentro do armazenamento.

    Só o identificador do condomínio e a extensão real: o nome que a pessoa deu
    ao arquivo não entra aqui. Com o provedor local, esta chave é também o
    caminho do arquivo sob ``MEDIA_ROOT``.
    """
    return f"condominios/logos/{condominio.pk}.{extensao}"


# -- Guardar, trocar, tirar -----------------------------------------------------


def tem_logo(condominio) -> bool:
    """Diz se o condomínio tem uma logo guardada."""
    return bool((getattr(condominio, "logo_storage_key", "") or "").strip())


def definir_logo(condominio, arquivo) -> None:
    """Guarda a logo do condomínio, substituindo a que houver.

    A imagem vai para o armazenamento antes de a referência existir no banco: uma
    falha de armazenamento não deixa um condomínio apontando para uma imagem que
    não subiu. O objeto anterior é removido quando o formato muda e, com ele, o
    caminho — o mesmo formato é substituído no lugar.
    """
    conteudo, mime, extensao = validar_logo(arquivo)
    chave = montar_chave(condominio=condominio, extensao=extensao)
    anterior = (getattr(condominio, "logo_storage_key", "") or "").strip()

    provedor = _provedor()
    provedor.enviar(key=chave, conteudo=conteudo, content_type=mime)
    if anterior and anterior != chave:
        _remover_sem_falhar(provedor, anterior)

    with transaction.atomic():
        condominio.logo_storage_provider = provedor.nome
        condominio.logo_storage_bucket = provedor.bucket
        condominio.logo_storage_key = chave
        condominio.save(
            update_fields=[
                "logo_storage_provider",
                "logo_storage_bucket",
                "logo_storage_key",
                "atualizado_em",
            ]
        )


def remover_logo(condominio) -> None:
    """Tira a logo do condomínio.

    O objeto é removido do armazenamento, e o que não pode acontecer é a gestão
    ficar presa a uma imagem que decidiu tirar: se o armazenamento falhar, a
    referência é limpa do mesmo jeito, e o objeto pode ser recolhido depois. Sem
    logo, a identificação volta às iniciais do nome.
    """
    chave = (getattr(condominio, "logo_storage_key", "") or "").strip()
    if chave:
        try:
            _remover_sem_falhar(_provedor(), chave)
        except ImproperlyConfigured:
            pass

    with transaction.atomic():
        condominio.logo_storage_provider = ""
        condominio.logo_storage_bucket = ""
        condominio.logo_storage_key = ""
        condominio.save(
            update_fields=[
                "logo_storage_provider",
                "logo_storage_bucket",
                "logo_storage_key",
                "atualizado_em",
            ]
        )


def endereco(condominio) -> str:
    """O endereço temporário da logo, pronto para a tela — ou nada.

    É o que as telas usam: sem logo, ou sem armazenamento, a resposta é uma
    string vazia e a interface cai no fallback. Nenhuma página depende do
    armazenamento para abrir.
    """
    if condominio is None or not tem_logo(condominio):
        return ""
    return acesso_temporario(condominio) or ""


def acesso_temporario(condominio, *, validade_segundos: int | None = None) -> str | None:
    """Devolve o acesso à logo, pedido ao provedor na hora — nunca guardado.

    Quem decide a forma do endereço é o provedor: no remoto ele é assinado e
    vale por pouco tempo; no disco local é o endereço da própria mídia, que não
    expira. A camada da logo não supõe nenhum dos dois.

    Sem logo, ou com o armazenamento fora do ar, a resposta é nada: a tela cai
    nas iniciais, e a identificação continua legível. Uma imagem que não abre
    não pode derrubar a página inteira.
    """
    chave = (getattr(condominio, "logo_storage_key", "") or "").strip()
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
    """O provedor do espaço de logos, ou a recusa citando a variável certa.

    O provedor é global, mas o bucket de logos é próprio do domínio: quando o
    provedor remoto está escolhido, pedir a logo sem o bucket dela é recusado —
    e não desviado, em silêncio, para o bucket de outro domínio. No provedor
    local não há bucket, e nada disso se aplica.
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
