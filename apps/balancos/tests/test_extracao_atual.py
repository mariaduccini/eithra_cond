"""Testes da leitura financeira do balanço do modelo atual.

Tudo aqui roda sobre PDFs sintéticos com camada textual controlada: nenhum
arquivo de balanço real entra na suíte e nenhum teste conversa com o storage.
O que se verifica é o que a leitura entende — e, principalmente, o que ela se
recusa a inventar.

Os nomes de condomínio, administradora e fornecedor são fictícios de propósito:
a leitura não conhece nenhum deles, e nenhum deles vira regra.
"""
from __future__ import annotations

import hashlib
import json
from unittest.mock import patch

from django.test import TestCase

from apps.balancos.categorias import CategoriaEithra
from apps.balancos.constants import LayoutBalanco, StatusImportacao
from apps.balancos.contratos import (
    SCHEMA_VERSAO,
    ErroDeContrato,
    Severidade,
    TipoInconsistencia,
    de_dicionario,
    para_dicionario,
    validar_dicionario,
)
from apps.balancos.detector import ErroDeDeteccaoDeLayout
from apps.balancos.extrator_atual import (
    ErroDeExtracao,
    extrair_balanco_atual,
    ler_balanco_atual,
)
from apps.balancos.models import BalancoMensal, ImportacaoBalanco
from apps.balancos.natureza_despesa import NaturezaDespesa
from apps.balancos.normalizadores import chave_de_comparacao
from apps.balancos.services import receber_importacao
from apps.balancos.tests.apoio import (
    StorageDeMentira,
    criar_pessoa,
    gerar_pdf_textual,
    pdf,
)
from apps.condominios.models import Condominio


# -- Documentos sintéticos ------------------------------------------------------


def pdf_do_modelo_atual() -> bytes:
    """Balanço completo do modelo atual, com todos os blocos que a leitura usa.

    Os números fecham de propósito: a soma das receitas e a das despesas batem
    com os totais declarados, e o saldo obedece à equação. É o caso comum — o
    caso em que a leitura não tem nada a apontar.
    """
    return gerar_pdf_textual(
        "\n".join(
            [
                "Condominio Exemplo",
                "Demonstrativo de Contas",
                "Competencia: Agosto/2026",
                "Periodo: 01/08/2026 a 31/08/2026",
            ]
        ),
        "\n".join(
            [
                "Receitas Detalhadas",
                "Taxa Condominial 45.000,00",
                "Fundo de Reserva 5.000,00",
                "Total de Receitas 50.000,00",
            ]
        ),
        "\n".join(
            [
                "Despesas Detalhadas",
                "Folha de Pagamento 12.000,00",
                "Energia Eletrica 3.450,00",
                "Manutencao do Elevador LTDA 1.200,00",
                "Total de Despesas 16.650,00",
            ]
        ),
        "\n".join(
            [
                "Resumo Financeiro",
                "Saldo Anterior 10.000,00",
                "Saldo Final 43.350,00",
                "Inadimplencia do mes: 4 inadimplentes (3,5%) - R$ 1.500,00",
            ]
        ),
    )


def pdf_com_competencia_conflitante() -> bytes:
    """Dois meses possíveis no mesmo documento: a leitura não escolhe sozinha."""
    return gerar_pdf_textual(
        "Competencia: Julho/2026",
        "\n".join(
            [
                "Competencia: Agosto/2026",
                "Receitas Detalhadas",
                "Taxa Condominial 1.000,00",
            ]
        ),
    )


def pdf_com_total_divergente() -> bytes:
    """A soma das despesas não bate com o total declarado."""
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "Energia Eletrica 1.000,00\nTotal de Despesas 9.999,00",
    )


def pdf_com_despesa_repetida() -> bytes:
    """O mesmo lançamento aparece duas vezes com o mesmo valor."""
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "\n".join(
            [
                "Material de limpeza 200,00",
                "Material de limpeza 200,00",
                "Total de Despesas 400,00",
            ]
        ),
    )


def pdf_com_linha_sem_descricao() -> bytes:
    """Um valor sozinho, sem descrição: não vira lançamento em silêncio."""
    return gerar_pdf_textual("Receitas Detalhadas", "1.234,56")


def pdf_com_agrupamento() -> bytes:
    """O documento agrupa os lançamentos e o agrupamento é preservado."""
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "\n".join(
            [
                "Manutencao",
                "Troca de bomba do recalque 2.500,00",
                "Total de Despesas 2.500,00",
            ]
        ),
    )


def pdf_com_documento_e_parcela() -> bytes:
    """Lançamento com nota e parcela declaradas na própria linha."""
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "Reforma da fachada - NFe 12345 - parcela 3/6 6.000,00\nTotal de Despesas 6.000,00",
    )


def pdf_com_anexos() -> bytes:
    """Comprovantes anexos não podem virar lançamento."""
    return gerar_pdf_textual(
        "Despesas Detalhadas",
        "\n".join(
            [
                "Energia Eletrica 1.000,00",
                "Total de Despesas 1.000,00",
                "Documentos Anexos",
                "Comprovante de pagamento 9.999,00",
            ]
        ),
    )


def pdf_sem_secoes() -> bytes:
    """Documento sem nada que a leitura reconheça: ausência é ausência."""
    return gerar_pdf_textual(
        "Aviso interno", "Agenda de reuniao do condominio"
    )


def _despesa(despesas, descricao: str):
    """Acha a despesa pela descrição normalizada."""
    alvo = chave_de_comparacao(descricao)
    return next(item for item in despesas if item.descricao_normalizada == alvo)


# -- Leitura pura ---------------------------------------------------------------


class LeituraDoModeloAtualTests(TestCase):
    """O que a leitura entende de um balanço do modelo atual."""

    @classmethod
    def setUpTestData(cls):
        cls.balanco, cls.inconsistencias = ler_balanco_atual(pdf_do_modelo_atual())

    def test_reconhece_a_competencia_e_o_periodo(self):
        self.assertEqual(self.balanco.competencia, "2026-08-01")
        self.assertEqual(self.balanco.periodo.inicio, "2026-08-01")
        self.assertEqual(self.balanco.periodo.fim, "2026-08-31")

    def test_le_os_totais_declarados(self):
        self.assertEqual(self.balanco.receitas_total, "50000.00")
        self.assertEqual(self.balanco.despesas_total, "16650.00")

    def test_le_os_saldos_de_abertura_e_fechamento(self):
        self.assertEqual(self.balanco.saldo_anterior, "10000.00")
        self.assertEqual(self.balanco.saldo_final, "43350.00")
        self.assertEqual(len(self.balanco.saldos), 2)

    def test_le_cada_receita_com_valor_e_procedencia(self):
        self.assertEqual(len(self.balanco.receitas), 2)
        taxa = self.balanco.receitas[0]
        self.assertEqual(taxa.descricao_original, "Taxa Condominial")
        self.assertEqual(taxa.valor, "45000.00")
        self.assertEqual(taxa.procedencia.secao, "receitas")
        self.assertIsNotNone(taxa.procedencia.pagina)
        self.assertEqual(taxa.procedencia.metodo, "camada textual do PDF")

    def test_le_cada_despesa_com_valor(self):
        self.assertEqual(len(self.balanco.despesas), 3)
        folha = _despesa(self.balanco.despesas, "Folha de Pagamento")
        self.assertEqual(folha.valor, "12000.00")
        self.assertEqual(folha.procedencia.secao, "despesas")

    def test_categoriza_pela_descricao_sem_apagar_o_original(self):
        folha = _despesa(self.balanco.despesas, "Folha de Pagamento")
        energia = _despesa(self.balanco.despesas, "Energia Eletrica")
        elevador = _despesa(self.balanco.despesas, "Manutencao do Elevador LTDA")

        self.assertEqual(folha.categoria_eithra, CategoriaEithra.PESSOAL)
        self.assertEqual(energia.categoria_eithra, CategoriaEithra.ENERGIA)
        self.assertEqual(elevador.categoria_eithra, CategoriaEithra.ELEVADORES)
        self.assertIsNone(energia.categoria_original)

    def test_sugere_a_natureza_da_despesa(self):
        folha = _despesa(self.balanco.despesas, "Folha de Pagamento")
        energia = _despesa(self.balanco.despesas, "Energia Eletrica")
        elevador = _despesa(self.balanco.despesas, "Manutencao do Elevador LTDA")

        self.assertEqual(folha.natureza, NaturezaDespesa.RECORRENTE_FIXA)
        self.assertEqual(energia.natureza, NaturezaDespesa.RECORRENTE_VARIAVEL)
        self.assertEqual(elevador.natureza, NaturezaDespesa.NAO_DETERMINADA)

    def test_guarda_o_fornecedor_apenas_como_candidato(self):
        elevador = _despesa(self.balanco.despesas, "Manutencao do Elevador LTDA")
        self.assertEqual(elevador.fornecedor_candidato, "Manutencao do Elevador LTDA")
        folha = _despesa(self.balanco.despesas, "Folha de Pagamento")
        self.assertIsNone(folha.fornecedor_candidato)

    def test_agrega_a_inadimplencia_em_um_so_numero(self):
        inadimplencia = self.balanco.inadimplencia_agregada
        self.assertIsNotNone(inadimplencia)
        self.assertEqual(inadimplencia.valor, "1500.00")
        self.assertEqual(inadimplencia.percentual, "3.5")
        self.assertEqual(inadimplencia.quantidade, 4)

    def test_agrega_por_categoria_do_maior_para_o_menor(self):
        categorias = {item.categoria: item for item in self.balanco.categorias}
        self.assertEqual(set(categorias), {
            CategoriaEithra.PESSOAL,
            CategoriaEithra.ENERGIA,
            CategoriaEithra.ELEVADORES,
        })
        self.assertEqual(categorias[CategoriaEithra.PESSOAL].total, "12000.00")
        self.assertEqual(categorias[CategoriaEithra.PESSOAL].quantidade, 1)
        self.assertEqual(self.balanco.categorias[0].categoria, CategoriaEithra.PESSOAL)

    def test_resume_o_mes_para_a_gestao(self):
        resumo = self.balanco.resumo
        self.assertEqual(resumo.recorrente_total, "15450.00")
        self.assertEqual(resumo.extraordinaria_total, "0.00")
        self.assertEqual(resumo.nao_determinada_total, "1200.00")
        self.assertEqual(resumo.receitas_identificadas, 2)
        self.assertEqual(resumo.despesas_identificadas, 3)
        self.assertEqual(resumo.pontos_de_atencao, 0)
        self.assertEqual(
            resumo.principais_categorias, ("Pessoal", "Energia", "Elevadores")
        )

    def test_registra_de_onde_leu_sem_copiar_o_documento(self):
        prov = self.balanco.provenance
        self.assertEqual(prov["metodo"], "camada textual do PDF")
        self.assertEqual(prov["total_paginas"], 4)
        self.assertEqual(prov["paginas_lidas"], 4)
        self.assertIn("receitas", prov["secoes_encontradas"])
        self.assertIn("despesas", prov["secoes_encontradas"])

    def test_nao_aponta_nada_quando_a_matematica_fecha(self):
        self.assertEqual(self.inconsistencias, ())
        self.assertEqual(self.balanco.confianca, "alta")


class PreservacaoDaEstruturaTests(TestCase):
    """O documento manda: agrupamento, anexos e afins são respeitados."""

    def test_preserva_o_agrupamento_do_documento(self):
        balanco, _ = ler_balanco_atual(pdf_com_agrupamento())
        despesa = balanco.despesas[0]
        self.assertEqual(despesa.categoria_original, "Manutencao")
        self.assertEqual(despesa.categoria_eithra, CategoriaEithra.MANUTENCAO)
        self.assertEqual(despesa.natureza, NaturezaDespesa.EXTRAORDINARIA)

    def test_le_documento_e_parcela_da_propria_linha(self):
        balanco, _ = ler_balanco_atual(pdf_com_documento_e_parcela())
        despesa = balanco.despesas[0]
        self.assertEqual(despesa.documento, "12345")
        self.assertEqual(despesa.parcela, "3/6")
        self.assertEqual(despesa.categoria_eithra, CategoriaEithra.OBRAS_MELHORIAS)
        self.assertEqual(despesa.natureza, NaturezaDespesa.EXTRAORDINARIA)

    def test_anexos_nao_viram_lancamento(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_anexos())
        self.assertEqual(len(balanco.despesas), 1)
        self.assertEqual(balanco.despesas_total, "1000.00")
        self.assertEqual(inconsistencias, ())


class AusenciaTests(TestCase):
    """Sem evidência, a leitura devolve vazio — nunca uma suposição."""

    @classmethod
    def setUpTestData(cls):
        cls.balanco, cls.inconsistencias = ler_balanco_atual(pdf_sem_secoes())

    def test_nao_inventa_competencia_nem_lancamento(self):
        self.assertIsNone(self.balanco.competencia)
        self.assertEqual(self.balanco.receitas, ())
        self.assertEqual(self.balanco.despesas, ())
        self.assertEqual(self.balanco.categorias, ())
        self.assertIsNone(self.balanco.inadimplencia_agregada)

    def test_nao_aponta_problema_onde_nao_ha_leitura(self):
        self.assertEqual(self.inconsistencias, ())
        self.assertEqual(self.balanco.confianca, "media")


class PontosDeAtencaoTests(TestCase):
    """A conferência por exceção: só se aponta o que se pode afirmar."""

    def test_competencia_conflitante_nao_e_escolhida_em_silencio(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_competencia_conflitante())

        self.assertIsNone(balanco.competencia)
        tipos = [item.tipo for item in inconsistencias]
        self.assertIn(TipoInconsistencia.COMPETENCIA_CONFLITANTE, tipos)
        conflito = next(
            item
            for item in inconsistencias
            if item.tipo == TipoInconsistencia.COMPETENCIA_CONFLITANTE
        )
        self.assertEqual(conflito.severidade, Severidade.ALTA)
        self.assertEqual(balanco.confianca, "baixa")

    def test_total_divergente_vira_ponto_de_atencao(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_total_divergente())

        divergencias = [
            item
            for item in inconsistencias
            if item.tipo == TipoInconsistencia.TOTAL_DIVERGENTE
        ]
        self.assertEqual(len(divergencias), 1)
        self.assertEqual(divergencias[0].severidade, Severidade.MEDIA)
        self.assertEqual(balanco.confianca, "media")

    def test_lancamento_repetido_pede_uma_olhada(self):
        _, inconsistencias = ler_balanco_atual(pdf_com_despesa_repetida())
        tipos = [item.tipo for item in inconsistencias]
        self.assertIn(TipoInconsistencia.POSSIVEL_DUPLICIDADE, tipos)

    def test_valor_sem_descricao_nao_vira_lancamento(self):
        balanco, inconsistencias = ler_balanco_atual(pdf_com_linha_sem_descricao())

        self.assertEqual(balanco.receitas, ())
        tipos = [item.tipo for item in inconsistencias]
        self.assertIn(TipoInconsistencia.LINHA_INCOMPLETA, tipos)


# -- Persistência da leitura ----------------------------------------------------


class ExtracaoDaImportacaoTests(TestCase):
    """A leitura sobre a importação gravada: o que fica guardado, e o que não."""

    def setUp(self):
        usuario = criar_pessoa("gestao.extracao@exemplo.com", "Gestao Extracao")
        condominio = Condominio.objects.create(nome="Condominio Extracao")
        conteudo = pdf_do_modelo_atual()
        self.importacao = ImportacaoBalanco.objects.create(
            condominio=condominio,
            criado_por=usuario,
            nome_original="balanco.pdf",
            mime_type="application/pdf",
            tamanho_bytes=len(conteudo),
            sha256=hashlib.sha256(conteudo).hexdigest(),
            storage_provider="supabase",
            storage_bucket="bucket-de-teste",
            storage_key="balancos/importacao/documento.pdf",
            status=StatusImportacao.PRONTO,
            layout_detectado=LayoutBalanco.ATUAL_CANONICO,
        )

    def test_grava_o_resultado_e_fica_pronta(self):
        resultado = extrair_balanco_atual(
            self.importacao, conteudo=pdf_do_modelo_atual()
        )

        self.importacao.refresh_from_db()
        self.assertTrue(resultado.processado)
        self.assertEqual(self.importacao.status, StatusImportacao.PRONTO)
        self.assertEqual(self.importacao.schema_versao, SCHEMA_VERSAO)
        self.assertEqual(self.importacao.inconsistencias, [])
        self.assertEqual(
            self.importacao.resultado_extraido["competencia"], "2026-08-01"
        )
        self.assertEqual(
            self.importacao.resultado_extraido["receitas_total"], "50000.00"
        )

    def test_resultado_gravado_respeita_o_contrato(self):
        extrair_balanco_atual(self.importacao, conteudo=pdf_do_modelo_atual())
        self.importacao.refresh_from_db()

        validar_dicionario(self.importacao.resultado_extraido)
        json.dumps(self.importacao.resultado_extraido)

    def test_nao_cria_balanco_mensal(self):
        """A leitura não oficializa o mês: isso é decisão da gestão."""
        extrair_balanco_atual(self.importacao, conteudo=pdf_do_modelo_atual())
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_reexecutar_produz_o_mesmo_resultado_sem_criar_objeto(self):
        primeiro = extrair_balanco_atual(
            self.importacao, conteudo=pdf_do_modelo_atual()
        )
        segundo = extrair_balanco_atual(
            self.importacao, conteudo=pdf_do_modelo_atual()
        )

        self.importacao.refresh_from_db()
        self.assertEqual(para_dicionario(primeiro.balanco), para_dicionario(segundo.balanco))
        self.assertEqual(ImportacaoBalanco.objects.count(), 1)
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_le_o_conteudo_do_storage_quando_nao_recebe_em_maos(self):
        with patch(
            "apps.balancos.extrator_atual._baixar_pdf_privado",
            return_value=(pdf_do_modelo_atual(), 4),
        ) as baixar:
            resultado = extrair_balanco_atual(self.importacao)

        self.assertTrue(resultado.processado)
        baixar.assert_called_once()
        self.importacao.refresh_from_db()
        self.assertEqual(self.importacao.status, StatusImportacao.PRONTO)

    def test_falha_de_storage_vira_erro_sem_vazar_detalhe(self):
        with patch(
            "apps.balancos.extrator_atual._baixar_pdf_privado",
            side_effect=ErroDeDeteccaoDeLayout(
                "Nao foi possivel falar com o armazenamento."
            ),
        ):
            with self.assertRaises(ErroDeExtracao):
                extrair_balanco_atual(self.importacao)

        self.importacao.refresh_from_db()
        self.assertEqual(self.importacao.status, StatusImportacao.ERRO)
        self.assertNotIn("http", self.importacao.erro_processamento.lower())
        self.assertEqual(self.importacao.resultado_extraido, {})
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_pdf_ilegivel_vira_erro_humano(self):
        with self.assertRaises(ErroDeExtracao):
            extrair_balanco_atual(self.importacao, conteudo=b"isto nao e um pdf")

        self.importacao.refresh_from_db()
        self.assertEqual(self.importacao.status, StatusImportacao.ERRO)
        self.assertTrue(self.importacao.erro_processamento)
        self.assertEqual(BalancoMensal.objects.count(), 0)


# -- Fluxo de recebimento -------------------------------------------------------


def detectar_como_atual(importacao, *, provider=None):
    """Fecha a importação como modelo atual, sem exercitar o detector real."""
    importacao.layout_detectado = LayoutBalanco.ATUAL_CANONICO
    importacao.status = StatusImportacao.PRONTO
    importacao.erro_processamento = ""
    importacao.save(
        update_fields=["layout_detectado", "status", "erro_processamento", "atualizado_em"]
    )
    return None


def detectar_como_desconhecido(importacao, *, provider=None):
    """Fecha a importação como não reconhecida, sem exercitar o detector real."""
    importacao.layout_detectado = LayoutBalanco.DESCONHECIDO
    importacao.status = StatusImportacao.PRONTO
    importacao.erro_processamento = ""
    importacao.save(
        update_fields=["layout_detectado", "status", "erro_processamento", "atualizado_em"]
    )
    return None


class FluxoDeRecebimentoTests(TestCase):
    """O recebimento do modelo atual já sai com os dados financeiros lidos."""

    def setUp(self):
        self.usuario = criar_pessoa("gestao.fluxo@exemplo.com", "Gestao Fluxo")
        self.condominio = Condominio.objects.create(nome="Condominio Fluxo")
        self.provider = StorageDeMentira()
        obter_provider = patch(
            "apps.balancos.services.obter_provider", return_value=self.provider
        )
        obter_provider.start()
        self.addCleanup(obter_provider.stop)

    def receber(self):
        return receber_importacao(
            condominio=self.condominio,
            usuario=self.usuario,
            arquivo=pdf(conteudo=pdf_do_modelo_atual()),
        )

    def test_modelo_atual_sai_com_os_dados_lidos(self):
        with patch(
            "apps.balancos.services.detectar_layout_balanco",
            side_effect=detectar_como_atual,
        ):
            importacao = self.receber().importacao

        importacao.refresh_from_db()
        self.assertEqual(importacao.status, StatusImportacao.PRONTO)
        self.assertEqual(importacao.schema_versao, SCHEMA_VERSAO)
        self.assertEqual(
            importacao.resultado_extraido["competencia"], "2026-08-01"
        )
        self.assertEqual(
            importacao.resultado_extraido["receitas_total"], "50000.00"
        )
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_leitura_usa_o_arquivo_ja_em_maos(self):
        """O upload já trouxe os bytes: a leitura não acessa o storage de novo."""
        with patch(
            "apps.balancos.services.detectar_layout_balanco",
            side_effect=detectar_como_atual,
        ), patch("apps.balancos.extrator_atual._baixar_pdf_privado") as baixar:
            self.receber()

        baixar.assert_not_called()

    def test_formato_nao_reconhecido_nao_e_lido(self):
        with patch(
            "apps.balancos.services.detectar_layout_balanco",
            side_effect=detectar_como_desconhecido,
        ):
            importacao = self.receber().importacao

        importacao.refresh_from_db()
        self.assertEqual(importacao.layout_detectado, LayoutBalanco.DESCONHECIDO)
        self.assertEqual(importacao.resultado_extraido, {})
        self.assertEqual(BalancoMensal.objects.count(), 0)


class RecusaDeLayoutTests(TestCase):
    """Documento que não é do modelo atual não é lido — devolve o motivo."""

    def setUp(self):
        usuario = criar_pessoa("gestao.recusa@exemplo.com", "Gestao Recusa")
        condominio = Condominio.objects.create(nome="Condominio Recusa")
        conteudo = pdf_sem_secoes()
        self.importacao = ImportacaoBalanco.objects.create(
            condominio=condominio,
            criado_por=usuario,
            nome_original="outro.pdf",
            mime_type="application/pdf",
            tamanho_bytes=len(conteudo),
            sha256=hashlib.sha256(conteudo).hexdigest(),
            storage_provider="supabase",
            storage_bucket="bucket-de-teste",
            storage_key="balancos/importacao/documento.pdf",
            status=StatusImportacao.PRONTO,
            layout_detectado=LayoutBalanco.LEGADO,
        )

    def test_legado_aponta_o_caminho_certo(self):
        resultado = extrair_balanco_atual(self.importacao, conteudo=pdf_do_modelo_atual())

        self.assertFalse(resultado.processado)
        self.assertEqual(
            resultado.motivo,
            "Este formato será processado pela leitura de documentos legados.",
        )
        self.importacao.refresh_from_db()
        self.assertEqual(self.importacao.resultado_extraido, {})

    def test_desconhecido_nao_promete_o_que_nao_sabe(self):
        self.importacao.layout_detectado = LayoutBalanco.DESCONHECIDO
        self.importacao.save(update_fields=["layout_detectado"])

        resultado = extrair_balanco_atual(self.importacao, conteudo=pdf_do_modelo_atual())

        self.assertFalse(resultado.processado)
        self.assertEqual(
            resultado.motivo,
            "Ainda não foi possível reconhecer o formato deste documento.",
        )


# -- Contrato -------------------------------------------------------------------


class ContratoDaLeituraTests(TestCase):
    """O contrato é o que viaja para o banco: precisa ser fiel e recuperável."""

    @classmethod
    def setUpTestData(cls):
        cls.balanco, _ = ler_balanco_atual(pdf_do_modelo_atual())
        cls.dados = para_dicionario(cls.balanco)

    def test_vira_json_puro(self):
        json.dumps(self.dados)
        self.assertIsInstance(self.dados["saldos"], list)
        self.assertIsInstance(self.dados["receitas"], list)
        self.assertIsInstance(self.dados["despesas"], list)
        self.assertIsInstance(self.dados["categorias"], list)

    def test_sobrevive_a_ida_e_volta(self):
        reconstruido = de_dicionario(self.dados)
        self.assertEqual(reconstruido.competencia, self.balanco.competencia)
        self.assertEqual(reconstruido.receitas_total, self.balanco.receitas_total)
        self.assertEqual(len(reconstruido.despesas), len(self.balanco.despesas))
        self.assertEqual(len(reconstruido.categorias), len(self.balanco.categorias))
        self.assertEqual(reconstruido.resumo, self.balanco.resumo)

    def test_recusa_competencia_que_nao_e_o_primeiro_dia(self):
        invalido = dict(self.dados, competencia="2026-08-15")
        with self.assertRaises(ErroDeContrato):
            validar_dicionario(invalido)

    def test_recusa_versao_de_contrato_desconhecida(self):
        invalido = dict(self.dados, schema_versao=SCHEMA_VERSAO + 1)
        with self.assertRaises(ErroDeContrato):
            validar_dicionario(invalido)
