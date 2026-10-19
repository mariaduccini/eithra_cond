"""Quem pode ver e mexer nas manutenções, decidido pelo domínio."""
from django.test import TestCase

from apps.condominios.constants import Papel
from apps.manutencoes.constants import StatusManutencao
from apps.manutencoes.models import Manutencao
from apps.manutencoes.permissoes import (
    condominios_para_criar,
    condominios_para_ver,
    pode_alterar_status,
    pode_cancelar_manutencao,
    pode_concluir,
    pode_criar_manutencao,
    pode_editar_manutencao,
    pode_ver_manutencao,
    status_permitidos_para,
    usuarios_elegiveis_como_responsavel,
)
from apps.manutencoes.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)


class VisibilidadeTests(TestCase):
    """Quem enxerga o módulo — e quem não é avisado de que ele existe."""

    def setUp(self):
        self.condominio = criar_condominio()

    def test_morador_e_porteiro_nao_veem(self):
        for papel in (Papel.MORADOR, Papel.PORTEIRO):
            pessoa = criar_pessoa(f"{papel}@exemplo.test", papel)
            criar_vinculo(pessoa, self.condominio, papel=papel)
            self.assertFalse(condominios_para_ver(pessoa).exists())

    def test_supervisora_ve_e_cria(self):
        supervisora = criar_pessoa("sup@exemplo.test", "Supervisora")
        criar_vinculo(supervisora, self.condominio, papel=Papel.SUPERVISORA)
        self.assertTrue(condominios_para_ver(supervisora).exists())
        self.assertTrue(condominios_para_criar(supervisora).exists())
        self.assertTrue(pode_criar_manutencao(supervisora, self.condominio))

    def test_operacional_ve_mas_nao_cria(self):
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        self.assertTrue(condominios_para_ver(operacional).exists())
        self.assertFalse(condominios_para_criar(operacional).exists())
        self.assertFalse(pode_criar_manutencao(operacional, self.condominio))

    def test_sem_vinculo_nao_ve(self):
        pessoa = criar_pessoa("sem@exemplo.test", "Sem vínculo")
        self.assertFalse(condominios_para_ver(pessoa).exists())

    def test_vinculo_inativo_nao_conta(self):
        pessoa = criar_pessoa("inativo@exemplo.test", "Inativo")
        criar_vinculo(pessoa, self.condominio, papel=Papel.SINDICA, ativo=False)
        self.assertFalse(condominios_para_ver(pessoa).exists())


class AcessoATerceirosTests(TestCase):
    """Ninguém alcança a manutenção de outro condomínio."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.outro = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.alheia = Manutencao.objects.create(
            condominio=self.outro, titulo="Do Beta", criado_por=self.gestao
        )

    def test_gestao_de_outro_condominio_nao_ve(self):
        self.assertFalse(pode_ver_manutencao(self.gestao, self.alheia))

    def test_gestao_do_condominio_ve(self):
        propria = Manutencao.objects.create(
            condominio=self.condominio, titulo="Do Alpha", criado_por=self.gestao
        )
        self.assertTrue(pode_ver_manutencao(self.gestao, propria))


class PapelTests(TestCase):
    """O que cada papel decide sobre uma manutenção do condomínio."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.supervisora = criar_pessoa("sup@exemplo.test", "Supervisora")
        criar_vinculo(self.supervisora, self.condominio, papel=Papel.SUPERVISORA)
        self.operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(self.operacional, self.condominio, papel=Papel.OPERACIONAL)
        self.manutencao = Manutencao.objects.create(
            condominio=self.condominio, titulo="Serviço", criado_por=self.gestao
        )

    def test_gestao_cancela(self):
        self.assertTrue(pode_cancelar_manutencao(self.gestao, self.manutencao))

    def test_supervisora_nao_cancela(self):
        self.assertFalse(pode_cancelar_manutencao(self.supervisora, self.manutencao))
        self.assertNotIn(
            StatusManutencao.CANCELADA,
            status_permitidos_para(self.supervisora, self.manutencao),
        )

    def test_supervisora_edita_move_e_conclui(self):
        self.assertTrue(pode_editar_manutencao(self.supervisora, self.manutencao))
        self.assertTrue(pode_alterar_status(self.supervisora, self.manutencao))
        self.assertTrue(pode_concluir(self.supervisora, self.manutencao))

    def test_operacional_sem_responsabilidade_nao_mexe(self):
        self.assertFalse(pode_alterar_status(self.operacional, self.manutencao))
        self.assertFalse(pode_concluir(self.operacional, self.manutencao))
        self.assertFalse(pode_editar_manutencao(self.operacional, self.manutencao))

    def test_operacional_responsavel_move_e_conclui(self):
        self.manutencao.responsavel = self.operacional
        self.manutencao.save(update_fields=["responsavel"])
        self.assertTrue(pode_alterar_status(self.operacional, self.manutencao))
        self.assertTrue(pode_concluir(self.operacional, self.manutencao))
        self.assertNotIn(
            StatusManutencao.CANCELADA,
            status_permitidos_para(self.operacional, self.manutencao),
        )

    def test_gestao_alcanca_todas_as_situacoes(self):
        self.assertEqual(
            status_permitidos_para(self.gestao, self.manutencao),
            frozenset(StatusManutencao.values),
        )

    def test_manutencao_encerrada_nao_e_movida_por_quem_nao_gerencia(self):
        self.manutencao.status = StatusManutencao.CANCELADA
        self.manutencao.save(update_fields=["status"])
        self.assertFalse(pode_alterar_status(self.supervisora, self.manutencao))
        self.assertTrue(pode_alterar_status(self.gestao, self.manutencao))


class ResponsaveisElegiveisTests(TestCase):
    """Só quem enxerga o módulo pode responder por uma manutenção."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.outro = criar_condominio("Beta")

    def test_elegiveis_sao_de_quem_enxerga_no_condominio(self):
        sindica = criar_pessoa("sindica@exemplo.test", "Síndica")
        criar_vinculo(sindica, self.condominio, papel=Papel.SINDICA)
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        de_fora = criar_pessoa("fora@exemplo.test", "Fora")
        criar_vinculo(de_fora, self.outro, papel=Papel.SINDICA)

        elegiveis = usuarios_elegiveis_como_responsavel(self.condominio)
        self.assertIn(sindica, elegiveis)
        self.assertNotIn(morador, elegiveis)
        self.assertNotIn(de_fora, elegiveis)
