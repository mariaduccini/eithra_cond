"""Testes do comando que prepara os usuários de teste do piloto.

O comando existe para o piloto não depender de cadastro manual. O que estes
testes provam é o que ele promete: sete contas, uma por perfil; a administração
global em ``is_staff``/``is_superuser`` e as demais sem esse acesso; cada conta
com exatamente o vínculo esperado no condomínio piloto; repetir a execução sem
duplicar nada nem trocar senha em silêncio; e ``--redefinir-senhas`` trocando as
senhas de fato. Nenhum condomínio novo é criado, e nenhum papel sai trocado.
"""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from apps.accounts.models import User
from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio, Condominio

# O que o piloto espera, escrito de forma independente do comando: cada perfil,
# seu e-mail e o papel no condomínio. ``None`` é a administração global, que não
# depende de vínculo.
ESPERADOS = (
    ("admin@eithra.local", None),
    ("administradora@eithra.local", Papel.ADMINISTRADORA),
    ("sindica@eithra.local", Papel.SINDICA),
    ("supervisao@eithra.local", Papel.SUPERVISORA),
    ("operacional@eithra.local", Papel.OPERACIONAL),
    ("portaria@eithra.local", Papel.PORTEIRO),
    ("morador@eithra.local", Papel.MORADOR),
)
EMAILS = tuple(email for email, _ in ESPERADOS)


class CriarUsuariosPilotoTests(TestCase):
    """Prepara as contas do piloto e permanece seguro ao repetir."""

    def setUp(self):
        self.condominio = Condominio.objects.create(nome="Rio Vermelho Exclusive")

    def _executar(self, *args) -> str:
        saida = StringIO()
        call_command("criar_usuarios_piloto", *args, stdout=saida)
        return saida.getvalue()

    def _linhas_de_credenciais(self, saida: str) -> list[str]:
        """As linhas da tabela que trazem e-mail e senha — sem o cabeçalho."""
        return [
            linha
            for linha in saida.splitlines()
            if " | " in linha and "eithra.local" in linha
        ]

    # -- Modelo ---------------------------------------------------------------
    def test_cria_um_usuario_para_cada_perfil(self):
        self._executar()

        for email, _ in ESPERADOS:
            self.assertTrue(User.objects.filter(email=email).exists(), email)

    def test_admin_global_e_staff_e_superuser(self):
        self._executar()

        admin = User.objects.get(email="admin@eithra.local")
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_superuser)
        self.assertTrue(admin.is_active)

    def test_admin_global_nao_recebe_papel_condominial(self):
        """Ser administrador do sistema não faz dele responsável pelo condomínio."""
        self._executar()

        admin = User.objects.get(email="admin@eithra.local")
        self.assertFalse(
            AcessoUsuarioCondominio.objects.filter(usuario=admin).exists()
        )

    def test_demais_nao_sao_staff_nem_superuser(self):
        self._executar()

        outros = User.objects.filter(email__in=EMAILS).exclude(
            email="admin@eithra.local"
        )
        self.assertEqual(outros.count(), 6)
        for usuario in outros:
            self.assertFalse(usuario.is_staff, usuario.email)
            self.assertFalse(usuario.is_superuser, usuario.email)

    # -- Vínculos -------------------------------------------------------------
    def test_cada_usuario_tem_exatamente_o_papel_esperado(self):
        self._executar()

        for email, papel in ESPERADOS:
            usuario = User.objects.get(email=email)
            vinculos = AcessoUsuarioCondominio.objects.filter(usuario=usuario)
            if papel is None:
                self.assertFalse(vinculos.exists(), email)
                continue

            self.assertEqual(vinculos.count(), 1, email)
            vinculo = vinculos.get()
            self.assertEqual(vinculo.condominio, self.condominio)
            self.assertEqual(vinculo.papel, papel)
            self.assertTrue(vinculo.ativo)

    def test_nenhum_vinculo_fora_do_condominio_piloto(self):
        self._executar()

        self.assertEqual(
            AcessoUsuarioCondominio.objects.count(),
            AcessoUsuarioCondominio.objects.filter(
                condominio=self.condominio, ativo=True
            ).count(),
        )
        self.assertEqual(AcessoUsuarioCondominio.objects.count(), 6)

    # -- Idempotência ---------------------------------------------------------
    def test_repetir_o_comando_nao_duplica_vinculos(self):
        self._executar()
        self._executar()

        sindica = User.objects.get(email="sindica@eithra.local")
        self.assertEqual(
            AcessoUsuarioCondominio.objects.filter(usuario=sindica).count(), 1
        )
        self.assertEqual(AcessoUsuarioCondominio.objects.count(), 6)

    def test_segunda_execucao_sem_redefinir_nao_altera_senha_existente(self):
        self._executar()
        antes = User.objects.get(email="sindica@eithra.local").password

        self._executar()

        self.assertEqual(
            User.objects.get(email="sindica@eithra.local").password, antes
        )

    def test_segunda_execucao_sem_redefinir_nao_imprime_senhas(self):
        self._executar()

        saida = self._executar()

        self.assertNotIn("Perfil | E-mail | Senha", saida)

    def test_redefinir_senhas_altera_as_senhas(self):
        self._executar()
        antes = {
            usuario.email: usuario.password
            for usuario in User.objects.filter(email__in=EMAILS)
        }

        self._executar("--redefinir-senhas")

        for email, _ in ESPERADOS:
            depois = User.objects.get(email=email).password
            self.assertNotEqual(antes[email], depois, email)

    def test_redefinir_senhas_imprime_as_sete_credenciais(self):
        self._executar()

        saida = self._executar("--redefinir-senhas")

        self.assertEqual(len(self._linhas_de_credenciais(saida)), 7)

    # -- Credenciais ----------------------------------------------------------
    def test_senhas_geradas_sao_diferentes_entre_si(self):
        saida = self._executar()

        linhas = self._linhas_de_credenciais(saida)
        self.assertEqual(len(linhas), 7)
        senhas = [linha.split(" | ")[-1] for linha in linhas]
        self.assertEqual(len(set(senhas)), 7)

    def test_saida_traz_a_tabela_com_o_cabecalho(self):
        saida = self._executar()

        self.assertIn("Perfil | E-mail | Senha", saida)
        self.assertIn("admin@eithra.local", saida)

    # -- Condomínio -----------------------------------------------------------
    def test_nao_cria_segundo_condominio(self):
        self._executar()

        self.assertEqual(Condominio.objects.count(), 1)

    def test_mais_de_um_condominio_exige_identificador(self):
        Condominio.objects.create(nome="Outro Condomínio")

        with self.assertRaises(CommandError):
            self._executar()

        self.assertFalse(User.objects.filter(email__in=EMAILS).exists())

    def test_identificador_escolhe_o_condominio(self):
        outro = Condominio.objects.create(nome="Outro Condomínio")

        self._executar("--condominio", str(outro.pk))

        sindica = User.objects.get(email="sindica@eithra.local")
        vinculo = AcessoUsuarioCondominio.objects.get(usuario=sindica)
        self.assertEqual(vinculo.condominio, outro)

    def test_identificador_invalido_e_recusado(self):
        with self.assertRaises(CommandError):
            self._executar("--condominio", "não-é-um-identificador")

    def test_sem_condominio_cadastrado_o_comando_para(self):
        Condominio.objects.all().delete()

        with self.assertRaises(CommandError):
            self._executar()

        self.assertFalse(User.objects.filter(email__in=EMAILS).exists())
