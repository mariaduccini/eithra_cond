"""Smoke test ponta a ponta do módulo de Demandas, para o piloto (§9).

Percorre o fluxo real que a gestão usa, da criação à conclusão, exercitando o
wizard de três etapas, a prioridade calculada pelo motor e a correção manual, as
ações do detalhe e a listagem. É um teste de integração: fala com as views de
verdade, pelo mesmo caminho que o navegador falaria — nada de chamar serviços
por baixo.

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

# O relato carrega o portão — equipamento essencial — e a palavra de falha
# "travou": pela regra estruturada, o motor sustenta "Alta" sozinho. As palavras
# "urgente e crítica" não promovem nada por si (§2): quem decide o peso é o
# sinal estruturado, não o adjetivo no texto.
RELATO = "[HML] portão externo travou, situação urgente e crítica na garagem"


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

        # 2. A prioridade NÃO é informada à mão: o motor calcula pela regra
        # estruturada. O portão essencial travado sustenta "Alta" sem que
        # ninguém escolha — as palavras "urgente e crítica" do relato não
        # promovem nada por si.
        final = self.client.post(
            reverse("demandas:criar"),
            {
                "condominio": str(self.condominio.pk),
                "relato": RELATO,
                "titulo": "[HML] Portão externo travando",
                "local": str(self.local.pk),
            },
        )
        self.assertEqual(final.status_code, 302)

        demanda = Demanda.objects.get()
        # 3. O peso veio do motor, sem escolha humana.
        self.assertEqual(demanda.prioridade, PrioridadeDemanda.ALTA)
        self.assertEqual(demanda.status, StatusDemanda.ABERTA)
        # E nenhum prazo foi inventado a partir da prioridade (§7).
        self.assertIsNone(demanda.prazo)

        # O registro nasce com histórico (§8).
        self.assertTrue(
            demanda.historico.filter(tipo_evento=TipoEventoDemanda.CRIADA).exists()
        )

        # 4. Abrir o detalhe: leitura em seções contínuas (§4).
        detalhe = self.client.get(reverse("demandas:detalhe", args=[demanda.pk]))
        self.assertEqual(detalhe.status_code, 200)
        conteudo = detalhe.content.decode()
        for secao in (">Resumo da demanda</h2>", ">Informações principais</h2>", ">Acompanhamento</h2>", ">Histórico</h2>"):
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

        # 8. Corrigir a prioridade depois, conforme permissão (§4).
        edicao = self.client.post(
            reverse("demandas:editar", args=[demanda.pk]),
            {
                "condominio": str(self.condominio.pk),
                "titulo": demanda.titulo,
                "descricao": demanda.descricao,
                "prioridade": PrioridadeDemanda.BAIXA,
            },
        )
        self.assertEqual(edicao.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.prioridade, PrioridadeDemanda.BAIXA)

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

        # A correção de prioridade guarda o antes e o depois.
        evento = demanda.historico.filter(
            tipo_evento=TipoEventoDemanda.PRIORIDADE
        ).latest("data_hora")
        self.assertEqual(evento.dados_anteriores.get("prioridade"), PrioridadeDemanda.ALTA)
        self.assertEqual(evento.dados_novos.get("prioridade"), PrioridadeDemanda.BAIXA)

        # 11. A listagem e os filtros rápidos refletem o estado final.
        listagem = self.client.get(reverse("demandas:listagem"))
        self.assertEqual(listagem.status_code, 200)
        chips = {chip["chave"]: chip["total"] for chip in listagem.context["chips"]}
        self.assertEqual(chips["concluidas"], 1)
        # Nada ficou atrasado: o chip de atrasadas está zerado.
        self.assertEqual(chips["atrasadas"], 0)
        # A lista separa o que ainda pede ação do que já foi encerrado (§5): a
        # demanda concluída sai da aba Ativas e passa a viver na aba Concluídas.
        ativas = [linha["titulo"] for linha in listagem.context["demandas"]]
        self.assertNotIn("[HML] Portão externo travando", ativas)

        concluidas = self.client.get(
            reverse("demandas:listagem"), {"aba": "concluidas"}
        )
        self.assertEqual(concluidas.status_code, 200)
        encerradas = [linha["titulo"] for linha in concluidas.context["demandas"]]
        self.assertIn("[HML] Portão externo travando", encerradas)
