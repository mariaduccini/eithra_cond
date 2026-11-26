"""A marca do alto do menu do shell.

Hoje o alto do menu lateral renderiza uma marca fixa (a imagem do produto no
link do shell), e não a logo do condomínio. Estes testes fixam o que o template
renderiza hoje: a marca do shell aparece, o nome do condomínio não aparece ao
lado, e a logo do condomínio não entra no alto do menu. Olham para a página
inicial porque nela a única marca na tela é a do shell — não há seletor de
condomínio no corpo.
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
        self.pessoa = entrar_como(self.client, Papel.ADMINISTRADORA, self.condominio)
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

    def _marca_do_shell(self, resposta):
        """A marca fixa que o alto do menu renderiza hoje."""
        self.assertContains(resposta, "eithra-brand-link-condominio")
        self.assertContains(resposta, "eithra-brand-implantacao-menu")
        self.assertContains(resposta, "img/logorveoficial.PNG")

    def test_o_alto_do_menu_usa_a_marca_do_shell(self):
        resposta = self._home()
        self._marca_do_shell(resposta)
        self.assertNotContains(resposta, self._logo_guardada(self.condominio))

    def test_o_alto_do_menu_nao_usa_a_marca_fixa_do_produto(self):
        """A identidade principal nunca é a marca do Eithra."""
        menu = self._menu(self._home())
        self.assertNotIn("eithra-mark.svg", menu)
        self.assertNotIn("app-brand-text", menu)

    def test_cadastrar_logo_nao_usa_a_logo_do_condominio_no_shell(self):
        self._salvar(logo=png("marca.png"))
        resposta = self._home()
        self._marca_do_shell(resposta)
        self.assertNotContains(resposta, self._logo_guardada(self.condominio))

    def test_substituir_logo_nao_muda_a_marca_do_shell(self):
        self._salvar(logo=png("primeira.png"))
        self._salvar(logo=jpg("segunda.jpg"))
        resposta = self._home()
        self._marca_do_shell(resposta)
        self.assertNotContains(resposta, f"logos/{self.condominio.pk}.jpg")
        self.assertNotContains(resposta, f"logos/{self.condominio.pk}.png")

    def test_remover_logo_mantem_a_marca_do_shell(self):
        self._salvar(logo=png("marca.png"))
        self._salvar(remover_logo="on")
        resposta = self._home()
        self._marca_do_shell(resposta)
        self.assertNotContains(resposta, self._logo_guardada(self.condominio))

    def test_apos_salvar_o_render_seguinte_mantem_a_marca_do_shell(self):
        resposta = self._salvar(logo=png("marca.png"))
        self.assertEqual(resposta.status_code, 302)
        seguinte = self.client.get(resposta["Location"])
        self._marca_do_shell(seguinte)
        self.assertNotContains(seguinte, self._logo_guardada(self.condominio))

    def test_trocar_de_condominio_mantem_a_marca_do_shell(self):
        outro = criar_condominio("Aurora")
        criar_vinculo(self.pessoa, outro, papel=Papel.SINDICA)
        logo.definir_logo(outro, png("aurora.png"))
        self._salvar(logo=png("rve.png"))

        resposta = self._home(condominio=outro)

        self.assertEqual(resposta.context["condominio_da_marca"], outro)
        self._marca_do_shell(resposta)
        self.assertNotContains(resposta, self._logo_guardada(outro))
        self.assertNotContains(resposta, self._logo_guardada(self.condominio))

    def test_recarregar_mantem_a_marca_do_shell(self):
        self._salvar(logo=png("marca.png"))
        self._marca_do_shell(self._home())
        self._marca_do_shell(self._home())
        self.assertNotContains(self._home(), self._logo_guardada(self.condominio))

    def test_logo_horizontal_nao_usa_a_logo_no_shell(self):
        self._salvar(logo=self._logo_png(nome="horizontal.png", largura=96, altura=32))
        resposta = self._home()
        self._marca_do_shell(resposta)
        self.assertNotContains(resposta, self._logo_guardada(self.condominio))

    def test_logo_vertical_nao_usa_a_logo_no_shell(self):
        self._salvar(logo=self._logo_png(nome="vertical.png", largura=32, altura=96))
        resposta = self._home()
        self._marca_do_shell(resposta)
        self.assertNotContains(resposta, self._logo_guardada(self.condominio))

    def test_logo_quadrada_nao_usa_a_logo_no_shell(self):
        self._salvar(logo=self._logo_png(nome="quadrada.png", largura=64, altura=64))
        resposta = self._home()
        self._marca_do_shell(resposta)
        self.assertNotContains(resposta, self._logo_guardada(self.condominio))

    def test_nao_exibe_o_nome_do_condominio_ao_lado_da_marca(self):
        """A marca aparece sozinha: sem nome ao lado."""
        self._salvar(logo=png("marca.png"))
        menu = self._menu(self._home())
        self.assertNotIn("eithra-contexto-nome", menu)
        self.assertNotIn(f">{self.condominio.nome}<", menu)
