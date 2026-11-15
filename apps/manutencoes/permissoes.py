"""Quem pode ver e mexer nas manutenções operacionais.

A resposta vem sempre do vínculo ativo da pessoa com o condomínio, e nunca do
menu, do formulário ou de um identificador trazido pelo navegador. O menu pode
esconder o que não serve — esconder não é autorizar.

Administradora e síndica respondem pelo condomínio: criam, editam, atribuem,
movem, concluem e cancelam. A supervisão acompanha e registra — cria, edita os
dados operacionais, atribui responsável, define local, equipamento e fornecedor,
registra custos, inicia, coloca em aguardando e conclui —, mas não cancela nem
administra cadastros. A operação participa apenas daquilo que é dela: sem ser
responsável, não mexe em nada.

A pergunta "esta pessoa acessa este condomínio?" continua em
``apps.condominios.acesso``, para não haver duas respostas para a mesma pergunta.
"""
from django.contrib.auth import get_user_model
from django.db.models import QuerySet

from apps.condominios.acesso import condominios_com_papel
from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio, Condominio

from .constants import StatusConferencia, StatusManutencao

# Administradora e síndica respondem pelo condomínio.
PAPEIS_QUE_GERENCIAM = frozenset({Papel.ADMINISTRADORA, Papel.SINDICA})

# Administrar a biblioteca de modelos reutilizáveis de conferência é ato da
# Administradora — quem responde pela configuração estrutural do condomínio —, e
# não da operação do dia a dia. A síndica conduz a operação, mas não administra
# esta biblioteca nem a aplica ao plano. É capacidade própria, e não a matriz de
# quem gerencia; a administração do sistema não entra por ser ``is_staff``.
PAPEIS_QUE_ADMINISTRAM_MODELOS = frozenset({Papel.ADMINISTRADORA})

# A supervisão cria e opera a manutenção, sem cancelar nem administrar cadastros.
PAPEIS_QUE_CRIAM = PAPEIS_QUE_GERENCIAM | {Papel.SUPERVISORA}

# A operação só atua sobre o que está sob responsabilidade dela.
PAPEIS_QUE_OPERAM = frozenset({Papel.OPERACIONAL})

# Quem enxerga o módulo. Portaria e morador não entram nesta fase.
PAPEIS_QUE_VEEM = PAPEIS_QUE_CRIAM | PAPEIS_QUE_OPERAM

# Movimentos que quem não responde pelo condomínio alcança. Concluir depende de
# ser o responsável — por isso não entra aqui.
SITUACOES_DE_QUEM_OPERA = frozenset(
    {
        StatusManutencao.ABERTA,
        StatusManutencao.PROGRAMADA,
        StatusManutencao.EM_ANDAMENTO,
        StatusManutencao.AGUARDANDO,
    }
)


def _com_papel(usuario, papeis: frozenset[str]) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa atua com algum destes papéis.

    A regra — vínculo ativo com um dos papéis, ou o escopo global do
    administrador do sistema — vem de ``apps.condominios.acesso``, em um lugar
    só. Aqui se dizem apenas quais papéis importam para as manutenções.
    """
    return condominios_com_papel(usuario, papeis)


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
    """Condomínios cujas manutenções a pessoa pode consultar agora."""
    return _com_papel(usuario, PAPEIS_QUE_VEEM)


def condominios_para_criar(usuario) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa pode registrar manutenção agora."""
    return _com_papel(usuario, PAPEIS_QUE_CRIAM)


def pode_ver_alguma_manutencao(usuario) -> bool:
    """Diz se a pessoa enxerga o módulo em algum condomínio agora.

    É a mesma pergunta que o menu faz, com a mesma regra — quem pergunta é a
    navegação, mas a resposta continua sendo do domínio.
    """
    return condominios_para_ver(usuario).exists()


def pode_criar_em_algum_condominio(usuario) -> bool:
    """Diz se a pessoa pode registrar manutenção em algum condomínio agora."""
    return condominios_para_criar(usuario).exists()


def pode_criar_manutencao(usuario, condominio) -> bool:
    """Diz se a pessoa pode registrar manutenção neste condomínio agora.

    Recebe o condomínio resolvido. Sem certeza, a resposta é negar.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_criar(usuario).filter(pk=condominio.pk).exists()


def condominios_para_administrar_modelos(usuario) -> QuerySet[Condominio]:
    """Condomínios cujos modelos de conferência a pessoa pode administrar agora."""
    return _com_papel(usuario, PAPEIS_QUE_ADMINISTRAM_MODELOS)


def pode_administrar_modelos_de_conferencia(usuario, condominio) -> bool:
    """Diz se a pessoa pode criar, editar e desativar modelos deste condomínio.

    Modelo reutilizável é configuração estrutural do condomínio, e não operação
    do dia: cabe à Administradora, e a mais ninguém. Sem certeza, a resposta é
    negar.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return (
        condominios_para_administrar_modelos(usuario)
        .filter(pk=condominio.pk)
        .exists()
    )


def pode_ver_manutencao(usuario, manutencao) -> bool:
    """Diz se a pessoa pode consultar esta manutenção agora.

    A pergunta é feita ao condomínio da manutenção, resolvido no backend. Sem
    certeza, a resposta é negar.
    """
    if manutencao is None or not getattr(manutencao, "pk", None):
        return False

    condominio = getattr(manutencao, "condominio", None)
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_ver(usuario).filter(pk=condominio.pk).exists()


def _pode_gerenciar(usuario, condominio) -> bool:
    """Diz se a pessoa responde pelo condomínio — ponto comum das decisões definitivas."""
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return _com_papel(usuario, PAPEIS_QUE_GERENCIAM).filter(pk=condominio.pk).exists()


def pode_editar_manutencao(usuario, manutencao) -> bool:
    """Diz se a pessoa pode editar o conteúdo: tipo, título, problema, serviço,
    local, equipamento, fornecedor, responsável, previsão e custo. A prioridade
    não entra aqui: ela é consequência do problema relatado, lida pela mesma
    regra do resto do sistema, e não se corrige à mão.

    Editar os dados operacionais é a mesma responsabilidade de criar. Sem
    certeza, a resposta é negar.
    """
    if manutencao is None or not getattr(manutencao, "pk", None):
        return False

    condominio = manutencao.condominio
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_criar(usuario).filter(pk=condominio.pk).exists()


def pode_cancelar_manutencao(usuario, manutencao) -> bool:
    """Diz se a pessoa pode cancelar esta manutenção.

    Cancelar encerra o registro sem que o serviço aconteça. É decisão de quem
    responde pelo condomínio, e de mais ninguém. Sem certeza, a resposta é negar.
    """
    if manutencao is None or not getattr(manutencao, "pk", None):
        return False

    return _pode_gerenciar(usuario, manutencao.condominio)


def pode_administrar_manutencao(usuario, manutencao) -> bool:
    """Diz se a pessoa pode retirar a manutenção da operação, e devolvê-la.

    Desativar é retirada administrativa: o registro sai das listas sem ser
    apagado. É outra coisa que cancelar — não se confundem.
    """
    return pode_cancelar_manutencao(usuario, manutencao)


def status_permitidos_para(usuario, manutencao) -> frozenset[str]:
    """Situações que esta pessoa pode dar a esta manutenção agora.

    Uma função só responde por isso, para a regra não se espalhar pelas views em
    uma dúzia de ``if`` de papel. Sem certeza, a resposta é o conjunto vazio.
    """
    if not pode_ver_manutencao(usuario, manutencao):
        return frozenset()

    papeis = _papeis_do_usuario(usuario, manutencao.condominio)

    if papeis & PAPEIS_QUE_GERENCIAM:
        return frozenset(StatusManutencao.values)

    if manutencao.esta_encerrada:
        return frozenset()

    if Papel.SUPERVISORA in papeis:
        return frozenset(
            situacao
            for situacao in StatusManutencao.values
            if situacao != StatusManutencao.CANCELADA
        )

    if Papel.OPERACIONAL in papeis:
        if manutencao.responsavel_id != getattr(usuario, "pk", None):
            return frozenset()
        permitidos = set(SITUACOES_DE_QUEM_OPERA)
        permitidos.add(StatusManutencao.CONCLUIDA)
        return frozenset(permitidos)

    return frozenset()


def situacoes_para_mudar(usuario, manutencao) -> frozenset[str]:
    """Situações oferecidas no movimento comum, sem a conclusão.

    Concluir não é só trocar de situação: exige dizer o que foi feito. Por isso
    tem ação própria, e não aparece no meio das outras escolhas.
    """
    return frozenset(
        situacao
        for situacao in status_permitidos_para(usuario, manutencao)
        if situacao != StatusManutencao.CONCLUIDA
    )


def pode_alterar_status(usuario, manutencao) -> bool:
    """Diz se a pessoa pode mover a situação desta manutenção agora."""
    return bool(situacoes_para_mudar(usuario, manutencao))


def pode_concluir(usuario, manutencao) -> bool:
    """Diz se a pessoa pode concluir esta manutenção agora.

    Concluir depende de alcançar a situação e de a manutenção ainda estar em
    andamento — concluir o que já terminou não é movimento, é correção.
    """
    if manutencao is None or manutencao.esta_encerrada:
        return False

    return StatusManutencao.CONCLUIDA in status_permitidos_para(usuario, manutencao)


def pode_registrar_execucao(usuario, manutencao) -> bool:
    """Diz se a pessoa pode registrar que o serviço desta manutenção foi executado.

    Registrar execução é o mesmo ato de quem opera a manutenção: quem responde
    pelo condomínio, a supervisão e o responsável sob cuja mão o serviço
    acontece. Não é privilégio novo — é a autoridade que já move e encerra a
    manutenção. Uma execução já aprovada não se registra de novo: a decisão foi
    tomada, e o caminho dali é concluir.
    """
    if manutencao is None or manutencao.esta_encerrada:
        return False

    if manutencao.status_conferencia == StatusConferencia.APROVADA:
        return False

    return StatusManutencao.CONCLUIDA in status_permitidos_para(usuario, manutencao)


def pode_conferir(usuario, manutencao) -> bool:
    """Diz se a pessoa pode conferir o que foi entregue nesta manutenção.

    Conferir é olhar a entrega e dizer se ela passa — não é executar. Quem
    responde pelo condomínio e a supervisão conferem; a operação, que executa,
    não entra aqui nesta fase. Só faz sentido onde há conferência devida, e não
    depois que a manutenção já encerrou. Sem certeza, a resposta é negar.
    """
    if manutencao is None or not getattr(manutencao, "pk", None):
        return False

    if not manutencao.exige_conferencia:
        return False

    if manutencao.esta_encerrada:
        return False

    condominio = manutencao.condominio
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_criar(usuario).filter(pk=condominio.pk).exists()


def pode_anexar_evidencia(usuario, manutencao) -> bool:
    """Diz se a pessoa pode anexar uma prova a esta manutenção.

    Anexar é o mesmo ato de quem opera a manutenção: quem responde pelo
    condomínio, a supervisão e o responsável sob cuja mão o serviço acontece.
    Não é privilégio novo, e não se amplia sozinho — o operacional só anexa ao
    que é dele, e o fornecedor externo não tem acesso nesta fase. Sem certeza, a
    resposta é negar.
    """
    if not pode_ver_manutencao(usuario, manutencao):
        return False

    condominio = manutencao.condominio
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    if condominios_para_criar(usuario).filter(pk=condominio.pk).exists():
        return True

    papeis = _papeis_do_usuario(usuario, condominio)
    return Papel.OPERACIONAL in papeis and manutencao.responsavel_id == getattr(
        usuario, "pk", None
    )


def usuarios_elegiveis_como_responsavel(condominio) -> QuerySet:
    """Pessoas que podem responder por uma manutenção deste condomínio.

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
