"""Quem pode ver e mexer nas rotinas recorrentes.

A resposta vem sempre do vínculo ativo da pessoa com o condomínio, e nunca do
menu, do formulário ou de um identificador trazido pelo navegador. O menu pode
esconder o que não serve — esconder não é autorizar.

Administradora e síndica respondem pelo condomínio: criam a rotina, editam a
regra, ativam e inativam, atribuem responsável, geram a próxima ocorrência e
cancelam uma execução. A supervisão opera o dia a dia — cria, edita os dados
operacionais, gera ocorrência, inicia, conclui e ignora —, mas não desativa uma
rotina estrutural nem cancela execução: as duas coisas continuam sendo de quem
responde pelo condomínio.

A operação não administra rotina nenhuma. O que ela enxerga são as ocorrências
atribuídas a ela, e só elas: iniciar, concluir e registrar o que encontrou. Sem
vínculo com o condomínio, ou fora do papel que alcança o módulo, a resposta é
negar — nunca abrir por conta própria.

A pergunta "esta pessoa acessa este condomínio?" continua em
``apps.condominios.acesso``, para não haver duas respostas para a mesma pergunta.
"""
from django.contrib.auth import get_user_model
from django.db.models import Q, QuerySet

from apps.condominios.acesso import condominios_do_usuario
from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio, Condominio

from .constants import StatusExecucao
from .models import ExecucaoRotina, ItemModeloChecklist, ModeloChecklist, RotinaRecorrente

# Administradora e síndica respondem pelo condomínio.
PAPEIS_QUE_GERENCIAM = frozenset({Papel.ADMINISTRADORA, Papel.SINDICA})

# A supervisão cria e opera a rotina, sem desativá-la nem cancelar ocorrência.
PAPEIS_QUE_CRIAM = PAPEIS_QUE_GERENCIAM | {Papel.SUPERVISORA}

# A operação só atua sobre as ocorrências que são dela.
PAPEIS_QUE_OPERAM = frozenset({Papel.OPERACIONAL})

# Quem enxerga o módulo. Portaria e morador não entram nesta fase.
PAPEIS_QUE_VEEM = PAPEIS_QUE_CRIAM | PAPEIS_QUE_OPERAM

# Movimentos que a operação alcança em uma ocorrência que é dela.
SITUACOES_DE_QUEM_OPERA = frozenset(
    {StatusExecucao.EM_ANDAMENTO, StatusExecucao.CONCLUIDA, StatusExecucao.IGNORADA}
)


def _com_papel(usuario, papeis: frozenset[str]) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa tem vínculo ativo com algum destes papéis."""
    return (
        condominios_do_usuario(usuario)
        .filter(
            acessos__usuario_id=getattr(usuario, "pk", None),
            acessos__ativo=True,
            acessos__papel__in=papeis,
        )
        .distinct()
    )


def _papeis_do_usuario(usuario, condominio) -> frozenset[str]:
    """Papéis ativos da pessoa neste condomínio. Vazio quando não há vínculo."""
    if condominio is None or not getattr(condominio, "pk", None):
        return frozenset()
    if not getattr(usuario, "pk", None):
        return frozenset()

    return frozenset(
        AcessoUsuarioCondominio.objects.filter(
            usuario_id=usuario.pk,
            condominio_id=condominio.pk,
            ativo=True,
        ).values_list("papel", flat=True)
    )


def condominios_para_ver(usuario) -> QuerySet[Condominio]:
    """Condomínios cujas rotinas a pessoa pode consultar agora."""
    return _com_papel(usuario, PAPEIS_QUE_VEEM)


def condominios_para_criar(usuario) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa pode registrar rotina agora."""
    return _com_papel(usuario, PAPEIS_QUE_CRIAM)


def condominios_para_gerenciar(usuario) -> QuerySet[Condominio]:
    """Condomínios cujas rotinas a pessoa pode administrar agora."""
    return _com_papel(usuario, PAPEIS_QUE_GERENCIAM)


def condominios_para_checklist(usuario) -> QuerySet[Condominio]:
    """Condomínios cujos checklists a pessoa pode montar agora.

    Montar a lista de tarefas é a mesma responsabilidade de criar a rotina —
    administração, síndica e supervisão. A operação não alcança o modelo
    estrutural: o que ela faz é executar as tarefas da ocorrência que é dela.
    """
    return _com_papel(usuario, PAPEIS_QUE_CRIAM)


def rotinas_para_ver(usuario) -> QuerySet[RotinaRecorrente]:
    """Rotinas que esta pessoa pode consultar agora.

    Quem responde ou supervisiona alcança as rotinas dos condomínios em que
    opera. A operação não entra aqui: o que ela vê são as execuções dela, e não
    o cadastro das rotinas.
    """
    return RotinaRecorrente.objects.filter(
        condominio__in=_com_papel(usuario, PAPEIS_QUE_CRIAM)
    )


def execucoes_para_ver(usuario) -> QuerySet[ExecucaoRotina]:
    """Execuções que esta pessoa alcança agora.

    É a mesma lista para todo mundo, com o conteúdo filtrado pela regra: quem
    responde ou supervisiona vê as ocorrências dos condomínios em que opera; a
    operação vê apenas aquelas em que é responsável. Sem vínculo, a resposta é
    vazia.
    """
    criam = _com_papel(usuario, PAPEIS_QUE_CRIAM)
    operacao = _com_papel(usuario, PAPEIS_QUE_OPERAM)

    return ExecucaoRotina.objects.filter(
        Q(condominio__in=criam)
        | Q(
            condominio__in=operacao,
            responsavel_id=getattr(usuario, "pk", None),
        )
    )


def pode_ver_alguma_rotina(usuario) -> bool:
    """Diz se a pessoa enxerga o módulo em algum condomínio agora.

    É a mesma pergunta que o menu faz, com a mesma regra — quem pergunta é a
    navegação, mas a resposta continua sendo do domínio.
    """
    return condominios_para_ver(usuario).exists()


def pode_criar_em_algum_condominio(usuario) -> bool:
    """Diz se a pessoa pode registrar rotina em algum condomínio agora."""
    return condominios_para_criar(usuario).exists()


def pode_criar_rotina(usuario, condominio) -> bool:
    """Diz se a pessoa pode registrar rotina neste condomínio agora.

    Recebe o condomínio resolvido. Sem certeza, a resposta é negar.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_criar(usuario).filter(pk=condominio.pk).exists()


def pode_ver_rotina(usuario, rotina) -> bool:
    """Diz se a pessoa pode consultar o cadastro desta rotina agora.

    A operação não entra aqui: ela acompanha a ocorrência, não a regra. Sem
    certeza, a resposta é negar.
    """
    if rotina is None or not getattr(rotina, "pk", None):
        return False

    condominio = getattr(rotina, "condominio", None)
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_criar(usuario).filter(pk=condominio.pk).exists()


def _pode_gerenciar(usuario, condominio) -> bool:
    """Diz se a pessoa responde pelo condomínio — ponto comum das decisões definitivas."""
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return _com_papel(usuario, PAPEIS_QUE_GERENCIAM).filter(pk=condominio.pk).exists()


def pode_editar_rotina(usuario, rotina) -> bool:
    """Diz se a pessoa pode editar a regra: tipo, título, periodicidade, contexto
    e responsável.

    Editar a regra é a mesma responsabilidade de criar. Sem certeza, a resposta
    é negar.
    """
    return pode_ver_rotina(usuario, rotina)


def pode_gerar_execucao(usuario, rotina) -> bool:
    """Diz se a pessoa pode adiantar a próxima ocorrência desta rotina agora."""
    return pode_ver_rotina(usuario, rotina)


def pode_administrar_rotina(usuario, rotina) -> bool:
    """Diz se a pessoa pode ativar ou inativar esta rotina.

    Desativar para a geração de novas ocorrências sem apagar as que existem. É
    decisão de quem responde pelo condomínio, e não de quem apenas supervisiona.
    """
    if rotina is None or not getattr(rotina, "pk", None):
        return False

    return _pode_gerenciar(usuario, getattr(rotina, "condominio", None))


def pode_ver_execucao(usuario, execucao) -> bool:
    """Diz se a pessoa pode consultar esta ocorrência agora.

    Quem responde ou supervisiona alcança todas as ocorrências do condomínio. A
    operação alcança apenas aquelas em que é responsável. Sem certeza, a resposta
    é negar.
    """
    if execucao is None or not getattr(execucao, "pk", None):
        return False

    condominio = getattr(execucao, "condominio", None)
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    papeis = _papeis_do_usuario(usuario, condominio)

    if papeis & PAPEIS_QUE_CRIAM:
        return True

    if Papel.OPERACIONAL in papeis:
        return execucao.responsavel_id == getattr(usuario, "pk", None)

    return False


def situacoes_permitidas_para(usuario, execucao) -> frozenset[str]:
    """Situações que esta pessoa pode dar a esta ocorrência agora.

    Uma função só responde por isso, para a regra não se espalhar pelas views em
    uma dúzia de ``if`` de papel. Uma ocorrência encerrada não muda mais. Sem
    certeza, a resposta é o conjunto vazio.
    """
    if not pode_ver_execucao(usuario, execucao):
        return frozenset()

    if execucao.esta_encerrada:
        return frozenset()

    papeis = _papeis_do_usuario(usuario, execucao.condominio)

    if papeis & PAPEIS_QUE_GERENCIAM:
        return frozenset(StatusExecucao.values)

    if Papel.SUPERVISORA in papeis:
        return frozenset(
            situacao
            for situacao in StatusExecucao.values
            if situacao != StatusExecucao.CANCELADA
        )

    if Papel.OPERACIONAL in papeis:
        if execucao.responsavel_id != getattr(usuario, "pk", None):
            return frozenset()
        return frozenset(SITUACOES_DE_QUEM_OPERA)

    return frozenset()


def pode_iniciar(usuario, execucao) -> bool:
    """Diz se a pessoa pode começar esta ocorrência agora."""
    if execucao is None or execucao.status != StatusExecucao.PENDENTE:
        return False

    return StatusExecucao.EM_ANDAMENTO in situacoes_permitidas_para(usuario, execucao)


def pode_concluir(usuario, execucao) -> bool:
    """Diz se a pessoa pode concluir esta ocorrência agora.

    Concluir exige dizer o que se encontrou, por isso tem ação própria — e não
    aparece no meio do movimento comum.
    """
    if execucao is None or execucao.esta_encerrada:
        return False

    return StatusExecucao.CONCLUIDA in situacoes_permitidas_para(usuario, execucao)


def pode_ignorar(usuario, execucao) -> bool:
    """Diz se a pessoa pode registrar que a atividade não se aplicava agora."""
    if execucao is None or execucao.esta_encerrada:
        return False

    return StatusExecucao.IGNORADA in situacoes_permitidas_para(usuario, execucao)


def pode_cancelar_execucao(usuario, execucao) -> bool:
    """Diz se a pessoa pode cancelar esta ocorrência.

    Cancelar diz que a ocorrência não vai acontecer, sem cancelar a rotina. É
    decisão de quem responde pelo condomínio, e de mais ninguém.
    """
    if execucao is None or execucao.esta_encerrada:
        return False

    return StatusExecucao.CANCELADA in situacoes_permitidas_para(usuario, execucao)


def pode_tratar_item(usuario, execucao) -> bool:
    """Diz se a pessoa pode registrar o que aconteceu com uma tarefa do checklist.

    Tratar uma tarefa é parte de fazer a ocorrência: quem pode movimentá-la
    enquanto ela está aberta pode dizer o que fez em cada tarefa. Depois de
    encerrada, o retrato não se mexe mais — nem as tarefas. Sem certeza, a
    resposta é negar.
    """
    if execucao is None or not getattr(execucao, "pk", None):
        return False

    return bool(situacoes_permitidas_para(usuario, execucao))


def pode_gerar_demanda(usuario, execucao) -> bool:
    """Diz se a pessoa pode abrir uma demanda a partir desta ocorrência.

    Abrir a consequência é a mesma responsabilidade de registrar demanda no
    condomínio. Nada é criado sozinho: quem encontrou o problema diz o que
    encontrou, e quem responde confirmou que aquilo vira demanda.
    """
    if execucao is None or not getattr(execucao, "pk", None):
        return False

    return _pode_criar(usuario, getattr(execucao, "condominio", None))


def pode_gerar_manutencao(usuario, execucao) -> bool:
    """Diz se a pessoa pode criar uma manutenção preventiva a partir desta ocorrência."""
    return pode_gerar_demanda(usuario, execucao)


def _pode_criar(usuario, condominio) -> bool:
    """Diz se a pessoa pode registrar demanda ou manutenção neste condomínio."""
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return _com_papel(usuario, PAPEIS_QUE_CRIAM).filter(pk=condominio.pk).exists()


def checklists_para_ver(usuario) -> QuerySet[ModeloChecklist]:
    """Checklists modelo que esta pessoa pode consultar agora.

    O checklist é contexto de Rotinas, não um módulo à parte: quem responde ou
    supervisiona alcança os modelos dos condomínios em que opera. A operação
    não entra — o cadastro do modelo não é o trabalho dela.
    """
    return ModeloChecklist.objects.filter(
        condominio__in=_com_papel(usuario, PAPEIS_QUE_CRIAM)
    )


def pode_ver_algum_checklist(usuario) -> bool:
    """Diz se a pessoa alcança o cadastro de checklists em algum condomínio."""
    return condominios_para_checklist(usuario).exists()


def pode_ver_checklist(usuario, checklist) -> bool:
    """Diz se a pessoa pode consultar este checklist modelo agora.

    Sem certeza, a resposta é negar: quem não enxerga o módulo não descobre que
    o checklist existe.
    """
    if checklist is None or not getattr(checklist, "pk", None):
        return False

    condominio = getattr(checklist, "condominio", None)
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_checklist(usuario).filter(pk=condominio.pk).exists()


def pode_editar_checklist(usuario, checklist) -> bool:
    """Diz se a pessoa pode criar ou editar o modelo e as tarefas dele.

    Montar a lista é a mesma responsabilidade de criar a rotina: administração,
    síndica e supervisão.
    """
    return pode_ver_checklist(usuario, checklist)


def pode_administrar_checklist(usuario, checklist) -> bool:
    """Diz se a pessoa pode ativar ou inativar este checklist.

    Retirar o modelo de circulação é decisão de quem responde pelo condomínio, e
    não de quem apenas supervisiona — como acontece com a rotina.
    """
    if checklist is None or not getattr(checklist, "pk", None):
        return False

    return _pode_gerenciar(usuario, getattr(checklist, "condominio", None))


def pode_editar_item_do_checklist(usuario, item) -> bool:
    """Diz se a pessoa pode mexer numa tarefa do modelo.

    Trata-se da mesma permissão de editar o checklist: a tarefa é o conteúdo
    dele.
    """
    if not isinstance(item, ItemModeloChecklist):
        return False

    return pode_editar_checklist(usuario, getattr(item, "checklist", None))


def usuarios_elegiveis_como_responsavel(condominio) -> QuerySet:
    """Pessoas que podem responder por uma rotina deste condomínio.

    Só quem tem vínculo ativo e enxerga o módulo: o responsável precisa poder
    acompanhar o que assume. Morador e portaria não entram nesta fase.
    """
    Usuario = get_user_model()

    if condominio is None or not getattr(condominio, "pk", None):
        return Usuario.objects.none()

    ids = AcessoUsuarioCondominio.objects.filter(
        condominio_id=condominio.pk,
        ativo=True,
        papel__in=PAPEIS_QUE_VEEM,
        usuario__is_active=True,
    ).values_list("usuario_id", flat=True)

    return Usuario.objects.filter(pk__in=ids).distinct()


def usuarios_elegiveis_entre(condominios) -> QuerySet:
    """Pessoas elegíveis como responsável em qualquer um destes condomínios.

    Serve à lista, onde a gestão filtra por responsável olhando vários
    condomínios de uma vez. A regra de elegibilidade é a mesma de
    ``usuarios_elegiveis_como_responsavel``. Sem condomínios informados, a
    resposta é vazia.
    """
    Usuario = get_user_model()

    if condominios is None:
        return Usuario.objects.none()

    ids = AcessoUsuarioCondominio.objects.filter(
        condominio__in=condominios,
        ativo=True,
        papel__in=PAPEIS_QUE_VEEM,
        usuario__is_active=True,
    ).values_list("usuario_id", flat=True)

    return Usuario.objects.filter(pk__in=ids).distinct()
