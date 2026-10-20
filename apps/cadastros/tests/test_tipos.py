"""Catálogos de tipo: nome único no condomínio, ativo/inativo, vínculo e acesso.

Os tipos dão nome ao que se cadastra — local, equipamento e o que o fornecedor
atende. A tela abre ao lado da lista a que pertence, e quem responde pelo
condomínio mantém o catálogo; a supervisão lê e não reescreve.
"""
from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import (
    Equipamento,
    EspecialidadeFornecedor,
    Fornecedor,
    FornecedorEspecialidade,
    LocalOperacional,
    TipoEquipamento,
    TipoLocalOperacional,
)
from apps.cadastros.services import criar_fornecedor, criar_tipo_local
from apps.cadastros.tests.apoio import criar_condominio, entrar_como
from apps.condominios.constants import Papel


class TiposDeLocalTests(TestCase):
    """O vocabulário com que o condomínio classifica os seus lugares."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("cadastros:tipos_de_local")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)

    def test_gestao_cadastra_tipo_guardando_o_condominio(self):
        resposta = self.client.post(
            reverse("cadastros:criar_tipo_local"),
            {
                "condominio": str(self.condominio.pk),
                "nome": "Área comum",
                "descricao": "Uso coletivo",
            },
        )
        self.assertRedirects(
            resposta, f"{self.url}?condominio={self.condominio.pk}"
        )
        tipo = TipoLocalOperacional.objects.get(nome="Área comum")
        self.assertEqual(tipo.condominio, self.condominio)
        self.assertEqual(tipo.criado_por, self.gestao)

    def test_nome_repetido_ignorando_caixa_e_recusado_com_frase(self):
        TipoLocalOperacional.objects.create(
            condominio=self.condominio, nome="Garagem"
        )
        resposta = self.client.post(
            reverse("cadastros:criar_tipo_local"),
            {"condominio": str(self.condominio.pk), "nome": "garagem"},
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(TipoLocalOperacional.objects.count(), 1)

    def test_mesmo_nome_em_outro_condominio_e_permitido(self):
        TipoLocalOperacional.objects.create(
            condominio=self.condominio, nome="Garagem"
        )
        outro = criar_condominio("Beta")
        tipo = criar_tipo_local(
            condominio=outro, criado_por=None, dados={"nome": "Garagem"}
        )
        self.assertEqual(tipo.condominio, outro)

    def test_edita_tipo_existente(self):
        tipo = TipoLocalOperacional.objects.create(
            condominio=self.condominio, nome="Garagem"
        )
        resposta = self.client.post(
            reverse("cadastros:editar_tipo_local", args=[tipo.pk]),
            {
                "condominio": str(self.condominio.pk),
                "nome": "Garagem coberta",
            },
        )
        self.assertEqual(resposta.status_code, 302)
        tipo.refresh_from_db()
        self.assertEqual(tipo.nome, "Garagem coberta")

    def test_tipo_de_outro_condominio_e_404_ao_editar(self):
        alheio = TipoLocalOperacional.objects.create(
            condominio=criar_condominio("Beta"), nome="Garagem"
        )
        resposta = self.client.post(
            reverse("cadastros:editar_tipo_local", args=[alheio.pk]),
            {"condominio": str(self.condominio.pk), "nome": "Tentativa"},
        )
        self.assertEqual(resposta.status_code, 404)

    def test_alternar_tira_de_circulacao_sem_apagar(self):
        tipo = TipoLocalOperacional.objects.create(
            condominio=self.condominio, nome="Garagem"
        )
        resposta = self.client.post(
            reverse("cadastros:alternar_tipo_local", args=[tipo.pk]),
            {"condominio": str(self.condominio.pk)},
        )
        self.assertEqual(resposta.status_code, 302)
        tipo.refresh_from_db()
        self.assertFalse(tipo.ativo)
        self.assertTrue(TipoLocalOperacional.objects.filter(pk=tipo.pk).exists())

    def test_supervisora_le_o_catalogo_mas_nao_cadastra(self):
        entrar_como(
            self.client, Papel.SUPERVISORA, self.condominio, "supervisora@exemplo.test"
        )
        self.assertEqual(self.client.get(self.url).status_code, 200)
        resposta = self.client.post(
            reverse("cadastros:criar_tipo_local"),
            {"condominio": str(self.condominio.pk), "nome": "Garagem"},
        )
        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(TipoLocalOperacional.objects.exists())

    def test_operacional_recebe_404_na_tela_de_tipos(self):
        entrar_como(
            self.client, Papel.OPERACIONAL, self.condominio, "operacional@exemplo.test"
        )
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_local_aponta_para_o_tipo_do_condominio(self):
        tipo = TipoLocalOperacional.objects.create(
            condominio=self.condominio, nome="Garagem"
        )
        local = LocalOperacional.objects.create(
            condominio=self.condominio, nome="Garagem 1", tipo_local=tipo
        )
        self.assertEqual(local.tipo_local, tipo)
        self.assertIn(local, list(tipo.locais.all()))


class TiposDeEquipamentoTests(TestCase):
    """O vocabulário com que o condomínio classifica o que existe nos lugares."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("cadastros:tipos_de_equipamento")
        entrar_como(self.client, Papel.SINDICA, self.condominio)

    def test_gestao_cadastra_tipo_e_o_vincula_ao_equipamento(self):
        resposta = self.client.post(
            reverse("cadastros:criar_tipo_equipamento"),
            {"condominio": str(self.condominio.pk), "nome": "Bomba"},
        )
        self.assertRedirects(
            resposta, f"{self.url}?condominio={self.condominio.pk}"
        )
        tipo = TipoEquipamento.objects.get(nome="Bomba")
        equipamento = Equipamento.objects.create(
            condominio=self.condominio, nome="Bomba de recalque", tipo_equipamento=tipo
        )
        self.assertEqual(equipamento.tipo_equipamento, tipo)

    def test_nome_repetido_ignorando_caixa_e_recusado(self):
        TipoEquipamento.objects.create(condominio=self.condominio, nome="Bomba")
        resposta = self.client.post(
            reverse("cadastros:criar_tipo_equipamento"),
            {"condominio": str(self.condominio.pk), "nome": "bomba"},
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(TipoEquipamento.objects.count(), 1)


class EspecialidadesTests(TestCase):
    """O vocabulário do que um fornecedor atende neste condomínio."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("cadastros:especialidades")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)

    def test_gestao_cadastra_especialidade(self):
        resposta = self.client.post(
            reverse("cadastros:criar_especialidade"),
            {"condominio": str(self.condominio.pk), "nome": "Elétrica"},
        )
        self.assertRedirects(
            resposta, f"{self.url}?condominio={self.condominio.pk}"
        )
        especialidade = EspecialidadeFornecedor.objects.get(nome="Elétrica")
        self.assertEqual(especialidade.condominio, self.condominio)

    def test_nome_repetido_ignorando_caixa_e_recusado(self):
        EspecialidadeFornecedor.objects.create(
            condominio=self.condominio, nome="Elétrica"
        )
        resposta = self.client.post(
            reverse("cadastros:criar_especialidade"),
            {"condominio": str(self.condominio.pk), "nome": "elétrica"},
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(EspecialidadeFornecedor.objects.count(), 1)

    def test_fornecedor_guarda_o_que_atende(self):
        eletrica = EspecialidadeFornecedor.objects.create(
            condominio=self.condominio, nome="Elétrica"
        )
        hidraulica = EspecialidadeFornecedor.objects.create(
            condominio=self.condominio, nome="Hidráulica"
        )
        fornecedor = criar_fornecedor(
            condominio=self.condominio,
            criado_por=self.gestao,
            dados={"nome_razao_social": "Manutenção Silva"},
            especialidades=[eletrica, hidraulica],
        )
        self.assertEqual(
            set(fornecedor.especialidades.values_list("especialidade", flat=True)),
            {eletrica.pk, hidraulica.pk},
        )

    def test_editar_fornecedor_retira_o_vinculo_que_saiu(self):
        eletrica = EspecialidadeFornecedor.objects.create(
            condominio=self.condominio, nome="Elétrica"
        )
        hidraulica = EspecialidadeFornecedor.objects.create(
            condominio=self.condominio, nome="Hidráulica"
        )
        fornecedor = criar_fornecedor(
            condominio=self.condominio,
            criado_por=self.gestao,
            dados={"nome_razao_social": "Manutenção Silva"},
            especialidades=[eletrica, hidraulica],
        )
        resposta = self.client.post(
            reverse("cadastros:editar_fornecedor", args=[fornecedor.pk]),
            {
                "condominio": str(self.condominio.pk),
                "nome_razao_social": "Manutenção Silva",
                "tipo_pessoa": "pj",
                "especialidades": [str(eletrica.pk)],
            },
        )
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(
            set(
                FornecedorEspecialidade.objects.filter(
                    fornecedor=fornecedor
                ).values_list("especialidade", flat=True)
            ),
            {eletrica.pk},
        )
        # A especialidade que saiu do vínculo continua existindo no catálogo.
        self.assertTrue(EspecialidadeFornecedor.objects.filter(pk=hidraulica.pk).exists())

    def test_especialidade_de_outro_condominio_e_recusada_no_fornecedor(self):
        alheia = EspecialidadeFornecedor.objects.create(
            condominio=criar_condominio("Beta"), nome="Elétrica"
        )
        resposta = self.client.post(
            reverse("cadastros:criar_fornecedor"),
            {
                "condominio": str(self.condominio.pk),
                "nome_razao_social": "Manutenção Silva",
                "tipo_pessoa": "pj",
                "especialidades": [str(alheia.pk)],
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(Fornecedor.objects.exists())
