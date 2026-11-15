"""A situação e o peso de uma demanda não se decidem do mesmo jeito.

A situação continua sendo consequência, não escolha: não existe campo para
escolhê-la, e ela só se move por uma ação que a pessoa alcança. O peso, ao
contrário, passou a ser escolha explícita de quem registra — a tela pergunta, e
o que a pessoa responde é o que vale. A leitura do relato não decide a
prioridade operacional: mesmo um relato que antes seria lido como urgente não
sobrepõe a escolha humana.
"""
from django.test import TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.core.constants import Prioridade
from apps.demandas.constants import StatusDemanda, TipoEventoDemanda
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


class SemCampoDeSituacaoTests(BaseDeEstado):
    """A situação não tem campo; a prioridade tem (§2, §10)."""

    def test_formulario_oferece_prioridade_e_nao_situacao(self):
        formulario = DemandaForm(condominio=self.condominio)
        self.assertNotIn("status", formulario.fields)
        self.assertIn("prioridade", formulario.fields)


class PrioridadeEscolhidaTests(BaseDeEstado):
    """A prioridade gravada é a escolha de quem registra (§1, §10)."""

    def test_registro_grava_a_prioridade_escolhida(self):
        resposta = self._registrar(prioridade=Prioridade.ALTA)
        self.assertEqual(resposta.status_code, 302)
        demanda = Demanda.objects.get()
        self.assertEqual(demanda.prioridade, Prioridade.ALTA)

    def test_escolha_do_usuario_vence_a_leitura_do_relato(self):
        # Sozinho, o relato antes derivaria urgência; a escolha humana manda, e
        # é essa a regressão que fecha o escopo: a leitura não decide o peso.
        resposta = self._registrar(relato=RELATO_URGENTE, prioridade=Prioridade.BAIXA)
        self.assertEqual(resposta.status_code, 302)
        demanda = Demanda.objects.get()
        self.assertEqual(demanda.prioridade, Prioridade.BAIXA)

    def test_relato_urgente_sem_escolha_fica_no_padrao_do_dominio(self):
        resposta = self._registrar(relato=RELATO_URGENTE)
        self.assertEqual(resposta.status_code, 302)
        demanda = Demanda.objects.get()
        self.assertEqual(demanda.prioridade, Prioridade.NORMAL)

    def test_edicao_muda_a_prioridade_quando_ha_permissao(self):
        demanda = self._demanda()
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
        self.assertEqual(demanda.prioridade, Prioridade.URGENTE)

    def test_edicao_sem_prioridade_no_envio_preserva_a_escolha_existente(self):
        demanda = self._demanda(prioridade=Prioridade.ALTA)
        resposta = self.client.post(
            reverse("demandas:editar", args=[demanda.pk]),
            {
                "condominio": str(self.condominio.pk),
                "titulo": demanda.titulo,
                "descricao": demanda.descricao,
            },
        )
        self.assertEqual(resposta.status_code, 302)
        demanda.refresh_from_db()
        # A edição que não fala de peso não mexe no peso: o registro antigo
        # mantém a prioridade que já tinha.
        self.assertEqual(demanda.prioridade, Prioridade.ALTA)

    def test_registro_criado_com_prioridade_preserva_o_valor(self):
        demanda = self._demanda(prioridade=Prioridade.URGENTE)
        self.assertEqual(
            Demanda.objects.get(pk=demanda.pk).prioridade, Prioridade.URGENTE
        )


class BackendRecusaSituacaoManualTests(BaseDeEstado):
    """A situação não se decide por campo (§9, §12)."""

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


class PrioridadeNoHistoricoTests(BaseDeEstado):
    """A prioridade muda com registro no histórico, sem reescrever o passado (§8)."""

    def test_mudar_a_prioridade_registra_o_antes_e_o_depois(self):
        demanda = self._demanda(prioridade=Prioridade.BAIXA)
        self.client.post(
            reverse("demandas:editar", args=[demanda.pk]),
            {
                "condominio": str(self.condominio.pk),
                "titulo": demanda.titulo,
                "descricao": demanda.descricao,
                "prioridade": Prioridade.URGENTE,
            },
        )
        evento = demanda.historico.get(tipo_evento=TipoEventoDemanda.PRIORIDADE)
        self.assertEqual(evento.dados_anteriores, {"prioridade": Prioridade.BAIXA})
        self.assertEqual(evento.dados_novos, {"prioridade": Prioridade.URGENTE})

    def test_o_evento_de_criacao_nao_e_reescrito_pela_edicao(self):
        demanda = self._demanda()
        criada = demanda.historico.get(tipo_evento=TipoEventoDemanda.CRIADA)
        momento_da_criacao = criada.data_hora

        self.client.post(
            reverse("demandas:editar", args=[demanda.pk]),
            {
                "condominio": str(self.condominio.pk),
                "titulo": demanda.titulo,
                "descricao": demanda.descricao,
                "prioridade": Prioridade.ALTA,
            },
        )

        # O evento de criação continua exatamente como nasceu.
        criada.refresh_from_db()
        self.assertEqual(criada.data_hora, momento_da_criacao)
        self.assertIsNone(criada.dados_anteriores)
