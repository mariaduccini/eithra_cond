"""Leitura da fala na portaria: o texto vira intenção, e nada é registrado (§29).

A transcrição já chegou fiel do núcleo de áudio; aqui se verifica o que a
portaria entende dela. Três coisas atravessam os testes:

* a fala diz o que aconteceu;
* o cadastro do condomínio confirma a quem aquilo se refere — e é ele que decide
  quando a fala é ambígua;
* o que não se confirma não vira fato: vira pergunta, ou não vira nada.

E, no fim, a garantia que separa ler de agir: nenhuma leitura cria registro. Quem
grava é ``services.py``, e só depois da confirmação.
"""
from django.test import TestCase

from apps.cadastros.tests import apoio as cadastros_apoio
from apps.condominios.constants import Papel
from apps.core.interpretacao import Confianca, Estado
from apps.demandas.models import Demanda
from apps.manutencoes.models import Manutencao
from apps.portaria import interpretacao
from apps.portaria.constants import (
    ContextoAcessoPortaria,
    IntencaoPortaria,
    MeioAutorizacaoPortaria,
    StatusAutorizacaoPortaria,
    TipoAcessoPortaria,
)
from apps.portaria.models import AcessoPortaria, AutorizacaoPortaria
from apps.portaria.tests import apoio


class LeituraDaFalaTests(TestCase):
    """Base comum: um condomínio, uma pessoa de cadastro e a leitura da fala."""

    def setUp(self):
        self.usuario = apoio.criar_pessoa("porteiro@teste.com", "Porteiro")
        self.condominio = apoio.criar_condominio()
        apoio.criar_vinculo(self.usuario, self.condominio, papel=Papel.PORTEIRO)

    def _ler(self, fala):
        return interpretacao.interpretar(fala, condominio=self.condominio)

    def _sem_registros(self):
        """A leitura não grava: nenhuma autorização e nenhuma presença nova."""
        self.assertEqual(AutorizacaoPortaria.objects.count(), 0)
        self.assertEqual(AcessoPortaria.objects.count(), 0)


class FalaIncompreendidaTests(LeituraDaFalaTests):
    """Sem sinal não há intenção — e mesmo assim vem uma pergunta (§20)."""

    def test_fala_vazia_pede_para_contar_o_que_aconteceu(self):
        for fala in (None, "", "   "):
            with self.subTest(fala=repr(fala)):
                leitura = self._ler(fala)
                self.assertEqual(leitura.tipo, IntencaoPortaria.INSUFICIENTE)
                self.assertEqual(leitura.confianca, Confianca.BAIXA)
                self.assertEqual(leitura.perguntas, ("O que aconteceu?",))

    def test_fala_sem_assunto_operacional_pergunta_o_que_a_portaria_espera(self):
        leitura = self._ler("Bom dia, tudo bem por aqui?")

        self.assertEqual(leitura.tipo, IntencaoPortaria.INSUFICIENTE)
        self.assertTrue(leitura.perguntas)


class ChegadaTests(LeituraDaFalaTests):
    """Chegou alguém: quem, para onde e com que firmeza (§16)."""

    def test_chegada_confere_com_o_cadastro_e_fica_confirmada(self):
        unidade = apoio.comunidade_apoio.criar_unidade(
            self.condominio, identificador="902"
        )
        apoio.comunidade_apoio.criar_pessoa_da_comunidade(self.condominio, "Carlos")

        leitura = self._ler("Chegou Carlos, visita do 902.")

        self.assertEqual(leitura.tipo, IntencaoPortaria.CHEGADA)
        self.assertEqual(leitura.confianca, Confianca.ALTA)
        self.assertEqual(leitura.entidade("pessoa").confianca, Confianca.ALTA)
        self.assertEqual(leitura.entidade("unidade").objeto, unidade)
        self.assertEqual(
            leitura.entidade("tipo_acesso").valor, TipoAcessoPortaria.VISITANTE
        )
        self.assertIn("registrar_chegada", leitura.acoes)
        self.assertEqual(leitura.perguntas, ())
        self._sem_registros()

    def test_chegada_sem_cadastro_declara_mas_nao_confirma(self):
        leitura = self._ler("Chegou Carlos, visita do 902.")

        self.assertEqual(leitura.tipo, IntencaoPortaria.CHEGADA)
        self.assertEqual(leitura.confianca, Confianca.MEDIA)
        self.assertEqual(leitura.entidade("pessoa").confianca, Confianca.MEDIA)
        self.assertEqual(leitura.entidade("unidade").confianca, Confianca.MEDIA)

    def test_chegada_sem_unidade_pede_a_unidade(self):
        apoio.comunidade_apoio.criar_pessoa_da_comunidade(self.condominio, "Carlos")

        leitura = self._ler("Chegou Carlos.")

        self.assertEqual(leitura.confianca, Confianca.BAIXA)
        self.assertIn("unidade", leitura.faltantes)
        self.assertEqual(leitura.perguntas, ("Para qual unidade?",))

    def test_chegada_sem_saber_quem_pergunta_quem_chegou(self):
        apoio.comunidade_apoio.criar_unidade(self.condominio, identificador="902")

        leitura = self._ler("Chegou no 902.")

        self.assertIn("pessoa", leitura.faltantes)
        self.assertEqual(leitura.perguntas, ("Quem chegou?",))

    def test_a_autorizacao_da_mesma_fala_nao_vira_uma_segunda_consequencia(self):
        """§16: "Ana autorizou pelo interfone" faz parte da chegada, não é outra ação."""
        apoio.comunidade_apoio.criar_unidade(self.condominio, identificador="902")
        apoio.comunidade_apoio.criar_pessoa_da_comunidade(self.condominio, "Carlos")
        apoio.comunidade_apoio.criar_pessoa_da_comunidade(self.condominio, "Ana")

        leitura = self._ler(
            "Chegou Carlos, visita do 902. Ana autorizou pelo interfone."
        )

        self.assertEqual(leitura.tipo, IntencaoPortaria.CHEGADA)
        self.assertEqual(leitura.secundarias, ())
        self.assertEqual(leitura.acoes, ("registrar_chegada",))
        self.assertEqual(
            leitura.entidade("autorizador").valor, "Ana"
        )
        self.assertEqual(leitura.entidade("meio").valor, MeioAutorizacaoPortaria.INTERFONE)
        self.assertEqual(
            leitura.entidade("status").valor, StatusAutorizacaoPortaria.AUTORIZADO
        )
        self.assertIn("autorizado por Ana pelo interfone", leitura.resumo)


class TipoDeAcessoTests(LeituraDaFalaTests):
    """Visitante e prestador se distinguem pela fala (§29)."""

    def test_prestador_quando_a_fala_fala_de_servico(self):
        leitura = self._ler("Chegou o técnico da Atlas para o 902.")

        self.assertEqual(
            leitura.entidade("tipo_acesso").valor, TipoAcessoPortaria.PRESTADOR
        )

    def test_visita_quando_a_fala_fala_de_visita(self):
        leitura = self._ler("Chegou uma visita do 902.")

        self.assertEqual(
            leitura.entidade("tipo_acesso").valor, TipoAcessoPortaria.VISITANTE
        )

    def test_sem_dizer_e_tratado_como_visita_a_confirmar(self):
        leitura = self._ler("Chegou Carlos no 902.")

        tipo = leitura.entidade("tipo_acesso")
        self.assertEqual(tipo.valor, TipoAcessoPortaria.VISITANTE)
        self.assertEqual(tipo.confianca, Confianca.MEDIA)


class QuemEstaChegandoTests(LeituraDaFalaTests):
    """A portaria diz quem chegou de muitos jeitos: a leitura vê a pessoa, a
    unidade e o tipo mesmo quando o verbo "chegou" não aparece (§D1–D3)."""

    def test_chegada_com_o_verbo_e_a_unidade(self):
        apoio.comunidade_apoio.criar_unidade(self.condominio, identificador="302")

        leitura = self._ler("Ana chegou pro 302")

        self.assertEqual(leitura.tipo, IntencaoPortaria.CHEGADA)
        self.assertEqual(leitura.entidade("pessoa").valor, "Ana")
        self.assertEqual(
            leitura.entidade("tipo_acesso").valor, TipoAcessoPortaria.VISITANTE
        )

    def test_saudacao_nao_vira_nome_e_o_nome_tem_tres_palavras(self):
        apoio.comunidade_apoio.criar_unidade(self.condominio, identificador="302")

        leitura = self._ler("Oi, a Ana Paula está aqui pro 302")

        self.assertEqual(leitura.tipo, IntencaoPortaria.CHEGADA)
        self.assertEqual(leitura.entidade("pessoa").valor, "Ana Paula")
        self.assertEqual(
            leitura.entidade("tipo_acesso").valor, TipoAcessoPortaria.VISITANTE
        )

    def test_chegada_sem_o_verbo_chegou(self):
        apoio.comunidade_apoio.criar_unidade(self.condominio, identificador="501")

        leitura = self._ler("visita pro 501, Carlos")

        self.assertEqual(leitura.tipo, IntencaoPortaria.CHEGADA)
        self.assertEqual(leitura.entidade("pessoa").valor, "Carlos")
        self.assertEqual(
            leitura.entidade("tipo_acesso").valor, TipoAcessoPortaria.VISITANTE
        )

    def test_entregador_do_delivery_nao_tem_nome_de_pessoa(self):
        apoio.comunidade_apoio.criar_unidade(self.condominio, identificador="1102")

        leitura = self._ler("tem um entregador do iFood pro 1102")

        self.assertEqual(leitura.tipo, IntencaoPortaria.CHEGADA)
        pessoa = leitura.entidade("pessoa")
        self.assertTrue(pessoa is None or not pessoa.existe)
        self.assertEqual(
            leitura.entidade("tipo_acesso").valor, TipoAcessoPortaria.DELIVERY
        )

    def test_sedex_vira_correios(self):
        apoio.comunidade_apoio.criar_unidade(self.condominio, identificador="704")

        leitura = self._ler("chegou sedex para o 704")

        self.assertEqual(leitura.tipo, IntencaoPortaria.CHEGADA)
        self.assertEqual(
            leitura.entidade("tipo_acesso").valor, TipoAcessoPortaria.CORREIOS
        )

    def test_saudacao_nao_vira_o_nome_do_tecnico(self):
        cadastros_apoio.criar_fornecedor(
            self.condominio,
            nome_razao_social="Atlas Elevadores Ltda.",
            nome_fantasia="Atlas",
        )

        leitura = self._ler("bom dia, o técnico da Atlas chegou")

        self.assertEqual(leitura.tipo, IntencaoPortaria.CHEGADA)
        self.assertNotIn("pessoa", leitura.entidades)
        self.assertEqual(leitura.entidade("empresa").valor, "Atlas")
        self.assertEqual(
            leitura.entidade("tipo_acesso").valor, TipoAcessoPortaria.PRESTADOR
        )

    def test_saida_com_unidade_e_nome(self):
        apoio.comunidade_apoio.criar_unidade(self.condominio, identificador="902")

        leitura = self._ler("Carlos do 902 saiu")

        self.assertEqual(leitura.tipo, IntencaoPortaria.SAIDA)
        self.assertEqual(leitura.entidade("pessoa").valor, "Carlos")

    def test_o_primeiro_nome_confere_com_a_visita_esperada_hoje(self):
        unidade = apoio.comunidade_apoio.criar_unidade(
            self.condominio, identificador="302"
        )
        apoio.criar_autorizacao(
            self.condominio, self.usuario, nome="Mariana Lopes", unidade=unidade
        )

        leitura = self._ler("Mariana chegou pro 302")

        pessoa = leitura.entidade("pessoa")
        self.assertTrue(pessoa.confirmada)
        self.assertEqual(pessoa.valor, "Mariana Lopes")

    def test_corrigir_tipo_troca_o_tipo_lido(self):
        apoio.comunidade_apoio.criar_unidade(self.condominio, identificador="1102")
        leitura = self._ler("tem um entregador do iFood pro 1102")

        interpretacao.corrigir_tipo(leitura, TipoAcessoPortaria.VISITANTE.value)

        self.assertEqual(
            leitura.entidade("tipo_acesso").valor, TipoAcessoPortaria.VISITANTE
        )

    def test_corrigir_tipo_ignora_valor_invalido(self):
        apoio.comunidade_apoio.criar_unidade(self.condominio, identificador="302")
        leitura = self._ler("Ana chegou pro 302")

        interpretacao.corrigir_tipo(leitura, "nao-existe")

        self.assertEqual(
            leitura.entidade("tipo_acesso").valor, TipoAcessoPortaria.VISITANTE
        )

    def test_entrega_nomeia_a_transportadora_sem_a_ligacao(self):
        """Chegou ifood pro 1102" nomeia quem entrega tanto quanto "do iFood"."""
        apoio.comunidade_apoio.criar_unidade(self.condominio, identificador="1102")

        leitura = self._ler("chegou ifood pro 1102")

        self.assertEqual(leitura.tipo, IntencaoPortaria.CHEGADA)
        self.assertEqual(leitura.entidade("empresa").valor, "ifood")
        self.assertEqual(
            leitura.entidade("tipo_acesso").valor, TipoAcessoPortaria.DELIVERY
        )

    def test_entrega_sem_transportadora_ainda_e_uma_chegada(self):
        """Sem transportadora dita, a própria entrega é o nome de quem chegou."""
        apoio.comunidade_apoio.criar_unidade(self.condominio, identificador="1102")

        leitura = self._ler("tem uma entrega pro 1102")

        self.assertEqual(leitura.tipo, IntencaoPortaria.CHEGADA)
        self.assertEqual(leitura.entidade("empresa").valor, "Entrega")
        self.assertEqual(
            leitura.entidade("tipo_acesso").valor, TipoAcessoPortaria.DELIVERY
        )


class AmbiguidadeDeUnidadeTests(LeituraDaFalaTests):
    """Dois cadastros para o mesmo número: o Eithra pergunta, não escolhe (§22)."""

    def test_um_numero_que_serve_a_duas_unidades_vira_pergunta(self):
        apoio.comunidade_apoio.criar_unidade(self.condominio, identificador="A-101")
        apoio.comunidade_apoio.criar_unidade(self.condominio, identificador="B-101")

        leitura = self._ler("Chegou Carlos, visita do 101.")

        self.assertEqual(leitura.confianca, Confianca.BAIXA)
        self.assertEqual(leitura.entidade("unidade").confianca, Confianca.BAIXA)
        self.assertEqual(leitura.perguntas, ("Qual unidade: A-101 ou B-101?",))


class SaidaTests(LeituraDaFalaTests):
    """Alguém saiu: encerra a presença certa — ou pergunta qual é (§18)."""

    def test_saida_encerra_a_presenca_que_corresponde(self):
        unidade = apoio.comunidade_apoio.criar_unidade(
            self.condominio, identificador="902"
        )
        acesso = apoio.criar_acesso(
            self.condominio, self.usuario, nome="Carlos", unidade=unidade
        )
        antes = (
            AcessoPortaria.objects.count(),
            AutorizacaoPortaria.objects.count(),
        )

        leitura = self._ler("Carlos do 902 saiu.")

        self.assertEqual(leitura.tipo, IntencaoPortaria.SAIDA)
        self.assertEqual(leitura.confianca, Confianca.ALTA)
        self.assertEqual(leitura.entidade("acesso").objeto, acesso)
        self.assertEqual(leitura.acoes, ("registrar_saida",))
        self.assertEqual(leitura.perguntas, ())
        self.assertEqual(
            (
                AcessoPortaria.objects.count(),
                AutorizacaoPortaria.objects.count(),
            ),
            antes,
        )
        acesso.refresh_from_db()
        self.assertTrue(acesso.esta_dentro)

    def test_saida_sem_presenca_aberta_nao_encerra_nada(self):
        apoio.comunidade_apoio.criar_unidade(self.condominio, identificador="902")

        leitura = self._ler("Carlos do 902 saiu.")

        self.assertEqual(leitura.tipo, IntencaoPortaria.SAIDA)
        self.assertEqual(leitura.confianca, Confianca.BAIXA)
        self.assertEqual(leitura.acoes, ())
        self.assertEqual(leitura.faltantes, ("acesso",))
        self.assertTrue(leitura.perguntas)

    def test_duas_presencas_compativeis_viram_pergunta(self):
        unidade = apoio.comunidade_apoio.criar_unidade(
            self.condominio, identificador="902"
        )
        apoio.criar_acesso(
            self.condominio, self.usuario, nome="Carlos Visitante", unidade=unidade
        )
        apoio.criar_acesso(
            self.condominio, self.usuario, nome="Carlos Silva", unidade=unidade
        )

        leitura = self._ler("Carlos do 902 saiu.")

        self.assertEqual(leitura.confianca, Confianca.BAIXA)
        self.assertEqual(leitura.acoes, ())
        self.assertTrue(leitura.perguntas[0].startswith("Quem saiu:"))


class AutorizacaoTests(LeituraDaFalaTests):
    """Autorizou, recusou ou não respondeu — e a recusa é lida antes (§29)."""

    def test_autorizacao_com_autorizador_unidade_e_meio(self):
        apoio.comunidade_apoio.criar_unidade(self.condominio, identificador="902")
        apoio.comunidade_apoio.criar_pessoa_da_comunidade(self.condominio, "Ana")

        leitura = self._ler("Ana autorizou a visita do 902 pelo interfone.")

        self.assertEqual(leitura.tipo, IntencaoPortaria.AUTORIZACAO)
        self.assertEqual(leitura.entidade("autorizador").valor, "Ana")
        self.assertEqual(leitura.entidade("unidade").valor, "902")
        self.assertEqual(leitura.entidade("meio").valor, MeioAutorizacaoPortaria.INTERFONE)
        self.assertEqual(
            leitura.entidade("status").valor, StatusAutorizacaoPortaria.AUTORIZADO
        )
        self.assertEqual(leitura.acoes, ("registrar_autorizacao",))

    def test_recusa_nao_e_lida_como_autorizacao(self):
        leitura = self._ler("O morador do 902 não autorizou a entrada.")

        self.assertEqual(leitura.tipo, IntencaoPortaria.AUTORIZACAO)
        self.assertEqual(
            leitura.entidade("status").valor, StatusAutorizacaoPortaria.NAO_AUTORIZADO
        )

    def test_sem_resposta_tem_status_proprio(self):
        leitura = self._ler("O 902 não respondeu ao interfone.")

        self.assertEqual(
            leitura.entidade("status").valor, StatusAutorizacaoPortaria.SEM_RESPOSTA
        )
        self.assertEqual(leitura.entidade("meio").valor, MeioAutorizacaoPortaria.INTERFONE)

    def test_sem_saber_quem_autorizou_pergunta_quem_autorizou(self):
        leitura = self._ler("Autorizou a entrada do 902.")

        self.assertIn("autorizador", leitura.faltantes)
        self.assertEqual(leitura.perguntas, ("Quem autorizou?",))


class RelatoDeProblemaTests(LeituraDaFalaTests):
    """Um problema é uma demanda: a leitura para na intenção (§15)."""

    def test_problema_vira_intencao_de_demanda_sem_abrir_nada(self):
        leitura = self._ler("Tem um barulho estranho no elevador.")

        self.assertEqual(leitura.tipo, IntencaoPortaria.RELATO_DE_PROBLEMA)
        self.assertEqual(leitura.acoes, ("registrar_ocorrencia",))
        self._sem_registros()

    def test_chegada_que_traz_junto_um_problema_mostra_as_duas_consequencias(self):
        """§25: uma fala, várias consequências — apresentadas, não executadas."""
        leitura = self._ler(
            "Chegou o técnico da Atlas e o elevador está fazendo barulho."
        )

        self.assertEqual(leitura.tipo, IntencaoPortaria.CHEGADA)
        self.assertIn(IntencaoPortaria.RELATO_DE_PROBLEMA, leitura.secundarias)
        self.assertEqual(
            leitura.acoes, ("registrar_chegada", "registrar_ocorrencia")
        )
        self._sem_registros()


class EstadoDaEntidadeTests(LeituraDaFalaTests):
    """A origem da informação não se confunde com a firmeza dela (§11)."""

    def test_o_que_o_cadastro_confirma_fica_confirmado(self):
        unidade = apoio.comunidade_apoio.criar_unidade(
            self.condominio, identificador="902"
        )
        apoio.comunidade_apoio.criar_pessoa_da_comunidade(self.condominio, "Carlos")

        leitura = self._ler("Chegou Carlos, visita do 902.")

        self.assertEqual(leitura.entidade("pessoa").estado, Estado.CONFIRMADA)
        self.assertEqual(leitura.entidade("unidade").estado, Estado.CONFIRMADA)
        self.assertEqual(leitura.entidade("unidade").objeto, unidade)
        self.assertEqual(leitura.entidade("contexto").estado, Estado.INFERIDA)

    def test_o_que_a_fala_diz_fica_declarado(self):
        leitura = self._ler("Chegou Carlos, visita do 902.")

        self.assertEqual(leitura.entidade("pessoa").estado, Estado.DECLARADA)
        self.assertEqual(leitura.entidade("unidade").estado, Estado.DECLARADA)
        self.assertEqual(leitura.entidade("tipo_acesso").estado, Estado.DECLARADA)


class ContextoDoAcessoTests(LeituraDaFalaTests):
    """Nem toda chegada vai a uma unidade — o destino se procura antes (§1, §4)."""

    def _com_atlas_no_cadastro(self):
        cadastros_apoio.criar_fornecedor(
            self.condominio,
            nome_razao_social="Atlas Elevadores Ltda.",
            nome_fantasia="Atlas",
        )

    def test_prestador_do_condominio_nao_pergunta_unidade(self):
        """Cenário F: o rapaz da Atlas vem olhar o elevador — serviço do condomínio."""
        self._com_atlas_no_cadastro()
        cadastros_apoio.criar_equipamento(
            self.condominio, nome="Elevador de serviço"
        )

        leitura = self._ler("Chegou o rapaz da Atlas para olhar o elevador.")

        self.assertEqual(leitura.entidade("empresa").valor, "Atlas")
        self.assertEqual(leitura.entidade("servico").valor, "Elevador de serviço")
        self.assertIn(
            leitura.entidade("contexto").valor,
            (
                ContextoAcessoPortaria.PRESTADOR_DO_CONDOMINIO.value,
                ContextoAcessoPortaria.FORNECEDOR_DO_CONDOMINIO.value,
            ),
        )
        self.assertNotIn("unidade", leitura.faltantes)
        self.assertEqual(leitura.perguntas, ())

    def test_empresa_conhecida_pelo_nome_que_a_fala_deu(self):
        """Cenário D: "A Atlas chegou" — o nome dito é uma empresa, não uma pessoa."""
        self._com_atlas_no_cadastro()

        leitura = self._ler("A Atlas chegou.")

        self.assertEqual(leitura.entidade("empresa").valor, "Atlas")
        self.assertEqual(leitura.entidade("empresa").estado, Estado.CONFIRMADA)
        self.assertNotIn("pessoa", leitura.entidades)
        self.assertNotIn("unidade", leitura.faltantes)
        self.assertEqual(leitura.perguntas, ())

    def test_servico_sem_destino_pergunta_qual_servico(self):
        """Cenário H: sem serviço conhecido, a pergunta é a mínima."""
        leitura = self._ler("Chegou o eletricista.")

        self.assertEqual(
            leitura.entidade("tipo_acesso").valor, TipoAcessoPortaria.PRESTADOR
        )
        self.assertNotIn("unidade", leitura.faltantes)
        self.assertEqual(leitura.perguntas, ("Ele veio para qual serviço?",))

    def test_chamado_pela_sindica_fica_declarado(self):
        """Cenário G: quem chamou foi dito na fala — e isso é declaração (§8)."""
        leitura = self._ler("Chegou o eletricista que a síndica chamou.")

        solicitante = leitura.entidade("solicitante")
        self.assertEqual(solicitante.valor, Papel.SINDICA.value)
        self.assertEqual(solicitante.estado, Estado.DECLARADA)
        self.assertEqual(
            leitura.entidade("contexto").valor,
            ContextoAcessoPortaria.PRESTADOR_DO_CONDOMINIO.value,
        )
        self.assertNotIn("unidade", leitura.faltantes)

    def test_empresa_e_unidade_na_mesma_fala(self):
        """Cenário E: o destino é a unidade, e a empresa vem junto (§10)."""
        unidade = apoio.comunidade_apoio.criar_unidade(
            self.condominio, identificador="902"
        )

        leitura = self._ler("Chegou o técnico da Atlas para o 902.")

        self.assertEqual(leitura.entidade("empresa").valor, "Atlas")
        self.assertEqual(leitura.entidade("unidade").objeto, unidade)
        self.assertEqual(
            leitura.entidade("contexto").valor,
            ContextoAcessoPortaria.PRESTADOR_DE_UNIDADE.value,
        )


class ServicoDeDemandaOuManutencaoTests(LeituraDaFalaTests):
    """O serviço aberto — manutenção ou demanda — responde à chegada (§7).

    Quem vem olhar o elevador vem ao condomínio inteiro: é o serviço aberto que
    identifica o destino e dispensa a unidade. Quando há mais de um serviço
    compatível, a leitura não escolhe — pergunta qual, e só isso. E o que só
    diz a natureza do trabalho não relaciona nada: a correspondência se faz
    pelo que o serviço tem de específico, nunca por uma palavra genérica (§8).
    """

    def _manutencao(self, titulo):
        return Manutencao.objects.create(
            condominio=self.condominio, criado_por=self.usuario, titulo=titulo
        )

    def _demanda(self, titulo):
        return Demanda.objects.create(
            condominio=self.condominio, criado_por=self.usuario, titulo=titulo
        )

    def test_manutencao_aberta_define_o_servico_sem_perguntar_unidade(self):
        """Cenário A: manutenção do elevador em aberto responde à chegada."""
        manutencao = self._manutencao("Manutenção do elevador")

        leitura = self._ler("Chegou o técnico para o elevador.")

        self.assertEqual(leitura.tipo, IntencaoPortaria.CHEGADA)
        self.assertEqual(
            leitura.entidade("tipo_acesso").valor, TipoAcessoPortaria.PRESTADOR
        )
        self.assertEqual(leitura.entidade("servico").objeto, manutencao)
        self.assertEqual(leitura.entidade("servico").estado, Estado.CONFIRMADA)
        self.assertEqual(
            leitura.entidade("contexto").valor,
            ContextoAcessoPortaria.PRESTADOR_DO_CONDOMINIO.value,
        )
        self.assertNotIn("unidade", leitura.faltantes)
        self.assertEqual(Manutencao.objects.count(), 1)
        self._sem_registros()

    def test_demanda_aberta_define_o_servico_sem_perguntar_unidade(self):
        """Cenário B: demanda do gerador em aberto responde à chegada."""
        demanda = self._demanda("Gerador sem energia")

        leitura = self._ler("Chegou o eletricista para ver o gerador.")

        self.assertEqual(leitura.tipo, IntencaoPortaria.CHEGADA)
        self.assertEqual(
            leitura.entidade("tipo_acesso").valor, TipoAcessoPortaria.PRESTADOR
        )
        self.assertEqual(leitura.entidade("servico").objeto, demanda)
        self.assertEqual(leitura.entidade("servico").estado, Estado.CONFIRMADA)
        self.assertEqual(
            leitura.entidade("contexto").valor,
            ContextoAcessoPortaria.PRESTADOR_DO_CONDOMINIO.value,
        )
        self.assertNotIn("unidade", leitura.faltantes)
        self.assertEqual(Demanda.objects.count(), 1)
        self._sem_registros()

    def test_dois_servicos_compativeis_perguntam_qual_sem_escolher(self):
        """Cenário C: dois serviços reais — a pergunta mínima, sem wizard."""
        self._manutencao("Manutenção do elevador")
        self._demanda("Elevador travando")

        leitura = self._ler("Chegou o técnico para o elevador.")

        servico = leitura.entidade("servico")
        self.assertEqual(servico.estado, Estado.AMBIGUA)
        self.assertIsNone(servico.objeto)
        self.assertEqual(leitura.perguntas, ("Ele veio para qual serviço?",))
        self._sem_registros()

    def test_palavra_generica_nao_relaciona_servico(self):
        """§8: "manutenção" sozinha não aponta um serviço — não se relaciona."""
        self._demanda("Manutenção preventiva")

        leitura = self._ler("Chegou o técnico para uma manutenção.")

        self.assertNotIn("servico", leitura.entidades)
        self.assertNotIn("unidade", leitura.faltantes)
        self.assertEqual(leitura.perguntas, ("Ele veio para qual serviço?",))
        self._sem_registros()


class VisitaEsperadaTests(LeituraDaFalaTests):
    """A visita que a portaria já esperava completa o que a fala não repetiu (§5)."""

    def test_visita_esperada_completa_o_que_a_fala_nao_disse(self):
        """Cenário C: "Chegou João." — a autorização já sabia quem e para onde."""
        unidade = apoio.comunidade_apoio.criar_unidade(
            self.condominio, identificador="902"
        )
        apoio.comunidade_apoio.criar_pessoa_da_comunidade(self.condominio, "João")
        apoio.criar_autorizacao(
            self.condominio, self.usuario, nome="João", unidade=unidade
        )

        leitura = self._ler("Chegou João.")

        self.assertEqual(leitura.entidade("esperado").estado, Estado.CONFIRMADA)
        self.assertEqual(leitura.entidade("unidade").objeto, unidade)
        self.assertEqual(leitura.confianca, Confianca.ALTA)
        self.assertEqual(leitura.perguntas, ())


class SaidaSemPresencaTests(LeituraDaFalaTests):
    """Cenário I: "Carlos saiu" sem presença aberta não encerra ninguém (§18)."""

    def test_saida_de_nome_conhecido_sem_presenca_aberta_nao_encerra_nada(self):
        leitura = self._ler("Carlos saiu.")

        self.assertEqual(leitura.tipo, IntencaoPortaria.SAIDA)
        self.assertEqual(leitura.entidade("pessoa").valor, "Carlos")
        self.assertEqual(leitura.acoes, ())
        self.assertIn("acesso", leitura.faltantes)
        self.assertTrue(leitura.perguntas)


class ChegadaComProblemaTests(LeituraDaFalaTests):
    """Cenário J: uma fala traz duas consequências — apresentadas, não executadas."""

    def test_chegada_de_fornecedor_com_problema_mostra_as_duas_consequencias(self):
        cadastros_apoio.criar_fornecedor(
            self.condominio,
            nome_razao_social="Atlas Elevadores Ltda.",
            nome_fantasia="Atlas",
        )
        cadastros_apoio.criar_equipamento(
            self.condominio, nome="Elevador de serviço"
        )

        leitura = self._ler(
            "A Atlas chegou e disse que o elevador está com problema."
        )

        self.assertEqual(leitura.tipo, IntencaoPortaria.CHEGADA)
        self.assertIn(IntencaoPortaria.RELATO_DE_PROBLEMA, leitura.secundarias)
        self.assertEqual(
            leitura.acoes, ("registrar_chegada", "registrar_ocorrencia")
        )
        self.assertEqual(leitura.entidade("empresa").valor, "Atlas")
        self._sem_registros()
