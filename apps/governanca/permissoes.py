"""Quem pode ver e manter contratos, garantias e documentos.

Contrato, garantia e documento são obrigações com prazo: quem responde pelo
condomínio decide sobre elas, e a supervisão acompanha e mantém o dia a dia —
cria, corrige, registra o que chegou. O que a supervisão **não** faz é mexer no
catálogo que dá nome às coisas: os tipos continuam sendo de quem responde pelo
condomínio, porque mudar o vocabulário do cadastro muda o entendimento de tudo
que já foi registrado com ele.

A resposta vem sempre do vínculo ativo da pessoa com o condomínio, e nunca do
menu, do formulário ou de um identificador trazido pelo navegador. O menu pode
esconder o que não serve — esconder não é autorizar.

A pergunta "esta pessoa acessa este condomínio?" continua em
``apps.condominios.acesso``, para não haver duas respostas para a mesma pergunta.
"""
from django.db.models import QuerySet

from apps.condominios.acesso import condominios_com_papel
from apps.condominios.constants import Papel
from apps.condominios.models import Condominio

# Administradora e síndica respondem pelo condomínio: decidem sobre contratos,
# garantias e documentos, e mantêm o catálogo de tipos.
PAPEIS_QUE_GERENCIAM = frozenset({Papel.ADMINISTRADORA, Papel.SINDICA})

# A supervisão acompanha e mantém o registro do dia a dia sem responder por ele.
PAPEIS_QUE_OPERAM = PAPEIS_QUE_GERENCIAM | {Papel.SUPERVISORA}

# Quem enxerga o módulo. É a mesma lista de quem opera: aqui não há um nível de
# leitura pura separado do trabalho, como acontece no cadastro básico.
PAPEIS_QUE_VEEM = PAPEIS_QUE_OPERAM


def _com_papel(usuario, papeis: frozenset[str]) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa atua com algum destes papéis.

    A regra — vínculo ativo com um dos papéis, ou o escopo global do
    administrador do sistema — vem de ``apps.condominios.acesso``, em um lugar
    só. Aqui se dizem apenas quais papéis importam para a governança.
    """
    return condominios_com_papel(usuario, papeis)


def condominios_para_ver(usuario) -> QuerySet[Condominio]:
    """Condomínios cujos contratos, garantias e documentos a pessoa consulta."""
    return _com_papel(usuario, PAPEIS_QUE_VEEM)


def condominios_para_operar(usuario) -> QuerySet[Condominio]:
    """Condomínios em que a pessoa pode criar e corrigir estes registros."""
    return _com_papel(usuario, PAPEIS_QUE_OPERAM)


def condominios_para_gerenciar(usuario) -> QuerySet[Condominio]:
    """Condomínios cujos tipos de contrato e de documento a pessoa mantém."""
    return _com_papel(usuario, PAPEIS_QUE_GERENCIAM)


def pode_ver_governanca(usuario, condominio) -> bool:
    """Diz se a pessoa pode consultar contratos, garantias e documentos daqui.

    Recebe o condomínio resolvido. Sem certeza, a resposta é negar.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_ver(usuario).filter(pk=condominio.pk).exists()


def pode_ver_governanca_em_algum(usuario) -> bool:
    """Diz se a pessoa enxerga o módulo em algum condomínio agora.

    É a mesma pergunta que o menu faz, com a mesma regra — quem pergunta é a
    navegação, mas a resposta continua sendo do domínio.
    """
    return condominios_para_ver(usuario).exists()


def pode_operar(usuario, condominio) -> bool:
    """Diz se a pessoa pode criar e corrigir contratos, garantias e documentos.

    É a responsabilidade de acompanhar o registro: quem responde pelo condomínio
    e quem supervisiona. Sem certeza, a resposta é negar.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_operar(usuario).filter(pk=condominio.pk).exists()


def pode_gerenciar_tipos(usuario, condominio) -> bool:
    """Diz se a pessoa pode manter o catálogo de tipos deste condomínio.

    O tipo dá nome ao que se registra. Quem cria o vocabulário é quem responde
    pelo condomínio — a supervisão lê os tipos, mas não os reescreve.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return False

    return condominios_para_gerenciar(usuario).filter(pk=condominio.pk).exists()


def _get_condominio(objeto):
    """O condomínio do registro, ou nada quando ele não foi resolvido."""
    if objeto is None or not getattr(objeto, "pk", None):
        return None
    return getattr(objeto, "condominio", None)


def pode_ver_contrato(usuario, contrato) -> bool:
    """Diz se a pessoa pode consultar este contrato agora."""
    return pode_ver_governanca(usuario, _get_condominio(contrato))


def pode_operar_contrato(usuario, contrato) -> bool:
    """Diz se a pessoa pode criar e corrigir contratos deste condomínio."""
    return pode_operar(usuario, _get_condominio(contrato))


def pode_ver_garantia(usuario, garantia) -> bool:
    """Diz se a pessoa pode consultar esta garantia agora."""
    return pode_ver_governanca(usuario, _get_condominio(garantia))


def pode_operar_garantia(usuario, garantia) -> bool:
    """Diz se a pessoa pode criar e corrigir garantias deste condomínio."""
    return pode_operar(usuario, _get_condominio(garantia))


def pode_ver_documento(usuario, documento) -> bool:
    """Diz se a pessoa pode consultar este documento agora."""
    return pode_ver_governanca(usuario, _get_condominio(documento))


def pode_operar_documento(usuario, documento) -> bool:
    """Diz se a pessoa pode criar e corrigir documentos deste condomínio."""
    return pode_operar(usuario, _get_condominio(documento))
