"""Testes do provider do Gmail, sem tocar a rede nem o banco.

A conversa HTTP é substituída por uma resposta falsa, como em
``apps/core/tests/test_audio.py``. O que se verifica é o contrato técnico: o que
o provider aceita, o que ele recusa, como autentica, como normaliza a mensagem,
como separa corpo de anexo — e o compromisso que atravessa tudo: nenhum caminho
registra credencial, corpo ou anexo, e nenhuma exceção bruta da rede chega à
camada de cima.

O provider não cria, lê nem altera nada no domínio: não há banco aqui. Ele só
lê o Gmail e devolve ``MensagemGmail`` em memória.
"""
import base64
import io
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.core import gmail

CLIENTE = "cliente-de-teste.apps.googleusercontent.com"
SEGREDO = "segredo-de-teste"
REFRESH = "refresh-de-teste"
TOKEN = "acesso-de-teste"
CONTA = "portaria@exemplo.test"

CREDENCIAIS = gmail.CredenciaisGmail(
    client_id=CLIENTE,
    client_secret=SEGREDO,
    refresh_token=REFRESH,
    conta=CONTA,
)

CONFIGURADO = {
    "EITHRA_GMAIL_CLIENT_ID": CLIENTE,
    "EITHRA_GMAIL_CLIENT_SECRET": SEGREDO,
    "EITHRA_GMAIL_REFRESH_TOKEN": REFRESH,
    "EITHRA_GMAIL_CONTA": CONTA,
}


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------


def b64(texto: str) -> str:
    """O mesmo base64url que a API do Gmail usa no corpo das partes."""
    return base64.urlsafe_b64encode(texto.encode("utf-8")).decode("ascii")


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


def roteador(*, token=None, api=None):
    """Um ``urlopen`` falso: responde o token e roteia a API por trecho de URL.

    As rotas são pares ``(trecho, resposta)``, avaliados na ordem. Uma resposta
    que seja ``Exception`` é levantada — é assim que se simula a falha da API.
    """
    resposta_token = (
        token if token is not None else {"access_token": TOKEN, "expires_in": 3600}
    )
    rotas = list(api or [])

    def _urlopen(requisicao, timeout=None):
        url = requisicao.full_url
        if url.startswith(gmail.ENDERECO_TOKEN):
            if isinstance(resposta_token, Exception):
                raise resposta_token
            return RespostaFalsa(resposta_token)
        for marca, resposta in rotas:
            if marca in url:
                if isinstance(resposta, Exception):
                    raise resposta
                return RespostaFalsa(resposta)
        raise AssertionError(f"URL não roteada: {url}")

    return _urlopen


def mensagem_json(
    *,
    message_id="msg-1",
    thread_id="tr-1",
    de="Atlas Elevadores <Financeiro@Atlas.com.br>",
    para="sindica@exemplo.test",
    assunto="Orçamento de elevador",
    data="Mon, 27 Oct 2026 10:30:00 -0300",
    texto=None,
    partes=None,
    mime="text/plain",
    history_id="9001",
    internal_date="1793185800000",
):
    """Um recurso Message do Gmail, no recorte que o provider lê."""
    payload = {
        "mimeType": mime,
        "headers": [
            {"name": "From", "value": de},
            {"name": "To", "value": para},
            {"name": "Subject", "value": assunto},
            {"name": "Date", "value": data},
        ],
    }
    if partes is not None:
        payload["parts"] = partes
        payload["body"] = {"size": 0}
    else:
        conteudo = texto or ""
        payload["body"] = {"size": len(conteudo), "data": b64(conteudo)}

    return {
        "id": message_id,
        "threadId": thread_id,
        "historyId": history_id,
        "internalDate": internal_date,
        "payload": payload,
    }


def parte_texto(conteudo: str):
    return {"mimeType": "text/plain", "body": {"size": len(conteudo), "data": b64(conteudo)}}


def parte_html(conteudo: str):
    return {"mimeType": "text/html", "body": {"size": len(conteudo), "data": b64(conteudo)}}


def parte_anexo(nome: str, mime: str, anexo_id: str, tamanho: int):
    return {
        "mimeType": mime,
        "filename": nome,
        "body": {"attachmentId": anexo_id, "size": tamanho},
    }


def http_error(codigo: int, *, motivo: str | None = None, corpo: bytes | None = None):
    """Um HTTPError como o ``urlopen`` levanta, com corpo opcional."""
    if corpo is None and motivo:
        corpo = json.dumps(
            {"error": {"errors": [{"reason": motivo}]}}
        ).encode("utf-8")
    return urllib.error.HTTPError(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        codigo,
        "erro",
        {},
        io.BytesIO(corpo) if corpo is not None else None,
    )


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------


class ConfiguracaoTests(SimpleTestCase):
    """Sem credencial não há leitura — e isso não é erro."""

    @override_settings(EITHRA_GMAIL_CLIENT_ID="", EITHRA_GMAIL_CLIENT_SECRET="", EITHRA_GMAIL_REFRESH_TOKEN="")
    def test_sem_credencial_nao_esta_configurado(self):
        self.assertFalse(gmail.configurado())

    @override_settings(EITHRA_GMAIL_CLIENT_ID=CLIENTE, EITHRA_GMAIL_CLIENT_SECRET="", EITHRA_GMAIL_REFRESH_TOKEN="")
    def test_credencial_incompleta_nao_configura(self):
        self.assertFalse(gmail.configurado())

    @override_settings(**CONFIGURADO)
    def test_com_credencial_esta_configurado(self):
        self.assertTrue(gmail.configurado())

    @override_settings(EITHRA_GMAIL_CLIENT_ID="", EITHRA_GMAIL_CLIENT_SECRET="", EITHRA_GMAIL_REFRESH_TOKEN="")
    def test_obter_provedor_sem_configuracao_recusa(self):
        with self.assertRaises(gmail.GmailNaoConfigurado):
            gmail.obter_provedor()

    @override_settings(EITHRA_GMAIL_CLIENT_ID="", EITHRA_GMAIL_CLIENT_SECRET="", EITHRA_GMAIL_REFRESH_TOKEN="")
    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_provedor_sem_credencial_recusa_sem_tocar_a_rede(self, urlopen):
        with self.assertRaises(gmail.GmailNaoConfigurado):
            gmail.ProvedorGmail().listar_mensagens()
        urlopen.assert_not_called()


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------


class AutenticacaoTests(SimpleTestCase):
    """O contrato de autenticação: refresh token em troca de um token de acesso."""

    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_autentica_e_le_com_token_no_cabecalho(self, urlopen):
        urlopen.side_effect = roteador(
            api=[("messages", {"messages": [{"id": "m1", "threadId": "t1"}]})]
        )

        gmail.ProvedorGmail(credenciais=CREDENCIAIS).listar_mensagens()

        pedido_de_token = urlopen.call_args_list[0].args[0]
        self.assertEqual(pedido_de_token.get_method(), "POST")
        self.assertEqual(pedido_de_token.full_url, gmail.ENDERECO_TOKEN)
        self.assertIn(f"client_id={CLIENTE}".encode(), pedido_de_token.data)
        self.assertIn(b"grant_type=refresh_token", pedido_de_token.data)

        pedido_da_api = urlopen.call_args_list[1].args[0]
        self.assertEqual(pedido_da_api.headers["Authorization"], f"Bearer {TOKEN}")

    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_token_e_reaproveitado_entre_chamadas(self, urlopen):
        urlopen.side_effect = roteador(api=[("messages", {"messages": []})])

        provedor = gmail.ProvedorGmail(credenciais=CREDENCIAIS)
        provedor.listar_mensagens()
        provedor.listar_mensagens()

        pedidos_de_token = [
            chamada.args[0]
            for chamada in urlopen.call_args_list
            if chamada.args[0].full_url == gmail.ENDERECO_TOKEN
        ]
        self.assertEqual(len(pedidos_de_token), 1)

    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_credencial_recusada_vira_credencial_invalida(self, urlopen):
        urlopen.side_effect = roteador(token=http_error(400, motivo="invalid_grant"))

        with self.assertRaises(gmail.GmailCredencialInvalida):
            gmail.ProvedorGmail(credenciais=CREDENCIAIS).listar_mensagens()

    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_autenticacao_sem_token_vira_credencial_invalida(self, urlopen):
        urlopen.side_effect = roteador(token={"expires_in": 3600})

        with self.assertRaises(gmail.GmailCredencialInvalida):
            gmail.ProvedorGmail(credenciais=CREDENCIAIS).listar_mensagens()

    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_falha_de_rede_ao_autenticar_vira_indisponivel(self, urlopen):
        urlopen.side_effect = roteador(token=urllib.error.URLError("sem rede"))

        with self.assertRaises(gmail.GmailIndisponivel):
            gmail.ProvedorGmail(credenciais=CREDENCIAIS).listar_mensagens()


# ---------------------------------------------------------------------------
# Listagem
# ---------------------------------------------------------------------------


class ListagemTests(SimpleTestCase):
    """A listagem devolve referências — sem baixar o conteúdo."""

    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_lista_referencias_de_mensagem(self, urlopen):
        urlopen.side_effect = roteador(
            api=[
                (
                    "messages",
                    {
                        "messages": [
                            {"id": "m1", "threadId": "t1"},
                            {"id": "m2", "threadId": "t1"},
                        ]
                    },
                )
            ]
        )

        pagina = gmail.ProvedorGmail(credenciais=CREDENCIAIS).listar_mensagens()

        self.assertEqual(
            [(item.message_id, item.thread_id) for item in pagina.itens],
            [("m1", "t1"), ("m2", "t1")],
        )
        self.assertEqual(pagina.proxima_pagina, "")

    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_paginacao_devolve_e_reenvia_o_cursor(self, urlopen):
        urlopen.side_effect = roteador(
            api=[
                (
                    "messages",
                    {
                        "messages": [{"id": "m1", "threadId": "t1"}],
                        "nextPageToken": "pg-2",
                    },
                )
            ]
        )
        provedor = gmail.ProvedorGmail(credenciais=CREDENCIAIS)

        primeira = provedor.listar_mensagens()
        self.assertEqual(primeira.proxima_pagina, "pg-2")

        provedor.listar_mensagens(pagina_token=primeira.proxima_pagina)

        ultima = urlopen.call_args_list[-1].args[0]
        self.assertIn("pageToken=pg-2", ultima.full_url)

    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_busca_e_limite_sao_enviados(self, urlopen):
        urlopen.side_effect = roteador(api=[("messages", {"messages": []})])

        gmail.ProvedorGmail(credenciais=CREDENCIAIS).listar_mensagens(
            consulta="in:inbox newer_than:7d", quantidade=25
        )

        pedido = urlopen.call_args_list[-1].args[0]
        self.assertIn("maxResults=25", pedido.full_url)
        self.assertIn("q=in%3Ainbox", pedido.full_url)

    @override_settings(EITHRA_GMAIL_TIMEOUT=7.5)
    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_a_conversa_tem_tempo_maximo_de_espera(self, urlopen):
        urlopen.side_effect = roteador(api=[("messages", {"messages": []})])

        gmail.ProvedorGmail(credenciais=CREDENCIAIS).listar_mensagens()

        self.assertEqual(urlopen.call_args.kwargs["timeout"], 7.5)

    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_resposta_ilegivel_vira_resposta_invalida(self, urlopen):
        urlopen.side_effect = roteador(api=[("messages", b"isto nao e json")])

        with self.assertRaises(gmail.GmailRespostaInvalida):
            gmail.ProvedorGmail(credenciais=CREDENCIAIS).listar_mensagens()

    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_resposta_fora_do_formato_vira_resposta_invalida(self, urlopen):
        urlopen.side_effect = roteador(api=[("messages", ["nao", "e", "objeto"])])

        with self.assertRaises(gmail.GmailRespostaInvalida):
            gmail.ProvedorGmail(credenciais=CREDENCIAIS).listar_mensagens()


# ---------------------------------------------------------------------------
# Mensagem normalizada
# ---------------------------------------------------------------------------


class MensagemTests(SimpleTestCase):
    """A mensagem chega normalizada: cabeçalho, texto e anexos separados."""

    def _obter(self, carga):
        with patch("apps.core.gmail.urllib.request.urlopen") as urlopen:
            urlopen.side_effect = roteador(api=[("messages/msg-1", carga)])
            return gmail.ProvedorGmail(credenciais=CREDENCIAIS).obter_mensagem("msg-1")

    def test_normaliza_remetente(self):
        mensagem = self._obter(mensagem_json(texto="Bom dia."))
        self.assertEqual(mensagem.remetente, "financeiro@atlas.com.br")

    def test_le_assunto_e_destinatarios(self):
        mensagem = self._obter(
            mensagem_json(
                para="Síndica <sindica@exemplo.test>, zelador@exemplo.test",
                assunto="  Orçamento   de elevador ",
                texto="Bom dia.",
            )
        )
        self.assertEqual(mensagem.assunto, "Orçamento de elevador")
        self.assertEqual(
            mensagem.destinatarios, ("sindica@exemplo.test", "zelador@exemplo.test")
        )

    def test_le_message_id_thread_id_e_history_id(self):
        mensagem = self._obter(mensagem_json(texto="Bom dia."))
        self.assertEqual(mensagem.message_id, "msg-1")
        self.assertEqual(mensagem.thread_id, "tr-1")
        self.assertEqual(mensagem.history_id, "9001")

    def test_data_e_hora_vem_com_fuso(self):
        mensagem = self._obter(mensagem_json(texto="Bom dia."))
        self.assertEqual(
            mensagem.momento,
            datetime(2026, 10, 27, 10, 30, tzinfo=timezone(timedelta(hours=-3))),
        )

    def test_sem_data_usa_o_internal_date(self):
        mensagem = self._obter(
            mensagem_json(data="", internal_date="1793185800000", texto="Bom dia.")
        )
        self.assertEqual(
            mensagem.momento, datetime.fromtimestamp(1793185800000 / 1000, tz=timezone.utc)
        )

    def test_corpo_text_plain(self):
        mensagem = self._obter(mensagem_json(texto="O elevador parou no oitavo andar."))
        self.assertEqual(mensagem.texto, "O elevador parou no oitavo andar.")

    def test_corpo_html_como_fallback(self):
        carga = mensagem_json(partes=[parte_html("<p>O elevador parou.</p><p>Segunda vez.</p>")])
        mensagem = self._obter(carga)
        self.assertIn("O elevador parou.", mensagem.texto)
        self.assertNotIn("<p>", mensagem.texto)

    def test_multipart_prefere_text_plain(self):
        carga = mensagem_json(
            partes=[
                parte_texto("Corpo em texto."),
                parte_html("<p>Corpo em html.</p>"),
            ]
        )
        mensagem = self._obter(carga)
        self.assertEqual(mensagem.texto, "Corpo em texto.")

    def test_identifica_anexos_sem_baixar(self):
        carga = mensagem_json(
            partes=[
                parte_texto("Segue o orçamento em anexo."),
                parte_anexo("orcamento.pdf", "application/pdf", "att-1", 2048),
            ]
        )
        mensagem = self._obter(carga)
        self.assertEqual(len(mensagem.anexos), 1)
        anexo = mensagem.anexos[0]
        self.assertEqual(anexo.attachment_id, "att-1")
        self.assertEqual(anexo.nome, "orcamento.pdf")
        self.assertEqual(anexo.mime_type, "application/pdf")
        self.assertEqual(anexo.tamanho, 2048)

    def test_anexo_de_texto_nao_e_confundido_com_o_corpo(self):
        carga = mensagem_json(partes=[parte_anexo("notas.txt", "text/plain", "att-2", 10)])
        mensagem = self._obter(carga)
        self.assertEqual(mensagem.texto, "")
        self.assertEqual(len(mensagem.anexos), 1)


# ---------------------------------------------------------------------------
# Anexo
# ---------------------------------------------------------------------------


class AnexoTests(SimpleTestCase):
    """Os bytes do anexo só vêm quando pedidos — e nada é gravado."""

    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_baixa_os_bytes_do_anexo(self, urlopen):
        conteudo = b"%PDF-1.4 orcamento"
        urlopen.side_effect = roteador(
            api=[
                (
                    "attachments/att-1",
                    {"size": len(conteudo), "data": base64.urlsafe_b64encode(conteudo).decode()},
                )
            ]
        )

        dados = gmail.ProvedorGmail(credenciais=CREDENCIAIS).baixar_anexo("msg-1", "att-1")

        self.assertEqual(dados, conteudo)
        pedido = urlopen.call_args_list[-1].args[0]
        self.assertIn("messages/msg-1/attachments/att-1", pedido.full_url)

    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_anexo_inexistente_vira_anexo_inexistente(self, urlopen):
        urlopen.side_effect = roteador(api=[("attachments/att-9", http_error(404))])

        with self.assertRaises(gmail.GmailAnexoInexistente):
            gmail.ProvedorGmail(credenciais=CREDENCIAIS).baixar_anexo("msg-1", "att-9")

    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_anexo_sem_identificador_recusa_sem_tocar_a_rede(self, urlopen):
        with self.assertRaises(gmail.GmailAnexoInexistente):
            gmail.ProvedorGmail(credenciais=CREDENCIAIS).baixar_anexo("msg-1", "")
        urlopen.assert_not_called()


# ---------------------------------------------------------------------------
# Mensagem inexistente
# ---------------------------------------------------------------------------


class MensagemInexistenteTests(SimpleTestCase):
    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_mensagem_inexistente_vira_mensagem_inexistente(self, urlopen):
        urlopen.side_effect = roteador(api=[("messages/msg-9", http_error(404))])

        with self.assertRaises(gmail.GmailMensagemInexistente):
            gmail.ProvedorGmail(credenciais=CREDENCIAIS).obter_mensagem("msg-9")

    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_mensagem_sem_identificador_recusa_sem_tocar_a_rede(self, urlopen):
        with self.assertRaises(gmail.GmailMensagemInexistente):
            gmail.ProvedorGmail(credenciais=CREDENCIAIS).obter_mensagem("")
        urlopen.assert_not_called()


# ---------------------------------------------------------------------------
# Falha temporária e credencial
# ---------------------------------------------------------------------------


class FalhaDaApiTests(SimpleTestCase):
    """A camada de cima distingue temporário de credencial sem ver HTTP."""

    def _usar(self, erro):
        with patch("apps.core.gmail.urllib.request.urlopen") as urlopen:
            urlopen.side_effect = roteador(api=[("messages", erro)])
            gmail.ProvedorGmail(credenciais=CREDENCIAIS).listar_mensagens()

    def test_servico_indisponivel_vira_indisponivel(self):
        with self.assertRaises(gmail.GmailIndisponivel):
            self._usar(http_error(503))

    def test_limite_de_uso_vira_indisponivel(self):
        with self.assertRaises(gmail.GmailIndisponivel):
            self._usar(http_error(403, motivo="rateLimitExceeded"))

    def test_credencial_expirada_vira_credencial_invalida(self):
        with self.assertRaises(gmail.GmailCredencialInvalida):
            self._usar(http_error(401))

    def test_permissao_insuficiente_vira_credencial_invalida(self):
        with self.assertRaises(gmail.GmailCredencialInvalida):
            self._usar(http_error(403, motivo="insufficientPermissions"))

    def test_rede_fora_vira_indisponivel(self):
        with self.assertRaises(gmail.GmailIndisponivel):
            self._usar(urllib.error.URLError("sem rede"))

    def test_tempo_esgotado_vira_indisponivel(self):
        with self.assertRaises(gmail.GmailIndisponivel):
            self._usar(TimeoutError("tempo esgotado"))


# ---------------------------------------------------------------------------
# Privacidade
# ---------------------------------------------------------------------------


class PrivacidadeTests(SimpleTestCase):
    """O que se registra é o fato técnico — nunca credencial, corpo ou anexo."""

    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_o_log_nao_carrega_a_credencial(self, urlopen):
        urlopen.side_effect = roteador(token=http_error(400, motivo="invalid_grant"))

        with self.assertLogs("apps.core.gmail", level="WARNING") as capturado:
            with self.assertRaises(gmail.GmailCredencialInvalida):
                gmail.ProvedorGmail(credenciais=CREDENCIAIS).listar_mensagens()

        registro = "\n".join(capturado.output)
        self.assertNotIn(SEGREDO, registro)
        self.assertNotIn(REFRESH, registro)
        self.assertNotIn(TOKEN, registro)

    @patch("apps.core.gmail.urllib.request.urlopen")
    def test_o_log_nao_carrega_o_corpo_da_mensagem(self, urlopen):
        sentenca = "O elevador parou no oitavo andar depois do horário."
        urlopen.side_effect = roteador(
            api=[("messages", http_error(500, corpo=sentenca.encode("utf-8")))]
        )

        with self.assertLogs("apps.core.gmail", level="WARNING") as capturado:
            with self.assertRaises(gmail.GmailIndisponivel):
                gmail.ProvedorGmail(credenciais=CREDENCIAIS).listar_mensagens()

        registro = "\n".join(capturado.output)
        self.assertNotIn(sentenca, registro)
        self.assertIn("HTTP 500", registro)


# ---------------------------------------------------------------------------
# HTML → texto
# ---------------------------------------------------------------------------


class HtmlParaTextoTests(SimpleTestCase):
    """A conversão é mínima: tira marcador e script, sem limpeza semântica."""

    def test_remove_marcadores_e_script(self):
        texto = gmail.html_para_texto(
            "<html><body><p>Olá</p><script>alert('x')</script><p>Segunda linha</p></body></html>"
        )
        self.assertIn("Olá", texto)
        self.assertIn("Segunda linha", texto)
        self.assertNotIn("<p>", texto)
        self.assertNotIn("alert", texto)

    def test_html_vazio_devolve_texto_vazio(self):
        self.assertEqual(gmail.html_para_texto(""), "")
