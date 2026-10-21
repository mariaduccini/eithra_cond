"""O que os cadastros de comunidade recusam e o que eles derivam.

Estas perguntas são do domínio, não da tela: uma unidade repetida, um documento
com dígitos a menos, uma placa impossível. E, do outro lado, o que nunca se
guarda — quem está ligado à unidade hoje sai dos vínculos vigentes, na hora.
"""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.cadastros.constants import TipoPessoa
from apps.cadastros.tests.apoio import criar_condominio
from apps.comunidade.constants import TipoVinculoPessoaUnidade
from apps.comunidade.models import (
    Pessoa,
    Unidade,
    Veiculo,
    VinculoPessoaUnidade,
    VinculoVeiculoPessoa,
    VinculoVeiculoUnidade,
)
from apps.comunidade.tests import apoio


class UnidadeTests(TestCase):
    """A unidade e a identificação que o condomínio usa no dia a dia."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")

    def test_identificacao_repetida_no_mesmo_condominio_e_recusada(self):
        apoio.criar_unidade(self.condominio, "101")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                apoio.criar_unidade(self.condominio, "101")

    def test_identificacao_igual_mudando_a_caixa_e_a_mesma_unidade(self):
        apoio.criar_unidade(self.condominio, "A-101")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                apoio.criar_unidade(self.condominio, "a-101")

    def test_a_mesma_identificacao_vale_em_outro_condominio(self):
        apoio.criar_unidade(self.condominio, "101")
        outro = apoio.criar_unidade(criar_condominio("Beta"), "101")
        self.assertEqual(outro.identificador, "101")

    def test_rotulo_junta_bloco_e_identificacao(self):
        unidade = apoio.criar_unidade(self.condominio, "101", bloco_torre="Bloco A")
        self.assertEqual(unidade.rotulo, "Bloco A · 101")

    def test_rotulo_e_so_a_identificacao_sem_bloco(self):
        unidade = apoio.criar_unidade(self.condominio, "101")
        self.assertEqual(unidade.rotulo, "101")

    def test_pessoas_atuais_vem_dos_vinculos_vigentes(self):
        unidade = apoio.criar_unidade(self.condominio)
        moradora = apoio.criar_pessoa_da_comunidade(self.condominio, "Moradora Exemplo")
        apoia = apoio.criar_pessoa_da_comunidade(self.condominio, "Saiu Exemplo")
        apoio.criar_vinculo_pessoa_unidade(self.condominio, moradora, unidade)
        apoio.criar_vinculo_pessoa_unidade(
            self.condominio, apoia, unidade, data_fim=apoio.ontem()
        )
        self.assertEqual(list(unidade.pessoas_atuais()), [moradora])

    def test_unidade_nao_guarda_quem_mora_nela(self):
        campos = {campo.name for campo in Unidade._meta.get_fields()}
        self.assertNotIn("morador", campos)
        self.assertNotIn("ocupada", campos)


class PessoaTests(TestCase):
    """O cadastro da pessoa: o que é opcional e o que precisa fazer sentido."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")

    def test_nome_e_obrigatorio(self):
        pessoa = Pessoa(condominio=self.condominio, nome="   ")
        with self.assertRaises(ValidationError):
            pessoa.full_clean()

    def test_documento_de_pessoa_fisica_com_digitos_a_menos_e_recusado(self):
        pessoa = Pessoa(
            condominio=self.condominio,
            nome="Pessoa Exemplo 01",
            tipo_pessoa=TipoPessoa.PF,
            documento="123",
        )
        with self.assertRaises(ValidationError):
            pessoa.full_clean()

    def test_documento_de_pessoa_juridica_com_catorze_digitos_e_aceito(self):
        pessoa = Pessoa(
            condominio=self.condominio,
            nome="Empresa Exemplo Ltda.",
            tipo_pessoa=TipoPessoa.PJ,
            documento="12345678000195",
        )
        pessoa.full_clean()

    def test_documento_e_guardado_so_com_digitos(self):
        pessoa = apoio.criar_pessoa_da_comunidade(
            self.condominio, documento="123.456.789-01"
        )
        self.assertEqual(pessoa.documento, "12345678901")
        self.assertEqual(pessoa.documento_formatado, "123.456.789-01")

    def test_contato_e_guardado_normalizado(self):
        pessoa = apoio.criar_pessoa_da_comunidade(
            self.condominio,
            telefone="(71) 99999-0000",
            whatsapp="71 98888 1111",
            email="  Exemplo@Teste.COM ",
        )
        self.assertEqual(pessoa.telefone, "71999990000")
        self.assertEqual(pessoa.whatsapp, "71988881111")
        self.assertEqual(pessoa.email, "exemplo@teste.com")

    def test_busca_acha_por_nome_telefone_e_email(self):
        pessoa = apoio.criar_pessoa_da_comunidade(
            self.condominio,
            "Pessoa Exemplo 01",
            telefone="71999990000",
            email="exemplo@teste.com",
        )
        for termo in ("Exemplo", "71999990000", "exemplo@teste.com"):
            with self.subTest(termo=termo):
                self.assertIn(pessoa, Pessoa.objects.buscar(termo))

    def test_busca_acha_por_whatsapp_com_separadores(self):
        pessoa = apoio.criar_pessoa_da_comunidade(
            self.condominio, "Pessoa Exemplo 01", whatsapp="71988887777"
        )
        self.assertIn(pessoa, Pessoa.objects.buscar("(71) 98888-7777"))

    def test_duplicidade_avisa_quem_compartilha_o_telefone(self):
        primeira = apoio.criar_pessoa_da_comunidade(
            self.condominio, "Pessoa Exemplo 01", telefone="71999990000"
        )
        candidatos = Pessoa.objects.possiveis_duplicidades(telefone="(71) 99999-0000")
        self.assertIn(primeira, candidatos)

    def test_duplicidade_nao_aponta_o_proprio_cadastro(self):
        pessoa = apoio.criar_pessoa_da_comunidade(
            self.condominio, "Pessoa Exemplo 01", telefone="71999990000"
        )
        candidatos = Pessoa.objects.possiveis_duplicidades(
            telefone="71999990000", excluir_pk=pessoa.pk
        )
        self.assertNotIn(pessoa, candidatos)

    def test_sem_contato_nao_ha_duplicidade_a_avisar(self):
        apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa Exemplo 01")
        self.assertFalse(Pessoa.objects.possiveis_duplicidades().exists())

    def test_unidades_atuais_vem_dos_vinculos_vigentes(self):
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa Exemplo 01")
        atual = apoio.criar_unidade(self.condominio, "101")
        antiga = apoio.criar_unidade(self.condominio, "202")
        apoio.criar_vinculo_pessoa_unidade(self.condominio, pessoa, atual)
        apoio.criar_vinculo_pessoa_unidade(
            self.condominio, pessoa, antiga, data_fim=apoio.ontem()
        )
        self.assertEqual(list(pessoa.unidades_atuais()), [atual])


class VeiculoTests(TestCase):
    """A placa é a identidade do veículo dentro do condomínio."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")

    def test_placa_em_branco_e_recusada(self):
        veiculo = Veiculo(condominio=self.condominio, placa="   ")
        with self.assertRaises(ValidationError):
            veiculo.full_clean()

    def test_placa_fora_dos_padroes_e_recusada(self):
        veiculo = Veiculo(condominio=self.condominio, placa="AB123")
        with self.assertRaises(ValidationError):
            veiculo.full_clean()

    def test_padrao_antigo_e_do_mercosul_sao_aceitos(self):
        for placa in ("ABC1234", "ABC1D23"):
            with self.subTest(placa=placa):
                veiculo = Veiculo(condominio=self.condominio, placa=placa)
                veiculo.full_clean()

    def test_placa_e_guardada_em_maiusculas_e_sem_separadores(self):
        veiculo = apoio.criar_veiculo(self.condominio, placa="abc-1d23")
        self.assertEqual(veiculo.placa, "ABC1D23")

    def test_placa_repetida_no_mesmo_condominio_e_recusada(self):
        apoio.criar_veiculo(self.condominio, "ABC1D23")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                apoio.criar_veiculo(self.condominio, "abc1d23")

    def test_a_mesma_placa_vale_em_outro_condominio(self):
        apoio.criar_veiculo(self.condominio, "ABC1D23")
        outro = apoio.criar_veiculo(criar_condominio("Beta"), "ABC1D23")
        self.assertEqual(outro.placa, "ABC1D23")

    def test_pessoas_atuais_vem_dos_vinculos_vigentes(self):
        veiculo = apoio.criar_veiculo(self.condominio)
        usa = apoio.criar_pessoa_da_comunidade(self.condominio, "Usa Exemplo")
        usou = apoio.criar_pessoa_da_comunidade(self.condominio, "Usou Exemplo")
        apoio.criar_vinculo_veiculo_pessoa(self.condominio, veiculo, usa)
        apoio.criar_vinculo_veiculo_pessoa(
            self.condominio, veiculo, usou, data_fim=apoio.ontem()
        )
        self.assertEqual(list(veiculo.pessoas_atuais()), [usa])

    def test_unidades_atuais_vem_dos_vinculos_vigentes(self):
        veiculo = apoio.criar_veiculo(self.condominio)
        unidade = apoio.criar_unidade(self.condominio, "101")
        apoia = apoio.criar_unidade(self.condominio, "202")
        apoio.criar_vinculo_veiculo_unidade(self.condominio, veiculo, unidade)
        apoio.criar_vinculo_veiculo_unidade(
            self.condominio, veiculo, apoia, data_fim=apoio.ontem()
        )
        self.assertEqual(list(veiculo.unidades_atuais()), [unidade])


class VinculoPessoaUnidadeTests(TestCase):
    """O vínculo entre pessoa e unidade: datas e coerência entre condomínios."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.pessoa = apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa Exemplo 01")
        self.unidade = apoio.criar_unidade(self.condominio)

    def _vinculo(self, **campos):
        padrao = {
            "condominio": self.condominio,
            "pessoa": self.pessoa,
            "unidade": self.unidade,
            "data_inicio": apoio.HOJE,
        }
        return VinculoPessoaUnidade(**{**padrao, **campos})

    def test_vinculo_entre_condominios_diferentes_e_recusado(self):
        alheia = apoio.criar_unidade(criar_condominio("Beta"), "101")
        vinculo = self._vinculo(unidade=alheia)
        with self.assertRaises(ValidationError):
            vinculo.full_clean()

    def test_fim_antes_do_inicio_e_recusado(self):
        vinculo = self._vinculo(data_fim=apoio.ontem())
        with self.assertRaises(ValidationError):
            vinculo.full_clean()

    def test_vinculo_sem_fim_esta_vigente(self):
        vinculo = apoio.criar_vinculo_pessoa_unidade(
            self.condominio, self.pessoa, self.unidade
        )
        self.assertTrue(vinculo.vigente)
        self.assertFalse(vinculo.encerrado)

    def test_vinculo_com_fim_passado_esta_encerrado(self):
        vinculo = apoio.criar_vinculo_pessoa_unidade(
            self.condominio, self.pessoa, self.unidade, data_fim=apoio.ontem()
        )
        self.assertTrue(vinculo.encerrado)
        self.assertFalse(vinculo.vigente)

    def test_vinculo_que_comeca_amanha_ainda_nao_vale(self):
        inicio = timezone.localdate() + timedelta(days=30)
        vinculo = apoio.criar_vinculo_pessoa_unidade(
            self.condominio, self.pessoa, self.unidade, data_inicio=inicio
        )
        self.assertFalse(vinculo.vigente)

    def test_registrar_o_mesmo_vinculo_duas_vezes_e_recusado(self):
        apoio.criar_vinculo_pessoa_unidade(
            self.condominio, self.pessoa, self.unidade, data_inicio=apoio.HOJE
        )
        with self.assertRaises(ValidationError):
            self._vinculo().full_clean()

    def test_o_mesmo_dia_com_papeis_diferentes_e_outro_vinculo(self):
        apoio.criar_vinculo_pessoa_unidade(self.condominio, self.pessoa, self.unidade)
        outro = apoio.criar_vinculo_pessoa_unidade(
            self.condominio,
            self.pessoa,
            self.unidade,
            tipo_vinculo=TipoVinculoPessoaUnidade.PROPRIETARIO,
        )
        self.assertEqual(outro.tipo_vinculo, TipoVinculoPessoaUnidade.PROPRIETARIO)

    def test_rotulo_origem_e_opcional(self):
        vinculo = apoio.criar_vinculo_pessoa_unidade(
            self.condominio, self.pessoa, self.unidade
        )
        self.assertEqual(vinculo.rotulo_origem, "")

    def test_rotulo_origem_e_guardado_normalizado(self):
        vinculo = apoio.criar_vinculo_pessoa_unidade(
            self.condominio,
            self.pessoa,
            self.unidade,
            rotulo_origem="  ESPOSA   ",
        )
        self.assertEqual(vinculo.rotulo_origem, "ESPOSA")

    def test_uma_unidade_admite_varios_vinculos_ao_mesmo_tempo(self):
        outra = apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa Exemplo 02")
        apoio.criar_vinculo_pessoa_unidade(self.condominio, self.pessoa, self.unidade)
        apoio.criar_vinculo_pessoa_unidade(self.condominio, outra, self.unidade)
        self.assertEqual(
            VinculoPessoaUnidade.objects.da_unidade(self.unidade).vigentes().count(), 2
        )


class VinculosDoVeiculoTests(TestCase):
    """As duas perguntas sobre um veículo não se respondem uma pela outra."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.veiculo = apoio.criar_veiculo(self.condominio)
        self.pessoa = apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa Exemplo 01")
        self.unidade = apoio.criar_unidade(self.condominio)

    def test_vinculo_com_pessoa_de_outro_condominio_e_recusado(self):
        alheia = apoio.criar_pessoa_da_comunidade(criar_condominio("Beta"), "Pessoa Exemplo 02")
        vinculo = VinculoVeiculoPessoa(
            condominio=self.condominio,
            veiculo=self.veiculo,
            pessoa=alheia,
            data_inicio=apoio.HOJE,
        )
        with self.assertRaises(ValidationError):
            vinculo.full_clean()

    def test_vinculo_com_unidade_de_outro_condominio_e_recusado(self):
        alheia = apoio.criar_unidade(criar_condominio("Beta"), "101")
        vinculo = VinculoVeiculoUnidade(
            condominio=self.condominio,
            veiculo=self.veiculo,
            unidade=alheia,
            data_inicio=apoio.HOJE,
        )
        with self.assertRaises(ValidationError):
            vinculo.full_clean()

    def test_quem_usa_o_veiculo_nao_diz_a_que_unidade_ele_se_relaciona(self):
        apoio.criar_vinculo_veiculo_pessoa(self.condominio, self.veiculo, self.pessoa)
        self.assertEqual(VinculoVeiculoUnidade.objects.count(), 0)

    def test_vinculo_encerrado_sai_do_estado_atual_e_fica_no_historico(self):
        vinculo = apoio.criar_vinculo_veiculo_unidade(
            self.condominio, self.veiculo, self.unidade, data_fim=apoio.ontem()
        )
        self.assertTrue(vinculo.encerrado)
        self.assertFalse(VinculoVeiculoUnidade.objects.vigentes().exists())
        self.assertTrue(VinculoVeiculoUnidade.objects.encerrados().exists())
