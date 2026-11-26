"""Testes da ingestão: do Gmail para o item da carga histórica.

O provedor é sempre falso — nenhum teste toca a rede. O que se verifica é o
contrato da ingestão: o que ela registra, o que ela nunca grava, o que ela
preserva ao rodar duas vezes, e o que ela deixa de fazer (não interpreta, não
cria vínculo, não cria demanda, não cria manutenção, não cria documento, não
baixa anexo).

A pergunta que a ingestão responde é só uma — "esta mensagem já está
registrada?" — e é isso que estes testes cobram.
"""
import hashlib
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from apps.condominios.models import Condominio
from apps.core import gmail
from apps.core.constants import (
    EstadoProcessamentoImportacao,
    OrigemImportacaoHistorica,
)
from apps.core.models import (
    ItemImportacaoHistorica,
    LoteImportacaoHistorica,
    VinculoImportacaoHistorica,
)
from apps.demandas.models import Demanda
from apps.governanca.models import DocumentoOperacional
from apps.manutencoes.models import Manutencao

CLIENTE = "cliente-de-teste.apps.googleusercontent.com"
SEGREDO = "segredo-de-teste-nao-vaza"  # noqa: S105 — é de teste, não é real
REFRESH = "refresh-de-teste-nao-vaza"
CONTA = "portaria@exemplo.test"

CONFIGURADO = {
    "EITHRA_GMAIL_ATIVO": True,
    "EITHRA_GMAIL_CLIENT_ID": CLIENTE,
    "EITHRA_GMAIL_CLIENT_SECRET": SEGREDO,
    "EITHRA_GMAIL_REFRESH_TOKEN": REFRESH,
    "EITHRA_GMAIL_CONTA": CONTA,
}

SEM_CONFIGURACAO = {
    "EITHRA_GMAIL_CLIENT_ID": "",
    "EITHRA_GMAIL_CLIENT_SECRET": "",
    "EITHRA_GMAIL_REFRESH_TOKEN": "",
    "EITHRA_GMAIL_CONTA": "",
}

CORPO_SIGILOSO = "O elevador parou no oitavo andar; não repita isto em lugar nenhum."

MOMENTO = datetime(2026, 10, 27, 10, 30, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Dublês
# ---------------------------------------------------------------------------


class ProvedorFalso:
    """Um provedor de mentira: entrega o que o teste mandar, sem rede.

    Registra o que lhe foi pedido para que os testes possam conferir consulta,
    paginação e limite — e recusa baixar anexo, porque nesta fase a ingestão não
    deve baixar nenhum.
    """

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
            {
                "consulta": consulta,
                "quantidade": quantidade,
                "pagina_token": pagina_token,
            }
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


def mensagem(message_id="msg-1", **campos):
    """Uma MensagemGmail normalizada, como o provider devolveria."""
    base = {
        "message_id": message_id,
        "thread_id": "thread-1",
        "remetente": "financeiro@atlas.com.br",
        "destinatarios": ("sindica@exemplo.test",),
        "assunto": "Orçamento do elevador social",
        "momento": MOMENTO,
        "texto": CORPO_SIGILOSO,
        "anexos": (gmail.AnexoGmail("att-1", "orcamento.pdf", "application/pdf", 2048),),
    }
    base.update(campos)
    return gmail.MensagemGmail(**base)


def referencia(message_id, thread_id="thread-1"):
    return gmail.ReferenciaMensagem(message_id=message_id, thread_id=thread_id)


def pagina(*identificadores, proxima=""):
    return gmail.PaginaDeMensagens(
        itens=tuple(referencia(i) for i in identificadores),
        proxima_pagina=proxima,
    )


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


@override_settings(**CONFIGURADO)
class BaseIngestaoTests(TestCase):
    """Cada teste roda o comando com o provider falso e lê a saída."""

    def setUp(self):
        self.condominio = Condominio.objects.create(nome="Condomínio de Teste")
        self.outro_condominio = Condominio.objects.create(nome="Outro Condomínio")
        self.saida = StringIO()

    def executar(self, provedor, *argumentos, **opcoes):
        """Roda o comando com o provider dado, sem tocar o Gmail."""
        opcoes.setdefault("stdout", self.saida)
        with patch(
            "apps.core.management.commands.sincronizar_gmail.obter_provedor",
            return_value=provedor,
        ):
            return call_command("sincronizar_gmail", *argumentos, **opcoes)

    def executar_com(self, provedor, *, condominio=None, **opcoes):
        """Roda o comando já com o condomínio resolvido."""
        alvo = condominio or self.condominio
        return self.executar(provedor, condominio=str(alvo.pk), **opcoes)

    @property
    def texto_da_saida(self):
        return self.saida.getvalue()


# ---------------------------------------------------------------------------
# Configuração e condomínio
# ---------------------------------------------------------------------------


class ConfiguracaoECondominioTests(BaseIngestaoTests):
    @override_settings(**SEM_CONFIGURACAO)
    def test_gmail_nao_configurado_recusa(self):
        with self.assertRaises(CommandError):
            self.executar_com(ProvedorFalso(), limite=10)

    def test_condominio_e_obrigatorio(self):
        with self.assertRaises(CommandError):
            self.executar(ProvedorFalso(), limite=10)

    def test_condominio_vazio_recusa(self):
        with self.assertRaises(CommandError):
            self.executar(ProvedorFalso(), condominio="", limite=10)

    def test_condominio_inexistente_recusa(self):
        from uuid import uuid4

        with self.assertRaises(CommandError):
            self.executar(
                ProvedorFalso(), condominio=str(uuid4()), limite=10
            )

    def test_condominio_mal_formado_recusa(self):
        with self.assertRaises(CommandError):
            self.executar(ProvedorFalso(), condominio="não-é-uuid", limite=10)

    def test_sem_filtro_e_sem_limite_recusa(self):
        with self.assertRaises(CommandError):
            self.executar_com(ProvedorFalso())


# ---------------------------------------------------------------------------
# Registro da mensagem
# ---------------------------------------------------------------------------


class RegistroDaMensagemTests(BaseIngestaoTests):
    def test_mensagem_nova_vira_item(self):
        provedor = ProvedorFalso(
            paginas=[pagina("msg-1")], mensagens={"msg-1": mensagem("msg-1")}
        )

        self.executar_com(provedor, limite=10)

        self.assertEqual(ItemImportacaoHistorica.objects.count(), 1)

    def test_item_novo_comeca_pendente(self):
        provedor = ProvedorFalso(
            paginas=[pagina("msg-1")], mensagens={"msg-1": mensagem("msg-1")}
        )

        self.executar_com(provedor, limite=10)

        item = ItemImportacaoHistorica.objects.get()
        self.assertEqual(
            item.estado_processamento, EstadoProcessamentoImportacao.PENDENTE
        )
        self.assertIsNone(item.processado_em)

    def test_guarda_os_metadados_da_mensagem(self):
        provedor = ProvedorFalso(
            paginas=[pagina("msg-1")],
            mensagens={
                "msg-1": mensagem(
                    "msg-1",
                    thread_id="thread-9",
                    remetente="Atlas Elevadores <Financeiro@Atlas.com.br>",
                    assunto="Orçamento do elevador social",
                )
            },
        )

        self.executar_com(provedor, limite=10)

        item = ItemImportacaoHistorica.objects.get()
        self.assertEqual(item.identificador_externo, "msg-1")
        self.assertEqual(item.message_id, "msg-1")
        self.assertEqual(item.thread_id, "thread-9")
        self.assertEqual(item.remetente, "financeiro@atlas.com.br")
        self.assertEqual(item.assunto, "Orçamento do elevador social")
        self.assertEqual(item.momento_da_mensagem, MOMENTO)
        self.assertEqual(item.origem, OrigemImportacaoHistorica.GMAIL)
        self.assertEqual(item.conta_origem, CONTA)

    def test_guarda_o_condominio_informado_e_nao_a_conta(self):
        provedor = ProvedorFalso(
            paginas=[pagina("msg-1")], mensagens={"msg-1": mensagem("msg-1")}
        )

        self.executar_com(provedor, condominio=self.outro_condominio, limite=10)

        item = ItemImportacaoHistorica.objects.get()
        self.assertEqual(item.condominio, self.outro_condominio)
        self.assertEqual(item.conta_origem, CONTA)

    def test_guarda_um_fingerprint_estavel(self):
        provedor = ProvedorFalso(
            paginas=[pagina("msg-1")], mensagens={"msg-1": mensagem("msg-1")}
        )

        self.executar_com(provedor, limite=10)

        esperado = hashlib.sha256(
            "\n".join(
                ("msg-1", "thread-1", "financeiro@atlas.com.br", "Orçamento do elevador social", MOMENTO.isoformat())
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(ItemImportacaoHistorica.objects.get().hash_fonte, esperado)

    def test_o_corpo_nao_e_persistido_nem_mostrado(self):
        provedor = ProvedorFalso(
            paginas=[pagina("msg-1")], mensagens={"msg-1": mensagem("msg-1")}
        )

        self.executar_com(provedor, limite=10)

        item = ItemImportacaoHistorica.objects.get()
        campos = " ".join(str(valor) for valor in item.__dict__.values())
        self.assertNotIn(CORPO_SIGILOSO, campos)
        self.assertNotIn(CORPO_SIGILOSO, self.texto_da_saida)

    def test_nenhum_anexo_e_baixado(self):
        provedor = ProvedorFalso(
            paginas=[pagina("msg-1")], mensagens={"msg-1": mensagem("msg-1")}
        )

        self.executar_com(provedor, limite=10)

        self.assertEqual(provedor.anexos_baixados, [])

    def test_um_lote_por_execucao(self):
        provedor = ProvedorFalso(
            paginas=[pagina("msg-1", "msg-2")],
            mensagens={"msg-1": mensagem("msg-1"), "msg-2": mensagem("msg-2")},
        )

        self.executar_com(provedor, limite=10)

        self.assertEqual(LoteImportacaoHistorica.objects.count(), 1)
        lote = LoteImportacaoHistorica.objects.get()
        self.assertEqual(lote.origem, OrigemImportacaoHistorica.GMAIL)
        self.assertEqual(lote.conta_origem, CONTA)
        self.assertEqual(lote.condominio, self.condominio)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 2)
        self.assertEqual(
            set(ItemImportacaoHistorica.objects.values_list("lote_id", flat=True)),
            {lote.pk},
        )


# ---------------------------------------------------------------------------
# Idempotência e reingestão
# ---------------------------------------------------------------------------


class ReingestaoTests(BaseIngestaoTests):
    def _provedor(self):
        return ProvedorFalso(
            paginas=[pagina("msg-1")], mensagens={"msg-1": mensagem("msg-1")}
        )

    def test_rodar_duas_vezes_nao_duplica_o_item(self):
        self.executar_com(self._provedor(), limite=10)
        self.executar_com(self._provedor(), limite=10)

        self.assertEqual(ItemImportacaoHistorica.objects.count(), 1)
        self.assertEqual(ItemImportacaoHistorica.objects.get().message_id, "msg-1")

    def test_rodar_duas_vezes_cria_um_lote_por_execucao(self):
        self.executar_com(self._provedor(), limite=10)
        self.executar_com(self._provedor(), limite=10)

        self.assertEqual(LoteImportacaoHistorica.objects.count(), 2)

    def test_reingestao_atualiza_metadados_legitimos(self):
        self.executar_com(self._provedor(), limite=10)

        atualizado = ProvedorFalso(
            paginas=[pagina("msg-1")],
            mensagens={"msg-1": mensagem("msg-1", assunto="Orçamento revisado")},
        )
        self.executar_com(atualizado, limite=10)

        self.assertEqual(ItemImportacaoHistorica.objects.count(), 1)
        self.assertEqual(
            ItemImportacaoHistorica.objects.get().assunto, "Orçamento revisado"
        )

    def _preserva_estado(self, estado, transicoes=()):
        self.executar_com(self._provedor(), limite=10)
        item = ItemImportacaoHistorica.objects.get()
        for passo in transicoes:
            item = ItemImportacaoHistorica.objects.alterar_estado_processamento(
                item=item, novo_estado=passo
            )
        item = ItemImportacaoHistorica.objects.alterar_estado_processamento(
            item=item, novo_estado=estado
        )

        self.executar_com(self._provedor(), limite=10)

        self.assertEqual(ItemImportacaoHistorica.objects.count(), 1)
        self.assertEqual(
            ItemImportacaoHistorica.objects.get().estado_processamento, estado
        )

    def test_reingestao_preserva_processado(self):
        self._preserva_estado(
            EstadoProcessamentoImportacao.PROCESSADO,
            transicoes=(EstadoProcessamentoImportacao.PROCESSANDO,),
        )

    def test_reingestao_preserva_aguardando_confirmacao(self):
        self._preserva_estado(
            EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO,
            transicoes=(EstadoProcessamentoImportacao.PROCESSANDO,),
        )

    def test_reingestao_preserva_processando(self):
        self._preserva_estado(EstadoProcessamentoImportacao.PROCESSANDO)

    def test_reingestao_preserva_erro(self):
        self._preserva_estado(
            EstadoProcessamentoImportacao.ERRO,
            transicoes=(EstadoProcessamentoImportacao.PROCESSANDO,),
        )

    def test_reingestao_nao_cria_vinculo_demanda_manutencao_nem_documento(self):
        self.executar_com(self._provedor(), limite=10)
        self.executar_com(self._provedor(), limite=10)

        self.assertEqual(VinculoImportacaoHistorica.objects.count(), 0)
        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(Manutencao.objects.count(), 0)
        self.assertEqual(DocumentoOperacional.objects.count(), 0)

    def test_a_ingestao_nao_atribui_autoria_ao_item(self):
        """A execução é do sistema, não de uma pessoa: o item nasce sem autor."""
        self.executar_com(self._provedor(), limite=10)

        self.assertIsNone(ItemImportacaoHistorica.objects.get().criado_por)

    def test_reingestao_nao_apaga_a_autoria_existente_do_item(self):
        from apps.cadastros.tests.apoio import criar_pessoa

        self.executar_com(self._provedor(), limite=10)
        gestao = criar_pessoa("gestao.carga@exemplo.test", "Gestão da carga")
        ItemImportacaoHistorica.objects.filter().update(criado_por=gestao)

        self.executar_com(self._provedor(), limite=10)

        self.assertEqual(ItemImportacaoHistorica.objects.get().criado_por, gestao)


# ---------------------------------------------------------------------------
# Listagem, paginação e limite
# ---------------------------------------------------------------------------


class ListagemTests(BaseIngestaoTests):
    def test_paginacao_percorre_todas_as_paginas(self):
        provedor = ProvedorFalso(
            paginas=[
                pagina("msg-1", proxima="pg-2"),
                pagina("msg-2"),
            ],
            mensagens={"msg-1": mensagem("msg-1"), "msg-2": mensagem("msg-2")},
        )

        self.executar_com(provedor, limite=10)

        self.assertEqual(ItemImportacaoHistorica.objects.count(), 2)
        self.assertEqual(provedor.chamadas_de_listagem[1]["pagina_token"], "pg-2")

    def test_o_limite_e_respeitado(self):
        provedor = ProvedorFalso(
            paginas=[pagina("msg-1", "msg-2", "msg-3", proxima="pg-2")],
            mensagens={
                "msg-1": mensagem("msg-1"),
                "msg-2": mensagem("msg-2"),
                "msg-3": mensagem("msg-3"),
            },
        )

        self.executar_com(provedor, limite=2)

        self.assertEqual(ItemImportacaoHistorica.objects.count(), 2)
        self.assertEqual(provedor.mensagens_pedidas, ["msg-1", "msg-2"])

    def test_avisa_quando_ficou_mensagem_de_fora(self):
        provedor = ProvedorFalso(
            paginas=[pagina("msg-1", "msg-2", proxima="pg-2")],
            mensagens={"msg-1": mensagem("msg-1"), "msg-2": mensagem("msg-2")},
        )

        self.executar_com(provedor, limite=2)

        self.assertIn("Havia mais mensagens", self.texto_da_saida)

    def test_a_consulta_e_encaminhada_ao_provider(self):
        provedor = ProvedorFalso(paginas=[pagina()])

        self.executar_com(provedor, consulta="in:inbox newer_than:7d", limite=5)

        self.assertEqual(
            provedor.chamadas_de_listagem[0]["consulta"], "in:inbox newer_than:7d"
        )

    def test_desde_vira_filtro_de_data(self):
        provedor = ProvedorFalso(paginas=[pagina()])

        self.executar_com(provedor, desde="2026-10-01", limite=5)

        self.assertEqual(provedor.chamadas_de_listagem[0]["consulta"], "after:2026/10/01")

    def test_desde_mal_formado_recusa(self):
        with self.assertRaises(CommandError):
            self.executar_com(ProvedorFalso(paginas=[pagina()]), desde="01/10/2026")

    def test_consulta_e_desde_se_somam(self):
        provedor = ProvedorFalso(paginas=[pagina()])

        self.executar_com(provedor, consulta="in:inbox", desde="2026-10-01", limite=5)

        self.assertEqual(
            provedor.chamadas_de_listagem[0]["consulta"], "in:inbox after:2026/10/01"
        )

    def test_limite_invalido_recusa(self):
        with self.assertRaises(CommandError):
            self.executar_com(ProvedorFalso(paginas=[pagina()]), limite=0)


# ---------------------------------------------------------------------------
# Falhas
# ---------------------------------------------------------------------------


class FalhasTests(BaseIngestaoTests):
    def test_mensagem_inexistente_e_reportada_sem_travar_o_resto(self):
        provedor = ProvedorFalso(
            paginas=[pagina("msg-1", "msg-2")],
            mensagens={
                "msg-1": mensagem("msg-1"),
                "msg-2": gmail.GmailMensagemInexistente(),
            },
        )

        self.executar_com(provedor, limite=10)

        self.assertEqual(ItemImportacaoHistorica.objects.count(), 1)
        self.assertIn("Falhas: 1", self.texto_da_saida)
        self.assertIn("msg-2", self.texto_da_saida)

    def test_mensagem_ilegivel_e_reportada_e_nao_vira_item(self):
        provedor = ProvedorFalso(
            paginas=[pagina("msg-1")],
            mensagens={"msg-1": gmail.GmailRespostaInvalida()},
        )

        self.executar_com(provedor, limite=10)

        self.assertEqual(ItemImportacaoHistorica.objects.count(), 0)
        self.assertIn("Falhas: 1", self.texto_da_saida)

    def test_falha_de_listagem_aborta_sem_deixar_lote(self):
        provedor = ProvedorFalso(erro_ao_listar=gmail.GmailIndisponivel())

        with self.assertRaises(CommandError):
            self.executar_com(provedor, limite=10)

        self.assertEqual(LoteImportacaoHistorica.objects.count(), 0)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 0)

    def test_credencial_recusada_aborta_controladamente(self):
        provedor = ProvedorFalso(erro_ao_listar=gmail.GmailCredencialInvalida())

        with self.assertRaises(CommandError):
            self.executar_com(provedor, limite=10)

        self.assertEqual(ItemImportacaoHistorica.objects.count(), 0)

    def test_indisponibilidade_no_meio_aborta_controladamente(self):
        provedor = ProvedorFalso(
            paginas=[pagina("msg-1", "msg-2")],
            mensagens={
                "msg-1": mensagem("msg-1"),
                "msg-2": gmail.GmailIndisponivel(),
            },
        )

        with self.assertRaises(CommandError):
            self.executar_com(provedor, limite=10)

        # O que já tinha sido registrado continua registrado; nada duplicou.
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 1)
        self.assertEqual(ItemImportacaoHistorica.objects.get().message_id, "msg-1")


# ---------------------------------------------------------------------------
# Resumo e simulação
# ---------------------------------------------------------------------------


class ResumoTests(BaseIngestaoTests):
    def test_o_resumo_mostra_o_que_e_operacional(self):
        provedor = ProvedorFalso(
            paginas=[pagina("msg-1", "msg-2")],
            mensagens={"msg-1": mensagem("msg-1"), "msg-2": mensagem("msg-2")},
        )

        self.executar_com(provedor, limite=10)

        saida = self.texto_da_saida
        self.assertIn("Condomínio de Teste", saida)
        self.assertIn(CONTA, saida)
        self.assertIn("Mensagens encontradas: 2", saida)
        self.assertIn("Novas: 2", saida)
        self.assertIn("Total processado: 2", saida)

    def test_o_resumo_nao_vaza_segredo_nem_corpo(self):
        provedor = ProvedorFalso(
            paginas=[pagina("msg-1")], mensagens={"msg-1": mensagem("msg-1")}
        )

        self.executar_com(provedor, limite=10)

        saida = self.texto_da_saida
        self.assertNotIn(SEGREDO, saida)
        self.assertNotIn(REFRESH, saida)
        self.assertNotIn(CORPO_SIGILOSO, saida)


class SimulacaoTests(BaseIngestaoTests):
    def test_dry_run_nao_grava_nada(self):
        provedor = ProvedorFalso(
            paginas=[pagina("msg-1", "msg-2")],
            mensagens={"msg-1": mensagem("msg-1"), "msg-2": mensagem("msg-2")},
        )

        self.executar_com(provedor, limite=10, dry_run=True)

        self.assertEqual(LoteImportacaoHistorica.objects.count(), 0)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 0)
        self.assertEqual(provedor.mensagens_pedidas, ["msg-1", "msg-2"])
        self.assertIn("simulação", self.texto_da_saida)

    def test_dry_run_conta_o_que_seria_registrado(self):
        provedor = ProvedorFalso(
            paginas=[pagina("msg-1", "msg-2")],
            mensagens={"msg-1": mensagem("msg-1"), "msg-2": mensagem("msg-2")},
        )

        self.executar_com(provedor, limite=10, dry_run=True)

        self.assertIn("Mensagens encontradas: 2", self.texto_da_saida)
