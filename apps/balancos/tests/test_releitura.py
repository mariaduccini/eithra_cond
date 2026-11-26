"""O balancete escaneado reconhecido — e a releitura do que ficou sem leitura.

Um PDF só de imagem não tem camada textual: sem o OCR local, os marcadores
legados nunca aparecem e o documento fica desconhecido. Aqui se trava o
caminho novo — OCR na abertura, uma página — e a releitura segura: só para o
que não tem leitura nem desfecho, sempre sobre o arquivo já guardado.
"""
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.balancos.constants import LayoutBalanco, StatusImportacao
from apps.balancos.detector import (
    AVISO_SEM_OCR,
    detectar_layout_balanco,
    detectar_layout_do_pdf,
)
from apps.balancos.models import ImportacaoBalanco
from apps.balancos.services import reler_importacao
from apps.balancos.tests.apoio import (
    HASH_PDF,
    PaginaDeImagem,
    StorageDeMentira,
    criar_pessoa,
    criar_vinculo,
    gerar_pdf_textual,
)
from apps.condominios.constants import Papel
from apps.condominios.models import Condominio


def pdf_escaneado() -> bytes:
    """Um PDF só de imagem: nenhuma página tem camada textual."""
    return gerar_pdf_textual(PaginaDeImagem())


class BaseDoBalanceteTests(TestCase):
    """Condomínio, síndica e o storage em memória, sem rede."""

    def setUp(self):
        self.condominio = Condominio.objects.create(nome="Condomínio A")
        self.sindica = criar_pessoa("sindica@exemplo.com.br", "Síndica Teste")
        criar_vinculo(self.sindica, self.condominio, Papel.SINDICA)

        self.provider = StorageDeMentira()
        self.obter_provider = patch(
            "apps.balancos.services.obter_provider", return_value=self.provider
        )
        self.obter_provider.start()
        self.addCleanup(self.obter_provider.stop)

    def importacao(self, *, layout=LayoutBalanco.DESCONHECIDO, status=StatusImportacao.PRONTO):
        importacao = ImportacaoBalanco.objects.create(
            condominio=self.condominio,
            criado_por=self.sindica,
            nome_original="Balancete Jan 2024 (2).pdf",
            mime_type="application/pdf",
            tamanho_bytes=128,
            sha256=HASH_PDF,
            storage_provider="mentira",
            storage_bucket="bucket-de-teste",
            storage_key="balancos/documento.pdf",
            status=status,
            layout_detectado=layout,
        )
        self.provider.objetos[importacao.storage_key] = pdf_escaneado()
        return importacao


class BalanceteEscaneadoTests(BaseDoBalanceteTests):
    """O PDF só de imagem passa a ser reconhecido pelo OCR da abertura."""

    def test_ocr_reconhece_o_balancete_e_classifica_como_legado(self):
        with patch(
            "apps.balancos.detector._texto_de_ocr_da_abertura",
            return_value=("Balancete de janeiro de 2024", ""),
        ):
            resultado = detectar_layout_do_pdf(pdf_escaneado())

        self.assertEqual(resultado.layout, LayoutBalanco.LEGADO)

    def test_balancete_escaneado_passa_pelo_extrator_legado(self):
        importacao = self.importacao()
        with patch(
            "apps.balancos.detector._texto_de_ocr_da_abertura",
            return_value=("Balancete de janeiro de 2024", ""),
        ), patch("apps.balancos.services.extrair_balanco_legado") as extrair:
            reler_importacao(importacao)

        importacao.refresh_from_db()
        self.assertEqual(importacao.layout_detectado, LayoutBalanco.LEGADO)
        extrair.assert_called_once()

    def test_sem_ocr_fica_desconhecido_com_a_mensagem(self):
        importacao = self.importacao()
        with patch("apps.estoque.leitura_compra.ocr_disponivel", return_value=False):
            detectar_layout_balanco(importacao, conteudo=pdf_escaneado())

        importacao.refresh_from_db()
        self.assertEqual(importacao.layout_detectado, LayoutBalanco.DESCONHECIDO)
        self.assertEqual(importacao.status, StatusImportacao.PRONTO)
        self.assertEqual(importacao.erro_processamento, AVISO_SEM_OCR)


class LerDeNovoTests(BaseDoBalanceteTests):
    """"Ler de novo" só aparece para o que ainda não tem leitura nem desfecho."""

    def _lista(self):
        self.client.force_login(self.sindica)
        return self.client.get(reverse("balancos:importacoes"))

    def test_nao_aparece_para_importacao_confirmada(self):
        importacao = self.importacao(layout=LayoutBalanco.LEGADO)
        importacao.confirmado_em = timezone.now()
        importacao.save(update_fields=["confirmado_em", "atualizado_em"])

        resposta = self._lista()

        self.assertNotContains(resposta, reverse("balancos:reler", args=[importacao.pk]))
        self.assertNotContains(resposta, "Ler de novo")


class ComandoRelerImportacoesTests(BaseDoBalanceteTests):
    """O comando pega só as importações de layout desconhecido."""

    def test_so_pega_desconhecidas(self):
        desconhecida = self.importacao(layout=LayoutBalanco.DESCONHECIDO)
        self.importacao(layout=LayoutBalanco.LEGADO)

        with patch(
            "apps.balancos.management.commands.reler_importacoes.reler_importacao"
        ) as reler:
            call_command("reler_importacoes", "--desconhecidas")

        reler.assert_called_once()
        self.assertEqual(reler.call_args.args[0].pk, desconhecida.pk)
