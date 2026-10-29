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

    def test_criar_redireciona_para_o_detalhe(self):
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
        self.assertEqual(resposta.url, reverse("demandas:detalhe", args=[demanda.pk]))

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

    def test_indicadores_representam_o_universo_visivel_e_nao_os_filtros(self):
        hoje = timezone.localdate()
        self._demanda(titulo="Aberta")
        self._demanda(
            titulo="Vencida",
            prazo=hoje - timedelta(days=1),
            status=StatusDemanda.EM_ANDAMENTO,
        )
        self._demanda(titulo="Andamento", status=StatusDemanda.EM_ANDAMENTO)
        self._demanda(
            titulo="Concluída",
            status=StatusDemanda.CONCLUIDA,
        )

        resposta = self.client.get(
            reverse("demandas:listagem"), {"prioridade": PrioridadeDemanda.URGENTE}
        )
        indicadores = {item["titulo"]: item["valor"] for item in resposta.context["indicadores"]}
        self.assertEqual(indicadores["Em aberto"], 3)
        self.assertEqual(indicadores["Vencidas"], 1)
        self.assertEqual(indicadores["Em andamento"], 2)
        self.assertEqual(indicadores["Concluídas no mês"], 1)


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
        self.assertEqual(sugestoes["prioridade"]["valor"], "Alta")
        self.assertEqual(sugestoes["prioridade"]["motivo"], "Falha recorrente em portão")
        self.assertEqual(sugestoes["prazo"]["valor"], "")

    def test_o_que_a_leitura_nao_sustentou_permanece_sem_resposta(self):
        resposta = self._descricao(relato="Comprar lâmpadas para o corredor")
        sugestoes = resposta.context["sugestoes"]
        # Sem sinal objetivo o peso fica no padrão do domínio, e não há data a
        # inventar: a etapa também não exige que a pessoa escolha a outra.
        self.assertEqual(sugestoes["prioridade"]["valor"], "Normal")
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
        self.assertEqual(revisao["prioridade"]["valor"], "Alta")
        self.assertEqual(revisao["prazo"]["valor"], "Sem prazo")

    def test_prazo_nao_e_criado_automaticamente_pela_prioridade(self):
        resposta = self.client.post(
            reverse("demandas:criar"),
            {
                "condominio": str(self.condominio.pk),
                "relato": self.RELATO,
                "titulo": "Nova falha no motor do portão interno",
            },
        )

        self.assertEqual(resposta.status_code, 302)
        demanda = Demanda.objects.get()
        self.assertEqual(demanda.descricao, self.RELATO)
        self.assertEqual(demanda.prioridade, PrioridadeDemanda.ALTA)
        self.assertIsNone(demanda.prazo)


class TelaDeAcompanhamentoTests(BaseDeTelas):
    """A interface segue o design operacional aprovado."""

    def test_listagem_usa_tabela_operacional_com_indicadores(self):
        self._demanda(titulo="Tabela")
        resposta = self.client.get(reverse("demandas:listagem"))
        conteudo = resposta.content.decode()

        self.assertIn("Em aberto", conteudo)
        self.assertIn("Concluídas no mês", conteudo)
        self.assertIn("<table", conteudo)
        # Código, título e descrição ocupam colunas próprias na grid.
        self.assertIn(">Código<", conteudo)
        self.assertIn(">Título<", conteudo)
        self.assertIn(">Descrição<", conteudo)
        self.assertNotIn('<ul class="eithra-lista">', conteudo)

    def test_registro_exibe_wizard_de_tres_etapas(self):
        resposta = self.client.get(reverse("demandas:listagem"))
        conteudo = resposta.content.decode()
        for etapa in ("Descrição", "Informações", "Revisão"):
            self.assertIn(etapa, conteudo)
        self.assertIn("Descreva o que aconteceu", conteudo)

    def test_detalhe_exibe_abas_aprovadas(self):
        demanda = self._demanda()
        resposta = self.client.get(reverse("demandas:detalhe", args=[demanda.pk]))
        conteudo = resposta.content.decode()

        self.assertNotIn("Mudar situação", conteudo)
        for aba in ("Visão geral", "Histórico", "Anexos", "Relacionadas"):
            self.assertIn(aba, conteudo)
        self.assertIn("Detalhe da demanda", conteudo)

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
        self.assertIn("Atualizar demanda", conteudo)
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
        # O filtro aparece com o nome que a gestão lê, não com o do campo.
        self.assertIn("Tipo de problema", resposta.content.decode())

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
