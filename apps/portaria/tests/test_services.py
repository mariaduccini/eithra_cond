from datetime import timedelta

from django.test import TestCase

from apps.condominios.constants import Papel
from apps.portaria.constants import OrigemPortaria, StatusAutorizacaoPortaria
from apps.portaria.models import AcessoPortaria
from apps.portaria.services import (
    ErroDePortaria,
    empresas_conhecidas,
    registrar_chegada,
    registrar_chegada_nao_prevista,
    registrar_saida,
)
from apps.portaria.tests import apoio


class PortariaServicesTests(TestCase):
    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)

    def test_registra_chegada_de_autorizado(self):
        autorizacao = apoio.criar_autorizacao(self.condominio, self.usuario)
        acesso = registrar_chegada(autorizacao=autorizacao, usuario=self.usuario)
        self.assertEqual(acesso.autorizacao, autorizacao)
        self.assertTrue(acesso.esta_dentro)

    def test_recusa_duplicidade_de_presenca_aberta(self):
        autorizacao = apoio.criar_autorizacao(self.condominio, self.usuario)
        registrar_chegada(autorizacao=autorizacao, usuario=self.usuario)
        with self.assertRaises(ErroDePortaria):
            registrar_chegada(autorizacao=autorizacao, usuario=self.usuario)

    def test_registra_saida(self):
        acesso = apoio.criar_acesso(self.condominio, self.usuario)
        registrar_saida(acesso=acesso, usuario=self.usuario)
        acesso.refresh_from_db()
        self.assertFalse(acesso.esta_dentro)

    def test_chegada_nao_prevista_fica_aguardando_sem_abrir_entrada(self):
        unidade = apoio.comunidade_apoio.criar_unidade(self.condominio, identificador="301")
        autorizacao = registrar_chegada_nao_prevista(
            condominio=self.condominio,
            usuario=self.usuario,
            dados={"nome": "Carlos Visitante", "unidade": unidade, "tipo_acesso": "visitante"},
        )
        self.assertEqual(autorizacao.nome, "Carlos Visitante")
        self.assertIsNone(autorizacao.pessoa_id)
        self.assertEqual(autorizacao.status, StatusAutorizacaoPortaria.AGUARDANDO)
        self.assertEqual(autorizacao.origem, OrigemPortaria.CHEGADA_NAO_PREVISTA)
        self.assertFalse(AcessoPortaria.objects.filter(autorizacao=autorizacao).exists())

    def test_chegada_nao_prevista_nunca_aponta_para_pessoa_cadastrada(self):
        unidade = apoio.comunidade_apoio.criar_unidade(
            self.condominio, identificador="303"
        )
        morador = apoio.comunidade_apoio.criar_pessoa_da_comunidade(
            self.condominio, "Ana Moradora"
        )
        autorizacao = registrar_chegada_nao_prevista(
            condominio=self.condominio,
            usuario=self.usuario,
            dados={
                "nome": "Carlos Visitante",
                "unidade": unidade,
                "tipo_acesso": "visitante",
                "pessoa": morador,
            },
        )
        self.assertIsNone(autorizacao.pessoa_id)


class EmpresasConhecidasTests(TestCase):
    """A memória de empresas da portaria é derivada do histórico, não cadastro.

    O campo Empresa é texto livre: ninguém cadastra nada antes, e a portaria não
    bloqueia quem não está na base. O que a lista faz é evitar redigitar o que já
    passou por aqui — e por isso ela é só sugestão.
    """

    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)

    def test_portaria_sem_historico_nao_sugere_nada(self):
        self.assertEqual(empresas_conhecidas(self.condominio), [])

    def test_empresa_em_branco_nao_vira_sugestao(self):
        apoio.criar_acesso(self.condominio, self.usuario)
        self.assertEqual(empresas_conhecidas(self.condominio), [])

    def test_lembra_quem_ja_passou_por_aqui(self):
        apoio.criar_acesso(self.condominio, self.usuario, empresa="Bombas Eithra")
        self.assertEqual(empresas_conhecidas(self.condominio), ["Bombas Eithra"])

    def test_grafia_repetida_vira_uma_so_e_a_mais_recente_fica(self):
        unidade = apoio.comunidade_apoio.criar_unidade(self.condominio)
        agora = apoio.agora()
        for nome, empresa, quando in (
            ("Primeiro", "bombas eithra", agora - timedelta(hours=2)),
            ("Segundo", "Bombas Eithra", agora),
        ):
            AcessoPortaria.objects.create(
                condominio=self.condominio,
                tipo_acesso="prestador",
                nome=nome,
                empresa=empresa,
                unidade=unidade,
                entrada_registrada_por=self.usuario,
                entrada_em=quando,
            )
        self.assertEqual(empresas_conhecidas(self.condominio), ["Bombas Eithra"])

    def test_o_que_foi_esperado_tambem_alimenta_a_memoria(self):
        apoio.criar_autorizacao(self.condominio, self.usuario, empresa="Climatiza Ar")
        self.assertEqual(empresas_conhecidas(self.condominio), ["Climatiza Ar"])

    def test_memoria_nunca_atravessa_o_condominio(self):
        vizinho = apoio.criar_condominio(nome="Condomínio Vizinho")
        apoio.criar_acesso(vizinho, self.usuario, empresa="Bombas do Vizinho")
        self.assertEqual(empresas_conhecidas(self.condominio), [])
