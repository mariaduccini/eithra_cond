"""A Administradora é função do condomínio; administrar o Eithra é outra coisa.

Este arquivo guarda a decisão em três partes. Primeiro, o vocabulário: quem
responde pelo condomínio é a Administradora, e o papel de administração do
sistema não mora no ``Papel`` — ele é global, em ``is_staff``/``is_superuser``.
Depois, o legado: o papel que se chamava "Administração" passa a se chamar
"Administradora" sem que nenhum vínculo se perca. Por fim, a resolução: um
condomínio só tem Administradora quando existe exatamente uma, e ninguém mais
serve de substituto — nem Supervisão, nem Síndico, nem administrador do sistema.
"""
import importlib

from django.apps import apps as registro_de_apps
from django.db.migrations import RunPython
from django.db.migrations.exceptions import IrreversibleError
from django.test import TestCase

from apps.accounts.models import User
from apps.condominios.constants import PAPEIS_DE_GESTAO, Papel
from apps.condominios.gestao import ErroDeGestao, administradora_do_condominio
from apps.condominios.models import AcessoUsuarioCondominio, Condominio

MIGRACAO_DO_PAPEL = importlib.import_module(
    "apps.condominios.migrations.0007_papel_administradora"
)


def criar_pessoa(email, nome="Pessoa de Teste"):
    """Cria uma pessoa de teste. Nunca usa dados reais."""
    return User.objects.create_user(
        email=email, password="senha-forte-123", full_name=nome
    )


def criar_vinculo(usuario, condominio, papel, ativo=True):
    """Cria o vínculo de uma pessoa com um condomínio."""
    return AcessoUsuarioCondominio.objects.create(
        usuario=usuario, condominio=condominio, papel=papel, ativo=ativo
    )


class VocabularioDePapeisTests(TestCase):
    """O papel condominial e a administração do sistema não se confundem."""

    def test_administradora_existe_como_papel_do_condominio(self):
        self.assertEqual(Papel.ADMINISTRADORA, "administradora")
        self.assertEqual(Papel.ADMINISTRADORA.label, "Administradora")

    def test_nao_ha_papel_condominial_chamado_admin(self):
        """O nome "admin" sai do vocabulário: ele nomeava a administração local."""
        self.assertNotIn("admin", Papel.values)
        self.assertNotIn("ADMIN", Papel.names)

    def test_supervisora_e_sindica_continuam_distintas(self):
        self.assertEqual(Papel.SUPERVISORA, "supervisora")
        self.assertEqual(Papel.SUPERVISORA.label, "Supervisão")
        self.assertEqual(Papel.SINDICA, "sindica")
        self.assertEqual(Papel.SINDICA.label, "Síndico(a)")
        self.assertNotEqual(Papel.SUPERVISORA, Papel.ADMINISTRADORA)
        self.assertNotEqual(Papel.SINDICA, Papel.ADMINISTRADORA)

    def test_gestao_do_condominio_e_administradora_ou_sindica(self):
        self.assertEqual(
            PAPEIS_DE_GESTAO, frozenset({Papel.ADMINISTRADORA, Papel.SINDICA})
        )

    def test_administracao_do_sistema_e_global_e_nao_um_papel(self):
        """Quem administra o Eithra é ``is_staff``/``is_superuser``, do Django."""
        nomes_de_campo = {campo.name for campo in User._meta.fields}
        self.assertIn("is_staff", nomes_de_campo)
        self.assertIn("is_superuser", nomes_de_campo)
        self.assertFalse(hasattr(Papel, "ADMIN"))


class MigracaoDoPapelTests(TestCase):
    """O vínculo legado vira Administradora sem perder nada do que era."""

    def setUp(self):
        self.condominio = Condominio.objects.create(nome="Residencial Aurora")
        self.pessoa = criar_pessoa("legado@exemplo.com.br", "Ana Souza")
        self.vinculo = criar_vinculo(
            self.pessoa, self.condominio, Papel.MORADOR, ativo=False
        )
        # O estado legado: o mesmo vínculo, gravado com o nome antigo do papel.
        AcessoUsuarioCondominio.objects.filter(pk=self.vinculo.pk).update(
            papel="admin"
        )

    def _papel_gravado(self):
        return AcessoUsuarioCondominio.objects.get(pk=self.vinculo.pk)

    def test_vinculo_legado_e_convertido_para_administradora(self):
        MIGRACAO_DO_PAPEL.administradora_no_lugar_da_administracao(
            registro_de_apps, None
        )
        self.assertEqual(self._papel_gravado().papel, Papel.ADMINISTRADORA)

    def test_condominio_usuario_e_situacao_continuam_intactos(self):
        MIGRACAO_DO_PAPEL.administradora_no_lugar_da_administracao(
            registro_de_apps, None
        )
        vinculo = self._papel_gravado()
        self.assertEqual(vinculo.condominio_id, self.condominio.pk)
        self.assertEqual(vinculo.usuario_id, self.pessoa.pk)
        self.assertFalse(vinculo.ativo)
        self.assertEqual(AcessoUsuarioCondominio.objects.count(), 1)

    def test_conversao_nao_alcanca_outros_papeis(self):
        criar_vinculo(self.pessoa, Condominio.objects.create(nome="Beta"), Papel.SINDICA)
        MIGRACAO_DO_PAPEL.administradora_no_lugar_da_administracao(
            registro_de_apps, None
        )
        papeis = set(AcessoUsuarioCondominio.objects.values_list("papel", flat=True))
        self.assertEqual(papeis, {Papel.ADMINISTRADORA, Papel.SINDICA})

    def test_rollback_declarado_irreversivel(self):
        """A operação de dados não declara reverso: não há volta segura a dar.

        Depois da conversão, o sistema cria Administradoras novas que nunca
        vieram do legado. Uma volta genérica as rebaixaria junto, e por isso a
        migration é irreversível de propósito.
        """
        operacoes = [
            operacao
            for operacao in MIGRACAO_DO_PAPEL.Migration.operations
            if isinstance(operacao, RunPython)
        ]
        self.assertEqual(len(operacoes), 1)
        self.assertFalse(operacoes[0].reversible)

    def test_rollback_e_recusado_com_erro_do_django(self):
        """Reverter a migration é recusado de forma explícita, sem tocar dados."""
        migration = MIGRACAO_DO_PAPEL.Migration(
            "0007_papel_administradora", "condominios"
        )
        # ``unapply`` recusa na montagem do plano, antes de qualquer operação.
        with self.assertRaises(IrreversibleError):
            migration.unapply(None, None)


class AdministradoraDoCondominioTests(TestCase):
    """Um condomínio, uma Administradora — e nenhum substituto."""

    def setUp(self):
        self.condominio = Condominio.objects.create(nome="Residencial Aurora")
        self.administradora = criar_pessoa(
            "administradora@exemplo.com.br", "Administradora"
        )
        self.supervisora = criar_pessoa("supervisao@exemplo.com.br", "Supervisão")
        self.sindico = criar_pessoa("sindico@exemplo.com.br", "Síndico")
        self.admin_do_sistema = criar_pessoa(
            "tecnico@exemplo.com.br", "Administração do sistema"
        )
        self.admin_do_sistema.is_staff = True
        self.admin_do_sistema.is_superuser = True
        self.admin_do_sistema.save(update_fields=["is_staff", "is_superuser"])

    def test_unica_administradora_ativa_e_a_responsavel(self):
        criar_vinculo(self.administradora, self.condominio, Papel.ADMINISTRADORA)
        self.assertEqual(
            administradora_do_condominio(self.condominio), self.administradora
        )

    def test_sem_administradora_recusa(self):
        criar_vinculo(self.supervisora, self.condominio, Papel.SUPERVISORA)
        criar_vinculo(self.sindico, self.condominio, Papel.SINDICA)
        criar_vinculo(self.admin_do_sistema, self.condominio, Papel.SUPERVISORA)

        with self.assertRaises(ErroDeGestao) as erro:
            administradora_do_condominio(self.condominio)

        self.assertEqual(
            str(erro.exception),
            "Este condomínio ainda não possui uma administradora definida.",
        )

    def test_duas_administradoras_recusam(self):
        criar_vinculo(self.administradora, self.condominio, Papel.ADMINISTRADORA)
        criar_vinculo(self.supervisora, self.condominio, Papel.ADMINISTRADORA)

        with self.assertRaises(ErroDeGestao) as erro:
            administradora_do_condominio(self.condominio)

        self.assertEqual(
            str(erro.exception),
            "Há mais de uma administradora ativa para este condomínio. "
            "Regularize o cadastro antes de continuar.",
        )

    def test_supervisao_sindico_e_administrador_do_sistema_nao_substituem(self):
        criar_vinculo(self.supervisora, self.condominio, Papel.SUPERVISORA)
        criar_vinculo(self.sindico, self.condominio, Papel.SINDICA)
        criar_vinculo(self.admin_do_sistema, self.condominio, Papel.MORADOR)

        with self.assertRaises(ErroDeGestao):
            administradora_do_condominio(self.condominio)

    def test_vinculo_inativo_nao_conta(self):
        criar_vinculo(
            self.administradora, self.condominio, Papel.ADMINISTRADORA, ativo=False
        )

        with self.assertRaises(ErroDeGestao):
            administradora_do_condominio(self.condominio)

    def test_administradora_de_outro_condominio_nao_serve(self):
        outro = Condominio.objects.create(nome="Residencial Beta")
        criar_vinculo(self.administradora, outro, Papel.ADMINISTRADORA)

        with self.assertRaises(ErroDeGestao):
            administradora_do_condominio(self.condominio)

    def test_condominio_ausente_recusa(self):
        with self.assertRaises(ErroDeGestao):
            administradora_do_condominio(None)
