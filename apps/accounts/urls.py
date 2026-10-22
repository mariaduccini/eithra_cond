"""Endereços do app de contas."""
from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("entrar/", views.LoginView.as_view(), name="login"),
    path("sair/", views.LogoutView.as_view(), name="logout"),
    # Quem esqueceu a senha pede um novo endereço aqui. A tela não diz se o
    # e-mail tem conta — responde a mesma coisa nos dois casos.
    path(
        "esqueci-senha/",
        views.SolicitarRedefinicaoView.as_view(),
        name="esqueci_senha",
    ),
    # Troca de senha de quem já está dentro, e só da própria conta.
    path(
        "minha-senha/",
        views.AlterarSenhaView.as_view(),
        name="alterar_senha",
    ),
    # O endereço de primeiro acesso é a única credencial de quem ainda não tem
    # senha: quem o recebe define a própria senha e ele deixa de valer.
    path(
        "primeiro-acesso/<str:uidb64>/<str:token>/",
        views.PrimeiroAcessoView.as_view(),
        name="primeiro_acesso",
    ),
]
