"""Constantes do domínio de comunidade.

Unidade, pessoa e veículo são o lado humano e patrimonial do condomínio: quem
mora, quem responde e o que circula. O que dá sentido a esses cadastros é o
vínculo — um fato com início e, quando chega ao fim, uma data de encerramento.

As listas aqui são curtas de propósito. Elas existem para agrupar e encontrar,
não para descrever cada situação possível: o que não couber em nenhuma das
opções fica em "Outro" e continua sendo um vínculo legítimo.
"""
from django.db import models


class TipoVinculoPessoaUnidade(models.TextChoices):
    """O que liga uma pessoa a uma unidade.

    O papel é do vínculo, e não da pessoa: a mesma pessoa pode ser proprietária
    de uma unidade e moradora de outra, e isso muda com o tempo. Nenhum destes
    papéis, por si só, concede acesso ou autorização dentro do condomínio —
    isso pertence a outra decisão, em outra fase.

    "Funcionário da unidade" é dito por inteiro de propósito: é quem trabalha
    para a unidade (a diarista da casa), e não funcionário do condomínio.
    """

    PROPRIETARIO = "proprietario", "Proprietário"
    MORADOR = "morador", "Morador"
    LOCATARIO = "locatario", "Locatário"
    FAMILIAR = "familiar", "Familiar"
    FUNCIONARIO_UNIDADE = "funcionario_unidade", "Funcionário da unidade"
    OUTRO = "outro", "Outro"


class OrigemInformacao(models.TextChoices):
    """De onde veio uma informação importante.

    O Eithra não infere origem: o que não foi cadastrado por alguém aqui veio de
    algum lugar, e esse lugar precisa ser dito. A lista é curta e limitada para
    que a pergunta "de onde veio isto?" tenha sempre a mesma resposta possível.
    """

    CADASTRO = "cadastro", "Cadastro administrativo"
    IMPORTACAO = "importacao", "Importação"
    DECLARACAO = "declaracao", "Declaração do morador"
    INTEGRACAO = "integracao", "Integração"
