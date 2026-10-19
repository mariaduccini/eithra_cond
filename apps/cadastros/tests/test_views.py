"""Telas dos cadastros: quem entra, o que vê e o que consegue mudar."""
from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Equipamento, Fornecedor, LocalOperacional
from apps.cadastros.tests.apoio import (
    criar_condominio,
    criar_local,
    criar_pessoa,
    criar_vinculo,
    entrar_como,
)
from apps.condominios.constants import Papel
from apps.condominios.models import Condominio


class AcessoAsTelasTests(TestCase):
    """Sem vínculo que enxergue o módulo, a resposta é 404."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.telas = (
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
    """O cadastro do condomínio: leitura para a supervisão, escrita para a gestão."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("cadastros:condominio")

    def test_gestao_ve_os_dados_do_condominio(self):
        entrar_como(self.client, Papel.SINDICA, self.condominio)
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.context["pode_gerenciar"])
        self.assertContains(resposta, "Alpha")

    def test_gestao_salva_o_cadastro_normalizado(self):
        entrar_como(self.client, Papel.SINDICA, self.condominio)
        resposta = self.client.post(
            self.url,
            {
                "condominio": str(self.condominio.pk),
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
        self.assertRedirects(
            resposta, f"{self.url}?condominio={self.condominio.pk}"
        )
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
            self.url,
            {"condominio": str(self.condominio.pk), "nome": "Alpha", "cnpj": "123"},
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
        self.assertFalse(resposta.context["pode_gerenciar"])
        self.assertTrue(resposta.context["formulario"].fields["nome"].disabled)

    def test_supervisora_nao_salva(self):
        entrar_como(
            self.client, Papel.SUPERVISORA, self.condominio, "supervisora2@exemplo.test"
        )
        resposta = self.client.post(
            self.url, {"condominio": str(self.condominio.pk), "nome": "Invadido"}
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
        resposta = self.client.post(
            reverse("cadastros:criar_local"),
            {
                "condominio": str(self.condominio.pk),
                "nome": "Casa de máquinas",
                "tipo": "casa_de_maquinas",
                "descricao": "Ao lado do elevador social",
            },
        )
        self.assertRedirects(
            resposta, f"{self.url}?condominio={self.condominio.pk}"
        )
        local = LocalOperacional.objects.get(nome="Casa de máquinas")
        self.assertEqual(local.criado_por, self.gestao)
        self.assertEqual(local.condominio, self.condominio)

    def test_local_sem_nome_volta_com_erro(self):
        resposta = self.client.post(
            reverse("cadastros:criar_local"),
            {"condominio": str(self.condominio.pk), "nome": "  "},
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(LocalOperacional.objects.exists())

    def test_edita_local_existente(self):
        local = criar_local(self.condominio, nome="Bloco A")
        resposta = self.client.post(
            reverse("cadastros:editar_local", args=[local.pk]),
            {
                "condominio": str(self.condominio.pk),
                "nome": "Bloco A - reformado",
                "tipo": "area_comum",
            },
        )
        self.assertEqual(resposta.status_code, 302)
        local.refresh_from_db()
        self.assertEqual(local.nome, "Bloco A - reformado")

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


class EquipamentosTests(TestCase):
    """O local oferecido é sempre do condomínio em vista."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("cadastros:equipamentos")
        entrar_como(self.client, Papel.ADMIN, self.condominio)

    def test_cadastra_equipamento_apontando_um_local_do_condominio(self):
        local = criar_local(self.condominio, nome="Casa de máquinas")
        resposta = self.client.post(
            reverse("cadastros:criar_equipamento"),
            {
                "condominio": str(self.condominio.pk),
                "nome": "Bomba de recalque",
                "tipo_texto": "Bomba",
                "local": str(local.pk),
            },
        )
        self.assertRedirects(resposta, f"{self.url}?condominio={self.condominio.pk}")
        equipamento = Equipamento.objects.get(nome="Bomba de recalque")
        self.assertEqual(equipamento.local, local)

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


class MenuTests(TestCase):
    """O grupo Cadastros existe para quem enxerga o módulo, e some para o resto."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")

    def _menu(self):
        resposta = self.client.get(reverse("core:home"))
        return resposta.context["navigation"]

    def test_grupo_cadastros_traz_os_cinco_cadastros(self):
        entrar_como(self.client, Papel.SINDICA, self.condominio)
        grupo = next(item for item in self._menu() if item["label"] == "Cadastros")
        self.assertEqual(
            [filho["label"] for filho in grupo["filhos"]],
            [
                "Condomínio",
                "Usuários e acessos",
                "Áreas e locais",
                "Equipamentos",
                "Fornecedores",
            ],
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

    def test_operacional_nao_recebe_o_grupo(self):
        entrar_como(self.client, Papel.OPERACIONAL, self.condominio)
        self.assertNotIn("Cadastros", [item["label"] for item in self._menu()])
