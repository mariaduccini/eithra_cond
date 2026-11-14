"""Telas de planos preventivos: cadastro e edição pela gestão.

A tela pergunta o que fazer, onde, com quem presta, de quanto em quanto tempo e
quando volta. Duas decisões não são perguntas: quem acompanha é a Administradora
do condomínio — resolvida pelo vínculo ativo de Administradora — e a conferência
depois da execução é sempre exigida.

Administração do sistema, Supervisão e Síndico não substituem a Administradora:
não há fallback. Esta suíte prova isso, confere que o cadastro grava as duas
decisões, que ninguém as escolhe por engano, que editar o plano relê a
Administradora vigente, que o que já nasceu do plano não é reescrito e que o
isolamento por condomínio continua de pé.
"""
from datetime import date

from django.test import TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.condominios.models import AcessoUsuarioCondominio
from apps.manutencoes.constants import FrequenciaUnidade
from apps.manutencoes.models import PlanoPreventivo
from apps.manutencoes.services import gerar_ocorrencia_preventiva
from apps.manutencoes.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)


class BaseDePlanos(TestCase):
    """Um condomínio com a Administradora e uma pessoa de gestão que o opera.

    A Administradora é o único vínculo de Administradora — é ela que o domínio
    grava como responsável. A gestão responde pelo condomínio e usa a tela. A
    administração do sistema, a Supervisão e o Síndico estão presentes de
    propósito: são as iscas de quem tentaria virar responsável sem poder.
    """

    def setUp(self):
        self.condominio = criar_condominio("Alpha")

        self.administradora = criar_pessoa(
            "administradora@exemplo.test", "Administradora"
        )
        criar_vinculo(self.administradora, self.condominio, papel=Papel.ADMINISTRADORA)

        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)

        self.supervisora = criar_pessoa("supervisao@exemplo.test", "Supervisão")
        criar_vinculo(self.supervisora, self.condominio, papel=Papel.SUPERVISORA)

        # Quem administra o Eithra: global, e não papel de condomínio. Como toda
        # pessoa do condomínio, tem um vínculo — e ele não é o de Administradora.
        self.admin_do_sistema = criar_pessoa(
            "admin@exemplo.test", "Administração do sistema"
        )
        self.admin_do_sistema.is_staff = True
        self.admin_do_sistema.is_superuser = True
        self.admin_do_sistema.save(update_fields=["is_staff", "is_superuser"])
        criar_vinculo(
            self.admin_do_sistema, self.condominio, papel=Papel.SUPERVISORA
        )

        self.sindico = criar_pessoa("sindico@exemplo.test", "Síndico")
        criar_vinculo(self.sindico, self.condominio, papel=Papel.SINDICA)

        self.client.force_login(self.gestao)

    def _dados(self, **campos):
        """O que a tela enviaria, com o condomínio em vista."""
        base = {
            "condominio": str(self.condominio.pk),
            "titulo": "Revisão anual do reservatório",
            "frequencia": "todo_mes",
            "proxima_data_prevista": "2026-12-01",
        }
        base.update(campos)
        return base

    def _plano(self, **campos):
        """Cadastra um plano pela tela e devolve o que ficou gravado."""
        resposta = self.client.post(
            reverse("manutencoes:criar_plano"), self._dados(**campos)
        )
        self.assertEqual(resposta.status_code, 302)
        return PlanoPreventivo.objects.get()

    def _retirar_administradora(self):
        """Deixa o condomínio sem Administradora, mantendo as iscas no lugar."""
        vinculo = AcessoUsuarioCondominio.objects.get(
            usuario=self.administradora, condominio=self.condominio
        )
        vinculo.ativo = False
        vinculo.save(update_fields=["ativo"])


class CadastroTests(BaseDePlanos):
    """O plano novo já nasce com as duas decisões que ninguém escolhe."""

    def test_novo_plano_recebe_a_administradora(self):
        plano = self._plano()
        self.assertEqual(plano.responsavel, self.administradora)

    def test_novo_plano_exige_conferencia(self):
        plano = self._plano()
        self.assertTrue(plano.exige_conferencia)

    def test_admin_do_sistema_nunca_e_escolhido(self):
        """Quem administra o Eithra não é a Administradora da preventiva."""
        plano = self._plano(responsavel=str(self.admin_do_sistema.pk))
        self.assertNotEqual(plano.responsavel, self.admin_do_sistema)
        self.assertEqual(plano.responsavel, self.administradora)

    def test_supervisao_nunca_e_escolhida(self):
        plano = self._plano(responsavel=str(self.supervisora.pk))
        self.assertNotEqual(plano.responsavel, self.supervisora)
        self.assertEqual(plano.responsavel, self.administradora)

    def test_sindico_nunca_e_escolhido(self):
        plano = self._plano(responsavel=str(self.sindico.pk))
        self.assertNotEqual(plano.responsavel, self.sindico)
        self.assertEqual(plano.responsavel, self.administradora)

    def test_responsavel_forjado_pelo_navegador_e_ignorado(self):
        """A tela não oferece a escolha; forçá-la pelo POST não vale."""
        plano = self._plano(
            responsavel=str(self.sindico.pk), exige_conferencia="false"
        )
        self.assertEqual(plano.responsavel, self.administradora)
        self.assertTrue(plano.exige_conferencia)

    def test_sem_administradora_recusa_mesmo_com_as_outras_iscas(self):
        """Sem Administradora, nada é gravado — e as iscas não servem de resposta."""
        self._retirar_administradora()

        resposta = self.client.post(
            reverse("manutencoes:criar_plano"), self._dados()
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(PlanoPreventivo.objects.count(), 0)
        self.assertContains(
            resposta,
            "Este condomínio ainda não possui uma administradora definida.",
            status_code=400,
        )

    def test_duas_administradoras_recusam_o_cadastro(self):
        segunda = criar_pessoa("administradora-2@exemplo.test", "Administradora 2")
        criar_vinculo(segunda, self.condominio, papel=Papel.ADMINISTRADORA)

        resposta = self.client.post(
            reverse("manutencoes:criar_plano"), self._dados()
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(PlanoPreventivo.objects.count(), 0)
        self.assertContains(
            resposta, "Há mais de uma administradora ativa", status_code=400
        )

    def test_frequencia_personalizada_e_guardada_nos_mesmos_campos(self):
        plano = self._plano(
            frequencia="personalizar",
            frequencia_intervalo_personalizado=3,
            frequencia_unidade_personalizada=FrequenciaUnidade.MESES,
        )
        self.assertEqual(plano.frequencia_unidade, FrequenciaUnidade.MESES)
        self.assertEqual(plano.frequencia_intervalo, 3)

    def test_titulo_e_data_obrigatorios(self):
        resposta = self.client.post(
            reverse("manutencoes:criar_plano"),
            {"condominio": str(self.condominio.pk), "frequencia": "todo_mes"},
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(PlanoPreventivo.objects.count(), 0)

    def test_tela_mostra_quem_acompanha_e_a_conferencia(self):
        resposta = self.client.get(reverse("manutencoes:planos"))
        self.assertContains(resposta, "Quem acompanha")
        self.assertContains(resposta, "Administradora")
        self.assertContains(
            resposta, "A administradora deverá conferir o serviço após a execução."
        )


class EdicaoTests(BaseDePlanos):
    """Editar a regra não muda quem acompanha nem desliga a conferência."""

    def test_edicao_mantem_a_administradora_e_a_conferencia(self):
        plano = self._plano()
        resposta = self.client.post(
            reverse("manutencoes:editar_plano", args=[plano.pk]),
            self._dados(
                titulo="Revisão semestral",
                frequencia="a_cada_2_meses",
                responsavel=str(self.sindico.pk),
                exige_conferencia="false",
            ),
        )
        self.assertEqual(resposta.status_code, 302)

        plano.refresh_from_db()
        self.assertEqual(plano.titulo, "Revisão semestral")
        self.assertEqual(plano.responsavel, self.administradora)
        self.assertTrue(plano.exige_conferencia)
        self.assertEqual(plano.frequencia_unidade, FrequenciaUnidade.MESES)
        self.assertEqual(plano.frequencia_intervalo, 2)

    def test_edicao_usa_a_administradora_vigente(self):
        """A responsável segue o vínculo de Administradora, e não um nome fixo."""
        plano = self._plano()
        self.assertEqual(plano.responsavel, self.administradora)

        self._retirar_administradora()
        nova = criar_pessoa("administradora-nova@exemplo.test", "Administradora nova")
        criar_vinculo(nova, self.condominio, papel=Papel.ADMINISTRADORA)

        resposta = self.client.post(
            reverse("manutencoes:editar_plano", args=[plano.pk]),
            self._dados(titulo="Revisão anual do reservatório"),
        )
        self.assertEqual(resposta.status_code, 302)

        plano.refresh_from_db()
        self.assertEqual(plano.responsavel, nova)
        self.assertNotEqual(plano.responsavel, self.administradora)

    def test_edicao_preserva_ocorrencias_anteriores(self):
        plano = self._plano()
        ocorrencia = gerar_ocorrencia_preventiva(
            plano=plano, usuario=self.gestao
        )
        retrato = (
            ocorrencia.titulo,
            ocorrencia.competencia_preventiva,
            ocorrencia.exige_conferencia,
            ocorrencia.responsavel_id,
        )

        resposta = self.client.post(
            reverse("manutencoes:editar_plano", args=[plano.pk]),
            self._dados(titulo="Outro escopo, decidido depois", frequencia="todo_ano"),
        )
        self.assertEqual(resposta.status_code, 302)

        ocorrencia.refresh_from_db()
        self.assertEqual(
            (
                ocorrencia.titulo,
                ocorrencia.competencia_preventiva,
                ocorrencia.exige_conferencia,
                ocorrencia.responsavel_id,
            ),
            retrato,
        )
        self.assertEqual(ocorrencia.titulo, "Revisão anual do reservatório")
        self.assertEqual(ocorrencia.competencia_preventiva, date(2026, 12, 1))


class IsolamentoTests(BaseDePlanos):
    """Nada de outro condomínio atravessa a tela."""

    def setUp(self):
        super().setUp()
        self.outro = criar_condominio("Beta")
        administradora_beta = criar_pessoa(
            "administradora-beta@exemplo.test", "Administradora do Beta"
        )
        criar_vinculo(administradora_beta, self.outro, papel=Papel.ADMINISTRADORA)
        self.plano_do_outro = PlanoPreventivo.objects.create(
            condominio=self.outro,
            titulo="Plano do Beta",
            criado_por=administradora_beta,
            responsavel=administradora_beta,
            frequencia_unidade=FrequenciaUnidade.MESES,
            frequencia_intervalo=1,
            proxima_data_prevista=date(2026, 12, 1),
        )

    def test_plano_de_outro_condominio_nao_aparece_na_lista(self):
        resposta = self.client.get(reverse("manutencoes:planos"))
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["total"], 0)

    def test_editar_plano_de_outro_condominio_e_404(self):
        resposta = self.client.post(
            reverse("manutencoes:editar_plano", args=[self.plano_do_outro.pk]),
            self._dados(titulo="Tentativa"),
        )
        self.assertEqual(resposta.status_code, 404)
        self.plano_do_outro.refresh_from_db()
        self.assertEqual(self.plano_do_outro.titulo, "Plano do Beta")

    def test_cadastrar_em_outro_condominio_e_recusado(self):
        resposta = self.client.post(
            reverse("manutencoes:criar_plano"),
            self._dados(condominio=str(self.outro.pk), titulo="Forjado"),
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(
            PlanoPreventivo.objects.filter(
                condominio=self.outro, titulo="Forjado"
            ).exists()
        )


class PermissaoTests(BaseDePlanos):
    """Quem não enxerga o módulo não descobre nem cadastra plano."""

    def test_morador_nao_ve_nem_cadastra(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)

        self.assertEqual(
            self.client.get(reverse("manutencoes:planos")).status_code, 404
        )
        self.assertEqual(
            self.client.post(
                reverse("manutencoes:criar_plano"), self._dados()
            ).status_code,
            404,
        )
        self.assertEqual(PlanoPreventivo.objects.count(), 0)
