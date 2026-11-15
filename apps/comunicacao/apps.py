"""Configuração do app da capacidade transversal de comunicação."""
from django.apps import AppConfig


class ComunicacaoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.comunicacao"
    verbose_name = "Comunicação"
