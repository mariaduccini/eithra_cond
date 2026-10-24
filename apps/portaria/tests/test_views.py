from django.test import TestCase
from django.urls import reverse

from apps.comunidade.models import Pessoa
from apps.comunidade.tests import apoio as comunidade_apoio
from apps.condominios.constants import Papel
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
