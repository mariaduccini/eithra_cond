"""Testes dos cadastros: o que o domínio aceita e o que recusa."""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.cadastros.constants import TipoLocal, TipoPessoa
from apps.cadastros.models import Equipamento, Fornecedor, LocalOperacional
from apps.cadastros.tests.apoio import (
    criar_condominio,
    criar_equipamento,
    criar_fornecedor,
    criar_local,
)


class LocalTests(TestCase):
    """Um lugar do condomínio, e de mais nenhum."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")

    def test_nasce_ativo_e_com_tipo_outro(self):
        local = criar_local(self.condominio)
        self.assertTrue(local.ativo)
        self.assertEqual(local.tipo, TipoLocal.OUTRO)

    def test_nome_e_normalizado_ao_guardar(self):
        local = criar_local(self.condominio, nome="  Bloco   A  ")
        self.assertEqual(local.nome, "Bloco A")

    def test_descricao_preserva_paragrafos(self):
        local = criar_local(self.condominio, descricao="  Linha 1\n\nLinha 2  ")
        self.assertEqual(local.descricao, "Linha 1\n\nLinha 2")

    def test_sem_nome_e_recusado(self):
        with self.assertRaises(ValidationError):
            LocalOperacional(condominio=self.condominio, nome="   ").full_clean()

    def test_nome_repetido_no_mesmo_condominio_e_recusado(self):
        criar_local(self.condominio, nome="Bloco A")
        with self.assertRaises(IntegrityError), transaction.atomic():
            criar_local(self.condominio, nome="bloco a")

    def test_mesmo_nome_em_outro_condominio_e_aceito(self):
        outro = criar_condominio("Beta")
        criar_local(self.condominio, nome="Bloco A")
        local = criar_local(outro, nome="Bloco A")
        self.assertEqual(local.condominio, outro)

    def test_queryset_filtra_por_condominio_e_ativos(self):
        criar_local(self.condominio, nome="Bloco A")
        criar_local(self.condominio, nome="Bloco B", ativo=False)
        criar_local(criar_condominio("Beta"), nome="Bloco C")
        self.assertEqual(LocalOperacional.objects.ativos().do_condominio(self.condominio).count(), 1)

    def test_do_condominio_sem_condominio_devolve_vazio(self):
        criar_local(self.condominio)
        self.assertFalse(LocalOperacional.objects.do_condominio(None).exists())


class EquipamentoTests(TestCase):
    """O equipamento vive no condomínio e aponta para um local dele."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.local = criar_local(self.condominio, nome="Casa de máquinas")

    def test_pertence_ao_condominio_e_pode_ficar_sem_local(self):
        equipamento = criar_equipamento(self.condominio)
        self.assertEqual(equipamento.condominio, self.condominio)
        self.assertIsNone(equipamento.local)

    def test_local_de_outro_condominio_e_recusado(self):
        alheio = criar_local(criar_condominio("Beta"), nome="Bloco do vizinho")
        equipamento = Equipamento(
            condominio=self.condominio, nome="Portão", local=alheio
        )
        with self.assertRaises(ValidationError) as capturado:
            equipamento.full_clean()
        self.assertIn("local", capturado.exception.message_dict)

    def test_sem_nome_e_recusado(self):
        with self.assertRaises(ValidationError):
            Equipamento(condominio=self.condominio, nome="").full_clean()

    def test_nome_repetido_no_mesmo_local_e_recusado(self):
        criar_equipamento(self.condominio, nome="Portão", local=self.local)
        with self.assertRaises(IntegrityError), transaction.atomic():
            criar_equipamento(self.condominio, nome="portão", local=self.local)

    def test_busca_encontra_por_fabricante_e_serie(self):
        criar_equipamento(self.condominio, nome="Portão", fabricante="Genius")
        criar_equipamento(self.condominio, nome="Bomba", numero_serie="SN-9981")
        self.assertEqual(
            Equipamento.objects.do_condominio(self.condominio).buscar("genius").count(), 1
        )
        self.assertEqual(
            Equipamento.objects.do_condominio(self.condominio).buscar("9981").count(), 1
        )

    def test_por_local_encontra_os_equipamentos_do_lugar(self):
        criar_equipamento(self.condominio, nome="Bomba 1", local=self.local)
        criar_equipamento(self.condominio, nome="Bomba 2")
        self.assertEqual(Equipamento.objects.por_local(self.local).count(), 1)


class FornecedorTests(TestCase):
    """Quem atende o condomínio, identificado pelo documento dentro dele."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")

    def test_documento_e_telefone_guardados_so_com_digitos(self):
        fornecedor = criar_fornecedor(
            self.condominio,
            nome_razao_social="Elevadores Silva Ltda.",
            documento="12.345.678/0001-95",
            telefone="(71) 99999-8888",
        )
        self.assertEqual(fornecedor.documento, "12345678000195")
        self.assertEqual(fornecedor.telefone, "71999998888")

    def test_documento_formatado_para_leitura(self):
        fornecedor = criar_fornecedor(
            self.condominio, documento="12.345.678/0001-95", tipo_pessoa=TipoPessoa.PJ
        )
        self.assertEqual(fornecedor.documento_formatado, "12.345.678/0001-95")

    def test_cpf_com_tamanho_errado_e_recusado(self):
        fornecedor = Fornecedor(
            condominio=self.condominio,
            nome_razao_social="Eletricista",
            tipo_pessoa=TipoPessoa.PF,
            documento="123",
        )
        with self.assertRaises(ValidationError) as capturado:
            fornecedor.full_clean()
        self.assertIn("documento", capturado.exception.message_dict)

    def test_sem_documento_o_cadastro_continua_valido(self):
        fornecedor = criar_fornecedor(self.condominio, nome_razao_social="Zelador")
        fornecedor.full_clean()
        self.assertEqual(fornecedor.documento, "")

    def test_mesmo_documento_no_condominio_e_recusado(self):
        criar_fornecedor(self.condominio, documento="12345678000195")
        with self.assertRaises(IntegrityError), transaction.atomic():
            criar_fornecedor(
                self.condominio,
                nome_razao_social="Outro nome",
                documento="12345678000195",
            )

    def test_dois_cadastros_sem_documento_sao_aceitos(self):
        criar_fornecedor(self.condominio, nome_razao_social="Zelador")
        criar_fornecedor(self.condominio, nome_razao_social="Jardineiro")
        self.assertEqual(Fornecedor.objects.do_condominio(self.condominio).count(), 2)

    def test_mesmo_documento_em_outro_condominio_e_aceito(self):
        outro = criar_condominio("Beta")
        criar_fornecedor(self.condominio, documento="12345678000195")
        fornecedor = criar_fornecedor(outro, documento="12345678000195")
        self.assertEqual(fornecedor.condominio, outro)
