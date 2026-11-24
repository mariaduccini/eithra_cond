"""Operações do domínio de rotinas recorrentes.

Aqui vive o que dá vida a uma rotina: registrar a regra, preparar a próxima
ocorrência e percorrer os desfechos de uma execução — começar, concluir, ignorar
ou cancelar. Cada operação acontece inteira, em uma transação: ou a ocorrência e
a rotina mudam juntas, ou nada muda.

Nada dispara sozinho. Concluir uma ocorrência abre a próxima porque a regra
continua valendo — não porque o Eithra decidiu fazer algo. Criar uma manutenção
ou abrir uma demanda a partir de uma ocorrência só acontece quando a gestão
pede; a rotina sugere, a pessoa confirma. O que o Eithra alcança é lembrar.

Duas decisões merecem ser ditas em voz alta. ``proxima_execucao`` tem um sentido
só — a ocorrência operacional que ainda espera alguém — e é escrita apenas por
aqui: ``_recalcular_proxima_execucao`` é a única regra do campo, e nenhuma
operação grava esse valor por conta própria. E a ocorrência preserva o
responsável que tinha no momento em que foi preparada: mudar o responsável da
rotina depois não reescreve o passado.

A terceira decisão é o checklist. Quando a ocorrência nasce, as tarefas ativas do
modelo são copiadas para ela — um retrato do que se esperava naquele dia. A
partir daí, o modelo pode mudar à vontade: a ocorrência continua contando o que
foi combinado então. Concluir exige que as tarefas obrigatórias estejam
resolvidas; as opcionais podem ficar como o que não foi feito.
"""
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.constants import Prioridade
from apps.core.recorrencia import calcular_proxima_data
from apps.demandas.constants import OrigemDemanda
from apps.demandas.services import criar_demanda
from apps.manutencoes.constants import TipoManutencao
from apps.manutencoes.services import criar_manutencao

from .constants import (
    SITUACOES_PENDENTES,
    STATUS_QUE_TRATAM_O_ITEM,
    Periodicidade,
    ResultadoExecucao,
    StatusExecucao,
    StatusItemChecklist,
    TipoRotina,
)
from .models import (
    ExecucaoRotina,
    ItemExecucaoChecklist,
    ItemModeloChecklist,
    ModeloChecklist,
    RotinaRecorrente,
)


class ErroDeRotina(Exception):
    """Uma decisão do domínio sobre a rotina que a gestão precisa saber."""


def _texto(valor: str | None) -> str:
    """Colapsa espaços repetidos e devolve string vazia quando não há conteúdo."""
    return " ".join((valor or "").split())


def _texto_longo(valor: str | None) -> str:
    """Preserva os parágrafos de um texto livre, sem espaços nas pontas."""
    return (valor or "").strip()


def _data_por_extenso(valor) -> str:
    """A data como a gestão a lê, ou a ausência dela."""
    return valor.strftime("%d/%m/%Y") if valor else "sem data"


def _primeiro_recado(erro: ValidationError) -> str:
    """A primeira frase humana de um erro de validação do modelo."""
    if hasattr(erro, "message_dict"):
        for mensagens in erro.message_dict.values():
            if mensagens:
                return mensagens[0]
    if erro.messages:
        return erro.messages[0]
    return "Não foi possível guardar a rotina."


def _completar_local_pelo_equipamento(rotina) -> None:
    """Quando o equipamento tem local e a rotina não escolheu nenhum, assume o dele.

    É conveniência para não pedir duas vezes a mesma informação — e só isso. Uma
    escolha explícita divergente nunca é sobrescrita: a coerência é cobrada
    adiante, e a rotina é recusada em vez de corrigida em silêncio.
    """
    if rotina.local_id or not rotina.equipamento_id:
        return

    local_do_equipamento = rotina.equipamento.local
    if local_do_equipamento is not None:
        rotina.local = local_do_equipamento


def _validar(objeto) -> None:
    """Confere a coerência do registro e traduz a recusa para a gestão."""
    try:
        objeto.full_clean()
    except ValidationError as erro:
        raise ErroDeRotina(_primeiro_recado(erro)) from erro


def _momento_do_fato(data) -> "datetime":
    """Quando a verificação aconteceu: o dia informado, com a hora do registro.

    O campo guarda dia e hora. Quando a gestão diz o dia em que a rotina foi
    executada, é esse dia que fica guardado — a hora é a do momento em que ela
    registrou. Sem dia informado, vale o próprio momento do registro.

    Isto é só o dia do fato: a agenda da rotina continua vindo de
    ``data_prevista``, e é por isso que um lançamento retroativo não move a
    próxima ocorrência.
    """
    if data is None:
        return timezone.now()
    return timezone.make_aware(datetime.combine(data, timezone.localtime().time()))


def _calcular_proxima(rotina, *, a_partir_de=None):
    """A próxima ocorrência da rotina depois de ``a_partir_de``, ou a primeira."""
    return calcular_proxima_data(
        rotina.periodicidade_tipo,
        data_inicio=rotina.data_inicio,
        intervalo=rotina.intervalo,
        dia_semana=rotina.dia_semana,
        dia_mes=rotina.dia_mes,
        mes_ano=rotina.mes_ano,
        a_partir_de=a_partir_de,
        data_fim=rotina.data_fim,
    )


def _ultima_data(rotina):
    """A data da ocorrência mais distante já criada para a rotina, se houver."""
    return (
        rotina.execucoes.order_by("-data_prevista")
        .values_list("data_prevista", flat=True)
        .first()
    )


def _data_pendente(rotina):
    """A data da ocorrência em aberto mais antiga, se houver alguma."""
    return (
        rotina.execucoes.filter(status__in=SITUACOES_PENDENTES)
        .order_by("data_prevista")
        .values_list("data_prevista", flat=True)
        .first()
    )


def _definir_proxima(rotina, valor) -> None:
    """Guarda o valor decidido pela regra, e só quando ele realmente muda."""
    if rotina.proxima_execucao != valor:
        rotina.proxima_execucao = valor
        rotina.save(update_fields=["proxima_execucao", "atualizado_em"])


def _recalcular_proxima_execucao(rotina) -> None:
    """A única regra do campo: a próxima ocorrência operacional a ser tratada.

    Uma ocorrência em aberto é, por definição, o que ainda espera alguém. Então
    a próxima é a mais antiga delas — mesmo que já exista outra preparada mais
    adiante, como acontece quando se adianta uma geração. Sem nada em aberto, a
    próxima é a data que a regra de recorrência aponta depois da última já
    criada. E quando não há o que esperar — rotina inativa, fim de vigência
    alcançado, nenhuma data futura válida —, o campo fica vazio, em vez de
    anunciar uma data que não vai acontecer.

    Toda escrita passa por aqui: o valor não depende de qual operação pediu a
    conta, e sim do estado da rotina naquele instante.
    """
    aberta = _data_pendente(rotina)
    if aberta is not None:
        _definir_proxima(rotina, aberta)
        return

    if not rotina.ativo:
        _definir_proxima(rotina, None)
        return

    _definir_proxima(
        rotina, _calcular_proxima(rotina, a_partir_de=_ultima_data(rotina))
    )


@transaction.atomic
def criar_rotina(
    *,
    condominio,
    criado_por,
    titulo: str,
    data_inicio,
    tipo: str = TipoRotina.OPERACIONAL,
    descricao: str = "",
    periodicidade_tipo: str = Periodicidade.MENSAL,
    intervalo: int = 1,
    dia_semana=None,
    dia_mes=None,
    mes_ano=None,
    data_fim=None,
    responsavel=None,
    local=None,
    equipamento=None,
    fornecedor=None,
    checklist_modelo=None,
    prioridade: str = Prioridade.NORMAL,
    gera_manutencao: bool = False,
) -> RotinaRecorrente:
    """Registra a rotina e já prepara a primeira ocorrência.

    Uma rotina nova não fica só na intenção: a primeira execução nasce junto,
    para que a próxima data apareça na tela e alguém possa assumi-la.
    """
    rotina = RotinaRecorrente(
        condominio=condominio,
        titulo=_texto(titulo),
        descricao=_texto_longo(descricao),
        tipo=tipo,
        periodicidade_tipo=periodicidade_tipo,
        intervalo=intervalo,
        dia_semana=dia_semana,
        dia_mes=dia_mes,
        mes_ano=mes_ano,
        data_inicio=data_inicio,
        data_fim=data_fim,
        responsavel=responsavel,
        local=local,
        equipamento=equipamento,
        fornecedor=fornecedor,
        checklist_modelo=checklist_modelo,
        prioridade=prioridade,
        gera_manutencao=gera_manutencao,
        criado_por=criado_por,
    )
    _completar_local_pelo_equipamento(rotina)
    _validar(rotina)
    rotina.save()

    gerar_proxima_execucao(rotina)
    return rotina


@transaction.atomic
def editar_rotina(
    *,
    rotina: RotinaRecorrente,
    titulo: str,
    data_inicio,
    tipo: str,
    descricao: str = "",
    periodicidade_tipo: str,
    intervalo: int = 1,
    dia_semana=None,
    dia_mes=None,
    mes_ano=None,
    data_fim=None,
    responsavel=None,
    local=None,
    equipamento=None,
    fornecedor=None,
    checklist_modelo=None,
    prioridade: str,
    gera_manutencao: bool = False,
) -> RotinaRecorrente:
    """Atualiza a regra da rotina e reassenta a próxima ocorrência.

    Mudar a regra vale para o que vem depois: uma ocorrência já em aberto não é
    movida nem descartada por causa de uma edição. O que muda é a próxima ainda
    por preparar. Trocar o checklist também só vale para o que vem depois — as
    ocorrências que já nasceram guardam o retrato do dia em que foram geradas.
    """
    rotina.titulo = _texto(titulo)
    rotina.descricao = _texto_longo(descricao)
    rotina.tipo = tipo
    rotina.periodicidade_tipo = periodicidade_tipo
    rotina.intervalo = intervalo
    rotina.dia_semana = dia_semana
    rotina.dia_mes = dia_mes
    rotina.mes_ano = mes_ano
    rotina.data_inicio = data_inicio
    rotina.data_fim = data_fim
    rotina.responsavel = responsavel
    rotina.local = local
    rotina.equipamento = equipamento
    rotina.fornecedor = fornecedor
    rotina.checklist_modelo = checklist_modelo
    rotina.prioridade = prioridade
    rotina.gera_manutencao = gera_manutencao

    _completar_local_pelo_equipamento(rotina)
    _validar(rotina)
    rotina.save()

    _reassentar_proxima(rotina)
    return rotina


def _proxima_ordem(checklist: ModeloChecklist) -> int:
    """A ordem seguinte à última tarefa do modelo, para a nova entrar no fim."""
    ultima = (
        checklist.itens.order_by("-ordem").values_list("ordem", flat=True).first()
    )
    return (ultima or 0) + 1


@transaction.atomic
def criar_checklist(
    *, condominio, criado_por, nome: str, descricao: str = ""
) -> ModeloChecklist:
    """Registra um checklist modelo do condomínio.

    O modelo descreve o que se costuma fazer; ele ainda não vale para ocorrência
    nenhuma até que uma rotina o cite.
    """
    checklist = ModeloChecklist(
        condominio=condominio,
        criado_por=criado_por,
        nome=_texto(nome),
        descricao=_texto_longo(descricao),
    )
    _validar(checklist)
    checklist.save()
    return checklist


@transaction.atomic
def editar_checklist(
    *, checklist: ModeloChecklist, nome: str, descricao: str = ""
) -> ModeloChecklist:
    """Atualiza o nome e a descrição do modelo.

    Mudar o modelo vale só para o que vem: as ocorrências já geradas guardam o
    retrato do dia em que nasceram.
    """
    checklist.nome = _texto(nome)
    checklist.descricao = _texto_longo(descricao)
    _validar(checklist)
    checklist.save()
    return checklist


@transaction.atomic
def definir_checklist_ativo(
    *, checklist: ModeloChecklist, ativo: bool
) -> ModeloChecklist:
    """Tira o modelo das próximas rotinas, ou o devolve, sem apagar nada.

    As ocorrências que já nasceram continuam contando o que foi combinado, e as
    rotinas que já citam o modelo seguem funcionando.
    """
    checklist.ativo = bool(ativo)
    checklist.save(update_fields=["ativo", "atualizado_em"])
    return checklist


@transaction.atomic
def criar_item_do_checklist(
    *,
    checklist: ModeloChecklist,
    descricao: str,
    ordem: int | None = None,
    obrigatorio: bool = True,
) -> ItemModeloChecklist:
    """Acrescenta uma tarefa ao modelo, no fim da lista quando a ordem não vem."""
    item = ItemModeloChecklist(
        checklist=checklist,
        descricao=_texto(descricao),
        ordem=_proxima_ordem(checklist) if ordem is None else ordem,
        obrigatorio=bool(obrigatorio),
    )
    _validar(item)
    item.save()
    return item


@transaction.atomic
def editar_item_do_checklist(
    *,
    item: ItemModeloChecklist,
    descricao: str,
    ordem: int = 0,
    obrigatorio: bool = True,
) -> ItemModeloChecklist:
    """Atualiza a tarefa do modelo: o que fazer, em que posição e se é obrigatória."""
    item.descricao = _texto(descricao)
    item.ordem = ordem
    item.obrigatorio = bool(obrigatorio)
    _validar(item)
    item.save()
    return item


@transaction.atomic
def definir_item_do_checklist_ativo(
    *, item: ItemModeloChecklist, ativo: bool
) -> ItemModeloChecklist:
    """Tira a tarefa das próximas ocorrências, ou a devolve, sem apagar nada.

    Uma tarefa desativada deixa de ser copiada para as ocorrências novas; o que
    já foi feito continua guardado no retrato de cada uma.
    """
    item.ativo = bool(ativo)
    item.save(update_fields=["ativo", "atualizado_em"])
    return item


@transaction.atomic
def gerar_proxima_execucao(rotina: RotinaRecorrente) -> ExecucaoRotina | None:
    """Prepara a próxima ocorrência da rotina e devolve a execução.

    Só uma rotina ativa gera ocorrência, a data respeita o início e o fim da
    vigência, e a mesma data nunca gera duas execuções — a rotina é travada
    enquanto isso, para que duas requisições simultâneas não criem a mesma
    ocorrência. Devolve nada quando a rotina está inativa ou quando a próxima
    data cairia depois do fim.

    Quando a ocorrência nasce, o checklist do modelo é copiado para ela. A cópia
    acontece uma única vez, na criação: repetir a geração não recria nem
    sobrescreve tarefa nenhuma, e o modelo pode mudar depois sem tocar no que já
    foi registrado.
    """
    origem = rotina
    rotina = RotinaRecorrente.objects.select_for_update().get(pk=rotina.pk)

    execucao = None
    if rotina.ativo:
        proxima = _calcular_proxima(rotina, a_partir_de=_ultima_data(rotina))
        if proxima is not None:
            execucao, criada = ExecucaoRotina.objects.get_or_create(
                rotina=rotina,
                data_prevista=proxima,
                defaults={
                    "condominio": rotina.condominio,
                    "responsavel": rotina.responsavel,
                    "status": StatusExecucao.PENDENTE,
                },
            )
            if criada:
                _copiar_checklist(rotina, execucao)

    # A ocorrência preparada não é, por si, a próxima a tratar: quem decide isso
    # é a regra, olhando o que ficou em aberto.
    _recalcular_proxima_execucao(rotina)
    origem.proxima_execucao = rotina.proxima_execucao
    return execucao


def _copiar_checklist(rotina: RotinaRecorrente, execucao: ExecucaoRotina) -> None:
    """Retrata na ocorrência as tarefas ativas do checklist do modelo.

    Descrição, ordem e obrigatoriedade são copiadas como estavam naquele dia —
    é o que preserva o histórico. Sem checklist, nada é copiado: a ocorrência
    simplesmente não tem lista.
    """
    if rotina.checklist_modelo_id is None:
        return

    itens = [
        ItemExecucaoChecklist(
            execucao=execucao,
            item_modelo=item,
            descricao=item.descricao,
            ordem=item.ordem,
            obrigatorio=item.obrigatorio,
            status=StatusItemChecklist.PENDENTE,
        )
        for item in rotina.checklist_modelo.itens.filter(ativo=True)
    ]
    if itens:
        ItemExecucaoChecklist.objects.bulk_create(itens)


def _reassentar_proxima(rotina: RotinaRecorrente) -> None:
    """Recalcula a próxima e, se nada estiver em aberto, prepara a seguinte.

    Uma ocorrência em aberto continua sendo a próxima — a edição não a move. Sem
    nada em aberto e com a rotina ativa, a regra é retomada depois da última já
    criada, e a ocorrência é preparada na hora.
    """
    if rotina.ativo and _data_pendente(rotina) is None:
        gerar_proxima_execucao(rotina)
        return

    _recalcular_proxima_execucao(rotina)


@transaction.atomic
def iniciar_execucao(*, execucao: ExecucaoRotina, usuario) -> ExecucaoRotina:
    """Marca a ocorrência como começada, guardando quando isso aconteceu.

    Começar só acontece uma vez: o momento do início é preservado, e voltar a
    ele não reescreve o começo.
    """
    if execucao.status != StatusExecucao.PENDENTE:
        raise ErroDeRotina("Esta ocorrência já foi iniciada ou encerrada.")

    campos = ["status", "atualizado_em"]
    if execucao.iniciado_em is None:
        execucao.iniciado_em = timezone.now()
        campos.append("iniciado_em")

    execucao.status = StatusExecucao.EM_ANDAMENTO
    _validar(execucao)
    execucao.save(update_fields=campos)
    return execucao


@transaction.atomic
def registrar_atualizacao(
    *, execucao: ExecucaoRotina, usuario, texto: str
) -> ExecucaoRotina:
    """Anota uma atualização curta na ocorrência, sem mexer na situação.

    A ocorrência não tem histórico próprio: o que se registra sobre ela vive em
    ``observacao_execucao``, o mesmo campo do que se encontrou ao encerrar. Cada
    atualização é acrescentada com o momento e quem a escreveu, para que o
    relato não perca a mão de quem conduz — e o texto acumulado é o que a
    ocorrência leva para a conclusão. Uma ocorrência encerrada não muda mais.
    """
    if execucao.esta_encerrada:
        raise ErroDeRotina("Esta ocorrência já foi encerrada.")

    texto = _texto(texto)
    if not texto:
        raise ErroDeRotina("Escreva a atualização antes de registrar.")

    nome = (
        getattr(usuario, "full_name", "")
        or getattr(usuario, "short_name", "")
        or getattr(usuario, "email", "")
        or "Sem responsável"
    )
    linha = f"[{timezone.localtime():%d/%m %H:%M} — {nome}] {texto}"
    anterior = (execucao.observacao_execucao or "").strip()

    execucao.observacao_execucao = f"{anterior}\n{linha}".strip() if anterior else linha
    _validar(execucao)
    execucao.save(update_fields=["observacao_execucao", "atualizado_em"])
    return execucao


@transaction.atomic
def concluir_execucao(
    *,
    execucao: ExecucaoRotina,
    usuario,
    resultado: str,
    observacao: str = "",
    data_execucao=None,
) -> ExecucaoRotina:
    """Conclui a ocorrência, registrando o que se encontrou e quando.

    Concluir exige dizer o que se encontrou — a atividade não termina em
    silêncio. Depois disso, a rotina segue em frente e a próxima ocorrência é
    preparada, porque a regra continua valendo.

    Se a ocorrência carrega checklist, nenhuma tarefa obrigatória pode ficar
    pendente: cada uma precisa estar concluída ou marcada como não aplicável.
    Tarefa opcional pendente não segura a conclusão — ela fica registrada como o
    que não foi feito.

    A data é a do dia da verificação, não a do registro: uma rotina feita em
    setembro pode ser lançada agora. Ela é gravada em ``concluido_em``, que é o
    quando da verificação — a agenda da rotina vem de ``data_prevista``, então
    um lançamento retroativo não move a próxima ocorrência.
    """
    if execucao.esta_encerrada:
        raise ErroDeRotina("Esta ocorrência já foi encerrada.")

    if resultado not in ResultadoExecucao.values:
        raise ErroDeRotina("Diga o que se encontrou antes de concluir.")

    if data_execucao is not None and data_execucao > timezone.localdate():
        raise ErroDeRotina("A data da execução não pode estar no futuro.")

    if execucao.tem_item_obrigatorio_pendente:
        raise ErroDeRotina(
            "Ainda há tarefas obrigatórias por concluir. Marque cada uma como "
            "concluída ou não aplicável antes de encerrar a ocorrência."
        )

    execucao.status = StatusExecucao.CONCLUIDA
    execucao.resultado = resultado
    execucao.observacao_execucao = _texto_longo(observacao)
    execucao.concluido_em = _momento_do_fato(data_execucao)
    _validar(execucao)
    execucao.save(
        update_fields=[
            "status",
            "resultado",
            "observacao_execucao",
            "concluido_em",
            "atualizado_em",
        ]
    )

    gerar_proxima_execucao(execucao.rotina)
    return execucao


@transaction.atomic
def tratar_item_checklist(
    *,
    item: ItemExecucaoChecklist,
    usuario,
    status: str | None = None,
    observacao: str = "",
) -> ItemExecucaoChecklist:
    """Diz o que aconteceu com uma tarefa: concluída, não aplicável — ou só uma nota.

    Tratar tira a tarefa de pendente e registra quem tratou e quando — o momento
    é o do tratamento, e não o da digitação. Uma ocorrência encerrada não muda
    mais, e as tarefas dela também não. Corrigir uma tarefa já tratada é aceito
    enquanto a ocorrência está aberta; reabri-la, não: o retrato anda para a
    frente, nunca de volta para "pendente".

    Quando só se anota, sem dizer como a tarefa ficou, ela continua pendente e o
    que se guarda é o recado — o que não a dá por tratada nem libera a conclusão.
    """
    if item is None or not getattr(item, "pk", None):
        raise ErroDeRotina("Escolha a tarefa do checklist.")

    if status not in (None, "") and status not in STATUS_QUE_TRATAM_O_ITEM:
        raise ErroDeRotina("Diga se a tarefa foi concluída ou se não se aplicava.")

    recado = _texto_longo(observacao)
    if not status and not recado:
        raise ErroDeRotina("Diga o que aconteceu com a tarefa.")

    execucao = item.execucao
    if execucao.esta_encerrada:
        raise ErroDeRotina("Esta ocorrência já foi encerrada.")

    campos = ["atualizado_em"]
    if status:
        item.status = status
        item.concluido_por = usuario
        item.concluido_em = timezone.now()
        campos += ["status", "concluido_por", "concluido_em"]
    if recado:
        item.observacao = recado
        campos.append("observacao")

    _validar(item)
    item.save(update_fields=campos)
    return item


@transaction.atomic
def ignorar_execucao(
    *, execucao: ExecucaoRotina, usuario, justificativa: str
) -> ExecucaoRotina:
    """Registra que a atividade não se aplicava naquele período.

    Ignorar não cancela a rotina: ela avança para a próxima ocorrência. A
    justificativa é obrigatória — dizer "não se aplicava" sem dizer por quê não
    ajuda quem lê o histórico depois.
    """
    if execucao.esta_encerrada:
        raise ErroDeRotina("Esta ocorrência já foi encerrada.")

    motivo = _texto_longo(justificativa)
    if not motivo:
        raise ErroDeRotina("Diga por que a atividade não se aplicava.")

    execucao.status = StatusExecucao.IGNORADA
    execucao.observacao_execucao = motivo
    _validar(execucao)
    execucao.save(
        update_fields=["status", "observacao_execucao", "atualizado_em"]
    )

    gerar_proxima_execucao(execucao.rotina)
    return execucao


@transaction.atomic
def cancelar_execucao(
    *, execucao: ExecucaoRotina, usuario, justificativa: str
) -> ExecucaoRotina:
    """Encerra uma ocorrência que não vai acontecer, sem cancelar a rotina.

    Cancelar preserva o que houve, exige o motivo e não apaga nada. A rotina
    segue adiante: a próxima ocorrência é preparada do mesmo jeito.
    """
    if execucao.esta_encerrada:
        raise ErroDeRotina("Esta ocorrência já foi encerrada.")

    motivo = _texto_longo(justificativa)
    if not motivo:
        raise ErroDeRotina("Diga por que a ocorrência está sendo cancelada.")

    execucao.status = StatusExecucao.CANCELADA
    execucao.observacao_execucao = motivo
    _validar(execucao)
    execucao.save(
        update_fields=["status", "observacao_execucao", "atualizado_em"]
    )

    gerar_proxima_execucao(execucao.rotina)
    return execucao


@transaction.atomic
def definir_ativo(*, rotina: RotinaRecorrente, ativo: bool) -> RotinaRecorrente:
    """Para de gerar novas ocorrências, ou volta a gerar, sem apagar nada.

    Desativar é retirada administrativa: as execuções que existem continuam como
    estão, e nenhuma é cancelada por causa disso. O que está em aberto continua
    sendo a próxima a tratar; sem nada em aberto, a rotina inativa deixa de
    anunciar uma data. Reativar devolve a rotina ao ritmo, preparando a próxima
    ocorrência se não houver uma em aberto.
    """
    ativo = bool(ativo)
    if rotina.ativo == ativo:
        return rotina

    rotina.ativo = ativo
    rotina.save(update_fields=["ativo", "atualizado_em"])

    if ativo:
        _reassentar_proxima(rotina)
    else:
        _recalcular_proxima_execucao(rotina)
    return rotina


@transaction.atomic
def abrir_demanda_da_execucao(
    *,
    execucao: ExecucaoRotina,
    usuario,
    titulo: str = "",
    descricao: str = "",
    prioridade: str = Prioridade.NORMAL,
    prazo=None,
):
    """Abre uma demanda a partir do que a ocorrência encontrou.

    Nada é automático: quem encontrou o problema descreve, e a demanda nasce com
    procedência registrada. Uma ocorrência já ligada a uma demanda não gera
    outra — o vínculo é único. Quando a rotina aponta um local, ele já vem
    preenchido: é o lugar onde a ocorrência aconteceu.
    """
    if execucao.demanda_gerada_id:
        raise ErroDeRotina("Esta ocorrência já tem uma demanda aberta.")

    demanda = criar_demanda(
        condominio=execucao.condominio,
        criado_por=usuario,
        titulo=_texto(titulo) or f"Rotina: {execucao.rotina.titulo}",
        descricao=_texto_longo(descricao),
        origem=OrigemDemanda.SISTEMA,
        prioridade=prioridade,
        prazo=prazo,
        local=execucao.rotina.local,
    )

    execucao.demanda_gerada = demanda
    execucao.save(update_fields=["demanda_gerada", "atualizado_em"])
    return execucao


@transaction.atomic
def criar_manutencao_da_execucao(
    *,
    execucao: ExecucaoRotina,
    usuario,
    titulo: str = "",
    descricao_problema: str = "",
    data_prevista=None,
):
    """Cria uma manutenção preventiva a partir da ocorrência.

    O Eithra prepara; a pessoa confirma. A manutenção nasce com o contexto da
    rotina — local, equipamento, fornecedor e responsável — e o vínculo fica
    registrado na ocorrência. Uma ocorrência já ligada a uma manutenção não gera
    outra.
    """
    if execucao.manutencao_gerada_id:
        raise ErroDeRotina("Esta ocorrência já tem uma manutenção criada.")

    rotina = execucao.rotina
    manutencao = criar_manutencao(
        condominio=execucao.condominio,
        criado_por=usuario,
        titulo=_texto(titulo) or f"Preventiva: {rotina.titulo}",
        tipo=TipoManutencao.PREVENTIVA,
        descricao_problema=_texto_longo(descricao_problema),
        prioridade=rotina.prioridade,
        local=rotina.local,
        equipamento=rotina.equipamento,
        fornecedor=rotina.fornecedor,
        responsavel=execucao.responsavel,
        data_prevista=data_prevista or execucao.data_prevista,
    )

    execucao.manutencao_gerada = manutencao
    execucao.save(update_fields=["manutencao_gerada", "atualizado_em"])
    return execucao
