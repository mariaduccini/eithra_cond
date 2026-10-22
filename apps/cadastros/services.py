"""Operações de cadastro do domínio de cadastros básicos.

Cada operação roda em uma transação: ou o cadastro fica coerente, ou nada muda.
Nenhuma regra de tela mora aqui — a view traduz o formulário, e o domínio
grava. Quem entrou com o cadastro e em qual condomínio ele vive são decididos
pela view a partir do vínculo da pessoa, e nunca pelo navegador.

O que os formulários já validaram chega aqui como ``dados``, com os nomes dos
campos do cadastro. Assim não há duas listas de campos para manter em sincronia.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.condominios import services as servicos_de_acesso
from apps.condominios.constants import Papel

from .models import (
    Condominio,
    Equipamento,
    EspecialidadeFornecedor,
    Fornecedor,
    FornecedorEspecialidade,
    LocalOperacional,
    TipoEquipamento,
    TipoLocalOperacional,
)


class ErroDeCadastro(Exception):
    """Uma decisão do domínio de cadastros que a gestão precisa saber."""


def _primeiro_recado(erro: ValidationError) -> str:
    """A primeira frase humana de um erro de validação do cadastro."""
    if hasattr(erro, "message_dict"):
        for mensagens in erro.message_dict.values():
            if mensagens:
                return mensagens[0]
    if erro.messages:
        return erro.messages[0]
    return "Não foi possível guardar o cadastro."


def _validar(instancia) -> None:
    """Confere a coerência do cadastro e traduz a recusa para a gestão."""
    try:
        instancia.full_clean()
    except ValidationError as erro:
        raise ErroDeCadastro(_primeiro_recado(erro)) from erro


def _conferir_mesmo_condominio(alvos, condominio_id, recado: str) -> list:
    """Recusa quando algum alvo escolhido não é do condomínio do cadastro."""
    escolhidos = [alvo for alvo in alvos if alvo is not None]
    for alvo in escolhidos:
        if getattr(alvo, "condominio_id", None) != condominio_id:
            raise ErroDeCadastro(recado)
    return escolhidos


@transaction.atomic
def salvar_condominio(*, condominio: Condominio, dados: dict) -> Condominio:
    """Grava o cadastro do condomínio já validado."""
    return _aplicar(condominio, dados)


@transaction.atomic
def criar_condominio(*, dados: dict, criado_por) -> Condominio:
    """Cria um condomínio e já entrega ao criador o primeiro acesso de gestão.

    O condomínio não pode nascer órfão: quem o criou precisa continuar
    enxergando e mantendo esse cadastro na sequência. O primeiro vínculo nasce
    como administração, usando a própria identidade já autenticada.
    """
    condominio = Condominio()
    _aplicar(condominio, dados, validar=True)
    servicos_de_acesso.adicionar_acesso(
        condominio=condominio,
        email=criado_por.email,
        nome_completo=criado_por.full_name,
        papel=Papel.ADMIN,
        executor=criado_por,
    )
    return condominio


# -- Tipos: as listas que a gestão mantém ---------------------------------------
#
# Cada tipo tem a mesma forma e a mesma operação. Escrevê-las por extenso é
# deliberado: quem lê o serviço precisa saber de qual catálogo se trata sem
# subir dois arquivos.


@transaction.atomic
def criar_tipo_local(
    *, condominio: Condominio, criado_por, dados: dict
) -> TipoLocalOperacional:
    """Registra um tipo de local novo no condomínio."""
    tipo = TipoLocalOperacional(condominio=condominio, criado_por=criado_por)
    return _aplicar(tipo, dados, validar=True)


@transaction.atomic
def editar_tipo_local(*, tipo: TipoLocalOperacional, dados: dict) -> TipoLocalOperacional:
    """Atualiza o nome e a descrição de um tipo de local."""
    return _aplicar(tipo, dados, validar=True)


@transaction.atomic
def criar_tipo_equipamento(
    *, condominio: Condominio, criado_por, dados: dict
) -> TipoEquipamento:
    """Registra um tipo de equipamento novo no condomínio."""
    tipo = TipoEquipamento(condominio=condominio, criado_por=criado_por)
    return _aplicar(tipo, dados, validar=True)


@transaction.atomic
def editar_tipo_equipamento(
    *, tipo: TipoEquipamento, dados: dict
) -> TipoEquipamento:
    """Atualiza o nome e a descrição de um tipo de equipamento."""
    return _aplicar(tipo, dados, validar=True)


@transaction.atomic
def criar_especialidade(
    *, condominio: Condominio, criado_por, dados: dict
) -> EspecialidadeFornecedor:
    """Registra uma especialidade nova no condomínio."""
    especialidade = EspecialidadeFornecedor(condominio=condominio, criado_por=criado_por)
    return _aplicar(especialidade, dados, validar=True)


@transaction.atomic
def editar_especialidade(
    *, especialidade: EspecialidadeFornecedor, dados: dict
) -> EspecialidadeFornecedor:
    """Atualiza o nome e a descrição de uma especialidade."""
    return _aplicar(especialidade, dados, validar=True)


@transaction.atomic
def criar_local(
    *, condominio: Condominio, criado_por, dados: dict
) -> LocalOperacional:
    """Registra um local novo no condomínio."""
    return LocalOperacional.objects.create(
        condominio=condominio, criado_por=criado_por, **dados
    )


@transaction.atomic
def editar_local(*, local: LocalOperacional, dados: dict) -> LocalOperacional:
    """Atualiza o cadastro do local."""
    return _aplicar(local, dados)


@transaction.atomic
def criar_equipamento(
    *, condominio: Condominio, criado_por, dados: dict
) -> Equipamento:
    """Registra um equipamento novo no condomínio."""
    return Equipamento.objects.create(
        condominio=condominio, criado_por=criado_por, **dados
    )


@transaction.atomic
def editar_equipamento(*, equipamento: Equipamento, dados: dict) -> Equipamento:
    """Atualiza o cadastro do equipamento."""
    return _aplicar(equipamento, dados)


@transaction.atomic
def criar_fornecedor(
    *, condominio: Condominio, criado_por, dados: dict, especialidades=()
) -> Fornecedor:
    """Registra um fornecedor novo, com o que ele atende, numa transação só.

    Quem cadastra já responde pela liberação aos moradores: se o cadastro nasce
    disponível, é esta pessoa que assina essa decisão.
    """
    especialidades = _conferir_mesmo_condominio(
        especialidades,
        condominio.pk,
        "Uma das especialidades escolhidas não é deste condomínio.",
    )
    fornecedor = Fornecedor(condominio=condominio, criado_por=criado_por)
    return _gravar_fornecedor(
        fornecedor,
        dados=dados,
        especialidades=especialidades,
        disponivel_antes=False,
        responsavel=criado_por,
    )


@transaction.atomic
def editar_fornecedor(
    *, fornecedor: Fornecedor, dados: dict, especialidades=(), responsavel=None
) -> Fornecedor:
    """Atualiza o cadastro do fornecedor e o que ele atende.

    ``responsavel`` é quem responde pela liberação aos moradores, quando ela
    acontece nesta gravação: a pessoa que está na tela.
    """
    especialidades = _conferir_mesmo_condominio(
        especialidades,
        fornecedor.condominio_id,
        "Uma das especialidades escolhidas não é deste condomínio.",
    )
    # O "antes" é lido do banco, e não do objeto recebido: a tela valida por um
    # ModelForm, que escreve os campos limpos na própria instância antes de o
    # domínio ser chamado. Ler o objeto em memória faria a passagem para
    # "disponível" parecer que já existia, e a liberação ficaria sem autoria.
    disponivel_antes = bool(
        Fornecedor.objects.filter(pk=fornecedor.pk)
        .values_list("ativo_para_moradores", flat=True)
        .first()
    )
    return _gravar_fornecedor(
        fornecedor,
        dados=dados,
        especialidades=especialidades,
        disponivel_antes=disponivel_antes,
        responsavel=responsavel,
    )


def _gravar_fornecedor(
    fornecedor: Fornecedor,
    *,
    dados: dict,
    especialidades,
    disponivel_antes: bool,
    responsavel,
) -> Fornecedor:
    """Aplica o que veio, confere a regra da indicação, assina e grava uma vez.

    A conferência é a do domínio — a mesma frase que a tela mostra —, e não uma
    segunda lista de regras escrita aqui. A assinatura da liberação acompanha a
    passagem para "disponível", e só ela: corrigir a descrição meses depois não
    pode reiniciar, em silêncio, o prazo de revalidação.
    """
    for campo, valor in dados.items():
        setattr(fornecedor, campo, valor)
    _validar(fornecedor)
    if fornecedor.ativo_para_moradores and not disponivel_antes:
        _assinar_liberacao(fornecedor, responsavel=responsavel)
    fornecedor.save()
    _sincronizar_especialidades(fornecedor=fornecedor, especialidades=especialidades)
    return fornecedor


def _assinar_liberacao(fornecedor: Fornecedor, *, responsavel) -> None:
    """Registra quem liberou o fornecedor aos moradores, e quando.

    Sem responsável identificado não há liberação: uma indicação sem autoria não
    pode ser revista, e é justamente a possibilidade de revisar que sustenta o
    que o condomínio mostra aos moradores.
    """
    if responsavel is None:
        raise ErroDeCadastro(
            "A liberação aos moradores registra quem respondeu por ela."
        )
    fornecedor.validado_em = timezone.now()
    fornecedor.validado_por = responsavel


def _sincronizar_especialidades(*, fornecedor: Fornecedor, especialidades) -> None:
    """Ajusta o que o fornecedor atende para exatamente o que foi escolhido.

    Acrescenta o que passou a existir e retira o vínculo que deixou de existir.
    O que sai é a linha do vínculo, nunca a especialidade: ela continua no
    condomínio depois que este fornecedor deixa de atendê-la.
    """
    atuais = {
        vinculo.especialidade_id: vinculo
        for vinculo in FornecedorEspecialidade.objects.filter(fornecedor=fornecedor)
    }
    desejadas = {especialidade.pk: especialidade for especialidade in especialidades}

    for chave in atuais.keys() - desejadas.keys():
        atuais[chave].delete()
    for chave in desejadas.keys() - atuais.keys():
        FornecedorEspecialidade.objects.create(
            fornecedor=fornecedor, especialidade=desejadas[chave]
        )


@transaction.atomic
def definir_ativo(*, objeto, ativo: bool):
    """Ativa ou desativa um cadastro.

    Desativar é a forma de tirar algo de circulação sem apagar o cadastro: o que
    aponta para ele continua legível.
    """
    objeto.ativo = bool(ativo)
    objeto.save(update_fields=["ativo", "atualizado_em"])
    return objeto


def _aplicar(objeto, dados: dict, *, validar: bool = False):
    """Aplica ao cadastro o que o formulário já validou e grava uma vez só.

    A conferência do domínio é opcional porque cada cadastro já traz a sua: o
    formulário valida o que é dele, e o banco barra o que não deveria entrar. Os
    catálogos de tipo pedem a conferência explícita para que o nome repetido
    chegue à tela como uma frase, e não como um erro do banco.
    """
    for campo, valor in dados.items():
        setattr(objeto, campo, valor)
    if validar:
        _validar(objeto)
    objeto.save()
    return objeto
