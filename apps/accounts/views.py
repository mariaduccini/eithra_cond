"""Acesso ao sistema."""
from django.contrib import messages
from django.contrib.auth import get_user_model, logout
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import FormView

from apps.accounts import primeiro_acesso
from apps.accounts.forms import (
    AlterarSenhaForm,
    AuthenticationForm,
    DefinirSenhaForm,
    SolicitarRedefinicaoForm,
)


class LoginView(auth_views.LoginView):
    """Entrada no Eithra."""

    template_name = "registration/login.html"
    authentication_form = AuthenticationForm
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    """Saída do Eithra."""

    next_page = reverse_lazy("accounts:login")


class PrimeiroAcessoView(FormView):
    """Onde a própria pessoa define a senha do primeiro acesso.

    É uma tela pública, e tem de ser: quem chega ainda não tem senha. O que
    autoriza é o endereço, e não uma sessão — por isso ele é conferido antes de
    a tela existir. Um endereço vencido ou já usado não abre formulário nenhum:
    a pessoa é avisada e pode pedir outro a quem cuida do condomínio.

    O mesmo endereço serve ao primeiro acesso e à redefinição de senha: a
    diferença está só no que a tela diz, porque quem já tinha senha não está
    "definindo a primeira" — está criando outra.
    """

    template_name = "registration/primeiro_acesso.html"
    form_class = DefinirSenhaForm

    def dispatch(self, request, *args, **kwargs):
        self.conta = primeiro_acesso.conta_do_endereco(
            kwargs.get("uidb64"), kwargs.get("token")
        )
        if self.conta is None:
            return render(
                request, self.template_name, {"endereco_invalido": True}, status=410
            )
        # O endereço vale por si: se outra conta estiver com sessão aberta,
        # ela não pode "emprestar" contexto a esta definição de senha.
        if request.user.is_authenticated and request.user.pk != self.conta.pk:
            logout(request)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["conta"] = self.conta
        return kwargs

    def get_context_data(self, **kwargs):
        return {
            **super().get_context_data(**kwargs),
            "conta": self.conta,
            "primeira_senha": primeiro_acesso.precisa_definir_senha(self.conta),
        }

    def form_valid(self, form):
        primeira_senha = primeiro_acesso.precisa_definir_senha(self.conta)
        self.conta.set_password(form.cleaned_data["senha"])
        self.conta.save(update_fields=["password", "updated_at"])
        messages.success(
            self.request,
            (
                "Senha definida. Agora entre com o seu e-mail e a senha que "
                "você escolheu."
                if primeira_senha
                else "Sua senha foi alterada. Entre com a nova senha."
            ),
        )
        return redirect("accounts:login")


class SolicitarRedefinicaoView(FormView):
    """"Esqueci minha senha": onde se pede um novo endereço de senha.

    A tela nunca revela se o e-mail tem conta. A frase de resposta é a mesma
    exista ou não a conta, entregue ou não o e-mail: sem isso, o formulário
    viraria uma maneira de descobrir quem está no Eithra sondando endereços.

    Isso também explica uma escolha deste trecho: uma falha de envio não vira
    mensagem de erro aqui. Um aviso que só aparece quando a conta existe
    contaria justamente o que a frase neutra esconde. Quem opera o condomínio
    recebe o erro de entrega na tela de acessos, onde já se sabe de quem se
    trata.
    """

    template_name = "registration/esqueci_senha.html"
    form_class = SolicitarRedefinicaoForm
    success_url = reverse_lazy("accounts:login")

    def form_valid(self, form):
        email = form.cleaned_data["email"].strip()
        conta = (
            get_user_model()
            .objects.filter(email__iexact=email, is_active=True)
            .first()
        )
        if conta is not None:
            try:
                primeiro_acesso.enviar_endereco_por_email(
                    conta=conta, request=self.request
                )
            except primeiro_acesso.ErroDeEntregaDoPrimeiroAcesso:
                # Silêncio deliberado: ver a explicação na docstring da view.
                pass
        messages.success(
            self.request, primeiro_acesso.recado_neutro_de_redefinicao()
        )
        return super().form_valid(form)


class AlterarSenhaView(auth_views.PasswordChangeView):
    """Onde quem já está dentro troca a própria senha.

    A senha de agora é exigida — sessão aberta não basta para trocá-la. Quem
    troca é sempre o dono da conta: a tela só conhece quem está logado, e não
    existe caminho por onde alguém alcance a senha de outra pessoa.

    A sessão continua valendo depois da troca: quem fez a coisa certa não é
    expulso por isso.
    """

    template_name = "registration/alterar_senha.html"
    form_class = AlterarSenhaForm
    success_url = reverse_lazy("core:home")

    def form_valid(self, form):
        resposta = super().form_valid(form)
        messages.success(self.request, "Sua senha foi alterada.")
        return resposta
