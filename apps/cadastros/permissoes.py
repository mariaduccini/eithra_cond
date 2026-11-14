"""Quem pode ver e manter os cadastros básicos operacionais.

A resposta vem sempre do vínculo ativo da pessoa com o condomínio, e nunca do
menu, do formulário ou de um identificador trazido pelo navegador. O menu pode
esconder o que não serve — esconder não é autorizar.

Administradora e síndica mantêm os cadastros operacionais: os locais, os
equipamentos, os fornecedores e os catálogos que os descrevem. A supervisão
enxerga tudo isso em modo de leitura, sem alterar. Operação, portaria e morador
não entram nesta fase — e quem não entra não é avisado de que a tela existe.

A configuração estrutural do condomínio é outra pergunta, e tem outra resposta.
O cadastro do condomínio, a identidade dele, a abertura de um condomínio novo e
a administração dos acessos são da Administradora. O síndico atua na operação
do dia a dia, e não decide sobre a configuração do condomínio.

Aqui mora apenas a regra do domínio. A pergunta "esta pessoa acessa este
condomínio?" continua em ``apps.condominios.acesso``, para não haver duas
respostas para a mesma pergunta.
"""
from django.db.models import QuerySet

from apps.condominios.acesso import condominios_do_usuario
from apps.condominios.constants import Papel
from apps.condominios.models import Condominio

# Administradora e síndica respondem pela operação do condomínio: mantêm os
# cadastros operacionais dele — locais, equipamentos, fornecedores e catálogos.
PAPEIS_QUE_GERENCIAM = frozenset({Papel.ADMINISTRADORA, Papel.SINDICA})

# A supervisão acompanha o cadastro sem respondê-lo: vê, não altera.
PAPEIS_QUE_VEEM = PAPEIS_QUE_GERENCIAM | {Papel.SUPERVISORA}

# A configuração estrutural do condomínio — o cadastro dele, a identidade, a
# abertura de um condomínio novo — é decisão da Administradora. Ser síndica não
# confere configuração: o síndico opera o condomínio, não o reconfigura.
PAPEIS_QUE_CONFIGURAM_CONDOMINIO = frozenset({Papel.ADMINISTRADORA})

# Administrar acessos é a mesma decisão estrutural sobre quem responde pelo
# condomínio: dar, mudar, ativar e retirar vínculo também é da Administradora.
PAPEIS_QUE_ADMINISTRAM_ACESSOS = frozenset({Papel.ADMINISTRADORA})

# Disponibilização de um fornecedor aos moradores: as quatro decisões — editar
# o que o morador lê, indicar, liberar e revalidar — são a mesma responsabilidade
# de responder pelo condomínio, e por isso vivem todas em PAPEIS_QUE_GERENCIAM.
# A supervisão vê os dados públicos junto com o cadastro, mas não decide sobre
# eles: mostrar ao morador é decisão de quem responde pelo condomínio.


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


def _responde_com(usuario, condominio, papeis: frozenset[str]) -> bool:
    """Diz se a pessoa responde por este condomínio com algum destes papéis.

    Recebe o condomínio já resolvido, e nunca aceita o que o navegador diz.
    Sem condomínio resolvido não há o que responder: a resposta é negar.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return _com_papel(usuario, papeis).filter(pk=condominio.pk).exists()


def condominios_para_gerenciar(usuario) -> QuerySet[Condominio]:
    """Condomínios cujos cadastros operacionais a pessoa pode manter agora."""
    return _com_papel(usuario, PAPEIS_QUE_GERENCIAM)


def condominios_para_ver(usuario) -> QuerySet[Condominio]:
    """Condomínios cujos cadastros a pessoa pode consultar agora."""
    return _com_papel(usuario, PAPEIS_QUE_VEEM)


def pode_ver_cadastros(usuario, condominio) -> bool:
    """Diz se a pessoa pode consultar os cadastros deste condomínio agora."""
    return _responde_com(usuario, condominio, PAPEIS_QUE_VEEM)


def pode_ver_cadastros_em_algum(usuario) -> bool:
    """Diz se a pessoa enxerga o módulo em algum condomínio agora.

    É a mesma pergunta que o menu faz, com a mesma regra — quem pergunta é a
    navegação, mas a resposta continua sendo do domínio.
    """
    return condominios_para_ver(usuario).exists()


def pode_criar_condominios(usuario) -> bool:
    """Diz se a pessoa pode abrir novos condomínios no módulo de cadastros.

    Abrir um condomínio é configuração estrutural: nasce um condomínio novo, e
    quem o cria já recebe o vínculo de Administradora. A resposta é a de quem
    configura condomínios, e não a de quem responde pela operação.
    """
    return _com_papel(usuario, PAPEIS_QUE_CONFIGURAM_CONDOMINIO).exists()


def pode_gerenciar_cadastros(usuario, condominio) -> bool:
    """Diz se a pessoa pode manter os cadastros operacionais deste condomínio.

    É a mesma responsabilidade de responder pela operação do condomínio —
    locais, equipamentos, fornecedores e catálogos. Não confere configuração
    estrutural nem administração de acessos.
    """
    return _responde_com(usuario, condominio, PAPEIS_QUE_GERENCIAM)


def pode_configurar_condominio(usuario, condominio) -> bool:
    """Diz se a pessoa pode alterar a configuração estrutural deste condomínio.

    O cadastro do condomínio, a identidade dele e a abertura de um condomínio
    novo são decisão da Administradora. Ser síndico não confere configuração:
    o síndico opera o condomínio, não o reconfigura.
    """
    return _responde_com(usuario, condominio, PAPEIS_QUE_CONFIGURAM_CONDOMINIO)


def pode_administrar_acessos(usuario, condominio) -> bool:
    """Diz se a pessoa pode administrar os acessos deste condomínio agora.

    Dar, mudar, ativar e retirar vínculo é a decisão estrutural sobre quem
    responde pelo condomínio, e é da Administradora. O síndico opera o
    condomínio sem decidir sobre quem entra nele; a supervisão consulta.
    """
    return _responde_com(usuario, condominio, PAPEIS_QUE_ADMINISTRAM_ACESSOS)
