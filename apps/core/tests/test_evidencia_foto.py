"""A identificação visual compartilhada das evidências fotográficas.

A marca é um reforço: grava, na própria imagem, o condomínio, a hora do
servidor, quem registrou e a referência do objeto. Estes testes prendem o que
importa do contrato: a imagem marcada é diferente da original e continua no
mesmo formato; a marca ocupa a base e não toca o topo; e, quando não dá para
marcar, o arquivo segue intacto — a evidência nunca deixa de ser guardada por
causa do desenho.
"""
import io

from django.test import SimpleTestCase
from django.utils import timezone
from PIL import Image

from apps.core.evidencia_foto import FORMATOS, marcar_evidencia
from apps.core.tests.apoio import gerar_png


def agora():
    return timezone.localtime()


def _abrir(conteudo: bytes) -> Image.Image:
    return Image.open(io.BytesIO(conteudo)).convert("RGB")


class MarcaDaEvidenciaTests(SimpleTestCase):
    """A imagem que comprova um fato carrega a sua própria identificação."""

    def _png(self, largura=320, altura=240) -> bytes:
        return gerar_png(largura=largura, altura=altura)

    def _marcar(self, conteudo: bytes, mime_type="image/png") -> bytes:
        return marcar_evidencia(
            conteudo,
            mime_type=mime_type,
            condominio="Condomínio Alpha",
            registrado_em=agora(),
            autor="Maria Porteira",
            referencia="Visitante Exemplo",
        )

    def test_marca_a_imagem_e_preserva_o_formato(self):
        original = self._png()

        marcada = self._marcar(original)

        self.assertTrue(marcada.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertNotEqual(marcada, original)
        self.assertEqual(_abrir(marcada).size, (320, 240))

    def test_a_marca_fica_na_base_sem_tocar_o_topo(self):
        original = self._png()

        marcada = self._marcar(original)

        antes = _abrir(original)
        depois = _abrir(marcada)
        # O topo da foto é preservado: a identificação não cobre a imagem.
        self.assertEqual(
            antes.crop((0, 0, 320, 100)).tobytes(),
            depois.crop((0, 0, 320, 100)).tobytes(),
        )
        # A base muda: é onde a faixa de identificação é desenhada.
        self.assertNotEqual(
            antes.crop((0, 210, 320, 240)).tobytes(),
            depois.crop((0, 210, 320, 240)).tobytes(),
        )

    def test_jpeg_permanece_jpeg(self):
        buffer = io.BytesIO()
        Image.new("RGB", (320, 240), (90, 140, 190)).save(buffer, format="JPEG")
        original = buffer.getvalue()

        marcada = self._marcar(original, mime_type="image/jpeg")

        self.assertTrue(marcada.startswith(b"\xff\xd8\xff"))
        self.assertNotEqual(marcada, original)
        self.assertEqual(_abrir(marcada).size, (320, 240))

    def test_formato_desconhecido_devolve_a_original(self):
        original = self._png()

        self.assertEqual(
            marcar_evidencia(original, mime_type="application/pdf"), original
        )

    def test_conteudo_vazio_devolve_a_original(self):
        self.assertEqual(marcar_evidencia(b"", mime_type="image/png"), b"")

    def test_imagem_ilegivel_devolve_a_original(self):
        # O navegador diz que é PNG, mas o conteúdo não abre: guarda-se como veio.
        ruim = b"\x89PNG\r\n\x1a\n" + b"lixo" * 20

        self.assertEqual(marcar_evidencia(ruim, mime_type="image/png"), ruim)

    def test_sem_rotulo_devolve_a_original(self):
        original = self._png()

        self.assertEqual(marcar_evidencia(original, mime_type="image/png"), original)


class FormatosTests(SimpleTestCase):
    """A lista de formatos é a mesma régua de leitura das evidências."""

    def test_cobre_os_formatos_aceitos(self):
        self.assertEqual(
            set(FORMATOS),
            {"image/jpeg", "image/png", "image/webp"},
        )
