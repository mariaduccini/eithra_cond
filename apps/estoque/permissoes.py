"""Quem pode ver e mexer no estoque e nas compras operacionais.

A resposta vem sempre do vínculo ativo da pessoa com o condomínio, e nunca do
menu, do formulário ou de um identificador trazido pelo navegador. O menu pode
esconder o que não serve — esconder não é autorizar.

Administradora e síndica respondem pelo condomínio: cadastram itens, registram
movimentos, montam, confirmam e cancelam compras. A supervisão opera o estoque e
as compras do mesmo modo, mas não cancela — cancelar é decisão de quem responde
pelo condomínio. A operação não entra em compras nesta fase: não vê o módulo,
não cria, não confirma e não cancela. O que ela alcança é uma coisa só —
registrar a retirada de material para uma manutenção que é dela.

A pergunta "esta pessoa acessa este condomínio?" continua em
``apps.condominios.acesso``, para não haver duas respostas para a mesma pergunta.
"""
from django.db.models import Q, QuerySet

from apps.condominios.acesso import condominios_com_papel
from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio, Condominio
from apps.manutencoes.models import Manutencao

from .constants import StatusCompra, TipoMovimentacaoEstoque

# Administradora e síndica respondem pelo condomínio.
PAPEIS_QUE_GERENCIAM = frozenset({Papel.ADMINISTRADORA, Papel.SINDICA})

# A supervisão opera estoque e compras, mas não cancela compra.
PAPEIS_QUE_OPERAM = PAPEIS_QUE_GERENCIAM | {Papel.SUPERVISORA}

# Quem enxerga o estoque. A operação entra porque precisa retirar material para
# as manutenções que assume — sem acesso a compras.
PAPEIS_QUE_VEEM_ESTOQUE = PAPEIS_QUE_OPERAM | {Papel.OPERACIONAL}

# Compras são de quem responde pela gestão. A operação não entra nesta fase.
PAPEIS_QUE_VEEM_COMPRAS = PAPEIS_QUE_OPERAM

# Movimentos que exigem responder pelo condomínio ou supervisionar. A operação
# não registra uso, perda, vencimento nem ajuste — só retirada para manutenção.
TIPOS_DE_GESTAO = frozenset(
    {
        TipoMovimentacaoEstoque.SAIDA_USO,
        TipoMovimentacaoEstoque.PERDA,
        TipoMovimentacaoEstoque.VENCIMENTO,
        TipoMovimentacaoEstoque.AJUSTE_ENTRADA,
        TipoMovimentacaoEstoque.AJUSTE_SAIDA,
    }
)


def _com_papel(usuario, papeis: frozenset[str]) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa atua com algum destes papéis.

    A regra — vínculo ativo com um dos papéis, ou o escopo global do
    administrador do sistema — vem de ``apps.condominios.acesso``, em um lugar
    só. Aqui se dizem apenas quais papéis importam para o estoque e as compras.
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


def condominios_para_ver_estoque(usuario) -> QuerySet[Condominio]:
    """Condomínios cujo estoque a pessoa pode consultar agora."""
    return _com_papel(usuario, PAPEIS_QUE_VEEM_ESTOQUE)


def condominios_para_operar_estoque(usuario) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa pode cadastrar itens e registrar movimentos."""
    return _com_papel(usuario, PAPEIS_QUE_OPERAM)


def condominios_para_ver_compras(usuario) -> QuerySet[Condominio]:
    """Condomínios cujas compras a pessoa pode consultar agora."""
    return _com_papel(usuario, PAPEIS_QUE_VEEM_COMPRAS)


def condominios_para_criar_compras(usuario) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa pode abrir uma compra agora."""
    return _com_papel(usuario, PAPEIS_QUE_OPERAM)


def pode_ver_estoque(usuario, condominio) -> bool:
    """Diz se a pessoa pode consultar o estoque deste condomínio agora."""
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_ver_estoque(usuario).filter(pk=condominio.pk).exists()


def pode_ver_estoque_em_algum(usuario) -> bool:
    """Diz se a pessoa enxerga o módulo de estoque em algum condomínio agora."""
    return condominios_para_ver_estoque(usuario).exists()


def pode_ver_compras(usuario, condominio) -> bool:
    """Diz se a pessoa pode consultar as compras deste condomínio agora."""
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_ver_compras(usuario).filter(pk=condominio.pk).exists()


def pode_ver_compras_em_algum(usuario) -> bool:
    """Diz se a pessoa enxerga o módulo de compras em algum condomínio agora."""
    return condominios_para_ver_compras(usuario).exists()


def pode_gerenciar_itens(usuario, condominio) -> bool:
    """Diz se a pessoa pode cadastrar e alterar itens deste condomínio agora."""
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_operar_estoque(usuario).filter(pk=condominio.pk).exists()


def pode_ver_item(usuario, item) -> bool:
    """Diz se a pessoa pode consultar este item agora.

    A pergunta é feita ao condomínio do item, resolvido no backend. Sem certeza,
    a resposta é negar.
    """
    if item is None or not getattr(item, "pk", None):
        return False

    return pode_ver_estoque(usuario, item.condominio)


def pode_registrar_movimento(usuario, item) -> bool:
    """Diz se a pessoa pode registrar movimentos de gestão neste item agora.

    Uso, perda, vencimento e ajuste são de quem responde pelo condomínio ou
    supervisiona. A operação fica de fora: o que ela alcança tem regra própria.
    """
    if item is None or not getattr(item, "pk", None):
        return False

    return condominios_para_operar_estoque(usuario).filter(
        pk=item.condominio_id
    ).exists()


def tipos_de_movimento_permitidos(usuario, item) -> frozenset[str]:
    """Tipos de movimento que esta pessoa pode registrar neste item agora.

    Uma função só responde por isso, para a regra não se espalhar pela tela em
    uma dúzia de ``if`` de papel. Sem certeza, a resposta é o conjunto vazio.
    """
    if not pode_ver_item(usuario, item):
        return frozenset()

    papeis = _papeis_do_usuario(usuario, item.condominio)
    if papeis & PAPEIS_QUE_OPERAM:
        return frozenset(TIPOS_DE_GESTAO)

    return frozenset()


def pode_registrar_saida_para_manutencao(usuario, manutencao, item=None) -> bool:
    """Diz se a pessoa pode retirar material para esta manutenção agora.

    Quem responde pelo condomínio ou supervisiona sempre pode. A operação pode
    apenas quando é responsável pela manutenção — é o único movimento que ela
    alcança. Ter saldo é conferido no serviço, no momento da retirada: aqui se
    responde quem pode, não quanto há.
    """
    if manutencao is None or not getattr(manutencao, "pk", None):
        return False

    condominio = manutencao.condominio
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    if item is not None and getattr(item, "pk", None):
        if item.condominio_id != condominio.pk:
            return False

    papeis = _papeis_do_usuario(usuario, condominio)

    if papeis & PAPEIS_QUE_OPERAM:
        return True

    if Papel.OPERACIONAL in papeis:
        return manutencao.responsavel_id == getattr(usuario, "pk", None)

    return False


def manutencoes_para_retirada(usuario) -> QuerySet[Manutencao]:
    """Manutenções para as quais esta pessoa pode retirar material agora.

    Quem responde pelo condomínio ou supervisiona alcança as manutenções em uso
    dos condomínios em que opera. A operação alcança apenas as suas — aquelas em
    que é responsável. Sem vínculo, a resposta é vazia.
    """
    operam = _com_papel(usuario, PAPEIS_QUE_OPERAM)
    operacao = _com_papel(usuario, frozenset({Papel.OPERACIONAL}))

    return Manutencao.objects.filter(ativo=True).filter(
        Q(condominio__in=operam)
        | Q(
            condominio__in=operacao,
            responsavel_id=getattr(usuario, "pk", None),
        )
    )


def pode_ver_compra(usuario, compra) -> bool:
    """Diz se a pessoa pode consultar esta compra agora."""
    if compra is None or not getattr(compra, "pk", None):
        return False

    return pode_ver_compras(usuario, compra.condominio)


def pode_criar_compra(usuario, condominio) -> bool:
    """Diz se a pessoa pode abrir uma compra neste condomínio agora."""
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_criar_compras(usuario).filter(pk=condominio.pk).exists()


def pode_editar_compra(usuario, compra) -> bool:
    """Diz se a pessoa pode alterar esta compra agora.

    Alterar alcança o cabeçalho e as linhas — e só enquanto a compra é rascunho.
    O que já foi confirmado não se reescreve.
    """
    if not pode_ver_compra(usuario, compra):
        return False

    return compra.pode_editar


def pode_confirmar_compra(usuario, compra) -> bool:
    """Diz se a pessoa pode confirmar esta compra agora.

    Confirmar faz a compra valer e movimenta o estoque. É decisão de quem opera,
    e só sobre o que ainda é rascunho.
    """
    if not pode_ver_compra(usuario, compra):
        return False

    return compra.status == StatusCompra.RASCUNHO


def condominios_para_gerenciar_compras(usuario) -> QuerySet[Condominio]:
    """Condomínios cujas compras a pessoa pode encerrar agora."""
    return _com_papel(usuario, PAPEIS_QUE_GERENCIAM)


def pode_cancelar_compra(usuario, compra) -> bool:
    """Diz se a pessoa pode cancelar esta compra agora.

    Cancelar encerra um rascunho que não vai acontecer. É decisão de quem
    responde pelo condomínio, e de mais ninguém. Uma compra confirmada já entrou
    no estoque e não se cancela nesta fase.
    """
    if not pode_ver_compra(usuario, compra):
        return False

    if compra.status != StatusCompra.RASCUNHO:
        return False

    return condominios_para_gerenciar_compras(usuario).filter(
        pk=compra.condominio_id
    ).exists()


def pode_excluir_compra(usuario, compra) -> bool:
    """Diz se a pessoa pode apagar este rascunho agora.

    Apagar só alcança rascunho que ainda não virou nada: sem itens, não há o que
    perder. Quem responde pelo condomínio decide, como no cancelamento. O que já
    foi montado não se apaga — cancela-se.
    """
    if not pode_cancelar_compra(usuario, compra):
        return False

    return not compra.itens.exists()


def pode_marcar_reembolso(usuario, compra) -> bool:
    """Diz se a pessoa pode marcar esta compra para reembolso agora.

    Marcar não move dinheiro: só registra que alguém pagou e precisa ser
    reembolsado. Vale enquanto não houver reembolso nenhum e a compra não
    estiver cancelada.
    """
    from .constants import StatusReembolso

    if not pode_ver_compra(usuario, compra):
        return False
    if compra.status == StatusCompra.CANCELADA:
        return False
    if compra.status_reembolso != StatusReembolso.NAO_SOLICITADO:
        return False
    return condominios_para_gerenciar_compras(usuario).filter(
        pk=compra.condominio_id
    ).exists()


def pode_registrar_reembolso(usuario, compra) -> bool:
    """Só a gestão do condomínio registra que o dinheiro foi devolvido."""
    from .constants import StatusReembolso

    if not pode_ver_compra(usuario, compra):
        return False
    if compra.status != StatusCompra.CONFIRMADA:
        return False
    if compra.status_reembolso != StatusReembolso.PENDENTE:
        return False
    return condominios_para_gerenciar_compras(usuario).filter(
        pk=compra.condominio_id
    ).exists()
