"""Arquivos de um documento: o que comprova o que a gestão registrou.

O documento guarda o registro; o arquivo é o que o sustenta. O que se verifica
aqui é que o arquivo se prende ao documento certo, que o formato é conferido
pelo conteúdo e não pelo nome, que uma falha de armazenamento não deixa registro
sem conteúdo e que o acesso é gerado na hora, só para quem enxerga o documento.
"""
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.balancos.tests.apoio import StorageDeMentira
from apps.cadastros.tests.apoio import criar_condominio, entrar_como
from apps.condominios.constants import Papel
from apps.core.storage import ErroDeStorage
from apps.governanca.models import ArquivoDocumento
from apps.governanca.tests.apoio import criar_documento, criar_tipo_documento

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


def arquivo_pdf(nome="laudo.pdf") -> SimpleUploadedFile:
    """Um arquivo que começa como um PDF de verdade."""
    return SimpleUploadedFile(nome, PDF, content_type="application/pdf")


def arquivo_png(nome="certificado.png") -> SimpleUploadedFile:
    """Um arquivo que começa como um PNG de verdade."""
    return SimpleUploadedFile(nome, PNG, content_type="image/png")


class BaseDeArquivos(TestCase):
    """Uma gestão com um documento, e um armazenamento de mentira no lugar."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)
        self.documento = criar_documento(self.condominio, self.gestao)
        self.provider = StorageDeMentira("bucket-documentos")

        patcher = patch(
            "apps.governanca.arquivos.obter_provider", return_value=self.provider
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _url(self, documento=None) -> str:
        return reverse(
            "governanca:anexar_arquivo", args=[(documento or self.documento).pk]
        )

    def _anexar(self, arquivo, documento=None, **extra):
        dados = {"condominio": str(self.condominio.pk), **extra}
        return self.client.post(self._url(documento), {"arquivo": arquivo, **dados})

    def _somente(self) -> ArquivoDocumento:
        return self.documento.arquivos.get()


class AnexoDeArquivoTests(BaseDeArquivos):
    """O arquivo entra preso ao documento, conferido pelo conteúdo."""

    def test_anexa_arquivo_valido(self):
        resposta = self._anexar(arquivo_pdf())

        self.assertEqual(resposta.status_code, 302)
        arquivo = self._somente()
        self.assertEqual(arquivo.nome_original, "laudo.pdf")
        self.assertEqual(arquivo.mime_type, "application/pdf")
        self.assertEqual(arquivo.tamanho_bytes, len(PDF))
        self.assertEqual(len(arquivo.sha256), 64)
        self.assertEqual(arquivo.enviado_por, self.gestao)
        self.assertEqual(arquivo.storage_provider, "mentira")
        self.assertEqual(arquivo.storage_bucket, "bucket-documentos")
        self.assertIn(arquivo.storage_key, self.provider.objetos)

    def test_arquivo_fica_preso_ao_documento(self):
        self._anexar(arquivo_pdf())

        arquivo = self._somente()
        self.assertEqual(arquivo.documento, self.documento)
        self.assertEqual(
            arquivo.storage_key,
            f"documentos/{self.condominio.pk}/{self.documento.pk}/{arquivo.pk}.pdf",
        )

    def test_dois_arquivos_no_mesmo_documento(self):
        self._anexar(arquivo_pdf())
        self._anexar(arquivo_png())

        self.assertEqual(self.documento.arquivos.count(), 2)
        self.assertEqual(
            sorted(tipo for tipo in self.documento.arquivos.values_list("mime_type", flat=True)),
            ["application/pdf", "image/png"],
        )

    def test_arquivo_repetido_no_mesmo_documento_e_recusado(self):
        self._anexar(arquivo_pdf())

        resposta = self._anexar(arquivo_pdf("outro-nome.pdf"))

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(self.documento.arquivos.count(), 1)

    def test_documento_de_outro_condominio_e_404(self):
        beta = criar_condominio("Beta")
        alheio = criar_documento(beta, self.gestao)

        resposta = self._anexar(arquivo_pdf(), documento=alheio)

        self.assertEqual(resposta.status_code, 404)
        self.assertFalse(ArquivoDocumento.objects.exists())

    def test_morador_nao_anexa(self):
        self.client.force_login(
            entrar_como(
                self.client, Papel.MORADOR, self.condominio, "morador@exemplo.test"
            )
        )

        resposta = self._anexar(arquivo_pdf())

        self.assertEqual(resposta.status_code, 404)
        self.assertFalse(ArquivoDocumento.objects.exists())

    def test_sem_login_nao_anexa(self):
        self.client.logout()

        resposta = self._anexar(arquivo_pdf())

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("/entrar/", resposta["Location"])
        self.assertFalse(ArquivoDocumento.objects.exists())

    def test_documento_sem_arquivo_continua_na_lista(self):
        resposta = self.client.get(reverse("governanca:documentos"))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, self.documento.titulo)

    def test_falha_de_armazenamento_nao_deixa_registro(self):
        self.provider.falhar_ao_enviar = True

        resposta = self._anexar(arquivo_pdf())

        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(ArquivoDocumento.objects.exists())


class ConferenciaDoArquivoTests(BaseDeArquivos):
    """O que decide se o arquivo entra: o conteúdo, não o nome nem o aviso."""

    def test_formato_invalido_e_recusado(self):
        resposta = self._anexar(
            SimpleUploadedFile("ata.pdf", b"isto nao e um pdf", content_type="application/pdf")
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(ArquivoDocumento.objects.exists())
        self.assertContains(resposta, "formato aceito", status_code=400)

    def test_nome_informado_diferente_do_conteudo_e_recusado(self):
        resposta = self._anexar(
            SimpleUploadedFile("laudo.jpg", PDF, content_type="image/jpeg")
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(ArquivoDocumento.objects.exists())

    def test_arquivo_vazio_e_recusado(self):
        resposta = self._anexar(
            SimpleUploadedFile("vazio.pdf", b"", content_type="application/pdf")
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(ArquivoDocumento.objects.exists())

    def test_arquivo_acima_do_limite_e_recusado(self):
        grande = SimpleUploadedFile(
            "grande.pdf", PDF + b"0" * 40, content_type="application/pdf"
        )

        with self.settings(BALANCOS_MAX_UPLOAD_BYTES=20):
            resposta = self._anexar(grande)

        self.assertEqual(resposta.status_code, 400)
        self.assertContains(resposta, "passa do limite", status_code=400)
        self.assertFalse(ArquivoDocumento.objects.exists())

    def test_nome_do_arquivo_nao_define_o_caminho(self):
        self._anexar(arquivo_pdf("../../etc/passwd.pdf"))

        arquivo = self._somente()
        self.assertNotIn("/", arquivo.nome_original)
        self.assertNotIn("\\", arquivo.nome_original)
        self.assertNotIn("etc", arquivo.storage_key)
        self.assertNotIn("passwd", arquivo.storage_key)


class ArquivosNaTelaTests(BaseDeArquivos):
    """O modal mostra o que a gestão precisa para reconhecer e abrir o arquivo."""

    def setUp(self):
        super().setUp()
        self._anexar(arquivo_pdf())

    def _abrir_arquivos(self, documento=None):
        alvo = documento or self.documento
        return self.client.get(
            reverse("governanca:documentos"),
            {
                "condominio": str(self.condominio.pk),
                "abrir": "arquivos",
                "item": str(alvo.pk),
            },
        )

    def test_modal_mostra_nome_tipo_tamanho_e_data(self):
        resposta = self._abrir_arquivos()

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "laudo.pdf")
        self.assertContains(resposta, "PDF")
        self.assertContains(resposta, "Enviado em")
        self.assertContains(resposta, "arquivos-documento")

    def test_acesso_e_gerado_sob_demanda_e_aponta_para_o_arquivo(self):
        self.assertEqual(self.provider.assinaturas, [])

        resposta = self._abrir_arquivos()

        arquivo = self._somente()
        self.assertEqual(self.provider.assinaturas, [(arquivo.storage_key, 60)])
        self.assertContains(resposta, arquivo.storage_key)

    def test_documento_sem_arquivo_abre_o_modal_vazio(self):
        tipo = criar_tipo_documento(self.condominio, nome="Ata")
        novo = criar_documento(
            self.condominio,
            self.gestao,
            titulo="Ata da assembleia",
            tipo_documento=tipo,
        )

        resposta = self._abrir_arquivos(novo)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "ainda não tem arquivos")

    def test_acesso_indisponivel_nao_derruba_a_tela(self):
        class SemAssinatura(StorageDeMentira):
            def url_assinada(self, *, key, validade_segundos):
                raise ErroDeStorage("Não foi possível gerar o acesso temporário.")

        with patch(
            "apps.governanca.arquivos.obter_provider",
            return_value=SemAssinatura("bucket-documentos"),
        ):
            resposta = self._abrir_arquivos()

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "laudo.pdf")
        self.assertContains(resposta, "Acesso indisponível agora")
