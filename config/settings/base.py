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
    "apps.portaria",
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
                "apps.core.context_processors.marca_do_condominio",
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
# Arquivos privados (storage compartilhado entre os domínios)
# ---------------------------------------------------------------------------
# O arquivo privado nunca vai para o banco: o que se guarda ali é a referência
# do objeto, e quem guarda o arquivo é o provedor configurado. Em produção o
# disco é efêmero, e por isso o arquivo vive em bucket privado remoto; no
# desenvolvimento, ele fica no sistema de arquivos da própria aplicação
# (MEDIA_ROOT), servido pelo Django enquanto DJANGO_DEBUG for verdadeiro.
#
# Qual provedor guarda os arquivos privados do Eithra. A infraestrutura
# compartilhada vive em apps/core/storage.py.
EITHRA_STORAGE_PROVIDER = env("EITHRA_STORAGE_PROVIDER", default="").strip().lower()

# O primeiro nome dado a essa escolha, mantido para os ambientes que já o usam.
# As credenciais vêm do ambiente e são usadas somente no backend. Se o provedor
# remoto estiver escolhido e as variáveis obrigatórias faltarem, o envio é
# recusado: não existe queda silenciosa para outro lugar.
BALANCOS_STORAGE_PROVIDER = env("BALANCOS_STORAGE_PROVIDER", default="")
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

# A foto de quem usa o Eithra fica no mesmo provedor privado, em um bucket
# próprio: a imagem de uma pessoa não se mistura com os arquivos de outro
# domínio. Como o arquivo é privado, o acesso é sempre temporário e gerado na
# hora — nunca uma URL pública guardada.
EITHRA_AVATAR_BUCKET = env("EITHRA_AVATAR_BUCKET", default="")
# 2 MB. É um retrato de identificação, não um arquivo de trabalho: um limite
# menor que o do documento recusa cedo a imagem errada.
EITHRA_AVATAR_MAX_BYTES = env.int(
    "EITHRA_AVATAR_MAX_BYTES", default=2 * 1024 * 1024
)

# A logo do condomínio é identificação de contexto, não acervo: pequena, ao lado
# do nome. Fica em um bucket próprio pelo mesmo motivo da foto — a marca de um
# condomínio não se mistura com a imagem de outro domínio — e o acesso também é
# temporário e gerado na hora.
EITHRA_CONDOMINIO_LOGO_BUCKET = env("EITHRA_CONDOMINIO_LOGO_BUCKET", default="")
# 1 MB. Uma logo é um símbolo pequeno, exibido em poucos pixels: um limite
# apertado recusa cedo a imagem errada sem atrapalhar a legítima.
EITHRA_CONDOMINIO_LOGO_MAX_BYTES = env.int(
    "EITHRA_CONDOMINIO_LOGO_MAX_BYTES", default=1 * 1024 * 1024
)

# A evidência de uma demanda — a foto ou o vídeo que sustenta o que foi
# relatado — fica em um bucket privado próprio, pelo mesmo motivo dos demais: o
# arquivo de um domínio não se mistura com o de outro. O acesso é temporário e
# gerado na hora, nunca uma URL pública guardada.
DEMANDAS_STORAGE_BUCKET = env("DEMANDAS_STORAGE_BUCKET", default="")
# 10 MB por foto. É uma imagem para mostrar o que aconteceu, não um arquivo de
# trabalho: um limite que recusa cedo a foto errada sem apertar a legítima.
DEMANDAS_MAX_FOTO_BYTES = env.int(
    "DEMANDAS_MAX_FOTO_BYTES", default=10 * 1024 * 1024
)
# 25 MB por vídeo. Vídeo curto — o suficiente para mostrar o problema em
# movimento —, com espaço para um clipe de celular sem abrir a porta para
# arquivos longos de câmera.
DEMANDAS_MAX_VIDEO_BYTES = env.int(
    "DEMANDAS_MAX_VIDEO_BYTES", default=25 * 1024 * 1024
)
# Quantos arquivos a gestão pode anexar numa mesma atualização. Em excesso, o
# histórico vira galeria e deixa de ser memória.
DEMANDAS_MAX_EVIDENCIAS_POR_ENVIO = env.int(
    "DEMANDAS_MAX_EVIDENCIAS_POR_ENVIO", default=10
)
# Validade do acesso temporário ao arquivo. Curta de propósito: é o suficiente
# para abrir a foto ou o vídeo, não para deixar um endereço circulando.
DEMANDAS_URL_ACESSO_SEGUNDOS = env.int("DEMANDAS_URL_ACESSO_SEGUNDOS", default=60)

# A evidência de uma manutenção — a foto, o vídeo ou o documento que sustenta a
# execução e apoia a conferência — fica em um bucket privado próprio, pelo mesmo
# motivo dos demais: o arquivo de um domínio não se mistura com o de outro. O
# acesso é temporário e gerado na hora, nunca uma URL pública guardada.
MANUTENCOES_STORAGE_BUCKET = env("MANUTENCOES_STORAGE_BUCKET", default="")
# 10 MB por foto. A mesma régua da evidência de demanda: é uma imagem para
# mostrar o que foi feito, não um arquivo de trabalho.
MANUTENCOES_MAX_FOTO_BYTES = env.int(
    "MANUTENCOES_MAX_FOTO_BYTES", default=10 * 1024 * 1024
)
# 25 MB por vídeo. Vídeo curto — o suficiente para mostrar o serviço em
# movimento —, com espaço para um clipe de celular sem abrir a porta para
# arquivos longos de câmera.
MANUTENCOES_MAX_VIDEO_BYTES = env.int(
    "MANUTENCOES_MAX_VIDEO_BYTES", default=25 * 1024 * 1024
)
# 25 MB por documento. É o relatório técnico ou a ordem de serviço que
# acompanha a manutenção — um punhado de páginas —, e não um balanço inteiro:
# o mesmo limite do vídeo basta e recusa cedo o arquivo trocado.
MANUTENCOES_MAX_DOCUMENTO_BYTES = env.int(
    "MANUTENCOES_MAX_DOCUMENTO_BYTES", default=25 * 1024 * 1024
)
# Quantos arquivos podem ser anexados numa mesma vez. Em excesso, o histórico
# vira galeria e deixa de ser memória.
MANUTENCOES_MAX_EVIDENCIAS_POR_ENVIO = env.int(
    "MANUTENCOES_MAX_EVIDENCIAS_POR_ENVIO", default=10
)
# Validade do acesso temporário ao arquivo. Curta de propósito: é o suficiente
# para abrir a prova, não para deixar um endereço circulando.
MANUTENCOES_URL_ACESSO_SEGUNDOS = env.int("MANUTENCOES_URL_ACESSO_SEGUNDOS", default=60)


def _dias_por_prioridade(bruto: str) -> dict[str, int]:
    """A política de prazo no formato ``prioridade:dias``, separada por vírgula.

    Lida do ambiente para que o valor seja configuração do sistema, e não
    constante no código. O que não estiver no formato certo é ignorado em
    silêncio — a ausência de regra é um estado legítimo (ver
    ``apps/demandas/prazos.py``).
    """
    politica: dict[str, int] = {}
    for parte in (bruto or "").split(","):
        prioridade, _, dias = parte.partition(":")
        prioridade, dias = prioridade.strip().lower(), dias.strip()
        if prioridade and dias.isdigit() and int(dias) > 0:
            politica[prioridade] = int(dias)
    return politica


# Quantos dias cada prioridade concede a uma demanda, a partir do dia do
# registro. Vazio — o padrão — significa "sem prazo automático": nenhuma data é
# inventada sem regra configurada. Exemplo: "baixa:30,normal:15,alta:5,urgente:1".
EITHRA_DEMANDAS_PRAZO_DIAS = _dias_por_prioridade(
    env("EITHRA_DEMANDAS_PRAZO_DIAS", default="")
)

SUPABASE_URL = env("SUPABASE_URL", default="")
SUPABASE_SERVICE_ROLE_KEY = env("SUPABASE_SERVICE_ROLE_KEY", default="")

# ---------------------------------------------------------------------------
# Leitura semântica do relato (opcional)
# ---------------------------------------------------------------------------
# O Eithra lê o relato com regras próprias, e isso já basta para registrar uma
# demanda. Quando existe um serviço de linguagem configurado, ele entra como uma
# segunda leitura — para o que as palavras exatas não alcançam — e nunca como a
# única fonte: sem chave ou sem modelo, o sistema simplesmente segue
# determinístico, sem erro e sem tela quebrada (ver apps/core/llm.py).
#
# A credencial vive só no ambiente, é usada só no backend e nunca é registrada
# em log. O endereço segue o formato da API de chat da OpenAI, o que mantém a
# escolha de provedor fora do código.
EITHRA_LLM_API_KEY = env("EITHRA_LLM_API_KEY", default="").strip()
EITHRA_LLM_BASE_URL = env(
    "EITHRA_LLM_BASE_URL", default="https://api.openai.com/v1"
).strip()
EITHRA_LLM_MODEL = env("EITHRA_LLM_MODEL", default="").strip()
# Curto de propósito: a leitura semântica é uma ajuda, e o registro da demanda
# não pode ficar esperando por ela.
EITHRA_LLM_TIMEOUT = env.float("EITHRA_LLM_TIMEOUT", default=6.0)

# ---------------------------------------------------------------------------
# Áudio: fala em texto (opcional)
# ---------------------------------------------------------------------------
# A entrada por voz passa por um núcleo único (apps/core/audio.py), que devolve o
# texto fiel da fala e para aí — quem interpreta o texto é o domínio. O provedor
# e a credencial são os mesmos da leitura semântica acima; o que o áudio tem de
# próprio é o modelo de transcrição.
#
# SEM o modelo configurado, a entrada por voz fica indisponível e o registro por
# texto continua igual: não há erro 500 e nenhuma tela quebra.
EITHRA_AUDIO_MODEL = env("EITHRA_AUDIO_MODEL", default="").strip()
# Idioma padrão da transcrição, quando o pedido não informa outro.
EITHRA_AUDIO_IDIOMA = env("EITHRA_AUDIO_IDIOMA", default="pt").strip()
# Um recado operacional é curto, mas transcrever é mais lento que ler: o limite
# é maior que o da leitura semântica de propósito.
EITHRA_AUDIO_TIMEOUT = env.float("EITHRA_AUDIO_TIMEOUT", default=30.0)
# 10 MB. Recusa cedo o arquivo errado, sem apertar o recado legítimo.
EITHRA_AUDIO_MAX_BYTES = env.int("EITHRA_AUDIO_MAX_BYTES", default=10 * 1024 * 1024)

# ---------------------------------------------------------------------------
# Gmail: leitura de fonte externa (opcional)
# ---------------------------------------------------------------------------
# O Eithra lê a caixa do condomínio como uma fonte de informação a mais — nunca
# como uma segunda caixa de entrada nem como dona de nada (ver apps/core/gmail.py
# e docs/carga_historica_gmail_drive_auditoria.md). A leitura é somente leitura
# (gmail.readonly): não marca como lido, não arquiva, não exclui, não responde e
# não envia.
#
# A credencial vive só no ambiente, é usada só no backend e nunca é registrada
# em log. O acesso usa o fluxo *refresh token* (servidor a servidor): o refresh
# token é obtido uma única vez, fora do Eithra, por consentimento do responsável
# pela conta — nesta fase o Eithra não abre tela de consentimento.
#
# SEM essas variáveis, a leitura do Gmail fica indisponível (configurado() diz
# que não) e o resto do sistema segue igual: não há erro 500 nem tela quebrada.
EITHRA_GMAIL_CLIENT_ID = env("EITHRA_GMAIL_CLIENT_ID", default="").strip()
EITHRA_GMAIL_CLIENT_SECRET = env("EITHRA_GMAIL_CLIENT_SECRET", default="").strip()
EITHRA_GMAIL_REFRESH_TOKEN = env("EITHRA_GMAIL_REFRESH_TOKEN", default="").strip()
# Rótulo humano da conta lida (ex.: portaria@condominio.com.br). Opcional.
EITHRA_GMAIL_CONTA = env("EITHRA_GMAIL_CONTA", default="").strip()
# Tempo máximo de espera ao conversar com a API do Gmail, em segundos.
EITHRA_GMAIL_TIMEOUT = env.float("EITHRA_GMAIL_TIMEOUT", default=30.0)

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
