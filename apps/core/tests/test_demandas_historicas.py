"""Testes da carga histórica de Demandas a partir do e-mail.

A pergunta que estes testes cobram é a da frente:

    Entrou uma informação nova. Isso muda alguma coisa que mereça atenção?

O provedor do Gmail e a leitura semântica são sempre falsos — nenhum teste toca
a rede. O que se verifica é o contrato da carga: o que conta como ocorrência, o
que se agrupa como a mesma conversa, quando se relaciona a uma demanda que já
existe e quando se cria uma nova, o que fica de fora e — o mais importante — o
que a carga **não** faz: não funde na dúvida, não inventa local nem
equipamento, não escolhe prioridade, não persiste corpo de e-mail nem resposta
da leitura, e nada grava sem quem responda por ela.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.cadastros.models import Fornecedor
from apps.cadastros.tests.apoio import (
    criar_equipamento,
    criar_fornecedor,
    criar_local,
    criar_pessoa,
    criar_vinculo,
)
from apps.condominios.constants import Papel
from apps.condominios.models import Condominio
from apps.core import demandas_historicas, gmail
from apps.core.constants import (
    EntidadeImportacaoHistorica,
    EstadoProcessamentoImportacao,
    OrigemImportacaoHistorica,
    ResultadoImportacaoHistorica,
)
from apps.core.demandas_historicas import (
    CHAVE_DA_DEMANDA,
    CargaSemResponsavel,
    DecisaoDaCarga,
    EntradaDaCarga,
    ErroDaCargaHistorica,
    NaturezaDaMensagem,
)
from apps.core.email_interpretacao import (
    Consequencia,
    LeituraIndisponivel,
    interpretar,
    reconciliar,
)
from apps.core.models import (
    ItemImportacaoHistorica,
    LoteImportacaoHistorica,
    VinculoImportacaoHistorica,
)
from apps.demandas.constants import (
    OrigemDemanda,
    PrioridadeDemanda,
    StatusDemanda,
    TipoEventoDemanda,
    TipoProblema,
)
from apps.demandas.models import Demanda
from apps.demandas.services import criar_demanda

CONTA = "fernandaduccini.rve@gmail.com"
REMETENTE = "financeira@atlas.com.br"
MOMENTO = datetime(2026, 10, 27, 10, 30, tzinfo=timezone.utc)

CORPO_SIGILOSO = "O elevador parou no oitavo andar; não repita isto em lugar nenhum."

CLIENTE = "cliente-de-teste.apps.googleusercontent.com"
SEGREDO = "segredo-de-teste-nao-vaza"  # noqa: S105 — é de teste, não é real
REFRESH = "refresh-de-teste-nao-vaza"

CONFIGURADO = {
    "EITHRA_GMAIL_CLIENT_ID": CLIENTE,
    "EITHRA_GMAIL_CLIENT_SECRET": SEGREDO,
    "EITHRA_GMAIL_REFRESH_TOKEN": REFRESH,
    "EITHRA_GMAIL_CONTA": CONTA,
}


# ---------------------------------------------------------------------------
# Dublês
# ---------------------------------------------------------------------------


def interpretador(consequencia=Consequencia.NOVA_OCORRENCIA, *, por_mensagem=None):
    """Uma leitura falsa: devolve a consequência sem tocar o serviço de linguagem."""

    def ler(mensagem, *, condominio=None):
        if por_mensagem is not None:
            return SimpleNamespace(
                tipo_de_consequencia=por_mensagem.get(mensagem.message_id, "")
            )
        return SimpleNamespace(tipo_de_consequencia=consequencia)

    return ler


def interpretador_indisponivel():
    """Uma leitura que falha, como o serviço fora do ar."""

    def ler(mensagem, *, condominio=None):
        raise LeituraIndisponivel()

    return ler


def mensagem(
    *,
    message_id="msg-1",
    thread_id="thread-1",
    remetente=REMETENTE,
    assunto="Vazamento na garagem",
    texto="Vazamento de água na garagem.",
    momento=MOMENTO,
    anexos=(),
):
    """Uma MensagemGmail normalizada, como o provider devolveria."""
    return gmail.MensagemGmail(
        message_id=message_id,
        thread_id=thread_id,
        remetente=remetente,
        destinatarios=("sindica@exemplo.test",),
        assunto=assunto,
        momento=momento,
        texto=texto,
        anexos=tuple(anexos),
    )


class ProvedorFalso:
    """Um provedor de mentira: entrega o que o teste mandar, sem rede."""

    def __init__(self, *, paginas=None, mensagens=None, erro_ao_listar=None):
        self.paginas = list(paginas or [])
        self.mensagens = dict(mensagens or {})
        self.erro_ao_listar = erro_ao_listar
        self.chamadas_de_listagem = []
        self.mensagens_pedidas = []
        self.anexos_baixados = []
        self._proxima = 0

    def listar_mensagens(self, *, consulta="", quantidade=0, pagina_token=""):
        self.chamadas_de_listagem.append(
            {"consulta": consulta, "quantidade": quantidade, "pagina_token": pagina_token}
        )
        if self.erro_ao_listar is not None:
            raise self.erro_ao_listar
        if self._proxima >= len(self.paginas):
            return gmail.PaginaDeMensagens()
        pagina = self.paginas[self._proxima]
        self._proxima += 1
        return pagina

    def obter_mensagem(self, message_id):
        self.mensagens_pedidas.append(message_id)
        resultado = self.mensagens.get(message_id)
        if isinstance(resultado, Exception):
            raise resultado
        if resultado is None:
            raise gmail.GmailMensagemInexistente()
        return resultado

    def baixar_anexo(self, message_id, attachment_id):
        self.anexos_baixados.append((message_id, attachment_id))
        raise AssertionError("Nenhum anexo deve ser baixado nesta fase.")


def referencia(message_id, thread_id="thread-1"):
    return gmail.ReferenciaMensagem(message_id=message_id, thread_id=thread_id)


def pagina(*identificadores, proxima=""):
    return gmail.PaginaDeMensagens(
        itens=tuple(referencia(i) for i in identificadores),
        proxima_pagina=proxima,
    )


def criar_lote(condominio, *, origem=OrigemImportacaoHistorica.GMAIL):
    return LoteImportacaoHistorica.objects.create(
        condominio=condominio, origem=origem, conta_origem=CONTA
    )


def criar_item(
    condominio,
    *,
    message_id="msg-1",
    thread_id="thread-1",
    remetente=REMETENTE,
    assunto="",
):
    """Um item da carga histórica, como a ingestão o teria deixado."""
    item, _ = ItemImportacaoHistorica.objects.registrar_item(
        lote=criar_lote(condominio),
        identificador_externo=message_id,
        message_id=message_id,
        thread_id=thread_id,
        remetente=remetente,
        assunto=assunto,
    )
    return item


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class BaseCargaTests(TestCase):
    def setUp(self):
        self.condominio = Condominio.objects.create(nome="Condomínio de Teste")
        self.outro = Condominio.objects.create(nome="Outro Condomínio")
        self.gestao = criar_pessoa("gestao.carga@exemplo.test", "Gestão da carga")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)

    def ler(self, msg, consequencia=Consequencia.NOVA_OCORRENCIA, *, condominio=None):
        """Lê uma mensagem pelas mesmas regras da carga, com leitura falsa."""
        return demandas_historicas.ler_mensagem(
            EntradaDaCarga(mensagem=msg),
            condominio=condominio or self.condominio,
            interpretador=interpretador(consequencia),
        )

    def entrada(self, msg, *, item):
        return EntradaDaCarga(mensagem=msg, item=item)

    def demanda(self, titulo, *, condominio=None):
        return Demanda.objects.create(
            condominio=condominio or self.condominio,
            criado_por=self.gestao,
            titulo=titulo,
        )

    def vincular(self, item, demanda):
        return VinculoImportacaoHistorica.objects.registrar_vinculo(
            item=item,
            entidade_destino=EntidadeImportacaoHistorica.DEMANDA,
            identificador_destino=CHAVE_DA_DEMANDA,
            destino_id=demanda.pk,
            resultado=ResultadoImportacaoHistorica.CRIADO,
        )


# ---------------------------------------------------------------------------
# 1-4. Classificação da mensagem
# ---------------------------------------------------------------------------


class ClassificacaoTests(BaseCargaTests):
    def test_ocorrencia_e_conclusao_sao_as_unicas_naturezas(self):
        """1. Só anunciar ocorrência ou declarar conclusão sustenta decisão."""
        self.assertEqual(
            demandas_historicas.natureza_da(Consequencia.NOVA_OCORRENCIA),
            NaturezaDaMensagem.OCORRENCIA,
        )
        self.assertEqual(
            demandas_historicas.natureza_da(Consequencia.CONCLUSAO_DECLARADA),
            NaturezaDaMensagem.CONCLUSAO,
        )

    def test_atualizacao_cobranca_ambiguidade_sao_sem_ocorrencia(self):
        """2. O resto é contexto — nunca ocorrência por si só."""
        for consequencia in (
            Consequencia.ATUALIZACAO,
            Consequencia.COBRANCA,
            Consequencia.AGENDAMENTO,
            Consequencia.DOCUMENTO,
            Consequencia.AMBIGUO,
            Consequencia.SEM_ACAO,
            "",
        ):
            self.assertEqual(
                demandas_historicas.natureza_da(consequencia),
                NaturezaDaMensagem.SEM_OCORRENCIA,
            )

    def test_a_leitura_deriva_local_equipamento_e_natureza_do_cadastro(self):
        """3. Local e equipamento só entram quando o cadastro confirma o nome."""
        local = criar_local(self.condominio, nome="Garagem")
        equipamento = criar_equipamento(self.condominio, nome="Elevador social")
        msg = mensagem(
            assunto="Vazamento na garagem",
            texto="Vazamento de água na garagem, perto do elevador social.",
        )

        leitura = self.ler(msg)

        self.assertTrue(leitura.ocorrencia)
        self.assertEqual(leitura.local, local)
        self.assertEqual(leitura.equipamento, equipamento)
        self.assertEqual(leitura.tipo_de_problema, "vazamento")

    def test_a_cena_sem_palavra_declarada_nao_vira_natureza(self):
        """4. A suposição do Eithra não entra na descrição — só o declarado."""
        msg = mensagem(
            assunto="Água aparecendo",
            texto="Tem água na parede do corredor do térreo.",
        )

        leitura = self.ler(msg)

        self.assertEqual(leitura.tipo_de_problema, "")


# ---------------------------------------------------------------------------
# 5-9. Agrupamento
# ---------------------------------------------------------------------------


class AgrupamentoTests(BaseCargaTests):
    def test_o_mesmo_fio_reune_as_mensagens(self):
        """5. O fio da conversa é o primeiro sinal de que é a mesma coisa."""
        primeira = self.ler(mensagem(message_id="m1", thread_id="t1"))
        segunda = self.ler(
            mensagem(message_id="m2", thread_id="t1", momento=MOMENTO + timedelta(days=2))
        )

        grupos = demandas_historicas.agrupar([primeira, segunda])

        self.assertEqual(len(grupos), 1)
        self.assertEqual(len(grupos[0].leituras), 2)

    def test_assunto_remetente_e_proximidade_reunem_sem_fio(self):
        """6. Sem fio, assunto e remetente iguais e perto no tempo bastam."""
        primeira = self.ler(
            mensagem(message_id="m1", thread_id="", assunto="Portão da garagem")
        )
        segunda = self.ler(
            mensagem(
                message_id="m2",
                thread_id="",
                assunto="Portão da garagem",
                momento=MOMENTO + timedelta(days=3),
            )
        )

        grupos = demandas_historicas.agrupar([primeira, segunda])

        self.assertEqual(len(grupos), 1)

    def test_longe_demais_no_tempo_separa_sem_fio(self):
        """7. Fora da janela, o mesmo assunto pode ser outra ocorrência."""
        primeira = self.ler(
            mensagem(message_id="m1", thread_id="", assunto="Portão da garagem")
        )
        segunda = self.ler(
            mensagem(
                message_id="m2",
                thread_id="",
                assunto="Portão da garagem",
                momento=MOMENTO + timedelta(days=30),
            )
        )

        grupos = demandas_historicas.agrupar([primeira, segunda])

        self.assertEqual(len(grupos), 2)

    def test_equipamento_diferente_separa_mesmo_no_mesmo_fio(self):
        """8. O fio não é absoluto: dois equipamentos são duas ocorrências."""
        criar_equipamento(self.condominio, nome="Elevador social")
        criar_equipamento(self.condominio, nome="Bomba de recalque")
        primeira = self.ler(
            mensagem(
                message_id="m1",
                thread_id="t1",
                texto="O elevador social parou de funcionar.",
            )
        )
        segunda = self.ler(
            mensagem(
                message_id="m2",
                thread_id="t1",
                momento=MOMENTO + timedelta(hours=2),
                texto="A bomba de recalque está vazando.",
            )
        )

        grupos = demandas_historicas.agrupar([primeira, segunda])

        self.assertEqual(len(grupos), 2)

    def test_local_diferente_separa_mesmo_no_mesmo_fio(self):
        """9. Dois locais confirmados são duas ocorrências — não uma."""
        criar_local(self.condominio, nome="Garagem")
        criar_local(self.condominio, nome="Cobertura")
        primeira = self.ler(
            mensagem(
                message_id="m1",
                thread_id="t1",
                texto="Vazamento de água na garagem.",
            )
        )
        segunda = self.ler(
            mensagem(
                message_id="m2",
                thread_id="t1",
                momento=MOMENTO + timedelta(hours=2),
                texto="Vazamento de água na cobertura.",
            )
        )

        grupos = demandas_historicas.agrupar([primeira, segunda])

        self.assertEqual(len(grupos), 2)


# ---------------------------------------------------------------------------
# 10-13. Duplicidade e correspondência
# ---------------------------------------------------------------------------


class DuplicidadeTests(BaseCargaTests):
    def test_vinculo_existente_manda_relacionar(self):
        """10. Mensagem já ligada a uma demanda relaciona, não cria outra."""
        msg = mensagem(message_id="m1", thread_id="t1")
        item = criar_item(self.condominio, message_id="m1", thread_id="t1")
        demanda = self.demanda("Vazamento na garagem")
        self.vincular(item, demanda)

        grupo = demandas_historicas.agrupar(
            [self.ler(msg, condominio=self.condominio)]
        )[0]
        plano = demandas_historicas.planejar(grupo, condominio=self.condominio)

        self.assertEqual(plano.decisao, DecisaoDaCarga.RELACIONAR_EXISTENTE)
        self.assertEqual(plano.demanda, demanda)

    def test_fio_ja_ligado_em_outra_passagem_relaciona(self):
        """11. O fio ligado a uma única demanda, em outra passagem, relaciona."""
        anterior = criar_item(self.condominio, message_id="m0", thread_id="t1")
        demanda = self.demanda("Vazamento na garagem")
        self.vincular(anterior, demanda)

        msg = mensagem(message_id="m1", thread_id="t1")
        item = criar_item(self.condominio, message_id="m1", thread_id="t1")
        leitura = demandas_historicas.ler_mensagem(
            EntradaDaCarga(mensagem=msg, item=item),
            condominio=self.condominio,
            interpretador=interpretador(),
        )
        grupo = demandas_historicas.agrupar([leitura])[0]

        plano = demandas_historicas.planejar(grupo, condominio=self.condominio)

        self.assertEqual(plano.decisao, DecisaoDaCarga.RELACIONAR_EXISTENTE)
        self.assertEqual(plano.demanda, demanda)

    def test_sem_seguranca_de_duplicidade_cria_separada(self):
        """12. Mais de uma candidata não é segurança: cria nova."""
        primeira = criar_item(self.condominio, message_id="m1", thread_id="t1")
        segunda = criar_item(self.condominio, message_id="m2", thread_id="t1")
        self.vincular(primeira, self.demanda("Vazamento um"))
        self.vincular(segunda, self.demanda("Vazamento dois"))

        leituras = [
            demandas_historicas.ler_mensagem(
                EntradaDaCarga(mensagem=mensagem(message_id=mid, thread_id="t1"), item=item),
                condominio=self.condominio,
                interpretador=interpretador(),
            )
            for mid, item in (("m1", primeira), ("m2", segunda))
        ]
        grupo = demandas_historicas.agrupar(leituras)[0]

        plano = demandas_historicas.planejar(grupo, condominio=self.condominio)

        self.assertEqual(plano.decisao, DecisaoDaCarga.CRIAR)

    def test_o_contexto_nunca_atravessa_o_condominio(self):
        """13. Vínculo de outro condomínio não conta como correspondência."""
        outra = criar_item(self.outro, message_id="m1", thread_id="t1")
        self.vincular(outra, self.demanda("Vazamento do outro", condominio=self.outro))

        msg = mensagem(message_id="m1", thread_id="t1")
        item = criar_item(self.condominio, message_id="m1", thread_id="t1")
        leitura = demandas_historicas.ler_mensagem(
            EntradaDaCarga(mensagem=msg, item=item),
            condominio=self.condominio,
            interpretador=interpretador(),
        )
        grupo = demandas_historicas.agrupar([leitura])[0]

        plano = demandas_historicas.planejar(grupo, condominio=self.condominio)

        self.assertEqual(plano.decisao, DecisaoDaCarga.CRIAR)


# ---------------------------------------------------------------------------
# 14-18. Criação da demanda
# ---------------------------------------------------------------------------


class CriacaoTests(BaseCargaTests):
    def _importar_uma(self, *, texto=None, assunto=None):
        local = criar_local(self.condominio, nome="Garagem")
        equipamento = criar_equipamento(self.condominio, nome="Elevador social")
        msg = mensagem(
            message_id="m1",
            thread_id="t1",
            assunto=assunto or "Vazamento na garagem",
            texto=texto or "Vazamento de água na garagem, perto do elevador social.",
        )
        item = criar_item(
            self.condominio, message_id="m1", thread_id="t1", assunto=msg.assunto
        )
        resumo = demandas_historicas.importar(
            [self.entrada(msg, item=item)],
            condominio=self.condominio,
            usuario=self.gestao,
            interpretador=interpretador(),
        )
        return resumo, local, equipamento, item

    def test_a_ocorrencia_vira_demanda(self):
        """14. Ocorrência relatada registra a demanda, com autor e origem."""
        resumo, _, _, _ = self._importar_uma()

        self.assertEqual(len(resumo.criadas), 1)
        self.assertEqual(Demanda.objects.count(), 1)

        demanda = Demanda.objects.get()
        self.assertEqual(demanda.titulo, "Vazamento na garagem")
        self.assertEqual(demanda.origem, OrigemDemanda.EMAIL)
        self.assertEqual(demanda.criado_por, self.gestao)

    def test_a_prioridade_nao_e_escolhida_aqui(self):
        """15. Quem escolhe o peso é o relato, nunca o importador."""
        self._importar_uma()
        self.assertEqual(Demanda.objects.get().prioridade, PrioridadeDemanda.NORMAL)

    def test_a_descricao_e_factual_e_sem_corpo_de_email(self):
        """16. A descrição diz o que sustenta — e nada do corpo cru."""
        self._importar_uma()

        demanda = Demanda.objects.get()
        self.assertIn("Registro recebido por e-mail.", demanda.descricao)
        self.assertIn(f"Natureza: {TipoProblema.VAZAMENTO.label}.", demanda.descricao)
        self.assertNotIn(CORPO_SIGILOSO, demanda.descricao)

    def test_local_e_equipamento_confirmados_entram_na_demanda(self):
        """17. Só entra local e equipamento que o cadastro confirma."""
        _, local, equipamento, _ = self._importar_uma()

        demanda = Demanda.objects.get()
        self.assertEqual(demanda.local, local)
        self.assertEqual(demanda.equipamento, equipamento)

    def test_a_proveniencia_e_o_item_sao_fechados(self):
        """18. O vínculo grava de onde veio; o item termina processado."""
        resumo, _, _, item = self._importar_uma()
        demanda = Demanda.objects.get()

        vinculo = VinculoImportacaoHistorica.objects.get()
        self.assertEqual(vinculo.identificador_destino, CHAVE_DA_DEMANDA)
        self.assertEqual(vinculo.destino_id, demanda.pk)
        self.assertEqual(vinculo.item, item)
        self.assertEqual(vinculo.resultado, ResultadoImportacaoHistorica.CRIADO)

        item.refresh_from_db()
        self.assertEqual(
            item.estado_processamento, EstadoProcessamentoImportacao.PROCESSADO
        )
        self.assertEqual(len(resumo.demandas), 1)


# ---------------------------------------------------------------------------
# 19-21. Conclusão declarada
# ---------------------------------------------------------------------------


class ConclusaoTests(BaseCargaTests):
    def _importar_ocorrencia_e_conclusao(self):
        ocorrencia = mensagem(
            message_id="m1",
            thread_id="t1",
            texto="Vazamento de água na garagem.",
            momento=MOMENTO,
        )
        conclusao = mensagem(
            message_id="m2",
            thread_id="t1",
            texto="O vazamento foi consertado, obrigado.",
            momento=MOMENTO + timedelta(days=1),
        )
        item1 = criar_item(self.condominio, message_id="m1", thread_id="t1")
        item2 = criar_item(self.condominio, message_id="m2", thread_id="t1")
        ler = interpretador(
            por_mensagem={
                "m1": Consequencia.NOVA_OCORRENCIA,
                "m2": Consequencia.CONCLUSAO_DECLARADA,
            }
        )
        return demandas_historicas.importar(
            [
                EntradaDaCarga(mensagem=ocorrencia, item=item1),
                EntradaDaCarga(mensagem=conclusao, item=item2),
            ],
            condominio=self.condominio,
            usuario=self.gestao,
            interpretador=ler,
        )

    def test_conclusao_declarada_encerra_a_demanda(self):
        """19. A conclusão declarada é aplicada pelo mecanismo oficial."""
        resumo = self._importar_ocorrencia_e_conclusao()

        self.assertEqual(len(resumo.criadas), 1)
        demanda = Demanda.objects.get()
        self.assertEqual(demanda.status, StatusDemanda.CONCLUIDA)
        self.assertIsNotNone(demanda.concluido_em)

    def test_a_conclusao_registra_o_evento_no_historico(self):
        """20. Encerrar deixa memória: o histórico registra a conclusão."""
        self._importar_ocorrencia_e_conclusao()

        demanda = Demanda.objects.get()
        self.assertTrue(
            demanda.historico.filter(tipo_evento=TipoEventoDemanda.CONCLUIDA).exists()
        )

    def test_a_conversa_reunida_vira_uma_demanda_so(self):
        """21. Ocorrência e conclusão no mesmo fio são uma ocorrência."""
        self._importar_ocorrencia_e_conclusao()
        self.assertEqual(Demanda.objects.count(), 1)


# ---------------------------------------------------------------------------
# 22-25. Responsável e permissão
# ---------------------------------------------------------------------------


class ResponsavelTests(BaseCargaTests):
    def _entrada_simples(self):
        msg = mensagem(message_id="m1", thread_id="t1")
        item = criar_item(self.condominio, message_id="m1", thread_id="t1")
        return self.entrada(msg, item=item)

    def test_sem_responsavel_nada_e_gravado(self):
        """22. A execução definitiva exige quem responda por ela."""
        with self.assertRaises(CargaSemResponsavel):
            demandas_historicas.importar(
                [self._entrada_simples()],
                condominio=self.condominio,
                usuario=None,
                interpretador=interpretador(),
            )

        self.assertEqual(Demanda.objects.count(), 0)

    def test_quem_nao_acessa_o_condominio_nao_responde(self):
        """23. Sem vínculo ativo, não há como responder pela carga."""
        de_fora = criar_pessoa("de.fora@exemplo.test", "De fora")
        with self.assertRaises(CargaSemResponsavel):
            demandas_historicas.exigir_responsavel(de_fora, self.condominio)

    def test_quem_apenas_opera_nao_cria_demanda_por_carga(self):
        """24. Quem não registra demanda não assina a carga."""
        operacional = criar_pessoa("operacao@exemplo.test", "Operação")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        with self.assertRaises(CargaSemResponsavel):
            demandas_historicas.exigir_responsavel(operacional, self.condominio)

    def test_mensagem_sem_registro_de_origem_recusa_fora_da_simulacao(self):
        """25. Importar de verdade exige o item de origem de cada mensagem."""
        with self.assertRaises(ErroDaCargaHistorica):
            demandas_historicas.importar(
                [EntradaDaCarga(mensagem=mensagem())],
                condominio=self.condominio,
                usuario=self.gestao,
                interpretador=interpretador(),
            )


# ---------------------------------------------------------------------------
# 26-29. Simulação e idempotência
# ---------------------------------------------------------------------------


class SimulacaoEIdempotenciaTests(BaseCargaTests):
    def _entrada(self, message_id="m1"):
        msg = mensagem(message_id=message_id, thread_id="t1")
        item = criar_item(self.condominio, message_id=message_id, thread_id="t1")
        return self.entrada(msg, item=item)

    def test_a_simulacao_nao_grava_nada(self):
        """26. A simulação decide sem escrever: nem demanda, nem vínculo."""
        resumo = demandas_historicas.importar(
            [self._entrada()],
            condominio=self.condominio,
            interpretador=interpretador(),
            simulado=True,
        )

        self.assertTrue(resumo.simulado)
        self.assertEqual(len(resumo.criadas), 1)
        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(VinculoImportacaoHistorica.objects.count(), 0)

    def test_a_simulacao_nao_mexe_no_item(self):
        """27. Simular não move o item: ele continua onde estava."""
        entrada = self._entrada()
        demandas_historicas.importar(
            [entrada],
            condominio=self.condominio,
            interpretador=interpretador(),
            simulado=True,
        )

        entrada.item.refresh_from_db()
        self.assertEqual(
            entrada.item.estado_processamento, EstadoProcessamentoImportacao.PENDENTE
        )

    def test_rodar_duas_vezes_nao_duplica_a_demanda(self):
        """28. A segunda passagem relaciona a existente em vez de criar outra."""
        demandas_historicas.importar(
            [self._entrada()],
            condominio=self.condominio,
            usuario=self.gestao,
            interpretador=interpretador(),
        )
        segundo = demandas_historicas.importar(
            [self._entrada()],
            condominio=self.condominio,
            usuario=self.gestao,
            interpretador=interpretador(),
        )

        self.assertEqual(Demanda.objects.count(), 1)
        self.assertEqual(VinculoImportacaoHistorica.objects.count(), 1)
        self.assertEqual(len(segundo.relacionadas), 1)

    def test_leitura_indisponivel_marca_o_item_em_erro(self):
        """29. O que não deu para ler vira falha auditável, não silêncio."""
        entrada = self._entrada()
        resumo = demandas_historicas.importar(
            [entrada],
            condominio=self.condominio,
            usuario=self.gestao,
            interpretador=interpretador_indisponivel(),
        )

        self.assertEqual(len(resumo.falhas), 1)
        self.assertEqual(Demanda.objects.count(), 0)
        entrada.item.refresh_from_db()
        self.assertEqual(
            entrada.item.estado_processamento, EstadoProcessamentoImportacao.ERRO
        )


# ---------------------------------------------------------------------------
# 30-36. O comando, ponta a ponta
# ---------------------------------------------------------------------------


@override_settings(**CONFIGURADO)
class CommandTests(BaseCargaTests):
    def setUp(self):
        super().setUp()
        self.saida = StringIO()

    def executar(self, provedor, *argumentos, **opcoes):
        opcoes.setdefault("stdout", self.saida)
        with patch(
            "apps.core.email_interpretacao.llm.pedir_json",
            return_value={"consequencia": "nova_ocorrencia"},
        ), patch(
            "apps.core.management.commands.importar_demandas_historicas.obter_provedor",
            return_value=provedor,
        ):
            return call_command("importar_demandas_historicas", *argumentos, **opcoes)

    def executar_com(self, provedor, **opcoes):
        return self.executar(
            provedor, condominio=str(self.condominio.pk), limite=10, **opcoes
        )

    def provedor(self, *ids):
        return ProvedorFalso(
            paginas=[pagina(*ids)],
            mensagens={i: mensagem(message_id=i, thread_id="t1") for i in ids},
        )

    @property
    def texto_da_saida(self):
        return self.saida.getvalue()

    def test_importa_a_demanda_e_o_item(self):
        """30. A rodada definitiva grava a demanda, o item e o lote."""
        self.executar_com(
            self.provedor("msg-1"), responsavel=self.gestao.email
        )

        self.assertEqual(Demanda.objects.count(), 1)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 1)
        self.assertEqual(LoteImportacaoHistorica.objects.count(), 1)
        self.assertIn("Demandas registradas: 1", self.texto_da_saida)

    def test_a_demanda_nasce_do_email_e_com_autor(self):
        """31. A origem é e-mail; quem assina é o responsável informado."""
        self.executar_com(self.provedor("msg-1"), responsavel=self.gestao.email)

        demanda = Demanda.objects.get()
        self.assertEqual(demanda.origem, OrigemDemanda.EMAIL)
        self.assertEqual(demanda.criado_por, self.gestao)

    def test_execucao_definitiva_sem_responsavel_recusa(self):
        """32. Sem responsável, nada é gravado."""
        with self.assertRaises(CommandError):
            self.executar_com(self.provedor("msg-1"))

        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 0)

    def test_responsavel_sem_cadastro_recusa(self):
        """33. E-mail sem pessoa cadastrada não responde pela carga."""
        with self.assertRaises(CommandError):
            self.executar_com(
                self.provedor("msg-1"), responsavel="ninguem@exemplo.test"
            )

    def test_a_simulacao_nao_grava_nem_exige_responsavel(self):
        """34. A simulação lê, decide e não escreve nada."""
        self.executar_com(self.provedor("msg-1", "msg-2"), dry_run=True)

        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 0)
        self.assertEqual(LoteImportacaoHistorica.objects.count(), 0)
        self.assertIn("simulação", self.texto_da_saida)

    def test_rodar_duas_vezes_nao_duplica(self):
        """35. A segunda rodada relaciona a demanda já criada."""
        self.executar_com(self.provedor("msg-1"), responsavel=self.gestao.email)
        self.executar_com(self.provedor("msg-1"), responsavel=self.gestao.email)

        self.assertEqual(Demanda.objects.count(), 1)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 1)
        self.assertEqual(VinculoImportacaoHistorica.objects.count(), 1)

    def test_o_resumo_nao_vaza_segredo_nem_corpo(self):
        """36. O relato da rodada é operacional — sem corpo e sem segredo."""
        self.executar_com(self.provedor("msg-1"), responsavel=self.gestao.email)

        saida = self.texto_da_saida
        self.assertNotIn(SEGREDO, saida)
        self.assertNotIn(REFRESH, saida)
        self.assertNotIn(CORPO_SIGILOSO, saida)
        self.assertIn("Condomínio de Teste", saida)

    def test_a_caixa_da_portaria_nao_alimenta_esta_carga(self):
        """A caixa da Portaria é recusada antes de ler — e nada é gravado."""
        provedor = self.provedor("msg-1")
        with override_settings(EITHRA_GMAIL_CONTA="rve79portaria@gmail.com"):
            with self.assertRaises(CommandError):
                self.executar_com(provedor, responsavel=self.gestao.email)

        self.assertEqual(provedor.chamadas_de_listagem, [])
        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 0)
        self.assertEqual(LoteImportacaoHistorica.objects.count(), 0)
        self.assertEqual(VinculoImportacaoHistorica.objects.count(), 0)

    def test_a_caixa_de_demandas_roda_normalmente(self):
        """A conta de Demandas é a única caixa que alimenta esta carga."""
        with override_settings(EITHRA_GMAIL_CONTA=CONTA):
            self.executar_com(self.provedor("msg-1"), responsavel=self.gestao.email)

        self.assertEqual(Demanda.objects.count(), 1)

    def test_a_conta_e_comparada_sem_depender_do_formato(self):
        """Maiúsculas e espaço sobrando não mudam a caixa declarada."""
        with override_settings(EITHRA_GMAIL_CONTA="  Fernandaduccini.RVE@GMAIL.com  "):
            self.executar_com(self.provedor("msg-1"), responsavel=self.gestao.email)

        self.assertEqual(Demanda.objects.count(), 1)


# ---------------------------------------------------------------------------
# Semântica do relato: o que vira demanda e o que não conclui
# ---------------------------------------------------------------------------


class SemanticaDoRelatoTests(BaseCargaTests):
    """A leitura de verdade decide o destino — sem keyword nova, sem regra nova.

    Estes testes usam ``email_interpretacao`` como ele é: nada é dublado na
    decisão. O serviço de linguagem é o único ponto silenciado, e devolve um
    dicionário vazio — o determinístico já decide o texto, e o serviço não
    acrescenta nada a ele.
    """

    def _importar_relatos(self, *relatos):
        entradas = []
        for relato in relatos:
            message_id = relato["message_id"]
            fio = relato.get("thread_id", "t1")
            msg = mensagem(
                message_id=message_id,
                thread_id=fio,
                assunto=relato.get("assunto", ""),
                texto=relato["texto"],
                momento=relato.get("momento", MOMENTO),
            )
            item = criar_item(self.condominio, message_id=message_id, thread_id=fio)
            entradas.append(self.entrada(msg, item=item))

        with patch("apps.core.email_interpretacao.llm.pedir_json", return_value={}):
            return demandas_historicas.importar(
                entradas, condominio=self.condominio, usuario=self.gestao
            )

    def test_a_compra_operacional_vira_demanda(self):
        """Uma compra pedida no relato é ocorrência — e registra a demanda."""
        resumo = self._importar_relatos(
            {
                "message_id": "m1",
                "texto": "Precisamos comprar uma nova bomba para substituir a que queimou.",
            }
        )

        self.assertEqual(len(resumo.criadas), 1)
        self.assertEqual(resumo.grupos[0].decisao, DecisaoDaCarga.CRIAR)
        self.assertEqual(Demanda.objects.count(), 1)

    def test_o_comprovante_isolado_nao_cria_demanda(self):
        """Um comprovante isolado é contexto: não anuncia ocorrência nova."""
        resumo = self._importar_relatos(
            {"message_id": "m1", "texto": "Segue comprovante de pagamento da bomba."}
        )

        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(len(resumo.ignoradas), 1)
        self.assertEqual(resumo.grupos[0].decisao, DecisaoDaCarga.IGNORAR)

    def test_o_silencio_nao_conclui(self):
        """3. Sem mensagem de resolução, a ocorrência não encerra sozinha."""
        self._importar_relatos(
            {"message_id": "m1", "texto": "Vazamento de água na garagem."}
        )

        demanda = Demanda.objects.get()
        self.assertNotEqual(demanda.status, StatusDemanda.CONCLUIDA)
        self.assertFalse(
            demanda.historico.filter(tipo_evento=TipoEventoDemanda.CONCLUIDA).exists()
        )

    def test_o_fim_da_thread_nao_conclui(self):
        """4. Mensagem neutra depois do relato não é evidência de resolução."""
        self._importar_relatos(
            {
                "message_id": "m1",
                "texto": "Vazamento de água na garagem.",
                "assunto": "Vazamento na garagem",
            },
            {
                "message_id": "m2",
                "texto": "Ok, ciente.",
                "assunto": "Vazamento na garagem",
                "momento": MOMENTO + timedelta(days=2),
            },
        )

        self.assertEqual(Demanda.objects.count(), 1)
        demanda = Demanda.objects.get()
        self.assertNotEqual(demanda.status, StatusDemanda.CONCLUIDA)
        self.assertFalse(
            demanda.historico.filter(tipo_evento=TipoEventoDemanda.CONCLUIDA).exists()
        )

    def test_o_tempo_decorrido_nao_conclui(self):
        """5. Antiguidade não é resolução: o tempo não encerra o que não se resolveu."""
        self._importar_relatos(
            {
                "message_id": "m1",
                "texto": "O elevador social parou de funcionar.",
                "assunto": "Elevador parado",
                "momento": MOMENTO - timedelta(days=400),
            }
        )

        demanda = Demanda.objects.get()
        self.assertNotEqual(demanda.status, StatusDemanda.CONCLUIDA)
        self.assertFalse(
            demanda.historico.filter(tipo_evento=TipoEventoDemanda.CONCLUIDA).exists()
        )


# ---------------------------------------------------------------------------
# O que o cadastro não confirma não é inventado
# ---------------------------------------------------------------------------


class CamposIncertosTests(BaseCargaTests):
    """Nome que o cadastro não confirma continua sendo relato — nunca campo.

    A carga não preenche por dedução: sem correspondência no cadastro, local e
    equipamento ficam vazios na demanda, e o remetente que não é fornecedor
    cadastrado não vira fornecedor nenhum.
    """

    def _importar_um(self, *, texto, remetente=REMETENTE, assunto=""):
        msg = mensagem(
            message_id="m1",
            thread_id="t1",
            remetente=remetente,
            assunto=assunto,
            texto=texto,
        )
        item = criar_item(
            self.condominio,
            message_id="m1",
            thread_id="t1",
            remetente=remetente,
            assunto=assunto,
        )
        with patch("apps.core.email_interpretacao.llm.pedir_json", return_value={}):
            resumo = demandas_historicas.importar(
                [self.entrada(msg, item=item)],
                condominio=self.condominio,
                usuario=self.gestao,
            )
        return resumo, msg, item

    def test_o_local_incerto_nao_e_inventado(self):
        """Local citado que não existe no cadastro não vira o local da demanda."""
        criar_local(self.condominio, nome="Garagem")

        resumo, _, _ = self._importar_um(texto="Vazamento de água no salão de festas.")

        self.assertEqual(len(resumo.criadas), 1)
        demanda = Demanda.objects.get()
        self.assertIsNone(demanda.local)

    def test_o_equipamento_incerto_nao_e_inventado(self):
        """Equipamento citado pelo tipo, sem cadastro que o confirme, fica vazio."""
        criar_equipamento(self.condominio, nome="Bomba de recalque")

        resumo, _, _ = self._importar_um(texto="O portão da entrada travou e não abre.")

        self.assertEqual(len(resumo.criadas), 1)
        demanda = Demanda.objects.get()
        self.assertIsNone(demanda.equipamento)

    def test_o_fornecedor_incerto_nao_e_associado(self):
        """Remetente fora do cadastro não cria fornecedor nem associação."""
        criar_fornecedor(
            self.condominio,
            nome_razao_social="Elevadores Silva Ltda.",
            email="contato@outro-fornecedor.test",
        )

        resumo, msg, item = self._importar_um(texto="Vazamento de água na garagem.")

        self.assertEqual(len(resumo.criadas), 1)
        demanda = Demanda.objects.get()

        # Nenhum fornecedor nasce da mensagem, e o cadastro fica como estava.
        self.assertEqual(Fornecedor.objects.count(), 1)
        self.assertFalse(Fornecedor.objects.filter(email=REMETENTE).exists())

        # O vínculo do item aponta para a demanda — e não para um fornecedor.
        vinculo = VinculoImportacaoHistorica.objects.get()
        self.assertEqual(vinculo.entidade_destino, EntidadeImportacaoHistorica.DEMANDA)
        self.assertEqual(vinculo.destino_id, demanda.pk)

        # No ponto real da associação — a reconciliação — não há fornecedor.
        leitura = interpretar(msg, condominio=self.condominio, pedir_json=lambda **_: {})
        cruzada = reconciliar(
            leitura, mensagem=msg, item=item, condominio=self.condominio
        )
        self.assertIsNone(cruzada.fato("fornecedor"))


# ---------------------------------------------------------------------------
# O service oficial e o ator da conclusão
# ---------------------------------------------------------------------------


class ServiceDaCriacaoTests(BaseCargaTests):
    def test_a_criacao_passa_pelo_service_do_dominio(self):
        """A demanda nasce pelo service oficial — não por atribuição direta."""
        msg = mensagem(message_id="m1", thread_id="t1")
        item = criar_item(self.condominio, message_id="m1", thread_id="t1")

        with patch(
            "apps.demandas.services.criar_demanda", wraps=criar_demanda
        ) as espiao, patch(
            "apps.core.email_interpretacao.llm.pedir_json", return_value={}
        ):
            demandas_historicas.importar(
                [self.entrada(msg, item=item)],
                condominio=self.condominio,
                usuario=self.gestao,
            )

        self.assertEqual(espiao.call_count, 1)
        self.assertEqual(Demanda.objects.count(), 1)

        chamada = espiao.call_args.kwargs
        self.assertEqual(chamada["condominio"], self.condominio)
        self.assertEqual(chamada["criado_por"], self.gestao)
        self.assertEqual(chamada["origem"], OrigemDemanda.EMAIL)
        self.assertEqual(chamada["titulo"], "Vazamento na garagem")


class AtorDaConclusaoTests(BaseCargaTests):
    def test_o_responsavel_e_o_ator_da_conclusao(self):
        """Quem responde pela carga é quem assina a conclusão no histórico."""
        responsavel = criar_pessoa("responde.pela.carga@exemplo.test", "Responsável")
        criar_vinculo(responsavel, self.condominio, papel=Papel.SINDICA)

        ocorrencia = mensagem(
            message_id="m1",
            thread_id="t1",
            assunto="Vazamento na garagem",
            texto="Vazamento de água na garagem.",
            momento=MOMENTO,
        )
        conclusao = mensagem(
            message_id="m2",
            thread_id="t1",
            assunto="Vazamento na garagem",
            texto="Serviço realizado e concluído, obrigado.",
            momento=MOMENTO + timedelta(days=1),
        )
        item1 = criar_item(self.condominio, message_id="m1", thread_id="t1")
        item2 = criar_item(self.condominio, message_id="m2", thread_id="t1")

        with patch("apps.core.email_interpretacao.llm.pedir_json", return_value={}):
            resumo = demandas_historicas.importar(
                [
                    EntradaDaCarga(mensagem=ocorrencia, item=item1),
                    EntradaDaCarga(mensagem=conclusao, item=item2),
                ],
                condominio=self.condominio,
                usuario=responsavel,
            )

        self.assertEqual(len(resumo.criadas), 1)
        demanda = Demanda.objects.get()
        self.assertEqual(demanda.status, StatusDemanda.CONCLUIDA)

        evento = demanda.historico.get(tipo_evento=TipoEventoDemanda.CONCLUIDA)
        self.assertEqual(evento.usuario, responsavel)


# ---------------------------------------------------------------------------
# O fluxo inteiro: o que entra e o que não vira demanda
# ---------------------------------------------------------------------------


class FluxoDaImportacaoTests(BaseCargaTests):
    """A rodada completa, com a leitura de verdade — sem dublê na semântica.

    O serviço de linguagem devolve um dicionário vazio: o determinístico já
    decidiu o texto, e nada é acrescentado a ele. O que se mede aqui é o que
    sobrou no banco depois da rodada.
    """

    def _importar(self, *mensagens):
        entradas = []
        for msg in mensagens:
            item = criar_item(
                self.condominio,
                message_id=msg.message_id,
                thread_id=msg.thread_id,
                remetente=msg.remetente,
                assunto=msg.assunto,
            )
            entradas.append(EntradaDaCarga(mensagem=msg, item=item))

        with patch("apps.core.email_interpretacao.llm.pedir_json", return_value={}):
            return demandas_historicas.importar(
                entradas, condominio=self.condominio, usuario=self.gestao
            )

    def test_o_email_informativo_nao_cria_demanda(self):
        """1. Recado sem ocorrência atravessa a rodada inteira e não gera nada."""
        resumo = self._importar(
            mensagem(
                message_id="m1",
                thread_id="t1",
                assunto="Calendário de reuniões",
                texto="Segue o calendário de reuniões do condomínio para o próximo semestre.",
            )
        )

        self.assertEqual(len(resumo.ignoradas), 1)
        self.assertEqual(resumo.grupos[0].decisao, DecisaoDaCarga.IGNORAR)
        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(VinculoImportacaoHistorica.objects.count(), 0)

    def test_a_autorizacao_de_portaria_nao_cria_demanda(self):
        """2. Autorização de entrada é recado da portaria — não é ocorrência."""
        resumo = self._importar(
            mensagem(
                message_id="m1",
                thread_id="t1",
                assunto="Autorização de entrada",
                texto="Autorizo a entrada do prestador amanhã para o apartamento 501.",
            )
        )

        self.assertEqual(len(resumo.ignoradas), 1)
        self.assertEqual(resumo.grupos[0].decisao, DecisaoDaCarga.IGNORAR)
        self.assertEqual(Demanda.objects.count(), 0)

    def test_os_emails_da_mesma_ocorrencia_apontam_para_a_mesma_demanda(self):
        """3. Um fio, uma ocorrência: uma demanda e dois vínculos para ela."""
        resumo = self._importar(
            mensagem(message_id="m1", thread_id="t1"),
            mensagem(
                message_id="m2",
                thread_id="t1",
                texto="O vazamento na garagem continua.",
                momento=MOMENTO + timedelta(hours=3),
            ),
        )

        self.assertEqual(len(resumo.criadas), 1)
        self.assertEqual(Demanda.objects.count(), 1)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 2)

        demanda = Demanda.objects.get()
        vinculos = VinculoImportacaoHistorica.objects.all()
        self.assertEqual(vinculos.count(), 2)
        self.assertEqual(
            {vinculo.destino_id for vinculo in vinculos}, {demanda.pk}
        )

    def test_a_semelhanca_textual_fraca_nao_funde_ocorrencias(self):
        """4. Parecido não é o mesmo: sem fio nem remetente, são duas demandas."""
        resumo = self._importar(
            mensagem(
                message_id="m1",
                thread_id="t1",
                remetente="morador.um@exemplo.test",
                assunto="Vazamento na garagem",
                texto="Vazamento de água na garagem.",
            ),
            mensagem(
                message_id="m2",
                thread_id="t2",
                remetente="morador.dois@exemplo.test",
                assunto="Vazamento na cobertura",
                texto="Vazamento de água na cobertura.",
                momento=MOMENTO + timedelta(hours=1),
            ),
        )

        self.assertEqual(len(resumo.criadas), 2)
        self.assertEqual(Demanda.objects.count(), 2)
