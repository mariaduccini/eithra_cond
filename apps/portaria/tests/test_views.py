import re

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.tests import apoio as cadastros_apoio
from apps.comunidade.models import Pessoa
from apps.comunidade.tests import apoio as comunidade_apoio
from apps.condominios.constants import Papel
from apps.demandas.models import Demanda
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

    def test_busca_por_unidade_mostra_contexto(self):
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="301")
        pessoa = comunidade_apoio.criar_pessoa_da_comunidade(self.condominio, "Ana Moradora")
        comunidade_apoio.criar_vinculo_pessoa_unidade(self.condominio, pessoa, unidade)
        apoio.criar_autorizacao(
            self.condominio, self.usuario, nome="João Esperado", unidade=unidade
        )
        resposta = self.client.get(reverse("portaria:painel"), {"q": "301"})
        self.assertContains(resposta, "301")
        self.assertContains(resposta, "Ana Moradora")
        self.assertContains(resposta, "João Esperado")

    def test_busca_por_pessoa(self):
        pessoa = comunidade_apoio.criar_pessoa_da_comunidade(self.condominio, "Carlos Souza")
        resposta = self.client.get(reverse("portaria:painel"), {"q": "Carlos"})
        self.assertContains(resposta, "Carlos Souza")

    def test_busca_por_morador_traz_autorizacoes_relacionadas(self):
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="301")
        morador = comunidade_apoio.criar_pessoa_da_comunidade(self.condominio, "Ana Moradora")
        comunidade_apoio.criar_vinculo_pessoa_unidade(self.condominio, morador, unidade)
        apoio.criar_autorizacao(
            self.condominio, self.usuario, nome="João Esperado", unidade=unidade
        )
        resposta = self.client.get(reverse("portaria:painel"), {"q": "Ana"})
        self.assertContains(resposta, "Ana Moradora")
        self.assertContains(resposta, "João Esperado")

    def test_busca_por_placa(self):
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="501")
        pessoa = comunidade_apoio.criar_pessoa_da_comunidade(self.condominio, "Motorista")
        veiculo = comunidade_apoio.criar_veiculo(self.condominio, placa="ABC1D23")
        comunidade_apoio.criar_vinculo_veiculo_pessoa(self.condominio, veiculo, pessoa)
        comunidade_apoio.criar_vinculo_veiculo_unidade(self.condominio, veiculo, unidade)
        resposta = self.client.get(reverse("portaria:painel"), {"q": "ABC1D23"})
        self.assertContains(resposta, "ABC1D23")
        self.assertContains(resposta, "Motorista")
        self.assertContains(resposta, "501")

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
        self.assertContains(painel, "Movimentação atual")
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

    def test_privacidade_nao_exibe_documento_completo(self):
        comunidade_apoio.criar_pessoa_da_comunidade(
            self.condominio, "Pessoa Privada", documento="12345678901"
        )
        resposta = self.client.get(reverse("portaria:painel"), {"q": "Pessoa Privada"})
        self.assertContains(resposta, "Pessoa Privada")
        self.assertNotContains(resposta, "12345678901")


class PortariaBuscaDaTelaTests(TestCase):
    """O contrato da tela de busca: o que a view já sabe precisa chegar até ela.

    A view filtra corretamente quando recebe `q` — o que faltava era a tela
    entregar o que foi digitado. Estes testes prendem o formulário marcado, o
    campo que carrega o termo por `q` e o condomínio que viaja junto: sem isso
    a lista volta a exibir tudo, mesmo com o backend certo.
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

    def test_a_busca_principal_usa_o_mesmo_mecanismo_das_listagens(self):
        """A marca que o Eithra liga sozinho — a mesma das outras listagens."""
        formulario = self._formulario("eithra-portaria-barra")
        self.assertIn("data-eithra-busca", formulario)

    def test_o_campo_principal_continua_sendo_a_busca_por_q(self):
        formulario = self._formulario("eithra-portaria-barra")
        self.assertIn('type="search"', formulario)
        self.assertIn('name="q"', formulario)

    def test_o_historico_liga_os_filtros_reativos(self):
        formulario = self._formulario("eithra-portaria-filtros", aba="historico")
        self.assertIn("data-eithra-filtros", formulario)

    def test_o_condominio_em_vista_viaja_com_a_busca(self):
        """Buscar dentro de um condomínio não pode trocar de condomínio."""
        formulario = self._formulario("eithra-portaria-barra")
        self.assertIn('name="condominio" value="%s"' % self.condominio.pk, formulario)

    def test_busca_por_empresa_mostra_o_fornecedor(self):
        """Empresa é um dos cinco caminhos da busca — e o único sem cobertura."""
        cadastros_apoio.criar_fornecedor(
            self.condominio, nome_razao_social="Atlas Elevadores Ltda."
        )
        resposta = self.client.get(reverse("portaria:painel"), {"q": "Atlas"})
        self.assertContains(resposta, "Atlas Elevadores Ltda.")


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
                tipos, ["visitante", "prestador", "fornecedor", "tecnico"]
            )

        painel = self.client.get(reverse("portaria:painel"))
        self.assertContains(painel, "Visitante")
        self.assertContains(painel, "Prestador")
        self.assertContains(painel, "Fornecedor")
        self.assertContains(painel, "Técnico")

    def test_chegada_nao_prevista_pergunta_so_o_necessario(self):
        """A conversa tem duas perguntas; o resto entra por contexto.

        Informação disponível não é informação necessária: telefone, documento
        e observação existem no formulário, mas não viram pergunta. Placa e
        local continuam sendo recursos de registro, e chegam por um gatilho que
        diz o que passa a valer — nunca por uma lista de opcionais à vista.
        """
        conteudo = self.client.get(reverse("portaria:painel")).content.decode()
        corpo = conteudo[conteudo.find('id="registro-chegada"') :]

        for visivel in (
            "Registrar chegada",
            "Identificação",
            "Autorização",
            "Registro",
            "Digite o nome do morador ou apartamento",
            "Quem chegou?",
            "Tipo de acesso",
            "Resultado da autorização",
        ):
            self.assertIn(visivel, corpo)

        # O que não é necessário para esta decisão não aparece por padrão.
        for ausente in ('name="telefone"', 'name="documento"'):
            self.assertNotIn(ausente, corpo)
        self.assertNotIn("<details", corpo)
        self.assertNotIn("Mais informações", corpo)

        # O contexto tem gatilho próprio, e o campo nasce fora de vista.
        self.assertIn('data-eithra-revelado="placa"', corpo)
        self.assertIn('data-eithra-revelado="local"', corpo)
        self.assertIn('data-eithra-revelar="placa"', corpo)
        self.assertIn('data-eithra-revelar="local"', corpo)

    def test_esperado_pergunta_o_essencial_e_deriva_o_resto(self):
        """O esperado se cria com três respostas; o resto é do domínio.

        Situação, janela de validade e dados administrativos existem no
        formulário, mas não viram pergunta: quem registra já está autorizando, e
        a validade nasce junto com o registro.
        """
        conteudo = self.client.get(reverse("portaria:painel")).content.decode()
        corpo = conteudo[
            conteudo.find('id="novo-esperado"') : conteudo.find('id="registro-chegada"')
        ]

        for visivel in ("Registrar esperado", "Salvar esperado", "Buscar ou digitar empresa"):
            self.assertIn(visivel, corpo)

        for ausente in (
            "Situação",
            "Previsto para",
            "Até quando",
            "Telefone",
            "Documento",
            "Observação",
        ):
            self.assertNotIn(ausente, corpo)

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
            "Escolha a unidade que está recebendo a visita.",
            status_code=400,
        )

    def test_erro_reabre_o_wizard_no_passo_do_erro(self):
        """O modal reabre onde o erro está, não onde a pessoa parou.

        Quem envia do último passo e erra a identificação precisa ver o erro;
        reabrir mudo em outro passo deixa a correção sem explicação na tela.
        """
        unidade = comunidade_apoio.criar_unidade(self.condominio, identificador="905")

        # Erro do passo 1, enviado a partir do passo 3.
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
        self.assertContains(resposta, 'name="passo" value="1"', status_code=400)
        self.assertContains(resposta, "Informe quem chegou.", status_code=400)

        # Erro do passo 2 — o meio da autorização — reabre na autorização.
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
        self.assertContains(resposta, 'name="passo" value="2"', status_code=400)
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
