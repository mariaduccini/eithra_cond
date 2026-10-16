"""Testes do recebimento do arquivo: validação, hash, envio e desfechos.

Todo o tráfego de storage passa por um provedor de mentira. Nenhum teste
unitário conversa com o Supabase real.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.balancos.constants import StatusImportacao
from apps.balancos.models import BalancoMensal, ImportacaoBalanco
from apps.balancos.services import (
    ErroDeValidacaoDeArquivo,
    calcular_hash,
    descartar_importacao,
    formatar_tamanho,
    gerar_acesso_temporario,
    montar_chave,
    receber_importacao,
    sanitizar_nome,
    validar_arquivo,
)
from apps.balancos.storage import ErroDeStorage
from apps.balancos.tests.apoio import (
    CONTEUDO_PDF,
    HASH_PDF,
    StorageDeMentira,
    criar_pessoa,
    pdf,
)
from apps.condominios.models import Condominio


class ValidacaoDeArquivoTests(TestCase):
    """Um arquivo que não passa daqui não vira nem registro."""

    def test_aceita_pdf_de_verdade(self):
        validar_arquivo(pdf())

    def test_recusa_arquivo_ausente(self):
        with self.assertRaises(ErroDeValidacaoDeArquivo):
            validar_arquivo(None)

    def test_recusa_extensao_diferente(self):
        with self.assertRaises(ErroDeValidacaoDeArquivo):
            validar_arquivo(pdf(nome="balanco.docx"))

    def test_recusa_arquivo_sem_extensao(self):
        with self.assertRaises(ErroDeValidacaoDeArquivo):
            validar_arquivo(pdf(nome="balanco"))

    def test_recusa_mime_incompativel(self):
        """O que o navegador informa não é prova, mas incoerência é recusada."""
        with self.assertRaises(ErroDeValidacaoDeArquivo):
            validar_arquivo(pdf(content_type="image/png"))

    def test_recusa_magic_bytes_invalidos(self):
        """Renomear um arquivo qualquer para .pdf não o torna um PDF."""
        with self.assertRaises(ErroDeValidacaoDeArquivo):
            validar_arquivo(pdf(conteudo=b"nao sou um pdf"))

    def test_recusa_arquivo_vazio(self):
        with self.assertRaises(ErroDeValidacaoDeArquivo):
            validar_arquivo(pdf(conteudo=b""))

    @override_settings(BALANCOS_MAX_UPLOAD_BYTES=64)
    def test_respeita_tamanho_maximo(self):
        grande = b"%PDF-1.4\n" + b"x" * 200
        with self.assertRaises(ErroDeValidacaoDeArquivo):
            validar_arquivo(pdf(conteudo=grande))

    def test_aviso_de_tamanho_em_linguagem_comum(self):
        with self.assertRaises(ErroDeValidacaoDeArquivo) as capturado:
            with override_settings(BALANCOS_MAX_UPLOAD_BYTES=64):
                validar_arquivo(pdf(conteudo=b"%PDF-1.4\n" + b"x" * 200))
        self.assertIn("64 bytes", str(capturado.exception))

    def test_nome_original_perde_caminho_e_controle(self):
        nome = sanitizar_nome("C:\\Users\\alguem\\Balanço\x00.pdf")
        self.assertEqual(nome, "Balanço.pdf")

    def test_nome_vazio_recebe_um_padrao(self):
        self.assertEqual(sanitizar_nome("   "), "Documento.pdf")

    def test_tamanho_escrito_em_unidades_legiveis(self):
        self.assertEqual(formatar_tamanho(512), "512 bytes")
        self.assertEqual(formatar_tamanho(2048), "2 KB")
        self.assertEqual(formatar_tamanho(52428800), "50 MB")


class RecebimentoTests(TestCase):
    """Do arquivo recebido ao registro transitório, sem virar balanço."""

    def setUp(self):
        self.usuario = criar_pessoa("sindica@exemplo.com.br", "Síndica Teste")
        self.condominio_a = Condominio.objects.create(nome="Condomínio A")
        self.condominio_b = Condominio.objects.create(nome="Condomínio B")
        self.provider = StorageDeMentira()
        self.obter_provider = patch(
            "apps.balancos.services.obter_provider", return_value=self.provider
        )
        self.obter_provider.start()
        self.addCleanup(self.obter_provider.stop)

    def receber(self, condominio=None, arquivo=None):
        return receber_importacao(
            condominio=condominio or self.condominio_a,
            usuario=self.usuario,
            arquivo=arquivo or pdf(),
        )

    def test_registra_a_importacao_como_pronta_para_a_proxima_etapa(self):
        resultado = self.receber()
        self.assertFalse(resultado.duplicada)
        self.assertIsNotNone(resultado.importacao.pk)
        self.assertEqual(resultado.importacao.status, StatusImportacao.PRONTO)

    def test_guarda_o_hash_do_arquivo(self):
        resultado = self.receber()
        self.assertEqual(resultado.importacao.sha256, HASH_PDF)
        self.assertEqual(resultado.importacao.sha256, calcular_hash(CONTEUDO_PDF))

    def test_guarda_os_metadados_do_arquivo(self):
        importacao = self.receber().importacao
        self.assertEqual(importacao.nome_original, "balanco-do-mes.pdf")
        self.assertEqual(importacao.mime_type, "application/pdf")
        self.assertEqual(importacao.tamanho_bytes, len(CONTEUDO_PDF))
        self.assertEqual(importacao.condominio, self.condominio_a)
        self.assertEqual(importacao.criado_por, self.usuario)

    def test_guarda_a_referencia_do_objeto_sem_url(self):
        importacao = self.receber().importacao
        self.assertEqual(importacao.storage_provider, self.provider.nome)
        self.assertEqual(importacao.storage_bucket, self.provider.bucket)
        self.assertFalse(importacao.storage_key.startswith(("http://", "https://")))

    def test_chave_do_objeto_nao_carrega_dado_sensivel(self):
        """Nem nome de arquivo, nem nome de condomínio, nem competência."""
        importacao = self.receber().importacao
        esperada = montar_chave(condominio=self.condominio_a, importacao=importacao)
        self.assertEqual(importacao.storage_key, esperada)
        self.assertNotIn("balanco-do-mes", importacao.storage_key)
        self.assertNotIn(str(self.condominio_a.nome), importacao.storage_key)

    def test_envia_o_conteudo_ao_storage_privado(self):
        importacao = self.receber().importacao
        self.assertEqual(self.provider.objetos[importacao.storage_key], CONTEUDO_PDF)

    def test_nao_cria_balanco_mensal(self):
        """O mês só passa a valer com a confirmação da gestão, em fase futura."""
        self.receber()
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_arquivo_invalido_nao_vira_registro(self):
        with self.assertRaises(ErroDeValidacaoDeArquivo):
            self.receber(arquivo=pdf(conteudo=b"nao sou um pdf"))
        self.assertEqual(ImportacaoBalanco.objects.count(), 0)
        self.assertEqual(self.provider.objetos, {})

    def test_repeticao_do_mesmo_arquivo_e_sinalizada_sem_duplicar(self):
        primeira = self.receber().importacao
        segunda = self.receber()

        self.assertTrue(segunda.duplicada)
        self.assertEqual(segunda.importacao.pk, primeira.pk)
        self.assertEqual(ImportacaoBalanco.objects.count(), 1)
        self.assertEqual(len(self.provider.objetos), 1)

    def test_mesmo_arquivo_em_outro_condominio_e_outro_documento(self):
        primeira = self.receber(condominio=self.condominio_a).importacao
        segunda = self.receber(condominio=self.condominio_b)

        self.assertFalse(segunda.duplicada)
        self.assertNotEqual(segunda.importacao.pk, primeira.pk)
        self.assertEqual(ImportacaoBalanco.objects.count(), 2)

    def test_importacao_descartada_nao_conta_como_repeticao(self):
        primeira = self.receber().importacao
        primeira.status = StatusImportacao.DESCARTADO
        primeira.save(update_fields=["status"])

        segunda = self.receber()
        self.assertFalse(segunda.duplicada)
        self.assertNotEqual(segunda.importacao.pk, primeira.pk)

    def test_importacao_com_falha_nao_conta_como_repeticao(self):
        """Quem não conseguiu enviar vai querer tentar de novo."""
        primeira = self.receber().importacao
        primeira.status = StatusImportacao.ERRO
        primeira.save(update_fields=["status"])

        segunda = self.receber()
        self.assertFalse(segunda.duplicada)

    def test_falha_de_storage_nao_vira_sucesso(self):
        self.provider.falhar_ao_enviar = True
        resultado = self.receber()

        self.assertEqual(resultado.importacao.status, StatusImportacao.ERRO)
        self.assertTrue(resultado.importacao.erro_processamento)
        self.assertEqual(self.provider.objetos, {})
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_erro_guardado_nao_vaza_segredo(self):
        self.provider.falhar_ao_enviar = True
        erro = self.receber().importacao.erro_processamento

        self.assertNotIn("Bearer", erro)
        self.assertNotIn("service_role", erro)
        self.assertNotIn("https://", erro)


class AcessoTemporarioTests(TestCase):
    """Ver o arquivo exige autorização, e o acesso é curto."""

    def setUp(self):
        self.usuario = criar_pessoa("sindica@exemplo.com.br", "Síndica Teste")
        self.condominio = Condominio.objects.create(nome="Condomínio A")
        self.provider = StorageDeMentira()
        self.obter_provider = patch(
            "apps.balancos.services.obter_provider", return_value=self.provider
        )
        self.obter_provider.start()
        self.addCleanup(self.obter_provider.stop)
        self.importacao = receber_importacao(
            condominio=self.condominio, usuario=self.usuario, arquivo=pdf()
        ).importacao

    @override_settings(BALANCOS_URL_ACESSO_SEGUNDOS=90)
    def test_acesso_e_gerado_na_hora_com_validade_curta(self):
        endereco = gerar_acesso_temporario(self.importacao)
        self.assertEqual(self.provider.assinaturas, [(self.importacao.storage_key, 90)])
        self.assertIn(self.importacao.storage_key, endereco)

    def test_acesso_nao_fica_guardado_no_registro(self):
        gerar_acesso_temporario(self.importacao)
        self.importacao.refresh_from_db()
        self.assertFalse(
            self.importacao.storage_key.startswith(("http://", "https://"))
        )

    def test_arquivo_indisponivel_e_recusado(self):
        self.importacao.storage_key = ""
        with self.assertRaises(ErroDeStorage):
            gerar_acesso_temporario(self.importacao)


class DescarteTests(TestCase):
    """Descartar preserva a memória do recebimento e libera o arquivo."""

    def setUp(self):
        self.usuario = criar_pessoa("sindica@exemplo.com.br", "Síndica Teste")
        self.condominio = Condominio.objects.create(nome="Condomínio A")
        self.provider = StorageDeMentira()
        self.obter_provider = patch(
            "apps.balancos.services.obter_provider", return_value=self.provider
        )
        self.obter_provider.start()
        self.addCleanup(self.obter_provider.stop)
        self.importacao = receber_importacao(
            condominio=self.condominio, usuario=self.usuario, arquivo=pdf()
        ).importacao

    def test_descarte_marca_a_importacao_e_libera_o_arquivo(self):
        chave = self.importacao.storage_key
        descartar_importacao(self.importacao)

        self.importacao.refresh_from_db()
        self.assertEqual(self.importacao.status, StatusImportacao.DESCARTADO)
        self.assertIsNotNone(self.importacao.descartado_em)
        self.assertIn(chave, self.provider.removidos)
        self.assertEqual(self.provider.objetos, {})
        self.assertTrue(ImportacaoBalanco.objects.filter(pk=self.importacao.pk).exists())

    def test_importacao_confirmada_nao_e_descartada(self):
        from django.utils import timezone

        self.importacao.confirmado_em = timezone.now()
        self.importacao.save(update_fields=["confirmado_em"])

        with self.assertRaises(ErroDeStorage):
            descartar_importacao(self.importacao)

    def test_falha_ao_liberar_o_arquivo_nao_desfaz_o_descarte(self):
        def falhar(*, key):
            raise ErroDeStorage("Não foi possível falar com o armazenamento.")

        self.provider.remover = falhar
        descartar_importacao(self.importacao)

        self.importacao.refresh_from_db()
        self.assertEqual(self.importacao.status, StatusImportacao.DESCARTADO)
