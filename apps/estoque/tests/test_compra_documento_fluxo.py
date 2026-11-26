"""Fluxo do documento da compra: upload, persistência das sugestões e revisão."""
from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.balancos.tests.apoio import StorageDeMentira, pdf_textual, png
from apps.cadastros.constants import TipoPessoa
from apps.cadastros.models import Fornecedor
from apps.condominios.constants import Papel
from apps.estoque.constants import (
    DestinoDoItem,
    StatusCompra,
    StatusLeituraDocumentoCompra,
    TipoDocumentoCompra,
)
from apps.estoque.models import Compra, DocumentoCompra
from apps.estoque.services import (
    adicionar_item_compra,
    confirmar_compra,
    criar_compra,
)
from apps.estoque.tests.apoio import criar_condominio, criar_pessoa, criar_vinculo

NOTA = "\n".join(
    [
        "FORNECEDOR: Casa das Bombas Ltda",
        "CNPJ: 11.222.333/0001-81",
        "DATA DA COMPRA: 15/11/2026",
        "VALOR TOTAL DA NOTA: R$ 541,00",
        "FORMA DE PAGAMENTO: Pix",
        "ITEM: Bomba centrifuga | QTD: 2 | VALOR UNITARIO: 40,00 | TOTAL: 80,00",
    ]
)

CNPJ_DA_NOTA = "11222333000181"


class BaseDoFluxo(TestCase):
    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("fluxo-compra@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)
        self.client.force_login(self.gestao)
        self.storage = StorageDeMentira("documentos-compras")
        patcher = patch(
            "apps.estoque.services._provedor_documentos_compra",
            return_value=self.storage,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def rascunho(self, **campos):
        campos.setdefault("data_compra", None)
        # O rascunho destes testes existe para receber um documento: é esse o
        # caminho que permite abrir a compra ainda sem data nem fornecedor.
        campos.setdefault("tem_documento", True)
        return criar_compra(
            condominio=self.condominio,
            criado_por=self.gestao,
            **campos,
        )

    def dados_do_envio(self, texto=NOTA, arquivo=None):
        return {
            "condominio": str(self.condominio.pk),
            "tipo_documento": TipoDocumentoCompra.NOTA_FISCAL,
            "arquivo": arquivo if arquivo is not None else pdf_textual(texto),
        }

    def anexar(self, compra, texto=NOTA, arquivo=None):
        return self.client.post(
            reverse("compras:adicionar_documento", args=[compra.pk]),
            self.dados_do_envio(texto, arquivo),
        )

    def anexar_automatico(self, compra, texto=NOTA, arquivo=None):
        return self.client.post(
            reverse("compras:adicionar_documento", args=[compra.pk]),
            self.dados_do_envio(texto, arquivo),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def trazer_itens(self, compra, documento):
        return self.client.post(
            reverse("compras:trazer_itens_identificados", args=[compra.pk, documento.pk])
        )

    def reprocessar(self, compra, documento):
        return self.client.post(
            reverse("compras:reprocessar_documento", args=[compra.pk, documento.pk])
        )

    def documento_de(self, compra):
        return DocumentoCompra.objects.get(compra=compra)


class PersistenciaDasSugestoesTests(BaseDoFluxo):
    def test_sugestoes_continuam_apos_nova_requisicao(self):
        compra = self.rascunho()
        resposta = self.anexar(compra)
        self.assertEqual(resposta.status_code, 302)

        # Nova sessão de navegação: nada do formulário anterior permanece.
        self.client.logout()
        self.client.force_login(self.gestao)

        pagina = self.client.get(reverse("compras:detalhe", args=[compra.pk]))

        self.assertEqual(pagina.status_code, 200)
        self.assertContains(pagina, "Casa das Bombas Ltda")
        self.assertContains(pagina, "541,00")
        self.assertContains(pagina, "Bomba centrifuga")

    def test_documento_fica_ligado_a_compra_e_nao_confirma(self):
        compra = self.rascunho()
        self.anexar(compra)

        compra.refresh_from_db()
        documento = self.documento_de(compra)

        self.assertEqual(compra.status, StatusCompra.RASCUNHO)
        self.assertIsNone(compra.data_compra)
        self.assertEqual(compra.itens.count(), 0)
        self.assertEqual(documento.dados_extraidos["valor_total"], "541.00")
        self.assertEqual(Compra.objects.count(), 1)

    def test_topo_mostra_a_sugestao_e_nao_o_zero(self):
        compra = self.rascunho()
        self.anexar(compra)

        pagina = self.client.get(reverse("compras:detalhe", args=[compra.pk]))

        self.assertEqual(pagina.status_code, 200)
        self.assertContains(pagina, "Sugerido pela nota")
        self.assertNotContains(pagina, "Nenhum item ainda")
        self.assertContains(pagina, "Itens identificados")


class UploadAutomaticoTests(BaseDoFluxo):
    def test_envio_automatico_guarda_e_le_o_documento(self):
        compra = self.rascunho()

        resposta = self.anexar_automatico(compra)

        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertTrue(corpo["ok"])
        self.assertEqual(
            corpo["status_leitura"],
            DocumentoCompra.objects.get(compra=compra).get_status_leitura_display(),
        )
        documento = self.documento_de(compra)
        self.assertEqual(documento.status_leitura, StatusLeituraDocumentoCompra.LIDO)
        self.assertEqual(documento.dados_extraidos["valor_total"], "541.00")
        self.assertEqual(len(documento.sha256), 64)
        self.assertIn(documento.storage_key, self.storage.objetos)

    def test_envio_repetido_nao_duplica_o_documento(self):
        compra = self.rascunho()
        self.anexar_automatico(compra)

        resposta = self.anexar_automatico(compra)

        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(resposta.json()["ok"])
        self.assertEqual(DocumentoCompra.objects.filter(compra=compra).count(), 1)

    def test_envio_sem_arquivo_devolve_erro_e_nao_guarda_nada(self):
        compra = self.rascunho()

        resposta = self.client.post(
            reverse("compras:adicionar_documento", args=[compra.pk]),
            {
                "condominio": str(self.condominio.pk),
                "tipo_documento": TipoDocumentoCompra.NOTA_FISCAL,
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(resposta.json()["ok"])
        self.assertEqual(DocumentoCompra.objects.filter(compra=compra).count(), 0)

    def test_falha_de_leitura_nao_descarta_arquivo_nem_rascunho(self):
        compra = self.rascunho()

        resposta = self.anexar_automatico(compra, arquivo=png())

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.json()["ok"])
        compra.refresh_from_db()
        documento = self.documento_de(compra)
        self.assertEqual(compra.status, StatusCompra.RASCUNHO)
        self.assertIsNotNone(documento)
        self.assertEqual(
            documento.status_leitura, StatusLeituraDocumentoCompra.REVISAO_MANUAL
        )
        self.assertTrue(documento.erro_leitura)
        self.assertIn(documento.storage_key, self.storage.objetos)


class ReprocessamentoTests(BaseDoFluxo):
    def test_reprocessar_relê_sem_criar_documento_novo(self):
        compra = self.rascunho()
        self.anexar(compra)
        documento = self.documento_de(compra)
        chave = documento.storage_key

        resposta = self.reprocessar(compra, documento)

        self.assertEqual(resposta.status_code, 302)
        documento.refresh_from_db()
        self.assertEqual(DocumentoCompra.objects.filter(compra=compra).count(), 1)
        self.assertEqual(documento.storage_key, chave)
        # A releitura atualiza as propostas sem apagar fornecedor nem total.
        self.assertEqual(documento.dados_extraidos["valor_total"], "541.00")
        self.assertEqual(documento.dados_extraidos["fornecedor"], "Casa das Bombas Ltda")
        self.assertEqual(compra.itens.count(), 0)

    def test_reprocessar_recusa_quando_ja_ha_itens_incluidos(self):
        compra = self.rascunho()
        self.anexar(compra)
        documento = self.documento_de(compra)
        self.trazer_itens(compra, documento)

        resposta = self.reprocessar(compra, documento)

        self.assertEqual(resposta.status_code, 302)
        documento.refresh_from_db()
        self.assertEqual(compra.itens.count(), 1)
        self.assertTrue(documento.dados_extraidos["itens"][0]["item_compra_id"])

    def test_reprocessar_avisa_quando_o_arquivo_sumiu(self):
        compra = self.rascunho()
        self.anexar(compra)
        documento = self.documento_de(compra)
        self.storage.falhar_ao_baixar = True

        resposta = self.reprocessar(compra, documento)

        self.assertEqual(resposta.status_code, 302)
        documento.refresh_from_db()
        self.assertEqual(documento.dados_extraidos["valor_total"], "541.00")
        self.assertEqual(DocumentoCompra.objects.filter(compra=compra).count(), 1)


class ItensIdentificadosTests(BaseDoFluxo):
    def test_trazer_todos_cria_linhas_de_uso_imediato(self):
        compra = self.rascunho()
        self.anexar(compra)
        documento = self.documento_de(compra)

        resposta = self.trazer_itens(compra, documento)

        self.assertEqual(resposta.status_code, 302)
        linha = compra.itens.get()
        self.assertEqual(linha.descricao, "Bomba centrifuga")
        self.assertEqual(linha.destino, DestinoDoItem.USO_IMEDIATO)

    def test_trazer_todos_nao_duplica_itens(self):
        compra = self.rascunho()
        self.anexar(compra)
        documento = self.documento_de(compra)
        self.trazer_itens(compra, documento)

        resposta = self.trazer_itens(compra, documento)

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(compra.itens.count(), 1)
        documento.refresh_from_db()
        self.assertEqual(len(documento.dados_extraidos["itens"]), 1)

    def test_itens_identificados_reaparecem_apos_nova_requisicao(self):
        compra = self.rascunho()
        self.anexar(compra)

        self.client.logout()
        self.client.force_login(self.gestao)
        pagina = self.client.get(reverse("compras:detalhe", args=[compra.pk]))

        self.assertContains(pagina, "Itens identificados")
        self.assertContains(pagina, "Bomba centrifuga")
        self.assertContains(pagina, "Trazer todos os itens identificados")


class FornecedorTests(BaseDoFluxo):
    def test_upload_nao_cria_fornecedor(self):
        compra = self.rascunho()

        self.anexar(compra)

        self.assertEqual(Fornecedor.objects.count(), 0)
        compra.refresh_from_db()
        self.assertIsNone(compra.fornecedor_id)

    def test_confirmacao_com_autorizacao_cria_e_vincula_o_fornecedor(self):
        compra = self.rascunho()
        self.anexar(compra)
        self.trazer_itens(compra, self.documento_de(compra))

        resposta = self.client.post(
            reverse("compras:confirmar", args=[compra.pk]),
            {
                "confirmar-data_compra": "2026-11-15",
                "autorizar_fornecedor_novo": "on",
            },
        )

        self.assertEqual(resposta.status_code, 302)
        compra.refresh_from_db()
        self.assertEqual(compra.status, StatusCompra.CONFIRMADA)
        self.assertIsNotNone(compra.fornecedor_id)
        self.assertEqual(compra.fornecedor.documento, CNPJ_DA_NOTA)
        self.assertEqual(compra.fornecedor.tipo_pessoa, TipoPessoa.PJ)
        self.assertEqual(Fornecedor.objects.count(), 1)

    def test_confirmacao_sem_autorizacao_nao_cria_fornecedor(self):
        compra = self.rascunho()
        self.anexar(compra)
        self.trazer_itens(compra, self.documento_de(compra))

        resposta = self.client.post(
            reverse("compras:confirmar", args=[compra.pk]),
            {"confirmar-data_compra": "2026-11-15"},
        )

        self.assertEqual(resposta.status_code, 302)
        compra.refresh_from_db()
        self.assertEqual(compra.status, StatusCompra.CONFIRMADA)
        self.assertIsNone(compra.fornecedor_id)
        self.assertEqual(Fornecedor.objects.count(), 0)

    def test_cnpj_existente_reutiliza_o_fornecedor(self):
        existente = Fornecedor.objects.create(
            condominio=self.condominio,
            nome_razao_social="Casa das Bombas Ltda",
            documento=CNPJ_DA_NOTA,
            tipo_pessoa=TipoPessoa.PJ,
        )
        compra = self.rascunho()
        self.anexar(compra)
        self.trazer_itens(compra, self.documento_de(compra))

        resposta = self.client.post(
            reverse("compras:confirmar", args=[compra.pk]),
            {
                "confirmar-data_compra": "2026-11-15",
                "autorizar_fornecedor_novo": "on",
            },
        )

        self.assertEqual(resposta.status_code, 302)
        compra.refresh_from_db()
        self.assertEqual(compra.fornecedor_id, existente.pk)
        self.assertEqual(Fornecedor.objects.count(), 1)


class DuplicidadeTests(BaseDoFluxo):
    def test_confirmacao_recusa_documento_ja_usado(self):
        fornecedor = Fornecedor.objects.create(
            condominio=self.condominio,
            nome_razao_social="Casa das Bombas Ltda",
            documento=CNPJ_DA_NOTA,
            tipo_pessoa=TipoPessoa.PJ,
        )
        primeira = self.rascunho(
            fornecedor=fornecedor,
            numero_documento="NF-123",
            data_compra=date(2026, 11, 15),
        )
        adicionar_item_compra(
            compra=primeira,
            descricao="Bomba centrifuga",
            quantidade=1,
            valor_unitario="40.00",
            destino=DestinoDoItem.USO_IMEDIATO,
        )
        confirmar_compra(compra=primeira, usuario=self.gestao)

        segunda = self.rascunho()
        resposta = self.client.post(
            reverse("compras:confirmar", args=[segunda.pk]),
            {
                "confirmar-data_compra": "2026-11-15",
                "confirmar-fornecedor": str(fornecedor.pk),
                "confirmar-numero_documento": "NF-123",
            },
        )

        self.assertEqual(resposta.status_code, 302)
        segunda.refresh_from_db()
        self.assertEqual(segunda.status, StatusCompra.RASCUNHO)
        self.assertEqual(
            Compra.objects.filter(status=StatusCompra.CONFIRMADA).count(), 1
        )


class SegurancaTests(BaseDoFluxo):
    def test_upload_exige_login(self):
        compra = self.rascunho()
        self.client.logout()

        resposta = self.anexar_automatico(compra)

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/entrar/", resposta.headers["Location"])
        self.assertEqual(DocumentoCompra.objects.filter(compra=compra).count(), 0)

    def test_documento_de_outro_condominio_nao_pode_ser_usado(self):
        compra = self.rascunho()
        self.anexar(compra)
        documento = self.documento_de(compra)

        outro_condominio = criar_condominio("Beta")
        outra_pessoa = criar_pessoa("outro@exemplo.test", "Outra pessoa")
        criar_vinculo(outra_pessoa, outro_condominio, Papel.SINDICA)
        self.client.force_login(outra_pessoa)

        resposta = self.trazer_itens(compra, documento)

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(compra.itens.count(), 0)

    def test_pessoa_sem_vinculo_recebe_404(self):
        compra = self.rascunho()
        self.anexar(compra)
        documento = self.documento_de(compra)

        sem_vinculo = criar_pessoa("sem-vinculo@exemplo.test", "Sem vínculo")
        self.client.force_login(sem_vinculo)

        resposta = self.reprocessar(compra, documento)

        self.assertEqual(resposta.status_code, 404)

