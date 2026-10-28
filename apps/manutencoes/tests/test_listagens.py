"""Filtro e paginação na lista de manutenções.

Filtrar e paginar valem juntos: a página mostra o recorte do que foi filtrado,
nunca um recorte do conjunto inteiro. Mudar de página mantém o que foi pedido,
e mudar o filtro recomeça da primeira página.
"""
from django.test import TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.manutencoes.constants import StatusManutencao
from apps.manutencoes.services import alterar_status, criar_manutencao
from apps.manutencoes.tests.apoio import criar_condominio, criar_pessoa, criar_vinculo

TAMANHO = 20


class ListagemFiltradaEPaginadaTests(TestCase):
    """Trinta manutenções: vinte e cinco abertas e cinco em andamento."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

        for numero in range(1, 26):
            self._manutencao(f"Aberta {numero:02d}")
        for numero in range(1, 6):
            manutencao = self._manutencao(f"Andamento {numero:02d}")
            alterar_status(
                manutencao=manutencao,
                usuario=self.gestao,
                novo_status=StatusManutencao.EM_ANDAMENTO,
            )

    def _manutencao(self, titulo):
        return criar_manutencao(
            condominio=self.condominio, criado_por=self.gestao, titulo=titulo
        )

    def _abrir(self, **parametros):
        resposta = self.client.get(reverse("manutencoes:listagem"), parametros)
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def test_o_total_e_o_do_filtro_e_nao_o_do_condominio(self):
        resposta = self._abrir(status=StatusManutencao.ABERTA)
        self.assertEqual(resposta.context["total"], 25)
        self.assertEqual(len(resposta.context["manutencoes"]), TAMANHO)
        self.assertContains(resposta, "Mostrando 1–20 de 25 registros")

    def test_a_pagina_seguinte_continua_dentro_do_filtro(self):
        resposta = self._abrir(status=StatusManutencao.ABERTA, page=2)
        pagina = resposta.context["pagina_manutencoes"]
        self.assertEqual(pagina.number, 2)
        self.assertEqual(pagina.paginator.count, 25)
        self.assertEqual(len(resposta.context["manutencoes"]), 5)
        self.assertFalse(pagina.has_next())

    def test_filtro_sem_resultado_distingue_ausencia_de_resultado(self):
        resposta = self._abrir(status=StatusManutencao.CANCELADA)
        self.assertEqual(resposta.context["manutencoes"], [])
        self.assertEqual(resposta.context["total"], 0)
        self.assertContains(resposta, "Nenhum resultado encontrado")

    def test_navegar_de_pagina_nao_apaga_o_filtro(self):
        resposta = self._abrir(status=StatusManutencao.ABERTA, page=2)
        self.assertContains(resposta, f"status={StatusManutencao.ABERTA}")
        self.assertContains(resposta, "page=1")

    def test_so_ofertas_de_limpar_aparecem_quando_ha_o_que_limpar(self):
        com_filtro = self._abrir(status=StatusManutencao.ABERTA)
        self.assertContains(com_filtro, "Limpar filtros")

        sem_filtro = self._abrir()
        self.assertNotContains(sem_filtro, "Limpar filtros")

    def test_trocar_o_tamanho_traz_mais_registros_numa_pagina(self):
        resposta = self._abrir(per_page=50)
        pagina = resposta.context["pagina_manutencoes"]
        self.assertEqual(pagina.paginator.per_page, 50)
        self.assertEqual(len(resposta.context["manutencoes"]), 30)
        self.assertContains(resposta, "Mostrando 1–30 de 30 registros")

    def test_tamanho_fora_das_opcoes_cai_no_padrao(self):
        resposta = self._abrir(per_page=37)
        pagina = resposta.context["pagina_manutencoes"]
        self.assertEqual(pagina.paginator.per_page, TAMANHO)

    def test_pagina_fora_do_alcance_cai_na_ultima_disponivel(self):
        resposta = self._abrir(status=StatusManutencao.ABERTA, page=99)
        pagina = resposta.context["pagina_manutencoes"]
        self.assertEqual(pagina.number, 2)
        self.assertEqual(len(resposta.context["manutencoes"]), 5)

    def test_pagina_sem_numero_cai_na_primeira(self):
        resposta = self._abrir(page="abc")
        pagina = resposta.context["pagina_manutencoes"]
        self.assertEqual(pagina.number, 1)

    def test_lista_vazia_nao_mostra_rodape(self):
        resposta = self._abrir(status=StatusManutencao.CANCELADA)
        self.assertNotContains(resposta, "Mostrando")
