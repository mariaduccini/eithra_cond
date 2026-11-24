"""A próxima ação: escolhas rápidas sem fechar a resposta.

O domínio já responde *quem está com a bola* e *o que acontece agora* com os
campos que sempre teve — responsável, espera, próxima ação, previsão, histórico.
O que faltava era o atalho de quem repete a mesma frase todo dia. Estes testes
prendem o contrato: as sugestões são oferecidas como ``<datalist>``, o campo
continua aceitando qualquer texto, e a próxima ação passa a poder ser ditada
pelo mesmo microfone dos demais campos.

Nada aqui cria objeto novo: a sugestão é só apresentação, e o que se guarda
continua sendo a ``proxima_acao`` que o domínio já tinha.
"""
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.demandas.forms import SUGESTOES_DE_PROXIMA_ACAO, AcompanhamentoDaDemandaForm
from apps.demandas.services import criar_demanda
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)

# O provedor de transcrição configurado: é o bastante para a tela revelar o
# microfone. O serviço externo nunca é chamado — só se verifica o que a janela
# oferece quando a voz existe.
COM_VOZ = {
    "EITHRA_LLM_API_KEY": "chave-de-teste-do-provedor",
    "EITHRA_AUDIO_MODEL": "modelo-de-transcricao",
}

# Sem modelo de transcrição, a entrada por voz não está disponível — e isso não
# pode tirar as sugestões da janela.
SEM_VOZ = {"EITHRA_LLM_API_KEY": "chave-de-teste-do-provedor", "EITHRA_AUDIO_MODEL": ""}

TEXTO_LIVRE = "Falar com o síndico sobre a vistoria do elevador"


class SugestoesDaProximaAcaoTests(SimpleTestCase):
    """A lista é atalho, não catálogo — o campo continua sendo texto."""

    def test_o_campo_aponta_para_a_lista_de_sugestoes(self):
        formulario = AcompanhamentoDaDemandaForm()
        self.assertEqual(
            formulario.fields["proxima_acao"].widget.attrs.get("list"),
            "id_proxima_acao_lista",
        )

    def test_as_sugestoes_sao_texto_humano_unico(self):
        self.assertTrue(SUGESTOES_DE_PROXIMA_ACAO)
        for sugestao in SUGESTOES_DE_PROXIMA_ACAO:
            self.assertIsInstance(sugestao, str)
            self.assertEqual(sugestao, sugestao.strip())
            self.assertTrue(sugestao)
        self.assertEqual(
            len(SUGESTOES_DE_PROXIMA_ACAO), len(set(SUGESTOES_DE_PROXIMA_ACAO))
        )

    def test_a_sugestao_nao_fecha_o_campo(self):
        """Nada de escolha fechada: o campo segue aceitando qualquer texto."""
        campo = AcompanhamentoDaDemandaForm().fields["proxima_acao"]
        self.assertFalse(campo.required)
        self.assertIsNone(getattr(campo, "choices", None))


class JanelaDaProximaAcaoTests(TestCase):
    """A janela do acompanhamento oferece a lista e mantém o texto livre."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Fernanda")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)
        self.demanda = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Elevador parado no oitavo andar",
        )

    def _abrir(self):
        resposta = self.client.get(
            reverse("demandas:detalhe", args=[self.demanda.pk]),
            {"abrir": "acompanhamento"},
        )
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def _janela(self, resposta=None):
        html = (resposta or self._abrir()).content.decode()
        inicio = html.index('id="atualizar-acompanhamento"')
        return html[inicio : html.index("</form>", inicio)]

    def _salvar(self, **campos):
        dados = {
            "aguardando_tipo": "",
            "aguardando_descricao": "",
            "proxima_acao": "",
            "previsao": "",
            "impedimento": "",
            "responsavel_acompanhamento": "",
        }
        dados.update(campos)
        return self.client.post(
            reverse("demandas:acompanhar", args=[self.demanda.pk]), dados
        )

    # -- a lista aparece na janela -------------------------------------------

    def test_a_janela_oferece_as_sugestoes(self):
        janela = self._janela()
        self.assertIn('<datalist id="id_proxima_acao_lista">', janela)
        for sugestao in SUGESTOES_DE_PROXIMA_ACAO:
            self.assertIn(f'<option value="{sugestao}">', janela)

    def test_o_campo_aponta_para_a_lista_renderizada(self):
        janela = self._janela()
        self.assertIn('list="id_proxima_acao_lista"', janela)

    # -- o texto livre continua valendo ---------------------------------------

    def test_o_texto_livre_continua_sendo_aceito(self):
        resposta = self._salvar(proxima_acao=TEXTO_LIVRE)
        self.assertEqual(resposta.status_code, 302)
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.proxima_acao, TEXTO_LIVRE)

    def test_uma_sugestao_tambem_e_aceita(self):
        resposta = self._salvar(proxima_acao="Cobrar fornecedor")
        self.assertEqual(resposta.status_code, 302)
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.proxima_acao, "Cobrar fornecedor")

    # -- a voz entra pelo mesmo caminho ---------------------------------------

    @override_settings(**COM_VOZ)
    def test_com_a_voz_o_campo_pode_ser_ditado(self):
        janela = self._janela()
        self.assertIn('data-eithra-ditar-campo="#id_proxima_acao"', janela)
        self.assertIn(f"?demanda={self.demanda.pk}", janela)

    @override_settings(**SEM_VOZ)
    def test_sem_a_voz_a_lista_continua(self):
        janela = self._janela()
        self.assertNotIn('data-eithra-ditar-campo="#id_proxima_acao"', janela)
        self.assertIn('<datalist id="id_proxima_acao_lista">', janela)
