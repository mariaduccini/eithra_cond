"""Documentos: o que vale, até quando, e a que está ligado.

O documento pode não pertencer a mais nada — uma ata, um regimento — e pode
valer para um contrato e um equipamento ao mesmo tempo. O que o tipo decide é se
o documento precisa de validade, e com quantos dias de antecedência a gestão
quer ser lembrada.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse

from apps.cadastros.tests.apoio import criar_condominio, entrar_como
from apps.condominios.constants import Papel
from apps.governanca.constants import SituacaoVigencia
from apps.governanca.models import (
    DocumentoContrato,
    DocumentoEquipamento,
    DocumentoOperacional,
    DocumentoFornecedor,
)
from apps.governanca.tests.apoio import (
    HOJE,
    criar_contrato,
    criar_documento,
    criar_equipamento_do,
    criar_tipo_documento,
)


class DocumentoNaTelaTests(TestCase):
    """O registro do documento e os contextos a que ele se liga."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("governanca:documentos")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)
        self.tipo = criar_tipo_documento(self.condominio, nome="Certificado")

    def _dados(self, **campos):
        base = {
            "condominio": str(self.condominio.pk),
            "tipo_documento": str(self.tipo.pk),
            "titulo": "Certificado do elevador",
            "data_validade": "2027-01-10",
        }
        return {**base, **campos}

    def test_gestao_cadastra_documento(self):
        resposta = self.client.post(
            reverse("governanca:criar_documento"), self._dados()
        )
        self.assertEqual(resposta.status_code, 302)
        documento = DocumentoOperacional.objects.get(titulo="Certificado do elevador")
        self.assertEqual(documento.condominio, self.condominio)
        self.assertEqual(documento.tipo_documento, self.tipo)
        self.assertEqual(documento.relacionado_a, "Condomínio")

    def test_documento_ligado_ao_contrato_mostra_o_contrato(self):
        contrato = criar_contrato(self.condominio, self.gestao)
        resposta = self.client.post(
            reverse("governanca:criar_documento"),
            self._dados(contratos=[str(contrato.pk)]),
        )
        self.assertEqual(resposta.status_code, 302)
        documento = DocumentoOperacional.objects.get(titulo="Certificado do elevador")
        self.assertEqual(documento.relacionado_a, str(contrato))
        self.assertTrue(
            DocumentoContrato.objects.filter(
                documento=documento, contrato=contrato
            ).exists()
        )

    def test_documento_do_tipo_que_exige_validade_sem_data_e_recusado(self):
        exigente = criar_tipo_documento(
            self.condominio, nome="Laudo", exige_validade=True
        )
        resposta = self.client.post(
            reverse("governanca:criar_documento"),
            {
                "condominio": str(self.condominio.pk),
                "tipo_documento": str(exigente.pk),
                "titulo": "Laudo estrutural",
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(DocumentoOperacional.objects.exists())

    def test_tipo_de_outro_condominio_e_recusado(self):
        alheio = criar_tipo_documento(criar_condominio("Beta"), nome="Ata")
        resposta = self.client.post(
            reverse("governanca:criar_documento"),
            self._dados(tipo_documento=str(alheio.pk)),
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(DocumentoOperacional.objects.exists())

    def test_documento_de_outro_condominio_e_404_ao_editar(self):
        alheio = criar_documento(criar_condominio("Beta"), self.gestao)
        resposta = self.client.post(
            reverse("governanca:editar_documento", args=[alheio.pk]), self._dados()
        )
        self.assertEqual(resposta.status_code, 404)

    def test_documento_pode_valer_para_um_equipamento(self):
        equipamento = criar_equipamento_do(self.condominio, self.gestao)
        resposta = self.client.post(
            reverse("governanca:criar_documento"),
            self._dados(equipamentos=[str(equipamento.pk)]),
        )
        self.assertEqual(resposta.status_code, 302)
        documento = DocumentoOperacional.objects.get(titulo="Certificado do elevador")
        self.assertEqual(documento.relacionado_a, str(equipamento))
        self.assertTrue(
            DocumentoEquipamento.objects.filter(
                documento=documento, equipamento=equipamento
            ).exists()
        )

    def test_fornecedor_de_outro_condominio_e_recusado(self):
        from apps.cadastros.tests.apoio import criar_fornecedor

        alheio = criar_fornecedor(criar_condominio("Beta"), criado_por=self.gestao)
        resposta = self.client.post(
            reverse("governanca:criar_documento"),
            self._dados(fornecedores=[str(alheio.pk)]),
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(DocumentoOperacional.objects.exists())
        self.assertFalse(DocumentoFornecedor.objects.exists())

    def test_lista_nao_traz_tipo_de_documento_como_acao_principal(self):
        resposta = self.client.get(self.url)
        self.assertNotContains(resposta, reverse("governanca:tipos_de_documento"))


class VigenciaDoDocumentoTests(TestCase):
    """A validade do documento, lida do prazo e da regra do tipo."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)

    def test_documento_sem_validade_nao_vence(self):
        documento = criar_documento(self.condominio, self.gestao)
        self.assertEqual(documento.situacao_em(HOJE), SituacaoVigencia.SEM_VALIDADE)

    def test_documento_com_validade_longe_esta_vigente(self):
        tipo = criar_tipo_documento(
            self.condominio, nome="Laudo", antecedencia_alerta_padrao_dias=30
        )
        documento = criar_documento(
            self.condominio,
            self.gestao,
            tipo_documento=tipo,
            data_validade=HOJE + timedelta(days=300),
        )
        self.assertEqual(documento.situacao_em(HOJE), SituacaoVigencia.VIGENTE)

    def test_documento_perto_da_validade_esta_vencendo(self):
        tipo = criar_tipo_documento(
            self.condominio, nome="Laudo", antecedencia_alerta_padrao_dias=30
        )
        documento = criar_documento(
            self.condominio,
            self.gestao,
            tipo_documento=tipo,
            data_validade=HOJE + timedelta(days=10),
        )
        self.assertEqual(documento.situacao_em(HOJE), SituacaoVigencia.VENCENDO)

    def test_documento_vencido_esta_vencido(self):
        documento = criar_documento(
            self.condominio,
            self.gestao,
            data_validade=HOJE - timedelta(days=1),
        )
        self.assertEqual(documento.situacao_em(HOJE), SituacaoVigencia.VENCIDO)

    def test_documento_desativado_esta_inativo(self):
        documento = criar_documento(self.condominio, self.gestao, ativo=False)
        self.assertEqual(documento.situacao_em(HOJE), SituacaoVigencia.INATIVO)
