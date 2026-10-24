"""Formulários do app de contas."""
from django import forms
from django.contrib.auth.forms import (
    AuthenticationForm as DjangoAuthenticationForm,
    PasswordChangeForm as DjangoPasswordChangeForm,
)
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError


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


class DefinirSenhaForm(forms.Form):
    """A senha que a própria pessoa escolhe no primeiro acesso.

    Ninguém define senha por outra pessoa: quem concede o acesso entrega um
    endereço e a pessoa escolhe a dela. A conferência é a mesma de qualquer
    senha do sistema — as regras do Django, e não uma lista inventada aqui.
    """

    senha = forms.CharField(
        label="Nova senha",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "autofocus": True,
                "autocomplete": "new-password",
                "placeholder": "Escolha a sua senha",
            }
        ),
    )
    confirmacao = forms.CharField(
        label="Repita a nova senha",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "autocomplete": "new-password",
                "placeholder": "Repita a mesma senha",
            }
        ),
    )

    def __init__(self, *args, conta=None, **kwargs):
        super().__init__(*args, **kwargs)
        # A conta entra na conferência: uma senha igual ao próprio e-mail é
        # recusada pelas regras do sistema, e para isso ele precisa saber quem é.
        self.conta = conta

    def clean_senha(self):
        senha = self.cleaned_data["senha"]
        try:
            validate_password(senha, self.conta)
        except ValidationError as erro:
            raise forms.ValidationError(list(erro.messages)) from None
        return senha

    def clean(self):
        dados = super().clean()
        senha = dados.get("senha")
        confirmacao = dados.get("confirmacao")
        if senha and confirmacao and senha != confirmacao:
            self.add_error("confirmacao", "As duas senhas não são iguais.")
        return dados


class DefinirSenhaPelaGestaoForm(DefinirSenhaForm):
    """A senha que a administração técnica define para a conta de outra pessoa.

    Reaproveita a conferência da primeira senha: as regras são as do Django, e
    não uma lista inventada para esta tela. O que muda é só o nome do campo de
    confirmação, porque aqui quem digita não é o dono da conta.

    A senha de agora não existe para ser vista: o formulário não a pede nem a
    mostra. Quem a guarda é o Django.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["confirmacao"].label = "Confirmar nova senha"


class SolicitarRedefinicaoForm(forms.Form):
    """O e-mail de quem esqueceu a senha.

    Este formulário não responde se o e-mail existe — ele só recebe o
    endereço. Quem responde é a tela, e sempre com a mesma frase: é isso que
    impede alguém de descobrir quem tem conta no Eithra sondando o campo.
    """

    email = forms.EmailField(
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


class AlterarSenhaForm(DjangoPasswordChangeForm):
    """A troca de senha por quem já está dentro do sistema.

    A senha atual é exigida, e quem confere é o Django: alguém que deixasse uma
    sessão aberta não consegue trocar a senha sem saber a de agora. As regras
    de força da nova senha são as do projeto, as mesmas de qualquer outra porta
    de entrada — não há uma lista inventada só para esta tela.
    """

    error_messages = {
        **DjangoPasswordChangeForm.error_messages,
        "password_incorrect": "A senha atual não confere.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["old_password"].label = "Senha atual"
        self.fields["old_password"].widget.attrs.update(
            {
                "class": "form-control",
                "autofocus": True,
                "autocomplete": "current-password",
                "placeholder": "A senha que você usa hoje",
            }
        )
        self.fields["new_password1"].label = "Nova senha"
        self.fields["new_password1"].widget.attrs.update(
            {
                "class": "form-control",
                "autocomplete": "new-password",
                "placeholder": "Escolha a nova senha",
            }
        )
        self.fields["new_password2"].label = "Repita a nova senha"
        self.fields["new_password2"].widget.attrs.update(
            {
                "class": "form-control",
                "autocomplete": "new-password",
                "placeholder": "Repita a mesma senha",
            }
        )
