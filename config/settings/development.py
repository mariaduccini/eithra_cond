"""ConfiguraÃ§Ãµes para desenvolvimento local."""
from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import env

DEBUG = env.bool("DJANGO_DEBUG", default=True)

if not SECRET_KEY:  # noqa: F405
    # Valor apenas para desenvolvimento local. Nunca usado em produÃ§Ã£o.
    SECRET_KEY = "dev-only-insecure-key-eithra"

if not DATABASE_URL:  # noqa: F405
    # Sem fallback para SQLite: o banco de desenvolvimento do Eithra Ã© o
    # PostgreSQL canÃ´nico. Se a conexÃ£o nÃ£o estÃ¡ configurada, Ã© melhor o
    # ambiente nÃ£o subir do que subir contra um banco diferente do real.
    raise ImproperlyConfigured(
        "DATABASE_URL nÃ£o estÃ¡ definida. O desenvolvimento do Eithra usa "
        "PostgreSQL, definido por DATABASE_URL â€” normalmente o banco local, "
        "escolhido por .env.local (veja run-local.ps1 e test-local.ps1). O "
        "ambiente remoto continua disponÃ­vel quando selecionado de forma "
        "explÃ­cita. Informe a string de conexÃ£o no formato "
        "postgresql://USUARIO:SENHA@HOST:PORTA/BANCO."
    )

DATABASES = {"default": env.db_url("DATABASE_URL")}

ALLOWED_HOSTS = ["*"]

INTERNAL_IPS = ["127.0.0.1"]

EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)

# Acesso remoto temporário para testes via Cloudflare Quick Tunnel.
# Somente ambiente development.
#
# O túnel termina o TLS na borda e reapresenta a requisição ao servidor local
# por HTTP, anunciando o esquema original no cabeçalho X-Forwarded-Proto. Sem
# SECURE_PROXY_SSL_HEADER o Django trataria a requisição como insegura
# (request.is_secure() falso): os endereços absolutos sairiam em http — como o
# link de primeiro acesso entregue por e-mail — e a checagem de origem do CSRF
# deixaria de reconhecer o https. Confiar nesse cabeçalho aqui só é seguro
# porque o servidor de desenvolvimento fala com o mundo apenas através do
# túnel; em produção o mesmo cabeçalho já é configurado em production.py.
CSRF_TRUSTED_ORIGINS = [
    "https://*.trycloudflare.com",
]

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Sessão no teste mobile: nada aqui é definido de propósito.
#
# Fechar e reabrir o Eithra no celular não deve exigir a senha de novo. Quem
# sustenta isso é o comportamento padrão do Django, que já atende: a sessão não
# expira ao fechar o navegador (SESSION_EXPIRE_AT_BROWSER_CLOSE é falso por
# omissão) e o cookie vale por 14 dias (SESSION_COOKIE_AGE padrão, 1209600
# segundos). Não é sessão eterna e não há ajuste a fazer no development.
#
# O que já está definido na configuração comum (base.py) vale para todos os
# ambientes: cookie de sessão HttpOnly e SameSite=Lax. Em produção, o cookie
# ainda passa a ser Secure (production.py).
