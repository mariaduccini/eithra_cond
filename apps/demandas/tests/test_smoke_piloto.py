"""Smoke test ponta a ponta do módulo de Demandas, para o piloto (§9).

Percorre o fluxo real que a gestão usa, da criação à conclusão, exercitando o
wizard de três etapas, a escolha manual da prioridade, as ações do detalhe e a
listagem. É um teste de integração: fala com as views de verdade, pelo mesmo
caminho que o navegador falaria — nada de chamar serviços por baixo.

Roda no banco de teste, que é criado e descartado a cada execução: nenhum dado
real é tocado e nenhum resíduo sobra. Os títulos levam o prefixo ``[HML]``
porque o roteiro é o de uma carga de homologação — se algum dia este fluxo for
rodado à mão em um ambiente com dados, o prefixo deixa o que é de teste
inequívoco.
"""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.demandas.constants import PrioridadeDemanda, StatusDemanda, TipoEventoDemanda
from apps.demandas.models import Demanda, EvidenciaDemanda
from apps.demandas.tests.apoio import (
    StorageDeMentira,
    criar_condominio,
    criar_local,
    criar_pessoa,
    criar_vinculo,
    png,
)

# O relato carrega palavras que, no desenho antigo, fariam a leitura escolher
# prioridade alta/urgente. A escolha humana é "Baixa" — e é ela que deve valer.
RELATO = "[HML] portão externo travando, situação urgente e crítica na garagem"


class FluxoDePontaAPontaTests(TestCase):
    """O roteiro completo do §9, do relato à conclusão, com a lista no fim."""

    def setUp(self):
        self.condominio = criar_condominio("[HML] Condomínio Piloto")
        self.gestao = criar_pessoa("hml.gestao@exemplo.test", "[HML] Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.local = criar_local(self.condominio, "[HML] Garagem")
        self.client.force_login(self.gestao)

        self.provider = StorageDeMentira()
        patcher = patch(
            "apps.demandas.evidencias.obter_provider", return_value=self.provider
        )
        self.patch_do_storage = patcher.start()
        self.addCleanup(patcher.stop)

    # -- Passos do roteiro ----------------------------------------------------

    def test_fluxo_completo_ate_a_conclusao(self):
        # 1. Criar demanda pelo wizard: descrição → informações → revisão.
        primeira = self.client.post(
            reverse("demandas:criar"),
            {
                "condominio": str(self.condominio.pk),
                "relato": RELATO,
                "wizard": "descricao",
            },
        )
        self.assertEqual(primeira.status_code, 200)
        self.assertEqual(primeira.context["registro_etapa"], "informacoes")
        self.assertEqual(Demanda.objects.count(), 0)

        segunda = self.client.post(
            reverse("demandas:criar"),
            {
                "condominio": str(self.condominio.pk),
                "relato": RELATO,
                "titulo": "[HML] Portão externo travando",
                "local": str(self.local.pk),
                "wizard": "informacoes",
            },
        )
        self.assertEqual(segunda.context["registro_etapa"], "revisao")

        # A revisão mostra a prioridade antes de salvar (§2, etapa 3).
        self.assertIn("prioridade", segunda.context["revisao"])

        # 2. A prioridade é informada à mão: "Baixa", apesar do relato urgente.
        final = self.client.post(
            reverse("demandas:criar"),
            {
                "condominio": str(self.condominio.pk),
                "relato": RELATO,
                "titulo": "[HML] Portão externo travando",
                "local": str(self.local.pk),
                "prioridade": PrioridadeDemanda.BAIXA,
            },
        )
        self.assertEqual(final.status_code, 302)

        demanda = Demanda.objects.get()
        # 3. A escolha do usuário venceu a leitura do relato.
        self.assertEqual(demanda.prioridade, PrioridadeDemanda.BAIXA)
        self.assertEqual(demanda.status, StatusDemanda.ABERTA)
        # E nenhum prazo foi inventado a partir da prioridade (§3).
        self.assertIsNone(demanda.prazo)

        # O registro nasce com histórico (§8).
        self.assertTrue(
            demanda.historico.filter(tipo_evento=TipoEventoDemanda.CRIADA).exists()
        )

        # 4. Abrir o detalhe: leitura em seções contínuas (§4).
        detalhe = self.client.get(reverse("demandas:detalhe", args=[demanda.pk]))
        self.assertEqual(detalhe.status_code, 200)
        conteudo = detalhe.content.decode()
        for secao in (">Demanda</h2>", ">Informações</h2>", ">Acompanhamento</h2>", ">Histórico</h2>"):
            self.assertIn(secao, conteudo)

        # 5. Registrar atualização — a demanda passa a "Em andamento".
        atualizacao = self.client.post(
            reverse("demandas:atualizar", args=[demanda.pk]),
            {
                "acao": StatusDemanda.EM_ANDAMENTO,
                "texto": "Chamei o técnico e acompanho a visita.",
                "etapa_atualizacao": "conclusao",
            },
        )
        self.assertEqual(atualizacao.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.EM_ANDAMENTO)

        # 6. Alterar o acompanhamento quando permitido (§7).
        acompanhamento = self.client.post(
            reverse("demandas:acompanhar", args=[demanda.pk]),
            {
                "aguardando_tipo": "fornecedor",
                "aguardando_descricao": "[HML] Atlas Elevadores",
                "proxima_acao": "[HML] Confirmar a visita técnica",
                "previsao": "",
                "impedimento": "",
                "responsavel_acompanhamento": str(self.gestao.pk),
            },
        )
        self.assertEqual(acompanhamento.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.responsavel_acompanhamento_id, self.gestao.pk)

        # 7. Adicionar evidência pelo fluxo que já suporta o anexo (§5).
        evidencia = self.client.post(
            reverse("demandas:adicionar_evidencia", args=[demanda.pk]),
            {"evidencias": [png(marca="hml")]},
        )
        self.assertEqual(evidencia.status_code, 302)
        self.assertEqual(EvidenciaDemanda.objects.filter(demanda=demanda).count(), 1)

        # 8. Ajustar a prioridade depois, conforme permissão (§1, §10).
        edicao = self.client.post(
            reverse("demandas:editar", args=[demanda.pk]),
            {
                "condominio": str(self.condominio.pk),
                "titulo": demanda.titulo,
                "descricao": demanda.descricao,
                "prioridade": PrioridadeDemanda.ALTA,
            },
        )
        self.assertEqual(edicao.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.prioridade, PrioridadeDemanda.ALTA)

        # 9. Concluir a demanda.
        conclusao = self.client.post(
            reverse("demandas:atualizar", args=[demanda.pk]),
            {
                "acao": StatusDemanda.CONCLUIDA,
                "texto": "Portão reparado e testado.",
                "etapa_atualizacao": "conclusao",
            },
        )
        self.assertEqual(conclusao.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.CONCLUIDA)

        # 10. Confirmar o histórico: cada decisão deixou o seu evento (§8).
        tipos = set(demanda.historico.values_list("tipo_evento", flat=True))
        self.assertIn(TipoEventoDemanda.CRIADA, tipos)
        self.assertIn(TipoEventoDemanda.STATUS, tipos)
        self.assertIn(TipoEventoDemanda.PRIORIDADE, tipos)
        self.assertIn(TipoEventoDemanda.EVIDENCIA, tipos)
        self.assertIn(TipoEventoDemanda.ACOMPANHAMENTO, tipos)

        # A alteração de prioridade guarda o antes e o depois.
        evento = demanda.historico.filter(
            tipo_evento=TipoEventoDemanda.PRIORIDADE
        ).latest("data_hora")
        self.assertEqual(evento.dados_anteriores.get("prioridade"), PrioridadeDemanda.BAIXA)
        self.assertEqual(evento.dados_novos.get("prioridade"), PrioridadeDemanda.ALTA)

        # 11. A listagem e os indicadores refletem o estado final.
        listagem = self.client.get(reverse("demandas:listagem"))
        self.assertEqual(listagem.status_code, 200)
        indicadores = {
            item["titulo"]: item["valor"] for item in listagem.context["indicadores"]
        }
        self.assertEqual(indicadores["Concluídas no mês"], 1)
        self.assertEqual(indicadores["Em aberto"], 0)
        titulos = [linha["titulo"] for linha in listagem.context["demandas"]]
        self.assertIn("[HML] Portão externo travando", titulos)
