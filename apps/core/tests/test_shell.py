"""Testes do núcleo visual: menu, página inicial e páginas de erro."""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User


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
        self.assertContains(response, "Nada precisa da sua atenção agora")

    def test_menu_traz_apenas_itens_reais(self):
        """Todo item aponta para uma tela que existe de verdade."""
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, reverse("core:home"))
        self.assertContains(response, reverse("balancos:importacoes"))
        self.assertEqual(len(response.context["navigation"]), 2)

    def test_menu_da_conta_identifica_a_pessoa_sem_papel_global(self):
        """O papel é do vínculo; o menu da conta mostra identidade, não papel."""
        self.client.force_login(self.user)
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, "João Lima")
        self.assertContains(response, "morador@exemplo.com.br")


class ErrorPageTests(TestCase):
    """As falhas mantêm a identidade e não expõem detalhes técnicos."""

    def test_pagina_inexistente_usa_o_shell_visual(self):
        response = self.client.get("/endereco-que-nao-existe/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(
            response, "Não encontramos esta página", status_code=404
        )
        self.assertContains(response, "Ir para o início", status_code=404)
