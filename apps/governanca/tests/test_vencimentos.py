"""Vencimentos: contratos, garantias e documentos na faixa certa.

A leitura não guarda estado — cada faixa é lida do vencimento na hora. Por isso
os testes fixam uma data de referência e conferem em que faixa cada fonte cai,
que o que está inativo ou é de outro condomínio não aparece, que a ordem é por
urgência, que o painel corta o topo e quem entra na tela.
"""
from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.tests.apoio import criar_condominio, criar_pessoa, entrar_como
from apps.condominios.constants import Papel
from apps.governanca.tests.apoio import (
    HOJE,
    criar_contrato,
    criar_documento,
    criar_garantia,
)
from apps.governanca.vencimentos import (
    agrupar_por_faixa,
    coletar_vencimentos,
    vencimentos_proximos,
)


class FaixasDeVencimentoTests(TestCase):
    """Cada fonte cai na faixa que a sua data determina."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestor = criar_pessoa("gestor@exemplo.test")

    def _por_faixa(self, condominio=None, hoje=HOJE):
        itens = coletar_vencimentos(condominio or self.condominio, hoje=hoje)
        return {faixa["chave"]: faixa["itens"] for faixa in agrupar_por_faixa(itens)}

    def test_cada_fonte_cai_na_faixa_certa(self):
        criar_contrato(
            self.condominio, self.gestor, data_fim=HOJE - timedelta(days=5)
        )
        criar_garantia(
            self.condominio, self.gestor, data_fim=HOJE + timedelta(days=10)
        )
        criar_documento(
            self.condominio, self.gestor, data_validade=HOJE + timedelta(days=60)
        )
        criar_contrato(
            self.condominio,
            self.gestor,
            objeto="Contrato longo",
            data_fim=HOJE + timedelta(days=200),
        )

        faixas = self._por_faixa()
        self.assertEqual([item["tipo"] for item in faixas["vencido"]], ["Contrato"])
        self.assertEqual([item["tipo"] for item in faixas["ate_30"]], ["Garantia"])
        self.assertEqual([item["tipo"] for item in faixas["ate_90"]], ["Documento"])
        self.assertEqual([item["tipo"] for item in faixas["em_dia"]], ["Contrato"])

    def test_inativo_e_de_outro_condominio_nao_aparecem(self):
        criar_contrato(
            self.condominio, self.gestor, ativo=False, data_fim=HOJE - timedelta(days=1)
        )
        outro = criar_condominio("Beta")
        criar_contrato(outro, self.gestor, data_fim=HOJE - timedelta(days=1))

        self.assertEqual(coletar_vencimentos(self.condominio, hoje=HOJE), [])

    def test_ordem_por_urgencia(self):
        criar_contrato(
            self.condominio, self.gestor, objeto="B", data_fim=HOJE + timedelta(days=20)
        )
        criar_contrato(
            self.condominio, self.gestor, objeto="A", data_fim=HOJE - timedelta(days=10)
        )
        criar_contrato(
            self.condominio, self.gestor, objeto="C", data_fim=HOJE + timedelta(days=2)
        )

        itens = coletar_vencimentos(self.condominio, hoje=HOJE)
        self.assertEqual([item["titulo"] for item in itens], ["A", "C", "B"])

    def test_proximos_junta_vencidos_e_ate_30(self):
        criar_contrato(
            self.condominio, self.gestor, objeto="Vencido", data_fim=HOJE - timedelta(days=3)
        )
        criar_contrato(
            self.condominio, self.gestor, objeto="Breve", data_fim=HOJE + timedelta(days=20)
        )
        criar_contrato(
            self.condominio, self.gestor, objeto="Distante", data_fim=HOJE + timedelta(days=200)
        )

        titulos = [item["titulo"] for item in vencimentos_proximos(self.condominio, hoje=HOJE)]
        self.assertEqual(titulos, ["Vencido", "Breve"])


class TelaDeVencimentosTests(TestCase):
    """A tela e o painel, do acesso ao recorte."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestor = criar_pessoa("gestor@exemplo.test")

    def test_exige_login(self):
        self.assertEqual(self.client.get(reverse("governanca:vencimentos")).status_code, 302)

    def test_quem_nao_enxerga_o_modulo_recebe_404(self):
        entrar_como(self.client, Papel.OPERACIONAL, self.condominio, "op@exemplo.test")
        self.assertEqual(self.client.get(reverse("governanca:vencimentos")).status_code, 404)

    def test_gestao_e_supervisao_entram(self):
        papeis = (Papel.ADMINISTRADORA, Papel.SINDICA, Papel.SUPERVISORA)
        for indice, papel in enumerate(papeis):
            entrar_como(self.client, papel, self.condominio, f"g{indice}@exemplo.test")
            with self.subTest(papel=papel):
                self.assertEqual(
                    self.client.get(reverse("governanca:vencimentos")).status_code, 200
                )

    def test_tela_mostra_o_que_vence(self):
        criar_contrato(
            self.condominio,
            self.gestor,
            objeto="Elevador de serviço",
            data_fim=date.today() + timedelta(days=10),
        )
        entrar_como(self.client, Papel.SINDICA, self.condominio, "sindica@exemplo.test")
        resposta = self.client.get(reverse("governanca:vencimentos"))
        self.assertContains(resposta, "Elevador de serviço")
        self.assertContains(resposta, "Até 30 dias")

    def test_painel_corta_o_topo_em_cinco(self):
        hoje = date.today()
        for numero in range(6):
            criar_contrato(
                self.condominio,
                self.gestor,
                objeto=f"Contrato {numero}",
                data_fim=hoje + timedelta(days=numero),
            )
        entrar_como(self.client, Papel.SINDICA, self.condominio, "sindica@exemplo.test")
        resposta = self.client.get(reverse("core:home"))
        self.assertEqual(len(resposta.context["vencimentos_de_hoje"]), 5)
        self.assertTrue(resposta.context["tem_mais_vencimentos"])

    def test_painel_nao_mostra_o_bloco_para_a_supervisao(self):
        criar_contrato(
            self.condominio,
            self.gestor,
            data_fim=date.today() + timedelta(days=3),
        )
        entrar_como(self.client, Papel.SUPERVISORA, self.condominio, "sup@exemplo.test")
        resposta = self.client.get(reverse("core:home"))
        self.assertEqual(resposta.context["vencimentos_de_hoje"], [])
