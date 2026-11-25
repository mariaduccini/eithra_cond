"""Telas de demanda: listagem, detalhe e ações (§17, §21, §23, §28, §29)."""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.constants import TipoLocal
from apps.condominios.constants import Papel
from apps.demandas.constants import (
    ImpactoDemanda,
    PrioridadeDemanda,
    RiscoDemanda,
    StatusDemanda,
    TipoProblema,
)
from apps.demandas.models import Demanda
from apps.demandas.services import alterar_status, criar_demanda
from apps.manutencoes.services import criar_manutencao
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_equipamento,
    criar_local,
    criar_pessoa,
    criar_vinculo,
)
from apps.governanca.models import ContratoEquipamento, GarantiaEquipamento
from apps.governanca.tests.apoio import criar_contrato, criar_garantia

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

    def test_criar_redireciona_para_a_listagem(self):
        """Depois de registrar, a pessoa volta para a lista — e a demanda nova
        aparece no recorte operacional, sem filtro herdado que a esconda (§6).
        """
        resposta = self.client.post(
            reverse("demandas:criar"),
            {
                "condominio": str(self.condominio.pk),
                "titulo": "Vazamento na garagem",
            },
        )
        self.assertEqual(resposta.status_code, 302)
        demanda = Demanda.objects.get()
        self.assertEqual(demanda.titulo, "Vazamento na garagem")
        self.assertEqual(resposta.url, reverse("demandas:listagem"))

        listagem = self.client.get(resposta.url)
        self.assertEqual(listagem.status_code, 200)
        self.assertIn(
            "Vazamento na garagem",
            [linha["titulo"] for linha in listagem.context["demandas"]],
        )

    def test_titulo_obrigatorio(self):
        resposta = self.client.post(
            reverse("demandas:criar"),
            {
                "condominio": str(self.condominio.pk),
                "titulo": "",
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

    def test_cria_com_local_do_condominio(self):
        local = criar_local(self.condominio, "Garagem")
        resposta = self.client.post(
            reverse("demandas:criar"),
            {
                "condominio": str(self.condominio.pk),
                "titulo": "Vazamento na garagem",
                "local": str(local.pk),
            },
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(Demanda.objects.get().local, local)

    def test_local_de_outro_condominio_e_recusado(self):
        outro = criar_condominio("Beta")
        local_alheio = criar_local(outro, "Garagem do Beta")

        resposta = self.client.post(
            reverse("demandas:criar"),
            {
                "condominio": str(self.condominio.pk),
                "titulo": "Com local de fora",
                "local": str(local_alheio.pk),
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(Demanda.objects.count(), 0)
        self.assertIn("local", resposta.context["formulario_demanda"].errors)


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

    def test_filtro_por_local(self):
        local = criar_local(self.condominio, "Garagem G1")
        self._demanda(titulo="Da garagem", local=local)
        self._demanda(titulo="Sem local")

        resposta = self.client.get(
            reverse("demandas:listagem"), {"local": str(local.pk)}
        )
        titulos = [linha["titulo"] for linha in resposta.context["demandas"]]
        self.assertEqual(titulos, ["Da garagem"])

    def test_busca_encontra_por_descricao_e_local(self):
        self._demanda(titulo="Limpeza", descricao="troca do filtro da piscina")
        self._demanda(titulo="Pintura", local_contexto="piscina")

        resposta = self.client.get(reverse("demandas:listagem"), {"busca": "piscina"})
        titulos = {linha["titulo"] for linha in resposta.context["demandas"]}
        self.assertEqual(titulos, {"Limpeza", "Pintura"})

    def test_busca_encontra_por_local_cadastrado(self):
        local = criar_local(self.condominio, "Casa de máquinas")
        self._demanda(titulo="Barulho na bomba", local=local)
        self._demanda(titulo="Outra demanda")

        resposta = self.client.get(reverse("demandas:listagem"), {"busca": "máquinas"})
        titulos = [linha["titulo"] for linha in resposta.context["demandas"]]
        self.assertEqual(titulos, ["Barulho na bomba"])

    def test_linha_mostra_o_local_do_cadastro(self):
        local = criar_local(self.condominio, "Garagem")
        self._demanda(titulo="Com local", local=local, local_contexto="texto antigo")

        resposta = self.client.get(reverse("demandas:listagem"))
        self.assertEqual(resposta.context["demandas"][0]["local"], "Garagem")

    def test_linha_cai_no_texto_legado_sem_local(self):
        self._demanda(titulo="Legada", local_contexto="piscina")

        resposta = self.client.get(reverse("demandas:listagem"))
        self.assertEqual(resposta.context["demandas"][0]["local"], "piscina")

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

    def test_os_chips_substituem_os_antigos_cartoes_de_desempenho(self):
        """A lista trouxe filtros rápidos no lugar dos sete cartões de números."""
        self._demanda(titulo="Aberta")
        resposta = self.client.get(reverse("demandas:listagem"))

        self.assertNotIn("indicadores", resposta.context)
        self.assertEqual(
            [chip["chave"] for chip in resposta.context["chips"]],
            ["atrasadas", "esperando", "paradas", "concluidas"],
        )

    def test_o_contador_do_chip_segue_o_filtro_da_lista(self):
        """O número do chip é exatamente o número de linhas que ele mostra."""
        hoje = timezone.localdate()
        self._demanda(titulo="Aberta")
        self._demanda(
            titulo="Vencida",
            prazo=hoje - timedelta(days=1),
            status=StatusDemanda.EM_ANDAMENTO,
        )
        self._demanda(titulo="Andamento", status=StatusDemanda.EM_ANDAMENTO)
        self._demanda(titulo="Concluída", status=StatusDemanda.CONCLUIDA)

        sem_filtro = self.client.get(reverse("demandas:listagem"))
        chips = {chip["chave"]: chip["total"] for chip in sem_filtro.context["chips"]}
        self.assertEqual(chips["atrasadas"], 1)
        self.assertEqual(chips["concluidas"], 1)

        # Com um filtro de prioridade que não alcança ninguém, o contador zera
        # junto com a lista — o número nunca discorda das linhas.
        filtrada = self.client.get(
            reverse("demandas:listagem"), {"prioridade": PrioridadeDemanda.URGENTE}
        )
        chips_filtrados = {
            chip["chave"]: chip["total"] for chip in filtrada.context["chips"]
        }
        self.assertEqual(chips_filtrados["atrasadas"], 0)
        self.assertEqual(chips_filtrados["concluidas"], 0)


class ListagemPorAbasEOrdenacaoTests(BaseDeTelas):
    """Abas Ativas/Concluídas e ordenação pelas colunas da grade (§5–§12)."""

    def _abrir(self, **parametros):
        resposta = self.client.get(reverse("demandas:listagem"), parametros)
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def _titulos(self, resposta):
        return [linha["titulo"] for linha in resposta.context["demandas"]]

    # -- Separação entre ativas e concluídas ---------------------------------

    def test_ativas_trazem_so_o_que_ainda_pede_acao(self):
        self._demanda(titulo="Aberta")
        self._demanda(titulo="Andamento", status=StatusDemanda.EM_ANDAMENTO)
        self._demanda(titulo="Concluída", status=StatusDemanda.CONCLUIDA)
        self._demanda(titulo="Cancelada", status=StatusDemanda.CANCELADA)

        self.assertEqual(set(self._titulos(self._abrir())), {"Aberta", "Andamento"})

    def test_concluidas_trazem_encerradas(self):
        self._demanda(titulo="Aberta")
        self._demanda(titulo="Concluída", status=StatusDemanda.CONCLUIDA)
        self._demanda(titulo="Cancelada", status=StatusDemanda.CANCELADA)

        resposta = self._abrir(aba="concluidas")
        self.assertEqual(set(self._titulos(resposta)), {"Concluída", "Cancelada"})

    def test_ativa_nao_aparece_em_concluidas(self):
        self._demanda(titulo="Aberta")
        self.assertEqual(self._abrir(aba="concluidas").context["total"], 0)

    def test_aba_desconhecida_cai_nas_ativas(self):
        self._demanda(titulo="Aberta")
        self._demanda(titulo="Concluída", status=StatusDemanda.CONCLUIDA)
        self.assertEqual(self._titulos(self._abrir(aba="qualquer")), ["Aberta"])

    def test_trocar_de_aba_volta_a_primeira_pagina(self):
        for numero in range(1, 26):
            self._demanda(titulo=f"Ativa {numero:02d}")
        self._demanda(titulo="Concluída", status=StatusDemanda.CONCLUIDA)

        resposta = self._abrir(aba="concluidas", page=3)
        self.assertEqual(resposta.context["pagina_demandas"].number, 1)
        self.assertEqual(self._titulos(resposta), ["Concluída"])

    def test_concluidas_ordenam_pela_conclusao_mais_recente(self):
        agora = timezone.now()
        antiga = self._demanda(titulo="Concluída antiga", status=StatusDemanda.CONCLUIDA)
        recente = self._demanda(titulo="Concluída recente", status=StatusDemanda.CONCLUIDA)
        Demanda.objects.filter(pk=antiga.pk).update(concluido_em=agora - timedelta(days=5))
        Demanda.objects.filter(pk=recente.pk).update(concluido_em=agora)

        self.assertEqual(
            self._titulos(self._abrir(aba="concluidas")),
            ["Concluída recente", "Concluída antiga"],
        )

    # -- Ordenação por coluna ------------------------------------------------

    def test_ordena_por_titulo_nos_dois_sentidos(self):
        self._demanda(titulo="Banana")
        self._demanda(titulo="Abacaxi")
        self._demanda(titulo="Caju")

        self.assertEqual(
            self._titulos(self._abrir(ordenar="titulo")), ["Abacaxi", "Banana", "Caju"]
        )
        self.assertEqual(
            self._titulos(self._abrir(ordenar="-titulo")), ["Caju", "Banana", "Abacaxi"]
        )

    def test_ordena_por_local_e_deixa_o_vazio_no_fim(self):
        alfa = criar_local(self.condominio, "Alfa")
        zulu = criar_local(self.condominio, "Zulu")
        self._demanda(titulo="Da Zulu", local=zulu)
        self._demanda(titulo="Da Alfa", local=alfa)
        self._demanda(titulo="Sem local")

        self.assertEqual(
            self._titulos(self._abrir(ordenar="local")),
            ["Da Alfa", "Da Zulu", "Sem local"],
        )
        self.assertEqual(
            self._titulos(self._abrir(ordenar="-local")),
            ["Da Zulu", "Da Alfa", "Sem local"],
        )

    def test_prioridade_ordena_na_ordem_do_dominio(self):
        self._demanda(titulo="Baixa", prioridade=PrioridadeDemanda.BAIXA)
        self._demanda(titulo="Média", prioridade=PrioridadeDemanda.NORMAL)
        self._demanda(titulo="Alta", prioridade=PrioridadeDemanda.ALTA)
        self._demanda(titulo="Crítica", prioridade=PrioridadeDemanda.URGENTE)

        self.assertEqual(
            self._titulos(self._abrir(ordenar="prioridade")),
            ["Crítica", "Alta", "Média", "Baixa"],
        )
        self.assertEqual(
            self._titulos(self._abrir(ordenar="-prioridade")),
            ["Baixa", "Média", "Alta", "Crítica"],
        )

    def test_ordena_por_registrada_em_nos_dois_sentidos(self):
        primeira = self._demanda(titulo="Primeira")
        segunda = self._demanda(titulo="Segunda")
        agora = timezone.now()
        Demanda.objects.filter(pk=primeira.pk).update(criado_em=agora - timedelta(days=2))
        Demanda.objects.filter(pk=segunda.pk).update(criado_em=agora)

        self.assertEqual(
            self._titulos(self._abrir(ordenar="criado_em")), ["Primeira", "Segunda"]
        )
        self.assertEqual(
            self._titulos(self._abrir(ordenar="-criado_em")), ["Segunda", "Primeira"]
        )

    def test_ordena_por_prazo_e_deixa_o_vazio_no_fim(self):
        hoje = timezone.localdate()
        self._demanda(titulo="Sem prazo")
        self._demanda(titulo="Longe", prazo=hoje + timedelta(days=10))
        self._demanda(titulo="Perto", prazo=hoje + timedelta(days=1))

        self.assertEqual(
            self._titulos(self._abrir(ordenar="prazo")),
            ["Perto", "Longe", "Sem prazo"],
        )
        self.assertEqual(
            self._titulos(self._abrir(ordenar="-prazo")),
            ["Longe", "Perto", "Sem prazo"],
        )

    def test_situacao_ordena_na_ordem_do_ciclo(self):
        self._demanda(titulo="Aberta")
        self._demanda(titulo="Andamento", status=StatusDemanda.EM_ANDAMENTO)
        self._demanda(titulo="Aguardando", status=StatusDemanda.AGUARDANDO)

        self.assertEqual(
            self._titulos(self._abrir(ordenar="status")),
            ["Aberta", "Andamento", "Aguardando"],
        )
        self.assertEqual(
            self._titulos(self._abrir(ordenar="-status")),
            ["Aguardando", "Andamento", "Aberta"],
        )

    # -- Segurança do parâmetro de ordenação ---------------------------------

    def test_ordenar_invalido_volta_a_ordem_padrao(self):
        self._demanda(titulo="Vencida", prazo=timezone.localdate() - timedelta(days=1))
        self._demanda(titulo="Futura", prazo=timezone.localdate() + timedelta(days=5))

        resposta = self._abrir(ordenar="prioridade); DROP TABLE demandas; --")
        # O valor bruto nunca chega ao ORM: a lista cai na ordem de atenção.
        self.assertEqual(self._titulos(resposta), ["Vencida", "Futura"])
        self.assertEqual(resposta.context["ordenacao"]["chave"], "")

    def test_ordenar_por_coluna_desconhecida_e_ignorado(self):
        self._demanda(titulo="Um")
        resposta = self._abrir(ordenar="__class__")
        self.assertEqual(resposta.context["ordenacao"]["chave"], "")

    # -- Cabeçalhos ordenáveis ------------------------------------------------

    def test_so_a_coluna_ativa_mostra_o_sinal(self):
        self._demanda(titulo="Um")
        colunas = self._abrir(ordenar="titulo").context["ordenacao"]["colunas"]

        self.assertEqual(colunas["titulo"]["indicador"], "↑")
        for outra in ("local", "prioridade", "criado_em", "prazo", "status"):
            self.assertEqual(colunas[outra]["indicador"], "")

    def test_o_segundo_clique_inverte_a_direcao(self):
        self._demanda(titulo="Um")

        # Sem ordenação, "Registrada em" começa pela mais recente (decrescente).
        padrao = self._abrir().context["ordenacao"]["colunas"]["criado_em"]
        self.assertTrue(padrao["url"].endswith("ordenar=-criado_em"))

        desce = self._abrir(ordenar="-criado_em").context["ordenacao"]["colunas"]["criado_em"]
        self.assertTrue(desce["ativo"])
        self.assertEqual(desce["indicador"], "↓")
        self.assertTrue(desce["url"].endswith("ordenar=criado_em"))

        sobe = self._abrir(ordenar="criado_em").context["ordenacao"]["colunas"]["criado_em"]
        self.assertEqual(sobe["indicador"], "↑")

    def test_ordenar_preserva_a_busca_e_a_aba_e_zera_a_pagina(self):
        self._demanda(titulo="Barulho na garagem")
        resposta = self._abrir(busca="garagem", aba="ativas", ordenar="titulo", page=2)
        url_do_local = resposta.context["ordenacao"]["colunas"]["local"]["url"]

        self.assertIn("busca=garagem", url_do_local)
        self.assertIn("aba=ativas", url_do_local)
        self.assertIn("ordenar=local", url_do_local)
        self.assertNotIn("page=", url_do_local)

    def test_a_troca_de_aba_leva_a_busca_junto(self):
        self._demanda(titulo="Barulho na garagem")
        resposta = self._abrir(busca="garagem")
        self.assertContains(resposta, "busca=garagem")
        self.assertContains(resposta, "aba=concluidas")

    # -- Interface visível ----------------------------------------------------

    def test_o_recorte_visivel_e_so_a_busca(self):
        self._demanda()
        html = self._abrir().content.decode()
        inicio = html.rindex("<form", 0, html.index("eithra-filter-shell"))
        formulario = html[inicio : html.index("</form>", inicio)]

        self.assertIn('name="busca"', formulario)
        for campo in (
            "status",
            "prioridade",
            "local",
            "responsavel",
            "prazo",
            "equipamento",
            "tipo_problema",
            "impacto",
            "risco",
            "minhas",
        ):
            self.assertNotIn(f'name="{campo}"', formulario)

    def test_a_grade_vai_da_decisao_para_a_acao(self):
        """A ordem das colunas acompanha a leitura: o que é (situação) antes do
        que se faz (ações). "Registrada em" deixou de ser coluna — virou tempo
        relativo sob o título."""
        self._demanda()
        html = self._abrir().content.decode()
        self.assertLess(html.index(">Prioridade"), html.index(">Situação"))
        self.assertLess(html.index(">Situação"), html.index(">Ações"))
        self.assertNotIn("Registrada em", html)
        self.assertNotIn("eithra-col-registrada", html)


class AcoesTests(BaseDeTelas):
    """Editar, mudar situação e observar pela tela (§20, §29)."""

    def test_editar_atualiza_o_conteudo(self):
        demanda = self._demanda(titulo="Original")
        resposta = self.client.post(
            reverse("demandas:editar", args=[demanda.pk]),
            {
                "condominio": str(self.condominio.pk),
                "titulo": "Atualizada",
            },
        )
        self.assertEqual(resposta.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.titulo, "Atualizada")
        # O peso não vem do navegador: segue o que o relato sustenta — aqui,
        # nada além do padrão do domínio.
        self.assertEqual(demanda.prioridade, PrioridadeDemanda.NORMAL)

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
            },
        )
        self.assertEqual(resposta.status_code, 403)

    def test_mudar_status(self):
        demanda = self._demanda()
        resposta = self.client.post(
            reverse("demandas:mudar_status", args=[demanda.pk]),
            {"acao": StatusDemanda.EM_ANDAMENTO},
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
            {"acao": StatusDemanda.CANCELADA},
        )
        self.assertEqual(cancelamento.status_code, 400)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.ABERTA)

    def test_atualizar_demanda_reutiliza_status_e_observacao(self):
        demanda = self._demanda()
        resposta = self.client.post(
            reverse("demandas:atualizar", args=[demanda.pk]),
            {
                "acao": StatusDemanda.EM_ANDAMENTO,
                "texto": "Fornecedor acionado",
                "etapa_atualizacao": "conclusao",
            },
        )
        self.assertEqual(resposta.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.EM_ANDAMENTO)
        self.assertEqual(demanda.historico.count(), 3)


class RegistroEmTresEtapasTests(BaseDeTelas):
    """O relato vira wizard visual sem perder a compatibilidade de backend."""

    RELATO = "apareceu uma nova falha no motor do portão interno"

    def _descricao(self, **extra):
        dados = {
            "condominio": str(self.condominio.pk),
            "relato": self.RELATO,
            "wizard": "descricao",
        }
        dados.update(extra)
        return self.client.post(reverse("demandas:criar"), dados)

    def _informacoes(self, **extra):
        dados = {
            "condominio": str(self.condominio.pk),
            "relato": self.RELATO,
            "titulo": "Nova falha no motor do portão interno",
            "wizard": "informacoes",
        }
        dados.update(extra)
        return self.client.post(reverse("demandas:criar"), dados)

    def test_descricao_sozinha_nao_registra_e_abre_etapa_de_informacoes(self):
        resposta = self._descricao()

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(resposta.context["modal_aberto"], "criar")
        self.assertEqual(resposta.context["registro_etapa"], "informacoes")
        sugestoes = resposta.context["sugestoes"]
        self.assertTrue(sugestoes["assunto"]["valor"])
        # O peso sai do motor estruturado, que lê os sinais do relato — aqui
        # "nova falha" (reincidência) e um equipamento essencial fora de
        # operação somam dois sinais de atenção e sobem o peso.
        self.assertEqual(sugestoes["prioridade"]["valor"], "Crítica")
        self.assertEqual(sugestoes["prazo"]["valor"], "")

    def test_o_que_a_leitura_nao_sustentou_permanece_sem_resposta(self):
        resposta = self._descricao(relato="Comprar lâmpadas para o corredor")
        sugestoes = resposta.context["sugestoes"]
        # Sem sinal que eleve o peso, o motor fica no degrau padrão — Média —, e
        # não há data a inventar: a etapa também não exige que a pessoa escolha a
        # outra.
        self.assertEqual(sugestoes["prioridade"]["valor"], "Média")
        self.assertEqual(sugestoes["prazo"]["texto"], "Sem prazo definido")
        self.assertEqual(sugestoes["local"]["valor"], "")

    def test_local_reconhecido_no_relato_aparece_nas_informacoes(self):
        criar_local(self.condominio, "Garagem")
        resposta = self._descricao(relato="Vazamento na garagem do bloco A")
        self.assertEqual(resposta.context["sugestoes"]["local"]["valor"], "Garagem")

    def test_etapa_de_revisao_mostra_resumo_final(self):
        resposta = self._informacoes()
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["registro_etapa"], "revisao")
        revisao = resposta.context["revisao"]
        self.assertEqual(revisao["assunto"], "Nova falha no motor do portão interno")
        # A revisão exibe o peso que será gravado — o que o motor sustentou a
        # partir dos sinais do relato.
        self.assertEqual(revisao["prioridade"]["valor"], "Crítica")
        self.assertEqual(revisao["prazo"]["valor"], "Sem prazo")

    def test_prazo_nao_e_criado_automaticamente_pela_prioridade(self):
        resposta = self.client.post(
            reverse("demandas:criar"),
            {
                "condominio": str(self.condominio.pk),
                "relato": self.RELATO,
                "titulo": "Nova falha no motor do portão interno",
                "prioridade": PrioridadeDemanda.URGENTE,
            },
        )

        self.assertEqual(resposta.status_code, 302)
        demanda = Demanda.objects.get()
        self.assertEqual(demanda.descricao, self.RELATO)
        # O peso é o que a pessoa escolheu — e nem ele cria prazo sozinho.
        self.assertEqual(demanda.prioridade, PrioridadeDemanda.URGENTE)
        self.assertIsNone(demanda.prazo)


class TelaDeAcompanhamentoTests(BaseDeTelas):
    """A interface segue o design operacional aprovado."""

    def test_listagem_usa_tabela_operacional_com_filtros_rapidos(self):
        self._demanda(titulo="Tabela")
        resposta = self.client.get(reverse("demandas:listagem"))
        conteudo = resposta.content.decode()

        self.assertIn("Atrasadas", conteudo)
        self.assertIn("Concluídas no mês", conteudo)
        self.assertIn("<table", conteudo)
        # A grade enxuta: a demanda encabeça a própria coluna — o código virou a
        # linha miúda sob o título e a descrição, um contexto curto. Prazo e
        # situação se juntaram em "Situação".
        self.assertIn(">Demanda<", conteudo)
        self.assertIn(">Situação<", conteudo)
        # O código e a descrição deixaram de ser colunas próprias — o que se
        # confere é a ausência das colunas, não de palavras que o wizard da
        # própria página também usa ("Descrição" é uma etapa do registro).
        self.assertNotIn("eithra-col-codigo", conteudo)
        self.assertNotIn("eithra-col-descricao", conteudo)
        self.assertNotIn('<ul class="eithra-lista">', conteudo)

    def test_registro_exibe_wizard_de_tres_etapas(self):
        resposta = self.client.get(reverse("demandas:listagem"))
        conteudo = resposta.content.decode()
        for etapa in ("Descrição", "Informações", "Revisão"):
            self.assertIn(etapa, conteudo)
        self.assertIn("Descreva o que aconteceu", conteudo)

    def test_detalhe_le_secoes_continuas(self):
        demanda = self._demanda()
        resposta = self.client.get(reverse("demandas:detalhe", args=[demanda.pk]))
        conteudo = resposta.content.decode()

        self.assertNotIn("Mudar situação", conteudo)
        # O detalhe é uma leitura só, de cima para baixo — sem abas.
        for secao in ("Demanda", "Informações", "Acompanhamento", "Histórico"):
            self.assertIn(secao, conteudo)
        self.assertIn("eithra-detalhe-titulo", conteudo)

    def test_detalhe_mostra_acao_contextual_e_modal_de_atualizacao(self):
        demanda = self._demanda()
        criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Manutenção vinculada",
            demanda_origem=demanda,
        )

        resposta = self.client.get(
            reverse("demandas:detalhe", args=[demanda.pk]) + "?abrir=atualizar"
        )
        conteudo = resposta.content.decode()

        self.assertIn("Acompanhar manutenção", conteudo)
        self.assertIn("Registrar atualização", conteudo)
        self.assertIn("Conclusão", conteudo)


class ContextoOperacionalTests(BaseDeTelas):
    """O contexto que qualifica o problema: onde fica, sobre o que é e o que significa.

    A área não é coluna própria: ela sai do tipo do lugar cadastrado. O detalhe
    mostra o que existe, o histórico guarda o antes e o depois, e os filtros
    deixam estreitar a lista por tipo de problema, impacto e risco (§6, §23).
    """

    def _contexto(self, **campos):
        """Uma demanda presa a um lugar e ao equipamento que fica nele."""
        local = criar_local(
            self.condominio, "Casa de máquinas", tipo=TipoLocal.CASA_DE_MAQUINAS
        )
        equipamento = criar_equipamento(
            self.condominio, "Bomba de recalque", local=local
        )
        demanda = self._demanda(
            titulo="Bomba de recalque com vazamento",
            local=local,
            equipamento=equipamento,
            **campos,
        )
        return local, equipamento, demanda

    def test_detalhe_mostra_area_derivada_local_e_equipamento(self):
        _, _, demanda = self._contexto(
            tipo_problema=TipoProblema.VAZAMENTO,
            impacto=ImpactoDemanda.EQUIPAMENTO,
            risco=RiscoDemanda.ELETRICO,
        )

        resposta = self.client.get(reverse("demandas:detalhe", args=[demanda.pk]))
        self.assertEqual(resposta.status_code, 200)
        dados = resposta.context["demanda"]
        # A área é leitura do tipo do lugar, e não um campo guardado à parte.
        self.assertEqual(dados["area"], TipoLocal.CASA_DE_MAQUINAS.label)
        self.assertEqual(dados["equipamento"], "Bomba de recalque")
        self.assertEqual(dados["tipo_problema"], TipoProblema.VAZAMENTO.label)
        self.assertEqual(dados["risco"], RiscoDemanda.ELETRICO.label)

    def test_filtros_de_classificacao_estreitam_a_lista(self):
        self._contexto(
            tipo_problema=TipoProblema.VAZAMENTO, risco=RiscoDemanda.ELETRICO
        )
        self._demanda(titulo="Sem contexto")

        resposta = self.client.get(
            reverse("demandas:listagem"),
            {"tipo_problema": TipoProblema.VAZAMENTO, "risco": RiscoDemanda.ELETRICO},
        )
        titulos = [linha["titulo"] for linha in resposta.context["demandas"]]
        self.assertEqual(titulos, ["Bomba de recalque com vazamento"])
        # O recorte continua valendo pelo endereço, mas não ocupa mais a tela: o
        # filtro fino saiu da interface (§3) e segue vivo no backend (§19). O
        # campo de filtro não é renderizado como controle na listagem.
        self.assertNotIn('id="id_tipo_problema"', resposta.content.decode())

        vazia = self.client.get(
            reverse("demandas:listagem"), {"tipo_problema": TipoProblema.RUIDO}
        )
        self.assertEqual(vazia.context["demandas"], [])

    def test_filtro_por_equipamento_nao_alcanca_outro_condominio(self):
        _, _, demanda = self._contexto()
        outro = criar_condominio("Beta")
        alheio = criar_equipamento(outro, "Bomba do Beta")
        do_vizinho = Demanda.objects.create(
            condominio=outro,
            criado_por=self.gestao,
            titulo="Bomba do Beta com ruído",
            equipamento=alheio,
        )

        # O equipamento de outro condomínio não é oferecido nem alcança nada:
        # o filtro vale dentro do que se enxerga, e o vizinho fica fora.
        resposta = self.client.get(
            reverse("demandas:listagem"), {"equipamento": str(alheio.pk)}
        )
        titulos = [linha["titulo"] for linha in resposta.context["demandas"]]
        self.assertNotIn(do_vizinho.titulo, titulos)
        self.assertIn(demanda.titulo, titulos)

    def test_historico_guarda_o_antes_e_o_depois_da_classificacao(self):
        _, _, demanda = self._contexto(tipo_problema=TipoProblema.VAZAMENTO)

        resposta = self.client.post(
            reverse("demandas:editar", args=[demanda.pk]),
            {
                "titulo": demanda.titulo,
                "tipo_problema": TipoProblema.HIDRAULICA,
                "impacto": ImpactoDemanda.AREA,
                "risco": RiscoDemanda.ESTRUTURAL,
            },
        )
        self.assertEqual(resposta.status_code, 302)

        resposta = self.client.get(
            reverse("demandas:detalhe", args=[demanda.pk]) + "?aba=historico"
        )
        conteudo = resposta.content.decode()
        # O valor anterior e o novo, lado a lado, na língua da tela.
        self.assertIn("Tipo de problema", conteudo)
        self.assertIn(TipoProblema.VAZAMENTO.label, conteudo)
        self.assertIn(TipoProblema.HIDRAULICA.label, conteudo)

    def test_detalhe_reune_contratos_e_garantias_do_equipamento(self):
        _, equipamento, demanda = self._contexto()

        contrato = criar_contrato(self.condominio, self.gestao)
        ContratoEquipamento.objects.create(
            contrato=contrato, equipamento=equipamento
        )
        garantia = criar_garantia(self.condominio, self.gestao)
        GarantiaEquipamento.objects.create(
            garantia=garantia, equipamento=equipamento
        )

        resposta = self.client.get(reverse("demandas:detalhe", args=[demanda.pk]))
        conteudo = resposta.content.decode()
        # O que a Governança já sabia sobre o equipamento aparece aqui sem que a
        # demanda guarde vínculo próprio: a consulta é feita na hora (§6-F).
        self.assertIn("Contratos e garantias", conteudo)
        self.assertIn("Manutenção de elevadores", conteudo)
        self.assertIn("Garantia da bomba de recalque", conteudo)

    def test_sla_do_contrato_projeta_o_prazo_contratual(self):
        _, equipamento, demanda = self._contexto()
        contrato = criar_contrato(self.condominio, self.gestao, sla_dias=4)
        ContratoEquipamento.objects.create(
            contrato=contrato, equipamento=equipamento
        )

        resposta = self.client.get(reverse("demandas:detalhe", args=[demanda.pk]))
        conteudo = resposta.content.decode()
        esperado = timezone.localdate(demanda.criado_em) + timedelta(days=4)

        # O SLA estruturado do contrato é a base formal do prazo — exibido com o
        # contrato e o prazo contratual, sem nada deduzido do texto do documento.
        self.assertIn("SLA: 4 dias", conteudo)
        self.assertIn(f"Prazo contratual: {esperado.strftime('%d/%m/%Y')}", conteudo)
        self.assertEqual(
            resposta.context["contexto_governanca"]["prazo_do_contrato"], esperado
        )

    def test_contrato_sem_sla_nao_inventa_prazo(self):
        _, equipamento, demanda = self._contexto()
        contrato = criar_contrato(self.condominio, self.gestao)
        ContratoEquipamento.objects.create(
            contrato=contrato, equipamento=equipamento
        )

        resposta = self.client.get(reverse("demandas:detalhe", args=[demanda.pk]))
        conteudo = resposta.content.decode()

        # Contrato vigente, mas sem SLA: não há prazo a inventar a partir dele.
        self.assertIn("Sem SLA registrado — não gera prazo", conteudo)
        self.assertNotIn("Prazo contratual", conteudo)
        self.assertIsNone(
            resposta.context["contexto_governanca"]["prazo_do_contrato"]
        )
