"""Testes do adaptador de leitura semântica, sem tocar a rede.

A conversa HTTP é substituída por uma resposta falsa, como em
``apps/core/tests/test_gmail.py``. O que se verifica é o contrato de transporte:
o que sai no payload, o que o adaptador devolve quando a resposta é boa e o que
ele faz quando a resposta é ruim — e o compromisso que atravessa tudo: nenhuma
credencial entra no log e nenhuma exceção bruta da rede chega a quem chamou.

O adaptador não conhece domínio nenhum, então não há banco aqui: ele monta a
chamada, devolve o objeto JSON e desaparece.
"""
import io
import json
import urllib.error
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.core import llm

CHAVE = "chave-de-teste-do-provedor"
MODELO = "modelo-de-teste"
BASE = "https://provedor.invalido/v1"

CONFIGURADO = {
    "EITHRA_LLM_API_KEY": CHAVE,
    "EITHRA_LLM_BASE_URL": BASE,
    "EITHRA_LLM_MODEL": MODELO,
}

SISTEMA = "Responda apenas com JSON."
PEDIDO = "O elevador social parou de funcionar."
OBJETO = {"consequencia": "nova_ocorrencia"}
CONTEUDO = json.dumps(OBJETO)


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------


class RespostaFalsa:
    """Resposta mínima, usada como gerenciador de contexto."""

    def __init__(self, corpo=b"{}"):
        if isinstance(corpo, bytes):
            self._corpo = corpo
        elif isinstance(corpo, str):
            self._corpo = corpo.encode("utf-8")
        else:
            self._corpo = json.dumps(corpo).encode("utf-8")

    def read(self):
        return self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def resposta(*, conteudo=CONTEUDO):
    """A carga que a API devolve quando a leitura deu certo."""
    return {"choices": [{"message": {"content": conteudo}}]}


def http_error(codigo: int, *, corpo: bytes | None = None):
    """Um HTTPError como o ``urlopen`` levanta, com corpo opcional."""
    return urllib.error.HTTPError(
        f"{BASE}/chat/completions",
        codigo,
        "erro",
        {},
        io.BytesIO(corpo) if corpo is not None else None,
    )


def corpo_enviado(urlopen) -> dict:
    """O objeto JSON que o adaptador mandou — sem credencial, só o payload."""
    requisicao = urlopen.call_args.args[0]
    return json.loads(requisicao.data.decode("utf-8"))


def requisicao_enviada(urlopen):
    return urlopen.call_args.args[0]


# ---------------------------------------------------------------------------
# Disponibilidade
# ---------------------------------------------------------------------------


class DisponibilidadeTests(SimpleTestCase):
    """Sem serviço configurado não há chamada — e isso não é erro."""

    @override_settings(EITHRA_LLM_API_KEY="", EITHRA_LLM_MODEL=MODELO)
    def test_sem_credencial_nao_esta_disponivel(self):
        self.assertFalse(llm.disponivel())

    @override_settings(EITHRA_LLM_API_KEY=CHAVE, EITHRA_LLM_MODEL="")
    def test_sem_modelo_nao_esta_disponivel(self):
        self.assertFalse(llm.disponivel())

    @override_settings(**CONFIGURADO)
    def test_com_credencial_e_modelo_esta_disponivel(self):
        self.assertTrue(llm.disponivel())

    @override_settings(EITHRA_LLM_API_KEY="", EITHRA_LLM_MODEL="")
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_sem_servico_nao_toca_a_rede(self, urlopen):
        self.assertIsNone(llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO))
        urlopen.assert_not_called()


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


class PayloadTests(SimpleTestCase):
    """O que sai no pedido — e o que deliberadamente não sai."""

    @override_settings(**CONFIGURADO)
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_o_payload_padrao_nao_envia_temperature(self, urlopen):
        """Modelos que recusam o parâmetro recusariam a leitura inteira."""
        urlopen.return_value = RespostaFalsa(resposta())

        llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO)

        self.assertNotIn("temperature", corpo_enviado(urlopen))

    @override_settings(**CONFIGURADO)
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_o_payload_leva_o_modelo_configurado(self, urlopen):
        """O modelo continua sendo o do ambiente, e não um valor do código."""
        urlopen.return_value = RespostaFalsa(resposta())

        llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO)

        self.assertEqual(corpo_enviado(urlopen)["model"], MODELO)

    @override_settings(**CONFIGURADO)
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_o_payload_leva_as_duas_mensagens(self, urlopen):
        urlopen.return_value = RespostaFalsa(resposta())

        llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO)

        self.assertEqual(
            corpo_enviado(urlopen)["messages"],
            [
                {"role": "system", "content": SISTEMA},
                {"role": "user", "content": PEDIDO},
            ],
        )

    @override_settings(**CONFIGURADO)
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_a_chamada_e_a_de_chat_completions(self, urlopen):
        """O contrato de endereço não muda com a retirada do parâmetro."""
        urlopen.return_value = RespostaFalsa(resposta())

        llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO)

        self.assertEqual(
            requisicao_enviada(urlopen).full_url, f"{BASE}/chat/completions"
        )

    @override_settings(**CONFIGURADO)
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_a_credencial_vai_no_cabecalho(self, urlopen):
        """A credencial é transporte: sai no cabeçalho e em nenhum outro lugar."""
        urlopen.return_value = RespostaFalsa(resposta())

        llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO)

        requisicao = requisicao_enviada(urlopen)
        self.assertEqual(requisicao.get_header("Authorization"), f"Bearer {CHAVE}")
        self.assertNotIn(CHAVE, str(corpo_enviado(urlopen)))


# ---------------------------------------------------------------------------
# Resposta
# ---------------------------------------------------------------------------


class RespostaTests(SimpleTestCase):
    """A resposta boa continua virando o objeto JSON, ou nada."""

    @override_settings(**CONFIGURADO)
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_resposta_valida_devolve_o_objeto(self, urlopen):
        urlopen.return_value = RespostaFalsa(resposta())

        self.assertEqual(llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO), OBJETO)

    @override_settings(**CONFIGURADO)
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_json_embrulhado_em_texto_ainda_e_lido(self, urlopen):
        """Perder uma leitura boa por formatação seria perder contexto."""
        urlopen.return_value = RespostaFalsa(
            resposta(conteudo=f"Claro: {CONTEUDO} — pronto.")
        )

        self.assertEqual(llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO), OBJETO)

    @override_settings(**CONFIGURADO)
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_resposta_fora_do_formato_vira_none(self, urlopen):
        urlopen.return_value = RespostaFalsa({"escolhas": []})

        with self.assertLogs("apps.core.llm", level="WARNING"):
            self.assertIsNone(llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO))

    @override_settings(**CONFIGURADO)
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_conteudo_sem_json_vira_none(self, urlopen):
        urlopen.return_value = RespostaFalsa(resposta(conteudo="Sem objeto aqui."))

        with self.assertLogs("apps.core.llm", level="WARNING"):
            self.assertIsNone(llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO))


# ---------------------------------------------------------------------------
# Falhas
# ---------------------------------------------------------------------------


class FalhasTests(SimpleTestCase):
    """Nenhum caminho levanta: a leitura a menos não pode derrubar o registro."""

    @override_settings(**CONFIGURADO)
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_http_de_erro_vira_none(self, urlopen):
        urlopen.side_effect = http_error(
            400, corpo=b'{"error": {"param": "temperature"}}'
        )

        with self.assertLogs("apps.core.llm", level="WARNING") as capturado:
            self.assertIsNone(llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO))

        self.assertIn("HTTP 400", "\n".join(capturado.output))

    @override_settings(**CONFIGURADO)
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_o_http_de_erro_nao_e_repetido(self, urlopen):
        """Recusa HTTP não melhora por insistência — insistir só seguraria."""
        urlopen.side_effect = http_error(400)

        with self.assertLogs("apps.core.llm", level="WARNING"):
            llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO)

        self.assertEqual(urlopen.call_count, 1)

    @override_settings(**CONFIGURADO)
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_servico_fora_do_ar_vira_none(self, urlopen):
        urlopen.side_effect = urllib.error.URLError("sem rota")

        with self.assertLogs("apps.core.llm", level="WARNING"):
            self.assertIsNone(llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO))

        self.assertEqual(urlopen.call_count, 1)


# ---------------------------------------------------------------------------
# Repetição
# ---------------------------------------------------------------------------


class RepeticaoTests(SimpleTestCase):
    """Só a demora acima do limite vale uma segunda tentativa — e só uma."""

    @override_settings(**CONFIGURADO)
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_o_sucesso_na_primeira_nao_repete(self, urlopen):
        urlopen.return_value = RespostaFalsa(resposta())

        self.assertEqual(llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO), OBJETO)
        self.assertEqual(urlopen.call_count, 1)

    @override_settings(**CONFIGURADO)
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_a_segunda_tentativa_salva_a_leitura(self, urlopen):
        """O serviço respondeu da outra vez; insistir uma vez costuma bastar."""
        urlopen.side_effect = [TimeoutError(), RespostaFalsa(resposta())]

        with self.assertLogs("apps.core.llm", level="WARNING") as capturado:
            resultado = llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO)

        self.assertEqual(resultado, OBJETO)
        self.assertEqual(urlopen.call_count, 2)
        self.assertIn("tentativa 1 de 2", "\n".join(capturado.output))

    @override_settings(**CONFIGURADO)
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_duas_demoras_desistem(self, urlopen):
        """Repetir para sempre seguraria o registro atrás de um serviço parado."""
        urlopen.side_effect = TimeoutError()

        with self.assertLogs("apps.core.llm", level="WARNING") as capturado:
            self.assertIsNone(llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO))

        self.assertEqual(urlopen.call_count, 2)
        # A segunda é a última: nada de uma terceira tentativa.
        self.assertEqual(llm.TENTATIVAS, 2)
        self.assertIn("tentativa 2 de 2", "\n".join(capturado.output))

    @override_settings(**CONFIGURADO)
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_a_demora_nao_sobe_para_quem_chamou(self, urlopen):
        """Quem chama recebe ``None``; a repetição mora inteira aqui dentro."""
        urlopen.side_effect = TimeoutError()

        with self.assertLogs("apps.core.llm", level="WARNING"):
            self.assertIsNone(llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO))


# ---------------------------------------------------------------------------
# Privacidade
# ---------------------------------------------------------------------------


class PrivacidadeTests(SimpleTestCase):
    """O que se registra é o tipo da falha — nunca a credencial."""

    @override_settings(**CONFIGURADO)
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_o_log_nao_carrega_a_credencial(self, urlopen):
        """O corpo do erro pode ecoar o que foi enviado; ele fica fora do log."""
        urlopen.side_effect = http_error(
            401, corpo=json.dumps({"error": {"message": CHAVE}}).encode("utf-8")
        )

        with self.assertLogs("apps.core.llm", level="WARNING") as capturado:
            llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO)

        registro = "\n".join(capturado.output)
        self.assertNotIn(CHAVE, registro)
        self.assertIn("HTTP 401", registro)

    @override_settings(**CONFIGURADO)
    @patch("apps.core.llm.urllib.request.urlopen")
    def test_o_log_da_demora_nao_carrega_a_credencial(self, urlopen):
        """O log da repetição diz o tipo da falha — e nada do que foi enviado."""
        urlopen.side_effect = TimeoutError(f"Bearer {CHAVE}")

        with self.assertLogs("apps.core.llm", level="WARNING") as capturado:
            llm.pedir_json(sistema=SISTEMA, pedido=PEDIDO)

        registro = "\n".join(capturado.output)
        self.assertNotIn(CHAVE, registro)
        self.assertIn("TimeoutError", registro)
