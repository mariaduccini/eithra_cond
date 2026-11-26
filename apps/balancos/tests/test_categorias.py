"""Mão de obra é Pessoal, nunca Obras — e o que o comando faz com os meses já confirmados.

Estes testes fixam a regra ("locação de mão de obra" não é obra) e o comando de
reclassificação: sem ``--aplicar`` nada muda; com ``--aplicar`` a categoria da
máquina muda, a decisão manual permanece e os totais oficiais ficam intactos.
"""
import datetime
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.balancos.categorias import CategoriaEithra, categorizar
from apps.balancos.management.commands.reclassificar_balancos import (
    reclassificar_leitura,
)
from apps.balancos.tests.test_models import criar_balanco, criar_pessoa
from apps.condominios.models import Condominio

AGOSTO = datetime.date(2026, 8, 1)


class MaoDeObraTests(TestCase):
    """A regra de categorização em si."""

    def test_locacao_de_mao_de_obra_e_pessoal(self):
        self.assertEqual(categorizar("Locação de Mão de Obra"), CategoriaEithra.PESSOAL)

    def test_obra_da_fachada_continua_obras(self):
        self.assertEqual(
            categorizar("Obra da fachada"), CategoriaEithra.OBRAS_MELHORIAS
        )

    def test_terceirizacao_e_pessoal(self):
        self.assertEqual(
            categorizar("Terceirização de serviços"), CategoriaEithra.PESSOAL
        )


def _despesa(descricao, valor, categoria, *, categoria_original=None, correcao=None):
    """Um lançamento lido, no formato que vive dentro da leitura do balanço."""
    item = {
        "id": descricao[:12],
        "descricao_original": descricao,
        "descricao_normalizada": descricao.lower(),
        "valor": valor,
        "data": None,
        "fornecedor_candidato": None,
        "documento": None,
        "parcela": None,
        "categoria_original": categoria_original,
        "categoria_eithra": categoria,
        "natureza": "recorrente",
        "confianca": "media",
    }
    if correcao:
        item["correcao"] = correcao
    return item


def _validado(*despesas):
    """Um ``dados_validados`` mínimo, válido pelo contrato, com as despesas dadas."""
    leitura = {
        "schema_versao": 1,
        "competencia": AGOSTO.isoformat(),
        "receitas_total": "1000.00",
        "despesas_total": "600.00",
        "soma_despesas_extraida": "600.00",
        "saldos": [],
        "categorias": [],
        "grupos": [],
        "receitas": [],
        "despesas": list(despesas),
        "provenance": {},
        "resumo": {},
    }
    return {
        "schema_versao": 1,
        "origem_schema_versao": 1,
        "competencia": AGOSTO.isoformat(),
        "receitas_total": "1000.00",
        "despesas_total": "600.00",
        "total_declarado_despesas": "600.00",
        "total_detalhado_identificado": "600.00",
        "valor_nao_associado": "0.00",
        "percentual_detalhamento": "100.00",
        "confirmado_com_ressalvas": False,
        "confirmado_em": None,
        "leitura": leitura,
    }


class ReclassificarLeituraTests(TestCase):
    """A releitura de uma leitura, sem banco."""

    def test_rele_so_o_que_a_maquina_classificou(self):
        leitura = _validado(
            _despesa(
                "Locação de Mão de Obra", "300.00", CategoriaEithra.OBRAS_MELHORIAS
            ),
            _despesa(
                "Serviço avulso",
                "300.00",
                CategoriaEithra.OBRAS_MELHORIAS,
                correcao={"categoria_eithra": CategoriaEithra.MANUTENCAO},
            ),
        )["leitura"]

        mudancas = reclassificar_leitura(leitura)

        self.assertEqual(len(mudancas), 1)
        self.assertEqual(mudancas[0]["para"], CategoriaEithra.PESSOAL)
        self.assertEqual(
            leitura["despesas"][0]["categoria_eithra"], CategoriaEithra.PESSOAL
        )
        # A corrigida à mão não muda e nem aparece na lista de mudanças.
        self.assertEqual(
            leitura["despesas"][1]["categoria_eithra"], CategoriaEithra.OBRAS_MELHORIAS
        )


class ComandoReclassificarTests(TestCase):
    """O comando sobre o balanço já confirmado."""

    def setUp(self):
        self.gestao = criar_pessoa()
        self.condominio = Condominio.objects.create(nome="Condomínio A")

    def _balanco(self, *despesas):
        return criar_balanco(
            self.condominio,
            self.gestao,
            competencia=AGOSTO,
            dados_validados=_validado(*despesas),
        )

    def test_sem_aplicar_nao_grava(self):
        balanco = self._balanco(
            _despesa(
                "Locação de Mão de Obra", "300.00", CategoriaEithra.OBRAS_MELHORIAS
            )
        )

        saida = StringIO()
        call_command("reclassificar_balancos", stdout=saida)

        balanco.refresh_from_db()
        despesas = balanco.dados_validados["leitura"]["despesas"]
        self.assertEqual(
            despesas[0]["categoria_eithra"], CategoriaEithra.OBRAS_MELHORIAS
        )
        self.assertIn("Pessoal", saida.getvalue())
        self.assertIn("nada foi", saida.getvalue())

    def test_aplicar_reclassifica_preserva_manual_e_totais(self):
        balanco = self._balanco(
            _despesa(
                "Locação de Mão de Obra", "300.00", CategoriaEithra.OBRAS_MELHORIAS
            ),
            _despesa(
                "Serviço avulso",
                "300.00",
                CategoriaEithra.OBRAS_MELHORIAS,
                correcao={"categoria_eithra": CategoriaEithra.MANUTENCAO},
            ),
        )

        call_command("reclassificar_balancos", "--aplicar")

        balanco.refresh_from_db()
        dados = balanco.dados_validados
        despesas = dados["leitura"]["despesas"]

        # A que a máquina errou passa a Pessoal.
        self.assertEqual(despesas[0]["categoria_eithra"], CategoriaEithra.PESSOAL)
        # A corrigida à mão continua como a gestão decidiu.
        self.assertEqual(
            despesas[1]["categoria_eithra"], CategoriaEithra.OBRAS_MELHORIAS
        )
        self.assertEqual(
            despesas[1]["correcao"]["categoria_eithra"], CategoriaEithra.MANUTENCAO
        )
        # Totais oficiais intactos.
        self.assertEqual(dados["receitas_total"], "1000.00")
        self.assertEqual(dados["despesas_total"], "600.00")
        # Rastreável no histórico do balanço.
        revisoes = dados["leitura"]["revisoes"]
        self.assertEqual(revisoes[-1]["tipo"], "reclassificacao")
        self.assertIn("Reclassificação automática", revisoes[-1]["justificativa"])
