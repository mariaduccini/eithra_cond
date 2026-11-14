"""Quem enxerga e quem decide nas rotinas recorrentes (§69).

A resposta vem sempre do vínculo ativo da pessoa com o condomínio. Portaria e
morador não entram nesta fase; a operação entra apenas pelas ocorrências que são
dela, e nunca pelo cadastro das rotinas. Ninguém alcança o que é de outro
condomínio.
"""
from datetime import date

from django.test import TestCase

from apps.condominios.constants import Papel
from apps.rotinas.constants import (
    Periodicidade,
    ResultadoExecucao,
    StatusExecucao,
)
from apps.rotinas.permissoes import (
    condominios_para_criar,
    condominios_para_gerenciar,
    condominios_para_ver,
    execucoes_para_ver,
    pode_administrar_rotina,
    pode_cancelar_execucao,
    pode_concluir,
    pode_criar_em_algum_condominio,
    pode_criar_rotina,
    pode_editar_rotina,
    pode_gerar_demanda,
    pode_gerar_execucao,
    pode_gerar_manutencao,
    pode_ignorar,
    pode_iniciar,
    pode_ver_alguma_rotina,
    pode_ver_execucao,
    pode_ver_rotina,
    rotinas_para_ver,
    situacoes_permitidas_para,
    usuarios_elegiveis_como_responsavel,
    usuarios_elegiveis_entre,
)
from apps.rotinas.services import concluir_execucao, criar_rotina
from apps.rotinas.tests.apoio import criar_condominio, criar_pessoa, criar_vinculo


class VisibilidadeTests(TestCase):
    """Quem enxerga o módulo — e quem não é avisado de que ele existe."""

    def setUp(self):
        self.condominio = criar_condominio()

    def test_morador_e_porteiro_nao_veem(self):
        for papel in (Papel.MORADOR, Papel.PORTEIRO):
            pessoa = criar_pessoa(f"{papel}@exemplo.test", papel)
            criar_vinculo(pessoa, self.condominio, papel=papel)
            self.assertFalse(condominios_para_ver(pessoa).exists())
            self.assertFalse(pode_ver_alguma_rotina(pessoa))

    def test_sem_vinculo_nao_ve(self):
        pessoa = criar_pessoa("sem@exemplo.test", "Sem vínculo")
        self.assertFalse(pode_ver_alguma_rotina(pessoa))

    def test_vinculo_inativo_nao_conta(self):
        pessoa = criar_pessoa("inativo@exemplo.test", "Inativo")
        criar_vinculo(pessoa, self.condominio, papel=Papel.SINDICA, ativo=False)
        self.assertFalse(condominios_para_ver(pessoa).exists())

    def test_admin_gerencia(self):
        admin = criar_pessoa("admin@exemplo.test", "Administração")
        criar_vinculo(admin, self.condominio, papel=Papel.ADMINISTRADORA)
        self.assertTrue(condominios_para_gerenciar(admin).exists())

    def test_supervisora_ve_e_cria_sem_gerenciar(self):
        supervisora = criar_pessoa("sup@exemplo.test", "Supervisão")
        criar_vinculo(supervisora, self.condominio, papel=Papel.SUPERVISORA)
        self.assertTrue(condominios_para_ver(supervisora).exists())
        self.assertTrue(condominios_para_criar(supervisora).exists())
        self.assertFalse(condominios_para_gerenciar(supervisora).exists())

    def test_operacional_ve_mas_nao_cria(self):
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        self.assertTrue(condominios_para_ver(operacional).exists())
        self.assertFalse(pode_criar_em_algum_condominio(operacional))
        self.assertFalse(pode_criar_rotina(operacional, self.condominio))


class BaseDePermissoes(TestCase):
    """Um condomínio com a gestão, a supervisão e a operação, e uma rotina viva."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.supervisora = criar_pessoa("sup@exemplo.test", "Supervisão")
        criar_vinculo(self.supervisora, self.condominio, papel=Papel.SUPERVISORA)
        self.operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(self.operacional, self.condominio, papel=Papel.OPERACIONAL)

    def _rotina(self, **campos):
        base = {
            "condominio": self.condominio,
            "criado_por": self.gestao,
            "titulo": "Limpeza da caixa d'água",
            "data_inicio": date(2026, 1, 10),
            "periodicidade_tipo": Periodicidade.MENSAL,
            "dia_mes": 10,
        }
        base.update(campos)
        return criar_rotina(**base)

    def _pendente(self, rotina):
        return rotina.execucoes.get(status=StatusExecucao.PENDENTE)


class CatalogoDaRotinaTests(BaseDePermissoes):
    """O cadastro das rotinas é de quem responde ou supervisiona — não da operação."""

    def setUp(self):
        super().setUp()
        self.rotina = self._rotina()

    def test_gestao_e_supervisora_veem_o_cadastro(self):
        for pessoa in (self.gestao, self.supervisora):
            self.assertTrue(pode_ver_rotina(pessoa, self.rotina))
            self.assertTrue(
                rotinas_para_ver(pessoa).filter(pk=self.rotina.pk).exists()
            )

    def test_operacao_nao_ve_o_cadastro(self):
        self.assertFalse(pode_ver_rotina(self.operacional, self.rotina))
        self.assertFalse(
            rotinas_para_ver(self.operacional).filter(pk=self.rotina.pk).exists()
        )

    def test_morador_nao_ve_o_cadastro(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.assertFalse(pode_ver_rotina(morador, self.rotina))

    def test_sem_rotina_resolvida_e_negado(self):
        self.assertFalse(pode_ver_rotina(self.gestao, None))


class DecisaoSobreARegraTests(BaseDePermissoes):
    """Editar a regra e adiantar a ocorrência são de quem cria; desativar, só de quem gere."""

    def setUp(self):
        super().setUp()
        self.rotina = self._rotina()

    def test_gestao_edita_gera_e_administra(self):
        self.assertTrue(pode_editar_rotina(self.gestao, self.rotina))
        self.assertTrue(pode_gerar_execucao(self.gestao, self.rotina))
        self.assertTrue(pode_administrar_rotina(self.gestao, self.rotina))

    def test_supervisora_edita_e_gera_mas_nao_desativa(self):
        self.assertTrue(pode_editar_rotina(self.supervisora, self.rotina))
        self.assertTrue(pode_gerar_execucao(self.supervisora, self.rotina))
        self.assertFalse(pode_administrar_rotina(self.supervisora, self.rotina))

    def test_operacao_nao_decide_sobre_a_regra(self):
        self.assertFalse(pode_editar_rotina(self.operacional, self.rotina))
        self.assertFalse(pode_gerar_execucao(self.operacional, self.rotina))
        self.assertFalse(pode_administrar_rotina(self.operacional, self.rotina))


class DecisaoSobreAExecucaoTests(BaseDePermissoes):
    """A ocorrência: quem a enxerga, e o que cada papel pode fazer com ela."""

    def test_gestao_alcanca_todas_as_situacoes(self):
        rotina = self._rotina()
        execucao = self._pendente(rotina)
        self.assertTrue(pode_ver_execucao(self.gestao, execucao))
        self.assertEqual(
            situacoes_permitidas_para(self.gestao, execucao),
            frozenset(StatusExecucao.values),
        )
        self.assertTrue(pode_cancelar_execucao(self.gestao, execucao))

    def test_supervisora_opera_mas_nao_cancela(self):
        rotina = self._rotina()
        execucao = self._pendente(rotina)
        self.assertTrue(pode_iniciar(self.supervisora, execucao))
        self.assertTrue(pode_concluir(self.supervisora, execucao))
        self.assertTrue(pode_ignorar(self.supervisora, execucao))
        self.assertFalse(pode_cancelar_execucao(self.supervisora, execucao))
        self.assertNotIn(
            StatusExecucao.CANCELADA,
            situacoes_permitidas_para(self.supervisora, execucao),
        )

    def test_operacao_responsavel_alcanca_a_ocorrencia(self):
        rotina = self._rotina(responsavel=self.operacional)
        execucao = self._pendente(rotina)
        self.assertTrue(pode_ver_execucao(self.operacional, execucao))
        self.assertTrue(pode_iniciar(self.operacional, execucao))
        self.assertTrue(pode_concluir(self.operacional, execucao))
        self.assertTrue(pode_ignorar(self.operacional, execucao))
        self.assertFalse(pode_cancelar_execucao(self.operacional, execucao))

    def test_operacao_nao_responsavel_nao_alcanca(self):
        rotina = self._rotina()
        execucao = self._pendente(rotina)
        self.assertFalse(pode_ver_execucao(self.operacional, execucao))
        self.assertFalse(pode_iniciar(self.operacional, execucao))
        self.assertFalse(pode_concluir(self.operacional, execucao))
        self.assertFalse(
            execucoes_para_ver(self.operacional).filter(pk=execucao.pk).exists()
        )
        # Quem responde ou supervisiona continua alcançando a mesma ocorrência.
        self.assertTrue(
            execucoes_para_ver(self.supervisora).filter(pk=execucao.pk).exists()
        )

    def test_ocorrencia_encerrada_nao_muda_mais(self):
        rotina = self._rotina()
        execucao = self._pendente(rotina)
        concluir_execucao(
            execucao=execucao,
            usuario=self.gestao,
            resultado=ResultadoExecucao.OK,
        )
        execucao.refresh_from_db()
        self.assertEqual(
            situacoes_permitidas_para(self.gestao, execucao), frozenset()
        )
        self.assertFalse(pode_iniciar(self.gestao, execucao))
        self.assertFalse(pode_cancelar_execucao(self.gestao, execucao))

    def test_sem_execucao_resolvida_e_negado(self):
        self.assertFalse(pode_ver_execucao(self.gestao, None))
        self.assertFalse(pode_iniciar(self.gestao, None))


class ConsequenciaTests(BaseDePermissoes):
    """Abrir demanda ou manutenção é a responsabilidade de registrar no condomínio."""

    def test_gestao_e_supervisora_abrem_consequencia(self):
        rotina = self._rotina()
        execucao = self._pendente(rotina)
        self.assertTrue(pode_gerar_demanda(self.gestao, execucao))
        self.assertTrue(pode_gerar_manutencao(self.supervisora, execucao))

    def test_operacao_nao_abre_consequencia(self):
        rotina = self._rotina(responsavel=self.operacional)
        execucao = self._pendente(rotina)
        self.assertFalse(pode_gerar_demanda(self.operacional, execucao))
        self.assertFalse(pode_gerar_manutencao(self.operacional, execucao))


class OutroCondominioTests(TestCase):
    """Ninguém alcança a rotina de um condomínio onde não tem vínculo."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.outro = criar_condominio("Beta")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.alheia = criar_rotina(
            condominio=self.outro,
            criado_por=self.gestao,
            titulo="Do Beta",
            data_inicio=date(2026, 1, 10),
            periodicidade_tipo=Periodicidade.MENSAL,
            dia_mes=10,
        )

    def test_nao_ve_nem_administra_rotina_de_outro_condominio(self):
        self.assertFalse(pode_ver_rotina(self.gestao, self.alheia))
        self.assertFalse(pode_administrar_rotina(self.gestao, self.alheia))

    def test_nao_alcanca_a_ocorrencia_de_outro_condominio(self):
        execucao = self.alheia.execucoes.get()
        self.assertFalse(pode_ver_execucao(self.gestao, execucao))
        self.assertFalse(pode_gerar_demanda(self.gestao, execucao))


class ResponsaveisElegiveisTests(TestCase):
    """Só quem enxerga o módulo pode responder por uma rotina."""

    def setUp(self):
        self.condominio = criar_condominio()
        self.outro = criar_condominio("Beta")

    def test_elegiveis_sao_de_quem_enxerga_no_condominio(self):
        sindica = criar_pessoa("sindica@exemplo.test", "Síndica")
        criar_vinculo(sindica, self.condominio, papel=Papel.SINDICA)
        supervisora = criar_pessoa("sup@exemplo.test", "Supervisão")
        criar_vinculo(supervisora, self.condominio, papel=Papel.SUPERVISORA)
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        de_fora = criar_pessoa("fora@exemplo.test", "Fora")
        criar_vinculo(de_fora, self.outro, papel=Papel.SINDICA)

        elegiveis = usuarios_elegiveis_como_responsavel(self.condominio)
        self.assertIn(sindica, elegiveis)
        self.assertIn(supervisora, elegiveis)
        self.assertIn(operacional, elegiveis)
        self.assertNotIn(morador, elegiveis)
        self.assertNotIn(de_fora, elegiveis)

    def test_elegiveis_entre_varios_condominios(self):
        daqui = criar_pessoa("daqui@exemplo.test", "Daqui")
        criar_vinculo(daqui, self.condominio, papel=Papel.SINDICA)
        de_fora = criar_pessoa("fora@exemplo.test", "Fora")
        criar_vinculo(de_fora, self.outro, papel=Papel.SINDICA)

        elegiveis = usuarios_elegiveis_entre([self.condominio, self.outro])
        self.assertIn(daqui, elegiveis)
        self.assertIn(de_fora, elegiveis)
