"""Quem pode ver e mexer nas manutenções operacionais.

A resposta vem sempre do vínculo ativo da pessoa com o condomínio, e nunca do
menu, do formulário ou de um identificador trazido pelo navegador. O menu pode
esconder o que não serve — esconder não é autorizar.

Administração e síndica respondem pelo condomínio: criam, editam, atribuem,
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

from apps.condominios.acesso import condominios_do_usuario
from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio, Condominio

from .constants import StatusManutencao

# Administração e síndica respondem pelo condomínio.
PAPEIS_QUE_GERENCIAM = frozenset({Papel.ADMIN, Papel.SINDICA})

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
