"""O administrador do Eithra enxerga o sistema inteiro — sem virar papel de condomínio.

``is_superuser`` é a administração do sistema: global, do Django, e não um
``Papel``. Quem a exerce responde pelo Eithra por inteiro e, por isso, enxerga
todos os condomínios ativos e todos os módulos, mesmo sem nenhum vínculo
(``AcessoUsuarioCondominio``). Mas enxergar não é ser: o administrador global
**não** ganha papel condominial — não vira Administradora, não assume o
acompanhamento de plano preventivo e não é escolhido como responsável por
fallback.

``is_staff`` sozinho não basta: entra na administração técnica quem tem a chave
de superusuário, e só. Cada fronteira desta é provada aqui — e, junto delas, que
a pessoa comum segue limitada ao próprio vínculo.
"""
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.cadastros.permissoes import (
    PAPEIS_QUE_GERENCIAM,
    pode_administrar_acessos,
    pode_configurar_condominio,
    pode_criar_condominios,
)
from apps.condominios.acesso import (
    administra_o_sistema,
    condominios_com_papel,
    condominios_do_usuario,
)
from apps.condominios.constants import Papel
from apps.condominios.gestao import ErroDeGestao, administradora_do_condominio
from apps.condominios.models import AcessoUsuarioCondominio, Condominio
from apps.core.context_processors import NAVIGATION

SENHA = "senha-forte-123"


def criar_condominio(nome="Condomínio de Teste"):
    """Um condomínio de teste, sempre novo."""
    return Condominio.objects.create(nome=nome)


def criar_pessoa(email, nome="Pessoa de Teste"):
    """Uma pessoa de teste. Nunca usa dados reais."""
    return User.objects.create_user(email=email, password=SENHA, full_name=nome)


def criar_administrador_do_sistema(email="admin@eithra.local"):
    """Uma pessoa que administra o Eithra: global, sem vínculo com condomínio."""
    pessoa = criar_pessoa(email, "Administração do sistema")
    pessoa.is_staff = True
    pessoa.is_superuser = True
    pessoa.save(update_fields=["is_staff", "is_superuser"])
    return pessoa


def criar_vinculo(usuario, condominio, papel=Papel.SINDICA):
    """Liga uma pessoa a um condomínio, com o papel exercido lá."""
    return AcessoUsuarioCondominio.objects.create(
        usuario=usuario, condominio=condominio, papel=papel
    )


class AdministracaoDoSistemaTests(TestCase):
    """O predicado único: administra o Eithra quem tem a chave de superusuário."""

    def test_superuser_administra_o_sistema(self):
        self.assertTrue(administra_o_sistema(criar_administrador_do_sistema()))

    def test_is_staff_sem_superuser_nao_administra(self):
        tecnico = criar_pessoa("tecnico@exemplo.test", "Suporte técnico")
        tecnico.is_staff = True
        tecnico.save(update_fields=["is_staff"])
        self.assertFalse(administra_o_sistema(tecnico))

    def test_pessoa_comum_nao_administra(self):
        self.assertFalse(administra_o_sistema(criar_pessoa("comum@exemplo.test")))

    def test_visita_anonima_nao_administra(self):
        self.assertFalse(administra_o_sistema(None))

    def test_conta_inativa_nao_administra_mesmo_sendo_superuser(self):
        admin = criar_administrador_do_sistema()
        admin.is_active = False
        admin.save(update_fields=["is_active"])
        self.assertFalse(administra_o_sistema(admin))


class EscopoGlobalTests(TestCase):
    """Superuser sem vínculo enxerga todos os condomínios ativos — e só enxerga."""

    def setUp(self):
        self.alpha = criar_condominio("Alpha")
        self.beta = criar_condominio("Beta")
        self.inativo = criar_condominio("Gama")
        self.inativo.ativo = False
        self.inativo.save(update_fields=["ativo"])
        self.admin = criar_administrador_do_sistema()

    def test_ve_todos_os_condominios_ativos(self):
        self.assertEqual(
            set(condominios_do_usuario(self.admin)), {self.alpha, self.beta}
        )

    def test_nao_ve_condominio_inativo(self):
        self.assertNotIn(self.inativo, condominios_do_usuario(self.admin))

    def test_escopo_global_nao_cria_vinculo_nenhum(self):
        condominios_do_usuario(self.admin)
        self.assertEqual(
            AcessoUsuarioCondominio.objects.filter(usuario=self.admin).count(), 0
        )

    def test_condominios_com_papel_devolve_todos_os_ativos(self):
        self.assertEqual(
            set(condominios_com_papel(self.admin, PAPEIS_QUE_GERENCIAM)),
            {self.alpha, self.beta},
        )


class ModulosDoSistemaTests(TestCase):
    """O shell oferece ao superuser todos os módulos reais — e só a ele."""

    def setUp(self):
        criar_condominio("Alpha")
        criar_condominio("Beta")

    def _rotulos(self):
        resposta = self.client.get(reverse("core:home"))
        return [item["label"] for item in resposta.context["navigation"]]

    def test_superuser_sem_vinculo_ve_todos_os_modulos_existentes(self):
        self.client.force_login(criar_administrador_do_sistema())
        self.assertEqual(self._rotulos(), [item["label"] for item in NAVIGATION])

    def test_superuser_sem_vinculo_navega_pela_pagina(self):
        self.client.force_login(criar_administrador_do_sistema())
        self.assertEqual(self.client.get(reverse("core:home")).status_code, 200)

    def test_is_staff_sem_superuser_ve_apenas_o_painel(self):
        tecnico = criar_pessoa("tecnico@exemplo.test", "Suporte técnico")
        tecnico.is_staff = True
        tecnico.save(update_fields=["is_staff"])
        self.client.force_login(tecnico)
        self.assertEqual(self._rotulos(), ["Painel"])

    def test_pessoa_sem_vinculo_nenhum_ve_apenas_o_painel(self):
        self.client.force_login(criar_pessoa("sem-vinculo@exemplo.test"))
        self.assertEqual(self._rotulos(), ["Painel"])


class ConfiguracaoEstruturalTests(TestCase):
    """Sem vínculo, o superuser abre o que é estrutural: condomínios e acessos."""

    def setUp(self):
        self.alpha = criar_condominio("Alpha")
        self.beta = criar_condominio("Beta")
        self.admin = criar_administrador_do_sistema()
        self.client.force_login(self.admin)

    def test_pode_abrir_condominio_novo(self):
        self.assertTrue(pode_criar_condominios(self.admin))

    def test_pode_configurar_condominio_sem_vinculo(self):
        self.assertTrue(pode_configurar_condominio(self.admin, self.beta))

    def test_pode_administrar_acessos_sem_vinculo(self):
        self.assertTrue(pode_administrar_acessos(self.admin, self.beta))

    def test_lista_de_condominios_abre_para_o_superuser(self):
        resposta = self.client.get(reverse("cadastros:condominios"))
        self.assertEqual(resposta.status_code, 200)

    def test_acessos_abrem_para_o_superuser(self):
        resposta = self.client.get(reverse("cadastros:acessos"))
        self.assertEqual(resposta.status_code, 200)


class PessoaComumContinuaLimitadaTests(TestCase):
    """Quem não administra o Eithra segue preso ao próprio vínculo."""

    def setUp(self):
        self.alpha = criar_condominio("Alpha")
        self.beta = criar_condominio("Beta")
        self.sindica = criar_pessoa("sindica@exemplo.test", "Síndica")
        criar_vinculo(self.sindica, self.alpha, papel=Papel.SINDICA)

    def test_ve_apenas_o_condominio_do_vinculo(self):
        self.assertEqual(list(condominios_do_usuario(self.sindica)), [self.alpha])

    def test_escopo_do_vinculo_nao_alcanca_o_condominio_alheio(self):
        self.assertEqual(
            set(condominios_com_papel(self.sindica, PAPEIS_QUE_GERENCIAM)),
            {self.alpha},
        )

    def test_pessoa_sem_vinculo_nao_enxerga_condominio(self):
        sem_vinculo = criar_pessoa("sem-vinculo@exemplo.test")
        self.assertEqual(condominios_do_usuario(sem_vinculo).count(), 0)


class SemPapelCondominialTests(TestCase):
    """Enxergar tudo não é ser tudo: o superuser não ganha papel de condomínio."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.admin = criar_administrador_do_sistema()

    def test_superuser_sem_vinculo_nao_e_administradora(self):
        with self.assertRaises(ErroDeGestao):
            administradora_do_condominio(self.condominio)

    def test_superuser_com_vinculo_de_outro_papel_nao_vira_administradora(self):
        criar_vinculo(self.admin, self.condominio, papel=Papel.SUPERVISORA)

        with self.assertRaises(ErroDeGestao):
            administradora_do_condominio(self.condominio)

    def test_a_administradora_de_verdade_continua_sendo_a_resposta(self):
        administradora = criar_pessoa("administradora@exemplo.test", "Administradora")
        criar_vinculo(administradora, self.condominio, papel=Papel.ADMINISTRADORA)

        self.assertEqual(
            administradora_do_condominio(self.condominio), administradora
        )
