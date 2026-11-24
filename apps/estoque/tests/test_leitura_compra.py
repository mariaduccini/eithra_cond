"""Leitura local de comprovantes: parser textual e integração com o OCR.

Nenhum teste aqui depende de rede, de um arquivo real ou de o motor de OCR
existir: o texto reconhecido é injetado no lugar do motor e o PDF textual é
sintético. É assim que se prova o comportamento sem fingir que o OCR rodou.
"""
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase

from apps.balancos.tests.apoio import (
    PaginaDeImagem,
    StorageDeMentira,
    gerar_pdf_textual,
)
from apps.core.tests.apoio import CONTEUDO_PNG
from apps.estoque import leitura_compra
from apps.estoque.constants import (
    StatusCompra,
    StatusLeituraDocumentoCompra,
    TipoDocumentoCompra,
    UnidadeMedida,
)
from apps.estoque.leitura_compra import (
    ocr_disponivel,
    processar_texto,
    sugerir_dados,
)
from apps.estoque.models import Compra, ItemCompra
from apps.estoque.services import criar_compra_com_documento
from apps.estoque.tests.apoio import criar_condominio, criar_pessoa

# Formatos fictícios, escritos como o OCR costuma devolver: descrição em uma
# linha, "2 UN x 24,90" na seguinte e o total do item sozinho depois.
CUPOM = "\n".join(
    [
        "MERCADO DO BAIRRO LTDA",
        "CNPJ: 11.222.333/0001-81",
        "CUPOM FISCAL",
        "COO: 000456",
        "15/11/2026 14:32",
        "ARROZ TIPO 1 5KG",
        "2 UN x 24,90",
        "49,80",
        "DETERGENTE NEUTRO 500ML",
        "3 UN x 2,50",
        "7,50",
        "SUBTOTAL 57,30",
        "DESCONTO 2,30",
        "VALOR PAGO 60,00",
        "TROCO 5,00",
        "VALOR TOTAL R$ 55,00",
    ]
)

NOTA = "\n".join(
    [
        "RAZAO SOCIAL: CASA DAS BOMBAS LTDA",
        "CNPJ: 11.222.333/0001-81",
        "NUMERO DA NOTA: 12345",
        "DATA DE EMISSAO: 03/02/2026",
        "FORMA DE PAGAMENTO: Boleto",
        "ITEM: Bomba submersa 1/2 CV | QTD: 2 | UNIDADE: UN | "
        "VALOR UNITARIO: 270,50 | TOTAL: 541,00 | CATEGORIA: Hidraulica",
        "VALOR TOTAL DA NOTA: R$ 541,00",
    ]
)

# A segunda nota sintética: tabela de três itens, com a descrição de dois deles
# quebrada em duas linhas — como o OCR devolve quando a célula não cabe na
# largura da coluna. É a amostra em que cada valor é conferido contra o original.
NOTA_DE_TRES_ITENS = "\n".join(
    [
        "CASA DAS BOMBAS LTDA",
        "CNPJ: 11.222.333/0001-81",
        "NOTA FISCAL 98765",
        "DATA DE EMISSAO: 10/03/2026",
        "DESCRICAO QTD UN VL UNIT VL TOTAL",
        "Bomba submersa",
        "1/2 CV 2 UN 270,50 541,00",
        "Tubo PVC 25mm 10 M 12,40 124,00",
        "Registro de gaveta",
        "1/2 3 UN 89,90 269,70",
        "VALOR TOTAL DA NOTA: R$ 934,70",
    ]
)


class ProcessarTextoTests(SimpleTestCase):
    """O parser puro: entra texto, saem propostas — sem banco e sem OCR."""

    def test_fornecedor_cnpj_data_numero_total_e_itens_do_cupom(self):
        dados = processar_texto(CUPOM)

        self.assertEqual(dados["fornecedor"], "MERCADO DO BAIRRO LTDA")
        self.assertEqual(dados["cnpj"], "11222333000181")
        self.assertEqual(dados["data_compra"], "2026-11-15")
        self.assertEqual(dados["numero_documento"], "000456")
        self.assertEqual(dados["valor_total"], "55.00")

        self.assertEqual(len(dados["itens"]), 2)
        arroz = dados["itens"][0]
        self.assertEqual(arroz["descricao"], "ARROZ TIPO 1 5KG")
        self.assertEqual(arroz["quantidade"], "2")
        self.assertEqual(arroz["unidade"], UnidadeMedida.UNIDADE)
        self.assertEqual(arroz["valor_unitario"], "24.90")
        self.assertEqual(arroz["valor_total"], "49.80")
        detergente = dados["itens"][1]
        self.assertEqual(detergente["descricao"], "DETERGENTE NEUTRO 500ML")
        self.assertEqual(detergente["quantidade"], "3")
        self.assertEqual(detergente["valor_total"], "7.50")

    def test_nota_fiscal_textual_com_campos_rotulados(self):
        dados = processar_texto(NOTA)

        self.assertEqual(dados["fornecedor"], "CASA DAS BOMBAS LTDA")
        self.assertEqual(dados["cnpj"], "11222333000181")
        self.assertEqual(dados["numero_documento"], "12345")
        self.assertEqual(dados["data_compra"], "2026-02-03")
        self.assertEqual(dados["forma_pagamento"], "Boleto")
        self.assertEqual(dados["valor_total"], "541.00")
        item = dados["itens"][0]
        self.assertEqual(item["descricao"], "Bomba submersa 1/2 CV")
        self.assertEqual(item["quantidade"], "2")
        self.assertEqual(item["unidade"], UnidadeMedida.UNIDADE)
        self.assertEqual(item["valor_unitario"], "270.50")
        self.assertEqual(item["valor_total"], "541.00")

    def test_tabela_de_tres_itens_com_descricao_quebrada_em_duas_linhas(self):
        dados = processar_texto(NOTA_DE_TRES_ITENS)

        self.assertEqual(dados["valor_total"], "934.70")
        self.assertEqual(dados["numero_documento"], "98765")
        self.assertFalse(dados["tabela_de_itens_sem_linhas"])

        # Cada linha da nota é conferida contra o original, campo a campo: prova
        # que nenhum valor foi associado ao item errado.
        esperado = [
            ("Bomba submersa 1/2 CV", "2", UnidadeMedida.UNIDADE, "270.50", "541.00"),
            ("Tubo PVC 25mm", "10", UnidadeMedida.METRO, "12.40", "124.00"),
            ("Registro de gaveta 1/2", "3", UnidadeMedida.UNIDADE, "89.90", "269.70"),
        ]
        self.assertEqual(len(dados["itens"]), len(esperado))
        for item, (descricao, quantidade, unidade, unitario, total) in zip(
            dados["itens"], esperado
        ):
            self.assertEqual(item["descricao"], descricao)
            self.assertEqual(item["quantidade"], quantidade)
            self.assertEqual(item["unidade"], unidade)
            self.assertEqual(item["valor_unitario"], unitario)
            self.assertEqual(item["valor_total"], total)
            self.assertFalse(item["revisar"])

    def test_quantidade_encostada_na_unidade_nao_perde_a_linha(self):
        # A reconstrução da linha cola as células com um espaço, mas o OCR pode
        # devolver "2UN" já sem ele: a quantidade aceita vir encostada na unidade.
        dados = processar_texto(
            "\n".join(
                [
                    "DESCRICAO QTD UN VL UNIT VL TOTAL",
                    "Bomba submersa 1/2CV 2UN 270,50 541,00",
                ]
            )
        )

        item = dados["itens"][0]
        self.assertEqual(item["quantidade"], "2")
        self.assertEqual(item["unidade"], UnidadeMedida.UNIDADE)
        self.assertEqual(item["valor_unitario"], "270.50")
        self.assertEqual(item["valor_total"], "541.00")

    def test_item_com_coluna_trocada_fica_marcado_para_revisao(self):
        # Quantidade × unitário não fecham com o total: a linha é aceita, mas
        # marcada — algum valor pode ter caído na coluna errada.
        dados = processar_texto(
            "\n".join(
                [
                    "DESCRICAO QTD UN VL UNIT VL TOTAL",
                    "Bomba submersa 2 UN 541,00 270,50",
                ]
            )
        )

        item = dados["itens"][0]
        self.assertEqual(item["valor_unitario"], "541.00")
        self.assertEqual(item["valor_total"], "270.50")
        self.assertTrue(item["revisar"])

    def test_cabecalho_lido_sem_linhas_de_item_fica_explicito(self):
        dados = processar_texto(
            "\n".join(
                [
                    "DESCRICAO QTD UN VL UNIT VL TOTAL",
                    "Tabela de itens nao reconhecida",
                    "Conferir o documento original",
                ]
            )
        )

        self.assertEqual(dados["itens"], [])
        self.assertTrue(dados["tabela_de_itens_sem_linhas"])

    def test_recibo_simples_le_so_o_total_e_deixa_o_resto_em_branco(self):
        dados = processar_texto(
            "\n".join(["RECIBO DE PAGAMENTO", "VALOR TOTAL R$ 150,00"])
        )

        self.assertEqual(dados["valor_total"], "150.00")
        self.assertIsNone(dados["fornecedor"])
        self.assertIsNone(dados["cnpj"])
        self.assertIsNone(dados["data_compra"])
        self.assertIsNone(dados["numero_documento"])
        self.assertEqual(dados["itens"], [])

    def test_subtotal_desconto_pago_e_troco_nao_viram_total(self):
        dados = processar_texto(
            "\n".join(
                [
                    "SUBTOTAL 292,30",
                    "DESCONTO 12,30",
                    "VALOR PAGO 300,00",
                    "TROCO 20,00",
                ]
            )
        )

        self.assertIsNone(dados["valor_total"])

    def test_rotulos_colados_e_sem_acento_sao_reconhecidos(self):
        dados = processar_texto(
            "\n".join(
                [
                    "VALORTOTALDANOTA 541,00",
                    "RAZAOSOCIAL CASADASBOMBASLTDA",
                    "CNPJ 11222333000181",
                    "DATAEMISSAO 03/02/2026",
                ]
            )
        )

        self.assertEqual(dados["valor_total"], "541.00")
        self.assertEqual(dados["fornecedor"], "CASADASBOMBASLTDA")
        self.assertEqual(dados["cnpj"], "11222333000181")
        self.assertEqual(dados["data_compra"], "2026-02-03")

    def test_valor_na_linha_seguinte_ao_rotulo(self):
        dados = processar_texto(
            "\n".join(
                [
                    "SUPERMERCADO TESTE LTDA",
                    "CNPJ: 11.222.333/0001-81",
                    "SUBTOTAL",
                    "292,30",
                    "DESCONTO",
                    "10,00",
                    "VALOR TOTAL",
                    "282,30",
                ]
            )
        )

        self.assertEqual(dados["valor_total"], "282.30")
        self.assertEqual(dados["itens"], [])

    def test_numero_do_documento_nao_confunde_cnpj_data_nem_valor(self):
        dados = processar_texto(
            "\n".join(
                [
                    "CNPJ 11.222.333/0001-81",
                    "DATA 15/11/2026",
                    "VALOR TOTAL 100,00",
                ]
            )
        )

        self.assertIsNone(dados["numero_documento"])


class SugerirDadosTests(SimpleTestCase):
    """O roteamento por tipo de arquivo, com o OCR substituído por texto fixo."""

    def test_pdf_textual_usa_o_parser_sem_chamar_ocr(self):
        conteudo = gerar_pdf_textual(NOTA)

        with patch("apps.estoque.leitura_compra._texto_por_ocr") as ocr:
            status, dados, erro = sugerir_dados(
                conteudo=conteudo, mime_type="application/pdf"
            )

        ocr.assert_not_called()
        self.assertEqual(status, StatusLeituraDocumentoCompra.LIDO)
        self.assertEqual(erro, "")
        self.assertEqual(dados["valor_total"], "541.00")

    def test_pdf_sem_texto_cai_no_ocr(self):
        conteudo = gerar_pdf_textual(PaginaDeImagem())

        with patch(
            "apps.estoque.leitura_compra._texto_por_ocr", return_value=CUPOM
        ) as ocr:
            status, dados, erro = sugerir_dados(
                conteudo=conteudo, mime_type="application/pdf"
            )

        self.assertEqual(ocr.call_args[0][1], "application/pdf")
        self.assertEqual(status, StatusLeituraDocumentoCompra.LIDO)
        self.assertEqual(dados["valor_total"], "55.00")

    def test_pdf_com_rodape_curto_ainda_vai_para_o_ocr(self):
        # Camada textual pequena (um rodapé): passa de doze caracteres, mas é uma
        # única linha. Sem o corte por linhas úteis, o arquivo iria ao parser
        # textual e a tabela, que está na imagem, ficaria sem OCR.
        conteudo = gerar_pdf_textual("Pagina 1 de 3 - via do cliente")

        with patch(
            "apps.estoque.leitura_compra._texto_por_ocr", return_value=CUPOM
        ) as ocr:
            status, dados, erro = sugerir_dados(
                conteudo=conteudo, mime_type="application/pdf"
            )

        ocr.assert_called_once()
        self.assertEqual(status, StatusLeituraDocumentoCompra.LIDO)
        self.assertEqual(dados["valor_total"], "55.00")
        self.assertIn("OCR", dados["procedencia"])

    def test_cabecalho_sem_itens_avisa_no_rascunho(self):
        conteudo = gerar_pdf_textual(
            "\n".join(
                [
                    "DESCRICAO QTD UN VL UNIT VL TOTAL",
                    "Tabela de itens nao reconhecida",
                    "Conferir o documento original",
                ]
            )
        )

        status, dados, erro = sugerir_dados(
            conteudo=conteudo, mime_type="application/pdf"
        )

        self.assertEqual(status, StatusLeituraDocumentoCompra.LIDO)
        self.assertTrue(dados["tabela_de_itens_sem_linhas"])
        self.assertIn("cabeçalho", dados["aviso_itens"])
        self.assertEqual(dados["itens"], [])

    def test_imagem_cai_no_ocr(self):
        with patch(
            "apps.estoque.leitura_compra._texto_por_ocr", return_value=CUPOM
        ):
            status, dados, erro = sugerir_dados(
                conteudo=CONTEUDO_PNG, mime_type="image/png"
            )

        self.assertEqual(status, StatusLeituraDocumentoCompra.LIDO)
        self.assertEqual(dados["fornecedor"], "MERCADO DO BAIRRO LTDA")

    def test_ocr_indisponivel_fica_para_revisao_manual(self):
        with patch(
            "apps.estoque.leitura_compra._texto_por_ocr",
            side_effect=ImportError("sem rapidocr"),
        ):
            status, dados, erro = sugerir_dados(
                conteudo=CONTEUDO_PNG, mime_type="image/png"
            )

        self.assertEqual(status, StatusLeituraDocumentoCompra.REVISAO_MANUAL)
        self.assertIn("não está instalado", erro)
        self.assertEqual(dados["itens"], [])

    def test_falha_inesperada_no_ocr_vira_erro(self):
        with patch(
            "apps.estoque.leitura_compra._texto_por_ocr",
            side_effect=RuntimeError("falha do motor"),
        ):
            with self.assertLogs("apps.estoque.leitura_compra", level="ERROR"):
                status, dados, erro = sugerir_dados(
                    conteudo=CONTEUDO_PNG, mime_type="image/png"
                )

        self.assertEqual(status, StatusLeituraDocumentoCompra.ERRO)
        self.assertEqual(dados["itens"], [])

    def test_formato_sem_leitura_automatica_fica_para_revisao(self):
        status, dados, erro = sugerir_dados(
            conteudo=b"conteudo qualquer", mime_type="text/plain"
        )

        self.assertEqual(status, StatusLeituraDocumentoCompra.REVISAO_MANUAL)
        self.assertIn("não tem leitura", erro)

    def test_ocr_disponivel_responde_booleano(self):
        self.assertIsInstance(ocr_disponivel(), bool)


class LinhasDaTabelaTests(SimpleTestCase):
    """A tabela remontada pelas coordenadas dos blocos reconhecidos pelo OCR."""

    @staticmethod
    def _bloco(x0, y0, x1, y1, texto):
        """Um bloco no formato cru do motor: caixa de quatro cantos, texto, nota."""
        return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]], texto, 0.99]

    def _texto(self, resultado):
        return leitura_compra._texto_de_blocos(
            leitura_compra._blocos_do_resultado(resultado)
        )

    def test_blocos_da_mesma_linha_voltam_em_ordem_horizontal(self):
        # O motor devolve os blocos fora de ordem: quem manda é a coordenada.
        resultado = [
            self._bloco(560, 80, 650, 100, "541,00"),
            self._bloco(30, 80, 150, 100, "1/2 CV"),
            self._bloco(430, 80, 520, 100, "270,50"),
            self._bloco(300, 80, 320, 100, "2"),
            self._bloco(360, 80, 400, 100, "UN"),
        ]

        self.assertEqual(self._texto(resultado), "1/2 CV 2 UN 270,50 541,00")

    def test_descricao_em_duas_linhas_sobe_para_a_linha_do_item(self):
        resultado = [
            self._bloco(30, 10, 120, 30, "DESCRICAO"),
            self._bloco(300, 10, 340, 30, "QTD"),
            self._bloco(360, 10, 400, 30, "UN"),
            self._bloco(430, 10, 520, 30, "VL UNIT"),
            self._bloco(560, 10, 650, 30, "VL TOTAL"),
            self._bloco(30, 50, 150, 70, "Bomba submersa"),
            self._bloco(30, 80, 80, 100, "1/2 CV"),
            self._bloco(300, 80, 320, 100, "2"),
            self._bloco(360, 80, 400, 100, "UN"),
            self._bloco(430, 80, 520, 100, "270,50"),
            self._bloco(560, 80, 650, 100, "541,00"),
        ]

        item = processar_texto(self._texto(resultado))["itens"][0]

        self.assertEqual(item["descricao"], "Bomba submersa 1/2 CV")
        self.assertEqual(item["quantidade"], "2")
        self.assertEqual(item["unidade"], UnidadeMedida.UNIDADE)
        self.assertEqual(item["valor_unitario"], "270.50")
        self.assertEqual(item["valor_total"], "541.00")
        self.assertFalse(item["revisar"])


class PersistenciaDaLeituraTests(TestCase):
    """A sugestão é gravada no documento e reaparece ao reabrir o rascunho."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao-leitura@exemplo.test", "Gestão")
        self.storage = StorageDeMentira("documentos-compras")
        patcher = patch(
            "apps.estoque.services._provedor_documentos_compra",
            return_value=self.storage,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sugestoes_e_arquivo_persistem_ao_reabrir_o_rascunho(self):
        arquivo = SimpleUploadedFile(
            "cupom.png", CONTEUDO_PNG, content_type="image/png"
        )
        with patch(
            "apps.estoque.leitura_compra._texto_por_ocr", return_value=CUPOM
        ):
            compra, _documento = criar_compra_com_documento(
                condominio=self.condominio,
                criado_por=self.gestao,
                arquivo=arquivo,
                tipo_documento=TipoDocumentoCompra.CUPOM,
            )

        compra_reaberta = Compra.objects.get(pk=compra.pk)
        documento = compra_reaberta.documentos.get()

        self.assertEqual(
            documento.status_leitura, StatusLeituraDocumentoCompra.LIDO
        )
        self.assertEqual(documento.dados_extraidos["valor_total"], "55.00")
        self.assertEqual(documento.dados_extraidos["numero_documento"], "000456")
        self.assertIn(documento.storage_key, self.storage.objetos)
        self.assertEqual(compra_reaberta.status, StatusCompra.RASCUNHO)
        self.assertFalse(ItemCompra.objects.exists())

    def test_linhas_reconhecidas_persistem_ao_reabrir_o_rascunho(self):
        # "Sair e reabrir": a tabela lida por coordenadas volta inteira do banco,
        # com a descrição multi-linha já recolada.
        arquivo = SimpleUploadedFile(
            "nota.png", CONTEUDO_PNG, content_type="image/png"
        )
        with patch(
            "apps.estoque.leitura_compra._texto_por_ocr",
            return_value=NOTA_DE_TRES_ITENS,
        ):
            compra, _documento = criar_compra_com_documento(
                condominio=self.condominio,
                criado_por=self.gestao,
                arquivo=arquivo,
                tipo_documento=TipoDocumentoCompra.NOTA_FISCAL,
            )

        documento = Compra.objects.get(pk=compra.pk).documentos.get()
        itens = documento.dados_extraidos["itens"]

        self.assertEqual(
            documento.status_leitura, StatusLeituraDocumentoCompra.LIDO
        )
        self.assertEqual(len(itens), 3)
        self.assertEqual(itens[0]["descricao"], "Bomba submersa 1/2 CV")
        self.assertEqual(itens[0]["valor_unitario"], "270.50")
        self.assertEqual(itens[0]["valor_total"], "541.00")
        self.assertEqual(itens[2]["descricao"], "Registro de gaveta 1/2")
        self.assertEqual(itens[2]["valor_total"], "269.70")
        self.assertFalse(documento.dados_extraidos["tabela_de_itens_sem_linhas"])
        self.assertFalse(ItemCompra.objects.exists())
