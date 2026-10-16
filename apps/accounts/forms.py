"""Formulários do app de contas."""
from django import forms
from django.contrib.auth.forms import AuthenticationForm as DjangoAuthenticationForm


class AuthenticationForm(DjangoAuthenticationForm):
    """Entrada no sistema com rótulos e mensagens em linguagem humana."""

    username = forms.EmailField(
        label="E-mail",
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "autofocus": True,
                "autocomplete": "email",
                "inputmode": "email",
                "placeholder": "seu.email@exemplo.com.br",
            }
        ),
    )
    password = forms.CharField(
        label="Senha",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "autocomplete": "current-password",
                "placeholder": "Sua senha",
            }
        ),
    )

    error_messages = {
        "invalid_login": "Não encontramos uma conta com esse e-mail e senha.",
        "inactive": (
            "Esta conta está desativada. Procure a administração do condomínio."
        ),
    }
