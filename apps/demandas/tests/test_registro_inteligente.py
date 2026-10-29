"""Registro inteligente: quem relata o fato não classifica a demanda.

O princípio é um só, e este arquivo o prende por partes: a pessoa conta o que
aconteceu; o EITHRA lê, propõe a classificação e aplica a política de prazo; a
tela mostra o que foi identificado, para conferência. A pessoa só intervém
quando há ambiguidade real, quando falta informação ou quando quer corrigir.

Nenhuma regra crítica vive no navegador. A decisão de avançar depois da fala, a
classificação, a prioridade e o prazo são do domínio: o JavaScript, quando
existe, apenas aperta o mesmo Continuar que a pessoa apertaria.
"""
import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Equipamento, LocalOperacional
from apps.condominios.constants import Papel
from apps.core import audio, llm
from apps.demandas import derivacao, interpretacao
from apps.demandas.constants import PrioridadeDemanda, TipoProblema
from apps.demandas.models import Demanda
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_equipamento,
    criar_local,
    criar_pessoa,
    criar_vinculo,
)

# O relato do §4: a saudação abre a fala, o fato operacional vem depois.
RELATO_COM_SAUDACAO = (
    "Olá, boa tarde, o portão externo está fazendo barulho quando abre."
)

CONFIGURADO = {
    "EITHRA_LLM_API_KEY": "chave-de-teste-do-provedor",
    "EITHRA_LLM_BASE_URL": "https://provedor.invalido/v1",
    "EITHRA_AUDIO_MODEL": "modelo-de-transcricao",
}


class RespostaFalsa:
    """Resposta mínima do provedor, usada como gerenciador de contexto."""

    def __init__(self, corpo=b"{}"):
        self._corpo = corpo

    def read(self):
        return self._corpo

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def corpo_json(dados) -> bytes:
    return json.dumps(dados).encode("utf-8")


def recado(conteudo=b"RIFF....WEBM", nome="relato.webm", tipo="audio/webm"):
    return SimpleUploadedFile(nome, conteudo, content_type=tipo)


class SaudacaoNoRelatoTests(SimpleTestCase):
    """§4: a saudação abre o relato e não é o fato — o assunto é o problema."""

    def test_a_saudacao_sai_da_frente_do_assunto(self):
        assunto = derivacao.assunto_do_relato(RELATO_COM_SAUDACAO)
        normalizado = derivacao._normalizado(assunto)

        self.assertNotIn("ola", normalizado)
        self.assertNotIn("boa tarde", normalizado)
        self.assertIn("portao externo", normalizado)
        self.assertIn("barulho", normalizado)

    def test_so_cumprimento_nao_sustenta_o_registro(self):
        self.assertEqual(derivacao.assunto_do_relato("Olá, boa tarde."), "")
        self.assertFalse(derivacao.sustenta_registro("Olá, boa tarde."))
        self.assertFalse(derivacao.sustenta_registro(""))

    def test_o_cumprimento_nao_reescreve_o_relato_guardado(self):
        # O assunto perde a abertura; o relato, esse, nunca é alterado.
        leitura = derivacao.interpretar(RELATO_COM_SAUDACAO)
        self.assertTrue(leitura.assunto)
        self.assertNotEqual(leitura.assunto, RELATO_COM_SAUDACAO)

    def test_a_frase_que_nao_e_so_cumprimento_continua_no_relato(self):
        relato = "Boa tarde para todos, o portão ficou aberto."

        assunto = derivacao._normalizado(derivacao.assunto_do_relato(relato))

        self.assertTrue(assunto.startswith("boa tarde para todos"))


class FluxoDeVozTests(TestCase):
    """§1: a fala válida leva às Informações; a que não basta fica na Descrição."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    def _ditar(self, arquivo=None, **extra):
        dados = {"condominio": str(self.condominio.pk)}
        if arquivo is not None:
            dados["audio"] = arquivo
        dados.update(extra)
        return self.client.post(reverse("demandas:transcrever"), dados)

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_a_fala_valida_manda_seguir(self, urlopen):
        urlopen.return_value = RespostaFalsa(
            corpo_json({"text": "Vazamento na garagem do bloco A."})
        )

        resposta = self._ditar(recado())

        self.assertEqual(
            resposta.json(),
            {"texto": "Vazamento na garagem do bloco A.", "avancar": True},
        )
        self.assertEqual(Demanda.objects.count(), 0)

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_a_fala_sem_fato_nao_manda_seguir(self, urlopen):
        """Só o cumprimento transcrito não permite avançar: a descrição espera."""
        urlopen.return_value = RespostaFalsa(corpo_json({"text": "Olá, boa tarde."}))

        resposta = self._ditar(recado())

        self.assertEqual(resposta.json()["texto"], "Olá, boa tarde.")
        self.assertFalse(resposta.json()["avancar"])

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_a_fala_que_nao_deu_para_ouvir_nao_traz_sinal_de_seguir(self, urlopen):
        urlopen.return_value = RespostaFalsa(corpo_json({"text": ""}))

        resposta = self._ditar(recado())

        self.assertNotIn("avancar", resposta.json())
        self.assertEqual(resposta.json()["mensagem"], audio.MENSAGEM_SEM_FALA)

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_a_falha_do_servico_nao_traz_sinal_de_seguir(self, urlopen):
        urlopen.side_effect = OSError("rede fora")

        resposta = self._ditar(recado())

        self.assertNotIn("avancar", resposta.json())
        self.assertEqual(resposta.json()["mensagem"], audio.MENSAGEM_FALHA)

    def test_o_avancar_e_decidido_no_backend_e_so_seguido_pelo_controlador(self):
        """§14: o JavaScript não decide — ele reage ao sinal do servidor."""
        fonte = (
            Path(settings.BASE_DIR) / "static" / "js" / "eithra-ditar.js"
        ).read_text(encoding="utf-8")

        self.assertIn("if (leitura.avancar)", fonte)
        self.assertIn("seguirComoSeApertasseContinuar", fonte)


class PipelineUnicoTests(TestCase):
    """§2: texto digitado e texto transcrito seguem o mesmo caminho."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    def _relatar(self, relato):
        return self.client.post(
            reverse("demandas:criar"),
            {
                "condominio": str(self.condominio.pk),
                "relato": relato,
                "wizard": "descricao",
            },
        )

    def _ditar(self, arquivo):
        return self.client.post(
            reverse("demandas:transcrever"),
            {"condominio": str(self.condominio.pk), "audio": arquivo},
        )

    def test_o_relato_inteiro_chega_ao_leitor(self):
        """A leitura recebe o relato completo — a saudação não é removida dele."""
        with patch.object(
            interpretacao, "interpretar", wraps=interpretacao.interpretar
        ) as leitor:
            self._relatar(RELATO_COM_SAUDACAO)

        self.assertEqual(leitor.call_args.args[0], RELATO_COM_SAUDACAO)

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_a_fala_e_o_texto_chegam_ao_leitor_com_o_mesmo_texto(self, urlopen):
        urlopen.return_value = RespostaFalsa(
            corpo_json({"text": RELATO_COM_SAUDACAO})
        )
        texto_da_fala = self._ditar(recado()).json()["texto"]

        with patch.object(
            interpretacao, "interpretar", wraps=interpretacao.interpretar
        ) as leitor:
            self._relatar(RELATO_COM_SAUDACAO)
            self._relatar(texto_da_fala)

        self.assertEqual(texto_da_fala, RELATO_COM_SAUDACAO)
        relatos = [chamada.args[0] for chamada in leitor.call_args_list]
        self.assertEqual(relatos, [RELATO_COM_SAUDACAO, RELATO_COM_SAUDACAO])

    @override_settings(**CONFIGURADO)
    @patch("apps.core.audio.urllib.request.urlopen")
    def test_o_lugar_curto_e_lido_igual_pelo_texto_e_pela_fala(self, urlopen):
        """§11: o nome curto chega pela fala e pelo texto ao mesmo lugar."""
        criar_local(self.condominio, "G2")
        fala = "Uma das lâmpadas da garagem do G2 está queimada."
        urlopen.return_value = RespostaFalsa(corpo_json({"text": fala}))
        texto_da_fala = self._ditar(recado()).json()["texto"]

        digitado = self._relatar(fala).context["sugestoes"]["local"]["valor"]
        falado = self._relatar(texto_da_fala).context["sugestoes"]["local"]["valor"]

        self.assertEqual(digitado, "G2")
        self.assertEqual(falado, "G2")


class ClassificacaoAutomaticaTests(TestCase):
    """§3 e §5: o EITHRA propõe a classificação; a pessoa confere, não classifica."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    def _descricao(self, relato, **extra):
        dados = {
            "condominio": str(self.condominio.pk),
            "relato": relato,
            "wizard": "descricao",
        }
        dados.update(extra)
        return self.client.post(reverse("demandas:criar"), dados)

    def _informacoes(self, relato, **extra):
        dados = {
            "condominio": str(self.condominio.pk),
            "relato": relato,
            "wizard": "informacoes",
        }
        dados.update(extra)
        return self.client.post(reverse("demandas:criar"), dados)

    def _registrar(self, relato, **extra):
        dados = {"condominio": str(self.condominio.pk), "relato": relato}
        dados.update(extra)
        return self.client.post(reverse("demandas:criar"), dados)

    def test_a_saudacao_nao_vira_assunto_e_o_fato_e_classificado(self):
        criar_equipamento(self.condominio, "Portão externo")

        sugestoes = self._descricao(RELATO_COM_SAUDACAO).context["sugestoes"]

        self.assertNotIn("Olá", sugestoes["assunto"]["valor"])
        self.assertIn("portão externo", sugestoes["assunto"]["valor"])
        self.assertEqual(sugestoes["equipamento"]["valor"], "Portão externo")
        # A taxonomia real do sistema lê "portão" como falha de acesso/automação.
        self.assertEqual(
            sugestoes["tipo_problema"]["valor"], TipoProblema.ACESSO.label
        )

    def test_a_etapa_de_informacoes_apresenta_a_leitura_como_sintese(self):
        resposta = self._descricao(RELATO_COM_SAUDACAO)

        # A etapa não é um formulário de classificação: ela apresenta o que o
        # relato sustentou, e o ajuste fica atrás de uma ação discreta.
        self.assertContains(resposta, "Informações identificadas")
        self.assertContains(resposta, "Revise apenas se algo precisar ser corrigido.")
        self.assertContains(resposta, "Ajustar informações")

    def test_o_usuario_nao_precisa_escolher_o_tipo_para_avancar(self):
        criar_local(self.condominio, "Garagem")

        resposta = self._informacoes("Vazamento na garagem do bloco A.")

        self.assertEqual(resposta.context["registro_etapa"], "revisao")
        identificados = {
            item["rotulo"]: item["valor"]
            for item in resposta.context["revisao"]["identificados"]
        }
        self.assertEqual(
            identificados["Tipo de problema"], TipoProblema.VAZAMENTO.label
        )

    def test_a_classificacao_proposta_nasce_com_a_demanda(self):
        criar_equipamento(self.condominio, "Portão externo")

        resposta = self._registrar(RELATO_COM_SAUDACAO)

        self.assertEqual(resposta.status_code, 302)
        demanda = Demanda.objects.get()
        self.assertEqual(demanda.tipo_problema, TipoProblema.ACESSO)
        self.assertEqual(demanda.equipamento.nome, "Portão externo")


class PrioridadeAutomaticaTests(TestCase):
    """§6: o peso continua sendo do sistema — não é decisão manual obrigatória."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    def _registrar(self, relato, **extra):
        dados = {"condominio": str(self.condominio.pk), "relato": relato}
        dados.update(extra)
        return self.client.post(reverse("demandas:criar"), dados)

    def test_a_prioridade_e_derivada_do_relato(self):
        criar_equipamento(self.condominio, "Portão social")

        self._registrar("O portão social parou.")

        demanda = Demanda.objects.get()
        self.assertEqual(demanda.prioridade, PrioridadeDemanda.ALTA)

    def test_o_que_o_navegador_manda_nao_escolhe_o_peso(self):
        """A prioridade não vem da tela: o relato é quem sustenta o peso."""
        criar_equipamento(self.condominio, "Portão social")

        self._registrar("O portão social parou.", prioridade=PrioridadeDemanda.BAIXA)

        self.assertEqual(Demanda.objects.get().prioridade, PrioridadeDemanda.ALTA)


class PrazoPelaPoliticaTests(TestCase):
    """§7, §8 e §9: o prazo é da política determinística — a IA não inventa data."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)
        self.hoje = timezone.localdate()

    def _descricao(self, relato, **extra):
        dados = {
            "condominio": str(self.condominio.pk),
            "relato": relato,
            "wizard": "descricao",
        }
        dados.update(extra)
        return self.client.post(reverse("demandas:criar"), dados)

    def _registrar(self, relato, **extra):
        dados = {"condominio": str(self.condominio.pk), "relato": relato}
        dados.update(extra)
        return self.client.post(reverse("demandas:criar"), dados)

    def test_sem_regra_configurada_o_prazo_nao_e_inventado(self):
        leitura = derivacao.interpretar(
            RELATO_COM_SAUDACAO, condominio=self.condominio, hoje=self.hoje
        )

        self.assertIsNone(leitura.prazo)
        self.assertEqual(leitura.motivo_do_prazo, "")

        sugestoes = self._descricao(RELATO_COM_SAUDACAO).context["sugestoes"]
        self.assertEqual(sugestoes["prazo"]["valor"], "")

        self._registrar(RELATO_COM_SAUDACAO)
        self.assertIsNone(Demanda.objects.get().prazo)

    @override_settings(EITHRA_DEMANDAS_PRAZO_DIAS={"normal": 15, "alta": 5})
    def test_o_prazo_vem_dos_dias_configurados_para_a_prioridade(self):
        leitura = derivacao.interpretar(
            RELATO_COM_SAUDACAO, condominio=self.condominio, hoje=self.hoje
        )

        self.assertEqual(leitura.prioridade, PrioridadeDemanda.NORMAL)
        self.assertEqual(leitura.prazo, self.hoje + timedelta(days=15))
        self.assertIn("15 dias", leitura.motivo_do_prazo)

    @override_settings(EITHRA_DEMANDAS_PRAZO_DIAS={"normal": 15, "alta": 5})
    def test_a_data_do_registro_mais_a_regra_viram_o_prazo_da_demanda(self):
        self._registrar(RELATO_COM_SAUDACAO)

        self.assertEqual(
            Demanda.objects.get().prazo, self.hoje + timedelta(days=15)
        )

    @override_settings(EITHRA_DEMANDAS_PRAZO_DIAS={"normal": 15})
    def test_a_tela_diz_que_o_prazo_veio_da_regra(self):
        esperado = (self.hoje + timedelta(days=15)).strftime("%d/%m/%Y")

        sugestoes = self._descricao(RELATO_COM_SAUDACAO).context["sugestoes"]

        self.assertEqual(sugestoes["prazo"]["valor"], esperado)
        self.assertEqual(sugestoes["prazo"]["origem"], "Derivado por regra")

    def test_a_leitura_semantica_nao_opina_sobre_data_nem_peso(self):
        """A IA entende o relato; ela não define SLA nem prioridade (§8)."""
        resposta = {
            "tipo_problema": TipoProblema.ELETRICA,
            "prazo": "2020-01-01",
            "prioridade": "urgente",
        }

        with patch.object(llm, "pedir_json", return_value=resposta):
            leitura = interpretacao.interpretar(
                "Preciso de ajuda com uma situação estranha aqui.",
                condominio=self.condominio,
                hoje=self.hoje,
            )

        self.assertIsNone(leitura.prazo)
        self.assertEqual(leitura.prioridade, PrioridadeDemanda.NORMAL)
        self.assertNotIn("prazo", interpretacao.VOCABULARIO)
        self.assertNotIn("prioridade", interpretacao.VOCABULARIO)


class CorrecaoHumanaTests(TestCase):
    """§11: a correção da pessoa vale para aquela demanda — e não vira regra."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    def _registrar(self, relato, **extra):
        dados = {"condominio": str(self.condominio.pk), "relato": relato}
        dados.update(extra)
        return self.client.post(reverse("demandas:criar"), dados)

    def test_o_tipo_corrigido_na_tela_vale_no_registro(self):
        self._registrar(
            "Vazamento na garagem.", tipo_problema=TipoProblema.ELETRICA
        )

        self.assertEqual(Demanda.objects.get().tipo_problema, TipoProblema.ELETRICA)

    def test_a_correcao_de_uma_demanda_nao_vira_regra_das_outras(self):
        self._registrar(
            "Vazamento na garagem.", tipo_problema=TipoProblema.ELETRICA
        )
        self._registrar("Vazamento na garagem.")

        corrigida, seguinte = Demanda.objects.order_by("criado_em")
        self.assertEqual(corrigida.tipo_problema, TipoProblema.ELETRICA)
        self.assertEqual(seguinte.tipo_problema, TipoProblema.VAZAMENTO)

    @override_settings(EITHRA_DEMANDAS_PRAZO_DIAS={"normal": 15})
    def test_o_prazo_corrigido_prevalece_sobre_a_regra(self):
        escolhido = date(2026, 12, 1)

        self._registrar("O portão externo está fazendo barulho quando abre.",
                        prazo=escolhido.isoformat())

        self.assertEqual(Demanda.objects.get().prazo, escolhido)


class NaoInventaTests(TestCase):
    """§4 e §13: o que não está no cadastro não é inventado."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    def test_o_equipamento_incerto_nao_e_inventado(self):
        criar_equipamento(self.condominio, "Portão social")

        leitura = derivacao.interpretar(
            "A máquina do guindaste parou.", condominio=self.condominio
        )

        self.assertIsNone(leitura.equipamento)
        self.assertFalse(leitura.sugestoes["equipamento"].existe)

    def test_o_registro_incerto_nao_cria_cadastro_no_condominio(self):
        antes_equipamentos = Equipamento.objects.count()
        antes_locais = LocalOperacional.objects.count()

        self.client.post(
            reverse("demandas:criar"),
            {
                "condominio": str(self.condominio.pk),
                "relato": "A máquina do guindaste no galpão do fundo parou.",
            },
        )

        self.assertEqual(Equipamento.objects.count(), antes_equipamentos)
        self.assertEqual(LocalOperacional.objects.count(), antes_locais)
        self.assertIsNone(Demanda.objects.get().equipamento)


class SinteseDaEtapaDeInformacoesTests(TestCase):
    """§6, §8, §9 e §11: a etapa apresenta o resultado — não abre um formulário.

    O relato da lâmpada queimada é o exemplo de aceite: o EITHRA lê o lugar e o
    tipo, calcula o peso e o prazo pela política, e o equipamento — que o
    cadastro não tem — simplesmente não aparece. Nada disso é perguntado; a
    correção fica atrás de uma ação discreta.
    """

    LAMPADA = "Uma das lâmpadas da Garagem G2 está queimada."

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Maria Fernanda Duccini")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    def _descricao(self, relato, **extra):
        dados = {
            "condominio": str(self.condominio.pk),
            "relato": relato,
            "wizard": "descricao",
        }
        dados.update(extra)
        return self.client.post(reverse("demandas:criar"), dados)

    @override_settings(EITHRA_DEMANDAS_PRAZO_DIAS={"normal": 20})
    def test_a_sintese_do_exemplo_de_aceite(self):
        criar_local(self.condominio, "Garagem G2")
        hoje = timezone.localdate()

        resposta = self._descricao(self.LAMPADA)
        sugestoes = resposta.context["sugestoes"]

        self.assertEqual(sugestoes["local"]["valor"], "Garagem G2")
        self.assertEqual(
            sugestoes["tipo_problema"]["valor"], TipoProblema.ILUMINACAO.label
        )
        self.assertEqual(sugestoes["prioridade"]["valor"], PrioridadeDemanda.NORMAL.label)
        prazo = (hoje + timedelta(days=20)).strftime("%d/%m/%Y")
        self.assertEqual(sugestoes["prazo"]["texto"], f"Prazo 20 dias · {prazo}")
        self.assertEqual(sugestoes["prazo"]["origem"], "Derivado por regra")
        self.assertEqual(sugestoes["responsavel"]["valor"], "Maria Fernanda Duccini")

        # A síntese é resultado, não formulário: nada de classificar.
        self.assertContains(resposta, "Informações identificadas")
        self.assertContains(resposta, "Revise apenas se algo precisar ser corrigido.")
        self.assertContains(resposta, "Ajustar informações")
        self.assertContains(resposta, "Prioridade Normal")

        # O peso não é perguntado em lugar nenhum do wizard: quem sustenta é o
        # relato, e a tela só o apresenta.
        wizard = resposta.content.decode().split('id="registrar-demanda"', 1)[1]
        self.assertNotIn('name="prioridade"', wizard)

    def test_sem_equipamento_a_sintese_nao_mostra_linha_nem_alarme(self):
        """§2 e §9: ausência de equipamento é resposta comum, não campo pendente."""
        criar_local(self.condominio, "Garagem G2")

        sugestoes = self._descricao(self.LAMPADA).context["sugestoes"]

        self.assertEqual(sugestoes["equipamento"]["valor"], "")
        self.assertNotContains(self._descricao(self.LAMPADA), "Equipamento:")

    def test_a_sintese_nao_repete_a_explicacao_de_cada_campo(self):
        """§8: a origem e o motivo não competem com o resultado."""
        criar_local(self.condominio, "Garagem G2")

        resposta = self._descricao(self.LAMPADA)

        for ruido in (
            "Identificado no relato",
            "Pode ser ajustado antes do registro",
            "Sinal em iluminação",
            "Quem registrou também pode acompanhar esta demanda",
            "Só é preenchido com base objetiva",
        ):
            self.assertNotContains(resposta, ruido)

    def test_o_ajuste_por_excecao_traz_os_campos_para_corrigir(self):
        """§7 e §10: quem precisa corrigir encontra os campos — recolhidos."""
        criar_local(self.condominio, "Garagem G2")

        resposta = self._descricao(self.LAMPADA)

        self.assertContains(resposta, 'id="eithra-ajustar-informacoes"')
        for campo in ("id_titulo", "id_local_texto", "id_equipamento",
                      "id_tipo_problema", "id_responsavel", "id_prazo"):
            self.assertContains(resposta, campo)


class RegrasForaDoJavascriptTests(SimpleTestCase):
    """§14: nenhuma regra crítica de demanda mora no JavaScript."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fonte = (
            Path(settings.BASE_DIR) / "static" / "js" / "eithra-ditar.js"
        ).read_text(encoding="utf-8")

    def test_o_controlador_nao_conhece_prazo_nem_prioridade(self):
        minusculo = self.fonte.lower()

        self.assertNotIn("prazo", minusculo)
        self.assertNotIn("prioridade", minusculo)
        self.assertNotIn("EITHRA_DEMANDAS_PRAZO_DIAS", self.fonte)

    def test_o_controlador_nao_classifica(self):
        minusculo = self.fonte.lower()

        self.assertNotIn("tipo_problema", minusculo)
        self.assertNotIn("classificacao", minusculo)
