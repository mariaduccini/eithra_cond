"""Testes do desligamento da integração com o Gmail.

A pergunta é uma só, e vale para todos os caminhos:

    Com EITHRA_GMAIL_ATIVO=False, alguma coisa da integração com o Gmail
    ainda aparece ou roda?

A resposta esperada é sempre "não". O comando de sincronização não consulta
credencial, não toca a rede e não cria lote; o bloco de mensagens some da Home;
a tela de decisão de uma mensagem fica fora de alcance. A leitura do e-mail
passa a ser manual, como a supervisão pediu.

Nenhum teste aqui toca a rede: com a integração desligada, nada é chamado — e é
isso que se comprova.
"""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.cadastros.tests.apoio import criar_pessoa, criar_vinculo
from apps.condominios.constants import Papel
from apps.condominios.models import Condominio
from apps.core import email_acoes
from apps.core.constants import EstadoProcessamentoImportacao
from apps.core.models import ItemImportacaoHistorica, LoteImportacaoHistorica
from apps.core.tests.test_leitura_email import criar_item

TEXTO_DO_BLOCO = "Mensagens que pedem uma decisão"

TODOS_OS_CAMINHOS_DA_LEITURA = (
    "apps.core.gmail.obter_provedor",
    "apps.core.email_interpretacao.llm.pedir_json",
)


class ComandoDeSincronizacaoDesligadoTests(TestCase):
    """O comando vira um no-op quando a integração está desligada."""

    def setUp(self):
        self.condominio = Condominio.objects.create(nome="Condomínio de Teste")

    @override_settings(EITHRA_GMAIL_ATIVO=False)
    def test_comando_nao_sincroniza_e_avisa_no_log(self):
        saida = StringIO()
        with patch(TODOS_OS_CAMINHOS_DA_LEITURA[0]) as provedor, patch(
            TODOS_OS_CAMINHOS_DA_LEITURA[1]
        ) as ia:
            call_command(
                "sincronizar_gmail",
                condominio=str(self.condominio.pk),
                stdout=saida,
            )

        provedor.assert_not_called()
        ia.assert_not_called()
        self.assertEqual(LoteImportacaoHistorica.objects.count(), 0)
        self.assertIn("desligada", saida.getvalue())


class BlocoDoGmailNaHomeTests(TestCase):
    """O bloco de mensagens do Gmail some da Home quando a integração desliga."""

    def setUp(self):
        self.condominio = Condominio.objects.create(nome="Condomínio de Teste")
        self.sindica = criar_pessoa("sindica.gmail@exemplo.test", "Síndica")
        criar_vinculo(self.sindica, self.condominio, papel=Papel.SINDICA)
        self.item = self._aguardando()
        self.client.force_login(self.sindica)

    def _aguardando(self):
        """Um item lido pelo Gmail e parado em "aguardando confirmação"."""
        item = criar_item(self.condominio, assunto="Vazamento na garagem")
        objetos = ItemImportacaoHistorica.objects
        item = objetos.alterar_estado_processamento(
            item=item, novo_estado=EstadoProcessamentoImportacao.PROCESSANDO
        )
        return objetos.alterar_estado_processamento(
            item=item, novo_estado=EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO
        )

    @override_settings(EITHRA_GMAIL_ATIVO=True)
    def test_bloco_aparece_com_a_integracao_ligada(self):
        resposta = self.client.get(reverse("core:home"))
        self.assertContains(resposta, TEXTO_DO_BLOCO)

    @override_settings(EITHRA_GMAIL_ATIVO=False)
    def test_bloco_some_com_a_integracao_desligada(self):
        resposta = self.client.get(reverse("core:home"))
        self.assertNotContains(resposta, TEXTO_DO_BLOCO)

    def test_o_padrao_ja_e_desligado(self):
        """Sem override, o sistema nasce com a integração desligada."""
        resposta = self.client.get(reverse("core:home"))
        self.assertNotContains(resposta, TEXTO_DO_BLOCO)

    @override_settings(EITHRA_GMAIL_ATIVO=False)
    def test_as_pendencias_ficam_vazias_com_a_integracao_desligada(self):
        self.assertFalse(email_acoes.pendencias_para(self.sindica).exists())

    @override_settings(EITHRA_GMAIL_ATIVO=True)
    def test_as_pendencias_aparecem_com_a_integracao_ligada(self):
        self.assertTrue(email_acoes.pendencias_para(self.sindica).exists())

    @override_settings(EITHRA_GMAIL_ATIVO=False)
    def test_a_tela_da_mensagem_fica_fora_de_alcance_com_a_integracao_desligada(self):
        resposta = self.client.get(reverse("core:confirmacao", args=[self.item.pk]))
        self.assertEqual(resposta.status_code, 404)
