"""Paginação da lista de demandas.

A lista não carrega tudo de uma vez: mostra um recorte do que foi filtrado,
com rodapé padronizado, e andar entre as páginas nunca apaga filtro nem busca.
A leitura do número e do tamanho da página é a mesma em toda listagem — este
teste prova o comportamento na tela de demandas, onde o volume é maior.
"""
from django.test import TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.demandas.constants import StatusDemanda
from apps.demandas.services import alterar_status, criar_demanda
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)

TAMANHO = 20


class ListagemPaginadaTests(TestCase):
    """Quarenta e cinco demandas: vinte e cinco abertas e vinte em andamento."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

        for numero in range(1, 26):
            self._demanda(f"Infiltração no bloco {numero:02d}")
        for numero in range(1, 21):
            demanda = self._demanda(f"Ruído no apartamento {numero:02d}")
            alterar_status(
                demanda=demanda,
                usuario=self.gestao,
                novo_status=StatusDemanda.EM_ANDAMENTO,
            )

    def _demanda(self, titulo):
        return criar_demanda(
            condominio=self.condominio, criado_por=self.gestao, titulo=titulo
        )

    def _abrir(self, **parametros):
        resposta = self.client.get(reverse("demandas:listagem"), parametros)
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def test_primeira_pagina_mostra_vinte_de_quarenta_e_cinco(self):
        resposta = self._abrir()
        pagina = resposta.context["pagina_demandas"]
        self.assertEqual(pagina.number, 1)
        self.assertEqual(pagina.paginator.per_page, TAMANHO)
        self.assertEqual(len(resposta.context["demandas"]), TAMANHO)
        self.assertContains(resposta, "Mostrando 1–20 de 45 registros")

    def test_pagina_seguinte_continua_a_lista(self):
        resposta = self._abrir(page=2)
        pagina = resposta.context["pagina_demandas"]
        self.assertEqual(pagina.number, 2)
        self.assertEqual(len(resposta.context["demandas"]), TAMANHO)
        self.assertContains(resposta, "Mostrando 21–40 de 45 registros")

    def test_pagina_anterior_e_alcancavel_da_ultima(self):
        resposta = self._abrir(page=3)
        pagina = resposta.context["pagina_demandas"]
        self.assertEqual(pagina.number, 3)
        self.assertEqual(len(resposta.context["demandas"]), 5)
        self.assertTrue(pagina.has_previous())

        anterior = self._abrir(page=pagina.previous_page_number())
        self.assertEqual(anterior.context["pagina_demandas"].number, 2)

    def test_trocar_o_tamanho_para_cinquenta_traz_tudo_numa_pagina(self):
        resposta = self._abrir(per_page=50)
        pagina = resposta.context["pagina_demandas"]
        self.assertEqual(pagina.paginator.per_page, 50)
        self.assertEqual(len(resposta.context["demandas"]), 45)
        self.assertContains(resposta, "Mostrando 1–45 de 45 registros")

    def test_trocar_o_tamanho_recomeca_na_primeira_pagina(self):
        resposta = self._abrir(page=3, per_page=50)
        pagina = resposta.context["pagina_demandas"]
        # A página 3 do recorte anterior não existe mais: o paginador devolve a
        # última do recorte novo, que aqui é a primeira.
        self.assertEqual(pagina.number, 1)

    def test_filtro_atravessa_as_paginas(self):
        resposta = self._abrir(status=StatusDemanda.ABERTA, page=2)
        pagina = resposta.context["pagina_demandas"]
        self.assertEqual(pagina.paginator.count, 25)
        self.assertEqual(pagina.number, 2)
        self.assertEqual(len(resposta.context["demandas"]), 5)
        self.assertContains(resposta, "Mostrando 21–25 de 25 registros")

    def test_busca_atravessa_as_paginas(self):
        resposta = self._abrir(busca="Infiltração", page=2)
        pagina = resposta.context["pagina_demandas"]
        self.assertEqual(pagina.paginator.count, 25)
        self.assertEqual(len(resposta.context["demandas"]), 5)
        self.assertContains(resposta, "Mostrando 21–25 de 25 registros")

    def test_navegar_preserva_a_busca_na_querystring(self):
        resposta = self._abrir(busca="Infiltração")
        self.assertContains(resposta, "busca=Infiltra")
        self.assertContains(resposta, "page=2")

    def test_pagina_fora_do_alcance_cai_na_ultima_disponivel(self):
        resposta = self._abrir(page=9999)
        pagina = resposta.context["pagina_demandas"]
        self.assertEqual(pagina.number, 3)
        self.assertEqual(len(resposta.context["demandas"]), 5)

    def test_pagina_sem_numero_cai_na_primeira(self):
        resposta = self._abrir(page="invalida")
        self.assertEqual(resposta.context["pagina_demandas"].number, 1)

    def test_tamanho_fora_das_opcoes_cai_no_padrao(self):
        resposta = self._abrir(per_page=999)
        pagina = resposta.context["pagina_demandas"]
        self.assertEqual(pagina.paginator.per_page, TAMANHO)

    def test_zero_registros_nao_mostra_rodape(self):
        resposta = self._abrir(status=StatusDemanda.CANCELADA)
        self.assertEqual(resposta.context["total"], 0)
        self.assertNotContains(resposta, "Mostrando")

    def test_o_rodape_oferece_os_tres_tamanhos(self):
        resposta = self._abrir()
        self.assertContains(resposta, "per_page=20")
        self.assertContains(resposta, "per_page=50")
        self.assertContains(resposta, "per_page=100")

    def test_o_rodape_tem_navegacao_de_desktop_e_de_celular(self):
        resposta = self._abrir()
        self.assertContains(resposta, "eithra-paginacao-lista")
        self.assertContains(resposta, "eithra-paginacao-mobile")
