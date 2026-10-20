"""Configuração do app de governança.

Governança é o que liga um condomínio às obrigações que ele assumiu: contratos,
garantias e documentos. Ela fica em um app próprio porque olha para vários
domínios ao mesmo tempo — cadastros, manutenções, estoque, rotinas — sem que
nenhum deles precise olhar de volta.
"""
from django.apps import AppConfig


class GovernancaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.governanca"
    verbose_name = "Governança"
