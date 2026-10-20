"""Garantias: o que uma garantia em uso precisa dizer.

A regra que governa este arquivo é do domínio, e não do banco: uma garantia em
uso cobre pelo menos um equipamento, uma manutenção ou um item de compra. O
banco não alcança outra tabela, e por isso a resposta é humana e vem do serviço,
dentro da transação.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.tests.apoio import criar_condominio, entrar_como
from apps.condominios.constants import Papel
from apps.governanca.constants import SituacaoVigencia
from apps.governanca.models import (
    Garantia,
    GarantiaEquipamento,
    GarantiaItemCompra,
    GarantiaManutencao,
)
from apps.governanca.services import (
    ErroDeGovernanca,
    cobertura_da_garantia,
    criar_garantia,
    definir_ativo_garantia,
    editar_garantia,
)
from apps.governanca.tests.apoio import (
    HOJE,
    criar_equipamento_do,
    criar_item_de_compra,
    criar_manutencao,
)
from apps.governanca.tests.apoio import criar_garantia as garantia_de_teste


class GarantiaNaTelaTests(TestCase):
    """O registro da garantia e o que ela assegura."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("governanca:garantias")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)
        self.equipamento = criar_equipamento_do(self.condominio, self.gestao)

    def _dados(self, **campos):
        base = {
            "condominio": str(self.condominio.pk),
            "descricao": "Garantia da bomba de recalque",
            "data_inicio": "2026-01-10",
            "data_fim": "2027-01-10",
            "antecedencia_alerta_dias": "30",
        }
        return {**base, **campos}

    def test_gestao_cadastra_garantia_com_equipamento_coberto(self):
        resposta = self.client.post(
            reverse("governanca:criar_garantia"),
            self._dados(equipamentos=[str(self.equipamento.pk)]),
        )
        self.assertEqual(resposta.status_code, 302)
        garantia = Garantia.objects.get(descricao="Garantia da bomba de recalque")
        self.assertEqual(garantia.condominio, self.condominio)
        self.assertEqual(cobertura_da_garantia(garantia), 1)

    def test_garantia_em_uso_sem_cobertura_e_recusada_sem_gravar_nada(self):
        resposta = self.client.post(
            reverse("governanca:criar_garantia"), self._dados()
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(Garantia.objects.exists())

    def test_retirar_garantia_de_uso_preserva_o_registro(self):
        garantia = garantia_de_teste(self.condominio, self.gestao)
        definir_ativo_garantia(garantia=garantia, ativo=False)
        garantia.refresh_from_db()
        self.assertFalse(garantia.ativo)
        self.assertTrue(Garantia.objects.filter(pk=garantia.pk).exists())
        self.assertEqual(garantia.situacao, SituacaoVigencia.INATIVO)

    def test_equipamento_de_outro_condominio_e_recusado(self):
        alheio = criar_equipamento_do(criar_condominio("Beta"), self.gestao)
        resposta = self.client.post(
            reverse("governanca:criar_garantia"),
            self._dados(equipamentos=[str(alheio.pk)]),
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(Garantia.objects.exists())

    def test_garantia_de_outro_condominio_e_404_ao_editar(self):
        alheia = garantia_de_teste(criar_condominio("Beta"), self.gestao)
        resposta = self.client.post(
            reverse("governanca:editar_garantia", args=[alheia.pk]), self._dados()
        )
        self.assertEqual(resposta.status_code, 404)


class CoberturaDaGarantiaTests(TestCase):
    """O que a garantia cobre, somando os três tipos de vínculo."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)

    def test_garantia_sem_cobertura_e_recusada_pelo_servico(self):
        with self.assertRaises(ErroDeGovernanca):
            criar_garantia(
                condominio=self.condominio,
                criado_por=self.gestao,
                dados={
                    "descricao": "Garantia sem cobertura",
                    "data_inicio": HOJE,
                },
            )
        self.assertFalse(Garantia.objects.exists())

    def test_garantia_cobre_equipamento_manutencao_e_item_de_compra(self):
        equipamento = criar_equipamento_do(self.condominio, self.gestao)
        manutencao = criar_manutencao(self.condominio, self.gestao)
        item = criar_item_de_compra(self.condominio, self.gestao)
        garantia = criar_garantia(
            condominio=self.condominio,
            criado_por=self.gestao,
            dados={"descricao": "Garantia do selo mecânico", "data_inicio": HOJE},
            equipamentos=[equipamento],
            manutencoes=[manutencao],
            itens_de_compra=[item],
        )
        self.assertEqual(cobertura_da_garantia(garantia), 3)
        self.assertTrue(
            GarantiaEquipamento.objects.filter(
                garantia=garantia, equipamento=equipamento
            ).exists()
        )
        self.assertTrue(
            GarantiaManutencao.objects.filter(
                garantia=garantia, manutencao=manutencao
            ).exists()
        )
        self.assertTrue(
            GarantiaItemCompra.objects.filter(
                garantia=garantia, item_compra=item
            ).exists()
        )

    def test_manutencao_de_outro_condominio_e_recusada(self):
        alheia = criar_manutencao(criar_condominio("Beta"), self.gestao)
        with self.assertRaises(ErroDeGovernanca):
            criar_garantia(
                condominio=self.condominio,
                criado_por=self.gestao,
                dados={"descricao": "Garantia do serviço", "data_inicio": HOJE},
                manutencoes=[alheia],
            )
        self.assertFalse(Garantia.objects.exists())

    def test_retirar_a_ultima_cobertura_de_garantia_em_uso_e_recusado(self):
        equipamento = criar_equipamento_do(self.condominio, self.gestao)
        garantia = criar_garantia(
            condominio=self.condominio,
            criado_por=self.gestao,
            dados={"descricao": "Garantia da bomba", "data_inicio": HOJE},
            equipamentos=[equipamento],
        )
        with self.assertRaises(ErroDeGovernanca):
            editar_garantia(garantia=garantia, dados={}, equipamentos=[])
        self.assertEqual(cobertura_da_garantia(garantia), 1)

    def test_devolver_ao_uso_uma_garantia_sem_cobertura_e_recusado(self):
        garantia = garantia_de_teste(
            self.condominio, self.gestao, ativo=False
        )
        with self.assertRaises(ErroDeGovernanca):
            definir_ativo_garantia(garantia=garantia, ativo=True)
        garantia.refresh_from_db()
        self.assertFalse(garantia.ativo)


class VigenciaDaGarantiaTests(TestCase):
    """A situação da garantia também é lida do prazo, nunca guardada."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)

    def _garantia(self, **campos):
        return garantia_de_teste(
            self.condominio, self.gestao, descricao="Garantia da bomba", **campos
        )

    def test_garantia_em_curso_esta_vigente(self):
        self.assertEqual(
            self._garantia().situacao_em(HOJE), SituacaoVigencia.VIGENTE
        )

    def test_garantia_perto_do_fim_esta_vencendo(self):
        garantia = self._garantia(
            data_fim=HOJE + timedelta(days=5), antecedencia_alerta_dias=30
        )
        self.assertEqual(garantia.situacao_em(HOJE), SituacaoVigencia.VENCENDO)

    def test_garantia_terminada_esta_vencida(self):
        garantia = self._garantia(data_fim=HOJE - timedelta(days=5))
        self.assertEqual(garantia.situacao_em(HOJE), SituacaoVigencia.VENCIDO)

    def test_garantia_sem_fim_esta_vigente(self):
        self.assertEqual(
            self._garantia(data_fim=None).situacao_em(HOJE), SituacaoVigencia.VIGENTE
        )
