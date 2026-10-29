"""O ciclo de estado do wizard de registro: registrar é uma sessão efêmera.

O registro de uma demanda não é um formulário longo que se preenche devagar: é
uma sessão curta. Cada etapa que aparece na tela é a resposta do envio anterior
— o servidor devolve pronto o passo seguinte, com o relato e o que a leitura
dele sustentou. Não existe rascunho guardado em lugar nenhum: nem em sessão, nem
no endereço, nem no navegador.

É justamente por isso que fechar precisa descartar. Enquanto a resposta do envio
fica na frente, o passo seguinte continua ali — fechar só o esconde, e a próxima
abertura devolveria a mesma etapa. O que este arquivo prende é que fechar
descarta, que a leitura seguinte começa sempre na primeira etapa vazia e que a
confirmação final continua registrando normalmente.
"""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.demandas.models import Demanda, HistoricoDemanda
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)

RELATO = "O portão externo está fazendo barulho quando abre."


class BaseDoWizard(TestCase):
    """Uma pessoa de gestão ligada a um condomínio, pronta para registrar."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    def _descricao(self, **extra):
        dados = {
            "condominio": str(self.condominio.pk),
            "relato": RELATO,
            "wizard": "descricao",
        }
        dados.update(extra)
        return self.client.post(reverse("demandas:criar"), dados)

    def _informacoes(self, **extra):
        dados = {
            "condominio": str(self.condominio.pk),
            "relato": RELATO,
            "titulo": RELATO,
            "wizard": "informacoes",
        }
        dados.update(extra)
        return self.client.post(reverse("demandas:criar"), dados)

    def _confirmar(self, **extra):
        dados = {
            "condominio": str(self.condominio.pk),
            "relato": RELATO,
            "titulo": RELATO,
        }
        dados.update(extra)
        return self.client.post(reverse("demandas:criar"), dados)

    # O que a tela mostra depois de fechar: a leitura limpa da mesma página.
    def _leitura_limpa(self):
        return self.client.get(reverse("demandas:listagem"))


class LeituraLimpaTests(BaseDoWizard):
    """Fechar, reabrir ou recarregar chega sempre na primeira etapa, vazia."""

    def test_a_leitura_da_tela_abre_na_primeira_etapa(self):
        resposta = self._leitura_limpa()

        self.assertEqual(resposta.status_code, 200)
        self.assertIsNone(resposta.context["modal_aberto"])
        self.assertEqual(resposta.context["registro_etapa"], "descricao")
        self.assertIsNone(resposta.context["sugestoes"])
        self.assertIsNone(resposta.context["revisao"])
        self.assertContains(resposta, "Descreva o que aconteceu")

    def test_a_leitura_da_tela_nao_traz_relato_nem_descarte(self):
        conteudo = self._leitura_limpa().content.decode()

        # O modal existe, fechado, e é o formulário da primeira etapa: nada de
        # "voltar para algum lugar", porque não há passo anterior.
        self.assertNotIn("eithra-modal-reaberto", conteudo)
        self.assertNotIn("data-eithra-descartar", conteudo)
        self.assertNotIn("Informações identificadas", conteudo)
        self.assertNotIn("Revisar e registrar", conteudo)

    def test_o_relato_do_envio_anterior_nao_volta_na_leitura(self):
        self._descricao()

        conteudo = self._leitura_limpa().content.decode()

        self.assertNotIn(RELATO, conteudo)

    def test_a_leitura_limpa_e_o_que_fechar_devolve(self):
        """Fechar leva para o endereço de leitura: é o que o modal anuncia."""
        aberto = self._descricao().content.decode()

        self.assertIn(
            f'data-eithra-descartar="{reverse("demandas:listagem")}"', aberto
        )


class PassoDevolvidoTests(BaseDoWizard):
    """O passo que o servidor devolve é a resposta do envio — não um rascunho."""

    def test_a_primeira_etapa_nao_registra_e_devolve_a_segunda(self):
        resposta = self._descricao()

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(HistoricoDemanda.objects.count(), 0)
        self.assertEqual(resposta.context["modal_aberto"], "criar")
        self.assertEqual(resposta.context["registro_etapa"], "informacoes")

    def test_o_passo_devolvido_vem_aberto_e_marcado_para_descarte(self):
        conteudo = self._descricao().content.decode()

        self.assertIn("eithra-modal-reaberto", conteudo)
        self.assertIn("data-eithra-descartar", conteudo)
        self.assertIn('aria-hidden="false"', conteudo)

    def test_o_passo_devolvido_carrega_o_relato_no_proprio_formulario(self):
        """O relato viaja em campo oculto: é assim que ele segue para a frente."""
        conteudo = self._descricao().content.decode()

        self.assertIn('name="relato"', conteudo)

    def test_o_registro_nao_guarda_rascunho_em_sessao(self):
        antes = set(self.client.session.keys())

        self._descricao()
        self._informacoes()

        self.assertEqual(set(self.client.session.keys()), antes)

    def test_o_relato_vazio_volta_para_a_primeira_etapa(self):
        resposta = self.client.post(
            reverse("demandas:criar"),
            {
                "condominio": str(self.condominio.pk),
                "relato": "",
                "wizard": "descricao",
            },
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(resposta.context["registro_etapa"], "descricao")
        self.assertEqual(Demanda.objects.count(), 0)


class DescartarNaoRegistraTests(BaseDoWizard):
    """Descartar não cria nada: nem demanda, nem histórico."""

    def test_fechar_na_segunda_etapa_nao_cria_demanda(self):
        self._descricao()

        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(HistoricoDemanda.objects.count(), 0)

    def test_fechar_na_terceira_etapa_nao_cria_demanda(self):
        self._informacoes()

        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(HistoricoDemanda.objects.count(), 0)

    def test_o_passo_seguinte_da_leitura_limpa_e_a_primeira_etapa_vazia(self):
        self._descricao()
        self._informacoes()

        resposta = self._leitura_limpa()

        self.assertEqual(resposta.context["registro_etapa"], "descricao")
        self.assertIsNone(resposta.context["revisao"])
        self.assertEqual(
            resposta.context["formulario_demanda"].initial.get("relato", ""), ""
        )


class ConfirmacaoFinalTests(BaseDoWizard):
    """Confirmar é diferente de descartar: aqui a demanda nasce de verdade."""

    def test_a_confirmacao_final_continua_registrando_a_demanda(self):
        resposta = self._confirmar()

        self.assertEqual(resposta.status_code, 302)
        demanda = Demanda.objects.get()
        self.assertEqual(demanda.descricao, RELATO)
        self.assertEqual(demanda.condominio, self.condominio)
        self.assertTrue(demanda.historico.exists())


class ContratosDoFluxoTests(SimpleTestCase):
    """Nenhuma regra deste ciclo vive no JavaScript: ele só reage ao servidor."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.layout = (
            Path(settings.BASE_DIR) / "templates" / "layouts" / "app.html"
        ).read_text(encoding="utf-8")
        cls.listagem = (
            Path(settings.BASE_DIR)
            / "templates"
            / "pages"
            / "demandas"
            / "listagem.html"
        ).read_text(encoding="utf-8")
        cls.ditar = (
            Path(settings.BASE_DIR) / "static" / "js" / "eithra-ditar.js"
        ).read_text(encoding="utf-8")

    def test_fechar_com_fechamento_centralizado_e_um_caminho_so(self):
        """Cancelar e o X são o mesmo gesto: `data-bs-dismiss="modal"`."""
        self.assertEqual(
            self.listagem.count('data-bs-dismiss="modal"'), 4
        )
        self.assertNotIn("onclick", self.listagem)
        self.assertEqual(
            self.layout.count('document.addEventListener("click", function (event)'),
            1,
        )

    def test_o_descarte_esta_declarado_na_propria_etapa(self):
        self.assertIn("data-eithra-descartar", self.listagem)
        self.assertIn("descartar_url", self.listagem)

    def test_quem_fecha_devolve_a_tela_para_a_leitura(self):
        self.assertIn("dataset.eithraDescartar", self.layout)
        self.assertIn("window.location.assign(destino)", self.layout)

    def test_a_barra_de_endereco_nao_guarda_o_envio(self):
        """Recarregar não pode repetir o envio do formulário."""
        self.assertIn("window.history.replaceState", self.layout)

    def test_o_ditar_e_avisado_antes_de_a_pagina_sair(self):
        self.assertIn('new Event("eithra:descartar")', self.layout)
        self.assertIn(
            'document.addEventListener("eithra:descartar", descartar)', self.ditar
        )

    def test_o_descarte_encerra_a_gravacao_e_libera_o_microfone(self):
        trecho = self.ditar.split("function descartar()", 1)[1].split(
            "\n    }", 1
        )[0]

        self.assertIn("liberarMicrofone()", trecho)
        self.assertIn('ativo.state === "recording"', trecho)
        self.assertIn("ativo.stop()", trecho)

    def test_o_audio_pela_metade_nao_vira_transcricao(self):
        self.assertIn("if (descartando)", self.ditar)
        self.assertLess(
            self.ditar.index("if (descartando)"),
            self.ditar.index("transcrever(new Blob(pedacos"),
        )

    def test_o_descarte_nao_mexe_no_texto_que_a_pessoa_escreveu(self):
        trecho = self.ditar.split("function descartar()", 1)[1].split(
            "\n    }", 1
        )[0]

        self.assertNotIn("campo.value", trecho)

    def test_o_navegador_nao_guarda_o_rascunho(self):
        self.assertNotIn("localStorage", self.ditar + self.layout)
        self.assertNotIn("sessionStorage", self.ditar + self.layout)
