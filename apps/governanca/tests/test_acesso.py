"""Quem entra nas telas da governança e quem só consulta.

O módulo responde a quem responde pelo condomínio e a quem supervisiona o dia a
dia. Quem não enxerga o módulo não é avisado de que a tela existe — a resposta é
404 —, e a supervisão mantém o registro sem mexer no vocabulário dos tipos.
"""
from django.test import TestCase
from django.urls import reverse

from apps.cadastros.tests.apoio import criar_condominio, entrar_como
from apps.condominios.constants import Papel
from apps.governanca.models import TipoContrato

TELAS = (
    "governanca:contratos",
    "governanca:garantias",
    "governanca:documentos",
    "governanca:tipos_de_contrato",
    "governanca:tipos_de_documento",
)


class AcessoAsTelasDaGovernancaTests(TestCase):
    """A porta do módulo, papel por papel."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")

    def test_exige_login(self):
        for nome in TELAS:
            with self.subTest(tela=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 302)

    def test_quem_nao_enxerga_o_modulo_recebe_404(self):
        for indice, papel in enumerate(
            (Papel.OPERACIONAL, Papel.PORTEIRO, Papel.MORADOR)
        ):
            entrar_como(self.client, papel, self.condominio, f"{indice}@exemplo.test")
            for nome in TELAS:
                with self.subTest(papel=papel, tela=nome):
                    self.assertEqual(self.client.get(reverse(nome)).status_code, 404)

    def test_gestao_e_supervisao_entram_em_todas_as_telas(self):
        for indice, papel in enumerate((Papel.SINDICA, Papel.SUPERVISORA)):
            entrar_como(self.client, papel, self.condominio, f"g{indice}@exemplo.test")
            for nome in TELAS:
                with self.subTest(papel=papel, tela=nome):
                    self.assertEqual(self.client.get(reverse(nome)).status_code, 200)


class TiposDaGovernancaTests(TestCase):
    """O catálogo de tipos é de quem responde pelo condomínio."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("governanca:tipos_de_contrato")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)

    def test_gestao_cadastra_tipo_de_contrato(self):
        resposta = self.client.post(
            reverse("governanca:criar_tipo_contrato"),
            {
                "condominio": str(self.condominio.pk),
                "nome": "Manutenção preventiva",
                "descricao": "Contratos de rotina",
            },
        )
        self.assertRedirects(resposta, f"{self.url}?condominio={self.condominio.pk}")
        tipo = TipoContrato.objects.get(nome="Manutenção preventiva")
        self.assertEqual(tipo.condominio, self.condominio)
        self.assertEqual(tipo.criado_por, self.gestao)

    def test_nome_repetido_ignorando_caixa_e_recusado(self):
        TipoContrato.objects.create(condominio=self.condominio, nome="Seguro")
        resposta = self.client.post(
            reverse("governanca:criar_tipo_contrato"),
            {"condominio": str(self.condominio.pk), "nome": "seguro"},
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(TipoContrato.objects.count(), 1)

    def test_supervisora_le_o_catalogo_mas_nao_o_reescreve(self):
        entrar_como(
            self.client, Papel.SUPERVISORA, self.condominio, "supervisora@exemplo.test"
        )
        self.assertEqual(self.client.get(self.url).status_code, 200)
        resposta = self.client.post(
            reverse("governanca:criar_tipo_contrato"),
            {"condominio": str(self.condominio.pk), "nome": "Seguro"},
        )
        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(TipoContrato.objects.exists())

    def test_tipo_de_outro_condominio_e_404_ao_editar(self):
        alheio = TipoContrato.objects.create(
            condominio=criar_condominio("Beta"), nome="Seguro"
        )
        resposta = self.client.post(
            reverse("governanca:editar_tipo_contrato", args=[alheio.pk]),
            {"condominio": str(self.condominio.pk), "nome": "Tentativa"},
        )
        self.assertEqual(resposta.status_code, 404)
