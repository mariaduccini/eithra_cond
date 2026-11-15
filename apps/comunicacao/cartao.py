"""O desenho do card de um comunicado.

O card é a peça que o morador recebe. Ele precisa ser legível no celular, caber
no WhatsApp sem cortar nada e não deformar imagem nenhuma. Este módulo é a única
oficina do desenho: nenhuma view monta a arte e nenhum template decide cor. A
mesma paleta que pinta o PNG alimenta a prévia na tela, para que o que se vê
antes de gerar seja o que sai depois.

O desenho é feito com o Pillow, que desenha o texto e compõe a imagem sem
navegador e sem serviço externo — previsível no Windows e no servidor. A logo do
condomínio entra inteira dentro da sua área (``contain``): qualquer que seja a
proporção, ela não estica nem é cortada.

O que este módulo não faz: não fala com armazenamento, não grava nada e não
decide quem pode gerar. Ele recebe um comunicado e, quando houver, os bytes da
logo, e devolve o PNG.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from apps.condominios import cores as cores_do_condominio

from .constants import LIMITE_DA_MENSAGEM, ModeloComunicado, OrientacaoDoCard

# As cores de cada modelo. O mesmo dicionário serve ao PNG e à prévia na tela:
# um só lugar decide o tom do card, e não há como os dois divergirem.
PALETAS = {
    ModeloComunicado.AVISO: {
        "faixa": "#22344F",
        "destaque": "#4F84D6",
    },
    ModeloComunicado.MANUTENCAO: {
        "faixa": "#8A5A22",
        "destaque": "#C4883C",
    },
    ModeloComunicado.URGENTE: {
        "faixa": "#8E2B26",
        "destaque": "#D24A42",
    },
    ModeloComunicado.INFORMATIVO: {
        "faixa": "#1E6E63",
        "destaque": "#3AA391",
    },
    ModeloComunicado.ALERTA: {
        "faixa": "#9A6412",
        "destaque": "#E0A13A",
    },
}

# As cores comuns a todos os modelos: fundo claro, texto escuro e rodapé
# discreto. Vivem separadas porque não mudam com o tom escolhido.
CORES_BASE = {
    "fundo": "#F4F6FA",
    "titulo": "#1B2431",
    "texto": "#46536B",
    "rodape": "#8B95A7",
}

# O tamanho do card em cada orientação. Vertical é o formato natural do celular e
# do WhatsApp; horizontal serve a telas e quadros. Nenhum dos dois deforma o
# conteúdo — só muda onde ele cabe.
TAMANHOS = {
    OrientacaoDoCard.VERTICAL: (1080, 1350),
    OrientacaoDoCard.HORIZONTAL: (1350, 1080),
}


def cores_do_modelo(modelo: str, *, condominio=None) -> dict[str, str]:
    """A paleta completa de um modelo, pronta para a tela.

    Devolve faixa, destaque e as cores base em hexadecimal. É o que a prévia usa
    para se parecer com o card que ainda não foi gerado.

    Quando o condomínio tem uma cor principal configurada, ela toma a faixa — a
    área de identidade do card, onde ficam o nome e a logo —, e a cor secundária
    derivada toma o fio da faixa. O destaque, que é o tom do próprio tipo do
    comunicado, continua sendo o do modelo: a marca do condomínio entra sem
    apagar o sinal de urgência, que continua escrito e colorido pela sua paleta.
    """
    faixa = PALETAS.get(modelo) or PALETAS[ModeloComunicado.AVISO]
    cores = {**CORES_BASE, **faixa}
    cores["fio_da_faixa"] = cores["destaque"]

    if condominio is not None:
        principal = _cor_do_condominio(condominio, "cor_principal")
        secundaria = _cor_do_condominio(condominio, "cor_secundaria")
        if principal:
            cores["faixa"] = principal
        if secundaria:
            cores["fio_da_faixa"] = secundaria
    return cores


def _cor_do_condominio(condominio, atributo: str) -> str:
    """Uma cor do condomínio, quando existe e é válida — senão, nada."""
    valor = getattr(condominio, atributo, "")
    return valor if cores_do_condominio.cor_valida(valor) else ""


def desenhar_cartao(*, comunicacao, logo_bytes: bytes | None = None) -> bytes:
    """Desenha o card do comunicado e devolve o PNG.

    ``logo_bytes`` é a logo do condomínio quando ele tem uma; sem ela, a faixa
    fica só com o nome, e nada quebra por causa de uma imagem ausente.
    """
    paleta = cores_do_modelo(comunicacao.modelo, condominio=comunicacao.condominio)
    largura, altura = _tamanho(comunicacao.orientacao)

    imagem = Image.new("RGB", (largura, altura), _rgb(paleta["fundo"]))
    desenho = ImageDraw.Draw(imagem)

    margem = int(largura * 0.075)
    altura_da_faixa = int(altura * 0.26)

    _desenhar_faixa(
        imagem,
        desenho,
        paleta=paleta,
        largura=largura,
        altura_da_faixa=altura_da_faixa,
        margem=margem,
        nome=getattr(comunicacao.condominio, "nome", ""),
        logo_bytes=logo_bytes,
        escala=altura,
    )

    topo = altura_da_faixa + int(altura * 0.06)

    topo = _desenhar_rotulo(
        desenho,
        rotulo=comunicacao.get_modelo_display(),
        paleta=paleta,
        x=margem,
        y=topo,
        escala=altura,
    )

    topo += int(altura * 0.045)
    topo = _desenhar_paragrafo(
        desenho,
        texto=comunicacao.titulo,
        fonte=_fonte(int(altura * 0.052)),
        x=margem,
        y=topo,
        largura_max=largura - 2 * margem,
        cor=_rgb(paleta["titulo"]),
        altura_linha=int(altura * 0.066),
        max_linhas=3,
    )

    if (comunicacao.mensagem or "").strip():
        topo += int(altura * 0.025)
        _desenhar_paragrafo(
            desenho,
            texto=comunicacao.mensagem[:LIMITE_DA_MENSAGEM],
            fonte=_fonte(int(altura * 0.031)),
            x=margem,
            y=topo,
            largura_max=largura - 2 * margem,
            cor=_rgb(paleta["texto"]),
            altura_linha=int(altura * 0.044),
            max_linhas=6,
        )

    _desenhar_rodape(
        imagem,
        desenho,
        paleta=paleta,
        largura=largura,
        altura=altura,
        margem=margem,
        detalhe=_detalhe_do_rodape(comunicacao),
        escala=altura,
    )

    destino = io.BytesIO()
    imagem.save(destino, format="PNG", optimize=True)
    return destino.getvalue()


# -- Peças do desenho -----------------------------------------------------------


def _desenhar_faixa(
    imagem,
    desenho,
    *,
    paleta,
    largura,
    altura_da_faixa,
    margem,
    nome,
    logo_bytes,
    escala,
):
    """A faixa do alto: o nome do condomínio, a logo e o fio de destaque."""
    desenho.rectangle([0, 0, largura, altura_da_faixa], fill=_rgb(paleta["faixa"]))

    caixa_da_logo = (
        largura - margem - int(largura * 0.17),
        int(altura_da_faixa * 0.30),
        largura - margem,
        int(altura_da_faixa * 0.70),
    )
    tem_logo = _colar_logo(imagem, logo_bytes, caixa_da_logo)

    largura_do_nome = largura - 2 * margem - (int(largura * 0.20) if tem_logo else 0)
    _desenhar_paragrafo(
        desenho,
        texto=nome or "",
        fonte=_fonte(int(escala * 0.030)),
        x=margem,
        y=int(altura_da_faixa * 0.36),
        largura_max=largura_do_nome,
        cor=(255, 255, 255),
        altura_linha=int(escala * 0.040),
        max_linhas=2,
    )

    desenho.rectangle(
        [0, altura_da_faixa - 8, largura, altura_da_faixa],
        fill=_rgb(paleta["fio_da_faixa"]),
    )


def _desenhar_rotulo(desenho, *, rotulo, paleta, x, y, escala) -> int:
    """O rótulo do modelo, como uma etiqueta colorida. Devolve a base do rótulo."""
    fonte = _fonte(int(escala * 0.020))
    altura = int(escala * 0.042)
    largura = int(desenho.textlength(rotulo.upper(), font=fonte)) + int(escala * 0.030)
    desenho.rounded_rectangle(
        [x, y, x + largura, y + altura],
        radius=altura // 2,
        fill=_rgb(paleta["destaque"]),
    )
    desenho.text(
        (x + int(escala * 0.015), y + altura // 2),
        rotulo.upper(),
        font=fonte,
        fill=(255, 255, 255),
        anchor="lm",
    )
    return y + altura


def _desenhar_rodape(imagem, desenho, *, paleta, largura, altura, margem, detalhe, escala):
    """O pé do card: um fio, o detalhe do comunicado e a assinatura."""
    base = altura - int(altura * 0.10)
    desenho.line(
        [margem, base - int(altura * 0.02), largura - margem, base - int(altura * 0.02)],
        fill=_rgb(paleta["destaque"]),
        width=3,
    )

    fonte = _fonte(int(escala * 0.022))
    if detalhe:
        desenho.text((margem, base), detalhe, font=fonte, fill=_rgb(paleta["texto"]), anchor="lm")

    desenho.text(
        (largura - margem, base),
        "Eithra · Comunicação",
        font=fonte,
        fill=_rgb(paleta["rodape"]),
        anchor="rm",
    )


def _detalhe_do_rodape(comunicacao) -> str:
    """A linha de contexto do pé do card: o local e a data, quando existirem."""
    partes = [parte for parte in (comunicacao.local, comunicacao.periodo_por_extenso) if parte]
    return " · ".join(partes)


# -- Texto ----------------------------------------------------------------------


def _desenhar_paragrafo(
    desenho,
    *,
    texto,
    fonte,
    x,
    y,
    largura_max,
    cor,
    altura_linha,
    max_linhas=None,
) -> int:
    """Escreve o texto quebrado em linhas. Devolve o topo da próxima linha livre."""
    linhas = _quebrar(texto, desenho, fonte, largura_max)
    if max_linhas is not None and len(linhas) > max_linhas:
        linhas = linhas[:max_linhas]
        linhas[-1] = _encurtar(linhas[-1], desenho, fonte, largura_max)

    for indice, linha in enumerate(linhas):
        desenho.text((x, y + indice * altura_linha), linha, font=fonte, fill=cor)

    return y + len(linhas) * altura_linha


def _quebrar(texto, desenho, fonte, largura_max) -> list[str]:
    """Divide o texto em linhas que cabem na largura, sem cortar palavras."""
    linhas: list[str] = []
    for paragrafo in (texto or "").splitlines() or [""]:
        palavras = paragrafo.split()
        if not palavras:
            linhas.append("")
            continue
        atual = palavras[0]
        for palavra in palavras[1:]:
            tentativa = f"{atual} {palavra}"
            if desenho.textlength(tentativa, font=fonte) <= largura_max:
                atual = tentativa
            else:
                linhas.append(atual)
                atual = palavra
        linhas.append(atual)
    return linhas or [""]


def _encurtar(linha, desenho, fonte, largura_max) -> str:
    """Encurta a linha até caber, terminando em reticências."""
    while linha and desenho.textlength(f"{linha}…", font=fonte) > largura_max:
        linha = linha[:-1]
    return f"{linha}…"


# -- Fontes e imagem ------------------------------------------------------------


def _fonte(tamanho: int):
    """Uma fonte escalável; sem a fonte dimensionada, cai na padrão do Pillow."""
    try:
        return ImageFont.load_default(size=tamanho)
    except (TypeError, OSError, ImportError, ValueError):
        return ImageFont.load_default()


def _colar_logo(imagem, logo_bytes, caixa) -> bool:
    """Cola a logo inteira dentro da caixa, sem deformar. Devolve se colou algo."""
    if not logo_bytes:
        return False
    try:
        with Image.open(io.BytesIO(logo_bytes)) as original:
            logo = original.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError):
        return False

    largura, altura = _enquadrar(
        logo.width, logo.height, caixa[2] - caixa[0], caixa[3] - caixa[1]
    )
    if not largura or not altura:
        return False

    redimensionada = logo.resize((largura, altura), Image.Resampling.LANCZOS)
    x = caixa[0] + (caixa[2] - caixa[0] - largura) // 2
    y = caixa[1] + (caixa[3] - caixa[1] - altura) // 2
    imagem.paste(redimensionada, (x, y), redimensionada)
    return True


def _enquadrar(largura: int, altura: int, caixa_largura: int, caixa_altura: int):
    """A maior imagem da mesma proporção que cabe na caixa (``contain``).

    É a conta do ``object-fit: contain``: a imagem inteira aparece, na proporção
    de origem, sem esticar nem cortar.
    """
    if largura <= 0 or altura <= 0 or caixa_largura <= 0 or caixa_altura <= 0:
        return 0, 0
    escala = min(caixa_largura / largura, caixa_altura / altura)
    return max(1, int(round(largura * escala))), max(1, int(round(altura * escala)))


def _tamanho(orientacao: str) -> tuple[int, int]:
    """As dimensões do card na orientação escolhida."""
    return TAMANHOS.get(orientacao) or TAMANHOS[OrientacaoDoCard.VERTICAL]


def _rgb(hexadecimal: str) -> tuple[int, int, int]:
    """Converte ``#RRGGBB`` na tripla que o Pillow usa."""
    valor = hexadecimal.lstrip("#")
    return tuple(int(valor[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
