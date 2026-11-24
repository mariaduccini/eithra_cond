"""Identificação visual compartilhada das evidências fotográficas.

Toda foto que comprova um fato no Eithra carrega, gravados na própria imagem, os
dados que a tornam conferível fora do sistema: o condomínio, a hora do servidor,
quem registrou e a que objeto ela se prende. A marca é aplicada antes de a foto
ser guardada, e não substitui a autoria nem a data já registradas no banco — é a
mesma informação, legível por quem só tem a imagem.

Esta é a oficina única da marca. Ela não fala com armazenamento, não conhece
banco e não decide permissão: recebe os bytes de uma imagem já aceita e os
rótulos que a identificam, e devolve os bytes da imagem marcada. Cada domínio
segue dono do seu próprio registro (o acesso da portaria, o anexo de uma
demanda); o que se compartilha é só o desenho da identificação.

Se não for possível marcar — Pillow ausente, formato inesperado ou imagem
ilegível —, devolve a imagem original intacta, sem levantar erro. A evidência
nunca deixa de ser guardada por causa da marca: a identificação é um reforço, não
uma condição.

Não há GPS, reconhecimento facial, OCR nem qualquer leitura do conteúdo: a marca
só escreve o que o próprio sistema já sabe. Nada de "Local" — o lugar físico da
foto não entra aqui.
"""
from __future__ import annotations

import io
import logging
from datetime import datetime

from django.utils import timezone

logger = logging.getLogger(__name__)

# O formato de saída de cada tipo aceito por ``apps.core.imagem``. A imagem é
# relida e regravada no mesmo formato em que chegou.
FORMATOS = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}

# Como a cor da faixa e do texto aparecem sobre a foto.
_COR_DA_FAIXA = (17, 24, 33, 168)
_COR_DO_TEXTO = (255, 255, 255, 255)


def marcar_evidencia(
    conteudo: bytes,
    *,
    mime_type: str,
    condominio: str = "",
    registrado_em: datetime | None = None,
    autor: str = "",
    referencia: str = "",
) -> bytes:
    """Devolve a imagem com a faixa de identificação — ou a original, intacta.

    A faixa ocupa a base da imagem e traz duas linhas: na primeira, o condomínio
    e a data/hora do servidor; na segunda, quem registrou e a referência do
    objeto. Campos vazios são omitidos, e nada além disso é escrito.

    Quando o formato não é reconhecido ou o desenho não é possível, a imagem
    segue como veio. Não se levanta erro: quem chama guarda a evidência de todo
    jeito, e a ausência da marca não é falha do registro.
    """
    formato = FORMATOS.get((mime_type or "").strip().lower())
    if formato is None or not conteudo:
        return conteudo

    linhas = _linhas(
        condominio=condominio,
        registrado_em=registrado_em,
        autor=autor,
        referencia=referencia,
    )
    if not linhas:
        return conteudo

    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - dependência opcional
        logger.warning(
            "Pillow indisponível: a evidência foi guardada sem a identificação visual."
        )
        return conteudo

    try:
        with Image.open(io.BytesIO(conteudo)) as original:
            base = original.convert("RGB")
    except Exception:
        # Imagem que o Pillow não abre não vira marca: guarda-se como veio.
        logger.warning(
            "Não foi possível identificar visualmente uma evidência; o arquivo "
            "foi guardado como recebido."
        )
        return conteudo

    try:
        _desenhar_faixa(base, linhas)
        destino = io.BytesIO()
        base.save(destino, format=formato)
    except Exception:
        logger.warning(
            "Falha ao desenhar a identificação visual da evidência; o arquivo foi "
            "guardado como recebido."
        )
        return conteudo

    return destino.getvalue()


# -- O texto da marca -----------------------------------------------------------


def _linhas(
    *,
    condominio: str,
    registrado_em: datetime | None,
    autor: str,
    referencia: str,
) -> list[str]:
    """As duas linhas da faixa: contexto em cima, responsabilidade embaixo."""
    primeira = _juntar(condominio, _quando(registrado_em))
    segunda = _juntar(autor, referencia)
    return [linha for linha in (primeira, segunda) if linha]


def _juntar(*partes: str) -> str:
    """Une as partes preenchidas com um separador discreto."""
    return " · ".join(parte.strip() for parte in partes if (parte or "").strip())


def _quando(registrado_em: datetime | None) -> str:
    """A hora do servidor, no fuso local, em formato curto."""
    if registrado_em is None:
        return ""
    momento = registrado_em
    if timezone.is_aware(momento):
        momento = timezone.localtime(momento)
    return momento.strftime("%d/%m/%Y %H:%M")


# -- O desenho ------------------------------------------------------------------


def _desenhar_faixa(imagem, linhas: list[str]) -> None:
    """Desenha a faixa translúcida na base e escreve as linhas por cima."""
    from PIL import Image, ImageDraw

    largura, altura = imagem.size
    tamanho = max(12, int(altura * 0.028))
    fonte = _fonte(tamanho)
    margem = max(6, int(largura * 0.02))
    altura_linha = int(tamanho * 1.4)
    altura_faixa = margem * 2 + altura_linha * len(linhas)

    camada = Image.new("RGBA", imagem.size, (0, 0, 0, 0))
    desenho = ImageDraw.Draw(camada)
    desenho.rectangle(
        [0, altura - altura_faixa, largura, altura],
        fill=_COR_DA_FAIXA,
    )

    base = altura - altura_faixa + margem
    for indice, linha in enumerate(linhas):
        y = base + indice * altura_linha
        texto = _caber(linha, desenho, fonte, largura - 2 * margem)
        desenho.text((margem, y), texto, font=fonte, fill=_COR_DO_TEXTO)

    marcada = Image.alpha_composite(imagem.convert("RGBA"), camada).convert("RGB")
    imagem.paste(marcada)


def _fonte(tamanho: int):
    """A fonte dimensionada; sem ela, a padrão do Pillow — nunca falha por isso."""
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=tamanho)
    except (TypeError, OSError, ImportError, ValueError):
        return ImageFont.load_default()


def _caber(texto: str, desenho, fonte, largura_max: int) -> str:
    """Encurta a linha até caber na largura, terminando em reticências."""
    if desenho.textlength(texto, font=fonte) <= largura_max:
        return texto
    while texto and desenho.textlength(texto + "…", font=fonte) > largura_max:
        texto = texto[:-1]
    return texto + "…"


__all__ = ["FORMATOS", "marcar_evidencia"]
