"""Bloqueio por tentativas de senha erradas (usuário + IP).

A pergunta é simples: o que acontece quando alguém erra a senha várias vezes
seguidas? O Eithra não deixa a sexta acontecer — a combinação usuário+IP fica
trancada por meia hora, mesmo que a senha digitada esteja correta. A tentativa
fica registrada para a administração ver no admin do Django.

O bloqueio é por combinação, e não por IP solto, para não punir uma rede
compartilhada por causa de uma pessoa.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from axes.models import AccessAttempt

Usuario = get_user_model()

SENHA = "umaSenhaBoa2026"
SENHA_ERRADA = "senha-que-nao-e-a-dela"

LIMITE = 5
RESPOSTA_DE_BLOQUEIO = 429


class BloqueioDeLoginTests(TestCase):
    """Cinco erros seguidos trancam; a sexta tentativa já é bloqueada."""

    def setUp(self):
        self.url = reverse("accounts:login")
        self.pessoa = Usuario.objects.create_user(
            email="sindico@exemplo.test", password=SENHA, full_name="Síndica"
        )

    def tentar(self, senha):
        return self.client.post(
            self.url, {"username": self.pessoa.email, "password": senha}
        )

    def errar(self, vezes):
        return [self.tentar(SENHA_ERRADA) for _ in range(vezes)]

    def test_quatro_erros_ainda_recebem_o_aviso_normal(self):
        for resposta in self.errar(LIMITE - 1):
            self.assertEqual(resposta.status_code, 200)

    def test_a_quinta_tentativa_ja_cruza_o_limite(self):
        self.errar(LIMITE - 1)

        quinta = self.tentar(SENHA_ERRADA)
        self.assertEqual(quinta.status_code, RESPOSTA_DE_BLOQUEIO)
        self.assertTrue(AccessAttempt.objects.exists())

    def test_sexta_tentativa_errada_e_bloqueada(self):
        self.errar(LIMITE)

        sexta = self.tentar(SENHA_ERRADA)
        self.assertEqual(sexta.status_code, RESPOSTA_DE_BLOQUEIO)

    def test_durante_o_bloqueio_a_senha_certa_tambem_nao_entra(self):
        self.errar(LIMITE)

        resposta = self.tentar(SENHA)
        self.assertEqual(resposta.status_code, RESPOSTA_DE_BLOQUEIO)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_a_senha_minima_tem_dez_caracteres(self):
        validadores = {
            v["NAME"]: v.get("OPTIONS", {}) for v in settings.AUTH_PASSWORD_VALIDATORS
        }
        minimo = validadores[
            "django.contrib.auth.password_validation.MinimumLengthValidator"
        ]
        self.assertEqual(minimo.get("min_length"), 10)
