"""Testes da leitura do balancete antigo (Métrica Condominial).

O balancete real é um PDF escaneado, sem camada textual e com um layout que não
é o do Eithra. Aqui ele entra como TEXTO sintético, no mesmo arranjo do
demonstrativo: nenhum documento de morador é versionado no repositório e nenhum
teste depende de o OCR estar instalado. O que se verifica é a leitura dos números
do demonstrativo, a conferência do saldo — e, sobretudo, o que a visão do
morador não pode mostrar.

Os valores são os de um mês fechado de propósito: saldo anterior mais receitas
menos despesas dá exatamente o saldo final, e a soma das linhas é igual aos
totais declarados.
"""
from __future__ import annotations

from django.test import TestCase

from apps.balancos.categorias import CategoriaEithra
from apps.balancos.contratos import StatusConferencia, TipoInconsistencia
from apps.balancos.extrator_legado import (
    METODO_LEGADO,
    ler_balanco_legado,
    ler_balanco_legado_do_texto,
)
from apps.balancos.privacidade import (
    ROTULO_PRIVADO,
    linha_para_o_morador,
    linha_privada,
)
from apps.balancos.tests.apoio import gerar_pdf_textual

# A linha que cobra uma unidade e cita um processo: o morador não a lê como está.
LINHA_DO_APARTAMENTO = "Certidao apto 103 - processo judicial"


def texto_do_balancete() -> str:
    """O demonstrativo sintético da administradora anterior.

    Sem acento de propósito: o mesmo texto vai para dentro de um PDF de teste
    cuja codificação não guarda acento, e a leitura não depende deles.
    """
    return "\n".join(
        [
            "Metrica Condominial",
            "Demonstrativo do balancete",
            "Competencia: 08/2026",
            "Saldo anterior 119.495,71",
            "Receitas",
            "Taxa condominial 50.000,00",
            "Fundo de reserva 13.836,20",
            "Total de receitas 63.836,20",
            "Despesas",
            "Folha de pagamento 35.000,00",
            "Energia eletrica 8.500,00",
            "Agua e esgoto 4.200,00",
            "Portaria e vigilancia 10.000,00",
            f"{LINHA_DO_APARTAMENTO} 1.200,00",
            "Manutencao geral 6.000,00",
            "Material de limpeza 3.730,98",
            "Administracao 1.500,00",
            "Total de despesas 70.130,98",
            "Saldo final 113.200,93",
        ]
    )


def texto_do_balancete_escaneado() -> str:
    """O texto como o OCR local devolve, com os erros dele.

    É um trecho real do "Balancete Jan 2024" (12 páginas escaneadas). Os defeitos
    do reconhecimento estão preservados de propósito: a vírgula trocada por ponto
    ("152.60", "11.070.00"), a seção colada ao número ("1RECEITA", "2DESPESAS"),
    o rótulo colado ("Total deRECEITA", "Saldoem31/12/2023"), o valor do saldo na
    linha seguinte e a linha só com percentual e valor. Ler este texto sem
    limpá-lo é o que prova a tolerância ao OCR.
    """
    return "\n".join(
        [
            "W015A Condominio RioVermelho Exclusive(7)",
            "Demonstrativo de Receitas e Despesas",
            "Jan/2024; Detalhado; Agrupado pelo nivel 3; Com conta categoria",
            "C Saldoem31/12/2023 %/ Valor",
            "4.807,88",
            "1RECEITA",
            "1.1 TAXA DE CONDOMINIO 1.1 TAXA DE CONDOMiNIO (venc.12 /2023) 58.78% 35.883,42",
            "1.2 JUROS 2,85% 1.740,76",
            "1.2 JUROS (venc.12/2023) 0,25% 152.60",
            "1.3MULTAS 0,03% 18.05",
            "1.5 TARIFA BANCARIA (venc.12/2023) -0,21% -129,60",
            "1.10AGUAConsumo=11 m² 0,21% 125,34",
            "1.10 AGUA Consumo = 7 m² 0,09% 54.11",
            "0,18% 112,50",
            "1.10 AGUA Consumo = 8 m² 0.57% 350.34",
            "1.45TAXAEXTRAPARCELA4/10-REFORMAFACHADAEPISCINA 18,13% 11.070.00",
            "1.46RENDIMENTODEAPLICACOES 0,01% 5,46",
            "Total deRECEITA 100,00% 61.051,14",
            "2DESPESAS",
            "2.81 DESPESAS BANCARIAS",
            "2.81.60TARIFASDECOBRANCACESTAEMPRESARIAL 0,25% 141,90",
            "2.81.60TARIFASDECOBRANCAEXTRATOUNIFICADO 0,02% 11,25",
            "2.81.60 TARIFAS DE COBRANCA PIX 0.02% 12,92",
            "Total deDESPESASBANCARIAS 0,30% 166,07",
            "2.82TARIFASPUBLICAS-CONCESSIONARIAS",
            "2.82.60 ENERGIA ELETRICA 8.86% 4.985,72",
            "2.82.61AGUAEESGOTO 10,27% 5.778,43",
            "2.82.62 TELEFONIA -INTERNET 0,31% 177,15",
            "Total deTARIFASPUBLICAS-CONCESSIONARIAS 19,51% 10.981,20",
        ]
    )


class LeituraDoBalanceteEscaneadoTests(TestCase):
    """A leitura do texto como o OCR devolve, com os erros do reconhecimento."""

    @classmethod
    def setUpTestData(cls):
        cls.balanco, cls.inconsistencias = ler_balanco_legado_do_texto(
            texto_do_balancete_escaneado(), nome_arquivo="Balancete Jan 2024"
        )

    def test_a_competencia_vem_do_cabecalho(self):
        self.assertEqual(self.balanco.competencia, "2024-01-01")

    def test_o_vencimento_nao_define_competencia(self):
        """Se "venc.12/2023" valesse, a competência seria dezembro de 2023."""
        self.assertNotEqual(self.balanco.competencia, "2023-12-01")

    def test_le_o_saldo_anterior_da_linha_seguinte(self):
        self.assertEqual(self.balanco.saldo_anterior, "4807.88")

    def test_le_o_total_de_receitas(self):
        self.assertEqual(self.balanco.receitas_total, "61051.14")

    def test_le_a_linha_com_ponto_decimal_do_ocr(self):
        juros = next(
            item
            for item in self.balanco.receitas
            if "juros" in item.descricao_normalizada
            and "venc" in item.descricao_normalizada
        )
        self.assertEqual(juros.valor, "152.60")

    def test_le_valor_com_milhar_e_ponto_decimal(self):
        extra = next(
            item
            for item in self.balanco.receitas
            if "taxaextra" in item.descricao_normalizada
        )
        self.assertEqual(extra.valor, "11070.00")

    def test_linha_so_com_percentual_vira_continuacao(self):
        continuacao = next(
            item
            for item in self.balanco.receitas
            if "(continuação)" in item.descricao_original
        )
        self.assertEqual(continuacao.valor, "112.50")
        self.assertTrue(
            any(
                item.tipo == TipoInconsistencia.LINHA_INCOMPLETA
                for item in self.inconsistencias
            )
        )

    def test_o_grupo_281_fecha_com_o_subtotal(self):
        grupo = next(g for g in self.balanco.grupos if "bancarias" in g.nome.lower())
        self.assertEqual(grupo.quantidade, 3)
        self.assertEqual(grupo.total_declarado, "166.07")
        self.assertEqual(grupo.status, StatusConferencia.CONFERE)

    def test_o_grupo_282_e_lido_e_gera_apontamento(self):
        grupo = next(
            g for g in self.balanco.grupos if "concessionarias" in g.nome.lower()
        )
        self.assertEqual(grupo.quantidade, 3)
        self.assertEqual(grupo.status, StatusConferencia.FALTA_IDENTIFICAR)
        self.assertTrue(
            any(
                item.tipo == TipoInconsistencia.TOTAL_DIVERGENTE
                and "concessionarias" in item.referencia.lower()
                for item in self.inconsistencias
            )
        )

    def test_a_leitura_nao_devolve_erro_com_a_fixture_cortada(self):
        """A fixture é um trecho: o subtotal não fecha, mas a leitura acontece."""
        self.assertTrue(self.balanco.despesas)
        self.assertTrue(self.balanco.receitas)

    def test_cabecalho_e_nome_conflitantes_usam_o_cabecalho(self):
        texto = texto_do_balancete_escaneado().replace("Jan/2024", "Fev/2024")
        balanco, inconsistencias = ler_balanco_legado_do_texto(
            texto, nome_arquivo="Balancete Jan 2024"
        )

        self.assertEqual(balanco.competencia, "2024-02-01")
        self.assertTrue(
            any(
                item.tipo == TipoInconsistencia.COMPETENCIA_CONFLITANTE
                for item in inconsistencias
            )
        )


class LeituraDoBalanceteAntigoTests(TestCase):
    """O que a leitura entende da abertura do balancete antigo."""

    @classmethod
    def setUpTestData(cls):
        cls.balanco, cls.inconsistencias = ler_balanco_legado_do_texto(
            texto_do_balancete()
        )

    def test_le_a_competencia(self):
        self.assertEqual(self.balanco.competencia, "2026-08-01")

    def test_le_o_saldo_anterior(self):
        self.assertEqual(self.balanco.saldo_anterior, "119495.71")

    def test_le_o_total_de_receitas(self):
        self.assertEqual(self.balanco.receitas_total, "63836.20")

    def test_le_o_total_de_despesas(self):
        self.assertEqual(self.balanco.despesas_total, "70130.98")

    def test_le_o_saldo_final(self):
        self.assertEqual(self.balanco.saldo_final, "113200.93")

    def test_le_as_linhas_de_receitas_por_grupo(self):
        descricoes = sorted(item.descricao_normalizada for item in self.balanco.receitas)
        self.assertEqual(
            descricoes, ["fundo de reserva", "taxa condominial"]
        )

    def test_le_as_linhas_de_despesas_por_grupo(self):
        descricoes = sorted(item.descricao_normalizada for item in self.balanco.despesas)
        self.assertIn("folha de pagamento", descricoes)
        self.assertIn("energia eletrica", descricoes)
        self.assertIn("manutencao geral", descricoes)

    def test_a_checagem_do_saldo_fica_verde(self):
        """Saldo anterior + receitas − despesas = saldo final, sem aviso."""
        self.assertEqual(
            [item for item in self.inconsistencias if item.referencia == "saldo"], []
        )

    def test_a_matematica_do_documento_fecha(self):
        self.assertEqual(self.inconsistencias, ())

    def test_registra_o_metodo_proprio_da_leitura_legada(self):
        self.assertEqual(self.balanco.provenance["metodo"], METODO_LEGADO)
        self.assertTrue(self.balanco.despesas)
        self.assertEqual(self.balanco.despesas[0].procedencia.metodo, METODO_LEGADO)

    def test_o_que_nao_mapeia_nao_fica_sem_categoria(self):
        self.assertTrue(self.balanco.despesas)
        self.assertTrue(
            all(
                item.categoria_eithra != CategoriaEithra.NAO_CLASSIFICADO
                for item in self.balanco.despesas
            )
        )


class LeituraDoPdfDoBalanceteTests(TestCase):
    """O mesmo demonstrativo dentro de um PDF — o caminho do arquivo ao texto.

    O balancete real chega escaneado; aqui ele entra com camada textual para
    exercitar o arquivo inteiro sem depender de OCR instalado.
    """

    def test_le_o_pdf_textual_como_le_o_texto(self):
        balanco, inconsistencias = ler_balanco_legado(
            gerar_pdf_textual(texto_do_balancete())
        )

        self.assertEqual(balanco.competencia, "2026-08-01")
        self.assertEqual(balanco.saldo_anterior, "119495.71")
        self.assertEqual(balanco.receitas_total, "63836.20")
        self.assertEqual(balanco.despesas_total, "70130.98")
        self.assertEqual(balanco.saldo_final, "113200.93")
        self.assertEqual(inconsistencias, ())


class ChecagemVermelhaDoBalanceteTests(TestCase):
    """Quando o saldo não fecha, a leitura aponta — e a tela mostra o vermelho."""

    def test_saldo_que_nao_fecha_vira_apontamento(self):
        texto = texto_do_balancete().replace(
            "Saldo final 113.200,93", "Saldo final 100.000,00"
        )

        _, inconsistencias = ler_balanco_legado_do_texto(texto)

        referencias = [item.referencia for item in inconsistencias]
        self.assertIn("saldo", referencias)


class PrivacidadeDoMoradorTests(TestCase):
    """O que identifica pessoa ou unidade não chega ao morador como está."""

    def test_a_linha_do_apartamento_e_privada(self):
        self.assertTrue(linha_privada(LINHA_DO_APARTAMENTO))

    def test_o_acento_nao_esconde_a_unidade(self):
        self.assertTrue(linha_privada("Certidão apto 103 - processo judicial"))

    def test_a_linha_do_apartamento_vira_outras_despesas_para_o_morador(self):
        self.assertEqual(
            linha_para_o_morador(LINHA_DO_APARTAMENTO), ROTULO_PRIVADO
        )

    def test_uma_despesa_comum_continua_visivel(self):
        self.assertFalse(linha_privada("Manutencao geral"))
        self.assertEqual(linha_para_o_morador("Manutencao geral"), "Manutencao geral")

    def test_a_linha_que_a_leitura_leu_e_a_mesma_que_a_privacidade_esconde(self):
        """A prova ponta a ponta: o que o extrator lê é o que o morador não vê."""
        balanco, _ = ler_balanco_legado_do_texto(texto_do_balancete())
        privada = next(
            item
            for item in balanco.despesas
            if "apto 103" in item.descricao_normalizada
        )

        self.assertEqual(linha_para_o_morador(privada.descricao_original), ROTULO_PRIVADO)
