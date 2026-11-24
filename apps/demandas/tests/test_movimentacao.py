"""Registrar uma movimentação numa demanda que já existe — por texto ou por fala.

Contar o que aconteceu com uma demanda é a entrada; a movimentação é a saída.
Quem escreve e quem fala produzem o mesmo texto, a leitura do próprio relato
propõe apenas a consequência que ele sustenta, e nada muda de situação antes da
confirmação de quem registrou.

Estes testes prendem exatamente isso: o relato digitado e o relato ditado
chegam à mesma leitura, o fato entra no histórico, a conclusão é proposta —
nunca aplicada na leitura —, e uma confirmação produz uma única movimentação.

O serviço de transcrição nunca é chamado de verdade: a conversa HTTP é
substituída por uma resposta falsa, reaproveitando o que ``test_audio`` já usa.
Assim o que se verifica aqui é o que as Demandas fazem com o relato, não o que
o provedor faria com o áudio.
"""
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.cadastros.models import Fornecedor
from apps.condominios.constants import Papel
from apps.demandas.constants import StatusDemanda, TipoEventoDemanda
from apps.demandas.models import Demanda
from apps.demandas.services import alterar_status, criar_demanda
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)
from apps.demandas.tests.test_audio import (
    CONFIGURADO,
    SEM_LEITURA_SEMANTICA,
    RespostaFalsa,
    corpo_json,
    recado,
)
from apps.manutencoes.services import cancelar_manutencao, concluir_manutencao, criar_manutencao

RELATO_DO_FORNECEDOR = "A Atlas informou que virá amanhã às 14h."
RELATO_DE_CONCLUSAO = "Serviço concluído hoje. Elevador testado e funcionando."
RELATO_SEM_CONSEQUENCIA = "Ainda aguardando orçamento da empresa."
RELATO_AMBIGUO = "Falei com eles."


class CenarioDaMovimentacao:
    """Uma demanda aberta e uma pessoa que responde por ela.

    Reúne o ponto de partida e os dois jeitos de escrever a atualização — o
    texto digitado e a fala —, para que classes de teste diferentes partam do
    mesmo cenário sem repetir o preparo.
    """

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)
        self.demanda = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo="Elevador parado no oitavo andar",
        )

    # --- os dois jeitos de escrever a atualização --------------------------

    def _abrir(self, texto, **extra):
        """Primeira etapa: o relato volta com o que o EITHRA identificou."""
        dados = {"texto": texto, "etapa_atualizacao": "informacoes"}
        dados.update(extra)
        return self.client.post(
            reverse("demandas:atualizar", args=[self.demanda.pk]), dados
        )

    def _confirmar(self, texto, confirmacao, **extra):
        """Segunda etapa: é a confirmação que autoriza a movimentação."""
        dados = {
            "texto": texto,
            "etapa_atualizacao": "conclusao",
            "confirmacao": confirmacao,
        }
        dados.update(extra)
        return self.client.post(
            reverse("demandas:atualizar", args=[self.demanda.pk]), dados
        )

    def _ditar(self, texto, **extra):
        """Fala: o áudio vira texto no mesmo endereço que serve ao relato."""
        with override_settings(**CONFIGURADO):
            with patch("apps.core.audio.urllib.request.urlopen") as urlopen:
                urlopen.return_value = RespostaFalsa(corpo_json({"text": texto}))
                return self.client.post(
                    f"{reverse('demandas:transcrever')}?demanda={self.demanda.pk}",
                    {"audio": recado(), **extra},
                )

    def _atualizar(self, texto):
        """O caminho completo de uma atualização, do relato à confirmação."""
        etapa = self._abrir(texto)
        return self._confirmar(texto, etapa.context["confirmacao"]), etapa


class BaseDaMovimentacao(CenarioDaMovimentacao, TestCase):
    """A movimentação de uma demanda comum, pelo texto e pela fala."""

    # --- o fato sempre entra -------------------------------------------------

    def test_atualizacao_por_texto_entra_no_historico(self):
        resposta, _ = self._atualizar(RELATO_SEM_CONSEQUENCIA)

        self.assertEqual(resposta.status_code, 302)
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.status, StatusDemanda.ABERTA)

        evento = self.demanda.historico.get(tipo_evento=TipoEventoDemanda.OBSERVACAO)
        self.assertEqual(evento.descricao, RELATO_SEM_CONSEQUENCIA)
        self.assertEqual(evento.usuario, self.gestao)

    def test_atualizacao_por_audio_entra_no_historico(self):
        texto = self._ditar(RELATO_SEM_CONSEQUENCIA).json()["texto"]
        resposta, _ = self._atualizar(texto)

        self.assertEqual(resposta.status_code, 302)
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.status, StatusDemanda.ABERTA)

        evento = self.demanda.historico.get(tipo_evento=TipoEventoDemanda.OBSERVACAO)
        self.assertEqual(evento.descricao, RELATO_SEM_CONSEQUENCIA)
        self.assertEqual(evento.usuario, self.gestao)

    def test_texto_e_audio_chegam_a_mesma_leitura(self):
        """A fala vira texto antes de tudo: daí em diante não há duas rotas."""
        Fornecedor.objects.create(
            condominio=self.condominio, nome_razao_social="Atlas Elevadores"
        )

        por_texto = self._abrir(RELATO_DO_FORNECEDOR)
        por_audio = self._abrir(self._ditar(RELATO_DO_FORNECEDOR).json()["texto"])

        self.assertEqual(
            por_audio.context["resumo_atualizacao"],
            por_texto.context["resumo_atualizacao"],
        )
        self.assertIn(
            "Fornecedor reconhecido no cadastro: Atlas Elevadores",
            por_texto.context["resumo_atualizacao"]["identificado"],
        )

    # --- a leitura propõe, nunca aplica --------------------------------------

    def test_atualizacao_sem_consequencia_altera_apenas_o_historico(self):
        antes = self.demanda.historico.count()

        _, etapa = self._atualizar(RELATO_AMBIGUO)

        self.assertEqual(etapa.context["resumo_atualizacao"]["identificado"], [])
        self.assertFalse(etapa.context["resumo_atualizacao"]["propoe_conclusao"])
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.status, StatusDemanda.ABERTA)
        self.assertEqual(self.demanda.historico.count(), antes + 1)

    def test_a_conclusao_explicita_e_apenas_proposta_antes_da_confirmacao(self):
        etapa = self._abrir(RELATO_DE_CONCLUSAO)

        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.status, StatusDemanda.ABERTA)
        self.assertEqual(self.demanda.historico.count(), 1)
        self.assertTrue(etapa.context["resumo_atualizacao"]["propoe_conclusao"])
        self.assertEqual(
            etapa.context["resumo_atualizacao"]["consequencia"],
            "A demanda será concluída.",
        )

        self._confirmar(RELATO_DE_CONCLUSAO, etapa.context["confirmacao"])

        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.status, StatusDemanda.CONCLUIDA)

    def test_a_negacao_nao_e_lida_como_conclusao(self):
        etapa = self._abrir("O serviço ainda não foi concluído pela empresa.")

        self.assertFalse(etapa.context["resumo_atualizacao"]["propoe_conclusao"])
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.status, StatusDemanda.ABERTA)

    def test_a_confirmacao_usa_o_servico_oficial_de_status(self):
        """A transição não é escrita à mão: ela passa pelo domínio."""
        etapa = self._abrir(RELATO_DE_CONCLUSAO)

        with patch("apps.demandas.views.alterar_status", wraps=alterar_status) as oficial:
            self._confirmar(RELATO_DE_CONCLUSAO, etapa.context["confirmacao"])

        oficial.assert_called_once_with(
            demanda=self.demanda,
            usuario=self.gestao,
            novo_status=StatusDemanda.CONCLUIDA,
        )

    def test_relato_ambiguo_nao_inventa_dados(self):
        etapa = self._abrir(RELATO_AMBIGUO)
        resumo = etapa.context["resumo_atualizacao"]

        self.assertEqual(resumo["identificado"], [])
        self.assertEqual(
            resumo["consequencia"], "A demanda continua na mesma situação."
        )
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.status, StatusDemanda.ABERTA)

    def test_o_fornecedor_do_cadastro_e_reconhecido_sem_concluir(self):
        Fornecedor.objects.create(
            condominio=self.condominio, nome_razao_social="Atlas Elevadores"
        )

        etapa = self._abrir(RELATO_DO_FORNECEDOR)
        resumo = etapa.context["resumo_atualizacao"]

        self.assertIn(
            "Fornecedor reconhecido no cadastro: Atlas Elevadores",
            resumo["identificado"],
        )
        self.assertTrue(
            any(linha.startswith("Previsão informada:") for linha in resumo["identificado"])
        )
        self.assertFalse(resumo["propoe_conclusao"])

        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.status, StatusDemanda.ABERTA)

    # --- a tela oferece as duas formas de escrever ---------------------------

    @override_settings(**CONFIGURADO)
    def test_a_tela_do_detalhe_oferece_digitar_e_falar(self):
        resposta = self.client.get(
            reverse("demandas:detalhe", args=[self.demanda.pk]) + "?abrir=atualizar"
        )
        conteudo = resposta.content.decode()

        self.assertIn("Registrar atualização", conteudo)
        self.assertIn("data-eithra-ditar", conteudo)
        self.assertIn(
            f'{reverse("demandas:transcrever")}?demanda={self.demanda.pk}', conteudo
        )

    # --- a fala continua transitória ----------------------------------------

    def test_falha_de_transcricao_nao_altera_a_demanda(self):
        antes = self.demanda.historico.count()

        with override_settings(**CONFIGURADO):
            with patch(
                "apps.core.audio.urllib.request.urlopen",
                side_effect=OSError("sem rede"),
            ):
                resposta = self.client.post(
                    f"{reverse('demandas:transcrever')}?demanda={self.demanda.pk}",
                    {"audio": recado()},
                )

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("mensagem", resposta.json())
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.status, StatusDemanda.ABERTA)
        self.assertEqual(self.demanda.historico.count(), antes)

    def test_falha_semantica_preserva_o_relato(self):
        """Sem leitura semântica, o texto ditado continua inteiro e registrado."""
        with override_settings(**SEM_LEITURA_SEMANTICA):
            with patch("apps.core.audio.urllib.request.urlopen") as urlopen:
                urlopen.return_value = RespostaFalsa(
                    corpo_json({"text": RELATO_SEM_CONSEQUENCIA})
                )
                texto = self.client.post(
                    f"{reverse('demandas:transcrever')}?demanda={self.demanda.pk}",
                    {"audio": recado()},
                ).json()["texto"]

        resposta, _ = self._atualizar(texto)

        self.assertEqual(resposta.status_code, 302)
        self.demanda.refresh_from_db()
        evento = self.demanda.historico.get(tipo_evento=TipoEventoDemanda.OBSERVACAO)
        self.assertEqual(evento.descricao, RELATO_SEM_CONSEQUENCIA)

    # --- autorização e repetição ---------------------------------------------

    def test_isolamento_entre_condominios(self):
        outro = criar_condominio("Beta")
        alheia = Demanda.objects.create(
            condominio=outro, titulo="Alheia", criado_por=self.gestao
        )

        resposta = self.client.post(
            reverse("demandas:atualizar", args=[alheia.pk]),
            {"texto": RELATO_SEM_CONSEQUENCIA, "etapa_atualizacao": "conclusao"},
        )
        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(alheia.historico.count(), 0)

        ditar = self.client.post(
            f"{reverse('demandas:transcrever')}?demanda={alheia.pk}",
            {"audio": recado()},
        )
        self.assertEqual(ditar.status_code, 404)
        self.assertEqual(alheia.historico.count(), 0)

    def test_quem_nao_acompanha_a_demanda_nao_registra(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)

        resposta = self._abrir(RELATO_SEM_CONSEQUENCIA)

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(self.demanda.historico.count(), 1)

    def test_submissao_repetida_nao_duplica_movimentacao(self):
        etapa = self._abrir(RELATO_SEM_CONSEQUENCIA)
        confirmacao = etapa.context["confirmacao"]

        # O relato sustenta duas coisas agora: o que aconteceu (observação) e
        # por quem a demanda passa a esperar (acompanhamento). São dois eventos,
        # e uma movimentação continua valendo uma confirmação.
        self._confirmar(RELATO_SEM_CONSEQUENCIA, confirmacao)
        self.assertEqual(self.demanda.historico.count(), 3)

        repetida = self._confirmar(RELATO_SEM_CONSEQUENCIA, confirmacao)

        self.assertEqual(repetida.status_code, 302)
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.status, StatusDemanda.ABERTA)
        self.assertEqual(self.demanda.historico.count(), 3)


class SugestaoDeConclusaoPelaManutencaoTests(CenarioDaMovimentacao, TestCase):
    """A manutenção vinculada terminou: a próxima atualização sugere a conclusão.

    Enquanto nenhuma outra manutenção continuar aberta, a leitura propõe
    concluir a demanda — sempre como sugestão, nunca concluindo sozinha, e sem
    interferir quando ainda há um serviço em andamento.
    """

    def _manutencao(self, **campos):
        campos.setdefault("titulo", "Revisar elevador")
        return criar_manutencao(
            condominio=self.condominio,
            criado_por=self.gestao,
            demanda_origem=self.demanda,
            **campos,
        )

    def test_manutencao_concluida_sugere_conclusao_sem_concluir_sozinho(self):
        manutencao = self._manutencao()
        concluir_manutencao(
            manutencao=manutencao, usuario=self.gestao, descricao_servico="Feito."
        )

        etapa = self._abrir(RELATO_SEM_CONSEQUENCIA)

        resumo = etapa.context["resumo_atualizacao"]
        self.assertTrue(resumo["propoe_conclusao"])
        self.assertTrue(resumo["sugestao_por_manutencao"])
        # Antes da confirmação, a demanda continua exatamente onde estava.
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.status, StatusDemanda.ABERTA)

    def test_a_confirmacao_conclui_a_demanda(self):
        manutencao = self._manutencao()
        concluir_manutencao(
            manutencao=manutencao, usuario=self.gestao, descricao_servico="Feito."
        )
        etapa = self._abrir(RELATO_SEM_CONSEQUENCIA)

        self._confirmar(RELATO_SEM_CONSEQUENCIA, etapa.context["confirmacao"])

        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.status, StatusDemanda.CONCLUIDA)

    def test_manutencao_cancelada_tambem_sugere_conclusao(self):
        manutencao = self._manutencao()
        cancelar_manutencao(manutencao=manutencao, usuario=self.gestao)

        etapa = self._abrir(RELATO_SEM_CONSEQUENCIA)

        self.assertTrue(etapa.context["resumo_atualizacao"]["sugestao_por_manutencao"])

    def test_outra_manutencao_aberta_nao_sugere_conclusao(self):
        concluida = self._manutencao(titulo="Primeira")
        concluir_manutencao(
            manutencao=concluida, usuario=self.gestao, descricao_servico="Feito."
        )
        self._manutencao(titulo="Segunda")  # continua aberta

        etapa = self._abrir(RELATO_SEM_CONSEQUENCIA)

        resumo = etapa.context["resumo_atualizacao"]
        self.assertFalse(resumo["propoe_conclusao"])
        self.assertFalse(resumo["sugestao_por_manutencao"])
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.status, StatusDemanda.ABERTA)

    def test_demanda_encerrada_nao_e_reconduzida_pela_manutencao(self):
        """Demanda já cancelada não vira 'concluída' só porque a manutenção acabou."""
        manutencao = self._manutencao()
        concluir_manutencao(
            manutencao=manutencao, usuario=self.gestao, descricao_servico="Feito."
        )
        alterar_status(
            demanda=self.demanda,
            usuario=self.gestao,
            novo_status=StatusDemanda.CANCELADA,
        )

        etapa = self._abrir(RELATO_SEM_CONSEQUENCIA)

        self.assertFalse(etapa.context["resumo_atualizacao"]["propoe_conclusao"])
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.status, StatusDemanda.CANCELADA)
