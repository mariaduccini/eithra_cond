"""Busca instantânea e paginação nas listagens da comunidade.

A lista de trabalho não é um arquivo: primeiro o conjunto é reduzido pelo que a
pessoa procurou, depois ordenado, e só então recortado em páginas — paginar
antes de buscar daria páginas de um conjunto que não é o que foi pedido.

Estes testes seguram esse contrato onde ele é mais fácil de quebrar em silêncio:
a busca e a página convivem na mesma barra de endereços, e nenhuma delas alcança
o condomínio vizinho.
"""
from django.test import TestCase
from django.urls import reverse

from apps.cadastros.tests.apoio import criar_condominio, entrar_como
from apps.comunidade.tests import apoio
from apps.condominios.constants import Papel

# O mesmo tamanho de página das listagens inteiras.
TAMANHO = 20


class ListagemComBuscaEPaginacaoTests(TestCase):
    """Pessoas de Alpha: quarenta e cinco no total, vinte e cinco com "Pessoa"."""

    def setUp(self):
        self.alpha = criar_condominio("Alpha")
        entrar_como(self.client, Papel.SINDICA, self.alpha, "sindica@exemplo.test")
        for numero in range(1, 26):
            apoio.criar_pessoa_da_comunidade(
                self.alpha, nome=f"Pessoa Teste {numero:02d}"
            )
        for numero in range(1, 21):
            apoio.criar_pessoa_da_comunidade(self.alpha, nome=f"Morador {numero:02d}")

    def _abrir(self, **parametros):
        resposta = self.client.get(reverse("comunidade:pessoas"), parametros)
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def test_primeira_pagina_traz_vinte_e_conta_o_total(self):
        resposta = self._abrir()
        pagina = resposta.context["pagina_pessoas"]
        self.assertEqual(pagina.number, 1)
        self.assertEqual(pagina.paginator.count, 45)
        self.assertEqual(len(resposta.context["pessoas"]), TAMANHO)
        self.assertContains(resposta, "Mostrando 1–20 de 45")

    def test_pagina_seguinte_continua_de_onde_parou(self):
        resposta = self._abrir(page=2)
        pagina = resposta.context["pagina_pessoas"]
        self.assertEqual(pagina.number, 2)
        self.assertEqual(len(resposta.context["pessoas"]), TAMANHO)
        self.assertTrue(pagina.has_previous())
        self.assertTrue(pagina.has_next())

    def test_ultima_pagina_traz_o_resto_sem_proxima(self):
        resposta = self._abrir(page=3)
        pagina = resposta.context["pagina_pessoas"]
        self.assertEqual(pagina.number, 3)
        self.assertEqual(len(resposta.context["pessoas"]), 5)
        self.assertFalse(pagina.has_next())

    def test_pagina_fora_do_intervalo_nao_quebra_a_tela(self):
        pagina = self._abrir(page=99).context["pagina_pessoas"]
        self.assertEqual(pagina.number, 3)

    def test_termo_com_resultado_reduz_a_lista(self):
        resposta = self._abrir(q="Pessoa Teste 07")
        pagina = resposta.context["pagina_pessoas"]
        self.assertEqual(pagina.paginator.count, 1)
        self.assertEqual(resposta.context["pessoas"][0]["nome"], "Pessoa Teste 07")

    def test_termo_sem_resultado_nao_diz_que_nao_ha_cadastro(self):
        resposta = self._abrir(q="Zuleica")
        self.assertEqual(resposta.context["pessoas"], [])
        self.assertEqual(resposta.context["pagina_pessoas"].paginator.count, 0)
        self.assertContains(resposta, "Nenhum resultado encontrado")

    def test_busca_apagada_devolve_a_lista_inteira(self):
        resposta = self._abrir(q="")
        self.assertEqual(resposta.context["pagina_pessoas"].paginator.count, 45)
        self.assertEqual(len(resposta.context["pessoas"]), TAMANHO)

    def test_busca_e_pagina_valem_juntas(self):
        primeira = self._abrir(q="Pessoa")
        self.assertEqual(primeira.context["pagina_pessoas"].paginator.count, 25)
        self.assertEqual(len(primeira.context["pessoas"]), TAMANHO)

        segunda = self._abrir(q="Pessoa", page=2)
        pagina = segunda.context["pagina_pessoas"]
        self.assertEqual(pagina.paginator.count, 25)
        self.assertEqual(len(segunda.context["pessoas"]), 5)
        self.assertTrue(
            all("Pessoa" in linha["nome"] for linha in segunda.context["pessoas"])
        )

    def test_navegar_de_pagina_nao_apaga_a_busca(self):
        resposta = self._abrir(q="Pessoa", page=2)
        self.assertContains(resposta, "q=Pessoa")
        self.assertContains(resposta, "page=1")

    def test_mudar_a_busca_nao_carrega_a_pagina(self):
        resposta = self._abrir(q="Pessoa")
        # O formulário de busca só leva o termo: começar de novo é começar da
        # primeira página, e não da página em que a pessoa estava.
        self.assertNotContains(resposta, 'name="page"')
        self.assertEqual(resposta.context["pagina_pessoas"].number, 1)
        self.assertContains(resposta, "eithra-busca-limpar")

    def test_busca_nao_alcanca_registro_de_outro_condominio(self):
        beta = criar_condominio("Beta")
        apoio.criar_pessoa_da_comunidade(beta, nome="Pessoa do Beta")

        resposta = self._abrir(q="Pessoa do Beta")
        self.assertEqual(resposta.context["pessoas"], [])

        inteira = self._abrir()
        self.assertNotIn(
            "Pessoa do Beta",
            [linha["nome"] for linha in inteira.context["pessoas"]],
        )


class PaginacaoDasOutrasListasTests(TestCase):
    """Unidades e veículos crescem como pessoas e são recortados do mesmo jeito."""

    def setUp(self):
        self.alpha = criar_condominio("Alpha")
        entrar_como(self.client, Papel.SINDICA, self.alpha, "sindica@exemplo.test")
        for numero in range(1, 26):
            apoio.criar_unidade(self.alpha, identificador=f"{numero:03d}")
            apoio.criar_veiculo(self.alpha, placa=f"ABC{numero:02d}")

    def test_cada_lista_recorta_a_sua_propria_pagina(self):
        telas = (
            ("comunidade:unidades", "pagina_unidades", "unidades"),
            ("comunidade:veiculos", "pagina_veiculos", "veiculos"),
        )
        for nome, chave_pagina, chave_lista in telas:
            with self.subTest(tela=nome):
                resposta = self.client.get(reverse(nome))
                pagina = resposta.context[chave_pagina]
                self.assertEqual(pagina.paginator.count, 25)
                self.assertEqual(len(resposta.context[chave_lista]), TAMANHO)

                seguinte = self.client.get(reverse(nome), {"page": 2})
                self.assertEqual(len(seguinte.context[chave_lista]), 5)
