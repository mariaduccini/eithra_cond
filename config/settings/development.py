"""Configurações para desenvolvimento local."""
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = env.bool("DJANGO_DEBUG", default=True)

if not SECRET_KEY:  # noqa: F405
    # Valor apenas para desenvolvimento local. Nunca usado em produção.
    SECRET_KEY = "dev-only-insecure-key-eithra"

if not DATABASE_URL:  # noqa: F405
    # Sem fallback para SQLite: o banco de desenvolvimento do Eithra é o
    # PostgreSQL canônico. Se a conexão não está configurada, é melhor o
    # ambiente não subir do que subir contra um banco diferente do real.
    raise ImproperlyConfigured(
        "DATABASE_URL não está definida. O desenvolvimento do Eithra usa "
        "PostgreSQL remoto. Copie .env.example para .env e informe a string "
        "de conexão (formato: postgresql://USUARIO:SENHA@HOST:PORTA/BANCO)."
    )

DATABASES = {"default": env.db_url("DATABASE_URL")}

ALLOWED_HOSTS = ["*"]

INTERNAL_IPS = ["127.0.0.1"]

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
