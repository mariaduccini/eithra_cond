"""Testes do vínculo entre pessoas e condomínios, e da decisão de acesso."""
import uuid

from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.accounts.models import User
from apps.condominios.acesso import condominios_do_usuario, tem_acesso_ativo
from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio, Condominio


def criar_pessoa(email, nome="Pessoa de Teste"):
    """Cria uma pessoa de teste. Nunca usa dados reais."""
    return User.objects.create_user(email=email, password="senha-forte-123", full_name=nome)


class CondominioTests(TestCase):
    """O condomínio é o limite de isolamento do sistema."""

    def test_cria_condominio_com_identificador_proprio(self):
        condominio = Condominio.objects.create(nome="Residencial Aurora")
        self.assertIsInstance(condominio.pk, uuid.UUID)
        self.assertTrue(condominio.ativo)
        self.assertIsNotNone(condominio.criado_em)
        self.assertIsNotNone(condominio.atualizado_em)

    def test_condominio_ordenado_por_nome(self):
        Condominio.objects.create(nome="Zebrano")
        Condominio.objects.create(nome="Aurora")
        self.assertEqual(
            [c.nome for c in Condominio.objects.all()], ["Aurora", "Zebrano"]
        )

    def test_condominio_inativo_continua_existindo(self):
        """Desativar preserva o registro; nada é apagado."""
        condominio = Condominio.objects.create(nome="Aurora")
        condominio.ativo = False
        condominio.save(update_fields=["ativo"])
        self.assertTrue(Condominio.objects.filter(pk=condominio.pk).exists())


class VinculoTests(TestCase):
    """O papel pertence ao vínculo, não à pessoa."""

    def setUp(self):
        self.pessoa = criar_pessoa("pessoa@exemplo.com.br", "Ana Souza")
        self.aurora = Condominio.objects.create(nome="Residencial Aurora")

    def test_user_nao_tem_papel_nem_vinculo_embutido(self):
        nomes = {campo.name for campo in User._meta.fields}
        self.assertNotIn("role", nomes)
        self.assertNotIn("papel", nomes)
        self.assertNotIn("condominio", nomes)
        self.assertNotIn("unidade", nomes)

    def test_cria_vinculo_com_papel(self):
        vinculo = AcessoUsuarioCondominio.objects.create(
            usuario=self.pessoa, condominio=self.aurora, papel=Papel.SINDICA
        )
        self.assertIsInstance(vinculo.pk, uuid.UUID)
        self.assertTrue(vinculo.ativo)
        self.assertEqual(vinculo.papel, Papel.SINDICA)
        self.assertEqual(vinculo.get_papel_display(), "Síndico(a)")

    def test_vinculo_aparece_nos_dois_lados(self):
        AcessoUsuarioCondominio.objects.create(
            usuario=self.pessoa, condominio=self.aurora, papel=Papel.MORADOR
        )
        self.assertEqual(self.pessoa.acessos_condominio.count(), 1)
        self.assertEqual(self.aurora.acessos.count(), 1)

    def test_mesmo_par_usuario_condominio_nao_pode_duplicar(self):
        AcessoUsuarioCondominio.objects.create(
            usuario=self.pessoa, condominio=self.aurora, papel=Papel.MORADOR
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AcessoUsuarioCondominio.objects.create(
                    usuario=self.pessoa, condominio=self.aurora, papel=Papel.SINDICA
                )

    def test_nome_da_constraint_de_duplicidade(self):
        nomes = {c.name for c in AcessoUsuarioCondominio._meta.constraints}
        self.assertIn("acesso_unico_por_usuario_e_condominio", nomes)

    def test_reativar_usa_o_vinculo_existente(self):
        """Reativar acesso não cria um segundo vínculo para o mesmo par."""
        vinculo = AcessoUsuarioCondominio.objects.create(
            usuario=self.pessoa, condominio=self.aurora, papel=Papel.MORADOR, ativo=False
        )
        vinculo.ativo = True
        vinculo.save(update_fields=["ativo"])

        self.assertEqual(
            AcessoUsuarioCondominio.objects.filter(
                usuario=self.pessoa, condominio=self.aurora
            ).count(),
            1,
        )
        self.assertTrue(tem_acesso_ativo(self.pessoa, self.aurora))

    def test_vinculo_sem_papel_e_recusado(self):
        vinculo = AcessoUsuarioCondominio(usuario=self.pessoa, condominio=self.aurora)
        self.assertIn("papel", self._erros_de_validacao(vinculo))

    @staticmethod
    def _erros_de_validacao(vinculo) -> set:
        from django.core.exceptions import ValidationError

        try:
            vinculo.full_clean(exclude=None)
        except ValidationError as erro:
            return set(erro.message_dict)
        return set()


class MultiCondominioTests(TestCase):
    """A mesma pessoa pode atuar em condomínios diferentes, com papéis diferentes."""

    def setUp(self):
        self.pessoa = criar_pessoa("multi@exemplo.com.br", "Carlos Dias")
        self.aurora = Condominio.objects.create(nome="Residencial Aurora")
        self.bosque = Condominio.objects.create(nome="Edifício Bosque")

    def test_pessoa_com_um_condominio(self):
        AcessoUsuarioCondominio.objects.create(
            usuario=self.pessoa, condominio=self.aurora, papel=Papel.SINDICA
        )
        self.assertEqual(
            list(condominios_do_usuario(self.pessoa)), [self.aurora]
        )
        self.assertTrue(tem_acesso_ativo(self.pessoa, self.aurora))
        self.assertFalse(tem_acesso_ativo(self.pessoa, self.bosque))

    def test_pessoa_com_dois_condominios(self):
        AcessoUsuarioCondominio.objects.create(
            usuario=self.pessoa, condominio=self.aurora, papel=Papel.SINDICA
        )
        AcessoUsuarioCondominio.objects.create(
            usuario=self.pessoa, condominio=self.bosque, papel=Papel.MORADOR
        )
        self.assertEqual(
            set(condominios_do_usuario(self.pessoa)), {self.aurora, self.bosque}
        )

    def test_papeis_diferentes_por_condominio(self):
        AcessoUsuarioCondominio.objects.create(
            usuario=self.pessoa, condominio=self.aurora, papel=Papel.SINDICA
        )
        AcessoUsuarioCondominio.objects.create(
            usuario=self.pessoa, condominio=self.bosque, papel=Papel.MORADOR
        )
        papeis = {
            v.condominio_id: v.papel
            for v in AcessoUsuarioCondominio.objects.filter(usuario=self.pessoa)
        }
        self.assertEqual(papeis[self.aurora.pk], Papel.SINDICA)
        self.assertEqual(papeis[self.bosque.pk], Papel.MORADOR)
        self.assertFalse(hasattr(self.pessoa, "role"))

    def test_condominio_com_varias_pessoas(self):
        outra = criar_pessoa("outra@exemplo.com.br", "Beatriz Melo")
        for pessoa in (self.pessoa, outra):
            AcessoUsuarioCondominio.objects.create(
                usuario=pessoa, condominio=self.aurora, papel=Papel.MORADOR
            )
        self.assertEqual(self.aurora.acessos.count(), 2)


class AcessoATests(TestCase):
    """Quem não tem vínculo ativo não acessa."""

    def setUp(self):
        self.pessoa = criar_pessoa("acesso@exemplo.com.br", "Dora Alves")
        self.aurora = Condominio.objects.create(nome="Residencial Aurora")
        self.bosque = Condominio.objects.create(nome="Edifício Bosque")

    def test_pessoa_autenticada_sem_vinculo_nenhum(self):
        self.assertEqual(condominios_do_usuario(self.pessoa).count(), 0)
        self.assertFalse(tem_acesso_ativo(self.pessoa, self.aurora))

    def test_vinculo_inativo_nao_concede_acesso(self):
        AcessoUsuarioCondominio.objects.create(
            usuario=self.pessoa, condominio=self.aurora, papel=Papel.SINDICA, ativo=False
        )
        self.assertEqual(condominios_do_usuario(self.pessoa).count(), 0)
        self.assertFalse(tem_acesso_ativo(self.pessoa, self.aurora))

    def test_condominio_inativo_nao_concede_acesso(self):
        AcessoUsuarioCondominio.objects.create(
            usuario=self.pessoa, condominio=self.aurora, papel=Papel.SINDICA
        )
        self.aurora.ativo = False
        self.aurora.save(update_fields=["ativo"])
        self.assertEqual(condominios_do_usuario(self.pessoa).count(), 0)
        self.assertFalse(tem_acesso_ativo(self.pessoa, self.aurora))

    def test_lista_traz_apenas_vinculos_ativos(self):
        AcessoUsuarioCondominio.objects.create(
            usuario=self.pessoa, condominio=self.aurora, papel=Papel.SINDICA
        )
        AcessoUsuarioCondominio.objects.create(
            usuario=self.pessoa, condominio=self.bosque, papel=Papel.MORADOR, ativo=False
        )
        self.assertEqual(list(condominios_do_usuario(self.pessoa)), [self.aurora])

    def test_conta_inativa_nao_tem_acesso_nem_com_vinculo_ativo(self):
        """Desativar a conta corta o acesso; o vínculo continua registrado."""
        AcessoUsuarioCondominio.objects.create(
            usuario=self.pessoa, condominio=self.aurora, papel=Papel.SINDICA
        )
        self.pessoa.is_active = False
        self.pessoa.save(update_fields=["is_active"])

        self.assertFalse(tem_acesso_ativo(self.pessoa, self.aurora))
        self.assertEqual(condominios_do_usuario(self.pessoa).count(), 0)

        self.pessoa.is_active = True
        self.pessoa.save(update_fields=["is_active"])
        self.assertTrue(tem_acesso_ativo(self.pessoa, self.aurora))

    def test_vinculo_inativo_independe_da_conta_ativa(self):
        """São conceitos separados: a conta ativa não reabre um vínculo inativo."""
        AcessoUsuarioCondominio.objects.create(
            usuario=self.pessoa, condominio=self.aurora, papel=Papel.MORADOR, ativo=False
        )
        self.assertTrue(self.pessoa.is_active)
        self.assertFalse(tem_acesso_ativo(self.pessoa, self.aurora))


class IsolamentoTests(TestCase):
    """Nada de um condomínio vaza para o outro."""

    def setUp(self):
        self.ana = criar_pessoa("ana@exemplo.com.br", "Ana Souza")
        self.bruno = criar_pessoa("bruno@exemplo.com.br", "Bruno Lima")
        self.aurora = Condominio.objects.create(nome="Residencial Aurora")
        self.bosque = Condominio.objects.create(nome="Edifício Bosque")

        AcessoUsuarioCondominio.objects.create(
            usuario=self.ana, condominio=self.aurora, papel=Papel.SINDICA
        )
        AcessoUsuarioCondominio.objects.create(
            usuario=self.bruno, condominio=self.bosque, papel=Papel.SINDICA
        )

    def test_acesso_de_um_nao_vale_para_o_outro(self):
        self.assertTrue(tem_acesso_ativo(self.ana, self.aurora))
        self.assertFalse(tem_acesso_ativo(self.ana, self.bosque))
        self.assertTrue(tem_acesso_ativo(self.bruno, self.bosque))
        self.assertFalse(tem_acesso_ativo(self.bruno, self.aurora))

    def test_lista_nunca_devolve_condominio_alheio(self):
        self.assertEqual(list(condominios_do_usuario(self.ana)), [self.aurora])
        self.assertEqual(list(condominios_do_usuario(self.bruno)), [self.bosque])

    def test_usar_identificador_de_outro_condominio_e_negado(self):
        """Identificador vindo de fora não vira autorização."""
        self.assertFalse(tem_acesso_ativo(self.ana, self.bosque))
        self.assertFalse(tem_acesso_ativo(self.ana, self.bosque.pk))


class FailClosedTests(TestCase):
    """Sem informação suficiente, o acesso é negado."""

    def setUp(self):
        self.pessoa = criar_pessoa("fechado@exemplo.com.br", "Elisa Prado")
        self.aurora = Condominio.objects.create(nome="Residencial Aurora")
        AcessoUsuarioCondominio.objects.create(
            usuario=self.pessoa, condominio=self.aurora, papel=Papel.SINDICA
        )

    def test_sem_pessoa_nega(self):
        self.assertFalse(tem_acesso_ativo(None, self.aurora))
        self.assertEqual(condominios_do_usuario(None).count(), 0)

    def test_visita_anonima_nega(self):
        self.assertFalse(tem_acesso_ativo(AnonymousUser(), self.aurora))
        self.assertEqual(condominios_do_usuario(AnonymousUser()).count(), 0)

    def test_sem_condominio_nega(self):
        self.assertFalse(tem_acesso_ativo(self.pessoa, None))

    def test_identificador_solto_nega(self):
        """Nunca confiar em identificador recebido do navegador."""
        self.assertFalse(tem_acesso_ativo(self.pessoa, str(self.aurora.pk)))

    def test_condominio_nao_salvo_nega(self):
        self.assertFalse(tem_acesso_ativo(self.pessoa, Condominio(nome="Fantasma")))

    def test_lista_de_pessoa_sem_registro_e_vazia(self):
        self.assertEqual(condominios_do_usuario(User(email="x@exemplo.com.br")).count(), 0)
