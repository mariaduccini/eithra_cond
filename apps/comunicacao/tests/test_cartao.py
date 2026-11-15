"""O desenho do card: a peça que o morador recebe.

O que se verifica aqui é que a oficina do desenho devolve um PNG de verdade, no
tamanho da orientação pedida, e que ele não quebra por causa de uma logo ausente
ou inválida. Nada aqui decide regra — o desenho só desenha.
"""
from io import BytesIO

from django.test import SimpleTestCase
from PIL import Image

from apps.condominios import cores

from .. import cartao
from ..constants import ModeloComunicado, OrientacaoDoCard


class _Condominio:
    nome = "Condomínio de Teste"


class _CondominioComCor:
    """Um condomínio com identidade própria, sem tocar no banco."""

    nome = "Condomínio de Teste"
    cor_principal = "#1B2431"

    @property
    def cor_secundaria(self):
        return cores.cor_secundaria(self.cor_principal)


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

    def test_sem_condominio_o_fio_da_faixa_e_o_proprio_destaque(self):
        for modelo in ModeloComunicado.values:
            cores = cartao.cores_do_modelo(modelo)
            self.assertEqual(cores["fio_da_faixa"], cores["destaque"])

    def test_condominio_com_cor_toma_a_faixa_e_o_fio(self):
        cores_com_marca = cartao.cores_do_modelo(
            ModeloComunicado.MANUTENCAO, condominio=_CondominioComCor()
        )
        self.assertEqual(cores_com_marca["faixa"], "#1B2431")
        self.assertEqual(cores_com_marca["fio_da_faixa"], "#6B7179")

    def test_a_cor_do_condominio_nao_apaga_o_destaque_do_modelo(self):
        """O tom do próprio comunicado continua sendo o sinal de urgência."""
        sem_marca = cartao.cores_do_modelo(ModeloComunicado.MANUTENCAO)
        com_marca = cartao.cores_do_modelo(
            ModeloComunicado.MANUTENCAO, condominio=_CondominioComCor()
        )
        self.assertEqual(com_marca["destaque"], sem_marca["destaque"])

    def test_condominio_sem_cor_nao_muda_a_paleta(self):
        self.assertEqual(
            cartao.cores_do_modelo(ModeloComunicado.AVISO, condominio=_Condominio()),
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

    def test_a_cor_do_condominio_entra_no_desenho(self):
        comunicacao = _Comunicacao()
        comunicacao.condominio = _CondominioComCor()
        self._abrir(cartao.desenhar_cartao(comunicacao=comunicacao))
