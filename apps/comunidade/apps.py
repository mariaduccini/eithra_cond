"""Configuração do app de comunidade: unidades, pessoas e veículos."""
from django.apps import AppConfig


class ComunidadeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.comunidade"
    verbose_name = "Comunidade"
