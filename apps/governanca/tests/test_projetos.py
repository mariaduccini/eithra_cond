"""Projetos: o esforço que o condomínio toca, suas etapas e as demandas que o movem.

O projeto é cadastro simples — nome, situação, prazo previsto —, sem dinheiro
dentro. A hierarquia projeto → etapa → entrega é a mesma tabela apontando para
si mesma, e o vínculo com as demandas é explícito, com a integridade garantida
pelo banco.
"""
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.cadastros.tests.apoio import (
    criar_condominio,
    criar_fornecedor,
    entrar_como,
)
from apps.condominios.constants import Papel
from apps.demandas.models import Demanda
from apps.governanca.constants import StatusProjeto, TipoItemProjeto
from apps.governanca.models import Projeto, ProjetoDemanda, ProjetoItem


class ProjetoTests(TestCase):
    """O cadastro do projeto e os limites do que ele aceita."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)

    def _criar(self, **campos):
        padrao = {"condominio": self.condominio, "nome": "Regularização do AVCB"}
        return Projeto.objects.create(criado_por=self.gestao, **{**padrao, **campos})

    def test_guarda_o_projeto_normalizado(self):
        projeto = self._criar(nome="  Regularização   do  AVCB  ")
        self.assertEqual(projeto.nome, "Regularização do AVCB")
        self.assertEqual(projeto.status, StatusProjeto.EM_ESTUDO)
        self.assertTrue(projeto.ativo)

    def test_recusa_projeto_sem_nome(self):
        projeto = self._criar(nome="   ")
        with self.assertRaises(ValidationError) as capturado:
            projeto.full_clean()
        self.assertIn("nome", capturado.exception.message_dict)

    def test_recusa_previsao_anterior_ao_inicio(self):
        projeto = self._criar(
            data_inicio=date(2026, 5, 1), data_prevista_fim=date(2026, 4, 1)
        )
        with self.assertRaises(ValidationError) as capturado:
            projeto.full_clean()
        self.assertIn("data_prevista_fim", capturado.exception.message_dict)


class ProjetoItemTests(TestCase):
    """Etapas e entregas: os níveis da hierarquia do projeto."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)
        self.projeto = Projeto.objects.create(
            condominio=self.condominio,
            nome="Regularização do AVCB",
            criado_por=self.gestao,
        )

    def test_monta_a_hierarquia_etapa_e_entrega(self):
        etapa = ProjetoItem.objects.create(
            projeto=self.projeto, tipo=TipoItemProjeto.ETAPA, nome="Projeto executivo"
        )
        entrega = ProjetoItem.objects.create(
            projeto=self.projeto,
            parent=etapa,
            tipo=TipoItemProjeto.ENTREGA,
            nome="Pranchas assinadas",
        )
        self.assertIsNone(etapa.parent)
        self.assertEqual(entrega.parent, etapa)
        self.assertEqual(list(etapa.filhos.all()), [entrega])

    def test_recusa_item_superior_de_outro_projeto(self):
        outro = Projeto.objects.create(
            condominio=self.condominio, nome="Troca das bombas", criado_por=self.gestao
        )
        etapa_do_outro = ProjetoItem.objects.create(projeto=outro, nome="Etapa alheia")
        entrega = ProjetoItem(
            projeto=self.projeto, parent=etapa_do_outro, nome="Entrega mal ligada"
        )
        with self.assertRaises(ValidationError) as capturado:
            entrega.full_clean()
        self.assertIn("parent", capturado.exception.message_dict)

    def test_recusa_item_superior_de_si_mesmo(self):
        item = ProjetoItem.objects.create(projeto=self.projeto, nome="Etapa")
        item.parent = item
        with self.assertRaises(ValidationError) as capturado:
            item.full_clean()
        self.assertIn("parent", capturado.exception.message_dict)


class ProjetoDemandaTests(TestCase):
    """O vínculo entre o projeto e as demandas que o movem."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)
        self.projeto = Projeto.objects.create(
            condominio=self.condominio,
            nome="Regularização do AVCB",
            criado_por=self.gestao,
        )
        self.demanda = Demanda.objects.create(
            condominio=self.condominio,
            titulo="Reunir pranchas do AVCB",
            criado_por=self.gestao,
        )

    def test_liga_a_demanda_ao_projeto(self):
        vinculo = ProjetoDemanda.objects.create(
            projeto=self.projeto, demanda=self.demanda
        )
        self.assertEqual(vinculo.observacao, "")
        self.assertEqual(self.projeto.demandas_vinculadas.get(), vinculo)

    def test_nao_repete_o_mesmo_vinculo(self):
        ProjetoDemanda.objects.create(projeto=self.projeto, demanda=self.demanda)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProjetoDemanda.objects.create(
                    projeto=self.projeto, demanda=self.demanda
                )

    def test_recusa_demanda_de_outro_condominio(self):
        outro = criar_condominio("Beta")
        demanda_alheia = Demanda.objects.create(
            condominio=outro, titulo="Demanda de outro prédio", criado_por=self.gestao
        )
        vinculo = ProjetoDemanda(projeto=self.projeto, demanda=demanda_alheia)
        with self.assertRaises(ValidationError) as capturado:
            vinculo.full_clean()
        self.assertIn("demanda", capturado.exception.message_dict)


class ProjetoTelaTests(TestCase):
    """A tela de projetos: os totais, o toque que anda e a demanda que nasce ligada."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)
        self.projeto = Projeto.objects.create(
            condominio=self.condominio,
            nome="Regularização do AVCB",
            criado_por=self.gestao,
        )
        self.etapa = ProjetoItem.objects.create(
            projeto=self.projeto, tipo=TipoItemProjeto.ETAPA, nome="Projeto executivo"
        )
        self.entrega_concluida = ProjetoItem.objects.create(
            projeto=self.projeto,
            parent=self.etapa,
            tipo=TipoItemProjeto.ENTREGA,
            nome="Pranchas assinadas",
            status=StatusProjeto.CONCLUIDO,
            valor_previsto=Decimal("1000.00"),
            valor_contratado=Decimal("950.00"),
        )
        self.entrega_parada = ProjetoItem.objects.create(
            projeto=self.projeto,
            parent=self.etapa,
            tipo=TipoItemProjeto.ENTREGA,
            nome="Protocolo no Corpo de Bombeiros",
            status=StatusProjeto.AGUARDANDO,
            valor_previsto=Decimal("2000.00"),
        )

    def test_progresso_e_totais_saem_das_entregas(self):
        self.assertEqual(self.projeto.total_de_entregas, 2)
        self.assertEqual(self.projeto.entregas_concluidas, 1)
        self.assertEqual(self.projeto.progresso, 50)
        self.assertEqual(self.projeto.previsto_total, Decimal("3000.00"))
        self.assertEqual(self.projeto.contratado_total, Decimal("950.00"))

    def test_lista_mostra_totais_e_progresso(self):
        resposta = self.client.get(reverse("projetos:lista"))
        self.assertEqual(resposta.status_code, 200)
        linha = resposta.context["projetos"][0]
        self.assertEqual(linha["previsto_total"], "R$ 3.000,00")
        self.assertEqual(linha["contratado_total"], "R$ 950,00")
        self.assertEqual(linha["entregas_concluidas"], 1)
        self.assertEqual(linha["total_de_entregas"], 2)
        self.assertEqual(linha["progresso"], 50)

    def test_o_toque_avanca_o_status(self):
        resposta = self.client.post(
            reverse(
                "projetos:avancar_item",
                args=[self.projeto.pk, self.entrega_parada.pk],
            )
        )
        self.assertRedirects(
            resposta, reverse("projetos:detalhe", args=[self.projeto.pk])
        )
        self.entrega_parada.refresh_from_db()
        self.assertEqual(self.entrega_parada.status, StatusProjeto.EM_ESTUDO)

    def test_editar_o_valor_na_linha_grava(self):
        fornecedor = criar_fornecedor(self.condominio, "Pranchas & Cia")
        resposta = self.client.post(
            reverse(
                "projetos:editar_valores_item",
                args=[self.projeto.pk, self.entrega_parada.pk],
            ),
            {
                "valor_previsto": "1.500,00",
                "valor_contratado": "1500.00",
                "fornecedor": str(fornecedor.pk),
            },
        )
        self.assertEqual(resposta.status_code, 302)
        self.entrega_parada.refresh_from_db()
        self.assertEqual(self.entrega_parada.valor_previsto, Decimal("1500.00"))
        self.assertEqual(self.entrega_parada.valor_contratado, Decimal("1500.00"))
        self.assertEqual(self.entrega_parada.fornecedor_id, fornecedor.pk)

    def test_abrir_demanda_na_etapa_cria_a_demanda_e_o_vinculo(self):
        resposta = self.client.post(
            reverse("projetos:abrir_demanda", args=[self.projeto.pk, self.etapa.pk]),
            {"titulo": "Reunião com o engenheiro"},
        )
        demanda = Demanda.objects.get(
            condominio=self.condominio,
            titulo="Projeto executivo: Reunião com o engenheiro",
        )
        self.assertRedirects(
            resposta, reverse("demandas:detalhe", args=[demanda.pk])
        )
        self.assertTrue(
            ProjetoDemanda.objects.filter(
                projeto=self.projeto, demanda=demanda
            ).exists()
        )

    def test_morador_recebe_403(self):
        entrar_como(
            self.client,
            Papel.MORADOR,
            self.condominio,
            email="morador@exemplo.test",
        )
        resposta = self.client.get(reverse("projetos:lista"))
        self.assertEqual(resposta.status_code, 403)
