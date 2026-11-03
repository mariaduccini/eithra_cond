"""A marca do alto do menu é a identidade do condomínio em vista.

O shell diz em qual condomínio a pessoa está operando: a logo dele quando
existe, as iniciais do nome quando não. Nunca a marca fixa do produto, nunca o
nome ao lado, e nunca a identidade de um condomínio diferente do que está
aberto. Estes testes olham para a página inicial porque nela a única marca na
tela é a do shell — não há seletor de condomínio no corpo.
"""
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.tests.apoio import StorageDeMentira, gerar_png, jpg, png
from apps.cadastros.tests.apoio import criar_condominio, criar_vinculo, entrar_como
from apps.condominios import logo
from apps.condominios.constants import Papel


@override_settings(
    EITHRA_CONDOMINIO_LOGO_BUCKET="bucket-de-logos-de-teste",
    EITHRA_CONDOMINIO_LOGO_MAX_BYTES=1 * 1024 * 1024,
)
class MarcaDoShellTests(TestCase):
    """O alto do menu acompanha a logo do condomínio ativo."""

    def setUp(self):
        self.condominio = criar_condominio("Rio Vermelho Exclusive")
        self.pessoa = entrar_como(self.client, Papel.SINDICA, self.condominio)
        self.provider = StorageDeMentira(bucket="bucket-de-logos-de-teste")
        self.obter = patch(
            "apps.condominios.logo.obter_provider", return_value=self.provider
        )
        self.obter.start()
        self.addCleanup(self.obter.stop)
        self.url_edicao = reverse(
            "cadastros:editar_condominio", args=[self.condominio.pk]
        )

    def _home(self, condominio=None):
        url = reverse("core:home")
        if condominio is not None:
            url = f"{url}?condominio={condominio.pk}"
        return self.client.get(url)

    def _salvar(self, **extra):
        dados = {"nome": self.condominio.nome}
        dados.update(extra)
        return self.client.post(self.url_edicao, dados)

    def _logo_guardada(self, condominio):
        return f"logos/{condominio.pk}"

    def _logo_png(self, *, nome, largura, altura):
        """Uma logo PNG de teste com a proporção pedida."""
        return SimpleUploadedFile(
            nome,
            gerar_png(largura=largura, altura=altura),
            content_type="image/png",
        )

    def _menu(self, resposta):
        """O trecho do menu lateral, onde vive a identidade principal."""
        html = resposta.content.decode()
        return html[html.index('class="app-brand"') : html.index("</aside>")]

    def test_sem_logo_o_alto_do_menu_usa_as_iniciais(self):
        resposta = self._home()
        self.assertContains(resposta, "eithra-logo-condominio-iniciais")
        self.assertContains(resposta, "RVE")
        self.assertNotContains(resposta, self._logo_guardada(self.condominio))

    def test_o_alto_do_menu_nao_usa_a_marca_fixa_do_produto(self):
        """A identidade principal nunca é a marca do Eithra."""
        menu = self._menu(self._home())
        self.assertNotIn("eithra-mark.svg", menu)
        self.assertNotIn("app-brand-text", menu)

    def test_cadastrar_logo_o_alto_do_menu_passa_a_mostrar_a_logo(self):
        self._salvar(logo=png("marca.png"))
        resposta = self._home()
        self.assertContains(resposta, self._logo_guardada(self.condominio))
        self.assertNotContains(resposta, "eithra-logo-condominio-iniciais")
        self.assertContains(resposta, "eithra-logo-condominio-imagem--shell")

    def test_substituir_logo_o_alto_do_menu_mostra_a_nova(self):
        self._salvar(logo=png("primeira.png"))
        self._salvar(logo=jpg("segunda.jpg"))
        resposta = self._home()
        self.assertContains(resposta, f"logos/{self.condominio.pk}.jpg")
        self.assertNotContains(resposta, f"logos/{self.condominio.pk}.png")

    def test_remover_logo_o_alto_do_menu_volta_as_iniciais(self):
        self._salvar(logo=png("marca.png"))
        self._salvar(remover_logo="on")
        resposta = self._home()
        self.assertContains(resposta, "eithra-logo-condominio-iniciais")
        self.assertContains(resposta, "RVE")
        self.assertNotContains(resposta, self._logo_guardada(self.condominio))

    def test_apos_salvar_o_render_seguinte_ja_usa_a_nova_logo(self):
        resposta = self._salvar(logo=png("marca.png"))
        self.assertEqual(resposta.status_code, 302)
        self.assertContains(
            self.client.get(resposta["Location"]),
            self._logo_guardada(self.condominio),
        )

    def test_trocar_de_condominio_troca_a_marca(self):
        outro = criar_condominio("Aurora")
        criar_vinculo(self.pessoa, outro, papel=Papel.SINDICA)
        logo.definir_logo(outro, png("aurora.png"))
        self._salvar(logo=png("rve.png"))

        resposta = self._home(condominio=outro)

        self.assertEqual(resposta.context["condominio_da_marca"], outro)
        self.assertContains(resposta, self._logo_guardada(outro))
        self.assertNotContains(resposta, self._logo_guardada(self.condominio))

    def test_recarregar_mantem_a_marca_correta(self):
        self._salvar(logo=png("marca.png"))
        self.assertContains(self._home(), self._logo_guardada(self.condominio))
        self.assertContains(self._home(), self._logo_guardada(self.condominio))

    def test_logo_horizontal_aparece_no_shell(self):
        self._salvar(logo=self._logo_png(nome="horizontal.png", largura=96, altura=32))
        resposta = self._home()
        self.assertContains(resposta, self._logo_guardada(self.condominio))
        self.assertContains(resposta, "eithra-logo-condominio-imagem--shell")

    def test_logo_vertical_aparece_no_shell(self):
        self._salvar(logo=self._logo_png(nome="vertical.png", largura=32, altura=96))
        resposta = self._home()
        self.assertContains(resposta, self._logo_guardada(self.condominio))
        self.assertContains(resposta, "eithra-logo-condominio-imagem--shell")

    def test_logo_quadrada_aparece_no_shell(self):
        self._salvar(logo=self._logo_png(nome="quadrada.png", largura=64, altura=64))
        resposta = self._home()
        self.assertContains(resposta, self._logo_guardada(self.condominio))
        self.assertContains(resposta, "eithra-logo-condominio-imagem--shell")

    def test_nao_exibe_o_nome_do_condominio_ao_lado_da_marca(self):
        """A marca aparece sozinha: sem nome ao lado."""
        self._salvar(logo=png("marca.png"))
        menu = self._menu(self._home())
        self.assertNotIn("eithra-contexto-nome", menu)
        self.assertNotIn(f">{self.condominio.nome}<", menu)
