"""Checklists operacionais: modelo, retrato na ocorrência e desfecho (§15).

A rotina pode carregar uma lista de tarefas. O modelo diz o que se costuma
fazer; a ocorrência guarda o retrato daquele dia. Aqui se verifica que o retrato
não se reescreve quando o modelo muda, que uma tarefa obrigatória pendente
segura a conclusão e que uma opcional não segura — e que a autorização continua
sendo decidida no backend, pelo vínculo da pessoa com o condomínio.
"""
from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.rotinas.constants import (
    Periodicidade,
    ResultadoExecucao,
    StatusExecucao,
    StatusItemChecklist,
)
from apps.rotinas.models import (
    ExecucaoRotina,
    ItemModeloChecklist,
    ModeloChecklist,
    RotinaRecorrente,
)
from apps.rotinas.services import (
    ErroDeRotina,
    concluir_execucao,
    criar_rotina,
    tratar_item_checklist,
)
from apps.rotinas.tests.apoio import (
    criar_checklist,
    criar_condominio,
    criar_item_do_checklist,
    criar_pessoa,
    criar_vinculo,
)


class BaseDeChecklist(TestCase):
    """Um condomínio, um checklist de três tarefas e uma rotina que o cita."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.outro = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")

        self.checklist = criar_checklist(self.condominio, self.gestao)
        criar_item_do_checklist(self.checklist, "Limpar piso", ordem=1)
        criar_item_do_checklist(self.checklist, "Limpar mesas", ordem=2)
        criar_item_do_checklist(self.checklist, "Esvaziar lixeiras", ordem=3)

    def _rotina(self, **campos):
        base = {
            "condominio": self.condominio,
            "criado_por": self.gestao,
            "titulo": "Limpeza diária da área comum",
            "data_inicio": date(2026, 1, 10),
            "periodicidade_tipo": Periodicidade.MENSAL,
            "dia_mes": 10,
            "checklist_modelo": self.checklist,
        }
        base.update(campos)
        return criar_rotina(**base)

    def _pendente(self, rotina):
        return rotina.execucoes.get(status=StatusExecucao.PENDENTE)

    def _execucao(self, **campos):
        return self._pendente(self._rotina(**campos))

    def _item(self, execucao, descricao):
        return execucao.itens.get(descricao=descricao)


class ModeloChecklistTests(BaseDeChecklist):
    """O checklist modelo guarda a lista; a rotina apenas o cita."""

    def test_cria_checklist_com_nome(self):
        criado = criar_checklist(self.condominio, self.gestao, nome="Limpeza")
        self.assertEqual(criado.nome, "Limpeza")
        self.assertTrue(criado.ativo)
        self.assertEqual(criado.criado_por, self.gestao)

    def test_nome_espacos_sao_colapsados(self):
        criado = criar_checklist(
            self.condominio, self.gestao, nome="  Limpeza   pesada  "
        )
        self.assertEqual(criado.nome, "Limpeza pesada")

    def test_checklist_sem_nome_e_recusado(self):
        with self.assertRaises(ValidationError):
            ModeloChecklist(
                condominio=self.condominio, criado_por=self.gestao, nome="   "
            ).full_clean()

    def test_itens_vem_na_ordem(self):
        descricoes = list(self.checklist.itens.values_list("descricao", flat=True))
        self.assertEqual(
            descricoes, ["Limpar piso", "Limpar mesas", "Esvaziar lixeiras"]
        )

    def test_tarefa_sem_descricao_e_recusada(self):
        with self.assertRaises(ValidationError):
            ItemModeloChecklist(checklist=self.checklist, descricao="  ").full_clean()

    def test_queryset_do_condominio_nao_vaza(self):
        criar_checklist(self.outro, self.gestao, nome="Do Beta")
        nomes = list(
            ModeloChecklist.objects.do_condominio(self.condominio).values_list(
                "nome", flat=True
            )
        )
        self.assertEqual(nomes, ["Checklist de limpeza"])

    def test_queryset_ativos_deixa_fora_o_desativado(self):
        antigo = criar_checklist(self.condominio, self.gestao, nome="Antigo")
        antigo.ativo = False
        antigo.save(update_fields=["ativo"])

        ativos = list(
            ModeloChecklist.objects.do_condominio(self.condominio)
            .ativos()
            .values_list("nome", flat=True)
        )
        self.assertEqual(ativos, ["Checklist de limpeza"])


class VinculoDaRotinaTests(BaseDeChecklist):
    """A rotina cita um checklist do próprio condomínio — ou nenhum."""

    def test_rotina_guarda_o_checklist(self):
        rotina = self._rotina()
        self.assertEqual(rotina.checklist_modelo, self.checklist)

    def test_rotina_sem_checklist_continua_valida(self):
        rotina = self._rotina(checklist_modelo=None)
        self.assertIsNone(rotina.checklist_modelo)
        execucao = self._pendente(rotina)
        self.assertEqual(execucao.itens.count(), 0)
        self.assertFalse(execucao.tem_checklist)
        self.assertFalse(execucao.tem_item_obrigatorio_pendente)

    def test_checklist_de_outro_condominio_e_recusado_no_modelo(self):
        alheio = criar_checklist(self.outro, self.gestao, nome="Do Beta")
        with self.assertRaises(ValidationError):
            RotinaRecorrente(
                condominio=self.condominio,
                criado_por=self.gestao,
                titulo="Rotina do Alpha",
                data_inicio=date(2026, 1, 10),
                checklist_modelo=alheio,
            ).full_clean()

    def test_checklist_de_outro_condominio_e_recusado_no_servico(self):
        alheio = criar_checklist(self.outro, self.gestao, nome="Do Beta")
        with self.assertRaises(ErroDeRotina):
            self._rotina(checklist_modelo=alheio)


class RetratoDaOcorrenciaTests(BaseDeChecklist):
    """A ocorrência copia o checklist uma vez; o modelo não mexe no passado."""

    def test_geracao_copia_as_tarefas_ativas(self):
        execucao = self._execucao()
        self.assertEqual(execucao.itens.count(), 3)
        self.assertTrue(execucao.tem_checklist)
        descricoes = list(execucao.itens.values_list("descricao", flat=True))
        self.assertEqual(
            descricoes, ["Limpar piso", "Limpar mesas", "Esvaziar lixeiras"]
        )

    def test_retrato_preserva_ordem_e_obrigatoriedade(self):
        criar_item_do_checklist(
            self.checklist, "Conferir anormalidades", ordem=4, obrigatorio=False
        )
        execucao = self._pendente(self._rotina())
        ultima = self._item(execucao, "Conferir anormalidades")
        self.assertEqual(ultima.ordem, 4)
        self.assertFalse(ultima.obrigatorio)
        self.assertEqual(ultima.status, StatusItemChecklist.PENDENTE)

    def test_tarefa_desativada_nao_e_copiada(self):
        criar_item_do_checklist(
            self.checklist, "Tarefa antiga", ordem=9, ativo=False
        )
        execucao = self._pendente(self._rotina())
        self.assertEqual(execucao.itens.count(), 3)
        self.assertFalse(execucao.itens.filter(descricao="Tarefa antiga").exists())

    def test_mudar_o_modelo_depois_nao_reescreve_a_ocorrencia(self):
        execucao = self._pendente(self._rotina())
        item_modelo = self.checklist.itens.get(descricao="Limpar piso")

        item_modelo.descricao = "Limpar o piso com produto novo"
        item_modelo.obrigatorio = False
        item_modelo.save()

        retrato = execucao.itens.get(item_modelo=item_modelo)
        self.assertEqual(retrato.descricao, "Limpar piso")
        self.assertTrue(retrato.obrigatorio)

    def test_tarefa_nova_no_modelo_nao_aparece_na_ocorrencia_antiga(self):
        execucao = self._pendente(self._rotina())
        criar_item_do_checklist(self.checklist, "Repor insumos", ordem=4)
        self.assertEqual(execucao.itens.count(), 3)

    def test_item_modelo_apagado_nao_apaga_o_retrato(self):
        execucao = self._pendente(self._rotina())
        item_modelo = self.checklist.itens.get(descricao="Limpar piso")
        retrato = execucao.itens.get(item_modelo=item_modelo)

        item_modelo.delete()
        retrato.refresh_from_db()

        self.assertIsNone(retrato.item_modelo)
        self.assertEqual(retrato.descricao, "Limpar piso")


class TratamentoDaTarefaTests(BaseDeChecklist):
    """Tratar uma tarefa diz como ela ficou e quem cuidou dela."""

    def test_marcar_concluida_guarda_quem_e_quando(self):
        execucao = self._execucao()
        item = self._item(execucao, "Limpar piso")

        tratar_item_checklist(
            item=item, usuario=self.gestao, status=StatusItemChecklist.CONCLUIDO
        )
        item.refresh_from_db()

        self.assertEqual(item.status, StatusItemChecklist.CONCLUIDO)
        self.assertEqual(item.concluido_por, self.gestao)
        self.assertIsNotNone(item.concluido_em)
        self.assertTrue(item.esta_tratado)

    def test_marcar_nao_aplicavel(self):
        execucao = self._execucao()
        item = self._item(execucao, "Limpar mesas")

        tratar_item_checklist(
            item=item, usuario=self.gestao, status=StatusItemChecklist.NAO_APLICAVEL
        )
        item.refresh_from_db()

        self.assertEqual(item.status, StatusItemChecklist.NAO_APLICAVEL)
        self.assertTrue(item.esta_tratado)

    def test_anotar_sem_desfecho_mantem_a_tarefa_pendente(self):
        execucao = self._execucao()
        item = self._item(execucao, "Esvaziar lixeiras")

        tratar_item_checklist(item=item, usuario=self.gestao, observacao="Lixeira sem tampa.")
        item.refresh_from_db()

        self.assertEqual(item.status, StatusItemChecklist.PENDENTE)
        self.assertEqual(item.observacao, "Lixeira sem tampa.")
        self.assertFalse(item.esta_tratado)

    def test_nao_da_para_dizer_estado_invalido(self):
        execucao = self._execucao()
        item = self._item(execucao, "Limpar piso")
        with self.assertRaises(ErroDeRotina):
            tratar_item_checklist(
                item=item, usuario=self.gestao, status=StatusItemChecklist.PENDENTE
            )

    def test_ocorrencia_encerrada_nao_muda_mais(self):
        execucao = self._execucao()
        for item in execucao.itens.all():
            tratar_item_checklist(
                item=item, usuario=self.gestao, status=StatusItemChecklist.CONCLUIDO
            )
        concluir_execucao(
            execucao=execucao,
            usuario=self.gestao,
            resultado=ResultadoExecucao.OK,
        )

        item = self._item(execucao, "Limpar piso")
        with self.assertRaises(ErroDeRotina):
            tratar_item_checklist(
                item=item,
                usuario=self.gestao,
                status=StatusItemChecklist.NAO_APLICAVEL,
            )


class ConclusaoComChecklistTests(BaseDeChecklist):
    """Tarefa obrigatória segura a conclusão; opcional não segura."""

    def test_obrigatoria_pendente_bloqueia(self):
        execucao = self._execucao()
        with self.assertRaises(ErroDeRotina):
            concluir_execucao(
                execucao=execucao,
                usuario=self.gestao,
                resultado=ResultadoExecucao.OK,
            )

    def test_obrigatorias_tratadas_liberam(self):
        execucao = self._execucao()
        tratar_item_checklist(
            item=self._item(execucao, "Limpar piso"),
            usuario=self.gestao,
            status=StatusItemChecklist.CONCLUIDO,
        )
        tratar_item_checklist(
            item=self._item(execucao, "Limpar mesas"),
            usuario=self.gestao,
            status=StatusItemChecklist.NAO_APLICAVEL,
        )
        tratar_item_checklist(
            item=self._item(execucao, "Esvaziar lixeiras"),
            usuario=self.gestao,
            status=StatusItemChecklist.CONCLUIDO,
        )

        concluida = concluir_execucao(
            execucao=execucao,
            usuario=self.gestao,
            resultado=ResultadoExecucao.OK,
        )
        self.assertEqual(concluida.status, StatusExecucao.CONCLUIDA)

    def test_opcional_pendente_nao_bloqueia(self):
        criar_item_do_checklist(
            self.checklist, "Conferir anormalidades", ordem=4, obrigatorio=False
        )
        execucao = self._pendente(self._rotina())
        for descricao in ("Limpar piso", "Limpar mesas", "Esvaziar lixeiras"):
            tratar_item_checklist(
                item=self._item(execucao, descricao),
                usuario=self.gestao,
                status=StatusItemChecklist.CONCLUIDO,
            )

        concluida = concluir_execucao(
            execucao=execucao,
            usuario=self.gestao,
            resultado=ResultadoExecucao.ATENCAO,
        )
        self.assertEqual(concluida.status, StatusExecucao.CONCLUIDA)
        pendente = self._item(concluida, "Conferir anormalidades")
        self.assertEqual(pendente.status, StatusItemChecklist.PENDENTE)

    def test_checklist_nao_decide_o_resultado(self):
        execucao = self._execucao()
        for descricao in ("Limpar piso", "Limpar mesas", "Esvaziar lixeiras"):
            tratar_item_checklist(
                item=self._item(execucao, descricao),
                usuario=self.gestao,
                status=StatusItemChecklist.CONCLUIDO,
            )
        concluida = concluir_execucao(
            execucao=execucao,
            usuario=self.gestao,
            resultado=ResultadoExecucao.PROBLEMA,
            observacao="Vazamento encontrado na lavanderia.",
        )
        self.assertEqual(concluida.resultado, ResultadoExecucao.PROBLEMA)

    def test_sem_checklist_conclui_normalmente(self):
        execucao = self._execucao(checklist_modelo=None)
        concluida = concluir_execucao(
            execucao=execucao,
            usuario=self.gestao,
            resultado=ResultadoExecucao.OK,
        )
        self.assertEqual(concluida.status, StatusExecucao.CONCLUIDA)


class ProgressoTests(BaseDeChecklist):
    """O andamento do checklist é derivado, nunca guardado."""

    def test_progresso_zerado_sem_checklist(self):
        execucao = self._pendente(self._rotina(checklist_modelo=None))
        self.assertEqual(
            execucao.progresso_do_checklist,
            {
                "total": 0,
                "concluidos": 0,
                "pendentes": 0,
                "nao_aplicaveis": 0,
                "percentual": 0,
            },
        )

    def test_progresso_conta_cada_desfecho(self):
        execucao = self._execucao()
        tratar_item_checklist(
            item=self._item(execucao, "Limpar piso"),
            usuario=self.gestao,
            status=StatusItemChecklist.CONCLUIDO,
        )
        tratar_item_checklist(
            item=self._item(execucao, "Limpar mesas"),
            usuario=self.gestao,
            status=StatusItemChecklist.NAO_APLICAVEL,
        )

        progresso = execucao.progresso_do_checklist
        self.assertEqual(progresso["total"], 3)
        self.assertEqual(progresso["concluidos"], 1)
        self.assertEqual(progresso["nao_aplicaveis"], 1)
        self.assertEqual(progresso["pendentes"], 1)
        self.assertEqual(progresso["percentual"], 67)

    def test_progresso_completo_chega_a_cem(self):
        execucao = self._execucao()
        for descricao in ("Limpar piso", "Limpar mesas", "Esvaziar lixeiras"):
            tratar_item_checklist(
                item=self._item(execucao, descricao),
                usuario=self.gestao,
                status=StatusItemChecklist.CONCLUIDO,
            )
        self.assertEqual(execucao.progresso_do_checklist["percentual"], 100)


class ChecklistIncompletoTests(BaseDeChecklist):
    """O recorte de checklist pendente mostra o que ficou pela metade."""

    def test_aparece_quando_ha_tarefa_pendente(self):
        execucao = self._execucao()
        self.assertIn(execucao, ExecucaoRotina.objects.com_checklist_pendente())

    def test_sai_do_recorte_quando_tudo_foi_tratado(self):
        execucao = self._execucao()
        for descricao in ("Limpar piso", "Limpar mesas", "Esvaziar lixeiras"):
            tratar_item_checklist(
                item=self._item(execucao, descricao),
                usuario=self.gestao,
                status=StatusItemChecklist.CONCLUIDO,
            )
        self.assertNotIn(execucao, ExecucaoRotina.objects.com_checklist_pendente())

    def test_ocorrencia_sem_checklist_nunca_aparece(self):
        execucao = self._pendente(self._rotina(checklist_modelo=None))
        self.assertNotIn(execucao, ExecucaoRotina.objects.com_checklist_pendente())


class AutorizacaoPorPerfilTests(BaseDeChecklist):
    """A permissão de tratar tarefas vem do vínculo e do papel no condomínio."""

    def setUp(self):
        super().setUp()
        self.operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(self.operacional, self.condominio, papel=Papel.OPERACIONAL)

        self.supervisora = criar_pessoa("sup@exemplo.test", "Supervisora")
        criar_vinculo(self.supervisora, self.condominio, papel=Papel.SUPERVISORA)

        self.rotina = self._rotina(responsavel=self.operacional)
        self.execucao = self._pendente(self.rotina)

    def _url(self, item=None):
        item = item or self.execucao.itens.first()
        return reverse(
            "rotinas:tratar_item", args=[self.execucao.pk, item.pk]
        )

    def test_operacional_responsavel_registra_a_tarefa(self):
        self.client.force_login(self.operacional)
        resposta = self.client.post(
            self._url(), {"status": StatusItemChecklist.CONCLUIDO}
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(
            self.execucao.itens.first().status, StatusItemChecklist.CONCLUIDO
        )

    def test_operacional_nao_responsavel_recebe_404(self):
        outra = criar_pessoa("op2@exemplo.test", "Outra pessoa")
        criar_vinculo(outra, self.condominio, papel=Papel.OPERACIONAL)
        self.client.force_login(outra)
        resposta = self.client.post(
            self._url(), {"status": StatusItemChecklist.CONCLUIDO}
        )
        self.assertEqual(resposta.status_code, 404)

    def test_operacional_nao_abre_o_cadastro_do_checklist(self):
        self.client.force_login(self.operacional)
        self.assertEqual(
            self.client.get(reverse("rotinas:detalhe", args=[self.rotina.pk])).status_code,
            404,
        )

    def test_supervisora_registra_tarefa(self):
        self.client.force_login(self.supervisora)
        resposta = self.client.post(
            self._url(), {"status": StatusItemChecklist.NAO_APLICAVEL}
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(
            self.execucao.itens.first().status, StatusItemChecklist.NAO_APLICAVEL
        )

    def test_morador_recebe_404(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)
        resposta = self.client.post(
            self._url(), {"status": StatusItemChecklist.CONCLUIDO}
        )
        self.assertEqual(resposta.status_code, 404)

    def test_ocorrencia_encerrada_nao_aceita_tratamento(self):
        self.client.force_login(self.gestao)
        for item in self.execucao.itens.all():
            tratar_item_checklist(
                item=item, usuario=self.gestao, status=StatusItemChecklist.CONCLUIDO
            )
        concluir_execucao(
            execucao=self.execucao,
            usuario=self.gestao,
            resultado=ResultadoExecucao.OK,
        )

        item = self.execucao.itens.first()
        self.client.force_login(self.supervisora)
        resposta = self.client.post(
            reverse("rotinas:tratar_item", args=[self.execucao.pk, item.pk]),
            {"status": StatusItemChecklist.NAO_APLICAVEL},
        )
        self.assertEqual(resposta.status_code, 403)


class TelaDaOcorrenciaTests(BaseDeChecklist):
    """A tela da ocorrência mostra o checklist e o que falta nele."""

    def setUp(self):
        super().setUp()
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)
        self.rotina = self._rotina()
        self.execucao = self._pendente(self.rotina)

    def test_detalhe_mostra_o_checklist_e_a_tarefa_pendente(self):
        resposta = self.client.get(
            reverse("rotinas:execucao", args=[self.execucao.pk])
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.context["tem_checklist"])
        self.assertEqual(resposta.context["progresso"]["total"], 3)
        self.assertEqual(len(resposta.context["itens"]), 3)
        self.assertTrue(self.execucao.tem_item_obrigatorio_pendente)

    def test_conclusao_bloqueada_pela_tarefa_obrigatoria(self):
        resposta = self.client.post(
            reverse("rotinas:concluir", args=[self.execucao.pk]),
            {"resultado": ResultadoExecucao.OK},
        )
        self.assertEqual(resposta.status_code, 302)
        self.execucao.refresh_from_db()
        self.assertEqual(self.execucao.status, StatusExecucao.PENDENTE)

    def test_lista_de_ocorrencias_marca_o_checklist(self):
        resposta = self.client.get(
            reverse("rotinas:execucoes"), {"recorte": "com_checklist_pendente"}
        )
        self.assertEqual(resposta.status_code, 200)
        pks = [linha["pk"] for linha in resposta.context["execucoes"]]
        self.assertIn(self.execucao.pk, pks)
