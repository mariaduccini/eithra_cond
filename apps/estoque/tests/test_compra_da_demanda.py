"""A compra que nasce de uma demanda.

Registrar a compra a partir do menu da demanda grava o vínculo (``demanda_origem``)
e deixa, no histórico da demanda, a lembrança de que ela foi registrada. Estes
testes prendem esse contrato, o formulário que abre preenchido e o caminho de
volta no detalhe da compra. Nenhum dado real entra aqui.
"""
from datetime import date

from django.test import TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.demandas.constants import TipoEventoDemanda
from apps.demandas.models import HistoricoDemanda
from apps.demandas.services import criar_demanda
from apps.estoque.models import Compra
from apps.estoque.tests.apoio import (
    criar_condominio,
    criar_fornecedor,
    criar_pessoa,
    criar_vinculo,
)


class BaseDeCompraDaDemanda(TestCase):
    """Uma gestão ligada ao condomínio e uma demanda para citar."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    def _demanda(self, **campos):
        base = {
            "condominio": self.condominio,
            "criado_por": self.gestao,
            "titulo": "Comprar lâmpadas para o corredor",
        }
        base.update(campos)
        return criar_demanda(**base)


class RegistrarCompraDaDemandaTests(BaseDeCompraDaDemanda):
    """A compra registrada a partir da demanda preserva de onde veio."""

    def test_registrar_compra_grava_o_vinculo_e_o_historico(self):
        demanda = self._demanda()
        fornecedor = criar_fornecedor(self.condominio, "Elétrica Central")

        resposta = self.client.post(
            reverse("compras:criar"),
            {
                "condominio": str(self.condominio.pk),
                "data_compra": date.today().isoformat(),
                "fornecedor": str(fornecedor.pk),
                "demanda_origem": str(demanda.pk),
                "observacao": demanda.titulo,
            },
        )

        self.assertEqual(resposta.status_code, 302)
        compra = Compra.objects.get()
        self.assertEqual(compra.demanda_origem_id, demanda.pk)

        evento = HistoricoDemanda.objects.get(
            demanda=demanda, tipo_evento=TipoEventoDemanda.COMPRA
        )
        self.assertIn("Compra registrada", evento.descricao)
        self.assertIn("Elétrica Central", evento.descricao)

    def test_o_formulario_abre_preenchido_pela_demanda(self):
        demanda = self._demanda()

        resposta = self.client.get(
            reverse("compras:listagem"),
            {"abrir": "manual", "demanda": str(demanda.pk)},
        )

        self.assertEqual(resposta.status_code, 200)
        formulario = resposta.context["formulario_compra"]
        self.assertEqual(formulario.initial["observacao"], demanda.titulo)
        self.assertEqual(
            str(formulario.initial["demanda_origem"]), str(demanda.pk)
        )

    def test_o_detalhe_da_compra_mostra_a_demanda_de_origem(self):
        demanda = self._demanda()
        compra = Compra.objects.create(
            condominio=self.condominio,
            criado_por=self.gestao,
            data_compra=date.today(),
            demanda_origem=demanda,
        )

        resposta = self.client.get(reverse("compras:detalhe", args=[compra.pk]))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Veio da demanda")
        self.assertContains(resposta, demanda.titulo)
