"""Configuração do domínio de rotinas recorrentes."""
from django.apps import AppConfig


class RotinasConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.rotinas"
    verbose_name = "Rotinas"
