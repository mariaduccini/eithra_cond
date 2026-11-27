"""Configurações do acesso remoto seguro pelo túnel da Cloudflare.

O túnel da Cloudflare expõe o Eithra à internet sem abrir porta no roteador —
é o acesso de quem trabalha de fora de casa, pelo celular. Antes, esse acesso
rodava com ``config.settings.development`` e ``DEBUG=True``: qualquer erro
mostrava a página de depuração do Django, com a senha do banco, a do backup e
as credenciais do Gmail, a quem estivesse do outro lado do túnel.

Este ambiente herda de produção e fecha esse buraco: nunca há depuração, o
único host aceito é o que a Cloudflare entrega, os cookies exigem HTTPS e o
erro vai para um arquivo desta máquina — não para a tela de quem acessa.

Quem sobe este ambiente é ``acesso-remoto-seguro.ps1``, com o waitress atrás do
túnel. Não é produção num servidor: os arquivos continuam no disco daqui.
"""
from .base import env
from .production import *  # noqa: F401,F403

# ---------------------------------------------------------------------------
# Depuração
# ---------------------------------------------------------------------------
# Falso sempre, sem ler o ambiente: aqui não existe "ligar a depuração". A
# página de erro que mostra segredos é exatamente o que este ambiente veio
# desligar, e ela não volta nem por engano de configuração de quem opera.
DEBUG = False

# ---------------------------------------------------------------------------
# Hosts e CSRF
# ---------------------------------------------------------------------------
# O Quick Tunnel entrega um endereço novo a cada reinício, sempre sob
# trycloudflare.com; localhost e 127.0.0.1 servem a quem abre o Eithra na
# própria máquina. Um domínio próprio no futuro entra por DJANGO_ALLOWED_HOSTS,
# sem mexer no código.
ALLOWED_HOSTS = [
    ".trycloudflare.com",
    "localhost",
    "127.0.0.1",
    *env.list("DJANGO_ALLOWED_HOSTS", default=[]),
]

# A requisição chega pelo túnel em https, e é essa origem que o Django precisa
# reconhecer como confiável para aceitar os formulários. Um domínio próprio
# entra por DJANGO_CSRF_TRUSTED_ORIGINS.
CSRF_TRUSTED_ORIGINS = [
    "https://*.trycloudflare.com",
    *env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[]),
]

# ---------------------------------------------------------------------------
# HTTPS
# ---------------------------------------------------------------------------
# O túnel já entrega https a quem acessa e fala com este servidor em http local
# (127.0.0.1:8000), anunciando o esquema original em X-Forwarded-Proto — que o
# Django já confia por SECURE_PROXY_SSL_HEADER, herdado de produção. Redirecionar
# para https aqui criaria um laço, porque a requisição já chega marcada como
# segura. Os cookies, esses sim, só viajam por HTTPS.
SECURE_SSL_REDIRECT = False

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# ---------------------------------------------------------------------------
# Arquivos
# ---------------------------------------------------------------------------
# Os arquivos privados continuam no disco desta máquina — é o acesso de um
# condomínio a partir de casa, não uma instalação em servidor. O provedor é
# lido do ambiente pelo mesmo nome de sempre; sem escolha, vale o local.
EITHRA_STORAGE_PROVIDER = (
    env("EITHRA_STORAGE_PROVIDER", default="local").strip().lower() or "local"
)

# Com o provedor local, é o próprio Django que entrega os arquivos de
# MEDIA_ROOT. Sem depuração ligada, essa rota não existe por omissão; sem ela,
# as logos e as fotos não apareceriam no acesso pelo celular.
EITHRA_SERVIR_MEDIA = True

# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
# O caminho do admin continua vindo de EITHRA_ADMIN_URL (base.py). Num acesso
# exposto à internet, troque o "admin/" previsível por um caminho difícil de
# adivinhar, acrescentando em .env.local algo como:
#
#     EITHRA_ADMIN_URL=gestao-eithra-rve/
#
# Sem isso, o admin continua funcionando, mas na rota padrão — fácil de achar.

# ---------------------------------------------------------------------------
# Registro de erros
# ---------------------------------------------------------------------------
# Todo erro vai para um arquivo desta máquina (a pasta logs/ não é versionada).
# Nenhum rastro técnico é enviado a quem está do outro lado: a página que o
# visitante vê é a 404/500 própria do Eithra (errors/404.html e errors/500.html),
# simples e sem nenhum detalhe de configuração, banco ou credencial.
PASTA_DE_LOGS = BASE_DIR / "logs"  # noqa: F405
PASTA_DE_LOGS.mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "eithra": {
            "format": "{asctime} {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "arquivo": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(PASTA_DE_LOGS / "eithra-remoto.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 3,
            "encoding": "utf-8",
            "formatter": "eithra",
            "level": "INFO",
        },
    },
    "root": {"handlers": ["arquivo"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["arquivo"], "level": "INFO", "propagate": False},
        "django.request": {
            "handlers": ["arquivo"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}
