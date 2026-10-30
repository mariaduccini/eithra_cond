"""Operações do domínio de manutenções operacionais.

Criar, editar, mover a situação, concluir, cancelar e retirar da operação sempre
gravam o histórico na mesma transação: ou a manutenção e sua memória mudam
juntas, ou nada muda. Nenhuma regra de tela mora aqui — a view traduz o que a
gestão decidiu, e o domínio decide, aplica e registra.

Duas ideias próximas não se confundem neste arquivo. Cancelar é um desfecho
operacional: a manutenção deixa de acontecer, e isso fica no histórico com quem
decidiu e quando. Desativar é retirada administrativa: o registro sai das listas
sem ser apagado e sem mexer na situação.

As datas seguem o que cada uma significa. ``data_prevista`` é planejamento, e é
ela que diz se algo está atrasado. ``data_inicio`` e ``data_conclusao`` são
acontecimentos e guardam dia e hora: a primeira é escrita quando o serviço
começa, a segunda quando ele termina — e some quando a manutenção volta a andar,
porque aí ela ainda não terminou.

A manutenção corretiva nasce de uma decisão ou de uma demanda. A preventiva
nasce de um plano, e é isso que ``gerar_ocorrencia_preventiva`` faz: transforma a
regra em uma ocorrência do dia. O plano continua sendo a regra — a ocorrência é
o serviço concreto, com o retrato do que o plano dizia quando nasceu.
"""
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.constants import Prioridade

from .constants import StatusManutencao, TipoEventoManutencao, TipoManutencao
from .models import HistoricoManutencao, Manutencao, PlanoPreventivo
from .periodicidade import proxima_data

# A descrição do evento cabe no histórico como uma frase curta.
LIMITE_DO_EVENTO = 255


class ErroDeManutencao(Exception):
    """Uma decisão do domínio sobre a manutenção que a gestão precisa saber."""


def _texto(valor: str | None) -> str:
    """Colapsa espaços repetidos e devolve string vazia quando não há conteúdo."""
    return " ".join((valor or "").split())


def _texto_longo(valor: str | None) -> str:
    """Preserva os parágrafos de um texto livre, sem espaços nas pontas."""
    return (valor or "").strip()


def _id_de(objeto):
    """A chave de um registro relacionado, ou nada quando não há."""
    return getattr(objeto, "pk", None)


def _id_em_texto(identificador):
    """A chave como o histórico a guarda, sem inventar valor quando não existe."""
    return str(identificador) if identificador else None


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


def _rotulo(objeto, vazio: str = "nada definido") -> str:
    """O nome pelo qual um cadastro relacionado aparece no histórico."""
    if objeto is None:
        return vazio
    return (
        getattr(objeto, "nome", None)
        or getattr(objeto, "nome_razao_social", None)
        or str(objeto)
        or vazio
    )


def _data_por_extenso(valor) -> str:
    """A data prevista como a gestão a lê, ou a ausência dela."""
    return valor.strftime("%d/%m/%Y") if valor else "sem previsão"


def _valor_por_extenso(valor) -> str:
    """O dinheiro como a gestão o lê, ou a ausência dele."""
    if valor is None:
        return "sem valor"
    return "R$ " + f"{valor:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _primeiro_recado(erro: ValidationError) -> str:
    """A primeira frase humana de um erro de validação do modelo."""
    if hasattr(erro, "message_dict"):
        for mensagens in erro.message_dict.values():
            if mensagens:
                return mensagens[0]
    if erro.messages:
        return erro.messages[0]
    return "Não foi possível guardar a manutenção."


def _registrar(
    manutencao,
    usuario,
    tipo_evento,
    descricao: str,
    *,
    anteriores=None,
    novos=None,
) -> HistoricoManutencao:
    """Acrescenta um evento ao histórico. Nunca reescreve o que já existe."""
    return HistoricoManutencao.objects.create(
        manutencao=manutencao,
        usuario=usuario,
        tipo_evento=tipo_evento,
        descricao=descricao[:LIMITE_DO_EVENTO],
        dados_anteriores=anteriores,
        dados_novos=novos,
    )


def _completar_local_pelo_equipamento(manutencao) -> None:
    """Quando o equipamento tem local e a manutenção não escolheu nenhum, assume o dele.

    É conveniência para não pedir duas vezes a mesma informação — e só isso.
    Uma escolha explícita divergente nunca é sobrescrita: a coerência é cobrada
    adiante, e a manutenção é recusada em vez de corrigida em silêncio.
    """
    if manutencao.local_id or not manutencao.equipamento_id:
        return

    local_do_equipamento = manutencao.equipamento.local
    if local_do_equipamento is not None:
        manutencao.local = local_do_equipamento


def _momento_do_fato(data) -> "datetime":
    """Quando o serviço terminou: o dia informado, com a hora do registro.

    O campo guarda dia e hora. Quando a gestão diz o dia em que o serviço foi
    concluído, é esse dia que fica guardado — a hora é a do momento em que ela
    registrou. Sem dia informado, vale o próprio momento do registro.
    """
    if data is None:
        return timezone.now()
    return timezone.make_aware(datetime.combine(data, timezone.localtime().time()))


def _validar(manutencao) -> None:
    """Confere a coerência da manutenção e traduz a recusa para a gestão."""
    try:
        manutencao.full_clean()
    except ValidationError as erro:
        raise ErroDeManutencao(_primeiro_recado(erro)) from erro


@transaction.atomic
def criar_manutencao(
    *,
    condominio,
    criado_por,
    titulo: str,
    tipo: str = TipoManutencao.CORRETIVA,
    descricao_problema: str = "",
    prioridade: str = Prioridade.NORMAL,
    local=None,
    equipamento=None,
    fornecedor=None,
    responsavel=None,
    data_prevista=None,
    custo_previsto=None,
    demanda_origem=None,
) -> Manutencao:
    """Registra a manutenção e nasce o primeiro evento do histórico.

    Nascer de uma demanda não altera a demanda: ela continua como está, e a
    procedência fica guardada aqui. Quem decidiu tratar o assunto como serviço
    foi a gestão, e o registro diz de onde ele veio.
    """
    manutencao = Manutencao(
        condominio=condominio,
        demanda_origem=demanda_origem,
        tipo=tipo,
        titulo=_texto(titulo),
        descricao_problema=_texto_longo(descricao_problema),
        prioridade=prioridade,
        local=local,
        equipamento=equipamento,
        fornecedor=fornecedor,
        responsavel=responsavel,
        data_prevista=data_prevista,
        custo_previsto=custo_previsto,
        criado_por=criado_por,
    )
    _completar_local_pelo_equipamento(manutencao)
    _validar(manutencao)
    manutencao.save()

    descricao = "Manutenção criada"
    if manutencao.demanda_origem_id:
        descricao = (
            f"Manutenção criada a partir da demanda "
            f"«{manutencao.demanda_origem.titulo}»"
        )
    _registrar(manutencao, criado_por, TipoEventoManutencao.CRIADA, descricao)
    return manutencao


@transaction.atomic
def editar_manutencao(
    *,
    manutencao: Manutencao,
    usuario,
    tipo: str,
    titulo: str,
    descricao_problema: str = "",
    descricao_servico: str = "",
    prioridade: str,
    local=None,
    equipamento=None,
    fornecedor=None,
    responsavel=None,
    data_prevista=None,
    custo_previsto=None,
    custo_real=None,
) -> Manutencao:
    """Atualiza o conteúdo da manutenção, guardando cada mudança que importa.

    Só o que realmente mudou vira evento — o histórico registra decisão, não o
    ato de abrir e salvar o formulário.
    """
    titulo = _texto(titulo)
    descricao_problema = _texto_longo(descricao_problema)
    descricao_servico = _texto_longo(descricao_servico)

    campos = ["atualizado_em"]

    if tipo != manutencao.tipo:
        _registrar(
            manutencao,
            usuario,
            TipoEventoManutencao.EDICAO,
            f"Tipo: {TipoManutencao(manutencao.tipo).label} → "
            f"{TipoManutencao(tipo).label}",
            anteriores={"tipo": manutencao.tipo},
            novos={"tipo": tipo},
        )
        manutencao.tipo = tipo
        campos.append("tipo")

    if titulo and titulo != manutencao.titulo:
        _registrar(
            manutencao,
            usuario,
            TipoEventoManutencao.EDICAO,
            "Título atualizado",
            anteriores={"titulo": manutencao.titulo},
            novos={"titulo": titulo},
        )
        manutencao.titulo = titulo
        campos.append("titulo")

    if descricao_problema != (manutencao.descricao_problema or ""):
        _registrar(
            manutencao,
            usuario,
            TipoEventoManutencao.EDICAO,
            "Problema atualizado",
            anteriores={"descricao_problema": manutencao.descricao_problema},
            novos={"descricao_problema": descricao_problema},
        )
        manutencao.descricao_problema = descricao_problema
        campos.append("descricao_problema")

    if descricao_servico != (manutencao.descricao_servico or ""):
        _registrar(
            manutencao,
            usuario,
            TipoEventoManutencao.SERVICO,
            "Serviço realizado atualizado",
            anteriores={"descricao_servico": manutencao.descricao_servico},
            novos={"descricao_servico": descricao_servico},
        )
        manutencao.descricao_servico = descricao_servico
        campos.append("descricao_servico")

    if prioridade != manutencao.prioridade:
        _registrar(
            manutencao,
            usuario,
            TipoEventoManutencao.PRIORIDADE,
            f"Prioridade: {Prioridade(manutencao.prioridade).label} → "
            f"{Prioridade(prioridade).label}",
            anteriores={"prioridade": manutencao.prioridade},
            novos={"prioridade": prioridade},
        )
        manutencao.prioridade = prioridade
        campos.append("prioridade")

    if _id_de(local) != manutencao.local_id:
        _registrar(
            manutencao,
            usuario,
            TipoEventoManutencao.EDICAO,
            f"Local: {_rotulo(manutencao.local, 'sem local')} → "
            f"{_rotulo(local, 'sem local')}",
            anteriores={"local": _id_em_texto(manutencao.local_id)},
            novos={"local": _id_em_texto(_id_de(local))},
        )
        manutencao.local = local
        campos.append("local")

    if _id_de(equipamento) != manutencao.equipamento_id:
        _registrar(
            manutencao,
            usuario,
            TipoEventoManutencao.EDICAO,
            f"Equipamento: {_rotulo(manutencao.equipamento, 'sem equipamento')} → "
            f"{_rotulo(equipamento, 'sem equipamento')}",
            anteriores={"equipamento": _id_em_texto(manutencao.equipamento_id)},
            novos={"equipamento": _id_em_texto(_id_de(equipamento))},
        )
        manutencao.equipamento = equipamento
        campos.append("equipamento")

    if _id_de(fornecedor) != manutencao.fornecedor_id:
        _registrar(
            manutencao,
            usuario,
            TipoEventoManutencao.EDICAO,
            f"Fornecedor: {_rotulo(manutencao.fornecedor, 'sem fornecedor')} → "
            f"{_rotulo(fornecedor, 'sem fornecedor')}",
            anteriores={"fornecedor": _id_em_texto(manutencao.fornecedor_id)},
            novos={"fornecedor": _id_em_texto(_id_de(fornecedor))},
        )
        manutencao.fornecedor = fornecedor
        campos.append("fornecedor")

    if _id_de(responsavel) != manutencao.responsavel_id:
        _registrar(
            manutencao,
            usuario,
            TipoEventoManutencao.RESPONSAVEL,
            f"Responsável: {_nome_de(manutencao.responsavel)} → "
            f"{_nome_de(responsavel)}",
            anteriores={"responsavel": _id_em_texto(manutencao.responsavel_id)},
            novos={"responsavel": _id_em_texto(_id_de(responsavel))},
        )
        manutencao.responsavel = responsavel
        campos.append("responsavel")

    if data_prevista != manutencao.data_prevista:
        _registrar(
            manutencao,
            usuario,
            TipoEventoManutencao.PREVISTA,
            f"Data prevista: {_data_por_extenso(manutencao.data_prevista)} → "
            f"{_data_por_extenso(data_prevista)}",
            anteriores={
                "data_prevista": (
                    manutencao.data_prevista.isoformat()
                    if manutencao.data_prevista
                    else None
                )
            },
            novos={"data_prevista": data_prevista.isoformat() if data_prevista else None},
        )
        manutencao.data_prevista = data_prevista
        campos.append("data_prevista")

    if custo_previsto != manutencao.custo_previsto:
        _registrar(
            manutencao,
            usuario,
            TipoEventoManutencao.EDICAO,
            f"Custo previsto: {_valor_por_extenso(manutencao.custo_previsto)} → "
            f"{_valor_por_extenso(custo_previsto)}",
            anteriores={
                "custo_previsto": (
                    str(manutencao.custo_previsto)
                    if manutencao.custo_previsto is not None
                    else None
                )
            },
            novos={
                "custo_previsto": str(custo_previsto) if custo_previsto is not None else None
            },
        )
        manutencao.custo_previsto = custo_previsto
        campos.append("custo_previsto")

    if custo_real != manutencao.custo_real:
        _registrar(
            manutencao,
            usuario,
            TipoEventoManutencao.EDICAO,
            f"Custo real: {_valor_por_extenso(manutencao.custo_real)} → "
            f"{_valor_por_extenso(custo_real)}",
            anteriores={
                "custo_real": (
                    str(manutencao.custo_real)
                    if manutencao.custo_real is not None
                    else None
                )
            },
            novos={"custo_real": str(custo_real) if custo_real is not None else None},
        )
        manutencao.custo_real = custo_real
        campos.append("custo_real")

    _completar_local_pelo_equipamento(manutencao)
    _validar(manutencao)
    manutencao.save(update_fields=campos)
    return manutencao


@transaction.atomic
def alterar_status(*, manutencao: Manutencao, usuario, novo_status: str) -> Manutencao:
    """Move a manutenção para outra situação, ajustando as datas que a acompanham.

    Começar registra quando o serviço começou — e só na primeira vez, para não
    reescrever o começo a cada ida e volta. Cancelar passa pela decisão própria,
    porque encerrar sem que o serviço aconteça é outra coisa que mover o serviço.
    Concluir tem ação própria: ela exige dizer o que foi feito.
    """
    if novo_status not in StatusManutencao.values:
        raise ErroDeManutencao("Situação inválida.")

    if novo_status == StatusManutencao.CONCLUIDA:
        raise ErroDeManutencao(
            "Para concluir, use a conclusão — ela pede o serviço realizado."
        )

    if novo_status == StatusManutencao.CANCELADA:
        return cancelar_manutencao(manutencao=manutencao, usuario=usuario)

    anterior = manutencao.status
    if novo_status == anterior:
        return manutencao

    campos = ["status", "atualizado_em"]

    if novo_status == StatusManutencao.EM_ANDAMENTO and manutencao.data_inicio is None:
        manutencao.data_inicio = timezone.now()
        campos.append("data_inicio")

    # A manutenção voltou a andar: o que estava registrado como conclusão deixa
    # de valer, porque ela ainda não terminou.
    if manutencao.data_conclusao is not None:
        manutencao.data_conclusao = None
        campos.append("data_conclusao")

    _registrar(
        manutencao,
        usuario,
        TipoEventoManutencao.STATUS,
        f"Situação: {StatusManutencao(anterior).label} → "
        f"{StatusManutencao(novo_status).label}",
        anteriores={"status": anterior},
        novos={"status": novo_status},
    )
    manutencao.status = novo_status
    manutencao.save(update_fields=campos)
    return manutencao


@transaction.atomic
def concluir_manutencao(
    *,
    manutencao: Manutencao,
    usuario,
    descricao_servico: str = "",
    custo_real=None,
    data_conclusao=None,
) -> Manutencao:
    """Conclui a manutenção, registrando o que foi feito e quando.

    Concluir exige dizer o que foi realizado — a manutenção não termina em
    silêncio. O custo real pode continuar vazio: nem todo serviço tem valor
    fechado no momento em que termina.

    A data é a do fato, não a do registro: uma manutenção feita em setembro
    pode ser lançada agora, e o que fica guardado é o dia em que o serviço
    terminou. Sem data informada, vale o momento do registro. O histórico
    continua guardando o momento em que a conclusão foi registrada — as duas
    informações são diferentes e as duas importam.
    """
    if manutencao.status == StatusManutencao.CONCLUIDA:
        raise ErroDeManutencao("Esta manutenção já foi concluída.")

    if data_conclusao is not None and data_conclusao > timezone.localdate():
        raise ErroDeManutencao("A data da conclusão não pode estar no futuro.")

    servico = _texto_longo(descricao_servico) or _texto_longo(manutencao.descricao_servico)
    if not servico:
        raise ErroDeManutencao(
            "Descreva o serviço realizado antes de concluir a manutenção."
        )

    campos = ["status", "data_conclusao", "descricao_servico", "atualizado_em"]

    if servico != (manutencao.descricao_servico or ""):
        _registrar(
            manutencao,
            usuario,
            TipoEventoManutencao.SERVICO,
            "Serviço realizado registrado",
            anteriores={"descricao_servico": manutencao.descricao_servico},
            novos={"descricao_servico": servico},
        )
        manutencao.descricao_servico = servico

    if custo_real is not None and custo_real != manutencao.custo_real:
        _registrar(
            manutencao,
            usuario,
            TipoEventoManutencao.EDICAO,
            f"Custo real: {_valor_por_extenso(manutencao.custo_real)} → "
            f"{_valor_por_extenso(custo_real)}",
            anteriores={
                "custo_real": (
                    str(manutencao.custo_real)
                    if manutencao.custo_real is not None
                    else None
                )
            },
            novos={"custo_real": str(custo_real) if custo_real is not None else None},
        )
        manutencao.custo_real = custo_real
        campos.append("custo_real")

    manutencao.status = StatusManutencao.CONCLUIDA
    manutencao.data_conclusao = _momento_do_fato(data_conclusao)
    _validar(manutencao)

    _registrar(
        manutencao,
        usuario,
        TipoEventoManutencao.CONCLUIDA,
        "Manutenção concluída",
        novos={"status": StatusManutencao.CONCLUIDA},
    )
    manutencao.save(update_fields=campos)
    return manutencao


@transaction.atomic
def cancelar_manutencao(
    *, manutencao: Manutencao, usuario, motivo: str = ""
) -> Manutencao:
    """Encerra a manutenção sem que o serviço aconteça.

    Cancelar é desfecho operacional, e não conclusão: o histórico guarda quem
    cancelou e quando, o motivo quando houver, e a manutenção cancelada não
    registra data de conclusão — ela não foi concluída.
    """
    if manutencao.status == StatusManutencao.CANCELADA:
        raise ErroDeManutencao("Esta manutenção já está cancelada.")

    motivo = _texto(motivo)
    descricao = "Manutenção cancelada"
    if motivo:
        descricao = f"Manutenção cancelada · {motivo}"

    _registrar(
        manutencao,
        usuario,
        TipoEventoManutencao.CANCELADA,
        descricao,
        anteriores={"status": manutencao.status},
        novos={"status": StatusManutencao.CANCELADA},
    )
    manutencao.status = StatusManutencao.CANCELADA
    manutencao.data_conclusao = None
    manutencao.save(update_fields=["status", "data_conclusao", "atualizado_em"])
    return manutencao


@transaction.atomic
def definir_ativo(*, manutencao: Manutencao, ativo: bool) -> Manutencao:
    """Retira a manutenção da operação, ou a devolve, sem apagar nada.

    É retirada administrativa, e não desfecho operacional: a situação e o
    histórico do serviço continuam como estão. Cancelar é outra coisa.
    """
    ativo = bool(ativo)
    if manutencao.ativo == ativo:
        return manutencao

    manutencao.ativo = ativo
    manutencao.save(update_fields=["ativo", "atualizado_em"])
    return manutencao


@transaction.atomic
def gerar_ocorrencia_preventiva(
    *, plano: PlanoPreventivo, usuario
) -> Manutencao:
    """Transforma o plano na manutenção do dia e avança o plano.

    O plano é a regra; a manutenção é o serviço concreto. Aqui o plano deixa de
    ser intenção: copia-se o contexto operacional — o que fazer, onde, com quem
    — e guarda-se a data que originou a ocorrência. A partir daí o plano pode
    mudar à vontade; a ocorrência já nasceu com o retrato daquele dia.

    Duas coisas não se misturam, e a escolha é deliberada. O vínculo com o plano
    é referência — a procedência da ocorrência, que continua legível depois. A
    competência é cópia imutável — a data gravada uma única vez, que não muda se
    a manutenção for reagendada. É por ela que a mesma ocorrência não nasce duas
    vezes, e não pela ``data_prevista``, que pode ser remarcada.

    Tudo acontece em uma transação, e o plano é travado antes de qualquer coisa:
    ou a ocorrência, o histórico e o próximo passo do plano acontecem juntos, ou
    nada acontece. Gerar de novo para a mesma competência devolve a ocorrência
    que já existe, sem criar uma segunda.
    """
    origem = plano
    plano = PlanoPreventivo.objects.select_for_update().get(pk=plano.pk)

    if not plano.ativo:
        raise ErroDeManutencao(
            "Este plano está inativo e não gera novas ocorrências."
        )

    competencia = plano.proxima_data_prevista

    # A competência é a identidade da ocorrência. Se ela já gerou uma
    # manutenção, é essa que vale — e o plano já avançou quando ela nasceu.
    existente = Manutencao.objects.filter(
        plano_preventivo=plano, competencia_preventiva=competencia
    ).first()
    if existente is not None:
        return existente

    manutencao = Manutencao(
        condominio=plano.condominio,
        plano_preventivo=plano,
        competencia_preventiva=competencia,
        tipo=TipoManutencao.PREVENTIVA,
        titulo=plano.titulo,
        descricao_problema=plano.descricao,
        local=plano.local,
        equipamento=plano.equipamento,
        fornecedor=plano.fornecedor,
        responsavel=plano.responsavel,
        data_prevista=competencia,
        criado_por=usuario,
    )
    _completar_local_pelo_equipamento(manutencao)
    _validar(manutencao)
    manutencao.save()

    _registrar(
        manutencao,
        usuario,
        TipoEventoManutencao.CRIADA,
        f"Manutenção preventiva criada a partir do plano «{plano.titulo}»",
    )

    # A próxima data só anda depois que a ocorrência existe. Uma competência
    # vencida continua sendo a próxima até ser gerada — nada é pulado em
    # silêncio.
    plano.proxima_data_prevista = proxima_data(
        unidade=plano.frequencia_unidade,
        intervalo=plano.frequencia_intervalo,
        a_partir_de=competencia,
    )
    plano.save(update_fields=["proxima_data_prevista", "atualizado_em"])
    # A quem chamou, a data vigente passa a ser esta — e não a que ele tinha
    # quando pediu a geração.
    origem.proxima_data_prevista = plano.proxima_data_prevista
    return manutencao

