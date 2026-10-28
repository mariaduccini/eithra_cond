"""Falar uma demanda: a fala vira o mesmo relato que seria digitado.

O áudio tem uma responsabilidade única nesta tela: transformar o recado falado
no texto que a pessoa digitaria. Nada de interpretar, nada de sugerir, nada de
registrar — e nada de um caminho paralelo. É isso que estes testes prendem: o
áudio entrega texto, o texto segue pelo wizard de sempre, e a demanda só nasce
na confirmação.

O serviço externo nunca é chamado de verdade: a conversa HTTP é substituída por
uma resposta falsa, como em ``apps/core/tests/test_audio.py``. Assim o que se
verifica aqui é o que as Demandas fazem com o áudio, não o que o provedor faria
com ele.
"""
import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.core import audio
from apps.demandas.models import Demanda
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_local,
    criar_pessoa,
    criar_vinculo,
)

CONFIGURADO = {
    "EITHRA_LLM_API_KEY": "chave-de-teste-do-provedor",
    "EITHRA_LLM_BASE_URL": "https://provedor.invalido/v1",
    "EITHRA_AUDIO_MODEL": "modelo-de-transcricao",
}

# Só o modelo de transcrição falta: é o bastante para o núcleo de áudio dizer
# que a entrada por voz não está disponível, sem mexer na leitura de texto.
SEM_VOZ = {"EITHRA_AUDIO_MODEL": ""}

# A transcrição funciona e a leitura semântica não: sem modelo de linguagem, a
# leitura do relato fica só com as regras. É a falha semântica real — e o texto
# ditado tem de sobreviver a ela inteiro.
SEM_LEITURA_SEMANTICA = {**CONFIGURADO, "EITHRA_LLM_MODEL": ""}

SENTENCA = "O elevador de serviço voltou a parar no oitavo andar e tem morador preso."


class RespostaFalsa:
    """Resposta mínima do provedor, usada como gerenciador de contexto."""

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


def recado(conteudo=b"RIFF....WEBM", nome="relato.webm", tipo="audio/webm"):
    return SimpleUploadedFile(nome, conteudo, content_type=tipo)


class DemandaPorAudioTests(TestCase):
    """O endpoint de transcrição e o caminho do texto — do áudio à demanda."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    def _ditar(self, arquivo=None, **extra):
        dados = {"condominio": str(self.condominio.pk)}
        if arquivo is not None:
            dados["audio"] = arquivo
        dados.update(extra)
        return self.client.post(reverse("demandas:transcrever"), dados)

    def _relatar(self, relato, **extra):
        dados = {
            "condominio": str(self.condominio.pk),
            "relato": relato,
            "wizard": "descricao",
        }
        dados.update(extra)
        return self.client.post(reverse("demandas:criar"), dados)

    def _confirmar(self, resposta_da_etapa_dois):
        """Confirma o registro a partir do formulário que a etapa 2 devolveu."""
        return self.client.post(
            reverse("demandas:criar"),
            {
                "condominio": str(self.condominio.pk),
                "relato": resposta_da_etapa_dois.context["formulario_demanda"][
                    "relato"
                ].value(),
                "titulo": resposta_da_etapa_dois.context["formulario_demanda"][
                    "titulo"
                ].value(),
                "wizard": "revisao",
            },
        )

    # --- quem pode ditar ---------------------------------------------------

    def test_quem_nao_esta_logado_nao_dita(self):
        self.client.logout()

        resposta = self._ditar(recado())

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(Demanda.objects.count(), 0)

    def test_quem_nao_registra_no_condominio_e_recusado(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)

        resposta = self._ditar(recado())

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(Demanda.objects.count(), 0)

    def test_condominio_forjado_no_payload_e_recusado(self):
        """Ditar em nome de outro condomínio não passa: a regra é a mesma de registrar."""
        outro = criar_condominio("Outro")
        criar_vinculo(self.gestao, outro, papel=Papel.SINDICA)
        proibido = criar_condominio("Sem vínculo")

        resposta = self._ditar(recado(), condominio=str(proibido.pk))

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(Demanda.objects.count(), 0)

    # --- do áudio ao texto --------------------------------------------------

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_audio_valido_devolve_apenas_o_texto(self, urlopen):
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))

        resposta = self._ditar(recado())

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"texto": SENTENCA})
        self.assertEqual(Demanda.objects.count(), 0)

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_transcrever_nao_cria_nem_interpreta(self, urlopen):
        """A transcrição para no texto: quem lê o sentido é o wizard, depois."""
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))

        with patch("apps.demandas.interpretacao.interpretar") as interpretar:
            self._ditar(recado())

        interpretar.assert_not_called()
        self.assertEqual(Demanda.objects.count(), 0)

    # --- a transcrição entra no mesmo pipeline do texto ----------------------

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_a_transcricao_recebe_a_mesma_leitura_do_texto_digitado(self, urlopen):
        """O que o áudio produz é tratado exatamente como o relato escrito."""
        criar_local(self.condominio, "Casa de máquinas")
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))

        digitado = self._relatar(SENTENCA)
        transcrito = self._relatar(self._ditar(recado()).json()["texto"])

        self.assertEqual(digitado.context["registro_etapa"], "informacoes")
        self.assertEqual(
            transcrito.context["sugestoes"], digitado.context["sugestoes"]
        )
        self.assertEqual(
            transcrito.context["formulario_demanda"]["titulo"].value(),
            digitado.context["formulario_demanda"]["titulo"].value(),
        )

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_a_transcricao_pode_ser_revista_antes_da_confirmacao(self, urlopen):
        """O texto fica no formulário: quem ditou confere e corrige antes de seguir."""
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))

        etapa = self._relatar(self._ditar(recado()).json()["texto"])

        self.assertEqual(etapa.context["registro_etapa"], "informacoes")
        self.assertEqual(
            etapa.context["formulario_demanda"]["relato"].value(), SENTENCA
        )
        self.assertContains(etapa, SENTENCA)
        self.assertEqual(Demanda.objects.count(), 0)

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_a_confirmacao_cria_uma_unica_demanda(self, urlopen):
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))
        self._relatar(self._ditar(recado()).json()["texto"])

        etapa_dois = self._relatar(SENTENCA)
        resposta = self._confirmar(etapa_dois)

        self.assertEqual(Demanda.objects.count(), 1)
        demanda = Demanda.objects.get()
        self.assertEqual(demanda.condominio, self.condominio)
        self.assertEqual(demanda.descricao, SENTENCA)
        self.assertEqual(resposta.status_code, 302)

    @override_settings(**SEM_LEITURA_SEMANTICA)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_a_falha_semantica_nao_perde_a_transcricao(self, urlopen):
        """Sem leitura semântica, o texto continua inteiro no formulário."""
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))

        texto = self._ditar(recado()).json()["texto"]
        etapa = self._relatar(texto)

        self.assertEqual(texto, SENTENCA)
        self.assertEqual(etapa.context["registro_etapa"], "informacoes")
        self.assertEqual(etapa.context["formulario_demanda"]["relato"].value(), texto)
        self.assertEqual(Demanda.objects.count(), 0)

    # --- o que é recusado antes de chegar ao serviço -------------------------

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_audio_vazio_e_recusado(self, urlopen):
        resposta = self._ditar(recado(conteudo=b""))

        self.assertEqual(resposta.json()["mensagem"], audio.MENSAGEM_VAZIO)
        urlopen.assert_not_called()
        self.assertEqual(Demanda.objects.count(), 0)

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_pedido_sem_arquivo_e_recusado(self, urlopen):
        resposta = self._ditar()

        self.assertEqual(resposta.json()["mensagem"], audio.MENSAGEM_VAZIO)
        urlopen.assert_not_called()

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_formato_nao_aceito_e_recusado(self, urlopen):
        resposta = self._ditar(recado(nome="partitura.pdf", tipo="application/pdf"))

        self.assertEqual(resposta.json()["mensagem"], audio.MENSAGEM_FORMATO)
        urlopen.assert_not_called()
        self.assertEqual(Demanda.objects.count(), 0)

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_falha_do_servico_nao_cria_demanda(self, urlopen):
        urlopen.side_effect = OSError("rede fora")

        resposta = self._ditar(recado())

        self.assertEqual(resposta.json()["mensagem"], audio.MENSAGEM_FALHA)
        self.assertEqual(Demanda.objects.count(), 0)

    @override_settings(**SEM_VOZ)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_sem_servico_o_texto_continua_inteiro(self, urlopen):
        """Sem transcrição configurada, o registro por texto não muda em nada."""
        mensagem = self._ditar(recado()).json()["mensagem"]
        self.assertEqual(mensagem, audio.MENSAGEM_INDISPONIVEL)
        urlopen.assert_not_called()

        etapa = self._relatar("Portão da garagem travando")
        self.assertEqual(etapa.context["registro_etapa"], "informacoes")
        self.assertFalse(etapa.context["voz_disponivel"])

    # --- a tela --------------------------------------------------------------

    @override_settings(**CONFIGURADO)
    def test_a_etapa_da_descricao_oferece_falar(self):
        resposta = self.client.get(reverse("demandas:listagem"))

        self.assertTrue(resposta.context["voz_disponivel"])
        self.assertContains(resposta, "data-eithra-ditar-url")
        self.assertContains(resposta, reverse("demandas:transcrever"))

    @override_settings(**SEM_VOZ)
    def test_sem_servico_a_tela_nao_oferece_falar(self):
        resposta = self.client.get(reverse("demandas:listagem"))

        self.assertFalse(resposta.context["voz_disponivel"])
        self.assertNotContains(resposta, "data-eithra-ditar-url")
        self.assertNotContains(resposta, "js/eithra-ditar.js")
