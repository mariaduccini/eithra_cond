"""Operações do domínio de governança.

Contrato, garantia e documento são obrigações com prazo. Aqui ficam as decisões
que não podem depender da tela: o que pode ser registrado, o que precisa ser
dito junto e o que a gestão não pode deixar pela metade.

Três ideias governam o arquivo:

1. **A garantia diz o que cobre.** Uma garantia em uso não termina uma operação
   sem pelo menos um equipamento, uma manutenção ou um item de compra ligado a
   ela. Isto vive aqui, e não no banco, porque a regra olha para outra tabela —
   o tempo em que ela fica sem cobertura é o tempo da própria transação, nunca
   um estado em que a gestão encontre o registro depois.
2. **Nada muda pela metade.** A entidade e os seus vínculos são gravados numa
   transação só: ou o contrato fica com os equipamentos que ele cobre, ou nada
   é gravado.
3. **O condomínio é o mesmo em toda relação.** Tipo, fornecedor, equipamento,
   local, rotina e item de compra são conferidos contra o condomínio do
   registro. Divergência é recusa, nunca ajuste silencioso.

A situação de vigência não é guardada em lugar nenhum: ela é lida do prazo, na
hora. E o valor do contrato é informação operacional — diz quanto se paga, não
substitui a contabilidade.
"""
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.core.constants import (
    EntidadeImportacaoHistorica,
    ResultadoImportacaoHistorica,
)
from apps.core.models import ItemImportacaoHistorica, VinculoImportacaoHistorica
from apps.estoque.models import ItemCompra

from .models import (
    ContratoEquipamento,
    ContratoFornecedor,
    ContratoLocal,
    ContratoRotina,
    DocumentoContrato,
    DocumentoEquipamento,
    DocumentoFornecedor,
    DocumentoGarantia,
    DocumentoOperacional,
    Garantia,
    GarantiaEquipamento,
    GarantiaItemCompra,
    GarantiaManutencao,
    TipoContrato,
    TipoDocumento,
)


class ErroDeGovernanca(Exception):
    """Uma decisão do domínio de governança que a gestão precisa saber."""


def _primeiro_recado(erro: ValidationError) -> str:
    """A primeira frase humana de um erro de validação do modelo."""
    if hasattr(erro, "message_dict"):
        for mensagens in erro.message_dict.values():
            if mensagens:
                return mensagens[0]
    if erro.messages:
        return erro.messages[0]
    return "Não foi possível guardar o registro."


def _validar(instancia) -> None:
    """Confere a coerência do registro e traduz a recusa para a gestão."""
    try:
        instancia.full_clean()
    except ValidationError as erro:
        raise ErroDeGovernanca(_primeiro_recado(erro)) from erro


def _aplicar(objeto, dados: dict):
    """Aplica ao registro o que o formulário já validou e grava uma vez só."""
    for campo, valor in dados.items():
        setattr(objeto, campo, valor)
    _validar(objeto)
    objeto.save()
    return objeto


def _exigir_condominio(condominio) -> None:
    """Sem condomínio resolvido não há operação: a fronteira é o condomínio."""
    if condominio is None or not getattr(condominio, "pk", None):
        raise ErroDeGovernanca("Não foi possível saber de qual condomínio é isto.")


def _exigir_autor(criado_por) -> None:
    """Sem autor identificado não há cadastro operacional.

    Quem registra um contrato dentro do Eithra é uma pessoa autenticada, e o
    registro guarda quem foi. O único caminho que dispensa autor é a
    incorporação de contrato que já existia antes do sistema.
    """
    if criado_por is None or not getattr(criado_por, "pk", None):
        raise ErroDeGovernanca("Não foi possível saber quem está cadastrando.")


def _condominio_id_do_alvo(alvo):
    """O condomínio a que o alvo pertence.

    O item de compra não tem condomínio próprio: ele pertence ao da compra que
    o trouxe. Perguntar por ele é perguntar pela compra.
    """
    if isinstance(alvo, ItemCompra):
        return alvo.compra.condominio_id
    return getattr(alvo, "condominio_id", None)


def _conferir_mesmo_condominio(alvos, condominio_id, recado: str) -> list:
    """Recusa quando algum alvo escolhido não é do condomínio do registro."""
    escolhidos = [alvo for alvo in alvos if alvo is not None]
    for alvo in escolhidos:
        if _condominio_id_do_alvo(alvo) != condominio_id:
            raise ErroDeGovernanca(recado)
    return escolhidos


def _sincronizar(*, modelo, campo_pai, pai, campo_alvo, alvos) -> None:
    """Ajusta os vínculos do pai para exatamente os alvos escolhidos.

    Acrescenta o que passou a existir e retira o que deixou de existir. O que
    sai é a linha do vínculo, nunca o objeto apontado: o equipamento continua
    no condomínio depois que o contrato deixa de cobri-lo.
    """
    atuais = {
        getattr(vinculo, f"{campo_alvo}_id"): vinculo
        for vinculo in modelo.objects.filter(**{campo_pai: pai})
    }
    desejados = {alvo.pk: alvo for alvo in alvos}

    for chave in atuais.keys() - desejados.keys():
        atuais[chave].delete()
    for chave in desejados.keys() - atuais.keys():
        modelo.objects.create(**{campo_pai: pai, campo_alvo: desejados[chave]})


# -- Tipos de contrato e de documento -------------------------------------------


@transaction.atomic
def criar_tipo_contrato(*, condominio, criado_por, dados: dict) -> TipoContrato:
    """Registra um tipo de contrato novo no condomínio."""
    tipo = TipoContrato(condominio=condominio, criado_por=criado_por, **dados)
    _validar(tipo)
    tipo.save()
    return tipo


@transaction.atomic
def editar_tipo_contrato(*, tipo: TipoContrato, dados: dict) -> TipoContrato:
    """Atualiza o nome e a descrição de um tipo de contrato."""
    return _aplicar(tipo, dados)


@transaction.atomic
def criar_tipo_documento(*, condominio, criado_por, dados: dict) -> TipoDocumento:
    """Registra um tipo de documento novo no condomínio."""
    tipo = TipoDocumento(condominio=condominio, criado_por=criado_por, **dados)
    _validar(tipo)
    tipo.save()
    return tipo


@transaction.atomic
def editar_tipo_documento(*, tipo: TipoDocumento, dados: dict) -> TipoDocumento:
    """Atualiza o tipo de documento, inclusive a sua regra de validade."""
    return _aplicar(tipo, dados)


@transaction.atomic
def definir_ativo_tipo(*, tipo, ativo: bool):
    """Tira um tipo de circulação, ou o devolve, sem apagar o que já o usa."""
    tipo.ativo = bool(ativo)
    tipo.save(update_fields=["ativo", "atualizado_em"])
    return tipo


# -- Contratos ------------------------------------------------------------------


def _gravar_contrato(
    *,
    condominio,
    criado_por,
    dados: dict,
    equipamentos=(),
    locais=(),
    rotinas=(),
) -> ContratoFornecedor:
    """Grava o contrato com o que ele cobre, numa transação só.

    O trabalho é o mesmo venha o contrato do cadastro comum ou do histórico do
    condomínio. Quem decide se o autor é obrigatório é quem chama: o cadastro
    comum exige, a incorporação histórica não tem autor para exigir.
    """
    _exigir_condominio(condominio)
    equipamentos = _conferir_mesmo_condominio(
        equipamentos,
        condominio.pk,
        "Um dos equipamentos escolhidos não é deste condomínio.",
    )
    locais = _conferir_mesmo_condominio(
        locais, condominio.pk, "Um dos locais escolhidos não é deste condomínio."
    )
    rotinas = _conferir_mesmo_condominio(
        rotinas, condominio.pk, "Uma das rotinas escolhidas não é deste condomínio."
    )

    contrato = ContratoFornecedor(
        condominio=condominio, criado_por=criado_por, **dados
    )
    _validar(contrato)
    contrato.save()

    _sincronizar(
        modelo=ContratoEquipamento,
        campo_pai="contrato",
        pai=contrato,
        campo_alvo="equipamento",
        alvos=equipamentos,
    )
    _sincronizar(
        modelo=ContratoLocal,
        campo_pai="contrato",
        pai=contrato,
        campo_alvo="local",
        alvos=locais,
    )
    _sincronizar(
        modelo=ContratoRotina,
        campo_pai="contrato",
        pai=contrato,
        campo_alvo="rotina",
        alvos=rotinas,
    )
    return contrato


@transaction.atomic
def criar_contrato(
    *,
    condominio,
    criado_por,
    dados: dict,
    equipamentos=(),
    locais=(),
    rotinas=(),
) -> ContratoFornecedor:
    """Registra um contrato novo com o que ele cobre, numa transação só.

    Quem cadastra é quem responde pelo registro: sem autor identificado o
    cadastro é recusado. Contrato que já existia antes do Eithra não entra por
    aqui — para esse caso existe a incorporação histórica, que registra a fonte.
    """
    _exigir_autor(criado_por)
    return _gravar_contrato(
        condominio=condominio,
        criado_por=criado_por,
        dados=dados,
        equipamentos=equipamentos,
        locais=locais,
        rotinas=rotinas,
    )


@transaction.atomic
def criar_contrato_historico_importado(
    *,
    condominio,
    item: ItemImportacaoHistorica,
    dados: dict,
    equipamentos=(),
    locais=(),
    rotinas=(),
) -> ContratoFornecedor:
    """Incorpora um contrato que já existia antes de o condomínio usar o Eithra.

    É o único caminho que grava um contrato sem autor, e ele existe porque a
    situação é real: o contrato é anterior ao sistema, e não houve pessoa
    autenticada que o tivesse cadastrado aqui. Inventar um autor seria mentir
    sobre quem fez o quê.

    O que sustenta o registro no lugar do autor é a fonte: o item da carga diz
    de onde o contrato veio, e o vínculo gravado na mesma transação liga os
    dois. Sem item não há incorporação — um contrato sem autor e sem fonte
    ficaria sem explicação nenhuma.

    Nada mais é relaxado: condomínio, fornecedor, prazo e cobertura continuam
    sendo conferidos como no cadastro comum.
    """
    _exigir_condominio(condominio)
    if item is None or not getattr(item, "pk", None):
        raise ErroDeGovernanca("Diga de qual registro do histórico o contrato veio.")
    _conferir_mesmo_condominio(
        [item], condominio.pk, "O registro do histórico não é deste condomínio."
    )

    contrato = _gravar_contrato(
        condominio=condominio,
        criado_por=None,
        dados=dados,
        equipamentos=equipamentos,
        locais=locais,
        rotinas=rotinas,
    )
    VinculoImportacaoHistorica.objects.registrar_vinculo(
        item=item,
        entidade_destino=EntidadeImportacaoHistorica.CONTRATO_FORNECEDOR,
        destino_id=contrato.pk,
        resultado=ResultadoImportacaoHistorica.CRIADO,
    )
    return contrato


@transaction.atomic
def editar_contrato(
    *,
    contrato: ContratoFornecedor,
    dados: dict,
    equipamentos=None,
    locais=None,
    rotinas=None,
) -> ContratoFornecedor:
    """Atualiza o contrato e os objetos que ele cobre.

    Só os conjuntos informados são ajustados: quem edita apenas o cabeçalho não
    mexe, por acidente, nos vínculos que já existiam.
    """
    contrato = _aplicar(contrato, dados)

    if equipamentos is not None:
        _sincronizar(
            modelo=ContratoEquipamento,
            campo_pai="contrato",
            pai=contrato,
            campo_alvo="equipamento",
            alvos=_conferir_mesmo_condominio(
                equipamentos,
                contrato.condominio_id,
                "Um dos equipamentos escolhidos não é deste condomínio.",
            ),
        )
    if locais is not None:
        _sincronizar(
            modelo=ContratoLocal,
            campo_pai="contrato",
            pai=contrato,
            campo_alvo="local",
            alvos=_conferir_mesmo_condominio(
                locais,
                contrato.condominio_id,
                "Um dos locais escolhidos não é deste condomínio.",
            ),
        )
    if rotinas is not None:
        _sincronizar(
            modelo=ContratoRotina,
            campo_pai="contrato",
            pai=contrato,
            campo_alvo="rotina",
            alvos=_conferir_mesmo_condominio(
                rotinas,
                contrato.condominio_id,
                "Uma das rotinas escolhidas não é deste condomínio.",
            ),
        )
    return contrato


@transaction.atomic
def definir_ativo_contrato(*, contrato: ContratoFornecedor, ativo: bool):
    """Encerra a vigência de um contrato, ou a devolve, sem apagar o histórico."""
    contrato.ativo = bool(ativo)
    contrato.save(update_fields=["ativo", "atualizado_em"])
    return contrato


# -- Garantias ------------------------------------------------------------------


def cobertura_da_garantia(garantia: Garantia) -> int:
    """Quantos objetos esta garantia cobre, somando os três tipos de vínculo."""
    if garantia is None or not getattr(garantia, "pk", None):
        return 0
    return (
        GarantiaEquipamento.objects.filter(garantia=garantia).count()
        + GarantiaManutencao.objects.filter(garantia=garantia).count()
        + GarantiaItemCompra.objects.filter(garantia=garantia).count()
    )


def _exigir_cobertura(*, garantia: Garantia) -> None:
    """Recusa uma garantia em uso que não diga o que cobre.

    A garantia pode ficar sem cobertura enquanto a operação está acontecendo —
    ela é criada e os vínculos chegam logo depois, na mesma transação. O que não
    pode é a operação terminar assim: uma garantia ativa que não cobre nada não
    responde à única pergunta que se faz a uma garantia.
    """
    if not garantia.ativo:
        return
    if cobertura_da_garantia(garantia) == 0:
        raise ErroDeGovernanca(
            "Uma garantia em uso precisa cobrir pelo menos um equipamento, uma "
            "manutenção ou um item de compra. Escolha o que ela cobre, ou "
            "deixe a garantia inativa."
        )


@transaction.atomic
def criar_garantia(
    *,
    condominio,
    criado_por,
    dados: dict,
    equipamentos=(),
    manutencoes=(),
    itens_de_compra=(),
) -> Garantia:
    """Registra uma garantia com o que ela cobre, numa transação só."""
    _exigir_condominio(condominio)
    equipamentos = _conferir_mesmo_condominio(
        equipamentos,
        condominio.pk,
        "Um dos equipamentos escolhidos não é deste condomínio.",
    )
    manutencoes = _conferir_mesmo_condominio(
        manutencoes,
        condominio.pk,
        "Uma das manutenções escolhidas não é deste condomínio.",
    )
    itens_de_compra = _conferir_mesmo_condominio(
        itens_de_compra,
        condominio.pk,
        "Um dos itens de compra escolhidos não é deste condomínio.",
    )

    garantia = Garantia(condominio=condominio, criado_por=criado_por, **dados)
    _validar(garantia)
    garantia.save()

    _sincronizar(
        modelo=GarantiaEquipamento,
        campo_pai="garantia",
        pai=garantia,
        campo_alvo="equipamento",
        alvos=equipamentos,
    )
    _sincronizar(
        modelo=GarantiaManutencao,
        campo_pai="garantia",
        pai=garantia,
        campo_alvo="manutencao",
        alvos=manutencoes,
    )
    _sincronizar(
        modelo=GarantiaItemCompra,
        campo_pai="garantia",
        pai=garantia,
        campo_alvo="item_compra",
        alvos=itens_de_compra,
    )

    _exigir_cobertura(garantia=garantia)
    return garantia


@transaction.atomic
def editar_garantia(
    *,
    garantia: Garantia,
    dados: dict,
    equipamentos=None,
    manutencoes=None,
    itens_de_compra=None,
) -> Garantia:
    """Atualiza a garantia e os objetos que ela cobre.

    Só os conjuntos informados são ajustados. Ao final, a garantia continua
    tendo de dizer o que cobre enquanto estiver em uso.
    """
    garantia = _aplicar(garantia, dados)

    if equipamentos is not None:
        _sincronizar(
            modelo=GarantiaEquipamento,
            campo_pai="garantia",
            pai=garantia,
            campo_alvo="equipamento",
            alvos=_conferir_mesmo_condominio(
                equipamentos,
                garantia.condominio_id,
                "Um dos equipamentos escolhidos não é deste condomínio.",
            ),
        )
    if manutencoes is not None:
        _sincronizar(
            modelo=GarantiaManutencao,
            campo_pai="garantia",
            pai=garantia,
            campo_alvo="manutencao",
            alvos=_conferir_mesmo_condominio(
                manutencoes,
                garantia.condominio_id,
                "Uma das manutenções escolhidas não é deste condomínio.",
            ),
        )
    if itens_de_compra is not None:
        _sincronizar(
            modelo=GarantiaItemCompra,
            campo_pai="garantia",
            pai=garantia,
            campo_alvo="item_compra",
            alvos=_conferir_mesmo_condominio(
                itens_de_compra,
                garantia.condominio_id,
                "Um dos itens de compra escolhidos não é deste condomínio.",
            ),
        )

    _exigir_cobertura(garantia=garantia)
    return garantia


@transaction.atomic
def definir_ativo_garantia(*, garantia: Garantia, ativo: bool):
    """Devolve a garantia ao uso somente se ela ainda disser o que cobre."""
    garantia.ativo = bool(ativo)
    garantia.save(update_fields=["ativo", "atualizado_em"])
    _exigir_cobertura(garantia=garantia)
    return garantia


# -- Documentos -----------------------------------------------------------------


@transaction.atomic
def criar_documento(
    *,
    condominio,
    criado_por,
    dados: dict,
    fornecedores=(),
    contratos=(),
    equipamentos=(),
    garantias=(),
) -> DocumentoOperacional:
    """Registra um documento e os contextos em que ele vale, numa transação só."""
    _exigir_condominio(condominio)
    fornecedores = _conferir_mesmo_condominio(
        fornecedores,
        condominio.pk,
        "Um dos fornecedores escolhidos não é deste condomínio.",
    )
    contratos = _conferir_mesmo_condominio(
        contratos, condominio.pk, "Um dos contratos escolhidos não é deste condomínio."
    )
    equipamentos = _conferir_mesmo_condominio(
        equipamentos,
        condominio.pk,
        "Um dos equipamentos escolhidos não é deste condomínio.",
    )
    garantias = _conferir_mesmo_condominio(
        garantias, condominio.pk, "Uma das garantias escolhidas não é deste condomínio."
    )

    documento = DocumentoOperacional(
        condominio=condominio, criado_por=criado_por, **dados
    )
    _validar(documento)
    documento.save()

    _sincronizar(
        modelo=DocumentoFornecedor,
        campo_pai="documento",
        pai=documento,
        campo_alvo="fornecedor",
        alvos=fornecedores,
    )
    _sincronizar(
        modelo=DocumentoContrato,
        campo_pai="documento",
        pai=documento,
        campo_alvo="contrato",
        alvos=contratos,
    )
    _sincronizar(
        modelo=DocumentoEquipamento,
        campo_pai="documento",
        pai=documento,
        campo_alvo="equipamento",
        alvos=equipamentos,
    )
    _sincronizar(
        modelo=DocumentoGarantia,
        campo_pai="documento",
        pai=documento,
        campo_alvo="garantia",
        alvos=garantias,
    )
    return documento


@transaction.atomic
def editar_documento(
    *,
    documento: DocumentoOperacional,
    dados: dict,
    fornecedores=None,
    contratos=None,
    equipamentos=None,
    garantias=None,
) -> DocumentoOperacional:
    """Atualiza o documento e os contextos em que ele vale."""
    documento = _aplicar(documento, dados)

    if fornecedores is not None:
        _sincronizar(
            modelo=DocumentoFornecedor,
            campo_pai="documento",
            pai=documento,
            campo_alvo="fornecedor",
            alvos=_conferir_mesmo_condominio(
                fornecedores,
                documento.condominio_id,
                "Um dos fornecedores escolhidos não é deste condomínio.",
            ),
        )
    if contratos is not None:
        _sincronizar(
            modelo=DocumentoContrato,
            campo_pai="documento",
            pai=documento,
            campo_alvo="contrato",
            alvos=_conferir_mesmo_condominio(
                contratos,
                documento.condominio_id,
                "Um dos contratos escolhidos não é deste condomínio.",
            ),
        )
    if equipamentos is not None:
        _sincronizar(
            modelo=DocumentoEquipamento,
            campo_pai="documento",
            pai=documento,
            campo_alvo="equipamento",
            alvos=_conferir_mesmo_condominio(
                equipamentos,
                documento.condominio_id,
                "Um dos equipamentos escolhidos não é deste condomínio.",
            ),
        )
    if garantias is not None:
        _sincronizar(
            modelo=DocumentoGarantia,
            campo_pai="documento",
            pai=documento,
            campo_alvo="garantia",
            alvos=_conferir_mesmo_condominio(
                garantias,
                documento.condominio_id,
                "Uma das garantias escolhidas não é deste condomínio.",
            ),
        )
    return documento


@transaction.atomic
def definir_ativo_documento(*, documento: DocumentoOperacional, ativo: bool):
    """Tira um documento de circulação, ou o devolve, sem apagar o registro."""
    documento.ativo = bool(ativo)
    documento.save(update_fields=["ativo", "atualizado_em"])
    return documento
