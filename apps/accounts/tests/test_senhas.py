"""Os fluxos de senha do próprio usuário.

Uma regra só sustenta este arquivo: ninguém define a senha de outra pessoa.
Cada conta define a sua no primeiro acesso, redefine quando esquece e troca
quando já está dentro — sempre pela própria mão, sempre por um endereço que
vale para quem o recebe.

O envio é exercitado com o backend de teste do Django (memória), e não com
SMTP: o que se confere aqui é o endereço e o seu efeito, não a entrega externa.
Um caso à parte cobre o backend de console, onde a tela não pode prometer um
e-mail que não saiu.
"""
import io
import re
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from apps.accounts import primeiro_acesso

Usuario = get_user_model()

SENHA = "umaSenhaBoa2026"
OUTRA_SENHA = "outraSenhaBoa2026"

ENDERECO_DE_CONSOLE = "django.core.mail.backends.console.EmailBackend"
RECADO_NEUTRO = "Se houver uma conta com esse e-mail, enviaremos as instruções."


def conta_sem_senha(email="nova@exemplo.test", nome="Pessoa Nova"):
    """Uma conta como a que a tela de acessos cria: sem senha utilizável."""
    pessoa = Usuario(email=email, full_name=nome)
    pessoa.set_unusable_password()
    pessoa.save()
    return pessoa


def pessoa_com_senha(email="entrada@exemplo.test", senha=SENHA, nome="Pessoa de Dentro"):
    """Uma conta que já entra no sistema."""
    return Usuario.objects.create_user(email=email, password=senha, full_name=nome)


def caminho_do_ultimo_email() -> str:
    """O caminho do endereço de senha que saiu no último e-mail."""
    corpo = mail.outbox[-1].body
    encontrado = re.search(r"(https?://\S*primeiro-acesso/\S+)", corpo)
    return urlparse(encontrado.group(1)).path


class PrimeiroAcessoTests(TestCase):
    """Quem ainda não entrou nunca define a própria senha pelo endereço."""

    def setUp(self):
        self.pessoa = conta_sem_senha()
        endereco = primeiro_acesso.endereco_de_primeiro_acesso(
            conta=self.pessoa, request=RequestFactory().get("/")
        )
        self.caminho = urlparse(endereco).path

    def test_conta_nova_nao_tem_senha_utilizavel(self):
        self.assertFalse(self.pessoa.has_usable_password())
        self.assertFalse(
            self.client.login(username=self.pessoa.email, password=SENHA)
        )

    def test_endereco_valido_abre_a_definicao_de_senha(self):
        resposta = self.client.get(self.caminho)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["conta"], self.pessoa)
        self.assertTrue(resposta.context["primeira_senha"])
        self.assertContains(resposta, "Defina sua senha")

    def test_a_pessoa_define_a_sua_propria_senha(self):
        resposta = self.client.post(
            self.caminho, {"senha": SENHA, "confirmacao": SENHA}
        )

        self.assertRedirects(resposta, reverse("accounts:login"))
        self.pessoa.refresh_from_db()
        self.assertTrue(self.pessoa.check_password(SENHA))

    def test_senha_definida_permite_entrar(self):
        self.client.post(self.caminho, {"senha": SENHA, "confirmacao": SENHA})

        entrada = self.client.post(
            reverse("accounts:login"),
            {"username": self.pessoa.email, "password": SENHA},
        )
        self.assertRedirects(entrada, reverse("core:home"))

    def test_o_endereco_vale_uma_vez(self):
        self.client.post(self.caminho, {"senha": SENHA, "confirmacao": SENHA})

        self.assertEqual(self.client.get(self.caminho).status_code, 410)

    def test_endereco_com_token_adulterado_nao_define_senha(self):
        base = self.caminho.rstrip("/")
        trocado = "a" if base[-1] != "a" else "b"
        resposta = self.client.post(
            base[:-1] + trocado + "/", {"senha": SENHA, "confirmacao": SENHA}
        )

        self.assertEqual(resposta.status_code, 410)
        self.pessoa.refresh_from_db()
        self.assertFalse(self.pessoa.has_usable_password())

    def test_endereco_vencido_nao_define_senha(self):
        depois_do_prazo = datetime.now() + timedelta(
            days=primeiro_acesso.prazo_em_dias() + 1
        )

        with patch.object(
            default_token_generator, "_now", return_value=depois_do_prazo
        ):
            negado = self.client.get(self.caminho)
            resposta = self.client.post(
                self.caminho, {"senha": SENHA, "confirmacao": SENHA}
            )

        self.assertEqual(negado.status_code, 410)
        self.assertEqual(resposta.status_code, 410)
        self.pessoa.refresh_from_db()
        self.assertFalse(self.pessoa.has_usable_password())

    def test_senha_digitada_nao_volta_para_a_tela(self):
        resposta = self.client.post(
            self.caminho, {"senha": SENHA, "confirmacao": SENHA + "-outra"}
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, SENHA)
        self.pessoa.refresh_from_db()
        self.assertFalse(self.pessoa.has_usable_password())

    def test_definir_a_propria_senha_nao_amplia_permissao(self):
        self.client.post(self.caminho, {"senha": SENHA, "confirmacao": SENHA})

        self.pessoa.refresh_from_db()
        self.assertFalse(self.pessoa.is_staff)
        self.assertFalse(self.pessoa.is_superuser)
        self.assertEqual(self.pessoa.acessos_condominio.count(), 0)


class EsqueciMinhaSenhaTests(TestCase):
    """"Esqueci minha senha": o pedido do próprio usuário, sem revelar quem tem conta."""

    def setUp(self):
        self.url = reverse("accounts:esqueci_senha")
        self.pessoa = pessoa_com_senha()

    def test_a_tela_de_pedido_e_publica(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_conta_existente_recebe_as_instrucoes(self):
        resposta = self.client.post(
            self.url, {"email": self.pessoa.email}, follow=True
        )

        self.assertContains(resposta, RECADO_NEUTRO)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("primeiro-acesso", mail.outbox[0].body)
        self.assertIn(self.pessoa.email, mail.outbox[0].to)

    def test_email_desconhecido_recebe_a_mesma_resposta(self):
        resposta = self.client.post(
            self.url, {"email": "ninguem@exemplo.test"}, follow=True
        )

        self.assertContains(resposta, RECADO_NEUTRO)
        self.assertEqual(len(mail.outbox), 0)

    def test_conta_desativada_nao_recebe_instrucoes_nem_denuncia(self):
        self.pessoa.is_active = False
        self.pessoa.save(update_fields=["is_active"])

        resposta = self.client.post(
            self.url, {"email": self.pessoa.email}, follow=True
        )

        self.assertContains(resposta, RECADO_NEUTRO)
        self.assertEqual(len(mail.outbox), 0)

    def test_o_endereco_recebido_troca_a_senha_e_aposenta_a_anterior(self):
        self.client.post(self.url, {"email": self.pessoa.email})
        caminho = caminho_do_ultimo_email()

        self.client.logout()
        resposta = self.client.post(
            caminho, {"senha": OUTRA_SENHA, "confirmacao": OUTRA_SENHA}
        )

        self.assertRedirects(resposta, reverse("accounts:login"))
        self.pessoa.refresh_from_db()
        self.assertTrue(self.pessoa.check_password(OUTRA_SENHA))
        self.assertFalse(self.pessoa.check_password(SENHA))
        self.assertFalse(
            self.client.login(username=self.pessoa.email, password=SENHA)
        )

    def test_o_endereco_de_redefinicao_tambem_vale_uma_vez(self):
        self.client.post(self.url, {"email": self.pessoa.email})
        caminho = caminho_do_ultimo_email()
        self.client.post(caminho, {"senha": OUTRA_SENHA, "confirmacao": OUTRA_SENHA})

        self.assertEqual(self.client.get(caminho).status_code, 410)

    @override_settings(EMAIL_BACKEND=ENDERECO_DE_CONSOLE)
    def test_ambiente_de_console_avisa_onde_estao_as_instrucoes(self):
        with redirect_stdout(io.StringIO()):
            resposta = self.client.post(
                self.url, {"email": self.pessoa.email}, follow=True
            )

        self.assertContains(resposta, RECADO_NEUTRO)
        self.assertContains(resposta, "console do servidor")
        self.assertEqual(len(mail.outbox), 0)


class AlterarSenhaAutenticadaTests(TestCase):
    """Quem já está dentro troca a própria senha, e só a própria."""

    def setUp(self):
        self.url = reverse("accounts:alterar_senha")
        self.pessoa = pessoa_com_senha()
        self.client.force_login(self.pessoa)

    def test_a_tela_exige_estar_dentro(self):
        self.client.logout()
        resposta = self.client.get(self.url)
        self.assertEqual(resposta.status_code, 302)
        self.assertIn(reverse("accounts:login"), resposta["Location"])

    def test_quem_esta_dentro_troca_a_propria_senha(self):
        resposta = self.client.post(
            self.url,
            {
                "old_password": SENHA,
                "new_password1": OUTRA_SENHA,
                "new_password2": OUTRA_SENHA,
            },
            follow=True,
        )

        self.assertContains(resposta, "Sua senha foi alterada.")
        self.assertIn("_auth_user_id", self.client.session)
        self.pessoa.refresh_from_db()
        self.assertTrue(self.pessoa.check_password(OUTRA_SENHA))
        self.assertFalse(self.pessoa.check_password(SENHA))

    def test_a_senha_de_agora_e_exigida(self):
        resposta = self.client.post(
            self.url,
            {
                "old_password": "nao-e-a-minha-senha",
                "new_password1": OUTRA_SENHA,
                "new_password2": OUTRA_SENHA,
            },
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "A senha atual não confere.")
        self.pessoa.refresh_from_db()
        self.assertTrue(self.pessoa.check_password(SENHA))

    def test_a_senha_digitada_nao_aparece_na_tela(self):
        resposta = self.client.post(
            self.url,
            {
                "old_password": "nao-e-a-minha-senha",
                "new_password1": OUTRA_SENHA,
                "new_password2": OUTRA_SENHA,
            },
        )

        self.assertNotContains(resposta, OUTRA_SENHA)
        self.assertNotContains(resposta, "pbkdf2")

    def test_a_alteracao_nao_depende_de_condominio_nenhum(self):
        """A conta é da pessoa: trocar a senha não cria nem amplia vínculo."""
        self.client.post(
            self.url,
            {
                "old_password": SENHA,
                "new_password1": OUTRA_SENHA,
                "new_password2": OUTRA_SENHA,
            },
        )

        self.pessoa.refresh_from_db()
        self.assertEqual(self.pessoa.acessos_condominio.count(), 0)
        self.assertFalse(self.pessoa.is_staff)
