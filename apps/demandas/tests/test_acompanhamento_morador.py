"""Acompanhamento e avaliação do morador por link.

Quem pediu algo acompanha a própria demanda por um endereço com token, sem
entrar no sistema, e responde "Ficou resolvido?" quando ela é concluída. Estes
testes travam o que a página pública pode dizer — só o que diz respeito ao
morador —, a ida e volta da avaliação e a voz da gestão que fala com ele pelo
WhatsApp. Nada aqui toca dado real: nomes, títulos e números são de teste.
"""
import uuid

from urllib.parse import quote

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.comunidade.tests.apoio import criar_pessoa_da_comunidade
from apps.condominios.constants import Papel
from apps.demandas.constants import (
    AvaliacaoMorador,
    StatusDemanda,
    TipoEventoDemanda,
)
from apps.demandas.models import AcompanhamentoMorador, HistoricoDemanda
from apps.demandas.services import (
    alterar_status,
    criar_demanda,
    registrar_observacao,
)
from apps.demandas.tests.apoio import (
    criar_condominio,
    criar_local,
    criar_pessoa,
    criar_vinculo,
)

TITULO = "Bomba de recalque com vazamento"
OBSERVACAO_INTERNA = "Conferir o registro do banho antes de subir a bomba"
NOME_DO_RESPONSAVEL = "Fernanda Gestora"


class AcompanhamentoDoMoradorTests(TestCase):
    """A demanda comum, vista e respondida pelo morador que a pediu."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", NOME_DO_RESPONSAVEL)
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.local = criar_local(self.condominio, "Casa de máquinas")
        self.morador = criar_pessoa_da_comunidade(
            self.condominio, nome="Joana Moradora"
        )
        self.demanda = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo=TITULO,
            descricao="Vazamento visível junto da base da bomba.",
            local=self.local,
            solicitante=self.morador,
            responsavel=self.gestao,
        )
        self.acompanhamento = AcompanhamentoMorador.objects.create(
            demanda=self.demanda, pessoa=self.morador, criado_por=self.gestao
        )

    def _url_publica(self):
        return reverse(
            "demandas:acompanhar_publico", args=[self.acompanhamento.token]
        )

    def _concluir(self):
        alterar_status(
            demanda=self.demanda,
            usuario=self.gestao,
            novo_status=StatusDemanda.CONCLUIDA,
        )
        self.demanda.refresh_from_db()

    # -- A página pública -----------------------------------------------------

    def test_a_pagina_publica_mostra_so_o_que_diz_respeito_ao_morador(self):
        """Sem login, o morador vê o recorte — nunca a operação por dentro."""
        registrar_observacao(
            demanda=self.demanda, usuario=self.gestao, texto=OBSERVACAO_INTERNA
        )
        resposta = self.client.get(self._url_publica())
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, TITULO)
        self.assertContains(resposta, self.condominio.nome)
        self.assertContains(resposta, "Aberta")
        self.assertContains(resposta, '<meta name="robots" content="noindex">')
        # O que é da operação não atravessa a porta de fora.
        self.assertNotContains(resposta, OBSERVACAO_INTERNA)
        self.assertNotContains(resposta, NOME_DO_RESPONSAVEL)

    def test_token_desconhecido_resulta_em_404(self):
        resposta = self.client.get(
            reverse("demandas:acompanhar_publico", args=[uuid.uuid4()])
        )
        self.assertEqual(resposta.status_code, 404)

    # -- A avaliação ----------------------------------------------------------

    def test_so_ha_avaliacao_para_demanda_concluida(self):
        """Aberta, a página não pergunta nada; responder não registra nada."""
        resposta = self.client.get(self._url_publica())
        self.assertNotContains(resposta, "Ficou resolvido?")

        self.client.post(
            reverse("demandas:avaliar", args=[self.acompanhamento.token]),
            {"avaliacao": AvaliacaoMorador.RESOLVIDO},
        )
        self.acompanhamento.refresh_from_db()
        self.assertFalse(self.acompanhamento.respondido)
        self.assertFalse(
            HistoricoDemanda.objects.filter(
                demanda=self.demanda, tipo_evento=TipoEventoDemanda.AVALIACAO
            ).exists()
        )

    def test_a_avaliacao_grava_a_resposta_e_o_evento_uma_vez(self):
        self._concluir()
        resposta = self.client.get(self._url_publica())
        self.assertContains(resposta, "Ficou resolvido?")

        self.client.post(
            reverse("demandas:avaliar", args=[self.acompanhamento.token]),
            {"avaliacao": AvaliacaoMorador.RESOLVIDO, "comentario": "Ficou ótimo."},
        )
        self.acompanhamento.refresh_from_db()
        self.assertEqual(self.acompanhamento.avaliacao, AvaliacaoMorador.RESOLVIDO)
        self.assertEqual(self.acompanhamento.comentario, "Ficou ótimo.")
        self.assertIsNotNone(self.acompanhamento.avaliado_em)

        evento = HistoricoDemanda.objects.get(
            demanda=self.demanda, tipo_evento=TipoEventoDemanda.AVALIACAO
        )
        self.assertIn("Morador avaliou: Resolvido", evento.descricao)
        self.assertIn("Ficou ótimo.", evento.descricao)

        # A página confirma a resposta.
        confirmacao = self.client.get(self._url_publica())
        self.assertContains(confirmacao, "Obrigado! Sua resposta foi registrada.")

        # Só a primeira resposta vale: uma segunda tentativa não reescreve nada.
        self.client.post(
            reverse("demandas:avaliar", args=[self.acompanhamento.token]),
            {"avaliacao": AvaliacaoMorador.NAO_RESOLVIDO},
        )
        self.acompanhamento.refresh_from_db()
        self.assertEqual(self.acompanhamento.avaliacao, AvaliacaoMorador.RESOLVIDO)
        self.assertEqual(
            HistoricoDemanda.objects.filter(
                demanda=self.demanda, tipo_evento=TipoEventoDemanda.AVALIACAO
            ).count(),
            1,
        )

    def test_nao_resolvido_nao_reabre_a_demanda(self):
        self._concluir()
        self.client.post(
            reverse("demandas:avaliar", args=[self.acompanhamento.token]),
            {"avaliacao": AvaliacaoMorador.NAO_RESOLVIDO},
        )
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.status, StatusDemanda.CONCLUIDA)


class AvisoAoMoradorPeloWhatsAppTests(TestCase):
    """O ícone de WhatsApp que a gestão usa para falar com quem pediu."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", NOME_DO_RESPONSAVEL)
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)
        self.morador = criar_pessoa_da_comunidade(
            self.condominio, nome="Joana Moradora"
        )
        self.demanda = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo=TITULO,
            descricao="Vazamento visível junto da base da bomba.",
            solicitante=self.morador,
        )

    def _detalhe(self):
        resposta = self.client.get(
            reverse("demandas:detalhe", args=[self.demanda.pk])
        )
        self.assertEqual(resposta.status_code, 200)
        return resposta

    def _link_esperado(self):
        acompanhamento = AcompanhamentoMorador.objects.get(
            demanda=self.demanda, pessoa=self.morador
        )
        return reverse(
            "demandas:acompanhar_publico", args=[acompanhamento.token]
        )

    def test_o_icone_so_aparece_com_whatsapp_e_autorizacao(self):
        # Sem número, não há por onde falar.
        self.assertNotContains(self._detalhe(), "wa.me")

        # Com número, mas sem a autorização do morador, também não.
        self.morador.whatsapp = "(11) 99999-8888"
        self.morador.save()
        self.assertNotContains(self._detalhe(), "wa.me")

    def test_a_mensagem_da_demanda_aberta_convida_a_acompanhar(self):
        self.morador.whatsapp = "(11) 99999-8888"
        self.morador.aceita_avisos_whatsapp = True
        self.morador.save()

        resposta = self._detalhe()
        self.assertContains(resposta, "Enviar acompanhamento ao morador")

        link = f"http://testserver{self._link_esperado()}"
        texto = (
            f'Olá, Joana! Sua solicitação "{TITULO}" foi registrada no condomínio '
            f"Alpha. Acompanhe aqui: {link}"
        )
        esperado = f"https://wa.me/5511999998888?text={quote(texto, safe='')}"
        self.assertContains(resposta, esperado)

    def test_a_mensagem_da_demanda_concluida_pede_a_avaliacao(self):
        self.morador.whatsapp = "(11) 99999-8888"
        self.morador.aceita_avisos_whatsapp = True
        self.morador.save()
        alterar_status(
            demanda=self.demanda,
            usuario=self.gestao,
            novo_status=StatusDemanda.CONCLUIDA,
        )
        self.demanda.refresh_from_db()

        resposta = self._detalhe()
        self.assertContains(resposta, "Pedir avaliação ao morador")

        link = f"http://testserver{self._link_esperado()}"
        texto = (
            f'Olá, Joana! Sua solicitação "{TITULO}" foi concluída. '
            f"Ficou resolvido? Responda aqui: {link}"
        )
        esperado = f"https://wa.me/5511999998888?text={quote(texto, safe='')}"
        self.assertContains(resposta, esperado)


class RetornoDosMoradoresNoPainelTests(TestCase):
    """O bloco do painel que reúne o que os moradores responderam."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", NOME_DO_RESPONSAVEL)
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)
        self.morador = criar_pessoa_da_comunidade(
            self.condominio, nome="Joana Moradora"
        )
        self.demanda = criar_demanda(
            condominio=self.condominio,
            criado_por=self.gestao,
            titulo=TITULO,
            descricao="Vazamento visível junto da base da bomba.",
            solicitante=self.morador,
        )

    def test_o_painel_mostra_o_que_nao_foi_resolvido(self):
        AcompanhamentoMorador.objects.create(
            demanda=self.demanda,
            pessoa=self.morador,
            criado_por=self.gestao,
            avaliacao=AvaliacaoMorador.NAO_RESOLVIDO,
            comentario="O barulho continua.",
            avaliado_em=timezone.now(),
        )
        resposta = self.client.get(reverse("core:home"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Retorno dos moradores")
        self.assertContains(resposta, "Não resolvido")
        self.assertContains(resposta, TITULO)
        self.assertContains(resposta, "O barulho continua.")
        self.assertContains(resposta, "0% resolvido")
