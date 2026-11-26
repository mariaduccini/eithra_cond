"""Testes das telas de entrada do balanço.

O que se verifica aqui é a fronteira: quem entra, quem não entra, o que a
listagem mostra, o que nunca aparece numa resposta — e, no fim, a confirmação:
o único momento em que o mês passa a valer.
"""
import hashlib
import re
import uuid
from datetime import date
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.balancos import correcoes, evidencias
from apps.balancos.categorias import CategoriaEithra
from apps.balancos.constants import (
    LayoutBalanco,
    StatusBalanco,
    StatusImportacao,
    TipoVinculoEvidencia,
)
from apps.balancos.contratos import SCHEMA_VALIDADO, SCHEMA_VERSAO, de_dicionario_validado
from apps.balancos.extrator_atual import extrair_balanco_atual
from apps.balancos.extrator_legado import extrair_balanco_legado
from apps.balancos.models import BalancoMensal, EvidenciaVisualBalanco, ImportacaoBalanco
from apps.balancos.services import (
    ErroDeConfirmacao,
    confirmar_importacao,
    publicar_balanco_da_conferencia,
)
from apps.balancos.storage import ErroDeStorage
from apps.balancos.tests.apoio import (
    CONTEUDO_PDF,
    CONTEUDO_PNG,
    HASH_PDF,
    StorageDeMentira,
    criar_pessoa,
    criar_vinculo,
    foto,
    gerar_pdf_textual,
    jpg,
    pdf,
    png,
    webp,
)
from apps.balancos.tests.test_extracao_atual import (
    pdf_com_grupo_sem_lancamento,
    pdf_com_grupos_que_nao_fecham_no_total,
    pdf_com_grupos_reconciliados,
    pdf_com_leitura_acima_do_declarado,
    pdf_do_modelo_atual,
)
from apps.balancos.tests.test_extracao_legado import texto_do_balancete
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


def pdf_com_grupo_a_conferir() -> bytes:
    """Balanço confirmável com um agrupamento declarado que não rendeu lançamento.

    O mês vale pelos totais oficiais do documento; o agrupamento que o
    documento declara e a leitura não conseguiu detalhar aparece como ponto de
    atenção — sem lançamento inventado e sem diferença escondida.
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
                "Elevadores",
                "TOTAL DA CONTA ELEVADORES 5.800,00",
                "Total de Despesas 5.800,00",
            ]
        ),
    )


def pdf_com_grupos_confirmavel() -> bytes:
    """Balanço confirmável com dois agrupamentos declarados.

    Um agrupamento fecha com o subtotal do documento; o outro não. Serve para
    exercitar a correção de agrupamento com o mês já valendo pelos totais
    oficiais — a mesma estrutura de ``pdf_com_grupos_reconciliados``, com os
    blocos de competência e receitas que a confirmação exige.
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
                "Manutencao",
                "Troca de bomba do recalque 2.500,00",
                "Reparo de portao 500,00",
                "TOTAL DA CONTA MANUTENCAO 3.000,00",
                "Administracao",
                "Honorarios contabeis 1.200,00",
                "TOTAL DA CONTA ADMINISTRACAO 2.000,00",
                "Total de Despesas 5.000,00",
            ]
        ),
    )


def detectar_layout_de_mentira(importacao, *, provider=None, conteudo=None):
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
        criar_vinculo(self.admin, self.condominio_a, Papel.ADMINISTRADORA)

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
        criar_vinculo(self.morador, self.condominio_b, Papel.ADMINISTRADORA, ativo=False)
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
    """A listagem é de apoio: mostra o que chegou, e só do que é da pessoa.

    A linha precisa deixar dois caminhos separados e visíveis: o PDF original,
    que é o documento da administração, e a leitura do Eithra, que é onde a
    gestão confere antes de confirmar.
    """

    def _importacao_lida(self):
        importacao = self.importacao_em(self.condominio_a)
        importacao.layout_detectado = LayoutBalanco.ATUAL_CANONICO
        importacao.save(update_fields=["layout_detectado", "atualizado_em"])
        extrair_balanco_atual(importacao, conteudo=pdf_do_modelo_atual())
        return importacao

    def _lista(self):
        self.client.force_login(self.sindica)
        return self.client.get(reverse("balancos:importacoes"))

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

    def test_diz_que_espera_confirmacao_depois_da_leitura(self):
        self._importacao_lida()
        resposta = self._lista()

        self.assertContains(resposta, "Aguardando confirmação")
        self.assertNotContains(resposta, "Pronto para a próxima etapa")

    def test_sem_leitura_oferece_ler_de_novo_e_o_arquivo_original(self):
        """Sem leitura, a linha oferece reler e o arquivo original.

        A releitura é segura: ela roda sobre o arquivo já guardado, então o que
        sobra é o PDF original, a releitura e a exclusão.
        """
        importacao = self.importacao_em(self.condominio_a)
        resposta = self._lista()

        self.assertContains(resposta, "Dados ainda não lidos")
        self.assertContains(resposta, "Ver o PDF original")
        self.assertContains(
            resposta, reverse("balancos:excluir", args=[importacao.pk])
        )
        self.assertContains(resposta, reverse("balancos:reler", args=[importacao.pk]))
        self.assertContains(resposta, "Ler de novo")
        self.assertNotContains(resposta, "Ver dados")

    def test_a_coluna_de_acoes_se_identifica_e_fica_centralizada(self):
        """A coluna de ações tem cabeçalho visível — não um rótulo só para leitor."""
        self._importacao_lida()
        resposta = self._lista()

        self.assertContains(
            resposta, '<th scope="col" class="text-center">Ações</th>', html=False
        )

    def test_o_olho_leva_ao_pdf_e_ver_dados_leva_a_leitura(self):
        importacao = self._importacao_lida()
        resposta = self._lista()
        corpo = resposta.content.decode()
        destino = re.escape(reverse("balancos:detalhe", args=[importacao.pk]))

        self.assertContains(resposta, "Ver o PDF original")
        self.assertContains(
            resposta, reverse("balancos:arquivo", args=[importacao.pk])
        )
        self.assertRegex(corpo, rf'href="{destino}">\s*Ver dados')

    def test_importacao_com_erro_diz_o_que_aconteceu(self):
        importacao = self.importacao_em(self.condominio_a)
        importacao.status = StatusImportacao.ERRO
        importacao.save(update_fields=["status", "atualizado_em"])

        resposta = self._lista()

        self.assertContains(resposta, "Não foi possível receber")


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


class ExclusaoTests(BaseTelasTests):
    """Excluir apaga a importação: o registro sai e o arquivo é liberado.

    O banco primeiro, o bucket depois: a remoção do objeto só é disparada
    quando a transação confirma, e é por isso que os testes que a observam
    passam por ``captureOnCommitCallbacks``.
    """

    def test_exclui_a_importacao_e_libera_o_arquivo(self):
        importacao = self.importacao_em(self.condominio_a)
        chave = importacao.storage_key
        self.client.force_login(self.sindica)

        with self.captureOnCommitCallbacks(execute=True):
            resposta = self.client.post(
                reverse("balancos:excluir", args=[importacao.pk]), follow=True
            )

        self.assertContains(resposta, "Importação excluída.")
        self.assertFalse(ImportacaoBalanco.objects.filter(pk=importacao.pk).exists())
        self.assertIn(chave, self.provider.removidos)

    def test_falha_ao_liberar_o_arquivo_nao_impede_a_exclusao(self):
        importacao = self.importacao_em(self.condominio_a)

        def falhar(*, key):
            raise ErroDeStorage("Não foi possível falar com o armazenamento.")

        self.provider.remover = falhar
        self.client.force_login(self.sindica)

        with self.captureOnCommitCallbacks(execute=True):
            resposta = self.client.post(
                reverse("balancos:excluir", args=[importacao.pk]), follow=True
            )

        self.assertContains(resposta, "Importação excluída.")
        self.assertFalse(ImportacaoBalanco.objects.filter(pk=importacao.pk).exists())

    def test_a_importacao_excluida_sai_da_lista(self):
        importacao = self.importacao_em(self.condominio_a)
        self.client.force_login(self.sindica)

        resposta = self.client.post(
            reverse("balancos:excluir", args=[importacao.pk]), follow=True
        )

        self.assertNotContains(resposta, "balanco-do-mes.pdf")
        self.assertContains(resposta, "Nenhum arquivo recebido ainda")

    def test_importacao_confirmada_nao_e_excluida(self):
        from django.utils import timezone

        importacao = self.importacao_em(self.condominio_a)
        importacao.status = StatusImportacao.CONFIRMADO
        importacao.confirmado_em = timezone.now()
        importacao.save(update_fields=["status", "confirmado_em", "atualizado_em"])
        self.client.force_login(self.sindica)

        resposta = self.client.post(
            reverse("balancos:excluir", args=[importacao.pk]), follow=True
        )

        self.assertContains(resposta, "já virou balanço e não pode ser excluída")
        self.assertTrue(ImportacaoBalanco.objects.filter(pk=importacao.pk).exists())

    def test_exclusao_de_outro_condominio_e_negada(self):
        importacao = self.importacao_em(self.condominio_b, criado_por=self.admin)
        self.client.force_login(self.sindica)
        resposta = self.client.post(reverse("balancos:excluir", args=[importacao.pk]))
        self.assertEqual(resposta.status_code, 404)

    def test_exclusao_exige_post(self):
        importacao = self.importacao_em(self.condominio_a)
        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:excluir", args=[importacao.pk]))
        self.assertEqual(resposta.status_code, 405)

    def test_exclusao_exige_entrada(self):
        importacao = self.importacao_em(self.condominio_a)
        resposta = self.client.post(reverse("balancos:excluir", args=[importacao.pk]))
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
            "Este é um balancete antigo: quem o lê é a leitura de documentos legados.",
        )

    @override_settings(SUPABASE_SERVICE_ROLE_KEY=SEGREDO)
    def test_credencial_nao_aparece_no_detalhe(self):
        importacao = self._importacao_lida()
        resposta = self._abrir(importacao, self.sindica)
        self.assertNotContains(resposta, SEGREDO)


class ConferenciaDoBalanceteAntigoTests(BaseTelasTests):
    """O balancete antigo passa pela tela de conferência — não vira mês num clique.

    A leitura por OCR tem menos garantias: a gestão confere os valores e publica
    de lá. O caminho de um clique continua reservado ao modelo atual.
    """

    def _importacao_legada(self, condominio=None, criado_por=None):
        importacao = self.importacao_em(
            condominio or self.condominio_a, criado_por=criado_por
        )
        importacao.layout_detectado = LayoutBalanco.LEGADO
        importacao.save(update_fields=["layout_detectado", "atualizado_em"])
        extrair_balanco_legado(
            importacao, conteudo=gerar_pdf_textual(texto_do_balancete())
        )
        importacao.refresh_from_db()
        return importacao

    def test_o_detalhe_oferece_a_conferencia_no_lugar_do_clique(self):
        importacao = self._importacao_legada()
        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:detalhe", args=[importacao.pk]))

        self.assertContains(resposta, "Conferir e publicar")
        self.assertContains(
            resposta, reverse("balancos:conferencia", args=[importacao.pk])
        )
        self.assertNotContains(
            resposta, reverse("balancos:confirmar", args=[importacao.pk])
        )

    def test_a_confirmacao_num_clique_recusa_o_balancete_antigo(self):
        importacao = self._importacao_legada()
        self.client.force_login(self.sindica)
        resposta = self.client.post(
            reverse("balancos:confirmar", args=[importacao.pk]), follow=True
        )

        self.assertContains(resposta, "Este documento ainda não foi lido pelo Eithra.")
        self.assertEqual(BalancoMensal.objects.count(), 0)

    def test_publicar_pela_conferencia_cria_o_mes_do_balancete_antigo(self):
        importacao = self._importacao_legada()
        balanco = publicar_balanco_da_conferencia(
            condominio=self.condominio_a,
            usuario=self.sindica,
            competencia=date(2026, 8, 1),
            saldo_anterior="119495.71",
            receitas_total="63836.20",
            despesas_total="70130.98",
            saldo_final="113200.93",
            receitas=[("Taxa condominial", "63836.20")],
            despesas=[("Folha de pagamento", "70130.98")],
            importacao=importacao,
        )

        self.assertEqual(balanco.layout_detectado, LayoutBalanco.LEGADO)
        self.assertEqual(balanco.condominio, self.condominio_a)
        self.assertEqual(balanco.competencia, date(2026, 8, 1))
        importacao.refresh_from_db()
        self.assertEqual(importacao.layout_detectado, LayoutBalanco.LEGADO)
        self.assertIsNotNone(importacao.confirmado_em)


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

    def _confirmar_documento(self, documento):
        importacao = self._importacao_lida(documento)
        self._confirmar(importacao)
        return self._balanco()

    def _confirmar_parcial(self):
        return self._confirmar_documento(pdf_com_detalhamento_parcial())

    def _tela(self, balanco, usuario=None):
        self.client.force_login(usuario or self.sindica)
        return self.client.get(reverse("balancos:balanco", args=[balanco.pk]))

    def test_mostra_o_mes_confirmado(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        resposta = self._tela(self._balanco())

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Balanço · Agosto/2026")
        self.assertContains(resposta, "Condomínio A / Agosto/2026")
        self.assertContains(resposta, "Confirmado")
        self.assertContains(resposta, "Ver PDF original")
        self.assertContains(resposta, "Receitas")
        self.assertContains(resposta, "R$ 16.650,00")
        self.assertContains(resposta, "Despesas por categoria")

    def test_mostra_a_visao_gerencial_das_despesas(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        resposta = self._tela(self._balanco())

        self.assertContains(resposta, "Recorrentes")
        self.assertContains(resposta, "R$ 15.450,00")
        self.assertContains(resposta, "Extras")
        self.assertContains(resposta, "R$ 0,00")
        self.assertContains(resposta, "A confirmar")
        self.assertContains(resposta, "R$ 1.200,00")
        self.assertContains(resposta, "Não associado")

    def test_preserva_os_totais_oficiais_e_diz_o_residual(self):
        balanco = self._confirmar_parcial()
        resposta = self._tela(balanco)

        self.assertContains(resposta, "R$ 5.000,00")
        self.assertContains(resposta, "Detalhamento identificado: 84,0%")
        self.assertContains(resposta, "R$ 800,00 ainda não associados.")
        self.assertContains(resposta, "Detalhamento incompleto")

    def test_categorias_aparecem_ordenadas_por_total(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        resposta = self._tela(self._balanco())
        corpo = resposta.content.decode()

        self.assertLess(corpo.index("Pessoal"), corpo.index("Energia"))
        self.assertLess(corpo.index("Energia"), corpo.index("Elevadores"))

    def test_categoria_expansivel_mostra_os_lancamentos(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        resposta = self._tela(self._balanco())

        self.assertContains(resposta, 'data-bs-target="#categoria-desktop-1"')
        self.assertContains(resposta, "Folha de Pagamento")
        self.assertContains(resposta, "Energia Eletrica")
        self.assertContains(resposta, "Manutencao do Elevador LTDA")

    def test_saldos_aparecem_em_tabela_simples(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        resposta = self._tela(self._balanco())

        self.assertContains(resposta, "Saldos")
        self.assertContains(resposta, "Conta/Fundo")
        self.assertContains(resposta, "Saldo anterior")
        self.assertContains(resposta, "Saldo atual")
        self.assertContains(resposta, "Saldo Anterior")
        self.assertContains(resposta, "Saldo Final")

    def test_grupos_com_diferenca_ganham_bloco_discreto(self):
        balanco = self._confirmar_documento(pdf_com_grupo_a_conferir())
        resposta = self._tela(balanco)

        self.assertContains(resposta, "Pontos que precisam de atenção")
        self.assertContains(resposta, "1 grupo precisa de atenção.")
        self.assertContains(resposta, "Elevadores")
        self.assertContains(resposta, "R$ 5.800,00")

    def test_pontos_de_atencao_continuam_visiveis_sem_jargao(self):
        balanco = self._confirmar_parcial()
        resposta = self._tela(balanco)

        self.assertContains(resposta, "Pontos de atenção")
        self.assertContains(
            resposta,
            "A soma das despesas lançadas (R$ 4.200,00) não confere com o total do documento (R$ 5.000,00).",
        )

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
        self.assertNotContains(resposta, "RECORRENTE_FIXA")
        self.assertNotContains(resposta, "EXTRAORDINARIA")

    def test_detalhe_do_mes_e_do_condominio_de_quem_olha(self):
        criar_vinculo(self.admin, self.condominio_b, Papel.ADMINISTRADORA)
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

    def test_sem_vinculo_nao_alcanca_o_mes_confirmado(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        resposta = self._tela(self._balanco(), usuario=self.sem_vinculo)

        self.assertEqual(resposta.status_code, 404)

    def test_pdf_original_do_mes_confirmado_abre_por_acesso_curto(self):
        importacao = self._importacao_lida()
        self.provider.objetos[importacao.storage_key] = CONTEUDO_PDF
        self._confirmar(importacao)
        balanco = self._balanco()

        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:balanco_arquivo", args=[balanco.pk]))

        self.assertEqual(resposta.status_code, 302)
        self.assertIn(importacao.storage_key, resposta["Location"])

    def test_a_lista_aponta_para_o_balanco_confirmado(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        balanco = self._balanco()

        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:importacoes"))

        self.assertContains(resposta, "Balanços confirmados")
        self.assertContains(resposta, "Agosto/2026")
        self.assertContains(resposta, "R$ 50.000,00")
        self.assertContains(resposta, "R$ 16.650,00")
        self.assertContains(resposta, "Balanço confirmado")
        self.assertContains(resposta, reverse("balancos:balanco", args=[balanco.pk]))

    def test_a_lista_nao_oferece_confirmar_nem_excluir_de_novo(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)

        self.client.force_login(self.sindica)
        resposta = self.client.get(reverse("balancos:importacoes"))

        self.assertNotContains(resposta, "Confirmar balanço")
        self.assertNotContains(
            resposta, reverse("balancos:excluir", args=[importacao.pk])
        )

    @override_settings(SUPABASE_SERVICE_ROLE_KEY=SEGREDO)
    def test_credencial_nao_aparece_no_mes_confirmado(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        resposta = self._tela(self._balanco())

        self.assertNotContains(resposta, SEGREDO)

    def test_markup_mobile_da_categoria_fica_em_cards_compactos(self):
        importacao = self._importacao_lida()
        self._confirmar(importacao)
        resposta = self._tela(self._balanco())

        self.assertContains(resposta, 'class="table-responsive d-none d-lg-block"', html=False)
        self.assertContains(resposta, 'class="d-lg-none"', html=False)


class BaseCorrecaoTests(BaseConfirmacaoTests):
    """Apoio à correção manual: o mês confirmado e os lançamentos como valem.

    Nenhum teste mora aqui — só o que os testes de correção repetem. O mês é
    confirmado na primeira chamada, e cada teste escolhe o documento de que
    precisa sem esbarrar no balanço do outro.
    """

    def setUp(self):
        super().setUp()
        self._mes_confirmado = None

    def _mes(self, documento=None):
        """O balanço confirmado deste teste, criado na primeira chamada."""
        if self._mes_confirmado is None:
            importacao = self._importacao_lida(documento)
            self._confirmar(importacao)
            self._mes_confirmado = self._balanco()
        return self._mes_confirmado

    def _validado(self, balanco=None):
        balanco = balanco or self._mes()
        balanco.refresh_from_db()
        return de_dicionario_validado(balanco.dados_validados)

    def _leitura(self, balanco=None):
        return self._validado(balanco).leitura or {}

    def _revisoes(self, balanco=None):
        return correcoes.revisoes(self._leitura(balanco))

    def _lancamento(self, trecho, balanco=None):
        """O lançamento válido cuja descrição contém o trecho dado."""
        for item in correcoes.despesas_efetivas(self._leitura(balanco)):
            if trecho in item["descricao"]:
                return item
        raise AssertionError(f"Nenhum lançamento com {trecho!r} neste balanço.")

    def _total_da_categoria(self, codigo, balanco=None):
        for item in self._leitura(balanco).get("categorias") or []:
            if item.get("categoria") == codigo:
                return item.get("total")
        return None

    def _grupo(self, nome, balanco=None):
        for item in self._leitura(balanco).get("grupos") or []:
            if item.get("nome") == nome:
                return item
        return None

    def _correcao_de(self, lancamento, **mudancas):
        """O que a tela enviaria: o lançamento como está, com o que foi decidido."""
        dados = {
            "lancamento_id": lancamento["id"],
            "descricao": lancamento["descricao"],
            "valor": lancamento["valor"],
            "categoria": lancamento["categoria_eithra"],
            "natureza": lancamento["natureza"],
            "grupo": lancamento["grupo"] or "",
            "justificativa": "Conferido contra o documento.",
        }
        dados.update(mudancas)
        return dados

    def _post_correcao(self, lancamento, usuario=None, **mudancas):
        balanco = self._mes()
        self.client.force_login(usuario or self.sindica)
        return self.client.post(
            reverse("balancos:corrigir_lancamento", args=[balanco.pk]),
            self._correcao_de(lancamento, **mudancas),
            follow=True,
        )

    def _post_nao_despesa(self, lancamento, usuario=None, justificativa=None):
        balanco = self._mes()
        self.client.force_login(usuario or self.sindica)
        return self.client.post(
            reverse("balancos:marcar_nao_despesa", args=[balanco.pk]),
            {
                "lancamento_id": lancamento["id"],
                "justificativa": (
                    justificativa
                    if justificativa is not None
                    else "Não é despesa do mês."
                ),
            },
            follow=True,
        )

    def _post_manual(self, usuario=None, **campos):
        balanco = self._mes()
        dados = {
            "descricao": "Taxa de lixo extraordinária",
            "valor": "500,00",
            "categoria": "outros",
            "natureza": "extraordinaria",
            "grupo": "",
            "justificativa": "Cobrança avulsa do mês.",
        }
        dados.update(campos)
        self.client.force_login(usuario or self.sindica)
        return self.client.post(
            reverse("balancos:adicionar_lancamento", args=[balanco.pk]),
            dados,
            follow=True,
        )


class CorrecaoManualTests(BaseCorrecaoTests):
    """Corrigir o detalhamento do mês: quem pode, o que muda e o que fica.

    A máquina leu o documento; a gestão responde por ele. O que se verifica aqui
    é que corrigir muda o estado validado e o histórico — e não toca no que foi
    lido nem nos totais oficiais do documento.
    """

    def test_admin_corrige_o_lancamento(self):
        lancamento = self._lancamento("Energia Eletrica")
        antes = self._validado()

        resposta = self._post_correcao(lancamento, usuario=self.admin, valor="2.450,00")

        self.assertContains(resposta, "Correção registrada.")
        depois = self._validado()
        self.assertNotEqual(depois.leitura, antes.leitura)
        self.assertEqual(self._total_da_categoria("energia"), "2450.00")

    def test_sindica_corrige_o_lancamento(self):
        lancamento = self._lancamento("Energia Eletrica")

        resposta = self._post_correcao(lancamento, usuario=self.sindica, valor="2.450,00")

        self.assertContains(resposta, "Correção registrada.")
        self.assertEqual(self._total_da_categoria("energia"), "2450.00")

    def test_supervisora_nao_edita(self):
        """A supervisão acompanha o mês; responder por ele é de quem o importa."""
        balanco = self._mes()
        lancamento = self._lancamento("Energia Eletrica")
        antes = self._validado()

        self.client.force_login(self.supervisora)
        resposta = self.client.post(
            reverse("balancos:corrigir_lancamento", args=[balanco.pk]),
            self._correcao_de(lancamento, valor="2.450,00"),
        )

        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(self._validado().leitura, antes.leitura)
        self.assertEqual(self._revisoes(), [])

    def test_demais_papeis_nao_editam(self):
        balanco = self._mes()
        lancamento = self._lancamento("Energia Eletrica")

        for pessoa in (self.morador, self.porteiro, self.operacional, self.sem_vinculo):
            with self.subTest(pessoa=pessoa.email):
                self.client.force_login(pessoa)
                resposta = self.client.post(
                    reverse("balancos:corrigir_lancamento", args=[balanco.pk]),
                    self._correcao_de(lancamento, valor="2.450,00"),
                )
                self.assertEqual(resposta.status_code, 404)

        self.assertEqual(self._revisoes(), [])

    def test_outro_condominio_e_negado(self):
        """Quem responde por outro condomínio não alcança este mês."""
        balanco = self._mes()
        lancamento = self._lancamento("Energia Eletrica")
        outro = criar_pessoa("admin-b@exemplo.com.br", "Administração B")
        criar_vinculo(outro, self.condominio_b, Papel.ADMINISTRADORA)

        self.client.force_login(outro)
        resposta = self.client.post(
            reverse("balancos:corrigir_lancamento", args=[balanco.pk]),
            self._correcao_de(lancamento, valor="2.450,00"),
        )

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(self._revisoes(), [])

    def test_justificativa_e_obrigatoria(self):
        balanco = self._mes()
        lancamento = self._lancamento("Energia Eletrica")
        antes = self._validado()

        self.client.force_login(self.sindica)
        resposta = self.client.post(
            reverse("balancos:corrigir_lancamento", args=[balanco.pk]),
            self._correcao_de(lancamento, valor="2.450,00", justificativa="   "),
            follow=True,
        )

        self.assertContains(resposta, "A correção precisa de uma justificativa.")
        self.assertEqual(self._validado().leitura, antes.leitura)
        self.assertEqual(self._revisoes(), [])

    def test_dados_extraidos_nao_mudam(self):
        """O que a máquina leu continua intocado depois de qualquer ajuste."""
        balanco = self._mes()
        lancamento = self._lancamento("Energia Eletrica")
        extraidos = balanco.dados_extraidos

        self._post_correcao(lancamento, valor="2.450,00")
        self._post_manual()

        balanco.refresh_from_db()
        self.assertEqual(balanco.dados_extraidos, extraidos)
        self.assertEqual(balanco.dados_extraidos["despesas"][1]["valor"], "3450.00")

    def test_dados_validados_mudam(self):
        balanco = self._mes()
        lancamento = self._lancamento("Energia Eletrica")
        antes = balanco.dados_validados

        self._post_correcao(lancamento, valor="2.450,00")

        balanco.refresh_from_db()
        self.assertNotEqual(balanco.dados_validados, antes)
        self.assertEqual(balanco.dados_validados["leitura"]["despesas"][1]["correcao"], {"valor": "2450.00"})

    def test_a_revisao_fica_no_historico(self):
        lancamento = self._lancamento("Energia Eletrica")

        self._post_correcao(lancamento, valor="2.450,00")

        revisoes = self._revisoes()
        self.assertEqual(len(revisoes), 1)
        self.assertEqual(revisoes[0]["tipo"], "correcao")
        self.assertEqual(revisoes[0]["lancamento_id"], lancamento["id"])
        self.assertEqual(revisoes[0]["justificativa"], "Conferido contra o documento.")
        self.assertEqual(revisoes[0]["autor"], "Síndica Teste")
        self.assertEqual(
            revisoes[0]["mudancas"],
            [{"campo": "valor", "antes": "3450.00", "depois": "2450.00"}],
        )

    def test_a_segunda_revisao_preserva_a_primeira(self):
        lancamento = self._lancamento("Energia Eletrica")

        self._post_correcao(lancamento, valor="2.450,00")
        self._post_correcao(
            self._lancamento("Energia Eletrica"), descricao="Energia Elétrica revisada"
        )

        revisoes = self._revisoes()
        self.assertEqual(len(revisoes), 2)
        self.assertEqual(revisoes[0]["mudancas"][0]["campo"], "valor")
        self.assertEqual(revisoes[1]["mudancas"][0]["campo"], "descricao")

    def test_alterar_o_valor_recalcula(self):
        lancamento = self._lancamento("Energia Eletrica")

        self._post_correcao(lancamento, valor="2.450,00")

        self.assertEqual(self._total_da_categoria("energia"), "2450.00")
        self.assertEqual(self._validado().total_detalhado_identificado, "15650.00")

    def test_alterar_a_categoria_recalcula(self):
        lancamento = self._lancamento("Energia Eletrica")

        self._post_correcao(lancamento, categoria="manutencao")

        self.assertEqual(self._total_da_categoria("manutencao"), "3450.00")
        self.assertIsNone(self._total_da_categoria("energia"))

    def test_alterar_a_natureza_recalcula(self):
        lancamento = self._lancamento("Energia Eletrica")
        self.assertEqual(self._leitura()["resumo"]["recorrente_total"], "15450.00")
        self.assertEqual(self._leitura()["resumo"]["extraordinaria_total"], "0.00")

        self._post_correcao(lancamento, natureza="extraordinaria")

        resumo = self._leitura()["resumo"]
        self.assertEqual(resumo["recorrente_total"], "12000.00")
        self.assertEqual(resumo["extraordinaria_total"], "3450.00")
        self.assertEqual(resumo["nao_determinada_total"], "1200.00")

    def test_alterar_o_grupo_recalcula(self):
        balanco = self._mes(pdf_com_grupos_confirmavel())
        self.assertEqual(self._grupo("Manutencao", balanco)["soma_extraida"], "3000.00")

        portao = self._lancamento("Reparo de portao", balanco)
        self.client.force_login(self.sindica)
        self.client.post(
            reverse("balancos:corrigir_lancamento", args=[balanco.pk]),
            self._correcao_de(portao, grupo="Administracao"),
            follow=True,
        )

        self.assertEqual(self._grupo("Manutencao", balanco)["soma_extraida"], "2500.00")
        self.assertEqual(self._grupo("Administracao", balanco)["soma_extraida"], "1700.00")

    def test_nao_despesa_sai_da_soma(self):
        balanco = self._mes()
        lancamento = self._lancamento("Energia Eletrica")
        self.assertEqual(self._validado().total_detalhado_identificado, "16650.00")

        resposta = self._post_nao_despesa(lancamento)

        self.assertContains(resposta, "Lançamento marcado como não sendo despesa.")
        self.assertEqual(self._validado().total_detalhado_identificado, "13200.00")
        guardado = [
            item
            for item in correcoes.despesas_efetivas(
                self._leitura(), incluir_ignoradas=True
            )
            if item["id"] == lancamento["id"]
        ]
        self.assertTrue(guardado and guardado[0]["excluida"])
        self.assertEqual(self._revisoes()[0]["tipo"], "nao_despesa")

    def test_lancamento_manual_entra_na_soma(self):
        balanco = self._mes()
        self.assertEqual(self._validado().total_detalhado_identificado, "16650.00")

        resposta = self._post_manual(valor="500,00")

        self.assertContains(resposta, "Lançamento adicionado.")
        self.assertEqual(self._validado().total_detalhado_identificado, "17150.00")
        manuais = [
            item
            for item in correcoes.despesas_efetivas(self._leitura())
            if item["manual"]
        ]
        self.assertEqual(len(manuais), 1)
        self.assertEqual(manuais[0]["valor"], "500.00")
        self.assertEqual(manuais[0]["descricao"], "Taxa de lixo extraordinária")
        self.assertEqual(self._revisoes()[0]["tipo"], "inclusao")

    def test_o_total_oficial_nao_muda(self):
        balanco = self._mes()
        lancamento = self._lancamento("Energia Eletrica")

        self._post_correcao(lancamento, valor="2.450,00")
        self._post_nao_despesa(self._lancamento("Folha de Pagamento"))
        self._post_manual(valor="500,00")

        validado = self._validado()
        self.assertEqual(validado.despesas_total, "16650.00")
        self.assertEqual(validado.total_declarado_despesas, "16650.00")
        self.assertEqual(validado.receitas_total, "50000.00")
        self.assertEqual(self._leitura()["despesas_total"], "16650.00")
        self.assertEqual(balanco.resumo["despesas_total"], "16650.00")

    def test_o_residual_recalcula(self):
        balanco = self._mes()
        lancamento = self._lancamento("Energia Eletrica")
        self.assertEqual(self._validado().valor_nao_associado, "0.00")

        self._post_correcao(lancamento, valor="2.450,00")

        self.assertEqual(self._validado().valor_nao_associado, "1000.00")
        self.assertEqual(self._validado().percentual_detalhamento, "93.99")
        self.assertEqual(balanco.resumo["valor_nao_associado"], "1000.00")

    def test_erro_nao_deixa_rastro(self):
        """Uma correção que o domínio recusa não deixa nada pela metade."""
        balanco = self._mes()
        antes = balanco.dados_validados

        self.client.force_login(self.sindica)
        resposta = self.client.post(
            reverse("balancos:corrigir_lancamento", args=[balanco.pk]),
            {
                "lancamento_id": "nao-existe",
                "descricao": "Lançamento inventado",
                "valor": "10,00",
                "categoria": "outros",
                "natureza": "nao_determinada",
                "grupo": "",
                "justificativa": "Teste de rollback.",
            },
            follow=True,
        )

        self.assertContains(resposta, "O lançamento não foi encontrado neste balanço.")
        balanco.refresh_from_db()
        self.assertEqual(balanco.dados_validados, antes)
        self.assertEqual(self._revisoes(), [])

    def test_versao_desatualizada_e_recusada(self):
        """Corrigir sobre um estado que já mudou seria pior do que pedir recarga."""
        balanco = self._mes()
        # O estado de partida é lido à parte: listar os lançamentos para escolher
        # um lhes dá identidade, e isso não pode contaminar o "antes" comparado.
        antes = BalancoMensal.objects.get(pk=balanco.pk).dados_validados
        lancamento = self._lancamento("Energia Eletrica")

        resposta = self._post_correcao(
            lancamento, valor="2.450,00", versao="2020-01-01T00:00:00+00:00"
        )

        self.assertContains(resposta, "Recarregue a página")
        balanco.refresh_from_db()
        self.assertEqual(balanco.dados_validados, antes)
        self.assertEqual(self._revisoes(), [])


class CorrecaoManualNaTelaTests(BaseCorrecaoTests):
    """O que a tela do mês mostra sobre o ajuste — e o que ela não mostra."""

    def _tela(self, balanco, usuario=None):
        self.client.force_login(usuario or self.sindica)
        return self.client.get(reverse("balancos:balanco", args=[balanco.pk]))

    def test_a_tela_oferece_corrigir_e_adicionar(self):
        resposta = self._tela(self._mes())

        self.assertContains(resposta, "Adicionar lançamento")
        self.assertContains(resposta, 'data-bs-target="#ajustar-lancamento"', html=False)
        self.assertContains(resposta, 'title="Corrigir"', html=False)
        self.assertContains(resposta, "Justificativa")
        self.assertContains(resposta, "Não é despesa")

    def test_supervisora_le_o_mes_sem_acao_de_correcao(self):
        balanco = self._mes()

        resposta = self._tela(balanco, usuario=self.supervisora)

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, 'id="ajustar-lancamento"', html=False)
        self.assertNotContains(resposta, "Adicionar lançamento")

    def test_indicadores_de_ajuste_e_de_inclusao(self):
        lancamento = self._lancamento("Energia Eletrica")
        self._post_correcao(lancamento, valor="2.450,00")
        self._post_manual()

        resposta = self._tela(self._mes())

        self.assertContains(resposta, "Ajustado manualmente")
        self.assertContains(resposta, "lido R$ 3.450,00")
        self.assertContains(resposta, "Adicionado manualmente")

    def test_o_historico_aparece_na_expansao_com_o_antes_e_o_depois(self):
        lancamento = self._lancamento("Energia Eletrica")
        self._post_correcao(lancamento, valor="2.450,00")
        self._post_manual()

        resposta = self._tela(self._mes())

        self.assertContains(resposta, "Ajustes (2)")
        self.assertContains(resposta, "Ajustes feitos à mão")
        self.assertContains(resposta, "Lançamento ajustado")
        self.assertContains(resposta, "Lançamento adicionado")
        self.assertContains(resposta, "Valor:")
        self.assertContains(resposta, "R$ 3.450,00")
        self.assertContains(resposta, "R$ 2.450,00")
        self.assertContains(resposta, "Conferido contra o documento.")
        self.assertContains(resposta, "Cobrança avulsa do mês.")
        self.assertContains(resposta, "Síndica Teste")

    def test_o_historico_nao_expoe_detalhe_de_sistema(self):
        lancamento = self._lancamento("Energia Eletrica")
        self._post_correcao(lancamento, valor="2.450,00")

        resposta = self._tela(self._mes())

        self.assertNotContains(resposta, "schema_versao")
        self.assertNotContains(resposta, "provenance")
        self.assertNotContains(resposta, "confianca")
        # No histórico, o lançamento aparece pelo que ele é — nunca pelo
        # identificador interno que o liga à linha corrigida.
        historico = resposta.content.decode().rsplit('id="ajustes-do-balanco"', 1)[1]
        self.assertNotIn(lancamento["id"], historico)

    def test_quem_nao_corrige_nao_ve_os_modais(self):
        balanco = self._mes()

        resposta = self._tela(balanco, usuario=self.supervisora)

        self.assertNotContains(resposta, 'id="adicionar-lancamento"', html=False)
        self.assertNotContains(resposta, 'id="form-corrigir"', html=False)


# -- Evidências visuais ---------------------------------------------------------


class BaseEvidenciaTests(BaseCorrecaoTests):
    """Apoio às evidências visuais: o mês confirmado, fotos e um bucket só delas.

    As fotos ficam sob observação separada do documento: o storage do balanço é
    um, o das imagens é outro. É assim que se prova que a imagem entra no bucket
    privado e que o acesso a ela é pedido na hora — e nunca guardado.
    """

    def setUp(self):
        super().setUp()
        self.fotos = StorageDeMentira(bucket="bucket-de-fotos")
        self.provider_das_fotos = patch(
            "apps.balancos.evidencias.obter_provider", return_value=self.fotos
        )
        self.provider_das_fotos.start()
        self.addCleanup(self.provider_das_fotos.stop)

        self.sindica_b = criar_pessoa("sindica-b@exemplo.com.br", "Síndica do B")
        criar_vinculo(self.sindica_b, self.condominio_b, Papel.SINDICA)

    # -- o mês ----------------------------------------------------------------

    def _mes_do_b(self):
        """Um mês confirmado no outro condomínio, para provar o isolamento."""
        importacao = self._importacao_lida(condominio=self.condominio_b)
        return confirmar_importacao(importacao, usuario=self.sindica_b)

    # -- envio ----------------------------------------------------------------

    def _enviar(
        self,
        balanco,
        *,
        usuario=None,
        imagem=None,
        tipo_vinculo=None,
        referencia=None,
        legenda="",
        exibir_aos_moradores=None,
    ):
        """Envia uma foto para um mês, como a tela faria."""
        dados = {
            "tipo_vinculo": tipo_vinculo or TipoVinculoEvidencia.LANCAMENTO,
            "referencia": (
                self._lancamento("Energia Eletrica", balanco)["id"]
                if referencia is None
                else referencia
            ),
            "legenda": legenda,
            "imagem": png() if imagem is None else imagem,
        }
        if exibir_aos_moradores is not None:
            dados["exibir_aos_moradores"] = "on" if exibir_aos_moradores else ""
        self.client.force_login(usuario or self.sindica)
        return self.client.post(
            reverse("balancos:evidencia_adicionar", args=[balanco.pk]),
            dados,
            follow=True,
        )

    def _anexar(self, usuario=None, **campos):
        """Anexa uma foto ao mês deste teste, como a tela faria."""
        return self._enviar(self._mes(), usuario=usuario, **campos)

    def _foto_anexada(self, **campos):
        """Anexa e devolve a evidência gravada."""
        self._anexar(**campos)
        return self._unica()

    def _editar(self, evidencia, usuario=None, **campos):
        dados = {"legenda": "", "exibir_aos_moradores": ""}
        dados.update(campos)
        self.client.force_login(usuario or self.sindica)
        return self.client.post(
            reverse("balancos:evidencia_editar", args=[self._mes().pk, evidencia.pk]),
            dados,
            follow=True,
        )

    def _remover(self, evidencia, usuario=None):
        self.client.force_login(usuario or self.sindica)
        return self.client.post(
            reverse("balancos:evidencia_remover", args=[self._mes().pk, evidencia.pk]),
            follow=True,
        )

    # -- leitura --------------------------------------------------------------

    def _evidencias(self):
        return EvidenciaVisualBalanco.objects.order_by("criado_em")

    def _unica(self):
        return self._evidencias().get()

    def _abrir(self, usuario=None, balanco=None):
        self.client.force_login(usuario or self.sindica)
        return self.client.get(
            reverse("balancos:balanco", args=[(balanco or self._mes()).pk])
        )


class EvidenciaVisualTests(BaseEvidenciaTests):
    """A foto que explica o mês: quem anexa, o que é conferido e o que fica.

    A evidência é complementar: ela ilustra o número, não o substitui. O que se
    verifica aqui é que a foto entra no bucket privado, presa a um contexto que
    existe de verdade, sem tocar no detalhamento do balanço.
    """

    def test_administracao_anexa_a_foto(self):
        resposta = self._anexar(usuario=self.admin, legenda="  Troca do portão  ")

        self.assertContains(resposta, "Foto anexada.")
        evidencia = self._unica()
        self.assertEqual(evidencia.criado_por, self.admin)
        self.assertEqual(evidencia.legenda, "Troca do portão")
        self.assertEqual(evidencia.mime_type, "image/png")
        self.assertTrue(evidencia.ativo)
        self.assertIsNone(evidencia.removido_em)

    def test_sindica_anexa_a_foto(self):
        resposta = self._anexar(usuario=self.sindica)

        self.assertContains(resposta, "Foto anexada.")
        self.assertEqual(self._evidencias().count(), 1)

    def test_supervisora_le_o_mes_mas_nao_anexa(self):
        resposta = self._anexar(usuario=self.supervisora)

        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(self._evidencias().exists())

    def test_operacao_portaria_e_morador_nao_sao_avisados_do_mes(self):
        for pessoa in (self.operacional, self.porteiro, self.morador):
            with self.subTest(pessoa=pessoa.full_name):
                self.assertEqual(self._anexar(usuario=pessoa).status_code, 404)

        self.assertFalse(self._evidencias().exists())

    def test_pessoa_de_outro_condominio_nao_anexa(self):
        resposta = self._anexar(usuario=self.sindica_b)

        self.assertEqual(resposta.status_code, 404)
        self.assertFalse(self._evidencias().exists())

    def test_balanco_de_outro_condominio_e_negado(self):
        balanco_b = self._mes_do_b()

        resposta = self._enviar(balanco_b, usuario=self.sindica, referencia="qualquer")

        self.assertEqual(resposta.status_code, 404)
        self.assertFalse(
            EvidenciaVisualBalanco.objects.filter(balanco=balanco_b).exists()
        )

    def test_arquivo_que_nao_e_imagem_e_recusado(self):
        resposta = self._anexar(
            imagem=foto("comprovante.pdf", CONTEUDO_PDF, "application/pdf")
        )

        self.assertContains(resposta, "não é uma imagem aceita")
        self.assertFalse(self._evidencias().exists())

    def test_tipo_declarado_diferente_do_arquivo_e_recusado(self):
        resposta = self._anexar(
            imagem=foto("foto.png", CONTEUDO_PNG, "application/pdf")
        )

        self.assertContains(resposta, "não corresponde a uma imagem aceita")
        self.assertFalse(self._evidencias().exists())

    @override_settings(BALANCOS_MAX_FOTO_BYTES=200)
    def test_foto_acima_do_limite_e_recusada(self):
        resposta = self._anexar(imagem=foto("foto.png", CONTEUDO_PNG + b"\x00" * 500, "image/png"))

        self.assertContains(resposta, "passa do limite de")
        self.assertFalse(self._evidencias().exists())

    def test_a_foto_fica_presa_ao_lancamento(self):
        lancamento = self._lancamento("Energia Eletrica")

        self._anexar(referencia=lancamento["id"])

        evidencia = self._unica()
        self.assertEqual(evidencia.tipo_vinculo, TipoVinculoEvidencia.LANCAMENTO)
        self.assertEqual(evidencia.referencia, lancamento["id"])

    def test_a_foto_fica_presa_a_categoria(self):
        self._anexar(
            tipo_vinculo=TipoVinculoEvidencia.CATEGORIA,
            referencia=CategoriaEithra.OBRAS_MELHORIAS,
        )

        evidencia = self._unica()
        self.assertEqual(evidencia.tipo_vinculo, TipoVinculoEvidencia.CATEGORIA)
        self.assertEqual(evidencia.referencia, "obras_melhorias")

    def test_a_foto_do_destaque_nao_tem_referencia(self):
        self._anexar(tipo_vinculo=TipoVinculoEvidencia.DESTAQUE, referencia="")

        evidencia = self._unica()
        self.assertEqual(evidencia.tipo_vinculo, TipoVinculoEvidencia.DESTAQUE)
        self.assertEqual(evidencia.referencia, "")

    def test_jpeg_png_e_webp_entram(self):
        self._anexar(imagem=jpg(), legenda="Antes")
        self._anexar(
            imagem=webp(),
            tipo_vinculo=TipoVinculoEvidencia.CATEGORIA,
            referencia=CategoriaEithra.OBRAS_MELHORIAS,
            legenda="Depois",
        )
        self._anexar(
            imagem=png(),
            tipo_vinculo=TipoVinculoEvidencia.DESTAQUE,
            referencia="",
        )

        self.assertEqual(
            set(self._evidencias().values_list("mime_type", flat=True)),
            {"image/jpeg", "image/webp", "image/png"},
        )

    def test_lancamento_de_fora_do_mes_e_recusado(self):
        resposta = self._anexar(referencia="lancamento-inventado")

        self.assertContains(resposta, "não faz parte do balanço")
        self.assertFalse(self._evidencias().exists())

    def test_categoria_de_fora_do_catalogo_e_recusada(self):
        resposta = self._anexar(
            tipo_vinculo=TipoVinculoEvidencia.CATEGORIA,
            referencia="categoria-inventada",
        )

        self.assertContains(resposta, "não faz parte do balanço")
        self.assertFalse(self._evidencias().exists())

    def test_vinculo_fora_dos_tres_tipos_e_recusado(self):
        self._anexar(tipo_vinculo="solto")

        self.assertFalse(self._evidencias().exists())

    def test_a_foto_vai_para_o_bucket_privado(self):
        balanco = self._mes()

        self._anexar()

        evidencia = self._unica()
        self.assertEqual(evidencia.storage_provider, "mentira")
        self.assertEqual(evidencia.storage_bucket, "bucket-de-fotos")
        self.assertEqual(
            evidencia.storage_key,
            f"balancos/{self.condominio_a.pk}/{balanco.pk}/evidencias/"
            f"{evidencia.pk}.png",
        )
        self.assertIn(evidencia.storage_key, self.fotos.objetos)
        self.assertNotIn("http", evidencia.storage_key)

    def test_o_hash_da_foto_e_guardado(self):
        self._anexar(imagem=png())

        self.assertEqual(
            self._unica().sha256, hashlib.sha256(CONTEUDO_PNG).hexdigest()
        )

    def test_o_acesso_a_foto_e_gerado_na_hora(self):
        balanco = self._mes()
        evidencia = self._foto_anexada()

        # Anexar não gera acesso nenhum: o endereço só nasce quando alguém olha.
        self.assertEqual(self.fotos.assinaturas, [])

        resposta = self.client.get(
            reverse("balancos:evidencia_imagem", args=[balanco.pk, evidencia.pk])
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertIn(evidencia.storage_key, resposta["Location"])
        self.assertEqual(
            [chave for chave, _ in self.fotos.assinaturas], [evidencia.storage_key]
        )

        # Nada de URL assinada vira registro: o balanço guarda a referência do
        # objeto, e não um endereço que expira.
        balanco.refresh_from_db()
        self.assertNotIn("validade=", str(balanco.dados_validados))
        self.assertNotIn("http", evidencia.storage_key)

    def test_anexar_nao_muda_o_detalhamento_do_mes(self):
        balanco = self._mes()
        # O estado de partida é lido à parte: listar lançamentos para escolher um
        # lhes dá identidade, e isso não pode contaminar o "antes" comparado.
        antes = BalancoMensal.objects.get(pk=balanco.pk).dados_validados

        self._anexar()

        balanco.refresh_from_db()
        self.assertEqual(balanco.dados_validados, antes)

    def test_cada_vinculo_aceita_ate_tres_fotos(self):
        for numero in range(evidencias.LIMITE_DE_FOTOS_POR_VINCULO):
            self.assertContains(
                self._anexar(imagem=png(marca=f"-{numero}")), "Foto anexada."
            )

        resposta = self._anexar(imagem=png(marca="-quarta"))

        self.assertContains(resposta, "Cada vínculo aceita até 3 fotos")
        self.assertEqual(self._evidencias().count(), 3)

    def test_o_destaque_do_mes_aceita_uma_foto_so(self):
        self._anexar(tipo_vinculo=TipoVinculoEvidencia.DESTAQUE, referencia="")

        resposta = self._anexar(
            tipo_vinculo=TipoVinculoEvidencia.DESTAQUE,
            referencia="",
            imagem=png(marca="-outra"),
        )

        self.assertContains(resposta, "O destaque do mês já tem uma foto")
        self.assertEqual(self._evidencias().count(), 1)

    def test_a_legenda_curta_e_salva(self):
        self._anexar(legenda="Substituição do equipamento da área comum.")

        self.assertEqual(
            self._unica().legenda, "Substituição do equipamento da área comum."
        )

    def test_legenda_longa_demais_e_recusada(self):
        self._anexar(legenda="x" * (evidencias.LIMITE_DA_LEGENDA + 1))

        self.assertFalse(self._evidencias().exists())

    def test_por_padrao_a_foto_nao_vai_aos_moradores(self):
        self._anexar()

        self.assertFalse(self._unica().exibir_aos_moradores)

    def test_quem_responde_marca_a_foto_para_os_moradores(self):
        self._anexar(exibir_aos_moradores=True)

        self.assertTrue(self._unica().exibir_aos_moradores)

    def test_supervisora_nao_decide_exibir_aos_moradores(self):
        resposta = self._anexar(usuario=self.supervisora, exibir_aos_moradores=True)

        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(self._evidencias().exists())

    def test_supervisora_nao_muda_a_decisao_de_exibir(self):
        evidencia = self._foto_anexada()

        resposta = self._editar(
            evidencia, usuario=self.supervisora, exibir_aos_moradores="on"
        )

        self.assertEqual(resposta.status_code, 403)
        evidencia.refresh_from_db()
        self.assertFalse(evidencia.exibir_aos_moradores)

    def test_editar_muda_a_legenda_e_a_decisao(self):
        evidencia = self._foto_anexada(legenda="Antes")

        resposta = self._editar(evidencia, legenda="Depois", exibir_aos_moradores="on")

        self.assertContains(resposta, "Foto atualizada.")
        evidencia.refresh_from_db()
        self.assertEqual(evidencia.legenda, "Depois")
        self.assertTrue(evidencia.exibir_aos_moradores)
        # A imagem continua a mesma: trocar foto é anexar outra e remover esta.
        self.assertEqual(len(self.fotos.objetos), 1)

    def test_remover_tira_a_foto_do_mes_sem_apagar_o_registro(self):
        evidencia = self._foto_anexada()
        chave = evidencia.storage_key

        resposta = self._remover(evidencia)

        self.assertContains(resposta, "Foto removida.")
        evidencia.refresh_from_db()
        self.assertFalse(evidencia.ativo)
        self.assertIsNotNone(evidencia.removido_em)
        self.assertEqual(self.fotos.removidos, [chave])
        self.assertNotIn(chave, self.fotos.objetos)

    def test_remover_exige_quem_responde_pelo_balanco(self):
        evidencia = self._foto_anexada()

        resposta = self._remover(evidencia, usuario=self.supervisora)

        self.assertEqual(resposta.status_code, 403)
        evidencia.refresh_from_db()
        self.assertTrue(evidencia.ativo)

    def test_remover_duas_vezes_nao_encontra_a_foto(self):
        evidencia = self._foto_anexada()
        self._remover(evidencia)

        resposta = self._remover(evidencia)

        self.assertContains(resposta, "Esta foto já não está no balanço.")

    def test_a_mesma_foto_nao_entra_duas_vezes_no_mesmo_vinculo(self):
        self._anexar(imagem=png())

        resposta = self._anexar(imagem=png())

        self.assertContains(resposta, "Esta foto já está anexada aqui.")
        self.assertEqual(self._evidencias().count(), 1)

    def test_a_mesma_foto_pode_explicar_dois_contextos(self):
        self._anexar(imagem=png())
        self._anexar(
            imagem=png(),
            tipo_vinculo=TipoVinculoEvidencia.CATEGORIA,
            referencia=CategoriaEithra.OBRAS_MELHORIAS,
        )

        self.assertEqual(self._evidencias().count(), 2)


class EvidenciaVisualNaTelaTests(BaseEvidenciaTests):
    """O que a tela do mês mostra sobre as fotos — e o que ela não mostra."""

    def test_a_tela_oferece_anexar_foto(self):
        resposta = self._abrir()

        self.assertContains(resposta, 'id="adicionar-evidencia"', html=False)
        self.assertContains(resposta, "Adicionar foto")
        self.assertContains(resposta, "Destaque do mês")
        self.assertContains(
            resposta, "Confirme que a imagem não contém dados pessoais"
        )

    def test_a_foto_do_lancamento_aparece_como_miniatura(self):
        balanco = self._mes()
        evidencia = self._foto_anexada(legenda="Troca da bomba")

        resposta = self._abrir()

        self.assertContains(resposta, "eithra-thumb")
        self.assertContains(
            resposta,
            reverse("balancos:evidencia_imagem", args=[balanco.pk, evidencia.pk]),
        )
        self.assertContains(resposta, "Troca da bomba")

    def test_a_miniatura_nao_expoe_o_endereco_do_objeto(self):
        evidencia = self._foto_anexada()

        resposta = self._abrir()

        self.assertNotContains(resposta, evidencia.storage_key)
        self.assertNotContains(resposta, "validade=")

    def test_supervisora_ve_a_foto_sem_poder_mexer_nela(self):
        self._foto_anexada()

        resposta = self._abrir(usuario=self.supervisora)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "eithra-thumb")
        self.assertNotContains(resposta, 'id="adicionar-evidencia"', html=False)
        self.assertNotContains(resposta, "data-evidencia-editar-url")

    def test_morador_continua_sem_acesso_ao_mes(self):
        self._foto_anexada()

        self.assertEqual(self._abrir(usuario=self.morador).status_code, 404)

    def test_a_foto_do_destaque_aparece_com_a_decisao_tomada(self):
        self._foto_anexada(
            tipo_vinculo=TipoVinculoEvidencia.DESTAQUE,
            referencia="",
            legenda="Pintura da fachada",
            exibir_aos_moradores=True,
        )

        resposta = self._abrir()

        self.assertContains(resposta, "Destaque do mês")
        self.assertContains(resposta, "Pintura da fachada")
        self.assertContains(resposta, "Elegível para os moradores")

    def test_a_foto_removida_sai_da_tela(self):
        evidencia = self._foto_anexada()
        self._remover(evidencia)

        resposta = self._abrir()

        self.assertNotContains(resposta, "eithra-thumb")
        self.assertNotContains(resposta, 'id="ver-evidencia"', html=False)

