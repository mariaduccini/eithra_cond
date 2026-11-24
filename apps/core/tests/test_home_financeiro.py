"""Resumo financeiro e transparência segura na Home."""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.balancos.constants import StatusBalanco
from apps.balancos.models import BalancoMensal
from apps.condominios.constants import Papel
from apps.governanca.constants import PeriodicidadeCobranca
from apps.manutencoes.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)
from apps.governanca.tests.apoio import criar_contrato


def _balanco(condominio, usuario, competencia, status, *, receitas, despesas, saldo):
    """Cria somente agregados de teste; nenhum documento ou lançamento real."""
    return BalancoMensal.objects.create(
        condominio=condominio,
        criado_por=usuario,
        validado_por=usuario,
        validado_em="2026-01-02T10:00:00Z",
        competencia=competencia,
        status=status,
        dados_validados={
            "schema_versao": 1,
            "origem_schema_versao": 1,
            "receitas_total": receitas,
            "despesas_total": despesas,
            "saldo_final": saldo,
            "leitura": {
                "schema_versao": 1,
                "categorias": [
                    {
                        "categoria": "pessoal",
                        "total": despesas,
                    }
                ],
            },
        },
    )


class BaseHomeFinanceiraTests(TestCase):
    def setUp(self):
        self.condominio = criar_condominio("Condomínio Alpha")
        self.gestao = criar_pessoa("gestao.financeira@example.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)

    def _login(self, usuario):
        self.client.force_login(usuario)


class ResumoFinanceiroDaHomeTests(BaseHomeFinanceiraTests):
    def test_gestao_ve_o_balanco_confirmado_no_seu_condominio(self):
        _balanco(
            self.condominio,
            self.gestao,
            date(2026, 3, 1),
            StatusBalanco.VALIDADO,
            receitas="12500.00",
            despesas="9800.00",
            saldo="102700.00",
        )
        outro = criar_condominio("Condomínio Beta")
        _balanco(
            outro,
            self.gestao,
            date(2026, 3, 1),
            StatusBalanco.PUBLICADO,
            receitas="99999.00",
            despesas="88888.00",
            saldo="11111.00",
        )
        self._login(self.gestao)

        resposta = self.client.get(reverse("core:home"))

        self.assertContains(resposta, "R$ 12.500,00")
        self.assertContains(resposta, "R$ 9.800,00")
        self.assertContains(resposta, "Saldo do período")
        self.assertContains(resposta, "R$ 2.700,00")
        self.assertNotContains(resposta, "R$ 102.700,00")
        self.assertNotContains(resposta, "R$ 99.999,00")
        self.assertContains(resposta, reverse("core:transparencia", args=[
            BalancoMensal.objects.get(condominio=self.condominio).pk
        ]))

    def test_sem_balanco_nao_converte_ausencia_em_zero(self):
        self._login(self.gestao)

        resposta = self.client.get(reverse("core:home"))

        self.assertContains(resposta, "Ainda não há um balanço confirmado")
        self.assertNotContains(resposta, "R$ 0,00")

    def test_home_mostra_compromisso_mensal_sem_chamar_de_pagamento_pendente(self):
        criar_contrato(
            self.condominio,
            self.gestao,
            objeto="Serviço mensal",
            data_inicio=date(2020, 1, 1),
            data_fim=date(2030, 1, 1),
            valor=Decimal("1800.00"),
            periodicidade_cobranca=PeriodicidadeCobranca.MENSAL,
        )
        segundo = criar_contrato(
            self.condominio,
            self.gestao,
            objeto="Limpeza mensal",
            data_inicio=date(2020, 1, 1),
            data_fim=date(2030, 1, 1),
            valor=Decimal("900.00"),
            periodicidade_cobranca=PeriodicidadeCobranca.MENSAL,
        )
        criar_contrato(
            self.condominio,
            self.gestao,
            objeto="Seguro anual",
            fornecedor=segundo.fornecedor,
            data_inicio=date(2020, 1, 1),
            data_fim=date(2030, 1, 1),
            valor=Decimal("12000.00"),
            periodicidade_cobranca=PeriodicidadeCobranca.ANUAL,
        )
        criar_contrato(
            self.condominio,
            self.gestao,
            objeto="Contrato vencido",
            fornecedor=segundo.fornecedor,
            data_inicio=date(2018, 1, 1),
            data_fim=date(2020, 1, 1),
            valor=Decimal("5000.00"),
            periodicidade_cobranca=PeriodicidadeCobranca.MENSAL,
        )
        self._login(self.gestao)

        resposta = self.client.get(reverse("core:home"))

        self.assertContains(resposta, "R$ 2.700,00 por mês")
        self.assertContains(resposta, "2 contratos com cobrança mensal")
        self.assertContains(resposta, "não indica parcelas que ainda faltam pagar")
        self.assertNotContains(resposta, "R$ 12.000,00 por mês")
        self.assertNotContains(resposta, "R$ 5.000,00 por mês")

    def test_operacao_sem_permissao_financeira_nao_ve_totais(self):
        operacional = criar_pessoa("operacao.financeira@example.test", "Operação")
        criar_vinculo(operacional, self.condominio, Papel.OPERACIONAL)
        _balanco(
            self.condominio,
            self.gestao,
            date(2026, 3, 1),
            StatusBalanco.VALIDADO,
            receitas="12500.00",
            despesas="9800.00",
            saldo="2700.00",
        )
        self._login(operacional)

        resposta = self.client.get(reverse("core:home"))

        self.assertNotContains(resposta, "Finanças do condomínio")
        self.assertNotContains(resposta, "R$ 12.500,00")


class TransparenciaResidencialTests(BaseHomeFinanceiraTests):
    def setUp(self):
        super().setUp()
        self.morador = criar_pessoa("morador.financeiro@example.test", "Morador")
        criar_vinculo(self.morador, self.condominio, Papel.MORADOR)

    def test_morador_ve_apenas_balanco_publicado_e_dados_agregados(self):
        publicado = _balanco(
            self.condominio,
            self.gestao,
            date(2026, 3, 1),
            StatusBalanco.PUBLICADO,
            receitas="12500.00",
            despesas="9800.00",
            saldo="102700.00",
        )
        publicado.dados_validados["leitura"]["despesas"] = [
            {
                "id": "linha-elevador",
                "descricao_original": "Revisão dos elevadores",
                "descricao_normalizada": "revisao dos elevadores",
                "valor": "800.00",
                "data": "2026-03-07",
                "fornecedor_candidato": "Atlas Elevadores",
                "categoria_eithra": "elevadores",
                "origem": "extraida",
                "procedencia": {"pagina": 3},
            },
            {
                "id": "linha-agua",
                "descricao_original": "Conta de água",
                "descricao_normalizada": "conta de agua",
                "valor": "200.00",
                "data": "2026-03-08",
                "fornecedor_candidato": "Águas do Vale",
                "categoria_eithra": "agua",
                "origem": "extraida",
                "procedencia": {"pagina": 4},
            },
        ]
        publicado.dados_validados["leitura"]["categorias"] = [
            {"categoria": "elevadores", "total": "800.00"},
            {"categoria": "agua", "total": "200.00"},
        ]
        publicado.save(update_fields=["dados_validados"])
        _balanco(
            self.condominio,
            self.gestao,
            date(2026, 4, 1),
            StatusBalanco.VALIDADO,
            receitas="88888.00",
            despesas="77777.00",
            saldo="11111.00",
        )
        self._login(self.morador)

        home = self.client.get(reverse("core:home"))
        detalhe = self.client.get(
            reverse("core:transparencia", args=[publicado.pk])
        )

        self.assertContains(home, "R$ 12.500,00")
        self.assertNotContains(home, "R$ 88.888,00")
        self.assertContains(detalhe, "R$ 9.800,00")
        self.assertContains(detalhe, "R$ 2.700,00")
        self.assertNotContains(detalhe, "R$ 102.700,00")
        self.assertContains(detalhe, "Elevadores")
        self.assertContains(detalhe, "R$ 800,00")
        self.assertNotContains(detalhe, "Revisão dos elevadores")
        self.assertNotContains(detalhe, "Atlas Elevadores")
        self.assertNotContains(detalhe, "77777.00")
        self.assertNotContains(detalhe, "Arquivo original")
        self.assertEqual(detalhe.context["lancamentos"], [])

    def test_filtros_de_categoria_e_fornecedor_refinam_as_linhas(self):
        publicado = _balanco(
            self.condominio,
            self.gestao,
            date(2026, 3, 1),
            StatusBalanco.PUBLICADO,
            receitas="12500.00",
            despesas="9800.00",
            saldo="2700.00",
        )
        publicado.dados_validados["leitura"]["despesas"] = [
            {
                "id": "linha-elevador",
                "descricao_original": "Revisão dos elevadores",
                "valor": "800.00",
                "categoria_eithra": "elevadores",
                "fornecedor_candidato": "Atlas Elevadores",
                "origem": "extraida",
            },
            {
                "id": "linha-agua",
                "descricao_original": "Conta de água",
                "valor": "200.00",
                "categoria_eithra": "agua",
                "fornecedor_candidato": "Águas do Vale",
                "origem": "extraida",
            },
        ]
        publicado.save(update_fields=["dados_validados"])
        self._login(self.gestao)

        resposta = self.client.get(
            reverse("core:transparencia", args=[publicado.pk]),
            {"categoria": "elevadores", "fornecedor": "atlas"},
        )

        self.assertContains(resposta, "Revisão dos elevadores")
        self.assertNotContains(resposta, "Conta de água")
        self.assertContains(resposta, "Atlas Elevadores")

    def test_evolucao_nao_cria_meses_ausentes(self):
        fevereiro = _balanco(
            self.condominio,
            self.gestao,
            date(2026, 2, 1),
            StatusBalanco.PUBLICADO,
            receitas="10000.00",
            despesas="9000.00",
            saldo="1000.00",
        )
        marco = _balanco(
            self.condominio,
            self.gestao,
            date(2026, 3, 1),
            StatusBalanco.PUBLICADO,
            receitas="11000.00",
            despesas="9500.00",
            saldo="1500.00",
        )
        self._login(self.morador)

        resposta = self.client.get(
            reverse("core:transparencia", args=[marco.pk])
        )

        self.assertContains(resposta, "Fevereiro de 2026")
        self.assertContains(resposta, "Março de 2026")
        self.assertNotContains(resposta, "Janeiro de 2026")

    def test_morador_nao_abre_balanco_apenas_validado(self):
        validado = _balanco(
            self.condominio,
            self.gestao,
            date(2026, 3, 1),
            StatusBalanco.VALIDADO,
            receitas="12500.00",
            despesas="9800.00",
            saldo="2700.00",
        )
        self._login(self.morador)

        resposta = self.client.get(
            reverse("core:transparencia", args=[validado.pk])
        )

        self.assertEqual(resposta.status_code, 404)
        self.assertNotContains(self.client.get(reverse("core:home")), "R$ 12.500,00")

    def test_sem_balanco_publicado_mostra_estado_amigavel_sem_valor(self):
        _balanco(
            self.condominio,
            self.gestao,
            date(2026, 3, 1),
            StatusBalanco.VALIDADO,
            receitas="12500.00",
            despesas="9800.00",
            saldo="2700.00",
        )
        self._login(self.morador)

        resposta = self.client.get(reverse("core:home"))

        self.assertContains(resposta, "ainda está sendo preparada")
        self.assertNotContains(resposta, "R$ 0,00")
        self.assertNotContains(resposta, "R$ 12.500,00")

    def test_residente_nao_consulta_balanco_de_outro_condominio(self):
        outro = criar_condominio("Condomínio Beta")
        publicado = _balanco(
            outro,
            self.gestao,
            date(2026, 3, 1),
            StatusBalanco.PUBLICADO,
            receitas="12500.00",
            despesas="9800.00",
            saldo="2700.00",
        )
        self._login(self.morador)

        resposta = self.client.get(
            reverse("core:transparencia", args=[publicado.pk])
        )

        self.assertEqual(resposta.status_code, 404)

    def test_comparacao_usa_apenas_competencia_publicada_anterior(self):
        _balanco(
            self.condominio,
            self.gestao,
            date(2026, 2, 1),
            StatusBalanco.PUBLICADO,
            receitas="12000.00",
            despesas="10000.00",
            saldo="2000.00",
        )
        atual = _balanco(
            self.condominio,
            self.gestao,
            date(2026, 3, 1),
            StatusBalanco.PUBLICADO,
            receitas="12500.00",
            despesas="9000.00",
            saldo="3500.00",
        )
        _balanco(
            self.condominio,
            self.gestao,
            date(2026, 1, 1),
            StatusBalanco.PUBLICADO,
            receitas="50000.00",
            despesas="49000.00",
            saldo="1000.00",
        )
        self._login(self.morador)

        resposta = self.client.get(reverse("core:transparencia", args=[atual.pk]))

        self.assertContains(resposta, "Fevereiro de 2026")
        self.assertContains(resposta, "R$ 10.000,00")
        self.assertContains(resposta, "diminuíram 10,0%")
        self.assertContains(resposta, "Janeiro de 2026")
        self.assertEqual(
            resposta.context["competencia_anterior"], "Fevereiro de 2026"
        )
        self.assertEqual(
            resposta.context["variacao"]["diferenca"], "R$ 1.000,00"
        )

    def test_home_permite_escolher_outro_condominio_autorizado(self):
        outro = criar_condominio("Condomínio Beta")
        criar_vinculo(self.morador, outro, Papel.MORADOR)
        _balanco(
            self.condominio,
            self.gestao,
            date(2026, 3, 1),
            StatusBalanco.PUBLICADO,
            receitas="12500.00",
            despesas="9800.00",
            saldo="2700.00",
        )
        _balanco(
            outro,
            self.gestao,
            date(2026, 3, 1),
            StatusBalanco.PUBLICADO,
            receitas="22500.00",
            despesas="19800.00",
            saldo="2700.00",
        )
        self._login(self.morador)

        resposta = self.client.get(
            reverse("core:home"), {"condominio": str(outro.pk)}
        )

        self.assertContains(resposta, "R$ 22.500,00")
        self.assertNotContains(resposta, "R$ 12.500,00")
        self.assertContains(resposta, "Condomínio Beta")


class TransparenciaDetalhadaTests(BaseHomeFinanceiraTests):
    """Seções de apoio do dashboard: compromisso, fornecedor, natureza e receita.

    Nenhuma delas é uma segunda contabilização — todas leem o mesmo balanço por
    outro ângulo. Os testes fixam isso, além da separação que o demonstrativo já
    traz e do que o morador não pode ver.
    """

    def _balanco_com_detalhe(self, *, status=StatusBalanco.VALIDADO):
        balanco = _balanco(
            self.condominio,
            self.gestao,
            date(2026, 3, 1),
            status,
            receitas="12500.00",
            despesas="9800.00",
            saldo="2700.00",
        )
        leitura = balanco.dados_validados["leitura"]
        leitura["categorias"] = [
            {
                "categoria": "elevadores",
                "recorrente": "800.00",
                "extraordinaria": "0.00",
                "nao_determinada": "0.00",
                "total": "800.00",
            },
            {
                "categoria": "obras_melhorias",
                "recorrente": "0.00",
                "extraordinaria": "5000.00",
                "nao_determinada": "0.00",
                "total": "5000.00",
            },
        ]
        leitura["receitas"] = [
            {
                "descricao_original": "Cota condominial de março",
                "valor": "10000.00",
                "categoria_original": "Cotas condominiais",
            },
            {
                "descricao_original": "Multa por atraso",
                "valor": "2500.00",
                "categoria_original": "Multas e juros",
            },
        ]
        leitura["despesas"] = [
            {
                "id": "linha-elevador",
                "descricao_original": "Revisão dos elevadores",
                "valor": "800.00",
                "categoria_eithra": "elevadores",
                "fornecedor_candidato": "Atlas Elevadores",
                "origem": "extraida",
            },
            {
                "id": "linha-obra",
                "descricao_original": "Obra da piscina",
                "valor": "5000.00",
                "categoria_eithra": "obras_melhorias",
                "fornecedor_candidato": "Construtora Vale",
                "origem": "extraida",
            },
        ]
        balanco.save(update_fields=["dados_validados"])
        return balanco

    def test_dashboard_mostra_compromisso_fornecedores_e_media_por_unidade(self):
        self.condominio.quantidade_unidades = 49
        self.condominio.save(update_fields=["quantidade_unidades"])
        balanco = self._balanco_com_detalhe()
        criar_contrato(
            self.condominio,
            self.gestao,
            objeto="Portaria 24 horas",
            data_inicio=date(2020, 1, 1),
            data_fim=date(2030, 1, 1),
            valor=Decimal("3000.00"),
            periodicidade_cobranca=PeriodicidadeCobranca.MENSAL,
        )
        self._login(self.gestao)

        resposta = self.client.get(
            reverse("core:transparencia", args=[balanco.pk])
        )

        # Já comprometido vem dos contratos mensais em vigor, sem virar pagamento.
        self.assertContains(resposta, "Já comprometido")
        self.assertContains(resposta, "R$ 3.000,00")
        self.assertContains(resposta, "1 contrato com cobrança mensal em vigor")
        self.assertContains(resposta, "Com quem mais gastamos")
        self.assertContains(resposta, "Atlas Elevadores")
        self.assertContains(resposta, "Construtora Vale")
        # Média simples por unidade, com a ressalva explícita.
        self.assertContains(resposta, "R$ 200,00")
        self.assertContains(resposta, "Média simples")

    def test_dashboard_separa_receitas_e_natureza_das_despesas(self):
        balanco = self._balanco_com_detalhe()
        self._login(self.gestao)

        resposta = self.client.get(
            reverse("core:transparencia", args=[balanco.pk])
        )

        self.assertContains(resposta, "De onde veio o dinheiro")
        self.assertContains(resposta, "Cotas condominiais")
        self.assertContains(resposta, "Multas e juros")
        self.assertContains(resposta, "Despesas do dia a dia")
        self.assertContains(resposta, "Obras e gastos extraordinários")
        self.assertEqual(
            resposta.context["naturezas"],
            [
                {"rotulo": "Despesas do dia a dia", "valor": "R$ 800,00"},
                {
                    "rotulo": "Obras e gastos extraordinários",
                    "valor": "R$ 5.000,00",
                },
            ],
        )

    def test_visao_por_fornecedor_nao_altera_o_total_gasto(self):
        balanco = self._balanco_com_detalhe()
        self._login(self.gestao)

        resposta = self.client.get(
            reverse("core:transparencia", args=[balanco.pk])
        )

        # A leitura por fornecedor é outra perspectiva: soma as mesmas linhas,
        # sem tocar no total declarado do balanço — por isso não há dupla contagem.
        somatorio = sum(
            (
                Decimal(
                    item["valor"]
                    .replace("R$ ", "")
                    .replace(".", "")
                    .replace(",", ".")
                )
                for item in resposta.context["fornecedores"]
            ),
            Decimal("0.00"),
        )
        self.assertEqual(somatorio, Decimal("5800.00"))
        self.assertEqual(resposta.context["despesas"], "R$ 9.800,00")

    def test_morador_nao_ve_nomes_de_fornecedores_mas_ve_agregados(self):
        balanco = self._balanco_com_detalhe(status=StatusBalanco.PUBLICADO)
        morador = criar_pessoa("morador.detalhe@example.test", "Morador")
        criar_vinculo(morador, self.condominio, Papel.MORADOR)
        self._login(morador)

        resposta = self.client.get(
            reverse("core:transparencia", args=[balanco.pk])
        )

        self.assertNotContains(resposta, "Com quem mais gastamos")
        self.assertNotContains(resposta, "Atlas Elevadores")
        self.assertNotContains(resposta, "Construtora Vale")
        # Os agregados que não expõem pessoas nem fornecedores continuam visíveis.
        self.assertContains(resposta, "Despesas do dia a dia")
        self.assertContains(resposta, "Cotas condominiais")
        self.assertEqual(resposta.context["fornecedores"], [])
