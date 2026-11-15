"""O desenho do card: a peça que o morador recebe.

O que se verifica aqui é que a oficina do desenho devolve um PNG de verdade, no
tamanho da orientação pedida, e que ele não quebra por causa de uma logo ausente
ou inválida. Nada aqui decide regra — o desenho só desenha.
"""
from io import BytesIO

from django.test import SimpleTestCase
from PIL import Image

from .. import cartao
from ..constants import ModeloComunicado, OrientacaoDoCard


class _Condominio:
    nome = "Condomínio de Teste"


class _Comunicacao:
    """Um comunicado do mínimo que o desenho lê, sem tocar no banco."""

    def __init__(self, **campos):
        self.condominio = _Condominio()
        self.modelo = campos.get("modelo", ModeloComunicado.AVISO)
        self.orientacao = campos.get("orientacao", OrientacaoDoCard.VERTICAL)
        self.titulo = campos.get("titulo", "Aviso aos moradores")
        self.mensagem = campos.get("mensagem", "O elevador para na sexta-feira.")
        self.local = campos.get("local", "Torre A")
        self.periodo = campos.get("periodo")

    def get_modelo_display(self):
        return ModeloComunicado(self.modelo).label

    @property
    def periodo_por_extenso(self):
        return self.periodo.strftime("%d/%m/%Y") if self.periodo else ""


class CoresDoModeloTests(SimpleTestCase):
    def test_cada_modelo_tem_paleta_completa(self):
        for modelo in ModeloComunicado.values:
            cores = cartao.cores_do_modelo(modelo)
            for chave in ("faixa", "destaque", "fundo", "titulo", "texto", "rodape"):
                self.assertIn(chave, cores, msg=f"{modelo}: falta {chave}")

    def test_modelo_desconhecido_cai_no_aviso(self):
        self.assertEqual(
            cartao.cores_do_modelo("inexistente"),
            cartao.cores_do_modelo(ModeloComunicado.AVISO),
        )


class DesenharCartaoTests(SimpleTestCase):
    def _abrir(self, dados):
        self.assertTrue(dados.startswith(b"\x89PNG\r\n\x1a\n"))
        return Image.open(BytesIO(dados))

    def test_devolve_png_no_tamanho_vertical(self):
        dados = cartao.desenhar_cartao(comunicacao=_Comunicacao())
        imagem = self._abrir(dados)
        self.assertEqual(imagem.size, cartao.TAMANHOS[OrientacaoDoCard.VERTICAL])

    def test_devolve_png_no_tamanho_horizontal(self):
        dados = cartao.desenhar_cartao(
            comunicacao=_Comunicacao(orientacao=OrientacaoDoCard.HORIZONTAL)
        )
        imagem = self._abrir(dados)
        self.assertEqual(imagem.size, cartao.TAMANHOS[OrientacaoDoCard.HORIZONTAL])

    def test_logo_ausente_nao_quebra(self):
        dados = cartao.desenhar_cartao(comunicacao=_Comunicacao(), logo_bytes=None)
        self.assertTrue(dados)

    def test_logo_invalida_nao_quebra(self):
        dados = cartao.desenhar_cartao(
            comunicacao=_Comunicacao(), logo_bytes=b"isto nao e uma imagem"
        )
        self._abrir(dados)

    def test_logo_valida_e_embutida_sem_deformar(self):
        from apps.core.tests.apoio import gerar_png

        dados = cartao.desenhar_cartao(
            comunicacao=_Comunicacao(), logo_bytes=gerar_png(largura=64, altura=16)
        )
        self._abrir(dados)

    def test_titulo_longo_nao_quebra_o_desenho(self):
        dados = cartao.desenhar_cartao(
            comunicacao=_Comunicacao(titulo="Palavra " * 40)
        )
        self._abrir(dados)
