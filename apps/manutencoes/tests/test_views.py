"""Telas de manutenção: listagem, detalhe, ações e criação por demanda."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.condominios.constants import Papel
from apps.core.constants import Prioridade
from apps.manutencoes.constants import StatusManutencao, TipoManutencao
from apps.manutencoes.models import Manutencao
from apps.manutencoes.services import (
    alterar_status,
    criar_manutencao,
    definir_ativo,
)
from apps.manutencoes.tests.apoio import (
    criar_condominio,
    criar_demanda,
    criar_equipamento,
    criar_fornecedor,
    criar_local,
    criar_pessoa,
    criar_vinculo,
)

SENHA = "senha-forte-123"


class BaseDeTelas(TestCase):
    """Base com uma pessoa de gestão ligada a um condomínio."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao@exemplo.test", "Gestão")
        criar_vinculo(self.gestao, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestao)

    def _manutencao(self, **campos):
        # A manutenção nasce "Aberta"; quando o cenário pede outra situação, ela
        # é movida pelo mesmo caminho que a gestão usaria — nada escrito à mão.
        status = campos.pop("status", None)
        ativo = campos.pop("ativo", True)
        base = {
            "condominio": self.condominio,
            "criado_por": self.gestao,
            "titulo": "Manutenção de teste",
        }
        base.update(campos)
        manutencao = criar_manutencao(**base)
        if status and status != StatusManutencao.ABERTA:
            alterar_status(
                manutencao=manutencao, usuario=self.gestao, novo_status=status
            )
        if not ativo:
            definir_ativo(manutencao=manutencao, ativo=False)
        return manutencao


class AcessoAsTelasTests(BaseDeTelas):
    """Sem vínculo que enxergue o módulo, a resposta é 404."""

    def test_listagem_exige_login(self):
        self.client.logout()
        resposta = self.client.get(reverse("manutencoes:listagem"))
        self.assertEqual(resposta.status_code, 302)

    def test_morador_recebe_404_na_listagem(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)
        self.assertEqual(
            self.client.get(reverse("manutencoes:listagem")).status_code, 404
        )

    def test_porteiro_recebe_404_na_listagem(self):
        porteiro = criar_pessoa("porteiro@exemplo.test", "Porteiro")
        criar_vinculo(porteiro, self.condominio, papel=Papel.PORTEIRO)
        self.client.force_login(porteiro)
        self.assertEqual(
            self.client.get(reverse("manutencoes:listagem")).status_code, 404
        )

    def test_detalhe_de_outro_condominio_e_404(self):
        outro = criar_condominio("Beta")
        alheia = Manutencao.objects.create(
            condominio=outro, titulo="Alheia", criado_por=self.gestao
        )
        resposta = self.client.get(reverse("manutencoes:detalhe", args=[alheia.pk]))
        self.assertEqual(resposta.status_code, 404)

    def test_detalhe_autorizado_abre(self):
        manutencao = self._manutencao()
        resposta = self.client.get(reverse("manutencoes:detalhe", args=[manutencao.pk]))
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["manutencao"]["pk"], manutencao.pk)


class CriacaoTests(BaseDeTelas):
    """Registrar manutenção pela tela."""

    def test_criar_redireciona_para_o_detalhe(self):
        resposta = self.client.post(
            reverse("manutencoes:criar"),
            {
                "condominio": str(self.condominio.pk),
                "titulo": "Troca da bomba do reservatório",
                "tipo": TipoManutencao.CORRETIVA,
            },
        )
        self.assertEqual(resposta.status_code, 302)
        manutencao = Manutencao.objects.get()
        self.assertEqual(manutencao.titulo, "Troca da bomba do reservatório")
        self.assertEqual(
            resposta.url, reverse("manutencoes:detalhe", args=[manutencao.pk])
        )

    def test_titulo_obrigatorio(self):
        resposta = self.client.post(
            reverse("manutencoes:criar"),
            {
                "condominio": str(self.condominio.pk),
                "titulo": "",
                "tipo": TipoManutencao.CORRETIVA,
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(Manutencao.objects.count(), 0)
        self.assertEqual(resposta.context["modal_aberto"], "criar")

    def test_morador_nao_cria(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        self.client.force_login(morador)
        resposta = self.client.post(
            reverse("manutencoes:criar"),
            {"condominio": str(self.condominio.pk), "titulo": "Tentativa"},
        )
        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(Manutencao.objects.count(), 0)

    def test_get_nao_cria(self):
        resposta = self.client.get(reverse("manutencoes:criar"))
        self.assertEqual(resposta.status_code, 302)

    def test_fornecedor_de_outro_condominio_e_recusado(self):
        outro = criar_condominio("Beta")
        alheio = criar_fornecedor(outro, "Fornecedor do Beta")
        resposta = self.client.post(
            reverse("manutencoes:criar"),
            {
                "condominio": str(self.condominio.pk),
                "titulo": "Com fornecedor de fora",
                "tipo": TipoManutencao.CORRETIVA,
                "fornecedor": str(alheio.pk),
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("fornecedor", resposta.context["formulario_manutencao"].errors)

    def test_equipamento_com_local_preenche_o_local(self):
        local = criar_local(self.condominio, "Casa de máquinas")
        equipamento = criar_equipamento(self.condominio, "Bomba", local=local)
        self.client.post(
            reverse("manutencoes:criar"),
            {
                "condominio": str(self.condominio.pk),
                "titulo": "Revisar bomba",
                "tipo": TipoManutencao.CORRETIVA,
                "equipamento": str(equipamento.pk),
            },
        )
        self.assertEqual(Manutencao.objects.get().local, local)

    def test_local_divergente_do_equipamento_e_recusado(self):
        local = criar_local(self.condominio, "Casa de máquinas")
        outro_local = criar_local(self.condominio, "Telhado")
        equipamento = criar_equipamento(self.condominio, "Bomba", local=local)
        resposta = self.client.post(
            reverse("manutencoes:criar"),
            {
                "condominio": str(self.condominio.pk),
                "titulo": "Revisar bomba",
                "tipo": TipoManutencao.CORRETIVA,
                "equipamento": str(equipamento.pk),
                "local": str(outro_local.pk),
            },
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("local", resposta.context["formulario_manutencao"].errors)


class CriacaoPorDemandaTests(BaseDeTelas):
    """Criar manutenção a partir da demanda: sugestão, nunca gravação automática."""

    def setUp(self):
        super().setUp()
        self.demanda = criar_demanda(
            self.condominio,
            self.gestao,
            titulo="Vazamento no subsolo",
            descricao="Água acumulando perto do quadro elétrico.",
            prioridade=Prioridade.ALTA,
            prazo=timezone.localdate() + timedelta(days=5),
        )

    def test_abrir_a_partir_da_demanda_preenche_o_formulario(self):
        resposta = self.client.get(
            reverse("manutencoes:listagem"), {"demanda": str(self.demanda.pk)}
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context["modal_aberto"], "criar")
        self.assertEqual(resposta.context["demanda_origem"], self.demanda)
        # Nada é gravado ao abrir: o formulário só chega preenchido.
        self.assertEqual(Manutencao.objects.count(), 0)

    def test_demanda_com_lugar_e_equipamento_preenche_a_manutencao(self):
        local = criar_local(self.condominio, "Casa de máquinas")
        equipamento = criar_equipamento(self.condominio, "Bomba", local=local)
        demanda = criar_demanda(
            self.condominio, self.gestao, local=local, equipamento=equipamento
        )

        resposta = self.client.get(
            reverse("manutencoes:listagem"), {"demanda": str(demanda.pk)}
        )
        formulario = resposta.context["formulario_manutencao"]
        # A manutenção nasce sabendo onde o problema está: o lugar e o que fica
        # nele vêm junto, para ninguém refazer o trabalho que o registro fez.
        self.assertEqual(formulario.initial["local"], local.pk)
        self.assertEqual(formulario.initial["equipamento"], equipamento.pk)

    def test_confirmar_grava_com_a_procedencia(self):
        resposta = self.client.post(
            reverse("manutencoes:criar"),
            {
                "condominio": str(self.condominio.pk),
                "titulo": self.demanda.titulo,
                "descricao_problema": self.demanda.descricao,
                "tipo": TipoManutencao.CORRETIVA,
                "demanda_origem": str(self.demanda.pk),
            },
        )
        self.assertEqual(resposta.status_code, 302)
        manutencao = Manutencao.objects.get()
        self.assertEqual(manutencao.demanda_origem, self.demanda)

        # A demanda permanece como estava.
        self.demanda.refresh_from_db()
        self.assertEqual(self.demanda.prioridade, Prioridade.ALTA)


class ListagemEFiltrosTests(BaseDeTelas):
    """Filtros essenciais e "minhas manutenções"."""

    def test_lista_traz_as_manutencoes_do_condominio(self):
        self._manutencao(titulo="Uma manutenção")
        resposta = self.client.get(reverse("manutencoes:listagem"))
        self.assertEqual(resposta.status_code, 200)
        titulos = [linha["titulo"] for linha in resposta.context["manutencoes"]]
        self.assertIn("Uma manutenção", titulos)

    def test_filtro_por_status(self):
        self._manutencao(titulo="Aberta")
        self._manutencao(titulo="Em andamento", status=StatusManutencao.EM_ANDAMENTO)

        resposta = self.client.get(
            reverse("manutencoes:listagem"), {"status": StatusManutencao.EM_ANDAMENTO}
        )
        titulos = [linha["titulo"] for linha in resposta.context["manutencoes"]]
        self.assertEqual(titulos, ["Em andamento"])

    def test_filtro_por_tipo_e_prioridade(self):
        self._manutencao(titulo="Corretiva", prioridade=Prioridade.NORMAL)
        self._manutencao(
            titulo="Preventiva urgente",
            tipo=TipoManutencao.PREVENTIVA,
            prioridade=Prioridade.URGENTE,
        )
        resposta = self.client.get(
            reverse("manutencoes:listagem"),
            {"tipo": TipoManutencao.PREVENTIVA, "prioridade": Prioridade.URGENTE},
        )
        titulos = [linha["titulo"] for linha in resposta.context["manutencoes"]]
        self.assertEqual(titulos, ["Preventiva urgente"])

    def test_manutencao_desativada_nao_aparece_na_lista(self):
        self._manutencao(titulo="Fora da operação", ativo=False)
        resposta = self.client.get(reverse("manutencoes:listagem"))
        titulos = [linha["titulo"] for linha in resposta.context["manutencoes"]]
        self.assertNotIn("Fora da operação", titulos)

    def test_minhas_manutencoes(self):
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        self._manutencao(titulo="Minha", responsavel=operacional)
        self._manutencao(titulo="De outro")

        self.client.force_login(operacional)
        resposta = self.client.get(reverse("manutencoes:listagem"), {"minhas": "on"})
        titulos = [linha["titulo"] for linha in resposta.context["manutencoes"]]
        self.assertEqual(titulos, ["Minha"])

    def test_filtro_por_local_e_equipamento(self):
        casa = criar_local(self.condominio, "Casa de máquinas")
        bomba = criar_equipamento(self.condominio, "Bomba de recalque", local=casa)
        self._manutencao(titulo="No local", local=casa, equipamento=bomba)
        self._manutencao(titulo="Sem local")

        por_local = self.client.get(
            reverse("manutencoes:listagem"), {"local": str(casa.pk)}
        )
        self.assertEqual(
            [linha["titulo"] for linha in por_local.context["manutencoes"]],
            ["No local"],
        )

        por_equipamento = self.client.get(
            reverse("manutencoes:listagem"), {"equipamento": str(bomba.pk)}
        )
        self.assertEqual(
            [linha["titulo"] for linha in por_equipamento.context["manutencoes"]],
            ["No local"],
        )

    def test_filtro_por_fornecedor(self):
        fornecedor = criar_fornecedor(self.condominio, "Elevadores Alfa")
        self._manutencao(titulo="Terceirizada", fornecedor=fornecedor)
        self._manutencao(titulo="Própria")

        resposta = self.client.get(
            reverse("manutencoes:listagem"), {"fornecedor": str(fornecedor.pk)}
        )
        titulos = [linha["titulo"] for linha in resposta.context["manutencoes"]]
        self.assertEqual(titulos, ["Terceirizada"])

    def test_busca_alcanca_local_e_equipamento(self):
        casa = criar_local(self.condominio, "Subsolo técnico")
        bomba = criar_equipamento(self.condominio, "Bomba centrífuga", local=casa)
        self._manutencao(titulo="Sem pistas", local=casa, equipamento=bomba)
        self._manutencao(titulo="Outra coisa")

        por_local = self.client.get(
            reverse("manutencoes:listagem"), {"busca": "Subsolo"}
        )
        self.assertEqual(
            [linha["titulo"] for linha in por_local.context["manutencoes"]],
            ["Sem pistas"],
        )

        por_equipamento = self.client.get(
            reverse("manutencoes:listagem"), {"busca": "centrífuga"}
        )
        self.assertEqual(
            [linha["titulo"] for linha in por_equipamento.context["manutencoes"]],
            ["Sem pistas"],
        )


class AcoesTests(BaseDeTelas):
    """Editar, mover situação, concluir e cancelar pela tela."""

    def test_editar_atualiza_o_conteudo(self):
        manutencao = self._manutencao(titulo="Original")
        resposta = self.client.post(
            reverse("manutencoes:editar", args=[manutencao.pk]),
            {
                "condominio": str(self.condominio.pk),
                "titulo": "Atualizada",
                "tipo": TipoManutencao.CORRETIVA,
            },
        )
        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.titulo, "Atualizada")
        # O peso não vem do navegador: segue o que o problema relatado sustenta.
        self.assertEqual(manutencao.prioridade, Prioridade.NORMAL)

    def test_mudar_status(self):
        manutencao = self._manutencao()
        resposta = self.client.post(
            reverse("manutencoes:mudar_status", args=[manutencao.pk]),
            {"acao": StatusManutencao.EM_ANDAMENTO},
        )
        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.EM_ANDAMENTO)
        self.assertIsNotNone(manutencao.data_inicio)

    def test_mudar_status_exige_post(self):
        manutencao = self._manutencao()
        resposta = self.client.get(
            reverse("manutencoes:mudar_status", args=[manutencao.pk])
        )
        self.assertEqual(resposta.status_code, 405)

    def test_concluir_exige_descricao_e_registra_quando(self):
        manutencao = self._manutencao()
        resposta = self.client.post(
            reverse("manutencoes:concluir", args=[manutencao.pk]),
            {"descricao_servico": "Bomba substituída."},
        )
        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.CONCLUIDA)
        self.assertIsNotNone(manutencao.data_conclusao)

    def test_concluir_sem_descricao_e_recusado(self):
        manutencao = self._manutencao()
        resposta = self.client.post(
            reverse("manutencoes:concluir", args=[manutencao.pk]),
            {"descricao_servico": ""},
        )
        self.assertEqual(resposta.status_code, 400)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)

    def test_supervisora_nao_cancela(self):
        supervisora = criar_pessoa("sup@exemplo.test", "Supervisora")
        criar_vinculo(supervisora, self.condominio, papel=Papel.SUPERVISORA)
        manutencao = self._manutencao()
        self.client.force_login(supervisora)
        resposta = self.client.post(
            reverse("manutencoes:cancelar", args=[manutencao.pk]), {"motivo": "Não"}
        )
        self.assertEqual(resposta.status_code, 403)
        manutencao.refresh_from_db()
        self.assertNotEqual(manutencao.status, StatusManutencao.CANCELADA)

    def test_gestao_cancela(self):
        manutencao = self._manutencao()
        resposta = self.client.post(
            reverse("manutencoes:cancelar", args=[manutencao.pk]),
            {"motivo": "Serviço deixou de ser necessário"},
        )
        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.CANCELADA)

    def test_operacional_sem_responsabilidade_nao_muda_status(self):
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        manutencao = self._manutencao()
        self.client.force_login(operacional)
        resposta = self.client.post(
            reverse("manutencoes:mudar_status", args=[manutencao.pk]),
            {"acao": StatusManutencao.EM_ANDAMENTO},
        )
        self.assertEqual(resposta.status_code, 403)

    def test_operacional_responsavel_conclui(self):
        operacional = criar_pessoa("op@exemplo.test", "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        manutencao = self._manutencao(responsavel=operacional)
        self.client.force_login(operacional)
        resposta = self.client.post(
            reverse("manutencoes:concluir", args=[manutencao.pk]),
            {"descricao_servico": "Serviço feito pelo operacional."},
        )
        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.CONCLUIDA)

    def test_alternar_ativo_retira_da_operacao(self):
        manutencao = self._manutencao()
        resposta = self.client.post(
            reverse("manutencoes:alternar_ativo", args=[manutencao.pk])
        )
        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        self.assertFalse(manutencao.ativo)

    def test_concluir_com_data_retroativa_pela_tela(self):
        manutencao = self._manutencao()
        servico_em = timezone.localdate() - timedelta(days=37)
        resposta = self.client.post(
            reverse("manutencoes:concluir", args=[manutencao.pk]),
            {
                "descricao_servico": "Bomba substituída.",
                "data_conclusao": servico_em.strftime("%Y-%m-%d"),
            },
        )
        self.assertEqual(resposta.status_code, 302)
        manutencao.refresh_from_db()
        self.assertEqual(
            timezone.localtime(manutencao.data_conclusao).date(), servico_em
        )

    def test_concluir_com_data_futura_e_recusado(self):
        manutencao = self._manutencao()
        amanha = timezone.localdate() + timedelta(days=1)
        resposta = self.client.post(
            reverse("manutencoes:concluir", args=[manutencao.pk]),
            {
                "descricao_servico": "Feito.",
                "data_conclusao": amanha.strftime("%Y-%m-%d"),
            },
        )
        self.assertEqual(resposta.status_code, 400)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)

    def test_concluir_com_data_invalida_e_recusado(self):
        manutencao = self._manutencao()
        resposta = self.client.post(
            reverse("manutencoes:concluir", args=[manutencao.pk]),
            {"descricao_servico": "Feito.", "data_conclusao": "31/02/2026"},
        )
        self.assertEqual(resposta.status_code, 400)
        manutencao.refresh_from_db()
        self.assertEqual(manutencao.status, StatusManutencao.ABERTA)

    def test_concluir_de_outro_condominio_e_404(self):
        outro = criar_condominio("Beta")
        alheia = Manutencao.objects.create(
            condominio=outro, titulo="Alheia", criado_por=self.gestao
        )
        resposta = self.client.post(
            reverse("manutencoes:concluir", args=[alheia.pk]),
            {"descricao_servico": "Feito.", "data_conclusao": ""},
        )
        self.assertEqual(resposta.status_code, 404)

    def test_morador_nao_conclui(self):
        morador = criar_pessoa("morador@exemplo.test", "Morador")
        criar_vinculo(morador, self.condominio, papel=Papel.MORADOR)
        manutencao = self._manutencao()
        self.client.force_login(morador)
        resposta = self.client.post(
            reverse("manutencoes:concluir", args=[manutencao.pk]),
            {"descricao_servico": "Feito.", "data_conclusao": ""},
        )
        self.assertEqual(resposta.status_code, 404)


class VozNaExecucaoTests(BaseDeTelas):
    """O microfone aparece também para quem executa, não só para quem cria.

    Ditar é escrever: o microfone segue a permissão de escrever o texto livre
    desta manutenção. Quem a registra no condomínio já o via; quem executa o
    serviço sob sua responsabilidade passa a vê-lo também. Quem não pode nada
    com ela continua sem microfone, mesmo com o serviço de voz configurado.
    """

    def _operacional(self, email="op@exemplo.test"):
        operacional = criar_pessoa(email, "Operacional")
        criar_vinculo(operacional, self.condominio, papel=Papel.OPERACIONAL)
        return operacional

    def _detalhe(self, manutencao):
        return self.client.get(reverse("manutencoes:detalhe", args=[manutencao.pk]))

    @patch("apps.manutencoes.views.audio.disponivel", return_value=True)
    def test_operacional_responsavel_ve_o_microfone(self, _disponivel):
        operacional = self._operacional()
        manutencao = self._manutencao(responsavel=operacional)
        self.client.force_login(operacional)

        resposta = self._detalhe(manutencao)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "data-eithra-ditar")
        self.assertContains(resposta, "js/eithra-ditar.js")

    @patch("apps.manutencoes.views.audio.disponivel", return_value=True)
    def test_operacional_sem_responsabilidade_nao_ve_o_microfone(self, _disponivel):
        operacional = self._operacional()
        manutencao = self._manutencao()
        self.client.force_login(operacional)

        resposta = self._detalhe(manutencao)

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, "js/eithra-ditar.js")

    @patch("apps.manutencoes.views.audio.disponivel", return_value=True)
    def test_quem_cria_continua_vendo_o_microfone(self, _disponivel):
        manutencao = self._manutencao()

        resposta = self._detalhe(manutencao)

        self.assertContains(resposta, "js/eithra-ditar.js")

    @patch("apps.manutencoes.views.audio.disponivel", return_value=False)
    def test_sem_servico_de_voz_nao_ha_microfone(self, _disponivel):
        operacional = self._operacional()
        manutencao = self._manutencao(responsavel=operacional)
        self.client.force_login(operacional)

        resposta = self._detalhe(manutencao)

        self.assertEqual(resposta.status_code, 200)
        self.assertNotContains(resposta, "js/eithra-ditar.js")
