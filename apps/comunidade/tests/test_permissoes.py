"""Quem enxerga e quem mantém o cadastro de comunidade.

A resposta sai sempre do vínculo ativo da pessoa com o condomínio. Quem não
responde por ele — operação, portaria, morador — não alcança o módulo, e a
ausência de vínculo ativo nega o acesso em vez de concedê-lo.
"""
from django.test import TestCase

from apps.cadastros.tests.apoio import criar_condominio, criar_pessoa, criar_vinculo
from apps.comunidade import permissoes
from apps.comunidade.tests import apoio
from apps.condominios.constants import Papel

PAPEIS_QUE_ACESSAM = (Papel.ADMINISTRADORA, Papel.SINDICA, Papel.SUPERVISORA)
PAPEIS_QUE_NAO_ACESSAM = (Papel.OPERACIONAL, Papel.PORTEIRO, Papel.MORADOR)


class AcessoAoModuloTests(TestCase):
    """A pergunta da porta do módulo, papel por papel."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")

    def _pessoa_com(self, papel, **campos):
        pessoa = criar_pessoa(f"{papel}@exemplo.test", papel)
        criar_vinculo(pessoa, self.condominio, papel=papel, **campos)
        return pessoa

    def test_administracao_e_sindica_e_supervisao_acessam(self):
        for papel in PAPEIS_QUE_ACESSAM:
            with self.subTest(papel=papel):
                pessoa = self._pessoa_com(papel)
                self.assertTrue(permissoes.pode_ver_comunidade(pessoa, self.condominio))
                self.assertTrue(permissoes.pode_operar(pessoa, self.condominio))

    def test_operacao_portaria_e_morador_nao_acessam(self):
        for papel in PAPEIS_QUE_NAO_ACESSAM:
            with self.subTest(papel=papel):
                pessoa = self._pessoa_com(papel)
                self.assertFalse(
                    permissoes.pode_ver_comunidade(pessoa, self.condominio)
                )
                self.assertFalse(permissoes.pode_operar(pessoa, self.condominio))

    def test_vinculo_inativo_nao_concede_acesso(self):
        pessoa = self._pessoa_com(Papel.SINDICA, ativo=False)
        self.assertFalse(permissoes.pode_ver_comunidade(pessoa, self.condominio))

    def test_sem_vinculo_nao_concede_acesso(self):
        pessoa = criar_pessoa("sem-vinculo@exemplo.test", "Sem Vínculo")
        self.assertFalse(permissoes.pode_ver_comunidade(pessoa, self.condominio))
        self.assertFalse(permissoes.pode_operar(pessoa, self.condominio))

    def test_vinculo_em_outro_condominio_nao_concede_acesso_aqui(self):
        outro = criar_condominio("Beta")
        pessoa = criar_pessoa("outro@exemplo.test", "Outro")
        criar_vinculo(pessoa, outro, papel=Papel.SINDICA)
        self.assertFalse(permissoes.pode_ver_comunidade(pessoa, self.condominio))

    def test_condominio_nao_resolvido_nega_o_acesso(self):
        pessoa = self._pessoa_com(Papel.SINDICA)
        self.assertFalse(permissoes.pode_ver_comunidade(pessoa, None))
        self.assertFalse(permissoes.pode_operar(pessoa, None))

    def test_a_pessoa_que_acessa_em_algum_lugar_enxerga_o_menu(self):
        self.assertTrue(permissoes.pode_ver_comunidade_em_algum(self._pessoa_com(Papel.ADMINISTRADORA)))

    def test_a_pessoa_sem_papel_que_acessa_nao_enxerga_o_menu(self):
        pessoa = self._pessoa_com(Papel.PORTEIRO)
        self.assertFalse(permissoes.pode_ver_comunidade_em_algum(pessoa))


class AcessoPorEntidadeTests(TestCase):
    """Os pares por entidade respondem o mesmo que o condomínio do registro."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.sindica = criar_pessoa("sindica@exemplo.test", "Síndica")
        criar_vinculo(self.sindica, self.condominio, papel=Papel.SINDICA)
        self.unidade = apoio.criar_unidade(self.condominio)
        self.pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        self.veiculo = apoio.criar_veiculo(self.condominio)

    def test_quem_acessa_o_condominio_acessa_o_registro(self):
        self.assertTrue(permissoes.pode_ver_unidade(self.sindica, self.unidade))
        self.assertTrue(permissoes.pode_ver_pessoa(self.sindica, self.pessoa))
        self.assertTrue(permissoes.pode_ver_veiculo(self.sindica, self.veiculo))
        self.assertTrue(permissoes.pode_operar_unidade(self.sindica, self.unidade))
        self.assertTrue(permissoes.pode_operar_pessoa(self.sindica, self.pessoa))
        self.assertTrue(permissoes.pode_operar_veiculo(self.sindica, self.veiculo))

    def test_registro_de_outro_condominio_nao_e_alcancado(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.assertFalse(permissoes.pode_ver_unidade(morador, self.unidade))
        self.assertFalse(permissoes.pode_ver_pessoa(morador, self.pessoa))
        self.assertFalse(permissoes.pode_ver_veiculo(morador, self.veiculo))

    def test_registro_nao_resolvido_nega_o_acesso(self):
        self.assertFalse(permissoes.pode_ver_unidade(self.sindica, None))
        self.assertFalse(permissoes.pode_ver_pessoa(self.sindica, None))
        self.assertFalse(permissoes.pode_ver_veiculo(self.sindica, None))
