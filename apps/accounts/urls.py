"""Endereços do app de contas."""
from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("entrar/", views.LoginView.as_view(), name="login"),
    path("sair/", views.LogoutView.as_view(), name="logout"),
]
