"""As telas da comunicação, do ponto de vista de quem usa.

O que se verifica aqui é que a escrita passa sempre pelo domínio, que a
autorização é a do backend e não a da tela, e que o isolamento por condomínio
vale na listagem, no detalhe e no arquivo. Nada de rede: o armazenamento é o de
mentira.
"""
from unittest.mock import patch

from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.core.context_processors import navigation

from .apoio import (
    StorageDeMentira,
    criar_condominio,
    criar_demanda,
    criar_pessoa,
    criar_vinculo,
)
from ..constants import OrigemComunicado, StatusComunicacao
from ..models import Comunicacao


class BaseDaComunicacaoTests(TestCase):
    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("sindica@exemplo.test", "Síndica")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.operacao = criar_pessoa("operacao@exemplo.test", "Operação")
        criar_vinculo(self.operacao, self.condominio, papel=Papel.OPERACIONAL)

        self.provider = StorageDeMentira()
        patcher = patch(
            "apps.comunicacao.arquivos.obter_provider", return_value=self.provider
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _comunicacao(self, autor=None, **campos):
        from .. import services

        dados = {"titulo": "Aviso aos moradores"}
        dados.update(campos)
        return services.criar_comunicacao(
            condominio=self.condominio, criado_por=autor or self.gestao, **dados
        )

    def _dados_do_formulario(self, **campos):
        dados = {
            "condominio": str(self.condominio.pk),
            "modelo": "aviso",
            "orientacao": "vertical",
            "titulo": "Aviso aos moradores",
            "mensagem": "O elevador para na sexta-feira.",
            "local": "Torre A",
            "periodo": "",
            "publico": "todos",
            "publico_descricao": "",
        }
        dados.update(campos)
        return dados


class ListagemTests(BaseDaComunicacaoTests):
    def test_quem_nao_ve_nada_recebe_404(self):
        forasteiro = criar_pessoa("forasteiro@exemplo.test", "Forasteiro")
        self.client.force_login(forasteiro)
        resposta = self.client.get(reverse("comunicacao:listagem"))
        self.assertEqual(resposta.status_code, 404)

    def test_lista_apenas_o_condominio_da_pessoa(self):
        self._comunicacao(titulo="Do Alpha")
        outro = criar_condominio("Beta")
        criar_vinculo(self.gestao, outro, papel=Papel.SINDICA)
        from .. import services

        services.criar_comunicacao(
            condominio=outro, criado_por=self.gestao, titulo="Do Beta"
        )

        self.client.force_login(self.gestao)
        resposta = self.client.get(reverse("comunicacao:listagem") + f"?condominio={self.condominio.pk}")

        self.assertEqual(resposta.status_code, 200)
        conteudo = resposta.content.decode()
        self.assertIn("Do Alpha", conteudo)
        self.assertNotIn("Do Beta", conteudo)


class CriarComunicacaoViewTests(BaseDaComunicacaoTests):
    def test_formulario_abre_para_quem_responde_pelo_condominio(self):
        self.client.force_login(self.gestao)
        resposta = self.client.get(reverse("comunicacao:criar"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Novo comunicado")

    def test_operacao_nao_abre_o_formulario(self):
        self.client.force_login(self.operacao)
        resposta = self.client.get(reverse("comunicacao:criar"))
        self.assertEqual(resposta.status_code, 404)

    def test_post_registra_rascunho_e_redireciona(self):
        self.client.force_login(self.gestao)
        resposta = self.client.post(
            reverse("comunicacao:criar"), self._dados_do_formulario()
        )

        self.assertEqual(resposta.status_code, 302)
        comunicacao = Comunicacao.objects.get(condominio=self.condominio)
        self.assertEqual(comunicacao.status, StatusComunicacao.RASCUNHO)
        self.assertEqual(comunicacao.titulo, "Aviso aos moradores")
        self.assertIn(str(comunicacao.pk), resposta.url)

    def test_post_sem_titulo_nao_registra(self):
        self.client.force_login(self.gestao)
        resposta = self.client.post(
            reverse("comunicacao:criar"), self._dados_do_formulario(titulo="")
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(Comunicacao.objects.count(), 0)

    def test_criar_a_partir_de_uma_demanda_registra_a_origem(self):
        demanda = criar_demanda(self.condominio, self.gestao, titulo="Falta de água")
        self.client.force_login(self.gestao)

        resposta = self.client.post(
            reverse("comunicacao:criar") + f"?demanda={demanda.pk}",
            self._dados_do_formulario(),
        )

        self.assertEqual(resposta.status_code, 302)
        comunicacao = Comunicacao.objects.get()
        self.assertEqual(comunicacao.origem_tipo, OrigemComunicado.DEMANDA)
        self.assertEqual(comunicacao.origem_demanda, demanda)

    def test_demanda_de_outro_condominio_recebe_404(self):
        outro = criar_condominio("Beta")
        alheia = criar_demanda(outro, self.gestao)
        self.client.force_login(self.gestao)

        resposta = self.client.get(
            reverse("comunicacao:criar") + f"?demanda={alheia.pk}"
        )
        self.assertEqual(resposta.status_code, 404)


class DetalheComunicacaoViewTests(BaseDaComunicacaoTests):
    def test_detalhe_abre_para_quem_acompanha(self):
        comunicacao = self._comunicacao()
        self.client.force_login(self.operacao)
        resposta = self.client.get(
            reverse("comunicacao:detalhe", args=[comunicacao.pk])
        )
        self.assertEqual(resposta.status_code, 200)
        # A operação vê o comunicado, mas não recebe o formulário de revisão.
        self.assertNotContains(resposta, "Salvar revisão")

    def test_detalhe_de_outro_condominio_recebe_404(self):
        outro = criar_condominio("Beta")
        autor = criar_pessoa("sindico-beta@exemplo.test", "Síndico Beta")
        criar_vinculo(autor, outro, papel=Papel.SINDICA)
        from .. import services

        alheia = services.criar_comunicacao(
            condominio=outro, criado_por=autor, titulo="Do Beta"
        )

        self.client.force_login(self.gestao)
        resposta = self.client.get(reverse("comunicacao:detalhe", args=[alheia.pk]))
        self.assertEqual(resposta.status_code, 404)

    def test_gestao_recebe_o_formulario_de_revisao(self):
        comunicacao = self._comunicacao()
        self.client.force_login(self.gestao)
        resposta = self.client.get(
            reverse("comunicacao:detalhe", args=[comunicacao.pk])
        )
        self.assertContains(resposta, "Salvar revisão")


class EditarComunicacaoViewTests(BaseDaComunicacaoTests):
    def test_operacao_e_recusada_com_403(self):
        comunicacao = self._comunicacao()
        self.client.force_login(self.operacao)
        resposta = self.client.post(
            reverse("comunicacao:editar", args=[comunicacao.pk]),
            self._dados_do_formulario(titulo="Outro título"),
        )
        self.assertEqual(resposta.status_code, 403)

    def test_gestao_revisa_o_comunicado(self):
        comunicacao = self._comunicacao()
        self.client.force_login(self.gestao)
        resposta = self.client.post(
            reverse("comunicacao:editar", args=[comunicacao.pk]),
            self._dados_do_formulario(titulo="Aviso revisado"),
        )

        self.assertEqual(resposta.status_code, 302)
        comunicacao.refresh_from_db()
        self.assertEqual(comunicacao.titulo, "Aviso revisado")

    def test_editar_exige_post(self):
        comunicacao = self._comunicacao()
        self.client.force_login(self.gestao)
        resposta = self.client.get(reverse("comunicacao:editar", args=[comunicacao.pk]))
        self.assertEqual(resposta.status_code, 405)


class GerarCardViewTests(BaseDaComunicacaoTests):
    def test_operacao_e_recusada_com_403(self):
        comunicacao = self._comunicacao()
        self.client.force_login(self.operacao)
        resposta = self.client.post(reverse("comunicacao:gerar", args=[comunicacao.pk]))
        self.assertEqual(resposta.status_code, 403)

    def test_gerar_exige_post(self):
        comunicacao = self._comunicacao()
        self.client.force_login(self.gestao)
        resposta = self.client.get(reverse("comunicacao:gerar", args=[comunicacao.pk]))
        self.assertEqual(resposta.status_code, 405)

    def test_gestao_gera_o_card(self):
        comunicacao = self._comunicacao()
        self.client.force_login(self.gestao)

        resposta = self.client.post(reverse("comunicacao:gerar", args=[comunicacao.pk]))

        self.assertEqual(resposta.status_code, 302)
        comunicacao.refresh_from_db()
        self.assertEqual(comunicacao.status, StatusComunicacao.PRONTA)
        self.assertIn(comunicacao.storage_key, self.provider.objetos)

    def test_falha_de_storage_vira_aviso_e_nao_muda_a_situacao(self):
        comunicacao = self._comunicacao()
        self.provider.falhar_ao_enviar = True
        self.client.force_login(self.gestao)

        resposta = self.client.post(
            reverse("comunicacao:gerar", args=[comunicacao.pk]), follow=True
        )

        self.assertEqual(resposta.status_code, 200)
        comunicacao.refresh_from_db()
        self.assertEqual(comunicacao.status, StatusComunicacao.RASCUNHO)
        self.assertContains(resposta, "Não foi possível guardar o card")


class ArquivoComunicacaoViewTests(BaseDaComunicacaoTests):
    def test_entrega_o_endereco_temporario(self):
        comunicacao = self._comunicacao()
        from .. import services

        services.gerar_card(comunicacao, usuario=self.gestao)
        self.client.force_login(self.gestao)

        resposta = self.client.get(reverse("comunicacao:arquivo", args=[comunicacao.pk]))

        self.assertEqual(resposta.status_code, 302)
        self.assertIn(comunicacao.storage_key, resposta["Location"])

    def test_card_ausente_recebe_404(self):
        comunicacao = self._comunicacao()
        self.client.force_login(self.gestao)
        resposta = self.client.get(reverse("comunicacao:arquivo", args=[comunicacao.pk]))
        self.assertEqual(resposta.status_code, 404)


class MenuTests(BaseDaComunicacaoTests):
    def _rotulos(self, usuario):
        pedido = RequestFactory().get("/")
        pedido.user = usuario
        return [item["label"] for item in navigation(pedido)["navigation"]]

    def test_gestao_e_operacao_enxergam_o_item(self):
        self.assertIn("Comunicação", self._rotulos(self.gestao))
        self.assertIn("Comunicação", self._rotulos(self.operacao))

    def test_quem_nao_tem_vinculo_nao_recebe_o_item(self):
        forasteiro = criar_pessoa("forasteiro@exemplo.test", "Forasteiro")
        self.assertNotIn("Comunicação", self._rotulos(forasteiro))
