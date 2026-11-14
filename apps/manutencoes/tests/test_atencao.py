"""A fundação dos alertas derivados de manutenções.

Aqui se confere que a atenção é uma leitura do que já está guardado — e nunca
um dado novo. Os testes cobrem a janela de antecedência, o atraso, a execução
não registrada, a precedência da conferência, a composição com "sem
responsável", a ordenação, o isolamento por condomínio, a data de referência
injetável e o fato de consultar não gravar nada.
"""
from datetime import date, timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.condominios.constants import Papel
from apps.manutencoes.atencao import (
    PRECEDENCIA,
    ResumoDeAtencoes,
    TipoAtencao,
    obter_atencoes_de_manutencoes,
)
from apps.manutencoes.constants import (
    StatusConferencia,
    StatusManutencao,
    TipoManutencao,
)
from apps.manutencoes.models import HistoricoManutencao, Manutencao
from apps.manutencoes.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)

# Uma data fixa para o cálculo não depender do relógio da máquina.
HOJE = date(2026, 11, 3)


def _manutencao(condominio, criado_por, **campos):
    """Uma manutenção do condomínio, com o que o teste quiser trocar."""
    base = {
        "titulo": "Serviço de teste",
        "status": StatusManutencao.ABERTA,
        "tipo": TipoManutencao.CORRETIVA,
        "data_prevista": HOJE,
    }
    base.update(campos)
    return Manutencao.objects.create(
        condominio=condominio, criado_por=criado_por, **base
    )


class BaseDeAtencaoTests(TestCase):
    """O cenário comum: um condomínio, a gestão e um responsável de operação."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, Papel.SINDICA)
        self.responsavel = criar_pessoa("responsavel@exemplo.test", "Responsável")
        criar_vinculo(self.responsavel, self.condominio, Papel.OPERACIONAL)

    def atencao(self, **kwargs):
        return obter_atencoes_de_manutencoes(
            condominios=[self.condominio], hoje=HOJE, **kwargs
        )


class VocabularioTests(TestCase):
    """A precedência é única e cobre todo o vocabulário."""

    def test_precedencia_cobre_todos_os_tipos(self):
        self.assertEqual(set(PRECEDENCIA), set(TipoAtencao.values))

    def test_ordem_de_precedencia_e_a_declarada(self):
        self.assertEqual(
            sorted(PRECEDENCIA, key=PRECEDENCIA.get),
            [
                TipoAtencao.AGUARDANDO_CORRECAO,
                TipoAtencao.AGUARDANDO_CONFERENCIA,
                TipoAtencao.AGUARDANDO_CONCLUSAO,
                TipoAtencao.ATRASADA,
                TipoAtencao.HOJE,
                TipoAtencao.SEM_RESPONSAVEL,
                TipoAtencao.PROXIMA,
            ],
        )


class ProximaTests(BaseDeAtencaoTests):
    """A janela de antecedência mostra o que está por vir — sem antecipar tudo."""

    def test_dentro_da_janela_aparece(self):
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Limpeza da piscina",
            data_prevista=HOJE + timedelta(days=2),
            responsavel=self.responsavel,
        )
        itens = self.atencao()
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0].tipo, TipoAtencao.PROXIMA)
        self.assertEqual(itens[0].dias_de_diferenca, 2)

    def test_fora_da_janela_nao_aparece(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE + timedelta(days=15),
            responsavel=self.responsavel,
        )
        self.assertEqual(self.atencao(), [])

    @override_settings(MANUTENCOES_ALERTA_ANTECEDENCIA_DIAS=10)
    def test_janela_vem_da_configuracao(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE + timedelta(days=5),
            responsavel=self.responsavel,
        )
        itens = obter_atencoes_de_manutencoes(
            condominios=[self.condominio], hoje=HOJE
        )
        self.assertEqual([item.tipo for item in itens], [TipoAtencao.PROXIMA])

    def test_janela_pode_ser_informada(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE + timedelta(days=7),
            responsavel=self.responsavel,
        )
        itens = self.atencao(dias_antecedencia=7)
        self.assertEqual([item.tipo for item in itens], [TipoAtencao.PROXIMA])


class HojeTests(BaseDeAtencaoTests):
    """Vence hoje é uma atenção; conferência é outra, e tem precedência."""

    def test_prevista_para_hoje_aparece(self):
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Poda das árvores",
            data_prevista=HOJE,
            responsavel=self.responsavel,
        )
        itens = self.atencao()
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0].tipo, TipoAtencao.HOJE)
        self.assertEqual(itens[0].mensagem, "Vence hoje")

    def test_executada_aguardando_conferencia_nao_aparece_como_hoje(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE,
            responsavel=self.responsavel,
            exige_conferencia=True,
            status_conferencia=StatusConferencia.PENDENTE,
            data_execucao=timezone.now(),
        )
        itens = self.atencao()
        self.assertEqual([item.tipo for item in itens], [
            TipoAtencao.AGUARDANDO_CONFERENCIA
        ])


class AtrasadaTests(BaseDeAtencaoTests):
    """O que passou da data e continua aberto; o encerrado sai da leitura."""

    def test_vencida_aberta_aparece(self):
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Revisão do elevador",
            data_prevista=HOJE - timedelta(days=2),
            responsavel=self.responsavel,
            exige_conferencia=False,
        )
        itens = self.atencao()
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0].tipo, TipoAtencao.ATRASADA)
        self.assertTrue(itens[0].execucao_nao_registrada)

    def test_concluida_nao_aparece(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=5),
            responsavel=self.responsavel,
            status=StatusManutencao.CONCLUIDA,
            data_conclusao=timezone.now(),
        )
        self.assertEqual(self.atencao(), [])

    def test_cancelada_nao_aparece(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=5),
            responsavel=self.responsavel,
            status=StatusManutencao.CANCELADA,
        )
        self.assertEqual(self.atencao(), [])

    def test_inativa_nao_aparece(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=5),
            responsavel=self.responsavel,
            ativo=False,
        )
        self.assertEqual(self.atencao(), [])


class ExecucaoNaoRegistradaTests(BaseDeAtencaoTests):
    """A ausência de execução é dita como é — sem inventar o motivo."""

    def test_vencida_sem_execucao_informa_ausencia(self):
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Limpeza da piscina",
            data_prevista=HOJE - timedelta(days=2),
            responsavel=self.responsavel,
        )
        itens = self.atencao()
        self.assertEqual(len(itens), 1)
        self.assertTrue(itens[0].execucao_nao_registrada)
        self.assertEqual(
            itens[0].mensagem, "Atrasada há 2 dias · execução não registrada"
        )

    def test_nao_inventa_fornecedor_nao_compareceu(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=1),
            responsavel=self.responsavel,
        )
        mensagem = self.atencao()[0].mensagem.casefold()
        self.assertIn("execução não registrada", mensagem)
        self.assertNotIn("fornecedor", mensagem)
        self.assertNotIn("compareceu", mensagem)


class ConferenciaTests(BaseDeAtencaoTests):
    """A conferência tem precedência sobre o prazo: o que falta é decidir."""

    def test_pendente_prevalece_sobre_atraso(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=4),
            responsavel=self.responsavel,
            exige_conferencia=True,
            status_conferencia=StatusConferencia.PENDENTE,
            data_execucao=timezone.now(),
        )
        itens = self.atencao()
        self.assertEqual([item.tipo for item in itens], [
            TipoAtencao.AGUARDANDO_CONFERENCIA
        ])

    def test_reprovada_prevalece_sobre_pendente_e_atraso(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=4),
            responsavel=self.responsavel,
            exige_conferencia=True,
            status_conferencia=StatusConferencia.REPROVADA,
            data_execucao=timezone.now(),
        )
        itens = self.atencao()
        self.assertEqual([item.tipo for item in itens], [
            TipoAtencao.AGUARDANDO_CORRECAO
        ])

    def test_aprovada_nao_gera_atencao_de_conferencia(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE + timedelta(days=30),
            responsavel=self.responsavel,
            exige_conferencia=True,
            status_conferencia=StatusConferencia.APROVADA,
            data_execucao=timezone.now(),
        )
        # Aprovada deixa de ser conferência: o que resta é encerrar o registro.
        tipos = [item.tipo for item in self.atencao()]
        self.assertNotIn(TipoAtencao.AGUARDANDO_CONFERENCIA, tipos)

    def test_aprovada_e_vencida_vira_aguardando_conclusao(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=3),
            responsavel=self.responsavel,
            exige_conferencia=True,
            status_conferencia=StatusConferencia.APROVADA,
            data_execucao=timezone.now(),
        )
        itens = self.atencao()
        self.assertEqual([item.tipo for item in itens], [
            TipoAtencao.AGUARDANDO_CONCLUSAO
        ])


class AguardandoConclusaoTests(BaseDeAtencaoTests):
    """Executada e só faltando encerrar: o próximo passo é concluir, não prazo."""

    def test_executada_sem_conferencia_aguarda_conclusao(self):
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Troca da bomba",
            data_prevista=HOJE - timedelta(days=2),
            responsavel=self.responsavel,
            exige_conferencia=False,
            data_execucao=timezone.now(),
        )
        itens = self.atencao()
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0].tipo, TipoAtencao.AGUARDANDO_CONCLUSAO)
        self.assertEqual(itens[0].titulo, "Aguardando conclusão")
        self.assertEqual(
            itens[0].mensagem,
            "Execução registrada · falta concluir a manutenção",
        )
        self.assertEqual(itens[0].acao_sugerida, "Concluir a manutenção")

    def test_conferencia_aprovada_aguarda_conclusao(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=2),
            responsavel=self.responsavel,
            exige_conferencia=True,
            status_conferencia=StatusConferencia.APROVADA,
            data_execucao=timezone.now(),
        )
        itens = self.atencao()
        self.assertEqual([item.tipo for item in itens], [
            TipoAtencao.AGUARDANDO_CONCLUSAO
        ])

    def test_conferencia_pendente_prevalece(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=2),
            responsavel=self.responsavel,
            exige_conferencia=True,
            status_conferencia=StatusConferencia.PENDENTE,
            data_execucao=timezone.now(),
        )
        itens = self.atencao()
        self.assertEqual([item.tipo for item in itens], [
            TipoAtencao.AGUARDANDO_CONFERENCIA
        ])

    def test_conferencia_reprovada_prevalece(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=2),
            responsavel=self.responsavel,
            exige_conferencia=True,
            status_conferencia=StatusConferencia.REPROVADA,
            data_execucao=timezone.now(),
        )
        itens = self.atencao()
        self.assertEqual([item.tipo for item in itens], [
            TipoAtencao.AGUARDANDO_CORRECAO
        ])

    def test_prevalece_sobre_atraso(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=9),
            responsavel=self.responsavel,
            data_execucao=timezone.now(),
        )
        itens = self.atencao()
        self.assertEqual([item.tipo for item in itens], [
            TipoAtencao.AGUARDANDO_CONCLUSAO
        ])

    def test_nao_marca_execucao_nao_registrada(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=9),
            responsavel=self.responsavel,
            data_execucao=timezone.now(),
        )
        item = self.atencao()[0]
        self.assertFalse(item.execucao_nao_registrada)
        self.assertNotIn("execução não registrada", item.mensagem)
        self.assertNotIn("Atrasada", item.mensagem)

    def test_sem_responsavel_compce_sem_duplicar(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=2),
            responsavel=None,
            data_execucao=timezone.now(),
        )
        itens = self.atencao()
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0].tipo, TipoAtencao.AGUARDANDO_CONCLUSAO)
        self.assertTrue(itens[0].sem_responsavel)
        self.assertEqual(
            itens[0].mensagem,
            "Execução registrada · falta concluir a manutenção · sem responsável",
        )

    def test_concluida_nao_gera_atencao(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=5),
            responsavel=self.responsavel,
            data_execucao=timezone.now(),
            status=StatusManutencao.CONCLUIDA,
            data_conclusao=timezone.now(),
        )
        self.assertEqual(self.atencao(), [])

    def test_consulta_nao_persiste(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=2),
            responsavel=self.responsavel,
            data_execucao=timezone.now(),
        )
        historico = HistoricoManutencao.objects.count()
        self.atencao()
        self.assertEqual(HistoricoManutencao.objects.count(), historico)


class ResponsavelTests(BaseDeAtencaoTests):
    """Sem responsável é atenção própria — e compõe a mais crítica, sem duplicar."""

    def test_sem_responsavel_aparece_quando_nao_ha_fato_mais_critico(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE + timedelta(days=30),
            responsavel=None,
        )
        itens = self.atencao()
        self.assertEqual([item.tipo for item in itens], [TipoAtencao.SEM_RESPONSAVEL])
        self.assertTrue(itens[0].sem_responsavel)

    def test_sem_responsavel_compce_o_atraso_sem_duplicar(self):
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Limpeza da piscina",
            data_prevista=HOJE - timedelta(days=3),
            responsavel=None,
        )
        itens = self.atencao()
        self.assertEqual(len(itens), 1)
        self.assertEqual(itens[0].tipo, TipoAtencao.ATRASADA)
        self.assertTrue(itens[0].sem_responsavel)
        self.assertEqual(
            itens[0].mensagem,
            "Atrasada há 3 dias · execução não registrada · sem responsável",
        )


class OrdenacaoTests(BaseDeAtencaoTests):
    """A leitura entrega primeiro o que pede ação mais imediata."""

    def test_ordem_por_precedencia(self):
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Correção",
            data_prevista=HOJE - timedelta(days=1),
            responsavel=self.responsavel,
            exige_conferencia=True,
            status_conferencia=StatusConferencia.REPROVADA,
            data_execucao=timezone.now(),
        )
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Conferência",
            data_prevista=HOJE - timedelta(days=1),
            responsavel=self.responsavel,
            exige_conferencia=True,
            status_conferencia=StatusConferencia.PENDENTE,
            data_execucao=timezone.now(),
        )
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Conclusão",
            data_prevista=HOJE - timedelta(days=1),
            responsavel=self.responsavel,
            data_execucao=timezone.now(),
        )
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Atraso",
            data_prevista=HOJE - timedelta(days=2),
            responsavel=self.responsavel,
        )
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Hoje",
            data_prevista=HOJE,
            responsavel=self.responsavel,
        )
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Próxima",
            data_prevista=HOJE + timedelta(days=1),
            responsavel=self.responsavel,
        )

        self.assertEqual(
            [item.tipo for item in self.atencao()],
            [
                TipoAtencao.AGUARDANDO_CORRECAO,
                TipoAtencao.AGUARDANDO_CONFERENCIA,
                TipoAtencao.AGUARDANDO_CONCLUSAO,
                TipoAtencao.ATRASADA,
                TipoAtencao.HOJE,
                TipoAtencao.PROXIMA,
            ],
        )

    def test_atraso_maior_vem_primeiro(self):
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Atraso pequeno",
            data_prevista=HOJE - timedelta(days=1),
            responsavel=self.responsavel,
        )
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Atraso grande",
            data_prevista=HOJE - timedelta(days=9),
            responsavel=self.responsavel,
        )
        titulos = [item.manutencao.titulo for item in self.atencao()]
        self.assertEqual(titulos, ["Atraso grande", "Atraso pequeno"])


class IsolamentoTests(TestCase):
    """Nenhuma atenção atravessa a fronteira do condomínio."""

    def setUp(self):
        self.alpha = criar_condominio("Alpha")
        self.beta = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.alpha, Papel.SINDICA)

    def test_consulta_por_usuario_nao_traz_outro_condominio(self):
        _manutencao(
            self.alpha,
            self.gestao,
            titulo="Da Alpha",
            data_prevista=HOJE - timedelta(days=2),
        )
        _manutencao(
            self.beta,
            self.gestao,
            titulo="Da Beta",
            data_prevista=HOJE - timedelta(days=2),
        )
        itens = obter_atencoes_de_manutencoes(usuario=self.gestao, hoje=HOJE)
        self.assertEqual([item.manutencao.titulo for item in itens], ["Da Alpha"])

    def test_consulta_por_lista_de_condominios_respeita_o_recorte(self):
        _manutencao(
            self.alpha,
            self.gestao,
            titulo="Da Alpha",
            data_prevista=HOJE - timedelta(days=2),
        )
        _manutencao(
            self.beta,
            self.gestao,
            titulo="Da Beta",
            data_prevista=HOJE - timedelta(days=2),
        )
        itens = obter_atencoes_de_manutencoes(condominios=[self.alpha], hoje=HOJE)
        self.assertEqual([item.manutencao.titulo for item in itens], ["Da Alpha"])

    def test_sem_escopo_a_resposta_e_vazia(self):
        _manutencao(
            self.alpha,
            self.gestao,
            data_prevista=HOJE - timedelta(days=2),
        )
        self.assertEqual(obter_atencoes_de_manutencoes(hoje=HOJE), [])


class DataDeReferenciaTests(BaseDeAtencaoTests):
    """O cálculo vem da data informada, nunca do relógio."""

    def test_mesma_manutencao_muda_com_a_referencia(self):
        _manutencao(
            self.condominio,
            self.gestao,
            titulo="Limpeza da piscina",
            data_prevista=HOJE,
            responsavel=self.responsavel,
        )
        hoje = obter_atencoes_de_manutencoes(
            condominios=[self.condominio], hoje=HOJE
        )
        self.assertEqual([item.tipo for item in hoje], [TipoAtencao.HOJE])

        antes = obter_atencoes_de_manutencoes(
            condominios=[self.condominio], hoje=HOJE - timedelta(days=1)
        )
        self.assertEqual([item.tipo for item in antes], [TipoAtencao.PROXIMA])

    def test_sem_referencia_usa_o_dia_de_hoje(self):
        itens = obter_atencoes_de_manutencoes(condominios=[self.condominio])
        self.assertIsInstance(itens, list)


class PersistenciaTests(BaseDeAtencaoTests):
    """Consultar atenção é leitura pura: nada é gravado."""

    def test_consulta_nao_grava(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=2),
            responsavel=self.responsavel,
        )
        manutencoes = Manutencao.objects.count()
        historico = HistoricoManutencao.objects.count()

        self.atencao()

        self.assertEqual(Manutencao.objects.count(), manutencoes)
        self.assertEqual(HistoricoManutencao.objects.count(), historico)


class PerformanceTests(BaseDeAtencaoTests):
    """Uma leitura, uma consulta — sem uma ida ao banco por manutenção."""

    def test_uma_consulta_para_varias_manutencoes(self):
        for indice in range(5):
            _manutencao(
                self.condominio,
                self.gestao,
                titulo=f"Serviço {indice}",
                data_prevista=HOJE - timedelta(days=indice + 1),
                responsavel=self.responsavel,
            )
        with self.assertNumQueries(1):
            itens = obter_atencoes_de_manutencoes(
                condominios=[self.condominio], hoje=HOJE
            )
        self.assertEqual(len(itens), 5)


class ResumoTests(BaseDeAtencaoTests):
    """As contagens são derivadas das atenções, sem tabela de contador."""

    def test_contagens_por_tipo(self):
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=2),
            responsavel=self.responsavel,
            exige_conferencia=True,
            status_conferencia=StatusConferencia.REPROVADA,
            data_execucao=timezone.now(),
        )
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=1),
            responsavel=self.responsavel,
        )
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE - timedelta(days=1),
            responsavel=self.responsavel,
            data_execucao=timezone.now(),
        )
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE,
            responsavel=self.responsavel,
        )
        _manutencao(
            self.condominio,
            self.gestao,
            data_prevista=HOJE + timedelta(days=2),
            responsavel=self.responsavel,
        )

        resumo = ResumoDeAtencoes.de(self.atencao())
        self.assertEqual(resumo.total, 5)
        self.assertEqual(resumo.atrasadas, 1)
        self.assertEqual(resumo.hoje, 1)
        self.assertEqual(resumo.proximas, 1)
        self.assertEqual(resumo.aguardando_correcao, 1)
        self.assertEqual(resumo.aguardando_conclusao, 1)
        self.assertEqual(resumo.aguardando_conferencia, 0)

    def test_resumo_vazio(self):
        resumo = ResumoDeAtencoes.de([])
        self.assertEqual(resumo.total, 0)
