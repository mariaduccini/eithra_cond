"""Testes das telas de entrada do balanço.

O que se verifica aqui é a fronteira: quem entra, quem não entra, o que a
listagem mostra e o que nunca aparece numa resposta.
"""
import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.balancos.constants import LayoutBalanco, StatusImportacao
from apps.balancos.extrator_atual import extrair_balanco_atual
from apps.balancos.models import BalancoMensal, ImportacaoBalanco
from apps.balancos.tests.apoio import HASH_PDF, StorageDeMentira, criar_pessoa, criar_vinculo, pdf
from apps.balancos.tests.test_extracao_atual import pdf_do_modelo_atual
from apps.condominios.constants import Papel
from apps.condominios.models import Condominio

SEGREDO = "segredo-que-nao-pode-aparecer-na-tela"


def detectar_layout_de_mentira(importacao, *, provider=None):
    """Fecha a importação como pronta para os testes de tela."""
    importacao.layout_detectado = LayoutBalanco.DESCONHECIDO
    importacao.status = StatusImportacao.PRONTO
    importacao.erro_processamento = ""
    importacao.save(
        update_fields=["layout_detectado", "status", "erro_processamento", "atualizado_em"]
    )
    return None


class BaseTelasTests(TestCase):
    """Cenário com dois condomínios e uma pessoa autorizada em cada."""

    def setUp(self):
        self.condominio_a = Condominio.objects.create(nome="Condomínio A")
        self.condominio_b = Condominio.objects.create(nome="Condomínio B")

        self.sindica = criar_pessoa("sindica@exemplo.com.br", "Síndica Teste")
        criar_vinculo(self.sindica, self.condominio_a, Papel.SINDICA)

        self.admin = criar_pessoa("admin@exemplo.com.br", "Administração Teste")
        criar_vinculo(self.admin, self.condominio_a, Papel.ADMIN)

        self.morador = criar_pessoa("morador@exemplo.com.br", "Morador Teste")
        criar_vinculo(self.morador, self.condominio_a, Papel.MORADOR)

        self.porteiro = criar_pessoa("porteiro@exemplo.com.br", "Porteiro Teste")
        criar_vinculo(self.porteiro, self.condominio_a, Papel.PORTEIRO)

        self.operacional = criar_pessoa("operacional@exemplo.com.br", "Operação Teste")
        criar_vinculo(self.operacional, self.condominio_a, Papel.OPERACIONAL)

        self.supervisora = criar_pessoa("supervisora@exemplo.com.br", "Supervisão Teste")
        criar_vinculo(self.supervisora, self.condominio_a, Papel.SUPERVISORA)

        self.sem_vinculo = criar_pessoa("estranho@exemplo.com.br", "Sem Vínculo")

        self.provider = StorageDeMentira()
        self.obter_provider = patch(
            "apps.balancos.services.obter_provider", return_value=self.provider
        )
        self.obter_provider.start()
        self.addCleanup(self.obter_provider.stop)
        self.detectar_layout = patch(
            "apps.balancos.services.detectar_layout_balanco",
            side_effect=detectar_layout_de_mentira,
        )
        self.detectar_layout.start()
        self.addCleanup(self.detectar_layout.stop)

    def importacao_em(self, condominio, criado_por=None):
        """Grava uma importação direto no banco, sem passar pelo upload."""
        return ImportacaoBalanco.objects.create(
            condominio=condominio,
            criado_por=criado_por or self.sindica,
            nome_original="balanco-do-mes.pdf",
            mime_type="application/pdf",
            tamanho_bytes=64,
            sha256=HASH_PDF,
            storage_provider="mentira",
            storage_bucket="bucket-de-teste",
            storage_key=f"balancos/{condominio.pk}/{uuid.uuid4()}/documento.pdf",
            status=StatusImportacao.PRONTO,
        )


class EntradaTests(BaseTelasTests):
    """A tela de importações exige entrada."""

    def test_listagem_exige_entrada(self):
        resposta = self.client.get(reverse("balancos:importacoes"))
        self.assertRedirects(
            resposta,
            f"{reverse('accounts:login')}?next={reverse('balancos:importacoes')}",
        )

    def test_upload_exige_entrada(self):
        resposta = self.client.post(reverse("balancos:importar"), {"arquivo": pdf()})
        self.assertEqual(resposta.status_code, 302)
        self.assertIn(reverse("accounts:login"), resposta["Location"])


class AutorizacaoTests(BaseTelasTests):
    """Importar balanço é de quem responde pelo documento."""

    def _pode_abrir(self, usuario):
        self.client.force_login(usuario)
        return self.client.get(reverse("balancos:importacoes"))

    def test_sem_vinculo_nao_entra(self):
        self.assertEqual(self._pode_abrir(self.sem_vinculo).status_code, 404)

    def test_morador_nao_entra(self):
        self.assertEqual(self._pode_abrir(self.morador).status_code, 404)

    def test_porteiro_nao_entra(self):
        self.assertEqual(self._pode_abrir(self.porteiro).status_code, 404)

    def test_operacional_nao_entra(self):
        self.assertEqual(self._pode_abrir(self.operacional).status_code, 404)

    def test_supervisora_nao_entra_sem_decisao_explicita(self):
        self.assertEqual(self._pode_abrir(self.supervisora).status_code, 404)

    def test_sindica_entra(self):
        self.assertEqual(self._pode_abrir(self.sindica).status_code, 200)

    def test_administracao_entra(self):
        self.assertEqual(self._pode_abrir(self.admin).status_code, 200)

    def test_vinculo_inativo_nao_autoriza(self):
        criar_vinculo(self.morador, self.condominio_b, Papel.SINDICA, ativo=False)
        self.assertEqual(self._pode_abrir(self.morador).status_code, 404)

    def test_morador_nao_importa_mesmo_enviando_o_formulario(self):
        self.client.force_login(self.morador)
        self.client.post(
            reverse("balancos:importar"),
            {"condominio": str(self.condominio_a.pk), "arquivo": pdf()},
        )
        self.assertEqual(ImportacaoBalanco.objects.count(), 0)


class UploadTests(BaseTelasTests):
    """O caminho de entrada do arquivo."""

    def test_sindica_recebe_o_arquivo(self):
        self.client.force_login(self.sindica)
        resposta = self.client.post(
            reverse("balancos:importar"),
            {"condominio": str(self.condominio_a.pk), "arquivo": pdf()},
            follow=True,
        )
        self.assertRedirects(resposta, reverse("balancos:importacoes"))
        self.assertContains(resposta, "Arquivo recebido.")

        importacao = ImportacaoBalanco.objects.get()
        self.assertEqual(importacao.condominio, self.condominio_a)
        self.assertEqual(importacao.status, StatusImportacao.PRONTO)
        self.assertEqual(importacao.layout_detectado, "desconhecido")
        self.assertEqual(importacao.resultado_extraido, {})
        self.assertEqual(importacao.inconsistencias, [])

    def test_upload_nao_cria_balanco_mensal(self):
        self.client.force_login(self.sindica)
        self.client.post(
            reverse("balancos:importar"),
            {"condominio": str(self.condominio_a.pk), "arquivo": pdf()},
        )
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_endereco_de_upload_nao_aceita_visita_direta(self):
        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:importar"))
        self.assertRedirects(resposta, reverse("balancos:importacoes"))

    def test_arquivo_invalido_volta_para_a_tela_sem_registrar(self):
        self.client.force_login(self.sindica)
        resposta = self.client.post(
            reverse("balancos:importar"),
            {"condominio": str(self.condominio_a.pk), "arquivo": pdf(conteudo=b"nao sou pdf")},
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(ImportacaoBalanco.objects.count(), 0)

    def test_condominio_de_outro_vinculo_e_recusado(self):
        """O condomínio vem do vínculo da pessoa, não do navegador."""
        self.client.force_login(self.sindica)
        resposta = self.client.post(
            reverse("balancos:importar"),
            {"condominio": str(self.condominio_b.pk), "arquivo": pdf()},
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(ImportacaoBalanco.objects.count(), 0)

    def test_condominio_ausente_e_recusado(self):
        self.client.force_login(self.sindica)
        resposta = self.client.post(
            reverse("balancos:importar"), {"arquivo": pdf()}
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(ImportacaoBalanco.objects.count(), 0)

    def test_repeticao_avisa_sem_duplicar(self):
        """O mesmo conteúdo, enviado de novo, é sinalizado e não duplica."""
        self.client.force_login(self.sindica)
        primeiro = {
            "condominio": str(self.condominio_a.pk),
            "arquivo": pdf(nome="primeiro-envio.pdf"),
        }
        segundo = {
            "condominio": str(self.condominio_a.pk),
            "arquivo": pdf(nome="segundo-envio.pdf"),
        }
        self.client.post(reverse("balancos:importar"), primeiro)
        resposta = self.client.post(reverse("balancos:importar"), segundo, follow=True)

        self.assertContains(resposta, "já havia sido recebido")
        self.assertEqual(ImportacaoBalanco.objects.count(), 1)

    def test_falha_de_storage_avisa_sem_fingir_sucesso(self):
        self.provider.falhar_ao_enviar = True
        self.client.force_login(self.sindica)
        resposta = self.client.post(
            reverse("balancos:importar"),
            {"condominio": str(self.condominio_a.pk), "arquivo": pdf()},
            follow=True,
        )
        self.assertNotContains(resposta, "Arquivo recebido.")
        self.assertContains(
            resposta,
            "Não foi possível processar o arquivo automaticamente.",
        )

    def test_formulario_pede_o_condominio_quando_ha_mais_de_um(self):
        criar_vinculo(self.sindica, self.condominio_b, Papel.SINDICA)
        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:importacoes"))
        formulario = resposta.context["formulario_importacao"]
        self.assertEqual(
            list(formulario.fields["condominio"].queryset),
            [self.condominio_a, self.condominio_b],
        )


class MenuContextualTests(BaseTelasTests):
    """O item administrativo aparece só para quem pode usá-lo.

    Isto é navegação, não segurança: o item existe para não oferecer um caminho
    que terminaria em 404. Quem protege a tela continua sendo a view.
    """

    def _menu(self, usuario):
        """Devolve os rótulos do menu como a pessoa os vê."""
        self.client.force_login(usuario)
        resposta = self.client.get(reverse("core:home"))
        return [item["label"] for item in resposta.context["navigation"]]

    def test_aparece_para_sindica(self):
        self.assertIn("Balanços", self._menu(self.sindica))

    def test_aparece_para_administracao(self):
        self.assertIn("Balanços", self._menu(self.admin))

    def test_nao_aparece_para_morador(self):
        self.assertNotIn("Balanços", self._menu(self.morador))

    def test_nao_aparece_para_porteiro(self):
        self.assertNotIn("Balanços", self._menu(self.porteiro))

    def test_nao_aparece_para_operacional(self):
        self.assertNotIn("Balanços", self._menu(self.operacional))

    def test_nao_aparece_para_supervisora(self):
        self.assertNotIn("Balanços", self._menu(self.supervisora))

    def test_nao_aparece_sem_vinculo(self):
        self.assertNotIn("Balanços", self._menu(self.sem_vinculo))

    def test_nao_aparece_com_vinculo_inativo(self):
        criar_vinculo(self.morador, self.condominio_b, Papel.ADMIN, ativo=False)
        self.assertNotIn("Balanços", self._menu(self.morador))

    def test_nunca_aparece_em_condominio_sem_papel_autorizado(self):
        """O papel conta no condomínio em que é exercido."""
        so_morador = criar_pessoa("so-morador@exemplo.com.br", "Só Morador")
        criar_vinculo(so_morador, self.condominio_b, Papel.MORADOR)
        self.assertNotIn("Balanços", self._menu(so_morador))

    def test_painel_continua_visivel_para_todos(self):
        """O menu contextual não esvazia a navegação de ninguém."""
        self.assertEqual(self._menu(self.morador), ["Painel"])
        self.assertIn("Painel", self._menu(self.sindica))

    def test_menu_pergunta_ao_dominio(self):
        """Não existe lista de papéis paralela: a regra é a mesma do backend."""
        from apps.balancos import permissoes

        self.assertTrue(permissoes.pode_importar_em_algum_condominio(self.sindica))
        self.assertTrue(permissoes.pode_importar_em_algum_condominio(self.admin))
        self.assertFalse(permissoes.pode_importar_em_algum_condominio(self.morador))
        self.assertFalse(permissoes.pode_importar_em_algum_condominio(self.porteiro))
        self.assertFalse(permissoes.pode_importar_em_algum_condominio(self.operacional))
        self.assertFalse(permissoes.pode_importar_em_algum_condominio(self.supervisora))
        self.assertFalse(permissoes.pode_importar_em_algum_condominio(self.sem_vinculo))

    def test_esconder_o_menu_nao_desprotege_a_tela(self):
        """Sem o item, a rota continua negando quem não pode."""
        with patch("apps.core.context_processors.NAVIGATION", []):
            self.client.force_login(self.morador)
            resposta = self.client.get(reverse("balancos:importacoes"))
            self.assertEqual(resposta.status_code, 404)

    def test_esconder_o_menu_nao_impede_quem_pode(self):
        """Sem o item, quem tem autorização continua entrando pela rota."""
        with patch("apps.core.context_processors.NAVIGATION", []):
            self.client.force_login(self.sindica)
            resposta = self.client.get(reverse("balancos:importacoes"))
            self.assertEqual(resposta.status_code, 200)

    def test_rota_administrativa_nao_e_do_morador(self):
        """A rota é administrativa: o morador não a alcança por endereço direto."""
        self.client.force_login(self.morador)
        self.assertEqual(
            self.client.get(reverse("balancos:importacoes")).status_code, 404
        )
        self.assertEqual(
            self.client.post(
                reverse("balancos:importar"),
                {"condominio": str(self.condominio_a.pk), "arquivo": pdf()},
            ).status_code,
            404,
        )


class ListagemTests(BaseTelasTests):
    """A listagem é de apoio: mostra o que chegou, e só do que é da pessoa."""

    def test_mostra_o_arquivo_recebido(self):
        self.importacao_em(self.condominio_a)
        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:importacoes"))
        self.assertContains(resposta, "balanco-do-mes.pdf")
        self.assertContains(resposta, self.condominio_a.nome)

    def test_nao_mostra_arquivo_de_condominio_sem_vinculo(self):
        self.importacao_em(self.condominio_b, criado_por=self.admin)
        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:importacoes"))
        self.assertEqual(list(resposta.context["importacoes"]), [])

    def test_tela_vazia_explica_o_proposito(self):
        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:importacoes"))
        self.assertContains(resposta, "Nenhum arquivo recebido ainda")

    def test_nao_mistura_importacao_com_balanco_validado(self):
        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:importacoes"))
        self.assertEqual(resposta.context["importacoes"].model, ImportacaoBalanco)


class ArquivoTests(BaseTelasTests):
    """Ver o arquivo passa por um acesso curto, sempre autorizado."""

    def test_acesso_ao_arquivo_autorizado_redireciona_para_endereco_temporario(self):
        importacao = self.importacao_em(self.condominio_a)
        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:arquivo", args=[importacao.pk]))

        self.assertEqual(resposta.status_code, 302)
        self.assertIn(importacao.storage_key, resposta["Location"])
        self.assertEqual(self.provider.assinaturas[0][0], importacao.storage_key)

    def test_acesso_ao_arquivo_de_outro_condominio_e_negado(self):
        importacao = self.importacao_em(self.condominio_b, criado_por=self.admin)
        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:arquivo", args=[importacao.pk]))
        self.assertEqual(resposta.status_code, 404)

    def test_acesso_ao_arquivo_inexistente_e_negado(self):
        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:arquivo", args=[uuid.uuid4()]))
        self.assertEqual(resposta.status_code, 404)

    def test_acesso_ao_arquivo_exige_entrada(self):
        importacao = self.importacao_em(self.condominio_a)
        resposta = self.client.get(reverse("balancos:arquivo", args=[importacao.pk]))
        self.assertEqual(resposta.status_code, 302)
        self.assertIn(reverse("accounts:login"), resposta["Location"])


class DescarteTests(BaseTelasTests):
    """Descartar é uma recusa explícita, com memória."""

    def test_descarte_marca_a_importacao(self):
        importacao = self.importacao_em(self.condominio_a)
        self.client.force_login(self.sindica)
        resposta = self.client.post(
            reverse("balancos:descartar", args=[importacao.pk]), follow=True
        )

        self.assertContains(resposta, "Importação descartada.")
        importacao.refresh_from_db()
        self.assertEqual(importacao.status, StatusImportacao.DESCARTADO)
        self.assertIsNotNone(importacao.descartado_em)
        self.assertTrue(ImportacaoBalanco.objects.filter(pk=importacao.pk).exists())

    def test_descarte_de_outro_condominio_e_negado(self):
        importacao = self.importacao_em(self.condominio_b, criado_por=self.admin)
        self.client.force_login(self.sindica)
        resposta = self.client.post(reverse("balancos:descartar", args=[importacao.pk]))
        self.assertEqual(resposta.status_code, 404)

    def test_descarte_exige_post(self):
        importacao = self.importacao_em(self.condominio_a)
        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:descartar", args=[importacao.pk]))
        self.assertEqual(resposta.status_code, 405)

    def test_descarte_exige_entrada(self):
        importacao = self.importacao_em(self.condominio_a)
        resposta = self.client.post(reverse("balancos:descartar", args=[importacao.pk]))
        self.assertEqual(resposta.status_code, 302)
        self.assertIn(reverse("accounts:login"), resposta["Location"])


class SegredoTests(BaseTelasTests):
    """A credencial do storage fica só no backend."""

    @override_settings(SUPABASE_SERVICE_ROLE_KEY=SEGREDO)
    def test_credencial_nao_aparece_na_tela(self):
        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:importacoes"))
        self.assertNotContains(resposta, SEGREDO)

    @override_settings(SUPABASE_SERVICE_ROLE_KEY=SEGREDO)
    def test_credencial_nao_aparece_na_resposta_do_upload(self):
        self.client.force_login(self.sindica)
        resposta = self.client.post(
            reverse("balancos:importar"),
            {"condominio": str(self.condominio_a.pk), "arquivo": pdf()},
            follow=True,
        )
        self.assertNotContains(resposta, SEGREDO)


class DetalheTests(BaseTelasTests):
    """A tela de detalhe mostra o que foi lido — e só para quem pode conferir."""

    def _importacao_lida(self, condominio=None, criado_por=None):
        """Grava uma importação do modelo atual já com os dados financeiros lidos."""
        importacao = self.importacao_em(
            condominio or self.condominio_a, criado_por=criado_por
        )
        importacao.layout_detectado = LayoutBalanco.ATUAL_CANONICO
        importacao.save(update_fields=["layout_detectado", "atualizado_em"])
        extrair_balanco_atual(importacao, conteudo=pdf_do_modelo_atual())
        importacao.refresh_from_db()
        return importacao

    def _abrir(self, importacao, usuario):
        self.client.force_login(usuario)
        return self.client.get(reverse("balancos:detalhe", args=[importacao.pk]))

    def test_exige_entrada(self):
        importacao = self._importacao_lida()
        resposta = self.client.get(reverse("balancos:detalhe", args=[importacao.pk]))
        self.assertEqual(resposta.status_code, 302)
        self.assertIn(reverse("accounts:login"), resposta["Location"])

    def test_mostra_o_que_foi_lido(self):
        importacao = self._importacao_lida()
        resposta = self._abrir(importacao, self.sindica)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Dados lidos")
        self.assertContains(resposta, "Agosto/2026")
        self.assertContains(resposta, "R$ 50.000,00")
        self.assertContains(resposta, "R$ 16.650,00")
        self.assertContains(resposta, "Manutencao do Elevador LTDA")

    def test_nao_cria_balanco_mensal(self):
        importacao = self._importacao_lida()
        self._abrir(importacao, self.sindica)
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_nao_oferece_confirmacao_ainda(self):
        """Confirmar é etapa futura: a tela não promete um botão que não existe."""
        importacao = self._importacao_lida()
        resposta = self._abrir(importacao, self.sindica)
        self.assertNotContains(resposta, "Confirmar")

    def test_importacao_de_outro_condominio_e_negada(self):
        importacao = self._importacao_lida(self.condominio_b, criado_por=self.admin)
        resposta = self._abrir(importacao, self.sindica)
        self.assertEqual(resposta.status_code, 404)

    def test_morador_nao_alcanca_o_detalhe(self):
        importacao = self._importacao_lida()
        resposta = self._abrir(importacao, self.morador)
        self.assertEqual(resposta.status_code, 404)

    def test_inexistente_e_negada(self):
        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:detalhe", args=[uuid.uuid4()]))
        self.assertEqual(resposta.status_code, 404)

    def test_formato_nao_reconhecido_mostra_o_motivo_sem_dados(self):
        importacao = self.importacao_em(self.condominio_a)
        resposta = self._abrir(importacao, self.sindica)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "não foi possível reconhecer")
        self.assertNotContains(resposta, "Dados lidos")

    def test_formato_legado_aponta_para_a_leitura_futura(self):
        importacao = self.importacao_em(self.condominio_a)
        importacao.layout_detectado = LayoutBalanco.LEGADO
        importacao.save(update_fields=["layout_detectado", "atualizado_em"])
        resposta = self._abrir(importacao, self.sindica)

        self.assertContains(
            resposta,
            "Este formato será processado pela leitura de documentos legados.",
        )

    @override_settings(SUPABASE_SERVICE_ROLE_KEY=SEGREDO)
    def test_credencial_nao_aparece_no_detalhe(self):
        importacao = self._importacao_lida()
        resposta = self._abrir(importacao, self.sindica)
        self.assertNotContains(resposta, SEGREDO)
