"""Falar uma manutenção: a fala vira o mesmo texto que seria digitado.

A manutenção mostra o microfone a quem escreve o texto livre — quem a registra
neste condomínio ou quem executa o serviço sob sua responsabilidade. O áudio
tem uma responsabilidade única: transformar o recado falado no texto que a
pessoa digitaria. Nada de interpretar, nada de registrar.

Estes testes prendem o essencial: a porta de áudio é a das Manutenções, a
permissão é a do próprio módulo, o condomínio sai do registro — e nunca de um
palpite — e a resposta termina no texto (só a transcrição de Demandas diz se a
fala basta para seguir adiante).

O serviço externo nunca é chamado de verdade: a conversa HTTP é substituída por
uma resposta falsa, como em ``apps/core/tests/test_audio.py``. Assim o que se
verifica aqui é o que as Manutenções fazem com o áudio, não o que o provedor
faria com ele.
"""
import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.manutencoes.services import criar_manutencao
from apps.manutencoes.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)

CONFIGURADO = {
    "EITHRA_LLM_API_KEY": "chave-de-teste-do-provedor",
    "EITHRA_LLM_BASE_URL": "https://provedor.invalido/v1",
    "EITHRA_AUDIO_MODEL": "modelo-de-transcricao",
}

SENTENCA = "Troquei a boia da caixa d'água e testei o nível do reservatório."


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


class ManutencaoPorAudioTests(TestCase):
    """A porta de áudio das Manutenções e quem pode passar por ela."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)

        self.operacional = criar_pessoa("operacional@exemplo.test", "Operação")
        criar_vinculo(self.operacional, self.condominio, papel=Papel.OPERACIONAL)

        self.client.force_login(self.gestao)

    def _manutencao(self, responsavel=None):
        return criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Manutenção de teste",
            responsavel=responsavel,
        )

    def _ditar(self, manutencao=None, **extra):
        dados = {}
        if manutencao is not None:
            dados["manutencao"] = str(manutencao.pk)
        else:
            dados["condominio"] = str(self.condominio.pk)
        dados["audio"] = extra.pop("audio", recado())
        dados.update(extra)
        return self.client.post(reverse("manutencoes:transcrever"), dados)

    # --- quem pode ditar ---------------------------------------------------

    def test_quem_nao_esta_logado_nao_dita(self):
        self.client.logout()

        resposta = self._ditar(self._manutencao(responsavel=self.operacional))

        self.assertEqual(resposta.status_code, 302)

    def test_quem_registra_manutencao_tambem_dita(self):
        """A gestão escreve o texto desta manutenção, e por isso pode ditá-lo."""
        manutencao = self._manutencao()

        with patch("apps.core.audio.urllib.request.urlopen") as urlopen:
            urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))
            with override_settings(**CONFIGURADO):
                resposta = self._ditar(manutencao)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"texto": SENTENCA})

    def test_o_operacional_que_executa_a_manutencao_transcreve(self):
        """O responsável pela execução escreve o que foi feito — e pode ditar."""
        manutencao = self._manutencao(responsavel=self.operacional)
        self.client.force_login(self.operacional)

        with patch("apps.core.audio.urllib.request.urlopen") as urlopen:
            urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))
            with override_settings(**CONFIGURADO):
                resposta = self._ditar(manutencao)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"texto": SENTENCA})

    def test_a_resposta_nao_traz_o_sinal_de_avancar(self):
        """Só a transcrição de Demandas diz se a fala basta para seguir."""
        manutencao = self._manutencao(responsavel=self.operacional)
        self.client.force_login(self.operacional)

        with patch("apps.core.audio.urllib.request.urlopen") as urlopen:
            urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))
            with override_settings(**CONFIGURADO):
                resposta = self._ditar(manutencao)

        self.assertNotIn("avancar", resposta.json())

    # --- quem não pode ditar ------------------------------------------------

    def test_sem_permissao_recebe_404(self):
        """Sem vínculo que enxergue o módulo, o registro não existe para a pessoa."""
        manutencao = self._manutencao(responsavel=self.operacional)
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)

        resposta = self._ditar(manutencao)

        self.assertEqual(resposta.status_code, 404)

    def test_operacional_que_nao_e_responsavel_recebe_404(self):
        """Enxerga a manutenção, mas não escreve nela: a porta não se abre."""
        manutencao = self._manutencao()  # sem responsável
        self.client.force_login(self.operacional)

        resposta = self._ditar(manutencao)

        self.assertEqual(resposta.status_code, 404)

    def test_manutencao_de_outro_condominio_recebe_404(self):
        """O condomínio sai do próprio registro; o de fora não existe."""
        outro = criar_condominio("Beta")
        alheia = criar_manutencao(
            condominio=outro,
            criado_por=self.gestao,
            titulo="Manutenção de outro condomínio",
        )

        resposta = self._ditar(alheia)

        self.assertEqual(resposta.status_code, 404)

    def test_o_operacional_nao_dita_para_uma_manutencao_nova(self):
        """Registrar manutenção não é do operacional: a porta de áudio também não."""
        self.client.force_login(self.operacional)

        resposta = self._ditar(manutencao=None)

        self.assertEqual(resposta.status_code, 404)

    def test_condominio_forjado_na_criacao_e_recusado(self):
        """Ditar em nome de um condomínio sem permissão não passa."""
        proibido = criar_condominio("Sem vínculo")

        resposta = self._ditar(manutencao=None, condominio=str(proibido.pk))

        self.assertEqual(resposta.status_code, 404)

    # --- o serviço é chamado só onde deve ------------------------------------

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_sem_arquivo_e_recusado_antes_do_servico(self, urlopen):
        self.client.force_login(self.operacional)
        manutencao = self._manutencao(responsavel=self.operacional)

        resposta = self._ditar(manutencao, audio=SimpleUploadedFile("vazio", b"", "audio/webm"))

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("mensagem", resposta.json())
        urlopen.assert_not_called()


class TelaDaManutencaoTests(TestCase):
    """O microfone do detalhe aponta para a porta de áudio das Manutenções."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.operacional = criar_pessoa("operacional@exemplo.test", "Operação")
        criar_vinculo(self.operacional, self.condominio, papel=Papel.OPERACIONAL)
        self.manutencao = criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Manutenção de teste",
            responsavel=self.operacional,
        )

    @override_settings(**CONFIGURADO)
    def test_o_operacional_ve_o_microfone_apontando_para_o_proprio_endpoint(self):
        self.client.force_login(self.operacional)

        resposta = self.client.get(
            reverse("manutencoes:detalhe", args=[self.manutencao.pk])
        )
        conteudo = resposta.content.decode()

        self.assertTrue(resposta.context["voz_disponivel"])
        self.assertIn(reverse("manutencoes:transcrever"), conteudo)
        self.assertIn(f"manutencao={self.manutencao.pk}", conteudo)
        self.assertNotIn(reverse("demandas:transcrever"), conteudo)
