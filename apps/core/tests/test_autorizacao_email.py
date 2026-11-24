"""Testes do e-mail que autoriza um acesso de portaria.

A pergunta desta fase é uma só:

    Um e-mail que autoriza uma entrada pode alimentar a Portaria sem criar
    uma Demanda?

O caminho obrigatório é o mesmo das demais leituras — e-mail → entrada bruta →
interpretação → sugestão → confirmação humana → autorização da portaria — e a
autorização só nasce do clique, por um service do domínio, com o que a mensagem
sustenta. Nunca de um atalho "e-mail → autorização definitiva": o que a mensagem
não disse não é inventado, e quem decide revisa antes.

O provedor do Gmail e o serviço de linguagem são sempre falsos: nenhum teste
toca a rede.
"""
from datetime import timedelta
from unittest.mock import patch

from apps.comunidade.tests import apoio as comunidade_apoio
from apps.core import email_acoes
from apps.core.constants import (
    EntidadeImportacaoHistorica,
    EstadoProcessamentoImportacao,
)
from apps.core.email_interpretacao import AcaoProposta
from apps.core.models import ItemImportacaoHistorica, VinculoImportacaoHistorica
from apps.core.tests.test_email_acoes import BaseConfirmacaoTests, patchar_leitura
from apps.core.tests.test_leitura_email import (
    MOMENTO,
    ProvedorFalso,
    criar_item,
    mensagem,
)
from apps.demandas.models import Demanda
from apps.portaria.constants import OrigemPortaria
from apps.portaria.models import AutorizacaoPortaria

# O que a mensagem diz — e o que a leitura entende dela. O nome do visitante
# chega pela leitura semântica (o serviço simulado o devolve); a unidade, pelo
# número citado no texto e pelo cadastro que o confirma.
AUTORIZACAO = "Autorizo a entrada do técnico no apto 302."
ASSUNTO_DA_AUTORIZACAO = "Autorização de entrada"
NOME_DO_VISITANTE = "João da Silva"

# Vocabulário de entrada sem quem entra nem para onde: continua mensagem comum,
# nunca autorização.
ENTRADA_SEM_DADO = "Houve entrada de água na garagem."

JANELA = timedelta(hours=12)

ACAO = AcaoProposta.CRIAR_AUTORIZACAO_PORTARIA
MENSAGEM_DE_FALTA = email_acoes.MENSAGEM_DE_FALTA_DE_INFORMACAO


class BaseAutorizacaoTests(BaseConfirmacaoTests):
    """A mensagem de autorização, lida e confirmada sem tocar a rede."""

    def provedor_da_autorizacao(self, texto=AUTORIZACAO):
        return ProvedorFalso(
            mensagem=mensagem(texto=texto, assunto=ASSUNTO_DA_AUTORIZACAO)
        )

    def unidade(self, identificador="302"):
        return comunidade_apoio.criar_unidade(self.condominio, identificador=identificador)

    def ler(self, item, provedor, *, nome=NOME_DO_VISITANTE):
        """Roda a leitura com o provedor falso — sem tocar o Gmail."""
        provedor.item_pk = item.pk
        with patchar_leitura(nome=nome):
            resultado = self.processar(provedor, item)
        item.refresh_from_db()
        return item, resultado

    def aguardando_autorizacao(self, *, nome=NOME_DO_VISITANTE, texto=AUTORIZACAO):
        item = criar_item(self.condominio, assunto=ASSUNTO_DA_AUTORIZACAO)
        return self.ler(item, self.provedor_da_autorizacao(texto), nome=nome)

    def confirmar_autorizacao(self, item, provedor, *, nome=NOME_DO_VISITANTE):
        with patchar_leitura(nome=nome):
            return email_acoes.confirmar_acao_email(
                item, ACAO, usuario=self.sindica, provedor=provedor
            )

    def reabrir_e_reler(self, item):
        """Reprocessa o item, como um retry faria, e o devolve lido de novo."""
        ItemImportacaoHistorica.objects.reprocessar_item(item=item)
        item.refresh_from_db()
        provedor = self.provedor_da_autorizacao()
        provedor.item_pk = item.pk
        with patchar_leitura(nome=NOME_DO_VISITANTE):
            self.processar(provedor, item)
        item.refresh_from_db()
        return item


# ---------------------------------------------------------------------------
# 1-3. A sugestão revisável — e nunca uma Demanda
# ---------------------------------------------------------------------------


class SugestaoDeAutorizacaoTests(BaseAutorizacaoTests):
    def test_email_de_autorizacao_vira_sugestao_revisavel(self):
        """1. A mensagem vira proposta de autorização, aguardando decisão."""
        self.unidade("302")

        _, resultado = self.aguardando_autorizacao()

        self.assertEqual(resultado.proposta.acao, ACAO)
        self.assertTrue(resultado.necessita_confirmacao)
        self.assertEqual(
            resultado.estado_final, EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO
        )
        self.assertEqual(AutorizacaoPortaria.objects.count(), 0)

    def test_email_de_autorizacao_nao_cria_demanda(self):
        """2. A autorização não é ocorrência: nenhuma demanda nasce da leitura."""
        self.unidade("302")

        self.aguardando_autorizacao()

        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(AutorizacaoPortaria.objects.count(), 0)

    def test_vocabulario_sem_dado_de_acesso_nao_propoe_autorizacao(self):
        """3. "Entrada de água" não é autorização: falta quem entra ou o destino."""
        _, resultado = self.aguardando_autorizacao(texto=ENTRADA_SEM_DADO, nome="")

        self.assertNotEqual(resultado.proposta.acao, ACAO)
        self.assertEqual(Demanda.objects.count(), 0)

    def test_resposta_sem_nome_continua_valida(self):
        """4. O contrato do serviço não mudou: ``resposta()`` sem nome segue lido."""
        self.unidade("302")
        item = criar_item(self.condominio, assunto=ASSUNTO_DA_AUTORIZACAO)
        provedor = self.provedor_da_autorizacao()
        provedor.item_pk = item.pk

        with patchar_leitura():  # sem "nome" no que o serviço devolve
            resultado = self.processar(provedor, item)

        # A unidade citada já é dado de acesso suficiente para propor.
        self.assertEqual(resultado.proposta.acao, ACAO)


# ---------------------------------------------------------------------------
# 5-9. A confirmação cria a autorização — pelo service, sem inventar nada
# ---------------------------------------------------------------------------


class ConfirmacaoDaAutorizacaoTests(BaseAutorizacaoTests):
    def test_confirmar_cria_autorizacao_com_origem_email(self):
        """5. Confirmar registra a autorização da portaria, com origem E-mail."""
        unidade = self.unidade("302")
        item, _ = self.aguardando_autorizacao()

        resultado = self.confirmar_autorizacao(item, self.provedor_da_autorizacao())

        self.assertTrue(resultado.executado)
        autorizacao = AutorizacaoPortaria.objects.get()
        self.assertEqual(autorizacao.origem, OrigemPortaria.EMAIL)
        self.assertEqual(autorizacao.nome, NOME_DO_VISITANTE)
        self.assertEqual(autorizacao.unidade_id, unidade.pk)
        self.assertEqual(autorizacao.condominio_id, self.condominio.pk)
        self.assertEqual(autorizacao.registrado_por, self.sindica)
        item.refresh_from_db()
        self.assertEqual(
            item.estado_processamento, EstadoProcessamentoImportacao.PROCESSADO
        )

    def test_confirmar_nao_cria_demanda(self):
        """6. A confirmação cria autorização e jamais uma demanda."""
        self.unidade("302")
        item, _ = self.aguardando_autorizacao()

        self.confirmar_autorizacao(item, self.provedor_da_autorizacao())

        self.assertEqual(AutorizacaoPortaria.objects.count(), 1)
        self.assertEqual(Demanda.objects.count(), 0)

    def test_confirmar_registra_vinculo_de_autorizacao(self):
        """7. Fica a proveniência: de qual mensagem veio esta autorização."""
        self.unidade("302")
        item, _ = self.aguardando_autorizacao()

        resultado = self.confirmar_autorizacao(item, self.provedor_da_autorizacao())

        vinculo = VinculoImportacaoHistorica.objects.get(item=item)
        self.assertEqual(
            vinculo.entidade_destino, EntidadeImportacaoHistorica.AUTORIZACAO_PORTARIA
        )
        self.assertEqual(vinculo.destino_id, resultado.destino.pk)

    def test_confirmar_usa_o_service_do_dominio(self):
        """8. Quem grava é ``apps.portaria.services.registrar_esperado``."""
        from apps.portaria import services as portaria_services

        self.unidade("302")
        item, _ = self.aguardando_autorizacao()

        with patch.object(
            portaria_services,
            "registrar_esperado",
            wraps=portaria_services.registrar_esperado,
        ) as registrar:
            self.confirmar_autorizacao(item, self.provedor_da_autorizacao())

        chamada = registrar.call_args
        self.assertIsNotNone(chamada)
        self.assertEqual(chamada.kwargs["origem"], OrigemPortaria.EMAIL)
        self.assertEqual(chamada.kwargs["condominio"], self.condominio)
        self.assertEqual(chamada.kwargs["usuario"], self.sindica)

    def test_janela_da_autorizacao_conta_do_momento_da_mensagem(self):
        """9. O prazo é o horizonte de um turno, contado do e-mail original."""
        self.unidade("302")
        item, _ = self.aguardando_autorizacao()
        ItemImportacaoHistorica.objects.filter(pk=item.pk).update(
            momento_da_mensagem=MOMENTO
        )
        item.refresh_from_db()

        self.confirmar_autorizacao(item, self.provedor_da_autorizacao())

        autorizacao = AutorizacaoPortaria.objects.get()
        self.assertEqual(autorizacao.inicio_previsto, MOMENTO)
        self.assertEqual(autorizacao.fim_previsto, MOMENTO + JANELA)


# ---------------------------------------------------------------------------
# 10-12. Segurança: nada é inventado e nada duplica
# ---------------------------------------------------------------------------


class SegurancaDaAutorizacaoTests(BaseAutorizacaoTests):
    def test_sem_nome_nada_e_inventado(self):
        """10. Faltando quem entra, a autorização não nasce pela metade."""
        self.unidade("302")
        item, _ = self.aguardando_autorizacao()

        resultado = self.confirmar_autorizacao(
            item, self.provedor_da_autorizacao(), nome=""
        )

        self.assertFalse(resultado.executado)
        self.assertEqual(resultado.mensagem, MENSAGEM_DE_FALTA)
        self.assertEqual(AutorizacaoPortaria.objects.count(), 0)
        item.refresh_from_db()
        self.assertEqual(
            item.estado_processamento, EstadoProcessamentoImportacao.AGUARDANDO_CONFIRMACAO
        )

    def test_destino_nao_confirmado_nada_e_inventado(self):
        """11. Unidade citada sem cadastro que a confirme não vira destino."""
        item, _ = self.aguardando_autorizacao()  # nenhuma unidade foi criada

        resultado = self.confirmar_autorizacao(item, self.provedor_da_autorizacao())

        self.assertFalse(resultado.executado)
        self.assertEqual(resultado.mensagem, MENSAGEM_DE_FALTA)
        self.assertEqual(AutorizacaoPortaria.objects.count(), 0)

    def test_dupla_confirmacao_nao_cria_segunda_autorizacao(self):
        """12. Confirmar duas vezes reaproveita a mesma autorização."""
        self.unidade("302")
        item, _ = self.aguardando_autorizacao()
        primeira = self.confirmar_autorizacao(item, self.provedor_da_autorizacao())
        self.assertEqual(AutorizacaoPortaria.objects.count(), 1)

        item = self.reabrir_e_reler(item)
        segunda = self.confirmar_autorizacao(item, self.provedor_da_autorizacao())

        self.assertTrue(segunda.executado)
        self.assertEqual(AutorizacaoPortaria.objects.count(), 1)
        self.assertEqual(segunda.destino.pk, primeira.destino.pk)
