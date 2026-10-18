"""Testes das telas de entrada do balanço.

O que se verifica aqui é a fronteira: quem entra, quem não entra, o que a
listagem mostra, o que nunca aparece numa resposta — e, no fim, a confirmação:
o único momento em que o mês passa a valer.
"""
import uuid
from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.balancos.constants import LayoutBalanco, StatusBalanco, StatusImportacao
from apps.balancos.contratos import SCHEMA_VALIDADO, SCHEMA_VERSAO, de_dicionario_validado
from apps.balancos.extrator_atual import extrair_balanco_atual
from apps.balancos.models import BalancoMensal, ImportacaoBalanco
from apps.balancos.services import ErroDeConfirmacao
from apps.balancos.tests.apoio import (
    CONTEUDO_PDF,
    HASH_PDF,
    StorageDeMentira,
    criar_pessoa,
    criar_vinculo,
    gerar_pdf_textual,
    pdf,
)
from apps.balancos.tests.test_extracao_atual import (
    pdf_com_grupo_sem_lancamento,
    pdf_com_grupos_que_nao_fecham_no_total,
    pdf_com_grupos_reconciliados,
    pdf_com_leitura_acima_do_declarado,
    pdf_do_modelo_atual,
)
from apps.condominios.constants import Papel
from apps.condominios.models import Condominio

SEGREDO = "segredo-que-nao-pode-aparecer-na-tela"


def pdf_com_detalhamento_parcial() -> bytes:
    """Balanço cujos totais oficiais não fecham com o detalhe que a leitura achou.

    O documento declara R$ 5.000,00 de despesas e a leitura só conseguiu associar
    R$ 4.200,00. É o caso em que o mês vale pelos totais oficiais, com o residual
    dito — e não escondido para o balanço "fechar".
    """
    return gerar_pdf_textual(
        "\n".join(
            [
                "Condominio Exemplo",
                "Demonstrativo de Contas",
                "Competencia: Agosto/2026",
            ]
        ),
        "\n".join(
            [
                "Receitas Detalhadas",
                "Taxa Condominial 50.000,00",
                "Total de Receitas 50.000,00",
            ]
        ),
        "\n".join(
            [
                "Despesas Detalhadas",
                "Manutencao do Elevador LTDA 3.000,00",
                "Energia Eletrica 1.200,00",
                "Total de Despesas 5.000,00",
            ]
        ),
    )


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
        return self._importacao_do_documento(pdf_do_modelo_atual(), condominio, criado_por)

    def _importacao_do_documento(self, documento, condominio=None, criado_por=None):
        """Lê um documento específico e devolve a importação gravada."""
        importacao = self.importacao_em(
            condominio or self.condominio_a, criado_por=criado_por
        )
        importacao.layout_detectado = LayoutBalanco.ATUAL_CANONICO
        importacao.save(update_fields=["layout_detectado", "atualizado_em"])
        extrair_balanco_atual(importacao, conteudo=documento)
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

    def test_nao_lista_receitas_individuais(self):
        """O MVP não reproduz centenas de recebimentos: só o total do documento."""
        importacao = self._importacao_lida()
        resposta = self._abrir(importacao, self.sindica)

        self.assertContains(resposta, "R$ 50.000,00")
        self.assertNotContains(resposta, "Taxa Condominial")
        self.assertNotContains(resposta, "Fundo de Reserva")

    def test_conferencia_por_categoria_do_documento(self):
        """Cada categoria mostra o declarado, o identificado e a diferença."""
        importacao = self._importacao_do_documento(pdf_com_grupos_reconciliados())
        resposta = self._abrir(importacao, self.sindica)

        self.assertContains(resposta, "Despesas por categoria")
        self.assertContains(resposta, "Manutencao")
        self.assertContains(resposta, "Administracao")
        self.assertContains(resposta, "R$ 3.000,00")
        self.assertContains(resposta, "R$ 2.000,00")
        self.assertContains(resposta, "R$ 1.200,00")
        self.assertContains(resposta, "Confere")
        self.assertContains(resposta, "Falta identificar R$ 800,00")

    def test_categoria_guarda_os_lancamentos_atras_da_seta(self):
        """A tela abre enxuta; o lançamento fica a um toque, dentro da categoria."""
        importacao = self._importacao_do_documento(pdf_com_grupos_reconciliados())
        resposta = self._abrir(importacao, self.sindica)

        self.assertContains(resposta, 'data-bs-toggle="collapse"')
        self.assertContains(resposta, "Ver lançamentos")
        self.assertContains(resposta, "Reparo de portao")
        self.assertContains(resposta, "Honorarios contabeis")

    def test_categoria_sem_lancamento_diz_o_que_falta_identificar(self):
        importacao = self._importacao_do_documento(pdf_com_grupo_sem_lancamento())
        resposta = self._abrir(importacao, self.sindica)

        self.assertContains(resposta, "Elevadores")
        self.assertContains(resposta, "Falta identificar R$ 5.800,00")
        self.assertContains(resposta, "Nenhum lançamento foi identificado nesta categoria.")

    def test_aponta_quando_as_categorias_nao_fecham_com_o_total_do_documento(self):
        importacao = self._importacao_do_documento(
            pdf_com_grupos_que_nao_fecham_no_total()
        )
        resposta = self._abrir(importacao, self.sindica)

        self.assertContains(resposta, "não confere com o total do documento")
        self.assertContains(resposta, "R$ 8.500,00")

    def test_diz_o_que_falta_identificar_quando_a_leitura_encontrou_menos(self):
        importacao = self._importacao_do_documento(pdf_com_grupos_reconciliados())
        resposta = self._abrir(importacao, self.sindica)

        self.assertContains(resposta, "Não identificado")
        self.assertContains(resposta, "R$ 800,00")
        self.assertNotContains(resposta, "Acima do declarado")

    def test_nao_diz_o_inverso_quando_a_leitura_encontrou_mais(self):
        importacao = self._importacao_do_documento(pdf_com_leitura_acima_do_declarado())
        resposta = self._abrir(importacao, self.sindica)

        self.assertContains(resposta, "Acima do declarado")
        self.assertContains(resposta, "R$ 650,00")
        self.assertNotContains(resposta, "Não identificado")

    def test_nao_cria_balanco_mensal(self):
        importacao = self._importacao_lida()
        self._abrir(importacao, self.sindica)
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_oferece_confirmacao_quando_o_documento_pode_virar_balanco(self):
        """A leitura reconheceu o mês: a gestão pode confirmar — em etapa própria."""
        importacao = self._importacao_lida()
        resposta = self._abrir(importacao, self.sindica)

        self.assertContains(resposta, "Confirmar balanço")
        self.assertContains(
            resposta, "Os totais oficiais do documento foram identificados."
        )

    def test_nao_oferece_confirmacao_sem_leitura(self):
        """Sem dados lidos não há o que confirmar: a tela diz o que falta."""
        importacao = self.importacao_em(self.condominio_a)
        resposta = self._abrir(importacao, self.sindica)

        self.assertNotContains(resposta, "Confirmar balanço")

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


class BaseConfirmacaoTests(BaseTelasTests):
    """Apoio às telas de confirmação: importação lida, confirmação e abertura."""

    def _importacao_lida(self, documento=None, condominio=None, criado_por=None):
        """Uma importação do modelo atual, já lida e pronta para virar balanço."""
        importacao = self.importacao_em(
            condominio or self.condominio_a, criado_por=criado_por
        )
        importacao.layout_detectado = LayoutBalanco.ATUAL_CANONICO
        importacao.save(update_fields=["layout_detectado", "atualizado_em"])
        extrair_balanco_atual(importacao, conteudo=documento or pdf_do_modelo_atual())
        importacao.refresh_from_db()
        return importacao

    def _confirmar(self, importacao, usuario=None):
        self.client.force_login(usuario or self.sindica)
        return self.client.post(
            reverse("balancos:confirmar", args=[importacao.pk]), follow=True
        )

    def _abrir(self, importacao, usuario=None):
        self.client.force_login(usuario or self.sindica)
        return self.client.get(reverse("balancos:detalhe", args=[importacao.pk]))

    def _balanco(self):
        return BalancoMensal.objects.get()


class ConfirmacaoTests(BaseConfirmacaoTests):
    """Confirmar é a primeira vez que o mês passa a valer.

    Enquanto ninguém confirma, não existe balanço: a leitura só mostra o que
    entendeu. Confirmar não exige que todo lançamento tenha sido compreendido —
    o total oficial continua sendo o do documento, e o que ficou sem detalhe é
    dito ao lado, não escondido.
    """

    def test_confirma_cria_o_balanco_do_mes(self):
        importacao = self._importacao_lida()
        resposta = self._confirmar(importacao)
        balanco = self._balanco()

        self.assertRedirects(resposta, reverse("balancos:balanco", args=[balanco.pk]))
        self.assertEqual(balanco.condominio, self.condominio_a)
        self.assertEqual(balanco.competencia, date(2026, 8, 1))
        self.assertEqual(balanco.versao, 1)
        self.assertEqual(balanco.status, StatusBalanco.VALIDADO)
        self.assertEqual(balanco.criado_por, self.sindica)
        self.assertEqual(balanco.validado_por, self.sindica)
        self.assertIsNotNone(balanco.validado_em)

    def test_confirma_cria_o_documento_do_balanco(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        documento = self._balanco().documentos.get()

        self.assertEqual(documento.nome_original, importacao.nome_original)
        self.assertEqual(documento.sha256, importacao.sha256)
        self.assertEqual(documento.mime_type, importacao.mime_type)
        self.assertEqual(documento.enviado_por, self.sindica)

    def test_o_arquivo_continua_sendo_o_mesmo_objeto_do_storage(self):
        """Nada é enviado de novo: o balanço aponta para o arquivo já recebido."""
        importacao = self._importacao_lida()
        self.provider.objetos[importacao.storage_key] = CONTEUDO_PDF

        self._confirmar(importacao)

        documento = self._balanco().documentos.get()
        self.assertEqual(documento.storage_key, importacao.storage_key)
        self.assertEqual(documento.storage_bucket, importacao.storage_bucket)
        self.assertEqual(
            self.provider.objetos, {importacao.storage_key: CONTEUDO_PDF}
        )

    def test_a_importacao_fica_confirmada(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        importacao.refresh_from_db()

        self.assertEqual(importacao.status, StatusImportacao.CONFIRMADO)
        self.assertIsNotNone(importacao.confirmado_em)
        self.assertIsNone(importacao.descartado_em)
        self.assertTrue(ImportacaoBalanco.objects.filter(pk=importacao.pk).exists())

    def test_preserva_o_resultado_da_leitura(self):
        importacao = self._importacao_lida()
        lido = importacao.resultado_extraido
        self._confirmar(importacao)
        balanco = self._balanco()

        self.assertEqual(balanco.dados_extraidos, lido)
        self.assertEqual(balanco.dados_validados["leitura"], lido)

    def test_os_dados_validados_sao_versionados(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        balanco = self._balanco()

        self.assertEqual(balanco.dados_validados["schema_versao"], SCHEMA_VALIDADO)
        self.assertEqual(
            balanco.dados_validados["origem_schema_versao"], SCHEMA_VERSAO
        )
        validado = de_dicionario_validado(balanco.dados_validados)
        self.assertEqual(validado.competencia, "2026-08-01")
        self.assertIsNotNone(validado.confirmado_em)

    def test_preserva_os_pontos_de_atencao_apontados(self):
        """A confirmação não apaga o que ficou em aberto: ela assume."""
        importacao = self._importacao_lida(pdf_com_detalhamento_parcial())
        self.assertNotEqual(importacao.inconsistencias, [])

        self._confirmar(importacao)
        balanco = self._balanco()

        self.assertEqual(balanco.inconsistencias, importacao.inconsistencias)
        self.assertGreaterEqual(balanco.resumo["pontos_de_atencao"], 1)
        self.assertTrue(balanco.resumo["confirmado_com_ressalvas"])

    def test_o_total_oficial_nao_e_substituido_pela_soma_lida(self):
        importacao = self._importacao_lida(pdf_com_detalhamento_parcial())
        self._confirmar(importacao)
        balanco = self._balanco()

        self.assertEqual(balanco.resumo["despesas_total"], "5000.00")
        self.assertEqual(balanco.resumo["total_detalhado_identificado"], "4200.00")
        self.assertEqual(balanco.resumo["receitas_total"], "50000.00")

    def test_o_que_ficou_sem_associacao_e_registrado(self):
        importacao = self._importacao_lida(pdf_com_detalhamento_parcial())
        self._confirmar(importacao)
        balanco = self._balanco()

        self.assertEqual(balanco.resumo["valor_nao_associado"], "800.00")
        self.assertEqual(balanco.dados_validados["valor_nao_associado"], "800.00")

    def test_o_percentual_de_detalhamento_e_calculado(self):
        importacao = self._importacao_lida(pdf_com_detalhamento_parcial())
        self._confirmar(importacao)
        balanco = self._balanco()

        self.assertEqual(balanco.resumo["percentual_detalhamento"], "84.00")

    def test_confirma_mesmo_com_detalhamento_incompleto(self):
        importacao = self._importacao_lida(pdf_com_detalhamento_parcial())
        resposta = self._confirmar(importacao)

        self.assertContains(resposta, "Balanço confirmado.")
        self.assertEqual(BalancoMensal.objects.count(), 1)

    def test_confirma_sem_residual_nao_marca_ressalva(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        balanco = self._balanco()

        self.assertEqual(balanco.resumo["valor_nao_associado"], "0.00")
        self.assertEqual(balanco.resumo["percentual_detalhamento"], "100.00")
        self.assertFalse(balanco.resumo["confirmado_com_ressalvas"])

    def test_confirmar_de_novo_nao_cria_segundo_balanco(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        resposta = self._confirmar(importacao)

        self.assertContains(resposta, "Balanço confirmado.")
        self.assertEqual(BalancoMensal.objects.count(), 1)

    def test_competencia_ja_confirmada_nao_e_sobrescrita(self):
        primeira = self._importacao_lida()
        self._confirmar(primeira)

        segunda = self._importacao_lida()
        resposta = self._confirmar(segunda)

        self.assertContains(
            resposta, "Já existe balanço confirmado para esta competência."
        )
        self.assertEqual(BalancoMensal.objects.count(), 1)
        segunda.refresh_from_db()
        self.assertNotEqual(segunda.status, StatusImportacao.CONFIRMADO)
        self.assertIsNone(segunda.confirmado_em)

    def test_importacao_de_outro_condominio_e_negada(self):
        importacao = self._importacao_lida(
            condominio=self.condominio_b, criado_por=self.admin
        )
        resposta = self._confirmar(importacao)

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_morador_nao_confirma(self):
        importacao = self._importacao_lida()
        resposta = self._confirmar(importacao, usuario=self.morador)

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_porteiro_nao_confirma(self):
        importacao = self._importacao_lida()
        resposta = self._confirmar(importacao, usuario=self.porteiro)

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_operacional_nao_confirma(self):
        importacao = self._importacao_lida()
        resposta = self._confirmar(importacao, usuario=self.operacional)

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_sem_vinculo_nao_confirma(self):
        importacao = self._importacao_lida()
        resposta = self._confirmar(importacao, usuario=self.sem_vinculo)

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_confirmacao_exige_entrada(self):
        importacao = self._importacao_lida()
        resposta = self.client.post(
            reverse("balancos:confirmar", args=[importacao.pk])
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertIn(reverse("accounts:login"), resposta["Location"])
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_confirmacao_exige_post(self):
        importacao = self._importacao_lida()
        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:confirmar", args=[importacao.pk]))

        self.assertEqual(resposta.status_code, 405)
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_importacao_com_erro_nao_confirma(self):
        importacao = self._importacao_lida()
        importacao.status = StatusImportacao.ERRO
        importacao.save(update_fields=["status", "atualizado_em"])

        resposta = self._confirmar(importacao)

        self.assertContains(resposta, "A leitura deste documento ainda não terminou.")
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_importacao_legada_nao_confirma_por_este_fluxo(self):
        importacao = self._importacao_lida()
        importacao.layout_detectado = LayoutBalanco.LEGADO
        importacao.save(update_fields=["layout_detectado", "atualizado_em"])

        resposta = self._confirmar(importacao)

        self.assertContains(resposta, "Este documento ainda não foi lido pelo Eithra.")
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_importacao_descartada_nao_confirma(self):
        importacao = self._importacao_lida()
        from django.utils import timezone

        importacao.status = StatusImportacao.DESCARTADO
        importacao.descartado_em = timezone.now()
        importacao.save(update_fields=["status", "descartado_em", "atualizado_em"])

        resposta = self._confirmar(importacao)

        self.assertContains(resposta, "Uma importação descartada não vira balanço.")
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_falha_ao_criar_o_documento_desfaz_a_confirmacao(self):
        """Se qualquer passo falhar, nada fica: nem balanço, nem desfecho."""
        importacao = self._importacao_lida()
        falha = patch(
            "apps.balancos.services._criar_documento",
            side_effect=ErroDeConfirmacao("O balanço não foi confirmado."),
        )

        with falha:
            resposta = self._confirmar(importacao)

        self.assertContains(resposta, "O balanço não foi confirmado.")
        self.assertEqual(BalancoMensal.objects.count(), 0)
        importacao.refresh_from_db()
        self.assertEqual(importacao.status, StatusImportacao.PRONTO)
        self.assertIsNone(importacao.confirmado_em)

    def test_oferece_o_resumo_antes_de_confirmar(self):
        importacao = self._importacao_lida(pdf_com_detalhamento_parcial())
        resposta = self._abrir(importacao)

        self.assertContains(resposta, "Detalhamento das despesas")
        self.assertContains(resposta, "84,0% identificado")
        self.assertContains(resposta, "Não associado ao detalhamento")
        self.assertContains(resposta, "R$ 800,00")
        self.assertContains(resposta, "Parte do detalhamento ainda não pôde ser associada")

    def test_o_modal_de_confirmacao_nao_pede_preenchimento(self):
        importacao = self._importacao_lida()
        resposta = self._abrir(importacao)

        self.assertContains(resposta, 'id="confirmar-balanco"')
        self.assertContains(
            resposta, reverse("balancos:confirmar", args=[importacao.pk])
        )
        self.assertContains(resposta, "Confirmar o balanço de Agosto/2026?")

    def test_nao_confirma_sem_dados_lidos(self):
        importacao = self.importacao_em(self.condominio_a)
        importacao.layout_detectado = LayoutBalanco.ATUAL_CANONICO
        importacao.save(update_fields=["layout_detectado", "atualizado_em"])

        resposta = self._confirmar(importacao)

        self.assertContains(resposta, "A leitura deste documento não produziu dados.")
        self.assertEqual(BalancoMensal.objects.count(), 0)


class BalancoConfirmadoTests(BaseConfirmacaoTests):
    """O mês confirmado, como a gestão o vê: resumo, detalhe e residual."""

    def _confirmar_parcial(self):
        importacao = self._importacao_lida(pdf_com_detalhamento_parcial())
        self._confirmar(importacao)
        return self._balanco()

    def _tela(self, balanco, usuario=None):
        self.client.force_login(usuario or self.sindica)
        return self.client.get(reverse("balancos:balanco", args=[balanco.pk]))

    def test_mostra_o_mes_confirmado(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        resposta = self._tela(self._balanco())

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Balanço de Agosto/2026")
        self.assertContains(resposta, "Confirmado")
        self.assertContains(resposta, "Resumo do mês")
        self.assertContains(resposta, "R$ 16.650,00")
        self.assertContains(resposta, "Despesas por categoria")

    def test_preserva_os_totais_oficiais_e_diz_o_residual(self):
        balanco = self._confirmar_parcial()
        resposta = self._tela(balanco)

        self.assertContains(resposta, "R$ 5.000,00")
        self.assertContains(
            resposta, "Detalhamento identificado em 84,0% das despesas."
        )
        self.assertContains(resposta, "R$ 800,00")

    def test_nao_mostra_detalhe_de_pipeline(self):
        """A tela é de gestão: nada de score, schema ou referência de storage."""
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        balanco = self._balanco()
        resposta = self._tela(balanco)

        self.assertNotContains(resposta, balanco.documentos.get().storage_key)
        self.assertNotContains(resposta, "schema_versao")
        self.assertNotContains(resposta, "provenance")
        self.assertNotContains(resposta, "confianca")

    def test_detalhe_do_mes_e_do_condominio_de_quem_olha(self):
        criar_vinculo(self.admin, self.condominio_b, Papel.ADMIN)
        importacao = self._importacao_lida(
            condominio=self.condominio_b, criado_por=self.admin
        )
        self._confirmar(importacao, usuario=self.admin)
        balanco = self._balanco()

        self.assertEqual(self._tela(balanco, usuario=self.sindica).status_code, 404)
        self.assertEqual(self._tela(balanco, usuario=self.morador).status_code, 404)
        self.assertEqual(self._tela(balanco, usuario=self.admin).status_code, 200)

    def test_detalhe_do_mes_exige_entrada(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        balanco = self._balanco()
        self.client.logout()

        resposta = self.client.get(reverse("balancos:balanco", args=[balanco.pk]))

        self.assertEqual(resposta.status_code, 302)
        self.assertIn(reverse("accounts:login"), resposta["Location"])

    def test_mes_inexistente_e_negado(self):
        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:balanco", args=[uuid.uuid4()]))

        self.assertEqual(resposta.status_code, 404)

    def test_a_lista_aponta_para_o_balanco_confirmado(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        balanco = self._balanco()

        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:importacoes"))

        self.assertContains(resposta, "Balanço confirmado")
        self.assertContains(resposta, reverse("balancos:balanco", args=[balanco.pk]))

    def test_a_lista_nao_oferece_confirmar_nem_descartar_de_novo(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)

        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:importacoes"))

        self.assertNotContains(resposta, "Confirmar balanço")
        self.assertNotContains(
            resposta, reverse("balancos:descartar", args=[importacao.pk])
        )

    @override_settings(SUPABASE_SERVICE_ROLE_KEY=SEGREDO)
    def test_credencial_nao_aparece_no_mes_confirmado(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        resposta = self._tela(self._balanco())

        self.assertNotContains(resposta, SEGREDO)
