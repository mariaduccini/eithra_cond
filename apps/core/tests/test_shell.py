"""Testes do núcleo visual: menu, página inicial e páginas de erro."""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.condominios.constants import Papel
from apps.portaria.tests.apoio import criar_condominio, criar_vinculo


class HomeTests(TestCase):
    """A página inicial não exige nada do usuário além da entrada."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="morador@exemplo.com.br",
            password="senha-forte-123",
            full_name="João Lima",
        )

    def test_exige_entrada(self):
        response = self.client.get(reverse("core:home"))
        self.assertRedirects(
            response,
            f"{reverse('accounts:login')}?next={reverse('core:home')}",
        )

    def test_protecao_nao_depende_do_menu(self):
        """A rota continua protegida mesmo sem nenhum item de menu.

        Garante que o controle de acesso vive na view/backend e não no
        desenho da navegação.
        """
        with patch("apps.core.context_processors.NAVIGATION", []):
            response = self.client.get(reverse("core:home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response["Location"])

    def test_mostra_saudacao(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, "João")

    def test_nao_exibe_dashboard_ficticio(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:home"))
        self.assertNotContains(response, "demo")
        self.assertContains(response, "Tudo em ordem por aqui.")

    def test_menu_traz_apenas_itens_reais(self):
        """Todo item oferecido leva a uma tela que existe de verdade.

        Quem não responde por balanço não recebe o item administrativo no menu.
        """
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, reverse("core:home"))
        self.assertNotContains(response, reverse("balancos:importacoes"))
        self.assertEqual(len(response.context["navigation"]), 1)
        self.assertEqual(
            [item["label"] for item in response.context["navigation"]], ["Painel"]
        )

    def test_menu_da_conta_identifica_a_pessoa_sem_papel_global(self):
        """O papel é do vínculo; o menu da conta mostra identidade, não papel."""
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, "João Lima")
        self.assertContains(response, "morador@exemplo.com.br")

    def test_menu_do_porteiro_exibe_portaria(self):
        condominio = criar_condominio()
        criar_vinculo(self.user, condominio, papel=Papel.PORTEIRO)
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, reverse("portaria:painel"))


class MenuEstreitoTests(TestCase):
    """A barra abre e fecha em tela estreita sem o main.js do Sneat.

    O tema entrega o estado (`layout-menu-expanded`) e a cortina; o shell só
    responde ao clique. Estes testes travam esse contrato.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="morador@exemplo.com.br",
            password="senha-forte-123",
            full_name="João Lima",
        )
        self.client.force_login(self.user)

    def _home(self):
        return self.client.get(reverse("core:home"))

    def _botao_do_menu(self, resposta):
        """O trecho do botão que abre a barra na barra de navegação."""
        html = resposta.content.decode()
        inicio = html.index('class="layout-menu-toggle navbar-nav')
        return html[inicio : html.index("</div>", inicio)]

    def _script_do_shell(self, resposta):
        """O bloco de script do shell, onde o clique é ligado."""
        html = resposta.content.decode()
        return html[html.index("DOMContentLoaded") :]

    def test_botao_do_menu_estreito_e_acessivel(self):
        botao = self._botao_do_menu(self._home())
        self.assertIn('aria-label="Abrir menu"', botao)
        self.assertIn('aria-controls="layout-menu"', botao)
        self.assertIn('aria-expanded="false"', botao)

    def test_o_shell_usa_o_estado_que_o_tema_preve(self):
        """Abre e fecha pela classe de layout do Sneat, não por uma nova."""
        self.assertIn("layout-menu-expanded", self._script_do_shell(self._home()))

    def test_a_cortina_fecha_a_barra(self):
        script = self._script_do_shell(self._home())
        self.assertIn('document.querySelector(".layout-overlay")', script)

    def test_o_escape_fecha_a_barra(self):
        self.assertIn('"Escape"', self._script_do_shell(self._home()))

    def test_nao_depende_do_main_js_do_sneat(self):
        self.assertNotContains(self._home(), "vendor/sneat/js/main.js")

    def test_o_desktop_nao_entra_no_estado_de_mobile(self):
        """O recolhido de desktop continua fora do alcance deste clique."""
        self.assertNotContains(self._home(), "layout-menu-collapsed")


class HeaderCompactoTests(TestCase):
    """O topo do shell é uma barra de navegação, não um bloco de conteúdo.

    A altura e o respiro do cabeçalho são decididos na composição (CSS). Estes
    testes travam o que a compactação não pode levar junto: a estrutura da
    barra, a identificação de quem está usando o sistema e os caminhos que ela
    oferece.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="morador@exemplo.com.br",
            password="senha-forte-123",
            full_name="João Lima",
        )
        self.client.force_login(self.user)

    def _home(self):
        return self.client.get(reverse("core:home"))

    def test_o_cabecalho_continua_sendo_a_barra_de_navegacao(self):
        resposta = self._home()
        self.assertContains(resposta, 'id="layout-navbar"')
        self.assertContains(resposta, "layout-navbar")

    def test_a_pessoa_continua_identificada_no_cabecalho(self):
        resposta = self._home()
        self.assertContains(resposta, 'aria-label="Menu da conta"')
        self.assertContains(resposta, "João Lima")
        self.assertContains(resposta, "morador@exemplo.com.br")

    def test_os_caminhos_do_cabecalho_continuam_disponiveis(self):
        """Compactar é espaçamento; nenhuma navegação foi retirada."""
        resposta = self._home()
        self.assertContains(resposta, reverse("accounts:alterar_senha"))
        self.assertContains(resposta, reverse("accounts:logout"))

    def test_o_botao_do_menu_estreito_continua_no_cabecalho(self):
        resposta = self._home()
        self.assertContains(resposta, 'aria-controls="layout-menu"')


class ErrorPageTests(TestCase):
    """As falhas mantêm a identidade e não expõem detalhes técnicos."""

    def test_pagina_inexistente_usa_o_shell_visual(self):
        response = self.client.get("/endereco-que-nao-existe/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(
            response, "Não encontramos esta página", status_code=404
        )
        self.assertContains(response, "Ir para o início", status_code=404)
