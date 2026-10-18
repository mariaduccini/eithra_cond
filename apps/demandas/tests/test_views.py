"""Telas de demanda: listagem, detalhe e ações (§17, §21, §23, §28, §29)."""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.condominios.constants import Papel
from apps.demandas.constants import PrioridadeDemanda, StatusDemanda
from apps.demandas.models import Demanda
from apps.demandas.services import alterar_status, criar_demanda
from apps.demandas.tests.apoio import criar_condominio, criar_pessoa, criar_vinculo

SENHA = "senha-forte-123"


class BaseDeTelas(TestCase):
    """Base com uma pessoa de gestão ligada a um condomínio."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    def _demanda(self, **campos):
        # A demanda nasce em "Aberta"; quando o cenário pede outra situação, ela
        # é movida pelo mesmo caminho que a gestão usaria — nada é escrito à mão.
        status = campos.pop("status", None)
        base = {
            "condominio": self.condominio,
            "criado_por": self.gestao,
            "titulo": "Demanda de teste",
        }
        base.update(campos)
        demanda = criar_demanda(**base)
        if status and status != StatusDemanda.ABERTA:
            alterar_status(demanda=demanda, usuario=self.gestao, novo_status=status)
        return demanda


class AcessoAsTelasTests(BaseDeTelas):
    """Sem vínculo que enxergue o módulo, a resposta é 404 (§14)."""

    def test_listagem_exige_login(self):
        self.client.logout()
        resposta = self.client.get(reverse("demandas:listagem"))
        self.assertEqual(resposta.status_code, 302)

    def test_morador_recebe_404_na_listagem(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)
        self.assertEqual(self.client.get(reverse("demandas:listagem")).status_code, 404)

    def test_porteiro_recebe_404_na_listagem(self):
        porteiro = criar_pessoa("porteiro@exemplo.test", "Porteiro")
        criar_vinculo(porteiro, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(porteiro)
        self.assertEqual(self.client.get(reverse("demandas:listagem")).status_code, 404)

    def test_detalhe_de_outro_condominio_e_404(self):
        outro = criar_condominio("Beta")
        alheia = Demanda.objects.create(
            condominio=outro, titulo="Alheia", criado_por=self.gestao
        )
        resposta = self.client.get(reverse("demandas:detalhe", args=[alheia.pk]))
        self.assertEqual(resposta.status_code, 404)

    def test_detalhe_autorizado_abre(self):
        demanda = self._demanda()
        resposta = self.client.get(reverse("demandas:detalhe", args=[demanda.pk]))
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["demanda"]["pk"], demanda.pk)


class CriacaoTests(BaseDeTelas):
    """Registrar demanda pela tela (§16, §35)."""

    def test_criar_redireciona_para_o_detalhe(self):
        resposta = self.client.post(
            reverse("demandas:criar"),
            {
                "condominio": str(self.condominio.pk),
                "titulo": "Vazamento na garagem",
                "prioridade": PrioridadeDemanda.NORMAL,
            },
        )
        self.assertEqual(resposta.status_code, 302)
        demanda = Demanda.objects.get()
        self.assertEqual(demanda.titulo, "Vazamento na garagem")
        self.assertEqual(resposta.url, reverse("demandas:detalhe", args=[demanda.pk]))

    def test_titulo_obrigatorio(self):
        resposta = self.client.post(
            reverse("demandas:criar"),
            {
                "condominio": str(self.condominio.pk),
                "titulo": "",
                "prioridade": PrioridadeDemanda.NORMAL,
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(resposta.context["modal_aberto"], "criar")

    def test_responsavel_de_outro_condominio_e_recusado(self):
        outro = criar_condominio("Beta")
        alheio = criar_pessoa("alheio@exemplo.test", "Alheio")
        criar_vinculo(alheio, outro, papel=Papel.SINDICA)

        resposta = self.client.post(
            reverse("demandas:criar"),
            {
                "condominio": str(self.condominio.pk),
                "titulo": "Com responsável de fora",
                "prioridade": PrioridadeDemanda.NORMAL,
                "responsavel": str(alheio.pk),
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("responsavel", resposta.context["formulario_demanda"].errors)

    def test_morador_nao_cria(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)
        resposta = self.client.post(
            reverse("demandas:criar"),
            {"condominio": str(self.condominio.pk), "titulo": "Tentativa"},
        )
        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(Demanda.objects.count(), 0)

    def test_get_nao_cria(self):
        resposta = self.client.get(reverse("demandas:criar"))
        self.assertEqual(resposta.status_code, 302)


class ListagemEFiltrosTests(BaseDeTelas):
    """Filtros essenciais, busca e "minhas demandas" (§23, §24, §25)."""

    def test_lista_traz_as_demandas_do_condominio(self):
        self._demanda(titulo="Uma demanda")
        resposta = self.client.get(reverse("demandas:listagem"))
        self.assertEqual(resposta.status_code, 200)
        titulos = [linha["titulo"] for linha in resposta.context["demandas"]]
        self.assertIn("Uma demanda", titulos)

    def test_filtro_por_status(self):
        self._demanda(titulo="Aberta")
        self._demanda(titulo="Em andamento", status=StatusDemanda.EM_ANDAMENTO)

        resposta = self.client.get(
            reverse("demandas:listagem"), {"status": StatusDemanda.EM_ANDAMENTO}
        )
        titulos = [linha["titulo"] for linha in resposta.context["demandas"]]
        self.assertEqual(titulos, ["Em andamento"])

    def test_filtro_por_prioridade(self):
        self._demanda(titulo="Urgente", prioridade=PrioridadeDemanda.URGENTE)
        self._demanda(titulo="Normal")

        resposta = self.client.get(
            reverse("demandas:listagem"), {"prioridade": PrioridadeDemanda.URGENTE}
        )
        titulos = [linha["titulo"] for linha in resposta.context["demandas"]]
        self.assertEqual(titulos, ["Urgente"])

    def test_filtro_por_responsavel(self):
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        self._demanda(titulo="Do operacional", responsavel=operacional)
        self._demanda(titulo="Sem responsável")

        resposta = self.client.get(
            reverse("demandas:listagem"), {"responsavel": str(operacional.pk)}
        )
        titulos = [linha["titulo"] for linha in resposta.context["demandas"]]
        self.assertEqual(titulos, ["Do operacional"])

    def test_busca_encontra_por_descricao_e_local(self):
        self._demanda(titulo="Limpeza", descricao="troca do filtro da piscina")
        self._demanda(titulo="Pintura", local_contexto="piscina")

        resposta = self.client.get(reverse("demandas:listagem"), {"busca": "piscina"})
        titulos = {linha["titulo"] for linha in resposta.context["demandas"]}
        self.assertEqual(titulos, {"Limpeza", "Pintura"})

    def test_minhas_demandas(self):
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        self._demanda(titulo="Minha", responsavel=operacional)
        self._demanda(titulo="De outro")

        self.client.force_login(operacional)
        resposta = self.client.get(reverse("demandas:listagem"), {"minhas": "on"})
        titulos = [linha["titulo"] for linha in resposta.context["demandas"]]
        self.assertEqual(titulos, ["Minha"])

    def test_demandas_vencidas_aparecem_na_frente(self):
        self._demanda(titulo="Vencida", prazo=timezone.localdate() - timedelta(days=1))
        self._demanda(titulo="Futura", prazo=timezone.localdate() + timedelta(days=5))

        resposta = self.client.get(reverse("demandas:listagem"))
        titulos = [linha["titulo"] for linha in resposta.context["demandas"]]
        self.assertEqual(titulos[0], "Vencida")


class AcoesTests(BaseDeTelas):
    """Editar, mudar situação e observar pela tela (§20, §29)."""

    def test_editar_atualiza_o_conteudo(self):
        demanda = self._demanda(titulo="Original")
        resposta = self.client.post(
            reverse("demandas:editar", args=[demanda.pk]),
            {
                "condominio": str(self.condominio.pk),
                "titulo": "Atualizada",
                "prioridade": PrioridadeDemanda.ALTA,
            },
        )
        self.assertEqual(resposta.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.titulo, "Atualizada")
        self.assertEqual(demanda.prioridade, PrioridadeDemanda.ALTA)

    def test_supervisora_nao_edita(self):
        supervisora = criar_pessoa("sup@exemplo.test", "Supervisora")
        criar_vinculo(supervisora, self.condominio, papel=Papel.SUPERVISORA)
        demanda = self._demanda()
        self.client.force_login(supervisora)
        resposta = self.client.post(
            reverse("demandas:editar", args=[demanda.pk]),
            {
                "condominio": str(self.condominio.pk),
                "titulo": "Tentativa",
                "prioridade": PrioridadeDemanda.NORMAL,
            },
        )
        self.assertEqual(resposta.status_code, 403)

    def test_mudar_status(self):
        demanda = self._demanda()
        resposta = self.client.post(
            reverse("demandas:mudar_status", args=[demanda.pk]),
            {"status": StatusDemanda.EM_ANDAMENTO},
        )
        self.assertEqual(resposta.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.EM_ANDAMENTO)

    def test_mudar_status_exige_post(self):
        demanda = self._demanda()
        resposta = self.client.get(reverse("demandas:mudar_status", args=[demanda.pk]))
        self.assertEqual(resposta.status_code, 405)

    def test_observar_registra_no_historico(self):
        demanda = self._demanda()
        resposta = self.client.post(
            reverse("demandas:observar", args=[demanda.pk]),
            {"texto": "Orçamento pedido ao fornecedor"},
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(demanda.historico.count(), 2)

    def test_operacional_observa_mas_nao_cancela(self):
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        demanda = self._demanda()
        self.client.force_login(operacional)

        observacao = self.client.post(
            reverse("demandas:observar", args=[demanda.pk]), {"texto": "Vi no local"}
        )
        self.assertEqual(observacao.status_code, 302)

        cancelamento = self.client.post(
            reverse("demandas:mudar_status", args=[demanda.pk]),
            {"status": StatusDemanda.CANCELADA},
        )
        self.assertEqual(cancelamento.status_code, 400)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.ABERTA)
