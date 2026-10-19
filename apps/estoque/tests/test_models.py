"""Modelos de estoque e compras: o saldo derivado e o que o banco recusa.

Os testes daqui olham para duas coisas. A primeira é o que o modelo calcula:
o saldo nunca é campo, é a soma das movimentações. A segunda é o que o banco
guarda por conta própria — a unicidade do item e as coerências que não podem
depender só da boa vontade de quem grava.
"""
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.estoque.constants import (
    DestinoDoItem,
    SituacaoDoSaldo,
    StatusCompra,
    TipoMovimentacaoEstoque,
)
from apps.estoque.models import Compra, ItemCompra, ItemEstoque, MovimentacaoEstoque
from apps.estoque.tests.apoio import (
    criar_condominio,
    criar_item_estoque,
    criar_pessoa,
)


class BaseDeModelos(TestCase):
    """Uma gestão com um condomínio e um item, para o que se repete."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        self.item = criar_item_estoque(self.condominio, self.gestao)

    def _movimento(self, tipo, quantidade, **campos):
        return MovimentacaoEstoque.objects.create(
            condominio=self.condominio,
            item=self.item,
            tipo=tipo,
            quantidade=quantidade,
            usuario=self.gestao,
            **campos,
        )

    def _compra(self, **campos):
        base = {"condominio": self.condominio, "data_compra": date.today()}
        base.update(campos)
        return Compra.objects.create(**base)


class SaldoDerivadoTests(BaseDeModelos):
    """O saldo é leitura, não campo: entradas menos saídas."""

    def test_saldo_e_a_entrada_menos_a_saida(self):
        self._movimento(TipoMovimentacaoEstoque.ENTRADA_COMPRA, Decimal("10"))
        self._movimento(
            TipoMovimentacaoEstoque.SAIDA_USO, Decimal("4"), motivo="Consumo no dia"
        )

        lido = ItemEstoque.objects.com_saldo().get(pk=self.item.pk)
        self.assertEqual(lido.saldo, Decimal("6"))

    def test_sem_movimento_o_saldo_e_zero(self):
        lido = ItemEstoque.objects.com_saldo().get(pk=self.item.pk)
        self.assertEqual(lido.saldo, Decimal("0"))

    def test_situacao_le_o_saldo_ao_lado_do_minimo(self):
        self.item.estoque_minimo = Decimal("5")
        self.item.save()
        self._movimento(TipoMovimentacaoEstoque.ENTRADA_COMPRA, Decimal("10"))

        lido = ItemEstoque.objects.com_saldo().get(pk=self.item.pk)
        self.assertEqual(lido.situacao_do_saldo, SituacaoDoSaldo.NORMAL)

        lido.estoque_minimo = Decimal("20")
        self.assertEqual(lido.situacao_do_saldo, SituacaoDoSaldo.ABAIXO_DO_MINIMO)

    def test_zerado_tem_precedencia_sobre_o_minimo(self):
        self.item.estoque_minimo = Decimal("5")
        self.item.save()

        lido = ItemEstoque.objects.com_saldo().get(pk=self.item.pk)
        self.assertEqual(lido.situacao_do_saldo, SituacaoDoSaldo.ZERADO)
        self.assertTrue(lido.esta_zerado)

    def test_sem_saldo_anotado_nao_se_inventa_situacao(self):
        self.assertEqual(self.item.situacao_do_saldo, "")


class UnicidadeDoItemTests(BaseDeModelos):
    """O mesmo item não se repete no condomínio, mesmo com outra caixa."""

    def test_mesmo_nome_com_outra_caixa_e_recusado_pelo_modelo(self):
        repetido = ItemEstoque(condominio=self.condominio, nome="lâmpada led")
        with self.assertRaises(ValidationError):
            repetido.full_clean()

    def test_mesmo_nome_com_outra_caixa_e_recusado_pelo_banco(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ItemEstoque.objects.create(
                    condominio=self.condominio, nome="LÂMPADA LED"
                )

    def test_mesmo_nome_em_outro_condominio_e_permitido(self):
        outro = criar_condominio("Beta")
        item = ItemEstoque.objects.create(condominio=outro, nome="Lâmpada LED")
        self.assertEqual(item.nome, "Lâmpada LED")

    def test_nome_e_guardado_sem_espacos_repetidos(self):
        item = ItemEstoque.objects.create(
            condominio=self.condominio, nome="  Filtro   de   água  "
        )
        self.assertEqual(item.nome, "Filtro de água")


class MovimentacaoTests(BaseDeModelos):
    """A quantidade é sempre positiva; o tipo é que diz o sentido."""

    def test_direcao_vem_do_tipo(self):
        entrada = self._movimento(TipoMovimentacaoEstoque.ENTRADA_COMPRA, Decimal("3"))
        saida = self._movimento(
            TipoMovimentacaoEstoque.SAIDA_USO, Decimal("1"), motivo="Uso"
        )
        self.assertTrue(entrada.soma_ao_saldo)
        self.assertFalse(saida.soma_ao_saldo)

    def test_quantidade_zero_e_recusada_pelo_banco(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._movimento(TipoMovimentacaoEstoque.ENTRADA_COMPRA, Decimal("0"))

    def test_movimento_que_exige_motivo_sem_motivo_e_recusado(self):
        movimento = MovimentacaoEstoque(
            condominio=self.condominio,
            item=self.item,
            tipo=TipoMovimentacaoEstoque.PERDA,
            quantidade=Decimal("1"),
            usuario=self.gestao,
        )
        with self.assertRaises(ValidationError):
            movimento.full_clean()

    def test_entrada_por_compra_nao_exige_motivo(self):
        movimento = self._movimento(
            TipoMovimentacaoEstoque.ENTRADA_COMPRA, Decimal("2")
        )
        self.assertEqual(movimento.motivo, "")


class CompraECoerenciaTests(BaseDeModelos):
    """A situação diz o que aconteceu; a data diz quando."""

    def test_compra_nasce_rascunho_e_editavel(self):
        compra = self._compra()
        self.assertEqual(compra.status, StatusCompra.RASCUNHO)
        self.assertTrue(compra.pode_editar)
        self.assertIsNone(compra.confirmada_em)

    def test_confirmada_sem_data_e_recusada(self):
        compra = Compra(
            condominio=self.condominio,
            data_compra=date.today(),
            status=StatusCompra.CONFIRMADA,
        )
        with self.assertRaises(ValidationError):
            compra.full_clean()

    def test_rascunho_com_data_de_confirmacao_e_recusado(self):
        compra = Compra(
            condominio=self.condominio,
            data_compra=date.today(),
            status=StatusCompra.RASCUNHO,
            confirmada_em=timezone.now(),
        )
        with self.assertRaises(ValidationError):
            compra.full_clean()

    def test_total_e_a_soma_das_linhas(self):
        compra = self._compra()
        ItemCompra.objects.create(
            compra=compra,
            item_estoque=self.item,
            descricao="Lâmpada",
            quantidade=Decimal("4"),
            valor_unitario=Decimal("2.50"),
            destino=DestinoDoItem.ESTOQUE,
            quantidade_estoque=Decimal("4"),
        )
        self.assertEqual(compra.total_calculado(), Decimal("10.00"))


class LinhaDaCompraTests(BaseDeModelos):
    """A linha diz para onde vai; a conta do destino precisa fechar."""

    def test_total_da_linha_e_recalculado_no_save(self):
        compra = self._compra()
        linha = ItemCompra.objects.create(
            compra=compra,
            item_estoque=self.item,
            descricao="Lâmpada",
            quantidade=Decimal("3"),
            valor_unitario=Decimal("2.50"),
            valor_total=Decimal("999"),
            destino=DestinoDoItem.ESTOQUE,
            quantidade_estoque=Decimal("3"),
        )
        self.assertEqual(linha.valor_total, Decimal("7.50"))

    def test_destino_estoque_sem_item_e_recusado_pelo_banco(self):
        compra = self._compra()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ItemCompra.objects.create(
                    compra=compra,
                    descricao="Sem item",
                    quantidade=Decimal("1"),
                    destino=DestinoDoItem.ESTOQUE,
                    quantidade_estoque=Decimal("1"),
                )

    def test_uso_imediato_nao_exige_item_de_estoque(self):
        compra = self._compra()
        linha = ItemCompra.objects.create(
            compra=compra,
            descricao="Material consumido no ato",
            quantidade=Decimal("2"),
            destino=DestinoDoItem.USO_IMEDIATO,
            quantidade_uso_imediato=Decimal("2"),
        )
        self.assertEqual(linha.quantidade_que_vai_para_o_estoque, Decimal("0"))

    def test_misto_que_nao_fecha_e_recusado_pelo_banco(self):
        compra = self._compra()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ItemCompra.objects.create(
                    compra=compra,
                    item_estoque=self.item,
                    descricao="Misto torto",
                    quantidade=Decimal("10"),
                    destino=DestinoDoItem.MISTO,
                    quantidade_uso_imediato=Decimal("4"),
                    quantidade_estoque=Decimal("4"),
                )

    def test_misto_que_fecha_diz_quanto_vai_para_o_estoque(self):
        compra = self._compra()
        linha = ItemCompra.objects.create(
            compra=compra,
            item_estoque=self.item,
            descricao="Misto certo",
            quantidade=Decimal("10"),
            destino=DestinoDoItem.MISTO,
            quantidade_uso_imediato=Decimal("4"),
            quantidade_estoque=Decimal("6"),
        )
        self.assertEqual(linha.quantidade_que_vai_para_o_estoque, Decimal("6"))
