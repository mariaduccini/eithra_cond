"""Conferência do balanço lido: a matemática do documento fecha?

Antes de a gestão olhar o resultado, o Eithra confere o que dá para conferir
sozinho: a soma das despesas bate com o total declarado? E a das receitas? O
saldo bate com o que entrou e saiu? E há algo repetido por engano?

Tudo aqui é determinístico e opera sobre o contrato já montado — nenhuma
leitura de PDF acontece neste módulo. A regra é a mesma em todo o Eithra: só se
aponta o que se pode afirmar. Quando o documento não declara um total, ou usa
uma estrutura contábil que a equação simples não descreve (várias contas,
fundos), a resposta honesta é **não apontar nada**. Silêncio é resultado
válido.

As diferenças são toleradas até ``TOLERANCIA`` porque o valor de cada linha já
foi arredondado ao centavo: um resíduo de arredondamento não é divergência, e
transformar isso em ponto de atenção treinaria a gestão a ignorar os avisos.
"""
from __future__ import annotations

from decimal import Decimal

from apps.balancos.contratos import (
    BalancoExtraidoV1,
    Inconsistencia,
    Severidade,
    TipoInconsistencia,
)
from apps.balancos.normalizadores import texto_para_decimal

# Resíduo aceitável entre a soma dos lançamentos e o total declarado, em reais.
# Cobre apenas erro de arredondamento ao centavo — jamais um lançamento inteiro.
TOLERANCIA = Decimal("0.05")


def validar_balanco(balanco: BalancoExtraidoV1) -> tuple[Inconsistencia, ...]:
    """Confere o contrato e devolve os pontos que merecem atenção.

    Devolve uma tupla possivelmente vazia: nada a apontar é o caso comum e
    desejável. Não altera o contrato — quem valida não corrige, apenas relata.
    """
    return (
        *_conferir_despesas(balanco),
        *_conferir_receitas(balanco),
        *_conferir_saldo(balanco),
        *_possiveis_duplicidades(balanco),
    )


def _conferir_despesas(balanco: BalancoExtraidoV1) -> tuple[Inconsistencia, ...]:
    total = _decimal(balanco.despesas_total)
    if total is None or not balanco.despesas:
        return ()

    soma, quantidade = _somar(despesa.valor for despesa in balanco.despesas)
    if quantidade == 0 or abs(soma - total) <= TOLERANCIA:
        return ()

    return (
        Inconsistencia(
            tipo=TipoInconsistencia.TOTAL_DIVERGENTE,
            severidade=Severidade.MEDIA,
            mensagem=(
                f"A soma das despesas lançadas ({_moeda(soma)}) não confere com "
                f"o total do documento ({_moeda(total)})."
            ),
            referencia="despesas",
        ),
    )


def _conferir_receitas(balanco: BalancoExtraidoV1) -> tuple[Inconsistencia, ...]:
    total = _decimal(balanco.receitas_total)
    if total is None or not balanco.receitas:
        return ()

    soma, quantidade = _somar(receita.valor for receita in balanco.receitas)
    if quantidade == 0 or abs(soma - total) <= TOLERANCIA:
        return ()

    return (
        Inconsistencia(
            tipo=TipoInconsistencia.TOTAL_DIVERGENTE,
            severidade=Severidade.MEDIA,
            mensagem=(
                f"A soma das receitas lançadas ({_moeda(soma)}) não confere com "
                f"o total do documento ({_moeda(total)})."
            ),
            referencia="receitas",
        ),
    )


def _conferir_saldo(balanco: BalancoExtraidoV1) -> tuple[Inconsistencia, ...]:
    """Confere ``saldo anterior + receitas - despesas = saldo final``.

    A equação só vale para um documento de conta única. Com mais de um saldo —
    contas, fundos — a estrutura contábil é outra, e forçar a conta de uma
    conta só produziria um aviso falso. Nesse caso, não se aponta nada.
    """
    anterior = _decimal(balanco.saldo_anterior)
    final = _decimal(balanco.saldo_final)
    if anterior is None or final is None or len(balanco.saldos) > 1:
        return ()

    receitas = _total_ou_soma(balanco.receitas_total, balanco.receitas)
    despesas = _total_ou_soma(balanco.despesas_total, balanco.despesas)
    if receitas is None or despesas is None:
        return ()

    calculado = anterior + receitas - despesas
    if abs(calculado - final) <= TOLERANCIA:
        return ()

    return (
        Inconsistencia(
            tipo=TipoInconsistencia.TOTAL_DIVERGENTE,
            severidade=Severidade.MEDIA,
            mensagem=(
                f"Saldo anterior mais receitas menos despesas dá {_moeda(calculado)}, "
                f"mas o documento informa saldo final de {_moeda(final)}."
            ),
            referencia="saldo",
        ),
    )


def _possiveis_duplicidades(balanco: BalancoExtraidoV1) -> tuple[Inconsistencia, ...]:
    """Aponta despesas iguais repetidas na mesma leitura.

    Mesma descrição, mesmo valor e mesma data em duas linhas costuma ser o mesmo
    lançamento lido duas vezes. Pode ser legítimo — duas passagens iguais no
    mesmo dia —, por isso a severidade é baixa: é um pedido de olhada, não uma
    acusação.
    """
    vistos = {}
    repetidos = []
    for despesa in balanco.despesas:
        if not despesa.descricao_normalizada or not despesa.valor:
            continue
        chave = (despesa.descricao_normalizada, despesa.valor, despesa.data)
        if chave in vistos:
            repetidos.append(despesa)
        else:
            vistos[chave] = despesa

    return tuple(
        Inconsistencia(
            tipo=TipoInconsistencia.POSSIVEL_DUPLICIDADE,
            severidade=Severidade.BAIXA,
            mensagem=(
                f"“{despesa.descricao_original}” aparece mais de uma vez com o "
                f"mesmo valor e a mesma data."
            ),
            referencia=despesa.descricao_normalizada,
            procedencia=despesa.procedencia,
        )
        for despesa in repetidos
    )


def _total_ou_soma(total_declarado: str | None, lancamentos) -> Decimal | None:
    """Prefere o total declarado; sem ele, soma os lançamentos lidos."""
    numero = _decimal(total_declarado)
    if numero is not None:
        return numero

    soma, quantidade = _somar(item.valor for item in lancamentos)
    return soma if quantidade else None


def _decimal(texto: str | None) -> Decimal | None:
    if not texto:
        return None
    return texto_para_decimal(texto)


def _somar(valores) -> tuple[Decimal, int]:
    """Soma os valores que puder converter e conta quantos entraram.

    O contador importa: uma soma de zero lançamentos lidos não é "zero real" e
    não deve ser comparada com total nenhum.
    """
    total = Decimal("0.00")
    quantidade = 0
    for valor in valores:
        numero = _decimal(valor)
        if numero is None:
            continue
        total += numero
        quantidade += 1
    return total, quantidade


def _moeda(valor: Decimal) -> str:
    """Formata um valor em reais para a mensagem exibida à gestão."""
    texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"
