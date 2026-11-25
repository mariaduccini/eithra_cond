"""O atalho "Chegou encomenda": uma etapa só, do toque ao aviso.

Quem entrega não é visita, e o registro inteiro cabe em uma decisão: a unidade
que recebe. A etiqueta sugere o destino e a transportadora, mas quem decide é a
portaria — e só a unidade é obrigatória.

A encomenda não inventa um segundo domínio: ela nasce pelo mesmo
``registrar_chegada_com_resultado`` da chegada não prevista, como Correios ou
Delivery conforme a transportadora. O que estes testes prendem é isso — o tipo
certo, o nome gerado, a foto opcional que fica guardada, o aviso oferecido
apenas a quem pode receber e a recusa firme quando falta a unidade.
"""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.comunidade.tests import apoio as comunidade_apoio
from apps.condominios.constants import Papel
from apps.core.tests.apoio import StorageDeMentira, png
from apps.demandas.tests.apoio import criar_condominio, criar_pessoa, criar_vinculo
from apps.portaria.constants import (
    InstrucaoEntrega,
    StatusAutorizacaoPortaria,
    TipoAcessoPortaria,
)
from apps.portaria.models import AcessoPortaria, AutorizacaoPortaria


class BaseDaEncomenda(TestCase):
    """Uma unidade com uma moradora que autoriza o aviso por WhatsApp."""

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

    def _registrar(self, follow=False, **campos):
        dados = {
            "condominio": str(self.condominio.pk),
            "unidade": str(self.unidade.pk),
        }
        dados.update({chave: valor for chave, valor in campos.items() if valor is not None})
        return self.client.post(reverse("portaria:chegou_encomenda"), dados, follow=follow)

    def _ultimo_acesso(self):
        return AcessoPortaria.objects.order_by("-entrada_em").first()


class TipoDaEncomendaTests(BaseDaEncomenda):
    """Só a unidade basta: o tipo nasce da transportadora escolhida."""

    def test_correios_vira_acesso_correios(self):
        self._registrar(transportadora="Correios")

        acesso = self._ultimo_acesso()
        self.assertEqual(acesso.tipo_acesso, TipoAcessoPortaria.CORREIOS)
        autorizacao = AutorizacaoPortaria.objects.get(pk=acesso.autorizacao_id)
        self.assertEqual(autorizacao.tipo_acesso, TipoAcessoPortaria.CORREIOS)

    def test_outra_transportadora_vira_delivery(self):
        self._registrar(transportadora="Mercado Livre")

        self.assertEqual(self._ultimo_acesso().tipo_acesso, TipoAcessoPortaria.DELIVERY)

    def test_sem_transportadora_vira_delivery(self):
        self._registrar()

        self.assertEqual(self._ultimo_acesso().tipo_acesso, TipoAcessoPortaria.DELIVERY)

    def test_outra_vira_delivery(self):
        self._registrar(transportadora="Outra")

        self.assertEqual(self._ultimo_acesso().tipo_acesso, TipoAcessoPortaria.DELIVERY)

    def test_encomenda_nasce_autorizada(self):
        self._registrar(transportadora="Amazon")

        acesso = self._ultimo_acesso()
        self.assertIsNotNone(acesso)
        autorizacao = AutorizacaoPortaria.objects.get(pk=acesso.autorizacao_id)
        self.assertEqual(autorizacao.status, StatusAutorizacaoPortaria.AUTORIZADO)

    def test_instrucao_padrao_e_ficar_na_portaria(self):
        self._registrar(transportadora="Shopee")

        self.assertEqual(
            self._ultimo_acesso().instrucao_entrega,
            InstrucaoEntrega.DEIXAR_NA_PORTARIA,
        )

    def test_instrucao_escolhida_entra_no_registro(self):
        self._registrar(
            transportadora="Shopee",
            instrucao_entrega=InstrucaoEntrega.MORADOR_DESCE.value,
        )

        self.assertEqual(
            self._ultimo_acesso().instrucao_entrega,
            InstrucaoEntrega.MORADOR_DESCE,
        )


class NomeDaEncomendaTests(BaseDaEncomenda):
    """O nome diz, de relance, qual transportadora trouxe a encomenda."""

    def test_nome_traz_a_transportadora(self):
        self._registrar(transportadora="Mercado Livre")

        self.assertEqual(self._ultimo_acesso().nome, "Entrega Mercado Livre")

    def test_correios_no_nome(self):
        self._registrar(transportadora="Correios")

        self.assertEqual(self._ultimo_acesso().nome, "Entrega Correios")

    def test_outra_nao_vira_nome_de_empresa(self):
        self._registrar(transportadora="Outra")

        acesso = self._ultimo_acesso()
        self.assertEqual(acesso.nome, "Encomenda")
        self.assertEqual(acesso.empresa, "")

    def test_transportadora_fica_como_empresa(self):
        self._registrar(transportadora="Amazon")

        self.assertEqual(self._ultimo_acesso().empresa, "Amazon")


class FotoDaEncomendaTests(BaseDaEncomenda):
    """A foto é opcional e, quando vem, fica presa ao acesso."""

    def setUp(self):
        super().setUp()
        self.provider = StorageDeMentira()
        patcher = patch(
            "apps.portaria.fotos.obter_provider", return_value=self.provider
        )
        self.patch_do_storage = patcher.start()
        self.addCleanup(patcher.stop)

    def test_foto_enviada_fica_guardada(self):
        self._registrar(transportadora="Mercado Livre", foto=png())

        acesso = self._ultimo_acesso()
        self.assertNotEqual(acesso.foto_storage_key, "")
        self.assertIn(acesso.foto_storage_key, self.provider.objetos)

    def test_sem_foto_o_registro_continua(self):
        self._registrar(transportadora="Mercado Livre")

        self.assertEqual(self._ultimo_acesso().foto_storage_key, "")


class AvisoDaEncomendaTests(BaseDaEncomenda):
    """O aviso é oferecido a quem tem número E autorização — e só a eles."""

    def test_com_autorizacao_redireciona_para_o_aviso(self):
        resposta = self._registrar(transportadora="Mercado Livre")

        acesso = self._ultimo_acesso()
        self.assertIn(f"avisar={acesso.pk}", resposta.url)
        self.assertIn(f"pessoa={self.moradora.pk}", resposta.url)

    def test_sem_autorizacao_nao_oferece_o_aviso(self):
        self.moradora.aceita_avisos_whatsapp = False
        self.moradora.save(update_fields=["aceita_avisos_whatsapp"])

        resposta = self._registrar(transportadora="Mercado Livre", follow=True)

        self.assertContains(
            resposta, "Encomenda registrada. Sem WhatsApp autorizado nesta unidade."
        )

    def test_sem_numero_nao_oferece_o_aviso(self):
        self.moradora.whatsapp = ""
        self.moradora.save(update_fields=["whatsapp"])

        resposta = self._registrar(transportadora="Mercado Livre", follow=True)

        self.assertContains(
            resposta, "Encomenda registrada. Sem WhatsApp autorizado nesta unidade."
        )

    def test_mais_de_um_morador_lista_os_nomes(self):
        outra = comunidade_apoio.criar_pessoa_da_comunidade(
            self.condominio,
            nome="João Souza",
            whatsapp="11988887777",
            aceita_avisos_whatsapp=True,
        )
        comunidade_apoio.criar_vinculo_pessoa_unidade(
            self.condominio, outra, self.unidade
        )

        resposta = self._registrar(transportadora="Mercado Livre", follow=True)

        self.assertRegex(
            resposta.content.decode(),
            r"Avisar por WhatsApp: (Maria, João|João, Maria)\.",
        )

    def test_um_morador_avisa_pelo_nome(self):
        resposta = self._registrar(transportadora="Mercado Livre", follow=True)

        self.assertContains(
            resposta, "Encomenda registrada. Avisando Maria pelo WhatsApp."
        )


class SemUnidadeTests(BaseDaEncomenda):
    """Sem a unidade não há encomenda: o modal reabre com o motivo."""

    def test_sem_unidade_vira_erro_no_modal(self):
        resposta = self.client.post(
            reverse("portaria:chegou_encomenda"),
            {"condominio": str(self.condominio.pk), "transportadora": "Correios"},
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertContains(
            resposta, "Escolha a unidade que recebe a encomenda.", status_code=400
        )
        self.assertContains(resposta, "chegou-encomenda", status_code=400)
        self.assertFalse(AcessoPortaria.objects.exists())

    def test_unidade_de_outro_condominio_e_recusada(self):
        outro = criar_condominio("Beta")
        unidade_alheia = comunidade_apoio.criar_unidade(outro, identificador="101")

        resposta = self.client.post(
            reverse("portaria:chegou_encomenda"),
            {
                "condominio": str(self.condominio.pk),
                "unidade": str(unidade_alheia.pk),
            },
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(AcessoPortaria.objects.exists())


class PermissaoTests(BaseDaEncomenda):
    """Quem não opera a portaria não registra encomenda."""

    def test_morador_nao_registra_encomenda(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)

        resposta = self.client.post(
            reverse("portaria:chegou_encomenda"),
            {
                "condominio": str(self.condominio.pk),
                "unidade": str(self.unidade.pk),
            },
        )

        self.assertEqual(resposta.status_code, 404)

    def test_exige_post(self):
        resposta = self.client.get(reverse("portaria:chegou_encomenda"))

        self.assertEqual(resposta.status_code, 405)
