"""Telas dos cadastros: quem entra, o que vê e o que consegue mudar."""
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.balancos.tests.apoio import StorageDeMentira, jpg, png
from apps.cadastros.models import (
    Equipamento,
    EspecialidadeFornecedor,
    Fornecedor,
    LocalOperacional,
    TipoEquipamento,
    TipoLocalOperacional,
)
from apps.cadastros.tests.apoio import (
    criar_condominio,
    criar_local,
    criar_pessoa,
    criar_vinculo,
    entrar_como,
)
from apps.condominios import logo
from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio, Condominio


class AcessoAsTelasTests(TestCase):
    """Sem vínculo que enxergue o módulo, a resposta é 404."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.telas = (
            "cadastros:condominios",
            "cadastros:pessoas_e_unidades",
            "cadastros:tipos_e_categorias",
            "cadastros:condominio",
            "cadastros:acessos",
            "cadastros:locais",
            "cadastros:equipamentos",
            "cadastros:fornecedores",
        )

    def test_exige_login(self):
        for nome in self.telas:
            with self.subTest(tela=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 302)

    def test_operacional_porteiro_e_morador_recebem_404(self):
        for indice, papel in enumerate(
            (Papel.OPERACIONAL, Papel.PORTEIRO, Papel.MORADOR)
        ):
            entrar_como(self.client, papel, self.condominio, f"{indice}@exemplo.test")
            for nome in self.telas:
                with self.subTest(papel=papel, tela=nome):
                    self.assertEqual(self.client.get(reverse(nome)).status_code, 404)

    def test_supervisora_entra_em_todas_as_telas(self):
        entrar_como(
            self.client, Papel.SUPERVISORA, self.condominio, "supervisora@exemplo.test"
        )
        for nome in self.telas:
            with self.subTest(tela=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 200)


class CondominioTests(TestCase):
    """A ficha do condomínio agora abre em modal dentro da lista."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("cadastros:condominio")

    def test_gestao_ve_o_modal_do_condominio_atual(self):
        entrar_como(self.client, Papel.SINDICA, self.condominio)
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["modal_aberto"], "editar")
        self.assertEqual(resposta.context["item_edicao"], self.condominio)
        self.assertTrue(resposta.context["pode_gerenciar_item_edicao"])
        self.assertContains(resposta, "Alpha")

    def test_gestao_salva_o_cadastro_normalizado(self):
        entrar_como(self.client, Papel.SINDICA, self.condominio)
        resposta = self.client.post(
            reverse("cadastros:editar_condominio", args=[self.condominio.pk]),
            {
                "nome": "  Residencial   Alpha ",
                "cnpj": "12.345.678/0001-95",
                "logradouro": "Rua das Acácias",
                "numero": "120",
                "bairro": "Pituba",
                "cidade": "Salvador",
                "uf": "ba",
                "cep": "40000-000",
                "telefone_administrativo": "(71) 3333-4444",
                "email_administrativo": "Sindico@Exemplo.com.BR",
                "quantidade_unidades": "120",
            },
        )
        self.assertRedirects(resposta, reverse("cadastros:condominios"))
        self.condominio.refresh_from_db()
        self.assertEqual(self.condominio.nome, "Residencial Alpha")
        self.assertEqual(self.condominio.cnpj, "12345678000195")
        self.assertEqual(self.condominio.uf, "BA")
        self.assertEqual(self.condominio.cep, "40000000")
        self.assertEqual(self.condominio.telefone_administrativo, "7133334444")
        self.assertEqual(self.condominio.email_administrativo, "sindico@exemplo.com.br")
        self.assertEqual(self.condominio.quantidade_unidades, 120)

    def test_cnpj_invalido_e_recusado_sem_salvar(self):
        entrar_como(self.client, Papel.SINDICA, self.condominio)
        resposta = self.client.post(
            reverse("cadastros:editar_condominio", args=[self.condominio.pk]),
            {"nome": "Alpha", "cnpj": "123"},
        )
        self.assertEqual(resposta.status_code, 400)
        self.condominio.refresh_from_db()
        self.assertEqual(self.condominio.cnpj, "")

    def test_supervisora_le_em_modo_somente_leitura(self):
        entrar_como(
            self.client, Papel.SUPERVISORA, self.condominio, "supervisora@exemplo.test"
        )
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(resposta.context["pode_gerenciar_item_edicao"])
        self.assertTrue(resposta.context["formulario_edicao"].fields["nome"].disabled)

    def test_supervisora_nao_salva(self):
        entrar_como(
            self.client, Papel.SUPERVISORA, self.condominio, "supervisora2@exemplo.test"
        )
        resposta = self.client.post(
            reverse("cadastros:editar_condominio", args=[self.condominio.pk]),
            {"nome": "Invadido"},
        )
        self.assertEqual(resposta.status_code, 403)
        self.condominio.refresh_from_db()
        self.assertEqual(self.condominio.nome, "Alpha")

    def test_leitura_nao_renderiza_botao_de_salvar(self):
        entrar_como(
            self.client, Papel.SUPERVISORA, self.condominio, "supervisora3@exemplo.test"
        )
        resposta = self.client.get(self.url)
        self.assertNotContains(resposta, "Salvar")


@override_settings(
    EITHRA_CONDOMINIO_LOGO_BUCKET="bucket-de-logos-de-teste",
    EITHRA_CONDOMINIO_LOGO_MAX_BYTES=1 * 1024 * 1024,
)
class LogoDoCondominioTests(TestCase):
    """A logo entra pela edição do condomínio — nunca por uma tela à parte.

    Adicionar, substituir e tirar são passos explícitos. Salvar o cadastro sem
    escolher arquivo mantém a logo como está; tirar é declarado, e não o efeito
    de um campo vazio.
    """

    def setUp(self):
        self.condominio = criar_condominio("Rio Vermelho Exclusive")
        self.pessoa = entrar_como(self.client, Papel.SINDICA, self.condominio)
        self.url = reverse("cadastros:editar_condominio", args=[self.condominio.pk])
        self.provider = StorageDeMentira(bucket="bucket-de-logos-de-teste")
        self.obter = patch(
            "apps.condominios.logo.obter_provider", return_value=self.provider
        )
        self.obter.start()
        self.addCleanup(self.obter.stop)

    def _salvar(self, **extra):
        dados = {"nome": self.condominio.nome}
        dados.update(extra)
        return self.client.post(self.url, dados)

    def test_adiciona_a_logo_pela_edicao(self):
        self._salvar(logo=png("marca.png"))
        self.condominio.refresh_from_db()
        self.assertTrue(self.condominio.tem_logo)
        self.assertEqual(
            self.condominio.logo_storage_key, f"logos/{self.condominio.pk}.png"
        )

    def test_substitui_a_logo_existente(self):
        self._salvar(logo=png("primeira.png"))
        self._salvar(logo=jpg("segunda.jpg"))
        self.condominio.refresh_from_db()
        self.assertEqual(
            self.condominio.logo_storage_key, f"logos/{self.condominio.pk}.jpg"
        )

    def test_salvar_sem_escolher_arquivo_mantem_a_logo(self):
        self._salvar(logo=png("marca.png"))
        self._salvar()
        self.condominio.refresh_from_db()
        self.assertTrue(self.condominio.tem_logo)

    def test_tirar_a_logo_e_declarado_e_nao_por_ausencia(self):
        self._salvar(logo=png("marca.png"))
        self._salvar(remover_logo="on")
        self.condominio.refresh_from_db()
        self.assertFalse(self.condominio.tem_logo)

    def test_arquivo_que_nao_e_imagem_e_recusado_sem_salvar(self):
        falsa = SimpleUploadedFile(
            "marca.jpg", b"isto nao e uma imagem", content_type="image/jpeg"
        )
        resposta = self._salvar(logo=falsa)
        self.assertEqual(resposta.status_code, 400)
        self.condominio.refresh_from_db()
        self.assertFalse(self.condominio.tem_logo)

    def test_condominio_sem_logo_e_identificado_pelas_iniciais(self):
        resposta = self.client.get(reverse("cadastros:acessos"))
        self.assertContains(resposta, "RVE")

    def test_o_campo_da_logo_aparece_no_modal_de_edicao(self):
        """O campo precisa estar na tela, e não só no formulário.

        Sem logo guardada e sem bucket configurado, o campo continua visível: o
        armazenamento decide o salvamento, nunca a existência do campo.
        """
        resposta = self.client.get(
            f"{reverse('cadastros:condominios')}?abrir=editar&item={self.condominio.pk}"
        )
        html = resposta.content.decode()
        self.assertIn('name="logo"', html[html.index('id="editar-condominio"'):])
        self.assertContains(resposta, "Logo do condomínio")
        self.assertContains(resposta, 'enctype="multipart/form-data"')

    def test_a_ficha_do_condominio_tambem_traz_o_campo_da_logo(self):
        """A ficha do condomínio é a outra porta do mesmo cadastro.

        Ela monta os campos na mão, e por isso precisa trazer a logo também —
        senão a mesma decisão existiria em uma tela e não na outra.
        """
        resposta = self.client.get(reverse("cadastros:condominio"))
        self.assertContains(resposta, "Logo do condomínio")
        self.assertContains(resposta, 'name="logo"')
        self.assertContains(resposta, 'enctype="multipart/form-data"')

    def test_trocar_de_condominio_troca_nome_e_logo_juntos(self):
        outro = criar_condominio("Aurora")
        criar_vinculo(self.pessoa, outro, papel=Papel.SINDICA)
        logo.definir_logo(outro, png("aurora.png"))
        self._salvar(logo=png("rve.png"))

        resposta = self.client.get(
            f"{reverse('cadastros:condominios')}?condominio={outro.pk}"
        )

        self.assertEqual(resposta.context["condominio_atual"], outro)
        self.assertIn(f"logos/{outro.pk}.png", resposta.context["logo_do_condominio"])
        self.assertNotIn(
            f"logos/{self.condominio.pk}.png", resposta.context["logo_do_condominio"]
        )


class ListaDeCondominiosTests(TestCase):
    """A grade inicial mostra só os condomínios autorizados para esta pessoa."""

    def setUp(self):
        self.alpha = criar_condominio("Alpha", cidade="Salvador", uf="BA")
        self.beta = criar_condominio("Beta", quantidade_unidades=48)
        self.gama = criar_condominio("Gama")
        self.url = reverse("cadastros:condominios")
        self.pessoa = criar_pessoa("gestora@exemplo.test", "Gestora")
        criar_vinculo(self.pessoa, self.alpha, papel=Papel.SINDICA)
        criar_vinculo(self.pessoa, self.beta, papel=Papel.SUPERVISORA)
        self.client.force_login(self.pessoa)

    def test_lista_mostra_so_os_condominios_autorizados(self):
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 200)
        nomes = [item["nome"] for item in resposta.context["condominios_lista"]]
        self.assertEqual(nomes, ["Alpha", "Beta"])
        self.assertNotContains(resposta, "Gama")

    def test_lista_indica_papel_e_nivel_de_acesso(self):
        resposta = self.client.get(self.url)
        self.assertContains(resposta, "Síndico(a)")
        self.assertContains(resposta, "Supervisão")
        self.assertContains(resposta, "Pode gerenciar")
        self.assertContains(resposta, "Somente leitura")

    def test_lista_monta_link_para_abrir_o_modal_do_condominio(self):
        resposta = self.client.get(self.url)
        links = {item["nome"]: item["url"] for item in resposta.context["condominios_lista"]}
        self.assertEqual(
            links["Alpha"],
            f"{reverse('cadastros:condominios')}?abrir=editar&item={self.alpha.pk}",
        )
        self.assertEqual(
            links["Beta"],
            f"{reverse('cadastros:condominios')}?abrir=editar&item={self.beta.pk}",
        )

    def test_gestao_ve_botao_novo_condominio(self):
        resposta = self.client.get(self.url)
        self.assertContains(resposta, "Novo condomínio")

    def test_supervisora_nao_ve_botao_novo_condominio(self):
        supervisora = criar_pessoa("supervisora@exemplo.test", "Supervisora")
        criar_vinculo(supervisora, self.alpha, papel=Papel.SUPERVISORA)
        self.client.force_login(supervisora)
        resposta = self.client.get(self.url)
        self.assertNotContains(resposta, "Novo condomínio")

    def test_cadastra_condominio_e_cria_acesso_para_quem_cadastrou(self):
        resposta = self.client.post(
            reverse("cadastros:criar_condominio"),
            {
                "nome": "  Residencial   Delta ",
                "cnpj": "12.345.678/0001-95",
                "cidade": "Salvador",
                "uf": "ba",
            },
        )
        self.assertRedirects(resposta, reverse("cadastros:condominios"))
        condominio = Condominio.objects.get(nome="Residencial Delta")
        self.assertEqual(condominio.cnpj, "12345678000195")
        self.assertEqual(condominio.uf, "BA")
        vinculo = AcessoUsuarioCondominio.objects.get(
            usuario=self.pessoa,
            condominio=condominio,
        )
        self.assertEqual(vinculo.papel, Papel.ADMIN)

    def test_supervisora_recebe_403_ao_tentar_cadastrar_condominio(self):
        supervisora = criar_pessoa("supervisora@exemplo.test", "Supervisora")
        criar_vinculo(supervisora, self.alpha, papel=Papel.SUPERVISORA)
        self.client.force_login(supervisora)
        resposta = self.client.post(
            reverse("cadastros:criar_condominio"),
            {"nome": "Novo condomínio"},
        )
        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(Condominio.objects.filter(nome="Novo condomínio").exists())

    def test_modal_edicao_abre_o_condominio_pedido(self):
        resposta = self.client.get(f"{self.url}?abrir=editar&item={self.beta.pk}")
        self.assertEqual(resposta.context["modal_aberto"], "editar")
        self.assertEqual(resposta.context["item_edicao"], self.beta)
        self.assertFalse(resposta.context["pode_gerenciar_item_edicao"])


class ContextoDoCondominioTests(TestCase):
    """Um vínculo abre direto; vários oferecem a escolha."""

    def setUp(self):
        self.alpha = criar_condominio("Alpha")
        self.beta = criar_condominio("Beta")
        self.pessoa = criar_pessoa("sindica@exemplo.test", "Síndica")
        criar_vinculo(self.pessoa, self.alpha, papel=Papel.SINDICA)
        self.client.force_login(self.pessoa)
        self.url = reverse("cadastros:locais")

    def test_um_condominio_nao_mostra_seletor(self):
        resposta = self.client.get(self.url)
        self.assertFalse(resposta.context["mostrar_seletor"])
        self.assertEqual(resposta.context["condominio_atual"], self.alpha)

    def test_dois_condominios_mostram_seletor(self):
        criar_vinculo(self.pessoa, self.beta, papel=Papel.SINDICA)
        resposta = self.client.get(self.url)
        self.assertTrue(resposta.context["mostrar_seletor"])

    def test_escolha_no_endereco_troca_o_condominio_em_vista(self):
        criar_vinculo(self.pessoa, self.beta, papel=Papel.SINDICA)
        resposta = self.client.get(f"{self.url}?condominio={self.beta.pk}")
        self.assertEqual(resposta.context["condominio_atual"], self.beta)

    def test_condominio_nao_autorizado_no_endereco_e_404(self):
        alheio = criar_condominio("Gama")
        resposta = self.client.get(f"{self.url}?condominio={alheio.pk}")
        self.assertEqual(resposta.status_code, 404)

    def test_condominio_inexistente_no_endereco_e_404(self):
        resposta = self.client.get(
            f"{self.url}?condominio=00000000-0000-4000-8000-000000000000"
        )
        self.assertEqual(resposta.status_code, 404)


class AgrupadoresDeNavegacaoTests(TestCase):
    """O menu resume contextos sem esconder o que ja existe no sistema."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        entrar_como(self.client, Papel.SINDICA, self.condominio)

    def test_pessoas_e_unidades_reune_os_atalhos_da_comunidade(self):
        resposta = self.client.get(reverse("cadastros:pessoas_e_unidades"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Unidades")
        self.assertContains(resposta, "Pessoas")
        self.assertContains(resposta, "Veículos")

    def test_tipos_e_categorias_reune_os_catalogos_existentes(self):
        resposta = self.client.get(reverse("cadastros:tipos_e_categorias"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Tipos de local")
        self.assertContains(resposta, "Tipos de equipamento")
        self.assertContains(resposta, "Especialidades de fornecedor")
        self.assertContains(resposta, "Tipos de contrato")
        self.assertContains(resposta, "Tipos de documento")


class LocaisTests(TestCase):
    """Áreas e locais: a gestão mantém, a supervisão só lê."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("cadastros:locais")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)

    def test_lista_mostra_os_locais_do_condominio(self):
        criar_local(self.condominio, nome="Bloco A")
        criar_local(criar_condominio("Beta"), nome="Bloco do vizinho")
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 200)
        nomes = [local["nome"] for local in resposta.context["locais"]]
        self.assertEqual(nomes, ["Bloco A"])

    def test_cadastra_local_guardando_quem_cadastrou(self):
        tipo = TipoLocalOperacional.objects.create(
            condominio=self.condominio, nome="Casa de máquinas"
        )
        resposta = self.client.post(
            reverse("cadastros:criar_local"),
            {
                "condominio": str(self.condominio.pk),
                "nome": "Casa de máquinas do bloco A",
                "tipo_local": str(tipo.pk),
                "descricao": "Ao lado do elevador social",
            },
        )
        self.assertRedirects(
            resposta, f"{self.url}?condominio={self.condominio.pk}"
        )
        local = LocalOperacional.objects.get(nome="Casa de máquinas do bloco A")
        self.assertEqual(local.criado_por, self.gestao)
        self.assertEqual(local.condominio, self.condominio)
        self.assertEqual(local.tipo_local, tipo)

    def test_local_sem_nome_volta_com_erro(self):
        resposta = self.client.post(
            reverse("cadastros:criar_local"),
            {"condominio": str(self.condominio.pk), "nome": "  "},
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(LocalOperacional.objects.exists())

    def test_edita_local_existente(self):
        local = criar_local(self.condominio, nome="Bloco A")
        tipo = TipoLocalOperacional.objects.create(
            condominio=self.condominio, nome="Área comum"
        )
        resposta = self.client.post(
            reverse("cadastros:editar_local", args=[local.pk]),
            {
                "condominio": str(self.condominio.pk),
                "nome": "Bloco A - reformado",
                "tipo_local": str(tipo.pk),
            },
        )
        self.assertEqual(resposta.status_code, 302)
        local.refresh_from_db()
        self.assertEqual(local.nome, "Bloco A - reformado")
        self.assertEqual(local.tipo_local, tipo)

    def test_local_de_outro_condominio_e_404_ao_editar(self):
        alheio = criar_local(criar_condominio("Beta"), nome="Bloco do vizinho")
        resposta = self.client.post(
            reverse("cadastros:editar_local", args=[alheio.pk]),
            {"condominio": str(self.condominio.pk), "nome": "Tentativa"},
        )
        self.assertEqual(resposta.status_code, 404)

    def test_alterna_local_sem_apagar(self):
        local = criar_local(self.condominio, nome="Bloco A")
        resposta = self.client.post(
            reverse("cadastros:alternar_local", args=[local.pk]),
            {"condominio": str(self.condominio.pk)},
        )
        self.assertEqual(resposta.status_code, 302)
        local.refresh_from_db()
        self.assertFalse(local.ativo)
        self.assertTrue(LocalOperacional.objects.filter(pk=local.pk).exists())

    def test_edicao_abre_o_modal_do_registro_pedido(self):
        local = criar_local(self.condominio, nome="Bloco A")
        resposta = self.client.get(
            f"{self.url}?condominio={self.condominio.pk}&abrir=editar&item={local.pk}"
        )
        self.assertEqual(resposta.context["modal_aberto"], "editar")
        self.assertEqual(resposta.context["item_edicao"], local)

    def test_supervisora_nao_cadastra(self):
        entrar_como(
            self.client, Papel.SUPERVISORA, self.condominio, "supervisora@exemplo.test"
        )
        resposta = self.client.post(
            reverse("cadastros:criar_local"),
            {"condominio": str(self.condominio.pk), "nome": "Bloco Novo"},
        )
        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(LocalOperacional.objects.exists())

    def test_supervisora_nao_ve_o_botao_de_cadastrar(self):
        entrar_como(
            self.client, Papel.SUPERVISORA, self.condominio, "supervisora2@exemplo.test"
        )
        criar_local(self.condominio, nome="Bloco A")
        resposta = self.client.get(self.url)
        self.assertNotContains(resposta, "Novo local")

    def test_lista_nao_traz_o_atalho_de_tipos_como_acao_principal(self):
        resposta = self.client.get(self.url)
        self.assertNotContains(resposta, reverse("cadastros:tipos_de_local"))


class EquipamentosTests(TestCase):
    """O local oferecido é sempre do condomínio em vista."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("cadastros:equipamentos")
        entrar_como(self.client, Papel.ADMIN, self.condominio)

    def test_cadastra_equipamento_apontando_um_local_do_condominio(self):
        local = criar_local(self.condominio, nome="Casa de máquinas")
        tipo = TipoEquipamento.objects.create(
            condominio=self.condominio, nome="Bomba"
        )
        resposta = self.client.post(
            reverse("cadastros:criar_equipamento"),
            {
                "condominio": str(self.condominio.pk),
                "nome": "Bomba de recalque",
                "tipo_equipamento": str(tipo.pk),
                "local": str(local.pk),
            },
        )
        self.assertRedirects(resposta, f"{self.url}?condominio={self.condominio.pk}")
        equipamento = Equipamento.objects.get(nome="Bomba de recalque")
        self.assertEqual(equipamento.local, local)
        self.assertEqual(equipamento.tipo_equipamento, tipo)

    def test_local_de_outro_condominio_e_recusado(self):
        alheio = criar_local(criar_condominio("Beta"), nome="Bloco do vizinho")
        resposta = self.client.post(
            reverse("cadastros:criar_equipamento"),
            {
                "condominio": str(self.condominio.pk),
                "nome": "Portão",
                "local": str(alheio.pk),
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(Equipamento.objects.exists())

    def test_alterna_equipamento(self):
        equipamento = Equipamento.objects.create(
            condominio=self.condominio, nome="Portão"
        )
        self.client.post(
            reverse("cadastros:alternar_equipamento", args=[equipamento.pk]),
            {"condominio": str(self.condominio.pk)},
        )
        equipamento.refresh_from_db()
        self.assertFalse(equipamento.ativo)

    def test_lista_nao_traz_o_atalho_de_tipos_como_acao_principal(self):
        resposta = self.client.get(self.url)
        self.assertNotContains(resposta, reverse("cadastros:tipos_de_equipamento"))


class FornecedoresTests(TestCase):
    """O documento identifica o fornecedor dentro do condomínio."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("cadastros:fornecedores")
        entrar_como(self.client, Papel.SINDICA, self.condominio)

    def test_cadastra_fornecedor(self):
        resposta = self.client.post(
            reverse("cadastros:criar_fornecedor"),
            {
                "condominio": str(self.condominio.pk),
                "nome_razao_social": "Elevadores Silva Ltda.",
                "tipo_pessoa": "pj",
                "documento": "12.345.678/0001-95",
                "telefone": "(71) 99999-8888",
            },
        )
        self.assertRedirects(resposta, f"{self.url}?condominio={self.condominio.pk}")
        fornecedor = Fornecedor.objects.get(nome_razao_social="Elevadores Silva Ltda.")
        self.assertEqual(fornecedor.documento, "12345678000195")

    def test_documento_repetido_no_condominio_e_recusado(self):
        Fornecedor.objects.create(
            condominio=self.condominio,
            nome_razao_social="Elevadores Silva Ltda.",
            documento="12345678000195",
        )
        resposta = self.client.post(
            reverse("cadastros:criar_fornecedor"),
            {
                "condominio": str(self.condominio.pk),
                "nome_razao_social": "Outra empresa",
                "tipo_pessoa": "pj",
                "documento": "12.345.678/0001-95",
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(Fornecedor.objects.count(), 1)

    def test_lista_mostra_documento_formatado(self):
        Fornecedor.objects.create(
            condominio=self.condominio,
            nome_razao_social="Elevadores Silva Ltda.",
            documento="12345678000195",
        )
        resposta = self.client.get(self.url)
        self.assertContains(resposta, "12.345.678/0001-95")

    def test_lista_nao_traz_especialidades_como_acao_principal(self):
        resposta = self.client.get(self.url)
        self.assertNotContains(resposta, reverse("cadastros:especialidades"))


class MenuTests(TestCase):
    """O grupo Cadastros existe para quem enxerga o módulo, e some para o resto."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")

    def _menu(self):
        resposta = self.client.get(reverse("core:home"))
        return resposta.context["navigation"]

    def test_grupo_cadastros_traz_os_dez_itens_contextuais(self):
        entrar_como(self.client, Papel.SINDICA, self.condominio)
        grupo = next(item for item in self._menu() if item["label"] == "Cadastros")
        self.assertEqual(
            [filho["label"] for filho in grupo["filhos"]],
            [
                "Condomínios",
                "Pessoas e unidades",
                "Usuários e acessos",
                "Áreas e locais",
                "Equipamentos",
                "Fornecedores",
                "Contratos",
                "Garantias",
                "Documentos",
                "Tipos e categorias",
            ],
        )

    def test_grupo_abre_na_lista_de_condominios(self):
        entrar_como(self.client, Papel.SINDICA, self.condominio)
        resposta = self.client.get(reverse("cadastros:condominios"))
        grupo = next(
            item for item in resposta.context["navigation"] if item["label"] == "Cadastros"
        )
        self.assertTrue(grupo["ativo"])
        self.assertTrue(
            next(filho for filho in grupo["filhos"] if filho["label"] == "Condomínios")[
                "ativo"
            ]
        )

    def test_grupo_abre_quando_uma_tela_dele_esta_a_vista(self):
        entrar_como(self.client, Papel.SINDICA, self.condominio)
        resposta = self.client.get(reverse("cadastros:locais"))
        grupo = next(
            item for item in resposta.context["navigation"] if item["label"] == "Cadastros"
        )
        self.assertTrue(grupo["ativo"])
        self.assertTrue(
            next(filho for filho in grupo["filhos"] if filho["label"] == "Áreas e locais")[
                "ativo"
            ]
        )

    def test_grupo_marca_pessoas_e_unidades_quando_a_comunidade_esta_aberta(self):
        entrar_como(self.client, Papel.SINDICA, self.condominio)
        resposta = self.client.get(reverse("comunidade:pessoas"))
        grupo = next(
            item for item in resposta.context["navigation"] if item["label"] == "Cadastros"
        )
        self.assertTrue(
            next(
                filho
                for filho in grupo["filhos"]
                if filho["label"] == "Pessoas e unidades"
            )["ativo"]
        )

    def test_grupo_marca_tipos_e_categorias_quando_um_catalogo_esta_aberto(self):
        entrar_como(self.client, Papel.SINDICA, self.condominio)
        resposta = self.client.get(reverse("cadastros:tipos_de_local"))
        grupo = next(
            item for item in resposta.context["navigation"] if item["label"] == "Cadastros"
        )
        self.assertTrue(
            next(
                filho
                for filho in grupo["filhos"]
                if filho["label"] == "Tipos e categorias"
            )["ativo"]
        )

    def test_operacional_nao_recebe_o_grupo(self):
        entrar_como(self.client, Papel.OPERACIONAL, self.condominio)
        self.assertNotIn("Cadastros", [item["label"] for item in self._menu()])
