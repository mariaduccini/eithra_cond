"""A situação e o peso de uma demanda são consequência, não escolha.

Esta frente tirou os dois da mão do formulário: não existe campo para escolher
a situação nem o peso. A situação só se move por uma ação que a pessoa alcança,
e o peso é sempre lido do relato pela mesma regra que o resto do sistema usa —
``prioridade_do_relato``. O que o navegador mandar de fora é ignorado ou
recusado, no backend.
"""
from django.test import TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.core.constants import Prioridade
from apps.demandas.constants import StatusDemanda, TipoEventoDemanda
from apps.demandas.derivacao import prioridade_do_relato
from apps.demandas.forms import AcaoDeStatusForm, DemandaForm
from apps.demandas.models import Demanda
from apps.demandas.permissoes import status_permitidos_para
from apps.demandas.services import alterar_status, criar_demanda
from apps.demandas.tests.apoio import criar_condominio, criar_pessoa, criar_vinculo

# Dois relatos com peso previsível: um pede atenção, o outro não tem pressa.
RELATO_URGENTE = "cheiro de gás na casa de máquinas, risco de explosão"
RELATO_SEM_PRESSA = "comprar lâmpadas para o corredor do bloco B, sem pressa"


class BaseDeEstado(TestCase):
    """Uma pessoa de gestão ligada a um condomínio."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    def _demanda(self, **campos):
        base = {
            "condominio": self.condominio,
            "criado_por": self.gestao,
            "titulo": "Demanda de teste",
        }
        base.update(campos)
        return criar_demanda(**base)

    def _registrar(self, **extra):
        dados = {"condominio": str(self.condominio.pk), "relato": RELATO_SEM_PRESSA}
        dados.update(extra)
        return self.client.post(reverse("demandas:criar"), dados)


class SemCampoDeEstadoTests(BaseDeEstado):
    """Não existe campo para escolher situação nem peso (§10, §11)."""

    def test_formulario_nao_oferece_situacao_nem_peso(self):
        formulario = DemandaForm(condominio=self.condominio)
        self.assertNotIn("status", formulario.fields)
        self.assertNotIn("prioridade", formulario.fields)


class BackendRecusaEscolhaManualTests(BaseDeEstado):
    """O que o navegador manda de fora não move nada (§9, §12)."""

    def test_peso_do_navegador_e_ignorado_no_registro(self):
        resposta = self._registrar(prioridade=Prioridade.URGENTE)
        self.assertEqual(resposta.status_code, 302)
        demanda = Demanda.objects.get()
        # O relato não sustenta urgência: o "Urgente" do POST não conta.
        self.assertEqual(demanda.prioridade, Prioridade.BAIXA)

    def test_peso_do_navegador_e_ignorado_na_edicao(self):
        demanda = self._demanda(
            titulo="Comprar lâmpadas", descricao="para o corredor do bloco B"
        )
        resposta = self.client.post(
            reverse("demandas:editar", args=[demanda.pk]),
            {
                "condominio": str(self.condominio.pk),
                "titulo": demanda.titulo,
                "descricao": demanda.descricao,
                "prioridade": Prioridade.URGENTE,
            },
        )
        self.assertEqual(resposta.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.prioridade, Prioridade.NORMAL)

    def test_campo_status_no_post_nao_muda_a_situacao(self):
        demanda = self._demanda()
        resposta = self.client.post(
            reverse("demandas:mudar_status", args=[demanda.pk]),
            {"status": StatusDemanda.CONCLUIDA},
        )
        self.assertEqual(resposta.status_code, 400)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.ABERTA)

    def test_editar_nao_move_a_situacao(self):
        demanda = self._demanda()
        self.client.post(
            reverse("demandas:editar", args=[demanda.pk]),
            {
                "condominio": str(self.condominio.pk),
                "titulo": demanda.titulo,
                "status": StatusDemanda.CONCLUIDA,
            },
        )
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.ABERTA)

    def test_acao_fora_do_alcance_e_recusada(self):
        # A supervisão move a demanda, mas não cancela: pedir o cancelamento por
        # POST não passa, porque a ação não está entre as que ela alcança.
        supervisora = criar_pessoa("sup@exemplo.test", "Supervisora")
        criar_vinculo(supervisora, self.condominio, papel=Papel.SUPERVISORA)
        demanda = self._demanda()
        self.client.force_login(supervisora)

        resposta = self.client.post(
            reverse("demandas:mudar_status", args=[demanda.pk]),
            {"acao": StatusDemanda.CANCELADA},
        )
        self.assertEqual(resposta.status_code, 400)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.ABERTA)


class AcaoMoveASituacaoTests(BaseDeEstado):
    """A situação se move pelo verbo que a alcança, e só por ele (§7, §8)."""

    def test_as_acoes_oferecidas_sao_as_alcancaveis(self):
        demanda = self._demanda()
        formulario = AcaoDeStatusForm(
            permitidos=status_permitidos_para(self.gestao, demanda), demanda=demanda
        )
        situacoes = [valor for valor, _ in formulario.fields["acao"].choices]
        # A demanda já está "Aberta": não se oferece levá-la aonde ela já está.
        self.assertNotIn(StatusDemanda.ABERTA, situacoes)
        self.assertIn(StatusDemanda.CONCLUIDA, situacoes)

    def test_iniciar_move_para_em_andamento(self):
        demanda = self._demanda()
        resposta = self.client.post(
            reverse("demandas:mudar_status", args=[demanda.pk]),
            {"acao": StatusDemanda.EM_ANDAMENTO},
        )
        self.assertEqual(resposta.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.EM_ANDAMENTO)

    def test_concluir_pela_acao_existente(self):
        demanda = self._demanda()
        resposta = self.client.post(
            reverse("demandas:mudar_status", args=[demanda.pk]),
            {"acao": StatusDemanda.CONCLUIDA},
        )
        self.assertEqual(resposta.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.CONCLUIDA)
        self.assertIsNotNone(demanda.concluido_em)

    def test_reabrir_pela_acao_existente(self):
        demanda = self._demanda()
        alterar_status(
            demanda=demanda, usuario=self.gestao, novo_status=StatusDemanda.CONCLUIDA
        )
        resposta = self.client.post(
            reverse("demandas:mudar_status", args=[demanda.pk]),
            {"acao": StatusDemanda.ABERTA},
        )
        self.assertEqual(resposta.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.ABERTA)
        self.assertIsNone(demanda.concluido_em)


class PesoVemDoRelatoTests(BaseDeEstado):
    """O peso sai da regra existente e acompanha a correção do relato (§3, §15)."""

    def test_peso_segue_a_mesma_regra_do_relato(self):
        resposta = self._registrar(relato=RELATO_URGENTE)
        self.assertEqual(resposta.status_code, 302)
        demanda = Demanda.objects.get()
        esperado, _ = prioridade_do_relato(RELATO_URGENTE)
        self.assertEqual(demanda.prioridade, esperado)
        self.assertEqual(demanda.prioridade, Prioridade.URGENTE)

    def test_corrigir_o_relato_recalcula_o_peso(self):
        self._registrar(relato=RELATO_SEM_PRESSA)
        demanda = Demanda.objects.get()
        self.assertEqual(demanda.prioridade, Prioridade.BAIXA)

        resposta = self.client.post(
            reverse("demandas:editar", args=[demanda.pk]),
            {
                "condominio": str(self.condominio.pk),
                "titulo": demanda.titulo,
                "descricao": RELATO_URGENTE,
            },
        )
        self.assertEqual(resposta.status_code, 302)
        demanda.refresh_from_db()
        # O fato mudou; o peso acompanha.
        self.assertEqual(demanda.prioridade, Prioridade.URGENTE)


class HistoricoNaoRecalculadoTests(BaseDeEstado):
    """O passado guarda o que foi decidido; recalcular não reescreve história (§16)."""

    def test_recalcular_o_peso_nao_reescreve_o_historico(self):
        self._registrar(relato=RELATO_SEM_PRESSA)
        demanda = Demanda.objects.get()
        criada = demanda.historico.get(tipo_evento=TipoEventoDemanda.CRIADA)
        momento_da_criacao = criada.data_hora

        self.client.post(
            reverse("demandas:editar", args=[demanda.pk]),
            {
                "condominio": str(self.condominio.pk),
                "titulo": demanda.titulo,
                "descricao": RELATO_URGENTE,
            },
        )

        # O evento de criação continua exatamente como nasceu.
        criada.refresh_from_db()
        self.assertEqual(criada.data_hora, momento_da_criacao)
        self.assertIsNone(criada.dados_anteriores)

        # A correção acrescenta o que mudou, sem apagar o que já estava.
        peso = demanda.historico.get(tipo_evento=TipoEventoDemanda.PRIORIDADE)
        self.assertEqual(peso.dados_anteriores, {"prioridade": Prioridade.BAIXA})
        self.assertEqual(peso.dados_novos, {"prioridade": Prioridade.URGENTE})
