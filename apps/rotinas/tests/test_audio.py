"""Falar uma rotina: a fala vira o mesmo relato que seria digitado.

O microfone da lista de rotinas aparece a quem pode registrar rotina. O áudio
tem uma responsabilidade única: transformar o recado falado no texto que a
pessoa digitaria. Nada de interpretar, nada de sugerir periodicidade, nada de
registrar.

Estes testes prendem o essencial: a porta de áudio é a das Rotinas, a permissão
é a do próprio módulo, o condomínio vem do formulário — e nunca de um palpite —
e a resposta termina no texto (só a transcrição de Demandas diz se a fala basta
para seguir adiante).

O serviço externo nunca é chamado de verdade: a conversa HTTP é substituída por
uma resposta falsa, como em ``apps/core/tests/test_audio.py``.
"""
import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.rotinas.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)

CONFIGURADO = {
    "EITHRA_LLM_API_KEY": "chave-de-teste-do-provedor",
    "EITHRA_LLM_BASE_URL": "https://provedor.invalido/v1",
    "EITHRA_AUDIO_MODEL": "modelo-de-transcricao",
}

SENTENCA = "Limpar a caixa d'água e conferir o nível do reservatório toda semana."


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


class RotinaPorAudioTests(TestCase):
    """A porta de áudio das Rotinas e quem pode passar por ela."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    def _ditar(self, **extra):
        dados = {"condominio": str(self.condominio.pk), "audio": extra.pop("audio", recado())}
        dados.update(extra)
        return self.client.post(reverse("rotinas:transcrever"), dados)

    # --- quem pode ditar ---------------------------------------------------

    def test_quem_nao_esta_logado_nao_dita(self):
        self.client.logout()

        resposta = self._ditar()

        self.assertEqual(resposta.status_code, 302)

    def test_quem_registra_rotina_tambem_dita(self):
        with patch("apps.core.audio.urllib.request.urlopen") as urlopen:
            urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))
            with override_settings(**CONFIGURADO):
                resposta = self._ditar()

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"texto": SENTENCA})

    # --- quem não pode ditar ------------------------------------------------

    def test_sem_permissao_recebe_404(self):
        """A operação não registra rotina: a porta de áudio também não se abre."""
        operacional = criar_pessoa("operacional@exemplo.test", "Operação")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        self.client.force_login(operacional)

        resposta = self._ditar()

        self.assertEqual(resposta.status_code, 404)

    def test_morador_recebe_404(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)

        resposta = self._ditar()

        self.assertEqual(resposta.status_code, 404)

    def test_condominio_forjado_e_recusado(self):
        """Ditar em nome de um condomínio sem permissão não passa."""
        proibido = criar_condominio("Sem vínculo")

        resposta = self._ditar(condominio=str(proibido.pk))

        self.assertEqual(resposta.status_code, 404)

    def test_pedido_sem_condominio_e_recusado(self):
        """Sem o condomínio do formulário, nada é adivinhado: a resposta é 404."""
        resposta = self._ditar(condominio="")

        self.assertEqual(resposta.status_code, 404)

    # --- a resposta termina no texto ----------------------------------------

    def test_a_resposta_nao_traz_o_sinal_de_avancar(self):
        """Só a transcrição de Demandas diz se a fala basta para seguir."""
        with patch("apps.core.audio.urllib.request.urlopen") as urlopen:
            urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))
            with override_settings(**CONFIGURADO):
                resposta = self._ditar()

        self.assertNotIn("avancar", resposta.json())


class TelaDaRotinaTests(TestCase):
    """O microfone da lista aponta para a porta de áudio das Rotinas."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    @override_settings(**CONFIGURADO)
    def test_a_lista_aponta_para_o_proprio_endpoint(self):
        resposta = self.client.get(reverse("rotinas:listagem"))
        conteudo = resposta.content.decode()

        self.assertTrue(resposta.context["voz_disponivel"])
        self.assertIn(reverse("rotinas:transcrever"), conteudo)
        self.assertNotIn(reverse("demandas:transcrever"), conteudo)
