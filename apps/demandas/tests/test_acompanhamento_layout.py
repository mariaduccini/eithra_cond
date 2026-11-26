"""A janela do acompanhamento: mais larga, mais baixa, os mesmos campos.

O acompanhamento é uma ação curta — sete campos que se leem dois a dois — e a
janela que o corrige é só composição sobre o que já existia. Estes testes travam
esse contrato: a janela continua abrindo pela mesma ação, com os mesmos campos,
no mesmo endereço, com o corpo rolando em vez da página inteira, e sem inventar
card dentro de card.
"""
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.demandas.forms import AcompanhamentoDaDemandaForm
from apps.demandas.services import criar_demanda
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)


class ModalDeAcompanhamentoTests(TestCase):
    """A janela do acompanhamento de uma demanda comum, aberta pela gestão."""

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

    # -- atalhos --------------------------------------------------------------

    def _abrir(self):
        resposta = self.client.get(
            reverse("demandas:detalhe", args=[self.demanda.pk]),
            {"abrir": "atualizar"},
        )
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def _janela(self, resposta=None):
        """O trecho da janela da atualização, recortado do resto da página."""
        html = (resposta or self._abrir()).content.decode()
        inicio = html.index('id="atualizar-demanda"')
        return html[inicio : html.index("</form>", inicio)]

    # -- a janela continua sendo a mesma porta ---------------------------------

    def test_a_janela_abre_pela_mesma_acao(self):
        """`?abrir=atualizar` devolve a tela com a janela já aberta."""
        resposta = self._abrir()
        self.assertEqual(resposta.context["modal_aberto"], "atualizar")
        self.assertContains(resposta, 'id="atualizar-demanda"')
        self.assertContains(resposta, "eithra-modal-reaberto")

    def test_o_formulario_posta_no_mesmo_endereco(self):
        janela = self._janela()
        destino = reverse("demandas:atualizar", args=[self.demanda.pk])
        self.assertIn(f'action="{destino}?aba=visao_geral"', janela)
        self.assertIn('method="post"', janela)

    # -- composição: larga, baixa e rolável -----------------------------------

    def test_a_janela_e_larga_e_o_corpo_e_que_rola(self):
        janela = self._janela()
        self.assertIn("modal-xl", janela)
        self.assertIn("modal-dialog-scrollable", janela)

    def test_os_campos_se_leem_em_pares(self):
        """Uma coluna no celular, duas no desktop — o par vem do próprio tema."""
        janela = self._janela()
        self.assertIn("row g-3", janela)
        self.assertEqual(janela.count("col-12 col-lg-6"), 8)
        # O respiro entre os campos vem do vão da grade, não de margem em cada um.
        self.assertNotIn("mb-6", janela)
        self.assertEqual(janela.count("mb-0"), 10)

    def test_a_janela_nao_cria_card_interno(self):
        janela = self._janela()
        self.assertNotIn('class="card', janela)
        self.assertNotIn("card-body", janela)

    def test_o_cabecalho_identifica_a_demanda_em_uma_linha(self):
        resposta = self._abrir()
        janela = self._janela(resposta)
        self.assertIn("Registrar atualização", janela)
        self.assertIn(resposta.context["demanda"]["referencia"], janela)

    def test_o_rodape_tem_uma_acao_principal(self):
        janela = self._janela()
        self.assertIn("Cancelar", janela)
        self.assertIn("Continuar", janela)
        self.assertEqual(janela.count('type="submit"'), 1)

    # -- nada foi removido -----------------------------------------------------

    def test_os_campos_do_formulario_continuam_na_janela(self):
        janela = self._janela()
        for campo in AcompanhamentoDaDemandaForm().fields:
            self.assertIn(f'name="{campo}"', janela)

    def test_nenhum_campo_do_formulario_ficou_de_fora(self):
        """O que a janela mostra é exatamente o que o formulário pergunta."""
        janela = self._janela()
        na_janela = {
            campo for campo in AcompanhamentoDaDemandaForm().fields if f'name="{campo}"' in janela
        }
        self.assertEqual(na_janela, set(AcompanhamentoDaDemandaForm().fields))

    # -- erros continuam onde estavam -----------------------------------------

    def test_o_erro_mantem_a_janela_aberta_e_o_valor_digitado(self):
        resposta = self.client.post(
            reverse("demandas:acompanhar", args=[self.demanda.pk]),
            {
                "aguardando_tipo": "",
                "aguardando_descricao": "A" * 201,
                "proxima_acao": "Cobrar o laudo",
                "previsao": "",
                "impedimento": "",
                "responsavel_acompanhamento": "",
            },
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(resposta.context["modal_aberto"], "acompanhamento")
        janela = self._janela(resposta)
        self.assertIn("data-eithra-erro-de-campo", janela)
        self.assertIn("Cobrar o laudo", janela)

    # -- responsividade --------------------------------------------------------

    def test_a_composicao_tem_regra_propria_no_css(self):
        css = Path(settings.BASE_DIR, "static", "css", "eithra.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".eithra-modal-operacional", css)
        self.assertIn(".eithra-modal-identidade", css)
        self.assertIn(".eithra-modal-nota", css)
