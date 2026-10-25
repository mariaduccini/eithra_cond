"""Testes do provedor de armazenamento, sem tocar a rede.

A conversa HTTP é substituída por uma resposta falsa. O que se verifica é o
contrato: o que o provedor envia, como trata conflito e falha, e como monta um
acesso temporário.
"""
import json
import urllib.error
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from apps.balancos.storage import (
    TEMPO_LIMITE_SEGUNDOS,
    ErroDeStorage,
    SupabaseStorageProvider,
    obter_provider,
)

ENDERECO = "https://projeto.invalido"
CHAVE = "chave-de-servico-de-teste"
BUCKET = "eithra-v3-balancos-dev"


class RespostaFalsa:
    """Resposta mínima, usada como gerenciador de contexto."""

    def __init__(self, corpo=b"{}"):
        self._corpo = corpo

    def read(self):
        return self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def conflito(codigo=409):
    """Erro HTTP, como o ``urlopen`` levanta."""
    return urllib.error.HTTPError(
        f"{ENDERECO}/storage/v1/object/{BUCKET}/x", codigo, "erro", {}, None
    )


def cabecalhos(requisicao):
    """Cabeçalhos em caixa baixa: o ``urllib`` reescreve a caixa original."""
    return {nome.lower(): valor for nome, valor in requisicao.headers.items()}


def criar_provider():
    return SupabaseStorageProvider(url=ENDERECO, service_role_key=CHAVE, bucket=BUCKET)


class EnvioTests(TestCase):
    """Guardar o arquivo no bucket privado."""

    def setUp(self):
        self.provider = criar_provider()

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_envia_por_post_autenticado(self, urlopen):
        urlopen.return_value = RespostaFalsa()
        self.provider.enviar(key="balancos/a/b/documento.pdf", conteudo=b"%PDF-", content_type="application/pdf")

        requisicao = urlopen.call_args.args[0]
        self.assertEqual(requisicao.get_method(), "POST")
        self.assertEqual(requisicao.data, b"%PDF-")
        self.assertIn(f"/object/{BUCKET}/balancos/a/b/documento.pdf", requisicao.full_url)
        self.assertEqual(cabecalhos(requisicao)["authorization"], f"Bearer {CHAVE}")

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_conflito_reenvia_substituindo(self, urlopen):
        """O mesmo envio repetido não cria um segundo objeto."""
        urlopen.side_effect = [conflito(409), RespostaFalsa()]
        self.provider.enviar(key="k/documento.pdf", conteudo=b"%PDF-", content_type="application/pdf")

        segunda = urlopen.call_args.args[0]
        self.assertEqual(segunda.get_method(), "PUT")
        self.assertEqual(cabecalhos(segunda)["x-upsert"], "true")

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_falha_de_rede_vira_erro_de_storage_sem_vazar_a_chave(self, urlopen):
        urlopen.side_effect = urllib.error.URLError("sem rede")
        with self.assertRaises(ErroDeStorage) as capturado:
            self.provider.enviar(key="k/documento.pdf", conteudo=b"%PDF-", content_type="application/pdf")

        self.assertNotIn(CHAVE, str(capturado.exception))

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_erro_do_provedor_vira_erro_de_storage(self, urlopen):
        urlopen.side_effect = conflito(500)
        with self.assertRaises(ErroDeStorage):
            self.provider.enviar(key="k/documento.pdf", conteudo=b"%PDF-", content_type="application/pdf")


class TempoLimiteTests(TestCase):
    """Nenhuma conversa com o provedor fica sem tempo máximo de espera."""

    def setUp(self):
        self.provider = criar_provider()

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_envio_tem_tempo_limite(self, urlopen):
        urlopen.return_value = RespostaFalsa()
        self.provider.enviar(
            key="k/documento.pdf", conteudo=b"%PDF-", content_type="application/pdf"
        )
        self.assertEqual(urlopen.call_args.kwargs["timeout"], TEMPO_LIMITE_SEGUNDOS)

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_reenvio_substituindo_tambem_tem_tempo_limite(self, urlopen):
        urlopen.side_effect = [conflito(409), RespostaFalsa()]
        self.provider.enviar(
            key="k/documento.pdf", conteudo=b"%PDF-", content_type="application/pdf"
        )
        self.assertEqual(urlopen.call_args.kwargs["timeout"], TEMPO_LIMITE_SEGUNDOS)

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_remocao_tem_tempo_limite(self, urlopen):
        urlopen.return_value = RespostaFalsa()
        self.provider.remover(key="k/documento.pdf")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], TEMPO_LIMITE_SEGUNDOS)

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_acesso_temporario_tem_tempo_limite(self, urlopen):
        urlopen.return_value = RespostaFalsa(
            json.dumps({"signedURL": "/object/sign/b/d.pdf"}).encode()
        )
        self.provider.url_assinada(key="k/documento.pdf", validade_segundos=60)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], TEMPO_LIMITE_SEGUNDOS)

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_tempo_esgotado_vira_erro_de_storage_sem_vazar_a_chave(self, urlopen):
        """Esperar demais é falha explícita, e a resposta continua limpa."""
        urlopen.side_effect = TimeoutError("tempo esgotado")
        with self.assertRaises(ErroDeStorage) as capturado:
            self.provider.enviar(
                key="k/documento.pdf", conteudo=b"%PDF-", content_type="application/pdf"
            )
        self.assertNotIn(CHAVE, str(capturado.exception))

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_conexao_recusada_vira_erro_de_storage(self, urlopen):
        urlopen.side_effect = ConnectionRefusedError("conexão recusada")
        with self.assertRaises(ErroDeStorage):
            self.provider.remover(key="k/documento.pdf")

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_resposta_ilegivel_e_reportada_sem_o_corpo_do_provedor(self, urlopen):
        """O que o provedor devolveu não é repassado: só o fato de não servir."""
        urlopen.return_value = RespostaFalsa(b"nao e json")
        with self.assertRaises(ErroDeStorage) as capturado:
            self.provider.url_assinada(key="k/documento.pdf", validade_segundos=60)
        self.assertNotIn("nao e json", str(capturado.exception))

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_mensagem_de_erro_nunca_carrega_credencial_nem_cabecalho(self, urlopen):
        for falha in (conflito(500), urllib.error.URLError("sem rede"), TimeoutError()):
            with self.subTest(falha=type(falha).__name__):
                urlopen.side_effect = falha
                with self.assertRaises(ErroDeStorage) as capturado:
                    self.provider.enviar(
                        key="k/documento.pdf",
                        conteudo=b"%PDF-",
                        content_type="application/pdf",
                    )
                mensagem = str(capturado.exception)
                self.assertNotIn(CHAVE, mensagem)
                self.assertNotIn("Bearer", mensagem)
                self.assertNotIn("Authorization", mensagem)
                self.assertNotIn("apikey", mensagem)


class RemocaoTests(TestCase):
    """Remover objeto ausente não é falha."""

    def setUp(self):
        self.provider = criar_provider()

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_remove_por_delete(self, urlopen):
        urlopen.return_value = RespostaFalsa()
        self.provider.remover(key="k/documento.pdf")

        requisicao = urlopen.call_args.args[0]
        self.assertEqual(requisicao.get_method(), "DELETE")

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_objeto_ausente_e_tolerado(self, urlopen):
        urlopen.side_effect = conflito(404)
        self.provider.remover(key="k/documento.pdf")

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_outra_falha_e_reportada(self, urlopen):
        urlopen.side_effect = conflito(500)
        with self.assertRaises(ErroDeStorage):
            self.provider.remover(key="k/documento.pdf")


class AcessoAssinadoTests(TestCase):
    """Acesso curto, gerado na hora."""

    def setUp(self):
        self.provider = criar_provider()

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_monta_endereco_absoluto_a_partir_do_caminho_relativo(self, urlopen):
        urlopen.return_value = RespostaFalsa(
            json.dumps({"signedURL": "/object/sign/b/documento.pdf?token=abc"}).encode()
        )
        endereco = self.provider.url_assinada(key="k/documento.pdf", validade_segundos=60)
        self.assertTrue(endereco.startswith(ENDERECO))
        self.assertIn("token=abc", endereco)

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_preserva_endereco_absoluto_do_provedor(self, urlopen):
        absoluto = "https://outro.invalido/documento.pdf?token=abc"
        urlopen.return_value = RespostaFalsa(
            json.dumps({"signedUrl": absoluto}).encode()
        )
        self.assertEqual(
            self.provider.url_assinada(key="k/documento.pdf", validade_segundos=60),
            absoluto,
        )

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_resposta_sem_endereco_e_recusada(self, urlopen):
        urlopen.return_value = RespostaFalsa(json.dumps({}).encode())
        with self.assertRaises(ErroDeStorage):
            self.provider.url_assinada(key="k/documento.pdf", validade_segundos=60)

    @patch("apps.balancos.storage.urllib.request.urlopen")
    def test_validade_pedida_ao_provedor(self, urlopen):
        urlopen.return_value = RespostaFalsa(
            json.dumps({"signedURL": "/object/sign/b/d.pdf"}).encode()
        )
        self.provider.url_assinada(key="k/documento.pdf", validade_segundos=45)
        requisicao = urlopen.call_args.args[0]
        self.assertEqual(json.loads(requisicao.data), {"expiresIn": 45})


class EscolhaDoProviderTests(TestCase):
    """Escolhido o remoto, o que falta é recusado — não desviado para o disco."""

    @override_settings(
        EITHRA_STORAGE_PROVIDER="supabase",
        BALANCOS_STORAGE_PROVIDER="supabase",
        SUPABASE_URL="",
        SUPABASE_SERVICE_ROLE_KEY="",
        BALANCOS_STORAGE_BUCKET="",
    )
    def test_aponta_exatamente_as_variaveis_que_faltam(self):
        with self.assertRaises(ImproperlyConfigured) as capturado:
            obter_provider()

        mensagem = str(capturado.exception)
        self.assertIn("SUPABASE_URL", mensagem)
        self.assertIn("SUPABASE_SERVICE_ROLE_KEY", mensagem)
        self.assertIn("BALANCOS_STORAGE_BUCKET", mensagem)

    @override_settings(
        EITHRA_STORAGE_PROVIDER="supabase",
        BALANCOS_STORAGE_PROVIDER="supabase",
        SUPABASE_URL=ENDERECO,
        SUPABASE_SERVICE_ROLE_KEY="",
        BALANCOS_STORAGE_BUCKET=BUCKET,
    )
    def test_lista_apenas_o_que_falta(self):
        with self.assertRaises(ImproperlyConfigured) as capturado:
            obter_provider()
        mensagem = str(capturado.exception)
        self.assertIn("SUPABASE_SERVICE_ROLE_KEY", mensagem)
        self.assertNotIn("SUPABASE_URL,", mensagem)

    @override_settings(
        EITHRA_STORAGE_PROVIDER="disco-local",
        BALANCOS_STORAGE_PROVIDER="disco-local",
        SUPABASE_URL=ENDERECO,
        SUPABASE_SERVICE_ROLE_KEY=CHAVE,
        BALANCOS_STORAGE_BUCKET=BUCKET,
    )
    def test_recusa_provedor_desconhecido(self):
        with self.assertRaises(ImproperlyConfigured):
            obter_provider()

    @override_settings(
        EITHRA_STORAGE_PROVIDER="supabase",
        BALANCOS_STORAGE_PROVIDER="supabase",
        SUPABASE_URL=ENDERECO,
        SUPABASE_SERVICE_ROLE_KEY=CHAVE,
        BALANCOS_STORAGE_BUCKET=BUCKET,
    )
    def test_monta_o_provider_quando_tudo_existe(self):
        provider = obter_provider()
        self.assertIsInstance(provider, SupabaseStorageProvider)
        self.assertEqual(provider.bucket, BUCKET)
        self.assertEqual(provider.nome, "supabase")
