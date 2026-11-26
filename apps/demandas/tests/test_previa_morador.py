"""A gestão vê a demanda como o morador a vê.

Quem responde pela demanda abre a página pública por um ícone no grupo de ações
do detalhe. Quando o acompanhamento ainda não existe, o clique o cria com o
morador que pediu — ou sem morador, quando não há solicitante — e leva à página.
Na página, a gestão vê a faixa de pré-visualização e não avalia: a avaliação é
do morador. Nada aqui toca dado real: nomes, e-mails e títulos são de teste.
"""
from django.test import Client, TestCase
from django.urls import reverse

from apps.comunidade.tests.apoio import criar_pessoa_da_comunidade
from apps.condominios.constants import Papel
from apps.demandas.constants import (
    AvaliacaoMorador,
    StatusDemanda,
    TipoEventoDemanda,
)
from apps.demandas.models import AcompanhamentoMorador, HistoricoDemanda
from apps.demandas.services import alterar_status, criar_demanda
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_local,
    criar_pessoa,
    criar_vinculo,
)

TITULO = "Lâmpada queimada no corredor"
TOOLTIP = "Ver como o morador vê"


class PreviaDoMoradorTests(TestCase):
    """O ícone no detalhe e a página pública vista pela gestão."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Fernanda Gestora")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.operacao = criar_pessoa("operacao@exemplo.test", "Caio Operador")
        criar_vinculo(self.operacao, self.condominio, papel=Papel.OPERACIONAL)
        self.local = criar_local(self.condominio, "Corredor do bloco A")
        self.morador = criar_pessoa_da_comunidade(
            self.condominio, nome="Joana Moradora"
        )
        self.demanda = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo=TITULO,
            descricao="A lâmpada do corredor parou de acender.",
            local=self.local,
            solicitante=self.morador,
            responsavel=self.gestao,
        )

    def _url_detalhe(self):
        return reverse("demandas:detalhe", args=[self.demanda.pk])

    def _url_previa(self):
        return reverse(
            "demandas:pre_visualizar_acompanhamento", args=[self.demanda.pk]
        )

    def _url_publica(self, acompanhamento):
        return reverse(
            "demandas:acompanhar_publico", args=[acompanhamento.token]
        )

    # -- O ícone no detalhe ---------------------------------------------------

    def test_o_icone_so_aparece_para_quem_pode_editar(self):
        """Só quem responde pela demanda — quem pode editá-la — vê o ícone."""
        self.client.force_login(self.gestao)
        resposta = self.client.get(self._url_detalhe())
        self.assertContains(resposta, TOOLTIP)
        self.assertContains(resposta, "bx-show")

        # A operação enxerga a demanda, mas não responde por ela: sem ícone.
        self.client.force_login(self.operacao)
        self.assertNotContains(self.client.get(self._url_detalhe()), TOOLTIP)

    # -- A pré-visualização ---------------------------------------------------

    def test_a_previa_cria_o_acompanhamento_e_redireciona(self):
        """Sem acompanhamento, o POST o cria com o solicitante e leva à página."""
        self.assertFalse(
            AcompanhamentoMorador.objects.filter(demanda=self.demanda).exists()
        )
        self.client.force_login(self.gestao)

        resposta = self.client.post(self._url_previa())

        acompanhamento = AcompanhamentoMorador.objects.get(demanda=self.demanda)
        self.assertEqual(acompanhamento.pessoa, self.morador)
        self.assertEqual(acompanhamento.criado_por, self.gestao)
        self.assertRedirects(resposta, self._url_publica(acompanhamento))

    def test_a_previa_cria_o_acompanhamento_sem_solicitante(self):
        """Sem solicitante, o acompanhamento nasce sem morador — e ainda leva à página."""
        demanda = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Barulho no salão",
            descricao="Relato sem morador identificado.",
            local=self.local,
        )
        self.client.force_login(self.gestao)

        resposta = self.client.post(
            reverse("demandas:pre_visualizar_acompanhamento", args=[demanda.pk])
        )

        acompanhamento = AcompanhamentoMorador.objects.get(demanda=demanda)
        self.assertIsNone(acompanhamento.pessoa)
        self.assertRedirects(resposta, self._url_publica(acompanhamento))

    # -- A faixa de pré-visualização ------------------------------------------

    def test_a_faixa_aparece_para_a_gestao_logada_e_nao_sem_login(self):
        acompanhamento = AcompanhamentoMorador.objects.create(
            demanda=self.demanda, pessoa=self.morador, criado_por=self.gestao
        )
        url = self._url_publica(acompanhamento)

        self.client.force_login(self.gestao)
        self.assertContains(self.client.get(url), "Pré-visualização")

        # Sem login, é o morador quem abre: nenhuma faixa.
        self.assertNotContains(Client().get(url), "Pré-visualização")

    # -- A avaliação é só do morador ------------------------------------------

    def test_a_gestao_nao_consegue_avaliar(self):
        acompanhamento = AcompanhamentoMorador.objects.create(
            demanda=self.demanda, pessoa=self.morador, criado_por=self.gestao
        )
        alterar_status(
            demanda=self.demanda,
            usuario=self.gestao,
            novo_status=StatusDemanda.CONCLUIDA,
        )

        self.client.force_login(self.gestao)
        self.client.post(
            reverse("demandas:avaliar", args=[acompanhamento.token]),
            {"avaliacao": AvaliacaoMorador.RESOLVIDO, "comentario": "Tentativa."},
        )

        acompanhamento.refresh_from_db()
        self.assertFalse(acompanhamento.respondido)
        self.assertFalse(
            HistoricoDemanda.objects.filter(
                demanda=self.demanda, tipo_evento=TipoEventoDemanda.AVALIACAO
            ).exists()
        )
