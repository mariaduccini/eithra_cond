import re

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.tests import apoio as cadastros_apoio
from apps.comunidade.models import Pessoa
from apps.comunidade.tests import apoio as comunidade_apoio
from apps.condominios.constants import Papel
from apps.demandas.models import Demanda
from apps.portaria.constants import HORA_DE_ABERTURA, TipoAcessoPortaria
from apps.portaria.forms import AutorizacaoPortariaForm, ChegadaNaoPrevistaForm
from apps.portaria.models import AcessoPortaria, AutorizacaoPortaria
from apps.portaria.tests import apoio


class PortariaViewsTests(TestCase):
    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(self.usuario)

    def test_porteiro_acessa_o_painel(self):
        resposta = self.client.get(reverse("portaria:painel"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Portaria")

    def test_morador_recebe_404(self):
        morador = apoio.criar_pessoa("morador@teste.com", "Morador")
        apoio.criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)
        resposta = self.client.get(reverse("portaria:painel"))
        self.assertEqual(resposta.status_code, 404)

    def test_esperado_aparece_na_lista(self):
        apoio.criar_autorizacao(self.condominio, self.usuario, nome="João Esperado")
        resposta = self.client.get(reverse("portaria:painel"))
        self.assertContains(resposta, "João Esperado")
        self.assertContains(resposta, "Chegou")

    def test_chegou_registra_entrada(self):
        autorizacao = apoio.criar_autorizacao(self.condominio, self.usuario, nome="João Esperado")
        resposta = self.client.post(
            reverse("portaria:chegou", args=[autorizacao.pk]),
            {"condominio": str(self.condominio.pk)},
        )
        self.assertEqual(resposta.status_code, 302)
        painel = self.client.get(reverse("portaria:painel"))
        self.assertContains(painel, "Chegando")
        self.assertContains(painel, "João Esperado")
        self.assertContains(painel, "Saiu")

    def test_saiu_fecha_a_presenca(self):
        acesso = apoio.criar_acesso(self.condominio, self.usuario, nome="Visitante Dentro")
        resposta = self.client.post(
            reverse("portaria:saiu", args=[acesso.pk]),
            {"condominio": str(self.condominio.pk)},
        )
        self.assertEqual(resposta.status_code, 302)
        acesso.refresh_from_db()
        self.assertIsNotNone(acesso.saida_em)

    def test_chegada_nao_prevista(self):
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="701")
        resposta = self.client.post(
            reverse("portaria:criar_chegada_nao_prevista"),
            {
                "condominio": str(self.condominio.pk),
                "tipo_acesso": "visitante",
                "nome": "Visitante Avulso",
                "unidade": str(unidade.pk),
            },
        )
        self.assertEqual(resposta.status_code, 302)
        painel = self.client.get(reverse("portaria:painel"))
        self.assertContains(painel, "Visitante Avulso")
        self.assertContains(painel, "Aguardando")

    def test_isolamento_por_condominio(self):
        outro = apoio.criar_condominio("Outro")
        autorizacao = apoio.criar_autorizacao(outro, self.usuario, nome="Fora do escopo")
        resposta = self.client.post(
            reverse("portaria:chegou", args=[autorizacao.pk]),
            {"condominio": str(self.condominio.pk)},
        )
        self.assertEqual(resposta.status_code, 404)


class PortariaTurnoAnteriorTests(TestCase):
    """Quem entrou antes do turno de hoje continua dentro — e a tela diz isso.

    Sem a marca, uma presença que atravessou o turno se confunde com uma chegada
    recente, e a portaria perde a única pista visual de que aquele acesso ficou
    pendente de ontem. A identificação precisa aparecer na movimentação e na
    lista de saída.
    """

    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro.turno@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(self.usuario)

    def _hoje_as(self, hora):
        return timezone.localtime().replace(
            hour=hora, minute=0, second=0, microsecond=0
        )

    def test_presenca_que_atravessou_o_turno_e_identificada(self):
        ficou = comunidade_apoio.criar_unidade(self.condominio, identificador="201")
        chegou = comunidade_apoio.criar_unidade(self.condominio, identificador="202")
        apoio.criar_acesso(
            self.condominio,
            self.usuario,
            nome="Ficou de Ontem",
            unidade=ficou,
            entrada_em=self._hoje_as(HORA_DE_ABERTURA - 4),
        )
        apoio.criar_acesso(
            self.condominio,
            self.usuario,
            nome="Chegou Agora",
            unidade=chegou,
            entrada_em=self._hoje_as(HORA_DE_ABERTURA + 2),
        )

        conteudo = self.client.get(reverse("portaria:painel")).content.decode()
        self.assertIn("Turno anterior", conteudo)
        # A marca pertence a quem ficou, não à chegada recente.
        self.assertRegex(
            conteudo,
            r"(?s)Ficou de Ontem</div>.{0,400}Turno anterior",
        )

    def test_sem_presenca_antiga_nao_marca_turno_anterior(self):
        chegou = comunidade_apoio.criar_unidade(self.condominio, identificador="203")
        apoio.criar_acesso(
            self.condominio,
            self.usuario,
            nome="Chegou Agora",
            unidade=chegou,
            entrada_em=self._hoje_as(HORA_DE_ABERTURA + 2),
        )
        conteudo = self.client.get(reverse("portaria:painel")).content.decode()
        self.assertNotIn("Turno anterior", conteudo)


class PortariaBuscaDaTelaTests(TestCase):
    """O contrato da tela da busca: o campo, o gatilho e onde a lista aparece.

    A busca da portaria usa o mesmo caminho do modal de chegada — digita,
    espera, e a lista aparece abaixo do campo, sem recarregar a página. Estes
    testes prendem o campo marcado, o endereço que ele consulta, o condomínio
    que viaja junto e o lugar da lista: colada ao campo e antes do relato, do
    resumo e da movimentação. Nada vem pronto do servidor.
    """

    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(self.usuario)

    def _formulario(self, classe, **params):
        conteudo = self.client.get(reverse("portaria:painel"), params).content.decode()
        inicio = conteudo.find('class="%s"' % classe)
        self.assertNotEqual(inicio, -1, "formulário %s não está na tela" % classe)
        return conteudo[inicio : conteudo.find("</form>", inicio)]

    def _painel(self, **params):
        return self.client.get(reverse("portaria:painel"), params).content.decode()

    def _campo_da_busca(self, conteudo):
        """O próprio elemento do campo: do `<input` que o abre até o `>`."""
        campo = conteudo.find('id="busca-portaria"')
        self.assertNotEqual(campo, -1, "o campo da busca não está na tela")
        inicio = conteudo.rfind("<input", 0, campo)
        return conteudo[inicio : conteudo.find(">", campo)]

    def test_a_busca_principal_usa_o_autocomplete_do_registro_de_chegada(self):
        """Um mecanismo só: o campo carrega o endereço que a lista consulta."""
        conteudo = self._painel()
        campo = self._campo_da_busca(conteudo)
        self.assertIn("data-eithra-portaria-busca", campo)
        self.assertIn(reverse("portaria:buscar_portaria"), campo)
        self.assertIn("data-eithra-portaria-resultado", conteudo)

    def test_a_busca_principal_nao_recarrega_a_pagina(self):
        """Soltar o filtro: nada de `data-eithra-busca` na busca de cima."""
        conteudo = self._painel()
        inicio = conteudo.find('class="eithra-portaria-barra"')
        barra = conteudo[inicio : conteudo.find('id="busca-portaria"')]
        self.assertNotIn("data-eithra-busca", barra)
        self.assertNotIn("<form", barra)

    def test_o_campo_principal_e_o_campo_de_busca(self):
        campo = self._campo_da_busca(self._painel())
        self.assertIn('type="search"', campo)
        self.assertIn('name="q"', campo)

    def test_o_historico_liga_os_filtros_reativos(self):
        formulario = self._formulario("eithra-portaria-filtros", aba="historico")
        self.assertIn("data-eithra-filtros", formulario)

    def test_o_condominio_em_vista_viaja_com_a_busca(self):
        """Buscar dentro de um condomínio não pode trocar de condomínio."""
        campo = self._campo_da_busca(self._painel())
        self.assertIn('data-condominio="%s"' % self.condominio.pk, campo)

    def test_o_resultado_da_busca_fica_colado_ao_campo_e_antes_do_resto(self):
        """Sem termo, nada pronto: a lista nasce logo abaixo do próprio campo.

        Quem busca não deve procurar onde a resposta apareceu: ela vem antes do
        relato, do resumo e da movimentação — e nada dela vem do servidor.
        """
        conteudo = self._painel()
        resultado = conteudo.find("data-eithra-portaria-resultado")

        self.assertNotEqual(resultado, -1, "o lugar da lista não está na tela")
        self.assertLess(conteudo.find('id="busca-portaria"'), resultado)
        for seguinte in (
            "eithra-portaria-barra-acoes",
            'id="relato-portaria"',
            "eithra-portaria-resumo",
            "Chegando",
        ):
            self.assertLess(resultado, conteudo.find(seguinte), seguinte)

        self.assertNotIn("eithra-autorizador-opcao", conteudo)


class PortariaBuscaPortariaTests(TestCase):
    """A busca da portaria numa lista só, do jeito que a lista é desenhada.

    Quem opera não escolhe o que está procurando: digita o que sabe e recebe o
    que existe — nome, unidade, morador, placa ou empresa. A resposta é única,
    ordenada por relevância, sem repetir o mesmo item e sem atravessar
    condomínio. O markup é o mesmo do registro de chegada, para a tela não
    inventar uma segunda aparência.
    """

    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(self.usuario)
        self.url = reverse("portaria:buscar_portaria")

    def _buscar(self, termo, condominio=None):
        return self.client.get(
            self.url,
            {"q": termo, "condominio": str((condominio or self.condominio).pk)},
        )

    def _morador(self, identificador, nome):
        unidade = comunidade_apoio.criar_unidade(
            self.condominio, identificador=identificador
        )
        pessoa = comunidade_apoio.criar_pessoa_da_comunidade(self.condominio, nome)
        comunidade_apoio.criar_vinculo_pessoa_unidade(self.condominio, pessoa, unidade)
        return unidade, pessoa

    def test_sem_termo_a_lista_nao_aparece(self):
        """Termo vazio fecha o dropdown: nada de lista nem de estado vazio."""
        resposta = self._buscar("")

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, "eithra-autorizador-opcao")
        self.assertNotContains(resposta, "Nenhum resultado encontrado.")

    def test_nome_retorna_a_pessoa(self):
        self._morador("502", "Ana Paula")

        resposta = self._buscar("Ana")

        self.assertContains(resposta, "Ana Paula")

    def test_unidade_retorna_o_morador(self):
        self._morador("301", "Ana Moradora")

        resposta = self._buscar("301")

        self.assertContains(resposta, "301")
        self.assertContains(resposta, "Ana Moradora")

    def test_empresa_retorna_o_fornecedor(self):
        cadastros_apoio.criar_fornecedor(
            self.condominio, nome_razao_social="Elevadores Atlas Schindler Ltda."
        )

        resposta = self._buscar("Atlas")

        self.assertContains(resposta, "Elevadores Atlas Schindler Ltda.")
        self.assertContains(resposta, "Fornecedor")

    def test_placa_retorna_o_contexto_do_veiculo(self):
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="501")
        pessoa = comunidade_apoio.criar_pessoa_da_comunidade(self.condominio, "Motorista")
        veiculo = comunidade_apoio.criar_veiculo(self.condominio, placa="ABC1D23")
        comunidade_apoio.criar_vinculo_veiculo_pessoa(self.condominio, veiculo, pessoa)
        comunidade_apoio.criar_vinculo_veiculo_unidade(self.condominio, veiculo, unidade)

        resposta = self._buscar("ABC1D23")

        self.assertContains(resposta, "ABC1D23")
        self.assertContains(resposta, "Motorista")

    def test_a_lista_nao_repete_o_mesmo_item(self):
        """Ana aparece uma vez — como pessoa — e não também pelo veículo.

        A placa e a unidade até citam Ana no contexto, mas nenhuma delas é uma
        segunda "Ana Paula" na lista: o nome principal é único.
        """
        unidade, pessoa = self._morador("502", "Ana Paula")
        veiculo = comunidade_apoio.criar_veiculo(self.condominio, placa="ANA1234")
        comunidade_apoio.criar_vinculo_veiculo_pessoa(self.condominio, veiculo, pessoa)
        comunidade_apoio.criar_vinculo_veiculo_unidade(self.condominio, veiculo, unidade)

        conteudo = self._buscar("Ana").content.decode()

        self.assertEqual(
            conteudo.count(
                '<span class="eithra-person-result-nome">Ana Paula</span>'
            ),
            1,
        )

    def test_lista_vazia_diz_que_nao_encontrou(self):
        resposta = self._buscar("Ninguem Aqui")

        self.assertContains(resposta, "Nenhum resultado encontrado.")
        self.assertNotContains(resposta, "eithra-autorizador-opcao")

    def test_o_resultado_usa_o_markup_do_registro_de_chegada(self):
        """A lista é a mesma do modal: opção, avatar, nome, contexto e seta."""
        self._morador("502", "Ana Paula")

        resposta = self._buscar("Ana")

        self.assertContains(resposta, "eithra-autorizador-opcao")
        self.assertContains(resposta, "eithra-person-result")
        self.assertContains(resposta, "eithra-person-result-nome")
        self.assertContains(resposta, "eithra-person-result-contexto")
        self.assertContains(resposta, "eithra-avatar")
        self.assertContains(resposta, "bx-chevron-right")

    def test_a_busca_nao_traz_outro_condominio(self):
        """O que é de fora não entra — nem por nome, nem por unidade."""
        outro = apoio.criar_condominio("Outro")
        unidade = comunidade_apoio.criar_unidade(outro, identificador="999")
        pessoa = comunidade_apoio.criar_pessoa_da_comunidade(outro, "Ana do Outro")
        comunidade_apoio.criar_vinculo_pessoa_unidade(outro, pessoa, unidade)

        resposta = self._buscar("Ana")

        self.assertNotContains(resposta, "Ana do Outro")
        self.assertNotContains(resposta, "999")

    def test_quem_nao_opera_a_portaria_nao_busca(self):
        """A busca não atravessa a permissão: quem não opera não vê nada."""
        morador = apoio.criar_pessoa("morador@teste.com", "Morador")
        apoio.criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)

        resposta = self._buscar("Ana")

        self.assertEqual(resposta.status_code, 404)

    def test_a_lista_nao_mostra_documento(self):
        comunidade_apoio.criar_pessoa_da_comunidade(
            self.condominio, "Pessoa Privada", documento="12345678901"
        )

        resposta = self._buscar("Pessoa Privada")

        self.assertContains(resposta, "Pessoa Privada")
        self.assertNotContains(resposta, "12345678901")

    # -- O clique localiza contexto, não opera -------------------------------

    def _destinos(self, resposta):
        """Os endereços que a lista oferece, na ordem em que aparecem."""
        return re.findall(r'href="([^"]+)"', resposta.content.decode())

    def test_a_pessoa_leva_ao_historico_dela(self):
        """A busca acha a pessoa; o histórico é que conta o que ela fez aqui."""
        self._morador("502", "Ana Paula")

        destinos = self._destinos(self._buscar("Ana"))

        self.assertEqual(len(destinos), 1)
        self.assertIn("aba=historico", destinos[0])
        self.assertIn("q=Ana+Paula", destinos[0])
        self.assertEqual(self.client.get(destinos[0]).status_code, 200)

    def test_a_unidade_leva_ao_historico_da_unidade(self):
        """A unidade leva pelo identificador — é o que o filtro do histórico lê."""
        self._morador("502", "Ana Paula")

        destinos = self._destinos(self._buscar("502"))

        self.assertEqual(len(destinos), 1)
        self.assertIn("q=502", destinos[0])
        self.assertEqual(self.client.get(destinos[0]).status_code, 200)

    def test_a_presenca_nao_traz_a_acao_de_saida(self):
        """Quem está dentro continua assunto da operação: a busca não o fecha."""
        acesso = apoio.criar_acesso(
            self.condominio, self.usuario, nome="João da Silva"
        )

        conteudo = self._buscar("João").content.decode()

        self.assertIn("Dentro agora", conteudo)
        self.assertNotIn(reverse("portaria:saiu", args=[acesso.pk]), conteudo)
        self.assertNotIn(">Saiu<", conteudo)

    def test_a_autorizacao_abre_a_revisao_e_nao_a_chegada(self):
        """Esperado se revisa na tela que já existe; a entrada é ato da portaria."""
        esperado = apoio.criar_autorizacao(
            self.condominio, self.usuario, nome="João Esperado"
        )

        destinos = self._destinos(self._buscar("João Esperado"))

        self.assertEqual(len(destinos), 1)
        self.assertIn("abrir=verificar", destinos[0])
        self.assertIn("item=%s" % esperado.pk, destinos[0])
        self.assertNotIn(reverse("portaria:chegou", args=[esperado.pk]), destinos[0])
        self.assertEqual(self.client.get(destinos[0]).status_code, 200)

    def test_o_veiculo_leva_ao_historico_pela_placa(self):
        """O histórico já procura por placa: o veículo leva para lá."""
        unidade = comunidade_apoio.criar_unidade(
            self.condominio, identificador="501"
        )
        pessoa = comunidade_apoio.criar_pessoa_da_comunidade(
            self.condominio, "Motorista"
        )
        veiculo = comunidade_apoio.criar_veiculo(self.condominio, placa="ABC1D23")
        comunidade_apoio.criar_vinculo_veiculo_pessoa(
            self.condominio, veiculo, pessoa
        )
        comunidade_apoio.criar_vinculo_veiculo_unidade(
            self.condominio, veiculo, unidade
        )

        destinos = self._destinos(self._buscar("ABC1D23"))

        self.assertEqual(len(destinos), 1)
        self.assertIn("aba=historico", destinos[0])
        self.assertIn("q=ABC1D23", destinos[0])
        self.assertEqual(self.client.get(destinos[0]).status_code, 200)

    def test_o_fornecedor_e_informacao_e_nao_acao(self):
        """Sem tela própria, o fornecedor se apresenta: não promete um clique."""
        cadastros_apoio.criar_fornecedor(
            self.condominio, nome_razao_social="Elevadores Atlas Schindler Ltda."
        )

        resposta = self._buscar("Atlas")
        conteudo = resposta.content.decode()

        self.assertContains(resposta, "Elevadores Atlas Schindler Ltda.")
        self.assertEqual(self._destinos(resposta), [])
        self.assertIn("eithra-person-result-informativo", conteudo)
        self.assertNotIn("bx-chevron-right", conteudo)

    def test_nenhum_resultado_dispara_operacao(self):
        """A busca localiza: nenhum item carrega formulário, POST ou ação."""
        self._morador("502", "Ana Paula")
        apoio.criar_acesso(self.condominio, self.usuario, nome="João da Silva")
        apoio.criar_autorizacao(self.condominio, self.usuario, nome="João Esperado")
        cadastros_apoio.criar_fornecedor(
            self.condominio, nome_razao_social="Elevadores Atlas Schindler Ltda."
        )

        conteudo = self._buscar("Jo").content.decode()

        self.assertNotIn("<form", conteudo)
        self.assertNotIn("method=", conteudo)
        self.assertNotIn("csrfmiddlewaretoken", conteudo)
        # Nenhum dos caminhos que mudam estado aparece na lista.
        for caminho in ("/chegadas/", "/acessos/", "/autorizacoes/", "/esperados/"):
            self.assertNotIn(caminho, conteudo)

    def test_o_destino_nao_atravessa_condominio(self):
        """Todo endereço da lista aponta para o condomínio que está em vista."""
        self._morador("502", "Ana Paula")
        apoio.criar_acesso(self.condominio, self.usuario, nome="João da Silva")
        cadastros_apoio.criar_fornecedor(
            self.condominio, nome_razao_social="Ana Cosméticos"
        )

        destinos = self._destinos(self._buscar("Ana"))

        self.assertTrue(destinos)
        for destino in destinos:
            self.assertIn("condominio=%s" % self.condominio.pk, destino)


class PortariaEsperadosTests(TestCase):
    """Quem a portaria ainda aguarda, depois de uma visita já cumprida.

    Uma autorização é uma visita planejada e se cumpre de uma vez: assim que
    gera entrada deixa de ser esperada, e não volta a sê-lo depois da saída.
    """

    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(self.usuario)

    def _esperados(self):
        return AutorizacaoPortaria.objects.do_condominio(self.condominio).esperadas_agora()

    def _chegou(self, autorizacao):
        self.client.post(
            reverse("portaria:chegou", args=[autorizacao.pk]),
            {"condominio": str(self.condominio.pk)},
        )
        return AcessoPortaria.objects.get(autorizacao=autorizacao)

    def _saiu(self, acesso):
        self.client.post(
            reverse("portaria:saiu", args=[acesso.pk]),
            {"condominio": str(self.condominio.pk)},
        )

    def _convite_para_chegar(self, autorizacao):
        """O botão "Chegou" daquela autorização, que a tela oferece ou não."""
        return reverse("portaria:chegou", args=[autorizacao.pk])

    def test_ciclo_completo_da_visita(self):
        autorizacao = apoio.criar_autorizacao(
            self.condominio, self.usuario, nome="Carmem Ciclo"
        )
        painel = reverse("portaria:painel")

        self.assertIn(autorizacao, self._esperados())
        self.assertContains(self.client.get(painel), self._convite_para_chegar(autorizacao))

        acesso = self._chegou(autorizacao)
        self.assertNotIn(autorizacao, self._esperados())
        self.assertTrue(acesso.esta_dentro)

        self._saiu(acesso)
        acesso.refresh_from_db()
        self.assertFalse(acesso.esta_dentro)
        self.assertIsNotNone(acesso.entrada_em)
        self.assertIsNotNone(acesso.saida_em)
        self.assertNotIn(autorizacao, self._esperados())
        self.assertNotContains(self.client.get(painel), self._convite_para_chegar(autorizacao))

    def test_nova_visita_e_nova_autorizacao(self):
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="902")
        primeira = apoio.criar_autorizacao(
            self.condominio, self.usuario, nome="Primeira Visita", unidade=unidade
        )
        self._saiu(self._chegou(primeira))

        segunda = apoio.criar_autorizacao(
            self.condominio, self.usuario, nome="Segunda Visita", unidade=unidade
        )
        esperados = self._esperados()
        self.assertNotIn(primeira, esperados)
        self.assertIn(segunda, esperados)


class PortariaFormulariosTests(TestCase):
    """O mínimo que a portaria precisa perguntar para registrar uma chegada."""

    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(self.usuario)

    def test_tipos_operacionais_sao_oferecidos(self):
        for formulario in (
            ChegadaNaoPrevistaForm(condominio=self.condominio),
            AutorizacaoPortariaForm(condominio=self.condominio),
        ):
            tipos = [valor for valor, _ in formulario.fields["tipo_acesso"].choices]
            self.assertEqual(
                tipos,
                [
                    "visitante",
                    "prestador",
                    "fornecedor",
                    "tecnico",
                    "delivery",
                    "correios",
                ],
            )

        painel = self.client.get(reverse("portaria:painel"))
        self.assertContains(painel, "Visitante")
        self.assertContains(painel, "Prestador")
        self.assertContains(painel, "Fornecedor")
        self.assertContains(painel, "Técnico")
        self.assertContains(painel, "Delivery")
        self.assertContains(painel, "Correios")

    def test_chegada_nao_prevista_pergunta_so_o_necessario(self):
        """Três decisões bastam; o resto entra por contexto.

        Informação disponível não é informação necessária: telefone, documento
        e observação existem no formulário, mas não viram pergunta. Placa e
        local continuam sendo recursos de registro, e chegam por um gatilho que
        diz o que passa a valer — nunca por uma lista de opcionais à vista.
        """
        conteudo = self.client.get(reverse("portaria:painel")).content.decode()
        corpo = conteudo[conteudo.find('id="registro-chegada"') :]

        for visivel in (
            "Registrar chegada",
            "Passo 1 de 3",
            "Quem recebe / para onde vai?",
            "Digite o nome do morador ou apartamento",
            "Quem chegou?",
            "Tipo de acesso",
            "Quem autorizou?",
            "Resultado da autorização",
        ):
            self.assertIn(visivel, corpo)

        # O que não é necessário para esta decisão não aparece por padrão.
        for ausente in ('name="telefone"', 'name="documento"'):
            self.assertNotIn(ausente, corpo)
        self.assertNotIn("<details", corpo)
        self.assertNotIn("Mais informações", corpo)

        # O contexto tem gatilho próprio, e o campo nasce fora de vista: a placa
        # só passa a valer para quem respondeu que chegou de veículo.
        self.assertIn('data-eithra-revelado="placa"', corpo)
        self.assertIn('data-eithra-revelado="local"', corpo)
        self.assertIn("data-eithra-veiculo-sim", corpo)
        self.assertIn("data-eithra-veiculo-nao", corpo)
        self.assertIn('data-eithra-revelar="local"', corpo)

    def test_esperado_reusa_o_fluxo_de_chegada(self):
        """O esperado se registra no mesmo fluxo de três decisões da chegada.

        O modal antigo pedia Tipo, Nome, Unidade e Local num formulário só.
        Agora ele é o mesmo wizard da chegada — para onde vai, quem é esperado,
        quem autorizou — e o que é derivado (situação e janela de validade)
        continua fora da pergunta.
        """
        comunidade_apoio.criar_unidade(self.condominio, identificador="902")
        conteudo = self.client.get(reverse("portaria:painel")).content.decode()
        corpo = conteudo[
            conteudo.find('id="novo-esperado"') : conteudo.find('id="registro-chegada"')
        ]

        for visivel in (
            "Adicionar esperado",
            "Passo 1 de 3",
            "Quem recebe / para onde vai?",
            "Quem é esperado?",
            "Tipo de acesso",
            "Quem autorizou?",
            "Buscar ou digitar empresa",
        ):
            self.assertIn(visivel, corpo)

        # O que o domínio deriva continua sem virar pergunta.
        for ausente in ("Situação", "Previsto para", "Até quando", "Telefone", "Documento"):
            self.assertNotIn(ausente, corpo)

        # A estrutura é a mesma da chegada: mapa de destino, autorizadores do
        # cadastro real e o conteúdo livre pelo mesmo controlador.
        for marcador in (
            'data-eithra-wizard="esperado"',
            "data-eithra-mapa-unidade",
            "data-eithra-autorizadores",
            'data-eithra-revelar-observacao',
        ):
            self.assertIn(marcador, corpo)

        self.assertNotIn("<details", corpo)
        self.assertNotIn("Mais informações", corpo)

    def test_esperado_salva_sem_situacao_nem_validade(self):
        """A capacidade de registro segue inteira sem o que é derivado."""
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="903")
        resposta = self.client.post(
            reverse("portaria:criar_esperado"),
            {
                "condominio": str(self.condominio.pk),
                "tipo_acesso": "visitante",
                "nome": "Visita do Dia",
                "unidade": str(unidade.pk),
            },
        )
        self.assertEqual(resposta.status_code, 302)
        autorizacao = AutorizacaoPortaria.objects.get(nome="Visita do Dia")
        self.assertEqual(autorizacao.status, "autorizado")
        self.assertEqual(autorizacao.unidade_id, unidade.pk)
        self.assertIsNotNone(autorizacao.inicio_previsto)
        self.assertIsNotNone(autorizacao.fim_previsto)

    def test_esperado_nao_vira_acesso_antes_do_chegou(self):
        """Esperar não é entrar: o esperado só vira presença no toque de Chegou.

        Quem registra um esperado cria uma autorização; nenhuma presença nasce
        disso. A entrada continua dependendo do toque que o porteiro dá quando a
        pessoa efetivamente chega.
        """
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="704")
        resposta = self.client.post(
            reverse("portaria:criar_esperado"),
            {
                "condominio": str(self.condominio.pk),
                "tipo_acesso": "visitante",
                "nome": "Esperado sem entrar",
                "unidade": str(unidade.pk),
            },
        )
        self.assertEqual(resposta.status_code, 302)
        autorizacao = AutorizacaoPortaria.objects.get(nome="Esperado sem entrar")
        self.assertFalse(
            AcessoPortaria.objects.filter(autorizacao=autorizacao).exists()
        )
        painel = self.client.get(reverse("portaria:painel"))
        self.assertContains(painel, "Esperado sem entrar")

    def test_chegou_nao_duplica_a_autorizacao(self):
        """A ação Chegou aproveita a autorização existente; não cria outra.

        O esperado é o mesmo objeto operacional em qualquer caminho: a chegada
        de quem já foi avisado abre presença sobre a autorização que já existia,
        sem uma segunda autorização para o mesmo fato.
        """
        autorizacao = apoio.criar_autorizacao(
            self.condominio, self.usuario, nome="João da ClimaTech"
        )
        antes = AutorizacaoPortaria.objects.count()
        acesso = apoio.criar_acesso(self.condominio, self.usuario, nome="Outro")

        self.client.post(
            reverse("portaria:chegou", args=[autorizacao.pk]),
            {"condominio": str(self.condominio.pk)},
        )

        self.assertEqual(AutorizacaoPortaria.objects.count(), antes)
        presenca = AcessoPortaria.objects.get(autorizacao=autorizacao)
        self.assertTrue(presenca.esta_dentro)
        self.assertNotEqual(presenca.pk, acesso.pk)

    def test_esperado_exige_destino_do_condominio(self):
        """O destino do esperado é do condomínio em vista, sempre."""
        outro = apoio.criar_condominio("Outro")
        unidade_de_fora = comunidade_apoio.criar_unidade(outro, identificador="999")
        resposta = self.client.post(
            reverse("portaria:criar_esperado"),
            {
                "condominio": str(self.condominio.pk),
                "tipo_acesso": "visitante",
                "nome": "Destino de fora",
                "unidade": str(unidade_de_fora.pk),
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(
            AutorizacaoPortaria.objects.filter(nome="Destino de fora").exists()
        )

    def test_esperado_recusa_autorizador_de_outro_condominio(self):
        """Quem autoriza é do condomínio; autorizador de fora não vale."""
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="801")
        outro = apoio.criar_condominio("Outro")
        de_fora = comunidade_apoio.criar_pessoa_da_comunidade(outro, "Ana de Fora")
        resposta = self.client.post(
            reverse("portaria:criar_esperado"),
            {
                "condominio": str(self.condominio.pk),
                "tipo_acesso": "visitante",
                "nome": "Esperado com autorizador de fora",
                "unidade": str(unidade.pk),
                "autorizador_pessoa": str(de_fora.pk),
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(
            AutorizacaoPortaria.objects.filter(
                nome="Esperado com autorizador de fora"
            ).exists()
        )

    def test_esperado_guarda_quem_autorizou_da_unidade(self):
        """Quem autorizou sai do cadastro real de quem mora na unidade."""
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="802")
        morador = comunidade_apoio.criar_pessoa_da_comunidade(
            self.condominio, "Ana Moradora"
        )
        comunidade_apoio.criar_vinculo_pessoa_unidade(self.condominio, morador, unidade)

        resposta = self.client.post(
            reverse("portaria:criar_esperado"),
            {
                "condominio": str(self.condominio.pk),
                "tipo_acesso": "visitante",
                "nome": "Visita da Ana",
                "unidade": str(unidade.pk),
                "autorizador_pessoa": str(morador.pk),
            },
        )
        self.assertEqual(resposta.status_code, 302)
        autorizacao = AutorizacaoPortaria.objects.get(nome="Visita da Ana")
        self.assertEqual(autorizacao.autorizador_pessoa_id, morador.pk)
        self.assertEqual(autorizacao.destino_para_exibir, unidade.rotulo)

    def test_esperado_funciona_para_cada_tipo(self):
        """Visitante, prestador, fornecedor e delivery andam pelo mesmo fluxo."""
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="803")
        for tipo in ("visitante", "prestador", "fornecedor", "delivery"):
            resposta = self.client.post(
                reverse("portaria:criar_esperado"),
                {
                    "condominio": str(self.condominio.pk),
                    "tipo_acesso": tipo,
                    "nome": "Esperado %s" % tipo,
                    "unidade": str(unidade.pk),
                    "empresa": "Empresa %s" % tipo,
                },
            )
            self.assertEqual(resposta.status_code, 302)
            autorizacao = AutorizacaoPortaria.objects.get(nome="Esperado %s" % tipo)
            self.assertEqual(autorizacao.tipo_acesso, tipo)
            self.assertEqual(autorizacao.status, "autorizado")
            self.assertFalse(
                AcessoPortaria.objects.filter(autorizacao=autorizacao).exists()
            )

    def test_erro_reabre_o_esperado_no_passo_do_erro(self):
        """O esperado reabre onde o erro está, como o registro de chegada."""
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="804")
        resposta = self.client.post(
            reverse("portaria:criar_esperado"),
            {
                "condominio": str(self.condominio.pk),
                "passo": "3",
                "tipo_acesso": "visitante",
                "unidade": str(unidade.pk),
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertContains(resposta, 'name="passo" value="2"', status_code=400)
        self.assertContains(resposta, "Informe quem está chegando.", status_code=400)

    def test_chegada_nao_prevista_exige_quem_chegou(self):
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="901")
        resposta = self.client.post(
            reverse("portaria:criar_chegada_nao_prevista"),
            {
                "condominio": str(self.condominio.pk),
                "tipo_acesso": "visitante",
                "unidade": str(unidade.pk),
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertContains(resposta, "Informe quem chegou.", status_code=400)

    def test_chegada_nao_prevista_exige_quem_autoriza(self):
        resposta = self.client.post(
            reverse("portaria:criar_chegada_nao_prevista"),
            {
                "condominio": str(self.condominio.pk),
                "tipo_acesso": "visitante",
                "nome": "Sem Destino",
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertContains(
            resposta,
            "Escolha a unidade ou a área comum que recebe esta visita.",
            status_code=400,
        )

    def test_erro_reabre_o_wizard_no_passo_do_erro(self):
        """O modal reabre onde o erro está, não onde a pessoa parou.

        Quem envia do último passo e erra quem chegou precisa ver o erro;
        reabrir mudo em outro passo deixa a correção sem explicação na tela.
        """
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="905")

        # Erro do passo 2 — quem chegou —, enviado a partir do passo 3.
        resposta = self.client.post(
            reverse("portaria:criar_chegada_nao_prevista"),
            {
                "condominio": str(self.condominio.pk),
                "passo": "3",
                "tipo_acesso": "visitante",
                "unidade": str(unidade.pk),
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertContains(resposta, 'name="passo" value="2"', status_code=400)
        self.assertContains(resposta, "Informe quem chegou.", status_code=400)

        # Erro do passo 3 — o meio da autorização — reabre na autorização.
        resposta = self.client.post(
            reverse("portaria:criar_chegada_nao_prevista"),
            {
                "condominio": str(self.condominio.pk),
                "passo": "3",
                "tipo_acesso": "visitante",
                "unidade": str(unidade.pk),
                "nome": "Visitante Sem Meio",
                "status": "autorizado",
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertContains(resposta, 'name="passo" value="3"', status_code=400)
        self.assertContains(
            resposta,
            "Informe por qual meio esse resultado foi registrado.",
            status_code=400,
        )

    def test_chegada_nao_prevista_nao_cria_pessoa(self):
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="902")
        antes = Pessoa.objects.count()
        resposta = self.client.post(
            reverse("portaria:criar_chegada_nao_prevista"),
            {
                "condominio": str(self.condominio.pk),
                "tipo_acesso": "visitante",
                "nome": "Visitante Eventual",
                "placa": "QQQ1A11",
                "unidade": str(unidade.pk),
            },
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(Pessoa.objects.count(), antes)
        autorizacao = AutorizacaoPortaria.objects.get(nome="Visitante Eventual")
        self.assertIsNone(autorizacao.pessoa_id)
        self.assertEqual(autorizacao.placa, "QQQ1A11")
        self.assertEqual(autorizacao.status, "aguardando_confirmacao")
        self.assertFalse(AcessoPortaria.objects.filter(autorizacao=autorizacao).exists())

    def test_prestador_registra_sem_pessoa_permanente(self):
        local = apoio.criar_local(self.condominio, nome="Casa de bombas")
        antes = Pessoa.objects.count()
        resposta = self.client.post(
            reverse("portaria:criar_chegada_nao_prevista"),
            {
                "condominio": str(self.condominio.pk),
                "tipo_acesso": "prestador",
                "nome": "Técnico da Bomba",
                "empresa": "Bombas Eithra",
                "local": str(local.pk),
            },
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(Pessoa.objects.count(), antes)
        autorizacao = AutorizacaoPortaria.objects.get(nome="Técnico da Bomba")
        self.assertEqual(autorizacao.tipo_acesso, "prestador")
        self.assertEqual(autorizacao.empresa, "Bombas Eithra")
        self.assertEqual(autorizacao.local_id, local.pk)
        self.assertIsNone(autorizacao.pessoa_id)

    def test_fornecedor_e_tecnico_registram_sem_pessoa_permanente(self):
        """Os outros dois tipos de serviço andam pelo mesmo caminho.

        Fornecedor e Técnico estão em uso — o segundo vive atrás de "Mais
        tipos" — e ambos carregam empresa como o prestador, sem virar cadastro
        de pessoa. Sem esta prova, só o prestador teria ido ponta a ponta.
        """
        local = apoio.criar_local(self.condominio, nome="Casa de bombas")
        for tipo in ("fornecedor", "tecnico"):
            resposta = self.client.post(
                reverse("portaria:criar_chegada_nao_prevista"),
                {
                    "condominio": str(self.condominio.pk),
                    "tipo_acesso": tipo,
                    "nome": "Serviço %s" % tipo,
                    "empresa": "Empresa %s" % tipo,
                    "local": str(local.pk),
                },
            )
            self.assertEqual(resposta.status_code, 302)
            autorizacao = AutorizacaoPortaria.objects.get(nome="Serviço %s" % tipo)
            self.assertEqual(autorizacao.tipo_acesso, tipo)
            self.assertEqual(autorizacao.empresa, "Empresa %s" % tipo)
            self.assertIsNone(autorizacao.pessoa_id)

    def test_morador_nao_e_oferecido_como_terceiro(self):
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="301")
        morador = comunidade_apoio.criar_pessoa_da_comunidade(self.condominio, "Ana Moradora")
        comunidade_apoio.criar_vinculo_pessoa_unidade(self.condominio, morador, unidade)
        visitante = comunidade_apoio.criar_pessoa_da_comunidade(
            self.condominio, "Vera Visitante"
        )

        # A chegada sem autorização não oferece o cadastro da comunidade: quem
        # chegou é o terceiro, e quem mora autoriza a entrada.
        chegada = ChegadaNaoPrevistaForm(condominio=self.condominio)
        self.assertNotIn("pessoa", chegada.fields)

        pessoas = list(
            AutorizacaoPortariaForm(condominio=self.condominio)
            .fields["pessoa"]
            .queryset
        )
        self.assertNotIn(morador, pessoas)
        self.assertIn(visitante, pessoas)


class PortariaAutorizadorTests(TestCase):
    """A busca única de quem pode autorizar uma entrada.

    A portaria não escolhe entre procurar pessoa ou apartamento: digita o que
    ouviu, e o Eithra devolve os dois lados do mesmo par — quem responde e por
    qual unidade.
    """

    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(self.usuario)
        self.url = reverse("portaria:buscar_autorizador")

    def _buscar(self, termo, condominio=None):
        return self.client.get(
            self.url,
            {"q": termo, "condominio": str((condominio or self.condominio).pk)},
        )

    def test_por_apartamento_mostra_quem_responde(self):
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="301")
        morador = comunidade_apoio.criar_pessoa_da_comunidade(
            self.condominio, "Ana Moradora"
        )
        comunidade_apoio.criar_vinculo_pessoa_unidade(self.condominio, morador, unidade)

        resposta = self._buscar("301")

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "301")
        self.assertContains(resposta, "Ana Moradora")
        self.assertContains(resposta, "Usar Ana Moradora")

    def test_por_nome_mostra_o_apartamento(self):
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="304")
        morador = comunidade_apoio.criar_pessoa_da_comunidade(
            self.condominio, "Maria Fernanda"
        )
        comunidade_apoio.criar_vinculo_pessoa_unidade(self.condominio, morador, unidade)

        resposta = self._buscar("Maria")

        self.assertContains(resposta, "Maria Fernanda")
        self.assertContains(resposta, "304")

    def test_por_parte_do_nome(self):
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="305")
        morador = comunidade_apoio.criar_pessoa_da_comunidade(
            self.condominio, "João Carlos Pereira"
        )
        comunidade_apoio.criar_vinculo_pessoa_unidade(self.condominio, morador, unidade)

        resposta = self._buscar("Pereira")

        self.assertContains(resposta, "João Carlos Pereira")

    def test_pessoa_com_duas_unidades_oferece_as_duas(self):
        primeira = comunidade_apoio.criar_unidade(self.condominio, identificador="401")
        segunda = comunidade_apoio.criar_unidade(self.condominio, identificador="402")
        morador = comunidade_apoio.criar_pessoa_da_comunidade(
            self.condominio, "Clara Dupla"
        )
        comunidade_apoio.criar_vinculo_pessoa_unidade(self.condominio, morador, primeira)
        comunidade_apoio.criar_vinculo_pessoa_unidade(self.condominio, morador, segunda)

        resposta = self._buscar("Clara")

        self.assertContains(resposta, "401")
        self.assertContains(resposta, "402")

    def test_vinculo_encerrado_nao_autoriza(self):
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="306")
        morador = comunidade_apoio.criar_pessoa_da_comunidade(
            self.condominio, "Vera Antiga"
        )
        comunidade_apoio.criar_vinculo_pessoa_unidade(
            self.condominio, morador, unidade, data_fim=comunidade_apoio.ontem()
        )

        resposta = self._buscar("Vera Antiga")

        self.assertNotContains(resposta, "Vera Antiga")
        self.assertContains(resposta, "Não encontrei esse morador ou apartamento.")

    def test_sem_moradores_vinculados_avisa(self):
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="307")

        resposta = self._buscar("307")

        self.assertContains(resposta, "307")
        self.assertContains(resposta, "Sem moradores vinculados hoje.")

    def test_sem_resultado_nao_inventa_cadastro(self):
        resposta = self._buscar("Zzz Inexistente")
        self.assertContains(resposta, "Não encontrei esse morador ou apartamento.")
        self.assertNotContains(resposta, "Usar ")

    def test_sem_termo_apenas_orienta(self):
        resposta = self._buscar("")
        self.assertContains(resposta, "Digite o nome do morador ou apartamento.")
        self.assertNotContains(resposta, "Não encontrei")

    def test_nao_expoe_dado_tecnico_do_morador(self):
        """Quem responde aparece pelo nome, pelo papel e pela unidade.

        O papel na unidade é o contexto que a portaria usa para reconhecer
        quem autoriza. Documento e telefone não são contexto de portaria, e
        continuam fora da tela.
        """
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="308")
        morador = comunidade_apoio.criar_pessoa_da_comunidade(
            self.condominio, "Pessoa Reservada", documento="12345678901"
        )
        comunidade_apoio.criar_vinculo_pessoa_unidade(self.condominio, morador, unidade)

        resposta = self._buscar("Reservada")

        self.assertContains(resposta, "Morador")
        self.assertNotContains(resposta, "12345678901")

    def test_nao_vaza_outro_condominio(self):
        outro = apoio.criar_condominio("Outro")
        unidade = comunidade_apoio.criar_unidade(outro, identificador="309")
        morador = comunidade_apoio.criar_pessoa_da_comunidade(outro, "Morador de Fora")
        comunidade_apoio.criar_vinculo_pessoa_unidade(outro, morador, unidade)

        resposta = self._buscar("Morador de Fora")

        self.assertNotContains(resposta, "Morador de Fora")
        self.assertContains(resposta, "Não encontrei esse morador ou apartamento.")

    def test_quem_nao_opera_nao_busca(self):
        morador = apoio.criar_pessoa("morador@teste.com", "Morador")
        apoio.criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)

        resposta = self._buscar("301")

        self.assertEqual(resposta.status_code, 404)


class PortariaDestinoPorVozTests(TestCase):
    """O destino falado: a fala vira número, o cadastro confirma.

    O áudio já transcreve fiel; o que faltava era ler o número ditado como o
    condomínio o escreve. "novecentos e dois" e "nove zero dois" são o 902 —
    desde que o 902 exista. Número que não existe não vira destino: no máximo
    vira uma pergunta, e só quando há um vizinho seguro no cadastro.
    """

    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(self.usuario)
        self.url = reverse("portaria:buscar_autorizador")

    def _buscar(self, termo):
        return self.client.get(
            self.url, {"q": termo, "condominio": str(self.condominio.pk)}
        )

    def _unidade_com_morador(self, identificador):
        unidade = comunidade_apoio.criar_unidade(
            self.condominio, identificador=identificador
        )
        morador = comunidade_apoio.criar_pessoa_da_comunidade(
            self.condominio, "Carlos Mendes"
        )
        comunidade_apoio.criar_vinculo_pessoa_unidade(
            self.condominio, morador, unidade
        )
        return unidade

    def test_por_extenso_encontra_o_apartamento(self):
        unidade = self._unidade_com_morador("902")

        resposta = self._buscar("novecentos e dois")

        self.assertContains(resposta, f'data-destino="{unidade.rotulo}"')
        self.assertContains(resposta, "Carlos Mendes")

    def test_o_numero_em_digitos_como_sempre_foi(self):
        unidade = self._unidade_com_morador("902")

        resposta = self._buscar("902")

        self.assertContains(resposta, f'data-destino="{unidade.rotulo}"')

    def test_com_a_palavra_apartamento_na_frente(self):
        unidade = self._unidade_com_morador("902")

        resposta = self._buscar("apartamento 902")

        self.assertContains(resposta, f'data-destino="{unidade.rotulo}"')

    def test_com_a_abreviacao_apto_na_frente(self):
        unidade = self._unidade_com_morador("902")

        resposta = self._buscar("apto 902")

        self.assertContains(resposta, f'data-destino="{unidade.rotulo}"')

    def test_digito_a_digito(self):
        unidade = self._unidade_com_morador("902")

        resposta = self._buscar("nove zero dois")

        self.assertContains(resposta, f'data-destino="{unidade.rotulo}"')

    def test_numero_inexistente_sugere_o_vizinho_sem_selecionar(self):
        self._unidade_com_morador("902")

        resposta = self._buscar("906")

        self.assertNotContains(resposta, "data-unidade=")
        self.assertContains(
            resposta, "Não encontrei o apartamento 906. Você quis dizer 902?"
        )

    def test_numero_inexistente_sem_vizinho_seguro_nao_sugere(self):
        self._unidade_com_morador("902")

        resposta = self._buscar("999")

        self.assertNotContains(resposta, "data-unidade=")
        self.assertContains(resposta, "Não encontrei esse morador ou apartamento.")
        self.assertNotContains(resposta, "Você quis dizer")

    def test_a_busca_por_nome_continua_intacta(self):
        self._unidade_com_morador("902")

        resposta = self._buscar("Carlos Mendes")

        self.assertContains(resposta, "Carlos Mendes")


class PortariaEmpresaTests(TestCase):
    """O campo Empresa do prestador: a casa sugere, quem digita decide.

    A memória da portaria é oferecida como sugestão; o texto escrito continua
    valendo por si, sem cadastro prévio e sem bloquear a chegada.
    """

    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(self.usuario)

    def test_os_dois_campos_de_empresa_convidam_a_buscar_ou_digitar(self):
        """Empresa aparece no esperado e na chegada — com a mesma experiência."""
        conteudo = self.client.get(reverse("portaria:painel")).content.decode()

        self.assertEqual(conteudo.count("Buscar ou digitar empresa"), 2)
        for formulario in (
            AutorizacaoPortariaForm(condominio=self.condominio),
            ChegadaNaoPrevistaForm(condominio=self.condominio),
        ):
            campo = formulario.fields["empresa"].widget
            self.assertEqual(campo.attrs["placeholder"], "Buscar ou digitar empresa")
            self.assertEqual(
                campo.attrs["data-eithra-empresa"], "empresas-conhecidas"
            )

    def test_a_empresa_que_ja_passou_volta_como_sugestao(self):
        apoio.criar_acesso(self.condominio, self.usuario, empresa="Bombas Eithra")

        conteudo = self.client.get(reverse("portaria:painel")).content.decode()

        self.assertIn('id="empresas-conhecidas"', conteudo)
        self.assertIn('["Bombas Eithra"]', conteudo)

    def test_empresa_desconhecida_nao_bloqueia_a_chegada(self):
        """A sugestão é da tela; o que a portaria registra é o que foi digitado."""
        apoio.criar_acesso(self.condominio, self.usuario, empresa="Bombas Eithra")
        local = apoio.criar_local(self.condominio, nome="Casa de bombas")

        resposta = self.client.post(
            reverse("portaria:criar_chegada_nao_prevista"),
            {
                "condominio": str(self.condominio.pk),
                "tipo_acesso": "prestador",
                "nome": "Técnica Nova",
                "empresa": "Vidraçaria Nova",
                "local": str(local.pk),
            },
        )

        self.assertEqual(resposta.status_code, 302)
        autorizacao = AutorizacaoPortaria.objects.get(nome="Técnica Nova")
        self.assertEqual(autorizacao.empresa, "Vidraçaria Nova")


class PortariaEntregasTests(TestCase):
    """Delivery e Correios: quem chega é uma entrega, não uma visita.

    As duas naturezas compartilham o mesmo fluxo da portaria, mas não o
    vocabulário: os Correios trazem um objeto que pode ser classificado, e o
    Delivery traz uma instrução de como proceder no destino. Cada campo só
    existe dentro do próprio tipo — o que não faz sentido no tipo escolhido não
    entra no registro — e o que foi decidido na chegada acompanha a presença.
    """

    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(self.usuario)
        self.unidade = comunidade_apoio.criar_unidade(
            self.condominio, identificador="701"
        )

    def _chegada(self, **campos):
        dados = {
            "condominio": str(self.condominio.pk),
            "unidade": str(self.unidade.pk),
            "nome": "Entrega Exemplo",
            "status": "autorizado",
            "meio_autorizacao": "interfone",
        }
        dados.update(campos)
        return self.client.post(
            reverse("portaria:criar_chegada_nao_prevista"), dados
        )

    def test_delivery_guarda_a_instrucao_de_entrega(self):
        resposta = self._chegada(
            tipo_acesso="delivery",
            nome="Motoboy do Restaurante",
            instrucao_entrega="morador_desce",
        )

        self.assertEqual(resposta.status_code, 302)
        autorizacao = AutorizacaoPortaria.objects.get(nome="Motoboy do Restaurante")
        self.assertEqual(autorizacao.tipo_acesso, "delivery")
        self.assertEqual(autorizacao.instrucao_entrega, "morador_desce")
        self.assertEqual(autorizacao.natureza_objeto, "")

    def test_correios_guardam_a_natureza_do_objeto(self):
        resposta = self._chegada(
            tipo_acesso="correios",
            nome="Carteiro",
            natureza_objeto="encomenda",
        )

        self.assertEqual(resposta.status_code, 302)
        autorizacao = AutorizacaoPortaria.objects.get(nome="Carteiro")
        self.assertEqual(autorizacao.tipo_acesso, "correios")
        self.assertEqual(autorizacao.natureza_objeto, "encomenda")
        self.assertEqual(autorizacao.instrucao_entrega, "")

    def test_a_instrucao_acompanha_a_presenca_aberta(self):
        self._chegada(
            tipo_acesso="delivery",
            nome="Motoboy do Restaurante",
            instrucao_entrega="autorizado_subir",
        )

        acesso = AcessoPortaria.objects.get(nome="Motoboy do Restaurante")
        self.assertEqual(acesso.tipo_acesso, "delivery")
        self.assertEqual(acesso.instrucao_entrega, "autorizado_subir")

    def test_a_natureza_acompanha_a_presenca_aberta(self):
        self._chegada(
            tipo_acesso="correios",
            nome="Carteiro",
            natureza_objeto="carta",
        )

        acesso = AcessoPortaria.objects.get(nome="Carteiro")
        self.assertEqual(acesso.natureza_objeto, "carta")

    def test_cada_campo_vive_dentro_do_proprio_tipo(self):
        """Natureza é dos Correios; instrução é do Delivery.

        Um payload que envia os dois campos para o mesmo tipo não guarda o que
        não pertence àquele tipo: melhor vazio do que guardado errado.
        """
        self._chegada(
            tipo_acesso="delivery",
            nome="Motoboy",
            natureza_objeto="encomenda",
            instrucao_entrega="autorizado_subir",
        )

        autorizacao = AutorizacaoPortaria.objects.get(nome="Motoboy")
        self.assertEqual(autorizacao.natureza_objeto, "")
        self.assertEqual(autorizacao.instrucao_entrega, "autorizado_subir")

    def test_entrega_pode_ir_para_area_comum(self):
        local = apoio.criar_local(self.condominio, nome="Portaria / recepção")

        resposta = self.client.post(
            reverse("portaria:criar_chegada_nao_prevista"),
            {
                "condominio": str(self.condominio.pk),
                "tipo_acesso": "correios",
                "nome": "Carteiro",
                "natureza_objeto": "registrado",
                "status": "autorizado",
                "meio_autorizacao": "presencial",
                "local": str(local.pk),
            },
        )

        self.assertEqual(resposta.status_code, 302)
        autorizacao = AutorizacaoPortaria.objects.get(nome="Carteiro")
        self.assertIsNone(autorizacao.unidade_id)
        self.assertEqual(autorizacao.local_id, local.pk)

    def test_o_painel_oferece_delivery_e_correios(self):
        """A tela pede o destino da entrega no mesmo fluxo da visita."""
        conteudo = self.client.get(reverse("portaria:painel")).content.decode()

        self.assertIn("Delivery", conteudo)
        self.assertIn("Correios", conteudo)
        self.assertIn("data-eithra-contexto-entrega", conteudo)
        self.assertIn('data-eithra-entrega="correios"', conteudo)
        self.assertIn('data-eithra-entrega="delivery"', conteudo)


class PortariaMapaDeUnidadesTests(TestCase):
    """Escolher o destino por clique, a partir do cadastro que já existe.

    O mapa preenche a mesma unidade que a busca preenche: bloco/torre, andar e
    unidade, montados do cadastro do condomínio — sem inventar estrutura, sem
    mostrar unidade inativa e sem atravessar condomínio.
    """

    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(self.usuario)

    def _painel(self):
        return self.client.get(reverse("portaria:painel"))

    def test_a_unidade_aparece_para_escolher_por_clique(self):
        comunidade_apoio.criar_unidade(
            self.condominio, identificador="101", bloco_torre="A", andar="1"
        )

        resposta = self._painel()

        self.assertContains(resposta, "data-eithra-mapa-unidade")
        self.assertContains(resposta, "data-eithra-unidade-chip")
        self.assertContains(resposta, ">101<")

    def test_o_mapa_agrupa_por_bloco_e_andar(self):
        comunidade_apoio.criar_unidade(
            self.condominio, identificador="101", bloco_torre="A", andar="1"
        )
        comunidade_apoio.criar_unidade(
            self.condominio, identificador="201", bloco_torre="B", andar="2"
        )

        estrutura = self._painel().context["estrutura_unidades"]

        self.assertEqual([bloco["nome"] for bloco in estrutura], ["A", "B"])
        self.assertEqual(estrutura[0]["andares"][0]["nome"], "1")
        self.assertEqual(
            estrutura[1]["andares"][0]["unidades"][0]["identificador"], "201"
        )

    def test_o_predio_se_le_de_cima_para_baixo(self):
        """O prédio aparece de pé: o 10º andar acima do 2º, como a planta o tem."""
        comunidade_apoio.criar_unidade(
            self.condominio, identificador="102", bloco_torre="A", andar="10"
        )
        comunidade_apoio.criar_unidade(
            self.condominio, identificador="101", bloco_torre="A", andar="2"
        )

        estrutura = self._painel().context["estrutura_unidades"]

        self.assertEqual(
            [andar["nome"] for andar in estrutura[0]["andares"]], ["10", "2"]
        )
        self.assertEqual(
            [andar["rotulo"] for andar in estrutura[0]["andares"]], ["10º", "2º"]
        )

    def test_o_andar_ordena_como_numero(self):
        """Dez andares de um bloco não se leem como texto: 2º, 9º, 10º."""
        for andar in ("10", "9", "2"):
            comunidade_apoio.criar_unidade(
                self.condominio, identificador=f"1{andar}", bloco_torre="A", andar=andar
            )

        estrutura = self._painel().context["estrutura_unidades"]

        self.assertEqual(
            [andar["nome"] for andar in estrutura[0]["andares"]], ["10", "9", "2"]
        )

    def test_o_andar_sem_numero_fica_por_ultimo_e_sem_rotulo(self):
        """O cadastro incompleto não vira andar inventado nem rótulo falso."""
        comunidade_apoio.criar_unidade(
            self.condominio, identificador="101", bloco_torre="A", andar="2"
        )
        comunidade_apoio.criar_unidade(
            self.condominio, identificador="900", bloco_torre="A"
        )

        estrutura = self._painel().context["estrutura_unidades"]

        self.assertEqual(
            [andar["rotulo"] for andar in estrutura[0]["andares"]], ["2º", ""]
        )

    def test_o_mapa_nao_traz_outro_condominio(self):
        outro = apoio.criar_condominio("Outro")
        comunidade_apoio.criar_unidade(outro, identificador="999", bloco_torre="Z")

        resposta = self._painel()

        self.assertEqual(resposta.context["estrutura_unidades"], [])
        self.assertContains(resposta, "Nenhuma unidade cadastrada neste condomínio.")

    def test_unidade_inativa_nao_aparece(self):
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="101")
        unidade.ativa = False
        unidade.save()

        resposta = self._painel()

        self.assertEqual(resposta.context["estrutura_unidades"], [])
        self.assertContains(resposta, "Nenhuma unidade cadastrada neste condomínio.")

    def test_sem_unidades_o_mapa_avisa(self):
        resposta = self._painel()

        self.assertEqual(resposta.context["estrutura_unidades"], [])
        self.assertContains(resposta, "Nenhuma unidade cadastrada neste condomínio.")

    def test_a_unidade_oferece_os_moradores_como_autorizadores(self):
        """O mapa escolhe a unidade; quem autoriza sai dos moradores dela.

        A lista de "Autorizado por" não é um cadastro paralelo: é o vínculo
        vigente da própria unidade, do cadastro que já existe.
        """
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="101")
        ana = comunidade_apoio.criar_pessoa_da_comunidade(self.condominio, "Ana Paula")
        bruno = comunidade_apoio.criar_pessoa_da_comunidade(self.condominio, "Bruno Dias")
        comunidade_apoio.criar_vinculo_pessoa_unidade(self.condominio, ana, unidade)
        comunidade_apoio.criar_vinculo_pessoa_unidade(self.condominio, bruno, unidade)

        resposta = self._painel()
        moradores = resposta.context["moradores_por_unidade"]

        self.assertEqual(
            [morador["nome"] for morador in moradores[str(unidade.pk)]],
            ["Ana Paula", "Bruno Dias"],
        )
        self.assertContains(resposta, 'id="moradores-por-unidade"')

    def test_unidade_sem_morador_nao_oferece_autorizador(self):
        """Sem vínculo vigente, a unidade não inventa quem autorizou."""
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="102")

        moradores = self._painel().context["moradores_por_unidade"]

        self.assertNotIn(str(unidade.pk), moradores)


class PortariaFidelidadeDasTelasTests(TestCase):
    """A Portaria com as telas que ela promete: decidir, tocar, confirmar.

    O que se prende aqui é a estrutura que a tela oferece, não o desenho dela: a
    chegada em três decisões, o destino que acompanha os passos seguintes, a
    placa que só existe para quem chegou de veículo, a foto opcional e o resumo
    que precede o último toque. É o que o porteiro vê e usa.
    """

    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(self.usuario)

    def _painel(self):
        return self.client.get(reverse("portaria:painel")).content.decode()

    def test_a_tela_principal_diz_o_que_a_portaria_faz(self):
        pagina = self._painel()

        for visivel in (
            "Portaria",
            "Controle de chegadas e saídas.",
            "Buscar nome, apartamento, morador, placa ou empresa...",
            "Registrar chegada",
            "Conte o que está acontecendo... Ex.: o elevador parou, há água na garagem ou chegou um visitante para o 902.",
            "Chegando",
            "Aqui agora",
            "Hoje",
        ):
            self.assertIn(visivel, pagina)

    def test_o_registro_de_chegada_se_abre_em_tres_decisoes(self):
        pagina = self._painel()

        for painel in ('"1"', '"2"', '"3"'):
            self.assertIn(f"data-eithra-step-panel={painel}", pagina)

        for visivel in (
            "Passo 1 de 3",
            "Quem recebe / para onde vai?",
            "Digite o nome do morador ou apartamento",
            "Quem chegou?",
            "Tipo de acesso",
            "Chegou de veículo?",
            "Tirar foto",
            "Quem autorizou?",
            "Autorizado por",
            "Resultado da autorização",
        ):
            self.assertIn(visivel, pagina)

    def test_o_destino_acompanha_os_passos_seguintes(self):
        """Quem já escolheu para onde vai não precisa lembrar de cabeça."""
        unidade = comunidade_apoio.criar_unidade(
            self.condominio, identificador="902", andar="9"
        )

        pagina = self._painel()

        self.assertIn("data-eithra-contexto-destino", pagina)
        self.assertIn("data-eithra-contexto-destino-rotulo", pagina)
        self.assertIn('data-eithra-ir-para="1"', pagina)
        self.assertIn(">Trocar<", pagina)
        self.assertIn(f'data-unidade="{unidade.pk}"', pagina)
        self.assertIn('data-rotulo="902"', pagina)

        # O destino escolhido acompanha os dois passos seguintes: avançar não
        # apaga para onde a pessoa vai.
        passo2 = pagina[
            pagina.find('data-eithra-step-panel="2"') : pagina.find(
                'data-eithra-step-panel="3"'
            )
        ]
        self.assertIn("data-eithra-contexto-destino", passo2)
        passo3 = pagina[pagina.find('data-eithra-step-panel="3"') :]
        self.assertIn("data-eithra-contexto-destino", passo3)

    def test_o_voltar_nao_reconstroi_o_que_ja_foi_escolhido(self):
        """Voltar é reaparecer, não recomeçar: a escolha fica onde foi feita."""
        pagina = self._painel()
        corpo = pagina[pagina.find('id="registro-chegada"') :]

        self.assertIn("data-eithra-step-voltar", corpo)
        # O passo 1 é alternado por visibilidade, e os chips continuam nele:
        # voltar encontra o que foi marcado, sem redesenhar a planta.
        self.assertIn('data-eithra-step-panel="1"', corpo)
        self.assertIn("data-eithra-unidade-chip", corpo)

    def test_a_proxima_chegada_comeca_sem_o_destino_anterior(self):
        """Registrada uma chegada, a tela reabre limpa: nada do registro anterior."""
        unidade = comunidade_apoio.criar_unidade(
            self.condominio, identificador="902", andar="9"
        )

        resposta = self.client.post(
            reverse("portaria:criar_chegada_nao_prevista"),
            {
                "condominio": str(self.condominio.pk),
                "tipo_acesso": "visitante",
                "nome": "Visitante Um",
                "unidade": str(unidade.pk),
            },
        )
        self.assertEqual(resposta.status_code, 302)

        pagina = self.client.get(reverse("portaria:painel")).content.decode()

        # Nenhuma unidade marcada e nenhum contexto aberto: cada chegada é
        # decidida de novo, e nunca herda para onde a anterior foi. O tipo de
        # acesso nasce no primeiro da lista, como a própria tela define.
        self.assertNotIn("eithra-mapa-chip is-selected", pagina)
        self.assertNotIn('class="eithra-autorizador-escolhido" data-eithra-autorizador-escolhido>', pagina)
        chegada = pagina[pagina.find('id="registro-chegada"') :]
        self.assertEqual(chegada.count("data-eithra-contexto-destino hidden"), 2)

    def test_a_placa_so_vale_depois_do_sim_ao_veiculo(self):
        """A placa é do carro: sem carro, ela não é perguntada."""
        pagina = self._painel()
        corpo = pagina[pagina.find('id="registro-chegada"') :]

        self.assertIn("Chegou de veículo?", corpo)
        self.assertIn("data-eithra-veiculo-sim", corpo)
        self.assertIn("data-eithra-veiculo-nao", corpo)
        self.assertIn('class="d-none mt-3" data-eithra-revelado="placa"', corpo)

    def test_a_foto_e_opcional_e_tem_captura_no_celular(self):
        """A câmera é capacidade de verdade — e a foto nunca é exigida."""
        pagina = self._painel()
        corpo = pagina[pagina.find('id="registro-chegada"') :]

        self.assertIn('name="foto"', corpo)
        self.assertIn('accept="image/*"', corpo)
        self.assertIn('capture="environment"', corpo)
        self.assertIn("data-eithra-foto-entrada", corpo)
        self.assertIn("data-eithra-foto-tirar", corpo)
        self.assertIn("data-eithra-foto-remover", corpo)
        self.assertIn("data-eithra-foto-previa", corpo)
        self.assertIn("Tirar foto", corpo)

    def test_o_interfone_e_o_meio_e_nao_se_pergunta_de_novo(self):
        """O meio já está definido: a decisão do passo 3 é quem autorizou."""
        pagina = self._painel()
        corpo = pagina[pagina.find('id="registro-chegada"') :]

        self.assertIn("Meio:", corpo)
        self.assertIn("Interfone", corpo)
        self.assertIn('name="meio_autorizacao"', corpo)

    def test_o_resumo_precede_a_acao_final(self):
        """Antes do último toque, o que vai ser gravado cabe em duas linhas."""
        pagina = self._painel()
        corpo = pagina[pagina.find('id="registro-chegada"') :]

        self.assertIn("data-eithra-resumo-chegada", corpo)
        self.assertIn("data-eithra-resumo-tipo", corpo)
        self.assertIn("data-eithra-resumo-nome", corpo)
        self.assertIn("data-eithra-resumo-destino", corpo)

    def test_a_acao_final_e_registrar_chegada(self):
        pagina = self._painel()
        rodape = pagina[pagina.find('id="registro-chegada"') :]

        self.assertIn(">Cancelar<", rodape)
        self.assertIn(">Voltar<", rodape)
        self.assertIn("Registrar chegada", rodape)
        self.assertNotIn("Registrar entrada<", rodape)

    def test_nenhum_comentario_tecnico_aparece_no_html(self):
        """Comentário de template é recado para quem escreve, não para a tela.

        O ``{# … #}`` de várias linhas não é comentário para o Django: ele
        precisa caber numa linha só para sumir, e o que não couber vira texto na
        cara de quem opera a portaria. Aqui a página inteira é conferida.
        """
        for caminho in (
            reverse("portaria:painel"),
            reverse("portaria:painel") + "?aba=historico",
        ):
            pagina = self.client.get(caminho).content.decode()

            self.assertNotIn("{#", pagina)
            self.assertNotIn("{%", pagina)


class PortariaRelatoTests(TestCase):
    """A primeira conversa por texto: entender, perguntar o mínimo, confirmar.

    Dois envios, nenhum estado entre eles. O primeiro só lê o relato; o segundo
    reinterpreta o mesmo texto e, se a leitura ainda sustentar a ação, chama as
    operações que a Portaria já tinha.
    """

    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(self.usuario)
        self.unidade = comunidade_apoio.criar_unidade(
            self.condominio, identificador="301"
        )

    # --- utilidades -------------------------------------------------------

    def _entender(self, relato, **extra):
        dados = {"condominio": str(self.condominio.pk), "relato": relato}
        dados.update(extra)
        return self.client.post(reverse("portaria:interpretar"), dados)

    def _confirmar(self, relato, acao, **extra):
        dados = {
            "condominio": str(self.condominio.pk),
            "relato": relato,
            "acao": acao,
        }
        dados.update(extra)
        return self.client.post(reverse("portaria:confirmar"), dados)

    def _valor_do_campo(self, resposta):
        achado = re.search(
            r'id="relato-portaria".*?value="([^"]*)"',
            resposta.content.decode(),
            re.S,
        )
        return achado.group(1) if achado else None

    # --- leitura sem consequência ----------------------------------------

    def test_entender_nao_grava_nada(self):
        resposta = self._entender("Chegou Carlos, visita do 301")

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(AutorizacaoPortaria.objects.count(), 0)
        self.assertEqual(AcessoPortaria.objects.count(), 0)

    def test_quem_nao_opera_nao_interpreta(self):
        morador = apoio.criar_pessoa("morador@teste.com", "Morador")
        apoio.criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)

        resposta = self._entender("Chegou Carlos, visita do 301")

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(AutorizacaoPortaria.objects.count(), 0)

    # --- leituras que já sustentam uma ação ------------------------------

    def test_chegada_clara_vira_proposta(self):
        resposta = self._entender("Chegou Carlos, visita do 301")

        self.assertContains(resposta, 'name="acao" value="chegada"')
        self.assertContains(
            resposta, 'eithra-portaria-leitura-titulo">Carlos</div>'
        )
        self.assertContains(resposta, "data-eithra-corrigir")

    def test_saida_clara_vira_proposta(self):
        apoio.criar_acesso(
            self.condominio, self.usuario, nome="Joana Prado", unidade=self.unidade
        )

        resposta = self._entender("Joana saiu")

        self.assertContains(resposta, 'name="acao" value="saida"')
        self.assertContains(
            resposta, 'eithra-portaria-leitura-titulo">Joana</div>'
        )

    # --- pergunta mínima -------------------------------------------------

    def test_o_que_faltou_vira_pergunta(self):
        resposta = self._entender("Chegou uma visita")

        self.assertContains(
            resposta, 'eithra-portaria-leitura-pergunta">Quem chegou?</p>'
        )
        self.assertEqual(self._valor_do_campo(resposta), "")
        self.assertContains(resposta, 'data-eithra-placeholder="Quem chegou?"')
        self.assertContains(
            resposta, 'value="Chegou uma visita" data-eithra-respondendo'
        )
        self.assertEqual(AutorizacaoPortaria.objects.count(), 0)

    def test_a_resposta_curta_completa_o_relato(self):
        primeira = self._entender("Chegou Carlos")
        self.assertContains(
            primeira, 'eithra-portaria-leitura-pergunta">Para qual unidade?</p>'
        )

        segunda = self._entender("301", respondendo="Chegou Carlos")

        self.assertContains(segunda, 'name="acao" value="chegada"')
        self.assertEqual(AutorizacaoPortaria.objects.count(), 0)

    # --- confirmação -----------------------------------------------------

    def test_confirmar_chegada_registra_a_entrada(self):
        resposta = self._confirmar("Chegou Carlos, visita do 301", "chegada")

        self.assertEqual(resposta.status_code, 302)
        acesso = AcessoPortaria.objects.get(nome="Carlos")
        self.assertIsNotNone(acesso.entrada_em)
        self.assertEqual(acesso.unidade_id, self.unidade.pk)

    def test_tipo_corrigido_troca_o_tipo_lido(self):
        """Um toque em "Visita" grava visita — mesmo tendo lido entrega."""
        resposta = self._confirmar(
            "Chegou uma encomenda do iFood pro 301",
            "chegada",
            tipo_corrigido=TipoAcessoPortaria.VISITANTE.value,
        )

        self.assertEqual(resposta.status_code, 302)
        acesso = AcessoPortaria.objects.get(nome="iFood")
        self.assertEqual(acesso.tipo_acesso, TipoAcessoPortaria.VISITANTE)

    def test_tipo_corrigido_invalido_e_ignorado(self):
        """Um valor fora do domínio não muda o tipo que a leitura encontrou."""
        resposta = self._confirmar(
            "Chegou uma encomenda do iFood pro 301",
            "chegada",
            tipo_corrigido="nao-existe",
        )

        self.assertEqual(resposta.status_code, 302)
        acesso = AcessoPortaria.objects.get(nome="iFood")
        self.assertEqual(acesso.tipo_acesso, TipoAcessoPortaria.DELIVERY)

    def test_chegada_de_visitante_nao_vaza_texto_de_encomenda(self):
        """Registrada uma visita, nem a mensagem nem a linha tratam-na como entrega."""
        dados = {
            "condominio": str(self.condominio.pk),
            "relato": "Chegou Carlos, visita do 301",
            "acao": "chegada",
        }
        resposta = self.client.post(
            reverse("portaria:confirmar"), dados, follow=True
        )

        mensagem = " ".join(str(m) for m in resposta.context["messages"]).casefold()
        self.assertIn("carlos entrou", mensagem)
        self.assertNotIn("encomenda", mensagem)
        self.assertNotIn("whatsapp", mensagem)

        # O painel sempre traz o caminho da encomenda e o aviso por WhatsApp; o
        # que a visita não pode receber é esse texto na própria linha de "Aqui
        # agora". A linha é uma só: o Carlos recém-registrado.
        html = resposta.content.decode()
        inicio = html.index("eithra-portaria-linha")
        linha = html[inicio : html.index("</li>", inicio)].casefold()
        self.assertNotIn("encomenda", linha)
        self.assertNotIn("whatsapp", linha)
        self.assertNotIn("data-eithra-avisar", linha)

    def test_confirmar_saida_fecha_a_presenca(self):
        acesso = apoio.criar_acesso(
            self.condominio, self.usuario, nome="Joana Prado", unidade=self.unidade
        )

        resposta = self._confirmar("Joana saiu", "saida")

        self.assertEqual(resposta.status_code, 302)
        acesso.refresh_from_db()
        self.assertIsNotNone(acesso.saida_em)

    # --- payload forjado -------------------------------------------------

    def test_payload_forjado_nao_escolhe_a_acao(self):
        """A portaria leu chegada; um POST dizendo saída não executa nada."""
        resposta = self._confirmar("Chegou Carlos, visita do 301", "saida")

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "O relato mudou desde a leitura.")
        self.assertEqual(AutorizacaoPortaria.objects.count(), 0)
        self.assertEqual(AcessoPortaria.objects.count(), 0)

    def test_payload_forjado_nao_troca_de_condominio(self):
        outro = apoio.criar_condominio("Outro Condomínio")

        lido = self._entender(
            "Chegou Carlos, visita do 301", condominio=str(outro.pk)
        )
        confirmado = self._confirmar(
            "Chegou Carlos, visita do 301",
            "chegada",
            condominio=str(outro.pk),
        )

        self.assertEqual(lido.status_code, 404)
        self.assertEqual(confirmado.status_code, 404)
        self.assertEqual(AutorizacaoPortaria.objects.count(), 0)
        self.assertEqual(AcessoPortaria.objects.count(), 0)

    # --- quem não passa pela portaria ------------------------------------

    def test_quem_nao_esta_logado_nao_interpreta(self):
        """Sem sessão não há leitura: o primeiro portão é o login."""
        self.client.logout()

        resposta = self._entender("Chegou Carlos, visita do 301")

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(AutorizacaoPortaria.objects.count(), 0)
        self.assertEqual(AcessoPortaria.objects.count(), 0)

    # --- quando a fala serve a mais de uma leitura ------------------------

    def test_unidade_ambigua_pergunta_em_vez_de_escolher(self):
        """Dois cadastros respondem ao mesmo número: quem escolhe é a portaria."""
        comunidade_apoio.criar_unidade(self.condominio, identificador="A-101")
        comunidade_apoio.criar_unidade(self.condominio, identificador="B-101")

        resposta = self._entender("Chegou Carlos, visita do 101")

        self.assertContains(resposta, "Qual unidade: A-101 ou B-101?")
        self.assertNotContains(resposta, 'name="acao"')
        self.assertEqual(AutorizacaoPortaria.objects.count(), 0)

    # --- destino do prestador --------------------------------------------

    def test_prestador_do_condominio_nao_pede_unidade(self):
        """Quem vem ao condomínio inteiro não tem unidade a informar."""
        apoio.criar_local(self.condominio, nome="Elevador de serviço")

        relato = "Chegou João da Atlas para ver o elevador."
        resposta = self._entender(relato)

        self.assertContains(resposta, 'name="acao" value="chegada"')
        self.assertNotContains(resposta, "Para qual unidade?")

        self._confirmar(relato, "chegada")

        acesso = AcessoPortaria.objects.get(nome="João")
        self.assertIsNone(acesso.unidade_id)
        self.assertIsNotNone(acesso.local_id)

    def test_prestador_de_unidade_mantem_a_unidade(self):
        """O serviço é em uma unidade: ela continua sendo o destino."""
        relato = "Chegou o técnico da Atlas para a unidade 301."
        resposta = self._entender(relato)

        self.assertContains(resposta, 'name="acao" value="chegada"')

        self._confirmar(relato, "chegada")

        acesso = AcessoPortaria.objects.get()
        self.assertEqual(acesso.unidade_id, self.unidade.pk)
        self.assertEqual(acesso.tipo_acesso, "prestador")

    # --- corrigir ---------------------------------------------------------

    def test_corrigir_guarda_o_relato_para_devolver_ao_campo(self):
        """O relato original fica na tela mesmo quando o campo se esvazia.

        É o que permite reescrever sem reiniciar: enquanto a leitura pede uma
        resposta, o texto dito continua guardado para o "Corrigir" devolvê-lo.
        """
        resposta = self._entender("Chegou Carlos")

        self.assertContains(
            resposta, 'eithra-portaria-leitura-pergunta">Para qual unidade?</p>'
        )
        self.assertContains(resposta, 'data-eithra-relato-original="Chegou Carlos"')
        self.assertContains(
            resposta, 'value="Chegou Carlos" data-eithra-respondendo'
        )
        self.assertContains(resposta, "data-eithra-corrigir")
        self.assertNotContains(resposta, 'name="acao"')

    # --- o que ainda não tem fluxo por aqui -------------------------------

    def test_problema_na_fala_nao_abre_ocorrencia(self):
        """A ocorrência é anunciada, mas quem a registra é o fluxo convencional."""
        cadastros_apoio.criar_fornecedor(
            self.condominio,
            nome_razao_social="Atlas Elevadores Ltda.",
            nome_fantasia="Atlas",
        )
        local = apoio.criar_local(self.condominio, nome="Casa de máquinas")
        cadastros_apoio.criar_equipamento(
            self.condominio, nome="Elevador de serviço", local=local
        )
        relato = "Chegou o técnico da Atlas e o elevador está fazendo barulho."

        resposta = self._entender(relato)

        self.assertContains(
            resposta, "Registrar ocorrência ainda não está disponível por aqui."
        )
        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(AutorizacaoPortaria.objects.count(), 0)

        self._confirmar(relato, "chegada")

        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(AutorizacaoPortaria.objects.count(), 1)

    def test_autorizacao_nao_abre_fluxo_novo(self):
        """Uma autorização dita na portaria para no aviso, sem registro novo."""
        resposta = self._entender("A Ana autorizou a entrada do Carlos no 301.")

        self.assertContains(
            resposta, "Registrar autorização ainda não está disponível por aqui."
        )
        self.assertNotContains(resposta, 'name="acao"')
        self.assertEqual(AutorizacaoPortaria.objects.count(), 0)
        self.assertEqual(AcessoPortaria.objects.count(), 0)


class PortariaVerificacaoTests(TestCase):
    """Concluir a chegada que ficou aguardando confirmação.

    A chegada não prevista nasce aguardando: só depois de falar com a unidade
    o resultado se fecha. E o desfecho decide a passagem — apenas "Autorizado"
    abre a presença; os demais encerram a espera sem deixar ninguém dentro.
    """

    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(self.usuario)
        self.unidade = comunidade_apoio.criar_unidade(
            self.condominio, identificador="902"
        )

    def _aguardando(self, nome="Visitante Aguardando"):
        return apoio.criar_autorizacao(
            self.condominio,
            self.usuario,
            nome=nome,
            unidade=self.unidade,
            status="aguardando_confirmacao",
        )

    def _verificar(self, autorizacao, **campos):
        dados = {"condominio": str(self.condominio.pk), "status": "autorizado"}
        dados.update(campos)
        return self.client.post(
            reverse("portaria:verificar", args=[autorizacao.pk]), dados
        )

    def test_autorizado_abre_a_passagem(self):
        autorizacao = self._aguardando()

        resposta = self._verificar(autorizacao, meio_autorizacao="interfone")

        self.assertEqual(resposta.status_code, 302)
        autorizacao.refresh_from_db()
        self.assertEqual(autorizacao.status, "autorizado")
        self.assertTrue(
            AcessoPortaria.objects.filter(autorizacao=autorizacao).exists()
        )

    def test_negado_encerra_sem_abrir_a_passagem(self):
        autorizacao = self._aguardando()

        resposta = self._verificar(
            autorizacao, status="nao_autorizado", meio_autorizacao="interfone"
        )

        self.assertEqual(resposta.status_code, 302)
        autorizacao.refresh_from_db()
        self.assertEqual(autorizacao.status, "nao_autorizado")
        self.assertFalse(
            AcessoPortaria.objects.filter(autorizacao=autorizacao).exists()
        )

    def test_sem_resposta_encerra_e_nao_guarda_autorizador(self):
        """Sem resposta não há quem autorizou: nome solto não vira autorizador."""
        autorizacao = self._aguardando()

        resposta = self._verificar(
            autorizacao, status="sem_resposta", autorizador_nome="Fulano Solto"
        )

        self.assertEqual(resposta.status_code, 302)
        autorizacao.refresh_from_db()
        self.assertEqual(autorizacao.status, "sem_resposta")
        self.assertEqual(autorizacao.autorizador_nome, "")
        self.assertFalse(
            AcessoPortaria.objects.filter(autorizacao=autorizacao).exists()
        )

    def test_resultado_decisivo_exige_o_meio(self):
        autorizacao = self._aguardando()

        resposta = self._verificar(autorizacao)

        self.assertEqual(resposta.status_code, 400)
        self.assertContains(
            resposta,
            "Informe por qual meio esse resultado foi registrado.",
            status_code=400,
        )
        autorizacao.refresh_from_db()
        self.assertEqual(autorizacao.status, "aguardando_confirmacao")

    def test_a_passagem_ja_aberta_nao_abre_de_novo(self):
        autorizacao = self._aguardando()
        apoio.criar_acesso(
            self.condominio,
            self.usuario,
            autorizacao=autorizacao,
            unidade=self.unidade,
            nome=autorizacao.nome,
        )

        resposta = self._verificar(autorizacao, meio_autorizacao="interfone")

        self.assertEqual(resposta.status_code, 400)
        self.assertContains(
            resposta,
            "A entrada desta pessoa já foi registrada.",
            status_code=400,
        )
        self.assertEqual(
            AcessoPortaria.objects.filter(autorizacao=autorizacao).count(), 1
        )

    def test_nao_verifica_outro_condominio(self):
        outro = apoio.criar_condominio("Outro")
        autorizacao = apoio.criar_autorizacao(
            outro, self.usuario, nome="Fora do escopo", status="aguardando_confirmacao"
        )

        resposta = self._verificar(autorizacao, meio_autorizacao="interfone")

        self.assertEqual(resposta.status_code, 404)
