"""Operações do domínio de demandas operacionais.

Criar, editar, mover a situação, concluir, cancelar ou observar sempre grava o
histórico na mesma transação: ou a demanda e sua memória mudam juntas, ou nada
muda. Nenhuma regra de tela mora aqui — a view traduz o que a gestão decidiu, e
o domínio decide e registra.
"""
from django.db import transaction
from django.utils import timezone

from .constants import (
    OrigemDemanda,
    PrioridadeDemanda,
    StatusDemanda,
    TipoEventoDemanda,
)
from .models import Demanda, HistoricoDemanda

# A descrição do evento cabe no histórico como uma frase curta. Observações
# longas são cortadas aqui, e não na tela.
LIMITE_DA_OBSERVACAO = 255


class ErroDeDemanda(Exception):
    """Uma decisão do domínio sobre a demanda que a gestão precisa saber."""


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


def _data_por_extenso(valor) -> str:
    """A data como a gestão a lê, ou a ausência dela."""
    return valor.strftime("%d/%m/%Y") if valor else "sem prazo"


def _registrar(
    demanda,
    usuario,
    tipo_evento,
    descricao: str,
    *,
    anteriores=None,
    novos=None,
) -> HistoricoDemanda:
    """Acrescenta um evento ao histórico. Nunca reescreve o que já existe."""
    return HistoricoDemanda.objects.create(
        demanda=demanda,
        usuario=usuario,
        tipo_evento=tipo_evento,
        descricao=descricao[:LIMITE_DA_OBSERVACAO],
        dados_anteriores=anteriores,
        dados_novos=novos,
    )


@transaction.atomic
def criar_demanda(
    *,
    condominio,
    criado_por,
    titulo: str,
    descricao: str = "",
    origem: str = OrigemDemanda.MANUAL,
    prioridade: str = PrioridadeDemanda.NORMAL,
    responsavel=None,
    prazo=None,
    local_contexto: str = "",
) -> Demanda:
    """Registra a demanda e nasce o primeiro evento do histórico."""
    demanda = Demanda.objects.create(
        condominio=condominio,
        titulo=_texto(titulo),
        descricao=_texto_longo(descricao),
        origem=origem,
        prioridade=prioridade,
        responsavel=responsavel,
        prazo=prazo,
        local_contexto=_texto(local_contexto),
        criado_por=criado_por,
    )
    _registrar(demanda, criado_por, TipoEventoDemanda.CRIADA, "Demanda criada")
    return demanda


@transaction.atomic
def editar_demanda(
    *,
    demanda: Demanda,
    usuario,
    titulo: str,
    descricao: str = "",
    prioridade: str,
    responsavel=None,
    prazo=None,
    local_contexto: str = "",
) -> Demanda:
    """Atualiza o conteúdo da demanda, guardando cada mudança que importa.

    Só o que realmente mudou vira evento — o histórico registra decisão, não o
    ato de abrir e salvar o formulário.
    """
    titulo = _texto(titulo)
    descricao = _texto_longo(descricao)
    local_contexto = _texto(local_contexto)

    campos = ["atualizado_em"]

    if titulo and titulo != demanda.titulo:
        _registrar(
            demanda,
            usuario,
            TipoEventoDemanda.EDICAO,
            "Título atualizado",
            anteriores={"titulo": demanda.titulo},
            novos={"titulo": titulo},
        )
        demanda.titulo = titulo
        campos.append("titulo")

    if descricao != (demanda.descricao or ""):
        _registrar(
            demanda,
            usuario,
            TipoEventoDemanda.EDICAO,
            "Descrição atualizada",
            anteriores={"descricao": demanda.descricao},
            novos={"descricao": descricao},
        )
        demanda.descricao = descricao
        campos.append("descricao")

    if local_contexto != (demanda.local_contexto or ""):
        _registrar(
            demanda,
            usuario,
            TipoEventoDemanda.EDICAO,
            "Local atualizado",
            anteriores={"local_contexto": demanda.local_contexto},
            novos={"local_contexto": local_contexto},
        )
        demanda.local_contexto = local_contexto
        campos.append("local_contexto")

    if prioridade != demanda.prioridade:
        _registrar(
            demanda,
            usuario,
            TipoEventoDemanda.PRIORIDADE,
            f"Prioridade: {PrioridadeDemanda(demanda.prioridade).label} → "
            f"{PrioridadeDemanda(prioridade).label}",
            anteriores={"prioridade": demanda.prioridade},
            novos={"prioridade": prioridade},
        )
        demanda.prioridade = prioridade
        campos.append("prioridade")

    if getattr(responsavel, "pk", None) != demanda.responsavel_id:
        _registrar(
            demanda,
            usuario,
            TipoEventoDemanda.RESPONSAVEL,
            f"Responsável: {_nome_de(demanda.responsavel)} → {_nome_de(responsavel)}",
            anteriores={
                "responsavel": str(demanda.responsavel_id) if demanda.responsavel_id else None
            },
            novos={
                "responsavel": str(responsavel.pk)
                if getattr(responsavel, "pk", None)
                else None
            },
        )
        demanda.responsavel = responsavel
        campos.append("responsavel")

    if prazo != demanda.prazo:
        _registrar(
            demanda,
            usuario,
            TipoEventoDemanda.PRAZO,
            f"Prazo: {_data_por_extenso(demanda.prazo)} → {_data_por_extenso(prazo)}",
            anteriores={"prazo": demanda.prazo.isoformat() if demanda.prazo else None},
            novos={"prazo": prazo.isoformat() if prazo else None},
        )
        demanda.prazo = prazo
        campos.append("prazo")

    demanda.save(update_fields=campos)
    return demanda


@transaction.atomic
def alterar_status(*, demanda: Demanda, usuario, novo_status: str) -> Demanda:
    """Move a demanda para outra situação, ajustando as datas que a acompanham.

    Concluir registra quando; cancelar registra quando; voltar a andar limpa as
    duas. A situação e as datas nunca se contradizem — e o evento que descreve a
    mudança é do tipo certo: conclusão, cancelamento ou simples movimento.
    """
    if novo_status not in StatusDemanda.values:
        raise ErroDeDemanda("Situação inválida.")

    anterior = demanda.status
    if novo_status == anterior:
        return demanda

    campos = ["status", "atualizado_em"]

    if novo_status == StatusDemanda.CONCLUIDA:
        demanda.concluido_em = timezone.now()
        demanda.cancelado_em = None
        campos += ["concluido_em", "cancelado_em"]
        tipo_evento = TipoEventoDemanda.CONCLUIDA
        descricao = "Demanda concluída"
    elif novo_status == StatusDemanda.CANCELADA:
        demanda.cancelado_em = timezone.now()
        demanda.concluido_em = None
        campos += ["concluido_em", "cancelado_em"]
        tipo_evento = TipoEventoDemanda.CANCELADA
        descricao = "Demanda cancelada"
    else:
        demanda.concluido_em = None
        demanda.cancelado_em = None
        campos += ["concluido_em", "cancelado_em"]
        tipo_evento = TipoEventoDemanda.STATUS
        descricao = (
            f"Situação: {StatusDemanda(anterior).label} → "
            f"{StatusDemanda(novo_status).label}"
        )

    _registrar(
        demanda,
        usuario,
        tipo_evento,
        descricao,
        anteriores={"status": anterior},
        novos={"status": novo_status},
    )
    demanda.status = novo_status
    demanda.save(update_fields=campos)
    return demanda


@transaction.atomic
def registrar_observacao(*, demanda: Demanda, usuario, texto: str):
    """Registra o que se sabe sobre a demanda, sem mexer em nenhum campo dela."""
    texto = _texto(texto)
    if not texto:
        raise ErroDeDemanda("Escreva a observação antes de registrar.")

    return _registrar(demanda, usuario, TipoEventoDemanda.OBSERVACAO, texto)
