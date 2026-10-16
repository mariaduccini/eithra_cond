"""Natureza da despesa: o que é comportamento, não valor.

Recorrência não se confunde com variação do valor. Uma conta de energia é
recorrente — chega todo mês — e varia de valor; a troca de uma peça é
extraordinária, mesmo que apareça em dois meses seguidos. Por isso a natureza
tem quatro respostas e não apenas "fixa" e "variável":

* ``RECORRENTE_FIXA`` — chega todo mês com valor previsível, e o documento diz
  isso ("mensalidade", "contrato", "parcela mensal");
* ``RECORRENTE_VARIAVEL`` — chega todo mês, mas o valor acompanha o consumo
  (água, energia, gás);
* ``EXTRAORDINARIA`` — aconteceu, não é previsível (troca, reparo, obra);
* ``NAO_DETERMINADA`` — não há evidência suficiente.

``NAO_DETERMINADA`` não é fracasso: é a resposta honesta enquanto não houver
histórico. A classificação é textual e determinística. Não se conclui que algo
é recorrente só porque o fornecedor é sempre o mesmo — quem é recorrente é a
despesa, não quem a presta.
"""
from __future__ import annotations

import re

from django.db import models

from apps.balancos.categorias import CategoriaEithra
from apps.balancos.normalizadores import chave_de_comparacao


class NaturezaDespesa(models.TextChoices):
    """Comportamento da despesa ao longo dos meses."""

    RECORRENTE_FIXA = "recorrente_fixa", "Recorrente"
    RECORRENTE_VARIAVEL = "recorrente_variavel", "Recorrente variável"
    EXTRAORDINARIA = "extraordinaria", "Extra"
    NAO_DETERMINADA = "nao_determinada", "A confirmar"


# Evidência forte de compromisso mensal.
TERMOS_RECORRENTE_FIXA = (
    "mensal",
    "mensais",
    "mensalidade",
    "mensalidades",
    "contrato",
    "contratos",
    "contratual",
    "assinatura",
    "aluguel",
    "locacao",
    "franquia",
    "folha de pagamento",
    "salario",
    "salarios",
    "encargos",
    "prestacao",
)

# Evidência de que o valor segue o consumo, e não um contrato.
TERMOS_RECORRENTE_VARIAVEL = (
    "consumo",
    "consumos",
    "leitura",
    "medicao",
    "fatura",
    "faturas",
)

# Evidência de que a despesa não se repete como rotina.
TERMOS_EXTRAORDINARIA = (
    "troca",
    "trocas",
    "substituicao",
    "reparo",
    "reparos",
    "conserto",
    "consertos",
    "obra",
    "obras",
    "reforma",
    "reformas",
    "instalacao",
    "instalacoes",
    "emergencial",
    "eventual",
    "eventuais",
    "pontual",
    "pontuais",
    "peca",
    "pecas",
    "placa",
    "placas",
    "equipamento",
    "modernizacao",
    "ampliacao",
    "sinistro",
)

# Categorias que, por natureza, acompanham consumo: mesmo sem a palavra
# "consumo" na linha, uma conta de água é recorrente e variável.
CATEGORIAS_DE_CONSUMO = frozenset(
    {
        CategoriaEithra.AGUA,
        CategoriaEithra.ENERGIA,
        CategoriaEithra.GAS,
    }
)


def classificar_natureza(
    descricao: str, *, categoria_eithra: str | None = None
) -> str:
    """Sugere o comportamento da despesa a partir do que o documento diz.

    Quando o texto traz sinais contraditórios — "troca de peça" e "parcela
    mensal" na mesma linha —, a resposta é ``NAO_DETERMINADA``. Preferir a
    dúvida honesta a uma certeza inventada é o que mantém a conferência da
    gestão confiável.
    """
    alvo = chave_de_comparacao(descricao or "")

    compromisso = _contem(alvo, TERMOS_RECORRENTE_FIXA)
    pontual = _contem(alvo, TERMOS_EXTRAORDINARIA)
    por_consumo = _contem(alvo, TERMOS_RECORRENTE_VARIAVEL)

    if compromisso and pontual:
        return NaturezaDespesa.NAO_DETERMINADA
    if pontual:
        return NaturezaDespesa.EXTRAORDINARIA
    if compromisso:
        return NaturezaDespesa.RECORRENTE_FIXA
    if por_consumo or categoria_eithra in CATEGORIAS_DE_CONSUMO:
        return NaturezaDespesa.RECORRENTE_VARIAVEL

    return NaturezaDespesa.NAO_DETERMINADA


def _contem(alvo: str, termos: tuple[str, ...]) -> bool:
    """Procura os termos em palavra inteira, como na categorização."""
    for termo in termos:
        padrao = rf"(?<![a-z0-9]){re.escape(termo)}(?![a-z0-9])"
        if re.search(padrao, alvo):
            return True
    return False


def rotulo(natureza: str) -> str:
    """Nome humano da natureza, para a tela."""
    try:
        return NaturezaDespesa(natureza).label
    except ValueError:
        return "A confirmar"
