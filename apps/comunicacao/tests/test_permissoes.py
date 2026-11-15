"""Quem pode ver, escrever e gerar — a resposta vem sempre do domínio.

O que se verifica aqui é a régua de autorização: os papéis que respondem pelo
condomínio, os que apenas acompanham, e a fronteira entre condomínios. O menu não
entra nesta conta — esconder um item nunca foi a mesma coisa que negar acesso.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.condominios.constants import Papel

from .apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)
from .. import permissoes
from ..constants import ModeloComunicado


class PermissoesDaComunicacaoTests(TestCase):
    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.outro = criar_condominio("Beta")
        self.comunicacao = self._comunicacao_no_condominio(self.condominio)

    def _comunicacao_no_condominio(self, condominio):
        from ..models import Comunicacao

        autor = criar_pessoa(f"autor-{condominio.pk}@exemplo.test", "Autor")
        criar_vinculo(autor, condominio, papel=Papel.ADMINISTRADORA)
        return Comunicacao.objects.create(
            condominio=condominio,
            modelo=ModeloComunicado.AVISO,
            titulo="Aviso",
            criado_por=autor,
        )

    def _usuario_com(self, papel):
        usuario = criar_pessoa(f"{papel}@exemplo.test", papel)
        criar_vinculo(usuario, self.condominio, papel=papel)
        return usuario

    def test_administradora_e_sindica_gerenciam(self):
        for papel in (Papel.ADMINISTRADORA, Papel.SINDICA):
            with self.subTest(papel=papel):
                usuario = self._usuario_com(papel)
                self.assertTrue(
                    permissoes.pode_ver_comunicacao(usuario, self.comunicacao)
                )
                self.assertTrue(
                    permissoes.pode_criar_comunicacao(usuario, self.condominio)
                )
                self.assertTrue(
                    permissoes.pode_editar_comunicacao(usuario, self.comunicacao)
                )

    def test_supervisora_escreve_mas_nao_gera(self):
        usuario = self._usuario_com(Papel.SUPERVISORA)
        self.assertTrue(permissoes.pode_ver_comunicacao(usuario, self.comunicacao))
        self.assertTrue(permissoes.pode_criar_comunicacao(usuario, self.condominio))
        self.assertFalse(
            permissoes.pode_editar_comunicacao(usuario, self.comunicacao)
        )

    def test_operacao_apenas_acompanha(self):
        usuario = self._usuario_com(Papel.OPERACIONAL)
        self.assertTrue(permissoes.pode_ver_comunicacao(usuario, self.comunicacao))
        self.assertFalse(permissoes.pode_criar_comunicacao(usuario, self.condominio))
        self.assertFalse(
            permissoes.pode_editar_comunicacao(usuario, self.comunicacao)
        )

    def test_quem_nao_tem_vinculo_nao_enxerga(self):
        forasteiro = criar_pessoa("forasteiro@exemplo.test", "Forasteiro")
        self.assertFalse(
            permissoes.pode_ver_comunicacao(forasteiro, self.comunicacao)
        )
        self.assertFalse(
            permissoes.pode_criar_comunicacao(forasteiro, self.condominio)
        )
        self.assertFalse(permissoes.pode_ver_comunicacao_em_algum(forasteiro))

    def test_comunicacao_de_outro_condominio_nao_e_vista(self):
        usuario = self._usuario_com(Papel.SINDICA)
        alheia = self._comunicacao_no_condominio(self.outro)
        self.assertFalse(permissoes.pode_ver_comunicacao(usuario, alheia))
        self.assertFalse(permissoes.pode_editar_comunicacao(usuario, alheia))

    def test_administrador_do_sistema_tem_escopo_global(self):
        adm = get_user_model().objects.create_superuser(
            email="admin@exemplo.test", password="senha-forte-123", full_name="Admin"
        )
        alheia = self._comunicacao_no_condominio(self.outro)
        self.assertTrue(permissoes.pode_ver_comunicacao(adm, self.comunicacao))
        self.assertTrue(permissoes.pode_ver_comunicacao(adm, alheia))
        self.assertTrue(permissoes.pode_criar_comunicacao(adm, self.outro))
        self.assertTrue(permissoes.pode_editar_comunicacao(adm, alheia))

    def test_comunicacao_nula_ou_sem_chave_e_negada(self):
        usuario = self._usuario_com(Papel.SINDICA)
        self.assertFalse(permissoes.pode_ver_comunicacao(usuario, None))
        self.assertFalse(permissoes.pode_criar_comunicacao(usuario, None))
        self.assertFalse(permissoes.pode_editar_comunicacao(usuario, None))
