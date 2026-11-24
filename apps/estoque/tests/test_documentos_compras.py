"""Documento, leitura revisável, catálogo canônico e reembolso da compra."""
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.balancos.tests.apoio import StorageDeMentira, pdf_textual
from apps.condominios.constants import Papel
from apps.core.tests.apoio import CONTEUDO_PNG
from apps.estoque.constants import (
    DestinoDoItem,
    StatusCompra,
    StatusLeituraDocumentoCompra,
    StatusReembolso,
    TipoDocumentoCompra,
)
from apps.estoque.forms import ReembolsoCompraForm
from apps.estoque.models import Compra, DocumentoCompra, ItemCompra, ItemEstoque
from apps.estoque.services import (
    ErroDeEstoque,
    adicionar_item_compra,
    cadastrar_item_extraido,
    configurar_reembolso_compra,
    confirmar_compra,
    criar_compra,
    criar_compra_com_documento,
    incluir_item_extraido,
    marcar_reembolso_realizado,
    selecionar_item_extraido,
)
from apps.estoque.tests.apoio import (
    criar_condominio,
    criar_item_estoque,
    criar_pessoa,
    criar_vinculo,
)


class DocumentoCompraTests(TestCase):
    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao-compras@exemplo.test", "Gestão")
        self.storage = StorageDeMentira("documentos-compras")
        patcher = patch(
            "apps.estoque.services._provedor_documentos_compra",
            return_value=self.storage,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _pdf(self, texto, nome="nota.pdf"):
        return pdf_textual(texto, nome=nome)

    def _compra_com_documento(self, texto):
        return criar_compra_com_documento(
            condominio=self.condominio,
            criado_por=self.gestao,
            arquivo=self._pdf(texto),
            tipo_documento=TipoDocumentoCompra.NOTA_FISCAL,
        )

    def test_upload_guarda_metadados_e_vincula_documento_a_compra(self):
        compra, documento = self._compra_com_documento("Documento de teste")

        self.assertEqual(compra.documentos.get(), documento)
        self.assertEqual(documento.criado_por, self.gestao)
        self.assertEqual(documento.storage_provider, "mentira")
        self.assertEqual(documento.storage_bucket, "documentos-compras")
        self.assertIn(documento.storage_key, self.storage.objetos)
        self.assertEqual(documento.status_leitura, StatusLeituraDocumentoCompra.REVISAO_MANUAL)
        self.assertFalse(ItemCompra.objects.exists())

    def test_leitura_parcial_gera_sugestoes_sem_confirmar_ou_persistir_linhas(self):
        texto = "\n".join(
            [
                "FORNECEDOR: Loja Central",
                "CNPJ: 11222333000181",
                "DATA DA COMPRA: 15/11/2026",
                "VALOR TOTAL DA NOTA: R$ 12,50",
                "FORMA DE PAGAMENTO: Pix",
                "ITEM: Lampada LED | QTD: 2 | VALOR UNITARIO: 6,25 | TOTAL: 12,50 | CATEGORIA: Eletrica",
            ]
        )

        compra, documento = self._compra_com_documento(texto)
        compra.refresh_from_db()
        documento.refresh_from_db()

        self.assertEqual(documento.status_leitura, StatusLeituraDocumentoCompra.LIDO)
        self.assertEqual(documento.dados_extraidos["fornecedor"], "Loja Central")
        self.assertEqual(documento.dados_extraidos["data_compra"], "2026-11-15")
        self.assertEqual(documento.dados_extraidos["valor_total"], "12.50")
        self.assertEqual(documento.dados_extraidos["itens"][0]["valor_unitario"], "6.25")
        self.assertIsNone(compra.data_compra)
        self.assertIsNone(compra.fornecedor_id)
        self.assertEqual(compra.status, StatusCompra.RASCUNHO)
        self.assertFalse(ItemCompra.objects.exists())

    def test_imagem_sem_texto_reconhecivel_e_guardada_sem_inventar_dados(self):
        """A foto é armazenada; sem texto legível, nada é deduzido."""
        arquivo = SimpleUploadedFile(
            "cupom.png", CONTEUDO_PNG, content_type="image/png"
        )
        with patch(
            "apps.estoque.leitura_compra._texto_por_ocr", return_value=""
        ):
            compra, documento = criar_compra_com_documento(
                condominio=self.condominio,
                criado_por=self.gestao,
                arquivo=arquivo,
                tipo_documento=TipoDocumentoCompra.CUPOM,
            )

        self.assertEqual(documento.compra, compra)
        self.assertIn(documento.storage_key, self.storage.objetos)
        self.assertEqual(documento.status_leitura, StatusLeituraDocumentoCompra.REVISAO_MANUAL)
        self.assertIn("não foi possível reconhecer texto", documento.erro_leitura)
        self.assertEqual(documento.dados_extraidos["itens"], [])
        self.assertIsNone(documento.dados_extraidos["valor_total"])
        self.assertIsNone(documento.dados_extraidos["fornecedor"])

    def test_imagem_com_texto_legivel_gera_sugestoes_para_revisao(self):
        """Uma foto de cupom reconhecida preenche as sugestões sem confirmar nada."""
        texto_ocr = "\n".join(
            [
                "CASA DAS BOMBAS LTDA",
                "CNPJ: 11.222.333/0001-81",
                "CUPOM FISCAL 000123",
                "DATA: 15/11/2026",
                "BOMBA SUBMERSA 1/2 CV",
                "2 UN x 32,90",
                "65,80",
                "SUBTOTAL 65,80",
                "TROCO 0,20",
                "VALOR TOTAL R$ 65,80",
            ]
        )
        arquivo = SimpleUploadedFile(
            "cupom.png", CONTEUDO_PNG, content_type="image/png"
        )
        with patch(
            "apps.estoque.leitura_compra._texto_por_ocr", return_value=texto_ocr
        ):
            compra, documento = criar_compra_com_documento(
                condominio=self.condominio,
                criado_por=self.gestao,
                arquivo=arquivo,
                tipo_documento=TipoDocumentoCompra.CUPOM,
            )

        self.assertEqual(documento.status_leitura, StatusLeituraDocumentoCompra.LIDO)
        self.assertEqual(documento.dados_extraidos["fornecedor"], "CASA DAS BOMBAS LTDA")
        self.assertEqual(documento.dados_extraidos["cnpj"], "11222333000181")
        self.assertEqual(documento.dados_extraidos["data_compra"], "2026-11-15")
        self.assertEqual(documento.dados_extraidos["valor_total"], "65.80")
        item = documento.dados_extraidos["itens"][0]
        self.assertEqual(item["descricao"], "BOMBA SUBMERSA 1/2 CV")
        self.assertEqual(item["quantidade"], "2")
        self.assertEqual(item["valor_unitario"], "32.90")
        self.assertEqual(item["valor_total"], "65.80")
        self.assertEqual(compra.status, StatusCompra.RASCUNHO)
        self.assertFalse(ItemCompra.objects.exists())

    def test_arquivo_duplicado_na_compra_e_recusado(self):
        compra, _documento = self._compra_com_documento("Recibo sem campos")
        with self.assertRaisesMessage(ErroDeEstoque, "já está anexado"):
            from apps.estoque.services import adicionar_documento_compra

            adicionar_documento_compra(
                compra=compra,
                arquivo=self._pdf("Recibo sem campos", nome="outra-copia.pdf"),
                usuario=self.gestao,
                tipo_documento=TipoDocumentoCompra.RECIBO,
            )
        self.assertEqual(compra.documentos.count(), 1)
        self.assertEqual(len(self.storage.objetos), 1)

    def test_item_exato_vincula_catalogo_sem_criar_item_duplicado(self):
        item = criar_item_estoque(
            self.condominio, self.gestao, nome="Lampada LED"
        )
        _compra, documento = self._compra_com_documento(
            "ITEM: Lampada LED | QTD: 1 | VALOR UNITARIO: 5,00"
        )

        proposta = documento.dados_extraidos["itens"][0]
        self.assertEqual(proposta["item_estoque_sugerido"], str(item.pk))
        self.assertEqual(ItemEstoque.objects.count(), 1)

    def test_item_nao_encontrado_exige_escolha_e_nao_cria_catalogo(self):
        parecido = criar_item_estoque(
            self.condominio, self.gestao, nome="Valvula nova em bronze"
        )
        _compra, documento = self._compra_com_documento(
            "ITEM: Valvula nova | QTD: 2 | VALOR UNITARIO: 3,50"
        )

        self.assertEqual(documento.dados_extraidos["itens"][0]["item_estoque_sugerido"], "")
        self.assertEqual(
            documento.dados_extraidos["itens"][0]["item_estoque_candidatos"],
            [str(parecido.pk)],
        )
        self.assertEqual(ItemEstoque.objects.count(), 1)

    def test_selecionar_item_existente_e_incluir_linha_apenas_uma_vez(self):
        existente = criar_item_estoque(
            self.condominio, self.gestao, nome="Lampada LED premium"
        )
        compra, documento = self._compra_com_documento(
            "ITEM: Lampada LED | QTD: 2 | VALOR UNITARIO: 6,25"
        )
        selecionar_item_extraido(documento=documento, indice=0, item=existente)
        linha = incluir_item_extraido(
            compra=compra,
            documento=documento,
            indice=0,
            descricao="Lampada LED revisada",
            quantidade=Decimal("2"),
            valor_unitario=Decimal("6.25"),
            unidade="unidade",
            destino=DestinoDoItem.ESTOQUE,
            item_estoque=existente,
        )

        with self.assertRaisesMessage(ErroDeEstoque, "já foi incluído"):
            incluir_item_extraido(
                compra=compra,
                documento=documento,
                indice=0,
                descricao="Lampada LED revisada",
                quantidade=Decimal("2"),
                valor_unitario=Decimal("6.25"),
                unidade="unidade",
                destino=DestinoDoItem.ESTOQUE,
                item_estoque=existente,
            )
        self.assertEqual(compra.itens.count(), 1)
        self.assertEqual(linha.item_estoque, existente)

    def test_novo_item_so_e_criado_apos_acao_explicita_e_usa_catalogo_canonico(self):
        compra, documento = self._compra_com_documento(
            "ITEM: Item sem cadastro | QTD: 1 | VALOR UNITARIO: 4,00 | CATEGORIA: Geral"
        )

        item = cadastrar_item_extraido(
            documento=documento,
            indice=0,
            criado_por=self.gestao,
            nome="Item revisado",
            categoria_texto="Material geral",
        )

        self.assertEqual(item.condominio, self.condominio)
        self.assertEqual(item.nome, "Item revisado")
        self.assertEqual(item.categoria_texto, "Material geral")
        self.assertEqual(ItemEstoque.objects.count(), 1)
        self.assertEqual(
            compra.documentos.get().dados_extraidos["itens"][0][
                "item_estoque_sugerido"
            ],
            str(item.pk),
        )

    def test_usuario_de_outro_condominio_nao_pode_receber_reembolso(self):
        outro = criar_condominio("Beta")
        usuario_alheio = criar_pessoa("alheio@exemplo.test", "Pessoa de Beta")
        criar_vinculo(usuario_alheio, outro, Papel.SINDICA)
        formulario = ReembolsoCompraForm(
            {
                "comprada_por_outra_pessoa": "on",
                "precisa_reembolso": "on",
                "reembolso_para": str(usuario_alheio.pk),
                "valor_reembolso": "5.00",
            },
            condominio=self.condominio,
            total=Decimal("10.00"),
        )

        self.assertFalse(formulario.is_valid())
        self.assertIn("reembolso_para", formulario.errors)

    def test_formulario_aceita_usuario_ativo_e_valor_parcial(self):
        usuario = criar_pessoa("reembolso-valido@exemplo.test", "Pessoa")
        criar_vinculo(usuario, self.condominio, Papel.SINDICA)
        formulario = ReembolsoCompraForm(
            {
                "comprada_por_outra_pessoa": "on",
                "precisa_reembolso": "on",
                "reembolso_para": str(usuario.pk),
                "valor_reembolso": "35.00",
            },
            condominio=self.condominio,
            total=Decimal("100.00"),
        )

        self.assertTrue(formulario.is_valid(), formulario.errors)
        self.assertEqual(
            formulario.cleaned_data["reembolso_para"],
            usuario,
        )
        self.assertEqual(
            formulario.cleaned_data["valor_reembolso"],
            Decimal("35.00"),
        )

    def test_item_de_outro_condominio_nao_pode_ser_vinculado(self):
        outro = criar_condominio("Beta")
        item_alheio = criar_item_estoque(
            outro, self.gestao, nome="Item do outro condomínio"
        )
        _compra, documento = self._compra_com_documento(
            "ITEM: Item sem cadastro | QTD: 1"
        )

        with self.assertRaisesMessage(ErroDeEstoque, "deste condomínio"):
            selecionar_item_extraido(
                documento=documento,
                indice=0,
                item=item_alheio,
            )

    def test_compra_sem_reembolso_nao_cria_registro_financeiro_paralelo(self):
        compra = criar_compra(
            condominio=self.condominio,
            criado_por=self.gestao,
            data_compra=date.today(),
        )
        configurar_reembolso_compra(
            compra=compra,
            comprada_por_outra_pessoa=False,
            precisa_reembolso=False,
        )

        compra.refresh_from_db()
        self.assertFalse(compra.precisa_reembolso)
        self.assertEqual(compra.status_reembolso, StatusReembolso.NAO_SOLICITADO)
        self.assertEqual(Compra.objects.count(), 1)

    def test_reembolso_parcial_pende_e_registra_data_ao_ser_realizado(self):
        usuario = criar_pessoa("reembolso@exemplo.test", "Pessoa Reembolsada")
        criar_vinculo(usuario, self.condominio, Papel.SINDICA)
        compra = criar_compra(
            condominio=self.condominio,
            criado_por=self.gestao,
            data_compra=date.today(),
        )
        from apps.estoque.services import adicionar_item_compra

        adicionar_item_compra(
            compra=compra,
            descricao="Compra de teste",
            quantidade=Decimal("1"),
            valor_unitario=Decimal("100.00"),
            destino=DestinoDoItem.USO_IMEDIATO,
        )
        configurar_reembolso_compra(
            compra=compra,
            comprada_por_outra_pessoa=True,
            precisa_reembolso=True,
            reembolso_para=usuario,
            valor_reembolso=Decimal("35.00"),
            observacao_reembolso="Parcial",
        )
        confirmar_compra(compra=compra, usuario=self.gestao)

        compra.refresh_from_db()
        self.assertEqual(compra.valor_reembolso, Decimal("35.00"))
        self.assertEqual(compra.status_reembolso, StatusReembolso.PENDENTE)
        self.assertEqual(Compra.objects.count(), 1)
        self.assertEqual(ItemCompra.objects.count(), 1)

        marcar_reembolso_realizado(compra=compra)
        compra.refresh_from_db()
        self.assertEqual(compra.status_reembolso, StatusReembolso.REEMBOLSADO)
        self.assertIsNotNone(compra.reembolsado_em)
        self.assertEqual(Compra.objects.count(), 1)


class UploadCompraViewTests(TestCase):
    def test_tela_principal_prioriza_importacao_e_oferece_cadastro_manual(self):
        condominio = criar_condominio("Alpha")
        gestao = criar_pessoa("tela-compra@exemplo.test", "Gestão")
        criar_vinculo(gestao, condominio, Papel.SINDICA)
        self.client.force_login(gestao)

        resposta = self.client.get(reverse("compras:criar"))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Enviar documento")
        self.assertContains(resposta, "Cadastrar manualmente")

    def test_detalhe_apresenta_leitura_separada_dos_dados_da_compra(self):
        condominio = criar_condominio("Alpha")
        gestao = criar_pessoa("detalhe-compra@exemplo.test", "Gestão")
        criar_vinculo(gestao, condominio, Papel.SINDICA)
        self.client.force_login(gestao)
        storage = StorageDeMentira("documentos-compras")
        patcher = patch(
            "apps.estoque.services._provedor_documentos_compra",
            return_value=storage,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        compra, _documento = criar_compra_com_documento(
            condominio=condominio,
            criado_por=gestao,
            arquivo=pdf_textual(
                "ITEM: Lampada LED | QTD: 2 | VALOR UNITARIO: 6,25"
            ),
            tipo_documento=TipoDocumentoCompra.NOTA_FISCAL,
        )

        resposta = self.client.get(
            reverse("compras:detalhe", args=[compra.pk])
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Documento e leitura da compra")
        self.assertContains(resposta, "Lampada LED")
        self.assertContains(resposta, "Item não encontrado")
        self.assertContains(resposta, "Cadastrar item")
        compra.refresh_from_db()
        self.assertIsNone(compra.data_compra)
        self.assertEqual(compra.itens.count(), 0)

    def test_detalhe_avisa_quando_a_tabela_de_itens_ficou_pendente(self):
        condominio = criar_condominio("Alpha")
        gestao = criar_pessoa("pendentes-compra@exemplo.test", "Gestão")
        criar_vinculo(gestao, condominio, Papel.SINDICA)
        self.client.force_login(gestao)
        storage = StorageDeMentira("documentos-compras")
        patcher = patch(
            "apps.estoque.services._provedor_documentos_compra",
            return_value=storage,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        arquivo = SimpleUploadedFile(
            "nota.png", CONTEUDO_PNG, content_type="image/png"
        )
        with patch(
            "apps.estoque.leitura_compra._texto_por_ocr",
            return_value=(
                "DESCRICAO QTD UN VL UNIT VL TOTAL\n"
                "Tabela de itens nao reconhecida\n"
                "Conferir o documento original"
            ),
        ):
            compra, _documento = criar_compra_com_documento(
                condominio=condominio,
                criado_por=gestao,
                arquivo=arquivo,
                tipo_documento=TipoDocumentoCompra.NOTA_FISCAL,
            )

        resposta = self.client.get(reverse("compras:detalhe", args=[compra.pk]))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(
            resposta,
            "O cabeçalho da tabela de itens foi reconhecido",
        )

    def test_detalhe_marca_a_linha_cuja_associacao_e_incerta(self):
        condominio = criar_condominio("Alpha")
        gestao = criar_pessoa("incerta-compra@exemplo.test", "Gestão")
        criar_vinculo(gestao, condominio, Papel.SINDICA)
        self.client.force_login(gestao)
        storage = StorageDeMentira("documentos-compras")
        patcher = patch(
            "apps.estoque.services._provedor_documentos_compra",
            return_value=storage,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        arquivo = SimpleUploadedFile(
            "nota.png", CONTEUDO_PNG, content_type="image/png"
        )
        with patch(
            "apps.estoque.leitura_compra._texto_por_ocr",
            return_value=(
                "DESCRICAO QTD UN VL UNIT VL TOTAL\n"
                "Bomba submersa 2 UN 541,00 270,50"
            ),
        ):
            compra, _documento = criar_compra_com_documento(
                condominio=condominio,
                criado_por=gestao,
                arquivo=arquivo,
                tipo_documento=TipoDocumentoCompra.NOTA_FISCAL,
            )

        resposta = self.client.get(reverse("compras:detalhe", args=[compra.pk]))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Confira esta linha")

    def test_upload_persiste_documento_e_nao_confirma_compra(self):
        condominio = criar_condominio("Alpha")
        gestao = criar_pessoa("upload-compra@exemplo.test", "Gestão")
        criar_vinculo(gestao, condominio, Papel.SINDICA)
        self.client.force_login(gestao)
        storage = StorageDeMentira("documentos-compras")
        patcher = patch(
            "apps.estoque.services._provedor_documentos_compra",
            return_value=storage,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        resposta = self.client.post(
            reverse("compras:criar"),
            {
                "condominio": str(condominio.pk),
                "tipo_documento": TipoDocumentoCompra.RECIBO,
                "arquivo": pdf_textual("RECIBO"),
            },
        )

        self.assertEqual(resposta.status_code, 302)
        compra = Compra.objects.get()
        documento = DocumentoCompra.objects.get(compra=compra)
        self.assertEqual(compra.status, StatusCompra.RASCUNHO)
        self.assertEqual(compra.data_compra, None)
        self.assertEqual(documento.tipo_documento, TipoDocumentoCompra.RECIBO)
        self.assertEqual(compra.itens.count(), 0)

    def test_confirmacao_da_tela_registra_reembolso_parcial_na_compra(self):
        condominio = criar_condominio("Alpha")
        gestao = criar_pessoa("confirmar-compra@exemplo.test", "Gestão")
        reembolsada = criar_pessoa("beneficiaria@exemplo.test", "Beneficiária")
        criar_vinculo(gestao, condominio, Papel.SINDICA)
        criar_vinculo(reembolsada, condominio, Papel.MORADOR)
        self.client.force_login(gestao)
        compra = criar_compra(
            condominio=condominio,
            criado_por=gestao,
            data_compra=date(2026, 11, 15),
        )
        adicionar_item_compra(
            compra=compra,
            descricao="Material",
            quantidade=Decimal("1"),
            valor_unitario=Decimal("100.00"),
            destino=DestinoDoItem.USO_IMEDIATO,
        )

        resposta = self.client.post(
            reverse("compras:confirmar", args=[compra.pk]),
            {
                "condominio": str(condominio.pk),
                "data_compra": "2026-11-15",
                "fornecedor": "",
                "forma_pagamento": "Pix",
                "numero_documento": "",
                "observacao": "",
                "manutencao_origem": "",
                "comprada_por_outra_pessoa": "on",
                "precisa_reembolso": "on",
                "reembolso_para": str(reembolsada.pk),
                "valor_reembolso": "35.00",
                "observacao_reembolso": "Parcial",
            },
        )

        self.assertEqual(resposta.status_code, 302)
        compra.refresh_from_db()
        self.assertEqual(compra.status, StatusCompra.CONFIRMADA)
        self.assertEqual(compra.status_reembolso, StatusReembolso.PENDENTE)
        self.assertEqual(compra.valor_reembolso, Decimal("35.00"))
        self.assertEqual(Compra.objects.count(), 1)
