"""Disponibilização do fornecedor aos moradores.

Indicar e liberar são duas decisões, e não uma. A indicação diz que a gestão
confia no fornecedor; a liberação diz que ele pode ser mostrado agora. Só as
duas juntas — e com o fornecedor em uso — o fazem aparecer aos moradores. O que
o morador lê vive em campos próprios, e nunca é copiado dos campos internos.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import EspecialidadeFornecedor, Fornecedor
from apps.cadastros.services import (
    ErroDeCadastro,
    criar_fornecedor,
    editar_fornecedor,
)
from apps.cadastros.tests.apoio import (
    criar_condominio,
    criar_fornecedor as novo_fornecedor,
    criar_pessoa,
    entrar_como,
)
from apps.condominios.constants import Papel


class ElegibilidadeTests(TestCase):
    """Quem pode aparecer aos moradores, e quem não pode."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("sindica@exemplo.test", "Síndica")

    def _indicado(self, **extra):
        dados = {
            "nome_razao_social": "Elevadores Silva Ltda.",
            "indicado_para_moradores": True,
            "ativo_para_moradores": True,
        }
        dados.update(extra)
        return criar_fornecedor(
            condominio=self.condominio, criado_por=self.gestao, dados=dados
        )

    def test_fornecedor_comum_nao_aparece(self):
        fornecedor = novo_fornecedor(self.condominio)
        self.assertNotIn(fornecedor, Fornecedor.objects.indicados_para_moradores())

    def test_indicado_sem_liberacao_nao_aparece(self):
        fornecedor = novo_fornecedor(self.condominio, indicado_para_moradores=True)
        self.assertNotIn(fornecedor, Fornecedor.objects.indicados_para_moradores())

    def test_liberado_sem_indicacao_e_recusado_e_nao_elegivel(self):
        fornecedor = novo_fornecedor(self.condominio)
        with self.assertRaises(ErroDeCadastro):
            editar_fornecedor(
                fornecedor=fornecedor,
                dados={"ativo_para_moradores": True},
                responsavel=self.gestao,
            )
        # Mesmo forçado no banco, não é elegível: a leitura confere as três
        # condições, e não uma delas.
        Fornecedor.objects.filter(pk=fornecedor.pk).update(
            ativo_para_moradores=True
        )
        self.assertNotIn(
            fornecedor.pk,
            Fornecedor.objects.indicados_para_moradores().values_list(
                "pk", flat=True
            ),
        )

    def test_indicado_e_liberado_e_em_uso_aparece(self):
        fornecedor = self._indicado()
        self.assertIn(fornecedor, Fornecedor.objects.indicados_para_moradores())
        self.assertTrue(fornecedor.disponivel_para_moradores)

    def test_fornecedor_inativo_nao_aparece(self):
        fornecedor = self._indicado()
        fornecedor.ativo = False
        fornecedor.save()
        self.assertNotIn(fornecedor, Fornecedor.objects.indicados_para_moradores())
        self.assertFalse(fornecedor.disponivel_para_moradores)

    def test_fornecedor_de_outro_condominio_nao_aparece_neste(self):
        outro = criar_condominio("Beta")
        alheio = criar_fornecedor(
            condominio=outro,
            criado_por=self.gestao,
            dados={
                "nome_razao_social": "Elevadores Silva Ltda.",
                "indicado_para_moradores": True,
                "ativo_para_moradores": True,
            },
        )
        visiveis = (
            Fornecedor.objects.do_condominio(self.condominio).indicados_para_moradores()
        )
        self.assertNotIn(alheio, visiveis)


class ValidacaoHumanaTests(TestCase):
    """Liberar aos moradores registra quem respondeu por isso, e quando."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("sindica@exemplo.test", "Síndica")

    def test_liberar_no_cadastro_registra_quem_e_quando(self):
        fornecedor = criar_fornecedor(
            condominio=self.condominio,
            criado_por=self.gestao,
            dados={
                "nome_razao_social": "Elevadores Silva Ltda.",
                "indicado_para_moradores": True,
                "ativo_para_moradores": True,
            },
        )
        self.assertIsNotNone(fornecedor.validado_em)
        self.assertEqual(fornecedor.validado_por, self.gestao)

    def test_liberar_na_edicao_registra_quem_esta_na_tela(self):
        fornecedor = novo_fornecedor(self.condominio, indicado_para_moradores=True)
        self.assertIsNone(fornecedor.validado_em)
        outra = criar_pessoa("supervisora@exemplo.test", "Supervisora")
        editar_fornecedor(
            fornecedor=fornecedor,
            dados={"ativo_para_moradores": True},
            responsavel=outra,
        )
        fornecedor.refresh_from_db()
        self.assertIsNotNone(fornecedor.validado_em)
        self.assertEqual(fornecedor.validado_por, outra)

    def test_liberar_sem_responsavel_e_recusado(self):
        fornecedor = novo_fornecedor(self.condominio, indicado_para_moradores=True)
        with self.assertRaises(ErroDeCadastro):
            editar_fornecedor(
                fornecedor=fornecedor,
                dados={"ativo_para_moradores": True},
                responsavel=None,
            )
        fornecedor.refresh_from_db()
        self.assertFalse(fornecedor.ativo_para_moradores)

    def test_edicao_posterior_nao_reinicia_a_liberacao(self):
        fornecedor = criar_fornecedor(
            condominio=self.condominio,
            criado_por=self.gestao,
            dados={
                "nome_razao_social": "Elevadores Silva Ltda.",
                "indicado_para_moradores": True,
                "ativo_para_moradores": True,
            },
        )
        anterior = fornecedor.validado_em
        editar_fornecedor(
            fornecedor=fornecedor,
            dados={"descricao_publica": "Atende de segunda a sexta."},
            responsavel=self.gestao,
        )
        fornecedor.refresh_from_db()
        self.assertEqual(fornecedor.validado_em, anterior)


class SeparacaoDeDadosTests(TestCase):
    """O que o morador lê não é o que a portaria usa."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("sindica@exemplo.test", "Síndica")

    def test_contato_interno_nao_vira_contato_publico(self):
        fornecedor = criar_fornecedor(
            condominio=self.condominio,
            criado_por=self.gestao,
            dados={
                "nome_razao_social": "Elevadores Silva Ltda.",
                "telefone": "(71) 99999-8888",
                "email": "contato@silva.test",
                "contato": "Sr. João",
                "observacao": "Prefere contato por telefone.",
                "indicado_para_moradores": True,
                "ativo_para_moradores": True,
            },
        )
        # Nada do contato interno atravessa para os campos dos moradores.
        self.assertEqual(fornecedor.telefone_publico, "")
        self.assertEqual(fornecedor.whatsapp_publico, "")
        self.assertEqual(fornecedor.site_publico, "")
        self.assertEqual(fornecedor.descricao_publica, "")
        self.assertEqual(fornecedor.observacao_publica, "")
        # O que a portaria usa continua guardado — só não é mostrado.
        self.assertEqual(fornecedor.telefone, "71999998888")
        self.assertEqual(fornecedor.email, "contato@silva.test")


class EspecialidadesReutilizadasTests(TestCase):
    """Indicar não cria um segundo vocabulário: usa o do fornecedor."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("sindica@exemplo.test", "Síndica")

    def test_indicacao_reutiliza_a_especialidade_do_cadastro(self):
        eletrica = EspecialidadeFornecedor.objects.create(
            condominio=self.condominio, nome="Elétrica"
        )
        fornecedor = criar_fornecedor(
            condominio=self.condominio,
            criado_por=self.gestao,
            dados={
                "nome_razao_social": "Manutenção Silva",
                "indicado_para_moradores": True,
                "ativo_para_moradores": True,
            },
            especialidades=[eletrica],
        )
        self.assertIn(
            eletrica.pk,
            set(fornecedor.especialidades.values_list("especialidade", flat=True)),
        )
        self.assertIn(fornecedor, Fornecedor.objects.indicados_para_moradores())


class RevalidacaoTests(TestCase):
    """A indicação envelhece: a janela diz quando rever, sem guardar estado."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("sindica@exemplo.test", "Síndica")
        self.fornecedor = criar_fornecedor(
            condominio=self.condominio,
            criado_por=self.gestao,
            dados={
                "nome_razao_social": "Elevadores Silva Ltda.",
                "indicado_para_moradores": True,
                "ativo_para_moradores": True,
            },
        )

    def _na_janela(self, janela_dias):
        return (
            Fornecedor.objects.indicacao_sem_revalidacao(janela_dias=janela_dias)
            .filter(pk=self.fornecedor.pk)
            .exists()
        )

    def test_indicacao_recente_esta_em_dia(self):
        self.assertFalse(self._na_janela(365))

    def test_indicacao_antiga_pede_revalidacao(self):
        Fornecedor.objects.filter(pk=self.fornecedor.pk).update(
            validado_em=timezone.now() - timedelta(days=400)
        )
        self.assertTrue(self._na_janela(365))

    def test_indicacao_sem_validacao_registrada_pede_revalidacao(self):
        Fornecedor.objects.filter(pk=self.fornecedor.pk).update(validado_em=None)
        self.assertTrue(self._na_janela(365))

    def test_fornecedor_nao_elegivel_nao_entra_na_revalidacao(self):
        Fornecedor.objects.filter(pk=self.fornecedor.pk).update(
            ativo=False, validado_em=timezone.now() - timedelta(days=400)
        )
        self.assertFalse(self._na_janela(365))


class PermissoesTests(TestCase):
    """Quem responde pelo condomínio libera; a supervisão só acompanha."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("cadastros:fornecedores")

    def test_sindica_libera_pela_tela_e_fica_registrado(self):
        pessoa = entrar_como(self.client, Papel.SINDICA, self.condominio)
        resposta = self.client.post(
            reverse("cadastros:criar_fornecedor"),
            {
                "condominio": str(self.condominio.pk),
                "nome_razao_social": "Elevadores Silva Ltda.",
                "tipo_pessoa": "pj",
                "indicado_para_moradores": "on",
                "ativo_para_moradores": "on",
            },
        )
        self.assertRedirects(resposta, f"{self.url}?condominio={self.condominio.pk}")
        fornecedor = Fornecedor.objects.get(nome_razao_social="Elevadores Silva Ltda.")
        self.assertTrue(fornecedor.disponivel_para_moradores)
        self.assertEqual(fornecedor.validado_por, pessoa)

    def test_sindica_libera_pela_edicao_e_fica_registrado(self):
        """Liberar pela edição também registra quem respondeu.

        A tela valida por um ModelForm, que escreve os dados limpos na própria
        instância antes de o domínio ser chamado. O "antes" precisa vir do
        banco: lido do objeto em memória, a passagem para "disponível" passaria
        despercebida e a liberação ficaria sem autor.
        """
        pessoa = entrar_como(self.client, Papel.SINDICA, self.condominio)
        criar_fornecedor(
            condominio=self.condominio,
            criado_por=pessoa,
            dados={"nome_razao_social": "Elevadores Silva Ltda."},
        )
        fornecedor = Fornecedor.objects.get(nome_razao_social="Elevadores Silva Ltda.")
        self.assertFalse(fornecedor.ativo_para_moradores)

        resposta = self.client.post(
            reverse("cadastros:editar_fornecedor", args=[fornecedor.pk]),
            {
                "condominio": str(self.condominio.pk),
                "nome_razao_social": "Elevadores Silva Ltda.",
                "tipo_pessoa": "pj",
                "indicado_para_moradores": "on",
                "ativo_para_moradores": "on",
            },
        )
        self.assertRedirects(resposta, f"{self.url}?condominio={self.condominio.pk}")
        fornecedor.refresh_from_db()
        self.assertTrue(fornecedor.disponivel_para_moradores)
        self.assertEqual(fornecedor.validado_por, pessoa)
        self.assertIsNotNone(fornecedor.validado_em)

    def test_supervisora_ve_a_lista_mas_nao_libera(self):
        entrar_como(self.client, Papel.SUPERVISORA, self.condominio)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        resposta = self.client.post(
            reverse("cadastros:criar_fornecedor"),
            {
                "condominio": str(self.condominio.pk),
                "nome_razao_social": "Elevadores Silva Ltda.",
                "indicado_para_moradores": "on",
                "ativo_para_moradores": "on",
            },
        )
        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(Fornecedor.objects.exists())

    def test_operacional_nao_entra_na_tela(self):
        entrar_como(self.client, Papel.OPERACIONAL, self.condominio)
        self.assertEqual(self.client.get(self.url).status_code, 404)
