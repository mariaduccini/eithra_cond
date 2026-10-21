"""Quem vê e quem mantém unidades, pessoas e veículos.

O cadastro de comunidade é o retrato de quem vive e circula pelo condomínio, e
por isso fica com quem responde por ele: administração e síndica decidem, e a
supervisão acompanha e mantém o dia a dia — cadastra, corrige, registra os
vínculos. O restante da operação (operacional, portaria) e o próprio morador
não alcançam este módulo.

A resposta vem sempre do vínculo ativo da pessoa com o condomínio, e nunca do
menu, do formulário ou de um identificador trazido pelo navegador. O menu pode
esconder o que não serve — esconder não é autorizar.

A pergunta "esta pessoa acessa este condomínio?" continua em
``apps.condominios.acesso``, para não haver duas respostas para a mesma pergunta.
"""
from django.db.models import QuerySet

from apps.condominios.acesso import condominios_do_usuario
from apps.condominios.constants import Papel
from apps.condominios.models import Condominio

# Administração e síndica respondem pelo condomínio; a supervisão acompanha e
# mantém o registro. Nesta fase não há um vocabulário de tipos que separe as
# duas responsabilidades, então ambas operam o cadastro.
PAPEIS_QUE_OPERAM = frozenset({Papel.ADMIN, Papel.SINDICA, Papel.SUPERVISORA})

# Quem enxerga o módulo é quem opera: não há, aqui, um nível de leitura pura.
PAPEIS_QUE_VEEM = PAPEIS_QUE_OPERAM


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


def condominios_para_ver(usuario) -> QuerySet[Condominio]:
    """Condomínios cujas unidades, pessoas e veículos a pessoa consulta."""
    return _com_papel(usuario, PAPEIS_QUE_VEEM)


def condominios_para_operar(usuario) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa pode cadastrar, corrigir e vincular."""
    return _com_papel(usuario, PAPEIS_QUE_OPERAM)


def pode_ver_comunidade(usuario, condominio) -> bool:
    """Diz se a pessoa pode consultar unidades, pessoas e veículos daqui.

    Recebe o condomínio resolvido. Sem certeza, a resposta é negar.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_ver(usuario).filter(pk=condominio.pk).exists()


def pode_ver_comunidade_em_algum(usuario) -> bool:
    """Diz se a pessoa enxerga o módulo em algum condomínio agora.

    É a mesma pergunta que o menu faz, com a mesma regra — quem pergunta é a
    navegação, mas a resposta continua sendo do domínio.
    """
    return condominios_para_ver(usuario).exists()


def pode_operar(usuario, condominio) -> bool:
    """Diz se a pessoa pode cadastrar, corrigir e vincular neste condomínio.

    Sem certeza, a resposta é negar.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_operar(usuario).filter(pk=condominio.pk).exists()


def _get_condominio(objeto):
    """O condomínio do registro, ou nada quando ele não foi resolvido."""
    if objeto is None or not getattr(objeto, "pk", None):
        return None
    return getattr(objeto, "condominio", None)


def pode_ver_unidade(usuario, unidade) -> bool:
    """Diz se a pessoa pode consultar esta unidade agora."""
    return pode_ver_comunidade(usuario, _get_condominio(unidade))


def pode_operar_unidade(usuario, unidade) -> bool:
    """Diz se a pessoa pode cadastrar e corrigir esta unidade."""
    return pode_operar(usuario, _get_condominio(unidade))


def pode_ver_pessoa(usuario, pessoa) -> bool:
    """Diz se a pessoa pode consultar este cadastro de pessoa agora."""
    return pode_ver_comunidade(usuario, _get_condominio(pessoa))


def pode_operar_pessoa(usuario, pessoa) -> bool:
    """Diz se a pessoa pode cadastrar e corrigir este cadastro de pessoa."""
    return pode_operar(usuario, _get_condominio(pessoa))


def pode_ver_veiculo(usuario, veiculo) -> bool:
    """Diz se a pessoa pode consultar este veículo agora."""
    return pode_ver_comunidade(usuario, _get_condominio(veiculo))


def pode_operar_veiculo(usuario, veiculo) -> bool:
    """Diz se a pessoa pode cadastrar e corrigir este veículo."""
    return pode_operar(usuario, _get_condominio(veiculo))
