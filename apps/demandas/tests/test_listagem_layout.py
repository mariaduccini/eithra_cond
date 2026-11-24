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
from apps.demandas.constants import PrioridadeDemanda, StatusDemanda
from apps.demandas.forms import DemandaForm
from apps.demandas.services import alterar_status, criar_demanda, editar_demanda
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

    def _celula_de_acoes(self, resposta):
        """O trecho de uma célula de ações, já recortado do corpo da tabela."""
        html = resposta.content.decode()
        corpo = html[html.index("<tbody>") : html.index("</tbody>")]
        inicio = corpo.index("eithra-col-acoes")
        return corpo[inicio : corpo.index("</td>", inicio)]

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

    # -- Responsividade das colunas -------------------------------------------
    #
    # A grade não cabe inteira em qualquer largura. Em vez de apertar as nove
    # colunas — o que deixaria prioridade e prazo encostados e a leitura em
    # blocos —, ela mostra menos: o que sai primeiro é o que menos decide. Estes
    # testes travam a ordem da saída, não a medida exata de cada faixa.

    def test_a_grade_ampla_traz_as_nove_colunas(self):
        """No desktop amplo nada foi retirado: as nove colunas estão lá."""
        self._demanda(descricao="Abre pela metade.")
        resposta = self._abrir()
        for coluna in (
            "eithra-col-codigo",
            "eithra-col-titulo",
            "eithra-col-descricao",
            "eithra-col-local",
            "eithra-col-prioridade",
            "eithra-col-prazo",
            "eithra-col-responsavel",
            "eithra-col-situacao",
            "eithra-col-acoes",
        ):
            self.assertContains(resposta, f'class="{coluna}')

    def test_o_cabecalho_da_descricao_cede_espaco_abaixo_de_xxl(self):
        """A descrição é a primeira coluna a sair quando a largura aperta.

        As nove colunas só cabem na faixa mais ampla. Abaixo dela a célula deixa
        de ser desenhada — e some por inteiro, sem largura reservada, para o
        espaço voltar ao título.
        """
        self._demanda(descricao="Abre pela metade.")
        resposta = self._abrir()
        self.assertContains(
            resposta, 'class="eithra-col-descricao d-none d-xxl-table-cell"'
        )

    def test_o_responsavel_cede_espaco_abaixo_de_xl(self):
        """Depois da descrição, é o responsável quem sai."""
        self._demanda()
        resposta = self._abrir()
        self.assertContains(
            resposta, 'class="eithra-col-responsavel d-none d-xl-table-cell"'
        )

    def test_a_situacao_nunca_deixa_a_grade(self):
        """A situação permanece em todas as faixas: é ela que diz onde a demanda está."""
        self._demanda()
        resposta = self._abrir()
        self.assertContains(resposta, 'class="eithra-col-situacao"')
        self.assertNotContains(resposta, 'class="eithra-col-situacao d-none')

    # -- O código é do sistema, nunca de quem digita ---------------------------

    def test_o_formulario_nao_oferece_campo_de_codigo(self):
        campos = set(DemandaForm().fields)

        self.assertNotIn("codigo", campos)
        self.assertNotIn("referencia", campos)

    def test_editar_a_demanda_nao_troca_o_codigo(self):
        demanda = self._demanda(titulo="Portão travando")
        codigo = f"D-{str(demanda.pk).split('-')[0].upper()}"

        editar_demanda(
            demanda=demanda,
            usuario=self.gestao,
            titulo="Portão da garagem travando",
            descricao="Abre pela metade.",
            prioridade=PrioridadeDemanda.NORMAL,
        )

        resposta = self._abrir()
        self.assertEqual(resposta.context["demandas"][0]["referencia"], codigo)

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

    # -- Coluna de ações -------------------------------------------------------
    #
    # A linha inteira leva ao detalhe. O que se faz com a demanda vive no menu
    # de três pontos — um único controle, e nada de ação direta competindo com
    # o clique da linha (§47).

    def test_a_coluna_de_acoes_mostra_apenas_o_menu_de_tres_pontos(self):
        self._demanda(titulo="Portão travando")
        celula = self._celula_de_acoes(self._abrir())

        self.assertIn("bx-dots-vertical-rounded", celula)
        # Um só controle fora do menu: o próprio botão do ⋮.
        self.assertEqual(celula.count("<button"), 1)
        # Nenhum botão direto na linha — nem lápis, nem olho, nem "visualizar".
        self.assertNotIn('<a class="btn', celula)
        self.assertNotIn("eithra-acoes", celula)

    def test_o_menu_da_linha_oferece_apenas_acoes_reais(self):
        """Cada item abre uma janela que a página da demanda já oferece."""
        self._demanda()
        resposta = self._abrir()
        acoes = resposta.context["demandas"][0]["acoes"]

        self.assertEqual(
            [acao["label"] for acao in acoes],
            [
                "Ver detalhes",
                "Registrar atualização",
                "Adicionar evidência",
                "Editar",
            ],
        )
        # Toda ação leva ao detalhe da demanda, onde a janela correspondente
        # abre — a lista não inventa um caminho que não exista.
        detalhe = reverse("demandas:detalhe", args=[resposta.context["demandas"][0]["pk"]])
        for acao in acoes:
            self.assertTrue(acao["url"].startswith(detalhe))

    def test_o_acompanhamento_nao_tem_entrada_propria(self):
        """O acompanhamento passou a fazer parte do fluxo de atualização.

        Duas entradas para a mesma decisão faziam parecer que registrar a
        atualização e mexer no acompanhamento eram coisas diferentes.
        """
        self._demanda()
        resposta = self._abrir()

        self.assertNotContains(resposta, "Atualizar acompanhamento")
        self.assertEqual(
            [acao["label"] for acao in resposta.context["demandas"][0]["acoes"]],
            [
                "Ver detalhes",
                "Registrar atualização",
                "Adicionar evidência",
                "Editar",
            ],
        )

    # -- Scroll ----------------------------------------------------------------

    def test_a_listagem_participa_do_scroll_da_pagina(self):
        """Sem scroll vertical próprio: a lista rola junto com a página.

        A tabela larga cede para o scroll horizontal do tema quando não cabe,
        mas nunca ganha altura limitada — nada de `max-height` nem
        `overflow-y` subindo junto com a listagem.
        """
        self._demanda(descricao=DESCRICAO_LONGA)
        resposta = self._abrir()

        self.assertNotContains(resposta, "overflow-y")
        self.assertNotContains(resposta, "max-height")

    # -- Linha clicável --------------------------------------------------------

    def test_a_linha_inteira_leva_ao_detalhe(self):
        demanda = self._demanda(titulo="Portão travando")
        resposta = self._abrir()
        destino = reverse("demandas:detalhe", args=[demanda.pk])

        # O `data-href` é o contrato do clique na linha, que o JS compartilhado
        # já conhece — não há handler novo para esta listagem.
        self.assertContains(resposta, f'data-href="{destino}"')

    # -- Celular ---------------------------------------------------------------

    def test_o_celular_recebe_cartao_com_titulo_e_situacao(self):
        self._demanda(titulo="Portão travando", descricao="Abre pela metade.")
        resposta = self._abrir()

        # A tabela some abaixo de lg e o cartão assume, mostrando o que a
        # operação precisa ver primeiro: o assunto e a situação.
        self.assertContains(resposta, "table-responsive d-none d-lg-block")
        self.assertContains(resposta, "list-group list-group-flush d-lg-none")
        self.assertContains(resposta, "Portão travando")
