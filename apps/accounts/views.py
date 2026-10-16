"""Acesso ao sistema."""
from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy

from apps.accounts.forms import AuthenticationForm


class LoginView(auth_views.LoginView):
    """Entrada no Eithra."""

    template_name = "registration/login.html"
    authentication_form = AuthenticationForm
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    """Saída do Eithra."""

    next_page = reverse_lazy("accounts:login")
