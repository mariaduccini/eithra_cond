"""Constantes do domínio de estoque e compras operacionais.

Estoque é o que existe fisicamente no condomínio: a lâmpada que sobrou, o filtro
reserva, o material de limpeza. Compra é o ato que repõe esse material — o que
foi adquirido, de quem, quando e por quanto.

Duas ideias governam este arquivo. A primeira: o saldo não é um dado guardado,
é a leitura das movimentações — por isso a **direção** de cada movimento é dita
uma única vez, nos conjuntos ``TIPOS_DE_ENTRADA`` e ``TIPOS_DE_SAIDA``, e o
resto do sistema pergunta a eles em vez de repetir a lista. A segunda: a
quantidade é sempre positiva; o que muda o sentido do movimento é o tipo, nunca
o sinal.
"""
from django.db import models


class UnidadeMedida(models.TextChoices):
    """Como o item é contado.

    Catálogo curto de propósito: a quantidade é decimal, então "2,5 litros" e
    "3,2 metros" cabem sem precisar de uma unidade para cada medida.
    """

    UNIDADE = "unidade", "Unidade"
    CAIXA = "caixa", "Caixa"
    PACOTE = "pacote", "Pacote"
    METRO = "metro", "Metro"
    LITRO = "litro", "Litro"
    QUILO = "quilo", "Quilo"
    ROLO = "rolo", "Rolo"
    PAR = "par", "Par"
    JOGO = "jogo", "Jogo"
    OUTRA = "outra", "Outra"


class DestinoDoItem(models.TextChoices):
    """Para onde vai o que foi comprado.

    Uso imediato consome no ato; estoque guarda para depois; misto divide a
    mesma linha entre as duas coisas — e, no misto, a soma das partes precisa
    fechar exatamente com o que foi comprado.
    """

    USO_IMEDIATO = "uso_imediato", "Uso imediato"
    ESTOQUE = "estoque", "Para o estoque"
    MISTO = "misto", "Parte em uso, parte no estoque"


class StatusCompra(models.TextChoices):
    """Onde a compra está.

    Rascunho é o que ainda está sendo montado e não movimentou nada. Confirmada
    é o que já valeu e já entrou no estoque quando o destino era estoque.
    Cancelada é o que não aconteceu.
    """

    RASCUNHO = "rascunho", "Rascunho"
    CONFIRMADA = "confirmada", "Confirmada"
    CANCELADA = "cancelada", "Cancelada"


class TipoMovimentacaoEstoque(models.TextChoices):
    """O que aconteceu com o estoque, e por quê.

    Não existe tipo "saldo": o saldo não se movimenta, ele é o resultado. Cada
    tipo abaixo é um fato — chegou por compra, saiu para uma manutenção, saiu
    para uso, foi ajustado por contagem, perdeu-se, venceu.
    """

    ENTRADA_COMPRA = "entrada_compra", "Entrada por compra"
    SAIDA_MANUTENCAO = "saida_manutencao", "Saída para manutenção"
    SAIDA_USO = "saida_uso", "Saída para uso"
    AJUSTE_ENTRADA = "ajuste_entrada", "Ajuste de entrada"
    AJUSTE_SAIDA = "ajuste_saida", "Ajuste de saída"
    PERDA = "perda", "Perda"
    VENCIMENTO = "vencimento", "Vencimento"


# A direção de cada movimento, declarada uma única vez. Quem precisa saber se um
# movimento soma ou subtrai pergunta a estes conjuntos — nunca repete a lista.
TIPOS_DE_ENTRADA = frozenset(
    {
        TipoMovimentacaoEstoque.ENTRADA_COMPRA,
        TipoMovimentacaoEstoque.AJUSTE_ENTRADA,
    }
)

TIPOS_DE_SAIDA = frozenset(
    {
        TipoMovimentacaoEstoque.SAIDA_MANUTENCAO,
        TipoMovimentacaoEstoque.SAIDA_USO,
        TipoMovimentacaoEstoque.AJUSTE_SAIDA,
        TipoMovimentacaoEstoque.PERDA,
        TipoMovimentacaoEstoque.VENCIMENTO,
    }
)

# Movimentos que só existem quando alguém explica o porquê. O vencimento fica de
# fora quando a validade já explica o fato.
#
# É uma tupla ordenada, e não um conjunto, porque esta lista entra numa condição
# de banco (``CHECK``): uma ordem estável mantém a migration igual toda vez que
# é gerada.
TIPOS_QUE_EXIGEM_MOTIVO = (
    TipoMovimentacaoEstoque.SAIDA_USO,
    TipoMovimentacaoEstoque.AJUSTE_ENTRADA,
    TipoMovimentacaoEstoque.AJUSTE_SAIDA,
    TipoMovimentacaoEstoque.PERDA,
)

# Movimentos que a gestão registra à mão. Entrada por compra e saída para
# manutenção têm caminho próprio — não aparecem como escolha solta.
TIPOS_DE_MOVIMENTO_MANUAL = (
    TipoMovimentacaoEstoque.SAIDA_USO,
    TipoMovimentacaoEstoque.PERDA,
    TipoMovimentacaoEstoque.VENCIMENTO,
    TipoMovimentacaoEstoque.AJUSTE_ENTRADA,
    TipoMovimentacaoEstoque.AJUSTE_SAIDA,
)


class SituacaoDoSaldo(models.TextChoices):
    """Como o saldo deve ser lido agora.

    Não é guardada em lugar nenhum: é derivada do saldo e do mínimo no momento
    da leitura. Zerado tem precedência sobre abaixo do mínimo, porque não ter
    nada é mais grave do que ter pouco.
    """

    NORMAL = "normal", "Normal"
    ABAIXO_DO_MINIMO = "abaixo_do_minimo", "Abaixo do mínimo"
    ZERADO = "zerado", "Zerado"


def direcao_do_tipo(tipo: str) -> str:
    """Diz se o tipo soma ou subtrai do saldo. Sem reconhecer, é erro.

    Não há resposta padrão: um tipo desconhecido não pode somar nem subtrair, e
    um palpite aqui viraria saldo errado. Por isso a função recusa em vez de
    adivinhar.
    """
    if tipo in TIPOS_DE_ENTRADA:
        return "entrada"
    if tipo in TIPOS_DE_SAIDA:
        return "saida"
    raise ValueError("Tipo de movimentação desconhecido.")


def situacao_do_saldo(saldo, estoque_minimo) -> str:
    """Lê o saldo: zerado, abaixo do mínimo ou normal."""
    if saldo is None:
        saldo = 0
    if saldo <= 0:
        return SituacaoDoSaldo.ZERADO
    if estoque_minimo and saldo < estoque_minimo:
        return SituacaoDoSaldo.ABAIXO_DO_MINIMO
    return SituacaoDoSaldo.NORMAL
