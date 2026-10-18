"""Configuração do domínio de demandas operacionais."""
from django.apps import AppConfig


class DemandasConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.demandas"
    verbose_name = "Demandas"
