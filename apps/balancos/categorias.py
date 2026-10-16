"""Catálogo de categorias do Eithra e as regras que levam até ele.

O balanço chega com o agrupamento de quem o escreveu — e esse agrupamento é
preservado. O que se acrescenta aqui é uma leitura própria do Eithra, por
regras determinísticas, para que meses de administradoras diferentes possam ser
comparados entre si.

Duas regras governam este módulo:

* **nada é substituído em silêncio** — a categoria original do documento
  continua guardada ao lado da categoria do Eithra;
* **não se adivinha** — sem evidência textual, a resposta é
  ``NAO_CLASSIFICADO``, que é uma resposta legítima e não um erro.

O catálogo é pequeno de propósito. Uma lista longa parece detalhada e vira
inútil: ninguém confere trinta categorias, e a classificação erra mais.
"""
from __future__ import annotations

import re

from django.db import models

from apps.balancos.normalizadores import chave_de_comparacao


class CategoriaEithra(models.TextChoices):
    """Categorias comparáveis entre meses e entre administradoras."""

    PESSOAL = "pessoal", "Pessoal"
    AGUA = "agua", "Água"
    ENERGIA = "energia", "Energia"
    GAS = "gas", "Gás"
    ELEVADORES = "elevadores", "Elevadores"
    LIMPEZA = "limpeza", "Limpeza"
    PORTARIA_SEGURANCA = "portaria_seguranca", "Portaria e segurança"
    MANUTENCAO = "manutencao", "Manutenção"
    SERVICOS = "servicos", "Serviços"
    ADMINISTRACAO = "administracao", "Administração"
    SEGUROS = "seguros", "Seguros"
    TRIBUTOS_TAXAS = "tributos_taxas", "Tributos e taxas"
    OBRAS_MELHORIAS = "obras_melhorias", "Obras e melhorias"
    CONSUMO_MATERIAIS = "consumo_materiais", "Consumo e materiais"
    TECNOLOGIA = "tecnologia", "Tecnologia"
    EXTRAORDINARIAS = "extraordinarias", "Extraordinárias"
    OUTROS = "outros", "Outros"
    NAO_CLASSIFICADO = "nao_classificado", "A classificar"


# A ordem é a regra. A primeira correspondência vence, e por isso o específico
# vem antes do genérico: "manutenção mensal do elevador" é Elevadores, e não
# Manutenção; "taxa de administração" é Administração, e não Tributos.
REGRAS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        CategoriaEithra.ELEVADORES,
        ("elevador", "elevadores", "ascensor", "ascensores"),
    ),
    (
        CategoriaEithra.AGUA,
        (
            "agua",
            "aguas",
            "esgoto",
            "esgotos",
            "saneamento",
            "hidrometro",
            "abastecimento",
        ),
    ),
    (
        CategoriaEithra.ENERGIA,
        # "elétrica" fica de fora de propósito: sozinha, ela aparece tanto em
        # "instalação elétrica" quanto em "cerca elétrica", e levaria a
        # classificação para o lugar errado.
        ("energia", "energias", "luz", "iluminacao"),
    ),
    (
        CategoriaEithra.GAS,
        ("gas", "gases", "gasoduto"),
    ),
    (
        CategoriaEithra.PORTARIA_SEGURANCA,
        (
            "portaria",
            "porteiro",
            "porteiros",
            "seguranca",
            "vigilancia",
            "vigilante",
            "vigilantes",
            "monitoramento",
            "cerca",
            "portao",
            "portoes",
            "interfone",
            "ronda",
            "rondas",
        ),
    ),
    (
        CategoriaEithra.LIMPEZA,
        (
            "limpeza",
            "faxina",
            "higienizacao",
            "dedetizacao",
            "desinsetizacao",
            "desratizacao",
            "lixo",
            "residuos",
            "conservacao",
        ),
    ),
    (
        CategoriaEithra.PESSOAL,
        (
            "salario",
            "salarios",
            "folha",
            "encargos",
            "inss",
            "fgts",
            "ferias",
            "decimo terceiro",
            "vale transporte",
            "vale refeicao",
            "vale alimentacao",
            "funcionario",
            "funcionarios",
            "empregado",
            "empregados",
            "colaborador",
            "colaboradores",
            "rescisao",
            "horas extras",
            "adicional noturno",
            "convencao coletiva",
        ),
    ),
    (
        CategoriaEithra.SEGUROS,
        ("seguro", "seguros", "apolice", "seguradora"),
    ),
    (
        CategoriaEithra.ADMINISTRACAO,
        (
            "administradora",
            "administracao",
            "honorario",
            "honorarios",
            "contabilidade",
            "contabil",
            "assessoria",
            "advogado",
            "juridico",
            "auditoria",
            "sindico profissional",
            "gestao condominial",
        ),
    ),
    (
        CategoriaEithra.TRIBUTOS_TAXAS,
        (
            "iptu",
            "tributo",
            "tributos",
            "imposto",
            "impostos",
            "taxa",
            "taxas",
            "tarifa",
            "tarifas",
            "iss",
            "irrf",
            "alvara",
            "bombeiros",
        ),
    ),
    (
        CategoriaEithra.TECNOLOGIA,
        (
            "internet",
            "telefonia",
            "telefone",
            "sistema",
            "sistemas",
            "software",
            "informatica",
            "tecnologia",
            "camera",
            "cameras",
            "cftv",
            "alarme",
            "antena",
            "aplicativo",
        ),
    ),
    (
        CategoriaEithra.MANUTENCAO,
        (
            "manutencao",
            "manutencoes",
            "reparo",
            "reparos",
            "conserto",
            "consertos",
            "hidraulica",
            "pintura",
            "alvenaria",
            "solda",
            "revisao",
            "lubrificacao",
            "preventiva",
            "corretiva",
        ),
    ),
    (
        CategoriaEithra.OBRAS_MELHORIAS,
        (
            "obra",
            "obras",
            "reforma",
            "reformas",
            "melhoria",
            "melhorias",
            "construcao",
            "ampliacao",
            "modernizacao",
            "retrofit",
            "benfeitoria",
            "benfeitorias",
        ),
    ),
    (
        CategoriaEithra.CONSUMO_MATERIAIS,
        (
            "material",
            "materiais",
            "consumo",
            "consumos",
            "peca",
            "pecas",
            "ferramenta",
            "ferramentas",
            "insumo",
            "insumos",
            "lampada",
            "tinta",
            "papelaria",
        ),
    ),
    (
        CategoriaEithra.SERVICOS,
        ("servico", "servicos", "terceirizado", "terceirizados"),
    ),
    (
        CategoriaEithra.EXTRAORDINARIAS,
        (
            "emergencial",
            "extraordinario",
            "extraordinaria",
            "extraordinarias",
            "eventual",
            "eventuais",
            "pontual",
            "pontuais",
            "sinistro",
            "sinistros",
        ),
    ),
    (
        CategoriaEithra.OUTROS,
        ("diversos", "diversas", "outros", "outras"),
    ),
)


def categorizar(descricao: str, *, categoria_original: str | None = None) -> str:
    """Diz a qual categoria do Eithra um lançamento pertence.

    O agrupamento do próprio documento é considerado primeiro: se a
    administradora já classificou o lançamento, essa é a melhor pista
    disponível. Sem ele, ou sem correspondência, a descrição decide. Sem
    nenhuma pista, a resposta é ``NAO_CLASSIFICADO``.
    """
    for fonte in (categoria_original, descricao):
        encontrada = _primeira_correspondencia(fonte)
        if encontrada:
            return encontrada

    return CategoriaEithra.NAO_CLASSIFICADO


def _primeira_correspondencia(texto: str | None) -> str | None:
    """Procura os termos da regra na descrição, respeitando palavra inteira.

    Palavra inteira e não trecho solto: sem isso, ``agua`` acharia
    "aguardando" e ``obra`` acharia "obra-prima". A classificação errada é pior
    do que a ausência de classificação.
    """
    alvo = chave_de_comparacao(texto or "")
    if not alvo:
        return None

    for categoria, termos in REGRAS:
        for termo in termos:
            padrao = rf"(?<![a-z0-9]){re.escape(termo)}(?![a-z0-9])"
            if re.search(padrao, alvo):
                return categoria

    return None


def rotulo(categoria: str) -> str:
    """Nome humano da categoria, para a tela. Nunca o código técnico."""
    try:
        return CategoriaEithra(categoria).label
    except ValueError:
        return "A classificar"
