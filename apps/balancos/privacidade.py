"""O que do demonstrativo nunca chega ao morador.

O balancete traz linhas que identificam pessoas: a citação de um apartamento
numa cobrança, um processo judicial, a folha de quem trabalha no condomínio. Ao
morador, essas linhas não aparecem — mas o valor delas continua somado ao total
do mês. O que se esconde é a identificação, nunca o dinheiro: a linha vira
"Outras despesas".

A regra é determinística e mora só aqui. Nada de IA decide o que é privado: o
que não casa com os padrões abaixo continua visível, e é preferível deixar
passar uma linha inofensiva a esconder, por engano, uma despesa inteira.
"""
from __future__ import annotations

import re

from apps.balancos.normalizadores import chave_de_comparacao

# Rótulo que substitui a linha identificável na visão do morador.
ROTULO_PRIVADO = "Outras despesas"

# Termos que sempre identificam pessoa ou coisa de alguém. A comparação acontece
# sobre a chave normalizada (sem acento, em minúsculas); por isso os termos aqui
# já vêm sem acento. O ``\b`` evita falsos positivos por dentro de outra palavra
# — "vigia" não casa dentro de "vigilância".
_TERMOS = (
    "processo judicial",
    "acao judicial",
    "judicial",
    "salario",
    "salarios",
    "salarial",
    "folha de pagamento",
    "folha de ponto",
    "decimo terceiro",
    "ferias",
    "fgts",
    "inss",
    "rescisao",
    "encargos trabalhistas",
    "encargos sociais",
    "vale transporte",
    "vale alimentacao",
    "vale refeicao",
    "plano de saude",
    "funcionario",
    "funcionarios",
    "colaborador",
    "colaboradores",
    "empregado",
    "empregados",
    "celetista",
    "porteiro",
    "porteiros",
    "zelador",
    "zeladora",
    "faxineiro",
    "faxineira",
    "servente",
    "copeira",
    "vigia",
    "jardineiro",
    "jardineira",
    "auxiliar de limpeza",
    "auxiliar de servicos",
)

# "apto 103", "apto. 103", "ap 103", "apartamento 103" — a unidade citada na
# própria linha. É o que transforma uma cobrança em dado de morador.
_RE_UNIDADE = re.compile(r"\bap(?:to|artamento|art)?\.?\s*n?[º°o]?\.?\s*\d+")

_TERMOS_RE = re.compile(r"\b(?:" + "|".join(_TERMOS) + r")\b")


def linha_privada(descricao: str | None) -> bool:
    """Se a linha identifica pessoa ou unidade e não deve ir ao morador."""
    chave = chave_de_comparacao(descricao or "")
    if not chave:
        return False
    if _RE_UNIDADE.search(chave):
        return True
    return bool(_TERMOS_RE.search(chave))


def linha_para_o_morador(descricao: str | None) -> str:
    """A descrição como o morador a lê: a identificação vira "Outras despesas"."""
    if linha_privada(descricao):
        return ROTULO_PRIVADO
    return (descricao or "").strip()
