"""As cores da identidade de um condomínio: validar, normalizar e derivar.

A cor principal é uma escolha da gestão; a secundária nasce dela, sem um segundo
cadastro para divergir. O que se verifica aqui é o contrato do módulo das cores
e o que o cadastro faz com ele — a cor fica guardada como ``#RRGGBB``, a cor
digitada sem o ``#`` é aceita, e o que não vira uma cor no formato é recusado.

Nada aqui toca tela, armazenamento ou desenho: só a decisão sobre a cor.
"""
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from apps.condominios import cores
from apps.condominios.models import Condominio


class CorValidaTests(SimpleTestCase):
    """O formato aceito, e só ele: ``#`` e seis dígitos hexadecimais."""

    def test_aceita_hexadecimal_com_o_jogo_da_antena(self):
        for valor in ("#1B2431", "#abcdef", "#000000", "#FFFFFF"):
            with self.subTest(valor=valor):
                self.assertTrue(cores.cor_valida(valor))

    def test_recusa_o_que_nao_e_uma_cor_no_formato(self):
        for valor in ("", None, " ", "1B2431", "#12345", "#1234567", "vermelho", "#GGGGGG"):
            with self.subTest(valor=valor):
                self.assertFalse(cores.cor_valida(valor))


class CorNormalizadaTests(SimpleTestCase):
    """A cor como fica guardada — ou o que veio, para a recusa poder citá-lo."""

    def test_vazio_continua_vazio(self):
        self.assertEqual(cores.cor_normalizada(""), "")
        self.assertEqual(cores.cor_normalizada(None), "")
        self.assertEqual(cores.cor_normalizada("   "), "")

    def test_sobe_as_letras_e_guarda_com_o_jogo_da_antena(self):
        self.assertEqual(cores.cor_normalizada("#1b2431"), "#1B2431")
        self.assertEqual(cores.cor_normalizada("  #1b2431  "), "#1B2431")

    def test_aceita_a_cor_digitada_sem_o_jogo_da_antena(self):
        self.assertEqual(cores.cor_normalizada("1b2431"), "#1B2431")

    def test_o_que_nao_e_cor_volta_sem_silencio(self):
        """Quem recusa é o cadastro; a recusa precisa citar o que foi dito."""
        self.assertEqual(cores.cor_normalizada("vermelho"), "vermelho")


class TomClaroTests(SimpleTestCase):
    """A derivada preserva o matiz: cada canal se aproxima do branco."""

    def test_preto_clareia_sem_mudar_de_matiz(self):
        self.assertEqual(cores.tom_claro("#000000"), "#595959")

    def test_branco_continua_branco(self):
        self.assertEqual(cores.tom_claro("#FFFFFF"), "#FFFFFF")

    def test_deriva_um_tom_mais_claro_do_mesmo_matiz(self):
        self.assertEqual(cores.tom_claro("#1B2431"), "#6B7179")

    def test_a_mistura_e_ajustavel(self):
        self.assertEqual(cores.tom_claro("#000000", mistura=0), "#000000")
        self.assertEqual(cores.tom_claro("#000000", mistura=1), "#FFFFFF")

    def test_sem_cor_valida_nao_ha_tom(self):
        self.assertEqual(cores.tom_claro(""), "")
        self.assertEqual(cores.tom_claro("vermelho"), "")


class CorSecundariaTests(SimpleTestCase):
    """A secundária é sempre derivada da principal — nunca um segundo cadastro."""

    def test_deriva_da_principal(self):
        self.assertEqual(cores.cor_secundaria("#1B2431"), "#6B7179")

    def test_sem_principal_valida_nao_ha_secundaria(self):
        self.assertEqual(cores.cor_secundaria(""), "")
        self.assertEqual(cores.cor_secundaria(None), "")
        self.assertEqual(cores.cor_secundaria("vermelho"), "")


class CorDoCondominioTests(TestCase):
    """O cadastro faz o que o módulo das cores define — nem mais, nem menos."""

    def test_sem_cor_por_padrao(self):
        condominio = Condominio.objects.create(nome="Aurora")
        self.assertEqual(condominio.cor_principal, "")
        self.assertFalse(condominio.tem_cor)
        self.assertEqual(condominio.cor_secundaria, "")

    def test_guarda_a_cor_normalizada(self):
        condominio = Condominio.objects.create(nome="Aurora", cor_principal="1b2431")
        condominio.refresh_from_db()
        self.assertEqual(condominio.cor_principal, "#1B2431")
        self.assertTrue(condominio.tem_cor)

    def test_a_secundaria_nasce_da_principal_guardada(self):
        condominio = Condominio.objects.create(nome="Aurora", cor_principal="#1B2431")
        self.assertEqual(condominio.cor_secundaria, "#6B7179")

    def test_a_cor_digitada_sem_o_jogo_da_antena_e_aceita(self):
        condominio = Condominio(nome="Aurora", cor_principal="1b2431")
        condominio.full_clean()  # não levanta

    def test_o_que_nao_e_cor_e_recusado_na_validacao(self):
        condominio = Condominio(nome="Aurora", cor_principal="vermelho")
        with self.assertRaises(ValidationError) as erro:
            condominio.full_clean()
        self.assertIn("cor_principal", erro.exception.message_dict)

    def test_sem_cor_a_validacao_passa(self):
        condominio = Condominio(nome="Aurora")
        condominio.full_clean()  # não levanta
