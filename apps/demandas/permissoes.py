"""Quem pode mexer nas demandas operacionais.

A resposta vem sempre do vínculo ativo da pessoa com o condomínio, e nunca do
menu, do formulário ou de um identificador trazido pelo navegador. O menu pode
esconder o que não serve — esconder não é autorizar.

Aqui mora apenas a regra do domínio: quais papéis criam demanda, quais editam o
conteúdo, quais apenas movem a situação e quem enxerga o módulo. A pergunta
"esta pessoa acessa este condomínio?" continua em ``apps.condominios.acesso``,
para não haver duas respostas para a mesma pergunta.
"""
from django.contrib.auth import get_user_model
from django.db.models import QuerySet

from apps.condominios.acesso import condominios_com_papel
from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio, Condominio

from .constants import StatusDemanda

# Administradora e síndica respondem pelo condomínio: criam, editam, atribuem,
# concluem e cancelam.
PAPEIS_QUE_GERENCIAM = frozenset({Papel.ADMINISTRADORA, Papel.SINDICA})

# A supervisão acompanha e registra, sem responder pelo condomínio: cria e move a
# situação, mas não cancela e não edita o conteúdo alheio.
PAPEIS_QUE_CRIAM = PAPEIS_QUE_GERENCIAM | {Papel.SUPERVISORA}

# A operação participa do dia a dia: vê as demandas do condomínio, move a
# situação e conclui o que é dela. Não cancela nem edita o conteúdo.
PAPEIS_QUE_OPERAM = frozenset({Papel.OPERACIONAL})

# Quem enxerga o módulo. Portaria e morador não entram nesta fase.
PAPEIS_QUE_VEEM = PAPEIS_QUE_CRIAM | PAPEIS_QUE_OPERAM

# Situações que quem não responde pelo condomínio alcança. Concluir depende de
# ser o responsável — por isso não entra aqui.
SITUACOES_DE_QUEM_OPERA = frozenset(
    {StatusDemanda.ABERTA, StatusDemanda.EM_ANDAMENTO, StatusDemanda.AGUARDANDO}
)


def _com_papel(usuario, papeis: frozenset[str]) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa atua com algum destes papéis.

    A regra — vínculo ativo com um dos papéis, ou o escopo global do
    administrador do sistema — vem de ``apps.condominios.acesso``, em um lugar
    só. Aqui se dizem apenas quais papéis importam para as demandas.
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
    """Condomínios cujas demandas a pessoa pode consultar agora."""
    return _com_papel(usuario, PAPEIS_QUE_VEEM)


def condominios_para_criar(usuario) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa pode registrar demanda agora."""
    return _com_papel(usuario, PAPEIS_QUE_CRIAM)


def condominios_para_gerenciar(usuario) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa responde pela gestão agora.

    É o recorte de quem decide — administradora e síndica. A supervisão
    acompanha e registra, mas não responde pelo condomínio; por isso não entra
    aqui. As exceções que pedem decisão são lidas neste escopo.
    """
    return _com_papel(usuario, PAPEIS_QUE_GERENCIAM)


def pode_gerenciar_em_algum_condominio(usuario) -> bool:
    """Diz se a pessoa responde pela gestão em algum condomínio agora."""
    return condominios_para_gerenciar(usuario).exists()


def pode_ver_alguma_demanda(usuario) -> bool:
    """Diz se a pessoa enxerga o módulo em algum condomínio agora.

    É a mesma pergunta que o menu faz, com a mesma regra — quem pergunta é a
    navegação, mas a resposta continua sendo do domínio.
    """
    return condominios_para_ver(usuario).exists()


def pode_criar_em_algum_condominio(usuario) -> bool:
    """Diz se a pessoa pode registrar demanda em algum condomínio agora."""
    return condominios_para_criar(usuario).exists()


def pode_criar_demanda(usuario, condominio) -> bool:
    """Diz se a pessoa pode registrar demanda neste condomínio agora.

    Recebe o condomínio resolvido. Sem certeza, a resposta é negar.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_criar(usuario).filter(pk=condominio.pk).exists()


def pode_ver_demanda(usuario, demanda) -> bool:
    """Diz se a pessoa pode consultar esta demanda agora.

    A pergunta é feita ao condomínio da demanda, resolvido no backend. Sem
    certeza, a resposta é negar.
    """
    if demanda is None or not getattr(demanda, "pk", None):
        return False

    condominio = getattr(demanda, "condominio", None)
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_ver(usuario).filter(pk=condominio.pk).exists()


def _pode_gerenciar(usuario, condominio) -> bool:
    """Diz se a pessoa responde pelo condomínio — ponto comum de editar e cancelar."""
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return _com_papel(usuario, PAPEIS_QUE_GERENCIAM).filter(pk=condominio.pk).exists()


def pode_editar_demanda(usuario, demanda) -> bool:
    """Diz se a pessoa pode editar o conteúdo: título, descrição, responsável,
    prazo e local. A prioridade não entra aqui: ela é consequência do relato,
    lida pela mesma regra do registro, e não se corrige à mão.

    Editar é a mesma responsabilidade de responder pelo condomínio. Sem
    certeza, a resposta é negar.
    """
    if demanda is None or not getattr(demanda, "pk", None):
        return False

    return _pode_gerenciar(usuario, demanda.condominio)


def status_permitidos_para(usuario, demanda) -> frozenset[str]:
    """Situações que esta pessoa pode dar a esta demanda agora.

    Uma função só responde por isso, para a regra não se espalhar pelas views em
    uma dúzia de ``if`` de papel. Uma demanda encerrada só volta a andar por
    quem responde pelo condomínio. Sem certeza, a resposta é o conjunto vazio.
    """
    if not pode_ver_demanda(usuario, demanda):
        return frozenset()

    papeis = _papeis_do_usuario(usuario, demanda.condominio)

    if papeis & PAPEIS_QUE_GERENCIAM:
        return frozenset(StatusDemanda.values)

    if demanda.esta_encerrada:
        return frozenset()

    if Papel.SUPERVISORA in papeis:
        return frozenset(
            situacao
            for situacao in StatusDemanda.values
            if situacao != StatusDemanda.CANCELADA
        )

    if Papel.OPERACIONAL in papeis:
        permitidos = set(SITUACOES_DE_QUEM_OPERA)
        if demanda.responsavel_id == getattr(usuario, "pk", None):
            permitidos.add(StatusDemanda.CONCLUIDA)
        return frozenset(permitidos)

    return frozenset()


def pode_alterar_status(usuario, demanda) -> bool:
    """Diz se a pessoa pode mover a situação desta demanda agora."""
    return bool(status_permitidos_para(usuario, demanda))


def pode_adicionar_observacao(usuario, demanda) -> bool:
    """Diz se a pessoa pode registrar uma observação no histórico.

    Quem acompanha a demanda pode dizer o que sabe sobre ela — é assim que a
    operação participa sem responder pelo condomínio.
    """
    return pode_ver_demanda(usuario, demanda)


def usuarios_elegiveis_como_responsavel(condominio) -> QuerySet:
    """Pessoas que podem responder por uma demanda deste condomínio.

    Só quem tem vínculo ativo e enxerga o módulo: o responsável precisa poder
    acompanhar a demanda que assume. Morador e portaria não entram nesta fase.
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
    ``usuarios_elegiveis_como_responsavel``: vínculo ativo e papel que enxerga
    o módulo. Sem condomínios informados, a resposta é vazia.
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
