"""Operações do domínio de comunicação.

Escrever, revisar e gerar o card de um comunicado sempre gravam o histórico da
comunicação na mesma transação: ou o comunicado e sua memória mudam juntos, ou
nada muda. Nenhuma regra de tela mora aqui — a view traduz o que a gestão decidiu,
e o domínio decide, aplica e registra.

Comunicação é uma capacidade transversal, e não um dono de dados: quando o
comunicado parte de uma demanda ou de uma manutenção, ele apenas aponta para a
origem. Ao gerar o card, registra-se um único evento na origem — "Comunicado
gerado" —, e nunca se copia para lá o histórico que é da comunicação.
"""
from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from apps.condominios import logo as logo_do_condominio
from apps.core.arquivos import calcular_hash

from . import arquivos, cartao
from .constants import (
    ModeloComunicado,
    OrigemComunicado,
    OrientacaoDoCard,
    PublicoComunicado,
    StatusComunicacao,
    TipoEventoComunicacao,
)
from .models import Comunicacao, HistoricoComunicacao

# A descrição do evento cabe no histórico como uma frase curta.
LIMITE_DO_EVENTO = 255

# Marca de "o chamador não falou sobre este campo". É diferente de ``None``, que
# é uma decisão: limpar a data ou trocar o público. Sem essa distinção, abrir e
# salvar o formulário antigo apagaria o que ninguém pediu para apagar — e o
# histórico registraria uma mudança que não houve.
_NAO_INFORMADO = object()

# O nome de cada campo na frase do histórico. O evento fala a mesma língua da tela.
ROTULOS_DOS_CAMPOS = {
    "titulo": "título",
    "mensagem": "mensagem",
    "local": "local",
    "periodo": "data",
    "publico": "público",
    "publico_descricao": "detalhe do público",
    "modelo": "modelo",
    "orientacao": "orientação",
}


class ErroDeComunicacao(Exception):
    """Uma decisão do domínio de comunicação que a gestão precisa saber."""


def _texto(valor: str | None) -> str:
    """Colapsa espaços e devolve string vazia quando não há conteúdo."""
    return " ".join((valor or "").split())


def _texto_longo(valor: str | None) -> str:
    """Preserva os parágrafos de um texto livre, sem espaços nas pontas."""
    return (valor or "").strip()


def _nome_de(usuario) -> str:
    """O nome pelo qual a pessoa é reconhecida no histórico."""
    if usuario is None:
        return "Sem responsável"
    return (
        getattr(usuario, "full_name", "")
        or getattr(usuario, "short_name", "")
        or getattr(usuario, "email", "")
        or "Sem responsável"
    )


def _periodo_por_extenso(valor) -> str:
    """A data como a gestão a lê, ou a ausência dela."""
    return valor.strftime("%d/%m/%Y") if valor else "sem data"


def _registrar(
    comunicacao,
    usuario,
    tipo_evento,
    descricao: str,
    *,
    anteriores=None,
    novos=None,
) -> HistoricoComunicacao:
    """Acrescenta um evento ao histórico. Nunca reescreve o que já existe."""
    return HistoricoComunicacao.objects.create(
        comunicacao=comunicacao,
        usuario=usuario,
        tipo_evento=tipo_evento,
        descricao=descricao[:LIMITE_DO_EVENTO],
        dados_anteriores=anteriores,
        dados_novos=novos,
    )


@transaction.atomic
def criar_comunicacao(
    *,
    condominio,
    criado_por,
    titulo: str,
    mensagem: str = "",
    local: str = "",
    periodo=None,
    publico: str = PublicoComunicado.TODOS,
    publico_descricao: str = "",
    modelo: str = ModeloComunicado.AVISO,
    orientacao: str = OrientacaoDoCard.VERTICAL,
    origem_tipo: str = OrigemComunicado.AVULSO,
    origem_demanda=None,
    origem_manutencao=None,
) -> Comunicacao:
    """Registra o comunicado como rascunho e nasce o primeiro evento do histórico.

    O comunicado nasce rascunho: o texto é escrito e revisado antes de o card
    existir. A origem é referência — a demanda ou a manutenção continua sendo a
    dona daqueles dados, e nada dela é copiado para cá.
    """
    comunicacao = Comunicacao(
        condominio=condominio,
        modelo=modelo,
        orientacao=orientacao,
        titulo=titulo,
        mensagem=mensagem,
        local=local,
        periodo=periodo,
        publico=publico,
        publico_descricao=publico_descricao,
        origem_tipo=origem_tipo,
        origem_demanda=origem_demanda,
        origem_manutencao=origem_manutencao,
        status=StatusComunicacao.RASCUNHO,
        criado_por=criado_por,
    )
    comunicacao.full_clean()
    comunicacao.save()
    _registrar(comunicacao, criado_por, TipoEventoComunicacao.CRIADA, "Comunicado criado")
    return comunicacao


@transaction.atomic
def editar_comunicacao(
    comunicacao,
    *,
    usuario,
    titulo=_NAO_INFORMADO,
    mensagem=_NAO_INFORMADO,
    local=_NAO_INFORMADO,
    periodo=_NAO_INFORMADO,
    publico=_NAO_INFORMADO,
    publico_descricao=_NAO_INFORMADO,
    modelo=_NAO_INFORMADO,
    orientacao=_NAO_INFORMADO,
) -> Comunicacao:
    """Revisa o comunicado e registra, no histórico, o que mudou de verdade.

    Só o campo que o chamador tocou é comparado: o que ninguém pediu para mudar
    continua como estava, e o histórico não registra edição que não houve.
    """
    novos_valores = {
        "titulo": titulo,
        "mensagem": mensagem,
        "local": local,
        "periodo": periodo,
        "publico": publico,
        "publico_descricao": publico_descricao,
        "modelo": modelo,
        "orientacao": orientacao,
    }

    anteriores: dict[str, str] = {}
    posteriores: dict[str, str] = {}
    rotulos_alterados: list[str] = []

    for campo, valor in novos_valores.items():
        if valor is _NAO_INFORMADO:
            continue
        atual = getattr(comunicacao, campo)
        if atual == valor:
            continue
        anteriores[campo] = _como_texto(campo, atual)
        posteriores[campo] = _como_texto(campo, valor)
        rotulos_alterados.append(ROTULOS_DOS_CAMPOS[campo])
        setattr(comunicacao, campo, valor)

    if rotulos_alterados:
        comunicacao.full_clean()
        comunicacao.save()
        _registrar(
            comunicacao,
            usuario,
            TipoEventoComunicacao.EDITADA,
            "Comunicado revisado: " + ", ".join(rotulos_alterados),
            anteriores=anteriores,
            novos=posteriores,
        )
    return comunicacao


@transaction.atomic
def gerar_card(comunicacao, *, usuario) -> Comunicacao:
    """Desenha o PNG do comunicado, guarda-o e o torna pronto para compartilhar.

    Este é o portal único que produz o arquivo final: o desenho, a guarda no
    bucket, a referência no banco, o evento na comunicação e a lembrança na
    origem acontecem aqui — e em nenhum outro lugar. Nenhuma view gera card por
    conta própria.

    O arquivo vai para o bucket antes de a referência existir: uma falha de
    armazenamento não deixa um comunicado apontando para um card que não subiu.
    """
    logo_bytes = logo_do_condominio.conteudo(comunicacao.condominio)
    conteudo = cartao.desenhar_cartao(comunicacao=comunicacao, logo_bytes=logo_bytes)

    provedor, chave = arquivos.guardar_cartao(
        comunicacao=comunicacao, conteudo=conteudo
    )

    comunicacao.storage_provider = provedor.nome
    comunicacao.storage_bucket = provedor.bucket
    comunicacao.storage_key = chave
    comunicacao.mime_type = arquivos.MIME_DO_CARD
    comunicacao.tamanho_bytes = len(conteudo)
    comunicacao.sha256 = calcular_hash(conteudo)
    comunicacao.status = StatusComunicacao.PRONTA
    comunicacao.gerado_em = timezone.now()
    comunicacao.save()

    _registrar(
        comunicacao,
        usuario,
        TipoEventoComunicacao.IMAGEM_GERADA,
        "Card gerado e guardado",
    )
    _lembrar_na_origem(comunicacao, usuario)
    return comunicacao


def acesso_temporario(comunicacao, *, validade_segundos: int | None = None) -> str:
    """O acesso curto ao card gerado, para a tela e o download."""
    return arquivos.acesso_temporario(comunicacao, validade_segundos=validade_segundos)


def _lembrar_na_origem(comunicacao, usuario) -> None:
    """Registra, na origem, o único evento que ela recebe: um comunicado saiu.

    O fato pertence à comunicação, e por isso o evento é só a lembrança de que
    ele existiu. O histórico da comunicação não é copiado para o domínio de
    origem.
    """
    descricao = f"Comunicado gerado: {comunicacao.titulo}"

    if comunicacao.origem_tipo == OrigemComunicado.DEMANDA and comunicacao.origem_demanda_id:
        from apps.demandas import services as demandas_services

        demandas_services.registrar_comunicado(
            comunicacao.origem_demanda, usuario, descricao
        )
    elif (
        comunicacao.origem_tipo == OrigemComunicado.MANUTENCAO
        and comunicacao.origem_manutencao_id
    ):
        from apps.manutencoes import services as manutencoes_services

        manutencoes_services.registrar_comunicado(
            comunicacao.origem_manutencao, usuario, descricao
        )


def _como_texto(campo: str, valor) -> str:
    """O valor de um campo como o histórico o guarda — em linguagem comum."""
    if valor is None:
        return ""
    if campo == "periodo":
        return _periodo_por_extenso(valor)
    if campo == "modelo":
        return ModeloComunicado(valor).label if valor else ""
    if campo == "orientacao":
        return OrientacaoDoCard(valor).label if valor else ""
    if campo == "publico":
        return PublicoComunicado(valor).label if valor else ""
    return str(valor)
