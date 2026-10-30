"""A janela de registrar atualização: mais larga, mais baixa, o mesmo fluxo.

A atualização é o relato do que aconteceu com a demanda, e a janela que o
recolhe é só composição sobre o que já existia: os mesmos campos, o mesmo
endereço, as mesmas duas etapas — relato e conclusão —, o mesmo envio único que
grava situação, histórico e provas na mesma transação. Estes testes travam esse
contrato: a janela abre larga e baixa, com o relato como campo principal, o
corpo rolando em vez da página, e nada do que a operação faz hoje foi perdido.
"""
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.demandas.services import LIMITE_DA_OBSERVACAO, criar_demanda
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)
from apps.demandas.tests.test_audio import CONFIGURADO

RELATO = "O elevador voltou a operar depois da troca do cabo de tração."


class ModalDeAtualizacaoTests(TestCase):
    """A janela de atualização de uma demanda comum, aberta pela gestão."""

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
            {"abrir": "atualizar"},
        )
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def _janela(self, resposta=None):
        """O trecho da janela, recortado do resto da página."""
        html = (resposta or self._abrir()).content.decode()
        inicio = html.index('id="atualizar-demanda"')
        return html[inicio : html.index("</form>", inicio)]

    def _relato(self, **dados):
        """O primeiro passo do fluxo, como o botão Continuar o envia."""
        return self.client.post(
            reverse("demandas:atualizar", args=[self.demanda.pk]),
            {"etapa_atualizacao": "informacoes", **dados},
        )

    # -- a janela continua sendo a mesma porta ---------------------------------

    def test_a_janela_abre_pela_mesma_acao(self):
        """`?abrir=atualizar` devolve a tela com a janela já aberta."""
        resposta = self._abrir()
        self.assertEqual(resposta.context["modal_aberto"], "atualizar")
        self.assertContains(resposta, 'id="atualizar-demanda"')
        self.assertContains(resposta, "eithra-modal-reaberto")

    def test_o_formulario_posta_no_mesmo_endereco(self):
        """Um envio só: arquivos e relato viajam juntos, como antes."""
        janela = self._janela()
        destino = reverse("demandas:atualizar", args=[self.demanda.pk])
        self.assertIn(f'action="{destino}?aba=visao_geral"', janela)
        self.assertIn('method="post"', janela)
        self.assertIn('enctype="multipart/form-data"', janela)

    # -- composição: larga, baixa e rolável ------------------------------------

    def test_a_janela_e_larga_e_o_corpo_e_que_rola(self):
        janela = self._janela()
        self.assertIn("modal-xl", janela)
        self.assertIn("modal-dialog-scrollable", janela)
        self.assertIn("eithra-modal-operacional", janela)

    def test_o_cabecalho_identifica_a_demanda_em_uma_linha(self):
        resposta = self._abrir()
        janela = self._janela(resposta)
        self.assertIn("Registrar atualização", janela)
        self.assertIn(resposta.context["demanda"]["referencia"], janela)
        self.assertIn("eithra-modal-identidade", janela)
        # As duas etapas do fluxo continuam anunciadas no cabeçalho.
        self.assertIn("Informações", janela)
        self.assertIn("Conclusão", janela)

    def test_o_relato_e_o_campo_principal(self):
        """Ele abre a leitura, ocupa a linha inteira e vem antes do resto."""
        janela = self._janela()
        self.assertIn('name="texto"', janela)
        self.assertIn("<textarea", janela)
        self.assertLess(janela.index('name="texto"'), janela.index('name="acao"'))
        self.assertIn('<div class="col-12">', janela)

    def test_os_complementos_se_leem_ao_lado(self):
        """O que aconteceu com a demanda e o aviso das provas dividem a linha."""
        janela = self._janela()
        self.assertIn("row g-3", janela)
        self.assertEqual(janela.count("col-12 col-lg-6"), 2)
        # O respiro vem do vão da grade, não de margem embaixo de cada bloco:
        # o relato, a ação e o aviso das provas fecham com `mb-0`.
        self.assertNotIn("mb-6", janela)
        self.assertEqual(janela.count("mb-0"), 3)

    def test_o_rodape_tem_uma_acao_principal(self):
        janela = self._janela()
        self.assertIn(">Cancelar<", janela)
        self.assertIn(">Continuar<", janela)
        self.assertEqual(janela.count("btn btn-primary"), 1)

    # -- nada do que a operação faz hoje foi perdido ---------------------------

    def test_os_campos_do_fluxo_continuam_na_janela(self):
        janela = self._janela()
        for campo in ("acao", "texto", "etapa_atualizacao"):
            self.assertIn(f'name="{campo}"', janela)
        self.assertIn("csrfmiddlewaretoken", janela)

    def test_a_etapa_de_conclusao_continua_igual(self):
        """O segundo passo mostra a leitura, a consequência e as provas."""
        resposta = self._relato(texto=RELATO)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["etapa_atualizacao"], "conclusao")

        janela = self._janela(resposta)
        self.assertIn("O EITHRA identificou", janela)
        self.assertIn('name="confirmacao"', janela)
        self.assertIn('name="evidencias"', janela)
        self.assertIn('value="corrigir"', janela)
        self.assertIn('value="conclusao"', janela)
        # No segundo passo a ação principal é Confirmar; Cancelar e Corrigir
        # continuam secundárias.
        self.assertEqual(janela.count("btn btn-primary"), 1)

    @override_settings(**CONFIGURADO)
    def test_o_relato_continua_podendo_ser_ditado(self):
        """Falar é o mesmo campo escrito de outro jeito — nada mudou nesse fluxo."""
        janela = self._janela()
        self.assertIn("data-eithra-ditar", janela)
        self.assertIn('data-eithra-ditar-campo="#id_texto"', janela)
        self.assertIn("data-eithra-ditar-recado", janela)

    # -- erros continuam onde estavam ------------------------------------------

    def test_o_erro_mantem_a_janela_aberta_e_o_relato_escrito(self):
        resposta = self._relato(texto="A" * (LIMITE_DA_OBSERVACAO + 1))

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(resposta.context["modal_aberto"], "atualizar")
        self.assertEqual(resposta.context["etapa_atualizacao"], "informacoes")

        janela = self._janela(resposta)
        self.assertIn("data-eithra-erro-de-campo", janela)

    # -- responsividade --------------------------------------------------------

    def test_a_composicao_usa_a_regra_das_janelas_operacionais(self):
        css = Path(settings.BASE_DIR, "static", "css", "eithra.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".eithra-modal-operacional .modal-body", css)
        self.assertIn(".eithra-modal-operacional .modal-footer", css)
