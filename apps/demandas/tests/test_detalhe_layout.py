"""Apresentação do detalhe da demanda: uma leitura só, de cima para baixo.

O detalhe deixou de ser um painel de abas e virou seções contínuas: o que
aconteceu, o que qualifica o problema, quem cuida dele, o que já passou e o que
se liga a ele. Quem separa uma seção da outra é o fio discreto entre elas, não
uma superfície com moldura própria. Estes testes travam esse contrato: a leitura
segue nessa ordem, a descrição continua, as informações essenciais continuam em
grade, o acompanhamento vazio é curto, as ações continuam disponíveis no
cabeçalho e nenhum caminho funcional foi removido.
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


class DetalheDaDemandaTests(TestCase):
    """O detalhe de uma demanda comum, lido pela gestão do condomínio."""

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

    def test_as_secoes_da_demanda_aparecem(self):
        """O corpo é uma sequência de seções, não um painel de abas."""
        conteudo = self._abrir().content.decode()
        for titulo in (
            ">Resumo da demanda</h2>",
            ">Informações principais</h2>",
            ">Acompanhamento</h2>",
            ">Histórico</h2>",
        ):
            self.assertIn(titulo, conteudo)
        # Nada de abas: a leitura é uma só, sem alternância de painéis.
        self.assertNotIn('role="tablist"', conteudo)
        self.assertNotIn("Visão geral", conteudo)

    def test_a_leitura_segue_de_cima_para_baixo(self):
        """As seções se sucedem na ordem em que se lê a demanda."""
        conteudo = self._abrir().content.decode()
        ordem = [
            conteudo.index(">Resumo da demanda</h2>"),
            conteudo.index(">Informações principais</h2>"),
            conteudo.index(">Acompanhamento</h2>"),
            conteudo.index(">Histórico</h2>"),
        ]
        self.assertEqual(ordem, sorted(ordem))

    def test_a_descricao_continua_renderizada(self):
        conteudo = self._abrir().content.decode()
        self.assertIn("Resumo da demanda", conteudo)
        self.assertIn("Vazamento visível junto da base da bomba.", conteudo)

    def test_as_informacoes_essenciais_continuam(self):
        conteudo = self._abrir().content.decode()
        for rotulo in ("Local", "Responsável", "Prazo", "Data de registro"):
            self.assertIn(rotulo, conteudo)
        self.assertIn("Casa de máquinas", conteudo)

    def test_as_informacoes_sao_uma_grade_de_pares(self):
        """Deixaram de ser uma lista vertical: são pares em grade compacta."""
        resposta = self._abrir()
        self.assertContains(resposta, "eithra-info-grid")
        self.assertContains(resposta, "eithra-info-item")

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

    # -- Ações ----------------------------------------------------------------

    def test_as_acoes_vivem_no_cabecalho(self):
        """O que fazer agora fica no cabeçalho, junto do estado que o explica.

        São as mesmas ações da linha da lista, com os mesmos ícones: concluir e
        contar novidade como ícones sem borda, e o menu de três pontos com o que
        é raro.
        """
        conteudo = self._abrir().content.decode()
        cabecalho = conteudo[
            conteudo.index("eithra-detalhe-topo"):conteudo.index("eithra-detalhe-grade")
        ]
        for marca in (
            "Concluir demanda",
            "Contar novidade",
            "Mais ações",
        ):
            self.assertIn(marca, cabecalho)
        self.assertIn("bx-check-circle", cabecalho)
        self.assertIn("bx-message-rounded-dots", cabecalho)

    def test_nenhum_caminho_funcional_foi_removido(self):
        conteudo = self._abrir().content.decode()
        # Voltar para a lista continua valendo.
        self.assertIn(reverse("demandas:listagem"), conteudo)
        # Contar novidade abre o Registrar atualização, que já aceita evidência:
        # a evidência anexada continua alcançável pelo mesmo fluxo.
        self.assertIn("?abrir=atualizar", conteudo)
        self.assertNotIn("?abrir=acompanhamento", conteudo)
        self.assertNotIn("Atualizar acompanhamento", conteudo)
        self.assertIn('id="atualizar-demanda"', conteudo)
        self.assertIn('id="adicionar-evidencia"', conteudo)

    def test_o_editar_continua_alcancavel_pelas_mais_acoes(self):
        """O lápis solto saiu da descrição; editar vive nas Mais ações."""
        conteudo = self._abrir().content.decode()
        self.assertIn("Mais ações", conteudo)
        self.assertIn("Editar demanda", conteudo)
        self.assertIn("?abrir=editar", conteudo)

    # -- Separação visual -----------------------------------------------------

    def test_o_css_separa_as_secoes_por_um_fio(self):
        """A leitura contínua separa uma seção da outra por cards com borda discreta."""
        css = Path(settings.BASE_DIR, "static", "css", "eithra.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".eithra-cartao", css)
        self.assertIn("border: 1px solid var(--eithra-divisor", css)
