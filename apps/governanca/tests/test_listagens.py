"""Busca instantânea nas listagens da governança.

Contratos, garantias e documentos já sabiam procurar dentro do próprio domínio —
o objeto, o fornecedor, o emissor. Estes testes seguram o que a tela passou a
expor: o termo estreita o conjunto antes de ordenar e paginar, a página convive
com a busca na mesma barra de endereços e nenhuma delas alcança o condomínio
vizinho.
"""
from django.test import TestCase
from django.urls import reverse

from apps.cadastros.tests.apoio import (
    criar_condominio,
    criar_fornecedor,
    entrar_como,
)
from apps.condominios.constants import Papel
from apps.governanca.tests import apoio

# O mesmo tamanho de página das listagens inteiras.
TAMANHO = 20


class BuscaDeContratosTests(TestCase):
    """Alpha: quarenta e cinco contratos, vinte e cinco deles da Atlas."""

    def setUp(self):
        self.alpha = criar_condominio("Alpha")
        self.gestao = entrar_como(
            self.client, Papel.SINDICA, self.alpha, "sindica@exemplo.test"
        )
        atlas = criar_fornecedor(
            self.alpha, nome_razao_social="Elevadores Atlas Ltda."
        )
        verde = criar_fornecedor(self.alpha, nome_razao_social="Jardins Verde Ltda.")
        for numero in range(1, 26):
            apoio.criar_contrato(
                self.alpha,
                self.gestao,
                fornecedor=atlas,
                objeto=f"Manutenção predial {numero:02d}",
            )
        for numero in range(1, 21):
            apoio.criar_contrato(
                self.alpha,
                self.gestao,
                fornecedor=verde,
                objeto=f"Serviço de jardinagem {numero:02d}",
            )

    def _abrir(self, **parametros):
        resposta = self.client.get(reverse("governanca:contratos"), parametros)
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def test_a_tela_oferece_a_busca_com_o_que_ela_realmente_procura(self):
        resposta = self._abrir()
        self.assertContains(resposta, "Buscar objeto, fornecedor ou referência")
        self.assertContains(resposta, "eithra-busca")

    def test_termo_com_resultado_reduz_a_lista_e_conta_o_que_sobrou(self):
        resposta = self._abrir(q="Manutenção predial 07")
        self.assertEqual(resposta.context["pagina_contratos"].paginator.count, 1)
        self.assertEqual(
            resposta.context["contratos"][0]["objeto"], "Manutenção predial 07"
        )
        self.assertContains(resposta, "Mostrando 1–1 de 1 registro")

    def test_termo_sem_resultado_nao_diz_que_nao_ha_contrato(self):
        resposta = self._abrir(q="Zuleica")
        self.assertEqual(resposta.context["contratos"], [])
        self.assertEqual(resposta.context["pagina_contratos"].paginator.count, 0)
        self.assertContains(resposta, "Nenhum resultado encontrado")
        self.assertNotContains(resposta, "Nenhum contrato registrado")

    def test_busca_por_fornecedor_encontra_os_contratos_dele(self):
        resposta = self._abrir(q="Atlas")
        self.assertEqual(resposta.context["pagina_contratos"].paginator.count, 25)
        self.assertTrue(
            all("predial" in linha["objeto"] for linha in resposta.context["contratos"])
        )

    def test_busca_e_pagina_valem_juntas(self):
        primeira = self._abrir(q="predial")
        self.assertEqual(primeira.context["pagina_contratos"].paginator.count, 25)
        self.assertEqual(len(primeira.context["contratos"]), TAMANHO)

        segunda = self._abrir(q="predial", page=2)
        pagina = segunda.context["pagina_contratos"]
        self.assertEqual(pagina.paginator.count, 25)
        self.assertEqual(len(segunda.context["contratos"]), 5)
        self.assertTrue(
            all("predial" in linha["objeto"] for linha in segunda.context["contratos"])
        )

    def test_navegar_de_pagina_nao_apaga_a_busca(self):
        resposta = self._abrir(q="predial", page=2)
        self.assertContains(resposta, "q=predial")
        self.assertContains(resposta, "page=1")

    def test_mudar_a_busca_nao_carrega_a_pagina(self):
        resposta = self._abrir(q="predial")
        # O formulário de busca só leva o termo: começar de novo é começar da
        # primeira página, e não da página em que a pessoa estava.
        self.assertNotContains(resposta, 'name="page"')
        self.assertEqual(resposta.context["pagina_contratos"].number, 1)
        self.assertContains(resposta, "eithra-busca-limpar")

    def test_busca_nao_alcanca_contrato_de_outro_condominio(self):
        beta = criar_condominio("Beta")
        apoio.criar_contrato(beta, self.gestao, objeto="Contrato do Beta")

        resposta = self._abrir(q="Contrato do Beta")
        self.assertEqual(resposta.context["contratos"], [])

        inteira = self._abrir()
        self.assertNotIn(
            "Contrato do Beta",
            [linha["objeto"] for linha in inteira.context["contratos"]],
        )


class BuscaDeGarantiasTests(TestCase):
    """Duas garantias em Alpha: a da piscina, do fornecedor Azul, e a do elevador."""

    def setUp(self):
        self.alpha = criar_condominio("Alpha")
        self.gestao = entrar_como(
            self.client, Papel.SINDICA, self.alpha, "sindica@exemplo.test"
        )
        azul = criar_fornecedor(self.alpha, nome_razao_social="Piscinas Azul Ltda.")
        apoio.criar_garantia(
            self.alpha,
            self.gestao,
            descricao="Garantia da revitalização da piscina",
            numero_referencia="GR-2026-14",
            fornecedor=azul,
        )
        apoio.criar_garantia(
            self.alpha, self.gestao, descricao="Garantia do elevador social"
        )

    def _abrir(self, **parametros):
        resposta = self.client.get(reverse("governanca:garantias"), parametros)
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def test_a_tela_oferece_a_busca_com_o_que_ela_realmente_procura(self):
        resposta = self._abrir()
        self.assertContains(resposta, "Buscar descrição, fornecedor ou referência")

    def test_busca_por_descricao_com_resultado(self):
        resposta = self._abrir(q="revitalização")
        self.assertEqual(resposta.context["pagina_garantias"].paginator.count, 1)
        self.assertEqual(
            resposta.context["garantias"][0]["descricao"],
            "Garantia da revitalização da piscina",
        )

    def test_busca_por_fornecedor_encontra_a_garantia_que_ele_responde(self):
        resposta = self._abrir(q="Piscinas Azul")
        self.assertEqual(resposta.context["pagina_garantias"].paginator.count, 1)
        self.assertEqual(
            resposta.context["garantias"][0]["fornecedor"], "Piscinas Azul Ltda."
        )

    def test_busca_por_referencia_com_resultado(self):
        resposta = self._abrir(q="GR-2026-14")
        self.assertEqual(resposta.context["pagina_garantias"].paginator.count, 1)

    def test_termo_sem_resultado_nao_diz_que_nao_ha_garantia(self):
        resposta = self._abrir(q="Zuleica")
        self.assertEqual(resposta.context["garantias"], [])
        self.assertContains(resposta, "Nenhum resultado encontrado")
        self.assertNotContains(resposta, "Nenhuma garantia registrada")


class BuscaDeDocumentosTests(TestCase):
    """Dois documentos em Alpha: o laudo, da Engenharia Alfa, e a apólice."""

    def setUp(self):
        self.alpha = criar_condominio("Alpha")
        self.gestao = entrar_como(
            self.client, Papel.SINDICA, self.alpha, "sindica@exemplo.test"
        )
        tipo = apoio.criar_tipo_documento(self.alpha)
        apoio.criar_documento(
            self.alpha,
            self.gestao,
            tipo_documento=tipo,
            titulo="Laudo do elevador",
            emissor="Engenharia Alfa",
            numero_referencia="LAU-118",
        )
        apoio.criar_documento(
            self.alpha, self.gestao, tipo_documento=tipo, titulo="Apólice do seguro"
        )

    def _abrir(self, **parametros):
        resposta = self.client.get(reverse("governanca:documentos"), parametros)
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def test_a_tela_oferece_a_busca_com_o_que_ela_realmente_procura(self):
        resposta = self._abrir()
        self.assertContains(resposta, "Buscar título, emissor ou referência")

    def test_busca_por_titulo_com_resultado(self):
        resposta = self._abrir(q="Apólice")
        self.assertEqual(resposta.context["pagina_documentos"].paginator.count, 1)
        self.assertEqual(
            resposta.context["documentos"][0]["titulo"], "Apólice do seguro"
        )

    def test_busca_por_emissor_com_resultado(self):
        resposta = self._abrir(q="Engenharia Alfa")
        self.assertEqual(resposta.context["pagina_documentos"].paginator.count, 1)
        self.assertEqual(
            resposta.context["documentos"][0]["titulo"], "Laudo do elevador"
        )

    def test_busca_por_referencia_com_resultado(self):
        resposta = self._abrir(q="LAU-118")
        self.assertEqual(resposta.context["pagina_documentos"].paginator.count, 1)

    def test_termo_sem_resultado_nao_diz_que_nao_ha_documento(self):
        resposta = self._abrir(q="Zuleica")
        self.assertEqual(resposta.context["documentos"], [])
        self.assertContains(resposta, "Nenhum resultado encontrado")
        self.assertNotContains(resposta, "Nenhum documento registrado")

    def test_busca_nao_alcanca_documento_de_outro_condominio(self):
        beta = criar_condominio("Beta")
        apoio.criar_documento(beta, self.gestao, titulo="Laudo do Beta")

        resposta = self._abrir(q="Laudo do Beta")
        self.assertEqual(resposta.context["documentos"], [])
