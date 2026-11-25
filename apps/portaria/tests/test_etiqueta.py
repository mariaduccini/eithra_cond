"""A leitura da etiqueta: o que o porteiro fotografa vira sugestão.

O motor de OCR é substituído por um texto fixo, e é isso que se prova aqui: a
parte de baixo — casar o que está escrito com a unidade e o morador do
condomínio, reconhecer a transportadora, medir a confiança — é determinística e
não grava nada. Nenhum modelo de linguagem participa da leitura.
"""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.comunidade.constants import TipoVinculoPessoaUnidade
from apps.comunidade.tests import apoio as comunidade_apoio
from apps.condominios.constants import Papel
from apps.core.tests.apoio import png
from apps.demandas.tests.apoio import (
    arquivo_qualquer,
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)
from apps.portaria import etiqueta

# Uma etiqueta plausível: destinatário com rótulo, unidade e transportadora.
TEXTO_DA_ETIQUETA = "DESTINATARIO: Maria Silva\nAPTO 302\nAMAZON"


class BaseDaEtiqueta(TestCase):
    """Um condomínio com a unidade 302 e a moradora Maria Silva."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.porteiro = criar_pessoa("portaria@exemplo.test", "Porteiro")
        criar_vinculo(self.porteiro, self.condominio, papel=Papel.PORTEIRO)
        self.unidade = comunidade_apoio.criar_unidade(
            self.condominio, identificador="302"
        )
        self.pessoa = comunidade_apoio.criar_pessoa_da_comunidade(
            self.condominio, nome="Maria Silva"
        )
        comunidade_apoio.criar_vinculo_pessoa_unidade(
            self.condominio,
            self.pessoa,
            self.unidade,
            tipo_vinculo=TipoVinculoPessoaUnidade.MORADOR,
        )

    def _com_texto(self, texto):
        """Substitui o OCR pelo texto que o teste quer — nada fala com o motor."""
        return patch("apps.portaria.etiqueta._texto_da_foto", return_value=texto)


class LeituraTests(BaseDaEtiqueta):
    """A leitura em si: o texto vira unidade, morador e transportadora."""

    def test_reconhece_unidade_nome_e_transportadora(self):
        with self._com_texto(TEXTO_DA_ETIQUETA):
            dados = etiqueta.ler_etiqueta(b"foto", self.condominio)

        self.assertEqual(dados["unidade_id"], str(self.unidade.pk))
        self.assertEqual(dados["unidade_rotulo"], self.unidade.rotulo)
        self.assertEqual(dados["pessoa_id"], str(self.pessoa.pk))
        self.assertEqual(dados["nome"], "Maria Silva")
        self.assertEqual(dados["transportadora"], "Amazon")
        self.assertEqual(dados["confianca"], "alta")

    def test_sem_unidade_mas_com_nome_fica_media(self):
        with self._com_texto("DESTINATARIO: Maria Silva\nMAGALU"):
            dados = etiqueta.ler_etiqueta(b"foto", self.condominio)

        self.assertEqual(dados["unidade_id"], "")
        self.assertEqual(dados["pessoa_id"], str(self.pessoa.pk))
        self.assertEqual(dados["transportadora"], "Magalu")
        self.assertEqual(dados["confianca"], "media")

    def test_texto_vazio_devolve_contrato_vazio(self):
        with self._com_texto(""):
            dados = etiqueta.ler_etiqueta(b"foto", self.condominio)

        self.assertEqual(dados["unidade_id"], "")
        self.assertEqual(dados["pessoa_id"], "")
        self.assertEqual(dados["nome"], "")
        self.assertEqual(dados["transportadora"], "")
        self.assertEqual(dados["confianca"], "baixa")

    def test_transportadora_desconhecida_fica_vazia(self):
        with self._com_texto("DESTINATARIO: Maria Silva\nAPTO 302\nENTREGADORA X"):
            dados = etiqueta.ler_etiqueta(b"foto", self.condominio)

        self.assertEqual(dados["transportadora"], "")

    def test_nome_muito_diferente_nao_casa_com_moradora(self):
        with self._com_texto("DESTINATARIO: Joaquim Pereira\nAPTO 302"):
            dados = etiqueta.ler_etiqueta(b"foto", self.condominio)

        # A unidade é encontrada, mas ninguém com esse nome mora lá: nada de
        # adivinhar — o nome lido volta como está, sem pessoa vinculada.
        self.assertEqual(dados["unidade_id"], str(self.unidade.pk))
        self.assertEqual(dados["pessoa_id"], "")
        self.assertEqual(dados["nome"], "Joaquim Pereira")
        self.assertEqual(dados["confianca"], "media")

    def test_bloco_divergente_nao_casa_com_a_unidade(self):
        self.unidade.bloco_torre = "A"
        self.unidade.save(update_fields=["bloco_torre"])

        with self._com_texto("APTO 302\nBloco B\nDESTINATARIO: Maria Silva"):
            dados = etiqueta.ler_etiqueta(b"foto", self.condominio)

        # O número existe no cadastro, mas em outro bloco: apontar para essa
        # unidade mandaria a encomenda para o lugar errado.
        self.assertEqual(dados["unidade_id"], "")


class EndpointTests(BaseDaEtiqueta):
    """O endpoint que a tela chama: permissão, validação da foto e resposta."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.porteiro)
        self.url = reverse("portaria:ler_etiqueta")

    def _post(self, **extra):
        dados = {"condominio": str(self.condominio.pk)}
        dados.update(extra)
        return self.client.post(self.url, dados)

    def test_sem_login_redireciona(self):
        self.client.logout()
        resposta = self._post(foto=png())
        self.assertEqual(resposta.status_code, 302)

    def test_morador_recebe_404(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)

        resposta = self._post(foto=png())

        self.assertEqual(resposta.status_code, 404)

    def test_recusa_arquivo_que_nao_e_foto(self):
        resposta = self._post(foto=arquivo_qualquer())
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("erro", resposta.json())

    def test_sem_foto_devolve_erro(self):
        resposta = self._post()
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("erro", resposta.json())

    @patch("apps.portaria.etiqueta.ocr_disponivel", return_value=False)
    def test_avisa_quando_a_leitura_nao_esta_disponivel(self, _ocr):
        resposta = self._post(foto=png())
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("erro", resposta.json())

    @patch("apps.portaria.etiqueta.ocr_disponivel", return_value=True)
    @patch("apps.portaria.etiqueta._texto_da_foto", return_value=TEXTO_DA_ETIQUETA)
    def test_devolve_a_leitura_da_etiqueta(self, _texto, _ocr):
        resposta = self._post(foto=png())

        self.assertEqual(resposta.status_code, 200)
        dados = resposta.json()
        self.assertEqual(dados["unidade_id"], str(self.unidade.pk))
        self.assertEqual(dados["pessoa_id"], str(self.pessoa.pk))
        self.assertEqual(dados["transportadora"], "Amazon")
        self.assertEqual(dados["confianca"], "alta")

    @patch("apps.portaria.etiqueta.ocr_disponivel", return_value=True)
    @patch("apps.portaria.etiqueta._texto_da_foto", return_value=TEXTO_DA_ETIQUETA)
    def test_leitura_nao_grava_nada(self, _texto, _ocr):
        from apps.portaria.models import AcessoPortaria

        antes = AcessoPortaria.objects.count()
        self._post(foto=png())
        self.assertEqual(AcessoPortaria.objects.count(), antes)
