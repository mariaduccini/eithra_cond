"""Autorização dos cadastros: a regra é uma só, e vive aqui."""
from django.test import TestCase

from apps.cadastros.permissoes import (
    PAPEIS_QUE_ADMINISTRAM_ACESSOS,
    PAPEIS_QUE_CONFIGURAM_CONDOMINIO,
    PAPEIS_QUE_GERENCIAM,
    PAPEIS_QUE_VEEM,
    condominios_para_gerenciar,
    condominios_para_ver,
    pode_administrar_acessos,
    pode_configurar_condominio,
    pode_criar_condominios,
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


class ConfiguracaoEstruturalTests(TestCase):
    """A configuração estrutural do condomínio é só da Administradora.

    O síndico opera o condomínio: mantém locais, equipamentos, fornecedores e
    catálogos. O que ele não decide é a configuração estrutural — o cadastro do
    condomínio, a abertura de um novo e a administração dos acessos. A
    supervisão acompanha, sem configurar.
    """

    def setUp(self):
        self.condominio = criar_condominio("Alpha")

    def _com_papel(self, papel, email):
        pessoa = criar_pessoa(email, papel)
        criar_vinculo(pessoa, self.condominio, papel=papel)
        return pessoa

    def test_so_a_administradora_configura_e_administra(self):
        self.assertEqual(PAPEIS_QUE_CONFIGURAM_CONDOMINIO, frozenset({Papel.ADMINISTRADORA}))
        self.assertEqual(PAPEIS_QUE_ADMINISTRAM_ACESSOS, frozenset({Papel.ADMINISTRADORA}))

    def test_administradora_configura_o_condominio_e_os_acessos(self):
        pessoa = self._com_papel(Papel.ADMINISTRADORA, "admin@exemplo.test")
        self.assertTrue(pode_configurar_condominio(pessoa, self.condominio))
        self.assertTrue(pode_administrar_acessos(pessoa, self.condominio))
        self.assertTrue(pode_criar_condominios(pessoa))

    def test_sindica_nao_configura_nem_administra_acessos(self):
        pessoa = self._com_papel(Papel.SINDICA, "sindica@exemplo.test")
        self.assertFalse(pode_configurar_condominio(pessoa, self.condominio))
        self.assertFalse(pode_administrar_acessos(pessoa, self.condominio))
        self.assertFalse(pode_criar_condominios(pessoa))
        # A operação continua: o síndico mantém os cadastros operacionais.
        self.assertTrue(pode_gerenciar_cadastros(pessoa, self.condominio))

    def test_supervisora_nao_configura_nem_administra_acessos(self):
        pessoa = self._com_papel(Papel.SUPERVISORA, "supervisora@exemplo.test")
        self.assertFalse(pode_configurar_condominio(pessoa, self.condominio))
        self.assertFalse(pode_administrar_acessos(pessoa, self.condominio))
        self.assertFalse(pode_criar_condominios(pessoa))

    def test_configurar_sem_condominio_resolvido_nega(self):
        pessoa = self._com_papel(Papel.ADMINISTRADORA, "admin2@exemplo.test")
        self.assertFalse(pode_configurar_condominio(pessoa, None))
        self.assertFalse(pode_administrar_acessos(pessoa, None))

    def test_configurar_nao_alcanca_condominio_de_outro(self):
        pessoa = self._com_papel(Papel.ADMINISTRADORA, "admin3@exemplo.test")
        outro = criar_condominio("Beta")
        self.assertFalse(pode_configurar_condominio(pessoa, outro))
        self.assertFalse(pode_administrar_acessos(pessoa, outro))

    def test_vinculo_inativo_nao_configura(self):
        pessoa = criar_pessoa("inativa@exemplo.test", "Inativa")
        criar_vinculo(
            pessoa, self.condominio, papel=Papel.ADMINISTRADORA, ativo=False
        )
        self.assertFalse(pode_configurar_condominio(pessoa, self.condominio))
        self.assertFalse(pode_administrar_acessos(pessoa, self.condominio))
