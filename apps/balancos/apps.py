"""Configuração do domínio de balanços."""
from django.apps import AppConfig


class BalancosConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.balancos"
    verbose_name = "Balanços"
