"""Configurações comuns a todos os ambientes do Eithra."""
import os
from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

# Diretório raiz do projeto (onde ficam manage.py, apps/, templates/, static/).
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_SECRET_KEY=(str, ""),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
)


def _arquivo_de_ambiente(nome: str) -> Path:
    """Resolve o arquivo de ambiente escolhido, sem deixar o projeto.

    O nome vem de ``EITHRA_ENV_FILE`` e existe para escolher de forma
    consciente entre ambientes — o local e o remoto não podem ser confundidos
    por descuido. Como o valor vem do ambiente, é tratado como entrada não
    confiável: um caminho absoluto ou com ".." apontaria para qualquer arquivo
    da máquina, e um arquivo de ambiente carrega segredos. Só o que fica dentro
    do projeto é aceito.
    """
    candidato = (BASE_DIR / nome).resolve()
    if BASE_DIR not in candidato.parents:
        raise ImproperlyConfigured(
            f"EITHRA_ENV_FILE aponta para fora do projeto: {nome!r}. "
            "Informe apenas o nome do arquivo, como .env.local."
        )
    return candidato


# O Eithra tem mais de um arquivo de ambiente — o remoto (.env) e o local
# (.env.local) —, e a escolha é explícita por EITHRA_ENV_FILE. Sem a variável,
# o comportamento é o de sempre: ler ".env".
_env_file_name = os.environ.get("EITHRA_ENV_FILE", "").strip() or ".env"
_env_file = _arquivo_de_ambiente(_env_file_name)

if _env_file.exists():
    environ.Env.read_env(_env_file)
elif _env_file_name != ".env":
    # Escolher um arquivo que não existe é engano de configuração: seguir em
    # frente faria o ambiente subir contra o banco errado sem avisar.
    raise ImproperlyConfigured(
        f"EITHRA_ENV_FILE aponta para {_env_file_name!r}, mas o arquivo não "
        f"existe em {BASE_DIR}. Confira o nome ou crie o arquivo."
    )


DEBUG = env.bool("DJANGO_DEBUG", default=False)

SECRET_KEY = env("DJANGO_SECRET_KEY", default="").strip()

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# ---------------------------------------------------------------------------
# Aplicações
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

LOCAL_APPS = [
    "apps.accounts",
    "apps.condominios",
    "apps.cadastros",
    "apps.balancos",
    "apps.demandas",
    "apps.manutencoes",
    "apps.rotinas",
    "apps.estoque",
    "apps.governanca",
    "apps.comunidade",
    "apps.core",
]

INSTALLED_APPS = DJANGO_APPS + LOCAL_APPS

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------
# O banco canônico vem de DATABASE_URL, definida por ambiente. O Eithra não
# define banco padrão: um ambiente que não informe a conexão falha de forma
# explícita (ver development.py e production.py). Isso evita o fallback
# silencioso para um banco local que não é o canônico.
DATABASE_URL = env("DATABASE_URL", default="").strip()

# Preenchido por cada ambiente. Nunca há valor assumido por omissão.
DATABASES: dict = {}

# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.EmailCaseInsensitiveBackend",
    "django.contrib.auth.backends.ModelBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Quanto tempo vale o endereço de primeiro acesso: tempo de a gestão entregar e
# a pessoa usar, sem ficar aberto para sempre. O endereço também deixa de valer
# assim que a senha é definida, e ninguém define senha por outra pessoa.
PASSWORD_RESET_TIMEOUT = 60 * 60 * 24 * 3

# ---------------------------------------------------------------------------
# E-mail transacional
# ---------------------------------------------------------------------------
# O backend de envio varia por ambiente. Aqui ficam apenas os parâmetros comuns
# aos backends reais: o ambiente local pode continuar imprimindo no console, e
# produção pode apontar para SMTP ou outro backend equivalente.
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL", default="Eithra <nao-responda@localhost>"
)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=10)

# ---------------------------------------------------------------------------
# Internacionalização
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Bahia"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Arquivos estáticos e mídia
# ---------------------------------------------------------------------------
# Caminhos absolutos: o sistema é servido em páginas de vários níveis
# (ex.: /conta/entrar/), onde um caminho relativo se perderia.
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Arquivos de balanço (storage externo privado)
# ---------------------------------------------------------------------------
# O PDF de um balanço nunca fica no sistema de arquivos da aplicação — em
# produção, o disco é efêmero. O arquivo vive em um bucket privado, e o banco
# guarda apenas a referência do objeto.
#
# As credenciais vêm do ambiente e são usadas somente no backend. Se o provedor
# estiver configurado e as variáveis obrigatórias faltarem, o envio é recusado:
# não existe queda silenciosa para disco local (ver apps/balancos/storage.py).
BALANCOS_STORAGE_PROVIDER = env("BALANCOS_STORAGE_PROVIDER", default="supabase")
BALANCOS_STORAGE_BUCKET = env("BALANCOS_STORAGE_BUCKET", default="")
# 50 MB. Um balanço real pode ter muitas páginas e vir com anexos; o limite
# existe para recusar cedo um arquivo absurdo, não para apertar o uso.
BALANCOS_MAX_UPLOAD_BYTES = env.int("BALANCOS_MAX_UPLOAD_BYTES", default=50 * 1024 * 1024)
# Validade do acesso temporário ao arquivo. Curta de propósito: é o suficiente
# para abrir o documento, não para deixar um endereço circulando.
BALANCOS_URL_ACESSO_SEGUNDOS = env.int("BALANCOS_URL_ACESSO_SEGUNDOS", default=60)
# 10 MB por foto de evidência. É uma imagem para explicar uma despesa, não um
# arquivo de trabalho: um limite menor que o do PDF recusa cedo a foto errada.
BALANCOS_MAX_FOTO_BYTES = env.int(
    "BALANCOS_MAX_FOTO_BYTES", default=10 * 1024 * 1024
)
# Leitura economica para detectar o formato: le apenas a abertura estrutural do
# documento (indice, resumo e primeiras secoes financeiras) e para cedo. Nao ha
# motivo para varrer centenas de paginas — nem para espalhar a amostra pelo
# arquivo — so para distinguir o layout; anexos e comprovantes nao definem o
# formato.
BALANCOS_DETECTOR_MAX_PAGINAS = env.int("BALANCOS_DETECTOR_MAX_PAGINAS", default=15)

# Documentos operacionais usam o mesmo provedor e as mesmas credenciais, em um
# bucket privado próprio: o arquivo de um domínio não deve ficar no bucket de
# outro. A infraestrutura compartilhada vive em ``apps/core/storage.py``.
DOCUMENTOS_STORAGE_BUCKET = env("DOCUMENTOS_STORAGE_BUCKET", default="")

SUPABASE_URL = env("SUPABASE_URL", default="")
SUPABASE_SERVICE_ROLE_KEY = env("SUPABASE_SERVICE_ROLE_KEY", default="")

# ---------------------------------------------------------------------------
# Fluxos de autenticação
# ---------------------------------------------------------------------------
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "accounts:login"

# ---------------------------------------------------------------------------
# Cabeçalhos e cookies
# ---------------------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# Nenhuma tela precisa ser embutida em iframe. Se, no futuro, uma view
# específica precisar de same-origin, a exceção deve ser aplicada apenas
# naquela view (@xframe_options_sameorigin), e não no projeto inteiro.
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
