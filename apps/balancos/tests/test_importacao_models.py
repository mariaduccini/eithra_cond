"""Testes do model transitório de importação.

A importação representa um arquivo recebido — nunca um balanço. Estes testes
fixam o que ela guarda, o que ela recusa e o que ela deixa em aberto para a
próxima fase.
"""
import uuid

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.balancos.constants import LayoutBalanco, StatusImportacao
from apps.balancos.models import BalancoMensal, ImportacaoBalanco
from apps.condominios.models import Condominio
from apps.balancos.tests.apoio import HASH_PDF, criar_pessoa


def nova_importacao(condominio, criado_por, **extra):
    """Monta uma importação em memória, ainda não gravada."""
    dados = {
        "condominio": condominio,
        "criado_por": criado_por,
        "nome_original": "balanco-do-mes.pdf",
        "mime_type": "application/pdf",
        "tamanho_bytes": 1024,
        "sha256": HASH_PDF,
        "storage_provider": "supabase",
        "storage_bucket": "eithra-v3-balancos-dev",
        "storage_key": f"balancos/{condominio.pk}/documento.pdf",
    }
    dados.update(extra)
    return ImportacaoBalanco(**dados)


class BaseImportacaoTests(TestCase):
    """Cenário mínimo reutilizado pelos testes do model."""

    def setUp(self):
        self.gestao = criar_pessoa("gestao@exemplo.com.br", "Usuário Gestão")
        self.condominio_a = Condominio.objects.create(nome="Condomínio A")
        self.condominio_b = Condominio.objects.create(nome="Condomínio B")

    def criar(self, condominio=None, **extra):
        importacao = nova_importacao(condominio or self.condominio_a, self.gestao, **extra)
        importacao.save()
        return importacao


class ImportacaoBalancoTests(BaseImportacaoTests):
    """O registro do arquivo recebido, antes de virar balanço."""

    def test_identificador_e_uuid(self):
        importacao = self.criar()
        self.assertIsInstance(importacao.pk, uuid.UUID)

    def test_comeca_recebido(self):
        self.assertEqual(self.criar().status, StatusImportacao.RECEBIDO)

    def test_layout_comeca_desconhecido(self):
        """Nenhum layout é detectado nesta fase: não se inventa valor."""
        self.assertEqual(self.criar().layout_detectado, LayoutBalanco.DESCONHECIDO)

    def test_resultado_extraido_comeca_vazio(self):
        """Não se finge leitura: o campo existe para a próxima fase."""
        self.assertEqual(self.criar().resultado_extraido, {})

    def test_inconsistencias_comecam_vazias(self):
        self.assertEqual(self.criar().inconsistencias, [])

    def test_desfechos_comecam_em_aberto(self):
        importacao = self.criar()
        self.assertIsNone(importacao.expira_em)
        self.assertIsNone(importacao.confirmado_em)
        self.assertIsNone(importacao.descartado_em)
        self.assertFalse(importacao.descartada)

    def test_nao_cria_balanco_mensal(self):
        """Guardar um arquivo não faz nenhum mês passar a valer."""
        self.criar()
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_recusa_url_publica_no_lugar_da_referencia(self):
        importacao = nova_importacao(
            self.condominio_a,
            self.gestao,
            storage_key="https://exemplo.invalido/documento.pdf",
        )
        with self.assertRaises(ValidationError):
            importacao.clean()

    def test_recusa_tamanho_negativo(self):
        importacao = nova_importacao(self.condominio_a, self.gestao, tamanho_bytes=-1)
        with self.assertRaises(ValidationError):
            importacao.clean()

    def test_recusa_confirmacao_e_descarte_juntos(self):
        from django.utils import timezone

        agora = timezone.now()
        importacao = nova_importacao(
            self.condominio_a,
            self.gestao,
            confirmado_em=agora,
            descartado_em=agora,
        )
        with self.assertRaises(ValidationError):
            importacao.clean()

    def test_banco_recusa_confirmacao_e_descarte_juntos(self):
        from django.utils import timezone

        agora = timezone.now()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.criar(confirmado_em=agora, descartado_em=agora)

    def test_mesmo_arquivo_pode_voltar_para_o_mesmo_condominio(self):
        """Não há unicidade: a repetição é sinalizada, não impedida pelo banco."""
        self.criar()
        self.criar()
        self.assertEqual(ImportacaoBalanco.objects.count(), 2)

    def test_mesmo_hash_vale_em_condominio_diferente(self):
        self.criar(condominio=self.condominio_a)
        self.criar(condominio=self.condominio_b)
        self.assertEqual(
            ImportacaoBalanco.objects.filter(sha256=HASH_PDF).count(), 2
        )

    def test_tom_do_estado_acompanha_a_situacao(self):
        self.assertEqual(self.criar(status=StatusImportacao.PRONTO).tom_do_estado, "primary")
        self.assertEqual(self.criar(status=StatusImportacao.ERRO).tom_do_estado, "danger")
        self.assertEqual(
            self.criar(status=StatusImportacao.DESCARTADO).tom_do_estado, "secondary"
        )

    def test_descartada_reconhece_o_descarte(self):
        importacao = self.criar(status=StatusImportacao.DESCARTADO)
        self.assertTrue(importacao.descartada)

    def test_texto_identifica_arquivo_e_situacao(self):
        importacao = self.criar()
        self.assertIn("balanco-do-mes.pdf", str(importacao))
        self.assertIn(importacao.get_status_display(), str(importacao))
