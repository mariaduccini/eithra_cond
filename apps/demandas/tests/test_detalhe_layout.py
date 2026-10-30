"""Apresentação da Visão Geral da demanda: hierarquia operacional e densidade.

A Visão Geral responde rápido a quatro perguntas: o que aconteceu, qual a
situação, quem cuida e qual é o próximo passo. Ela é só composição — duas zonas
em tela larga, uma só em tela estreita — sobre os mesmos dados e as mesmas ações
que a tela já oferecia. Estes testes travam esse contrato: as abas continuam, a
descrição continua, as informações essenciais continuam em grade, o
acompanhamento vazio é curto, as ações continuam disponíveis quando permitidas e
nenhum caminho funcional foi removido.
"""
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from apps.cadastros.constants import TipoLocal
from apps.condominios.constants import Papel
from apps.demandas.services import atualizar_acompanhamento, criar_demanda
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_local,
    criar_pessoa,
    criar_vinculo,
)


class VisaoGeralDaDemandaTests(TestCase):
    """A Visão Geral de uma demanda comum, lida pela gestão do condomínio."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Fernanda")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)
        self.local = criar_local(
            self.condominio, "Casa de máquinas", tipo=TipoLocal.CASA_DE_MAQUINAS
        )
        self.demanda = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Bomba de recalque com vazamento",
            descricao="Vazamento visível junto da base da bomba.",
            local=self.local,
        )

    def _abrir(self, demanda=None, **parametros):
        alvo = demanda or self.demanda
        resposta = self.client.get(
            reverse("demandas:detalhe", args=[alvo.pk]), parametros
        )
        self.assertEqual(resposta.status_code, 200)
        return resposta

    # -- Estrutura da tela ----------------------------------------------------

    def test_as_abas_continuam_na_tela(self):
        conteudo = self._abrir().content.decode()
        for aba in ("Visão geral", "Histórico", "Anexos", "Relacionadas"):
            self.assertIn(aba, conteudo)

    def test_a_visao_geral_se_divide_em_duas_zonas(self):
        """A leitura tem uma zona principal e uma operacional, nesta ordem."""
        conteudo = self._abrir().content.decode()
        self.assertIn("eithra-vg-principal", conteudo)
        self.assertIn("eithra-vg-operacional", conteudo)
        self.assertLess(
            conteudo.index("eithra-vg-principal"),
            conteudo.index("eithra-vg-operacional"),
        )

    def test_a_descricao_continua_renderizada(self):
        conteudo = self._abrir().content.decode()
        self.assertIn("Descrição", conteudo)
        self.assertIn("Vazamento visível junto da base da bomba.", conteudo)

    def test_as_informacoes_essenciais_continuam(self):
        conteudo = self._abrir().content.decode()
        for rotulo in ("Local", "Responsável", "Prazo", "Registrado por", "Data de registro"):
            self.assertIn(rotulo, conteudo)
        self.assertIn("Casa de máquinas", conteudo)

    def test_as_informacoes_sao_uma_grade_de_pares(self):
        """Deixaram de ser uma lista vertical: são pares em grade compacta."""
        resposta = self._abrir()
        self.assertContains(resposta, 'class="eithra-vg-info"')
        self.assertContains(resposta, "eithra-vg-par")

    # -- Acompanhamento -------------------------------------------------------

    def test_sem_acompanhamento_a_mensagem_e_curta(self):
        conteudo = self._abrir().content.decode()
        self.assertIn("Nenhum acompanhamento definido.", conteudo)
        self.assertNotIn("Nada em acompanhamento", conteudo)

    def test_com_acompanhamento_so_o_preenchido_aparece(self):
        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            proxima_acao="Confirmar a visita técnica",
        )
        conteudo = self._abrir().content.decode()
        self.assertIn("Próxima ação", conteudo)
        self.assertIn("Confirmar a visita técnica", conteudo)
        self.assertNotIn("Nenhum acompanhamento definido.", conteudo)

    # -- Próxima ação e ações -------------------------------------------------

    def test_a_proxima_acao_vive_na_zona_operacional(self):
        """O próximo passo e os botões ficam juntos, na coluna da direita."""
        conteudo = self._abrir().content.decode()
        operacional = conteudo[conteudo.index("eithra-vg-operacional"):]
        for marca in (
            "Próxima ação",
            "Registrar atualização",
            "Adicionar evidência",
            "Concluir demanda",
            "Mais ações",
        ):
            self.assertIn(marca, operacional)

    def test_nenhum_caminho_funcional_foi_removido(self):
        conteudo = self._abrir().content.decode()
        detalhe = reverse("demandas:detalhe", args=[self.demanda.pk])
        # Voltar para a lista e trocar de aba continuam valendo.
        self.assertIn(reverse("demandas:listagem"), conteudo)
        self.assertIn(f"{detalhe}?aba=historico", conteudo)
        # As ações contextuais apontam para as janelas que a tela já oferecia.
        self.assertIn("?abrir=atualizar", conteudo)
        self.assertIn("?abrir=evidencia", conteudo)
        self.assertIn("?abrir=acompanhamento", conteudo)

    def test_o_editar_continua_alcancavel_pelas_mais_acoes(self):
        """O lápis solto saiu da descrição; editar vive nas Mais ações."""
        conteudo = self._abrir().content.decode()
        self.assertIn("Mais ações", conteudo)
        self.assertIn("Editar demanda", conteudo)
        self.assertIn("?abrir=editar", conteudo)

    # -- Responsividade -------------------------------------------------------

    def test_o_layout_tem_regra_responsiva(self):
        """Em tela estreita as duas zonas viram uma só — a regra existe no CSS."""
        css = Path(settings.BASE_DIR, "static", "css", "eithra.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".eithra-vg {", css)
        self.assertIn("min-width: 992px", css)
        # Em uma coluna só, a ordem é a da leitura operacional, não a do HTML.
        self.assertIn("max-width: 991.98px", css)
        self.assertIn("display: contents", css)
        self.assertIn(".eithra-vg-bloco--acompanhamento", css)
        self.assertIn("order: 2", css)

    def test_em_uma_coluna_o_detalhamento_vem_por_ultimo(self):
        """Os blocos carregam a marca que o CSS usa para reordenar no celular."""
        conteudo = self._abrir().content.decode()
        for marca in (
            "eithra-vg-bloco--descricao",
            "eithra-vg-bloco--acompanhamento",
            "eithra-vg-bloco--acao",
            "eithra-vg-bloco--informacoes",
        ):
            self.assertIn(marca, conteudo)
