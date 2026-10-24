"""Operações do domínio de demandas operacionais.

Criar, editar, mover a situação, concluir, cancelar ou observar sempre grava o
histórico na mesma transação: ou a demanda e sua memória mudam juntas, ou nada
muda. Nenhuma regra de tela mora aqui — a view traduz o que a gestão decidiu, e
o domínio decide e registra.
"""
from django.db import transaction
from django.utils import timezone

from .constants import (
    ImpactoDemanda,
    OrigemDemanda,
    PrioridadeDemanda,
    RiscoDemanda,
    StatusDemanda,
    TipoEventoDemanda,
    TipoProblema,
)
from .models import Demanda, HistoricoDemanda

# A descrição do evento cabe no histórico como uma frase curta. Observações
# longas são cortadas aqui, e não na tela.
LIMITE_DA_OBSERVACAO = 255

# Marca de "o chamador não falou sobre este campo". É diferente de ``None``, que
# é uma decisão: limpar o equipamento ou tirar o tipo de problema. Sem essa
# distinção, abrir e salvar o formulário antigo apagaria o que ninguém pediu
# para apagar — e o histórico registraria uma mudança que não houve.
_NAO_INFORMADO = object()


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


def _nome_do_local(local) -> str:
    """O local como a gestão o lê no histórico, ou a ausência dele."""
    if local is None or not getattr(local, "pk", None):
        return "sem local"
    return local.nome


def _nome_do_equipamento(equipamento) -> str:
    """O equipamento como a gestão o lê no histórico, ou a ausência dele."""
    if equipamento is None or not getattr(equipamento, "pk", None):
        return "sem equipamento"
    return equipamento.nome


def _rotulo(escolhas, valor, vazio: str = "não informado") -> str:
    """Como a gestão lê um valor de escolha do cadastro, ou a ausência dele.

    O valor gravado nunca aparece cru: o histórico fala a mesma língua da tela.
    """
    if not valor:
        return vazio
    try:
        return escolhas(valor).label
    except ValueError:
        return valor


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
    local=None,
    local_contexto: str = "",
    equipamento=None,
    tipo_problema=None,
    impacto=None,
    risco=None,
    classificacao_sugerida=None,
) -> Demanda:
    """Registra a demanda e nasce o primeiro evento do histórico.

    O local vem do cadastro do condomínio. ``local_contexto`` é o texto legado
    das demandas antigas: continua aceito, mas não é preenchido por ninguém.

    ``classificacao_sugerida`` guarda o que a leitura do relato propôs, com a
    origem e a confiança de cada campo. Não é o mesmo que a demanda passou a
    valer: a sugestão existe para ser comparada com a confirmação mais adiante,
    e nunca é apresentada como fato.

    A área não é coluna: ela é derivada do tipo do local cadastrado, e a
    recorrência é uma leitura do histórico — guardar qualquer uma delas criaria
    uma segunda fonte de verdade que envelhece sozinha.
    """
    demanda = Demanda.objects.create(
        condominio=condominio,
        titulo=_texto(titulo),
        descricao=_texto_longo(descricao),
        origem=origem,
        prioridade=prioridade,
        responsavel=responsavel,
        prazo=prazo,
        local=local,
        local_contexto=_texto(local_contexto),
        equipamento=equipamento,
        tipo_problema=tipo_problema or None,
        impacto=impacto or None,
        risco=risco or None,
        classificacao_sugerida=classificacao_sugerida,
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
    local=None,
    local_contexto=None,
    equipamento=_NAO_INFORMADO,
    tipo_problema=_NAO_INFORMADO,
    impacto=_NAO_INFORMADO,
    risco=_NAO_INFORMADO,
) -> Demanda:
    """Atualiza o conteúdo da demanda, guardando cada mudança que importa.

    Só o que realmente mudou vira evento — o histórico registra decisão, não o
    ato de abrir e salvar o formulário. ``local_contexto`` só é tocado quando
    vem explícito: sem isso, o texto legado das demandas antigas fica intacto.

    Equipamento e classificação seguem a mesma regra, com uma marca própria de
    ausência: quem não fala do campo não o apaga, e quem manda vazio está
    decidindo limpá-lo. A classificação que a leitura propôs não é reescrita
    aqui — o que a gestão confirma vira o valor da demanda, e é isso que muda.
    """
    titulo = _texto(titulo)
    descricao = _texto_longo(descricao)

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

    if getattr(local, "pk", None) != demanda.local_id:
        _registrar(
            demanda,
            usuario,
            TipoEventoDemanda.EDICAO,
            f"Local: {_nome_do_local(demanda.local)} → {_nome_do_local(local)}",
            anteriores={"local": str(demanda.local_id) if demanda.local_id else None},
            novos={"local": str(local.pk) if getattr(local, "pk", None) else None},
        )
        demanda.local = local
        campos.append("local")

    if (
        equipamento is not _NAO_INFORMADO
        and getattr(equipamento, "pk", None) != demanda.equipamento_id
    ):
        _registrar(
            demanda,
            usuario,
            TipoEventoDemanda.EDICAO,
            f"Equipamento: {_nome_do_equipamento(demanda.equipamento)} → "
            f"{_nome_do_equipamento(equipamento)}",
            anteriores={
                "equipamento": str(demanda.equipamento_id)
                if demanda.equipamento_id
                else None
            },
            novos={
                "equipamento": str(equipamento.pk)
                if getattr(equipamento, "pk", None)
                else None
            },
        )
        demanda.equipamento = equipamento
        campos.append("equipamento")

    if local_contexto is not None:
        local_contexto = _texto(local_contexto)
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

    if tipo_problema is not _NAO_INFORMADO:
        novo = tipo_problema or None
        if novo != demanda.tipo_problema:
            _registrar(
                demanda,
                usuario,
                TipoEventoDemanda.EDICAO,
                "Tipo de problema: "
                f"{_rotulo(TipoProblema, demanda.tipo_problema)} → "
                f"{_rotulo(TipoProblema, novo)}",
                anteriores={"tipo_problema": demanda.tipo_problema},
                novos={"tipo_problema": novo},
            )
            demanda.tipo_problema = novo
            campos.append("tipo_problema")

    if impacto is not _NAO_INFORMADO:
        novo = impacto or None
        if novo != demanda.impacto:
            _registrar(
                demanda,
                usuario,
                TipoEventoDemanda.EDICAO,
                f"Impacto: {_rotulo(ImpactoDemanda, demanda.impacto)} → "
                f"{_rotulo(ImpactoDemanda, novo)}",
                anteriores={"impacto": demanda.impacto},
                novos={"impacto": novo},
            )
            demanda.impacto = novo
            campos.append("impacto")

    if risco is not _NAO_INFORMADO:
        novo = risco or None
        if novo != demanda.risco:
            _registrar(
                demanda,
                usuario,
                TipoEventoDemanda.EDICAO,
                f"Risco: {_rotulo(RiscoDemanda, demanda.risco)} → "
                f"{_rotulo(RiscoDemanda, novo)}",
                anteriores={"risco": demanda.risco},
                novos={"risco": novo},
            )
            demanda.risco = novo
            campos.append("risco")

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


def contexto_de_governanca(*, demanda: Demanda, usuario) -> dict:
    """O que a Governança já sabe sobre o local e o equipamento desta demanda.

    Contrato e garantia não são copiados para a demanda: quem responde por eles
    é a Governança, e a pergunta é feita na hora pelos vínculos que já existem —
    contrato que cobre o equipamento, contrato que cobre o local, garantia que
    cobre o equipamento. Guardar um atalho aqui criaria duas respostas para a
    mesma pergunta, e uma delas envelheceria.

    O import acontece dentro da função porque nenhum domínio conhece a
    Governança de antemão; é a demanda que pergunta, e só quando alguém abre a
    tela naquele condomínio.

    Sem permissão de consultar contratos e garantias ali, a resposta é vazia:
    saber que existe um contrato não é informação de quem não responde por ele.
    """
    from apps.governanca.constants import SituacaoVigencia
    from apps.governanca.models import (
        ContratoEquipamento,
        ContratoFornecedor,
        ContratoLocal,
        Garantia,
        GarantiaEquipamento,
    )
    from apps.governanca.permissoes import pode_ver_governanca

    resposta = {"pode_consultar": False, "contratos": [], "garantias": []}
    if not pode_ver_governanca(usuario, getattr(demanda, "condominio", None)):
        return resposta

    resposta["pode_consultar"] = True

    contratos_vinculados = set()
    if demanda.equipamento_id:
        contratos_vinculados.update(
            ContratoEquipamento.objects.filter(
                equipamento_id=demanda.equipamento_id
            ).values_list("contrato_id", flat=True)
        )
    if demanda.local_id:
        contratos_vinculados.update(
            ContratoLocal.objects.filter(local_id=demanda.local_id).values_list(
                "contrato_id", flat=True
            )
        )
    resposta["contratos"] = [
        {
            "objeto": contrato.objeto,
            "fornecedor": str(contrato.fornecedor),
            "fim": contrato.data_fim,
            "situacao": SituacaoVigencia(contrato.situacao).label,
            "tom": contrato.tom,
        }
        for contrato in ContratoFornecedor.objects.ativos()
        .filter(pk__in=contratos_vinculados)
        .select_related("fornecedor")
        .na_ordem_de_atencao()
    ]

    garantias_vinculadas = set()
    if demanda.equipamento_id:
        garantias_vinculadas.update(
            GarantiaEquipamento.objects.filter(
                equipamento_id=demanda.equipamento_id
            ).values_list("garantia_id", flat=True)
        )
    resposta["garantias"] = [
        {
            "descricao": garantia.descricao,
            "fornecedor": str(garantia.fornecedor) if garantia.fornecedor_id else "",
            "fim": garantia.data_fim,
            "situacao": SituacaoVigencia(garantia.situacao).label,
            "tom": garantia.tom,
        }
        for garantia in Garantia.objects.ativos()
        .filter(pk__in=garantias_vinculadas)
        .select_related("fornecedor")
        .na_ordem_de_atencao()
    ]

    return resposta
