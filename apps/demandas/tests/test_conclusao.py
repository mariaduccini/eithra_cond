"""Concluir a demanda: pelo ícone, em lote e só para quem pode.

Concluir continua sendo o serviço oficial do domínio que encerra — a tela nova
apenas orquestra, numa transação só, o que já existia: a mudança de situação, o
relato e a prova que o sustenta. Estes testes prendem o contrato: o ícone só
existe para quem tem a conclusão entre as situações que alcança; um clique
confirma e registra; o lote dá a cada demanda o próprio evento; a manutenção em
aberto pede a confirmação a mais; e o envio direto de quem não pode é recusado
com 403 — ou 404, quando a demanda nem é visível para a pessoa.

Nenhum teste fala com armazenamento de verdade: o provider é substituído por um
storage em memória, como no resto do módulo.
"""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.demandas.constants import StatusDemanda, TipoEventoDemanda
from apps.demandas.models import EvidenciaDemanda, HistoricoDemanda
from apps.demandas.services import criar_demanda
from apps.demandas.tests.apoio import (
    StorageDeMentira,
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
    png,
)
from apps.manutencoes.services import criar_manutencao


class BaseDeConclusao(TestCase):
    """Uma demanda de teste, gestão à frente e um storage em memória."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

        self.provider = StorageDeMentira()
        patcher = patch(
            "apps.demandas.evidencias.obter_provider", return_value=self.provider
        )
        self.patch_do_storage = patcher.start()
        self.addCleanup(patcher.stop)

    def _demanda(self, **campos):
        base = {
            "condominio": self.condominio,
            "criado_por": self.gestao,
            "titulo": "Demanda de teste",
        }
        base.update(campos)
        return criar_demanda(**base)

    def _concluir(self, demanda, **dados):
        return self.client.post(
            reverse("demandas:concluir", args=[demanda.pk]), dados
        )


class ConcluirPeloIconeTests(BaseDeConclusao):
    """A porta de uma etapa: o relato, a prova e a conclusão num envio só."""

    def test_concluir_encerra_a_demanda_e_registra_o_relato(self):
        demanda = self._demanda()

        resposta = self._concluir(
            demanda, texto="Trocado o rolamento e testado o portão."
        )

        self.assertEqual(resposta.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.CONCLUIDA)
        self.assertIsNotNone(demanda.concluido_em)

        tipos = list(
            HistoricoDemanda.objects.filter(demanda=demanda).values_list(
                "tipo_evento", flat=True
            )
        )
        self.assertIn(TipoEventoDemanda.CONCLUIDA, tipos)
        self.assertIn(TipoEventoDemanda.OBSERVACAO, tipos)
        self.assertTrue(
            HistoricoDemanda.objects.filter(
                demanda=demanda,
                tipo_evento=TipoEventoDemanda.OBSERVACAO,
                descricao="Trocado o rolamento e testado o portão.",
            ).exists()
        )

    def test_concluir_sem_relato_tambem_encerra(self):
        """Concluir precisa ser simples: nada é obrigatório para encerrar."""
        demanda = self._demanda()

        resposta = self._concluir(demanda)

        self.assertEqual(resposta.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.CONCLUIDA)
        # A demanda nasce com o evento CRIADA e a conclusão acrescenta o dela;
        # o relato vazio não vira uma observação a mais no histórico.
        self.assertTrue(
            HistoricoDemanda.objects.filter(
                demanda=demanda, tipo_evento=TipoEventoDemanda.CONCLUIDA
            ).exists()
        )
        self.assertFalse(
            HistoricoDemanda.objects.filter(
                demanda=demanda, tipo_evento=TipoEventoDemanda.OBSERVACAO
            ).exists()
        )

    def test_a_prova_acompanha_o_evento_de_conclusao(self):
        """A evidência se prende ao instante que ela sustenta: a conclusão."""
        demanda = self._demanda()

        resposta = self.client.post(
            reverse("demandas:concluir", args=[demanda.pk]),
            {"texto": "Reparo concluído.", "evidencias": [png()]},
        )

        self.assertEqual(resposta.status_code, 302)
        evidencia = EvidenciaDemanda.objects.get()
        self.assertEqual(evidencia.demanda_id, demanda.pk)
        self.assertEqual(evidencia.evento.tipo_evento, TipoEventoDemanda.CONCLUIDA)

    def test_concluir_exige_post(self):
        demanda = self._demanda()

        resposta = self.client.get(reverse("demandas:concluir", args=[demanda.pk]))

        self.assertEqual(resposta.status_code, 405)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.ABERTA)


class ConcluirEmLoteTests(BaseDeConclusao):
    """O lote muda o esforço de quem clica, nunca a memória de cada demanda."""

    def test_concluir_em_lote_encerra_cada_demanda_com_o_proprio_evento(self):
        primeira = self._demanda(titulo="Primeira")
        segunda = self._demanda(titulo="Segunda")

        resposta = self.client.post(
            reverse("demandas:concluir_em_lote"),
            {
                "demandas": [str(primeira.pk), str(segunda.pk)],
                "texto": "Encerradas na revisão de novembro.",
            },
        )

        self.assertEqual(resposta.status_code, 302)
        for demanda in (primeira, segunda):
            demanda.refresh_from_db()
            self.assertEqual(demanda.status, StatusDemanda.CONCLUIDA)
            # Cada uma tem o seu próprio evento de conclusão — o lote não
            # compartilha um evento entre as demandas.
            self.assertTrue(
                HistoricoDemanda.objects.filter(
                    demanda=demanda, tipo_evento=TipoEventoDemanda.CONCLUIDA
                ).exists()
            )
            self.assertTrue(
                HistoricoDemanda.objects.filter(
                    demanda=demanda,
                    tipo_evento=TipoEventoDemanda.OBSERVACAO,
                    descricao="Encerradas na revisão de novembro.",
                ).exists()
            )

    def test_o_lote_ignora_o_que_a_pessoa_nao_pode_concluir(self):
        """Quem enxerga mas não alcança a conclusão não conclui por engano."""
        demanda = self._demanda()
        operador = criar_pessoa("operacao@exemplo.test", "Operação")
        criar_vinculo(operador, self.condominio, papel=Papel.OPERACIONAL)
        self.client.force_login(operador)

        resposta = self.client.post(
            reverse("demandas:concluir_em_lote"),
            {"demandas": [str(demanda.pk)], "texto": "Encerradas."},
        )

        self.assertEqual(resposta.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.ABERTA)
        self.assertFalse(
            HistoricoDemanda.objects.filter(
                demanda=demanda, tipo_evento=TipoEventoDemanda.CONCLUIDA
            ).exists()
        )

    def test_concluir_em_lote_exige_post(self):
        resposta = self.client.get(reverse("demandas:concluir_em_lote"))

        self.assertEqual(resposta.status_code, 405)


class ManutencaoAbertaTests(BaseDeConclusao):
    """A manutenção em aberto é um aviso, não um impedimento do domínio."""

    def test_o_modal_avisa_da_manutencao_aberta(self):
        demanda = self._demanda()
        criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            demanda_origem=demanda,
            titulo="Trocar o rolamento",
        )

        resposta = self.client.get(reverse("demandas:detalhe", args=[demanda.pk]))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Há uma manutenção em aberto ligada a esta demanda")

    def test_concluir_com_manutencao_aberta_pede_confirmacao(self):
        demanda = self._demanda()
        criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            demanda_origem=demanda,
            titulo="Trocar o rolamento",
        )

        sem_confirmar = self._concluir(demanda, texto="Encerrando mesmo assim.")

        self.assertEqual(sem_confirmar.status_code, 400)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.ABERTA)

        confirmando = self._concluir(
            demanda, texto="Encerrando mesmo assim.", confirmar_manutencao="1"
        )

        self.assertEqual(confirmando.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.CONCLUIDA)

    def test_sem_manutencao_aberta_a_confirmacao_nao_e_pedida(self):
        demanda = self._demanda()

        resposta = self._concluir(demanda, texto="Reparo concluído.")

        self.assertEqual(resposta.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.CONCLUIDA)


class SemPermissaoTests(BaseDeConclusao):
    """A régua do backend é a mesma da tela: 404 para quem não vê, 403 para quem vê."""

    def _operacao_sem_vinculo_de_conclusao(self, papel=Papel.OPERACIONAL):
        """Operação que enxerga o módulo, mas não responde pela conclusão."""
        operador = criar_pessoa("operacao@exemplo.test", "Operação")
        criar_vinculo(operador, self.condominio, papel=papel)
        self.client.force_login(operador)
        return operador

    def test_quem_nao_enxerga_a_demanda_recebe_404(self):
        demanda = self._demanda()
        estranho = criar_pessoa("estranho@exemplo.test", "Estranho")
        self.client.force_login(estranho)

        resposta = self._concluir(demanda, texto="Tentativa indevida.")

        self.assertEqual(resposta.status_code, 404)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.ABERTA)

    def test_quem_enxerga_mas_nao_pode_concluir_recebe_403(self):
        demanda = self._demanda()
        self._operacao_sem_vinculo_de_conclusao()

        resposta = self._concluir(demanda, texto="Tentativa indevida.")

        self.assertEqual(resposta.status_code, 403)
        demanda.refresh_from_db()
        self.assertEqual(demanda.status, StatusDemanda.ABERTA)


class IconeNaTelaTests(BaseDeConclusao):
    """Onde o ícone aparece, e onde ele não aparece."""

    def _operacao(self):
        operador = criar_pessoa("operacao@exemplo.test", "Operação")
        criar_vinculo(operador, self.condominio, papel=Papel.OPERACIONAL)
        self.client.force_login(operador)
        return operador

    # -- Detalhe --------------------------------------------------------------

    def test_o_detalhe_mostra_o_icone_para_quem_pode_concluir(self):
        demanda = self._demanda()

        resposta = self.client.get(reverse("demandas:detalhe", args=[demanda.pk]))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'data-bs-target="#concluir-demanda"')
        self.assertContains(resposta, 'aria-label="Concluir demanda"')

    def test_o_detalhe_nao_mostra_o_icone_para_quem_nao_pode_concluir(self):
        demanda = self._demanda()
        self._operacao()

        resposta = self.client.get(reverse("demandas:detalhe", args=[demanda.pk]))

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, 'data-bs-target="#concluir-demanda"')
        self.assertNotContains(resposta, 'id="concluir-demanda"')

    # -- Listagem -------------------------------------------------------------

    def test_a_listagem_oferece_o_icone_e_a_selecao(self):
        self._demanda(titulo="Portão travando")

        resposta = self.client.get(reverse("demandas:listagem"))

        self.assertEqual(resposta.status_code, 200)
        # O ícone de concluir por linha, no mesmo padrão dos ícones de ação.
        self.assertContains(resposta, 'data-bs-target="#concluir-linha"')
        self.assertContains(resposta, 'aria-label="Concluir demanda"')
        # A seleção e a conclusão em lote.
        self.assertContains(resposta, "eithra-col-selecao")
        self.assertContains(resposta, 'form="form-concluir-lote"')
        self.assertContains(resposta, "Concluir selecionadas")

    def test_a_listagem_nao_oferece_o_que_a_pessoa_nao_pode_concluir(self):
        self._demanda()
        self._operacao()

        resposta = self.client.get(reverse("demandas:listagem"))

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, 'data-bs-target="#concluir-linha"')
        self.assertNotContains(resposta, 'form="form-concluir-lote"')
        self.assertNotContains(resposta, "eithra-lote-barra")

    def test_registrar_atualizacao_continua_no_detalhe_e_na_lista(self):
        """Contar uma novidade sem concluir continua existindo."""
        demanda = self._demanda()

        detalhe = self.client.get(reverse("demandas:detalhe", args=[demanda.pk]))
        self.assertContains(detalhe, "Registrar atualização")

        listagem = self.client.get(reverse("demandas:listagem"))
        self.assertContains(listagem, "Contar novidade")
