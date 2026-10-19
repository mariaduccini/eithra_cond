"""Operações do domínio de estoque e compras operacionais.

Aqui moram as decisões que não podem depender da tela: quanto existe de um item,
se há saldo para uma saída, o que acontece quando uma compra é confirmada e o que
muda quando ela é cancelada. A view traduz o que a gestão pediu; o domínio
decide, aplica e recusa.

Duas ideias governam o arquivo. A primeira: **o saldo não é guardado**, é lido —
por isso toda saída trava o item, recalcula o saldo dentro da transação e só
depois decide, para que dois pedidos simultâneos não consumam o mesmo material
duas vezes. A segunda: **a compra não é o consumo**. Ela é o fato econômico;
confirmá-la aplica o destino de cada linha e gera os movimentos correspondentes,
numa transação só — ou tudo entra, ou nada entra.

Nada aqui gera despesa, altera balanço ou cria lançamento financeiro. Os valores
são operacionais: dizem quanto se pagou, não substituem a contabilidade.
"""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from .constants import (
    TIPOS_DE_ENTRADA,
    TIPOS_DE_SAIDA,
    TIPOS_DE_MOVIMENTO_MANUAL,
    TIPOS_QUE_EXIGEM_MOTIVO,
    DestinoDoItem,
    StatusCompra,
    TipoMovimentacaoEstoque,
    UnidadeMedida,
    direcao_do_tipo,
)
from .models import Compra, ItemCompra, ItemEstoque, MovimentacaoEstoque

ZERO = Decimal("0")


class ErroDeEstoque(Exception):
    """Uma decisão do domínio sobre estoque ou compra que a gestão precisa saber."""


def _texto(valor: str | None) -> str:
    """Colapsa espaços repetidos e devolve string vazia quando não há conteúdo."""
    return " ".join((valor or "").split())


def _texto_longo(valor: str | None) -> str:
    """Preserva os parágrafos de um texto livre, sem espaços nas pontas."""
    return (valor or "").strip()


def _id_de(objeto):
    """A chave de um registro relacionado, ou nada quando não há."""
    return getattr(objeto, "pk", None)


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
        raise ErroDeEstoque(_primeiro_recado(erro)) from erro


def _quantidade(valor) -> Decimal:
    """A quantidade como número, já com três casas. Sem número, é erro."""
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        raise ErroDeEstoque("Informe uma quantidade válida.") from None
    return numero.quantize(Decimal("0.001"))


def _dinheiro(valor) -> Decimal:
    """O valor como dinheiro, já arredondado ao centavo."""
    if valor is None:
        return ZERO
    return Decimal(str(valor)).quantize(Decimal("0.01"))


def _item_do_condominio(item, condominio) -> ItemEstoque:
    """O item, garantindo que ele é do condomínio em uso. Sem isso, é erro."""
    if item is None or not getattr(item, "pk", None):
        raise ErroDeEstoque("Escolha o item de estoque.")
    if condominio is None or not getattr(condominio, "pk", None):
        raise ErroDeEstoque("Não foi possível saber de qual condomínio é o item.")
    if item.condominio_id != condominio.pk:
        raise ErroDeEstoque("O item escolhido não é deste condomínio.")
    return item


def _bloquear_item(item) -> ItemEstoque:
    """Trava a linha do item até o fim da transação.

    Enquanto uma saída está sendo decidida, ninguém mais mexe no mesmo item: o
    saldo lido logo depois não pode envelhecer no meio do caminho.
    """
    return ItemEstoque.objects.select_for_update().get(pk=item.pk)


def saldo_do_item(item) -> Decimal:
    """Quanto há deste item agora: entradas menos saídas.

    É leitura pura — o saldo não é campo, é a consequência das movimentações. Os
    conjuntos de entrada e saída vêm de ``constants``, a mesma fonte que decide
    o sentido de cada movimento.
    """
    if item is None or not getattr(item, "pk", None):
        return ZERO

    agregado = MovimentacaoEstoque.objects.do_item(item).aggregate(
        entradas=models.Sum("quantidade", filter=models.Q(tipo__in=TIPOS_DE_ENTRADA)),
        saidas=models.Sum("quantidade", filter=models.Q(tipo__in=TIPOS_DE_SAIDA)),
    )
    return (agregado["entradas"] or ZERO) - (agregado["saidas"] or ZERO)


def itens_com_saldo(condominio, *, ativos=True):
    """Itens do condomínio já com o saldo anotado, para leitura na tela."""
    consulta = ItemEstoque.objects.do_condominio(condominio).com_saldo()
    if ativos:
        consulta = consulta.ativos()
    return consulta


@transaction.atomic
def criar_item(
    *,
    condominio,
    criado_por,
    nome: str,
    descricao: str = "",
    categoria_texto: str = "",
    unidade: str = UnidadeMedida.UNIDADE,
    local_armazenamento=None,
    estoque_minimo=None,
    item_critico: bool = False,
    controla_validade: bool = False,
) -> ItemEstoque:
    """Cadastra o item, sem inventar estoque.

    Cadastrar é dizer que o item existe e passa a ser controlado. Quanto já há
    não vem daqui: para registrar o que existe, usa-se um ajuste de entrada, com
    motivo. Assim o saldo inicial também tem explicação.
    """
    item = ItemEstoque(
        condominio=condominio,
        nome=_texto(nome),
        descricao=_texto_longo(descricao),
        categoria_texto=_texto(categoria_texto),
        unidade=unidade,
        local_armazenamento=local_armazenamento,
        estoque_minimo=ZERO if estoque_minimo in (None, "") else estoque_minimo,
        item_critico=bool(item_critico),
        controla_validade=bool(controla_validade),
        criado_por=criado_por,
    )
    _validar(item)
    item.save()
    return item


@transaction.atomic
def editar_item(
    *,
    item: ItemEstoque,
    nome: str,
    descricao: str = "",
    categoria_texto: str = "",
    unidade: str = UnidadeMedida.UNIDADE,
    local_armazenamento=None,
    estoque_minimo=None,
    item_critico: bool = False,
    controla_validade: bool = False,
) -> ItemEstoque:
    """Atualiza o cadastro do item. O saldo não entra aqui — ele vem dos fatos."""
    item.nome = _texto(nome)
    item.descricao = _texto_longo(descricao)
    item.categoria_texto = _texto(categoria_texto)
    item.unidade = unidade
    item.local_armazenamento = local_armazenamento
    item.estoque_minimo = ZERO if estoque_minimo in (None, "") else estoque_minimo
    item.item_critico = bool(item_critico)
    item.controla_validade = bool(controla_validade)

    _validar(item)
    item.save()
    return item


@transaction.atomic
def definir_item_ativo(*, item: ItemEstoque, ativo: bool) -> ItemEstoque:
    """Retira o item da operação, ou o devolve, sem apagar o que já aconteceu."""
    ativo = bool(ativo)
    if item.ativo == ativo:
        return item

    item.ativo = ativo
    item.save(update_fields=["ativo", "atualizado_em"])
    return item


def _criar_movimento(
    *,
    condominio,
    item,
    usuario,
    tipo,
    quantidade,
    motivo: str = "",
    compra_item=None,
    manutencao=None,
) -> MovimentacaoEstoque:
    """Grava um movimento já validado. Não decide saldo: só registra o fato."""
    movimento = MovimentacaoEstoque(
        condominio=condominio,
        item=item,
        tipo=tipo,
        quantidade=quantidade,
        compra_item=compra_item,
        manutencao=manutencao,
        motivo=_texto(motivo),
        usuario=usuario,
    )
    _validar(movimento)
    movimento.save()
    return movimento


def _conferir_saldo(item, tipo, quantidade) -> None:
    """Recusa a saída que não cabe no que existe. Entrada sempre cabe.

    O saldo é lido agora, dentro da transação e com o item travado — nunca o que
    estava na tela quando a pessoa abriu o formulário.
    """
    if direcao_do_tipo(tipo) != "saida":
        return

    saldo = saldo_do_item(item)
    if quantidade > saldo:
        raise ErroDeEstoque(
            f"Não há saldo suficiente de «{item.nome}». "
            f"Existem {saldo:.3f} {item.get_unidade_display().lower()} "
            "no estoque agora."
        )


@transaction.atomic
def registrar_movimentacao(
    *,
    condominio,
    item,
    usuario,
    tipo: str,
    quantidade,
    motivo: str = "",
) -> MovimentacaoEstoque:
    """Registra um movimento feito à mão: uso, perda, vencimento ou ajuste.

    Entrada por compra e saída para manutenção não passam por aqui: cada uma tem
    caminho próprio, para não existir um movimento de compra sem compra. A
    quantidade é sempre positiva — o tipo é que diz se soma ou subtrai — e toda
    saída é conferida contra o saldo real, lido na hora.
    """
    if tipo not in TIPOS_DE_MOVIMENTO_MANUAL:
        raise ErroDeEstoque(
            "Este tipo de movimento tem um caminho próprio e não é registrado por aqui."
        )

    item = _item_do_condominio(item, condominio)
    quantidade = _quantidade(quantidade)
    if quantidade <= 0:
        raise ErroDeEstoque("A quantidade precisa ser maior que zero.")

    if tipo in TIPOS_QUE_EXIGEM_MOTIVO and not _texto(motivo):
        raise ErroDeEstoque("Explique o motivo deste movimento.")

    _bloquear_item(item)
    _conferir_saldo(item, tipo, quantidade)

    return _criar_movimento(
        condominio=condominio,
        item=item,
        usuario=usuario,
        tipo=tipo,
        quantidade=quantidade,
        motivo=motivo,
    )


@transaction.atomic
def registrar_saida_para_manutencao(
    *,
    manutencao,
    item,
    usuario,
    quantidade,
    motivo: str = "",
) -> MovimentacaoEstoque:
    """Registra a saída de material para uma manutenção que o consumiu.

    O material sai do estoque do condomínio da manutenção. A operação exige
    saldo: ninguém retira o que não existe. Quem pode fazer isso é decidido na
    autorização, antes de chegar aqui.
    """
    if manutencao is None or not getattr(manutencao, "pk", None):
        raise ErroDeEstoque("Escolha a manutenção que consumiu o material.")

    condominio = manutencao.condominio
    item = _item_do_condominio(item, condominio)
    quantidade = _quantidade(quantidade)
    if quantidade <= 0:
        raise ErroDeEstoque("A quantidade precisa ser maior que zero.")

    _bloquear_item(item)
    _conferir_saldo(item, TipoMovimentacaoEstoque.SAIDA_MANUTENCAO, quantidade)

    return _criar_movimento(
        condominio=condominio,
        item=item,
        usuario=usuario,
        tipo=TipoMovimentacaoEstoque.SAIDA_MANUTENCAO,
        quantidade=quantidade,
        motivo=motivo,
        manutencao=manutencao,
    )


def _dividir_por_destino(destino, quantidade, uso_imediato) -> tuple[Decimal, Decimal]:
    """Reparte a linha comprada entre uso imediato e estoque.

    O destino e as quantidades precisam contar a mesma história. Por isso quem
    reparte é o domínio, e não o formulário: no uso imediato tudo é consumido, no
    estoque tudo é guardado, e no misto a pessoa diz quanto é consumido e o resto
    vai para o estoque. Assim a conta sempre fecha.
    """
    if destino == DestinoDoItem.USO_IMEDIATO:
        return quantidade, ZERO

    if destino == DestinoDoItem.ESTOQUE:
        return ZERO, quantidade

    if destino != DestinoDoItem.MISTO:
        raise ErroDeEstoque("Escolha para onde vai o que foi comprado.")

    uso = _quantidade(uso_imediato)
    if uso <= 0 or uso >= quantidade:
        raise ErroDeEstoque(
            "No destino misto, informe quanto vai para uso imediato — "
            "e o restante vai para o estoque."
        )
    return uso, quantidade - uso


@transaction.atomic
def criar_compra(
    *,
    condominio,
    criado_por,
    data_compra,
    fornecedor=None,
    numero_documento: str = "",
    observacao: str = "",
    manutencao_origem=None,
) -> Compra:
    """Abre a compra como rascunho.

    O rascunho não move estoque nem vale nada: é a compra sendo montada. Só a
    confirmação aplica o destino das linhas.
    """
    compra = Compra(
        condominio=condominio,
        fornecedor=fornecedor,
        data_compra=data_compra,
        numero_documento=_texto(numero_documento),
        observacao=_texto_longo(observacao),
        manutencao_origem=manutencao_origem,
        status=StatusCompra.RASCUNHO,
        criado_por=criado_por,
    )
    _validar(compra)
    compra.save()
    return compra


@transaction.atomic
def editar_compra(
    *,
    compra: Compra,
    data_compra,
    fornecedor=None,
    numero_documento: str = "",
    observacao: str = "",
    manutencao_origem=None,
) -> Compra:
    """Atualiza o cabeçalho da compra, enquanto ela ainda é rascunho."""
    if not compra.pode_editar:
        raise ErroDeEstoque(
            "Uma compra confirmada não muda mais. Registre um novo movimento."
        )

    compra.data_compra = data_compra
    compra.fornecedor = fornecedor
    compra.numero_documento = _texto(numero_documento)
    compra.observacao = _texto_longo(observacao)
    compra.manutencao_origem = manutencao_origem

    _validar(compra)
    compra.save()
    return compra


@transaction.atomic
def adicionar_item_compra(
    *,
    compra: Compra,
    descricao: str,
    quantidade,
    valor_unitario=None,
    unidade: str = UnidadeMedida.UNIDADE,
    destino: str = DestinoDoItem.ESTOQUE,
    item_estoque=None,
    quantidade_uso_imediato=None,
    manutencao=None,
    validade=None,
) -> ItemCompra:
    """Acrescenta uma linha à compra, com o destino já dito.

    O item de estoque é escolhido, nunca criado em silêncio: quem decide o que
    passa a ser controlado é a gestão. Quando o destino inclui estoque, ele é
    obrigatório — a linha que alimenta o estoque precisa apontar para o item.
    """
    if not compra.pode_editar:
        raise ErroDeEstoque(
            "Uma compra confirmada não muda mais. Registre um novo movimento."
        )

    quantidade = _quantidade(quantidade)
    if quantidade <= 0:
        raise ErroDeEstoque("A quantidade precisa ser maior que zero.")

    if destino != DestinoDoItem.USO_IMEDIATO and not _id_de(item_estoque):
        raise ErroDeEstoque(
            "Escolha o item de estoque que esta linha repõe — "
            "se ele ainda não existe, cadastre-o primeiro."
        )

    if _id_de(item_estoque) and item_estoque.condominio_id != compra.condominio_id:
        raise ErroDeEstoque("O item escolhido não é deste condomínio.")

    uso, estoque = _dividir_por_destino(destino, quantidade, quantidade_uso_imediato)

    linha = ItemCompra(
        compra=compra,
        item_estoque=item_estoque,
        descricao=_texto(descricao),
        unidade=unidade,
        quantidade=quantidade,
        valor_unitario=_dinheiro(valor_unitario),
        destino=destino,
        quantidade_uso_imediato=uso,
        quantidade_estoque=estoque,
        manutencao=manutencao,
        validade=validade,
    )
    _validar(linha)
    linha.save()
    return linha


@transaction.atomic
def editar_item_compra(
    *,
    linha: ItemCompra,
    descricao: str,
    quantidade,
    valor_unitario=None,
    unidade: str = UnidadeMedida.UNIDADE,
    destino: str = DestinoDoItem.ESTOQUE,
    item_estoque=None,
    quantidade_uso_imediato=None,
    manutencao=None,
    validade=None,
) -> ItemCompra:
    """Atualiza uma linha da compra, enquanto ela ainda é rascunho."""
    compra = linha.compra
    if not compra.pode_editar:
        raise ErroDeEstoque(
            "Uma compra confirmada não muda mais. Registre um novo movimento."
        )

    quantidade = _quantidade(quantidade)
    if quantidade <= 0:
        raise ErroDeEstoque("A quantidade precisa ser maior que zero.")

    if destino != DestinoDoItem.USO_IMEDIATO and not _id_de(item_estoque):
        raise ErroDeEstoque(
            "Escolha o item de estoque que esta linha repõe — "
            "se ele ainda não existe, cadastre-o primeiro."
        )

    if _id_de(item_estoque) and item_estoque.condominio_id != compra.condominio_id:
        raise ErroDeEstoque("O item escolhido não é deste condomínio.")

    uso, estoque = _dividir_por_destino(destino, quantidade, quantidade_uso_imediato)

    linha.descricao = _texto(descricao)
    linha.unidade = unidade
    linha.quantidade = quantidade
    linha.valor_unitario = _dinheiro(valor_unitario)
    linha.destino = destino
    linha.item_estoque = item_estoque
    linha.quantidade_uso_imediato = uso
    linha.quantidade_estoque = estoque
    linha.manutencao = manutencao
    linha.validade = validade

    _validar(linha)
    linha.save()
    return linha


@transaction.atomic
def remover_item_compra(*, linha: ItemCompra) -> None:
    """Tira uma linha do rascunho. Só o rascunho perde linhas."""
    if not linha.compra.pode_editar:
        raise ErroDeEstoque(
            "Uma compra confirmada não muda mais. Registre um novo movimento."
        )
    linha.delete()


@transaction.atomic
def confirmar_compra(*, compra: Compra, usuario) -> Compra:
    """Faz a compra valer: aplica o destino de cada linha e movimenta o estoque.

    Tudo acontece numa transação só — a compra, os movimentos e o momento da
    confirmação —, de modo que não existe compra confirmada pela metade. Confirmar
    duas vezes é recusado: a segunda tentativa não duplica movimento nenhum.
    """
    compra = Compra.objects.select_for_update().get(pk=compra.pk)

    if compra.status == StatusCompra.CONFIRMADA:
        raise ErroDeEstoque("Esta compra já foi confirmada.")
    if compra.status != StatusCompra.RASCUNHO:
        raise ErroDeEstoque("Só um rascunho pode ser confirmado.")

    linhas = list(compra.itens.select_related("item_estoque").order_by("criado_em"))
    if not linhas:
        raise ErroDeEstoque(
            "Registre ao menos um item antes de confirmar a compra."
        )

    for linha in linhas:
        _validar(linha)

    # Trava os itens alcançados numa ordem estável, para dois pedidos ao mesmo
    # tempo não se travarem entre si. Só depois de travar é que se movimenta.
    alcancados = sorted(
        {linha.item_estoque_id for linha in linhas if linha.item_estoque_id}
    )
    for item_id in alcancados:
        ItemEstoque.objects.select_for_update().filter(pk=item_id).exists()

    for linha in linhas:
        quantidade = linha.quantidade_que_vai_para_o_estoque
        if quantidade <= 0:
            continue
        _criar_movimento(
            condominio=compra.condominio,
            item=linha.item_estoque,
            usuario=usuario,
            tipo=TipoMovimentacaoEstoque.ENTRADA_COMPRA,
            quantidade=quantidade,
            compra_item=linha,
        )

    compra.status = StatusCompra.CONFIRMADA
    compra.confirmada_em = timezone.now()
    compra.save(update_fields=["status", "confirmada_em", "atualizado_em"])
    return compra


@transaction.atomic
def cancelar_compra(*, compra: Compra, usuario) -> Compra:
    """Cancela um rascunho que não vai acontecer.

    Cancelar só vale para o que ainda não valeu: uma compra confirmada já entrou
    no estoque, e desfazê-la exigiria uma reversão — que não existe nesta fase.
    Nada é apagado: os movimentos, se houver, continuam guardados.
    """
    compra = Compra.objects.select_for_update().get(pk=compra.pk)

    if compra.status == StatusCompra.CANCELADA:
        raise ErroDeEstoque("Esta compra já está cancelada.")
    if compra.status == StatusCompra.CONFIRMADA:
        raise ErroDeEstoque(
            "Esta compra já foi confirmada e entrou no estoque. "
            "Uma compra confirmada não pode ser cancelada."
        )

    compra.status = StatusCompra.CANCELADA
    compra.confirmada_em = None
    compra.save(update_fields=["status", "confirmada_em", "atualizado_em"])
    return compra
