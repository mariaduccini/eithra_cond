"""Configuração do domínio de manutenções operacionais."""
from django.apps import AppConfig


class ManutencoesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.manutencoes"
    verbose_name = "Manutenções"
