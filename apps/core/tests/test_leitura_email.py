"""Testes da leitura semântica de e-mail e da reconciliação assistida.

A pergunta que estes testes cobram é só uma, e é a mesma do módulo:

    Entrou uma informação nova. Isso muda alguma coisa que mereça atenção?

O provedor do Gmail e o serviço de linguagem são sempre falsos — nenhum teste
toca a rede. O que se verifica é o contrato da leitura: o que ela extrai como
fato, o que ela conclui como inferência, o que ela propõe, quando ela pede
confirmação humana e — o mais importante nesta fase — o que ela **não** faz:
não cria demanda, não cria manutenção, não cria documento, não grava vínculo,
não baixa anexo e não atravessa condomínio.
"""
from datetime import datetime, timezone
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.cadastros.models import Equipamento, Fornecedor
from apps.cadastros.tests.apoio import criar_pessoa
from apps.condominios.models import Condominio
from apps.core import email_interpretacao, gmail
from apps.core.constants import (
    EntidadeImportacaoHistorica,
    EstadoProcessamentoImportacao,
    OrigemImportacaoHistorica,
    ResultadoImportacaoHistorica,
)
from apps.core.email_interpretacao import (
    AcaoProposta,
    Candidato,
    Consequencia,
    Fato,
    Inferencia,
    OrigemNaoSuportada,
    TipoCandidato,
)
from apps.core.interpretacao import Confianca, Estado
from apps.core.models import (
    ItemImportacaoHistorica,
    LoteImportacaoHistorica,
    VinculoImportacaoHistorica,
)
from apps.demandas.models import Demanda
from apps.governanca.models import DocumentoOperacional
from apps.manutencoes.constants import StatusManutencao
from apps.manutencoes.models import Manutencao

CONTA = "portaria@exemplo.test"
REMETENTE = "financeiro@atlas.com.br"
MOMENTO = datetime(2026, 10, 27, 10, 30, tzinfo=timezone.utc)

# O que nunca pode sair do e-mail: nem para o banco, nem para o log.
CORPO_SIGILOSO = "O elevador parou no oitavo andar; não repita isto em lugar nenhum."
RESUMO_SIGILOSO = "RESUMO-QUE-NAO-PODE-SER-GRAVADO"


# ---------------------------------------------------------------------------
# Dublês
# ---------------------------------------------------------------------------


class ProvedorFalso:
    """Um provedor de mentira: entrega a mensagem do teste, sem rede.

    Registra o que lhe foi pedido — para conferir que a leitura recupera a
    mensagem pelo ``message_id`` — e qual era o estado do item no instante da
    leitura, para provar que ele passou por ``PROCESSANDO``. Recusa baixar
    anexo: nesta fase nenhum anexo é baixado.
    """

    def __init__(self, *, mensagem=None, erro=None):
        self._mensagem = mensagem
        self._erro = erro
        self.mensagens_pedidas = []
        self.anexos_baixados = []
        self.estado_durante_leitura = None
        self.item_pk = None

    def obter_mensagem(self, message_id):
        self.mensagens_pedidas.append(message_id)
        if self.item_pk is not None:
            self.estado_durante_leitura = (
                ItemImportacaoHistorica.objects.get(pk=self.item_pk).estado_processamento
            )
        if self._erro is not None:
            raise self._erro
        return self._mensagem

    def baixar_anexo(self, message_id, attachment_id):
        self.anexos_baixados.append((message_id, attachment_id))
        raise AssertionError("Nenhum anexo deve ser baixado nesta fase.")


def mensagem(
    *,
    message_id="msg-1",
    thread_id="thread-1",
    remetente=REMETENTE,
    assunto="",
    texto=CORPO_SIGILOSO,
    anexos=(),
):
    """Uma MensagemGmail normalizada, como o provider devolveria."""
    return gmail.MensagemGmail(
        message_id=message_id,
        thread_id=thread_id,
        remetente=remetente,
        destinatarios=("sindica@exemplo.test",),
        assunto=assunto,
        momento=MOMENTO,
        texto=texto,
        anexos=tuple(anexos),
    )


def resposta(**campos):
    """O objeto JSON que o serviço de linguagem devolveria.

    Vazio por padrão: sem nada a acrescentar, a leitura determinística continua
    sendo quem decide. Os testes que precisam de uma consequência específica a
    informam — e é isso que se verifica.
    """
    base = {
        "consequencia": "",
        "equipamento": "",
        "local": "",
        "resumo": "",
        "pergunta": "",
    }
    base.update(campos)
    return base


def fato(resultado, campo):
    """O primeiro fato com este campo, se houver."""
    return next((item for item in resultado.fatos if item.campo == campo), None)


def candidatos(resultado, tipo):
    """Os candidatos deste tipo."""
    return [item for item in resultado.candidatos if item.tipo == tipo]


def criar_item(
    condominio,
    *,
    message_id="msg-1",
    thread_id="thread-1",
    remetente=REMETENTE,
    assunto="",
    origem=OrigemImportacaoHistorica.GMAIL,
):
    """Um item da carga histórica, como a ingestão o teria deixado."""
    lote = LoteImportacaoHistorica.objects.create(
        condominio=condominio, origem=origem, conta_origem=CONTA
    )
    item, _ = ItemImportacaoHistorica.objects.registrar_item(
        lote=lote,
        identificador_externo=message_id or "linha-1",
        message_id=message_id if origem == OrigemImportacaoHistorica.GMAIL else "",
        thread_id=thread_id,
        remetente=remetente,
        assunto=assunto,
    )
    return item


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class BaseLeituraTests(TestCase):
    def setUp(self):
        self.condominio = Condominio.objects.create(nome="Condomínio de Teste")
        self.outro = Condominio.objects.create(nome="Outro Condomínio")
        self.gestao = criar_pessoa("gestao.leitura@exemplo.test", "Gestão")

    def processar(self, provedor, item):
        """Roda a leitura com o provedor falso — sem tocar o Gmail."""
        provedor.item_pk = item.pk
        return email_interpretacao.processar_item_gmail(item, provedor=provedor)

    def equipamento(self, nome, condominio=None):
        return Equipamento.objects.create(
            condominio=condominio or self.condominio, nome=nome
        )

    def fornecedor(self, email=REMETENTE, nome="Atlas Elevadores Ltda.", condominio=None):
        return Fornecedor.objects.create(
            condominio=condominio or self.condominio,
            nome_razao_social=nome,
            email=email,
        )

    def demanda(self, titulo, *, condominio=None, equipamento=None):
        return Demanda.objects.create(
            condominio=condominio or self.condominio,
            criado_por=self.gestao,
            titulo=titulo,
            equipamento=equipamento,
        )

    def manutencao(self, titulo, *, condominio=None, equipamento=None, status=None):
        campos = {"equipamento": equipamento}
        if status is not None:
            campos["status"] = status
        if status == StatusManutencao.CONCLUIDA:
            # Concluir exige a data: a restrição do modelo recusa a conclusão
            # sem ela — cancelar não é concluir.
            campos["data_conclusao"] = datetime.now(timezone.utc)
        return Manutencao.objects.create(
            condominio=condominio or self.condominio,
            criado_por=self.gestao,
            titulo=titulo,
            **campos,
        )


# ---------------------------------------------------------------------------
# 1-4. Chamada, ciclo e recuperação
# ---------------------------------------------------------------------------


class ChamadaETrilhaTests(BaseLeituraTests):
    def test_item_de_origem_diferente_do_gmail_e_recusado(self):
        """1. Item não Gmail é recusado — a leitura não se aplica."""
        item = criar_item(self.condominio, origem=OrigemImportacaoHistorica.IMPORTACAO)
        provedor = ProvedorFalso(mensagem=mensagem())

        with self.assertRaises(OrigemNaoSuportada):
            self.processar(provedor, item)

        self.assertEqual(provedor.mensagens_pedidas, [])
        self.assertEqual(
            ItemImportacaoHistorica.objects.get(pk=item.pk).estado_processamento,
            EstadoProcessamentoImportacao.PENDENTE,
        )

    def test_item_pendente_passa_por_processando(self):
        """2. Item PENDENTE entra em processamento antes de ser lido."""
        item = criar_item(self.condominio, message_id="msg-2")
        provedor = ProvedorFalso(mensagem=mensagem(message_id="msg-2", texto="Obrigado, recebido."))

        resultado = self.processar(provedor, item)

        self.assertEqual(
            provedor.estado_durante_leitura, EstadoProcessamentoImportacao.PROCESSANDO
        )
        self.assertNotEqual(resultado.estado_final, EstadoProcessamentoImportacao.PENDENTE)

    def test_a_mensagem_e_recuperada_pelo_message_id(self):
        """3. O Gmail é recuperado por ``message_id``, e não pelo corpo guardado."""
        item = criar_item(self.condominio, message_id="msg-7")
        provedor = ProvedorFalso(mensagem=mensagem(message_id="msg-7", texto="Obrigado, recebido."))

        self.processar(provedor, item)

        self.assertEqual(provedor.mensagens_pedidas, ["msg-7"])

    def test_o_corpo_nao_e_persistido(self):
        """4. O corpo é usado em memória e não sobra em lugar nenhum."""
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(
            mensagem=mensagem(texto="Vazamento de água na garagem. " + CORPO_SIGILOSO)
        )

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(consequencia="nova_ocorrencia"),
        ):
            self.processar(provedor, item)

        campos = " ".join(
            str(valor) for valor in ItemImportacaoHistorica.objects.get(pk=item.pk).__dict__.values()
        )
        self.assertNotIn(CORPO_SIGILOSO, campos)
        self.assertNotIn(CORPO_SIGILOSO, str(VinculoImportacaoHistorica.objects.all()))


# ---------------------------------------------------------------------------
# 5. Fatos e inferências
# ---------------------------------------------------------------------------


class FatosEInferenciasTests(BaseLeituraTests):
    def test_fatos_e_inferencias_nao_se_misturam(self):
        """5. O que a mensagem disse é fato; o que o Eithra concluiu é inferência."""
        item = criar_item(self.condominio, remetente=REMETENTE)
        provedor = ProvedorFalso(
            mensagem=mensagem(texto="Vazamento de água na garagem.", assunto="Vazamento")
        )

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(
                consequencia="nova_ocorrencia", resumo="Há um vazamento na garagem."
            ),
        ):
            resultado = self.processar(provedor, item)

        self.assertTrue(all(isinstance(item, Fato) for item in resultado.fatos))
        self.assertTrue(
            all(isinstance(item, Inferencia) for item in resultado.inferencias)
        )

        remetente = fato(resultado, "remetente")
        self.assertIsNotNone(remetente)
        self.assertEqual(remetente.estado, Estado.DECLARADA)

        # O resumo é leitura, não declaração: fica como inferência, nunca como fato.
        self.assertIsNone(fato(resultado, "resumo"))
        self.assertTrue(any(i.campo == "resumo" for i in resultado.inferencias))


# ---------------------------------------------------------------------------
# 6-8. Remetente e fornecedor
# ---------------------------------------------------------------------------


class RemetenteTests(BaseLeituraTests):
    def test_remetente_exato_encontra_fornecedor(self):
        """6. Um remetente que é fornecedor cadastrado vira fato confirmado."""
        self.fornecedor(email=REMETENTE)
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(mensagem=mensagem(texto="Vazamento de água na garagem."))

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(consequencia="nova_ocorrencia"),
        ):
            resultado = self.processar(provedor, item)

        encontrados = candidatos(resultado, TipoCandidato.FORNECEDOR)
        self.assertEqual(len(encontrados), 1)
        self.assertEqual(encontrados[0].confianca, Confianca.ALTA)

        fornecedor = fato(resultado, "fornecedor")
        self.assertIsNotNone(fornecedor)
        self.assertTrue(fornecedor.confirmado)

    def test_remetente_desconhecido_nao_cria_fornecedor(self):
        """7. Um remetente fora do cadastro não inventa fornecedor."""
        item = criar_item(self.condominio, remetente="desconhecido@exemplo.test")
        provedor = ProvedorFalso(mensagem=mensagem(texto="Vazamento de água na garagem."))

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(consequencia="nova_ocorrencia"),
        ):
            resultado = self.processar(provedor, item)

        self.assertEqual(Fornecedor.objects.count(), 0)
        self.assertEqual(candidatos(resultado, TipoCandidato.FORNECEDOR), [])
        self.assertIsNone(fato(resultado, "fornecedor"))

    def test_fornecedor_ambiguo_pede_confirmacao(self):
        """8. Mais de um fornecedor com o mesmo endereço é decisão humana."""
        self.fornecedor(email=REMETENTE, nome="Atlas Elevadores Ltda.")
        self.fornecedor(email=REMETENTE, nome="Atlas Serviços Ltda.")
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(mensagem=mensagem(texto="Vazamento de água na garagem."))

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(consequencia="nova_ocorrencia"),
        ):
            resultado = self.processar(provedor, item)

        self.assertEqual(resultado.proposta.acao, AcaoProposta.CONFIRMAR_CONTEXTO)
        self.assertTrue(resultado.necessita_confirmacao)
        self.assertIn("fornecedor", resultado.pergunta.lower())


# ---------------------------------------------------------------------------
# 9-10. Thread
# ---------------------------------------------------------------------------


class ThreadTests(BaseLeituraTests):
    def _item_anterior(self, *, entidade, destino):
        anterior = criar_item(
            self.condominio, message_id="msg-anterior", thread_id="thread-1"
        )
        VinculoImportacaoHistorica.objects.registrar_vinculo(
            item=anterior,
            entidade_destino=entidade,
            destino_id=destino.pk,
            resultado=ResultadoImportacaoHistorica.CRIADO,
        )
        return anterior

    def test_thread_conhecida_vira_candidato(self):
        """9. O fio já relacionado a uma demanda vira candidato principal."""
        demanda = self.demanda("Elevador social parado")
        self._item_anterior(
            entidade=EntidadeImportacaoHistorica.DEMANDA, destino=demanda
        )
        item = criar_item(self.condominio, message_id="msg-nova", thread_id="thread-1")
        provedor = ProvedorFalso(mensagem=mensagem(message_id="msg-nova", texto="Vazamento de água na garagem."))

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(consequencia="nova_ocorrencia"),
        ):
            resultado = self.processar(provedor, item)

        do_fio = candidatos(resultado, TipoCandidato.THREAD)
        self.assertEqual(len(do_fio), 1)
        self.assertEqual(do_fio[0].objeto, demanda)

    def test_thread_encerrada_nao_forca_associacao_em_conflito(self):
        """10. Fio apontando para algo encerrado + ocorrência nova = confirmação."""
        encerrada = self.manutencao(
            "Troca do selo mecânico", status=StatusManutencao.CONCLUIDA
        )
        self._item_anterior(
            entidade=EntidadeImportacaoHistorica.MANUTENCAO, destino=encerrada
        )
        item = criar_item(self.condominio, message_id="msg-nova", thread_id="thread-1")
        provedor = ProvedorFalso(
            mensagem=mensagem(message_id="msg-nova", texto="Vazamento de água na garagem.")
        )

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(consequencia="nova_ocorrencia"),
        ):
            resultado = self.processar(provedor, item)

        self.assertEqual(resultado.proposta.acao, AcaoProposta.CONFIRMAR_CONTEXTO)
        self.assertTrue(resultado.necessita_confirmacao)
        self.assertIn("assunto novo", resultado.pergunta.lower())

        do_fio = candidatos(resultado, TipoCandidato.THREAD)
        self.assertEqual(len(do_fio), 1)
        self.assertEqual(do_fio[0].confianca, Confianca.BAIXA)


# ---------------------------------------------------------------------------
# 11-12. Equipamento e local
# ---------------------------------------------------------------------------


class EquipamentoTests(BaseLeituraTests):
    def test_equipamento_inequivoco_vira_candidato(self):
        """11. Um equipamento do cadastro citado no texto vira candidato forte."""
        elevador = self.equipamento("Elevador social")
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(
            mensagem=mensagem(texto="O elevador social parou de funcionar.")
        )

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(consequencia="nova_ocorrencia"),
        ):
            resultado = self.processar(provedor, item)

        encontrados = candidatos(resultado, TipoCandidato.EQUIPAMENTO)
        self.assertEqual(len(encontrados), 1)
        self.assertEqual(encontrados[0].objeto, elevador)
        self.assertEqual(encontrados[0].confianca, Confianca.ALTA)

        equipamento = fato(resultado, "equipamento")
        self.assertIsNotNone(equipamento)
        self.assertTrue(equipamento.confirmado)

    def test_equipamento_ambiguo_pede_confirmacao(self):
        """12. Vários equipamentos respondendo pelo tipo é decisão humana."""
        self.equipamento("Elevador social")
        self.equipamento("Elevador de serviço")
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(
            mensagem=mensagem(texto="O elevador parou de funcionar.")
        )

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(consequencia="nova_ocorrencia"),
        ):
            resultado = self.processar(provedor, item)

        self.assertEqual(resultado.proposta.acao, AcaoProposta.CONFIRMAR_CONTEXTO)
        self.assertTrue(resultado.necessita_confirmacao)
        self.assertIn("elevador", resultado.pergunta.lower())


# ---------------------------------------------------------------------------
# 13-15. Demanda, manutenção e nova ocorrência
# ---------------------------------------------------------------------------


class ContextoAbertoTests(BaseLeituraTests):
    def test_demanda_existente_e_preferida_a_nova(self):
        """13. Com uma demanda aberta no ponto, relaciona — não cria outra."""
        elevador = self.equipamento("Elevador social")
        demanda = self.demanda("Elevador social parado", equipamento=elevador)
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(
            mensagem=mensagem(texto="O elevador social parou de funcionar novamente.")
        )

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(consequencia="nova_ocorrencia"),
        ):
            resultado = self.processar(provedor, item)

        self.assertEqual(resultado.proposta.acao, AcaoProposta.RELACIONAR_DEMANDA)
        self.assertEqual(resultado.proposta.candidato.objeto, demanda)
        self.assertEqual(Demanda.objects.count(), 1)

    def test_manutencao_aberta_e_considerada(self):
        """14. Com uma manutenção aberta compatível, ela é considerada."""
        elevador = self.equipamento("Elevador social")
        manutencao = self.manutencao("Ajuste do elevador social", equipamento=elevador)
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(
            mensagem=mensagem(texto="O elevador social parou de funcionar.")
        )

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(consequencia="nova_ocorrencia"),
        ):
            resultado = self.processar(provedor, item)

        self.assertEqual(resultado.proposta.acao, AcaoProposta.RELACIONAR_MANUTENCAO)
        self.assertEqual(resultado.proposta.candidato.objeto, manutencao)
        self.assertEqual(Manutencao.objects.count(), 1)

    def test_nova_ocorrencia_gera_proposta_e_nao_criacao(self):
        """15. Sem contexto aberto, a leitura propõe — e não cria nada."""
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(
            mensagem=mensagem(texto="Vazamento de água na garagem.")
        )

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(consequencia="nova_ocorrencia"),
        ):
            resultado = self.processar(provedor, item)

        self.assertEqual(resultado.proposta.acao, AcaoProposta.CRIAR_NOVA_DEMANDA)
        self.assertTrue(resultado.necessita_confirmacao)
        self.assertEqual(Demanda.objects.count(), 0)


# ---------------------------------------------------------------------------
# 16-18. Conclusão, silêncio e ambiguidade
# ---------------------------------------------------------------------------


class DecisaoTests(BaseLeituraTests):
    def test_conclusao_declarada_nao_fecha_nada(self):
        """16. O fornecedor declarou conclusão; quem encerra é a gestão."""
        elevador = self.equipamento("Elevador social")
        manutencao = self.manutencao("Ajuste do elevador social", equipamento=elevador)
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(
            mensagem=mensagem(texto="O elevador social foi concluído.")
        )

        resultado = self.processar(provedor, item)

        self.assertEqual(
            resultado.tipo_de_consequencia, Consequencia.CONCLUSAO_DECLARADA
        )
        self.assertEqual(resultado.proposta.acao, AcaoProposta.AVALIAR_CONCLUSAO)
        self.assertTrue(resultado.necessita_confirmacao)

        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)
        self.assertFalse(manutencao.esta_encerrada)

    def test_email_sem_consequencia_pode_terminar_processado(self):
        """17. Recado inequívoco de recebimento termina sem decisão pendente."""
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(mensagem=mensagem(texto="Obrigado, recebido."))

        resultado = self.processar(provedor, item)

        self.assertEqual(resultado.estado_final, EstadoProcessamentoImportacao.PROCESSADO)
        self.assertTrue(resultado.concluiu)
        self.assertEqual(resultado.proposta.acao, AcaoProposta.NENHUMA)
        self.assertFalse(resultado.necessita_confirmacao)

    def test_ambiguidade_termina_aguardando_confirmacao(self):
        """18. Mais de um registro aberto compatível exige decisão humana."""
        elevador = self.equipamento("Elevador social")
        self.demanda("Elevador social parado", equipamento=elevador)
        self.demanda("Barulho no elevador social", equipamento=elevador)
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(
            mensagem=mensagem(texto="O elevador social parou de funcionar.")
        )

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(consequencia="nova_ocorrencia"),
        ):
            resultado = self.processar(provedor, item)

        self.assertEqual(
            resultado.estado_final, EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO
        )
        self.assertTrue(resultado.necessita_confirmacao)
        self.assertEqual(resultado.proposta.acao, AcaoProposta.CONFIRMAR_CONTEXTO)


# ---------------------------------------------------------------------------
# 19-20. Falhas
# ---------------------------------------------------------------------------


class FalhasTests(BaseLeituraTests):
    def test_servico_de_leitura_indisponivel_termina_erro(self):
        """19. Sem leitura semântica, o item vai para ERRO — sem consequência."""
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(
            mensagem=mensagem(texto="Vazamento de água na garagem.")
        )

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json", return_value=None
        ):
            resultado = self.processar(provedor, item)

        self.assertEqual(resultado.estado_final, EstadoProcessamentoImportacao.ERRO)
        self.assertEqual(
            ItemImportacaoHistorica.objects.get(pk=item.pk).estado_processamento,
            EstadoProcessamentoImportacao.ERRO,
        )
        self.assertEqual(Demanda.objects.count(), 0)

    def test_resposta_incompativel_termina_erro(self):
        """20. Consequência fora do vocabulário não é leitura — é falha."""
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(
            mensagem=mensagem(texto="Bom dia, seguem as informações combinadas.")
        )

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value={"consequencia": "categoria_inexistente"},
        ):
            resultado = self.processar(provedor, item)

        self.assertEqual(resultado.estado_final, EstadoProcessamentoImportacao.ERRO)
        self.assertEqual(Demanda.objects.count(), 0)

    def test_gmail_indisponivel_termina_erro(self):
        """O Gmail fora do ar não inventa conteúdo: o item vai para ERRO."""
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(erro=gmail.GmailMensagemInexistente())

        resultado = self.processar(provedor, item)

        self.assertEqual(resultado.estado_final, EstadoProcessamentoImportacao.ERRO)
        self.assertEqual(
            ItemImportacaoHistorica.objects.get(pk=item.pk).estado_processamento,
            EstadoProcessamentoImportacao.ERRO,
        )


# ---------------------------------------------------------------------------
# 21-26. O que a leitura nunca faz
# ---------------------------------------------------------------------------


class NadaCriadoTests(BaseLeituraTests):
    def _ler_ocorrencia(self, item, provedor=None):
        provedor = provedor or ProvedorFalso(
            mensagem=mensagem(texto="Vazamento de água na garagem.")
        )
        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(consequencia="nova_ocorrencia"),
        ):
            return self.processar(provedor, item)

    def test_nenhuma_demanda_e_criada(self):
        """21."""
        self._ler_ocorrencia(criar_item(self.condominio))
        self.assertEqual(Demanda.objects.count(), 0)

    def test_nenhuma_manutencao_e_criada(self):
        """22."""
        self._ler_ocorrencia(criar_item(self.condominio))
        self.assertEqual(Manutencao.objects.count(), 0)

    def test_nenhum_documento_e_criado(self):
        """23. Anexo é contexto: nenhum documento nasce dele."""
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(
            mensagem=mensagem(
                texto="Segue o laudo do elevador.",
                anexos=(gmail.AnexoGmail("att-1", "laudo.pdf", "application/pdf", 2048),),
            )
        )

        self._ler_ocorrencia(item, provedor)

        self.assertEqual(DocumentoOperacional.objects.count(), 0)

    def test_nenhum_vinculo_operacional_e_criado(self):
        """24. A proposta fica em memória; nenhum vínculo é gravado."""
        self._ler_ocorrencia(criar_item(self.condominio))
        self.assertEqual(VinculoImportacaoHistorica.objects.count(), 0)

    def test_nenhum_anexo_e_baixado(self):
        """25."""
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(
            mensagem=mensagem(
                texto="Segue o orçamento do elevador.",
                anexos=(gmail.AnexoGmail("att-1", "orcamento.pdf", "application/pdf", 1024),),
            )
        )

        self._ler_ocorrencia(item, provedor)

        self.assertEqual(provedor.anexos_baixados, [])

    def test_o_contexto_nunca_atravessa_o_condominio(self):
        """26. Equipamento e demanda de outro condomínio não entram na leitura."""
        elevador = self.equipamento("Bomba de recalque", condominio=self.outro)
        self.demanda("Falha na bomba", condominio=self.outro, equipamento=elevador)

        item = criar_item(self.condominio)
        provedor = ProvedorFalso(
            mensagem=mensagem(texto="A bomba de recalque está vazando.")
        )

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(consequencia="nova_ocorrencia"),
        ):
            resultado = self.processar(provedor, item)

        self.assertEqual(candidatos(resultado, TipoCandidato.DEMANDA), [])
        self.assertEqual(candidatos(resultado, TipoCandidato.EQUIPAMENTO), [])
        self.assertEqual(Demanda.objects.count(), 1)
        self.assertEqual(Demanda.objects.get().condominio, self.outro)


# ---------------------------------------------------------------------------
# 27-30. Ciclo, persistência e logs
# ---------------------------------------------------------------------------


class CicloELogsTests(BaseLeituraTests):
    def test_retry_funciona_apos_erro(self):
        """27. Um item em ERRO volta a ser lido pelo mesmo caminho."""
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(
            mensagem=mensagem(texto="Vazamento de água na garagem.")
        )

        with patch("apps.core.email_interpretacao.llm.pedir_json", return_value=None):
            primeira = self.processar(provedor, item)

        self.assertEqual(primeira.estado_final, EstadoProcessamentoImportacao.ERRO)

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(consequencia="nova_ocorrencia"),
        ):
            segunda = self.processar(provedor, item)

        self.assertEqual(
            segunda.estado_final, EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO
        )

    def test_reprocessamento_funciona(self):
        """28. Um item já processado pode ser relido com conteúdo novo."""
        item = criar_item(self.condominio)
        primeiro_provedor = ProvedorFalso(mensagem=mensagem(texto="Obrigado, recebido."))
        primeira = self.processar(primeiro_provedor, item)
        self.assertEqual(primeira.estado_final, EstadoProcessamentoImportacao.PROCESSADO)

        segundo_provedor = ProvedorFalso(
            mensagem=mensagem(texto="Vazamento de água na garagem.")
        )
        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(consequencia="nova_ocorrencia"),
        ):
            segunda = self.processar(segundo_provedor, item)

        self.assertEqual(
            segunda.estado_final, EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO
        )
        self.assertEqual(
            ItemImportacaoHistorica.objects.get(pk=item.pk).estado_processamento,
            EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO,
        )

    def test_nenhuma_resposta_bruta_do_servico_e_persistida(self):
        """29. A resposta da leitura vive em memória; o banco não a guarda."""
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(
            mensagem=mensagem(texto="Vazamento de água na garagem.")
        )

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(
                consequencia="nova_ocorrencia",
                resumo=RESUMO_SIGILOSO,
                pergunta="Confirma?",
            ),
        ):
            resultado = self.processar(provedor, item)

        # A leitura aconteceu e trouxe o resumo — só em memória.
        self.assertTrue(any(i.campo == "resumo" for i in resultado.inferencias))

        campos = " ".join(
            str(valor) for valor in ItemImportacaoHistorica.objects.get(pk=item.pk).__dict__.values()
        )
        self.assertNotIn(RESUMO_SIGILOSO, campos)
        self.assertNotIn(RESUMO_SIGILOSO, str(VinculoImportacaoHistorica.objects.all()))
        self.assertNotIn(RESUMO_SIGILOSO, str(LoteImportacaoHistorica.objects.all()))

    def test_o_log_nao_contem_o_corpo_do_email(self):
        """30. O log registra o desfecho, nunca o texto da mensagem."""
        item = criar_item(self.condominio)
        provedor = ProvedorFalso(
            mensagem=mensagem(texto="Vazamento de água na garagem. " + CORPO_SIGILOSO)
        )

        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value=resposta(consequencia="nova_ocorrencia"),
        ):
            with self.assertLogs("apps.core.email_interpretacao", level="INFO") as capturado:
                self.processar(provedor, item)

        registrado = "\n".join(capturado.output)
        self.assertNotIn(CORPO_SIGILOSO, registrado)


# ---------------------------------------------------------------------------
# Contrato em memória
# ---------------------------------------------------------------------------


class ContratoEmMemoriaTests(BaseLeituraTests):
    def test_admin_do_item_inexistente_e_recusado(self):
        """Um item sem identidade não entra no ciclo."""
        with self.assertRaises(ValidationError):
            email_interpretacao.processar_item_gmail(None, provedor=ProvedorFalso())

    def test_item_sem_message_id_e_recusado(self):
        """Um item do Gmail sem ``message_id`` não tem o que recuperar."""
        item = criar_item(self.condominio)
        ItemImportacaoHistorica.objects.filter(pk=item.pk).update(message_id="")
        item.refresh_from_db()

        with self.assertRaises(OrigemNaoSuportada):
            email_interpretacao.processar_item_gmail(item, provedor=ProvedorFalso())

    def test_proposta_sem_acao_e_o_silencio(self):
        """``NENHUMA`` é resposta: há algo a decidir apenas quando a ação existe."""
        from apps.core.email_interpretacao import Proposta

        self.assertFalse(Proposta().existe)
        self.assertTrue(Proposta(acao=AcaoProposta.CONFIRMAR_CONTEXTO).existe)

    def test_candidato_nao_carrega_identificador_tecnico_no_rotulo(self):
        """O rótulo é o que a pessoa lê; o objeto real fica no backend."""
        candidato = Candidato(tipo=TipoCandidato.DEMANDA, rotulo="Elevador social parado")
        self.assertEqual(candidato.rotulo, "Elevador social parado")
        self.assertIsNone(candidato.objeto)
