"""Configurações para execução de testes automatizados."""
from .base import *  # noqa: F401,F403

DEBUG = False

# Chave apenas para os testes; nunca usada fora deles.
if not SECRET_KEY:  # noqa: F405
    SECRET_KEY = "test-only-insecure-key-eithra"

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Hash rápido: os testes não precisam do custo do PBKDF2.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Os testes não passam por collectstatic: resolve os arquivos pelos finders
# em vez de varrer STATIC_ROOT (que ainda não existe neste ponto).
WHITENOISE_AUTOREFRESH = True

# Evita redirecionamentos HTTPS durante os testes.
SECURE_SSL_REDIRECT = False
