"""Testes da deteccao automatica do layout do balanco."""
from __future__ import annotations

import hashlib
import urllib.error
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from apps.balancos.constants import LayoutBalanco, StatusImportacao
from apps.balancos.detector import (
    ErroDeDeteccaoDeLayout,
    _baixar_pdf_privado,
    _paginas_para_inspecao,
    detectar_layout_balanco,
    detectar_layout_do_pdf,
)
from apps.balancos.models import BalancoMensal, ImportacaoBalanco
from apps.balancos.storage import ErroDeStorage, TEMPO_LIMITE_SEGUNDOS
from apps.balancos.tests.apoio import PaginaDeImagem, criar_pessoa, gerar_pdf_textual
from apps.condominios.models import Condominio


def pdf_atual_canonico() -> bytes:
    return gerar_pdf_textual(
        "\n".join(
            [
                "Indice",
                "Demonstrativo de Contas",
                "Resumo de Contas",
                "Receitas",
                "Despesas",
                "Resumo Financeiro",
                "Saldo Bancario",
                "Saldo Contabil",
                "Extratos Bancarios",
                "Realizado",
                "Receitas Detalhadas",
                "Inadimplencia",
                "Despesas Detalhadas",
                "Documentos Anexos",
            ]
        ),
        "Resumo financeiro consolidado e demonstrativo de contas do mes.",
    )


def pdf_atual_curto() -> bytes:
    """Formato atual em poucas páginas: só a abertura estrutural."""
    return gerar_pdf_textual(
        "Indice",
        "Demonstrativo de Contas\nResumo de Contas\nResumo Financeiro",
        "Receitas Detalhadas\nDespesas Detalhadas\nSaldo Contabil",
    )


def pdf_atual_longo() -> bytes:
    """Formato atual longo: abertura com os sinais e muitas páginas depois."""
    abertura = [
        "Indice",
        "Demonstrativo de Contas",
        "Resumo de Contas",
        "Resumo Financeiro\nSaldo Bancario\nSaldo Contabil",
    ]
    recheio = [
        f"Receitas detalhadas e despesas detalhadas do periodo, pagina {numero}"
        for numero in range(120)
    ]
    return gerar_pdf_textual(*abertura, *recheio)


def pdf_atual_com_muitos_anexos() -> bytes:
    """Formato atual com o balancete curto seguido de muitos comprovantes."""
    abertura = [
        "Indice",
        "Demonstrativo de Contas",
        "Resumo de Contas",
        "Resumo Financeiro\nSaldo Bancario\nSaldo Contabil",
        "Receitas Detalhadas\nDespesas Detalhadas",
    ]
    anexos = [PaginaDeImagem() for _ in range(60)]
    return gerar_pdf_textual(*abertura, *anexos)


def pdf_atual_parcial() -> bytes:
    """Só parte do balancete foi digitalizada, mas a abertura trouxe os sinais."""
    return gerar_pdf_textual(
        "Indice",
        "Demonstrativo de Contas",
        "Resumo de Contas\nResumo Financeiro\nReceitas Detalhadas\nDespesas Detalhadas",
    )


def pdf_legado() -> bytes:
    return gerar_pdf_textual("Balancete\nQuadro historico")


def pdf_legado_longo() -> bytes:
    return gerar_pdf_textual(
        *[
            f"Balancete exercicio {numero}\nQuadro historico do periodo"
            for numero in range(80)
        ]
    )


def pdf_desconhecido() -> bytes:
    return gerar_pdf_textual("Aviso interno\nAgenda de reuniao do condominio")


def pdf_conflitante() -> bytes:
    return gerar_pdf_textual(
        "Balancete\nReceitas\nDespesas\nResumo Financeiro\nSaldo Bancario\nResumo de Contas",
        "x",
        "x",
    )


def pdf_longo_com_sinais_apenas_na_abertura() -> bytes:
    """Documento longo em que só a abertura traz os sinais do formato atual."""
    texto_rico = (
        "Demonstrativo de contas, receitas, despesas e resumo financeiro do periodo. "
        * 20
    )
    abertura = [
        "Capa do documento",
        "Indice",
        "Demonstrativo de Contas",
        "Receitas",
        "Despesas",
        "Resumo Financeiro",
        "Saldo Contabil",
        f"Resumo de Contas. {texto_rico}",
    ]
    recheio = [f"Pagina {numero}" for numero in range(8, 60)]
    return gerar_pdf_textual(*abertura, *recheio)


class DetectorDoPdfTests(TestCase):
    """Exercita apenas a classificacao local do PDF."""

    def test_reconhece_o_formato_atual_canonico(self):
        resultado = detectar_layout_do_pdf(pdf_atual_canonico())

        self.assertEqual(resultado.layout, LayoutBalanco.ATUAL_CANONICO)
        self.assertGreaterEqual(resultado.confidence, 0.8)
        self.assertGreaterEqual(resultado.paginas_analisadas, 1)
        self.assertIn("modelo atual", " ".join(resultado.evidencias).lower())

    def test_reconhece_legado_quando_ha_evidencia_suficiente(self):
        resultado = detectar_layout_do_pdf(pdf_legado())

        self.assertEqual(resultado.layout, LayoutBalanco.LEGADO)
        self.assertGreaterEqual(resultado.confidence, 0.8)

    def test_mantem_desconhecido_quando_nao_ha_sinais_suficientes(self):
        resultado = detectar_layout_do_pdf(pdf_desconhecido())

        self.assertEqual(resultado.layout, LayoutBalanco.DESCONHECIDO)

    def test_pdf_valido_com_texto_insuficiente_nao_vira_erro(self):
        resultado = detectar_layout_do_pdf(gerar_pdf_textual("x"))

        self.assertEqual(resultado.layout, LayoutBalanco.DESCONHECIDO)
        self.assertGreaterEqual(resultado.confidence, 0.45)

    def test_marcadores_conflitantes_viram_desconhecido(self):
        resultado = detectar_layout_do_pdf(pdf_conflitante())

        self.assertEqual(resultado.layout, LayoutBalanco.DESCONHECIDO)
        self.assertTrue(
            any("conflitantes" in evidencia.lower() for evidencia in resultado.evidencias)
        )

    def test_pdf_corrompido_vira_falha_tecnica(self):
        with patch("pypdf._reader.logger_warning"):
            with self.assertRaises(ErroDeDeteccaoDeLayout):
                detectar_layout_do_pdf(b"%PDF-1.4\nnao fecha objeto nenhum")

    @override_settings(BALANCOS_DETECTOR_MAX_PAGINAS=5)
    def test_limita_paginas_analisadas(self):
        conteudo = gerar_pdf_textual(*[f"Pagina {indice}" for indice in range(30)])

        resultado = detectar_layout_do_pdf(conteudo)

        self.assertEqual(resultado.total_paginas, 30)
        self.assertEqual(resultado.paginas_analisadas, 5)

    def test_amostra_le_apenas_a_abertura(self):
        indices = _paginas_para_inspecao(175)

        self.assertEqual(indices, list(range(15)))

    def test_amostra_le_o_documento_inteiro_quando_curto(self):
        indices = _paginas_para_inspecao(3)

        self.assertEqual(indices, [0, 1, 2])

    @override_settings(BALANCOS_DETECTOR_MAX_PAGINAS=4)
    def test_limite_menor_que_a_abertura_fica_na_abertura(self):
        indices = _paginas_para_inspecao(175)

        self.assertEqual(indices, [0, 1, 2, 3])

    def test_documento_longo_reconhecido_pelos_sinais_da_abertura(self):
        resultado = detectar_layout_do_pdf(pdf_longo_com_sinais_apenas_na_abertura())

        self.assertEqual(resultado.total_paginas, 60)
        self.assertEqual(resultado.layout, LayoutBalanco.ATUAL_CANONICO)


class EstrategiaDePesosTests(TestCase):
    """Confirma a estrategia nova: o conteudo estrutural decide, nao o tamanho.

    Cada cenario muda o formato e o volume de paginas, nunca o modelo, e o
    resultado precisa continuar sendo o mesmo.
    """

    def test_pdf_curto_do_formato_atual(self):
        resultado = detectar_layout_do_pdf(pdf_atual_curto())

        self.assertEqual(resultado.layout, LayoutBalanco.ATUAL_CANONICO)
        self.assertEqual(resultado.total_paginas, 3)

    def test_pdf_longo_do_formato_atual(self):
        resultado = detectar_layout_do_pdf(pdf_atual_longo())

        self.assertEqual(resultado.layout, LayoutBalanco.ATUAL_CANONICO)
        self.assertGreater(resultado.total_paginas, 100)

    def test_pdf_atual_com_muitos_anexos(self):
        resultado = detectar_layout_do_pdf(pdf_atual_com_muitos_anexos())

        self.assertEqual(resultado.layout, LayoutBalanco.ATUAL_CANONICO)
        self.assertGreater(resultado.total_paginas, 60)

    def test_pdf_parcial_classificado_pela_evidencia_estrutural(self):
        resultado = detectar_layout_do_pdf(pdf_atual_parcial())

        self.assertEqual(resultado.layout, LayoutBalanco.ATUAL_CANONICO)

    def test_legado_curto(self):
        resultado = detectar_layout_do_pdf(pdf_legado())

        self.assertEqual(resultado.layout, LayoutBalanco.LEGADO)

    def test_legado_longo(self):
        resultado = detectar_layout_do_pdf(pdf_legado_longo())

        self.assertEqual(resultado.layout, LayoutBalanco.LEGADO)
        self.assertEqual(resultado.total_paginas, 80)

    def test_desconhecido(self):
        resultado = detectar_layout_do_pdf(pdf_desconhecido())

        self.assertEqual(resultado.layout, LayoutBalanco.DESCONHECIDO)

    def test_anexos_nao_mudam_o_layout_do_formato_atual(self):
        """O mesmo balancete atual, com e sem anexos, tem o mesmo layout."""
        sem_anexos = detectar_layout_do_pdf(pdf_atual_curto())
        com_anexos = detectar_layout_do_pdf(pdf_atual_com_muitos_anexos())

        self.assertEqual(sem_anexos.layout, LayoutBalanco.ATUAL_CANONICO)
        self.assertEqual(com_anexos.layout, sem_anexos.layout)


class DetectorDaImportacaoTests(TestCase):
    """Exercita o fluxo de deteccao sobre a importacao persistida."""

    def setUp(self):
        self.usuario = criar_pessoa("gestao.detector@exemplo.com", "Gestao Detector")
        self.condominio = Condominio.objects.create(nome="Condominio Detector")
        conteudo = pdf_desconhecido()
        self.importacao = ImportacaoBalanco.objects.create(
            condominio=self.condominio,
            criado_por=self.usuario,
            nome_original="detector.pdf",
            mime_type="application/pdf",
            tamanho_bytes=len(conteudo),
            sha256=hashlib.sha256(conteudo).hexdigest(),
            storage_provider="supabase",
            storage_bucket="bucket-de-teste",
            storage_key=f"balancos/{self.condominio.pk}/importacao/documento.pdf",
            status=StatusImportacao.RECEBIDO,
        )

    def test_deteccao_atualiza_status_e_layout_sem_criar_balanco(self):
        with patch(
            "apps.balancos.detector._baixar_pdf_privado",
            return_value=(pdf_atual_canonico(), 7),
        ):
            resultado = detectar_layout_balanco(self.importacao)

        self.importacao.refresh_from_db()
        self.assertEqual(self.importacao.status, StatusImportacao.PRONTO)
        self.assertEqual(self.importacao.layout_detectado, LayoutBalanco.ATUAL_CANONICO)
        self.assertEqual(self.importacao.resultado_extraido, {})
        self.assertEqual(self.importacao.inconsistencias, [])
        self.assertEqual(BalancoMensal.objects.count(), 0)
        self.assertGreaterEqual(resultado.tempo_download_ms, 7)

    def test_desconhecido_nao_vira_erro(self):
        with patch(
            "apps.balancos.detector._baixar_pdf_privado",
            return_value=(pdf_desconhecido(), 5),
        ):
            detectar_layout_balanco(self.importacao)

        self.importacao.refresh_from_db()
        self.assertEqual(self.importacao.status, StatusImportacao.PRONTO)
        self.assertEqual(self.importacao.layout_detectado, LayoutBalanco.DESCONHECIDO)

    def test_falha_ao_acessar_storage_vira_erro(self):
        with patch(
            "apps.balancos.detector._baixar_pdf_privado",
            side_effect=ErroDeStorage("Nao foi possivel falar com o armazenamento."),
        ):
            with self.assertRaises(ErroDeStorage):
                detectar_layout_balanco(self.importacao)

        self.importacao.refresh_from_db()
        self.assertEqual(self.importacao.status, StatusImportacao.ERRO)
        self.assertIn("armazenamento", self.importacao.erro_processamento.lower())
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_idempotencia_reexecuta_sem_duplicar_objetos(self):
        with patch(
            "apps.balancos.detector._baixar_pdf_privado",
            return_value=(pdf_atual_canonico(), 3),
        ):
            primeiro = detectar_layout_balanco(self.importacao)
            segundo = detectar_layout_balanco(self.importacao)

        self.importacao.refresh_from_db()
        self.assertEqual(self.importacao.layout_detectado, LayoutBalanco.ATUAL_CANONICO)
        self.assertEqual(self.importacao.status, StatusImportacao.PRONTO)
        self.assertEqual(primeiro.layout, segundo.layout)
        self.assertEqual(ImportacaoBalanco.objects.count(), 1)
        self.assertEqual(BalancoMensal.objects.count(), 0)


class DownloadPrivadoTests(TestCase):
    """Garante que o acesso privado ao PDF continua curto e controlado."""

    def setUp(self):
        self.usuario = criar_pessoa("gestao.download@exemplo.com", "Gestao Download")
        self.condominio = Condominio.objects.create(nome="Condominio Download")
        conteudo = pdf_desconhecido()
        self.importacao = ImportacaoBalanco.objects.create(
            condominio=self.condominio,
            criado_por=self.usuario,
            nome_original="download.pdf",
            mime_type="application/pdf",
            tamanho_bytes=len(conteudo),
            sha256=hashlib.sha256(conteudo).hexdigest(),
            storage_provider="supabase",
            storage_bucket="bucket-de-teste",
            storage_key=f"balancos/{self.condominio.pk}/importacao/documento.pdf",
            status=StatusImportacao.RECEBIDO,
        )

    def test_download_privado_usa_timeout_explicito(self):
        provider = MagicMock()
        provider.url_assinada.return_value = "https://exemplo.invalido/assinada"

        resposta = MagicMock()
        resposta.read.return_value = pdf_desconhecido()
        gerenciador = MagicMock()
        gerenciador.__enter__.return_value = resposta
        gerenciador.__exit__.return_value = False

        with patch("urllib.request.urlopen", return_value=gerenciador) as urlopen:
            conteudo, _ = _baixar_pdf_privado(self.importacao, provider=provider)

        self.assertTrue(conteudo.startswith(b"%PDF-"))
        self.assertEqual(urlopen.call_args.kwargs["timeout"], TEMPO_LIMITE_SEGUNDOS)

    def test_erro_http_nao_vaza_url_assinada(self):
        provider = MagicMock()
        provider.url_assinada.return_value = "https://exemplo.invalido/assinada?token=segredo"
        erro = urllib.error.HTTPError(
            provider.url_assinada.return_value,
            403,
            "forbidden",
            hdrs=None,
            fp=None,
        )

        with patch("urllib.request.urlopen", side_effect=erro):
            with self.assertRaises(ErroDeDeteccaoDeLayout) as capturado:
                _baixar_pdf_privado(self.importacao, provider=provider)

        self.assertNotIn("token", str(capturado.exception).lower())
        self.assertNotIn("https://", str(capturado.exception).lower())
