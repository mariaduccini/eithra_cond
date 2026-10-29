"""O acompanhamento operacional da demanda — e por que ele não é a situação.

A demanda tem um ciclo, e ele continua o de sempre: aberta, em andamento,
aguardando, concluída, cancelada. O acompanhamento responde outra pergunta —
por quem ela espera, qual é o próximo passo, para quando foi prometida, o que
a trava e quem a segue de perto.

Estes testes prendem exatamente a separação: salvar acompanhamento não move a
situação, previsão não é prazo, impedimento não encerra nada, e o que o relato
não sustenta não é inventado. O que muda vira histórico, com autor e com o
antes e o depois.

Os mesmos relatos que a movimentação já usa voltam aqui por outro motivo: eles
devem propor acompanhamento, e só o que eles sustentam.
"""
from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Fornecedor
from apps.condominios.constants import Papel
from apps.demandas.constants import AguardandoTipo, StatusDemanda, TipoEventoDemanda
from apps.demandas.models import Demanda
from apps.demandas.services import (
    ErroDeDemanda,
    alterar_status,
    atualizar_acompanhamento,
    criar_demanda,
)
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)

RELATO_DO_FORNECEDOR = "A Atlas informou que virá amanhã às 14h."
RELATO_DO_ORCAMENTO = "Ainda aguardando orçamento."
RELATO_DA_PECA = "Peça chegou. Técnico vem sexta."
RELATO_AMBIGUO = "Falei com eles."

CAMPOS_DO_ACOMPANHAMENTO = (
    "aguardando_tipo",
    "aguardando_descricao",
    "proxima_acao",
    "previsao",
    "impedimento",
    "responsavel_acompanhamento",
)


class BaseDoAcompanhamento(TestCase):
    """Uma demanda em andamento e uma pessoa que responde por ela."""

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

    # --- atalhos -----------------------------------------------------------

    def _demanda(self, **campos):
        base = {
            "condominio": self.condominio,
            "criado_por": self.gestao,
            "titulo": "Demanda do cenário",
        }
        base.update(campos)
        return criar_demanda(**base)

    def _com_fornecedor(self, nome="Atlas Elevadores"):
        """Reconhecer o fornecedor exige que ele exista no cadastro da casa."""
        return Fornecedor.objects.create(
            condominio=self.condominio,
            nome_razao_social=nome,
            nome_fantasia=nome,
        )

    def _acompanhar(self, **campos):
        """Envia a janela de acompanhamento com os seis campos preenchidos.

        O formulário fala de todos os campos de uma vez — quem não é nomeado
        vem vazio, e vazio é a decisão de limpar. Por isso o envio é montado
        por inteiro, como a janela faria.
        """
        dados = {campo: "" for campo in CAMPOS_DO_ACOMPANHAMENTO}
        dados.update(campos)
        return self.client.post(
            reverse("demandas:acompanhar", args=[self.demanda.pk]), dados
        )

    def _abrir(self, texto):
        """Primeira etapa da atualização: só o relato volta com o que foi lido."""
        return self.client.post(
            reverse("demandas:atualizar", args=[self.demanda.pk]),
            {"texto": texto, "etapa_atualizacao": "informacoes"},
        )

    def _confirmar(self, texto, confirmacao):
        """Segunda etapa: é a confirmação que autoriza aplicar a leitura."""
        return self.client.post(
            reverse("demandas:atualizar", args=[self.demanda.pk]),
            {
                "texto": texto,
                "etapa_atualizacao": "conclusao",
                "confirmacao": confirmacao,
            },
        )

    def _eventos(self, tipo):
        return self.demanda.historico.filter(tipo_evento=tipo)


class AcompanhamentoManualTests(BaseDoAcompanhamento):
    """1. Definir o acompanhamento à mão, pela janela da demanda."""

    def test_janela_grava_o_acompanhamento_completo(self):
        previsao = timezone.localdate() + timedelta(days=3)

        resposta = self._acompanhar(
            aguardando_tipo=AguardandoTipo.FORNECEDOR,
            aguardando_descricao="Atlas Elevadores",
            proxima_acao="Confirmar a visita técnica",
            previsao=previsao.isoformat(),
            impedimento="Peça ainda não entregue",
            responsavel_acompanhamento=self.gestao.pk,
        )

        self.assertEqual(resposta.status_code, 302)
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.aguardando_tipo, AguardandoTipo.FORNECEDOR)
        self.assertEqual(self.demanda.aguardando_descricao, "Atlas Elevadores")
        self.assertEqual(self.demanda.proxima_acao, "Confirmar a visita técnica")
        self.assertEqual(self.demanda.previsao, previsao)
        self.assertEqual(self.demanda.impedimento, "Peça ainda não entregue")
        self.assertEqual(self.demanda.responsavel_acompanhamento, self.gestao)

    def test_janela_vazia_nao_grava_nada_nem_gera_evento(self):
        resposta = self._acompanhar()

        self.assertEqual(resposta.status_code, 302)
        self.demanda.refresh_from_db()
        self.assertFalse(self.demanda.tem_acompanhamento)
        self.assertFalse(self._eventos(TipoEventoDemanda.ACOMPANHAMENTO).exists())

    def test_tipo_de_espera_desconhecido_e_recusado(self):
        with self.assertRaises(ErroDeDemanda):
            atualizar_acompanhamento(
                demanda=self.demanda,
                usuario=self.gestao,
                aguardando_tipo="qualquer coisa",
            )


class FronteiraComStatusTests(BaseDoAcompanhamento):
    """2, 3 e 4. O acompanhamento não encosta no ciclo nem nos prazos."""

    def test_aguardando_fornecedor_nao_muda_a_situacao(self):
        alterar_status(
            demanda=self.demanda,
            usuario=self.gestao,
            novo_status=StatusDemanda.EM_ANDAMENTO,
        )
        eventos_de_situacao = self._eventos(TipoEventoDemanda.STATUS).count()

        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            aguardando_tipo=AguardandoTipo.FORNECEDOR,
            aguardando_descricao="Atlas Elevadores",
        )

        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.status, StatusDemanda.EM_ANDAMENTO)
        # Acompanhar não é mover: nenhum evento de situação foi acrescentado.
        self.assertEqual(
            self._eventos(TipoEventoDemanda.STATUS).count(), eventos_de_situacao
        )

    def test_previsao_nao_mexe_no_prazo_da_demanda(self):
        prazo = timezone.localdate() + timedelta(days=20)
        demanda = self._demanda(prazo=prazo)
        self.demanda = demanda

        atualizar_acompanhamento(
            demanda=demanda,
            usuario=self.gestao,
            previsao=timezone.localdate() + timedelta(days=2),
        )

        demanda.refresh_from_db()
        self.assertEqual(demanda.prazo, prazo)
        self.assertEqual(demanda.previsao, timezone.localdate() + timedelta(days=2))
        self.assertFalse(
            demanda.historico.filter(tipo_evento=TipoEventoDemanda.PRAZO).exists()
        )

    def test_impedimento_nao_encerra_nem_muda_a_situacao(self):
        alterar_status(
            demanda=self.demanda,
            usuario=self.gestao,
            novo_status=StatusDemanda.EM_ANDAMENTO,
        )

        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            impedimento="Peça ainda não entregue",
        )

        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.status, StatusDemanda.EM_ANDAMENTO)
        self.assertEqual(self.demanda.impedimento, "Peça ainda não entregue")

    def test_previsao_vencida_nao_conclui_nem_muda_a_situacao(self):
        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            previsao=timezone.localdate() - timedelta(days=1),
        )

        self.demanda.refresh_from_db()
        self.assertTrue(self.demanda.previsao_vencida)
        self.assertEqual(self.demanda.status, StatusDemanda.ABERTA)
        self.assertIsNone(self.demanda.concluido_em)


class SugestaoPelaMovimentacaoTests(BaseDoAcompanhamento):
    """5 a 8. O relato propõe; só a confirmação grava, e só o que ele sustenta."""

    def test_relato_sugere_acompanhamento_na_etapa_de_conclusao(self):
        self._com_fornecedor()
        amanha = timezone.localdate() + timedelta(days=1)

        resposta = self._abrir(RELATO_DO_FORNECEDOR)

        self.assertEqual(resposta.status_code, 200)
        identificado = resposta.context["resumo_atualizacao"]["identificado"]
        texto = " | ".join(identificado)
        self.assertIn("Aguardando: Atlas Elevadores", texto)
        self.assertIn(f"Previsão informada: {amanha.strftime('%d/%m')} às 14h", texto)
        self.assertIn("Próxima ação: Acompanhar a visita", texto)

    def test_sugestao_nao_persiste_antes_da_confirmacao(self):
        self._com_fornecedor()

        self._abrir(RELATO_DO_FORNECEDOR)

        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.aguardando_tipo, AguardandoTipo.NINGUEM)
        self.assertEqual(self.demanda.aguardando_descricao, "")
        self.assertEqual(self.demanda.proxima_acao, "")
        self.assertIsNone(self.demanda.previsao)
        self.assertFalse(self._eventos(TipoEventoDemanda.ACOMPANHAMENTO).exists())

    def test_confirmacao_grava_so_o_que_o_relato_sustenta(self):
        self._com_fornecedor()
        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            impedimento="Peça ainda não entregue",
            responsavel_acompanhamento=self.gestao,
        )
        amanha = timezone.localdate() + timedelta(days=1)

        etapa = self._abrir(RELATO_DO_FORNECEDOR)
        self._confirmar(RELATO_DO_FORNECEDOR, etapa.context["confirmacao"])

        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.aguardando_tipo, AguardandoTipo.FORNECEDOR)
        self.assertEqual(self.demanda.aguardando_descricao, "Atlas Elevadores")
        self.assertEqual(self.demanda.proxima_acao, "Acompanhar a visita")
        self.assertEqual(self.demanda.previsao, amanha)
        # O relato não falou de impedimento nem de quem acompanha: o que já
        # estava lá continua onde estava.
        self.assertEqual(self.demanda.impedimento, "Peça ainda não entregue")
        self.assertEqual(self.demanda.responsavel_acompanhamento, self.gestao)

    def test_relato_de_orcamento_sugere_cobrar_sem_concluir(self):
        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            impedimento="Orçamento pendente",
        )

        etapa = self._abrir(RELATO_DO_ORCAMENTO)
        self._confirmar(RELATO_DO_ORCAMENTO, etapa.context["confirmacao"])

        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.aguardando_tipo, AguardandoTipo.FORNECEDOR)
        self.assertEqual(self.demanda.proxima_acao, "Cobrar o orçamento")
        self.assertEqual(self.demanda.status, StatusDemanda.ABERTA)
        self.assertFalse(
            self._eventos(TipoEventoDemanda.CONCLUIDA).exists(),
        )

    def test_relato_explicito_remove_o_impedimento_sem_concluir(self):
        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            impedimento="Peça ainda não entregue",
        )
        hoje = timezone.localdate()
        sexta = hoje + timedelta(days=(4 - hoje.weekday()) % 7 or 7)

        etapa = self._abrir(RELATO_DA_PECA)
        self._confirmar(RELATO_DA_PECA, etapa.context["confirmacao"])

        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.impedimento, "")
        self.assertEqual(self.demanda.aguardando_tipo, AguardandoTipo.FORNECEDOR)
        self.assertEqual(self.demanda.previsao, sexta)
        self.assertEqual(self.demanda.status, StatusDemanda.ABERTA)

    def test_relato_ambiguo_nao_inventa_acompanhamento(self):
        etapa = self._abrir(RELATO_AMBIGUO)
        self._confirmar(RELATO_AMBIGUO, etapa.context["confirmacao"])

        self.demanda.refresh_from_db()
        self.assertFalse(self.demanda.tem_acompanhamento)
        self.assertFalse(self._eventos(TipoEventoDemanda.ACOMPANHAMENTO).exists())
        # O fato continua sendo registrado — o que não acontece é a invenção.
        self.assertTrue(self._eventos(TipoEventoDemanda.OBSERVACAO).exists())


class HistoricoDoAcompanhamentoTests(BaseDoAcompanhamento):
    """9 e 10. Toda mudança vira evento, com autor e com o antes e o depois."""

    def test_atualizacao_gera_evento_com_antes_e_depois(self):
        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            aguardando_tipo=AguardandoTipo.FORNECEDOR,
            aguardando_descricao="Atlas Elevadores",
        )

        evento = self._eventos(TipoEventoDemanda.ACOMPANHAMENTO).get()
        self.assertEqual(evento.dados_anteriores["aguardando_tipo"], None)
        self.assertEqual(evento.dados_novos["aguardando_tipo"], AguardandoTipo.FORNECEDOR)
        self.assertEqual(evento.dados_novos["aguardando_descricao"], "Atlas Elevadores")
        self.assertIn("Aguardando: ninguém → Fornecedor", evento.descricao)

    def test_autor_fica_registrado_no_evento(self):
        outra = criar_pessoa("supervisora@exemplo.test", "Supervisora")
        criar_vinculo(outra, self.condominio, papel=Papel.SUPERVISORA)

        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=outra,
            proxima_acao="Cobrar retorno do fornecedor",
        )

        evento = self._eventos(TipoEventoDemanda.ACOMPANHAMENTO).get()
        self.assertEqual(evento.usuario, outra)

    def test_limpar_o_acompanhamento_nao_conclui_a_demanda(self):
        alterar_status(
            demanda=self.demanda,
            usuario=self.gestao,
            novo_status=StatusDemanda.EM_ANDAMENTO,
        )
        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            aguardando_tipo=AguardandoTipo.FORNECEDOR,
            proxima_acao="Cobrar retorno",
        )

        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            aguardando_tipo=AguardandoTipo.NINGUEM,
            proxima_acao="",
        )

        self.demanda.refresh_from_db()
        self.assertFalse(self.demanda.tem_acompanhamento)
        self.assertEqual(self.demanda.status, StatusDemanda.EM_ANDAMENTO)
        self.assertIsNone(self.demanda.concluido_em)
        self.assertEqual(self._eventos(TipoEventoDemanda.ACOMPANHAMENTO).count(), 2)


class AutorizacaoDoAcompanhamentoTests(BaseDoAcompanhamento):
    """11 e 12. Sem autorização, a resposta não confirma nem revela a demanda."""

    def test_demanda_de_outro_condominio_nao_abre(self):
        outro = criar_condominio("Beta")
        alheia = criar_demanda(
            condominio=outro,
            criado_por=self.gestao,
            titulo="Demanda de outro prédio",
        )

        resposta = self.client.post(
            reverse("demandas:acompanhar", args=[alheia.pk]),
            {"aguardando_tipo": AguardandoTipo.FORNECEDOR},
        )

        self.assertEqual(resposta.status_code, 404)
        alheia.refresh_from_db()
        self.assertEqual(alheia.aguardando_tipo, AguardandoTipo.NINGUEM)

    def test_quem_nao_acompanha_o_modulo_recebe_404_e_nada_muda(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)

        resposta = self.client.post(
            reverse("demandas:acompanhar", args=[self.demanda.pk]),
            {
                "aguardando_tipo": AguardandoTipo.FORNECEDOR,
                "aguardando_descricao": "Atlas Elevadores",
            },
        )

        self.assertEqual(resposta.status_code, 404)
        self.demanda.refresh_from_db()
        self.assertFalse(self.demanda.tem_acompanhamento)
        self.assertFalse(self._eventos(TipoEventoDemanda.ACOMPANHAMENTO).exists())


class TelaDaDemandaTests(BaseDoAcompanhamento):
    """14 e 15. O detalhe mostra o bloco; a lista mostra só o sinal."""

    def _com_acompanhamento(self):
        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            aguardando_tipo=AguardandoTipo.FORNECEDOR,
            aguardando_descricao="Atlas Elevadores",
            proxima_acao="Confirmar a visita técnica",
            previsao=timezone.localdate() + timedelta(days=1),
            impedimento="Peça ainda não entregue",
            responsavel_acompanhamento=self.gestao,
        )

    def test_detalhe_exibe_o_bloco_de_acompanhamento(self):
        self._com_acompanhamento()

        resposta = self.client.get(reverse("demandas:detalhe", args=[self.demanda.pk]))
        conteudo = resposta.content.decode()

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Acompanhamento", conteudo)
        self.assertIn("Atualizar acompanhamento", conteudo)

        linhas = resposta.context["demanda"]["acompanhamento"]
        self.assertEqual(
            [linha["rotulo"] for linha in linhas],
            ["Aguardando", "Próxima ação", "Previsão", "Impedimento", "Quem acompanha"],
        )
        valores = {linha["rotulo"]: linha["valor"] for linha in linhas}
        self.assertEqual(valores["Aguardando"], "Atlas Elevadores")
        self.assertEqual(valores["Próxima ação"], "Confirmar a visita técnica")
        self.assertEqual(valores["Impedimento"], "Peça ainda não entregue")
        self.assertEqual(valores["Quem acompanha"], "Fernanda")

    def test_detalhe_sem_acompanhamento_diz_que_nao_ha_nada(self):
        resposta = self.client.get(reverse("demandas:detalhe", args=[self.demanda.pk]))
        conteudo = resposta.content.decode()

        self.assertEqual(resposta.context["demanda"]["acompanhamento"], [])
        self.assertIn("Nada em acompanhamento", conteudo)

    def test_listagem_mostra_o_sinal_sem_abrir_coluna_nova(self):
        self._com_acompanhamento()

        resposta = self.client.get(reverse("demandas:listagem"))
        conteudo = resposta.content.decode()

        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Aguardando fornecedor", conteudo)
        # As colunas continuam as mesmas: o acompanhamento não virou uma delas.
        for cabecalho in (
            "Código",
            "Título",
            "Descrição",
            "Local",
            "Prioridade",
            "Prazo",
            "Responsável",
            "Situação",
            "Ações",
        ):
            self.assertIn(f">{cabecalho}<", conteudo)
        self.assertNotIn(">Aguardando</th>", conteudo)
        self.assertNotIn(">Previsão</th>", conteudo)
        self.assertNotIn(">Impedimento</th>", conteudo)

    def test_listagem_sinaliza_previsao_vencida(self):
        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            previsao=timezone.localdate() - timedelta(days=2),
        )

        resposta = self.client.get(reverse("demandas:listagem"))
        conteudo = resposta.content.decode()

        self.assertIn("Previsão vencida", conteudo)

    def test_demanda_encerrada_nao_anuncia_acompanhamento_na_lista(self):
        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            aguardando_tipo=AguardandoTipo.FORNECEDOR,
        )
        alterar_status(
            demanda=self.demanda,
            usuario=self.gestao,
            novo_status=StatusDemanda.CONCLUIDA,
        )

        resposta = self.client.get(reverse("demandas:listagem"))
        conteudo = resposta.content.decode()

        self.assertNotIn("Aguardando fornecedor", conteudo)


class UltimaMovimentacaoTests(BaseDoAcompanhamento):
    """16. A última movimentação vem do histórico — não do carimbo de gravação."""

    def test_ultima_movimentacao_e_o_evento_mais_recente(self):
        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            aguardando_tipo=AguardandoTipo.FORNECEDOR,
        )

        self.demanda.refresh_from_db()
        evento = self.demanda.historico.order_by("-data_hora").first()

        self.assertEqual(self.demanda.ultima_movimentacao.pk, evento.pk)
        self.assertEqual(
            self.demanda.ultima_movimentacao.tipo_evento,
            TipoEventoDemanda.ACOMPANHAMENTO,
        )

    def test_gravacao_sem_movimentacao_nao_muda_a_ultima_movimentacao(self):
        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            aguardando_tipo=AguardandoTipo.FORNECEDOR,
        )
        self.demanda.refresh_from_db()
        evento = self.demanda.ultima_movimentacao

        # Uma gravação que não passou por serviço nenhum: o carimbo de
        # atualização anda para a frente, mas nada aconteceu com a demanda.
        Demanda.objects.filter(pk=self.demanda.pk).update(
            atualizado_em=timezone.now() + timedelta(days=5)
        )

        self.demanda.refresh_from_db()
        self.assertGreater(self.demanda.atualizado_em, evento.data_hora)
        self.assertEqual(self.demanda.ultima_movimentacao.pk, evento.pk)

    def test_detalhe_mostra_quando_a_demanda_se_moveu(self):
        atualizar_acompanhamento(
            demanda=self.demanda,
            usuario=self.gestao,
            proxima_acao="Cobrar retorno",
        )

        resposta = self.client.get(reverse("demandas:detalhe", args=[self.demanda.pk]))

        self.assertIn("Última movimentação", resposta.content.decode())
        self.assertTrue(resposta.context["demanda"]["ultima_movimentacao"])

    def test_demanda_recem_criada_conta_a_criacao_como_ultima_movimentacao(self):
        self.assertIsNotNone(self.demanda.ultima_movimentacao)
        self.assertEqual(
            self.demanda.ultima_movimentacao.tipo_evento, TipoEventoDemanda.CRIADA
        )
