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
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
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
    def test_audio_valido_devolve_o_texto_e_o_sinal_de_seguir(self, urlopen):
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))

        resposta = self._ditar(recado())

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"texto": SENTENCA, "avancar": True})
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

    # --- a tela do registro: o microfone mora no campo ----------------------

    @override_settings(**CONFIGURADO)
    def test_a_etapa_da_descricao_oferece_o_microfone(self):
        resposta = self.client.get(reverse("demandas:listagem"))

        self.assertTrue(resposta.context["voz_disponivel"])
        self.assertContains(resposta, "data-eithra-ditar-url")
        self.assertContains(resposta, reverse("demandas:transcrever"))

    @override_settings(**CONFIGURADO)
    def test_o_microfone_esta_dentro_do_campo_do_relato(self):
        """Falar não é ação do rodapé: é um jeito de preencher o relato."""
        conteudo = self.client.get(reverse("demandas:listagem")).content.decode()

        inicio = conteudo.index('<div class="eithra-campo-voz">')
        fim = conteudo.index("</div>", inicio)

        self.assertIn("data-eithra-ditar", conteudo[inicio:fim])
        self.assertIn('data-eithra-ditar-campo="#id_relato"', conteudo)

    @override_settings(**CONFIGURADO)
    def test_o_rodape_do_percurso_nao_tem_mais_o_botao_falar(self):
        """O rodapé é Cancelar e Continuar — voz não é passo do formulário."""
        conteudo = self.client.get(reverse("demandas:listagem")).content.decode()

        self.assertNotIn(">Falar<", conteudo)
        self.assertIn(">Cancelar<", conteudo)
        self.assertIn(">Continuar<", conteudo)

    @override_settings(**CONFIGURADO)
    def test_o_microfone_tem_rotulo_proprio_e_icone(self):
        """Quem chega de leitor de tela sabe o que o botão faz."""
        resposta = self.client.get(reverse("demandas:listagem"))

        self.assertContains(resposta, 'aria-label="Gravar relato por voz"')
        self.assertContains(resposta, "bx bx-microphone")

    @override_settings(**CONFIGURADO)
    def test_o_campo_do_relato_continua_editavel(self):
        """A transcrição é ponto de partida: o texto é corrigível até Continuar."""
        campo = self.client.get(reverse("demandas:listagem")).context[
            "formulario_demanda"
        ]["relato"]
        html = str(campo)

        self.assertIn("<textarea", html)
        self.assertNotIn("readonly", html)
        self.assertNotIn("disabled", html)

    @override_settings(**SEM_VOZ)
    def test_sem_servico_a_tela_nao_oferece_microfone(self):
        resposta = self.client.get(reverse("demandas:listagem"))
        conteudo = resposta.content.decode()

        self.assertFalse(resposta.context["voz_disponivel"])
        self.assertNotContains(resposta, "data-eithra-ditar-url")
        self.assertNotContains(resposta, "data-eithra-campo-voz")
        self.assertNotContains(resposta, "js/eithra-ditar.js")

    # --- um erro por vez: o áudio fala pelo campo ----------------------------

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_falha_do_audio_nao_dispara_o_erro_de_relato(self, urlopen):
        """A falha de transcrição responde por si; a cobrança de relato é do envio."""
        urlopen.side_effect = OSError("rede fora")

        resposta = self._ditar(recado())
        conteudo = resposta.content.decode()

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["mensagem"], audio.MENSAGEM_FALHA)
        self.assertNotIn("texto", resposta.json())
        self.assertNotIn("Escreva o que aconteceu para registrar a demanda.", conteudo)
        self.assertEqual(Demanda.objects.count(), 0)

    def test_continuar_sem_relato_dispara_a_validacao_normal(self):
        """Sem texto e sem fala, quem cobra é o próprio formulário — como sempre."""
        resposta = self._relatar("")

        self.assertEqual(resposta.status_code, 400)
        self.assertContains(
            resposta,
            "Escreva o que aconteceu para registrar a demanda.",
            status_code=400,
        )
        self.assertEqual(Demanda.objects.count(), 0)

    # --- o formato que o navegador realmente produz ---------------------------

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_o_formato_do_navegador_e_aceito(self, urlopen):
        """O Chrome grava ``audio/webm;codecs=opus`` em ``relato.webm``."""
        urlopen.return_value = RespostaFalsa(corpo_json({"text": SENTENCA}))

        resposta = self._ditar(
            recado(tipo="audio/webm;codecs=opus", nome="relato.webm")
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"texto": SENTENCA, "avancar": True})
        urlopen.assert_called_once()


class ControladorDeVozTests(SimpleTestCase):
    """O contrato do controlador de voz, preso no código que a tela baixa.

    Sem um executor de JavaScript no projeto, a garantia de que a fala
    acrescenta em vez de substituir — e de que falhar não apaga nada — vive no
    próprio controlador. Estes testes leem o arquivo servido e prendem os
    compromissos que o núcleo não pode cobrir sozinho.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fonte = (
            Path(settings.BASE_DIR) / "static" / "js" / "eithra-ditar.js"
        ).read_text(encoding="utf-8")

    def test_a_fala_acrescenta_em_vez_de_substituir(self):
        self.assertIn("alvo.value = juntar(alvo.value, leitura.texto)", self.fonte)
        self.assertIn('return atual ? atual + "\\n" + novo : novo', self.fonte)
        self.assertNotIn(".value = leitura.texto", self.fonte)

    def test_falhar_nao_toca_no_texto_existente(self):
        """Só o sucesso escreve no campo: o resto do caminho não mexe nele."""
        self.assertEqual(self.fonte.count(".value = juntar("), 1)

    def test_a_gravacao_comeca_e_termina_pelo_mesmo_controle(self):
        self.assertIn("gravador.start()", self.fonte)
        self.assertIn("gravador.stop()", self.fonte)
        self.assertIn("Gravando…", self.fonte)
        self.assertIn("Transcrevendo…", self.fonte)

    def test_o_botao_encontra_o_campo_do_proprio_formulario(self):
        """O campo do microfone tocado, e não o primeiro da página.

        A Portaria tem dois formulários com os mesmos nomes de campo; procurar
        pelo documento inteiro escreveria no campo que está atrás do modal.
        """
        self.assertIn("function acharCampo(botao)", self.fonte)
        self.assertIn('var formulario = botao.closest("form");', self.fonte)
        self.assertIn("formulario.querySelector(seletor)", self.fonte)
        self.assertIn("achado.isConnected ? achado : null", self.fonte)

    def test_o_campo_e_reencontrado_na_hora_de_escrever(self):
        """Alvo fora do documento não recebe a fala: a tela perdeu o campo."""
        self.assertIn("var alvo = acharCampo(botao);", self.fonte)
        self.assertLess(
            self.fonte.index("var alvo = acharCampo(botao);"),
            self.fonte.index("alvo.value = juntar("),
        )
        self.assertIn("avisar(TEXTO_SEM_CAMPO, \"erro\");", self.fonte)

    def test_a_transcricao_acorda_o_campo_como_a_digitacao(self):
        """Quem procura sozinho enquanto se digita — o destino da chegada —
        precisa saber que a fala mudou o campo."""
        self.assertIn(
            'alvo.dispatchEvent(new Event("input", { bubbles: true }));', self.fonte
        )
        self.assertIn(
            'alvo.dispatchEvent(new Event("change", { bubbles: true }));', self.fonte
        )
        depois = self.fonte.index("alvo.value = juntar(")
        entrada = self.fonte.index('alvo.dispatchEvent(new Event("input"')
        mudanca = self.fonte.index('alvo.dispatchEvent(new Event("change"')
        self.assertLess(depois, entrada)
        self.assertLess(entrada, mudanca)

    def test_nao_mostra_dois_erros_ao_mesmo_tempo(self):
        self.assertIn("calarAvisoDoCampo", self.fonte)
        self.assertIn("[data-eithra-erro-de-campo]", self.fonte)

    def test_os_formatos_do_navegador_estao_previstos(self):
        for formato in (
            "audio/webm;codecs=opus",
            "audio/webm",
            "audio/ogg;codecs=opus",
            "audio/ogg",
            "audio/mp4",
        ):
            self.assertIn(f'"{formato}"', self.fonte)

    def test_a_mensagem_de_falha_e_a_da_tela(self):
        self.assertIn(
            "Não foi possível transcrever o áudio. Tente novamente ou digite o relato.",
            self.fonte,
        )

    def test_o_rotulo_de_gravacao_e_humano(self):
        self.assertIn('var ROTULO_PRONTO = "Gravar relato por voz"', self.fonte)
        self.assertIn('var ROTULO_GRAVANDO = "Parar a gravação"', self.fonte)
