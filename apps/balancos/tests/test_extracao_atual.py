"""Testes da leitura financeira do balanço do modelo atual.

Tudo aqui roda sobre PDFs sintéticos com camada textual controlada: nenhum
arquivo de balanço real entra na suíte e nenhum teste conversa com o storage.
O que se verifica é o que a leitura entende — e, principalmente, o que ela se
recusa a inventar.

Os nomes de condomínio, administradora e fornecedor são fictícios de propósito:
a leitura não conhece nenhum deles, e nenhum deles vira regra.
"""
from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

from django.test import TestCase

from apps.balancos.categorias import CategoriaEithra
from apps.balancos.constants import LayoutBalanco, StatusImportacao
from apps.balancos.contratos import (
    SCHEMA_VERSAO,
    ErroDeContrato,
    Severidade,
    StatusConferencia,
    TipoInconsistencia,
    de_dicionario,
    para_dicionario,
    validar_dicionario,
)
from apps.balancos.detector import ErroDeDeteccaoDeLayout
from apps.balancos.extrator_atual import (
    METODO_RESUMO,
    ErroDeExtracao,
    extrair_balanco_atual,
    ler_balanco_atual,
)
from apps.balancos.models import BalancoMensal, ImportacaoBalanco
from apps.balancos.natureza_despesa import NaturezaDespesa
from apps.balancos.normalizadores import chave_de_comparacao
from apps.balancos.services import receber_importacao
from apps.balancos.tests.apoio import (
    StorageDeMentira,
    criar_pessoa,
    gerar_pdf_textual,
    pdf,
)
from apps.condominios.models import Condominio


# -- Documentos sintéticos ------------------------------------------------------


def pdf_do_modelo_atual() -> bytes:
    """Balanço completo do modelo atual, com todos os blocos que a leitura usa.

    Os números fecham de propósito: a soma das receitas e a das despesas batem
    com os totais declarados, e o saldo obedece à equação. É o caso comum — o
    caso em que a leitura não tem nada a apontar.
    """
    return gerar_pdf_textual(
        "\n".join(
            [
                "Condominio Exemplo",
                "Demonstrativo de Contas",
                "Competencia: Agosto/2026",
                "Periodo: 01/08/2026 a 31/08/2026",
            ]
        ),
        "\n".join(
            [
                "Receitas Detalhadas",
                "Taxa Condominial 45.000,00",
                "Fundo de Reserva 5.000,00",
                "Total de Receitas 50.000,00",
            ]
        ),
        "\n".join(
            [
                "Despesas Detalhadas",
                "Folha de Pagamento 12.000,00",
                "Energia Eletrica 3.450,00",
                "Manutencao do Elevador LTDA 1.200,00",
                "Total de Despesas 16.650,00",
            ]
        ),
        "\n".join(
            [
                "Resumo Financeiro",
                "Saldo Anterior 10.000,00",
                "Saldo Final 43.350,00",
                "Inadimplencia do mes: 4 inadimplentes (3,5%) - R$ 1.500,00",
            ]
        ),
    )


def pdf_com_competencia_conflitante() -> bytes:
    """Dois meses possíveis no mesmo documento: a leitura não escolhe sozinha."""
    return gerar_pdf_textual(
        "Competencia: Julho/2026",
        "\n".join(
            [
                "Competencia: Agosto/2026",
                "Receitas Detalhadas",
                "Taxa Condominial 1.000,00",
            ]
        ),
    )


def pdf_com_total_divergente() -> bytes:
    """A soma das despesas não bate com o total declarado."""
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "Energia Eletrica 1.000,00\nTotal de Despesas 9.999,00",
    )


def pdf_com_despesa_repetida() -> bytes:
    """O mesmo lançamento aparece duas vezes com o mesmo valor."""
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "\n".join(
            [
                "Material de limpeza 200,00",
                "Material de limpeza 200,00",
                "Total de Despesas 400,00",
            ]
        ),
    )


def pdf_com_linha_sem_descricao() -> bytes:
    """Um valor sozinho, sem descrição: não vira lançamento em silêncio."""
    return gerar_pdf_textual("Receitas Detalhadas", "1.234,56")


def pdf_com_agrupamento() -> bytes:
    """O documento agrupa os lançamentos e o agrupamento é preservado."""
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "\n".join(
            [
                "Manutencao",
                "Troca de bomba do recalque 2.500,00",
                "Total de Despesas 2.500,00",
            ]
        ),
    )


def pdf_com_documento_e_parcela() -> bytes:
    """Lançamento com nota e parcela declaradas na própria linha."""
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "Reforma da fachada - NFe 12345 - parcela 3/6 6.000,00\nTotal de Despesas 6.000,00",
    )


def pdf_com_anexos() -> bytes:
    """Comprovantes anexos não podem virar lançamento."""
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "\n".join(
            [
                "Energia Eletrica 1.000,00",
                "Total de Despesas 1.000,00",
                "Documentos Anexos",
                "Comprovante de pagamento 9.999,00",
            ]
        ),
    )


def pdf_sem_secoes() -> bytes:
    """Documento sem nada que a leitura reconheça: ausência é ausência."""
    return gerar_pdf_textual(
        "Aviso interno", "Agenda de reuniao do condominio"
    )


def pdf_com_moldura_colada_ao_total() -> bytes:
    """O rótulo de navegação do relatório chega colado ao total do mês.

    É o caso do demonstrativo real: a camada textual junta na mesma linha o
    total ("TOTAL DAS DESPESAS 3.450,00") e o rótulo de navegação que o gerador
    repete em toda página ("ContasData"). Sem separar moldura de conteúdo, o
    mês fica sem total declarado e o balanço não pode ser confirmado.
    """
    return gerar_pdf_textual(
        "\n".join(
            [
                "Condominio Exemplo",
                "Demonstrativo de Contas",
                "Competencia: Agosto/2026",
                "ContasData",
            ]
        ),
        "\n".join(
            [
                "Receitas Detalhadas",
                "Taxa Condominial 45.000,00",
                "Total de Receitas 45.000,00",
                "ContasData",
            ]
        ),
        "\n".join(
            [
                "Despesas Detalhadas",
                "Energia Eletrica 3.450,00",
                "TOTAL DAS DESPESAS 3.450,00 ContasData",
                "ContasData",
            ]
        ),
        "\n".join(
            [
                "Resumo Financeiro",
                "Saldo Anterior 10.000,00",
                "Saldo Final 51.550,00",
                "ContasData",
            ]
        ),
    )


def pdf_com_valor_em_qualquer_posicao() -> bytes:
    """O valor vem antes ou depois da descrição, às vezes com a data no meio."""
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "\n".join(
            [
                "01/08/2026 Energia Eletrica 45.000,00",
                "01/08/2026 5.000,00 Material de limpeza",
                "2.000,00 01/08/2026 Multa de atraso",
            ]
        ),
    )


def pdf_com_acumulado_e_percentual() -> bytes:
    """Ao lado do lançamento vêm o acumulado do mês e o percentual de rateio."""
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "\n".join(
            [
                "DEB. C/C Folha de Pagamento 12.000,00 35.644,13 55,55%",
                "Total de Despesas 12.000,00",
            ]
        ),
    )


def pdf_com_subtotal_de_categoria() -> bytes:
    """O subtotal de um agrupamento não é o total do mês."""
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "\n".join(
            [
                "Folha de Pagamento 12.000,00",
                "Material de limpeza 200,00",
                "Total de Despesas 12.200,00",
                "TOTAL DA CONTA DESPESA COM PESSOAL 12.000,00",
            ]
        ),
    )


def pdf_com_descricao_multilinha() -> bytes:
    """A linha seguinte, sem valor, completa a descrição do lançamento."""
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "\n".join(
            [
                "Energia Eletrica 3.450,00",
                "NF.: 98765 - concessionaria de energia",
                "Total de Despesas 3.450,00",
            ]
        ),
    )


def pdf_com_cabecalho_repetido() -> bytes:
    """O cabeçalho do relatório se repete em toda página e não é lançamento."""
    return gerar_pdf_textual(
        "\n".join(
            [
                "Demonstrativo de Contas",
                "Despesas Detalhadas",
                "Energia Eletrica 1.000,00",
            ]
        ),
        "\n".join(
            [
                "Demonstrativo de Contas",
                "Data Descricao Valor",
                "Material de limpeza 200,00",
                "Total de Despesas 1.200,00",
            ]
        ),
    )


def pdf_com_resumo_e_detalhe() -> bytes:
    """O resumo repete os valores; só o detalhamento gera lançamento."""
    return gerar_pdf_textual(
        "\n".join(
            [
                "Resumo Financeiro",
                "Taxa Condominial 45.000,00",
                "Energia Eletrica 1.000,00",
            ]
        ),
        "\n".join(
            [
                "Despesas Detalhadas",
                "Energia Eletrica 1.000,00",
                "Total de Despesas 1.000,00",
            ]
        ),
        "\n".join(
            [
                "Receitas Detalhadas",
                "Taxa Condominial 45.000,00",
                "Total de Receitas 45.000,00",
            ]
        ),
    )


def pdf_com_grupos_reconciliados() -> bytes:
    """Dois agrupamentos: um fecha com o subtotal declarado, o outro não.

    O documento declara o subtotal de cada grupo; a leitura confere o que
    encontrou dentro de cada um e mostra a diferença, sem inventar lançamento.
    """
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "\n".join(
            [
                "Manutencao",
                "Troca de bomba do recalque 2.500,00",
                "Reparo de portao 500,00",
                "TOTAL DA CONTA MANUTENCAO 3.000,00",
                "Administracao",
                "Honorarios contabeis 1.200,00",
                "TOTAL DA CONTA ADMINISTRACAO 2.000,00",
                "Total de Despesas 5.000,00",
            ]
        ),
    )


def pdf_com_grupo_sem_lancamento() -> bytes:
    """O documento declara o subtotal de um agrupamento que não rendeu linha."""
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "\n".join(
            [
                "Elevadores",
                "TOTAL DA CONTA ELEVADORES 5.800,00",
                "Total de Despesas 5.800,00",
            ]
        ),
    )


def pdf_com_grupos_que_nao_fecham_no_total() -> bytes:
    """Os subtotais dos grupos fecham entre si, mas não com o total declarado."""
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "\n".join(
            [
                "Manutencao",
                "Reparo de portao 500,00",
                "TOTAL DA CONTA MANUTENCAO 500,00",
                "Total de Despesas 9.000,00",
            ]
        ),
    )


def pdf_com_total_do_mes_rotulado_como_conta() -> bytes:
    """O total do mês vem rotulado como conta e não é subtotal de agrupamento.

    "Total da conta ordinária" fecha o documento, não um setor: tomá-lo por
    subtotal amarraria o mês inteiro ao último agrupamento lido.
    """
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "\n".join(
            [
                "Elevadores",
                "Troca de cabos de tracao 4.600,00",
                "TOTAL DA CONTA ELEVADORES 5.800,00",
                "TOTAL DA CONTA ORDINARIA 9.000,00",
                "Total de Despesas 9.000,00",
            ]
        ),
    )


def pdf_com_leitura_acima_do_declarado() -> bytes:
    """A leitura encontra mais do que o documento declara — e a tela diz isso."""
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "\n".join(
            [
                "Energia Eletrica 3.450,00",
                "Material de limpeza 200,00",
                "Total de Despesas 3.000,00",
            ]
        ),
    )


def pdf_com_tabela_de_colunas() -> bytes:
    """Tabela de movimento: várias colunas de valores na mesma linha.

    A linha com três ou mais valores alinhados não é um lançamento, e o rótulo
    de total do agrupamento pode chegar precedido da data e das colunas.
    """
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "\n".join(
            [
                "Energia Eletrica 3.450,00",
                "Movimento do mes 10.000,00 2.000,00 0,00 12.000,00",
                "25/08/2026 TOTAL DA CONTA DESPESA COM PESSOAL 3.450,00 3.450,00 55,55%",
                "Total de Despesas 3.450,00",
            ]
        ),
    )


# -- Precedência entre resumo e detalhe ----------------------------------------


def pdf_com_apenas_resumo_de_despesas() -> bytes:
    """Sem demonstrativo, o resumo é a única leitura que o documento oferece."""
    return gerar_pdf_textual(
        "\n".join(
            [
                "Resumo de Despesas",
                "Folha de Pagamento 12.000,00",
                "Total de Despesas 12.000,00",
            ]
        ),
    )


def pdf_com_apenas_demonstrativo_de_despesas() -> bytes:
    """Com demonstrativo, cada linha detalhada é um lançamento do mês."""
    return gerar_pdf_textual(
        "\n".join(
            [
                "Demonstrativo de Despesas",
                "Folha de Pagamento 12.000,00",
                "Total de Despesas 12.000,00",
            ]
        ),
    )


def pdf_com_resumo_e_demonstrativo() -> bytes:
    """O resumo antecipa os valores; o demonstrativo lista as despesas."""
    return gerar_pdf_textual(
        "\n".join(
            [
                "Resumo de Despesas",
                "Despesa com Pessoal 12.000,00",
                "Energia Eletrica 4.650,00",
                "Total de Despesas 16.650,00",
            ]
        ),
        "\n".join(
            [
                "Demonstrativo de Despesas",
                "Folha de Pagamento 12.000,00",
                "Energia Eletrica 4.650,00",
                "Total de Despesas 16.650,00",
            ]
        ),
    )


def pdf_com_resumo_e_demonstrativo_iguais() -> bytes:
    """As duas leituras dizem exatamente o mesmo: somá-las seria contar duas vezes."""
    return gerar_pdf_textual(
        "\n".join(
            [
                "Resumo de Despesas",
                "Material de limpeza 200,00",
                "Total de Despesas 200,00",
            ]
        ),
        "\n".join(
            [
                "Demonstrativo de Despesas",
                "Material de limpeza 200,00",
                "Total de Despesas 200,00",
            ]
        ),
    )


def pdf_com_resumo_e_demonstrativo_divergentes() -> bytes:
    """O resumo declara mais do que o demonstrativo detalha.

    A soma segue o detalhe — o valor a mais do resumo não vira lançamento — e a
    diferença declarada aparece como ponto de atenção.
    """
    return gerar_pdf_textual(
        "\n".join(
            [
                "Resumo de Despesas",
                "Folha de Pagamento 12.000,00",
                "Energia Eletrica 4.650,00",
                "Outras Despesas 3.350,00",
                "Total de Despesas 20.000,00",
            ]
        ),
        "\n".join(
            [
                "Demonstrativo de Despesas",
                "Folha de Pagamento 12.000,00",
                "Energia Eletrica 4.650,00",
                "Total de Despesas 20.000,00",
            ]
        ),
    )


def pdf_com_demonstrativo_repetido() -> bytes:
    """O mesmo demonstrativo aparece duas vezes, com outro bloco no meio.

    É o que o RVE faz: o demonstrativo consolidado vem na abertura e é repetido
    destacado mais adiante. Ler as duas cópias conta a mesma despesa duas vezes;
    a leitura fica com a primeira e ignora a segunda.
    """
    bloco = "\n".join(
        [
            "Demonstrativo de Despesas",
            "Folha de Pagamento 12.000,00",
            "Energia Eletrica 4.650,00",
            "Total de Despesas 16.650,00",
        ]
    )
    return gerar_pdf_textual(
        bloco,
        "Resumo Financeiro\nSaldo Final 10.000,00",
        bloco,
    )


def pdf_com_demonstrativo_em_varias_paginas() -> bytes:
    """O demonstrativo se estende por mais de uma página.

    O gerador repete o título "Demonstrativo de Despesas" no topo de cada página
    do mesmo bloco. Esse cabeçalho não é um segundo demonstrativo: as páginas
    seguintes continuam sendo a mesma lista, e todas as despesas valem.
    """
    return gerar_pdf_textual(
        "\n".join(
            [
                "Demonstrativo de Despesas",
                "Folha de Pagamento 12.000,00",
                "Energia Eletrica 4.650,00",
            ]
        ),
        "\n".join(
            [
                "Demonstrativo de Despesas",
                "Manutencao do Elevador 1.200,00",
                "Total de Despesas 17.850,00",
            ]
        ),
    )


def pdf_com_anexo_depois_do_demonstrativo() -> bytes:
    """O comprovante anexo traz valor, mas anexo não é lançamento do mês."""
    return gerar_pdf_textual(
        "\n".join(
            [
                "Demonstrativo de Despesas",
                "Folha de Pagamento 12.000,00",
                "Total de Despesas 12.000,00",
            ]
        ),
        "\n".join(
            [
                "Documentos Comprovantes Anexos",
                "Nota Fiscal 4455 9.999,00",
            ]
        ),
    )


def pdf_com_resumo_e_grupo_detalhado() -> bytes:
    """O grupo traz subtotal e lançamentos; o resumo acima não soma de novo."""
    return gerar_pdf_textual(
        "\n".join(
            [
                "Resumo de Despesas",
                "Manutencao 5.800,00",
                "Total de Despesas 5.800,00",
            ]
        ),
        "\n".join(
            [
                "Demonstrativo de Despesas",
                "Manutencao",
                "Troca de cabos de tracao 4.600,00",
                "Reparo de portao 1.200,00",
                "TOTAL DA CONTA MANUTENCAO 5.800,00",
                "Total de Despesas 5.800,00",
            ]
        ),
    )


# -- Saldos, contas e movimentação financeira ----------------------------------


def pdf_com_bloco_de_conta_bancaria() -> bytes:
    """Um bloco que anuncia saldo conta o que a conta tinha e o que sobrou.

    Nada do que ele lista é despesa do mês: sem reconhecer o bloco, os saldos
    entrariam na soma como se fossem lançamentos.
    """
    return gerar_pdf_textual(
        "\n".join(
            [
                "Despesas Detalhadas",
                "Folha de Pagamento 12.000,00",
                "Total de Despesas 12.000,00",
                "Conta bancaria Saldo atual",
                "Conta Corrente 10.000,00",
                "Aplicacao Automatica 2.500,00",
                "Fundo de Investimento 200,00",
            ]
        ),
    )


def pdf_com_saldo_anterior_e_final() -> bytes:
    """O resumo declara de quanto a conta partiu e onde ela chegou."""
    return gerar_pdf_textual(
        "\n".join(
            [
                "Resumo Financeiro",
                "Saldo Anterior 10.000,00",
                "Saldo Final 43.350,00",
            ]
        ),
    )


def pdf_com_saldos_por_fundo() -> bytes:
    """A tabela declara abertura e fechamento; cada fundo traz as duas pontas."""
    return gerar_pdf_textual(
        "\n".join(
            [
                "Despesas Detalhadas",
                "Folha de Pagamento 1.000,00",
                "Total de Despesas 1.000,00",
                "Resumo Financeiro Contabil Saldo anterior Creditos Debitos Saldo atual",
                "FUNDO DE RESERVA 10.000,00 5.000,00 3.000,00 12.000,00",
                "FUNDO DE OBRAS 2.000,00 500,00 200,00 2.300,00",
                "TOTAL 12.000,00 5.500,00 3.200,00 14.300,00",
            ]
        ),
    )


def pdf_com_movimentacao_bancaria() -> bytes:
    """O extrato lista movimentos datados: passam pela conta, não são despesa."""
    return gerar_pdf_textual(
        "\n".join(
            [
                "Despesas Detalhadas",
                "Folha de Pagamento 12.000,00",
                "Total de Despesas 12.000,00",
                "Conta bancaria Saldo anterior Creditos Debitos Saldo atual",
                "25/08/2026 Pagamento de fornecedor 1.000,00 1.200,00 300,00 1.900,00",
                "26/08/2026 Recebimento de taxa 1.900,00 800,00 100,00 2.600,00",
                "TOTAL 1.900,00 2.000,00 400,00 4.500,00",
            ]
        ),
    )


def pdf_com_saldo_sem_estrutura() -> bytes:
    """Valores sem rótulo de colunas: saldo e movimento não se distinguem."""
    return gerar_pdf_textual(
        "\n".join(
            [
                "Despesas Detalhadas",
                "Folha de Pagamento 12.000,00",
                "Total de Despesas 12.000,00",
                "Conta bancaria Saldo atual",
                "Movimento do mes 1.000,00 2.000,00 3.000,00 4.000,00",
            ]
        ),
    )


def pdf_com_saldo_entre_despesas() -> bytes:
    """O bloco de saldo no meio da lista; as despesas seguintes continuam lidas."""
    return gerar_pdf_textual(
        "\n".join(
            [
                "Despesas Detalhadas",
                "Folha de Pagamento 1.000,00",
                "Conta bancaria Saldo atual",
                "Conta Corrente 500,00",
                "MANUTENCAO GERAL",
                "Energia Eletrica 300,00",
            ]
        ),
    )


def _despesa(despesas, descricao: str):
    """Acha a despesa pela descrição normalizada."""
    alvo = chave_de_comparacao(descricao)
    return next(item for item in despesas if item.descricao_normalizada == alvo)


# -- Leitura pura ---------------------------------------------------------------


class LeituraDoModeloAtualTests(TestCase):
    """O que a leitura entende de um balanço do modelo atual."""

    @classmethod
    def setUpTestData(cls):
        cls.balanco, cls.inconsistencias = ler_balanco_atual(pdf_do_modelo_atual())

    def test_reconhece_a_competencia_e_o_periodo(self):
        self.assertEqual(self.balanco.competencia, "2026-08-01")
        self.assertEqual(self.balanco.periodo.inicio, "2026-08-01")
        self.assertEqual(self.balanco.periodo.fim, "2026-08-31")

    def test_le_os_totais_declarados(self):
        self.assertEqual(self.balanco.receitas_total, "50000.00")
        self.assertEqual(self.balanco.despesas_total, "16650.00")

    def test_le_os_saldos_de_abertura_e_fechamento(self):
        self.assertEqual(self.balanco.saldo_anterior, "10000.00")
        self.assertEqual(self.balanco.saldo_final, "43350.00")
        self.assertEqual(len(self.balanco.saldos), 2)

    def test_nao_le_receitas_individuais(self):
        """Recebimento por unidade não é objetivo: só o total do resumo é lido."""
        self.assertEqual(self.balanco.receitas, ())
        self.assertEqual(self.balanco.receitas_total, "50000.00")
        self.assertEqual(self.balanco.resumo.receitas_identificadas, 0)

    def test_le_cada_despesa_com_valor(self):
        self.assertEqual(len(self.balanco.despesas), 3)
        folha = _despesa(self.balanco.despesas, "Folha de Pagamento")
        self.assertEqual(folha.valor, "12000.00")
        self.assertEqual(folha.procedencia.secao, "despesas")

    def test_categoriza_pela_descricao_sem_apagar_o_original(self):
        folha = _despesa(self.balanco.despesas, "Folha de Pagamento")
        energia = _despesa(self.balanco.despesas, "Energia Eletrica")
        elevador = _despesa(self.balanco.despesas, "Manutencao do Elevador LTDA")

        self.assertEqual(folha.categoria_eithra, CategoriaEithra.PESSOAL)
        self.assertEqual(energia.categoria_eithra, CategoriaEithra.ENERGIA)
        self.assertEqual(elevador.categoria_eithra, CategoriaEithra.ELEVADORES)
        self.assertIsNone(energia.categoria_original)

    def test_sugere_a_natureza_da_despesa(self):
        folha = _despesa(self.balanco.despesas, "Folha de Pagamento")
        energia = _despesa(self.balanco.despesas, "Energia Eletrica")
        elevador = _despesa(self.balanco.despesas, "Manutencao do Elevador LTDA")

        self.assertEqual(folha.natureza, NaturezaDespesa.RECORRENTE_FIXA)
        self.assertEqual(energia.natureza, NaturezaDespesa.RECORRENTE_VARIAVEL)
        self.assertEqual(elevador.natureza, NaturezaDespesa.NAO_DETERMINADA)

    def test_guarda_o_fornecedor_apenas_como_candidato(self):
        elevador = _despesa(self.balanco.despesas, "Manutencao do Elevador LTDA")
        self.assertEqual(elevador.fornecedor_candidato, "Manutencao do Elevador LTDA")
        folha = _despesa(self.balanco.despesas, "Folha de Pagamento")
        self.assertIsNone(folha.fornecedor_candidato)

    def test_agrega_a_inadimplencia_em_um_so_numero(self):
        inadimplencia = self.balanco.inadimplencia_agregada
        self.assertIsNotNone(inadimplencia)
        self.assertEqual(inadimplencia.valor, "1500.00")
        self.assertEqual(inadimplencia.percentual, "3.5")
        self.assertEqual(inadimplencia.quantidade, 4)

    def test_agrega_por_categoria_do_maior_para_o_menor(self):
        categorias = {item.categoria: item for item in self.balanco.categorias}
        self.assertEqual(set(categorias), {
            CategoriaEithra.PESSOAL,
            CategoriaEithra.ENERGIA,
            CategoriaEithra.ELEVADORES,
        })
        self.assertEqual(categorias[CategoriaEithra.PESSOAL].total, "12000.00")
        self.assertEqual(categorias[CategoriaEithra.PESSOAL].quantidade, 1)
        self.assertEqual(self.balanco.categorias[0].categoria, CategoriaEithra.PESSOAL)

    def test_resume_o_mes_para_a_gestao(self):
        resumo = self.balanco.resumo
        self.assertEqual(resumo.recorrente_total, "15450.00")
        self.assertEqual(resumo.extraordinaria_total, "0.00")
        self.assertEqual(resumo.nao_determinada_total, "1200.00")
        self.assertEqual(resumo.receitas_identificadas, 0)
        self.assertEqual(resumo.despesas_identificadas, 3)
        self.assertEqual(resumo.grupos_conferidos, 0)
        self.assertEqual(resumo.grupos_com_diferenca, 0)
        self.assertEqual(resumo.pontos_de_atencao, 0)
        self.assertEqual(
            resumo.principais_categorias, ("Pessoal", "Energia", "Elevadores")
        )

    def test_registra_de_onde_leu_sem_copiar_o_documento(self):
        prov = self.balanco.provenance
        self.assertEqual(prov["metodo"], "camada textual do PDF")
        self.assertEqual(prov["total_paginas"], 4)
        self.assertEqual(prov["paginas_lidas"], 4)
        self.assertIn("receitas", prov["secoes_encontradas"])
        self.assertIn("despesas", prov["secoes_encontradas"])

    def test_nao_aponta_nada_quando_a_matematica_fecha(self):
        self.assertEqual(self.inconsistencias, ())
        self.assertEqual(self.balanco.confianca, "alta")


class PreservacaoDaEstruturaTests(TestCase):
    """O documento manda: agrupamento, anexos e afins são respeitados."""

    def test_preserva_o_agrupamento_do_documento(self):
        balanco, _ = ler_balanco_atual(pdf_com_agrupamento())
        despesa = balanco.despesas[0]
        self.assertEqual(despesa.categoria_original, "Manutencao")
        self.assertEqual(despesa.categoria_eithra, CategoriaEithra.MANUTENCAO)
        self.assertEqual(despesa.natureza, NaturezaDespesa.EXTRAORDINARIA)

    def test_le_documento_e_parcela_da_propria_linha(self):
        balanco, _ = ler_balanco_atual(pdf_com_documento_e_parcela())
        despesa = balanco.despesas[0]
        self.assertEqual(despesa.documento, "12345")
        self.assertEqual(despesa.parcela, "3/6")
        self.assertEqual(despesa.categoria_eithra, CategoriaEithra.OBRAS_MELHORIAS)
        self.assertEqual(despesa.natureza, NaturezaDespesa.EXTRAORDINARIA)

    def test_anexos_nao_viram_lancamento(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_anexos())
        self.assertEqual(len(balanco.despesas), 1)
        self.assertEqual(balanco.despesas_total, "1000.00")
        self.assertEqual(inconsistencias, ())


class FormaDaLinhaTests(TestCase):
    """Onde o valor estiver na linha, ele é lido — e só ele."""

    @classmethod
    def setUpTestData(cls):
        cls.balanco, cls.inconsistencias = ler_balanco_atual(
            pdf_com_valor_em_qualquer_posicao()
        )

    def test_le_o_valor_antes_depois_e_no_meio_da_data(self):
        valores = [despesa.valor for despesa in self.balanco.despesas]
        self.assertEqual(valores, ["45000.00", "5000.00", "2000.00"])
        for despesa in self.balanco.despesas:
            self.assertEqual(despesa.data, "2026-08-01")

    def test_nao_confunde_a_data_com_valor(self):
        self.assertEqual(self.balanco.receitas_total, None)
        self.assertEqual(self.inconsistencias, ())

    def test_usa_o_lancamento_e_ignora_acumulado_e_percentual(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_acumulado_e_percentual())

        self.assertEqual(len(balanco.despesas), 1)
        despesa = balanco.despesas[0]
        self.assertEqual(despesa.valor, "12000.00")
        self.assertEqual(despesa.descricao_original, "DEB. C/C Folha de Pagamento")
        self.assertEqual(balanco.despesas_total, "12000.00")
        self.assertEqual(inconsistencias, ())

    def test_subtotal_de_categoria_nao_sobrescreve_o_total_do_mes(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_subtotal_de_categoria())

        self.assertEqual(len(balanco.despesas), 2)
        self.assertEqual(balanco.despesas_total, "12200.00")
        self.assertEqual(inconsistencias, ())

    def test_descricao_quebrada_em_duas_linhas_completa_o_lancamento(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_descricao_multilinha())

        self.assertEqual(len(balanco.despesas), 1)
        despesa = balanco.despesas[0]
        self.assertEqual(despesa.valor, "3450.00")
        self.assertEqual(
            despesa.descricao_original,
            "Energia Eletrica NF.: 98765 - concessionaria de energia",
        )
        self.assertEqual(despesa.documento, "98765")
        self.assertEqual(balanco.despesas_total, "3450.00")
        self.assertEqual(inconsistencias, ())

    def test_cabecalho_repetido_nao_vira_dado(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_cabecalho_repetido())

        self.assertEqual(len(balanco.despesas), 2)
        material = _despesa(balanco.despesas, "Material de limpeza")
        self.assertEqual(material.valor, "200.00")
        self.assertEqual(material.categoria_original, None)
        self.assertEqual(balanco.despesas_total, "1200.00")
        self.assertEqual(inconsistencias, ())

    def test_resumo_e_detalhamento_nao_contam_duas_vezes(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_resumo_e_detalhe())

        self.assertEqual(balanco.receitas, ())
        self.assertEqual(len(balanco.despesas), 1)
        self.assertEqual(balanco.receitas_total, "45000.00")
        self.assertEqual(balanco.despesas_total, "1000.00")
        self.assertEqual(inconsistencias, ())


class TabelaDeColunasTests(TestCase):
    """Linha de tabela com várias colunas de valores não é lançamento."""

    @classmethod
    def setUpTestData(cls):
        cls.balanco, cls.inconsistencias = ler_balanco_atual(
            pdf_com_tabela_de_colunas()
        )

    def test_linha_de_colunas_e_subtotal_de_grupo_nao_viram_despesa(self):
        self.assertEqual(len(self.balanco.despesas), 1)
        despesa = self.balanco.despesas[0]
        self.assertEqual(despesa.descricao_original, "Energia Eletrica")
        self.assertEqual(despesa.valor, "3450.00")
        self.assertEqual(self.balanco.despesas_total, "3450.00")
        self.assertEqual(self.inconsistencias, ())


class GruposDeDespesaTests(TestCase):
    """Cada agrupamento é conferido contra o subtotal que o documento declara.

    O subtotal não vira lançamento e a diferença não é escondida: o grupo que
    não fecha termina em atenção, nunca com um lançamento inventado ao lado.
    """

    @classmethod
    def setUpTestData(cls):
        cls.balanco, cls.inconsistencias = ler_balanco_atual(
            pdf_com_grupos_reconciliados()
        )

    def _grupo(self, nome: str):
        alvo = chave_de_comparacao(nome)
        return next(
            grupo
            for grupo in self.balanco.grupos
            if chave_de_comparacao(grupo.nome) == alvo
        )

    def test_confere_o_grupo_que_fecha(self):
        manutencao = self._grupo("Manutencao")
        self.assertEqual(manutencao.total_declarado, "3000.00")
        self.assertEqual(manutencao.soma_extraida, "3000.00")
        self.assertEqual(manutencao.diferenca, "0.00")
        self.assertEqual(manutencao.status, StatusConferencia.CONFERE)
        self.assertEqual(manutencao.quantidade, 2)

    def test_mostra_a_diferenca_do_grupo_que_nao_fecha(self):
        administracao = self._grupo("Administracao")
        self.assertEqual(administracao.total_declarado, "2000.00")
        self.assertEqual(administracao.soma_extraida, "1200.00")
        self.assertEqual(administracao.diferenca, "800.00")
        self.assertEqual(administracao.status, StatusConferencia.FALTA_IDENTIFICAR)

    def test_subtotal_do_grupo_nao_vira_lancamento(self):
        descricoes = [item.descricao_normalizada for item in self.balanco.despesas]
        self.assertEqual(len(self.balanco.despesas), 3)
        self.assertFalse(any("total" in item for item in descricoes))

    def test_soma_extraida_fica_ao_lado_do_total_declarado(self):
        self.assertEqual(self.balanco.despesas_total, "5000.00")
        self.assertEqual(self.balanco.soma_despesas_extraida, "4200.00")

    def test_resumo_conta_os_grupos_conferidos_e_divergentes(self):
        self.assertEqual(self.balanco.resumo.grupos_conferidos, 1)
        self.assertEqual(self.balanco.resumo.grupos_com_diferenca, 1)

    def test_grupo_declarado_sem_lancamento_mostra_o_que_falta(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_grupo_sem_lancamento())

        self.assertEqual(balanco.despesas, ())
        grupo = balanco.grupos[0]
        self.assertEqual(grupo.nome, "Elevadores")
        self.assertEqual(grupo.soma_extraida, "0.00")
        self.assertEqual(grupo.total_declarado, "5800.00")
        self.assertEqual(grupo.diferenca, "5800.00")
        self.assertEqual(grupo.status, StatusConferencia.FALTA_IDENTIFICAR)
        self.assertEqual(inconsistencias, ())

    def test_aponta_quando_os_grupos_nao_fecham_com_o_total(self):
        balanco, inconsistencias = ler_balanco_atual(
            pdf_com_grupos_que_nao_fecham_no_total()
        )

        self.assertEqual(balanco.despesas_total, "9000.00")
        referencias = [item.referencia for item in inconsistencias]
        self.assertIn("grupos", referencias)

    def test_total_do_mes_nao_vira_subtotal_do_ultimo_grupo(self):
        balanco, _ = ler_balanco_atual(pdf_com_total_do_mes_rotulado_como_conta())

        self.assertEqual([grupo.nome for grupo in balanco.grupos], ["Elevadores"])
        elevadores = balanco.grupos[0]
        self.assertEqual(elevadores.total_declarado, "5800.00")
        self.assertEqual(elevadores.soma_extraida, "4600.00")
        self.assertEqual(elevadores.diferenca, "1200.00")
        self.assertEqual(elevadores.status, StatusConferencia.FALTA_IDENTIFICAR)
        self.assertEqual(balanco.despesas_total, "9000.00")
        self.assertEqual(balanco.soma_despesas_extraida, "4600.00")


class PrecedenciaEntreResumoEDetalheTests(TestCase):
    """O resumo confere; o demonstrativo lança.

    Quando o documento traz os dois blocos, só o detalhamento gera lançamento.
    Somar o resumo junto contaria a mesma despesa duas vezes — e é isso que a
    precedência entre os papéis dos blocos impede.
    """

    def test_resumo_sem_demonstrativo_vale_como_leitura(self):
        balanco, inconsistencias = ler_balanco_atual(
            pdf_com_apenas_resumo_de_despesas()
        )

        self.assertEqual(len(balanco.despesas), 1)
        despesa = balanco.despesas[0]
        self.assertEqual(despesa.valor, "12000.00")
        self.assertEqual(despesa.procedencia.metodo, METODO_RESUMO)
        self.assertEqual(balanco.despesas_total, "12000.00")
        self.assertEqual(inconsistencias, ())

    def test_demonstrativo_sem_resumo_le_normalmente(self):
        balanco, inconsistencias = ler_balanco_atual(
            pdf_com_apenas_demonstrativo_de_despesas()
        )

        self.assertEqual(len(balanco.despesas), 1)
        despesa = balanco.despesas[0]
        self.assertEqual(despesa.valor, "12000.00")
        self.assertNotEqual(despesa.procedencia.metodo, METODO_RESUMO)
        self.assertEqual(balanco.soma_despesas_extraida, "12000.00")
        self.assertEqual(inconsistencias, ())

    def test_com_resumo_e_demonstrativo_os_lancamentos_vem_do_detalhe(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_resumo_e_demonstrativo())

        descricoes = sorted(item.descricao_normalizada for item in balanco.despesas)
        self.assertEqual(descricoes, ["energia eletrica", "folha de pagamento"])
        self.assertEqual(balanco.soma_despesas_extraida, "16650.00")
        self.assertEqual(balanco.despesas_total, "16650.00")
        self.assertTrue(
            all(item.procedencia.metodo != METODO_RESUMO for item in balanco.despesas)
        )
        self.assertEqual(inconsistencias, ())

    def test_valores_iguais_no_resumo_e_no_detalhe_nao_duplicam(self):
        balanco, inconsistencias = ler_balanco_atual(
            pdf_com_resumo_e_demonstrativo_iguais()
        )

        self.assertEqual(len(balanco.despesas), 1)
        self.assertEqual(balanco.soma_despesas_extraida, "200.00")
        self.assertEqual(balanco.despesas_total, "200.00")
        self.assertEqual(inconsistencias, ())

    def test_demonstrativo_repetido_nao_conta_duas_vezes(self):
        balanco, inconsistencias = ler_balanco_atual(
            pdf_com_demonstrativo_repetido()
        )

        self.assertEqual(len(balanco.despesas), 2)
        self.assertEqual(balanco.soma_despesas_extraida, "16650.00")
        self.assertEqual(balanco.despesas_total, "16650.00")
        self.assertEqual(inconsistencias, ())

    def test_cabecalho_repetido_por_pagina_nao_corta_o_demonstrativo(self):
        balanco, inconsistencias = ler_balanco_atual(
            pdf_com_demonstrativo_em_varias_paginas()
        )

        # O título repetido no topo da segunda página é o mesmo bloco: as
        # despesas das duas páginas contam, e a leitura não para na primeira.
        self.assertEqual(len(balanco.despesas), 3)
        self.assertEqual(balanco.soma_despesas_extraida, "17850.00")
        self.assertEqual(balanco.despesas_total, "17850.00")
        self.assertEqual(inconsistencias, ())

    def test_valor_a_mais_no_resumo_nao_vira_lancamento(self):
        balanco, inconsistencias = ler_balanco_atual(
            pdf_com_resumo_e_demonstrativo_divergentes()
        )

        self.assertEqual(len(balanco.despesas), 2)
        self.assertEqual(balanco.soma_despesas_extraida, "16650.00")
        self.assertEqual(balanco.despesas_total, "20000.00")
        tipos = [item.tipo for item in inconsistencias]
        self.assertIn(TipoInconsistencia.TOTAL_DIVERGENTE, tipos)

    def test_valor_em_anexo_nao_vira_lancamento(self):
        balanco, inconsistencias = ler_balanco_atual(
            pdf_com_anexo_depois_do_demonstrativo()
        )

        self.assertEqual(len(balanco.despesas), 1)
        self.assertEqual(balanco.soma_despesas_extraida, "12000.00")
        self.assertEqual(balanco.despesas_total, "12000.00")
        self.assertEqual(inconsistencias, ())

    def test_grupo_com_subtotal_e_detalhes_nao_soma_o_resumo_acima(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_resumo_e_grupo_detalhado())

        self.assertEqual(len(balanco.despesas), 2)
        self.assertEqual(balanco.soma_despesas_extraida, "5800.00")

        grupo = balanco.grupos[0]
        self.assertEqual(grupo.nome, "Manutencao")
        self.assertEqual(grupo.total_declarado, "5800.00")
        self.assertEqual(grupo.soma_extraida, "5800.00")
        self.assertEqual(grupo.quantidade, 2)
        self.assertEqual(grupo.status, StatusConferencia.CONFERE)
        self.assertEqual(inconsistencias, ())


class SaldoContaEMovimentacaoTests(TestCase):
    """Saldo de conta e movimento bancário não são despesa do mês.

    Um bloco que anuncia saldo fala do que a conta tinha, do que passou por ela
    e do que sobrou. Contar esses valores como lançamento inflaria o mês, e é
    isso que a leitura impede — sem inventar saldo onde o documento não
    declarou.
    """

    def test_bloco_de_conta_bancaria_nao_vira_despesa(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_bloco_de_conta_bancaria())

        self.assertEqual(len(balanco.despesas), 1)
        self.assertEqual(balanco.despesas[0].descricao_normalizada, "folha de pagamento")
        self.assertEqual(balanco.soma_despesas_extraida, "12000.00")
        self.assertEqual(balanco.despesas_total, "12000.00")
        self.assertEqual(inconsistencias, ())

    def test_varias_contas_viram_varios_saldos(self):
        balanco, _ = ler_balanco_atual(pdf_com_bloco_de_conta_bancaria())

        self.assertEqual(len(balanco.saldos), 3)
        self.assertEqual(
            sorted(saldo.identificacao for saldo in balanco.saldos),
            ["Aplicacao Automatica", "Conta Corrente", "Fundo de Investimento"],
        )
        self.assertTrue(all(saldo.procedencia.pagina for saldo in balanco.saldos))

    def test_saldo_anterior_declarado(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_saldo_anterior_e_final())

        self.assertEqual(balanco.saldo_anterior, "10000.00")
        self.assertEqual(inconsistencias, ())

    def test_saldo_final_declarado(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_saldo_anterior_e_final())

        self.assertEqual(balanco.saldo_final, "43350.00")
        self.assertEqual(inconsistencias, ())

    def test_saldos_por_fundo_com_abertura_e_fechamento(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_saldos_por_fundo())

        self.assertEqual(len(balanco.saldos), 2)
        reserva = balanco.saldos[0]
        self.assertEqual(reserva.identificacao, "FUNDO DE RESERVA")
        self.assertEqual(reserva.saldo_anterior, "10000.00")
        self.assertEqual(reserva.saldo_final, "12000.00")
        self.assertEqual(balanco.saldo_anterior, "12000.00")
        self.assertEqual(balanco.saldo_final, "14300.00")
        self.assertEqual(len(balanco.despesas), 1)
        self.assertEqual(inconsistencias, ())

    def test_movimentacao_com_varios_valores_nao_gera_lancamentos(self):
        balanco, _ = ler_balanco_atual(pdf_com_movimentacao_bancaria())

        self.assertEqual(len(balanco.despesas), 1)
        self.assertEqual(balanco.saldos, ())

    def test_movimentacoes_nao_entram_na_soma(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_movimentacao_bancaria())

        self.assertEqual(balanco.soma_despesas_extraida, "12000.00")
        self.assertEqual(inconsistencias, ())

    def test_despesas_apos_o_bloco_de_saldo_continuam_sendo_lidas(self):
        balanco, _ = ler_balanco_atual(pdf_com_saldo_entre_despesas())

        self.assertEqual(
            sorted(item.descricao_normalizada for item in balanco.despesas),
            ["energia eletrica", "folha de pagamento"],
        )
        self.assertEqual(balanco.soma_despesas_extraida, "1300.00")
        self.assertEqual(len(balanco.saldos), 1)
        self.assertEqual(balanco.saldos[0].identificacao, "Conta Corrente")

    def test_saldo_sem_estrutura_declarada_nao_e_inferido(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_saldo_sem_estrutura())

        self.assertEqual(balanco.saldos, ())
        self.assertIsNone(balanco.saldo_anterior)
        self.assertIsNone(balanco.saldo_final)
        self.assertEqual(len(balanco.despesas), 1)
        self.assertEqual(inconsistencias, ())


class AusenciaTests(TestCase):
    """Sem evidência, a leitura devolve vazio — nunca uma suposição."""

    @classmethod
    def setUpTestData(cls):
        cls.balanco, cls.inconsistencias = ler_balanco_atual(pdf_sem_secoes())

    def test_nao_inventa_competencia_nem_lancamento(self):
        self.assertIsNone(self.balanco.competencia)
        self.assertEqual(self.balanco.receitas, ())
        self.assertEqual(self.balanco.despesas, ())
        self.assertEqual(self.balanco.categorias, ())
        self.assertIsNone(self.balanco.inadimplencia_agregada)

    def test_nao_aponta_problema_onde_nao_ha_leitura(self):
        self.assertEqual(self.inconsistencias, ())
        self.assertEqual(self.balanco.confianca, "media")


class MolduraDoRelatorioTests(TestCase):
    """O rótulo de navegação colado à linha não pode esconder o dado do mês."""

    @classmethod
    def setUpTestData(cls):
        cls.balanco, cls.inconsistencias = ler_balanco_atual(
            pdf_com_moldura_colada_ao_total()
        )

    def test_le_o_total_declarado_mesmo_com_moldura_colada(self):
        self.assertEqual(self.balanco.receitas_total, "45000.00")
        self.assertEqual(self.balanco.despesas_total, "3450.00")

    def test_nao_deixa_a_moldura_no_texto_do_lancamento(self):
        self.assertEqual(len(self.balanco.despesas), 1)
        despesa = self.balanco.despesas[0]
        self.assertEqual(despesa.descricao_original, "Energia Eletrica")
        self.assertEqual(despesa.valor, "3450.00")

    def test_documento_curto_nao_tem_moldura(self):
        """Em poucas páginas, nada é tomado por moldura nem aparado."""
        balanco, _ = ler_balanco_atual(pdf_com_agrupamento())

        self.assertEqual(balanco.despesas[0].categoria_original, "Manutencao")


class PontosDeAtencaoTests(TestCase):
    """A conferência por exceção: só se aponta o que se pode afirmar."""

    def test_competencia_conflitante_nao_e_escolhida_em_silencio(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_competencia_conflitante())

        self.assertIsNone(balanco.competencia)
        tipos = [item.tipo for item in inconsistencias]
        self.assertIn(TipoInconsistencia.COMPETENCIA_CONFLITANTE, tipos)
        conflito = next(
            item
            for item in inconsistencias
            if item.tipo == TipoInconsistencia.COMPETENCIA_CONFLITANTE
        )
        self.assertEqual(conflito.severidade, Severidade.ALTA)
        self.assertEqual(balanco.confianca, "baixa")

    def test_total_divergente_vira_ponto_de_atencao(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_total_divergente())

        divergencias = [
            item
            for item in inconsistencias
            if item.tipo == TipoInconsistencia.TOTAL_DIVERGENTE
        ]
        self.assertEqual(len(divergencias), 1)
        self.assertEqual(divergencias[0].severidade, Severidade.MEDIA)
        self.assertEqual(balanco.confianca, "media")

    def test_lancamento_repetido_pede_uma_olhada(self):
        _, inconsistencias = ler_balanco_atual(pdf_com_despesa_repetida())
        tipos = [item.tipo for item in inconsistencias]
        self.assertIn(TipoInconsistencia.POSSIVEL_DUPLICIDADE, tipos)

    def test_valor_sem_descricao_nao_vira_lancamento(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_linha_sem_descricao())

        self.assertEqual(balanco.receitas, ())
        tipos = [item.tipo for item in inconsistencias]
        self.assertIn(TipoInconsistencia.LINHA_INCOMPLETA, tipos)


# -- Persistência da leitura ----------------------------------------------------


class ExtracaoDaImportacaoTests(TestCase):
    """A leitura sobre a importação gravada: o que fica guardado, e o que não."""

    def setUp(self):
        usuario = criar_pessoa("gestao.extracao@exemplo.com", "Gestao Extracao")
        condominio = Condominio.objects.create(nome="Condominio Extracao")
        conteudo = pdf_do_modelo_atual()
        self.importacao = ImportacaoBalanco.objects.create(
            condominio=condominio,
            criado_por=usuario,
            nome_original="balanco.pdf",
            mime_type="application/pdf",
            tamanho_bytes=len(conteudo),
            sha256=hashlib.sha256(conteudo).hexdigest(),
            storage_provider="supabase",
            storage_bucket="bucket-de-teste",
            storage_key="balancos/importacao/documento.pdf",
            status=StatusImportacao.PRONTO,
            layout_detectado=LayoutBalanco.ATUAL_CANONICO,
        )

    def test_grava_o_resultado_e_fica_pronta(self):
        resultado = extrair_balanco_atual(
            self.importacao, conteudo=pdf_do_modelo_atual()
        )

        self.importacao.refresh_from_db()
        self.assertTrue(resultado.processado)
        self.assertEqual(self.importacao.status, StatusImportacao.PRONTO)
        self.assertEqual(self.importacao.schema_versao, SCHEMA_VERSAO)
        self.assertEqual(self.importacao.inconsistencias, [])
        self.assertEqual(
            self.importacao.resultado_extraido["competencia"], "2026-08-01"
        )
        self.assertEqual(
            self.importacao.resultado_extraido["receitas_total"], "50000.00"
        )

    def test_resultado_gravado_respeita_o_contrato(self):
        extrair_balanco_atual(self.importacao, conteudo=pdf_do_modelo_atual())
        self.importacao.refresh_from_db()

        validar_dicionario(self.importacao.resultado_extraido)
        json.dumps(self.importacao.resultado_extraido)

    def test_nao_cria_balanco_mensal(self):
        """A leitura não oficializa o mês: isso é decisão da gestão."""
        extrair_balanco_atual(self.importacao, conteudo=pdf_do_modelo_atual())
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_reexecutar_produz_o_mesmo_resultado_sem_criar_objeto(self):
        primeiro = extrair_balanco_atual(
            self.importacao, conteudo=pdf_do_modelo_atual()
        )
        segundo = extrair_balanco_atual(
            self.importacao, conteudo=pdf_do_modelo_atual()
        )

        self.importacao.refresh_from_db()
        self.assertEqual(para_dicionario(primeiro.balanco), para_dicionario(segundo.balanco))
        self.assertEqual(ImportacaoBalanco.objects.count(), 1)
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_le_o_conteudo_do_storage_quando_nao_recebe_em_maos(self):
        with patch(
            "apps.balancos.extrator_atual._baixar_pdf_privado",
            return_value=(pdf_do_modelo_atual(), 4),
        ) as baixar:
            resultado = extrair_balanco_atual(self.importacao)

        self.assertTrue(resultado.processado)
        baixar.assert_called_once()
        self.importacao.refresh_from_db()
        self.assertEqual(self.importacao.status, StatusImportacao.PRONTO)

    def test_falha_de_storage_vira_erro_sem_vazar_detalhe(self):
        with patch(
            "apps.balancos.extrator_atual._baixar_pdf_privado",
            side_effect=ErroDeDeteccaoDeLayout(
                "Nao foi possivel falar com o armazenamento."
            ),
        ):
            with self.assertRaises(ErroDeExtracao):
                extrair_balanco_atual(self.importacao)

        self.importacao.refresh_from_db()
        self.assertEqual(self.importacao.status, StatusImportacao.ERRO)
        self.assertNotIn("http", self.importacao.erro_processamento.lower())
        self.assertEqual(self.importacao.resultado_extraido, {})
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_pdf_ilegivel_vira_erro_humano(self):
        with self.assertRaises(ErroDeExtracao):
            extrair_balanco_atual(self.importacao, conteudo=b"isto nao e um pdf")

        self.importacao.refresh_from_db()
        self.assertEqual(self.importacao.status, StatusImportacao.ERRO)
        self.assertTrue(self.importacao.erro_processamento)
        self.assertEqual(BalancoMensal.objects.count(), 0)


# -- Fluxo de recebimento -------------------------------------------------------


def detectar_como_atual(importacao, *, provider=None):
    """Fecha a importação como modelo atual, sem exercitar o detector real."""
    importacao.layout_detectado = LayoutBalanco.ATUAL_CANONICO
    importacao.status = StatusImportacao.PRONTO
    importacao.erro_processamento = ""
    importacao.save(
        update_fields=["layout_detectado", "status", "erro_processamento", "atualizado_em"]
    )
    return None


def detectar_como_desconhecido(importacao, *, provider=None):
    """Fecha a importação como não reconhecida, sem exercitar o detector real."""
    importacao.layout_detectado = LayoutBalanco.DESCONHECIDO
    importacao.status = StatusImportacao.PRONTO
    importacao.erro_processamento = ""
    importacao.save(
        update_fields=["layout_detectado", "status", "erro_processamento", "atualizado_em"]
    )
    return None


class FluxoDeRecebimentoTests(TestCase):
    """O recebimento do modelo atual já sai com os dados financeiros lidos."""

    def setUp(self):
        self.usuario = criar_pessoa("gestao.fluxo@exemplo.com", "Gestao Fluxo")
        self.condominio = Condominio.objects.create(nome="Condominio Fluxo")
        self.provider = StorageDeMentira()
        obter_provider = patch(
            "apps.balancos.services.obter_provider", return_value=self.provider
        )
        obter_provider.start()
        self.addCleanup(obter_provider.stop)

    def receber(self):
        return receber_importacao(
            condominio=self.condominio,
            usuario=self.usuario,
            arquivo=pdf(conteudo=pdf_do_modelo_atual()),
        )

    def test_modelo_atual_sai_com_os_dados_lidos(self):
        with patch(
            "apps.balancos.services.detectar_layout_balanco",
            side_effect=detectar_como_atual,
        ):
            importacao = self.receber().importacao

        importacao.refresh_from_db()
        self.assertEqual(importacao.status, StatusImportacao.PRONTO)
        self.assertEqual(importacao.schema_versao, SCHEMA_VERSAO)
        self.assertEqual(
            importacao.resultado_extraido["competencia"], "2026-08-01"
        )
        self.assertEqual(
            importacao.resultado_extraido["receitas_total"], "50000.00"
        )
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_leitura_usa_o_arquivo_ja_em_maos(self):
        """O upload já trouxe os bytes: a leitura não acessa o storage de novo."""
        with patch(
            "apps.balancos.services.detectar_layout_balanco",
            side_effect=detectar_como_atual,
        ), patch("apps.balancos.extrator_atual._baixar_pdf_privado") as baixar:
            self.receber()

        baixar.assert_not_called()

    def test_formato_nao_reconhecido_nao_e_lido(self):
        with patch(
            "apps.balancos.services.detectar_layout_balanco",
            side_effect=detectar_como_desconhecido,
        ):
            importacao = self.receber().importacao

        importacao.refresh_from_db()
        self.assertEqual(importacao.layout_detectado, LayoutBalanco.DESCONHECIDO)
        self.assertEqual(importacao.resultado_extraido, {})
        self.assertEqual(BalancoMensal.objects.count(), 0)


class RecusaDeLayoutTests(TestCase):
    """Documento que não é do modelo atual não é lido — devolve o motivo."""

    def setUp(self):
        usuario = criar_pessoa("gestao.recusa@exemplo.com", "Gestao Recusa")
        condominio = Condominio.objects.create(nome="Condominio Recusa")
        conteudo = pdf_sem_secoes()
        self.importacao = ImportacaoBalanco.objects.create(
            condominio=condominio,
            criado_por=usuario,
            nome_original="outro.pdf",
            mime_type="application/pdf",
            tamanho_bytes=len(conteudo),
            sha256=hashlib.sha256(conteudo).hexdigest(),
            storage_provider="supabase",
            storage_bucket="bucket-de-teste",
            storage_key="balancos/importacao/documento.pdf",
            status=StatusImportacao.PRONTO,
            layout_detectado=LayoutBalanco.LEGADO,
        )

    def test_legado_aponta_o_caminho_certo(self):
        resultado = extrair_balanco_atual(self.importacao, conteudo=pdf_do_modelo_atual())

        self.assertFalse(resultado.processado)
        self.assertEqual(
            resultado.motivo,
            "Este é um balancete antigo: quem o lê é a leitura de documentos legados.",
        )
        self.importacao.refresh_from_db()
        self.assertEqual(self.importacao.resultado_extraido, {})

    def test_desconhecido_nao_promete_o_que_nao_sabe(self):
        self.importacao.layout_detectado = LayoutBalanco.DESCONHECIDO
        self.importacao.save(update_fields=["layout_detectado"])

        resultado = extrair_balanco_atual(self.importacao, conteudo=pdf_do_modelo_atual())

        self.assertFalse(resultado.processado)
        self.assertEqual(
            resultado.motivo,
            "Ainda não foi possível reconhecer o formato deste documento.",
        )


# -- Contrato -------------------------------------------------------------------


class ContratoDaLeituraTests(TestCase):
    """O contrato é o que viaja para o banco: precisa ser fiel e recuperável."""

    @classmethod
    def setUpTestData(cls):
        cls.balanco, _ = ler_balanco_atual(pdf_do_modelo_atual())
        cls.dados = para_dicionario(cls.balanco)

    def test_vira_json_puro(self):
        json.dumps(self.dados)
        self.assertIsInstance(self.dados["saldos"], list)
        self.assertIsInstance(self.dados["receitas"], list)
        self.assertIsInstance(self.dados["despesas"], list)
        self.assertIsInstance(self.dados["categorias"], list)
        self.assertIsInstance(self.dados["grupos"], list)

    def test_sobrevive_a_ida_e_volta(self):
        reconstruido = de_dicionario(self.dados)
        self.assertEqual(reconstruido.competencia, self.balanco.competencia)
        self.assertEqual(reconstruido.receitas_total, self.balanco.receitas_total)
        self.assertEqual(
            reconstruido.soma_despesas_extraida, self.balanco.soma_despesas_extraida
        )
        self.assertEqual(len(reconstruido.despesas), len(self.balanco.despesas))
        self.assertEqual(len(reconstruido.categorias), len(self.balanco.categorias))
        self.assertEqual(len(reconstruido.grupos), len(self.balanco.grupos))
        self.assertEqual(reconstruido.resumo, self.balanco.resumo)

    def test_recusa_competencia_que_nao_e_o_primeiro_dia(self):
        invalido = dict(self.dados, competencia="2026-08-15")
        with self.assertRaises(ErroDeContrato):
            validar_dicionario(invalido)

    def test_recusa_versao_de_contrato_desconhecida(self):
        invalido = dict(self.dados, schema_versao=SCHEMA_VERSAO + 1)
        with self.assertRaises(ErroDeContrato):
            validar_dicionario(invalido)
