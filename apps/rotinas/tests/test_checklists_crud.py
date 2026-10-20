"""CRUD de Modelos de checklist pela aplicação (§23) e proteção do histórico.

O checklist é contexto da Rotina: monta-se a lista pela tela de Rotinas, e não
por um menu à parte. Aqui se verifica que administração, síndica e supervisão
conseguem manter o modelo e as tarefas sem tocar no banco à mão, que a operação
e os demais perfis não alcançam o cadastro, e que editar o modelo nunca reescreve
o retrato de uma ocorrência já criada.
"""
from datetime import date

from django.test import TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.rotinas.constants import (
    Periodicidade,
    ResultadoExecucao,
    StatusExecucao,
    StatusItemChecklist,
)
from apps.rotinas.models import ExecucaoRotina, ItemModeloChecklist, ModeloChecklist
from apps.rotinas.tests.apoio import (
    criar_checklist,
    criar_condominio,
    criar_item_do_checklist,
    criar_pessoa,
    criar_vinculo,
)


class BaseDoCrudDeChecklist(TestCase):
    """Um condomínio com gestão vinculada e um checklist de três tarefas."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.outro = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)

        self.checklist = criar_checklist(self.condominio, self.gestao)
        criar_item_do_checklist(self.checklist, "Limpar piso", ordem=1)
        criar_item_do_checklist(self.checklist, "Limpar mesas", ordem=2)
        criar_item_do_checklist(self.checklist, "Esvaziar lixeiras", ordem=3)

    def _entrar(self, usuario):
        self.client.force_login(usuario)

    def _rotina(self, **campos):
        base = {
            "condominio": self.condominio.pk,
            "titulo": "Limpeza diária da área comum",
            "data_inicio": "2026-01-10",
            "periodicidade_tipo": Periodicidade.MENSAL,
            "intervalo": 1,
            "dia_mes": 10,
            "tipo": "operacional",
            "prioridade": "normal",
            "checklist_modelo": self.checklist.pk,
        }
        base.update(campos)
        return base


class CrudDoModeloTests(BaseDoCrudDeChecklist):
    """Criar, renomear e ativar/inativar o modelo, tudo pela tela."""

    def test_lista_abre_para_a_gestao(self):
        self._entrar(self.gestao)
        resposta = self.client.get(reverse("rotinas:checklists"))
        self.assertEqual(resposta.status_code, 200)
        pks = [linha["pk"] for linha in resposta.context["checklists"]]
        self.assertIn(self.checklist.pk, pks)

    def test_cria_checklist_pela_tela(self):
        self._entrar(self.gestao)
        resposta = self.client.post(
            reverse("rotinas:checklist_criar"),
            {"condominio": self.condominio.pk, "nome": "Piscina", "descricao": ""},
        )
        self.assertEqual(resposta.status_code, 302)
        criado = ModeloChecklist.objects.get(nome="Piscina")
        self.assertEqual(criado.condominio, self.condominio)
        self.assertEqual(criado.criado_por, self.gestao)

    def test_nome_vazio_e_recusado(self):
        self._entrar(self.gestao)
        resposta = self.client.post(
            reverse("rotinas:checklist_criar"),
            {"condominio": self.condominio.pk, "nome": "   ", "descricao": ""},
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(ModeloChecklist.objects.filter(nome="").exists())

    def test_renomeia_o_modelo(self):
        self._entrar(self.gestao)
        resposta = self.client.post(
            reverse("rotinas:checklist_editar", args=[self.checklist.pk]),
            {"condominio": self.condominio.pk, "nome": "Limpeza pesada", "descricao": "Semanal"},
        )
        self.assertEqual(resposta.status_code, 302)
        self.checklist.refresh_from_db()
        self.assertEqual(self.checklist.nome, "Limpeza pesada")
        self.assertEqual(self.checklist.descricao, "Semanal")

    def test_inativa_e_devolve_o_modelo_sem_apagar(self):
        self._entrar(self.gestao)
        url = reverse("rotinas:checklist_alternar", args=[self.checklist.pk])

        self.client.post(url)
        self.checklist.refresh_from_db()
        self.assertFalse(self.checklist.ativo)
        self.assertTrue(ModeloChecklist.objects.filter(pk=self.checklist.pk).exists())

        self.client.post(url)
        self.checklist.refresh_from_db()
        self.assertTrue(self.checklist.ativo)


class CrudDasTarefasTests(BaseDoCrudDeChecklist):
    """Acrescentar, ajustar e inativar tarefas, tudo pela tela."""

    def test_acrescenta_tarefa_no_fim(self):
        self._entrar(self.gestao)
        resposta = self.client.post(
            reverse("rotinas:checklist_item_criar", args=[self.checklist.pk]),
            {"descricao": "Repor insumos", "obrigatorio": "on"},
        )
        self.assertEqual(resposta.status_code, 302)
        nova = self.checklist.itens.get(descricao="Repor insumos")
        self.assertEqual(nova.ordem, 4)
        self.assertTrue(nova.obrigatorio)

    def test_edita_descricao_ordem_e_obrigatoriedade(self):
        self._entrar(self.gestao)
        item = self.checklist.itens.get(descricao="Limpar mesas")
        resposta = self.client.post(
            reverse("rotinas:checklist_item_editar", args=[self.checklist.pk, item.pk]),
            {"descricao": "Limpar mesas e cadeiras", "ordem": "1", "obrigatorio": ""},
        )
        self.assertEqual(resposta.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.descricao, "Limpar mesas e cadeiras")
        self.assertEqual(item.ordem, 1)
        self.assertFalse(item.obrigatorio)

    def test_inativa_tarefa_sem_apagar(self):
        self._entrar(self.gestao)
        item = self.checklist.itens.get(descricao="Esvaziar lixeiras")
        url = reverse(
            "rotinas:checklist_item_alternar", args=[self.checklist.pk, item.pk]
        )

        self.client.post(url)
        item.refresh_from_db()
        self.assertFalse(item.ativo)
        self.assertTrue(ItemModeloChecklist.objects.filter(pk=item.pk).exists())

    def test_detalhe_mostra_o_modal_de_edicao_da_tarefa(self):
        self._entrar(self.gestao)
        item = self.checklist.itens.get(descricao="Limpar piso")
        resposta = self.client.get(
            reverse("rotinas:checklist", args=[self.checklist.pk]),
            {"abrir": f"item-{item.pk}"},
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["item_em_edicao"], item)


class RotinasQueUsamTests(BaseDoCrudDeChecklist):
    """O detalhe do modelo mostra as rotinas que o citam."""

    def test_mostra_rotina_vinculada(self):
        self._entrar(self.gestao)
        self.client.post(reverse("rotinas:criar"), self._rotina())

        resposta = self.client.get(
            reverse("rotinas:checklist", args=[self.checklist.pk])
        )
        self.assertEqual(resposta.status_code, 200)
        titulos = [rotina["titulo"] for rotina in resposta.context["rotinas_que_usam"]]
        self.assertIn("Limpeza diária da área comum", titulos)


class AutorizacaoDoCrudTests(BaseDoCrudDeChecklist):
    """A permissão do cadastro vem do vínculo e do papel, decidida no backend."""

    def setUp(self):
        super().setUp()
        self.supervisora = criar_pessoa("sup@exemplo.test", "Supervisora")
        criar_vinculo(self.supervisora, self.condominio, papel=Papel.SUPERVISORA)

        self.operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(self.operacional, self.condominio, papel=Papel.OPERACIONAL)

        self.porteiro = criar_pessoa("porteiro@exemplo.test", "Porteiro")
        criar_vinculo(self.porteiro, self.condominio, papel=Papel.PORTEIRO)

        self.morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(self.morador, self.condominio, papel=Papel.MORADOR)

    def test_supervisora_monta_o_modelo(self):
        self._entrar(self.supervisora)
        self.assertEqual(
            self.client.get(reverse("rotinas:checklists")).status_code, 200
        )
        resposta = self.client.post(
            reverse("rotinas:checklist_item_criar", args=[self.checklist.pk]),
            {"descricao": "Conferir anormalidades", "obrigatorio": ""},
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(
            self.checklist.itens.filter(descricao="Conferir anormalidades").exists()
        )

    def test_supervisora_nao_inativa_o_modelo(self):
        self._entrar(self.supervisora)
        resposta = self.client.post(
            reverse("rotinas:checklist_alternar", args=[self.checklist.pk])
        )
        self.assertEqual(resposta.status_code, 403)
        self.checklist.refresh_from_db()
        self.assertTrue(self.checklist.ativo)

    def test_supervisora_inativa_uma_tarefa(self):
        self._entrar(self.supervisora)
        item = self.checklist.itens.first()
        resposta = self.client.post(
            reverse("rotinas:checklist_item_alternar", args=[self.checklist.pk, item.pk])
        )
        self.assertEqual(resposta.status_code, 302)
        item.refresh_from_db()
        self.assertFalse(item.ativo)

    def test_operacional_nao_alcanca_o_cadastro(self):
        self._entrar(self.operacional)
        self.assertEqual(
            self.client.get(reverse("rotinas:checklists")).status_code, 404
        )
        self.assertEqual(
            self.client.get(
                reverse("rotinas:checklist", args=[self.checklist.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse("rotinas:checklist_alternar", args=[self.checklist.pk])
            ).status_code,
            404,
        )

    def test_porteiro_e_morador_recebem_404(self):
        for pessoa in (self.porteiro, self.morador):
            self._entrar(pessoa)
            self.assertEqual(
                self.client.get(reverse("rotinas:checklists")).status_code, 404
            )


class ProtecaoDoHistoricoTests(BaseDoCrudDeChecklist):
    """Editar o modelo não reescreve os retratos das ocorrências já criadas (§4)."""

    def test_edicao_do_modelo_nao_toca_no_retrato(self):
        self._entrar(self.gestao)

        # 1. criar checklist com 3 itens — o setUp já o fez; 2. gerar execução.
        self.client.post(reverse("rotinas:criar"), self._rotina())
        execucao = ExecucaoRotina.objects.get(status=StatusExecucao.PENDENTE)
        self.assertEqual(execucao.itens.count(), 3)

        item_modelo = self.checklist.itens.get(descricao="Limpar mesas")

        # 3. alterar descrição de um item do modelo
        self.client.post(
            reverse("rotinas:checklist_item_editar", args=[self.checklist.pk, item_modelo.pk]),
            {"descricao": "Limpar mesas e cadeiras", "ordem": "2", "obrigatorio": "on"},
        )
        # 4. adicionar outro item
        self.client.post(
            reverse("rotinas:checklist_item_criar", args=[self.checklist.pk]),
            {"descricao": "Repor insumos", "obrigatorio": "on"},
        )
        # 5. inativar um item
        item_lixeiras = self.checklist.itens.get(descricao="Esvaziar lixeiras")
        self.client.post(
            reverse("rotinas:checklist_item_alternar", args=[self.checklist.pk, item_lixeiras.pk])
        )

        # 6. a execução antiga continua exatamente com o snapshot original
        execucao.refresh_from_db()
        self.assertEqual(execucao.itens.count(), 3)
        self.assertEqual(
            list(execucao.itens.values_list("descricao", flat=True)),
            ["Limpar piso", "Limpar mesas", "Esvaziar lixeiras"],
        )
        self.assertTrue(execucao.itens.get(descricao="Limpar mesas").obrigatorio)


class FluxoCompletoTests(BaseDoCrudDeChecklist):
    """O fluxo real de ponta a ponta, pela aplicação (§8)."""

    def test_do_modelo_ate_a_conclusao(self):
        self._entrar(self.gestao)

        # A. criar ModeloChecklist
        self.client.post(
            reverse("rotinas:checklist_criar"),
            {"condominio": self.condominio.pk, "nome": "Piscina", "descricao": ""},
        )
        modelo = ModeloChecklist.objects.get(nome="Piscina")

        # B. cadastrar itens
        for ordem, tarefa in enumerate(["Limpar piso", "Limpar mesas"], start=1):
            self.client.post(
                reverse("rotinas:checklist_item_criar", args=[modelo.pk]),
                {"descricao": tarefa, "ordem": str(ordem), "obrigatorio": "on"},
            )

        # C. criar Rotina usando esse checklist
        self.client.post(
            reverse("rotinas:criar"),
            self._rotina(checklist_modelo=modelo.pk),
        )

        # D. gerar ExecucaoRotina (nasce junto com a rotina) e E. verificar snapshot
        execucao = ExecucaoRotina.objects.get(status=StatusExecucao.PENDENTE)
        self.assertEqual(execucao.itens.count(), 2)

        # F. executar parcialmente
        primeiro = execucao.itens.get(descricao="Limpar piso")
        self.client.post(
            reverse("rotinas:tratar_item", args=[execucao.pk, primeiro.pk]),
            {"status": StatusItemChecklist.CONCLUIDO},
        )

        # G. tentar concluir com obrigatório pendente → bloqueado
        resposta = self.client.post(
            reverse("rotinas:concluir", args=[execucao.pk]),
            {"resultado": ResultadoExecucao.OK},
        )
        self.assertEqual(resposta.status_code, 302)
        execucao.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.PENDENTE)

        # H. finalizar o item que faltava
        segundo = execucao.itens.get(descricao="Limpar mesas")
        self.client.post(
            reverse("rotinas:tratar_item", args=[execucao.pk, segundo.pk]),
            {"status": StatusItemChecklist.CONCLUIDO},
        )

        # I. concluir a execução
        self.client.post(
            reverse("rotinas:concluir", args=[execucao.pk]),
            {"resultado": ResultadoExecucao.OK},
        )
        execucao.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.CONCLUIDA)

        # J. verificar progresso/histórico
        resposta = self.client.get(reverse("rotinas:execucao", args=[execucao.pk]))
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["progresso"]["percentual"], 100)
        item = execucao.itens.get(descricao="Limpar piso")
        self.assertEqual(item.concluido_por, self.gestao)
        self.assertIsNotNone(item.concluido_em)


class EscolhaDoChecklistNaRotinaTests(BaseDoCrudDeChecklist):
    """A rotina só aceita checklist do próprio condomínio."""

    def test_rotina_recusa_checklist_de_outro_condominio(self):
        self._entrar(self.gestao)
        alheio = criar_checklist(self.outro, self.gestao, nome="Do Beta")
        resposta = self.client.post(
            reverse("rotinas:criar"), self._rotina(checklist_modelo=alheio.pk)
        )
        self.assertEqual(resposta.status_code, 400)

    def test_rotina_sem_checklist_continua_valida(self):
        self._entrar(self.gestao)
        resposta = self.client.post(
            reverse("rotinas:criar"), self._rotina(checklist_modelo="")
        )
        self.assertEqual(resposta.status_code, 302)
