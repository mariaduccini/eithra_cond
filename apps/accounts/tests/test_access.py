"""Testes de entrada e saída do sistema."""
import uuid

from django.conf import settings
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User


class LoginTests(TestCase):
    """Entrada com e-mail, sem depender da caixa digitada."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="Sindico@Exemplo.com.br",
            password="senha-forte-123",
            full_name="Maria Souza",
        )

    def test_email_fica_guardado_em_minusculas(self):
        self.assertEqual(self.user.email, "sindico@exemplo.com.br")

    def test_entra_ignorando_maiusculas_do_email(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "SINDICO@EXEMPLO.COM.BR", "password": "senha-forte-123"},
        )
        self.assertRedirects(response, reverse("core:home"))

    def test_senha_errada_mostra_mensagem_humana(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "sindico@exemplo.com.br", "password": "senha-errada"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "Não encontramos uma conta com esse e-mail e senha."
        )

    def test_conta_desativada_e_recusada_sem_revelar_existencia(self):
        """A recusa acontece na fronteira do backend e não revela que a conta existe."""
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.client.post(
            reverse("accounts:login"),
            {"username": "sindico@exemplo.com.br", "password": "senha-forte-123"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "Não encontramos uma conta com esse e-mail e senha."
        )

    def test_quem_ja_entrou_nao_ve_o_formulario(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("accounts:login"))
        self.assertRedirects(response, reverse("core:home"))

    def test_sair_encerra_a_sessao(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("accounts:logout"))
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertNotIn("_auth_user_id", self.client.session)


class UserModelTests(TestCase):
    """O modelo de pessoa do Eithra, não o usuário padrão do Django."""

    def test_auth_user_model_aponta_para_contas(self):
        self.assertEqual(settings.AUTH_USER_MODEL, "accounts.User")

    def test_identificador_e_email_sem_campo_username(self):
        self.assertEqual(User.USERNAME_FIELD, "email")
        self.assertNotIn("username", {field.name for field in User._meta.fields})

    def test_identificador_e_uuid(self):
        user = User.objects.create_user(
            email="teste@exemplo.com.br", password="senha-forte-123", full_name="Ana Ré"
        )
        self.assertIsInstance(user.pk, uuid.UUID)

    def test_email_duplicado_e_recusado(self):
        User.objects.create_user(
            email="igual@exemplo.com.br", password="senha-forte-123", full_name="Ana Ré"
        )
        with self.assertRaises(IntegrityError):
            User.objects.create_user(
                email="igual@exemplo.com.br",
                password="senha-forte-123",
                full_name="Outra Pessoa",
            )

    def test_pessoa_guarda_apenas_identidade_e_autenticacao(self):
        """O que a pessoa faz fica no vínculo com o condomínio, não aqui."""
        nomes = {campo.name for campo in User._meta.fields}
        self.assertNotIn("role", nomes)
        self.assertNotIn("papel", nomes)
