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
import logging
import re
import unicodedata
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.formats import number_format

from apps.core.arquivos import (
    ErroDeArquivo,
    calcular_hash,
    montar_chave_documento,
    validar_arquivo,
)
from apps.core.storage import ErroDeStorage, obter_provider

from .constants import (
    TIPOS_DE_ENTRADA,
    TIPOS_DE_SAIDA,
    TIPOS_DE_MOVIMENTO_MANUAL,
    TIPOS_QUE_EXIGEM_MOTIVO,
    DestinoDoItem,
    StatusLeituraDocumentoCompra,
    StatusCompra,
    StatusReembolso,
    TipoMovimentacaoEstoque,
    UnidadeMedida,
    direcao_do_tipo,
)
from .models import (
    Compra,
    DocumentoCompra,
    ItemCompra,
    ItemEstoque,
    MovimentacaoEstoque,
)

ZERO = Decimal("0")
logger = logging.getLogger(__name__)


class ErroDeEstoque(Exception):
    """Uma decisão do domínio sobre estoque ou compra que a gestão precisa saber."""


def _provedor_documentos_compra():
    """Reutiliza o storage privado de documentos operacionais."""
    return obter_provider(
        bucket=getattr(settings, "DOCUMENTOS_STORAGE_BUCKET", ""),
        nome_do_bucket="DOCUMENTOS_STORAGE_BUCKET",
    )


def _remover_arquivo_enviado(provider, chave):
    """Desfaz o envio se o registro do documento não puder ser persistido."""
    try:
        provider.remover(key=chave)
    except (ErroDeStorage, OSError):
        logger.exception(
            "O arquivo da compra pode ter ficado órfão no storage privado."
        )


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
    ocorrencia=None,
    demanda=None,
    local=None,
) -> MovimentacaoEstoque:
    """Grava um movimento já validado. Não decide saldo: só registra o fato."""
    movimento = MovimentacaoEstoque(
        condominio=condominio,
        item=item,
        tipo=tipo,
        quantidade=quantidade,
        compra_item=compra_item,
        manutencao=manutencao,
        ocorrencia=ocorrencia,
        demanda=demanda,
        local=local,
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
    forma_pagamento: str = "",
    numero_documento: str = "",
    observacao: str = "",
    manutencao_origem=None,
    demanda_origem=None,
    tem_documento: bool = False,
) -> Compra:
    """Abre a compra como rascunho.

    O rascunho não move estoque nem vale nada: é a compra sendo montada. Só a
    confirmação aplica o destino das linhas. Quando a compra nasce de uma
    demanda, a demanda guarda a lembrança de que ela foi registrada — o fato é
    do estoque, e nada mais é copiado para lá.

    A compra não nasce vazia: exige ao menos a data, o fornecedor ou um
    documento — o que sozinho já diz que houve uma compra. Sem nenhum deles, um
    rascunho fantasma apareceria na lista sem nada que o explique.
    """
    if not (
        data_compra
        or fornecedor
        or _texto(numero_documento)
        or tem_documento
    ):
        raise ErroDeEstoque(
            "Informe a data, o fornecedor ou um documento para abrir a compra."
        )

    compra = Compra(
        condominio=condominio,
        fornecedor=fornecedor,
        forma_pagamento=_texto(forma_pagamento),
        data_compra=data_compra,
        numero_documento=_texto(numero_documento),
        observacao=_texto_longo(observacao),
        manutencao_origem=manutencao_origem,
        demanda_origem=demanda_origem,
        status=StatusCompra.RASCUNHO,
        criado_por=criado_por,
    )
    _validar(compra)
    compra.save()

    if compra.demanda_origem_id:
        _lembrar_compra_na_demanda(compra, criado_por)

    return compra


def _lembrar_compra_na_demanda(compra: Compra, usuario) -> None:
    """Registra, na demanda de origem, que a compra foi aberta.

    O fato pertence ao estoque, e por isso o evento na demanda é só a lembrança
    de que ele existiu — o histórico da compra não é copiado para lá. Sem
    demanda de origem, não há onde lembrar, e nada acontece.
    """
    from apps.demandas import services as demandas_services

    fornecedor = (
        compra.fornecedor.nome_razao_social
        if compra.fornecedor_id
        else "Sem fornecedor"
    )
    descricao = (
        f"Compra registrada: R$ "
        f"{number_format(compra.total_calculado(), decimal_pos=2, force_grouping=True)}"
        f" · {fornecedor}"
    )
    demandas_services.registrar_compra(compra.demanda_origem, usuario, descricao)


@transaction.atomic
def criar_compra_com_documento(*, condominio, criado_por, arquivo, tipo_documento):
    """Cria o rascunho e seu documento juntos, sem deixar compra incompleta."""
    compra = criar_compra(
        condominio=condominio,
        criado_por=criado_por,
        data_compra=None,
        tem_documento=True,
    )
    documento = adicionar_documento_compra(
        compra=compra,
        arquivo=arquivo,
        usuario=criado_por,
        tipo_documento=tipo_documento,
    )
    return compra, documento


@transaction.atomic
def adicionar_documento_compra(
    *, compra: Compra, arquivo, usuario, tipo_documento
) -> DocumentoCompra:
    """Guarda o comprovante e as propostas locais ligadas à compra existente."""
    compra = Compra.objects.select_for_update().get(pk=compra.pk)
    if not compra.pode_editar:
        raise ErroDeEstoque("Só é possível anexar documento a um rascunho.")

    try:
        conteudo, mime, extensao, nome = validar_arquivo(arquivo)
    except ErroDeArquivo as erro:
        raise ErroDeEstoque(str(erro)) from erro

    sha256 = calcular_hash(conteudo)
    existente = DocumentoCompra.objects.filter(
        compra=compra, sha256=sha256
    ).first()
    if existente is not None:
        raise ErroDeEstoque("Este documento já está anexado a esta compra.")

    from .leitura_compra import associar_itens_existentes, sugerir_dados

    estado, dados, erro_leitura = sugerir_dados(
        conteudo=conteudo,
        mime_type=mime,
    )
    documento = DocumentoCompra(
        compra=compra,
        tipo_documento=tipo_documento,
        nome_original=nome,
        mime_type=mime,
        tamanho_bytes=len(conteudo),
        sha256=sha256,
        storage_key="",
        status_leitura=estado,
        dados_extraidos=dados,
        erro_leitura=erro_leitura,
        criado_por=usuario,
    )
    documento.storage_key = montar_chave_documento(
        condominio=compra.condominio,
        documento=compra,
        arquivo=documento,
        extensao=extensao,
    )
    documento.full_clean()
    documento.dados_extraidos = associar_itens_existentes(documento)

    provider = _provedor_documentos_compra()
    try:
        provider.enviar(
            key=documento.storage_key,
            conteudo=conteudo,
            content_type=mime,
        )
        documento.storage_provider = provider.nome
        documento.storage_bucket = provider.bucket
        documento.save()
    except Exception:
        _remover_arquivo_enviado(provider, documento.storage_key)
        raise

    return documento


@transaction.atomic
def reprocessar_documento_compra(
    *, documento: DocumentoCompra, usuario
) -> DocumentoCompra:
    """Relê o arquivo já guardado, sem criar documento, item nem compra novos.

    O arquivo não é reenviado: é lido de volta do storage e submetido à mesma
    leitura local. Como a leitura não cria nada — só propostas —, reprocessar
    atualiza as sugestões do próprio documento. Quando alguma proposta já foi
    transformada em linha da compra, a releitura é recusada: uma nova leitura
    pode reordenar ou renomear propostas, e remarcá-las seria perder o vínculo
    que a gestão já estabeleceu.
    """
    compra = Compra.objects.select_for_update().get(pk=documento.compra_id)
    documento = DocumentoCompra.objects.select_for_update().get(
        pk=documento.pk,
        compra_id=compra.pk,
    )
    if not compra.pode_editar:
        raise ErroDeEstoque("Uma compra confirmada não muda mais.")

    propostas = (documento.dados_extraidos or {}).get("itens", [])
    if any(proposta.get("item_compra_id") for proposta in propostas):
        raise ErroDeEstoque(
            "Já há itens incluídos a partir desta leitura. "
            "Conclua ou remova essas linhas antes de reprocessar."
        )

    provider = _provedor_documentos_compra()
    try:
        conteudo = provider.baixar(key=documento.storage_key)
    except (ErroDeStorage, OSError) as erro:
        raise ErroDeEstoque(
            "Não foi possível recuperar o arquivo guardado para reler."
        ) from erro

    from .leitura_compra import associar_itens_existentes, sugerir_dados

    estado, dados, erro_leitura = sugerir_dados(
        conteudo=conteudo,
        mime_type=documento.mime_type,
    )
    documento.status_leitura = estado
    documento.erro_leitura = erro_leitura
    documento.dados_extraidos = dados
    documento.save(update_fields=["status_leitura", "erro_leitura", "dados_extraidos"])
    documento.dados_extraidos = associar_itens_existentes(documento)
    documento.save(update_fields=["dados_extraidos"])
    return documento


@transaction.atomic
def selecionar_item_extraido(*, documento, indice, item):
    """Registra a escolha humana de um item canônico para uma proposta."""
    compra = Compra.objects.select_for_update().get(pk=documento.compra_id)
    documento = DocumentoCompra.objects.select_for_update().get(
        pk=documento.pk,
        compra_id=compra.pk,
    )
    if not compra.pode_editar:
        raise ErroDeEstoque("Uma compra confirmada não pode mudar.")
    if item.condominio_id != compra.condominio_id or not item.ativo:
        raise ErroDeEstoque("Escolha um item ativo deste condomínio.")

    dados = dict(documento.dados_extraidos or {})
    itens = list(dados.get("itens", []))
    try:
        proposta = dict(itens[int(indice)])
    except (ValueError, TypeError, IndexError):
        raise ErroDeEstoque("O item extraído não está mais disponível.") from None
    proposta["item_estoque_sugerido"] = str(item.pk)
    proposta["categoria_sugerida"] = item.categoria_texto
    itens[int(indice)] = proposta
    dados["itens"] = itens
    documento.dados_extraidos = dados
    documento.save(update_fields=["dados_extraidos"])
    return documento


@transaction.atomic
def cadastrar_item_extraido(
    *, documento, indice, criado_por, nome=None, categoria_texto=None
):
    """Cadastra no catálogo canônico apenas após ação explícita da gestão."""
    compra = Compra.objects.select_for_update().get(pk=documento.compra_id)
    documento = DocumentoCompra.objects.select_for_update().get(
        pk=documento.pk,
        compra_id=compra.pk,
    )
    if not compra.pode_editar:
        raise ErroDeEstoque("Uma compra confirmada não pode mudar.")

    dados = dict(documento.dados_extraidos or {})
    itens = list(dados.get("itens", []))
    try:
        proposta = dict(itens[int(indice)])
    except (ValueError, TypeError, IndexError):
        raise ErroDeEstoque("O item extraído não está mais disponível.") from None

    existente_id = proposta.get("item_estoque_sugerido")
    if existente_id:
        existente = ItemEstoque.objects.filter(
            pk=existente_id,
            condominio_id=compra.condominio_id,
            ativo=True,
        ).first()
        if existente:
            raise ErroDeEstoque("Este item já está vinculado ao catálogo.")

    try:
        quantidade = Decimal(proposta.get("quantidade") or "1")
    except (InvalidOperation, TypeError, ValueError):
        quantidade = Decimal("1")
    unidade_padrao = (
        UnidadeMedida.UNIDADE
        if quantidade == quantidade.to_integral_value()
        else UnidadeMedida.OUTRA
    )
    unidade_sugerida = proposta.get("unidade")
    unidade = (
        unidade_sugerida
        if unidade_sugerida in dict(UnidadeMedida.choices)
        else unidade_padrao
    )
    item = criar_item(
        condominio=compra.condominio,
        criado_por=criado_por,
        nome=nome if nome is not None else proposta.get("descricao", ""),
        categoria_texto=(
            categoria_texto
            if categoria_texto is not None
            else proposta.get("categoria_sugerida", "")
        ),
        unidade=unidade,
    )
    proposta["item_estoque_sugerido"] = str(item.pk)
    proposta["categoria_sugerida"] = item.categoria_texto
    itens[int(indice)] = proposta
    dados["itens"] = itens
    documento.dados_extraidos = dados
    documento.save(update_fields=["dados_extraidos"])
    return item


@transaction.atomic
def incluir_item_extraido(
    *,
    compra: Compra,
    documento: DocumentoCompra,
    indice,
    descricao,
    quantidade,
    valor_unitario,
    unidade,
    destino,
    item_estoque=None,
    quantidade_uso_imediato=None,
    manutencao=None,
    validade=None,
) -> ItemCompra:
    """Inclui uma proposta revisada como linha real, uma única vez."""
    bloqueado = Compra.objects.select_for_update().get(pk=compra.pk)
    documento = DocumentoCompra.objects.select_for_update().get(
        pk=documento.pk,
        compra_id=bloqueado.pk,
    )
    if documento.compra_id != bloqueado.pk:
        raise ErroDeEstoque("O documento não pertence a esta compra.")
    if not bloqueado.pode_editar:
        raise ErroDeEstoque("Uma compra confirmada não pode mudar.")
    dados = dict(documento.dados_extraidos or {})
    itens = list(dados.get("itens", []))
    try:
        proposta = dict(itens[int(indice)])
    except (ValueError, TypeError, IndexError):
        raise ErroDeEstoque("O item extraído não está mais disponível.") from None
    if proposta.get("item_compra_id"):
        raise ErroDeEstoque("Este item já foi incluído na compra.")

    if destino != DestinoDoItem.USO_IMEDIATO and item_estoque is None:
        raise ErroDeEstoque(
            "Item não encontrado. Selecione ou cadastre um item antes de incluir."
        )
    linha = adicionar_item_compra(
        compra=bloqueado,
        descricao=descricao,
        quantidade=quantidade,
        valor_unitario=valor_unitario,
        unidade=unidade,
        destino=destino,
        item_estoque=item_estoque,
        quantidade_uso_imediato=quantidade_uso_imediato,
        manutencao=manutencao,
        validade=validade,
    )
    proposta["item_compra_id"] = str(linha.pk)
    itens[int(indice)] = proposta
    dados["itens"] = itens
    documento.dados_extraidos = dados
    documento.save(update_fields=["dados_extraidos"])
    return linha


@transaction.atomic
def trazer_itens_identificados(
    *, compra: Compra, documento: DocumentoCompra
) -> list[ItemCompra]:
    """Transforma de uma vez todas as propostas pendentes em linhas da compra.

    A gestão pediu para não repetir item a item o que o documento já disse. Cada
    proposta ainda não incluída vira uma linha com destino de **uso imediato** —
    a linha continua editável no rascunho, e mover para o estoque é uma decisão
    posterior. O que já foi incluído não entra de novo, e nada é criado que já
    tenha dono no catálogo.
    """
    bloqueado = Compra.objects.select_for_update().get(pk=compra.pk)
    documento = DocumentoCompra.objects.select_for_update().get(
        pk=documento.pk,
        compra_id=bloqueado.pk,
    )
    if not bloqueado.pode_editar:
        raise ErroDeEstoque("Uma compra confirmada não muda mais.")

    propostas = list((documento.dados_extraidos or {}).get("itens", []))
    incluidos = []
    for indice, proposta in enumerate(propostas):
        if proposta.get("item_compra_id"):
            continue
        descricao = _texto(proposta.get("descricao"))
        if not descricao:
            continue
        unidade = proposta.get("unidade") or UnidadeMedida.UNIDADE
        incluidos.append(
            incluir_item_extraido(
                compra=bloqueado,
                documento=documento,
                indice=indice,
                descricao=descricao,
                quantidade=proposta.get("quantidade") or "1",
                valor_unitario=proposta.get("valor_unitario"),
                unidade=unidade,
                destino=DestinoDoItem.USO_IMEDIATO,
            )
        )
    if not incluidos:
        raise ErroDeEstoque(
            "Não há itens identificados pendentes para trazer para a compra."
        )
    return incluidos


@transaction.atomic
def editar_compra(
    *,
    compra: Compra,
    data_compra,
    fornecedor=None,
    forma_pagamento: str = "",
    numero_documento: str = "",
    observacao: str = "",
    manutencao_origem=None,
    demanda_origem=None,
) -> Compra:
    """Atualiza o cabeçalho da compra, enquanto ela ainda é rascunho."""
    if not compra.pode_editar:
        raise ErroDeEstoque(
            "Uma compra confirmada não muda mais. Registre um novo movimento."
        )

    compra.data_compra = data_compra
    compra.fornecedor = fornecedor
    compra.forma_pagamento = _texto(forma_pagamento)
    compra.numero_documento = _texto(numero_documento)
    compra.observacao = _texto_longo(observacao)
    compra.manutencao_origem = manutencao_origem
    compra.demanda_origem = demanda_origem

    _validar(compra)
    compra.save()
    return compra


@transaction.atomic
def configurar_reembolso_compra(
    *,
    compra: Compra,
    comprada_por_outra_pessoa: bool,
    precisa_reembolso: bool,
    reembolso_para=None,
    valor_reembolso=None,
    observacao_reembolso: str = "",
) -> Compra:
    """Guarda a intenção de reembolso na própria compra, sem gerar despesa."""
    if not compra.pode_editar:
        raise ErroDeEstoque("Só é possível definir reembolso antes da confirmação.")

    compra.comprada_por_outra_pessoa = bool(comprada_por_outra_pessoa)
    compra.precisa_reembolso = bool(precisa_reembolso)
    compra.reembolso_para = reembolso_para if precisa_reembolso else None
    compra.valor_reembolso = (
        _dinheiro(valor_reembolso) if precisa_reembolso else None
    )
    compra.observacao_reembolso = (
        _texto_longo(observacao_reembolso) if precisa_reembolso else ""
    )
    compra.status_reembolso = (
        StatusReembolso.PENDENTE
        if precisa_reembolso
        else StatusReembolso.NAO_SOLICITADO
    )
    compra.reembolsado_em = None
    _validar(compra)
    compra.save()
    return compra


@transaction.atomic
def marcar_reembolso_compra(
    *,
    compra: Compra,
    reembolso_para,
    valor_reembolso,
    observacao_reembolso: str = "",
) -> Compra:
    """Marca a compra para reembolso direto da lista, sem passar pela edição.

    A compra já confirmada continua valendo: o que muda é só a intenção de
    devolver o dinheiro a quem pagou. O reembolso fica pendente até que alguém
    registre que ele foi pago.
    """
    compra = Compra.objects.select_for_update().get(pk=compra.pk)
    if compra.status == StatusCompra.CANCELADA:
        raise ErroDeEstoque("Uma compra cancelada não recebe reembolso.")
    if compra.status_reembolso != StatusReembolso.NAO_SOLICITADO:
        raise ErroDeEstoque("Esta compra já tem um reembolso registrado.")

    compra.comprada_por_outra_pessoa = True
    compra.precisa_reembolso = True
    compra.reembolso_para = reembolso_para
    compra.valor_reembolso = _dinheiro(valor_reembolso)
    compra.observacao_reembolso = _texto_longo(observacao_reembolso)
    compra.status_reembolso = StatusReembolso.PENDENTE
    compra.reembolsado_em = None
    _validar(compra)
    compra.save()
    return compra


@transaction.atomic
def marcar_reembolso_realizado(*, compra: Compra) -> Compra:
    """Marca uma solicitação pendente como paga uma única vez."""
    compra = Compra.objects.select_for_update().get(pk=compra.pk)
    if compra.status != StatusCompra.CONFIRMADA:
        raise ErroDeEstoque("Confirme a compra antes de registrar o reembolso.")
    if compra.status_reembolso != StatusReembolso.PENDENTE:
        raise ErroDeEstoque("Só um reembolso pendente pode ser marcado como realizado.")
    compra.status_reembolso = StatusReembolso.REEMBOLSADO
    compra.reembolsado_em = timezone.now()
    _validar(compra)
    compra.save(
        update_fields=[
            "status_reembolso",
            "reembolsado_em",
            "atualizado_em",
        ]
    )
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
    if compra.data_compra is None:
        raise ErroDeEstoque("Informe a data antes de confirmar a compra.")

    linhas = list(compra.itens.select_related("item_estoque").order_by("criado_em"))
    if not linhas:
        raise ErroDeEstoque(
            "Registre ao menos um item antes de confirmar a compra."
        )

    for linha in linhas:
        _validar(linha)
    _validar(compra)

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


# -- Material do trabalho e retirada rápida ------------------------------------
#
# A baixa de estoque acompanha o trabalho: quem conclui uma manutenção, uma
# ocorrência ou uma demanda diz, no mesmo gesto, o que consumiu. Aqui é
# diferente da retirada manual: a saída **não** exige saldo. O trabalho já
# aconteceu e negá-lo apagaria o fato — sem saldo, o registro fica e o sistema
# avisa que o estoque ficou negativo, para a contagem corrigir depois. O
# determinismo protege o saldo do erro, mas nunca impede registrar o que houve.

LIMITE_DE_CHIPS_DE_MATERIAL = 6
JANELA_DOS_MAIS_USADOS = 90  # dias
MOTIVO_DA_RETIRADA_RAPIDA = "Peguei do estoque"

PALAVRAS_IGNORADAS = frozenset(
    {"para", "com", "sem", "dos", "das", "uma", "tipo", "caixa", "pacote"}
)


def _sem_acento(texto: str) -> str:
    """O texto em minúsculas e sem acento, para casar nome de item com relato."""
    normalizado = unicodedata.normalize("NFKD", texto or "")
    return normalizado.encode("ascii", "ignore").decode("ascii").lower()


def _radicais_do_nome(nome: str) -> list[str]:
    """As palavras que identificam o item no relato, no singular.

    "Lâmpada LED" vira "lampada": o primeiro substantivo com corpo suficiente,
    sem o plural. É o casamento simples que o pedido descreve — palavra do nome
    contra palavra do texto, sem IA.
    """
    radicais = []
    for palavra in re.findall(r"[0-9a-z]+", _sem_acento(nome)):
        if len(palavra) < 4 or palavra in PALAVRAS_IGNORADAS:
            continue
        if palavra.endswith("s") and len(palavra) > 4:
            palavra = palavra[:-1]
        radicais.append(palavra)
    return radicais


def sugestao_no_texto(nome: str, texto: str) -> int:
    """A quantidade que o relato sugere para um item — só um palpite, sem IA.

    "troquei 2 lâmpadas" sugere 2 lâmpadas: procura-se o radical do item no
    texto e o número mais próximo antes dele. A sugestão aparece antes de
    concluir, e quem conclui confirma ou corrige — nada é gravado sem isso.
    """
    alvo = _sem_acento(texto)
    if not alvo:
        return 0
    for radical in _radicais_do_nome(nome):
        posicao = alvo.find(radical)
        while posicao != -1:
            antes = alvo[max(0, posicao - 20) : posicao]
            numeros = re.findall(r"\d+", antes)
            if numeros:
                return int(numeros[-1])
            posicao = alvo.find(radical, posicao + 1)
    return 0


def chips_de_material(
    condominio, *, texto: str = "", limite: int = LIMITE_DE_CHIPS_DE_MATERIAL
) -> list[dict]:
    """Até seis itens de estoque como chips, com a quantidade já sugerida.

    A ordem é a do uso: primeiro o que mais saiu nos últimos 90 dias; sem
    histórico, o crítico antes, e o resto por ordem alfabética. O palpite de
    quantidade vem do relato, quando ele cita o item com um número.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return []

    corte = timezone.now() - timedelta(days=JANELA_DOS_MAIS_USADOS)
    usos = Count(
        "movimentacoes",
        filter=Q(
            movimentacoes__tipo__in=TIPOS_DE_SAIDA,
            movimentacoes__criado_em__gte=corte,
        ),
    )
    itens = (
        ItemEstoque.objects.do_condominio(condominio)
        .ativos()
        .annotate(_usos=usos)
        .order_by("-_usos", "-item_critico", Lower("nome"))[:limite]
    )
    return [
        {
            "pk": str(item.pk),
            "nome": item.nome,
            "unidade": item.get_unidade_display(),
            "quantidade": sugestao_no_texto(item.nome, texto),
        }
        for item in itens
    ]


def _pares_de_material(material) -> list[tuple[str, Decimal]]:
    """Lê o material marcado no formulário em pares ("<pk>:<quantidade>").

    A tela manda tudo numa lista separada por vírgula; uma lista já pronta
    também serve. O que não tiver item e quantidade positiva é ignorado.
    """
    if isinstance(material, str):
        material = [parte for parte in material.split(",") if parte]
    pares = []
    for bruto in material or ():
        if not bruto or ":" not in str(bruto):
            continue
        chave, _, quantidade = str(bruto).partition(":")
        try:
            valor = Decimal(quantidade)
        except (InvalidOperation, ValueError):
            continue
        if valor > 0:
            pares.append((chave.strip(), valor))
    return pares


def _item_por_chave(condominio, chave):
    """O item do condomínio com esta chave, ou nada quando a chave é inválida."""
    try:
        return ItemEstoque.objects.filter(pk=chave, condominio=condominio).first()
    except (ValidationError, ValueError, TypeError):
        return None


@transaction.atomic
def registrar_saida_do_trabalho(
    *,
    condominio,
    item,
    usuario,
    quantidade,
    tipo,
    motivo: str = "",
    manutencao=None,
    ocorrencia=None,
    demanda=None,
    local=None,
) -> MovimentacaoEstoque:
    """Registra a saída de material ligada ao registro que a consumiu.

    Diferente da retirada manual, aqui a saída **não** exige saldo: o trabalho
    já aconteceu e negá-lo apagaria o fato. O item é travado e o saldo é relido
    dentro da transação; quem chama decide o que fazer quando o saldo fica
    negativo — na conclusão do trabalho, é concluir e avisar.
    """
    item = _item_do_condominio(item, condominio)
    quantidade = _quantidade(quantidade)
    if quantidade <= 0:
        raise ErroDeEstoque("A quantidade precisa ser maior que zero.")

    _bloquear_item(item)
    return _criar_movimento(
        condominio=condominio,
        item=item,
        usuario=usuario,
        tipo=tipo,
        quantidade=quantidade,
        motivo=motivo,
        manutencao=manutencao,
        ocorrencia=ocorrencia,
        demanda=demanda,
        local=local,
    )


def registrar_material_do_trabalho(
    *,
    condominio,
    usuario,
    material,
    tipo,
    motivo: str = "",
    manutencao=None,
    ocorrencia=None,
    demanda=None,
    local=None,
) -> tuple[list, bool]:
    """Registra todas as saídas marcadas e diz se alguma zerou o saldo.

    Devolve os movimentos criados e um aviso: se alguma saída deixou o saldo
    negativo, quem chamou mostra o recado discreto e a contagem corrige depois.
    Chave inválida ou item de outro condomínio é simplesmente ignorado.
    """
    movimentos = []
    negativo = False
    for chave, quantidade in _pares_de_material(material):
        item = _item_por_chave(condominio, chave)
        if item is None:
            continue
        movimentos.append(
            registrar_saida_do_trabalho(
                condominio=condominio,
                item=item,
                usuario=usuario,
                quantidade=quantidade,
                tipo=tipo,
                motivo=motivo,
                manutencao=manutencao,
                ocorrencia=ocorrencia,
                demanda=demanda,
                local=local,
            )
        )
        if saldo_do_item(item) < 0:
            negativo = True
    return movimentos, negativo


def material_da_manutencao(*, manutencao, usuario, material) -> tuple[list, bool]:
    """A saída do material usado nesta manutenção. O local vem da manutenção."""
    return registrar_material_do_trabalho(
        condominio=manutencao.condominio,
        usuario=usuario,
        material=material,
        tipo=TipoMovimentacaoEstoque.SAIDA_MANUTENCAO,
        manutencao=manutencao,
        local=manutencao.local,
    )


def material_da_ocorrencia(*, execucao, usuario, material) -> tuple[list, bool]:
    """A saída do material usado nesta ocorrência. O local vem da rotina."""
    return registrar_material_do_trabalho(
        condominio=execucao.condominio,
        usuario=usuario,
        material=material,
        tipo=TipoMovimentacaoEstoque.SAIDA_OCORRENCIA,
        ocorrencia=execucao,
        local=execucao.rotina.local,
    )


def material_da_demanda(*, demanda, usuario, material) -> tuple[list, bool]:
    """A saída do material usado nesta demanda. O local vem da demanda."""
    return registrar_material_do_trabalho(
        condominio=demanda.condominio,
        usuario=usuario,
        material=material,
        tipo=TipoMovimentacaoEstoque.SAIDA_DEMANDA,
        demanda=demanda,
        local=demanda.local,
    )


def ultimo_local_do_usuario(usuario, condominio):
    """O último local de onde esta pessoa retirou material neste condomínio.

    É o "último usado" da retirada rápida: ele já vem escolhido, para o gesto
    custar um toque. Sem histórico, não há sugestão.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return None

    movimento = (
        MovimentacaoEstoque.objects.do_condominio(condominio)
        .filter(usuario=usuario, local__isnull=False)
        .order_by("-criado_em")
        .first()
    )
    return movimento.local if movimento else None


# -- Reposição: o que foi feito com a compra anterior --------------------------


def _quantidade_legivel(valor, unidade: str) -> str:
    """O número sem zeros inúteis, com a unidade no fim: «2 un», «0,5 kg».

    É leitura de tela: o número existe para dizer o tamanho, não para calcular.
    """
    numero = f"{Decimal(valor).normalize():f}"
    if "," not in numero and "." in numero:
        inteiro, _, casas = numero.partition(".")
        numero = f"{inteiro},{casas}"
    return f"{numero} {unidade}".strip()


def _ultima_linha_de_compra(item):
    """A linha da compra confirmada mais recente que repôs este item."""
    return (
        ItemCompra.objects.filter(
            item_estoque=item, compra__status=StatusCompra.CONFIRMADA
        )
        .select_related("compra", "compra__fornecedor")
        .order_by("-compra__data_compra", "-criado_em")
        .first()
    )


def historico_de_reposicao(item) -> dict:
    """O que já aconteceu com este item, em números, para decidir a reposição.

    Responde três perguntas de uma vez: quanto veio na última compra e quando,
    quanto já saiu registrado e quanto saiu **sem destino** — isto é, sem local
    e sem vínculo com um registro de origem. É a diferença entre o material que
    se sabe para onde foi e o que ninguém explicou.
    """
    if item is None or not getattr(item, "pk", None):
        return {}

    ultima = _ultima_linha_de_compra(item)
    agregado = item.movimentacoes.saidas().aggregate(total=Sum("quantidade"))
    sem_destino = (
        item.movimentacoes.saidas()
        .filter(
            local__isnull=True,
            manutencao__isnull=True,
            ocorrencia__isnull=True,
            demanda__isnull=True,
        )
        .aggregate(total=Sum("quantidade"))
    )

    unidade = item.get_unidade_display().lower()
    return {
        "quantidade": _quantidade_legivel(ultima.quantidade, unidade) if ultima else "",
        "data": ultima.compra.data_compra if ultima else None,
        "saidas": _quantidade_legivel(agregado["total"] or ZERO, unidade),
        "sem_destino": _quantidade_legivel(sem_destino["total"] or ZERO, unidade),
    }


def saidas_do_item(item):
    """As saídas registradas do item, com local e quem — a lista curta."""
    if item is None or not getattr(item, "pk", None):
        return MovimentacaoEstoque.objects.none()

    return (
        item.movimentacoes.saidas()
        .select_related("usuario", "local", "manutencao", "ocorrencia", "demanda")
        .order_by("-criado_em")
    )


# -- Contagem do estoque -------------------------------------------------------


def contar_estoque(*, condominio, usuario, contagens, data=None) -> dict:
    """Ajusta o saldo ao que a contagem encontrou e devolve o que ficou pendente.

    Campo vazio é item não contado: não gera ajuste nenhum. Onde a contagem
    difere do saldo, entra um ajuste de entrada ou de saída com o motivo
    "Diferença na contagem de <data>" — e o histórico guarda quem contou. O
    resumo mostra quantos itens têm saída ainda sem destino registrado.
    """
    momento = data or timezone.localdate()
    motivo = f"Diferença na contagem de {momento.strftime('%d/%m/%Y')}"

    ajustes = 0
    for chave, valor in _contagens_do_pedido(contagens).items():
        item = _item_por_chave(condominio, chave)
        if item is None:
            continue
        diferenca = valor - saldo_do_item(item)
        if diferenca == 0:
            continue
        tipo = (
            TipoMovimentacaoEstoque.AJUSTE_ENTRADA
            if diferenca > 0
            else TipoMovimentacaoEstoque.AJUSTE_SAIDA
        )
        registrar_saida_do_trabalho(
            condominio=condominio,
            item=item,
            usuario=usuario,
            quantidade=abs(diferenca),
            tipo=tipo,
            motivo=motivo,
        )
        ajustes += 1

    return {
        "motivo": motivo,
        "ajustes": ajustes,
        "sem_destino": _itens_com_saida_sem_destino(condominio),
    }


def _contagens_do_pedido(contagens) -> dict:
    """Só o que foi digitado na contagem: campo vazio fica de fora."""
    lidas = {}
    for chave, valor in (contagens or {}).items():
        bruto = _texto(str(valor)) if valor not in (None, "") else ""
        if not bruto:
            continue
        lidas[chave] = _quantidade(bruto)
    return lidas


def _itens_com_saida_sem_destino(condominio) -> int:
    """Quantos itens do condomínio têm saída registrada sem destino nenhum."""
    return (
        ItemEstoque.objects.do_condominio(condominio)
        .filter(
            movimentacoes__tipo__in=TIPOS_DE_SAIDA,
            movimentacoes__local__isnull=True,
            movimentacoes__manutencao__isnull=True,
            movimentacoes__ocorrencia__isnull=True,
            movimentacoes__demanda__isnull=True,
        )
        .distinct()
        .count()
    )
