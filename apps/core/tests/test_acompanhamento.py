"""A jornada de acompanhamento: da Home ao registro canônico e de volta.

A supervisão vê uma ação na Home, chega ao registro original — a manutenção, a
ocorrência, a demanda — e o conduz ali mesmo: começa, atualiza e encerra, sempre
pelo serviço do próprio domínio. Estes testes travam isso: que a tela abre o
registro certo, que o início grava quem começou e quando (pelo relógio do
servidor), que a atualização entra no histórico canônico da origem — nunca em um
diário paralelo —, que a Home passa a ler o item como iniciado, que o
identificador de outro condomínio não é alcançável e que quem não tem permissão
não opera.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.condominios.constants import Papel
from apps.core.supervisao import ORIGEM_DEMANDA, ORIGEM_MANUTENCAO, ORIGEM_ROTINA
from apps.demandas.constants import StatusDemanda, TipoEventoDemanda
from apps.demandas.models import Demanda
from apps.manutencoes.constants import (
    StatusManutencao,
    TipoEventoManutencao,
    TipoManutencao,
)
from apps.manutencoes.models import HistoricoManutencao, Manutencao
from apps.manutencoes.tests.apoio import (
    criar_condominio,
    criar_fornecedor,
    criar_local,
    criar_pessoa,
    criar_vinculo,
)
from apps.rotinas.constants import ResultadoExecucao, StatusExecucao
from apps.rotinas.models import ExecucaoRotina, RotinaRecorrente


def _manutencao(condominio, criado_por, **campos):
    """Uma manutenção do condomínio, com o que o teste quiser trocar."""
    base = {
        "titulo": "Serviço de teste",
        "status": StatusManutencao.ABERTA,
        "tipo": TipoManutencao.CORRETIVA,
    }
    base.update(campos)
    return Manutencao.objects.create(condominio=condominio, criado_por=criado_por, **base)


def _execucao(condominio, criado_por, *, titulo="Ronda do prédio", local=None, **campos):
    """Uma ocorrência de rotina do condomínio, com o que o teste quiser trocar."""
    rotina = RotinaRecorrente.objects.create(
        condominio=condominio,
        titulo=titulo,
        data_inicio=timezone.localdate() - timedelta(days=30),
        criado_por=criado_por,
        local=local,
    )
    base = {
        "rotina": rotina,
        "condominio": condominio,
        "data_prevista": timezone.localdate(),
    }
    base.update(campos)
    return ExecucaoRotina.objects.create(**base)


def _demanda(condominio, criado_por, **campos):
    """Uma demanda do condomínio, com o que o teste quiser trocar."""
    base = {"titulo": "Demanda de teste", "status": StatusDemanda.ABERTA}
    base.update(campos)
    return Demanda.objects.create(condominio=condominio, criado_por=criado_por, **base)


class BaseDaJornada(TestCase):
    """O chão comum: um condomínio, uma supervisora e o cliente autenticado."""

    def setUp(self):
        self.hoje = timezone.localdate()
        self.condominio = criar_condominio("Alpha")
        self.supervisora = criar_pessoa("sup@exemplo.test", "Supervisão")
        criar_vinculo(self.supervisora, self.condominio, Papel.SUPERVISORA)
        self.client.force_login(self.supervisora)

    def tela(self, origem, pk):
        return self.client.get(reverse("core:acompanhamento", args=[origem, pk]))

    def gesto(self, origem, pk, **campos):
        seguir = campos.pop("follow", False)
        return self.client.post(
            reverse("core:acompanhar", args=[origem, pk]), campos, follow=seguir
        )


class TelaDoAcompanhamentoTests(BaseDaJornada):
    """A tela abre o registro canônico e mostra só o que ele tem."""

    def test_tela_abre_a_manutencao_como_registro_canonico(self):
        local = criar_local(self.condominio, "Casa de máquinas")
        manutencao = _manutencao(
            self.condominio,
            self.supervisora,
            titulo="Troca da bomba G1",
            local=local,
        )
        resposta = self.tela(ORIGEM_MANUTENCAO, manutencao.pk)
        self.assertContains(resposta, "Troca da bomba G1")
        self.assertContains(resposta, "Acompanhamento")
        self.assertContains(resposta, "Alpha")
        self.assertContains(resposta, "Casa de máquinas")

    def test_manutencao_sem_fornecedor_nao_bloqueia_e_mostra_o_nome_informado(self):
        manutencao = _manutencao(
            self.condominio, self.supervisora, titulo="Elevadores Bahia"
        )
        resposta = self.tela(ORIGEM_MANUTENCAO, manutencao.pk)
        self.assertContains(resposta, "Elevadores Bahia")
        self.assertContains(resposta, "Fornecedor ainda não vinculado ao cadastro.")

    def test_manutencao_com_fornecedor_vinculado_nao_diz_que_falta_cadastro(self):
        fornecedor = criar_fornecedor(self.condominio, "Elevadores Bahia Ltda")
        manutencao = _manutencao(
            self.condominio, self.supervisora, fornecedor=fornecedor
        )
        resposta = self.tela(ORIGEM_MANUTENCAO, manutencao.pk)
        self.assertContains(resposta, "Elevadores Bahia Ltda")
        self.assertNotContains(
            resposta, "Fornecedor ainda não vinculado ao cadastro."
        )

    def test_servico_ja_iniciado_mostra_que_comecou_e_nao_oferece_iniciar(self):
        inicio = timezone.now()
        manutencao = _manutencao(
            self.condominio,
            self.supervisora,
            status=StatusManutencao.EM_ANDAMENTO,
            data_inicio=inicio,
        )
        resposta = self.tela(ORIGEM_MANUTENCAO, manutencao.pk)
        self.assertContains(
            resposta, f"Acompanhamento iniciado às {timezone.localtime(inicio):%H:%M}"
        )
        self.assertNotContains(resposta, "Iniciar acompanhamento")
        self.assertContains(resposta, "Registrar atualização")

    def test_rotina_e_demanda_nao_inventam_bloco_de_prestador(self):
        execucao = _execucao(self.condominio, self.supervisora)
        demanda = _demanda(self.condominio, self.supervisora)
        self.assertNotContains(self.tela(ORIGEM_ROTINA, execucao.pk), "Prestador")
        self.assertNotContains(self.tela(ORIGEM_DEMANDA, demanda.pk), "Prestador")


class InicioDoAcompanhamentoTests(BaseDaJornada):
    """Começar grava quem começou e quando — sempre pelo relógio do servidor."""

    def test_iniciar_manutencao_grava_usuario_e_momento_do_servidor(self):
        manutencao = _manutencao(
            self.condominio, self.supervisora, data_prevista=self.hoje
        )
        antes = timezone.now()
        self.gesto(ORIGEM_MANUTENCAO, manutencao.pk, gesto="iniciar")
        depois = timezone.now()

        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.EM_ANDAMENTO)
        self.assertIsNotNone(manutencao.data_inicio)
        self.assertGreaterEqual(manutencao.data_inicio, antes)
        self.assertLessEqual(manutencao.data_inicio, depois)

    def test_horario_enviado_pelo_navegador_nao_e_autoridade(self):
        manutencao = _manutencao(self.condominio, self.supervisora)
        # O navegador tenta impor um começo de 2000; o servidor não o aceita.
        self.gesto(
            ORIGEM_MANUTENCAO,
            manutencao.pk,
            gesto="iniciar",
            data_inicio="2000-01-01T00:00:00Z",
        )
        manutencao.refresh_from_db()
        self.assertGreater(manutencao.data_inicio.year, 2000)

    def test_clicar_duas_vezes_nao_reescreve_o_comeco(self):
        manutencao = _manutencao(self.condominio, self.supervisora)
        self.gesto(ORIGEM_MANUTENCAO, manutencao.pk, gesto="iniciar")
        manutencao.refresh_from_db()
        primeiro_inicio = manutencao.data_inicio

        self.gesto(ORIGEM_MANUTENCAO, manutencao.pk, gesto="iniciar")
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.data_inicio, primeiro_inicio)
        self.assertEqual(
            manutencao.historico.filter(
                tipo_evento=TipoEventoManutencao.STATUS
            ).count(),
            1,
        )

    def test_iniciar_rotina_grava_quando_comecou(self):
        execucao = _execucao(self.condominio, self.supervisora)
        antes = timezone.now()
        self.gesto(ORIGEM_ROTINA, execucao.pk, gesto="iniciar")
        depois = timezone.now()

        execucao.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.EM_ANDAMENTO)
        self.assertGreaterEqual(execucao.iniciado_em, antes)
        self.assertLessEqual(execucao.iniciado_em, depois)

    def test_demanda_nao_tem_inicio_proprio(self):
        demanda = _demanda(
            self.condominio, self.supervisora, status=StatusDemanda.EM_ANDAMENTO
        )
        resposta = self.gesto(ORIGEM_DEMANDA, demanda.pk, gesto="iniciar", follow=True)
        self.assertContains(resposta, "Você não pode iniciar este acompanhamento.")
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.EM_ANDAMENTO)


class AtualizacaoDoAcompanhamentoTests(BaseDaJornada):
    """Atualizar escreve no histórico canônico da origem — nunca em um paralelo."""

    def test_atualizacao_da_manutencao_entra_no_historico_da_manutencao(self):
        manutencao = _manutencao(
            self.condominio,
            self.supervisora,
            status=StatusManutencao.EM_ANDAMENTO,
            data_inicio=timezone.now(),
        )
        self.gesto(
            ORIGEM_MANUTENCAO,
            manutencao.pk,
            gesto="atualizar",
            texto="Técnico iniciou a vistoria do elevador.",
        )

        evento = manutencao.historico.filter(
            tipo_evento=TipoEventoManutencao.ACOMPANHAMENTO
        ).get()
        self.assertEqual(evento.descricao, "Técnico iniciou a vistoria do elevador.")
        self.assertEqual(evento.usuario_id, self.supervisora.pk)
        self.assertIsNotNone(evento.data_hora)
        self.assertEqual(
            manutencao.historico.filter(
                tipo_evento=TipoEventoManutencao.ACOMPANHAMENTO
            ).count(),
            1,
        )

    def test_atualizacao_da_rotina_vive_na_observacao_da_execucao(self):
        execucao = _execucao(
            self.condominio,
            self.supervisora,
            status=StatusExecucao.EM_ANDAMENTO,
            iniciado_em=timezone.now(),
        )
        self.gesto(
            ORIGEM_ROTINA,
            execucao.pk,
            gesto="atualizar",
            texto="Falta conferir o reservatório.",
        )

        execucao.refresh_from_db()
        self.assertIn("Falta conferir o reservatório.", execucao.observacao_execucao)
        self.assertIn("Supervisão", execucao.observacao_execucao)

    def test_atualizacao_da_demanda_vira_observacao_no_historico(self):
        demanda = _demanda(
            self.condominio, self.supervisora, status=StatusDemanda.EM_ANDAMENTO
        )
        self.gesto(
            ORIGEM_DEMANDA,
            demanda.pk,
            gesto="atualizar",
            texto="Cobrei o orçamento por telefone.",
        )

        evento = demanda.historico.filter(
            tipo_evento=TipoEventoDemanda.OBSERVACAO
        ).get()
        self.assertEqual(evento.descricao, "Cobrei o orçamento por telefone.")
        self.assertEqual(evento.usuario_id, self.supervisora.pk)
        self.assertIsNotNone(evento.data_hora)

    def test_atualizacao_vazia_e_recusada_sem_gravar_nada(self):
        manutencao = _manutencao(
            self.condominio,
            self.supervisora,
            status=StatusManutencao.EM_ANDAMENTO,
            data_inicio=timezone.now(),
        )
        resposta = self.gesto(
            ORIGEM_MANUTENCAO, manutencao.pk, gesto="atualizar", texto="  ", follow=True
        )
        self.assertContains(resposta, "Escreva a atualização antes de registrar.")
        self.assertFalse(
            manutencao.historico.filter(
                tipo_evento=TipoEventoManutencao.ACOMPANHAMENTO
            ).exists()
        )

    def test_encerrar_manutencao_usa_a_conclusao_do_dominio(self):
        manutencao = _manutencao(
            self.condominio,
            self.supervisora,
            status=StatusManutencao.EM_ANDAMENTO,
            data_inicio=timezone.now(),
        )
        self.gesto(
            ORIGEM_MANUTENCAO,
            manutencao.pk,
            gesto="encerrar",
            texto="Vistoria concluída e elevador liberado.",
        )

        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.CONCLUIDA)
        self.assertEqual(
            manutencao.descricao_servico, "Vistoria concluída e elevador liberado."
        )
        self.assertFalse(
            manutencao.historico.filter(
                tipo_evento=TipoEventoManutencao.ACOMPANHAMENTO
            ).exists()
        )
        self.assertTrue(
            manutencao.historico.filter(
                tipo_evento=TipoEventoManutencao.CONCLUIDA
            ).exists()
        )

    def test_encerrar_manutencao_sem_dizer_o_servico_e_recusado(self):
        manutencao = _manutencao(
            self.condominio,
            self.supervisora,
            status=StatusManutencao.EM_ANDAMENTO,
            data_inicio=timezone.now(),
        )
        resposta = self.gesto(
            ORIGEM_MANUTENCAO, manutencao.pk, gesto="encerrar", texto="", follow=True
        )
        self.assertContains(
            resposta, "Descreva o serviço realizado antes de concluir a manutenção."
        )
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.EM_ANDAMENTO)

    def test_encerrar_rotina_registra_o_resultado_encontrado(self):
        execucao = _execucao(
            self.condominio,
            self.supervisora,
            status=StatusExecucao.EM_ANDAMENTO,
            iniciado_em=timezone.now(),
        )
        self.gesto(
            ORIGEM_ROTINA,
            execucao.pk,
            gesto="encerrar",
            texto="Sem novidades.",
            resultado=ResultadoExecucao.OK,
        )

        execucao.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.CONCLUIDA)
        self.assertEqual(execucao.resultado, ResultadoExecucao.OK)


class ReflexoNaHomeTests(BaseDaJornada):
    """Depois de iniciar, a Home lê o item como iniciado — sem repetir a regra."""

    def home(self):
        return self.client.get(reverse("core:home"))

    def test_acao_ainda_nao_iniciada_convida_a_acompanhar_a_visita(self):
        _manutencao(self.condominio, self.supervisora, data_prevista=self.hoje)
        resposta = self.home()
        self.assertContains(resposta, "Acompanhar visita")

    def test_acao_iniciada_muda_a_leitura_para_atualizar(self):
        inicio = timezone.now()
        manutencao = _manutencao(
            self.condominio,
            self.supervisora,
            status=StatusManutencao.EM_ANDAMENTO,
            data_inicio=inicio,
        )
        resposta = self.home()
        self.assertContains(
            resposta, f"Acompanhamento iniciado às {timezone.localtime(inicio):%H:%M}"
        )
        self.assertContains(resposta, "Atualizar")
        self.assertContains(
            resposta,
            reverse("core:acompanhamento", args=[ORIGEM_MANUTENCAO, manutencao.pk]),
        )

    def test_acompanhamento_iniciado_e_nao_encerrado_permanece_visivel(self):
        manutencao = _manutencao(
            self.condominio, self.supervisora, titulo="Elevadores Bahia"
        )
        self.gesto(ORIGEM_MANUTENCAO, manutencao.pk, gesto="iniciar")

        resposta = self.home()
        self.assertContains(resposta, "Elevadores Bahia")
        self.assertContains(resposta, "Acompanhamento iniciado às")
        self.assertContains(resposta, "Pendências")

    def test_acompanhamento_encerrado_desaparece_da_home(self):
        manutencao = _manutencao(
            self.condominio,
            self.supervisora,
            titulo="Elevadores Bahia",
            data_prevista=self.hoje - timedelta(days=1),
        )
        self.gesto(
            ORIGEM_MANUTENCAO,
            manutencao.pk,
            gesto="encerrar",
            texto="Serviço realizado.",
        )
        self.assertNotContains(self.home(), "Elevadores Bahia")


class SegurancaDaJornadaTests(BaseDaJornada):
    """O que vem do navegador não é autoridade — nem o identificador, nem o papel."""

    def test_identificador_de_outro_condominio_nao_e_alcancavel(self):
        beta = criar_condominio("Beta")
        manutencao = _manutencao(beta, self.supervisora, titulo="Segredo de Beta")
        self.assertEqual(self.tela(ORIGEM_MANUTENCAO, manutencao.pk).status_code, 404)

    def test_gesto_em_registro_de_outro_condominio_nao_muda_nada(self):
        beta = criar_condominio("Beta")
        manutencao = _manutencao(
            beta,
            self.supervisora,
            titulo="Segredo de Beta",
            status=StatusManutencao.ABERTA,
        )
        resposta = self.gesto(ORIGEM_MANUTENCAO, manutencao.pk, gesto="iniciar")
        self.assertEqual(resposta.status_code, 404)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)
        self.assertIsNone(manutencao.data_inicio)

    def test_origem_desconhecida_responde_404(self):
        demanda = _demanda(self.condominio, self.supervisora)
        self.assertEqual(self.tela("origem_inventada", demanda.pk).status_code, 404)

    def test_gesto_desconhecido_responde_404(self):
        manutencao = _manutencao(
            self.condominio, self.supervisora, data_prevista=self.hoje
        )
        resposta = self.gesto(ORIGEM_MANUTENCAO, manutencao.pk, gesto="qualquer_coisa")
        self.assertEqual(resposta.status_code, 404)

    def test_usuario_sem_papel_no_condominio_nao_alcanca_o_registro(self):
        manutencao = _manutencao(self.condominio, self.supervisora)
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, Papel.MORADOR)
        self.client.force_login(morador)
        self.assertEqual(self.tela(ORIGEM_MANUTENCAO, manutencao.pk).status_code, 404)

    def test_quem_nao_pode_operar_nao_recebe_os_gestos(self):
        manutencao = _manutencao(
            self.condominio, self.supervisora, data_prevista=self.hoje
        )
        operacional = criar_pessoa("op@exemplo.test", "Operação")
        criar_vinculo(operacional, self.condominio, Papel.OPERACIONAL)
        self.client.force_login(operacional)

        resposta = self.tela(ORIGEM_MANUTENCAO, manutencao.pk)
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, manutencao.titulo)
        self.assertNotContains(resposta, "Iniciar acompanhamento")
        self.assertNotContains(resposta, "Registrar atualização")
        self.assertNotContains(resposta, "Encerrar acompanhamento")

    def test_quem_nao_pode_operar_tem_o_gesto_negado(self):
        manutencao = _manutencao(
            self.condominio, self.supervisora, data_prevista=self.hoje
        )
        operacional = criar_pessoa("op@exemplo.test", "Operação")
        criar_vinculo(operacional, self.condominio, Papel.OPERACIONAL)
        self.client.force_login(operacional)

        resposta = self.gesto(ORIGEM_MANUTENCAO, manutencao.pk, gesto="iniciar", follow=True)
        self.assertContains(resposta, "Você não pode iniciar este acompanhamento.")
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)
        self.assertIsNone(manutencao.data_inicio)


class RegressaoDaJornadaTests(TestCase):
    """A jornada não quebrou a leitura da FASE B nem os domínios de origem."""

    def setUp(self):
        self.hoje = timezone.localdate()
        self.condominio = criar_condominio("Alpha")
        self.supervisora = criar_pessoa("sup@exemplo.test", "Supervisão")
        criar_vinculo(self.supervisora, self.condominio, Papel.SUPERVISORA)
        self.client.force_login(self.supervisora)

    def test_home_reune_as_tres_origens_nos_blocos_de_sempre(self):
        _manutencao(
            self.condominio,
            self.supervisora,
            titulo="Elevadores Bahia",
            data_prevista=self.hoje - timedelta(days=1),
        )
        _execucao(self.condominio, self.supervisora, data_prevista=self.hoje)
        _demanda(
            self.condominio,
            self.supervisora,
            status=StatusDemanda.EM_ANDAMENTO,
            proxima_acao="Cobrar o orçamento",
            prazo_proxima_acao=self.hoje - timedelta(days=1),
        )
        resposta = self.client.get(reverse("core:home"))
        self.assertContains(resposta, "O que precisa de você")
        self.assertContains(resposta, "Agora")
        self.assertContains(resposta, "Ainda hoje")
        self.assertContains(resposta, "3 coisas precisam da sua atenção")

    def test_manutencao_concluida_continua_registrando_o_historico_de_sempre(self):
        manutencao = _manutencao(
            self.condominio,
            self.supervisora,
            status=StatusManutencao.EM_ANDAMENTO,
            data_inicio=timezone.now(),
        )
        self.client.post(
            reverse("core:acompanhar", args=[ORIGEM_MANUTENCAO, manutencao.pk]),
            {"gesto": "encerrar", "texto": "Serviço realizado."},
        )
        tipos = set(
            HistoricoManutencao.objects.filter(manutencao=manutencao).values_list(
                "tipo_evento", flat=True
            )
        )
        self.assertEqual(
            tipos,
            {TipoEventoManutencao.SERVICO, TipoEventoManutencao.CONCLUIDA},
        )
        self.assertNotIn(TipoEventoManutencao.ACOMPANHAMENTO, tipos)

    def test_demanda_concluida_pelo_acompanhamento_registra_a_conclusao(self):
        demanda = _demanda(
            self.condominio, self.supervisora, status=StatusDemanda.EM_ANDAMENTO
        )
        self.client.post(
            reverse("core:acompanhar", args=[ORIGEM_DEMANDA, demanda.pk]),
            {"gesto": "encerrar"},
        )
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.CONCLUIDA)
        self.assertIsNotNone(demanda.concluido_em)
