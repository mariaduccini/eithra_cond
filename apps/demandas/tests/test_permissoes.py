"""Autorização do domínio: a regra é uma só, e vive aqui (§14, §15)."""
from django.test import TestCase

from apps.condominios.constants import Papel
from apps.demandas.constants import StatusDemanda
from apps.demandas.models import Demanda
from apps.demandas.permissoes import (
    PAPEIS_QUE_VEEM,
    condominios_para_criar,
    condominios_para_ver,
    pode_alterar_status,
    pode_criar_demanda,
    pode_editar_demanda,
    pode_ver_demanda,
    status_permitidos_para,
    usuarios_elegiveis_como_responsavel,
)
from apps.demandas.tests.apoio import criar_condominio, criar_pessoa, criar_vinculo


class VisibilidadeTests(TestCase):
    """Quem enxerga o módulo e em qual condomínio."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")

    def _com_papel(self, papel, email):
        pessoa = criar_pessoa(email, papel)
        criar_vinculo(pessoa, self.condominio, papel=papel)
        return pessoa

    def test_admin_sindica_supervisora_operacional_veem(self):
        for indice, papel in enumerate(sorted(PAPEIS_QUE_VEEM)):
            pessoa = self._com_papel(papel, f"{indice}@exemplo.test")
            self.assertTrue(condominios_para_ver(pessoa).exists(), papel)

    def test_morador_nao_ve(self):
        morador = self._com_papel(Papel.MORADOR, "morador@exemplo.test")
        self.assertFalse(condominios_para_ver(morador).exists())

    def test_porteiro_nao_ve(self):
        porteiro = self._com_papel(Papel.PORTEIRO, "porteiro@exemplo.test")
        self.assertFalse(condominios_para_ver(porteiro).exists())

    def test_vinculo_inativo_nao_ve(self):
        pessoa = criar_pessoa("inativo@exemplo.test", "Inativo")
        criar_vinculo(pessoa, self.condominio, papel=Papel.SINDICA, ativo=False)
        self.assertFalse(condominios_para_ver(pessoa).exists())

    def test_nao_ve_condominio_de_outro_vinculo(self):
        pessoa = self._com_papel(Papel.SINDICA, "sindica@exemplo.test")
        outro = criar_condominio("Beta")
        demanda_alheia = Demanda.objects.create(
            condominio=outro, titulo="De outro condomínio", criado_por=pessoa
        )
        self.assertFalse(pode_ver_demanda(pessoa, demanda_alheia))


class CriacaoTests(TestCase):
    """Quem pode registrar demanda (§14)."""

    def setUp(self):
        self.condominio = criar_condominio()

    def _pode_criar(self, papel, email):
        pessoa = criar_pessoa(email, papel)
        criar_vinculo(pessoa, self.condominio, papel=papel)
        return pode_criar_demanda(pessoa, self.condominio)

    def test_admin_cria(self):
        self.assertTrue(self._pode_criar(Papel.ADMINISTRADORA, "admin@exemplo.test"))

    def test_sindica_cria(self):
        self.assertTrue(self._pode_criar(Papel.SINDICA, "sindica@exemplo.test"))

    def test_supervisora_cria(self):
        self.assertTrue(self._pode_criar(Papel.SUPERVISORA, "supervisora@exemplo.test"))

    def test_operacional_nao_cria(self):
        self.assertFalse(self._pode_criar(Papel.OPERACIONAL, "operacional@exemplo.test"))

    def test_morador_nao_cria(self):
        self.assertFalse(self._pode_criar(Papel.MORADOR, "morador@exemplo.test"))

    def test_porteiro_nao_cria(self):
        self.assertFalse(self._pode_criar(Papel.PORTEIRO, "porteiro@exemplo.test"))

    def test_condominios_para_criar_e_vazio_para_quem_nao_pode(self):
        pessoa = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(pessoa, self.condominio, papel=Papel.MORADOR)
        self.assertFalse(condominios_para_criar(pessoa).exists())


class EdicaoTests(TestCase):
    """Editar o conteúdo é responder pelo condomínio (§14, §15)."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.demanda = Demanda.objects.create(
            condominio=self.condominio,
            titulo="Demanda",
            criado_por=criar_pessoa("criador@exemplo.test", "Criador"),
        )

    def _pessoa_com(self, papel, email):
        pessoa = criar_pessoa(email, papel)
        criar_vinculo(pessoa, self.condominio, papel=papel)
        return pessoa

    def test_admin_e_sindica_editam(self):
        self.assertTrue(pode_editar_demanda(self._pessoa_com(Papel.ADMINISTRADORA, "a@exemplo.test"), self.demanda))
        self.assertTrue(pode_editar_demanda(self._pessoa_com(Papel.SINDICA, "s@exemplo.test"), self.demanda))

    def test_supervisora_nao_edita_conteudo(self):
        supervisora = self._pessoa_com(Papel.SUPERVISORA, "sup@exemplo.test")
        self.assertFalse(pode_editar_demanda(supervisora, self.demanda))

    def test_operacional_nao_edita_conteudo(self):
        operacional = self._pessoa_com(Papel.OPERACIONAL, "op@exemplo.test")
        self.assertFalse(pode_editar_demanda(operacional, self.demanda))


class StatusPermitidosTests(TestCase):
    """Quem alcança qual situação — a decisão mora numa função só (§14)."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.criador = criar_pessoa("criador@exemplo.test", "Criador")
        self.demanda = Demanda.objects.create(
            condominio=self.condominio, titulo="Demanda", criado_por=self.criador
        )

    def _pessoa_com(self, papel, email):
        pessoa = criar_pessoa(email, papel)
        criar_vinculo(pessoa, self.condominio, papel=papel)
        return pessoa

    def test_gestao_alcanca_todas_as_situacoes(self):
        sindica = self._pessoa_com(Papel.SINDICA, "s@exemplo.test")
        permitidos = status_permitidos_para(sindica, self.demanda)
        self.assertEqual(set(permitidos), set(StatusDemanda.values))

    def test_supervisora_nao_cancela(self):
        supervisora = self._pessoa_com(Papel.SUPERVISORA, "sup@exemplo.test")
        permitidos = status_permitidos_para(supervisora, self.demanda)
        self.assertNotIn(StatusDemanda.CANCELADA, permitidos)
        self.assertIn(StatusDemanda.CONCLUIDA, permitidos)

    def test_operacional_move_mas_so_conclui_sendo_responsavel(self):
        operacional = self._pessoa_com(Papel.OPERACIONAL, "op@exemplo.test")
        permitidos = status_permitidos_para(operacional, self.demanda)
        self.assertIn(StatusDemanda.EM_ANDAMENTO, permitidos)
        self.assertNotIn(StatusDemanda.CONCLUIDA, permitidos)
        self.assertNotIn(StatusDemanda.CANCELADA, permitidos)

    def test_operacional_responsavel_conclui(self):
        operacional = self._pessoa_com(Papel.OPERACIONAL, "op@exemplo.test")
        self.demanda.responsavel = operacional
        self.demanda.save(update_fields=["responsavel"])
        self.assertIn(
            StatusDemanda.CONCLUIDA,
            status_permitidos_para(operacional, self.demanda),
        )

    def test_encerrada_nao_volta_a_andar_para_quem_nao_gerencia(self):
        operacional = self._pessoa_com(Papel.OPERACIONAL, "op@exemplo.test")
        self.demanda.status = StatusDemanda.CONCLUIDA
        self.demanda.concluido_em = self.demanda.criado_em
        self.demanda.save(update_fields=["status", "concluido_em"])
        self.assertEqual(status_permitidos_para(operacional, self.demanda), frozenset())
        self.assertFalse(pode_alterar_status(operacional, self.demanda))

    def test_sem_vinculo_nao_alcanca_nada(self):
        estranho = criar_pessoa("estranho@exemplo.test", "Estranho")
        self.assertEqual(status_permitidos_para(estranho, self.demanda), frozenset())


class ResponsaveisElegiveisTests(TestCase):
    """O responsável precisa poder acompanhar a demanda que assume (§7)."""

    def setUp(self):
        self.condominio = criar_condominio()

    def test_traz_quem_enxerga_o_modulo(self):
        sindica = criar_pessoa("s@exemplo.test", "Síndica")
        criar_vinculo(sindica, self.condominio, papel=Papel.SINDICA)
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)

        elegiveis = usuarios_elegiveis_como_responsavel(self.condominio)
        self.assertIn(sindica, elegiveis)
        self.assertIn(operacional, elegiveis)

    def test_nao_traz_morador_nem_porteiro(self):
        morador = criar_pessoa("m@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        porteiro = criar_pessoa("p@exemplo.test", "Porteiro")
        criar_vinculo(porteiro, self.condominio, papel=Papel.PORTEIRO)

        elegiveis = usuarios_elegiveis_como_responsavel(self.condominio)
        self.assertNotIn(morador, elegiveis)
        self.assertNotIn(porteiro, elegiveis)

    def test_nao_traz_gente_de_outro_condominio(self):
        outro = criar_condominio("Beta")
        alheio = criar_pessoa("alheio@exemplo.test", "Alheio")
        criar_vinculo(alheio, outro, papel=Papel.SINDICA)

        elegiveis = usuarios_elegiveis_como_responsavel(self.condominio)
        self.assertNotIn(alheio, elegiveis)

    def test_sem_condominio_devolve_vazio(self):
        self.assertFalse(usuarios_elegiveis_como_responsavel(None).exists())
