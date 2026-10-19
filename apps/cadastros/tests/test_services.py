"""Testes dos serviços de cadastro: o que muda, muda por inteiro."""
from django.test import TestCase

from apps.cadastros.models import Fornecedor, LocalOperacional
from apps.cadastros.services import (
    criar_equipamento,
    criar_fornecedor,
    criar_local,
    definir_ativo,
    editar_local,
    salvar_condominio,
)
from apps.cadastros.tests.apoio import (
    criar_condominio,
    criar_local as novo_local,
    criar_pessoa,
)


class CriacaoTests(TestCase):
    """Quem cadastrou e em qual condomínio ficam gravados junto."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.pessoa = criar_pessoa("sindica@exemplo.test", "Síndica")

    def test_local_guarda_quem_cadastrou(self):
        local = criar_local(
            condominio=self.condominio,
            criado_por=self.pessoa,
            dados={"nome": "Bloco A", "tipo": "outro", "descricao": ""},
        )
        self.assertEqual(local.condominio, self.condominio)
        self.assertEqual(local.criado_por, self.pessoa)

    def test_equipamento_e_fornecedor_guardam_quem_cadastrou(self):
        equipamento = criar_equipamento(
            condominio=self.condominio,
            criado_por=self.pessoa,
            dados={"nome": "Portão da garagem"},
        )
        fornecedor = criar_fornecedor(
            condominio=self.condominio,
            criado_por=self.pessoa,
            dados={"nome_razao_social": "Elevadores Silva Ltda."},
        )
        self.assertEqual(equipamento.criado_por, self.pessoa)
        self.assertEqual(fornecedor.criado_por, self.pessoa)


class EdicaoTests(TestCase):
    """Corrigir o cadastro não troca de dono nem perde o resto."""

    def test_editar_local_muda_so_o_que_veio(self):
        condominio = criar_condominio("Alpha")
        local = novo_local(condominio, nome="Bloco A", descricao="Perto da portaria")
        editar_local(local=local, dados={"nome": "Bloco A - reformado"})
        local.refresh_from_db()
        self.assertEqual(local.nome, "Bloco A - reformado")
        self.assertEqual(local.descricao, "Perto da portaria")
        self.assertEqual(local.condominio, condominio)

    def test_salvar_condominio_normaliza_o_que_foi_digitado(self):
        condominio = criar_condominio("Alpha")
        salvar_condominio(
            condominio=condominio,
            dados={
                "nome": "  Residencial   Alpha ",
                "cnpj": "12.345.678/0001-95",
                "uf": "ba",
                "cep": "40000-000",
                "quantidade_unidades": 120,
            },
        )
        condominio.refresh_from_db()
        self.assertEqual(condominio.nome, "Residencial Alpha")
        self.assertEqual(condominio.cnpj, "12345678000195")
        self.assertEqual(condominio.uf, "BA")
        self.assertEqual(condominio.cep, "40000000")
        self.assertEqual(condominio.quantidade_unidades, 120)


class DesativacaoTests(TestCase):
    """Desativar tira de circulação sem apagar."""

    def test_definir_ativo_desativa_e_reativa(self):
        condominio = criar_condominio("Alpha")
        local = novo_local(condominio)

        definir_ativo(objeto=local, ativo=False)
        local.refresh_from_db()
        self.assertFalse(local.ativo)

        definir_ativo(objeto=local, ativo=True)
        local.refresh_from_db()
        self.assertTrue(local.ativo)
        self.assertEqual(LocalOperacional.objects.filter(pk=local.pk).count(), 1)

    def test_cadastro_desativado_continua_no_banco(self):
        condominio = criar_condominio("Alpha")
        fornecedor = criar_fornecedor(
            condominio=condominio,
            criado_por=None,
            dados={"nome_razao_social": "Zelador"},
        )
        definir_ativo(objeto=fornecedor, ativo=False)
        self.assertTrue(Fornecedor.objects.filter(pk=fornecedor.pk).exists())
        self.assertFalse(Fornecedor.objects.ativos().filter(pk=fornecedor.pk).exists())
