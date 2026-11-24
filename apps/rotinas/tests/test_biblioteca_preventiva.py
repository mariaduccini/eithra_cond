"""Testes da biblioteca preventiva e das decisões humanas por condomínio."""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.condominios.constants import Papel
from apps.core.constants import Prioridade
from apps.rotinas.biblioteca import (
    ErroDeBiblioteca,
    ativar_modelo,
    marcar_nao_aplica,
    modelos_para_condominio,
    reabrir_modelo,
)
from apps.rotinas.constants import Periodicidade, TipoRotina
from apps.rotinas.models import (
    DecisaoModeloAtividade,
    EstadoModeloAtividade,
    ModeloAtividadeProgramada,
    RotinaRecorrente,
)
from apps.rotinas.tests.apoio import (
    criar_condominio,
    criar_equipamento,
    criar_pessoa,
    criar_vinculo,
)


class BibliotecaPreventivaTests(TestCase):
    def setUp(self):
        self.condominio = criar_condominio("Condomínio Alfa")
        self.gestora = criar_pessoa("gestora@biblioteca.test")
        criar_vinculo(self.gestora, self.condominio, papel=Papel.SINDICA)
        self.client.force_login(self.gestora)
        self.modelo = self.criar_modelo()

    def criar_modelo(self, **campos):
        dados = {
            "chave": "teste-gerador",
            "titulo": "Testar grupo gerador",
            "categoria": "Geradores",
            "periodicidade_sugerida": "Mensal",
            "periodicidade_tipo": Periodicidade.MENSAL,
            "periodicidade_intervalo": 1,
            "tipo": TipoRotina.PREVENTIVA,
            "gatilhos_equipamento": ["gerador"],
            "checklist_sugerido": ["Conferir nível de combustível"],
        }
        dados.update(campos)
        return ModeloAtividadeProgramada.objects.create(**dados)

    def dados_ativacao(self, **campos):
        hoje = timezone.localdate()
        dados = {
            "titulo": self.modelo.titulo,
            "descricao": self.modelo.descricao or self.modelo.observacoes,
            "tipo": self.modelo.tipo,
            "periodicidade_tipo": Periodicidade.MENSAL,
            "intervalo": 1,
            "dia_mes": hoje.day,
            "data_inicio": hoje,
            "prioridade": Prioridade.NORMAL,
            "confirmar_frequencia": True,
            "confirmar_condicao": True,
            "confirmar_fornecedor": True,
            "confirmar_validacao_tecnica": True,
        }
        dados.update(campos)
        return dados

    def test_consulta_sugere_apenas_com_evidencia_e_nao_cria_rotina(self):
        criar_equipamento(self.condominio, nome="Gerador principal")

        sugestoes, demais = modelos_para_condominio(self.condominio)

        self.assertEqual([item["modelo"] for item in sugestoes], [self.modelo])
        self.assertEqual(sugestoes[0]["evidencias"], ["Equipamento cadastrado: Gerador principal"])
        self.assertEqual(demais, [])
        self.assertFalse(RotinaRecorrente.objects.exists())

    def test_modelo_condicional_sem_evidencia_fica_para_revisao_manual(self):
        fossa = self.criar_modelo(
            chave="teste-fossa",
            titulo="Inspecionar fossa séptica",
            categoria="Esgoto",
            gatilhos_equipamento=["fossa séptica"],
            condicional=True,
            condicao_necessaria="O condomínio possui fossa séptica.",
        )

        sugestoes, demais = modelos_para_condominio(self.condominio)

        self.assertNotIn(fossa, [item["modelo"] for item in sugestoes])
        self.assertIn(fossa, [item["modelo"] for item in demais])
        self.assertFalse(RotinaRecorrente.objects.exists())

    def test_tela_da_biblioteca_nao_cria_atividade(self):
        resposta = self.client.get(
            reverse("rotinas:biblioteca_preventiva"),
            {"condominio": self.condominio.pk},
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Nenhuma atividade é criada até a confirmação.")
        self.assertFalse(RotinaRecorrente.objects.exists())

    def test_revisao_recusa_post_sem_confirmacoes_obrigatorias(self):
        self.modelo.condicional = True
        self.modelo.condicao_necessaria = "O gerador existe."
        self.modelo.exige_fornecedor_especializado = True
        self.modelo.exige_validacao_tecnica = True
        self.modelo.save()
        hoje = timezone.localdate()

        resposta = self.client.post(
            reverse(
                "rotinas:revisar_modelo_preventivo",
                args=[self.modelo.chave],
            ),
            {
                "condominio": self.condominio.pk,
                "titulo": self.modelo.titulo,
                "descricao": "",
                "tipo": self.modelo.tipo,
                "periodicidade_tipo": Periodicidade.MENSAL,
                "intervalo": 1,
                "dia_mes": hoje.day,
                "data_inicio": hoje.isoformat(),
                "prioridade": Prioridade.NORMAL,
            },
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertContains(
            resposta, "Revisei e confirmei a frequência", status_code=400
        )
        self.assertContains(
            resposta, "Confirmo que a estrutura indicada existe", status_code=400
        )
        self.assertContains(
            resposta, "Vou confirmar o fornecedor especializado", status_code=400
        )
        self.assertContains(
            resposta, "Confirmei a frequência com profissional", status_code=400
        )
        self.assertFalse(RotinaRecorrente.objects.exists())

    def test_revisao_confirmada_cria_atividade_e_registra_decisao(self):
        hoje = timezone.localdate()
        resposta = self.client.post(
            reverse(
                "rotinas:revisar_modelo_preventivo",
                args=[self.modelo.chave],
            ),
            {
                "condominio": self.condominio.pk,
                "titulo": self.modelo.titulo,
                "descricao": self.modelo.descricao,
                "tipo": self.modelo.tipo,
                "periodicidade_tipo": Periodicidade.MENSAL,
                "intervalo": 1,
                "dia_mes": hoje.day,
                "data_inicio": hoje.isoformat(),
                "prioridade": Prioridade.NORMAL,
                "confirmar_frequencia": "on",
            },
        )

        self.assertEqual(resposta.status_code, 302)
        rotina = RotinaRecorrente.objects.get(condominio=self.condominio)
        decisao = DecisaoModeloAtividade.objects.get(
            condominio=self.condominio, modelo=self.modelo
        )
        self.assertEqual(decisao.estado, EstadoModeloAtividade.ATIVADA)
        self.assertEqual(decisao.rotina, rotina)
        self.assertEqual(rotina.execucoes.count(), 1)

    def test_ativacao_cria_rotina_e_copia_checklist_para_ocorrencia(self):
        rotina = ativar_modelo(
            modelo=self.modelo,
            condominio=self.condominio,
            usuario=self.gestora,
            dados=self.dados_ativacao(),
        )
        decisao = DecisaoModeloAtividade.objects.get(
            condominio=self.condominio, modelo=self.modelo
        )

        self.assertEqual(decisao.estado, EstadoModeloAtividade.ATIVADA)
        self.assertEqual(decisao.rotina, rotina)
        self.assertEqual(rotina.execucoes.count(), 1)
        with self.assertRaisesMessage(ErroDeBiblioteca, "já foi ativado"):
            ativar_modelo(
                modelo=self.modelo,
                condominio=self.condominio,
                usuario=self.gestora,
                dados=self.dados_ativacao(),
            )
        self.assertEqual(RotinaRecorrente.objects.count(), 1)

        checklist = rotina.checklist_modelo
        self.assertEqual(
            list(checklist.itens.values_list("descricao", flat=True)),
            ["Conferir nível de combustível"],
        )
        item_execucao = rotina.execucoes.get().itens.get()
        self.assertEqual(item_execucao.descricao, "Conferir nível de combustível")

        self.modelo.checklist_sugerido = ["Nova tarefa do catálogo"]
        self.modelo.save()
        checklist.refresh_from_db()
        self.assertEqual(
            list(checklist.itens.values_list("descricao", flat=True)),
            ["Conferir nível de combustível"],
        )
        self.assertEqual(item_execucao.descricao, "Conferir nível de combustível")

    def test_ativacao_exige_confirmacoes_e_nao_duplica_rotina_existente_inativa(self):
        self.modelo.condicional = True
        self.modelo.condicao_necessaria = "O gerador existe."
        self.modelo.exige_fornecedor_especializado = True
        self.modelo.exige_validacao_tecnica = True
        self.modelo.save()

        with self.assertRaisesMessage(ErroDeBiblioteca, "Confirme a frequência"):
            ativar_modelo(
                modelo=self.modelo,
                condominio=self.condominio,
                usuario=self.gestora,
                dados=self.dados_ativacao(
                    confirmar_frequencia=False,
                    confirmar_condicao=False,
                    confirmar_fornecedor=False,
                    confirmar_validacao_tecnica=False,
                ),
            )
        self.assertFalse(RotinaRecorrente.objects.exists())

        dados = self.dados_ativacao(titulo="testar grupo gerador")
        ativar_modelo(
            modelo=self.modelo,
            condominio=self.condominio,
            usuario=self.gestora,
            dados=dados,
        )

        outro_modelo = self.criar_modelo(
            chave="teste-gerador-duplicado",
            titulo="Testar grupo gerador",
            gatilhos_equipamento=[],
        )
        rotina = RotinaRecorrente.objects.get(condominio=self.condominio)
        rotina.ativo = False
        rotina.save(update_fields=["ativo"])
        with self.assertRaisesMessage(ErroDeBiblioteca, "Já existe"):
            ativar_modelo(
                modelo=outro_modelo,
                condominio=self.condominio,
                usuario=self.gestora,
                dados=self.dados_ativacao(titulo=outro_modelo.titulo),
            )
        self.assertEqual(RotinaRecorrente.objects.count(), 1)

    def test_decisao_nao_se_aplica_isolada_por_condominio(self):
        outro_condominio = criar_condominio("Condomínio Beta")
        marcar_nao_aplica(
            modelo=self.modelo,
            condominio=self.condominio,
            usuario=self.gestora,
            observacao="Não possui gerador.",
        )

        sugestoes, demais = modelos_para_condominio(outro_condominio)

        self.assertEqual(sugestoes, [])
        estado_no_outro = next(
            item["estado"]
            for item in demais
            if item["modelo"].pk == self.modelo.pk
        )
        self.assertEqual(estado_no_outro, EstadoModeloAtividade.DISPONIVEL)
        reabrir_modelo(
            modelo=self.modelo,
            condominio=self.condominio,
            usuario=self.gestora,
        )
        decisao = DecisaoModeloAtividade.objects.get(
            condominio=self.condominio, modelo=self.modelo
        )
        self.assertEqual(decisao.estado, EstadoModeloAtividade.DISPONIVEL)
        self.assertFalse(RotinaRecorrente.objects.exists())

    def test_biblioteca_e_revisao_respeitam_escopo_autorizado(self):
        outro_condominio = criar_condominio("Condomínio não autorizado")

        resposta_biblioteca = self.client.get(
            reverse("rotinas:biblioteca_preventiva"),
            {"condominio": outro_condominio.pk},
        )
        resposta_revisao = self.client.get(
            reverse(
                "rotinas:revisar_modelo_preventivo",
                args=[self.modelo.chave],
            ),
            {"condominio": outro_condominio.pk},
        )

        self.assertEqual(resposta_biblioteca.status_code, 404)
        self.assertEqual(resposta_revisao.status_code, 404)
        self.assertFalse(RotinaRecorrente.objects.exists())

    def test_carga_idempotente_preserva_modelo_customizado_e_inativo(self):
        self.modelo.titulo = "Título customizado"
        self.modelo.ativo = False
        self.modelo.save()
        registro_inicial = {
            "chave": self.modelo.chave,
            "titulo": "Título do catálogo",
            "categoria": "Geradores",
            "periodicidade_sugerida": "Mensal",
        }
        novo_registro = {
            **registro_inicial,
            "chave": "novo-modelo-idempotente",
        }
        with patch(
            "apps.rotinas.management.commands.popular_biblioteca_preventiva.MODELOS_INICIAIS",
            [registro_inicial, novo_registro],
        ):
            primeira_saida = StringIO()
            call_command("popular_biblioteca_preventiva", stdout=primeira_saida)
            segunda_saida = StringIO()
            call_command("popular_biblioteca_preventiva", stdout=segunda_saida)

        self.modelo.refresh_from_db()
        self.assertIn("Modelos criados: 1", primeira_saida.getvalue())
        self.assertIn("Modelos criados: 0", segunda_saida.getvalue())
        self.assertEqual(self.modelo.titulo, "Título customizado")
        self.assertFalse(self.modelo.ativo)
        self.assertTrue(
            ModeloAtividadeProgramada.objects.filter(
                chave="novo-modelo-idempotente"
            ).exists()
        )
        self.assertFalse(RotinaRecorrente.objects.exists())
