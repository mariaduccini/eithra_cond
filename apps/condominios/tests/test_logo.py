"""A logo de um condomínio: o arquivo, a referência e o que se mostra.

A logo é identificação de contexto — não acervo. Aqui se verifica o que a
sustenta: a imagem é conferida pelo conteúdo real, vai para um bucket próprio e
fica guardada apenas como referência; o acesso é temporário e gerado na hora.

Sem logo, o condomínio não fica sem identificação: aparece pelas iniciais do
nome. E quando o armazenamento falha, a tela continua de pé — a imagem some, o
nome permanece.
"""
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from apps.balancos.tests.apoio import StorageDeMentira, jpg, png, webp
from apps.condominios import logo
from apps.condominios.models import Condominio

BUCKET = "bucket-de-logos-de-teste"

AJUSTES = dict(
    EITHRA_CONDOMINIO_LOGO_BUCKET=BUCKET,
    EITHRA_CONDOMINIO_LOGO_MAX_BYTES=1 * 1024 * 1024,
)


def arquivo_de_texto(nome="logo.jpg"):
    """Um arquivo que só tem nome de imagem. O conteúdo não é imagem nenhuma."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile(nome, b"isto nao e uma imagem", content_type="image/jpeg")


class IdentificacaoDoCondominioTests(TestCase):
    """O nome e o que dele se deriva quando não há logo."""

    def test_sem_logo_por_padrao(self):
        condominio = Condominio.objects.create(nome="Rio Vermelho Exclusive")
        self.assertFalse(condominio.tem_logo)
        self.assertFalse(logo.tem_logo(condominio))

    def test_iniciais_pegam_as_primeiras_letras_das_palavras(self):
        condominio = Condominio.objects.create(nome="Rio Vermelho Exclusive")
        self.assertEqual(condominio.iniciais, "RVE")

    def test_nome_de_uma_palavra_da_as_duas_primeiras_letras(self):
        condominio = Condominio.objects.create(nome="Aurora")
        self.assertEqual(condominio.iniciais, "AU")

    def test_nome_vazio_nao_quebra(self):
        condominio = Condominio()
        self.assertEqual(condominio.iniciais, "?")


@override_settings(**AJUSTES)
class GuardaDALogoTests(TestCase):
    """Guardar, trocar e tirar a logo — sempre longe do armazenamento real."""

    def setUp(self):
        self.condominio = Condominio.objects.create(nome="Rio Vermelho Exclusive")
        self.provider = StorageDeMentira(bucket=BUCKET)
        self.obter = patch("apps.condominios.logo.obter_provider", return_value=self.provider)
        self.obter.start()
        self.addCleanup(self.obter.stop)

    def test_guarda_a_logo_e_a_referencia(self):
        logo.definir_logo(self.condominio, png("marca.png"))

        self.condominio.refresh_from_db()
        self.assertTrue(self.condominio.tem_logo)
        self.assertTrue(logo.tem_logo(self.condominio))
        self.assertEqual(self.condominio.logo_storage_key, f"logos/{self.condominio.pk}.png")
        self.assertEqual(self.condominio.logo_storage_provider, "mentira")
        self.assertEqual(self.condominio.logo_storage_bucket, BUCKET)
        self.assertIn(self.condominio.logo_storage_key, self.provider.objetos)

    def test_substituir_no_mesmo_formato_nao_deixa_objeto_orfao(self):
        logo.definir_logo(self.condominio, png("primeira.png"))
        logo.definir_logo(self.condominio, png("segunda.png"))

        self.condominio.refresh_from_db()
        self.assertEqual(self.provider.removidos, [])
        self.assertEqual(len(self.provider.objetos), 1)

    def test_substituir_por_outro_formato_remove_o_objeto_anterior(self):
        logo.definir_logo(self.condominio, png("primeira.png"))
        logo.definir_logo(self.condominio, jpg("segunda.jpg"))

        self.condominio.refresh_from_db()
        self.assertEqual(self.condominio.logo_storage_key, f"logos/{self.condominio.pk}.jpg")
        self.assertEqual(self.provider.removidos, [f"logos/{self.condominio.pk}.png"])
        self.assertEqual(list(self.provider.objetos), [f"logos/{self.condominio.pk}.jpg"])

    def test_tirar_a_logo_limpa_a_referencia_e_o_objeto(self):
        logo.definir_logo(self.condominio, webp("marca.webp"))
        chave = self.condominio.logo_storage_key

        logo.remover_logo(self.condominio)

        self.condominio.refresh_from_db()
        self.assertFalse(self.condominio.tem_logo)
        self.assertEqual(self.condominio.logo_storage_provider, "")
        self.assertEqual(self.condominio.logo_storage_key, "")
        self.assertEqual(self.provider.removidos, [chave])
        self.assertEqual(self.provider.objetos, {})

    def test_falha_de_armazenamento_nao_grava_referencia(self):
        from apps.core.storage import ErroDeStorage

        self.provider.falhar_ao_enviar = True
        with self.assertRaises(ErroDeStorage):
            logo.definir_logo(self.condominio, png("marca.png"))

        self.condominio.refresh_from_db()
        self.assertFalse(self.condominio.tem_logo)

    def test_tirar_a_logo_com_armazenamento_fora_do_ar_ainda_limpa(self):
        logo.definir_logo(self.condominio, png("marca.png"))

        with patch(
            "apps.condominios.logo.obter_provider",
            side_effect=ImproperlyConfigured("sem bucket"),
        ):
            logo.remover_logo(self.condominio)

        self.condominio.refresh_from_db()
        self.assertFalse(self.condominio.tem_logo)


@override_settings(**AJUSTES)
class ConferenciaDoArquivoTests(TestCase):
    """A imagem é conferida pelo que o arquivo é, e não pelo nome que recebeu."""

    def setUp(self):
        self.condominio = Condominio.objects.create(nome="Aurora")

    def test_aceita_png_jpeg_e_webp(self):
        for arquivo in (png(), jpg(), webp()):
            with self.subTest(arquivo=arquivo.name):
                conteudo, mime, extensao = logo.validar_logo(arquivo)
                self.assertTrue(conteudo)
                self.assertIn(mime, {"image/png", "image/jpeg", "image/webp"})
                self.assertIn(extensao, {"png", "jpg", "webp"})

    def test_recusa_arquivo_que_so_tem_nome_de_imagem(self):
        with self.assertRaises(logo.ErroDeLogo):
            logo.validar_logo(arquivo_de_texto())

    def test_recusa_imagem_que_passa_do_limite(self):
        with override_settings(EITHRA_CONDOMINIO_LOGO_MAX_BYTES=10):
            with self.assertRaises(logo.ErroDeLogo):
                logo.validar_logo(png())

    def test_recusa_imagem_vazia(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        vazio = SimpleUploadedFile("vazia.png", b"", content_type="image/png")
        with self.assertRaises(logo.ErroDeLogo):
            logo.validar_logo(vazio)


@override_settings(**AJUSTES)
class AcessoTemporarioTests(TestCase):
    """O acesso à logo é gerado na hora — e nunca derruba a tela."""

    def setUp(self):
        self.condominio = Condominio.objects.create(nome="Aurora")
        self.provider = StorageDeMentira(bucket=BUCKET)
        self.obter = patch("apps.condominios.logo.obter_provider", return_value=self.provider)
        self.obter.start()
        self.addCleanup(self.obter.stop)

    def test_sem_logo_nao_ha_endereco(self):
        self.assertIsNone(logo.acesso_temporario(self.condominio))
        self.assertEqual(logo.endereco(self.condominio), "")

    def test_com_logo_o_endereco_e_assinado_na_hora(self):
        logo.definir_logo(self.condominio, png("marca.png"))

        endereco = logo.endereco(self.condominio)
        self.assertIn(f"logos/{self.condominio.pk}.png", endereco)
        self.assertTrue(self.provider.assinaturas)

    def test_condominio_ausente_nao_tem_endereco(self):
        self.assertEqual(logo.endereco(None), "")

    def test_armazenamento_fora_do_ar_nao_derruba_a_identificacao(self):
        logo.definir_logo(self.condominio, png("marca.png"))

        with patch(
            "apps.condominios.logo.obter_provider",
            side_effect=ImproperlyConfigured("sem bucket"),
        ):
            self.assertEqual(logo.endereco(self.condominio), "")
