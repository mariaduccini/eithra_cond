"""Apresentação da listagem de demandas: grade enxuta e filtros rápidos.

A lista não separa mais código, título e descrição em três colunas: o código
saiu da linha — vive no detalhe —, a descrição virou uma linha curta de
contexto sob o título (e some quando não acrescenta leitura), e o prazo e a
situação se juntaram em "Situação", dita em linguagem humana. Acima da grade,
quatro chips recortam a lista e cada contador é o número exato de linhas que ele
mostra. Estes testes travam essa estrutura, porque é ela que sustenta a leitura
objetiva em qualquer resolução.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.condominios.constants import Papel
from apps.demandas.constants import AguardandoTipo, PrioridadeDemanda, StatusDemanda
from apps.demandas.forms import DemandaForm
from apps.demandas.models import Demanda
from apps.demandas.services import (
    alterar_status,
    atualizar_acompanhamento,
    criar_demanda,
    editar_demanda,
)
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)
from apps.governanca.models import Projeto, ProjetoDemanda

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

    def test_a_grade_enxuta_tem_as_colunas_que_decidem(self):
        demanda = self._demanda(titulo="Portão travando", descricao="Abre e fecha.")
        resposta = self._abrir()

        # O código e a descrição saíram de colunas próprias; o que resta é o
        # que muda a decisão de quem olha a lista.
        for coluna in (
            "eithra-col-demanda",
            "eithra-col-local",
            "eithra-col-prioridade",
            "eithra-col-responsavel",
            "eithra-col-situacao",
            "eithra-col-acoes",
        ):
            self.assertContains(resposta, f'class="{coluna}')

        linha = resposta.context["demandas"][0]
        self.assertEqual(linha["referencia"], f"D-{str(demanda.pk).split('-')[0].upper()}")

    def test_a_descricao_nao_repete_o_titulo(self):
        titulo = "Portão da garagem travando"
        self._demanda(titulo=titulo, descricao="Abre pela metade e volta.")
        resposta = self._abrir()

        celula = resposta.content.decode()
        # O título aparece uma vez, no próprio título da linha — nunca dentro do
        # contexto, que traz a descrição.
        self.assertEqual(celula.count(f'title="{titulo}"'), 1)

    # -- Responsividade das colunas -------------------------------------------
    #
    # A grade não cabe inteira em qualquer largura. Em vez de apertar as colunas
    # — o que deixaria prioridade e situação encostados e a leitura em blocos —,
    # ela mostra menos: o que sai primeiro é o que menos decide. Estes testes
    # travam a ordem da saída, não a medida exata de cada faixa.

    def test_o_responsavel_cede_espaco_abaixo_de_xl(self):
        """O responsável é a coluna que sai primeiro quando a largura aperta."""
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
    # As ações do dia a dia são ícones sem borda na própria linha: concluir e
    # contar novidade. O menu de três pontos guarda só o que é raro — editar e as
    # demais ações que o detalhe também oferece —, para nada competir com o
    # clique da linha.

    def test_a_coluna_de_acoes_reune_os_icones_do_dia_a_dia_e_o_menu(self):
        self._demanda(titulo="Portão travando")
        celula = self._celula_de_acoes(self._abrir())

        # Os ícones sem borda do dia a dia: concluir e contar novidade.
        self.assertIn("bx-check-circle", celula)
        self.assertIn("bx-message-rounded-dots", celula)
        self.assertIn("eithra-acao-icone", celula)
        # O menu de três pontos, com as ações raras.
        self.assertIn("bx-dots-vertical-rounded", celula)
        # Dois controles clicáveis além dos ícones: o botão do ⋮ é o único
        # <button> aqui (concluir também é botão; contar novidade é um link).
        self.assertLessEqual(celula.count("<button"), 2)
        # Nenhum botão com moldura direto na linha — nem lápis, nem olho.
        self.assertNotIn('<a class="btn', celula)
        self.assertNotIn("eithra-acoes", celula)

    def test_o_menu_da_linha_guarda_apenas_as_acoes_raras(self):
        """O menu não repete o que já tem ícone na linha."""
        self._demanda()
        resposta = self._abrir()
        acoes = resposta.context["demandas"][0]["acoes"]
        rotulos = [acao["label"] for acao in acoes]

        # O que se faz o tempo todo vive nos ícones da linha, nunca aqui.
        for repetida in (
            "Ver detalhes",
            "Registrar atualização",
            "Adicionar evidência",
        ):
            self.assertNotIn(repetida, rotulos)
        # Editar é o caso raro que o menu precisa oferecer.
        self.assertIn("Editar demanda", rotulos)
        # Cada ação traz o próprio ícone — as mesmas marcas do topo do detalhe.
        for acao in acoes:
            self.assertTrue(acao["icon"])

    def test_o_menu_nao_guarda_situacoes_do_dia_a_dia(self):
        """Concluir tem ícone; andar e esperar vivem no contar novidade."""
        self._demanda()
        resposta = self._abrir()
        rotulos = [acao["label"] for acao in resposta.context["demandas"][0]["acoes"]]

        for fora in ("Concluir demanda", "Iniciar", "Aguardar"):
            self.assertNotIn(fora, rotulos)
        # A ordem do que fica é a da decisão: manutenção, compra, comunicado,
        # editar e cancelar — todos com ícone. Registrar a compra da demanda vem
        # logo depois de criar a manutenção.
        self.assertEqual(
            rotulos,
            [
                "Criar manutenção",
                "Registrar compra",
                "Gerar comunicado",
                "Editar demanda",
                "Cancelar",
            ],
        )

    def test_descricao_igual_ao_titulo_nao_vira_segunda_linha(self):
        titulo = "Portão da garagem travando"
        self._demanda(titulo=titulo, descricao=f"{titulo} há três dias")
        resposta = self._abrir()

        self.assertEqual(resposta.context["demandas"][0]["descricao_curta"], "")
        self.assertNotContains(resposta, "há três dias")

    def test_registro_por_email_vira_icone_sem_repetir_o_texto(self):
        self._demanda(
            titulo="Vazamento no 302",
            descricao="Registro recebido por e-mail. Natureza: Vazamento.",
        )
        resposta = self._abrir()
        linha = resposta.context["demandas"][0]

        self.assertTrue(linha["veio_por_email"])
        self.assertEqual(linha["email_natureza"], "Vazamento")
        self.assertEqual(linha["descricao_curta"], "")
        self.assertNotContains(resposta, "Registro recebido por e-mail")
        self.assertContains(resposta, "bx-envelope")

    def test_o_acompanhamento_nao_tem_entrada_propria(self):
        """O acompanhamento passou a fazer parte do fluxo de atualização.

        Duas entradas para a mesma decisão faziam parecer que registrar a
        atualização e mexer no acompanhamento eram coisas diferentes.
        """
        self._demanda()
        resposta = self._abrir()

        self.assertNotContains(resposta, "Atualizar acompanhamento")
        self.assertNotIn(
            "Registrar atualização",
            [acao["label"] for acao in resposta.context["demandas"][0]["acoes"]],
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

    def test_o_celular_recebe_cartao_compacto_com_titulo_e_situacao(self):
        self._demanda(titulo="Portão travando", descricao="Abre pela metade.")
        resposta = self._abrir()

        # A tabela some abaixo de lg e o cartão assume, mostrando o que a
        # operação precisa ver primeiro: o assunto, o selo da situação e o
        # responsável — sem repetir a grade inteira.
        self.assertContains(resposta, "table-responsive d-none d-lg-block")
        self.assertContains(resposta, "list-group list-group-flush d-lg-none")
        self.assertContains(resposta, "eithra-demanda-cartao")
        self.assertContains(resposta, "Portão travando")


class FiltrosRapidosDaListagemTests(TestCase):
    """Os chips recortam a lista e contam exatamente o número de linhas que mostram.

    Cada contador sai da mesma base que a lista usa, com o mesmo recorte que o
    chip aplica — por isso o número nunca discorda do que aparece ao clicar.
    """

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    def _demanda(self, **campos):
        status = campos.pop("status", None)
        base = {
            "condominio": self.condominio,
            "criado_por": self.gestao,
            "titulo": "Demanda de teste",
        }
        base.update(campos)
        demanda = criar_demanda(**base)
        if status and status != StatusDemanda.ABERTA:
            alterar_status(demanda=demanda, usuario=self.gestao, novo_status=status)
        return demanda

    def _abrir(self, **parametros):
        resposta = self.client.get(reverse("demandas:listagem"), parametros)
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def _chip(self, resposta, chave):
        return next(
            chip for chip in resposta.context["chips"] if chip["chave"] == chave
        )

    def _titulos(self, resposta):
        return [linha["titulo"] for linha in resposta.context["demandas"]]

    # -- Os quatro recortes ---------------------------------------------------

    def test_os_chips_trazem_os_quatro_recortes_na_ordem(self):
        resposta = self._abrir()
        self.assertEqual(
            [chip["chave"] for chip in resposta.context["chips"]],
            ["atrasadas", "esperando", "paradas", "concluidas"],
        )

    def test_o_contador_do_chip_e_o_numero_de_linhas_que_ele_mostra(self):
        """O número do chip é o número exato de linhas que ele abre."""
        self._demanda(
            titulo="Atrasada", prazo=timezone.localdate() - timedelta(days=2)
        )
        self._demanda(
            titulo="Em dia", prazo=timezone.localdate() + timedelta(days=5)
        )

        sem_filtro = self._abrir()
        self.assertEqual(self._chip(sem_filtro, "atrasadas")["total"], 1)

        com_filtro = self._abrir(chip="atrasadas")
        self.assertEqual(self._titulos(com_filtro), ["Atrasada"])
        # O chip ativo se marca e some com um clique de novo.
        self.assertTrue(self._chip(com_filtro, "atrasadas")["ativo"])

    def test_a_atrasada_conta_a_previsao_vencida(self):
        self._demanda(titulo="Previsão vencida")
        demanda = Demanda.objects.get(titulo="Previsão vencida")
        atualizar_acompanhamento(
            demanda=demanda,
            usuario=self.gestao,
            previsao=timezone.localdate() - timedelta(days=1),
        )

        resposta = self._abrir(chip="atrasadas")
        self.assertEqual(self._chip(resposta, "atrasadas")["total"], 1)
        self.assertEqual(self._titulos(resposta), ["Previsão vencida"])

    def test_esperando_alguem_conta_quem_aguarda_fornecedor_ou_terceiro(self):
        self._demanda(titulo="Esperando")
        demanda = Demanda.objects.get(titulo="Esperando")
        atualizar_acompanhamento(
            demanda=demanda,
            usuario=self.gestao,
            aguardando_tipo=AguardandoTipo.FORNECEDOR,
        )
        self._demanda(titulo="Sem espera")

        resposta = self._abrir(chip="esperando")
        self.assertEqual(self._chip(resposta, "esperando")["total"], 1)
        self.assertEqual(self._titulos(resposta), ["Esperando"])

    def test_paradas_conta_o_que_esta_sem_atualizacao_ha_mais_de_sete_dias(self):
        parada = self._demanda(titulo="Parada")
        self._demanda(titulo="Recente")
        limite = timezone.now() - timedelta(days=10)
        Demanda.objects.filter(pk=parada.pk).update(atualizado_em=limite)

        resposta = self._abrir(chip="paradas")
        self.assertEqual(self._chip(resposta, "paradas")["total"], 1)
        self.assertEqual(self._titulos(resposta), ["Parada"])

    def test_concluidas_no_mes_conta_o_que_fechou_neste_mes(self):
        self._demanda(titulo="Concluída", status=StatusDemanda.CONCLUIDA)
        self._demanda(titulo="Aberta")

        resposta = self._abrir(chip="concluidas")
        self.assertEqual(self._chip(resposta, "concluidas")["total"], 1)
        self.assertEqual(self._titulos(resposta), ["Concluída"])

    def test_o_chip_de_zero_fica_discreto_sem_desaparecer(self):
        self._demanda(titulo="Aberta")
        resposta = self._abrir()
        self.assertEqual(self._chip(resposta, "atrasadas")["total"], 0)
        # O chip continua na tela — só discreto, nunca invisível.
        self.assertContains(resposta, "is-vazio")

    # -- O filtro rápido recorta, não inventa ---------------------------------

    def test_o_filtro_do_formulario_estreita_tambem_o_contador_do_chip(self):
        """Contador e lista leem a mesma base filtrada — o número não mente."""
        self._demanda(
            titulo="Atrasada Alpha",
            prazo=timezone.localdate() - timedelta(days=3),
        )
        outro = criar_condominio("Beta")
        criar_vinculo(self.gestao, outro, papel=Papel.SINDICA)
        criar_demanda(
            condominio=outro,
            criado_por=self.gestao,
            titulo="Atrasada Beta",
            prazo=timezone.localdate() - timedelta(days=3),
        )

        resposta = self._abrir(chip="atrasadas", condominio=str(self.condominio.pk))
        self.assertEqual(self._chip(resposta, "atrasadas")["total"], 1)
        self.assertEqual(self._titulos(resposta), ["Atrasada Alpha"])

    # -- Ordem padrão por urgência --------------------------------------------

    def test_a_ordem_padrao_e_por_urgencia(self):
        """Atrasadas primeiro, depois pela data mais próxima, depois as demais."""
        self._demanda(
            titulo="Sem prazo",
            prioridade=PrioridadeDemanda.NORMAL,
        )
        self._demanda(
            titulo="Vence em 3 dias",
            prazo=timezone.localdate() + timedelta(days=3),
        )
        self._demanda(
            titulo="Atrasada",
            prazo=timezone.localdate() - timedelta(days=1),
        )

        self.assertEqual(
            self._titulos(self._abrir()),
            ["Atrasada", "Vence em 3 dias", "Sem prazo"],
        )


class ProjetoNaListagemTests(TestCase):
    """O vínculo com um projeto na lista: um chip discreto e um filtro."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)
        self.projeto = Projeto.objects.create(
            condominio=self.condominio,
            nome="Regularização do AVCB",
            criado_por=self.gestao,
        )
        self.ligada = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Reunir pranchas do AVCB",
        )
        self.solta = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Trocar lâmpada do corredor",
        )
        ProjetoDemanda.objects.create(projeto=self.projeto, demanda=self.ligada)

    def _abrir(self, **parametros):
        resposta = self.client.get(reverse("demandas:listagem"), parametros)
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def test_o_chip_do_projeto_aparece_na_linha(self):
        self.assertContains(self._abrir(), "Projeto: Regularização do AVCB")

    def test_o_filtro_de_projeto_recorta_a_lista(self):
        resposta = self._abrir(projeto=str(self.projeto.pk))
        self.assertContains(resposta, "Reunir pranchas do AVCB")
        self.assertNotContains(resposta, "Trocar lâmpada do corredor")
