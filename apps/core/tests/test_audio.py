"""Testes do núcleo único de áudio, sem tocar a rede nem o banco (§28).

A conversa HTTP é substituída por uma resposta falsa, como em
``apps/balancos/tests/test_storage.py``. O que se verifica é o contrato: o que o
núcleo aceita, o que ele recusa cedo, o que ele envia, como trata cada falha — e
o compromisso que atravessa tudo: nenhum caminho levanta, nenhum caminho registra
áudio, texto ou credencial, e a transcrição não é reescrita.

O núcleo não conhece domínio nenhum, então não há banco aqui: o condomínio e o
usuário do pedido são objetos mínimos, suficientes para provar que o áudio só
passa com dono.
"""
import json
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.core import audio

CONDOMINIO = SimpleNamespace(pk=1)
USUARIO = SimpleNamespace(pk=1, is_authenticated=True)

CHAVE = "chave-de-teste-do-provedor"
MODELO = "modelo-de-transcricao"
BASE = "https://provedor.invalido/v1"

CONFIGURADO = {
    "EITHRA_LLM_API_KEY": CHAVE,
    "EITHRA_LLM_BASE_URL": BASE,
    "EITHRA_AUDIO_MODEL": MODELO,
}

# A fala do §16, sem vírgula de sistema: é esta a forma que precisa sobreviver.
SENTENCA = "O elevador de serviço voltou a parar no oitavo andar."


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


def corpo_json(dados) -> bytes:
    return json.dumps(dados).encode("utf-8")


def pedido(**campos) -> audio.PedidoDeAudio:
    base = {
        "conteudo": b"RIFF....WEBM",
        "nome_do_arquivo": "recado.webm",
        "content_type": "audio/webm",
        "condominio": CONDOMINIO,
        "usuario": USUARIO,
    }
    return audio.PedidoDeAudio(**{**base, **campos})


class DisponibilidadeTests(SimpleTestCase):
    """Sem serviço configurado não há envio — e isso não é erro."""

    @override_settings(EITHRA_LLM_API_KEY="", EITHRA_AUDIO_MODEL=MODELO)
    def test_sem_credencial_nao_esta_disponivel(self):
        self.assertFalse(audio.disponivel())

    @override_settings(EITHRA_LLM_API_KEY=CHAVE, EITHRA_AUDIO_MODEL="")
    def test_sem_modelo_de_transcricao_nao_esta_disponivel(self):
        self.assertFalse(audio.disponivel())

    @override_settings(**CONFIGURADO)
    def test_com_credencial_e_modelo_esta_disponivel(self):
        self.assertTrue(audio.disponivel())


class RecusaAntesDoServicoTests(SimpleTestCase):
    """O que não tem como dar certo é recusado aqui, sem chamar o provedor."""

    @patch("apps.core.audio.urllib.request.urlopen")
    def test_audio_sem_dono_nao_atravessa(self, urlopen):
        for campo in ("condominio", "usuario"):
            with self.subTest(campo=campo):
                resultado = audio.transcrever(pedido(**{campo: None}))
                self.assertFalse(resultado.obtida)
                self.assertEqual(resultado.erro, audio.MENSAGEM_SEM_PERMISSAO)
                self.assertEqual(resultado.falha, "sem_permissao")
        urlopen.assert_not_called()

    @patch("apps.core.audio.urllib.request.urlopen")
    def test_usuario_nao_autenticado_nao_atravessa(self, urlopen):
        anonimo = SimpleNamespace(pk=1, is_authenticated=False)
        resultado = audio.transcrever(pedido(usuario=anonimo))
        self.assertEqual(resultado.falha, "sem_permissao")
        urlopen.assert_not_called()

    @patch("apps.core.audio.urllib.request.urlopen")
    def test_audio_vazio_e_recusado(self, urlopen):
        resultado = audio.transcrever(pedido(conteudo=b""))
        self.assertEqual(resultado.erro, audio.MENSAGEM_VAZIO)
        self.assertEqual(resultado.falha, "vazio")
        urlopen.assert_not_called()

    @patch("apps.core.audio.urllib.request.urlopen")
    def test_formato_desconhecido_e_recusado(self, urlopen):
        resultado = audio.transcrever(
            pedido(content_type="application/pdf", nome_do_arquivo="documento.pdf")
        )
        self.assertEqual(resultado.erro, audio.MENSAGEM_FORMATO)
        self.assertEqual(resultado.falha, "formato")
        urlopen.assert_not_called()

    @override_settings(EITHRA_AUDIO_MAX_BYTES=10)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_audio_grande_demais_e_recusado(self, urlopen):
        resultado = audio.transcrever(pedido(conteudo=b"x" * 11))
        self.assertEqual(resultado.erro, audio.MENSAGEM_GRANDE)
        self.assertEqual(resultado.falha, "tamanho")
        urlopen.assert_not_called()

    @override_settings(EITHRA_LLM_API_KEY=CHAVE, EITHRA_AUDIO_MODEL="")
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_sem_servico_configurado_ha_fallback_sem_erro_500(self, urlopen):
        resultado = audio.transcrever(pedido())
        self.assertEqual(resultado.erro, audio.MENSAGEM_INDISPONIVEL)
        self.assertEqual(resultado.falha, "nao_configurado")
        self.assertFalse(resultado.obtida)
        urlopen.assert_not_called()


class FormatoTests(SimpleTestCase):
    """Reconhecer o formato pelo tipo declarado ou pela extensão — nunca pelos dois."""

    def test_tipo_do_navegador_com_codec_e_aceito(self):
        self.assertEqual(audio._formato("audio/webm;codecs=opus", "blob"), "webm")

    def test_extensao_salva_o_nome_sem_tipo(self):
        self.assertEqual(audio._formato("", "recado.m4a"), "m4a")

    def test_formato_que_ninguem_reconhece_nao_e_adivinhado(self):
        self.assertEqual(audio._formato("application/octet-stream", "arquivo.bin"), "")


class TranscricaoValidaTests(SimpleTestCase):
    """O caminho feliz: o texto chega fiel e o que se sabe sobre ele vem junto."""

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_devolve_o_texto_com_idioma_e_duracao(self, urlopen):
        urlopen.return_value = RespostaFalsa(
            corpo_json({"text": SENTENCA, "language": "portuguese", "duration": 4.2})
        )

        resultado = audio.transcrever(pedido())

        self.assertTrue(resultado.obtida)
        self.assertEqual(resultado.texto, SENTENCA)
        self.assertEqual(resultado.idioma, "portuguese")
        self.assertEqual(resultado.duracao, 4.2)
        self.assertEqual(resultado.erro, "")

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_a_transcricao_nao_e_reescrita(self, urlopen):
        """Fidelidade (§5): o núcleo não resume nem troca a palavra por outra."""
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))

        resultado = audio.transcrever(pedido())

        self.assertEqual(resultado.texto, SENTENCA)
        self.assertNotIn("recorrente", resultado.texto)
        self.assertNotIn("8º", resultado.texto)


class EnvioAoServicoTests(SimpleTestCase):
    """Como o áudio sai daqui — endereço, autenticação, formulário e limite de tempo."""

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_envia_por_post_autenticado_no_endereco_da_transcricao(self, urlopen):
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))

        audio.transcrever(pedido())

        requisicao = urlopen.call_args.args[0]
        self.assertEqual(requisicao.get_method(), "POST")
        self.assertTrue(requisicao.full_url.endswith("/audio/transcriptions"))
        self.assertEqual(requisicao.headers["Authorization"], f"Bearer {CHAVE}")

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_o_formulario_leva_modelo_formato_e_idioma(self, urlopen):
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))

        audio.transcrever(pedido())

        requisicao = urlopen.call_args.args[0]
        corpo = requisicao.data
        self.assertIn(f'name="model"'.encode(), corpo)
        self.assertIn(MODELO.encode(), corpo)
        self.assertIn(b"verbose_json", corpo)
        self.assertIn(b'name="file"; filename="recado.webm"', corpo)

    @override_settings(**CONFIGURADO, EITHRA_AUDIO_TIMEOUT=12.5)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_a_conversa_tem_tempo_maximo_de_espera(self, urlopen):
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))

        audio.transcrever(pedido())

        self.assertEqual(urlopen.call_args.kwargs["timeout"], 12.5)


class FalhaControladaTests(SimpleTestCase):
    """Todo caminho de falha termina em resultado — nunca em exceção (§13)."""

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_tempo_esgotado_vira_erro_humano(self, urlopen):
        urlopen.side_effect = TimeoutError("tempo esgotado")

        resultado = audio.transcrever(pedido())

        self.assertFalse(resultado.obtida)
        self.assertEqual(resultado.erro, audio.MENSAGEM_FALHA)
        self.assertEqual(resultado.falha, "TimeoutError")

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_servico_fora_do_ar_vira_erro_humano(self, urlopen):
        urlopen.side_effect = urllib.error.URLError("sem rede")

        resultado = audio.transcrever(pedido())

        self.assertEqual(resultado.erro, audio.MENSAGEM_FALHA)
        self.assertEqual(resultado.falha, "URLError")

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_resposta_de_erro_do_servico_vira_erro_humano(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError(
            f"{BASE}/audio/transcriptions", 400, "ruim", {}, None
        )

        resultado = audio.transcrever(pedido())

        self.assertEqual(resultado.erro, audio.MENSAGEM_FALHA)
        self.assertEqual(resultado.falha, "http_400")

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_resposta_ilegivel_vira_erro_humano(self, urlopen):
        urlopen.return_value = RespostaFalsa(b"isto nao e json")

        resultado = audio.transcrever(pedido())

        self.assertEqual(resultado.erro, audio.MENSAGEM_FALHA)
        self.assertNotEqual(resultado.falha, "")
        self.assertNotIn("isto nao e json", resultado.erro)

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_resposta_fora_do_formato_e_recusada(self, urlopen):
        urlopen.return_value = RespostaFalsa(corpo_json(["nao", "e", "objeto"]))

        resultado = audio.transcrever(pedido())

        self.assertEqual(resultado.erro, audio.MENSAGEM_FALHA)
        self.assertEqual(resultado.falha, "resposta")

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_resposta_sem_fala_pede_para_gravar_de_novo(self, urlopen):
        urlopen.return_value = RespostaFalsa(corpo_json({"text": "   "}))

        resultado = audio.transcrever(pedido())

        self.assertEqual(resultado.erro, audio.MENSAGEM_SEM_FALA)
        self.assertEqual(resultado.falha, "sem_fala")


class PrivacidadeTests(SimpleTestCase):
    """O que se registra é o fato técnico — nunca o áudio, o texto ou a chave (§35)."""

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_o_log_de_sucesso_nao_carrega_texto_nem_credencial(self, urlopen):
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))

        with self.assertLogs("apps.core.audio", level="INFO") as capturado:
            audio.transcrever(pedido())

        registro = "\n".join(capturado.output)
        self.assertNotIn(SENTENCA, registro)
        self.assertNotIn(CHAVE, registro)
        self.assertIn(MODELO, registro)
