"""O aviso de encomenda por WhatsApp: um toque, sem envio automático.

O Eithra não manda mensagem: o ícone da linha da entrega abre a conversa no
aparelho de quem toca, com o recado já escrito. O que se prende aqui é a regra
que protege o morador — só aparece quem tem número E autorização —, o formato do
link e o registro de que a portaria avisou, no histórico do acesso.
"""
import re
from urllib.parse import parse_qs, urlparse

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.comunidade.tests import apoio as comunidade_apoio
from apps.condominios.constants import Papel
from apps.demandas.tests.apoio import criar_condominio, criar_pessoa, criar_vinculo
from apps.portaria.constants import (
    EventoAcessoPortaria,
    InstrucaoEntrega,
    MeioAutorizacaoPortaria,
    StatusAutorizacaoPortaria,
    TipoAcessoPortaria,
)
from apps.portaria.models import HistoricoAcessoPortaria
from apps.portaria.tests import apoio


class BaseDoAviso(TestCase):
    """Uma entrega prevista para a unidade da Maria, que autoriza o aviso."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.porteiro = criar_pessoa("portaria@exemplo.test", "Porteiro")
        criar_vinculo(self.porteiro, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(self.porteiro)

        self.unidade = comunidade_apoio.criar_unidade(
            self.condominio, identificador="302"
        )
        self.moradora = comunidade_apoio.criar_pessoa_da_comunidade(
            self.condominio,
            nome="Maria Silva",
            whatsapp="11999998888",
            aceita_avisos_whatsapp=True,
        )
        comunidade_apoio.criar_vinculo_pessoa_unidade(
            self.condominio, self.moradora, self.unidade
        )

    def _entrega(self, **campos):
        base = {
            "tipo_acesso": TipoAcessoPortaria.DELIVERY,
            "unidade": self.unidade,
            "empresa": "Correios",
        }
        base.update(campos)
        return apoio.criar_acesso(self.condominio, self.porteiro, **base)

    def _painel(self):
        return self.client.get(
            reverse("portaria:painel"), {"condominio": str(self.condominio.pk)}
        )

    def _link_do_painel(self, resposta):
        """O wa.me que a linha da entrega oferece, como está no HTML."""
        achado = re.search(r'data-whatsapp="([^"]+)"', resposta.content.decode())
        self.assertIsNotNone(achado, "A linha não trouxe o link do aviso.")
        return achado.group(1)


class IconeDoAvisoTests(BaseDoAviso):
    """O ícone aparece só para quem pode receber — e diz quando ninguém pode."""

    def test_icone_aparece_com_whatsapp_e_autorizacao(self):
        self._entrega()

        resposta = self._painel()

        self.assertContains(resposta, "bxl-whatsapp")
        self.assertContains(resposta, "wa.me/55")
        self.assertNotContains(resposta, "Sem WhatsApp autorizado nesta unidade")

    def test_sem_autorizacao_nao_oferece_o_icone(self):
        self.moradora.aceita_avisos_whatsapp = False
        self.moradora.save(update_fields=["aceita_avisos_whatsapp"])
        self._entrega()

        resposta = self._painel()

        self.assertNotContains(resposta, "bxl-whatsapp")
        self.assertContains(resposta, "Sem WhatsApp autorizado nesta unidade")

    def test_sem_whatsapp_nao_oferece_o_icone(self):
        self.moradora.whatsapp = ""
        self.moradora.save(update_fields=["whatsapp"])
        self._entrega()

        resposta = self._painel()

        self.assertNotContains(resposta, "bxl-whatsapp")
        self.assertContains(resposta, "Sem WhatsApp autorizado nesta unidade")

    def test_link_wa_me_leva_a_mensagem_certa(self):
        acesso = self._entrega(instrucao_entrega=InstrucaoEntrega.MORADOR_DESCE)

        link = self._link_do_painel(self._painel())
        endereco = urlparse(link)
        texto = parse_qs(endereco.query)["text"][0]

        self.assertEqual(endereco.netloc, "wa.me")
        self.assertEqual(endereco.path, "/5511999998888")
        hora = timezone.localtime(acesso.entrada_em).strftime("%H:%M")
        self.assertEqual(
            texto,
            "📦 Olá, Maria! Chegou uma encomenda para o apto "
            f"{self.unidade.rotulo}, Correios às {hora}. "
            "Morador desce para receber. "
            f"Portaria {self.condominio.nome}.",
        )


class RegistroDoAvisoTests(BaseDoAviso):
    """O toque no ícone deixa marca: quem foi avisado e por quem."""

    def _registrar_aviso(self, acesso, pessoa=None):
        return self.client.post(
            reverse("portaria:registrar_aviso", args=[acesso.pk]),
            {
                "condominio": str(self.condominio.pk),
                "pessoa": str((pessoa or self.moradora).pk),
            },
        )

    def test_registrar_aviso_grava_o_historico_do_acesso(self):
        acesso = self._entrega()

        resposta = self._registrar_aviso(acesso)

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.json()["ok"])
        evento = HistoricoAcessoPortaria.objects.get(
            acesso=acesso, evento=EventoAcessoPortaria.AVISO_WHATSAPP
        )
        self.assertEqual(evento.usuario, self.porteiro)
        self.assertIn("Maria Silva", evento.descricao)
        self.assertIn(str(self.porteiro), evento.descricao)

    def test_linha_mostra_avisado_depois_do_registro(self):
        acesso = self._entrega()

        self._registrar_aviso(acesso)
        resposta = self._painel()

        item = next(
            linha
            for linha in resposta.context["aqui_agora"]
            if linha["pk"] == acesso.pk
        )
        self.assertNotEqual(item["avisado_em"], "")

    def test_registrar_aviso_exige_permissao(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)
        acesso = self._entrega()

        resposta = self._registrar_aviso(acesso)

        self.assertEqual(resposta.status_code, 404)

    def test_registrar_aviso_exige_post(self):
        acesso = self._entrega()
        resposta = self.client.get(
            reverse("portaria:registrar_aviso", args=[acesso.pk])
        )
        self.assertEqual(resposta.status_code, 405)


class RecadoNaMensagemTests(BaseDoAviso):
    """A mensagem de sucesso diz, por escrito, quem pode receber o aviso."""

    def _registrar_chegada(self):
        return self.client.post(
            reverse("portaria:criar_chegada_nao_prevista"),
            {
                "condominio": str(self.condominio.pk),
                "tipo_acesso": TipoAcessoPortaria.DELIVERY.value,
                "nome": "Encomenda Correios",
                "unidade": str(self.unidade.pk),
                "empresa": "Correios",
                "status": StatusAutorizacaoPortaria.AUTORIZADO.value,
                "meio_autorizacao": MeioAutorizacaoPortaria.INTERFONE.value,
            },
            follow=True,
        )

    def test_mensagem_diz_quem_pode_receber(self):
        resposta = self._registrar_chegada()
        self.assertContains(resposta, "Aviso por WhatsApp disponível para Maria.")

    def test_mensagem_avisa_quando_ninguem_autorizou(self):
        self.moradora.aceita_avisos_whatsapp = False
        self.moradora.save(update_fields=["aceita_avisos_whatsapp"])

        resposta = self._registrar_chegada()

        self.assertContains(resposta, "Sem WhatsApp autorizado nesta unidade.")
