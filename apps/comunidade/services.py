"""Operações do domínio de comunidade.

Cada operação roda em uma transação: ou o cadastro e os seus vínculos ficam
coerentes, ou nada muda. Nenhuma regra de tela mora aqui — a view traduz o
formulário, e o domínio grava. Quem entrou com o cadastro e em qual condomínio
ele vive são decididos pela view a partir do vínculo da pessoa, e nunca pelo
navegador.

Três regras atravessam o arquivo. Nada de um condomínio aparece em outro: a
coerência entre pessoa, unidade e veículo é conferida antes de gravar. O
histórico não se reescreve: mudar de unidade encerra um vínculo e cria outro,
nunca apaga o anterior. E o mesmo período não se registra duas vezes: duas
anotações do mesmo fato com períodos que se cruzam são engano operacional, não
duas verdades. Cadastro com vínculo em vigor não é desativado em silêncio —
quem for desativar precisa antes encerrar o que está em aberto.

Um vínculo diz a relação entre duas coisas, e não uma autorização: o papel de
uma pessoa na unidade não responde se ela pode entrar no condomínio. Essa é
outra pergunta, de outra fase.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .constants import TipoVinculoPessoaUnidade
from .models import (
    Pessoa,
    Unidade,
    Veiculo,
    VinculoPessoaUnidade,
    VinculoVeiculoPessoa,
    VinculoVeiculoUnidade,
)


class ErroDeComunidade(Exception):
    """Uma decisão do domínio de comunidade que a gestão precisa saber."""


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
        raise ErroDeComunidade(_primeiro_recado(erro)) from erro


def _conferir_mesmo_condominio(alvos, condominio_id, recado: str) -> None:
    """Recusa quando algum alvo escolhido não é do condomínio do cadastro."""
    for alvo in alvos:
        if alvo is None:
            continue
        if getattr(alvo, "condominio_id", None) != condominio_id:
            raise ErroDeComunidade(recado)


def _aplicar(objeto, dados: dict):
    """Aplica ao cadastro o que veio, confere a coerência e grava uma vez só."""
    for campo, valor in dados.items():
        setattr(objeto, campo, valor)
    _validar(objeto)
    objeto.save()
    return objeto


def _recusar_sobreposicao(ja_registrados, *, data_inicio, data_fim, recado: str) -> None:
    """Recusa um período que se cruza com outro já registrado para o mesmo fato.

    O fim é inclusivo dos dois lados: um vínculo que termina em 31/03 e outro
    que começa em 31/03 disputam o mesmo dia, e é isso que a regra existe para
    impedir. Períodos vizinhos, um terminando em 30/03 e o outro começando em
    31/03, não se tocam.

    O conjunto recebido já chega restrito ao fato em questão — mesma pessoa e
    mesma unidade, por exemplo. Papéis diferentes na mesma unidade, unidades
    diferentes da mesma pessoa e pessoas diferentes na mesma unidade são fatos
    distintos, e é por isso que não aparecem aqui.
    """
    if data_inicio is None:
        return

    candidatos = ja_registrados
    if data_fim is not None:
        # Um vínculo que só começa depois do fim procurado não pode cruzar.
        candidatos = candidatos.filter(data_inicio__lte=data_fim)
    conflito = candidatos.filter(
        Q(data_fim__isnull=True) | Q(data_fim__gte=data_inicio)
    ).exists()
    if conflito:
        raise ErroDeComunidade(recado)


# -- Unidades ------------------------------------------------------------------


@transaction.atomic
def criar_unidade(*, condominio, criado_por, dados: dict) -> Unidade:
    """Registra uma unidade nova no condomínio."""
    unidade = Unidade(condominio=condominio, criado_por=criado_por)
    return _aplicar(unidade, dados)


@transaction.atomic
def editar_unidade(*, unidade: Unidade, dados: dict) -> Unidade:
    """Atualiza o cadastro da unidade."""
    return _aplicar(unidade, dados)


@transaction.atomic
def inativar_unidade(*, unidade: Unidade) -> Unidade:
    """Tira a unidade de circulação sem apagar o cadastro.

    Uma unidade com vínculo em vigor não é desativada em silêncio: quem for
    desativá-la precisa antes encerrar quem ainda está ligado a ela.
    """
    if _unidade_tem_vinculo_vigente(unidade):
        raise ErroDeComunidade(
            "Esta unidade tem vínculos em vigor. Encerre-os antes de desativá-la."
        )
    unidade.ativa = False
    unidade.save(update_fields=["ativa", "atualizado_em"])
    return unidade


@transaction.atomic
def reativar_unidade(*, unidade: Unidade) -> Unidade:
    """Devolve a unidade à operação."""
    unidade.ativa = True
    unidade.save(update_fields=["ativa", "atualizado_em"])
    return unidade


def _unidade_tem_vinculo_vigente(unidade: Unidade) -> bool:
    """Diz se a unidade ainda sustenta um vínculo vigente, de pessoa ou veículo."""
    if not unidade.pk:
        return False
    return (
        VinculoPessoaUnidade.objects.da_unidade(unidade).vigentes().exists()
        or VinculoVeiculoUnidade.objects.da_unidade(unidade).vigentes().exists()
    )


# -- Pessoas -------------------------------------------------------------------


@transaction.atomic
def criar_pessoa(*, condominio, criado_por, dados: dict) -> Pessoa:
    """Registra uma pessoa nova no condomínio."""
    pessoa = Pessoa(condominio=condominio, criado_por=criado_por)
    return _aplicar(pessoa, dados)


@transaction.atomic
def editar_pessoa(*, pessoa: Pessoa, dados: dict) -> Pessoa:
    """Atualiza o cadastro da pessoa."""
    return _aplicar(pessoa, dados)


@transaction.atomic
def inativar_pessoa(*, pessoa: Pessoa) -> Pessoa:
    """Tira a pessoa de circulação sem apagar o cadastro.

    Uma pessoa com vínculo em vigor não é desativada em silêncio: quem for
    desativá-la precisa antes encerrar os vínculos que ela ainda sustenta.
    """
    if _pessoa_tem_vinculo_vigente(pessoa):
        raise ErroDeComunidade(
            "Esta pessoa tem vínculos em vigor. Encerre-os antes de desativá-la."
        )
    pessoa.ativa = False
    pessoa.save(update_fields=["ativa", "atualizado_em"])
    return pessoa


@transaction.atomic
def reativar_pessoa(*, pessoa: Pessoa) -> Pessoa:
    """Devolve a pessoa à operação."""
    pessoa.ativa = True
    pessoa.save(update_fields=["ativa", "atualizado_em"])
    return pessoa


def _pessoa_tem_vinculo_vigente(pessoa: Pessoa) -> bool:
    """Diz se a pessoa ainda sustenta um vínculo vigente, com unidade ou veículo."""
    if not pessoa.pk:
        return False
    return (
        VinculoPessoaUnidade.objects.da_pessoa(pessoa).vigentes().exists()
        or VinculoVeiculoPessoa.objects.da_pessoa(pessoa).vigentes().exists()
    )


# -- Veículos ------------------------------------------------------------------


@transaction.atomic
def criar_veiculo(*, condominio, criado_por, dados: dict) -> Veiculo:
    """Registra um veículo novo no condomínio."""
    veiculo = Veiculo(condominio=condominio, criado_por=criado_por)
    return _aplicar(veiculo, dados)


@transaction.atomic
def editar_veiculo(*, veiculo: Veiculo, dados: dict) -> Veiculo:
    """Atualiza o cadastro do veículo."""
    return _aplicar(veiculo, dados)


@transaction.atomic
def inativar_veiculo(*, veiculo: Veiculo) -> Veiculo:
    """Tira o veículo de circulação sem apagar o cadastro.

    Um veículo com vínculo em vigor não é desativado em silêncio: quem for
    desativá-lo precisa antes encerrar os vínculos que ele ainda sustenta.
    """
    if _veiculo_tem_vinculo_vigente(veiculo):
        raise ErroDeComunidade(
            "Este veículo tem vínculos em vigor. Encerre-os antes de desativá-lo."
        )
    veiculo.ativo = False
    veiculo.save(update_fields=["ativo", "atualizado_em"])
    return veiculo


@transaction.atomic
def reativar_veiculo(*, veiculo: Veiculo) -> Veiculo:
    """Devolve o veículo à circulação."""
    veiculo.ativo = True
    veiculo.save(update_fields=["ativo", "atualizado_em"])
    return veiculo


def _veiculo_tem_vinculo_vigente(veiculo: Veiculo) -> bool:
    """Diz se o veículo ainda sustenta um vínculo vigente, com pessoa ou unidade."""
    if not veiculo.pk:
        return False
    return (
        VinculoVeiculoPessoa.objects.do_veiculo(veiculo).vigentes().exists()
        or VinculoVeiculoUnidade.objects.do_veiculo(veiculo).vigentes().exists()
    )


# -- Vínculos entre pessoa e unidade -------------------------------------------


@transaction.atomic
def criar_vinculo_pessoa_unidade(
    *,
    condominio,
    criado_por,
    pessoa: Pessoa,
    unidade: Unidade,
    tipo_vinculo: str = TipoVinculoPessoaUnidade.MORADOR,
    data_inicio,
    data_fim=None,
    observacao_interna: str = "",
) -> VinculoPessoaUnidade:
    """Liga uma pessoa a uma unidade a partir de uma data.

    A pessoa e a unidade precisam ser do mesmo condomínio — o vínculo não
    atravessa condomínios.

    A mesma pessoa pode ter mais de um papel na mesma unidade ao mesmo tempo:
    ser familiar e funcionária da casa são relações diferentes, e as duas
    cabem. O que não cabe é o mesmo papel duas vezes no mesmo período — isso
    não são dois vínculos, é o mesmo anotado duas vezes.
    """
    _conferir_mesmo_condominio(
        (pessoa, unidade),
        condominio.pk,
        "A pessoa ou a unidade escolhida não é deste condomínio.",
    )
    _recusar_sobreposicao(
        VinculoPessoaUnidade.objects.filter(
            pessoa=pessoa, unidade=unidade, tipo_vinculo=tipo_vinculo
        ),
        data_inicio=data_inicio,
        data_fim=data_fim,
        recado=(
            "Esta pessoa já tem este papel nesta unidade em um período que se "
            "cruza com o informado."
        ),
    )
    vinculo = VinculoPessoaUnidade(
        condominio=condominio,
        criado_por=criado_por,
        pessoa=pessoa,
        unidade=unidade,
        tipo_vinculo=tipo_vinculo,
        data_inicio=data_inicio,
        data_fim=data_fim,
        observacao_interna=observacao_interna,
    )
    return _aplicar(vinculo, {})


@transaction.atomic
def encerrar_vinculo_pessoa_unidade(
    *, vinculo: VinculoPessoaUnidade, data_fim=None
) -> VinculoPessoaUnidade:
    """Marca o fim de um vínculo, sem apagar o que já aconteceu.

    Sem data informada, o vínculo termina hoje.
    """
    vinculo.data_fim = data_fim or timezone.localdate()
    _validar(vinculo)
    vinculo.save(update_fields=["data_fim", "atualizado_em"])
    return vinculo


@transaction.atomic
def transferir_pessoa_unidade(
    *,
    vinculo: VinculoPessoaUnidade,
    para_unidade: Unidade,
    data=None,
    criado_por=None,
) -> VinculoPessoaUnidade:
    """Muda a pessoa de unidade encerrando aquele vínculo e abrindo o novo.

    Termina só o vínculo que a gestão mandou mudar: a mesma pessoa pode ter
    outros papéis na unidade de origem, e nenhum deles é tocado por causa desta
    mudança. O vínculo anterior não é editado nem apagado — ele ganha data de
    fim, e o novo nasce na mesma transação, com o mesmo papel e na mesma data
    em que o antigo terminou. É assim que a mudança de unidade continua legível
    no histórico.
    """
    _conferir_mesmo_condominio(
        (para_unidade,),
        vinculo.condominio_id,
        "A unidade escolhida não é deste condomínio.",
    )
    if para_unidade.pk == vinculo.unidade_id:
        raise ErroDeComunidade(
            "A unidade escolhida é a mesma em que a pessoa já está."
        )

    data = data or timezone.localdate()
    encerrar_vinculo_pessoa_unidade(vinculo=vinculo, data_fim=data)
    return criar_vinculo_pessoa_unidade(
        condominio=vinculo.condominio,
        criado_por=criado_por,
        pessoa=vinculo.pessoa,
        unidade=para_unidade,
        tipo_vinculo=vinculo.tipo_vinculo,
        data_inicio=data,
    )


# -- Vínculos entre veículo e pessoa -------------------------------------------


@transaction.atomic
def vincular_veiculo_pessoa(
    *,
    condominio,
    criado_por,
    veiculo: Veiculo,
    pessoa: Pessoa,
    data_inicio,
    data_fim=None,
    observacao: str = "",
) -> VinculoVeiculoPessoa:
    """Registra quem utiliza um veículo, a partir de uma data.

    O mesmo veículo pode ser utilizado por várias pessoas ao mesmo tempo — o
    carro da casa é dirigido por quem mora nela. O que não se registra duas
    vezes é o mesmo uso, pela mesma pessoa, em períodos que se cruzam.
    """
    _conferir_mesmo_condominio(
        (veiculo, pessoa),
        condominio.pk,
        "O veículo ou a pessoa escolhida não é deste condomínio.",
    )
    _recusar_sobreposicao(
        VinculoVeiculoPessoa.objects.filter(veiculo=veiculo, pessoa=pessoa),
        data_inicio=data_inicio,
        data_fim=data_fim,
        recado=(
            "Este veículo já está relacionado a esta pessoa em um período que "
            "se cruza com o informado."
        ),
    )
    vinculo = VinculoVeiculoPessoa(
        condominio=condominio,
        criado_por=criado_por,
        veiculo=veiculo,
        pessoa=pessoa,
        data_inicio=data_inicio,
        data_fim=data_fim,
        observacao=observacao,
    )
    return _aplicar(vinculo, {})


@transaction.atomic
def encerrar_vinculo_veiculo_pessoa(
    *, vinculo: VinculoVeiculoPessoa, data_fim=None
) -> VinculoVeiculoPessoa:
    """Marca o fim do uso de um veículo por uma pessoa."""
    vinculo.data_fim = data_fim or timezone.localdate()
    _validar(vinculo)
    vinculo.save(update_fields=["data_fim", "atualizado_em"])
    return vinculo


# -- Vínculos entre veículo e unidade ------------------------------------------


@transaction.atomic
def vincular_veiculo_unidade(
    *,
    condominio,
    criado_por,
    veiculo: Veiculo,
    unidade: Unidade,
    data_inicio,
    data_fim=None,
    observacao: str = "",
) -> VinculoVeiculoUnidade:
    """Relaciona um veículo a uma unidade, a partir de uma data.

    É uma pergunta diferente de "quem utiliza este veículo": registrar uma não
    responde pela outra.

    Um veículo pode estar relacionado a mais de uma unidade ao mesmo tempo — o
    carro fica em uma garagem e é usado por quem mora em outra. O que não se
    registra duas vezes é a mesma relação, em períodos que se cruzam.
    """
    _conferir_mesmo_condominio(
        (veiculo, unidade),
        condominio.pk,
        "O veículo ou a unidade escolhida não é deste condomínio.",
    )
    _recusar_sobreposicao(
        VinculoVeiculoUnidade.objects.filter(veiculo=veiculo, unidade=unidade),
        data_inicio=data_inicio,
        data_fim=data_fim,
        recado=(
            "Este veículo já está relacionado a esta unidade em um período que "
            "se cruza com o informado."
        ),
    )
    vinculo = VinculoVeiculoUnidade(
        condominio=condominio,
        criado_por=criado_por,
        veiculo=veiculo,
        unidade=unidade,
        data_inicio=data_inicio,
        data_fim=data_fim,
        observacao=observacao,
    )
    return _aplicar(vinculo, {})


@transaction.atomic
def encerrar_vinculo_veiculo_unidade(
    *, vinculo: VinculoVeiculoUnidade, data_fim=None
) -> VinculoVeiculoUnidade:
    """Marca o fim da relação de um veículo com uma unidade."""
    vinculo.data_fim = data_fim or timezone.localdate()
    _validar(vinculo)
    vinculo.save(update_fields=["data_fim", "atualizado_em"])
    return vinculo
