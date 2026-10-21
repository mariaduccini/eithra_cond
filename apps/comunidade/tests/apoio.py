"""Apoio aos testes da comunidade.

Reúne só o que os testes repetem: unidades, pessoas, veículos e os vínculos que
os ligam. Nenhum dado real entra aqui — nomes, placas e telefones são de teste.
"""
from datetime import date, timedelta

from apps.comunidade.models import (
    Pessoa,
    Unidade,
    Veiculo,
    VinculoPessoaUnidade,
    VinculoVeiculoPessoa,
    VinculoVeiculoUnidade,
)

HOJE = date(2026, 6, 15)

# O início padrão de um vínculo de teste fica para trás: um vínculo que existe
# hoje começou antes de hoje. Assim um fim recente continua coerente com a regra
# de que o fim nunca é anterior ao início.
INICIO_PADRAO = HOJE - timedelta(days=30)


def criar_unidade(condominio, identificador="101", **campos):
    """Uma unidade de teste no condomínio informado."""
    return Unidade.objects.create(
        condominio=condominio, identificador=identificador, **campos
    )


def criar_pessoa_da_comunidade(condominio, nome="Pessoa Exemplo 01", **campos):
    """Uma pessoa de teste no condomínio informado.

    O nome distingue-se do apoio dos cadastros, que cria a conta de acesso.
    """
    return Pessoa.objects.create(condominio=condominio, nome=nome, **campos)


def criar_veiculo(condominio, placa="ABC1D23", **campos):
    """Um veículo de teste no condomínio informado."""
    return Veiculo.objects.create(condominio=condominio, placa=placa, **campos)


def criar_vinculo_pessoa_unidade(
    condominio, pessoa, unidade, **campos
):
    """Liga uma pessoa a uma unidade, vigente desde INICIO_PADRAO."""
    padrao = {"data_inicio": INICIO_PADRAO}
    return VinculoPessoaUnidade.objects.create(
        condominio=condominio, pessoa=pessoa, unidade=unidade, **{**padrao, **campos}
    )


def criar_vinculo_veiculo_pessoa(condominio, veiculo, pessoa, **campos):
    """Liga um veículo a uma pessoa, vigente desde INICIO_PADRAO."""
    padrao = {"data_inicio": INICIO_PADRAO}
    return VinculoVeiculoPessoa.objects.create(
        condominio=condominio, veiculo=veiculo, pessoa=pessoa, **{**padrao, **campos}
    )


def criar_vinculo_veiculo_unidade(condominio, veiculo, unidade, **campos):
    """Liga um veículo a uma unidade, vigente desde INICIO_PADRAO."""
    padrao = {"data_inicio": INICIO_PADRAO}
    return VinculoVeiculoUnidade.objects.create(
        condominio=condominio, veiculo=veiculo, unidade=unidade, **{**padrao, **campos}
    )


def ontem() -> date:
    """Um dia antes da referência dos testes."""
    return HOJE - timedelta(days=1)


def amanha() -> date:
    """Um dia depois da referência dos testes."""
    return HOJE + timedelta(days=1)
