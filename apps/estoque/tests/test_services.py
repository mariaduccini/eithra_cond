"""Operações do domínio: criar item, movimentar, comprar e confirmar.

Os testes daqui exercitam as decisões que não podem depender da tela: quanto há
de um item, se há saldo para uma saída, o que acontece quando uma compra é
confirmada e o que muda quando ela é cancelada. Nada aqui gera despesa nem toca
em balanço — e há um teste que fixa isso.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.estoque.constants import (
    DestinoDoItem,
    StatusCompra,
    TipoMovimentacaoEstoque,
)
from apps.estoque.models import Compra, ItemEstoque, MovimentacaoEstoque
from apps.estoque.services import (
    ErroDeEstoque,
    adicionar_item_compra,
    cancelar_compra,
    confirmar_compra,
    criar_compra,
    criar_item,
    editar_compra,
    editar_item_compra,
    registrar_movimentacao,
    registrar_saida_para_manutencao,
    remover_item_compra,
    saldo_do_item,
)
from apps.estoque.tests.apoio import (
    criar_condominio,
    criar_item_estoque,
    criar_manutencao_do_condominio,
    criar_pessoa,
)


class BaseDeServicos(TestCase):
    """Uma gestão com um condomínio, para montar os cenários de cada teste."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")

    def _item(self, nome="Lâmpada LED", **campos):
        return criar_item_estoque(self.condominio, self.gestao, nome=nome, **campos)

    def _entrada(self, item, quantidade, motivo="Saldo inicial"):
        """Repõe o saldo pelo caminho certo: um ajuste de entrada, com motivo."""
        return registrar_movimentacao(
            condominio=self.condominio,
            item=item,
            usuario=self.gestao,
            tipo=TipoMovimentacaoEstoque.AJUSTE_ENTRADA,
            quantidade=quantidade,
            motivo=motivo,
        )

    def _compra(self, **campos):
        base = {"condominio": self.condominio, "criado_por": self.gestao,
                "data_compra": date.today()}
        base.update(campos)
        return criar_compra(**base)

    def _linha(self, compra, **campos):
        base = {"compra": compra, "descricao": "Material", "quantidade": Decimal("5")}
        base.update(campos)
        return adicionar_item_compra(**base)


class CriarItemTests(BaseDeServicos):
    """Cadastrar não cria estoque: só passa a controlar o item."""

    def test_cadastro_nao_inventa_saldo(self):
        item = criar_item(
            condominio=self.condominio, criado_por=self.gestao, nome="  Filtro  "
        )
        self.assertEqual(item.nome, "Filtro")
        self.assertEqual(saldo_do_item(item), Decimal("0"))

    def test_nome_repetido_no_condominio_e_recusado(self):
        self._item("Lâmpada LED")
        with self.assertRaises(ErroDeEstoque):
            self._item("lâmpada led")


class MovimentacaoManualTests(BaseDeServicos):
    """Uso, perda, vencimento e ajuste: cada um com sua regra."""

    def test_ajuste_de_entrada_exige_motivo(self):
        item = self._item()
        with self.assertRaises(ErroDeEstoque):
            registrar_movimentacao(
                condominio=self.condominio,
                item=item,
                usuario=self.gestao,
                tipo=TipoMovimentacaoEstoque.AJUSTE_ENTRADA,
                quantidade=Decimal("5"),
            )

    def test_entrada_por_compra_tem_caminho_proprio(self):
        item = self._item()
        with self.assertRaises(ErroDeEstoque):
            registrar_movimentacao(
                condominio=self.condominio,
                item=item,
                usuario=self.gestao,
                tipo=TipoMovimentacaoEstoque.ENTRADA_COMPRA,
                quantidade=Decimal("5"),
            )

    def test_saida_maior_que_o_saldo_e_recusada(self):
        item = self._item()
        self._entrada(item, Decimal("3"))
        with self.assertRaises(ErroDeEstoque):
            registrar_movimentacao(
                condominio=self.condominio,
                item=item,
                usuario=self.gestao,
                tipo=TipoMovimentacaoEstoque.SAIDA_USO,
                quantidade=Decimal("4"),
                motivo="Consumo",
            )
        self.assertEqual(saldo_do_item(item), Decimal("3"))

    def test_saida_dentro_do_saldo_e_registrada(self):
        item = self._item()
        self._entrada(item, Decimal("10"))
        registrar_movimentacao(
            condominio=self.condominio,
            item=item,
            usuario=self.gestao,
            tipo=TipoMovimentacaoEstoque.SAIDA_USO,
            quantidade=Decimal("4"),
            motivo="Consumo do dia",
        )
        self.assertEqual(saldo_do_item(item), Decimal("6"))

    def test_item_de_outro_condominio_e_recusado(self):
        outro = criar_condominio("Beta")
        alheio = criar_item_estoque(outro, self.gestao, nome="Item do Beta")
        with self.assertRaises(ErroDeEstoque):
            self._entrada(alheio, Decimal("1"))


class SaidaParaManutencaoTests(BaseDeServicos):
    """A retirada é o único movimento que a operação alcança, e exige saldo."""

    def test_retirada_liga_o_movimento_a_manutencao(self):
        item = self._item()
        self._entrada(item, Decimal("10"))
        manutencao = criar_manutencao_do_condominio(self.condominio, self.gestao)

        movimento = registrar_saida_para_manutencao(
            manutencao=manutencao,
            item=item,
            usuario=self.gestao,
            quantidade=Decimal("3"),
        )
        self.assertEqual(movimento.manutencao, manutencao)
        self.assertEqual(movimento.tipo, TipoMovimentacaoEstoque.SAIDA_MANUTENCAO)
        self.assertEqual(saldo_do_item(item), Decimal("7"))

    def test_retirada_sem_saldo_e_recusada(self):
        item = self._item()
        manutencao = criar_manutencao_do_condominio(self.condominio, self.gestao)
        with self.assertRaises(ErroDeEstoque):
            registrar_saida_para_manutencao(
                manutencao=manutencao,
                item=item,
                usuario=self.gestao,
                quantidade=Decimal("1"),
            )


class CompraTests(BaseDeServicos):
    """Montar a compra é rascunho; confirmar é o que move o estoque."""

    def test_compra_nasce_rascunho_sem_movimento(self):
        item = self._item()
        compra = self._compra()
        self._linha(compra, item_estoque=item, quantidade=Decimal("5"))
        self.assertEqual(compra.status, StatusCompra.RASCUNHO)
        self.assertEqual(MovimentacaoEstoque.objects.count(), 0)

    def test_destino_estoque_exige_item_escolhido(self):
        compra = self._compra()
        with self.assertRaises(ErroDeEstoque):
            self._linha(compra, destino=DestinoDoItem.ESTOQUE)

    def test_uso_imediato_dispensa_item(self):
        compra = self._compra()
        linha = self._linha(
            compra,
            destino=DestinoDoItem.USO_IMEDIATO,
            quantidade=Decimal("2"),
        )
        self.assertEqual(linha.quantidade_uso_imediato, Decimal("2"))
        self.assertEqual(linha.quantidade_estoque, Decimal("0"))

    def test_misto_reparte_entre_uso_e_estoque(self):
        item = self._item()
        compra = self._compra()
        linha = self._linha(
            compra,
            item_estoque=item,
            destino=DestinoDoItem.MISTO,
            quantidade=Decimal("10"),
            quantidade_uso_imediato=Decimal("4"),
        )
        self.assertEqual(linha.quantidade_uso_imediato, Decimal("4"))
        self.assertEqual(linha.quantidade_estoque, Decimal("6"))

    def test_misto_sem_dizer_quanto_consome_e_recusado(self):
        item = self._item()
        compra = self._compra()
        with self.assertRaises(ErroDeEstoque):
            self._linha(
                compra,
                item_estoque=item,
                destino=DestinoDoItem.MISTO,
                quantidade=Decimal("10"),
            )

    def test_editar_e_remover_linha_valem_so_no_rascunho(self):
        item = self._item()
        compra = self._compra()
        linha = self._linha(compra, item_estoque=item, quantidade=Decimal("5"))

        editar_item_compra(
            linha=linha,
            descricao="Material revisado",
            quantidade=Decimal("7"),
            destino=DestinoDoItem.ESTOQUE,
            item_estoque=item,
        )
        linha.refresh_from_db()
        self.assertEqual(linha.quantidade, Decimal("7"))
        self.assertEqual(linha.quantidade_estoque, Decimal("7"))

        remover_item_compra(linha=linha)
        self.assertEqual(compra.itens.count(), 0)


class ConfirmacaoTests(BaseDeServicos):
    """Confirmar aplica o destino das linhas e movimenta o estoque uma vez só."""

    def test_confirmar_move_so_o_que_vai_para_o_estoque(self):
        item = self._item()
        compra = self._compra()
        self._linha(
            compra,
            item_estoque=item,
            quantidade=Decimal("10"),
            destino=DestinoDoItem.ESTOQUE,
        )
        self._linha(
            compra,
            descricao="Consumido no ato",
            quantidade=Decimal("3"),
            destino=DestinoDoItem.USO_IMEDIATO,
        )

        confirmada = confirmar_compra(compra=compra, usuario=self.gestao)

        self.assertEqual(confirmada.status, StatusCompra.CONFIRMADA)
        self.assertIsNotNone(confirmada.confirmada_em)
        self.assertEqual(saldo_do_item(item), Decimal("10"))
        self.assertEqual(MovimentacaoEstoque.objects.count(), 1)

    def test_confirmar_duas_vezes_nao_duplica_movimento(self):
        item = self._item()
        compra = self._compra()
        self._linha(compra, item_estoque=item, quantidade=Decimal("5"))
        confirmar_compra(compra=compra, usuario=self.gestao)

        with self.assertRaises(ErroDeEstoque):
            confirmar_compra(compra=compra, usuario=self.gestao)

        self.assertEqual(MovimentacaoEstoque.objects.count(), 1)
        self.assertEqual(saldo_do_item(item), Decimal("5"))

    def test_compra_sem_linha_nao_confirma(self):
        compra = self._compra()
        with self.assertRaises(ErroDeEstoque):
            confirmar_compra(compra=compra, usuario=self.gestao)

    def test_compra_confirmada_nao_muda_mais(self):
        item = self._item()
        compra = self._compra()
        self._linha(compra, item_estoque=item, quantidade=Decimal("5"))
        confirmada = confirmar_compra(compra=compra, usuario=self.gestao)

        with self.assertRaises(ErroDeEstoque):
            editar_compra(compra=confirmada, data_compra=date.today())
        with self.assertRaises(ErroDeEstoque):
            self._linha(confirmada, item_estoque=item, quantidade=Decimal("1"))


class CancelamentoTests(BaseDeServicos):
    """Cancelar só vale para o que ainda não valeu; nada é apagado."""

    def test_rascunho_pode_ser_cancelado(self):
        compra = self._compra()
        cancelada = cancelar_compra(compra=compra, usuario=self.gestao)
        self.assertEqual(cancelada.status, StatusCompra.CANCELADA)
        self.assertIsNone(cancelada.confirmada_em)

    def test_compra_confirmada_nao_pode_ser_cancelada(self):
        item = self._item()
        compra = self._compra()
        self._linha(compra, item_estoque=item, quantidade=Decimal("5"))
        confirmar_compra(compra=compra, usuario=self.gestao)

        with self.assertRaises(ErroDeEstoque):
            cancelar_compra(compra=compra, usuario=self.gestao)

        compra.refresh_from_db()
        self.assertEqual(compra.status, StatusCompra.CONFIRMADA)
        self.assertEqual(MovimentacaoEstoque.objects.count(), 1)


class CustoNaoEhTocadoTests(BaseDeServicos):
    """Ligar compra a uma manutenção não altera o custo dela."""

    def test_confirmar_compra_nao_mexe_no_custo_da_manutencao(self):
        item = self._item()
        manutencao = criar_manutencao_do_condominio(self.condominio, self.gestao)
        antes = manutencao.custo_real

        compra = self._compra(manutencao_origem=manutencao)
        self._linha(
            compra,
            item_estoque=item,
            quantidade=Decimal("4"),
            valor_unitario=Decimal("10"),
            manutencao=manutencao,
        )
        confirmar_compra(compra=compra, usuario=self.gestao)

        manutencao.refresh_from_db()
        self.assertEqual(manutencao.custo_real, antes)
