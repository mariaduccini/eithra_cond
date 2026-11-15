"""Apoio aos testes da capacidade transversal de comunicação.

Reúne só o que os testes repetem: condomínios, pessoas, vínculos, e a criação de
uma demanda, de uma manutenção e de um comunicado pelos serviços oficiais de
cada domínio. O material neutro — o armazenamento em memória e as fotos — vem da
camada compartilhada e é reexportado aqui, para não haver uma segunda cópia.

Nenhum dado real entra aqui: nomes, e-mails e títulos são de teste.
"""
from apps.comunicacao import services as comunicacao_services
from apps.demandas import services as demandas_services
from apps.demandas.tests.apoio import (  # noqa: F401  (reexportado aos testes)
    StorageDeMentira,
    criar_condominio,
    criar_equipamento,
    criar_local,
    criar_pessoa,
    criar_vinculo,
    montar_condominio_com,
)
from apps.manutencoes import services as manutencoes_services


def criar_demanda(condominio, autor, titulo="Vazamento na garagem", **campos):
    """Registra uma demanda pelo serviço oficial, para servir de origem."""
    return demandas_services.criar_demanda(
        condominio=condominio, criado_por=autor, titulo=titulo, **campos
    )


def criar_manutencao(condominio, autor, titulo="Troca da bomba do reservatório", **campos):
    """Registra uma manutenção pelo serviço oficial, para servir de origem."""
    return manutencoes_services.criar_manutencao(
        condominio=condominio, criado_por=autor, titulo=titulo, **campos
    )


def criar_comunicacao(condominio, autor, **campos):
    """Registra um comunicado pelo serviço oficial do domínio."""
    dados = {"titulo": "Aviso aos moradores"}
    dados.update(campos)
    return comunicacao_services.criar_comunicacao(
        condominio=condominio, criado_por=autor, **dados
    )
