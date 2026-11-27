"""Testes do acesso remoto seguro e do aplicativo instalável no celular.

O túnel da Cloudflare expõe o Eithra a quem está fora de casa. Antes ele subia
com ``config.settings.development`` e ``DEBUG=True``, e qualquer erro mostrava a
página de depuração com a senha do banco, a do backup e o e-mail — para quem
estivesse do outro lado.

Estes testes travam a garantia que importa: o ambiente do túnel nunca liga a
depuração (nem quando pedem), só aceita o host que a Cloudflare entrega, e o
erro que o visitante vê é a página do próprio Eithra, sem nenhum rastro técnico.
"""
import os
import subprocess
import sys

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import path, reverse

from config import urls as urls_do_projeto

# Um host qualquer sob o domínio que o Quick Tunnel usa. O valor não importa:
# o que se verifica é a regra de aceitação, não um endereço específico.
HOST_DO_TUNEL = "aceitavel-aleatorio.trycloudflare.com"
HOST_ESTRANHO = "invasor.exemplo.com"

# Os hosts que o ambiente remoto aceita além do túnel, repetidos aqui para que o
# teste não dependa de ler o arquivo de settings.
HOSTS_DO_REMOTO = [".trycloudflare.com", "localhost", "127.0.0.1"]


# -- Urlconf mínimo, só para o teste da página 500 -------------------------------
# Só existe um jeito honesto de ver a página de 500: uma rota que realmente
# estoura. Esta urlconf de teste acrescenta essa rota às rotas reais do projeto
# (para que os nomes como "core:home" continuem resolvendo dentro da página) e
# reaproveita os handlers do projeto, para que a tela verificada seja a mesma
# que o visitante veria de verdade.
def _estourar(request):
    raise RuntimeError("falha proposital: exercita a página 500 do Eithra")


urlpatterns = [path("estouro/", _estourar), *urls_do_projeto.urlpatterns]
handler404 = urls_do_projeto.handler404
handler500 = urls_do_projeto.handler500


class AmbienteRemotoTests(TestCase):
    """O ambiente do túnel nunca liga a depuração, mesmo que peçam."""

    def test_debug_continua_falso_com_django_debug_ligado_no_ambiente(self):
        """DEBUG é False em config.settings.remoto mesmo com DJANGO_DEBUG=True.

        A garantia só vale se for provada num processo novo: o ``DEBUG`` é
        decidido quando o módulo de settings é carregado, e no processo dos
        testes ele já está em cache. Por isso o subprocesso, com a variável de
        ambiente ligada, exatamente como um descuido de quem opera faria.
        """
        ambiente = dict(os.environ)
        ambiente["DJANGO_SETTINGS_MODULE"] = "config.settings.remoto"
        ambiente["EITHRA_ENV_FILE"] = ".env.local"
        ambiente["DJANGO_DEBUG"] = "True"
        # Produção exige chave e banco para sequer importar. Nada é conectado
        # aqui — só o valor de DEBUG é lido.
        ambiente.setdefault("DJANGO_SECRET_KEY", "chave-apenas-para-teste")
        ambiente.setdefault(
            "DATABASE_URL",
            "postgresql://eithra:senha@localhost:5432/eithra_dev",
        )

        resultado = subprocess.run(
            [
                sys.executable,
                "-c",
                "from django.conf import settings; print(settings.DEBUG)",
            ],
            cwd=settings.BASE_DIR,
            env=ambiente,
            capture_output=True,
            text=True,
            timeout=120,
        )

        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertEqual(resultado.stdout.strip(), "False")


class HostsDoTunelTests(TestCase):
    """Só o host que a Cloudflare entrega é aceito; o resto é recusado."""

    @override_settings(ALLOWED_HOSTS=HOSTS_DO_REMOTO)
    def test_host_do_tunel_e_aceito(self):
        resposta = self.client.get(reverse("manifest"), HTTP_HOST=HOST_DO_TUNEL)
        self.assertEqual(resposta.status_code, 200)

    @override_settings(ALLOWED_HOSTS=HOSTS_DO_REMOTO)
    def test_host_desconhecido_e_recusado(self):
        resposta = self.client.get(reverse("manifest"), HTTP_HOST=HOST_ESTRANHO)
        self.assertEqual(resposta.status_code, 400)


class ManifestTests(TestCase):
    """O manifest é o que permite "Adicionar à tela inicial" abrir como app."""

    def test_manifest_responde_com_o_tipo_certo(self):
        resposta = self.client.get(reverse("manifest"))
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["Content-Type"], "application/manifest+json")

    def test_manifest_nao_exige_entrada(self):
        """O navegador busca o manifest sem sessão — a rota não pode pedir login."""
        resposta = self.client.get(reverse("manifest"))
        self.assertNotEqual(resposta.status_code, 302)
        self.assertEqual(resposta.json()["name"], "Eithra")


class PaginasDeErroTests(TestCase):
    """O visitante nunca vê detalhe técnico — só a página do próprio Eithra."""

    def test_a_404_e_a_pagina_propria_do_eithra(self):
        resposta = self.client.get("/pagina-que-nao-existe-eithra/")
        self.assertEqual(resposta.status_code, 404)
        self.assertContains(
            resposta, "Não encontramos esta página", status_code=404
        )

    @override_settings(ROOT_URLCONF="apps.core.tests.test_acesso_remoto")
    def test_a_500_nao_expoe_nada_tecnico(self):
        cliente = Client(raise_request_exception=False)
        resposta = cliente.get("/estouro/")
        self.assertEqual(resposta.status_code, 500)

        corpo = resposta.content.decode("utf-8")
        self.assertIn("Algo deu errado do nosso lado", corpo)
        for proibido in ("Traceback", "DATABASE_URL", "SECRET"):
            self.assertNotIn(proibido, corpo)
