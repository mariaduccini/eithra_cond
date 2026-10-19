"""Constantes do domínio de cadastros básicos operacionais.

Local, equipamento e fornecedor são o que dá substância à operação: onde as
coisas acontecem, o que existe nesses lugares e quem atende o condomínio.

Não há catálogo nacional de tipos aqui. A lista é curta de propósito: serve
para agrupar e encontrar, não para descrever cada canto do condomínio.
"""
from django.db import models


class TipoLocal(models.TextChoices):
    """Que espécie de lugar é este.

    O suficiente para agrupar e filtrar. O que não couber em nenhuma das
    opções fica em "Outro" — e continua sendo um local legítimo.
    """

    AREA_COMUM = "area_comum", "Área comum"
    GARAGEM = "garagem", "Garagem"
    ELEVADOR = "elevador", "Elevador"
    PORTARIA = "portaria", "Portaria"
    PISCINA = "piscina", "Piscina"
    FACHADA = "fachada", "Fachada"
    CASA_DE_MAQUINAS = "casa_de_maquinas", "Casa de máquinas"
    COBERTURA = "cobertura", "Cobertura"
    ADMINISTRATIVO = "administrativo", "Administrativo"
    OUTRO = "outro", "Outro"


class TipoPessoa(models.TextChoices):
    """Se o fornecedor é pessoa física ou jurídica.

    Muitas vezes isso não se sabe no primeiro contato. "Não informado" é uma
    resposta honesta: o cadastro existe e serve sem ela.
    """

    PF = "pf", "Pessoa física"
    PJ = "pj", "Pessoa jurídica"
    NAO_INFORMADO = "nao_informado", "Não informado"
