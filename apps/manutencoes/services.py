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
import logging

from dataclasses import dataclass, field
from datetime import date, datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.automacao import resolver_ator_automatico
from apps.core.constants import Prioridade

from .constants import (
    ResultadoCriterio,
    StatusConferencia,
    StatusManutencao,
    TipoEventoManutencao,
    TipoManutencao,
)
from .models import (
    CriterioConferenciaOcorrencia,
    HistoricoManutencao,
    Manutencao,
    PlanoPreventivo,
)
from .periodicidade import proxima_data

# A descrição do evento cabe no histórico como uma frase curta.
LIMITE_DO_EVENTO = 255

logger = logging.getLogger(__name__)


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


def estado_inicial_de_conferencia(exige_conferencia: bool) -> str:
    """A única autoridade sobre como a conferência nasce numa ocorrência.

    Exigir conferência faz a ocorrência nascer aguardando; não exigir faz a
    conferência nascer como não exigida. Essa decisão mora aqui e em mais
    nenhum lugar: quem cria uma ocorrência pergunta a esta função, em vez de
    repetir o mesmo ``if`` em cada ponto de geração.
    """
    if exige_conferencia:
        return StatusConferencia.PENDENTE
    return StatusConferencia.NAO_EXIGIDA


def _exigir_conferencia_pendente(manutencao) -> None:
    """Recusa conferir o que não deve conferência, o que não tem execução e o
    que já foi decidido.

    A ordem das recusas segue a pergunta natural: antes de decidir, é preciso
    que haja conferência devida e algo entregue para olhar.
    """
    if not manutencao.exige_conferencia:
        raise ErroDeManutencao("Esta manutenção não passa por conferência.")

    if manutencao.data_execucao is None:
        raise ErroDeManutencao(
            "Registre a execução antes de conferir o que foi entregue."
        )

    if manutencao.status_conferencia == StatusConferencia.APROVADA:
        raise ErroDeManutencao("Esta execução já foi aprovada.")

    if manutencao.status_conferencia == StatusConferencia.REPROVADA:
        raise ErroDeManutencao(
            "Esta execução foi reprovada. Registre a correção antes de conferir "
            "de novo."
        )


def _retratar_criterios(manutencao, plano) -> None:
    """Copia os critérios do plano para a ocorrência, como retrato do dia.

    Só faz sentido quando a ocorrência exige conferência — é para ela que os
    itens servem. A cópia é do valor, não da referência: o plano pode mudar
    depois sem tocar no que já nasceu.
    """
    if not manutencao.exige_conferencia:
        return

    CriterioConferenciaOcorrencia.objects.bulk_create(
        [
            CriterioConferenciaOcorrencia(
                manutencao=manutencao,
                descricao=criterio.descricao,
                ordem=criterio.ordem,
                obrigatorio=criterio.obrigatorio,
            )
            for criterio in plano.criterios_conferencia.all()
        ]
    )


def _aplicar_avaliacoes(manutencao, avaliacoes) -> None:
    """Grava o resultado de cada item conferido, sem decidir a conferência.

    Cada avaliação aponta um item pelo próprio critério ou pelo ``pk``. O item
    precisa pertencer a esta manutenção — conferir item de outro lugar não faz
    sentido. Resultado desconhecido é recusado em vez de adivinhado.
    """
    if not avaliacoes:
        return

    itens = {str(criterio.pk): criterio for criterio in manutencao.criterios_conferencia.all()}

    for avaliacao in avaliacoes:
        alvo = avaliacao.get("criterio")
        criterio = itens.get(str(getattr(alvo, "pk", alvo)))
        if criterio is None:
            raise ErroDeManutencao(
                "O item de conferência não pertence a esta manutenção."
            )

        resultado = avaliacao.get("resultado") or ResultadoCriterio.NAO_AVALIADO
        if resultado not in ResultadoCriterio.values:
            raise ErroDeManutencao("O resultado da conferência não é reconhecido.")

        criterio.resultado = resultado
        criterio.observacao = _texto(avaliacao.get("observacao"))
        criterio.save(update_fields=["resultado", "observacao"])


def _criterios_pendentes(manutencao) -> list[str]:
    """Os itens obrigatórios que ainda impedem a aprovação, por descrição.

    Item obrigatório só aprova quando está ``CONFORME``. Não avaliado, não
    conforme ou "não se aplica" continuam devendo uma decisão humana — o sistema
    não decide em silêncio que uma obrigação deixou de existir.
    """
    return [
        criterio.descricao
        for criterio in manutencao.criterios_conferencia.all()
        if criterio.obrigatorio and criterio.resultado != ResultadoCriterio.CONFORME
    ]


def _reabrir_criterios(manutencao) -> None:
    """Devolve os itens ao estado inicial, para uma nova conferência.

    Chamado quando uma nova execução reabre a conferência: os itens conferidos
    na entrega anterior não valem para a nova, e não podem aprová-la por
    inércia. O que foi decidido antes continua no histórico, e é de lá que a
    avaliação anterior é lida.
    """
    manutencao.criterios_conferencia.update(
        resultado=ResultadoCriterio.NAO_AVALIADO, observacao=""
    )


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

    # Onde há conferência devida, ela vem antes do fim. Executar não substitui
    # aprovar: enquanto a conferência não aprovou, a manutenção não termina.
    if (
        manutencao.exige_conferencia
        and manutencao.status_conferencia != StatusConferencia.APROVADA
    ):
        raise ErroDeManutencao(
            "Esta manutenção exige conferência aprovada antes de ser concluída."
        )

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
def registrar_execucao(
    *,
    manutencao: Manutencao,
    usuario,
    descricao_servico: str,
    data_execucao=None,
) -> Manutencao:
    """Registra que o serviço foi executado, sem concluir a manutenção.

    Executar e concluir não são a mesma coisa. Registrar a execução diz o que
    foi feito e quando; a manutenção continua aberta. Quando ela exige
    conferência, passa a aguardar quem confira o que foi entregue — e a
    conclusão segue dependendo de uma decisão à parte.

    O fornecedor não vem por parâmetro: é o que já responde pela manutenção,
    quando houver, e é ele que o histórico cita. Registrar de novo depois de uma
    reprovação descreve a nova execução e reabre a conferência, sem apagar a
    tentativa anterior — o histórico guarda as duas.

    A data é a do fato, não a do registro: um serviço feito ontem pode ser
    lançado hoje, e o que fica guardado é o dia em que ele aconteceu.
    """
    manutencao = Manutencao.objects.select_for_update().get(pk=manutencao.pk)

    if manutencao.esta_encerrada:
        raise ErroDeManutencao(
            "Esta manutenção já foi encerrada e não registra nova execução."
        )

    if manutencao.status_conferencia == StatusConferencia.APROVADA:
        raise ErroDeManutencao(
            "Esta execução já foi aprovada; conclua a manutenção."
        )

    servico = _texto_longo(descricao_servico)
    if not servico:
        raise ErroDeManutencao("Descreva o serviço realizado.")

    if data_execucao is not None and data_execucao > timezone.localdate():
        raise ErroDeManutencao("A data da execução não pode estar no futuro.")

    anterior = manutencao.status_conferencia

    campos = ["descricao_servico", "data_execucao", "atualizado_em"]
    manutencao.descricao_servico = servico
    manutencao.data_execucao = _momento_do_fato(data_execucao)

    if manutencao.exige_conferencia:
        manutencao.status_conferencia = StatusConferencia.PENDENTE
        campos.append("status_conferencia")
        if anterior == StatusConferencia.REPROVADA:
            # Nova entrega depois de uma correção: os itens voltam a aguardar,
            # e não podem aprovar por inércia. O que foi reprovado antes já
            # ficou no histórico da reprovação.
            _reabrir_criterios(manutencao)

    descricao = "Execução registrada"
    if anterior == StatusConferencia.REPROVADA:
        descricao = "Nova execução registrada após correção"
    if manutencao.fornecedor_id:
        descricao = f"{descricao} · {_rotulo(manutencao.fornecedor)}"

    _validar(manutencao)
    _registrar(
        manutencao,
        usuario,
        TipoEventoManutencao.EXECUCAO,
        descricao,
        novos={"descricao_servico": servico},
    )
    manutencao.save(update_fields=campos)
    return manutencao


@transaction.atomic
def aprovar_conferencia(
    *, manutencao: Manutencao, usuario, avaliacoes=None, observacao: str = ""
) -> Manutencao:
    """Aprova a conferência de uma execução, sem concluir a manutenção.

    Conferir é olhar o que foi entregue — os itens observáveis, os documentos,
    as evidências. Aprovar registra que a execução passou por essa olhada; não é
    um atestado de que o serviço está tecnicamente correto, e não conclui a
    manutenção. A conclusão continua sendo uma decisão à parte. Quem aprovou e
    quando ficam no histórico.

    Antes de aprovar, os itens conferidos são gravados e os obrigatórios são
    cobrados: item obrigatório que não está conforme — por não ter sido olhado,
    por estar reprovado ou por não se aplicar — impede a aprovação. O que é
    opcional pode ficar sem resposta sem travar a decisão.
    """
    manutencao = Manutencao.objects.select_for_update().get(pk=manutencao.pk)
    _exigir_conferencia_pendente(manutencao)

    _aplicar_avaliacoes(manutencao, avaliacoes)

    pendentes = _criterios_pendentes(manutencao)
    if pendentes:
        raise ErroDeManutencao(
            "Ainda há item obrigatório sem conformidade: "
            + "; ".join(pendentes)
            + "."
        )

    manutencao.status_conferencia = StatusConferencia.APROVADA

    descricao = "Conferência aprovada"
    observacao = _texto(observacao)
    if observacao:
        descricao = f"{descricao} · {observacao}"

    _validar(manutencao)
    _registrar(
        manutencao,
        usuario,
        TipoEventoManutencao.CONFERENCIA_APROVADA,
        descricao,
        novos={"status_conferencia": StatusConferencia.APROVADA},
    )
    manutencao.save(update_fields=["status_conferencia", "atualizado_em"])
    return manutencao


@transaction.atomic
def reprovar_conferencia(
    *, manutencao: Manutencao, usuario, motivo: str, criterios_nao_conformes=None
) -> Manutencao:
    """Reprova a conferência: o que foi entregue precisa de correção.

    Reprovar não encerra nada. A manutenção continua aberta, com o motivo no
    histórico, e uma nova execução depois da correção reabre a conferência. O
    motivo é obrigatório — reprovar sem dizer o que falta não ajuda quem vai
    corrigir.

    Os itens que ficaram não conformes podem ser apontados junto com a
    reprovação; eles são gravados como tal e citados no histórico, para a
    correção saber exatamente o que refazer. A tentativa reprovada não é
    apagada: fica registrada antes de uma nova execução reabrir a conferência.
    """
    manutencao = Manutencao.objects.select_for_update().get(pk=manutencao.pk)
    _exigir_conferencia_pendente(manutencao)

    motivo = _texto(motivo)
    if not motivo:
        raise ErroDeManutencao("Escreva o motivo da reprovação.")

    if criterios_nao_conformes:
        _aplicar_avaliacoes(
            manutencao,
            [
                {
                    "criterio": item.get("criterio"),
                    "resultado": ResultadoCriterio.NAO_CONFORME,
                    "observacao": item.get("observacao"),
                }
                for item in criterios_nao_conformes
            ],
        )

    manutencao.status_conferencia = StatusConferencia.REPROVADA

    nao_conformes = list(
        manutencao.criterios_conferencia.filter(
            resultado=ResultadoCriterio.NAO_CONFORME
        ).values_list("descricao", flat=True)
    )

    descricao = f"Conferência reprovada · {motivo}"
    if nao_conformes:
        descricao = f"{descricao} · {', '.join(nao_conformes)}"

    _validar(manutencao)
    _registrar(
        manutencao,
        usuario,
        TipoEventoManutencao.CONFERENCIA_REPROVADA,
        descricao,
        novos={
            "status_conferencia": StatusConferencia.REPROVADA,
            "motivo": motivo,
            "criterios_nao_conformes": nao_conformes,
        },
    )
    manutencao.save(update_fields=["status_conferencia", "atualizado_em"])
    return manutencao


@transaction.atomic
def nao_avaliar_conferencia(
    *, manutencao: Manutencao, usuario, observacao: str = ""
) -> Manutencao:
    """Registra que a conferência não pôde ser concluída agora.

    Não é aprovação nem reprovação: falta acesso, falta documento, falta quem
    responda. A conferência continua pendente e a manutenção segue aberta — o
    que fica é a observação no histórico, para o assunto poder ser retomado
    depois. Não se cria um estado técnico novo para o que ainda não foi
    decidido.
    """
    manutencao = Manutencao.objects.select_for_update().get(pk=manutencao.pk)
    _exigir_conferencia_pendente(manutencao)

    descricao = "Não foi possível avaliar a execução"
    observacao = _texto(observacao)
    if observacao:
        descricao = f"{descricao} · {observacao}"

    _registrar(
        manutencao,
        usuario,
        TipoEventoManutencao.CONFERENCIA_NAO_AVALIADA,
        descricao,
    )
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

    A regra de conferência também é retrato, e não referência: a ocorrência
    copia se exige conferência e os critérios daquele dia. Mudar o plano depois
    não alcança o que já nasceu — uma ocorrência de outubro não passa a ser
    avaliada pelos critérios criados em novembro.

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
        exige_conferencia=plano.exige_conferencia,
        status_conferencia=estado_inicial_de_conferencia(plano.exige_conferencia),
        criado_por=usuario,
    )
    _completar_local_pelo_equipamento(manutencao)
    _validar(manutencao)
    manutencao.save()

    # Os critérios viram retrato próprio da ocorrência. Só faz sentido quando
    # ela exige conferência — o helper cuida disso.
    _retratar_criterios(manutencao, plano)

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


# ---------------------------------------------------------------------------
# Geração periódica
# ---------------------------------------------------------------------------
#
# Gerar as preventivas de uma vez não é uma segunda forma de criar manutenção: é
# a mesma, repetida. O gerador não decide o que é uma ocorrência, não calcula a
# próxima data, não copia contexto nem escreve histórico — ele só encontra os
# planos que já venceram e entrega cada um ao serviço oficial, uma competência
# por vez. É por isso que recuperar uma parcela atrasada é o caminho comum, e
# não um atalho paralelo que precisaria ser mantido em dobro.


@dataclass
class ResumoDasPreventivas:
    """O que a rodada fez, em números, sem guardar estado nenhum.

    Não existe registro do gerador: cada ocorrência já conta a própria criação
    no seu histórico, e uma tabela de execuções seria uma segunda memória para o
    mesmo fato. O resumo serve para ler o resultado na hora e desaparecer.
    """

    data_referencia: date
    planos_verificados: int = 0
    planos_processados: int = 0
    planos_sem_necessidade: int = 0
    ocorrencias_geradas: int = 0
    competencias_reconciliadas: int = 0
    planos_limitados: list[str] = field(default_factory=list)
    falhas: list[tuple[str, str]] = field(default_factory=list)


def gerar_preventivas_pendentes(
    *, usuario=None, data_referencia=None, condominio=None
) -> ResumoDasPreventivas:
    """Gera as ocorrências dos planos preventivos que já venceram.

    Um plano vence quando sua ``proxima_data_prevista`` é anterior ou igual à
    data de referência — o plano não pula dia, e o que ficou para trás continua
    devendo. Sem condomínio informado, a rodada alcança todos; com um informado,
    só os planos dele. Plano inativo não é tocado: nem gera, nem tem a data
    adiantada.

    A data de referência é declarada, e não lida por dentro: o mesmo serviço
    gera o dia de hoje e recupera uma ausência longa, e quem chama é que diz a
    que dia a rodada se refere.

    Quem assina é declarado do mesmo modo. Numa execução manual, é a pessoa que
    pediu; numa execução automática, ninguém declarou nada — e aí quem assina é
    o ator técnico configurado. Sem ator configurado, a rodada não acontece:
    autoria inventada não serve à rastreabilidade.

    Cada plano roda isolado: uma falha em um é registrada no resumo e não
    interrompe os demais. Nenhuma falha é engolida em silêncio — ela aparece no
    resumo e no log.
    """
    if usuario is None:
        usuario = resolver_ator_automatico()
    if data_referencia is None:
        data_referencia = timezone.localdate()

    limite = max(
        1, int(settings.MANUTENCOES_MAX_OCORRENCIAS_RECUPERADAS_POR_PLANO)
    )
    resumo = ResumoDasPreventivas(data_referencia=data_referencia)

    escopo = PlanoPreventivo.objects.ativos()
    if condominio is not None:
        escopo = escopo.do_condominio(condominio)

    resumo.planos_verificados = escopo.count()
    pendentes = list(
        escopo.filter(proxima_data_prevista__lte=data_referencia).order_by(
            "condominio_id", "proxima_data_prevista", "titulo"
        )
    )
    # O que está no futuro não precisa de nada nesta rodada.
    resumo.planos_sem_necessidade = resumo.planos_verificados - len(pendentes)

    for plano in pendentes:
        try:
            geradas, reconciliadas, parou_no_limite = _recuperar_competencias(
                plano=plano,
                usuario=usuario,
                data_referencia=data_referencia,
                limite=limite,
            )
        except Exception as erro:  # noqa: BLE001 — um plano não derruba a rodada
            logger.exception(
                "Preventivas: falha ao gerar o plano «%s» (%s).", plano.titulo, plano.pk
            )
            resumo.falhas.append((str(plano.pk), _motivo_da_falha(erro)))
            continue

        if geradas or reconciliadas:
            resumo.planos_processados += 1
            resumo.ocorrencias_geradas += geradas
            resumo.competencias_reconciliadas += reconciliadas
            logger.info(
                "Preventivas: plano «%s» (%s) gerou %s ocorrência(s) e "
                "reconheceu %s competência(s) já materializada(s).",
                plano.titulo,
                plano.pk,
                geradas,
                reconciliadas,
            )
        else:
            resumo.planos_sem_necessidade += 1

        if parou_no_limite:
            resumo.planos_limitados.append(str(plano.pk))
            logger.warning(
                "Preventivas: plano «%s» (%s) parou no limite de %s competência(s) "
                "por execução; ainda há competências vencidas.",
                plano.titulo,
                plano.pk,
                limite,
            )

    return resumo


def _recuperar_competencias(
    *, plano, usuario, data_referencia: date, limite: int
) -> tuple[int, int, bool]:
    """Traz o plano até a data de referência, uma competência por vez.

    Cada passo é entregue ao serviço oficial, que faz uma de duas coisas: cria a
    ocorrência daquela competência e adianta o plano, ou devolve a ocorrência
    que já existia — e aí a data não anda. O primeiro caso é uma ocorrência
    gerada; o segundo é uma competência que já estava materializada, e o
    ponteiro precisa reconhecê-la, ou o plano fica preso nela para sempre e
    todas as competências seguintes se perdem.

    Nada aqui recalcula data nem copia contexto: o avanço é sempre o do serviço
    oficial, e o reconhecimento de uma competência materializada é uma operação
    de domínio própria, que só avança com a ocorrência confirmada no banco.

    Devolve quantas ocorrências nasceram, quantas competências já existentes
    foram reconhecidas e se a parada foi pelo limite. O limite conta as
    competências tratadas — geradas ou reconhecidas —, porque o que ele existe
    para conter é o trabalho de um plano numa execução, seja ele criar ou
    reconciliar.
    """
    geradas = 0
    reconciliadas = 0

    while True:
        competencia = plano.proxima_data_prevista
        if competencia > data_referencia:
            return geradas, reconciliadas, False
        if geradas + reconciliadas >= limite:
            return geradas, reconciliadas, True

        gerar_ocorrencia_preventiva(plano=plano, usuario=usuario)
        plano.refresh_from_db(fields=["proxima_data_prevista"])

        if plano.proxima_data_prevista != competencia:
            geradas += 1
            continue

        # A ocorrência desta competência já existia e o plano não andou: é uma
        # competência materializada. Reconhecê-la é o que destrava o plano.
        seguinte = reconciliar_competencia_preventiva(plano=plano)
        if seguinte is None:
            # Sem ocorrência correspondente, o avanço é proibido. Parar aqui é a
            # resposta honesta — insistir repetiria a mesma data para sempre.
            return geradas, reconciliadas, False
        plano.refresh_from_db(fields=["proxima_data_prevista"])
        reconciliadas += 1


@transaction.atomic
def reconciliar_competencia_preventiva(*, plano: PlanoPreventivo) -> date | None:
    """Reconhece uma competência que já virou ocorrência e adianta o plano.

    Existe um caso em que a geração não acontece e o plano também não pode
    andar: a ocorrência daquela competência já existe — criada antes, ou por
    outra execução. O serviço oficial devolve a que existe e não mexe na data,
    e sem esta passagem o plano ficaria preso naquela competência para sempre,
    sem nunca alcançar as seguintes.

    O avanço continua sendo um só e continua proibido quando não há ocorrência:
    a competência é conferida no banco, sob trava, e só então a próxima data é
    calculada pela mesma regra do serviço oficial. Não é uma segunda forma de
    gerar nem um segundo cálculo — é o reconhecimento de que aquele passo já
    aconteceu. Devolve a data nova, ou ``None`` quando não havia nada a
    reconhecer.

    Nenhuma manutenção é criada e nenhuma é alterada. Nenhum histórico é escrito
    por isso: nada aconteceu a uma ocorrência e ninguém agiu — o que muda é o
    ponteiro do plano, e disso fica o registro operacional no log.
    """
    plano = PlanoPreventivo.objects.select_for_update().get(pk=plano.pk)
    competencia = plano.proxima_data_prevista

    materializada = Manutencao.objects.filter(
        plano_preventivo=plano, competencia_preventiva=competencia
    ).exists()
    if not materializada:
        return None

    seguinte = proxima_data(
        unidade=plano.frequencia_unidade,
        intervalo=plano.frequencia_intervalo,
        a_partir_de=competencia,
    )
    if seguinte is None or seguinte <= competencia:
        raise ErroDeManutencao(
            "A frequência deste plano não avança a partir de "
            f"{_data_por_extenso(competencia)}; a geração parou para não repetir "
            "a mesma competência."
        )

    plano.proxima_data_prevista = seguinte
    plano.save(update_fields=["proxima_data_prevista", "atualizado_em"])
    logger.info(
        "Preventivas: a competência de %s do plano «%s» (%s) já estava "
        "materializada; o plano avançou para %s.",
        _data_por_extenso(competencia),
        plano.titulo,
        plano.pk,
        _data_por_extenso(seguinte),
    )
    return seguinte


def _motivo_da_falha(erro: Exception) -> str:
    """Uma frase curta sobre a falha, para o resumo — o detalhe fica no log."""
    if isinstance(erro, ErroDeManutencao):
        return str(erro)
    return type(erro).__name__

