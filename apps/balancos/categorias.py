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


class SistemaManutencao(models.TextChoices):
    """O sistema do prédio que uma despesa de manutenção alcança.

    É uma lente a mais sobre a mesma despesa: a categoria diz de que natureza
    é o gasto, o sistema diz onde ele acontece. Serve para separar a manutenção
    por equipamento — quanto foi o elevador, quanto foi a bomba — sem mexer no
    valor nem no total oficial.
    """

    ELEVADORES = "elevadores", "Elevadores"
    BOMBAS_E_AGUA = "bombas_e_agua", "Bombas e água"
    GERADOR = "gerador", "Gerador"
    PISCINA = "piscina", "Piscina"
    PORTAO_E_ACESSO = "portao_e_acesso", "Portão e acesso"
    ELETRICA = "eletrica", "Elétrica"
    HIDRAULICA = "hidraulica", "Hidráulica"
    INCENDIO = "incendio", "Incêndio"
    LIMPEZA_E_JARDIM = "limpeza_e_jardim", "Limpeza e jardim"
    OUTROS = "outros", "Outros"


# O sistema que cada termo indica, do mais específico para o mais genérico. O
# fornecedor é consultado antes da descrição: quem é "BSA" ou "JM Piscina" já diz
# a que sistema o gasto pertence, mesmo quando a linha não repete a palavra.
SISTEMAS_POR_TERMO: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        SistemaManutencao.ELEVADORES,
        (
            "elevador",
            "elevadores",
            "ascensor",
            "ascensores",
            "atlas",
            "schindler",
            "thyssenkrupp",
            "otis",
        ),
    ),
    (
        SistemaManutencao.GERADOR,
        ("gerador", "geradores", "eletrogeno", "grupo gerador", "ps eletro"),
    ),
    (
        SistemaManutencao.PISCINA,
        ("piscina", "piscinas", "jm piscina", "raia", "raias"),
    ),
    (
        SistemaManutencao.BOMBAS_E_AGUA,
        (
            "bomba",
            "bombas",
            "bsa",
            "agua",
            "aguas",
            "esgoto",
            "esgotos",
            "poco",
            "pocos",
            "reservatorio",
            "hidrometro",
            "abastecimento",
        ),
    ),
    (
        SistemaManutencao.PORTAO_E_ACESSO,
        (
            "portao",
            "portoes",
            "cancela",
            "cancelas",
            "acesso",
            "interfone",
            "fechadura",
            "trava",
            "controle de acesso",
        ),
    ),
    (
        SistemaManutencao.INCENDIO,
        (
            "incendio",
            "incendios",
            "extintor",
            "extintores",
            "mangueira",
            "mangueiras",
            "hidrante",
            "hidrantes",
            "sprinkler",
            "avcb",
            "fumaca",
        ),
    ),
    (
        SistemaManutencao.ELETRICA,
        (
            "eletrica",
            "eletrico",
            "eletricas",
            "eletricos",
            "iluminacao",
            "disjuntor",
            "quadro de distribuicao",
        ),
    ),
    (
        SistemaManutencao.HIDRAULICA,
        ("hidraulica", "hidraulico", "vazamento", "vazamentos", "encanamento", "tubulacao"),
    ),
    (
        SistemaManutencao.LIMPEZA_E_JARDIM,
        (
            "limpeza",
            "faxina",
            "jardim",
            "jardins",
            "jardinagem",
            "paisagismo",
            "grama",
            "poda",
            "rocagem",
        ),
    ),
)


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
            "mao de obra",
            "locacao de mao de obra",
            "terceirizacao",
            "terceirizada",
            "prestacao de servicos com locacao",
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


def categorizar(
    descricao: str,
    *,
    categoria_original: str | None = None,
    regra: str | None = None,
    categoria_do_fornecedor: str | None = None,
) -> str:
    """Diz a qual categoria do Eithra um lançamento pertence.

    A ordem das pistas é a ordem das decisões: primeiro a regra que o
    condomínio já ensinou, depois o fornecedor com contrato ativo, depois as
    palavras da descrição e, só quando elas não dizem nada, as palavras do
    agrupamento do documento. O grupo é a pista mais fraca de propósito:
    "Telefone" dentro de "Água, luz e telefone" é Tecnologia, e não Água — a
    linha é que diz de que se trata, não o título do bloco. Sem nenhuma pista,
    a resposta é ``NAO_CLASSIFICADO``.
    """
    if regra:
        return regra
    if categoria_do_fornecedor:
        return categoria_do_fornecedor

    encontrada = _primeira_correspondencia(descricao)
    if encontrada:
        return encontrada

    encontrada = _primeira_correspondencia(categoria_original)
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
            if termo in ("obra", "obras"):
                # "mão de obra" é Pessoal: o "obra" solto não pode levar a Obras.
                padrao = rf"(?<![a-z0-9])(?<!mao de ){re.escape(termo)}(?![a-z0-9])"
            else:
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


def rotulo_sistema(sistema: str) -> str:
    """Nome humano do sistema, para a tela. Vazio quando não há sistema."""
    try:
        return SistemaManutencao(sistema).label
    except (ValueError, TypeError):
        return ""


# -- Sistemas de manutenção -----------------------------------------------------


def sistema_para(descricao: str, *, fornecedor: str | None = None) -> str | None:
    """O sistema de manutenção que a linha indica, quando dá para dizer.

    O fornecedor decide primeiro e a descrição depois: quem já é "BSA" ou "JM
    Piscina" aponta o sistema mesmo quando a linha não repete a palavra. Sem
    pista nenhuma, a resposta é ``None`` — não se inventa um sistema.
    """
    for fonte in (fornecedor, descricao):
        encontrado = _primeiro_sistema(fonte)
        if encontrado:
            return encontrado
    return None


def _primeiro_sistema(texto: str | None) -> str | None:
    """Procura os termos de sistema no texto, respeitando palavra inteira."""
    alvo = chave_de_comparacao(texto or "")
    if not alvo:
        return None
    for sistema, termos in SISTEMAS_POR_TERMO:
        for termo in termos:
            if re.search(rf"(?<![a-z0-9]){re.escape(termo)}(?![a-z0-9])", alvo):
                return sistema
    return None


# -- Regras aprendidas e fornecedor com contrato --------------------------------


def chave_de_regra(descricao: str | None) -> str:
    """A chave de uma regra: a descrição sem números nem datas.

    Tirar números é o que faz o mesmo lançamento de dois meses diferentes virar
    a mesma chave — "Taxa 01/2026" e "Taxa 02/2026" são a mesma despesa.
    """
    base = re.sub(r"\d+", " ", chave_de_comparacao(descricao or ""))
    return " ".join(base.split())


def chaves_de_regra(
    *, descricao: str | None = None, fornecedor: str | None = None
) -> list[str]:
    """As chaves possíveis de uma linha, do mais específico para o mais amplo.

    A regra pode ter nascido do fornecedor ou da descrição; por isso as duas
    são procuradas, com o fornecedor primeiro — ele é a pista mais estável.
    """
    chaves: list[str] = []
    for valor in (fornecedor, descricao):
        chave = chave_de_regra(valor)
        if chave and chave not in chaves:
            chaves.append(chave)
    return chaves


def chave_da_regra(*, descricao: str | None, fornecedor: str | None = None) -> str:
    """A chave com que uma regra nova é gravada: o fornecedor, senão a descrição."""
    return chave_de_regra(fornecedor) or chave_de_regra(descricao)


def categoria_para(
    condominio,
    *,
    descricao: str,
    categoria_original: str | None = None,
    fornecedor: str | None = None,
    documento: str | None = None,
) -> str:
    """A categoria do Eithra para um lançamento, na ordem das decisões.

    Reúne as pistas que dependem do condomínio — a regra que ele já ensinou e
    o contrato ativo do fornecedor — e entrega as demais para ``categorizar``.
    Sem condomínio, cai só nas regras de texto, que é o comportamento seguro.
    """
    regra = _regra_aprendida(condominio, descricao=descricao, fornecedor=fornecedor)
    if regra is not None:
        return categorizar(
            descricao,
            categoria_original=categoria_original,
            regra=regra.categoria,
        )

    do_contrato = _categoria_do_contrato(
        condominio, fornecedor=fornecedor, documento=documento
    )
    return categorizar(
        descricao,
        categoria_original=categoria_original,
        categoria_do_fornecedor=do_contrato,
    )


def _regra_aprendida(condominio, *, descricao, fornecedor=None):
    """A primeira regra que o condomínio já aprendeu para esta linha."""
    if condominio is None or not getattr(condominio, "pk", None):
        return None
    from apps.balancos.models import RegraClassificacao

    for chave in chaves_de_regra(descricao=descricao, fornecedor=fornecedor):
        regra = RegraClassificacao.objects.filter(
            condominio=condominio, chave=chave
        ).first()
        if regra is not None:
            return regra
    return None


def _categoria_do_contrato(condominio, *, fornecedor=None, documento=None) -> str | None:
    """A categoria que o contrato ativo do fornecedor indica, quando há um.

    O contrato não guarda categoria: ela sai do que o próprio contrato diz
    fazer — o objeto e a descrição —, lidos pelos mesmos termos do catálogo.
    """
    if condominio is None or not getattr(condominio, "pk", None):
        return None

    registro = _achar_fornecedor(condominio, fornecedor=fornecedor, documento=documento)
    if registro is None:
        return None

    from apps.governanca.models import ContratoFornecedor

    contrato = (
        ContratoFornecedor.objects.filter(fornecedor=registro, ativo=True)
        .order_by("-data_inicio")
        .first()
    )
    if contrato is None:
        return None
    texto = " ".join(filter(None, [contrato.objeto, contrato.descricao]))
    return _primeira_correspondencia(texto)


def _achar_fornecedor(condominio, *, fornecedor=None, documento=None):
    """O fornecedor cadastrado que corresponde ao nome ou ao documento da linha."""
    from apps.cadastros.models import Fornecedor

    registros = Fornecedor.objects.filter(condominio=condominio)
    if documento:
        achado = registros.filter(documento=documento).first()
        if achado is not None:
            return achado
    if not fornecedor:
        return None

    alvo = chave_de_comparacao(fornecedor)
    if not alvo:
        return None
    for registro in registros:
        nomes = {
            chave_de_comparacao(registro.nome_razao_social or ""),
            chave_de_comparacao(registro.nome_fantasia or ""),
        }
        nomes.discard("")
        if any(nome and (nome in alvo or alvo in nome) for nome in nomes):
            return registro
    return None
