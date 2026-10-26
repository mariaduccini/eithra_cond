"""A situação e o peso de uma manutenção são consequência, não escolha.

Esta frente tirou os dois da mão do formulário: não existe campo para escolher a
situação nem o peso. A situação só se move por uma ação que a pessoa alcança — e
concluir e cancelar têm ação própria, porque uma pede o serviço realizado e a
outra pede o motivo. O peso é sempre lido do problema relatado pela mesma regra
que o resto do sistema usa — ``prioridade_do_relato``. O que o navegador mandar
de fora é ignorado ou recusado, no backend.
"""
from django.test import TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.core.constants import Prioridade
from apps.demandas.derivacao import prioridade_do_relato
from apps.manutencoes.constants import (
    StatusManutencao,
    TipoEventoManutencao,
    TipoManutencao,
)
from apps.manutencoes.forms import AcaoDeStatusForm, ManutencaoForm
from apps.manutencoes.models import Manutencao
from apps.manutencoes.permissoes import situacoes_para_mudar
from apps.manutencoes.services import (
    alterar_status,
    concluir_manutencao,
    criar_manutencao,
)
from apps.manutencoes.tests.apoio import criar_condominio, criar_pessoa, criar_vinculo

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

    def _manutencao(self, **campos):
        base = {
            "condominio": self.condominio,
            "criado_por": self.gestao,
            "titulo": "Manutenção de teste",
        }
        base.update(campos)
        return criar_manutencao(**base)

    def _registrar(self, **extra):
        dados = {
            "condominio": str(self.condominio.pk),
            "tipo": TipoManutencao.CORRETIVA,
            "titulo": "Serviço de teste",
            "descricao_problema": RELATO_SEM_PRESSA,
        }
        dados.update(extra)
        return self.client.post(reverse("manutencoes:criar"), dados)

    def _editar(self, manutencao, **extra):
        dados = {
            "condominio": str(self.condominio.pk),
            "tipo": manutencao.tipo,
            "titulo": manutencao.titulo,
        }
        dados.update(extra)
        return self.client.post(
            reverse("manutencoes:editar", args=[manutencao.pk]), dados
        )


class SemCampoDeEstadoTests(BaseDeEstado):
    """Não existe campo para escolher situação nem peso (§10, §11)."""

    def test_formulario_nao_oferece_situacao_nem_peso(self):
        formulario = ManutencaoForm(condominio=self.condominio)
        self.assertNotIn("status", formulario.fields)
        self.assertNotIn("prioridade", formulario.fields)


class BackendRecusaEscolhaManualTests(BaseDeEstado):
    """O que o navegador manda de fora não move nada (§9, §12)."""

    def test_peso_do_navegador_e_ignorado_no_registro(self):
        resposta = self._registrar(prioridade=Prioridade.URGENTE)
        self.assertEqual(resposta.status_code, 302)
        manutencao = Manutencao.objects.get()
        # O problema não sustenta urgência: o "Urgente" do POST não conta.
        self.assertEqual(manutencao.prioridade, Prioridade.BAIXA)

    def test_peso_do_navegador_e_ignorado_na_edicao(self):
        manutencao = self._manutencao(
            titulo="Comprar lâmpadas", descricao_problema="para o corredor do bloco B"
        )
        resposta = self._editar(manutencao, prioridade=Prioridade.URGENTE)
        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.prioridade, Prioridade.NORMAL)

    def test_campo_status_no_post_nao_muda_a_situacao(self):
        manutencao = self._manutencao()
        resposta = self.client.post(
            reverse("manutencoes:mudar_status", args=[manutencao.pk]),
            {"status": StatusManutencao.CONCLUIDA},
        )
        self.assertEqual(resposta.status_code, 400)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)

    def test_editar_nao_move_a_situacao(self):
        manutencao = self._manutencao()
        self._editar(manutencao, status=StatusManutencao.CONCLUIDA)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)

    def test_acao_fora_do_alcance_e_recusada(self):
        # A supervisão move a manutenção, mas não cancela: pedir o cancelamento
        # por POST não passa, porque a ação não está entre as que ela alcança.
        supervisora = criar_pessoa("sup@exemplo.test", "Supervisora")
        criar_vinculo(supervisora, self.condominio, papel=Papel.SUPERVISORA)
        manutencao = self._manutencao()
        self.client.force_login(supervisora)

        resposta = self.client.post(
            reverse("manutencoes:mudar_status", args=[manutencao.pk]),
            {"acao": StatusManutencao.CANCELADA},
        )
        self.assertEqual(resposta.status_code, 400)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)


class AcaoMoveASituacaoTests(BaseDeEstado):
    """A situação se move pelo verbo que a alcança, e só por ele (§7, §8)."""

    def test_as_acoes_oferecidas_sao_as_alcancaveis(self):
        manutencao = self._manutencao()
        formulario = AcaoDeStatusForm(
            permitidos=situacoes_para_mudar(self.gestao, manutencao),
            manutencao=manutencao,
        )
        situacoes = [valor for valor, _ in formulario.fields["acao"].choices]
        # A manutenção já está "Aberta": não se oferece levá-la aonde ela já está.
        self.assertNotIn(StatusManutencao.ABERTA, situacoes)
        self.assertIn(StatusManutencao.EM_ANDAMENTO, situacoes)
        # Concluir não anda pelo movimento comum: tem ação própria.
        self.assertNotIn(StatusManutencao.CONCLUIDA, situacoes)

    def test_iniciar_move_para_em_andamento(self):
        manutencao = self._manutencao()
        resposta = self.client.post(
            reverse("manutencoes:mudar_status", args=[manutencao.pk]),
            {"acao": StatusManutencao.EM_ANDAMENTO},
        )
        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.EM_ANDAMENTO)
        self.assertIsNotNone(manutencao.data_inicio)

    def test_concluir_pela_acao_existente(self):
        manutencao = self._manutencao()
        resposta = self.client.post(
            reverse("manutencoes:concluir", args=[manutencao.pk]),
            {"descricao_servico": "Trocada a bomba do reservatório."},
        )
        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.CONCLUIDA)
        self.assertIsNotNone(manutencao.data_conclusao)

    def test_reabrir_pela_acao_existente(self):
        manutencao = self._manutencao()
        concluir_manutencao(
            manutencao=manutencao,
            usuario=self.gestao,
            descricao_servico="Serviço concluído.",
        )
        resposta = self.client.post(
            reverse("manutencoes:mudar_status", args=[manutencao.pk]),
            {"acao": StatusManutencao.ABERTA},
        )
        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)
        self.assertIsNone(manutencao.data_conclusao)

    def test_concluir_pelo_movimento_comum_e_recusado(self):
        # O verbo "concluir" não existe no movimento comum: a situação alcançada
        # é recusada pelo próprio domínio, que exige a ação de conclusão.
        manutencao = self._manutencao()
        with self.assertRaises(Exception):
            alterar_status(
                manutencao=manutencao,
                usuario=self.gestao,
                novo_status=StatusManutencao.CONCLUIDA,
            )


class PesoVemDoRelatoTests(BaseDeEstado):
    """O peso sai da regra existente e acompanha a correção do relato (§3, §15)."""

    def test_peso_segue_a_mesma_regra_do_relato(self):
        resposta = self._registrar(descricao_problema=RELATO_URGENTE)
        self.assertEqual(resposta.status_code, 302)
        manutencao = Manutencao.objects.get()
        esperado, _ = prioridade_do_relato(RELATO_URGENTE)
        self.assertEqual(manutencao.prioridade, esperado)
        self.assertEqual(manutencao.prioridade, Prioridade.URGENTE)

    def test_corrigir_o_relato_recalcula_o_peso(self):
        self._registrar(descricao_problema=RELATO_SEM_PRESSA)
        manutencao = Manutencao.objects.get()
        self.assertEqual(manutencao.prioridade, Prioridade.BAIXA)

        resposta = self._editar(manutencao, descricao_problema=RELATO_URGENTE)
        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        # O fato mudou; o peso acompanha.
        self.assertEqual(manutencao.prioridade, Prioridade.URGENTE)


class HistoricoNaoRecalculadoTests(BaseDeEstado):
    """O passado guarda o que foi decidido; recalcular não reescreve história (§16)."""

    def test_recalcular_o_peso_nao_reescreve_o_historico(self):
        self._registrar(descricao_problema=RELATO_SEM_PRESSA)
        manutencao = Manutencao.objects.get()
        criada = manutencao.historico.get(tipo_evento=TipoEventoManutencao.CRIADA)
        momento_da_criacao = criada.data_hora

        self._editar(manutencao, descricao_problema=RELATO_URGENTE)

        # O evento de criação continua exatamente como nasceu.
        criada.refresh_from_db()
        self.assertEqual(criada.data_hora, momento_da_criacao)
        self.assertIsNone(criada.dados_anteriores)

        # A correção acrescenta o que mudou, sem apagar o que já estava.
        peso = manutencao.historico.get(tipo_evento=TipoEventoManutencao.PRIORIDADE)
        self.assertEqual(peso.dados_anteriores, {"prioridade": Prioridade.BAIXA})
        self.assertEqual(peso.dados_novos, {"prioridade": Prioridade.URGENTE})
