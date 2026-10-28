"""Apresentação da listagem de demandas: código, título e descrição.

A grid mostra três coisas diferentes em três colunas: o código identifica, o
título nomeia, a descrição contextualiza. Nenhuma delas repete a outra, e o
texto que não couber fica no detalhe — a lista não cresce para acomodar texto
longo. Estes testes travam essa estrutura, porque é ela que sustenta a leitura
em qualquer resolução.
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

TITULO_LONGO = (
    "Vazamento persistente na prumada do banheiro social do apartamento 302 "
    "com infiltração visível na laje do pavimento inferior"
)
DESCRICAO_LONGA = (
    "O morador relata que o vazamento começou há cerca de três semanas e que "
    "a mancha de umidade já alcança a parede da sala, com odor de mofo."
)


class ListagemDeDemandasTests(TestCase):
    """Uma lista com demandas de textos curtos, longos e vazios."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    def _demanda(self, titulo="Demanda de teste", descricao=""):
        return criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo=titulo,
            descricao=descricao,
        )

    def _abrir(self, **parametros):
        resposta = self.client.get(reverse("demandas:listagem"), parametros)
        self.assertEqual(resposta.status_code, 200)
        return resposta

    # -- Estrutura das colunas ------------------------------------------------

    def test_codigo_titulo_e_descricao_sao_colunas_separadas(self):
        demanda = self._demanda(titulo="Portão travando", descricao="Abre e fecha.")
        resposta = self._abrir()

        self.assertContains(resposta, "eithra-col-codigo")
        self.assertContains(resposta, "eithra-col-titulo")
        self.assertContains(resposta, "eithra-col-descricao")

        linha = resposta.context["demandas"][0]
        self.assertEqual(linha["referencia"], f"D-{str(demanda.pk).split('-')[0].upper()}")

    def test_a_descricao_nao_repete_o_titulo(self):
        titulo = "Portão da garagem travando"
        self._demanda(titulo=titulo, descricao="Abre pela metade e volta.")
        resposta = self._abrir()

        celula = resposta.content.decode()
        # O título aparece uma vez na linha da tabela e uma vez no card do
        # celular — nunca dentro da célula da descrição.
        self.assertEqual(celula.count(f'title="{titulo}"'), 1)

    def test_o_cabecalho_da_descricao_cede_espaco_abaixo_de_xl(self):
        self._demanda(descricao="Abre pela metade.")
        resposta = self._abrir()
        self.assertContains(
            resposta, 'class="eithra-col-descricao d-none d-xl-table-cell"'
        )

    # -- Textos ---------------------------------------------------------------

    def test_titulo_curto_aparece_inteiro(self):
        self._demanda(titulo="Lâmpada queimada")
        resposta = self._abrir()
        self.assertContains(resposta, "Lâmpada queimada")

    def test_titulo_longo_preserva_o_texto_para_a_dica(self):
        self._demanda(titulo=TITULO_LONGO)
        resposta = self._abrir()
        self.assertContains(resposta, f'title="{TITULO_LONGO}"')

    def test_descricao_curta_aparece_sem_corte(self):
        self._demanda(descricao="Abre pela metade e volta.")
        resposta = self._abrir()
        self.assertContains(resposta, "Abre pela metade e volta.")

    def test_descricao_longa_e_resumida_na_linha_e_inteira_na_dica(self):
        self._demanda(descricao=DESCRICAO_LONGA)
        resposta = self._abrir()
        resumo = resposta.context["demandas"][0]["descricao_curta"]

        self.assertTrue(resumo.endswith("..."))
        self.assertLess(len(resumo), len(DESCRICAO_LONGA))
        self.assertContains(resposta, f'title="{DESCRICAO_LONGA}"')

    def test_descricao_vazia_diz_que_nao_ha_contexto(self):
        self._demanda(titulo="Barulho no 4º andar", descricao="")
        resposta = self._abrir()
        self.assertContains(resposta, "Sem contexto adicional.")

    # -- Paginação e filtros continuam valendo --------------------------------

    def test_paginacao_continua_funcionando(self):
        for numero in range(1, 46):
            self._demanda(titulo=f"Demanda {numero:02d}")

        primeira = self._abrir()
        self.assertEqual(len(primeira.context["demandas"]), 20)

        segunda = self._abrir(page=2)
        self.assertEqual(segunda.context["pagina_demandas"].number, 2)
        self.assertEqual(len(segunda.context["demandas"]), 20)

        maior = self._abrir(per_page=50)
        self.assertEqual(len(maior.context["demandas"]), 45)

    def test_filtro_continua_funcionando(self):
        self._demanda(titulo="Aberta")
        segunda = self._demanda(titulo="Em andamento")
        alterar_status(
            demanda=segunda,
            usuario=self.gestao,
            novo_status=StatusDemanda.EM_ANDAMENTO,
        )

        resposta = self._abrir(status=StatusDemanda.EM_ANDAMENTO)
        self.assertEqual(resposta.context["total"], 1)
        self.assertEqual(
            [linha["titulo"] for linha in resposta.context["demandas"]],
            ["Em andamento"],
        )

    # -- Celular ---------------------------------------------------------------

    def test_o_celular_recebe_cartao_com_titulo_e_situacao(self):
        self._demanda(titulo="Portão travando", descricao="Abre pela metade.")
        resposta = self._abrir()

        # A tabela some abaixo de lg e o cartão assume, mostrando o que a
        # operação precisa ver primeiro: o assunto e a situação.
        self.assertContains(resposta, "table-responsive d-none d-lg-block")
        self.assertContains(resposta, "list-group list-group-flush d-lg-none")
        self.assertContains(resposta, "Portão travando")
