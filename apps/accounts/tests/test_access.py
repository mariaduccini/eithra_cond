"""Testes de entrada e saída do sistema."""
import re
import uuid

from django.conf import settings
from django.db import IntegrityError
from django.test import (
    Client,
    RequestFactory,
    SimpleTestCase,
    TestCase,
    override_settings,
)
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


class AcessoRemotoPorProxyTests(TestCase):
    """Entrada pelo túnel HTTPS (Cloudflare Quick Tunnel), como no celular.

    O túnel termina o TLS e reapresenta a requisição ao servidor local por HTTP,
    anunciando o esquema original no cabeçalho ``X-Forwarded-Proto``. O que
    autoriza a origem é a configuração: a origem do túnel entra em
    ``CSRF_TRUSTED_ORIGINS`` e o cabeçalho do proxy em ``SECURE_PROXY_SSL_HEADER``.
    Sem os dois, a autenticação remota não fecha com o esquema certo.
    """

    HOST_DO_TUNEL = "eithra-teste.trycloudflare.com"

    def setUp(self):
        self.user = User.objects.create_user(
            email="sindico@exemplo.com.br",
            password="senha-forte-123",
            full_name="Maria Souza",
        )

    def _cabecalhos_do_tunel(self) -> dict:
        origem = f"https://{self.HOST_DO_TUNEL}"
        return {
            "HTTP_HOST": self.HOST_DO_TUNEL,
            "HTTP_ORIGIN": origem,
            "HTTP_REFERER": f"{origem}/conta/entrar/",
            "HTTP_X_FORWARDED_PROTO": "https",
            "secure": True,
        }

    @override_settings(
        ALLOWED_HOSTS=["*"],
        CSRF_TRUSTED_ORIGINS=["https://*.trycloudflare.com"],
        SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    )
    def test_login_conclui_pelo_tunel_com_csrf_verificado(self):
        """A entrada remota passa pela verificação de CSRF e abre a sessão."""
        cliente = Client(enforce_csrf_checks=True)
        cabecalhos = self._cabecalhos_do_tunel()
        pagina = cliente.get(reverse("accounts:login"), **cabecalhos)
        token = re.search(
            r'name="csrfmiddlewaretoken" value="([^"]+)"',
            pagina.content.decode(),
        ).group(1)

        resposta = cliente.post(
            reverse("accounts:login"),
            {
                "username": "sindico@exemplo.com.br",
                "password": "senha-forte-123",
                "csrfmiddlewaretoken": token,
            },
            **cabecalhos,
        )

        self.assertRedirects(resposta, reverse("core:home"))
        self.assertIn("_auth_user_id", cliente.session)


class ConfiguracaoDoAcessoRemotoTests(SimpleTestCase):
    """A configuração de desenvolvimento que sustenta o túnel HTTPS.

    Este é o teste de regressão do ajuste: se ``SECURE_PROXY_SSL_HEADER`` sair do
    development, ``request.is_secure()`` volta a ser falso atrás do túnel e os
    endereços absolutos (links de primeiro acesso, redirecionamentos) saem em
    ``http`` — exatamente o que impedia o fluxo remoto de fechar.
    """

    CABECALHO_DO_PROXY = ("HTTP_X_FORWARDED_PROTO", "https")

    @override_settings(SECURE_PROXY_SSL_HEADER=CABECALHO_DO_PROXY)
    def test_requisicao_do_tunel_e_reconhecida_como_segura(self):
        requisicao = RequestFactory().get("/", HTTP_X_FORWARDED_PROTO="https")

        self.assertTrue(requisicao.is_secure())
        self.assertTrue(
            requisicao.build_absolute_uri("/conta/entrar/").startswith("https://")
        )

    @override_settings(SECURE_PROXY_SSL_HEADER=None)
    def test_sem_a_configuracao_do_proxy_a_requisicao_nao_e_segura(self):
        """Sem a setting, o Django trata a requisição do túnel como http."""
        requisicao = RequestFactory().get("/", HTTP_X_FORWARDED_PROTO="https")

        self.assertFalse(requisicao.is_secure())

    def test_development_aceita_o_tunel_e_confia_no_proxy(self):
        from config.settings import development

        self.assertIn(
            "https://*.trycloudflare.com", development.CSRF_TRUSTED_ORIGINS
        )
        self.assertEqual(
            development.SECURE_PROXY_SSL_HEADER,
            ("HTTP_X_FORWARDED_PROTO", "https"),
        )

    def test_o_tunel_e_liberado_apenas_no_development(self):
        """Produção herda a configuração comum, que não conhece o túnel."""
        from config.settings import base, development

        self.assertNotIn(
            "https://*.trycloudflare.com",
            getattr(base, "CSRF_TRUSTED_ORIGINS", []),
        )
        self.assertIn("https://*.trycloudflare.com", development.CSRF_TRUSTED_ORIGINS)
