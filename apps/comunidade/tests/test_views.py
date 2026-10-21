"""As telas da comunidade: o que elas mostram, o que elas mudam e o que negam.

Dois cuidados atravessam este arquivo. O primeiro: a autorização é conferida no
backend, e uma requisição direta de quem não alcança o módulo recebe 404 —
esconder o botão na tela não é controle de acesso. O segundo: listar é ver o
estado de hoje e o que já passou, então a ficha mostra quem está ligado agora e
guarda, recolhido, quem já esteve.

Hoje quem enxerga o módulo também o mantém: não existe, nesta fase, um nível de
leitura pura para separar 403 de 404. Por isso a recusa testada aqui é a de
quem não responde pelo condomínio, que é 404.
"""
from django.test import TestCase
from django.urls import reverse

from apps.cadastros.tests.apoio import criar_condominio, criar_pessoa, criar_vinculo, entrar_como
from apps.comunidade.constants import TipoVinculoPessoaUnidade
from apps.comunidade.models import Pessoa, Unidade, Veiculo, VinculoPessoaUnidade
from apps.comunidade.tests import apoio
from apps.condominios.constants import Papel

TELAS = (
    "comunidade:unidades",
    "comunidade:pessoas",
    "comunidade:veiculos",
)


class AcessoAsTelasTests(TestCase):
    """A porta do módulo, papel por papel, sem depender do menu."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")

    def test_exige_login(self):
        for nome in TELAS:
            with self.subTest(tela=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 302)

    def test_quem_nao_responde_pelo_condominio_recebe_404(self):
        for indice, papel in enumerate(
            (Papel.OPERACIONAL, Papel.PORTEIRO, Papel.MORADOR)
        ):
            entrar_como(self.client, papel, self.condominio, f"{indice}@exemplo.test")
            for nome in TELAS:
                with self.subTest(papel=papel, tela=nome):
                    self.assertEqual(self.client.get(reverse(nome)).status_code, 404)

    def test_gestao_e_supervisao_entram_em_todas_as_telas(self):
        for indice, papel in enumerate(
            (Papel.ADMIN, Papel.SINDICA, Papel.SUPERVISORA)
        ):
            entrar_como(self.client, papel, self.condominio, f"g{indice}@exemplo.test")
            for nome in TELAS:
                with self.subTest(papel=papel, tela=nome):
                    self.assertEqual(self.client.get(reverse(nome)).status_code, 200)


class EscritaNegadaTests(TestCase):
    """Quem não alcança o módulo também não o altera por requisição direta."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.unidade = apoio.criar_unidade(self.condominio)
        self.pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        self.veiculo = apoio.criar_veiculo(self.condominio)

    def test_morador_nao_cadastra_unidade_nem_pessoa_nem_veiculo(self):
        entrar_como(self.client, Papel.MORADOR, self.condominio)
        enderecos = (
            reverse("comunidade:criar_unidade"),
            reverse("comunidade:criar_pessoa"),
            reverse("comunidade:criar_veiculo"),
        )
        for endereco in enderecos:
            with self.subTest(endereco=endereco):
                resposta = self.client.post(
                    endereco,
                    {"condominio": str(self.condominio.pk), "identificador": "999"},
                )
                self.assertEqual(resposta.status_code, 404)
        self.assertFalse(Unidade.objects.filter(identificador="999").exists())

    def test_morador_nao_alcanca_a_ficha_por_endereco_direto(self):
        entrar_como(self.client, Papel.MORADOR, self.condominio)
        resposta = self.client.get(
            reverse("comunidade:unidade", args=[self.unidade.pk])
        )
        self.assertEqual(resposta.status_code, 404)


class IsolamentoEntreCondominiosTests(TestCase):
    """Nada de um condomínio é alcançado a partir de outro."""

    def setUp(self):
        self.alpha = criar_condominio("Alpha")
        self.beta = criar_condominio("Beta")
        self.sindica = entrar_como(self.client, Papel.SINDICA, self.alpha)
        self.unidade_do_beta = apoio.criar_unidade(self.beta, "303")
        self.pessoa_do_beta = apoio.criar_pessoa_da_comunidade(self.beta)

    def test_ficha_de_unidade_de_outro_condominio_e_404(self):
        resposta = self.client.get(
            reverse("comunidade:unidade", args=[self.unidade_do_beta.pk])
        )
        self.assertEqual(resposta.status_code, 404)

    def test_ficha_de_pessoa_de_outro_condominio_e_404(self):
        resposta = self.client.get(
            reverse("comunidade:pessoa", args=[self.pessoa_do_beta.pk])
        )
        self.assertEqual(resposta.status_code, 404)

    def test_editar_registro_de_outro_condominio_e_404(self):
        resposta = self.client.post(
            reverse("comunidade:editar_unidade", args=[self.unidade_do_beta.pk]),
            {"condominio": str(self.alpha.pk), "identificador": "Tentativa"},
        )
        self.assertEqual(resposta.status_code, 404)
        self.unidade_do_beta.refresh_from_db()
        self.assertEqual(self.unidade_do_beta.identificador, "303")

    def test_lista_mostra_apenas_o_condominio_em_vista(self):
        apoio.criar_unidade(self.alpha, "202")
        conteudo = self.client.get(reverse("comunidade:unidades")).content.decode()
        self.assertIn("202", conteudo)
        self.assertNotIn(self.unidade_do_beta.rotulo, conteudo)

    def test_condominio_nao_autorizado_no_endereco_e_404(self):
        resposta = self.client.get(
            reverse("comunidade:unidades"), {"condominio": str(self.beta.pk)}
        )
        self.assertEqual(resposta.status_code, 404)

    def test_chave_de_registro_torta_nao_derruba_a_tela(self):
        resposta = self.client.get("/comunidade/unidades/nao-e-uma-chave/")
        self.assertEqual(resposta.status_code, 404)


class ListaDeUnidadesTests(TestCase):
    """A lista responde quem está ligado a cada unidade hoje."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.sindica = entrar_como(self.client, Papel.SINDICA, self.condominio)
        self.unidade = apoio.criar_unidade(self.condominio, "101", bloco_torre="Bloco A")

    def test_resume_quantas_pessoas_estao_vinculadas(self):
        for indice in range(3):
            pessoa = apoio.criar_pessoa_da_comunidade(
                self.condominio, f"Pessoa Exemplo 0{indice}"
            )
            apoio.criar_vinculo_pessoa_unidade(self.condominio, pessoa, self.unidade)
        conteudo = self.client.get(reverse("comunidade:unidades")).content.decode()
        self.assertIn("3 pessoas vinculadas", conteudo)

    def test_unidade_sem_ninguem_diz_que_ninguem_esta_vinculado(self):
        conteudo = self.client.get(reverse("comunidade:unidades")).content.decode()
        self.assertIn("Ninguém vinculado no momento", conteudo)

    def test_lista_e_tabela_no_desktop_e_cartoes_no_mobile(self):
        conteudo = self.client.get(reverse("comunidade:unidades")).content.decode()
        self.assertIn("table-responsive d-none d-lg-block", conteudo)
        self.assertIn("list-group list-group-flush d-lg-none", conteudo)

    def test_busca_acha_pelo_bloco(self):
        apoio.criar_unidade(self.condominio, "303")
        conteudo = self.client.get(
            reverse("comunidade:unidades"), {"q": "Bloco A"}
        ).content.decode()
        self.assertIn("101", conteudo)
        self.assertNotIn("303", conteudo)

    def test_busca_sem_resultado_explica_que_nada_foi_encontrado(self):
        conteudo = self.client.get(
            reverse("comunidade:unidades"), {"q": "inventado"}
        ).content.decode()
        self.assertIn("Nada encontrado", conteudo)

    def test_lista_vazia_convida_a_cadastrar(self):
        Unidade.objects.all().delete()
        conteudo = self.client.get(reverse("comunidade:unidades")).content.decode()
        self.assertIn("Nenhuma unidade cadastrada", conteudo)


class TelasDeUnidadeTests(TestCase):
    """Cadastrar, corrigir, desativar e vincular, a partir da unidade."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.sindica = entrar_como(self.client, Papel.SINDICA, self.condominio)
        self.lista = reverse("comunidade:unidades")
        self.endereco_da_lista = f"{self.lista}?condominio={self.condominio.pk}"

    def test_cadastra_a_unidade_e_volta_para_a_lista(self):
        resposta = self.client.post(
            reverse("comunidade:criar_unidade"),
            {"condominio": str(self.condominio.pk), "identificador": "101", "bloco_torre": "Bloco A"},
        )
        self.assertRedirects(resposta, self.endereco_da_lista)
        unidade = Unidade.objects.get()
        self.assertEqual(unidade.condominio, self.condominio)
        self.assertEqual(unidade.criado_por, self.sindica)

    def test_identificacao_repetida_volta_com_erro_no_formulario(self):
        apoio.criar_unidade(self.condominio, "101")
        resposta = self.client.post(
            reverse("comunidade:criar_unidade"),
            {"condominio": str(self.condominio.pk), "identificador": "101"},
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(Unidade.objects.count(), 1)

    def test_ficha_mostra_dados_e_vinculos_de_hoje(self):
        unidade = apoio.criar_unidade(self.condominio, "101", bloco_torre="Bloco A", andar="10")
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa Exemplo 01")
        apoio.criar_vinculo_pessoa_unidade(self.condominio, pessoa, unidade)
        conteudo = self.client.get(
            reverse("comunidade:unidade", args=[unidade.pk])
        ).content.decode()
        self.assertIn("Bloco A · 101", conteudo)
        self.assertIn("Pessoa Exemplo 01", conteudo)
        self.assertIn("Morador", conteudo)

    def test_ficha_guarda_quem_saiu_no_historico(self):
        unidade = apoio.criar_unidade(self.condominio)
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa Exemplo 02")
        apoio.criar_vinculo_pessoa_unidade(
            self.condominio, pessoa, unidade, data_fim=apoio.ontem()
        )
        conteudo = self.client.get(
            reverse("comunidade:unidade", args=[unidade.pk])
        ).content.decode()
        self.assertIn("Histórico de pessoas (1)", conteudo)
        self.assertIn("Pessoa Exemplo 02", conteudo)

    def test_corrige_os_dados_da_unidade(self):
        unidade = apoio.criar_unidade(self.condominio)
        resposta = self.client.post(
            reverse("comunidade:editar_unidade", args=[unidade.pk]),
            {"condominio": str(self.condominio.pk), "identificador": "101", "andar": "10"},
        )
        self.assertRedirects(
            resposta,
            f"{reverse('comunidade:unidade', args=[unidade.pk])}?condominio={self.condominio.pk}",
        )
        unidade.refresh_from_db()
        self.assertEqual(unidade.andar, "10")

    def test_alterna_a_situacao_da_unidade(self):
        unidade = apoio.criar_unidade(self.condominio)
        resposta = self.client.post(
            reverse("comunidade:alternar_unidade", args=[unidade.pk]),
            {"condominio": str(self.condominio.pk)},
        )
        self.assertRedirects(resposta, self.endereco_da_lista)
        unidade.refresh_from_db()
        self.assertFalse(unidade.ativa)

    def test_unidade_com_vinculo_em_vigor_avisa_e_nao_desativa(self):
        unidade = apoio.criar_unidade(self.condominio)
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        apoio.criar_vinculo_pessoa_unidade(self.condominio, pessoa, unidade)
        resposta = self.client.post(
            reverse("comunidade:alternar_unidade", args=[unidade.pk]),
            {"condominio": str(self.condominio.pk)},
            follow=True,
        )
        self.assertContains(resposta, "Encerre-os antes de desativá-la")
        unidade.refresh_from_db()
        self.assertTrue(unidade.ativa)

    def test_vincular_pessoa_a_unidade_pela_ficha(self):
        unidade = apoio.criar_unidade(self.condominio)
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        resposta = self.client.post(
            reverse("comunidade:vincular_pessoa", args=[unidade.pk]),
            {
                "condominio": str(self.condominio.pk),
                "pessoa": str(pessoa.pk),
                "tipo_vinculo": TipoVinculoPessoaUnidade.MORADOR,
                "data_inicio": apoio.HOJE.isoformat(),
            },
        )
        self.assertRedirects(
            resposta,
            f"{reverse('comunidade:unidade', args=[unidade.pk])}?condominio={self.condominio.pk}",
        )
        self.assertTrue(VinculoPessoaUnidade.objects.vigentes().exists())

    def test_vincular_pessoa_de_outro_condominio_volta_com_erro(self):
        unidade = apoio.criar_unidade(self.condominio)
        alheia = apoio.criar_pessoa_da_comunidade(criar_condominio("Beta"))
        resposta = self.client.post(
            reverse("comunidade:vincular_pessoa", args=[unidade.pk]),
            {
                "condominio": str(self.condominio.pk),
                "pessoa": str(alheia.pk),
                "tipo_vinculo": TipoVinculoPessoaUnidade.MORADOR,
                "data_inicio": apoio.HOJE.isoformat(),
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(VinculoPessoaUnidade.objects.exists())

    def test_relaciona_veiculo_a_unidade(self):
        unidade = apoio.criar_unidade(self.condominio)
        veiculo = apoio.criar_veiculo(self.condominio)
        resposta = self.client.post(
            reverse("comunidade:vincular_veiculo", args=[unidade.pk]),
            {
                "condominio": str(self.condominio.pk),
                "veiculo": str(veiculo.pk),
                "data_inicio": apoio.HOJE.isoformat(),
            },
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertIn(veiculo, unidade.veiculos_relacionados())


class ListaDePessoasTests(TestCase):
    """A busca da pessoa é a mesma pergunta: quem é esta pessoa."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.sindica = entrar_como(self.client, Papel.SINDICA, self.condominio)

    def _conteudo(self, **parametros):
        return self.client.get(reverse("comunidade:pessoas"), parametros).content.decode()

    def test_mostra_as_unidades_de_cada_pessoa_hoje(self):
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa Exemplo 01")
        unidade = apoio.criar_unidade(self.condominio, "101")
        apoio.criar_vinculo_pessoa_unidade(self.condominio, pessoa, unidade)
        self.assertIn("Unidade 101", self._conteudo())

    def test_busca_por_telefone_e_por_email(self):
        apoio.criar_pessoa_da_comunidade(
            self.condominio,
            "Pessoa Exemplo 01",
            telefone="71999990000",
            email="exemplo@teste.com",
        )
        apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa Exemplo 02")
        self.assertIn("Pessoa Exemplo 01", self._conteudo(q="71999990000"))
        self.assertNotIn("Pessoa Exemplo 02", self._conteudo(q="exemplo@teste.com"))

    def test_documento_nao_aparece_na_lista(self):
        apoio.criar_pessoa_da_comunidade(
            self.condominio, "Pessoa Exemplo 01", documento="123.456.789-01"
        )
        self.assertNotIn("123.456.789-01", self._conteudo())

    def test_lista_usa_tabela_no_desktop_e_cartoes_no_mobile(self):
        apoio.criar_pessoa_da_comunidade(self.condominio)
        conteudo = self._conteudo()
        self.assertIn("table-responsive d-none d-lg-block", conteudo)
        self.assertIn("list-group list-group-flush d-lg-none", conteudo)


class TelasDePessoaTests(TestCase):
    """Cadastrar, corrigir, desativar e vincular, a partir da pessoa."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.sindica = entrar_como(self.client, Papel.SINDICA, self.condominio)
        self.endereco_da_lista = (
            f"{reverse('comunidade:pessoas')}?condominio={self.condominio.pk}"
        )

    def test_cadastra_a_pessoa_so_com_o_nome(self):
        resposta = self.client.post(
            reverse("comunidade:criar_pessoa"),
            {"condominio": str(self.condominio.pk), "nome": "Pessoa Exemplo 01"},
        )
        self.assertRedirects(resposta, self.endereco_da_lista)
        pessoa = Pessoa.objects.get()
        self.assertEqual(pessoa.nome, "Pessoa Exemplo 01")
        self.assertEqual(pessoa.criado_por, self.sindica)

    def test_pessoa_sem_nome_volta_com_erro(self):
        resposta = self.client.post(
            reverse("comunidade:criar_pessoa"),
            {"condominio": str(self.condominio.pk), "nome": "  "},
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(Pessoa.objects.exists())

    def test_ficha_mostra_unidades_atuais_e_historicas(self):
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa Exemplo 01")
        atual = apoio.criar_unidade(self.condominio, "101")
        antiga = apoio.criar_unidade(self.condominio, "202")
        apoio.criar_vinculo_pessoa_unidade(self.condominio, pessoa, atual)
        apoio.criar_vinculo_pessoa_unidade(
            self.condominio, pessoa, antiga, data_fim=apoio.ontem()
        )
        conteudo = self.client.get(
            reverse("comunidade:pessoa", args=[pessoa.pk])
        ).content.decode()
        self.assertIn("101", conteudo)
        self.assertIn("Histórico de unidades (1)", conteudo)
        self.assertIn("202", conteudo)

    def test_ficha_mostra_os_veiculos_da_pessoa(self):
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa Exemplo 01")
        veiculo = apoio.criar_veiculo(self.condominio, "ABC1D23")
        apoio.criar_vinculo_veiculo_pessoa(self.condominio, veiculo, pessoa)
        conteudo = self.client.get(
            reverse("comunidade:pessoa", args=[pessoa.pk])
        ).content.decode()
        self.assertIn("ABC1D23", conteudo)

    def test_corrige_o_contato_da_pessoa(self):
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        resposta = self.client.post(
            reverse("comunidade:editar_pessoa", args=[pessoa.pk]),
            {
                "condominio": str(self.condominio.pk),
                "nome": pessoa.nome,
                "telefone": "(71) 99999-0000",
            },
        )
        self.assertRedirects(
            resposta,
            f"{reverse('comunidade:pessoa', args=[pessoa.pk])}?condominio={self.condominio.pk}",
        )
        pessoa.refresh_from_db()
        self.assertEqual(pessoa.telefone, "71999990000")

    def test_alterna_a_situacao_da_pessoa(self):
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        resposta = self.client.post(
            reverse("comunidade:alternar_pessoa", args=[pessoa.pk]),
            {"condominio": str(self.condominio.pk)},
        )
        self.assertRedirects(resposta, self.endereco_da_lista)
        pessoa.refresh_from_db()
        self.assertFalse(pessoa.ativa)

    def test_vincular_a_pessoa_a_unidade_pela_ficha(self):
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        unidade = apoio.criar_unidade(self.condominio, "101")
        resposta = self.client.post(
            reverse("comunidade:vincular_unidade", args=[pessoa.pk]),
            {
                "condominio": str(self.condominio.pk),
                "unidade": str(unidade.pk),
                "tipo_vinculo": TipoVinculoPessoaUnidade.PROPRIETARIO,
                "data_inicio": apoio.HOJE.isoformat(),
            },
        )
        self.assertRedirects(
            resposta,
            f"{reverse('comunidade:pessoa', args=[pessoa.pk])}?condominio={self.condominio.pk}",
        )
        vinculo = VinculoPessoaUnidade.objects.get()
        self.assertEqual(vinculo.tipo_vinculo, TipoVinculoPessoaUnidade.PROPRIETARIO)


class ListaDeVeiculosTests(TestCase):
    """A lista responde quem usa o veículo e a que unidade ele se relaciona."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.sindica = entrar_como(self.client, Papel.SINDICA, self.condominio)

    def _conteudo(self, **parametros):
        return self.client.get(reverse("comunidade:veiculos"), parametros).content.decode()

    def test_mostra_quem_usa_e_a_que_unidade_se_relaciona(self):
        veiculo = apoio.criar_veiculo(self.condominio, "ABC1D23")
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa Exemplo 01")
        unidade = apoio.criar_unidade(self.condominio, "101")
        apoio.criar_vinculo_veiculo_pessoa(self.condominio, veiculo, pessoa)
        apoio.criar_vinculo_veiculo_unidade(self.condominio, veiculo, unidade)
        conteudo = self._conteudo()
        self.assertIn("1 pessoa vinculada", conteudo)
        self.assertIn("Unidade 101", conteudo)

    def test_busca_pela_placa_ignorando_a_caixa(self):
        apoio.criar_veiculo(self.condominio, "ABC1D23")
        apoio.criar_veiculo(self.condominio, "XYZ9K87")
        conteudo = self._conteudo(q="abc1d23")
        self.assertIn("ABC1D23", conteudo)
        self.assertNotIn("XYZ9K87", conteudo)

    def test_lista_usa_tabela_no_desktop_e_cartoes_no_mobile(self):
        apoio.criar_veiculo(self.condominio)
        conteudo = self._conteudo()
        self.assertIn("table-responsive d-none d-lg-block", conteudo)
        self.assertIn("list-group list-group-flush d-lg-none", conteudo)


class TelasDeVeiculoTests(TestCase):
    """Cadastrar, corrigir, desativar e relacionar, a partir do veículo."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.sindica = entrar_como(self.client, Papel.SINDICA, self.condominio)
        self.endereco_da_lista = (
            f"{reverse('comunidade:veiculos')}?condominio={self.condominio.pk}"
        )

    def test_cadastra_o_veiculo_pela_placa(self):
        resposta = self.client.post(
            reverse("comunidade:criar_veiculo"),
            {
                "condominio": str(self.condominio.pk),
                "placa": "abc1d23",
                "marca": "Fiat",
                "modelo": "Uno",
            },
        )
        self.assertRedirects(resposta, self.endereco_da_lista)
        veiculo = Veiculo.objects.get()
        self.assertEqual(veiculo.placa, "ABC1D23")
        self.assertEqual(veiculo.criado_por, self.sindica)

    def test_placa_repetida_volta_com_erro(self):
        apoio.criar_veiculo(self.condominio, "ABC1D23")
        resposta = self.client.post(
            reverse("comunidade:criar_veiculo"),
            {"condominio": str(self.condominio.pk), "placa": "abc1d23"},
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(Veiculo.objects.count(), 1)

    def test_placa_fora_do_padrao_volta_com_erro(self):
        resposta = self.client.post(
            reverse("comunidade:criar_veiculo"),
            {"condominio": str(self.condominio.pk), "placa": "AB123"},
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(Veiculo.objects.exists())

    def test_ficha_mostra_quem_utiliza_e_as_unidades(self):
        veiculo = apoio.criar_veiculo(self.condominio, "ABC1D23")
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa Exemplo 01")
        usou = apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa Exemplo 02")
        unidade = apoio.criar_unidade(self.condominio, "101")
        apoio.criar_vinculo_veiculo_pessoa(self.condominio, veiculo, pessoa)
        apoio.criar_vinculo_veiculo_pessoa(
            self.condominio, veiculo, usou, data_fim=apoio.ontem()
        )
        apoio.criar_vinculo_veiculo_unidade(self.condominio, veiculo, unidade)
        conteudo = self.client.get(
            reverse("comunidade:veiculo", args=[veiculo.pk])
        ).content.decode()
        self.assertIn("Pessoa Exemplo 01", conteudo)
        self.assertIn("Histórico de pessoas (1)", conteudo)
        self.assertIn("Pessoa Exemplo 02", conteudo)
        self.assertIn("101", conteudo)

    def test_corrige_os_dados_do_veiculo(self):
        veiculo = apoio.criar_veiculo(self.condominio, "ABC1D23")
        resposta = self.client.post(
            reverse("comunidade:editar_veiculo", args=[veiculo.pk]),
            {
                "condominio": str(self.condominio.pk),
                "placa": "ABC1D23",
                "cor": "Prata",
            },
        )
        self.assertRedirects(
            resposta,
            f"{reverse('comunidade:veiculo', args=[veiculo.pk])}?condominio={self.condominio.pk}",
        )
        veiculo.refresh_from_db()
        self.assertEqual(veiculo.cor, "Prata")

    def test_alterna_a_situacao_do_veiculo(self):
        veiculo = apoio.criar_veiculo(self.condominio)
        resposta = self.client.post(
            reverse("comunidade:alternar_veiculo", args=[veiculo.pk]),
            {"condominio": str(self.condominio.pk)},
        )
        self.assertRedirects(resposta, self.endereco_da_lista)
        veiculo.refresh_from_db()
        self.assertFalse(veiculo.ativo)

    def test_registra_quem_utiliza_o_veiculo(self):
        veiculo = apoio.criar_veiculo(self.condominio)
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        resposta = self.client.post(
            reverse("comunidade:vincular_pessoa_veiculo", args=[veiculo.pk]),
            {
                "condominio": str(self.condominio.pk),
                "pessoa": str(pessoa.pk),
                "data_inicio": apoio.HOJE.isoformat(),
            },
        )
        self.assertRedirects(
            resposta,
            f"{reverse('comunidade:veiculo', args=[veiculo.pk])}?condominio={self.condominio.pk}",
        )
        self.assertEqual(list(veiculo.pessoas_atuais()), [pessoa])

    def test_relaciona_o_veiculo_a_unidade(self):
        veiculo = apoio.criar_veiculo(self.condominio)
        unidade = apoio.criar_unidade(self.condominio, "101")
        resposta = self.client.post(
            reverse("comunidade:vincular_unidade_veiculo", args=[veiculo.pk]),
            {
                "condominio": str(self.condominio.pk),
                "unidade": str(unidade.pk),
                "data_inicio": apoio.HOJE.isoformat(),
            },
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(list(veiculo.unidades_atuais()), [unidade])


class EncerrarVinculoTests(TestCase):
    """Encerrar um vínculo volta para a ficha de onde a ação partiu."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.sindica = entrar_como(self.client, Papel.SINDICA, self.condominio)

    def test_encerra_o_vinculo_e_volta_para_a_unidade(self):
        unidade = apoio.criar_unidade(self.condominio, "101")
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        vinculo = apoio.criar_vinculo_pessoa_unidade(self.condominio, pessoa, unidade)
        resposta = self.client.post(
            reverse("comunidade:encerrar_vinculo_pessoa_unidade", args=[vinculo.pk]),
            {"condominio": str(self.condominio.pk), "voltar": "unidade"},
        )
        self.assertRedirects(
            resposta,
            f"{reverse('comunidade:unidade', args=[unidade.pk])}?condominio={self.condominio.pk}",
        )
        vinculo.refresh_from_db()
        self.assertIsNotNone(vinculo.data_fim)

    def test_encerra_o_vinculo_e_volta_para_a_pessoa(self):
        unidade = apoio.criar_unidade(self.condominio, "101")
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        vinculo = apoio.criar_vinculo_pessoa_unidade(self.condominio, pessoa, unidade)
        resposta = self.client.post(
            reverse("comunidade:encerrar_vinculo_pessoa_unidade", args=[vinculo.pk]),
            {"condominio": str(self.condominio.pk), "voltar": "pessoa"},
        )
        self.assertRedirects(
            resposta,
            f"{reverse('comunidade:pessoa', args=[pessoa.pk])}?condominio={self.condominio.pk}",
        )

    def test_voltar_desconhecido_nao_desvia_para_endereco_trazido(self):
        unidade = apoio.criar_unidade(self.condominio, "101")
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        vinculo = apoio.criar_vinculo_pessoa_unidade(self.condominio, pessoa, unidade)
        resposta = self.client.post(
            reverse("comunidade:encerrar_vinculo_pessoa_unidade", args=[vinculo.pk]),
            {
                "condominio": str(self.condominio.pk),
                "voltar": "https://exemplo.invalido/roubo",
            },
        )
        self.assertRedirects(
            resposta,
            f"{reverse('comunidade:unidade', args=[unidade.pk])}?condominio={self.condominio.pk}",
        )

    def test_encerra_o_uso_do_veiculo(self):
        veiculo = apoio.criar_veiculo(self.condominio)
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        vinculo = apoio.criar_vinculo_veiculo_pessoa(self.condominio, veiculo, pessoa)
        resposta = self.client.post(
            reverse("comunidade:encerrar_vinculo_veiculo_pessoa", args=[vinculo.pk]),
            {"condominio": str(self.condominio.pk), "voltar": "veiculo"},
        )
        self.assertEqual(resposta.status_code, 302)
        vinculo.refresh_from_db()
        self.assertIsNotNone(vinculo.data_fim)

    def test_encerra_a_relacao_do_veiculo_com_a_unidade(self):
        veiculo = apoio.criar_veiculo(self.condominio)
        unidade = apoio.criar_unidade(self.condominio, "101")
        vinculo = apoio.criar_vinculo_veiculo_unidade(
            self.condominio, veiculo, unidade
        )
        resposta = self.client.post(
            reverse("comunidade:encerrar_vinculo_veiculo_unidade", args=[vinculo.pk]),
            {"condominio": str(self.condominio.pk), "voltar": "unidade"},
        )
        self.assertRedirects(
            resposta,
            f"{reverse('comunidade:unidade', args=[unidade.pk])}?condominio={self.condominio.pk}",
        )
        vinculo.refresh_from_db()
        self.assertIsNotNone(vinculo.data_fim)

    def test_encerrar_vinculo_de_outro_condominio_e_404(self):
        beta = criar_condominio("Beta")
        unidade = apoio.criar_unidade(beta, "101")
        pessoa = apoio.criar_pessoa_da_comunidade(beta)
        vinculo = apoio.criar_vinculo_pessoa_unidade(beta, pessoa, unidade)
        resposta = self.client.post(
            reverse("comunidade:encerrar_vinculo_pessoa_unidade", args=[vinculo.pk]),
            {"condominio": str(self.condominio.pk), "voltar": "unidade"},
        )
        self.assertEqual(resposta.status_code, 404)

    def test_encerrar_vinculo_exige_post(self):
        unidade = apoio.criar_unidade(self.condominio, "101")
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        vinculo = apoio.criar_vinculo_pessoa_unidade(self.condominio, pessoa, unidade)
        resposta = self.client.get(
            reverse("comunidade:encerrar_vinculo_pessoa_unidade", args=[vinculo.pk])
        )
        self.assertEqual(resposta.status_code, 405)


class SeletorDeCondominioTests(TestCase):
    """Com mais de um condomínio, a tela oferece a escolha e não a inventa."""

    def setUp(self):
        self.alpha = criar_condominio("Alpha")
        self.beta = criar_condominio("Beta")

    def test_com_um_condominio_a_tela_nao_pergunta_qual(self):
        entrar_como(self.client, Papel.SINDICA, self.alpha)
        apoio.criar_unidade(self.alpha, "101")
        conteudo = self.client.get(reverse("comunidade:unidades")).content.decode()
        self.assertIn("101", conteudo)
        self.assertNotIn("Trocar de condomínio", conteudo)

    def test_com_dois_condominios_a_tela_oferece_a_escolha(self):
        gestor = criar_pessoa("gestor@exemplo.test", "Gestão")
        criar_vinculo(gestor, self.alpha, papel=Papel.SINDICA)
        criar_vinculo(gestor, self.beta, papel=Papel.SINDICA)
        self.client.force_login(gestor)
        apoio.criar_unidade(self.alpha, "999")
        apoio.criar_unidade(self.beta, "202")

        conteudo = self.client.get(
            reverse("comunidade:unidades"), {"condominio": str(self.beta.pk)}
        ).content.decode()
        self.assertIn("Trocar de condomínio", conteudo)
        self.assertIn("202", conteudo)
        self.assertNotIn("999", conteudo)


class ServicoDeVinculoNasTelasTests(TestCase):
    """A ficha confere o que o domínio derivou, sem segunda lista de regras."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.sindica = entrar_como(self.client, Papel.SINDICA, self.condominio)

    def test_unidade_com_vinculo_encerrado_aparece_sem_ninguem_hoje(self):
        unidade = apoio.criar_unidade(self.condominio, "101")
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        apoio.criar_vinculo_pessoa_unidade(
            self.condominio, pessoa, unidade, data_fim=apoio.ontem()
        )
        conteudo = self.client.get(reverse("comunidade:unidades")).content.decode()
        self.assertIn("Ninguém vinculado no momento", conteudo)

    def test_transferir_de_unidade_encerra_o_antigo_sem_apagar(self):
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa Exemplo 01")
        antiga = apoio.criar_unidade(self.condominio, "101")
        nova = apoio.criar_unidade(self.condominio, "202")
        vinculo = apoio.criar_vinculo_pessoa_unidade(self.condominio, pessoa, antiga)

        resposta = self.client.post(
            reverse("comunidade:transferir_unidade", args=[vinculo.pk]),
            {"condominio": str(self.condominio.pk), "unidade": str(nova.pk)},
        )
        self.assertRedirects(
            resposta,
            f"{reverse('comunidade:pessoa', args=[pessoa.pk])}?condominio={self.condominio.pk}",
        )

        # O vínculo anterior ganha data de fim e o novo nasce na mesma data, com
        # o mesmo papel: nada foi apagado nem reescrito.
        vinculo.refresh_from_db()
        self.assertIsNotNone(vinculo.data_fim)
        novo = VinculoPessoaUnidade.objects.da_pessoa(pessoa).da_unidade(nova).get()
        self.assertEqual(novo.data_inicio, vinculo.data_fim)
        self.assertEqual(novo.tipo_vinculo, vinculo.tipo_vinculo)
        self.assertEqual(VinculoPessoaUnidade.objects.da_pessoa(pessoa).count(), 2)

        conteudo = self.client.get(
            reverse("comunidade:pessoa", args=[pessoa.pk])
        ).content.decode()
        self.assertIn("202", conteudo)

    def test_alterar_a_unidade_nao_toca_os_outros_papeis_da_pessoa(self):
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa Exemplo 01")
        origem = apoio.criar_unidade(self.condominio, "101")
        destino = apoio.criar_unidade(self.condominio, "202")
        familiar = apoio.criar_vinculo_pessoa_unidade(
            self.condominio,
            pessoa,
            origem,
            tipo_vinculo=TipoVinculoPessoaUnidade.FAMILIAR,
        )
        funcionaria = apoio.criar_vinculo_pessoa_unidade(
            self.condominio,
            pessoa,
            origem,
            tipo_vinculo=TipoVinculoPessoaUnidade.FUNCIONARIO_UNIDADE,
        )

        self.client.post(
            reverse("comunidade:transferir_unidade", args=[familiar.pk]),
            {"condominio": str(self.condominio.pk), "unidade": str(destino.pk)},
        )

        funcionaria.refresh_from_db()
        self.assertIsNone(funcionaria.data_fim)
        self.assertEqual(
            funcionaria.tipo_vinculo, TipoVinculoPessoaUnidade.FUNCIONARIO_UNIDADE
        )

    def test_transferir_unidade_de_outro_condominio_e_404(self):
        beta = criar_condominio("Beta")
        unidade = apoio.criar_unidade(beta, "101")
        pessoa = apoio.criar_pessoa_da_comunidade(beta)
        vinculo = apoio.criar_vinculo_pessoa_unidade(beta, pessoa, unidade)
        destino = apoio.criar_unidade(self.condominio, "202")
        resposta = self.client.post(
            reverse("comunidade:transferir_unidade", args=[vinculo.pk]),
            {"condominio": str(self.condominio.pk), "unidade": str(destino.pk)},
        )
        self.assertEqual(resposta.status_code, 404)

    def test_transferir_unidade_exige_post(self):
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        unidade = apoio.criar_unidade(self.condominio, "101")
        vinculo = apoio.criar_vinculo_pessoa_unidade(self.condominio, pessoa, unidade)
        resposta = self.client.get(
            reverse("comunidade:transferir_unidade", args=[vinculo.pk])
        )
        self.assertEqual(resposta.status_code, 405)