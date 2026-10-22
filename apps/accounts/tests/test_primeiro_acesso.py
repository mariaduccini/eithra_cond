"""O primeiro acesso: a própria pessoa define a sua senha.

Quem concede o acesso cria a conta, e não escolhe a senha de ninguém. O endereço
entregue pela gestão é a única credencial de quem ainda não entrou: vale uma vez,
tem prazo e deixa de funcionar assim que a senha existe. É por ele que a conta
criada na tela de acessos sai do papel e passa a entrar no sistema.
"""
from urllib.parse import urlparse

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.accounts import primeiro_acesso

Usuario = get_user_model()

SENHA = "umaSenhaBoa2026"


def conta_sem_senha(email="nova@exemplo.test", nome="Pessoa Nova"):
    """Uma conta como a que a tela de acessos cria: sem senha utilizável."""
    pessoa = Usuario(email=email, full_name=nome)
    pessoa.set_unusable_password()
    pessoa.save()
    return pessoa


def caminho_de(pessoa) -> str:
    """O caminho do endereço que a gestão copiaria e entregaria à pessoa."""
    endereco = primeiro_acesso.endereco_de_primeiro_acesso(
        conta=pessoa, request=RequestFactory().get("/")
    )
    return urlparse(endereco).path


class OQueOEnderecoValeTests(TestCase):
    """As perguntas que a tela e o domínio fazem sobre a conta."""

    def test_conta_criada_pela_gestao_ainda_nao_tem_senha(self):
        self.assertTrue(primeiro_acesso.precisa_definir_senha(conta_sem_senha()))

    def test_quem_ja_tem_senha_nao_precisa_de_endereco(self):
        pessoa = Usuario.objects.create_user(
            email="pronta@exemplo.test", password=SENHA, full_name="Pessoa Pronta"
        )
        self.assertFalse(primeiro_acesso.precisa_definir_senha(pessoa))

    def test_o_prazo_do_endereco_e_contado_em_dias(self):
        self.assertGreaterEqual(primeiro_acesso.prazo_em_dias(), 1)


class DefinirSenhaTests(TestCase):
    """A tela onde a pessoa escolhe a própria senha."""

    def setUp(self):
        self.pessoa = conta_sem_senha()
        self.caminho = caminho_de(self.pessoa)

    def test_endereco_valido_abre_o_formulario_da_conta_certa(self):
        resposta = self.client.get(self.caminho)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["conta"], self.pessoa)
        self.assertContains(resposta, self.pessoa.email)

    def test_endereco_de_primeiro_acesso_nao_reaproveita_sessao_de_outra_conta(self):
        outra = Usuario.objects.create_user(
            email="gestao@exemplo.test",
            password=SENHA,
            full_name="Gestao",
        )
        self.assertTrue(
            self.client.login(username=outra.email, password=SENHA)
        )

        resposta = self.client.get(self.caminho)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["conta"], self.pessoa)
        self.assertNotIn("_auth_user_id", self.client.session)

        nova_senha = "senhaNovaBoa2026"
        definiu = self.client.post(
            self.caminho, {"senha": nova_senha, "confirmacao": nova_senha}
        )
        self.assertRedirects(definiu, reverse("accounts:login"))

        self.pessoa.refresh_from_db()
        outra.refresh_from_db()
        self.assertTrue(self.pessoa.check_password(nova_senha))
        self.assertTrue(outra.check_password(SENHA))

    def test_senha_definida_permite_entrar(self):
        resposta = self.client.post(
            self.caminho, {"senha": SENHA, "confirmacao": SENHA}
        )
        self.assertRedirects(resposta, reverse("accounts:login"))

        self.pessoa.refresh_from_db()
        self.assertTrue(self.pessoa.has_usable_password())
        self.assertTrue(self.pessoa.check_password(SENHA))

        entrada = self.client.post(
            reverse("accounts:login"),
            {"username": self.pessoa.email, "password": SENHA},
        )
        self.assertRedirects(entrada, reverse("core:home"))

    def test_endereco_deixa_de_valer_depois_de_usado(self):
        self.client.post(self.caminho, {"senha": SENHA, "confirmacao": SENHA})
        resposta = self.client.get(self.caminho)
        self.assertEqual(resposta.status_code, 410)
        self.assertContains(resposta, "não vale mais", status_code=410)

    def test_endereco_adulterado_nao_abre_formulario(self):
        base = self.caminho.rstrip("/")
        trocado = "a" if base[-1] != "a" else "b"
        resposta = self.client.get(base[:-1] + trocado + "/")
        self.assertEqual(resposta.status_code, 410)

    def test_endereco_de_conta_inexistente_nao_abre_formulario(self):
        pessoa = conta_sem_senha(email="some@exemplo.test")
        caminho = caminho_de(pessoa)
        pessoa.delete()
        self.assertEqual(self.client.get(caminho).status_code, 410)

    def test_senha_diferente_na_confirmacao_e_recusada(self):
        resposta = self.client.post(
            self.caminho, {"senha": SENHA, "confirmacao": SENHA + "-outra"}
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("confirmacao", resposta.context["form"].errors)

        self.pessoa.refresh_from_db()
        self.assertFalse(self.pessoa.has_usable_password())

    def test_senha_que_nao_passa_nas_regras_do_sistema_e_recusada(self):
        resposta = self.client.post(
            self.caminho, {"senha": "123", "confirmacao": "123"}
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("senha", resposta.context["form"].errors)

        self.pessoa.refresh_from_db()
        self.assertFalse(self.pessoa.has_usable_password())
        self.assertFalse(self.client.login(username=self.pessoa.email, password="123"))

    def test_senha_igual_ao_proprio_email_e_recusada(self):
        resposta = self.client.post(
            self.caminho,
            {"senha": self.pessoa.email, "confirmacao": self.pessoa.email},
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("senha", resposta.context["form"].errors)
