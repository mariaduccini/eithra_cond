"""Concluir a ocorrência direto da lista de ocorrências.

A lista de trabalho é onde a operação passa o dia. Aqui se verifica que a linha
inteira abre a ocorrência, que o ícone de concluir só aparece para quem pode
concluir, que a tarefa obrigatória pendente continua segurando a conclusão e que
tratar uma tarefa volta para a listagem — sem aceitar destino externo.
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
from apps.rotinas.services import criar_rotina
from apps.rotinas.tests.apoio import (
    criar_checklist,
    criar_condominio,
    criar_item_do_checklist,
    criar_pessoa,
    criar_vinculo,
)


class BaseDaListagem(TestCase):
    """Um condomínio com gestão, e rotinas que podem carregar um checklist."""

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

    def _rotina_com_checklist(self, **campos):
        checklist = criar_checklist(self.condominio, self.gestao)
        criar_item_do_checklist(checklist, "Limpar piso", ordem=1)
        criar_item_do_checklist(checklist, "Limpar mesas", ordem=2)
        return self._rotina(checklist_modelo=checklist, **campos)

    def _pendente(self, rotina):
        return rotina.execucoes.get(status=StatusExecucao.PENDENTE)


class IconeDeConcluirTests(BaseDaListagem):
    """O ícone de concluir acompanha quem pode concluir, e só."""

    def test_a_gestao_recebe_o_icone(self):
        execucao = self._pendente(self._rotina())
        resposta = self.client.get(reverse("rotinas:execucoes"))
        self.assertContains(
            resposta, reverse("rotinas:concluir", args=[execucao.pk])
        )

    def test_sem_poder_sobre_a_ocorrencia_nao_ha_icone(self):
        execucao = self._pendente(self._rotina())
        outra = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(outra, self.condominio, papel=Papel.OPERACIONAL)

        self.client.force_login(outra)
        resposta = self.client.get(reverse("rotinas:execucoes"))

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(
            resposta, reverse("rotinas:concluir", args=[execucao.pk])
        )


class ConclusaoComPendenciaTests(BaseDaListagem):
    """A tarefa obrigatória pendente continua segurando a conclusão."""

    def test_obrigatoria_pendente_recusa_a_conclusao(self):
        execucao = self._pendente(self._rotina_com_checklist())
        resposta = self.client.post(
            reverse("rotinas:concluir", args=[execucao.pk]),
            {"resultado": ResultadoExecucao.OK},
        )
        self.assertEqual(resposta.status_code, 302)
        execucao.refresh_from_db()
        self.assertEqual(execucao.status, StatusExecucao.PENDENTE)


class TratarTarefaComDestinoTests(BaseDaListagem):
    """Tratar a tarefa volta para a listagem, sem aceitar destino externo."""

    def _url(self, execucao, item):
        return reverse("rotinas:tratar_item", args=[execucao.pk, item.pk])

    def test_tratar_com_next_volta_para_a_listagem(self):
        execucao = self._pendente(self._rotina_com_checklist())
        item = execucao.itens.first()

        resposta = self.client.post(
            self._url(execucao, item),
            {
                "status": StatusItemChecklist.CONCLUIDO,
                "next": reverse("rotinas:execucoes"),
            },
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta.url, reverse("rotinas:execucoes"))
        item.refresh_from_db()
        self.assertEqual(item.status, StatusItemChecklist.CONCLUIDO)

    def test_next_externo_e_ignorado(self):
        execucao = self._pendente(self._rotina_com_checklist())
        item = execucao.itens.first()

        resposta = self.client.post(
            self._url(execucao, item),
            {
                "status": StatusItemChecklist.CONCLUIDO,
                "next": "https://exemplo.invalido/fora",
            },
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(
            resposta.url, reverse("rotinas:execucao", args=[execucao.pk])
        )


class LinhaAbreAOcorrenciaTests(BaseDaListagem):
    """A linha inteira leva à ocorrência; o nome da rotina não é mais link."""

    def test_linha_aponta_para_a_ocorrencia(self):
        rotina = self._rotina()
        execucao = self._pendente(rotina)

        resposta = self.client.get(reverse("rotinas:execucoes"))

        self.assertContains(
            resposta, reverse("rotinas:execucao", args=[execucao.pk])
        )
        self.assertNotContains(
            resposta, reverse("rotinas:detalhe", args=[rotina.pk])
        )
