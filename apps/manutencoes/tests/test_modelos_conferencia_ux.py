"""UX mínima dos modelos de conferência e sua aplicação ao plano preventivo.

A biblioteca vive dentro das preventivas e é administrada só pela Administradora.
Esta suíte prova o que a tela promete: a Administradora vê a lista, cria, edita,
reordena os itens e ativa/desativa; a síndica e a supervisão não administram nada
disso, nem por POST direto. No plano, o modelo de conferência é escolhido entre
os ativos do mesmo condomínio, a prévia corresponde ao modelo e os critérios são
copiados pelo serviço oficial — por valor, e nunca por vínculo vivo. Trocar uma
lista que já existe exige confirmação, o plano sem modelo continua permitido e
nenhuma ocorrência passada é reescrita.
"""
from datetime import date

from django.test import TestCase
from django.urls import reverse

from apps.condominios.constants import Papel
from apps.manutencoes.models import (
    ModeloConferenciaPreventiva,
    PlanoPreventivo,
)
from apps.manutencoes.services import (
    criar_modelo_conferencia,
    definir_ativo_do_modelo,
    editar_modelo_conferencia,
    gerar_ocorrencia_preventiva,
)
from apps.manutencoes.tests.apoio import (
    criar_condominio,
    criar_pessoa,
    criar_vinculo,
)


class BaseDeModelosUx(TestCase):
    """Um condomínio com a Administradora, as iscas de papel e um vizinho."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.outro = criar_condominio("Beta")

        self.administradora = criar_pessoa("adm@exemplo.test", "Administradora")
        criar_vinculo(
            self.administradora, self.condominio, papel=Papel.ADMINISTRADORA
        )

        self.sindica = criar_pessoa("sindica@exemplo.test", "Síndica")
        criar_vinculo(self.sindica, self.condominio, papel=Papel.SINDICA)

        self.supervisora = criar_pessoa("supervisao@exemplo.test", "Supervisão")
        criar_vinculo(self.supervisora, self.condominio, papel=Papel.SUPERVISORA)

        self.admin_beta = criar_pessoa("adm-beta@exemplo.test", "Administradora do Beta")
        criar_vinculo(self.admin_beta, self.outro, papel=Papel.ADMINISTRADORA)

        self.client.force_login(self.administradora)

    # -- apoio ---------------------------------------------------------------

    def _campos_criterios(self, criterios):
        """Os dois campos paralelos do editor, um par por item."""
        return {
            "criterio_descricao": [item[0] for item in criterios],
            "criterio_obrigatorio": [
                "sim" if item[1] else "nao" for item in criterios
            ],
        }

    def _dados_do_modelo(self, *, nome="Limpeza de piscina", descricao="", criterios=()):
        dados = {
            "condominio": str(self.condominio.pk),
            "nome": nome,
            "descricao": descricao,
        }
        dados.update(self._campos_criterios(criterios))
        return dados

    def _modelo_pela_tela(self, **campos):
        resposta = self.client.post(
            reverse("manutencoes:criar_modelo_conferencia"),
            self._dados_do_modelo(**campos),
        )
        self.assertEqual(resposta.status_code, 302)
        return ModeloConferenciaPreventiva.objects.get()

    def _modelo(self, *, condominio=None, criado_por=None, nome="Limpeza de piscina",
                descricao="", criterios=()):
        return criar_modelo_conferencia(
            condominio=condominio or self.condominio,
            criado_por=criado_por or self.administradora,
            nome=nome,
            descricao=descricao,
            criterios=criterios,
        )

    def _dados_do_plano(self, **campos):
        dados = {
            "condominio": str(self.condominio.pk),
            "titulo": "Limpeza do reservatório",
            "frequencia": "todo_mes",
            "proxima_data_prevista": "2026-12-01",
        }
        dados.update(campos)
        return dados

    def _plano(self, **campos):
        resposta = self.client.post(
            reverse("manutencoes:criar_plano"), self._dados_do_plano(**campos)
        )
        self.assertEqual(resposta.status_code, 302)
        return PlanoPreventivo.objects.get()

    def _descricoes(self, colecao):
        return [criterio.descricao for criterio in colecao]


class BibliotecaTests(BaseDeModelosUx):
    """A Administradora vê a biblioteca, cria, edita, reordena e ativa/desativa."""

    def test_administradora_acessa_a_biblioteca(self):
        resposta = self.client.get(reverse("manutencoes:modelos_conferencia"))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Modelos de conferência")
        self.assertContains(resposta, "Nenhum modelo de conferência")

    def test_administradora_cria_modelo_com_criterios(self):
        modelo = self._modelo_pela_tela(
            criterios=[("Água limpa", True), ("Relatório anexado", False)]
        )

        self.assertEqual(modelo.nome, "Limpeza de piscina")
        self.assertEqual(
            [(c.descricao, c.ordem, c.obrigatorio) for c in modelo.criterios.all()],
            [("Água limpa", 0, True), ("Relatório anexado", 1, False)],
        )

    def test_administradora_edita_modelo(self):
        modelo = self._modelo(criterios=[{"descricao": "Água limpa", "ordem": 0}])

        resposta = self.client.post(
            reverse("manutencoes:editar_modelo_conferencia", args=[modelo.pk]),
            {
                "condominio": str(self.condominio.pk),
                "nome": "Limpeza de piscina",
                "descricao": "Rotina mensal",
                **self._campos_criterios([("pH dentro da faixa", True)]),
            },
        )

        self.assertEqual(resposta.status_code, 302)
        modelo.refresh_from_db()
        self.assertEqual(modelo.descricao, "Rotina mensal")
        self.assertEqual(self._descricoes(modelo.criterios.all()), ["pH dentro da faixa"])

    def test_reordenar_itens_na_tela_muda_a_ordem_gravada(self):
        modelo = self._modelo(
            criterios=[
                {"descricao": "Primeiro", "ordem": 0},
                {"descricao": "Segundo", "ordem": 1},
                {"descricao": "Terceiro", "ordem": 2},
            ]
        )

        # A tela envia os itens na ordem em que as linhas aparecem: aqui, o
        # terceiro subiu para o começo.
        resposta = self.client.post(
            reverse("manutencoes:editar_modelo_conferencia", args=[modelo.pk]),
            {
                "condominio": str(self.condominio.pk),
                "nome": modelo.nome,
                "descricao": "",
                **self._campos_criterios(
                    [("Terceiro", True), ("Primeiro", True), ("Segundo", True)]
                ),
            },
        )

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(
            [(c.descricao, c.ordem) for c in modelo.criterios.all()],
            [("Terceiro", 0), ("Primeiro", 1), ("Segundo", 2)],
        )

    def test_administradora_desativa_e_reativa_modelo(self):
        modelo = self._modelo()

        self.client.post(
            reverse("manutencoes:alternar_modelo_conferencia", args=[modelo.pk])
        )
        modelo.refresh_from_db()
        self.assertFalse(modelo.ativo)

        self.client.post(
            reverse("manutencoes:alternar_modelo_conferencia", args=[modelo.pk])
        )
        modelo.refresh_from_db()
        self.assertTrue(modelo.ativo)

    def test_lista_mostra_nome_descricao_contagem_e_situacao(self):
        self._modelo(
            nome="Limpeza de piscina",
            descricao="Rotina mensal da piscina",
            criterios=[
                {"descricao": "Água limpa", "ordem": 0},
                {"descricao": "Relatório anexado", "ordem": 1},
            ],
        )

        resposta = self.client.get(reverse("manutencoes:modelos_conferencia"))

        self.assertContains(resposta, "Limpeza de piscina")
        self.assertContains(resposta, "Rotina mensal da piscina")
        self.assertContains(resposta, "2 itens")
        self.assertContains(resposta, ">Ativo<")

    def test_sindica_e_supervisora_nao_administram_a_biblioteca(self):
        for pessoa in (self.sindica, self.supervisora):
            self.client.force_login(pessoa)

            self.assertEqual(
                self.client.get(reverse("manutencoes:modelos_conferencia")).status_code,
                404,
            )
            self.assertEqual(
                self.client.post(
                    reverse("manutencoes:criar_modelo_conferencia"),
                    self._dados_do_modelo(),
                ).status_code,
                404,
            )

        self.assertFalse(ModeloConferenciaPreventiva.objects.exists())


class PlanoTests(BaseDeModelosUx):
    """O modelo entra no plano como referência, e a cópia é feita pelo serviço."""

    def test_formulario_lista_so_modelos_ativos_do_condominio(self):
        ativo = self._modelo(
            nome="Limpeza de piscina",
            criterios=[{"descricao": "Água limpa", "ordem": 0}],
        )
        parado = self._modelo(nome="Roteiro parado")
        definir_ativo_do_modelo(modelo=parado, usuario=self.administradora, ativo=False)
        self._modelo(
            condominio=self.outro,
            criado_por=self.admin_beta,
            nome="Bomba do Beta",
            criterios=[{"descricao": "Pressão", "ordem": 0}],
        )

        resposta = self.client.get(reverse("manutencoes:planos"))

        self.assertEqual(
            [modelo.pk for modelo in resposta.context["modelos_disponiveis"]],
            [ativo.pk],
        )
        self.assertContains(resposta, "Limpeza de piscina")
        self.assertNotContains(resposta, "Roteiro parado")
        self.assertNotContains(resposta, "Bomba do Beta")

    def test_previa_do_modelo_corresponde_ao_modelo(self):
        self._modelo(
            nome="Limpeza de piscina",
            criterios=[
                {"descricao": "Água limpa", "ordem": 0},
                {"descricao": "Relatório anexado", "ordem": 1, "obrigatorio": False},
            ],
        )

        resposta = self.client.get(reverse("manutencoes:planos"))
        previa = resposta.context["modelos_previa"]

        self.assertEqual(len(previa), 1)
        self.assertEqual(previa[0]["nome"], "Limpeza de piscina")
        self.assertEqual(
            [(item["descricao"], item["obrigatorio"]) for item in previa[0]["criterios"]],
            [("Água limpa", True), ("Relatório anexado", False)],
        )

    def test_plano_aplica_os_criterios_do_modelo(self):
        modelo = self._modelo(
            criterios=[
                {"descricao": "Água limpa", "ordem": 0},
                {"descricao": "Sem vazamento", "ordem": 1},
            ]
        )

        plano = self._plano(
            modelo_de_conferencia=str(modelo.pk),
            usar_modelo_de_conferencia="on",
        )

        self.assertEqual(
            self._descricoes(plano.criterios_conferencia.all()),
            ["Água limpa", "Sem vazamento"],
        )

    def test_criterios_copiados_sao_independentes_do_modelo(self):
        modelo = self._modelo(
            criterios=[{"descricao": "Água limpa", "ordem": 0}]
        )
        plano = self._plano(
            modelo_de_conferencia=str(modelo.pk),
            usar_modelo_de_conferencia="on",
        )

        # Editar o modelo depois vale para as próximas aplicações, e não alcança
        # o que já foi copiado para o plano.
        editar_modelo_conferencia(
            modelo=modelo,
            usuario=self.administradora,
            nome=modelo.nome,
            criterios=[{"descricao": "Outra coisa", "ordem": 0}],
        )

        self.assertEqual(
            self._descricoes(plano.criterios_conferencia.all()), ["Água limpa"]
        )

    def test_substituicao_exige_confirmacao(self):
        primeiro = self._modelo(
            nome="Roteiro antigo",
            criterios=[{"descricao": "Água limpa", "ordem": 0}],
        )
        segundo = self._modelo(
            nome="Roteiro novo",
            criterios=[{"descricao": "pH dentro da faixa", "ordem": 0}],
        )
        plano = self._plano(
            modelo_de_conferencia=str(primeiro.pk),
            usar_modelo_de_conferencia="on",
        )

        # Sem confirmar, a lista do plano não é trocada.
        sem_confirmar = self.client.post(
            reverse("manutencoes:editar_plano", args=[plano.pk]),
            self._dados_do_plano(
                modelo_de_conferencia=str(segundo.pk),
                usar_modelo_de_conferencia="on",
            ),
        )
        self.assertEqual(sem_confirmar.status_code, 400)
        self.assertEqual(
            self._descricoes(plano.criterios_conferencia.all()), ["Água limpa"]
        )

        # Com a confirmação, a troca acontece.
        confirmado = self.client.post(
            reverse("manutencoes:editar_plano", args=[plano.pk]),
            self._dados_do_plano(
                modelo_de_conferencia=str(segundo.pk),
                usar_modelo_de_conferencia="on",
                substituir_conferencia="on",
            ),
        )
        self.assertEqual(confirmado.status_code, 302)
        self.assertEqual(
            self._descricoes(plano.criterios_conferencia.all()),
            ["pH dentro da faixa"],
        )

    def test_ocorrencia_passada_nao_muda_quando_o_plano_e_substituido(self):
        primeiro = self._modelo(
            nome="Roteiro antigo",
            criterios=[
                {"descricao": "Água limpa", "ordem": 0},
                {"descricao": "Sem vazamento", "ordem": 1},
            ],
        )
        segundo = self._modelo(
            nome="Roteiro novo",
            criterios=[{"descricao": "pH dentro da faixa", "ordem": 0}],
        )
        plano = self._plano(
            modelo_de_conferencia=str(primeiro.pk),
            usar_modelo_de_conferencia="on",
        )
        ocorrencia = gerar_ocorrencia_preventiva(plano=plano, usuario=self.sindica)

        self.client.post(
            reverse("manutencoes:editar_plano", args=[plano.pk]),
            self._dados_do_plano(
                modelo_de_conferencia=str(segundo.pk),
                usar_modelo_de_conferencia="on",
                substituir_conferencia="on",
            ),
        )

        self.assertEqual(
            self._descricoes(plano.criterios_conferencia.all()),
            ["pH dentro da faixa"],
        )
        self.assertEqual(
            self._descricoes(ocorrencia.criterios_conferencia.all()),
            ["Água limpa", "Sem vazamento"],
        )

    def test_plano_sem_modelo_continua_permitido(self):
        plano = self._plano()

        self.assertTrue(plano.exige_conferencia)
        self.assertFalse(plano.criterios_conferencia.exists())

    def test_tela_mostra_atencao_quando_faltam_itens_de_conferencia(self):
        self._plano()

        resposta = self.client.get(reverse("manutencoes:planos"))

        self.assertContains(resposta, "Itens de conferência ainda não definidos")

    def test_atencao_some_quando_o_plano_recebe_os_itens(self):
        modelo = self._modelo(criterios=[{"descricao": "Água limpa", "ordem": 0}])
        self._plano(
            modelo_de_conferencia=str(modelo.pk),
            usar_modelo_de_conferencia="on",
        )

        resposta = self.client.get(reverse("manutencoes:planos"))

        self.assertNotContains(resposta, "Itens de conferência ainda não definidos")

    def test_tela_sem_modelo_mostra_estado_e_acao_da_administradora(self):
        resposta = self.client.get(reverse("manutencoes:planos"))

        self.assertContains(resposta, "Nenhum modelo definido ainda.")
        self.assertContains(resposta, "Criar modelo de conferência")

    def test_quem_nao_administra_nao_ve_a_acao_de_criar_modelo(self):
        self.client.force_login(self.sindica)

        resposta = self.client.get(reverse("manutencoes:planos"))

        self.assertContains(resposta, "Nenhum modelo definido ainda.")
        self.assertNotContains(resposta, "Criar modelo de conferência")
        self.assertNotContains(resposta, "Modelos de conferência")


class SegurancaTests(BaseDeModelosUx):
    """O backend bloqueia quem não administra, e o vizinho não atravessa."""

    def test_post_direto_de_sindica_e_bloqueado(self):
        self.client.force_login(self.sindica)

        resposta = self.client.post(
            reverse("manutencoes:criar_modelo_conferencia"), self._dados_do_modelo()
        )

        self.assertEqual(resposta.status_code, 404)
        self.assertFalse(ModeloConferenciaPreventiva.objects.exists())

    def test_post_direto_de_supervisora_e_bloqueado(self):
        self.client.force_login(self.supervisora)

        resposta = self.client.post(
            reverse("manutencoes:criar_modelo_conferencia"), self._dados_do_modelo()
        )

        self.assertEqual(resposta.status_code, 404)
        self.assertFalse(ModeloConferenciaPreventiva.objects.exists())

    def test_modelo_de_outro_condominio_nao_pode_ser_aplicado(self):
        alheio = self._modelo(
            condominio=self.outro,
            criado_por=self.admin_beta,
            nome="Bomba do Beta",
            criterios=[{"descricao": "Conferir pressão", "ordem": 0}],
        )

        resposta = self.client.post(
            reverse("manutencoes:criar_plano"),
            self._dados_do_plano(
                modelo_de_conferencia=str(alheio.pk),
                usar_modelo_de_conferencia="on",
            ),
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertEqual(PlanoPreventivo.objects.count(), 0)

    def test_editar_e_alternar_modelo_de_outro_condominio_sao_404(self):
        alheio = self._modelo(
            condominio=self.outro, criado_por=self.admin_beta, nome="Bomba do Beta"
        )

        self.assertEqual(
            self.client.post(
                reverse("manutencoes:editar_modelo_conferencia", args=[alheio.pk]),
                {
                    "condominio": str(self.outro.pk),
                    "nome": "Alterado",
                    **self._campos_criterios([("Algo", True)]),
                },
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse("manutencoes:alternar_modelo_conferencia", args=[alheio.pk])
            ).status_code,
            404,
        )

        alheio.refresh_from_db()
        self.assertEqual(alheio.nome, "Bomba do Beta")
        self.assertTrue(alheio.ativo)


class UxTests(BaseDeModelosUx):
    """A tela fala em linguagem humana e não depende de tabela no celular."""

    def test_biblioteca_tem_lista_compacta_no_celular(self):
        self._modelo()

        resposta = self.client.get(reverse("manutencoes:modelos_conferencia"))

        self.assertContains(resposta, "d-none d-lg-block")
        self.assertContains(resposta, "d-lg-none")

    def test_editor_de_criterios_e_em_blocos_simples(self):
        resposta = self.client.get(reverse("manutencoes:modelos_conferencia"))

        self.assertContains(resposta, "data-editor-criterios")
        self.assertContains(resposta, "data-linha-criterio")
        self.assertContains(resposta, "data-adicionar-criterio")
        self.assertContains(resposta, 'name="criterio_descricao"')
        self.assertContains(resposta, 'name="criterio_obrigatorio"')

    def test_situacao_em_linguagem_humana(self):
        self._modelo(nome="Roteiro ativo")
        parado = self._modelo(nome="Roteiro parado")
        definir_ativo_do_modelo(modelo=parado, usuario=self.administradora, ativo=False)

        resposta = self.client.get(reverse("manutencoes:modelos_conferencia"))

        self.assertContains(resposta, ">Ativo<")
        self.assertContains(resposta, ">Inativo<")

    def test_plano_escuta_o_modelo_em_blocos_e_nao_em_tabela(self):
        self._modelo(criterios=[{"descricao": "Água limpa", "ordem": 0}])

        resposta = self.client.get(reverse("manutencoes:planos"))

        self.assertContains(resposta, "Como a execução será conferida?")
        self.assertContains(resposta, "data-eithra-previa-modelo")
        self.assertContains(resposta, "data-eithra-usar-modelo")


class AcessoAoPlanoTests(BaseDeModelosUx):
    """O acesso à biblioteca é discreto e continua dentro das preventivas."""

    def test_administradora_ve_o_acesso_aos_modelos_nas_preventivas(self):
        resposta = self.client.get(reverse("manutencoes:planos"))

        self.assertContains(resposta, "Modelos de conferência")
        self.assertContains(
            resposta, reverse("manutencoes:modelos_conferencia")
        )

    def test_biblioteca_oferece_o_caminho_de_volta_as_preventivas(self):
        resposta = self.client.get(reverse("manutencoes:modelos_conferencia"))

        self.assertContains(resposta, reverse("manutencoes:planos"))

    def test_editar_modelo_avisa_que_planos_existentes_nao_mudam(self):
        modelo = self._modelo()

        resposta = self.client.get(
            reverse("manutencoes:modelos_conferencia"),
            {"abrir": "editar", "item": str(modelo.pk)},
        )

        self.assertContains(
            resposta,
            "Alterações neste modelo serão usadas apenas quando ele for aplicado novamente.",
        )
