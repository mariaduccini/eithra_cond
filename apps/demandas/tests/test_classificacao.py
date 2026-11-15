"""Classificação do relato: o que o EITHRA lê, com que base e com que firmeza (§6).

Três fontes entram na leitura, e cada teste procura mostrar qual delas respondeu:

* o relato diz o que aconteceu, mesmo quando descreve a cena sem o nome técnico;
* o cadastro do condomínio decide a que local e a que equipamento aquilo pertence;
* o histórico mostra o que já aconteceu naquele ponto antes.

E, atravessando os três, o que não tem base não é classificado: fica em aberto,
como pergunta ou como silêncio. É isso que separa "não identificado" de "outro"
e "não deu para avaliar" de "sem risco".
"""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.constants import TipoLocal
from apps.cadastros.models import TipoLocalOperacional
from apps.core import llm
from apps.demandas import derivacao, interpretacao
from apps.demandas.constants import (
    ImpactoDemanda,
    PrioridadeDemanda,
    RecorrenciaDemanda,
    RiscoDemanda,
    TipoProblema,
)
from apps.demandas.derivacao import Confianca, OrigemSugestao
from apps.demandas.models import Demanda
from apps.demandas.services import criar_demanda
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_equipamento,
    criar_local,
    criar_pessoa,
    criar_vinculo,
)


class LeituraDoRelatoTests(TestCase):
    """Base comum: um condomínio, uma pessoa e a leitura por regras."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")

    def _ler(self, relato, **kwargs):
        return derivacao.interpretar(relato, condominio=self.condominio, **kwargs)


class CenaDescritaTests(LeituraDoRelatoTests):
    """A cena descrita classifica mesmo sem a palavra técnica (§6-B)."""

    def test_agua_na_parede_e_infiltracao_sem_a_palavra_infiltracao(self):
        leitura = self._ler("A água está descendo pela parede do G2.")

        self.assertEqual(leitura.tipo_problema.valor, TipoProblema.INFILTRACAO)
        self.assertEqual(leitura.tipo_problema.origem, OrigemSugestao.EITHRA)
        # É interpretação, e não declaração: por isso vai a confirmar.
        self.assertEqual(leitura.tipo_problema.confianca, Confianca.MEDIA)
        self.assertNotIn("infiltracao", derivacao._normalizado(leitura.assunto))

    def test_agua_sem_o_lugar_nao_vira_infiltracao(self):
        leitura = self._ler("Caiu água no chão do salão de festas.")

        self.assertFalse(leitura.tipo_problema.existe)

    def test_portao_fora_de_comando_traz_a_cena_de_seguranca(self):
        leitura = self._ler("O portão não responde ao controle e ficou aberto.")

        self.assertEqual(leitura.tipo_problema.valor, TipoProblema.ACESSO)
        self.assertEqual(leitura.risco.valor, RiscoDemanda.SEGURANCA)
        self.assertEqual(leitura.risco.origem, OrigemSugestao.EITHRA)
        self.assertEqual(leitura.risco.confianca, Confianca.MEDIA)

    def test_a_palavra_tecnica_vale_como_declaracao(self):
        leitura = self._ler("Vazamento na garagem do bloco A.")

        self.assertEqual(leitura.tipo_problema.valor, TipoProblema.VAZAMENTO)
        self.assertEqual(leitura.tipo_problema.origem, OrigemSugestao.RELATO)
        self.assertEqual(
            leitura.tipo_problema.motivo, "Vazamento declarado no relato"
        )


class EquipamentoTests(LeituraDoRelatoTests):
    """A escolha do equipamento é do cadastro — e a ambiguidade vira pergunta."""

    def test_um_unico_equipamento_cadastrado_e_identificado_direto(self):
        equipamento = criar_equipamento(self.condominio, "Elevador de serviço")

        leitura = self._ler("O elevador de serviço parou novamente.")

        self.assertEqual(leitura.equipamento, equipamento)
        proposta = leitura.sugestoes["equipamento"]
        self.assertEqual(proposta.confianca, Confianca.ALTA)
        self.assertEqual(proposta.origem, OrigemSugestao.EITHRA)
        # Com o equipamento resolvido, não sobra o que perguntar.
        self.assertEqual(leitura.pergunta, "")

    def test_equipamento_citado_pelo_tipo_com_dois_cadastros_nao_e_escolhido(self):
        criar_equipamento(self.condominio, "Portão social")
        criar_equipamento(self.condominio, "Portão da garagem")

        leitura = self._ler("O portão não está funcionando.")

        self.assertIsNone(leitura.equipamento)
        self.assertFalse(leitura.sugestoes["equipamento"].existe)
        self.assertEqual(leitura.pergunta, "Qual portão apresentou o problema?")

    def test_equipamento_citado_pelo_tipo_com_um_cadastro_e_identificado(self):
        equipamento = criar_equipamento(self.condominio, "Portão social")

        leitura = self._ler("O portão não está funcionando.")

        self.assertEqual(leitura.equipamento, equipamento)
        self.assertEqual(leitura.pergunta, "")

    def test_equipamento_que_nao_esta_no_cadastro_nao_e_inventado(self):
        criar_equipamento(self.condominio, "Portão social")

        leitura = self._ler("A máquina do guindaste parou.")

        self.assertIsNone(leitura.equipamento)
        self.assertFalse(leitura.sugestoes["equipamento"].existe)

    def test_barulho_sem_equipamento_cadastrado_pede_um_esclarecimento(self):
        criar_equipamento(self.condominio, "Portão social")
        criar_equipamento(self.condominio, "Bomba do subsolo")

        leitura = self._ler("Está fazendo um barulho estranho.")

        self.assertEqual(leitura.tipo_problema.valor, TipoProblema.RUIDO)
        self.assertEqual(
            leitura.pergunta, "Qual equipamento está fazendo o barulho?"
        )

    def test_sem_equipamento_nenhum_nao_ha_por_que_perguntar(self):
        leitura = self._ler("Está fazendo um barulho estranho.")

        self.assertEqual(leitura.pergunta, "")


class ContextoDoCadastroTests(LeituraDoRelatoTests):
    """Área e impacto vêm do cadastro, e não de adivinhação (§6-C)."""

    def test_area_vem_do_tipo_do_local_cadastrado(self):
        criar_local(self.condominio, "Garagem", TipoLocal.GARAGEM)

        leitura = self._ler("Vazamento na garagem.")

        self.assertEqual(leitura.local.nome, "Garagem")
        self.assertEqual(leitura.area.valor, "Garagem")
        self.assertEqual(leitura.area.origem, OrigemSugestao.LOCAL)
        self.assertEqual(leitura.area.confianca, Confianca.ALTA)

    def test_area_vem_do_local_onde_o_equipamento_fica(self):
        casa = criar_local(
            self.condominio, "Casa de máquinas", TipoLocal.CASA_DE_MAQUINAS
        )
        criar_equipamento(self.condominio, "Bomba do subsolo", local=casa)

        leitura = self._ler("A bomba do subsolo parou.")

        self.assertEqual(leitura.area.valor, "Casa de máquinas")
        self.assertEqual(leitura.area.origem, OrigemSugestao.EQUIPAMENTO)
        self.assertEqual(leitura.area.confianca, Confianca.MEDIA)

    def test_area_nao_contradiz_o_local_quando_o_tipo_nomeia_outro_lugar(self):
        """Regressão: um lugar classificado com o nome de outro lugar.

        O condomínio tinha o G2 cadastrado com um tipo chamado "G1", que também
        é o nome de um lugar seu. Dizer "G2 pertence a G1" seria contradizer o
        cadastro, que já disse onde cada um fica — então a área não se sustenta,
        e o silêncio é a resposta. Nada é reclassificado nem inventado.
        """
        tipo_g1 = TipoLocalOperacional.objects.create(
            condominio=self.condominio, nome="G1"
        )
        criar_local(self.condominio, "G1")
        g2 = criar_local(self.condominio, "G2", tipo_local=tipo_g1)

        area = derivacao.area_do_contexto(g2, None)

        self.assertFalse(area.existe)
        self.assertEqual(area.valor, "")

    def test_area_segue_o_relacionamento_real_do_local(self):
        """Quando o tipo de fato agrupa o lugar, a área vem do cadastro."""
        torre = TipoLocalOperacional.objects.create(
            condominio=self.condominio, nome="Torre A"
        )
        criar_local(self.condominio, "Garagem G2", tipo_local=torre)

        leitura = self._ler("Vazamento na Garagem G2.")

        self.assertEqual(leitura.local.nome, "Garagem G2")
        self.assertEqual(leitura.area.valor, "Torre A")
        self.assertEqual(leitura.area.origem, OrigemSugestao.LOCAL)
        self.assertEqual(leitura.area.confianca, Confianca.ALTA)

    def test_lampada_queimada_sem_cadastro_nao_inventa_equipamento(self):
        """A luz queimada sustenta lugar e tipo — não sustenta equipamento."""
        criar_local(self.condominio, "Garagem G2")

        leitura = self._ler("Uma das lâmpadas da Garagem G2 está queimada.")

        self.assertEqual(leitura.local.nome, "Garagem G2")
        self.assertEqual(leitura.tipo_problema.valor, TipoProblema.ILUMINACAO)
        self.assertIsNone(leitura.equipamento)
        self.assertFalse(leitura.sugestoes["equipamento"].existe)
        self.assertEqual(leitura.prioridade, PrioridadeDemanda.NORMAL)
        self.assertEqual(leitura.pergunta, "")

    def test_impacto_acompanha_o_alcance_que_o_relato_aponta(self):
        equipamento = criar_equipamento(self.condominio, "Elevador de serviço")

        leitura = self._ler("O elevador de serviço parou.")

        self.assertEqual(leitura.impacto.valor, ImpactoDemanda.EQUIPAMENTO)
        self.assertEqual(leitura.impacto.origem, OrigemSugestao.EQUIPAMENTO)
        self.assertEqual(leitura.sugestoes["equipamento"].valor, equipamento.nome)

    def test_impacto_declarado_ganha_do_derivado(self):
        criar_local(self.condominio, "Garagem", TipoLocal.GARAGEM)

        leitura = self._ler("A falta de água atingiu o prédio inteiro.")

        self.assertEqual(leitura.impacto.valor, ImpactoDemanda.COLETIVO)
        self.assertEqual(leitura.impacto.origem, OrigemSugestao.RELATO)
        self.assertEqual(leitura.impacto.confianca, Confianca.ALTA)


class LocalCurtoTests(LeituraDoRelatoTests):
    """Nome curto no relato: só vale quando o cadastro o confirma (§1 a §5).

    "G2" não é procurado por dentro do texto — aí casaria em "G20", "AG2" e
    "G2X". Ele entra por palavra inteira e só porque existe como lugar deste
    condomínio: quem autoriza o nome curto é o cadastro, nunca a semelhança.
    """

    def test_o_nome_curto_sozinho_identifica_o_lugar(self):
        g2 = criar_local(self.condominio, "G2")

        leitura = self._ler("G2")

        self.assertEqual(leitura.local, g2)
        self.assertEqual(leitura.sugestoes["local"].valor, "G2")

    def test_o_nome_curto_aparece_no_meio_da_frase(self):
        g2 = criar_local(self.condominio, "G2")

        leitura = self._ler("Uma das lâmpadas queimada no G2")

        self.assertEqual(leitura.local, g2)

    def test_o_nome_curto_vem_depois_da_expressao_do_lugar(self):
        g2 = criar_local(self.condominio, "G2")

        leitura = self._ler("Problema na garagem do G2")

        self.assertEqual(leitura.local, g2)

    def test_os_dois_nomes_curtos_cadastrados_sao_reconhecidos(self):
        g1 = criar_local(self.condominio, "G1")
        g2 = criar_local(self.condominio, "G2")

        self.assertEqual(self._ler("Portão do G1 com defeito").local, g1)
        self.assertEqual(self._ler("Vazamento no G2").local, g2)

    def test_a_pontuacao_do_entorno_nao_impede_o_reconhecimento(self):
        g2 = criar_local(self.condominio, "G2")

        self.assertEqual(self._ler("Ocorrência no G2.").local, g2)
        self.assertEqual(self._ler("Ocorrência no G2, no térreo.").local, g2)

    def test_o_nome_curto_nao_casa_por_dentro_de_outra_palavra(self):
        criar_local(self.condominio, "G2")

        for relato in ("Problema no G20.", "Vazamento no AG2.", "Falha no G2X."):
            with self.subTest(relato=relato):
                leitura = self._ler(relato)
                self.assertIsNone(leitura.local)
                self.assertFalse(leitura.sugestoes["local"].existe)

    def test_nome_curto_fora_do_cadastro_nao_e_reconhecido(self):
        criar_local(self.condominio, "G2")

        leitura = self._ler("Problema no G7.")

        self.assertIsNone(leitura.local)

    def test_o_mesmo_nome_nao_atravessa_condominios(self):
        criar_local(self.condominio, "G2")
        outro = criar_condominio("Beta")

        leitura_do_dono = derivacao.local_do_relato("Problema no G2", self.condominio)

        self.assertEqual(leitura_do_dono.nome, "G2")
        self.assertIsNone(derivacao.local_do_relato("Problema no G2", outro))

    def test_o_lugar_mais_especifico_vence_quando_os_dois_casam(self):
        criar_local(self.condominio, "G2")
        completo = criar_local(self.condominio, "Garagem do G2")

        leitura = self._ler("Vazamento na Garagem do G2.")

        self.assertEqual(leitura.local, completo)

    def test_empatados_na_especificidade_nao_ha_escolha(self):
        criar_local(self.condominio, "G1")
        criar_local(self.condominio, "G2")

        leitura = self._ler("Problemas no G1 e no G2.")

        self.assertIsNone(leitura.local)
        self.assertFalse(leitura.sugestoes["local"].existe)

    def test_a_area_do_lugar_curto_segue_o_cadastro(self):
        torre = TipoLocalOperacional.objects.create(
            condominio=self.condominio, nome="Torre A"
        )
        g2 = criar_local(self.condominio, "G2", tipo_local=torre)

        leitura = self._ler("Vazamento no G2.")

        self.assertEqual(leitura.local, g2)
        self.assertEqual(leitura.area.valor, "Torre A")

    def test_a_area_nao_contradiz_o_lugar_curto(self):
        """O caso do cadastro inconsistente: "G2" classificado como "G1"."""
        tipo_g1 = TipoLocalOperacional.objects.create(
            condominio=self.condominio, nome="G1"
        )
        criar_local(self.condominio, "G1")
        criar_local(self.condominio, "G2", tipo_local=tipo_g1)

        leitura = self._ler("Vazamento no G2.")

        self.assertEqual(leitura.local.nome, "G2")
        self.assertFalse(leitura.area.existe)

    def test_o_lugar_curto_nao_inventa_equipamento(self):
        """§9: lugar e tipo vêm do relato; o equipamento, só do cadastro."""
        criar_local(self.condominio, "G2")

        leitura = self._ler("Uma das lâmpadas da garagem do G2 está queimada.")

        self.assertEqual(leitura.local.nome, "G2")
        self.assertEqual(leitura.tipo_problema.valor, TipoProblema.ILUMINACAO)
        self.assertIsNone(leitura.equipamento)
        self.assertFalse(leitura.sugestoes["equipamento"].existe)


class RiscoTests(LeituraDoRelatoTests):
    """"Sem risco" é resposta declarada; risco ausente é pergunta em aberto."""

    def test_risco_declarado_vale_como_fato_do_relato(self):
        leitura = self._ler("Tem fumaça saindo do quadro de energia.")

        self.assertEqual(leitura.risco.valor, RiscoDemanda.INCENDIO)
        self.assertEqual(leitura.risco.origem, OrigemSugestao.RELATO)
        self.assertEqual(leitura.risco.confianca, Confianca.ALTA)

    def test_sem_risco_declarado_e_diferente_de_risco_nao_avaliado(self):
        declarado = self._ler("Vazamento pequeno na garagem, sem risco para ninguém.")
        em_aberto = self._ler("Vazamento pequeno na garagem.")

        self.assertEqual(declarado.risco.valor, RiscoDemanda.SEM_RISCO)
        self.assertEqual(declarado.risco.origem, OrigemSugestao.RELATO)
        self.assertFalse(em_aberto.risco.existe)


class RecorrenciaTests(LeituraDoRelatoTests):
    """Declaração e histórico são coisas diferentes — e continuam separadas."""

    def test_o_relato_que_diz_que_voltou_a_acontecer(self):
        leitura = self._ler("O portão social travou novamente.")

        self.assertEqual(leitura.recorrencia.valor, RecorrenciaDemanda.DECLARADA)
        self.assertEqual(leitura.recorrencia.origem, OrigemSugestao.RELATO)
        self.assertEqual(leitura.recorrencia.confianca, Confianca.ALTA)

    def test_o_historico_mostra_a_ocorrencia_anterior_no_mesmo_ponto(self):
        equipamento = criar_equipamento(self.condominio, "Portão social")
        criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Portão social travado",
            equipamento=equipamento,
        )

        leitura = self._ler("O portão social travou hoje.")

        self.assertEqual(leitura.recorrencia.valor, RecorrenciaDemanda.OBSERVADA)
        self.assertEqual(leitura.recorrencia.origem, OrigemSugestao.HISTORICO)
        self.assertEqual(leitura.recorrencia.confianca, Confianca.MEDIA)
        self.assertTrue(leitura.recorrencia.motivo)

    def test_a_propria_demanda_revista_nao_e_historico_dela_mesma(self):
        equipamento = criar_equipamento(self.condominio, "Portão social")
        anterior = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Portão social travado",
            equipamento=equipamento,
        )

        leitura = self._ler("O portão social travou hoje.", ignorar=anterior)

        self.assertFalse(leitura.recorrencia.existe)

    def test_ocorrencia_fora_da_janela_do_historico_nao_conta(self):
        equipamento = criar_equipamento(self.condominio, "Portão social")
        anterior = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Portão social travado",
            equipamento=equipamento,
        )
        Demanda.objects.filter(pk=anterior.pk).update(
            criado_em=timezone.now() - timedelta(days=derivacao.DIAS_DE_HISTORICO + 20)
        )

        leitura = self._ler("O portão social travou hoje.")

        self.assertFalse(leitura.recorrencia.existe)

    def test_o_que_o_relato_declara_ganha_do_que_o_historico_mostraria(self):
        equipamento = criar_equipamento(self.condominio, "Portão social")
        criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Portão social travado",
            equipamento=equipamento,
        )

        leitura = self._ler("O portão social travou novamente.")

        self.assertEqual(leitura.recorrencia.valor, RecorrenciaDemanda.DECLARADA)
        self.assertEqual(leitura.recorrencia.origem, OrigemSugestao.RELATO)


class LeituraSemanticaTests(LeituraDoRelatoTests):
    """A leitura semântica ajuda; não decide sozinha nem contraria o relato (§6-C)."""

    RELATO = "Preciso de ajuda com uma situação estranha aqui."

    def _com_resposta(self, resposta, relato=None, **kwargs):
        with patch.object(llm, "pedir_json", return_value=resposta):
            return interpretacao.interpretar(
                relato or self.RELATO, condominio=self.condominio, **kwargs
            )

    def test_sem_servico_o_resultado_e_exatamente_o_das_regras(self):
        relato = "A água está descendo pela parede do G2."

        sem_servico = self._com_resposta(None, relato=relato)
        pelas_regras = self._ler(relato)

        self.assertEqual(sem_servico.registro(), pelas_regras.registro())

    def test_a_leitura_semantica_preenche_o_que_as_regras_deixaram_em_aberto(self):
        local = criar_local(self.condominio, "Garagem", TipoLocal.GARAGEM)
        equipamento = criar_equipamento(self.condominio, "Portão social")

        leitura = self._com_resposta(
            {
                "local": "Garagem",
                "equipamento": "Portão social",
                "tipo_problema": TipoProblema.ELETRICA,
                "impacto": ImpactoDemanda.COLETIVO,
                "risco": RiscoDemanda.ELETRICO,
                "recorrencia": RecorrenciaDemanda.DECLARADA,
            }
        )

        self.assertEqual(leitura.local, local)
        self.assertEqual(leitura.equipamento, equipamento)
        self.assertEqual(leitura.tipo_problema.valor, TipoProblema.ELETRICA)
        self.assertEqual(leitura.impacto.valor, ImpactoDemanda.COLETIVO)
        self.assertEqual(leitura.risco.valor, RiscoDemanda.ELETRICO)
        self.assertEqual(leitura.recorrencia.valor, RecorrenciaDemanda.DECLARADA)

    def test_o_que_a_leitura_semantica_propoe_e_sugestao_a_confirmar(self):
        leitura = self._com_resposta({"tipo_problema": TipoProblema.ELETRICA})

        proposta = leitura.tipo_problema
        self.assertEqual(proposta.origem, OrigemSugestao.EITHRA)
        self.assertEqual(proposta.confianca, Confianca.MEDIA)
        self.assertEqual(proposta.motivo, interpretacao.MOTIVO_DA_LEITURA)

    def test_local_e_equipamento_fora_do_cadastro_sao_descartados(self):
        criar_local(self.condominio, "Garagem")
        criar_equipamento(self.condominio, "Portão social")

        leitura = self._com_resposta(
            {"local": "Sótão do zelador", "equipamento": "Guindaste de obra"}
        )

        self.assertIsNone(leitura.local)
        self.assertIsNone(leitura.equipamento)

    def test_valor_fora_do_vocabulario_do_dominio_e_descartado(self):
        leitura = self._com_resposta(
            {
                "tipo_problema": "encanamento",
                "recorrencia": RecorrenciaDemanda.OBSERVADA,
            }
        )

        self.assertFalse(leitura.tipo_problema.existe)
        # "observada" é o que o histórico mostra: o serviço não decide por ele.
        self.assertFalse(leitura.recorrencia.existe)

    def test_o_que_as_regras_leram_nao_e_reescrito(self):
        criar_local(self.condominio, "Garagem", TipoLocal.GARAGEM)

        leitura = self._com_resposta(
            {"tipo_problema": TipoProblema.ELETRICA},
            relato="Vazamento na garagem.",
        )

        self.assertEqual(leitura.tipo_problema.valor, TipoProblema.VAZAMENTO)
        self.assertEqual(leitura.tipo_problema.origem, OrigemSugestao.RELATO)

    def test_o_peso_da_demanda_nao_e_assunto_da_leitura_semantica(self):
        leitura = self._com_resposta(
            {"tipo_problema": TipoProblema.ELETRICA},
            relato="O portão social parou.",
        )

        self.assertEqual(leitura.prioridade, PrioridadeDemanda.ALTA)
        self.assertEqual(leitura.motivo, "portão fora de operação")


class ClassificacaoNoRegistroTests(TestCase):
    """O relato classifica a demanda no registro, e a origem fica guardada (§6-D, §6-F)."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio)
        self.client.force_login(self.gestao)

    def _postar(self, relato, **extra):
        dados = {"condominio": str(self.condominio.pk), "relato": relato}
        dados.update(extra)
        return self.client.post(reverse("demandas:criar"), dados)

    def test_o_registro_guarda_o_que_o_relato_sustentou(self):
        equipamento = criar_equipamento(self.condominio, "Elevador de serviço")

        resposta = self._postar("O elevador de serviço parou novamente.")

        self.assertEqual(resposta.status_code, 302)
        demanda = Demanda.objects.get()
        self.assertEqual(demanda.equipamento, equipamento)
        self.assertEqual(demanda.tipo_problema, TipoProblema.ACESSO)
        self.assertEqual(demanda.impacto, ImpactoDemanda.EQUIPAMENTO)
        # A prioridade não é lida do relato: sem escolha de quem registra, ela
        # fica no padrão do domínio, mesmo quando o relato descreve uma parada.
        self.assertEqual(demanda.prioridade, PrioridadeDemanda.NORMAL)
        # A recorrência não vira coluna: ela é leitura do histórico, e continua
        # sendo calculada na hora, sem virar fato guardado (§6-E).

    def test_a_sugestao_fica_guardada_com_valor_origem_e_confianca(self):
        criar_equipamento(self.condominio, "Portão social")

        self._postar("O portão social travou e ficou aberto.")
        demanda = Demanda.objects.get()

        sugerido = demanda.classificacao_sugerida
        self.assertEqual(
            sugerido["equipamento"],
            {
                "valor": "Portão social",
                "origem": OrigemSugestao.EITHRA,
                "confianca": Confianca.ALTA,
            },
        )
        self.assertEqual(
            sugerido["risco"],
            {
                "valor": RiscoDemanda.SEGURANCA,
                "origem": OrigemSugestao.EITHRA,
                "confianca": Confianca.MEDIA,
            },
        )
        # O que a demanda passou a valer continua ao lado do que foi sugerido.
        self.assertEqual(demanda.risco, RiscoDemanda.SEGURANCA)

    def test_o_que_nao_foi_identificado_nao_vira_classificacao(self):
        self._postar("Está fazendo um barulho estranho.")
        demanda = Demanda.objects.get()

        self.assertEqual(demanda.tipo_problema, TipoProblema.RUIDO)
        self.assertIsNone(demanda.equipamento)
        self.assertIsNone(demanda.risco)
        self.assertNotIn("risco", demanda.classificacao_sugerida)
        self.assertNotIn("equipamento", demanda.classificacao_sugerida)

    def test_as_informacoes_mostram_a_sugestao_com_a_origem(self):
        criar_equipamento(self.condominio, "Portão social")

        resposta = self._postar("O portão social travou.", wizard="descricao")

        self.assertEqual(resposta.status_code, 200)
        identificados = {
            item["rotulo"]: item
            for item in resposta.context["sugestoes"]["identificados"]
        }
        self.assertEqual(identificados["Equipamento"]["valor"], "Portão social")
        self.assertEqual(
            identificados["Equipamento"]["origem"], OrigemSugestao.EITHRA.label
        )
        # A etapa apresenta a leitura como resultado — o lugar com o tipo de
        # problema, o peso e o prazo, o responsável, e o equipamento só quando o
        # cadastro o confirmou. O ajuste dos campos fica atrás de uma ação
        # discreta, e não compete com a síntese.
        conteudo = resposta.content.decode()
        self.assertIn("Informações identificadas", conteudo)
        self.assertIn("Equipamento: Portão social", conteudo)
        self.assertIn("Ajustar informações", conteudo)

    def test_as_informacoes_nao_despejam_a_confianca_na_tela(self):
        criar_equipamento(self.condominio, "Portão social")

        resposta = self._postar("O portão social travou.", wizard="descricao")

        # A firmeza da leitura é do sistema: ela decide o que preencher, o que
        # sugerir e o que perguntar, e não é assunto de quem usa a tela.
        self.assertNotIn("confianca", resposta.content.decode())

    def test_a_revisao_mostra_a_mesma_classificacao(self):
        criar_equipamento(self.condominio, "Portão social")

        resposta = self._postar(
            "O portão social travou e ficou aberto.", wizard="informacoes"
        )

        self.assertEqual(resposta.context["registro_etapa"], "revisao")
        identificados = {
            item["rotulo"]: item["valor"]
            for item in resposta.context["revisao"]["identificados"]
        }
        self.assertEqual(identificados["Equipamento"], "Portão social")
        self.assertEqual(identificados["Tipo de problema"], TipoProblema.ACESSO.label)
        self.assertEqual(identificados["Risco"], RiscoDemanda.SEGURANCA.label)

    def test_a_edicao_estrutural_nao_reclassifica_a_demanda(self):
        criar_equipamento(self.condominio, "Portão social")
        self._postar("O portão social travou.")
        demanda = Demanda.objects.get()

        resposta = self.client.post(
            reverse("demandas:editar", args=[demanda.pk]),
            {
                "relato": "Vazamento de água na garagem.",
                "titulo": "Vazamento na garagem",
                "prioridade": PrioridadeDemanda.NORMAL,
            },
        )

        self.assertEqual(resposta.status_code, 302)
        demanda.refresh_from_db()
        self.assertEqual(demanda.tipo_problema, TipoProblema.ACESSO)
        self.assertEqual(demanda.descricao, "Vazamento de água na garagem.")
        self.assertEqual(demanda.titulo, "Vazamento na garagem")
