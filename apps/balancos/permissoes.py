"""Quem pode mexer na entrada de balanços.

A resposta vem sempre do vínculo ativo da pessoa com o condomínio, e nunca do
menu, do formulário ou de um identificador trazido pelo navegador. O menu pode
esconder o que não serve — esconder não é autorizar.

Aqui mora apenas a regra do domínio: quais papéis importam balanço, quais o
leem e quais o corrigem. A pergunta "esta pessoa acessa este condomínio?"
continua em ``apps.condominios.acesso``, para não haver duas respostas para a
mesma pergunta.
"""
from django.db.models import QuerySet

from apps.condominios.acesso import condominios_com_papel
from apps.condominios.constants import Papel
from apps.condominios.models import Condominio

# A administradora do condomínio e a síndica recebem o documento da empresa
# administradora e respondem por ele. Operação, portaria e morador não importam
# balanço.
PAPEIS_QUE_IMPORTAM = frozenset({Papel.ADMINISTRADORA, Papel.SINDICA})

# Corrigir o detalhamento é a mesma responsabilidade de importar: quem responde
# pelo documento responde pelo que nele foi ajustado.
PAPEIS_QUE_CORRIGEM = PAPEIS_QUE_IMPORTAM

# Ler o mês confirmado é mais amplo: a supervisão acompanha o mês sem responder
# por ele. Ler não autoriza corrigir.
PAPEIS_QUE_LEEM = frozenset(
    {Papel.ADMINISTRADORA, Papel.SINDICA, Papel.SUPERVISORA}
)

# Anexar uma foto que explica uma despesa é a mesma responsabilidade de corrigir
# o que ela explica: quem responde pelo detalhamento responde pelo que o ilustra.
# A supervisão vê as fotos, mas não as acrescenta nem decide exibi-las aos moradores.
PAPEIS_QUE_GERENCIAM_EVIDENCIAS = PAPEIS_QUE_CORRIGEM

# A transparência residencial é uma leitura separada do detalhe administrativo:
# moradores só recebem balanços publicados e apenas em condomínios onde têm
# vínculo ativo como morador.
PAPEIS_QUE_LEEM_PUBLICADOS = frozenset({Papel.MORADOR})


def _com_papel(usuario, papeis: frozenset[str]) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa atua com algum destes papéis.

    A regra — vínculo ativo com um dos papéis, ou o escopo global do
    administrador do sistema — vem de ``apps.condominios.acesso``, em um lugar
    só. Aqui se dizem apenas quais papéis importam para os balanços.
    """
    return condominios_com_papel(usuario, papeis)


def condominios_para_importar(usuario) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa pode importar balanço agora.

    A exigência é dupla: vínculo ativo com o condomínio (a resposta de
    ``condominios_do_usuario``) e um papel que importa. Faltando qualquer uma
    das duas, o condomínio não entra na lista.
    """
    return _com_papel(usuario, PAPEIS_QUE_IMPORTAM)


def condominios_para_ler(usuario) -> QuerySet[Condominio]:
    """Condomínios cujo balanço confirmado a pessoa pode ler agora."""
    return _com_papel(usuario, PAPEIS_QUE_LEEM)


def condominios_para_ler_publicados(usuario) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa pode ler apenas balanços publicados."""
    return _com_papel(usuario, PAPEIS_QUE_LEEM_PUBLICADOS)


def condominios_para_corrigir(usuario) -> QuerySet[Condominio]:
    """Condomínios cujo balanço confirmado a pessoa pode corrigir agora."""
    return _com_papel(usuario, PAPEIS_QUE_CORRIGEM)


def condominios_para_gerenciar_evidencias(usuario) -> QuerySet[Condominio]:
    """Condomínios cujo balanço a pessoa pode ilustrar com fotos agora."""
    return _com_papel(usuario, PAPEIS_QUE_GERENCIAM_EVIDENCIAS)


def pode_importar_balanco(usuario, condominio) -> bool:
    """Diz se a pessoa pode importar balanço neste condomínio agora.

    Recebe o condomínio resolvido, como o resto da autorização do sistema.
    Sem certeza, a resposta é negar.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_importar(usuario).filter(pk=condominio.pk).exists()


def pode_importar_em_algum_condominio(usuario) -> bool:
    """Diz se a pessoa tem, agora, algum condomínio em que pode importar.

    É a mesma pergunta que a tela responde, com a mesma regra — quem pergunta
    é o menu, mas a resposta continua sendo do domínio. Assim não existem duas
    listas de papéis, uma para a navegação e outra para o backend, que possam
    divergir com o tempo.
    """
    return condominios_para_importar(usuario).exists()


def pode_corrigir_balanco(usuario, condominio) -> bool:
    """Diz se a pessoa pode corrigir o detalhamento deste balanço agora.

    Mesma regra de importar, com a mesma resposta do domínio — nunca do que a
    tela mostrou ou escondeu. Sem certeza, a resposta é negar.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_corrigir(usuario).filter(pk=condominio.pk).exists()


def pode_gerenciar_evidencias(usuario, condominio) -> bool:
    """Diz se a pessoa pode anexar, editar ou remover fotos deste balanço.

    A supervisão lê o mês e vê as fotos, mas não as acrescenta nem decide
    exibi-las aos moradores: essa decisão é de quem responde pelo balanço.
    Sem certeza, a resposta é negar.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return (
        condominios_para_gerenciar_evidencias(usuario).filter(pk=condominio.pk).exists()
    )
