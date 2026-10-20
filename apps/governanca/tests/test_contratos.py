"""Contratos: o que o condomínio assumiu, o que cobre e o que está para vencer.

O contrato é registrado com os objetos que ele cobre numa transação só, e a
situação de vigência é lida do prazo na hora da pergunta — nunca guardada.
"""
from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.tests.apoio import criar_condominio, criar_fornecedor, entrar_como
from apps.condominios.constants import Papel
from apps.governanca.constants import SituacaoVigencia
from apps.governanca.models import ContratoEquipamento, ContratoFornecedor, ContratoLocal
from apps.governanca.services import ErroDeGovernanca, criar_contrato
from apps.governanca.tests.apoio import (
    HOJE,
    criar_equipamento_do,
    criar_local_do,
    criar_tipo_contrato,
)
from apps.governanca.tests.apoio import criar_contrato as contrato_de_teste


class ContratosNaTelaTests(TestCase):
    """O registro do contrato e os objetos que ele cobre."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("governanca:contratos")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)
        self.equipamento = criar_equipamento_do(self.condominio, self.gestao)
        self.local = criar_local_do(self.condominio, self.gestao)
        self.tipo = criar_tipo_contrato(self.condominio)

    def _dados(self, **campos):
        fornecedor = criar_fornecedor(
            condominio=self.condominio, criado_por=self.gestao
        )
        base = {
            "condominio": str(self.condominio.pk),
            "fornecedor": str(fornecedor.pk),
            "tipo_contrato": str(self.tipo.pk),
            "objeto": "Manutenção de elevadores",
            "data_inicio": "2026-01-10",
            "data_fim": "2027-01-10",
            "antecedencia_alerta_dias": "30",
        }
        return {**base, **campos}

    def test_gestao_cadastra_contrato_com_o_que_ele_cobre(self):
        resposta = self.client.post(
            reverse("governanca:criar_contrato"),
            self._dados(
                equipamentos=[str(self.equipamento.pk)],
                locais=[str(self.local.pk)],
            ),
        )
        self.assertEqual(resposta.status_code, 302)
        contrato = ContratoFornecedor.objects.get(objeto="Manutenção de elevadores")
        self.assertEqual(contrato.condominio, self.condominio)
        self.assertEqual(contrato.criado_por, self.gestao)
        self.assertEqual(
            set(
                ContratoEquipamento.objects.filter(contrato=contrato).values_list(
                    "equipamento_id", flat=True
                )
            ),
            {self.equipamento.pk},
        )
        self.assertEqual(
            set(
                ContratoLocal.objects.filter(contrato=contrato).values_list(
                    "local_id", flat=True
                )
            ),
            {self.local.pk},
        )

    def test_equipamento_de_outro_condominio_e_recusado_sem_gravar_nada(self):
        alheio = criar_equipamento_do(criar_condominio("Beta"), self.gestao)
        resposta = self.client.post(
            reverse("governanca:criar_contrato"),
            self._dados(equipamentos=[str(alheio.pk)]),
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(ContratoFornecedor.objects.exists())

    def test_contrato_de_outro_condominio_e_404_ao_editar(self):
        alheio = contrato_de_teste(criar_condominio("Beta"), self.gestao)
        resposta = self.client.post(
            reverse("governanca:editar_contrato", args=[alheio.pk]),
            self._dados(),
        )
        self.assertEqual(resposta.status_code, 404)

    def test_supervisao_registra_e_corrige_contrato(self):
        entrar_como(
            self.client, Papel.SUPERVISORA, self.condominio, "supervisora@exemplo.test"
        )
        resposta = self.client.post(reverse("governanca:criar_contrato"), self._dados())
        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(ContratoFornecedor.objects.exists())

    def test_edicao_do_cabecalho_preserva_a_cobertura_escolhida(self):
        contrato = contrato_de_teste(self.condominio, self.gestao)
        ContratoEquipamento.objects.create(
            contrato=contrato, equipamento=self.equipamento
        )
        resposta = self.client.post(
            reverse("governanca:editar_contrato", args=[contrato.pk]),
            {
                "condominio": str(self.condominio.pk),
                "fornecedor": str(contrato.fornecedor.pk),
                "objeto": "Manutenção de elevadores — revisada",
                "data_inicio": "2026-01-10",
                "data_fim": "2027-01-10",
                "antecedencia_alerta_dias": "30",
                "equipamentos": [str(self.equipamento.pk)],
            },
        )
        self.assertEqual(resposta.status_code, 302)
        contrato.refresh_from_db()
        self.assertEqual(contrato.objeto, "Manutenção de elevadores — revisada")
        self.assertTrue(
            ContratoEquipamento.objects.filter(contrato=contrato).exists()
        )


class VigenciaDoContratoTests(TestCase):
    """A situação do contrato é lida do prazo: nada é guardado como estado."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)

    def _contrato(self, **campos):
        return contrato_de_teste(self.condominio, self.gestao, **campos)

    def test_contrato_em_curso_esta_vigente(self):
        contrato = self._contrato(antecedencia_alerta_dias=30)
        self.assertEqual(contrato.situacao_em(HOJE), SituacaoVigencia.VIGENTE)

    def test_contrato_perto_do_fim_esta_vencendo(self):
        contrato = self._contrato(
            data_fim=HOJE + timedelta(days=10), antecedencia_alerta_dias=30
        )
        self.assertEqual(contrato.situacao_em(HOJE), SituacaoVigencia.VENCENDO)

    def test_contrato_terminado_esta_vencido(self):
        contrato = self._contrato(data_fim=HOJE - timedelta(days=1))
        self.assertEqual(contrato.situacao_em(HOJE), SituacaoVigencia.VENCIDO)

    def test_contrato_que_ainda_nao_comecou_esta_futuro(self):
        contrato = self._contrato(
            data_inicio=HOJE + timedelta(days=10),
            data_fim=HOJE + timedelta(days=400),
        )
        self.assertEqual(contrato.situacao_em(HOJE), SituacaoVigencia.FUTURO)

    def test_contrato_desativado_esta_inativo(self):
        contrato = self._contrato(ativo=False)
        self.assertEqual(contrato.situacao_em(HOJE), SituacaoVigencia.INATIVO)

    def test_contrato_sem_fim_esta_vigente(self):
        contrato = self._contrato(data_fim=None)
        self.assertEqual(contrato.situacao_em(HOJE), SituacaoVigencia.VIGENTE)


class ServicoDeContratoTests(TestCase):
    """O domínio recusa o que atravessa a fronteira do condomínio."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)
        self.fornecedor = contrato_de_teste(self.condominio, self.gestao).fornecedor

    def test_local_de_outro_condominio_e_recusado_no_servico(self):
        alheio = criar_local_do(criar_condominio("Beta"), self.gestao)
        with self.assertRaises(ErroDeGovernanca):
            criar_contrato(
                condominio=self.condominio,
                criado_por=self.gestao,
                dados={
                    "fornecedor": self.fornecedor,
                    "objeto": "Portaria",
                    "data_inicio": date(2026, 1, 1),
                },
                locais=[alheio],
            )
        self.assertEqual(ContratoFornecedor.objects.count(), 1)
