"""A fala da Portaria vira texto — e só texto.

O áudio tem uma responsabilidade única nesta tela: transformar um recado falado
no mesmo relato que a portaria digitaria. Nada de interpretar, nada de registrar,
nada guardado. É isso que estes testes prendem: o portão de entrada, o formato da
resposta, a ausência de consequência e o descarte do arquivo.

O serviço externo de transcrição nunca é chamado de verdade: a conversa HTTP é
substituída por uma resposta falsa, como em ``apps/core/tests/test_audio.py``.
Assim o que se verifica aqui é o que a Portaria faz com o áudio — não o que o
provedor faria com ele.
"""
import json
import os
import shutil
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.comunidade.tests import apoio as comunidade_apoio
from apps.condominios.constants import Papel
from apps.core import audio
from apps.demandas.models import Demanda
from apps.portaria.models import AcessoPortaria, AutorizacaoPortaria
from apps.portaria.tests import apoio

CONFIGURADO = {
    "EITHRA_LLM_API_KEY": "chave-de-teste-do-provedor",
    "EITHRA_LLM_BASE_URL": "https://provedor.invalido/v1",
    "EITHRA_AUDIO_MODEL": "modelo-de-transcricao",
}

# Só o modelo de transcrição falta: é o bastante para o núcleo de áudio dizer
# que a entrada por voz não está disponível, sem mexer na leitura de texto.
SEM_VOZ = {"EITHRA_AUDIO_MODEL": ""}

SENTENCA = "Chegou Carlos, visita do 301, para ver o elevador."


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


class PortariaAudioTests(TestCase):
    """O endpoint de transcrição, do portão de entrada ao descarte do áudio."""

    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(self.usuario)

    def _enviar(self, arquivo=None, **extra):
        dados = {"condominio": str(self.condominio.pk)}
        if arquivo is not None:
            dados["audio"] = arquivo
        dados.update(extra)
        return self.client.post(reverse("portaria:transcrever"), dados)

    def _nada_aconteceu(self):
        self.assertEqual(AutorizacaoPortaria.objects.count(), 0)
        self.assertEqual(AcessoPortaria.objects.count(), 0)
        self.assertEqual(Demanda.objects.count(), 0)

    # --- quem pode ditar ---------------------------------------------------

    def test_quem_nao_esta_logado_nao_transcreve(self):
        """Sem sessão não há transcrição: o primeiro portão continua sendo o login."""
        self.client.logout()

        resposta = self._enviar(recado())

        self.assertEqual(resposta.status_code, 302)
        self._nada_aconteceu()

    def test_quem_nao_opera_o_condominio_e_recusado(self):
        morador = apoio.criar_pessoa("morador@teste.com", "Morador")
        apoio.criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)

        resposta = self._enviar(recado())

        self.assertEqual(resposta.status_code, 404)
        self._nada_aconteceu()

    def test_condominio_forjado_no_payload_e_recusado(self):
        """Um condomínio que a pessoa não opera não entra por POST."""
        outro = apoio.criar_condominio("Outro Condomínio")

        resposta = self._enviar(recado(), condominio=str(outro.pk))

        self.assertEqual(resposta.status_code, 404)
        self._nada_aconteceu()

    # --- o caminho feliz ---------------------------------------------------

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_audio_valido_vira_texto(self, urlopen):
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))

        resposta = self._enviar(recado())

        self.assertEqual(resposta.status_code, 200)
        urlopen.assert_called_once()
        self.assertEqual(resposta.json(), {"texto": SENTENCA})

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_a_resposta_traz_apenas_o_texto(self, urlopen):
        """O que a tela recebe é o relato — sem confiança, idioma ou duração."""
        urlopen.return_value = RespostaFalsa(
            corpo_json(
                {
                    "text": SENTENCA,
                    "language": "portuguese",
                    "confidence": 0.94,
                    "duration": 3.1,
                }
            )
        )

        corpo = self._enviar(recado()).json()

        self.assertEqual(set(corpo), {"texto"})
        self.assertNotIn("portuguese", corpo.values())
        self.assertNotIn(0.94, corpo.values())

    # --- transcrever não é operar -----------------------------------------

    @override_settings(**CONFIGURADO)
    @patch("apps.portaria.views.interpretacao.interpretar")
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_transcrever_nao_interpreta(self, urlopen, interpretar):
        """A transcrição para no texto: quem lê o sentido é o passo seguinte."""
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))

        self._enviar(recado())

        interpretar.assert_not_called()
        self._nada_aconteceu()

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_transcrever_nao_registra_autorizacao(self, urlopen):
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))

        self._enviar(recado())

        self.assertEqual(AutorizacaoPortaria.objects.count(), 0)

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_transcrever_nao_registra_acesso(self, urlopen):
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))

        self._enviar(recado())

        self.assertEqual(AcessoPortaria.objects.count(), 0)

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_transcrever_nao_cria_demanda(self, urlopen):
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))

        self._enviar(recado())

        self.assertEqual(Demanda.objects.count(), 0)

    # --- o que é recusado antes de chegar ao serviço -----------------------

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_audio_vazio_e_recusado(self, urlopen):
        resposta = self._enviar(recado(conteudo=b""))

        self.assertEqual(resposta.json()["mensagem"], audio.MENSAGEM_VAZIO)
        urlopen.assert_not_called()
        self._nada_aconteceu()

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_pedido_sem_arquivo_e_recusado(self, urlopen):
        resposta = self._enviar()

        self.assertEqual(resposta.json()["mensagem"], audio.MENSAGEM_VAZIO)
        urlopen.assert_not_called()
        self._nada_aconteceu()

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_tipo_invalido_e_recusado(self, urlopen):
        resposta = self._enviar(
            recado(conteudo=b"%PDF-1.4", nome="documento.pdf", tipo="application/pdf")
        )

        self.assertEqual(resposta.json()["mensagem"], audio.MENSAGEM_FORMATO)
        urlopen.assert_not_called()
        self._nada_aconteceu()

    @override_settings(**CONFIGURADO, EITHRA_AUDIO_MAX_BYTES=16)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_audio_acima_do_limite_e_recusado(self, urlopen):
        resposta = self._enviar(recado(conteudo=b"x" * 17))

        self.assertEqual(resposta.json()["mensagem"], audio.MENSAGEM_GRANDE)
        urlopen.assert_not_called()
        self._nada_aconteceu()

    # --- quando o serviço falha --------------------------------------------

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_servico_fora_do_ar_nao_derruba_a_tela(self, urlopen):
        urlopen.side_effect = TimeoutError("tempo esgotado")

        resposta = self._enviar(recado())

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["mensagem"], audio.MENSAGEM_FALHA)
        self._nada_aconteceu()

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_transcricao_sem_fala_pede_para_gravar_de_novo(self, urlopen):
        urlopen.return_value = RespostaFalsa(corpo_json({"text": "   "}))

        resposta = self._enviar(recado())

        self.assertEqual(resposta.json()["mensagem"], audio.MENSAGEM_SEM_FALA)
        self._nada_aconteceu()

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_a_falha_nao_expoe_motivo_tecnico(self, urlopen):
        urlopen.side_effect = TimeoutError("tempo esgotado")

        resposta = self._enviar(recado())

        self.assertEqual(set(resposta.json()), {"mensagem"})
        self.assertNotIn("TimeoutError", resposta.content.decode())

    # --- o áudio não fica --------------------------------------------------

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_nenhum_arquivo_permanece_em_storage(self, urlopen):
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))
        raiz = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, raiz, True)

        with override_settings(MEDIA_ROOT=raiz):
            self._enviar(recado())

        self.assertEqual(os.listdir(raiz), [])

    @override_settings(**CONFIGURADO, FILE_UPLOAD_MAX_MEMORY_SIZE=1)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_o_arquivo_que_passou_pelo_disco_e_removido(self, urlopen):
        """Acima do limite de memória o Django passa por disco — e não fica lá."""
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))
        spool = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, spool, True)

        with override_settings(FILE_UPLOAD_TEMP_DIR=spool):
            self._enviar(recado())

        self.assertEqual(os.listdir(spool), [])

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_a_transcricao_nao_entra_em_log(self, urlopen):
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))

        with self.assertLogs(level="INFO") as registro:
            self._enviar(recado())

        self.assertNotIn(SENTENCA, "\n".join(registro.output))


class PortariaVozIndisponivelTests(TestCase):
    """Voz é atalho, não caminho: sem ela a Portaria é exatamente a de antes."""

    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(self.usuario)
        comunidade_apoio.criar_unidade(self.condominio, identificador="301")

    def _entender(self, relato):
        return self.client.post(
            reverse("portaria:interpretar"),
            {"condominio": str(self.condominio.pk), "relato": relato},
        )

    @override_settings(**SEM_VOZ)
    def test_sem_servico_o_microfone_nao_e_oferecido(self):
        resposta = self.client.get(reverse("portaria:painel"))

        self.assertContains(resposta, 'id="relato-portaria"')
        self.assertNotContains(resposta, "data-eithra-ditar-url")

    @override_settings(**SEM_VOZ)
    def test_sem_servico_o_relato_por_texto_continua_inteiro(self):
        resposta = self._entender("Chegou Carlos, visita do 301")

        self.assertContains(resposta, 'name="acao" value="chegada"')

    @override_settings(**CONFIGURADO)
    def test_com_servico_o_microfone_so_aparece_se_o_navegador_gravar(self):
        """O botão nasce escondido: quem o revela é a checagem do navegador.

        Assim um navegador sem gravação — ou sem JavaScript — mostra o relato por
        texto exatamente como antes, e nunca um microfone que não funciona.
        """
        resposta = self.client.get(reverse("portaria:painel"))
        pagina = resposta.content.decode()

        self.assertContains(resposta, "data-eithra-ditar-url")
        self.assertRegex(pagina, r"data-eithra-ditar[\s\S]{0,400}?hidden>")
        self.assertIn("MediaRecorder", pagina)

    @override_settings(**CONFIGURADO)
    def test_permissao_negada_deixa_o_relato_por_texto_funcionando(self):
        """O caminho manual não depende da voz em nenhum momento."""
        painel = self.client.get(reverse("portaria:painel"))

        self.assertContains(painel, 'id="relato-portaria"')

        resposta = self._entender("Chegou Carlos, visita do 301")

        self.assertContains(resposta, 'name="acao" value="chegada"')

        confirmado = self.client.post(
            reverse("portaria:confirmar"),
            {
                "condominio": str(self.condominio.pk),
                "relato": "Chegou Carlos, visita do 301",
                "acao": "chegada",
            },
        )

        self.assertEqual(confirmado.status_code, 302)
        self.assertEqual(AcessoPortaria.objects.count(), 1)
