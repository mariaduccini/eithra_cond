"""Telas de rotinas: listagem, detalhe, filtros, ocorrências e ações (§70)."""
from datetime import date, timedelta

from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.condominios.constants import Papel
from apps.core.constants import Prioridade
from apps.core.context_processors import navigation
from apps.demandas.models import Demanda
from apps.manutencoes.models import Manutencao
from apps.rotinas.constants import (
    Periodicidade,
    ResultadoExecucao,
    StatusExecucao,
    TipoRotina,
)
from apps.rotinas.services import criar_rotina
from apps.rotinas.tests.apoio import (
    criar_condominio,
    criar_equipamento,
    criar_local,
    criar_pessoa,
    criar_vinculo,
)

SENHA = "senha-forte-123"


class BaseDeTelas(TestCase):
    """Base com uma pessoa de gestão ligada a um condomínio."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    def _rotina(self, **campos):
        base = {
            "condominio": self.condominio,
            "criado_por": self.gestao,
            "titulo": "Limpeza da caixa d'água",
            "data_inicio": date(2026, 1, 10),
            "periodicidade_tipo": Periodicidade.MENSAL,
            "dia_mes": 10,
        }
        base.update(campos)
        return criar_rotina(**base)

    def _rotina_diaria(self, titulo, data_inicio, **campos):
        """Uma rotina cuja ocorrência cai exatamente na data informada."""
        return self._rotina(
            titulo=titulo,
            data_inicio=data_inicio,
            periodicidade_tipo=Periodicidade.DIARIA,
            intervalo=1,
            dia_mes=None,
            **campos,
        )

    def _pendente(self, rotina):
        return rotina.execucoes.get(status=StatusExecucao.PENDENTE)

    def _dados_da_rotina(self, **campos):
        dados = {
            "condominio": str(self.condominio.pk),
            "titulo": "Limpeza da caixa d'água",
            "tipo": TipoRotina.LIMPEZA,
            "periodicidade_tipo": Periodicidade.MENSAL,
            "intervalo": "1",
            "dia_mes": "10",
            "data_inicio": "2026-01-10",
            "prioridade": Prioridade.NORMAL,
        }
        dados.update(campos)
        return dados


class AcessoAsTelasTests(BaseDeTelas):
    """Sem vínculo que enxergue o módulo, a resposta é 404."""

    def test_listagem_exige_login(self):
        self.client.logout()
        resposta = self.client.get(reverse("rotinas:listagem"))
        self.assertEqual(resposta.status_code, 302)

    def test_morador_recebe_404_na_listagem(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)
        self.assertEqual(
            self.client.get(reverse("rotinas:listagem")).status_code, 404
        )

    def test_porteiro_recebe_404_na_listagem(self):
        porteiro = criar_pessoa("porteiro@exemplo.test", "Porteiro")
        criar_vinculo(porteiro, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(porteiro)
        self.assertEqual(
            self.client.get(reverse("rotinas:listagem")).status_code, 404
        )

    def test_operacao_nao_abre_o_cadastro_da_rotina(self):
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        rotina = self._rotina()
        self.client.force_login(operacional)
        # A operação entra pela lista de ocorrências dela, não pelo cadastro.
        self.assertEqual(
            self.client.get(reverse("rotinas:execucoes")).status_code, 200
        )
        self.assertEqual(
            self.client.get(reverse("rotinas:detalhe", args=[rotina.pk])).status_code,
            404,
        )

    def test_detalhe_de_outro_condominio_e_404(self):
        outro = criar_condominio("Beta")
        alheia = criar_rotina(
            condominio=outro,
            criado_por=self.gestao,
            titulo="Do Beta",
            data_inicio=date(2026, 1, 10),
            periodicidade_tipo=Periodicidade.MENSAL,
            dia_mes=10,
        )
        resposta = self.client.get(reverse("rotinas:detalhe", args=[alheia.pk]))
        self.assertEqual(resposta.status_code, 404)

    def test_detalhe_autorizado_abre(self):
        rotina = self._rotina()
        resposta = self.client.get(reverse("rotinas:detalhe", args=[rotina.pk]))
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["rotina"]["pk"], rotina.pk)


class CriacaoTests(BaseDeTelas):
    """Registrar a rotina pela tela, já com a primeira ocorrência."""

    def test_criar_redireciona_para_o_detalhe(self):
        resposta = self.client.post(
            reverse("rotinas:criar"),
            self._dados_da_rotina(titulo="Limpeza trimestral da caixa d'água"),
        )
        self.assertEqual(resposta.status_code, 302)
        rotina = self._rotina_obj()
        self.assertEqual(rotina.titulo, "Limpeza trimestral da caixa d'água")
        self.assertEqual(rotina.execucoes.count(), 1)
        self.assertEqual(
            resposta.url, reverse("rotinas:detalhe", args=[rotina.pk])
        )

    def test_titulo_obrigatorio(self):
        resposta = self.client.post(
            reverse("rotinas:criar"), self._dados_da_rotina(titulo="")
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(resposta.context["modal_aberto"], "criar")
        self.assertEqual(
            len([r for r in resposta.context["rotinas"]]), 0
        )

    def test_morador_nao_cria(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)
        resposta = self.client.post(
            reverse("rotinas:criar"), self._dados_da_rotina()
        )
        self.assertEqual(resposta.status_code, 404)

    def test_get_nao_cria(self):
        resposta = self.client.get(reverse("rotinas:criar"))
        self.assertEqual(resposta.status_code, 302)

    def test_local_divergente_do_equipamento_e_recusado(self):
        casa = criar_local(self.condominio, "Casa de máquinas")
        telhado = criar_local(self.condominio, "Telhado")
        bomba = criar_equipamento(self.condominio, "Bomba", local=casa)
        resposta = self.client.post(
            reverse("rotinas:criar"),
            self._dados_da_rotina(
                equipamento=str(bomba.pk),
                local=str(telhado.pk),
            ),
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("local", resposta.context["formulario_rotina"].errors)

    def _rotina_obj(self):
        from apps.rotinas.models import RotinaRecorrente

        return RotinaRecorrente.objects.get()


class ListagemEFiltrosTests(BaseDeTelas):
    """Filtros essenciais da lista de rotinas."""

    def test_lista_traz_as_rotinas_do_condominio(self):
        self._rotina(titulo="Uma rotina")
        resposta = self.client.get(reverse("rotinas:listagem"))
        self.assertEqual(resposta.status_code, 200)
        titulos = [linha["titulo"] for linha in resposta.context["rotinas"]]
        self.assertIn("Uma rotina", titulos)

    def test_filtro_por_tipo(self):
        self._rotina(titulo="Uma limpeza", tipo=TipoRotina.LIMPEZA)
        self._rotina(titulo="Uma inspeção", tipo=TipoRotina.INSPECAO)
        resposta = self.client.get(
            reverse("rotinas:listagem"), {"tipo": TipoRotina.INSPECAO}
        )
        titulos = [linha["titulo"] for linha in resposta.context["rotinas"]]
        self.assertEqual(titulos, ["Uma inspeção"])

    def test_filtro_por_vencidas(self):
        self._rotina_diaria(
            "Vencida", timezone.localdate() - timedelta(days=3)
        )
        self._rotina_diaria("Vem por aí", timezone.localdate() + timedelta(days=3))
        resposta = self.client.get(
            reverse("rotinas:listagem"), {"situacao": "vencidas"}
        )
        titulos = [linha["titulo"] for linha in resposta.context["rotinas"]]
        self.assertEqual(titulos, ["Vencida"])

    def test_filtro_por_proximas(self):
        self._rotina_diaria(
            "Vencida", timezone.localdate() - timedelta(days=3)
        )
        self._rotina_diaria("Vem por aí", timezone.localdate() + timedelta(days=3))
        resposta = self.client.get(
            reverse("rotinas:listagem"), {"situacao": "proximas"}
        )
        titulos = [linha["titulo"] for linha in resposta.context["rotinas"]]
        self.assertEqual(titulos, ["Vem por aí"])

    def test_lista_inativas(self):
        rotina = self._rotina(titulo="Fora da operação")
        rotina.ativo = False
        rotina.save(update_fields=["ativo"])
        resposta = self.client.get(
            reverse("rotinas:listagem"), {"situacao": "inativas"}
        )
        titulos = [linha["titulo"] for linha in resposta.context["rotinas"]]
        self.assertEqual(titulos, ["Fora da operação"])

    def test_filtro_por_local_e_equipamento(self):
        casa = criar_local(self.condominio, "Casa de máquinas")
        bomba = criar_equipamento(self.condominio, "Bomba de recalque", local=casa)
        self._rotina(titulo="No local", local=casa, equipamento=bomba)
        self._rotina(titulo="Sem contexto")

        por_local = self.client.get(
            reverse("rotinas:listagem"), {"local": str(casa.pk)}
        )
        self.assertEqual(
            [linha["titulo"] for linha in por_local.context["rotinas"]],
            ["No local"],
        )

        por_equipamento = self.client.get(
            reverse("rotinas:listagem"), {"equipamento": str(bomba.pk)}
        )
        self.assertEqual(
            [linha["titulo"] for linha in por_equipamento.context["rotinas"]],
            ["No local"],
        )

    def test_busca_alcanca_titulo_e_descricao(self):
        self._rotina(titulo="Inspeção do gerador", descricao="Verificar o óleo.")
        self._rotina(titulo="Limpeza da caixa", descricao="Escovar as paredes.")

        por_titulo = self.client.get(
            reverse("rotinas:listagem"), {"busca": "gerador"}
        )
        self.assertEqual(
            [linha["titulo"] for linha in por_titulo.context["rotinas"]],
            ["Inspeção do gerador"],
        )

        por_descricao = self.client.get(
            reverse("rotinas:listagem"), {"busca": "paredes"}
        )
        self.assertEqual(
            [linha["titulo"] for linha in por_descricao.context["rotinas"]],
            ["Limpeza da caixa"],
        )


class ExecucoesTests(BaseDeTelas):
    """A lista de trabalho: próximas, atrasadas e as de um responsável."""

    def test_recorte_atrasadas(self):
        self._rotina_diaria(
            "Atrasada", timezone.localdate() - timedelta(days=2)
        )
        self._rotina_diaria("De hoje", timezone.localdate())
        resposta = self.client.get(
            reverse("rotinas:execucoes"), {"recorte": "atrasadas"}
        )
        self.assertEqual(resposta.status_code, 200)
        titulos = [linha["rotina"] for linha in resposta.context["execucoes"]]
        self.assertEqual(titulos, ["Atrasada"])

    def test_recorte_proximas(self):
        self._rotina_diaria(
            "Vem por aí", timezone.localdate() + timedelta(days=3)
        )
        self._rotina_diaria("De hoje", timezone.localdate())
        resposta = self.client.get(
            reverse("rotinas:execucoes"), {"recorte": "proximas"}
        )
        titulos = [linha["rotina"] for linha in resposta.context["execucoes"]]
        self.assertEqual(titulos, ["Vem por aí"])

    def test_recorte_de_hoje(self):
        self._rotina_diaria(
            "Atrasada", timezone.localdate() - timedelta(days=2)
        )
        self._rotina_diaria("De hoje", timezone.localdate())
        resposta = self.client.get(
            reverse("rotinas:execucoes"), {"recorte": "hoje"}
        )
        titulos = [linha["rotina"] for linha in resposta.context["execucoes"]]
        self.assertEqual(titulos, ["De hoje"])

    def test_minhas_ocorrencias_por_responsavel(self):
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        self._rotina_diaria("Minha", timezone.localdate(), responsavel=operacional)
        self._rotina_diaria("De outro", timezone.localdate())

        self.client.force_login(operacional)
        resposta = self.client.get(reverse("rotinas:execucoes"))
        titulos = [linha["rotina"] for linha in resposta.context["execucoes"]]
        self.assertEqual(titulos, ["Minha"])

    def test_operacao_nao_alcanca_ocorrencia_alheia(self):
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        rotina = self._rotina_diaria("Da gestão", timezone.localdate())
        execucao = self._pendente(rotina)

        self.client.force_login(operacional)
        resposta = self.client.post(
            reverse("rotinas:iniciar", args=[execucao.pk])
        )
        self.assertEqual(resposta.status_code, 404)


class AcoesTests(BaseDeTelas):
    """Editar, gerar ocorrência, ativar, começar, concluir, ignorar e cancelar."""

    def test_editar_atualiza_o_conteudo(self):
        rotina = self._rotina(titulo="Original")
        resposta = self.client.post(
            reverse("rotinas:editar", args=[rotina.pk]),
            self._dados_da_rotina(titulo="Atualizada", tipo=TipoRotina.INSPECAO),
        )
        self.assertEqual(resposta.status_code, 302)
        rotina.refresh_from_db()
        self.assertEqual(rotina.titulo, "Atualizada")
        self.assertEqual(rotina.tipo, TipoRotina.INSPECAO)

    def test_gerar_execucao_prepara_a_proxima(self):
        rotina = self._rotina()
        resposta = self.client.post(
            reverse("rotinas:executar_geracao", args=[rotina.pk])
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(rotina.execucoes.count(), 2)

    def test_alternar_ativo_retira_da_operacao(self):
        rotina = self._rotina()
        resposta = self.client.post(
            reverse("rotinas:alternar_ativo", args=[rotina.pk])
        )
        self.assertEqual(resposta.status_code, 302)
        rotina.refresh_from_db()
        self.assertFalse(rotina.ativo)

    def test_supervisora_nao_administra(self):
        supervisora = criar_pessoa("sup@exemplo.test", "Supervisão")
        criar_vinculo(supervisora, self.condominio, papel=Papel.SUPERVISORA)
        rotina = self._rotina()
        self.client.force_login(supervisora)
        resposta = self.client.post(
            reverse("rotinas:alternar_ativo", args=[rotina.pk])
        )
        self.assertEqual(resposta.status_code, 403)
        rotina.refresh_from_db()
        self.assertTrue(rotina.ativo)

    def test_iniciar_registra_quando(self):
        rotina = self._rotina()
        execucao = self._pendente(rotina)
        resposta = self.client.post(
            reverse("rotinas:iniciar", args=[execucao.pk])
        )
        self.assertEqual(resposta.status_code, 302)
        execucao.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.EM_ANDAMENTO)
        self.assertIsNotNone(execucao.iniciado_em)

    def test_iniciar_exige_post(self):
        rotina = self._rotina()
        execucao = self._pendente(rotina)
        resposta = self.client.get(reverse("rotinas:iniciar", args=[execucao.pk]))
        self.assertEqual(resposta.status_code, 405)

    def test_concluir_registra_o_resultado(self):
        rotina = self._rotina()
        execucao = self._pendente(rotina)
        resposta = self.client.post(
            reverse("rotinas:concluir", args=[execucao.pk]),
            {"resultado": ResultadoExecucao.OK, "observacao": "Caixa limpa."},
        )
        self.assertEqual(resposta.status_code, 302)
        execucao.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.CONCLUIDA)
        self.assertEqual(execucao.resultado, ResultadoExecucao.OK)
        self.assertIsNotNone(execucao.concluido_em)

    def test_concluir_sem_resultado_e_recusado(self):
        rotina = self._rotina()
        execucao = self._pendente(rotina)
        resposta = self.client.post(
            reverse("rotinas:concluir", args=[execucao.pk]), {"resultado": ""}
        )
        self.assertEqual(resposta.status_code, 302)
        execucao.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.PENDENTE)

    def test_ignorar_exige_justificativa_e_encerra(self):
        rotina = self._rotina()
        execucao = self._pendente(rotina)

        sem_motivo = self.client.post(
            reverse("rotinas:ignorar", args=[execucao.pk]), {"justificativa": ""}
        )
        self.assertEqual(sem_motivo.status_code, 302)
        execucao.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.PENDENTE)

        resposta = self.client.post(
            reverse("rotinas:ignorar", args=[execucao.pk]),
            {"justificativa": "Reservatório em manutenção."},
        )
        self.assertEqual(resposta.status_code, 302)
        execucao.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.IGNORADA)

    def test_cancelar_encerra_sem_cancelar_a_rotina(self):
        rotina = self._rotina()
        execucao = self._pendente(rotina)
        resposta = self.client.post(
            reverse("rotinas:cancelar", args=[execucao.pk]),
            {"justificativa": "Não vai acontecer neste ciclo."},
        )
        self.assertEqual(resposta.status_code, 302)
        execucao.refresh_from_db()
        rotina.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.CANCELADA)
        self.assertTrue(rotina.ativo)

    def test_operacional_responsavel_conclui(self):
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        rotina = self._rotina(responsavel=operacional)
        execucao = self._pendente(rotina)
        self.client.force_login(operacional)
        resposta = self.client.post(
            reverse("rotinas:concluir", args=[execucao.pk]),
            {"resultado": ResultadoExecucao.ATENCAO},
        )
        self.assertEqual(resposta.status_code, 302)
        execucao.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.CONCLUIDA)


class ConsequenciaTests(BaseDeTelas):
    """Abrir demanda ou manutenção a partir da ocorrência, só na confirmação."""

    def test_criar_demanda_a_partir_da_execucao(self):
        rotina = self._rotina()
        execucao = self._pendente(rotina)
        resposta = self.client.post(
            reverse("rotinas:demanda", args=[execucao.pk]),
            {
                "titulo": "Vazamento na caixa",
                "descricao": "Encontrado durante a limpeza.",
                "prioridade": Prioridade.ALTA,
            },
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(Demanda.objects.count(), 1)
        execucao.refresh_from_db()
        self.assertEqual(
            resposta.url,
            reverse("demandas:detalhe", args=[execucao.demanda_gerada_id]),
        )

    def test_criar_manutencao_a_partir_da_execucao(self):
        rotina = self._rotina()
        execucao = self._pendente(rotina)
        resposta = self.client.post(
            reverse("rotinas:manutencao", args=[execucao.pk]),
            {"titulo": "Revisar bomba", "descricao_problema": "Ruído acima do normal."},
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(Manutencao.objects.count(), 1)
        execucao.refresh_from_db()
        self.assertEqual(
            resposta.url,
            reverse("manutencoes:detalhe", args=[execucao.manutencao_gerada_id]),
        )

    def test_operacao_nao_abre_demanda(self):
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        rotina = self._rotina(responsavel=operacional)
        execucao = self._pendente(rotina)
        self.client.force_login(operacional)
        resposta = self.client.post(
            reverse("rotinas:demanda", args=[execucao.pk]),
            {"titulo": "Tentativa", "prioridade": Prioridade.NORMAL},
        )
        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(Demanda.objects.count(), 0)


class MenuTests(BaseDeTelas):
    """O item de menu aparece para quem enxerga o módulo, e só para esses."""

    def _labels(self, usuario):
        request = RequestFactory().get("/")
        request.user = usuario
        return [item["label"] for item in navigation(request)["navigation"]]

    def test_gestao_recebe_o_item(self):
        self.assertIn("Rotinas", self._labels(self.gestao))

    def test_morador_nao_recebe_o_item(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.assertNotIn("Rotinas", self._labels(morador))


class MobileTests(BaseDeTelas):
    """A tela serve o desktop e o celular: tabela de um lado, cards do outro."""

    def test_listagem_tem_tabela_e_cards(self):
        self._rotina()
        resposta = self.client.get(reverse("rotinas:listagem"))
        self.assertContains(resposta, "d-none d-lg-block")
        self.assertContains(resposta, "d-lg-none")

    def test_execucoes_tem_tabela_e_cards(self):
        self._rotina()
        resposta = self.client.get(reverse("rotinas:execucoes"))
        self.assertContains(resposta, "d-none d-lg-block")
        self.assertContains(resposta, "d-lg-none")
