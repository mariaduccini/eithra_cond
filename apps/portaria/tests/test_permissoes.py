from django.test import TestCase

from apps.condominios.constants import Papel
from apps.portaria.permissoes import (
    pode_operar_portaria,
    pode_ver_portaria_em_algum,
)
from apps.portaria.tests.apoio import criar_condominio, criar_pessoa, criar_vinculo


class PermissoesPortariaTests(TestCase):
    def test_porteiro_pode_ver_e_operar(self):
        usuario = criar_pessoa("porteiro@teste.com", "Porteiro")
        condominio = criar_condominio()
        criar_vinculo(usuario, condominio, papel=Papel.PORTEIRO)
        self.assertTrue(pode_ver_portaria_em_algum(usuario))
        self.assertTrue(pode_operar_portaria(usuario, condominio))

    def test_morador_nao_pode_ver(self):
        usuario = criar_pessoa("morador@teste.com", "Morador")
        condominio = criar_condominio()
        criar_vinculo(usuario, condominio, papel=Papel.MORADOR)
        self.assertFalse(pode_ver_portaria_em_algum(usuario))
        self.assertFalse(pode_operar_portaria(usuario, condominio))

