"""Configurações para produção."""
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import env

if not SECRET_KEY:  # noqa: F405
    raise ImproperlyConfigured(
        "Defina DJANGO_SECRET_KEY no ambiente ou no arquivo .env antes de "
        "executar em produção."
    )

if not DATABASE_URL:  # noqa: F405
    raise ImproperlyConfigured(
        "Defina DATABASE_URL antes de executar em produção."
    )

DATABASES = {"default": env.db_url("DATABASE_URL")}

DEBUG = False

# Redirecionamento e cookies seguros.
SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HSTS.
SECURE_HSTS_SECONDS = env.int("DJANGO_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# Preload mantido desligado por decisão consciente; o aviso security.W021
# permanece conhecido nesta fase. Reavaliar somente quando: houver domínio
# definitivo, HTTPS estável, subdomínios conhecidos, política HSTS
# definitiva aprovada e decisão explícita de entrar na lista de preload.
SECURE_HSTS_PRELOAD = False

# Proxy reverso (Vercel, Nginx, etc.).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Em produção o arquivo privado vive em bucket remoto: sem escolha explícita no
# ambiente, o provedor continua sendo o Supabase, como antes de o provedor local
# existir.
EITHRA_STORAGE_PROVIDER = (
    env("EITHRA_STORAGE_PROVIDER", default="supabase").strip().lower() or "supabase"
)

# Arquivos estáticos servidos com compressão e hash pelo WhiteNoise.
STORAGES["staticfiles"]["BACKEND"] = (  # noqa: F405
    "whitenoise.storage.CompressedManifestStaticFilesStorage"
)

EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend"
)
