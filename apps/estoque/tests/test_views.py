"""Telas de estoque e compras: acesso, cadastro, movimento e o ciclo da compra.

O que se fixa aqui é o comportamento das telas com o domínio por trás: quem não
enxerga o módulo recebe 404, quem enxerga mas não decide recebe 403, e as ações
que mudam o mundo são sempre POST.
"""
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.estoque.constants import (
    DestinoDoItem,
    StatusCompra,
    TipoMovimentacaoEstoque,
)
from apps.estoque.models import Compra, ItemEstoque, MovimentacaoEstoque
from apps.estoque.services import registrar_movimentacao
from apps.estoque.tests.apoio import (
    criar_condominio,
    criar_item_estoque,
    criar_manutencao_do_condominio,
    criar_pessoa,
    criar_vinculo,
)


class BaseDeTelas(TestCase):
    """Uma gestão ligada a um condomínio, com um item já com saldo."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)
        self.item = criar_item_estoque(self.condominio, self.gestao)

    def _com_saldo(self, item, quantidade):
        return registrar_movimentacao(
            condominio=self.condominio,
            item=item,
            usuario=self.gestao,
            tipo=TipoMovimentacaoEstoque.AJUSTE_ENTRADA,
            quantidade=Decimal(quantidade),
            motivo="Saldo inicial",
        )

    def _compra(self, **campos):
        base = {
            "condominio": self.condominio,
            "criado_por": self.gestao,
            "data_compra": date.today(),
        }
        base.update(campos)
        return Compra.objects.create(**base)


class AcessoAsTelasTests(BaseDeTelas):
    """Sem vínculo que enxergue o módulo, a resposta é 404."""

    def test_estoque_exige_login(self):
        self.client.logout()
        self.assertEqual(
            self.client.get(reverse("estoque:itens")).status_code, 302
        )

    def test_morador_recebe_404_no_estoque(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)
        self.assertEqual(
            self.client.get(reverse("estoque:itens")).status_code, 404
        )

    def test_operacao_ve_estoque_mas_recebe_404_em_compras(self):
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        self.client.force_login(operacional)

        self.assertEqual(self.client.get(reverse("estoque:itens")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("compras:listagem")).status_code, 404
        )

    def test_item_de_outro_condominio_e_404(self):
        outro = criar_condominio("Beta")
        alheio = criar_item_estoque(outro, self.gestao, nome="Item do Beta")
        resposta = self.client.get(
            reverse("estoque:item_detalhe", args=[alheio.pk])
        )
        self.assertEqual(resposta.status_code, 404)

    def test_detalhe_autorizado_abre(self):
        resposta = self.client.get(
            reverse("estoque:item_detalhe", args=[self.item.pk])
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["item"]["pk"], self.item.pk)


class CadastroEMovimentoTests(BaseDeTelas):
    """Cadastrar item e registrar movimento pela tela."""

    def test_criar_item_redireciona_para_o_detalhe(self):
        resposta = self.client.post(
            reverse("estoque:criar_item"),
            {
                "condominio": str(self.condominio.pk),
                "nome": "Filtro de água",
                "unidade": "unidade",
            },
        )
        self.assertEqual(resposta.status_code, 302)
        criado = ItemEstoque.objects.get(nome="Filtro de água")
        self.assertEqual(
            resposta.url, reverse("estoque:item_detalhe", args=[criado.pk])
        )

    def test_item_repetido_no_mesmo_condominio_e_recusado(self):
        resposta = self.client.post(
            reverse("estoque:criar_item"),
            {
                "condominio": str(self.condominio.pk),
                "nome": "lâmpada led",
                "unidade": "unidade",
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(resposta.context["modal_aberto"], "criar")
        self.assertIn("nome", resposta.context["formulario_item"].errors)

    def test_morador_nao_cadastra(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)
        resposta = self.client.post(
            reverse("estoque:criar_item"),
            {"condominio": str(self.condominio.pk), "nome": "Tentativa"},
        )
        self.assertEqual(resposta.status_code, 404)

    def test_movimento_de_entrada_exige_post(self):
        resposta = self.client.get(
            reverse("estoque:movimentar_item", args=[self.item.pk])
        )
        self.assertEqual(resposta.status_code, 405)

    def test_ajuste_de_entrada_registra_saldo(self):
        resposta = self.client.post(
            reverse("estoque:movimentar_item", args=[self.item.pk]),
            {
                "tipo": TipoMovimentacaoEstoque.AJUSTE_ENTRADA,
                "quantidade": "8",
                "motivo": "Contagem inicial",
            },
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(MovimentacaoEstoque.objects.count(), 1)

    def test_perda_sem_motivo_e_recusada(self):
        resposta = self.client.post(
            reverse("estoque:movimentar_item", args=[self.item.pk]),
            {
                "tipo": TipoMovimentacaoEstoque.PERDA,
                "quantidade": "1",
                "motivo": "",
            },
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(MovimentacaoEstoque.objects.count(), 0)


class RetiradaTests(BaseDeTelas):
    """A retirada de material para a manutenção, pela tela."""

    def test_gestao_retira_para_a_manutencao(self):
        self._com_saldo(self.item, "10")
        manutencao = criar_manutencao_do_condominio(self.condominio, self.gestao)
        resposta = self.client.post(
            reverse("estoque:retirar_para_manutencao", args=[self.item.pk]),
            {
                "manutencao": str(manutencao.pk),
                "item": str(self.item.pk),
                "quantidade": "2",
            },
        )
        self.assertEqual(resposta.status_code, 302)
        movimento = MovimentacaoEstoque.objects.get(
            tipo=TipoMovimentacaoEstoque.SAIDA_MANUTENCAO
        )
        self.assertEqual(movimento.manutencao, manutencao)

    def test_operacao_responsavel_retira(self):
        self._com_saldo(self.item, "10")
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        manutencao = criar_manutencao_do_condominio(self.condominio, self.gestao)
        manutencao.responsavel = operacional
        manutencao.save(update_fields=["responsavel"])

        self.client.force_login(operacional)
        resposta = self.client.post(
            reverse("estoque:retirar_para_manutencao", args=[self.item.pk]),
            {
                "manutencao": str(manutencao.pk),
                "item": str(self.item.pk),
                "quantidade": "2",
            },
        )
        self.assertEqual(resposta.status_code, 302)


class ComprasTests(BaseDeTelas):
    """O ciclo da compra pela tela: abrir, montar, confirmar e cancelar."""

    def test_listagem_abre_para_a_gestao(self):
        resposta = self.client.get(reverse("compras:listagem"))
        self.assertEqual(resposta.status_code, 200)

    def test_criar_compra_abre_rascunho(self):
        resposta = self.client.post(
            reverse("compras:criar"),
            {
                "condominio": str(self.condominio.pk),
                "data_compra": date.today().isoformat(),
            },
        )
        self.assertEqual(resposta.status_code, 302)
        compra = Compra.objects.get()
        self.assertEqual(compra.status, StatusCompra.RASCUNHO)

    def test_adicionar_linha_com_destino_estoque(self):
        compra = self._compra()
        resposta = self.client.post(
            reverse("compras:adicionar_linha", args=[compra.pk]),
            {
                "descricao": "Lâmpada",
                "unidade": "unidade",
                "quantidade": "5",
                "valor_unitario": "2.50",
                "destino": DestinoDoItem.ESTOQUE,
                "item_estoque": str(self.item.pk),
            },
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(compra.itens.count(), 1)

    def test_confirmar_compra_movimenta_o_estoque(self):
        compra = self._compra()
        self.client.post(
            reverse("compras:adicionar_linha", args=[compra.pk]),
            {
                "descricao": "Lâmpada",
                "unidade": "unidade",
                "quantidade": "5",
                "destino": DestinoDoItem.ESTOQUE,
                "item_estoque": str(self.item.pk),
            },
        )
        resposta = self.client.post(
            reverse("compras:confirmar", args=[compra.pk])
        )
        self.assertEqual(resposta.status_code, 302)
        compra.refresh_from_db()
        self.assertEqual(compra.status, StatusCompra.CONFIRMADA)
        self.assertEqual(MovimentacaoEstoque.objects.count(), 1)

    def test_supervisao_nao_cancela_compra(self):
        supervisora = criar_pessoa("sup@exemplo.test", "Supervisora")
        criar_vinculo(supervisora, self.condominio, papel=Papel.SUPERVISORA)
        compra = self._compra()
        self.client.force_login(supervisora)
        resposta = self.client.post(
            reverse("compras:cancelar", args=[compra.pk])
        )
        self.assertEqual(resposta.status_code, 403)
        compra.refresh_from_db()
        self.assertEqual(compra.status, StatusCompra.RASCUNHO)

    def test_gestao_cancela_rascunho(self):
        compra = self._compra()
        resposta = self.client.post(
            reverse("compras:cancelar", args=[compra.pk])
        )
        self.assertEqual(resposta.status_code, 302)
        compra.refresh_from_db()
        self.assertEqual(compra.status, StatusCompra.CANCELADA)

    def test_confirmar_exige_post(self):
        compra = self._compra()
        resposta = self.client.get(reverse("compras:confirmar", args=[compra.pk]))
        self.assertEqual(resposta.status_code, 405)

    def test_detalhe_do_item_mostra_o_ultimo_custo_da_compra_confirmada(self):
        compra = self._compra()
        self.client.post(
            reverse("compras:adicionar_linha", args=[compra.pk]),
            {
                "descricao": "Lâmpada",
                "unidade": "unidade",
                "quantidade": "5",
                "valor_unitario": "2.50",
                "destino": DestinoDoItem.ESTOQUE,
                "item_estoque": str(self.item.pk),
            },
        )

        sem_compra = self.client.get(
            reverse("estoque:item_detalhe", args=[self.item.pk])
        )
        self.assertEqual(sem_compra.context["item"]["ultimo_custo"], "")

        self.client.post(reverse("compras:confirmar", args=[compra.pk]))
        resposta = self.client.get(
            reverse("estoque:item_detalhe", args=[self.item.pk])
        )
        self.assertEqual(resposta.context["item"]["ultimo_custo"], "R$ 2,50")
        self.assertEqual(
            resposta.context["item"]["ultima_compra"],
            date.today().strftime("%d/%m/%Y"),
        )
