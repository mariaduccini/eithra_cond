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
