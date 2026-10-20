"""Configurações para execução de testes automatizados.

Os testes rodam no mesmo banco oficial do Eithra — PostgreSQL, definido por
``DATABASE_URL``. Não há banco de teste próprio nem fallback para SQLite: um
banco diferente do real esconderia diferenças justamente onde elas mais
aparecem (tipos, constraints, ordenação, transações). Se a conexão não estiver
configurada, é melhor os testes não começarem do que passarem sobre outro
banco.

Qual banco é alvo fica fora daqui: este arquivo aceita o ``DATABASE_URL`` que
lhe for dado, para servir tanto ao desenvolvimento local (``test-local.ps1``,
que confere que o alvo é local) quanto a um CI com PostgreSQL próprio. Fixar
``localhost`` aqui impediria o segundo caso.
"""
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = False

# Chave apenas para os testes; nunca usada fora deles.
if not SECRET_KEY:  # noqa: F405
    SECRET_KEY = "test-only-insecure-key-eithra"

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

if not DATABASE_URL:  # noqa: F405
    raise ImproperlyConfigured(
        "DATABASE_URL não está definida. Os testes do Eithra rodam sobre "
        "PostgreSQL. Informe a string de conexão no formato "
        "postgresql://USUARIO:SENHA@HOST:PORTA/BANCO — no desenvolvimento "
        "local, use .env.local e o script test-local.ps1."
    )

DATABASES = {"default": env.db_url("DATABASE_URL")}

# Hash rápido: os testes não precisam do custo do PBKDF2.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Os testes não passam por collectstatic: resolve os arquivos pelos finders
# em vez de varrer STATIC_ROOT (que ainda não existe neste ponto).
WHITENOISE_AUTOREFRESH = True

# Evita redirecionamentos HTTPS durante os testes.
SECURE_SSL_REDIRECT = False
