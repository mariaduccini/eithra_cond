"""A janela de adicionar evidência: a mesma família das outras janelas da demanda.

Anexar uma prova é uma ação curta — escolher o arquivo, conferir o que se aceita
e guardar —, e a janela que a recolhe é só composição sobre o que já existia: o
mesmo campo de arquivo, o mesmo endereço, o mesmo envio de várias provas de uma
vez. Estes testes travam esse contrato: a janela abre larga e baixa, com o corpo
rolando em vez da página, o campo de arquivo intacto — ``multiple`` e ``accept``
como o domínio os define — e nada do que a operação faz hoje foi perdido.
"""
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.demandas.evidencias import ACEITE_DE_EVIDENCIA
from apps.demandas.services import criar_demanda
from apps.demandas.tests.apoio import (
    arquivo_qualquer,
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)


class ModalDeEvidenciaTests(TestCase):
    """A janela de evidência de uma demanda comum, aberta pela gestão."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Fernanda")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)
        self.demanda = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Elevador parado no oitavo andar",
        )

    # -- atalhos ---------------------------------------------------------------

    def _abrir(self):
        resposta = self.client.get(
            reverse("demandas:detalhe", args=[self.demanda.pk]),
            {"abrir": "evidencia", "aba": "historico"},
        )
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def _janela(self, resposta=None):
        """O trecho da janela, recortado do resto da página."""
        html = (resposta or self._abrir()).content.decode()
        inicio = html.index('id="adicionar-evidencia"')
        return html[inicio : html.index("</form>", inicio)]

    # -- a janela continua sendo a mesma porta ---------------------------------

    def test_a_janela_abre_pela_mesma_acao(self):
        """`?abrir=evidencia` devolve a tela com a janela já aberta."""
        resposta = self._abrir()
        self.assertEqual(resposta.context["modal_aberto"], "evidencia")
        self.assertContains(resposta, 'id="adicionar-evidencia"')
        self.assertContains(resposta, "eithra-modal-reaberto")

    def test_o_formulario_posta_no_mesmo_endereco(self):
        """O mesmo envio de várias provas, com arquivos, como antes."""
        janela = self._janela()
        destino = reverse("demandas:adicionar_evidencia", args=[self.demanda.pk])
        self.assertIn(f'action="{destino}?aba=historico"', janela)
        self.assertIn('method="post"', janela)
        self.assertIn('enctype="multipart/form-data"', janela)
        self.assertIn("csrfmiddlewaretoken", janela)

    # -- composição: larga, baixa e rolável ------------------------------------

    def test_a_janela_e_larga_e_o_corpo_e_que_rola(self):
        janela = self._janela()
        self.assertIn("modal-xl", janela)
        self.assertIn("modal-dialog-scrollable", janela)
        self.assertIn("eithra-modal-operacional", janela)

    def test_o_cabecalho_identifica_a_demanda_em_uma_linha(self):
        resposta = self._abrir()
        janela = self._janela(resposta)
        self.assertIn("Adicionar evidência", janela)
        self.assertIn(resposta.context["demanda"]["referencia"], janela)
        self.assertIn("eithra-modal-identidade", janela)

    # -- o campo de arquivo continua o mesmo ------------------------------------

    def test_o_campo_de_arquivo_continua_presente(self):
        """Preservado: o mesmo nome, o mesmo tipo, a mesma escolha de vários."""
        janela = self._janela()
        self.assertIn('name="evidencias"', janela)
        self.assertIn('type="file"', janela)
        self.assertIn("multiple", janela)

    def test_o_campo_oferece_apenas_os_formatos_que_o_dominio_aceita(self):
        """O `accept` do navegador é o mesmo que a borda confere — nada mudou."""
        janela = self._janela()
        self.assertIn(f'accept="{ACEITE_DE_EVIDENCIA}"', janela)

    def test_nao_ha_margem_repetida_em_cada_bloco(self):
        """O respiro vem do corpo da janela, não de margem embaixo de cada peça."""
        janela = self._janela()
        self.assertNotIn("mb-6", janela)
        self.assertIn("mb-0", janela)

    # -- footer: uma ação principal --------------------------------------------

    def test_o_rodape_tem_uma_acao_principal(self):
        janela = self._janela()
        self.assertIn(">Cancelar<", janela)
        self.assertIn(">Anexar<", janela)
        self.assertEqual(janela.count("btn btn-primary"), 1)

    # -- erros continuam onde estavam ------------------------------------------

    def test_o_erro_mantem_a_janela_aberta(self):
        """Arquivo recusado volta com a janela aberta e a recusa no campo."""
        resposta = self.client.post(
            reverse("demandas:adicionar_evidencia", args=[self.demanda.pk]),
            {"evidencias": [arquivo_qualquer()]},
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(resposta.context["modal_aberto"], "evidencia")

        janela = self._janela(resposta)
        self.assertIn("data-eithra-erro-de-campo", janela)

    # -- a composição usa a regra das janelas operacionais ---------------------

    def test_a_composicao_usa_a_regra_das_janelas_operacionais(self):
        css = Path(settings.BASE_DIR, "static", "css", "eithra.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".eithra-modal-operacional .modal-body", css)
        self.assertIn(".eithra-modal-operacional .modal-footer", css)
