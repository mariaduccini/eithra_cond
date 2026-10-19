"""Autorização dos cadastros: a regra é uma só, e vive aqui."""
from django.test import TestCase

from apps.cadastros.permissoes import (
    PAPEIS_QUE_GERENCIAM,
    PAPEIS_QUE_VEEM,
    condominios_para_gerenciar,
    condominios_para_ver,
    pode_gerenciar_cadastros,
    pode_ver_cadastros,
    pode_ver_cadastros_em_algum,
)
from apps.cadastros.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)
from apps.condominios.constants import Papel


class VisibilidadeTests(TestCase):
    """Quem enxerga o módulo e em qual condomínio."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")

    def _com_papel(self, papel, email):
        pessoa = criar_pessoa(email, papel)
        criar_vinculo(pessoa, self.condominio, papel=papel)
        return pessoa

    def test_admin_sindica_supervisora_veem(self):
        for indice, papel in enumerate(sorted(PAPEIS_QUE_VEEM)):
            pessoa = self._com_papel(papel, f"{indice}@exemplo.test")
            self.assertTrue(condominios_para_ver(pessoa).exists(), papel)

    def test_operacional_porteiro_e_morador_nao_veem(self):
        for indice, papel in enumerate(
            (Papel.OPERACIONAL, Papel.PORTEIRO, Papel.MORADOR)
        ):
            pessoa = self._com_papel(papel, f"fora{indice}@exemplo.test")
            self.assertFalse(condominios_para_ver(pessoa).exists(), papel)
            self.assertFalse(pode_ver_cadastros_em_algum(pessoa), papel)

    def test_vinculo_inativo_nao_ve(self):
        pessoa = criar_pessoa("inativo@exemplo.test", "Inativo")
        criar_vinculo(pessoa, self.condominio, papel=Papel.SINDICA, ativo=False)
        self.assertFalse(condominios_para_ver(pessoa).exists())

    def test_sem_vinculo_nao_ve(self):
        pessoa = criar_pessoa("semvinculo@exemplo.test", "Sem vínculo")
        self.assertFalse(pode_ver_cadastros_em_algum(pessoa))

    def test_pode_ver_respeita_o_condominio(self):
        pessoa = self._com_papel(Papel.SINDICA, "sindica@exemplo.test")
        outro = criar_condominio("Beta")
        self.assertTrue(pode_ver_cadastros(pessoa, self.condominio))
        self.assertFalse(pode_ver_cadastros(pessoa, outro))

    def test_pode_ver_sem_condominio_resolvido_nega(self):
        pessoa = self._com_papel(Papel.SINDICA, "sindica2@exemplo.test")
        self.assertFalse(pode_ver_cadastros(pessoa, None))


class GestaoTests(TestCase):
    """Quem responde pelo condomínio mantém o cadastro."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")

    def _com_papel(self, papel, email):
        pessoa = criar_pessoa(email, papel)
        criar_vinculo(pessoa, self.condominio, papel=papel)
        return pessoa

    def test_admin_e_sindica_gerenciam(self):
        for indice, papel in enumerate(sorted(PAPEIS_QUE_GERENCIAM)):
            pessoa = self._com_papel(papel, f"gestao{indice}@exemplo.test")
            self.assertTrue(condominios_para_gerenciar(pessoa).exists(), papel)
            self.assertTrue(pode_gerenciar_cadastros(pessoa, self.condominio), papel)

    def test_supervisora_ve_mas_nao_gerencia(self):
        pessoa = self._com_papel(Papel.SUPERVISORA, "supervisora@exemplo.test")
        self.assertTrue(pode_ver_cadastros(pessoa, self.condominio))
        self.assertFalse(pode_gerenciar_cadastros(pessoa, self.condominio))

    def test_gerenciar_sem_condominio_resolvido_nega(self):
        pessoa = self._com_papel(Papel.SINDICA, "sindica@exemplo.test")
        self.assertFalse(pode_gerenciar_cadastros(pessoa, None))

    def test_gerenciar_nao_alcanca_condominio_de_outro(self):
        pessoa = self._com_papel(Papel.SINDICA, "sindica3@exemplo.test")
        outro = criar_condominio("Beta")
        self.assertFalse(pode_gerenciar_cadastros(pessoa, outro))
