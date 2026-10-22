"""As operações do domínio de comunidade e o que elas recusam.

Nada aqui passa pela tela: o serviço é chamado direto, como o faria qualquer
outro fluxo. Duas regras aparecem em quase todo teste — vínculo não atravessa
condomínio, e cadastro com vínculo em vigor não é desativado em silêncio.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.cadastros.tests.apoio import criar_condominio, criar_pessoa
from apps.comunidade import services
from apps.comunidade.constants import TipoVinculoPessoaUnidade
from apps.comunidade.models import (
    Pessoa,
    Unidade,
    Veiculo,
    VinculoPessoaUnidade,
    VinculoVeiculoPessoa,
    VinculoVeiculoUnidade,
)
from apps.comunidade.tests import apoio


class CadastroDeUnidadeTests(TestCase):
    """Registrar, corrigir e tirar a unidade de circulação."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão Exemplo")

    def test_cadastra_a_unidade_para_o_condominio_informado(self):
        unidade = services.criar_unidade(
            condominio=self.condominio,
            criado_por=self.gestao,
            dados={"identificador": "101", "bloco_torre": "Bloco A"},
        )
        self.assertEqual(unidade.condominio, self.condominio)
        self.assertEqual(unidade.criado_por, self.gestao)
        self.assertTrue(unidade.ativa)

    def test_identificacao_repetida_e_recusada_com_recado_humano(self):
        services.criar_unidade(
            condominio=self.condominio, criado_por=self.gestao, dados={"identificador": "101"}
        )
        with self.assertRaises(services.ErroDeComunidade):
            services.criar_unidade(
                condominio=self.condominio,
                criado_por=self.gestao,
                dados={"identificador": "101"},
            )

    def test_corrige_dados_da_unidade(self):
        unidade = apoio.criar_unidade(self.condominio)
        services.editar_unidade(unidade=unidade, dados={"andar": "10"})
        unidade.refresh_from_db()
        self.assertEqual(unidade.andar, "10")

    def test_desativa_e_devolve_a_unidade_a_operacao(self):
        unidade = apoio.criar_unidade(self.condominio)
        services.inativar_unidade(unidade=unidade)
        self.assertFalse(unidade.ativa)
        services.reativar_unidade(unidade=unidade)
        self.assertTrue(unidade.ativa)

    def test_unidade_com_vinculo_em_vigor_nao_e_desativada(self):
        unidade = apoio.criar_unidade(self.condominio)
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        apoio.criar_vinculo_pessoa_unidade(self.condominio, pessoa, unidade)
        with self.assertRaises(services.ErroDeComunidade):
            services.inativar_unidade(unidade=unidade)
        self.assertTrue(Unidade.objects.get(pk=unidade.pk).ativa)

    def test_unidade_com_vinculo_encerrado_pode_ser_desativada(self):
        unidade = apoio.criar_unidade(self.condominio)
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        apoio.criar_vinculo_pessoa_unidade(
            self.condominio, pessoa, unidade, data_fim=apoio.ontem()
        )
        services.inativar_unidade(unidade=unidade)
        self.assertFalse(Unidade.objects.get(pk=unidade.pk).ativa)

    def test_veiculo_relacionado_em_vigor_impede_desativar_a_unidade(self):
        unidade = apoio.criar_unidade(self.condominio)
        veiculo = apoio.criar_veiculo(self.condominio)
        apoio.criar_vinculo_veiculo_unidade(self.condominio, veiculo, unidade)
        with self.assertRaises(services.ErroDeComunidade):
            services.inativar_unidade(unidade=unidade)


class CadastroDePessoaTests(TestCase):
    """Registrar, corrigir e tirar a pessoa de circulação."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão Exemplo")

    def test_cadastra_a_pessoa(self):
        pessoa = services.criar_pessoa(
            condominio=self.condominio,
            criado_por=self.gestao,
            dados={"nome": "Pessoa Exemplo 01"},
        )
        self.assertEqual(pessoa.nome, "Pessoa Exemplo 01")
        self.assertTrue(pessoa.ativa)

    def test_pessoa_sem_nome_e_recusada(self):
        with self.assertRaises(services.ErroDeComunidade):
            services.criar_pessoa(
                condominio=self.condominio, criado_por=self.gestao, dados={"nome": "  "}
            )

    def test_corrige_o_contato_da_pessoa(self):
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        services.editar_pessoa(pessoa=pessoa, dados={"telefone": "(71) 99999-0000"})
        pessoa.refresh_from_db()
        self.assertEqual(pessoa.telefone, "71999990000")

    def test_pessoa_com_vinculo_em_vigor_nao_e_desativada(self):
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        unidade = apoio.criar_unidade(self.condominio)
        apoio.criar_vinculo_pessoa_unidade(self.condominio, pessoa, unidade)
        with self.assertRaises(services.ErroDeComunidade):
            services.inativar_pessoa(pessoa=pessoa)
        self.assertTrue(Pessoa.objects.get(pk=pessoa.pk).ativa)

    def test_desativar_e_devolver_a_pessoa_a_operacao(self):
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        services.inativar_pessoa(pessoa=pessoa)
        self.assertFalse(pessoa.ativa)
        services.reativar_pessoa(pessoa=pessoa)
        self.assertTrue(pessoa.ativa)


class CadastroDeVeiculoTests(TestCase):
    """Registrar, corrigir e tirar o veículo de circulação."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão Exemplo")

    def test_cadastra_o_veiculo_com_a_placa_normalizada(self):
        veiculo = services.criar_veiculo(
            condominio=self.condominio,
            criado_por=self.gestao,
            dados={"placa": "xyz9k87"},
        )
        self.assertEqual(veiculo.placa, "XYZ9K87")

    def test_placa_repetida_e_recusada_com_recado_humano(self):
        apoio.criar_veiculo(self.condominio, "ABC1D23")
        with self.assertRaises(services.ErroDeComunidade):
            services.criar_veiculo(
                condominio=self.condominio,
                criado_por=self.gestao,
                dados={"placa": "abc1d23"},
            )

    def test_veiculo_com_vinculo_em_vigor_nao_e_desativado(self):
        veiculo = apoio.criar_veiculo(self.condominio)
        pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        apoio.criar_vinculo_veiculo_pessoa(self.condominio, veiculo, pessoa)
        with self.assertRaises(services.ErroDeComunidade):
            services.inativar_veiculo(veiculo=veiculo)
        self.assertTrue(Veiculo.objects.get(pk=veiculo.pk).ativo)

    def test_desativar_e_devolver_o_veiculo_a_circulacao(self):
        veiculo = apoio.criar_veiculo(self.condominio)
        services.inativar_veiculo(veiculo=veiculo)
        self.assertFalse(veiculo.ativo)
        services.reativar_veiculo(veiculo=veiculo)
        self.assertTrue(veiculo.ativo)


class VinculoPessoaUnidadeTests(TestCase):
    """O que liga uma pessoa a uma unidade, dentro de um só condomínio."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão Exemplo")
        self.pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        self.unidade = apoio.criar_unidade(self.condominio)

    def test_liga_a_pessoa_a_unidade(self):
        vinculo = services.criar_vinculo_pessoa_unidade(
            condominio=self.condominio,
            criado_por=self.gestao,
            pessoa=self.pessoa,
            unidade=self.unidade,
            data_inicio=apoio.HOJE,
        )
        self.assertTrue(vinculo.vigente)
        self.assertEqual(vinculo.tipo_vinculo, TipoVinculoPessoaUnidade.MORADOR)

    def test_liga_a_pessoa_a_unidade_sem_data_de_inicio_conhecida(self):
        vinculo = services.criar_vinculo_pessoa_unidade(
            condominio=self.condominio,
            criado_por=self.gestao,
            pessoa=self.pessoa,
            unidade=self.unidade,
            data_inicio=None,
        )
        self.assertIsNone(vinculo.data_inicio)
        self.assertTrue(vinculo.vigente)

    def test_pessoa_de_outro_condominio_e_recusada(self):
        alheia = apoio.criar_pessoa_da_comunidade(criar_condominio("Beta"))
        with self.assertRaises(services.ErroDeComunidade):
            services.criar_vinculo_pessoa_unidade(
                condominio=self.condominio,
                criado_por=self.gestao,
                pessoa=alheia,
                unidade=self.unidade,
                data_inicio=apoio.HOJE,
            )

    def test_unidade_de_outro_condominio_e_recusada(self):
        alheia = apoio.criar_unidade(criar_condominio("Beta"), "101")
        with self.assertRaises(services.ErroDeComunidade):
            services.criar_vinculo_pessoa_unidade(
                condominio=self.condominio,
                criado_por=self.gestao,
                pessoa=self.pessoa,
                unidade=alheia,
                data_inicio=apoio.HOJE,
            )

    def test_encerrar_da_data_de_fim_ao_vinculo_sem_apagar(self):
        vinculo = apoio.criar_vinculo_pessoa_unidade(
            self.condominio, self.pessoa, self.unidade
        )
        services.encerrar_vinculo_pessoa_unidade(
            vinculo=vinculo, data_fim=apoio.ontem()
        )
        vinculo.refresh_from_db()
        self.assertIsNotNone(vinculo.data_fim)
        self.assertTrue(vinculo.encerrado)

    def test_encerrar_com_data_anterior_ao_inicio_e_recusado(self):
        vinculo = apoio.criar_vinculo_pessoa_unidade(
            self.condominio, self.pessoa, self.unidade, data_inicio=apoio.HOJE
        )
        with self.assertRaises(services.ErroDeComunidade):
            services.encerrar_vinculo_pessoa_unidade(
                vinculo=vinculo, data_fim=apoio.ontem()
            )

    def test_mudar_de_unidade_encerra_o_antigo_e_abre_o_novo(self):
        antiga = apoio.criar_unidade(self.condominio, "303")
        nova = apoio.criar_unidade(self.condominio, "404")
        vinculo = apoio.criar_vinculo_pessoa_unidade(
            self.condominio, self.pessoa, antiga
        )

        services.transferir_pessoa_unidade(
            vinculo=vinculo,
            para_unidade=nova,
            data=apoio.HOJE,
            criado_por=self.gestao,
        )

        vigentes = VinculoPessoaUnidade.objects.da_pessoa(self.pessoa).vigentes(
            hoje=apoio.amanha()
        )
        self.assertEqual(list(vigentes.values_list("unidade_id", flat=True)), [nova.pk])
        self.assertTrue(
            VinculoPessoaUnidade.objects.da_pessoa(self.pessoa)
            .da_unidade(antiga)
            .encerrados(hoje=apoio.amanha())
            .exists()
        )

    def test_transferencia_preserva_o_rotulo_original(self):
        antiga = apoio.criar_unidade(self.condominio, "303")
        nova = apoio.criar_unidade(self.condominio, "404")
        vinculo = apoio.criar_vinculo_pessoa_unidade(
            self.condominio,
            self.pessoa,
            antiga,
            tipo_vinculo=TipoVinculoPessoaUnidade.FAMILIAR,
            rotulo_origem="ESPOSA",
        )

        novo = services.transferir_pessoa_unidade(
            vinculo=vinculo,
            para_unidade=nova,
            data=apoio.HOJE,
            criado_por=self.gestao,
        )

        self.assertEqual(novo.tipo_vinculo, TipoVinculoPessoaUnidade.FAMILIAR)
        self.assertEqual(novo.rotulo_origem, "ESPOSA")

    def test_transferencia_de_inicio_desconhecido_preserva_historia_e_abre_novo_conhecido(self):
        antiga = apoio.criar_unidade(self.condominio, "303")
        nova = apoio.criar_unidade(self.condominio, "404")
        vinculo = apoio.criar_vinculo_pessoa_unidade(
            self.condominio,
            self.pessoa,
            antiga,
            tipo_vinculo=TipoVinculoPessoaUnidade.FAMILIAR,
            rotulo_origem="ESPOSA",
            data_inicio=None,
        )

        novo = services.transferir_pessoa_unidade(
            vinculo=vinculo,
            para_unidade=nova,
            data=apoio.HOJE,
            criado_por=self.gestao,
        )

        vinculo.refresh_from_db()
        self.assertIsNone(vinculo.data_inicio)
        self.assertEqual(vinculo.data_fim, apoio.HOJE)
        self.assertEqual(novo.data_inicio, apoio.HOJE)
        self.assertEqual(novo.rotulo_origem, "ESPOSA")
        self.assertEqual(novo.tipo_vinculo, TipoVinculoPessoaUnidade.FAMILIAR)


class SobreposicaoDeVinculosTests(TestCase):
    """O mesmo fato não se registra duas vezes no mesmo período.

    Um vínculo diz qual é a relação de uma pessoa com uma unidade — e só isso.
    A mesma pessoa pode ter relações diferentes com a mesma unidade ao mesmo
    tempo: ser familiar e funcionária da casa são duas relações, e as duas
    cabem. O que não cabe é a mesma relação, no mesmo lugar, em períodos que se
    cruzam: isso não são dois vínculos, é o mesmo anotado duas vezes.
    """

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão Exemplo")
        self.pessoa = apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa A")
        self.unidade = apoio.criar_unidade(self.condominio, "101")

    def _dia(self, dias: int):
        """Um dia contado a partir da referência dos testes."""
        return apoio.HOJE + timedelta(days=dias)

    def _abrir(
        self, *, inicio, fim=None, unidade=None, pessoa=None, tipo=None, rotulo=""
    ):
        return services.criar_vinculo_pessoa_unidade(
            condominio=self.condominio,
            criado_por=self.gestao,
            pessoa=pessoa or self.pessoa,
            unidade=unidade or self.unidade,
            tipo_vinculo=tipo or TipoVinculoPessoaUnidade.FAMILIAR,
            rotulo_origem=rotulo,
            data_inicio=inicio,
            data_fim=fim,
        )

    def test_mesmo_papel_no_mesmo_periodo_e_recusado(self):
        """A. O mesmo papel, em períodos que se cruzam, é o mesmo anotado duas vezes."""
        self._abrir(inicio=self._dia(-30))
        with self.assertRaises(services.ErroDeComunidade):
            self._abrir(inicio=self._dia(-10))

    def test_inicio_desconhecido_aberto_recusa_novo_periodo_aberto(self):
        self._abrir(inicio=None)
        with self.assertRaises(services.ErroDeComunidade):
            self._abrir(inicio=self._dia(10))

    def test_inicio_desconhecido_encerrado_antes_permite_novo_periodo_depois(self):
        antigo = self._abrir(inicio=None, fim=self._dia(-1))
        novo = self._abrir(inicio=self._dia(0))
        self.assertEqual(antigo.data_fim, self._dia(-1))
        self.assertEqual(novo.data_inicio, self._dia(0))

    def test_inicio_desconhecido_conflita_com_outro_inicio_desconhecido(self):
        self._abrir(inicio=None, fim=self._dia(-1))
        with self.assertRaises(services.ErroDeComunidade):
            self._abrir(inicio=None)

    def test_mesmo_papel_em_periodos_vizinhos_e_permitido(self):
        """B. Um papel que acaba e outro que começa logo depois são dois períodos."""
        self._abrir(inicio=self._dia(-60), fim=self._dia(-10))
        novo = self._abrir(inicio=self._dia(-9))
        self.assertTrue(novo.vigente)

    def test_a_mesma_pessoa_pode_ter_dois_papeis_na_mesma_unidade(self):
        """C. Relações de naturezas diferentes coexistem na mesma unidade."""
        self._abrir(
            inicio=self._dia(-30), tipo=TipoVinculoPessoaUnidade.FAMILIAR
        )
        outro = self._abrir(
            inicio=self._dia(-30),
            tipo=TipoVinculoPessoaUnidade.FUNCIONARIO_UNIDADE,
        )
        self.assertEqual(
            outro.tipo_vinculo, TipoVinculoPessoaUnidade.FUNCIONARIO_UNIDADE
        )
        self.assertEqual(
            list(self.pessoa.unidades_atuais()), [self.unidade]
        )

    def test_a_mesma_pessoa_pode_estar_em_duas_unidades(self):
        """D. Unidades diferentes da mesma pessoa são fatos distintos."""
        outra = apoio.criar_unidade(self.condominio, "202")
        self._abrir(inicio=self._dia(-30))
        segundo = self._abrir(
            inicio=self._dia(-30),
            unidade=outra,
            tipo=TipoVinculoPessoaUnidade.MORADOR,
        )
        self.assertEqual(segundo.unidade_id, outra.pk)

    def test_varias_pessoas_convivem_na_mesma_unidade(self):
        """E. A unidade admite mais de uma pessoa ao mesmo tempo."""
        outra = apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa B")
        self._abrir(inicio=self._dia(-30))
        self._abrir(inicio=self._dia(-30), pessoa=outra)
        self.assertEqual(
            VinculoPessoaUnidade.objects.da_unidade(self.unidade).vigentes().count(),
            2,
        )

    def test_papel_futuro_que_se_cruza_com_outro_e_recusado(self):
        """F. A regra olha o período inteiro, e não só o que já vigora."""
        self._abrir(inicio=self._dia(15), fim=self._dia(45))
        with self.assertRaises(services.ErroDeComunidade):
            self._abrir(inicio=self._dia(30), fim=self._dia(60))

    def test_o_dia_do_fim_ainda_disputa_com_o_novo_inicio(self):
        """G. O fim é inclusivo: quem termina em um dia ainda ocupa aquele dia."""
        self._abrir(inicio=self._dia(-60), fim=self._dia(-10))
        with self.assertRaises(services.ErroDeComunidade):
            self._abrir(inicio=self._dia(-10))

    def test_o_dia_seguinte_ao_fim_ja_e_outro_periodo(self):
        """G. Períodos vizinhos, sem dia em comum, não se cruzam."""
        self._abrir(inicio=self._dia(-60), fim=self._dia(-10))
        novo = self._abrir(inicio=self._dia(-9))
        self.assertTrue(novo.vigente)

    def test_rotulo_da_fonte_nao_muda_a_regra_de_sobreposicao(self):
        """I. O texto de origem é registro da fonte, não natureza do vínculo.

        Duas anotações do mesmo papel na mesma unidade, no mesmo período, são a
        mesma coisa duas vezes — mesmo que cada uma tenha vindo com um rótulo
        diferente da planilha.
        """
        self._abrir(inicio=self._dia(-30), rotulo="ESPOSA")
        with self.assertRaises(services.ErroDeComunidade):
            self._abrir(inicio=self._dia(-10), rotulo="PROCURADORA")

    def test_rotulo_da_fonte_e_preservado_no_vinculo_manual(self):
        """I. O rótulo da fonte convive com a natureza normalizada do papel."""
        vinculo = self._abrir(inicio=self._dia(-30), rotulo="  ESPOSA  ")

        self.assertEqual(vinculo.tipo_vinculo, TipoVinculoPessoaUnidade.FAMILIAR)
        self.assertEqual(vinculo.rotulo_origem, "ESPOSA")


class NormalizacaoDoRotuloDaFonteTests(TestCase):
    """A importação futura normaliza o tipo sem perder o texto de origem."""

    def test_familiar_preserva_rotulo_original(self):
        normalizado = services.normalizar_vinculo_pessoa_unidade_da_fonte("  ESPOSA  ")

        self.assertEqual(normalizado.tipo_vinculo, TipoVinculoPessoaUnidade.FAMILIAR)
        self.assertEqual(normalizado.rotulo_origem, "ESPOSA")
        self.assertFalse(normalizado.exige_tratamento_separado)

    def test_rotulo_ambiguo_vira_outro_sem_perder_texto(self):
        normalizado = services.normalizar_vinculo_pessoa_unidade_da_fonte(
            "PROCURADORA"
        )

        self.assertEqual(normalizado.tipo_vinculo, TipoVinculoPessoaUnidade.OUTRO)
        self.assertEqual(normalizado.rotulo_origem, "PROCURADORA")

    def test_contato_de_emergencia_nao_vira_vinculo_automaticamente(self):
        normalizado = services.normalizar_vinculo_pessoa_unidade_da_fonte(
            "CONTATO DE EMERGÊNCIA"
        )

        self.assertIsNone(normalizado.tipo_vinculo)
        self.assertEqual(normalizado.rotulo_origem, "CONTATO DE EMERGÊNCIA")
        self.assertTrue(normalizado.exige_tratamento_separado)


class VinculosDoVeiculoTests(TestCase):
    """Quem usa o veículo e a que unidade ele se relaciona, separadamente."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão Exemplo")
        self.veiculo = apoio.criar_veiculo(self.condominio)
        self.pessoa = apoio.criar_pessoa_da_comunidade(self.condominio)
        self.unidade = apoio.criar_unidade(self.condominio)

    def test_registra_quem_usa_o_veiculo(self):
        vinculo = services.vincular_veiculo_pessoa(
            condominio=self.condominio,
            criado_por=self.gestao,
            veiculo=self.veiculo,
            pessoa=self.pessoa,
            data_inicio=apoio.HOJE,
        )
        self.assertTrue(vinculo.vigente)

    def test_pessoa_de_outro_condominio_nao_usa_o_veiculo(self):
        alheia = apoio.criar_pessoa_da_comunidade(criar_condominio("Beta"))
        with self.assertRaises(services.ErroDeComunidade):
            services.vincular_veiculo_pessoa(
                condominio=self.condominio,
                criado_por=self.gestao,
                veiculo=self.veiculo,
                pessoa=alheia,
                data_inicio=apoio.HOJE,
            )

    def test_encerra_o_uso_do_veiculo(self):
        vinculo = apoio.criar_vinculo_veiculo_pessoa(
            self.condominio, self.veiculo, self.pessoa
        )
        services.encerrar_vinculo_veiculo_pessoa(
            vinculo=vinculo, data_fim=apoio.ontem()
        )
        self.assertTrue(VinculoVeiculoPessoa.objects.encerrados().exists())

    def test_relaciona_o_veiculo_a_unidade(self):
        vinculo = services.vincular_veiculo_unidade(
            condominio=self.condominio,
            criado_por=self.gestao,
            veiculo=self.veiculo,
            unidade=self.unidade,
            data_inicio=apoio.HOJE,
        )
        self.assertTrue(vinculo.vigente)

    def test_unidade_de_outro_condominio_nao_recebe_o_veiculo(self):
        alheia = apoio.criar_unidade(criar_condominio("Beta"), "101")
        with self.assertRaises(services.ErroDeComunidade):
            services.vincular_veiculo_unidade(
                condominio=self.condominio,
                criado_por=self.gestao,
                veiculo=self.veiculo,
                unidade=alheia,
                data_inicio=apoio.HOJE,
            )

    def test_encerra_a_relacao_do_veiculo_com_a_unidade(self):
        vinculo = apoio.criar_vinculo_veiculo_unidade(
            self.condominio, self.veiculo, self.unidade
        )
        services.encerrar_vinculo_veiculo_unidade(
            vinculo=vinculo, data_fim=apoio.ontem()
        )
        self.assertTrue(VinculoVeiculoUnidade.objects.encerrados().exists())

    def test_o_mesmo_uso_do_veiculo_no_mesmo_periodo_e_recusado(self):
        apoio.criar_vinculo_veiculo_pessoa(self.condominio, self.veiculo, self.pessoa)
        with self.assertRaises(services.ErroDeComunidade):
            services.vincular_veiculo_pessoa(
                condominio=self.condominio,
                criado_por=self.gestao,
                veiculo=self.veiculo,
                pessoa=self.pessoa,
                data_inicio=apoio.HOJE,
            )

    def test_o_mesmo_veiculo_pode_ser_usado_por_pessoas_diferentes(self):
        outra = apoio.criar_pessoa_da_comunidade(self.condominio, "Pessoa B")
        apoio.criar_vinculo_veiculo_pessoa(self.condominio, self.veiculo, self.pessoa)
        segundo = services.vincular_veiculo_pessoa(
            condominio=self.condominio,
            criado_por=self.gestao,
            veiculo=self.veiculo,
            pessoa=outra,
            data_inicio=apoio.HOJE,
        )
        self.assertEqual(segundo.pessoa_id, outra.pk)

    def test_a_mesma_relacao_do_veiculo_com_a_unidade_no_mesmo_periodo_e_recusada(self):
        apoio.criar_vinculo_veiculo_unidade(self.condominio, self.veiculo, self.unidade)
        with self.assertRaises(services.ErroDeComunidade):
            services.vincular_veiculo_unidade(
                condominio=self.condominio,
                criado_por=self.gestao,
                veiculo=self.veiculo,
                unidade=self.unidade,
                data_inicio=apoio.HOJE,
            )

    def test_o_mesmo_veiculo_pode_estar_em_unidades_diferentes(self):
        outra = apoio.criar_unidade(self.condominio, "202")
        apoio.criar_vinculo_veiculo_unidade(self.condominio, self.veiculo, self.unidade)
        segundo = services.vincular_veiculo_unidade(
            condominio=self.condominio,
            criado_por=self.gestao,
            veiculo=self.veiculo,
            unidade=outra,
            data_inicio=apoio.HOJE,
        )
        self.assertEqual(segundo.unidade_id, outra.pk)

    # -- Início histórico desconhecido ---------------------------------------

    def test_registra_uso_do_veiculo_sem_data_de_inicio(self):
        vinculo = services.vincular_veiculo_pessoa(
            condominio=self.condominio,
            criado_por=self.gestao,
            veiculo=self.veiculo,
            pessoa=self.pessoa,
        )
        self.assertIsNone(vinculo.data_inicio)
        self.assertTrue(vinculo.vigente)

    def test_relaciona_veiculo_a_unidade_sem_data_de_inicio(self):
        vinculo = services.vincular_veiculo_unidade(
            condominio=self.condominio,
            criado_por=self.gestao,
            veiculo=self.veiculo,
            unidade=self.unidade,
        )
        self.assertIsNone(vinculo.data_inicio)
        self.assertTrue(vinculo.vigente)

    def test_encerrar_hoje_preserva_o_inicio_desconhecido(self):
        vinculo = apoio.criar_vinculo_veiculo_pessoa(
            self.condominio, self.veiculo, self.pessoa, data_inicio=None
        )
        services.encerrar_vinculo_veiculo_pessoa(vinculo=vinculo)
        vinculo.refresh_from_db()
        self.assertIsNone(vinculo.data_inicio)
        self.assertEqual(vinculo.data_fim, timezone.localdate())

    def test_inicio_desconhecido_bloqueia_uso_em_aberto_da_mesma_pessoa(self):
        apoio.criar_vinculo_veiculo_pessoa(
            self.condominio, self.veiculo, self.pessoa, data_inicio=None
        )
        with self.assertRaises(services.ErroDeComunidade):
            services.vincular_veiculo_pessoa(
                condominio=self.condominio,
                criado_por=self.gestao,
                veiculo=self.veiculo,
                pessoa=self.pessoa,
                data_inicio=apoio.HOJE,
            )

    def test_inicio_desconhecido_com_fim_anterior_permite_novo_uso(self):
        apoio.criar_vinculo_veiculo_pessoa(
            self.condominio,
            self.veiculo,
            self.pessoa,
            data_inicio=None,
            data_fim=apoio.ontem(),
        )
        novo = services.vincular_veiculo_pessoa(
            condominio=self.condominio,
            criado_por=self.gestao,
            veiculo=self.veiculo,
            pessoa=self.pessoa,
            data_inicio=apoio.HOJE,
        )
        self.assertEqual(novo.data_inicio, apoio.HOJE)

    def test_uso_aberto_sem_inicio_conflita_com_uso_encerrado_anterior(self):
        apoio.criar_vinculo_veiculo_pessoa(
            self.condominio,
            self.veiculo,
            self.pessoa,
            data_inicio=None,
            data_fim=apoio.ontem(),
        )
        with self.assertRaises(services.ErroDeComunidade):
            services.vincular_veiculo_pessoa(
                condominio=self.condominio,
                criado_por=self.gestao,
                veiculo=self.veiculo,
                pessoa=self.pessoa,
            )

    def test_inicio_desconhecido_bloqueia_relacao_em_aberto_da_mesma_unidade(self):
        apoio.criar_vinculo_veiculo_unidade(
            self.condominio, self.veiculo, self.unidade, data_inicio=None
        )
        with self.assertRaises(services.ErroDeComunidade):
            services.vincular_veiculo_unidade(
                condominio=self.condominio,
                criado_por=self.gestao,
                veiculo=self.veiculo,
                unidade=self.unidade,
                data_inicio=apoio.HOJE,
            )

    def test_inicio_desconhecido_com_fim_anterior_permite_nova_relacao(self):
        apoio.criar_vinculo_veiculo_unidade(
            self.condominio,
            self.veiculo,
            self.unidade,
            data_inicio=None,
            data_fim=apoio.ontem(),
        )
        nova = services.vincular_veiculo_unidade(
            condominio=self.condominio,
            criado_por=self.gestao,
            veiculo=self.veiculo,
            unidade=self.unidade,
            data_inicio=apoio.HOJE,
        )
        self.assertEqual(nova.data_inicio, apoio.HOJE)
