"""Contratos: o que o condomínio assumiu, o que cobre e o que está para vencer.

O contrato é registrado com os objetos que ele cobre numa transação só, e a
situação de vigência é lida do prazo na hora da pergunta — nunca guardada.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.cadastros.tests.apoio import (
    criar_condominio,
    criar_fornecedor,
    criar_pessoa,
    entrar_como,
)
from apps.condominios.constants import Papel
from apps.core.constants import (
    EntidadeImportacaoHistorica,
    OrigemImportacaoHistorica,
    ResultadoImportacaoHistorica,
)
from apps.core.models import (
    ItemImportacaoHistorica,
    LoteImportacaoHistorica,
    VinculoImportacaoHistorica,
)
from apps.governanca.constants import PeriodicidadeCobranca, SituacaoVigencia
from apps.governanca.models import ContratoEquipamento, ContratoFornecedor, ContratoLocal
from apps.governanca.services import (
    ErroDeGovernanca,
    criar_contrato,
    criar_contrato_historico_importado,
)
from apps.governanca.tests.apoio import (
    HOJE,
    criar_equipamento_do,
    criar_local_do,
    criar_tipo_contrato,
)
from apps.governanca.tests.apoio import criar_contrato as contrato_de_teste


class ContratosNaTelaTests(TestCase):
    """O registro do contrato e os objetos que ele cobre."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("governanca:contratos")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)
        self.equipamento = criar_equipamento_do(self.condominio, self.gestao)
        self.local = criar_local_do(self.condominio, self.gestao)
        self.tipo = criar_tipo_contrato(self.condominio)

    def _dados(self, **campos):
        fornecedor = criar_fornecedor(
            condominio=self.condominio, criado_por=self.gestao
        )
        base = {
            "condominio": str(self.condominio.pk),
            "fornecedor": str(fornecedor.pk),
            "tipo_contrato": str(self.tipo.pk),
            "objeto": "Manutenção de elevadores",
            "data_inicio": "2026-01-10",
            "data_fim": "2027-01-10",
            "antecedencia_alerta_dias": "30",
        }
        return {**base, **campos}

    def test_gestao_cadastra_contrato_com_o_que_ele_cobre(self):
        resposta = self.client.post(
            reverse("governanca:criar_contrato"),
            self._dados(
                equipamentos=[str(self.equipamento.pk)],
                locais=[str(self.local.pk)],
            ),
        )
        self.assertEqual(resposta.status_code, 302)
        contrato = ContratoFornecedor.objects.get(objeto="Manutenção de elevadores")
        self.assertEqual(contrato.condominio, self.condominio)
        self.assertEqual(contrato.criado_por, self.gestao)
        self.assertEqual(
            set(
                ContratoEquipamento.objects.filter(contrato=contrato).values_list(
                    "equipamento_id", flat=True
                )
            ),
            {self.equipamento.pk},
        )
        self.assertEqual(
            set(
                ContratoLocal.objects.filter(contrato=contrato).values_list(
                    "local_id", flat=True
                )
            ),
            {self.local.pk},
        )

    def test_equipamento_de_outro_condominio_e_recusado_sem_gravar_nada(self):
        alheio = criar_equipamento_do(criar_condominio("Beta"), self.gestao)
        resposta = self.client.post(
            reverse("governanca:criar_contrato"),
            self._dados(equipamentos=[str(alheio.pk)]),
        )
        self.assertEqual(resposta.status_code, 400)
        self.assertFalse(ContratoFornecedor.objects.exists())

    def test_contrato_de_outro_condominio_e_404_ao_editar(self):
        alheio = contrato_de_teste(criar_condominio("Beta"), self.gestao)
        resposta = self.client.post(
            reverse("governanca:editar_contrato", args=[alheio.pk]),
            self._dados(),
        )
        self.assertEqual(resposta.status_code, 404)

    def test_supervisao_registra_e_corrige_contrato(self):
        entrar_como(
            self.client, Papel.SUPERVISORA, self.condominio, "supervisora@exemplo.test"
        )
        resposta = self.client.post(reverse("governanca:criar_contrato"), self._dados())
        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(ContratoFornecedor.objects.exists())

    def test_edicao_do_cabecalho_preserva_a_cobertura_escolhida(self):
        contrato = contrato_de_teste(self.condominio, self.gestao)
        ContratoEquipamento.objects.create(
            contrato=contrato, equipamento=self.equipamento
        )
        resposta = self.client.post(
            reverse("governanca:editar_contrato", args=[contrato.pk]),
            {
                "condominio": str(self.condominio.pk),
                "fornecedor": str(contrato.fornecedor.pk),
                "objeto": "Manutenção de elevadores — revisada",
                "data_inicio": "2026-01-10",
                "data_fim": "2027-01-10",
                "antecedencia_alerta_dias": "30",
                "equipamentos": [str(self.equipamento.pk)],
            },
        )
        self.assertEqual(resposta.status_code, 302)
        contrato.refresh_from_db()
        self.assertEqual(contrato.objeto, "Manutenção de elevadores — revisada")
        self.assertTrue(
            ContratoEquipamento.objects.filter(contrato=contrato).exists()
        )

    def test_lista_nao_traz_tipo_de_contrato_como_acao_principal(self):
        resposta = self.client.get(self.url)
        self.assertNotContains(resposta, reverse("governanca:tipos_de_contrato"))


def _achatado(resposta) -> str:
    """O HTML da resposta sem as quebras de linha do template.

    O que se confere nestes testes é a ordem e o aninhamento dos elementos, e
    não a forma como o template foi indentado.
    """
    return " ".join(resposta.content.decode().split())


class GridDeContratosTests(TestCase):
    """A linha de contrato mostra o essencial, e não o registro inteiro.

    A primeira coluna identifica — objeto, fornecedor e a classificação de
    apoio —, e o resto da linha é o que se compara e decide: vigência, valor e
    situação. O tipo deixa de ser coluna: sozinho ele raramente decide algo, e
    passa a acompanhar o objeto quando existe.
    """

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("governanca:contratos")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)
        self.tipo = criar_tipo_contrato(self.condominio, nome="Manutenção preventiva")

    def _na_lista(self, **campos):
        contrato = contrato_de_teste(self.condominio, self.gestao, **campos)
        return contrato, self.client.get(self.url)

    def test_a_grid_tem_as_colunas_do_essencial(self):
        _, resposta = self._na_lista()
        achatado = _achatado(resposta)

        for coluna in ("Contrato", "Vigência", "Valor", "Situação"):
            self.assertIn(f">{coluna}</th>", achatado)

    def test_tipo_de_contrato_deixa_de_ser_coluna_da_grid(self):
        _, resposta = self._na_lista(tipo_contrato=self.tipo)

        self.assertNotIn('<th scope="col">Tipo</th>', _achatado(resposta))

    def test_tipo_e_referencia_acompanham_o_objeto_quando_existem(self):
        _, resposta = self._na_lista(
            tipo_contrato=self.tipo, numero_referencia="2026-001"
        )

        self.assertContains(resposta, "Manutenção preventiva · Ref. 2026-001")

    def test_objeto_longo_e_limitado_na_apresentacao_e_preservado_no_dado(self):
        longo = (
            "Manutenção preventiva e corretiva de automação, portões, bombas e "
            "sistema de prevenção de incêndio"
        )
        contrato, resposta = self._na_lista(objeto=longo)

        self.assertContains(resposta, f'title="{longo}"')
        self.assertContains(resposta, "eithra-duas-linhas")
        contrato.refresh_from_db()
        self.assertEqual(contrato.objeto, longo)

    def test_vigencia_mostra_inicio_e_fim_na_mesma_linha(self):
        _, resposta = self._na_lista(
            data_inicio=date(2026, 7, 1), data_fim=date(2027, 6, 30)
        )

        self.assertIn(
            '<td class="text-nowrap">01/07/2026 → 30/06/2027</td>',
            _achatado(resposta),
        )

    def test_prazo_indeterminado_diz_que_nao_ha_termino_informado(self):
        _, resposta = self._na_lista(data_fim=None)

        self.assertContains(resposta, "sem término informado")
        self.assertNotContains(resposta, "None")

    def test_valor_ocupa_a_propria_coluna_com_a_periodicidade(self):
        _, resposta = self._na_lista(
            valor=Decimal("2000.00"),
            periodicidade_cobranca=PeriodicidadeCobranca.MENSAL,
        )

        self.assertContains(resposta, "R$ 2.000,00")
        self.assertContains(resposta, "Mensal")

    def test_contrato_sem_valor_mostra_travessao(self):
        _, resposta = self._na_lista(valor=None)

        self.assertContains(resposta, "—")
        self.assertNotContains(resposta, "None")

    def test_a_mesma_informacao_vira_item_no_celular(self):
        contrato, resposta = self._na_lista(valor=Decimal("2000.00"))

        celular = _achatado(resposta).split("d-lg-none", 1)[1]
        self.assertIn(contrato.objeto, celular)
        self.assertIn("R$ 2.000,00", celular)

    def test_acoes_da_linha_sao_icones_com_contexto_acessivel(self):
        contrato, resposta = self._na_lista()

        self.assertContains(resposta, 'title="Editar"')
        self.assertContains(resposta, f'aria-label="Editar {contrato.objeto}"')
        self.assertContains(resposta, 'title="Desativar"')
        self.assertContains(resposta, f'aria-label="Desativar {contrato.objeto}"')

    def test_a_lista_nao_expoe_informacao_tecnica(self):
        contrato_de_teste(self.condominio, None)
        texto = self.client.get(self.url).content.decode()

        for marcador in ("None", "criado_por", "ContratoFornecedor"):
            self.assertNotIn(marcador, texto)


class ModalDeContratoTests(TestCase):
    """O modal mantém a ação principal visível: só o corpo rola.

    O formulário é o próprio conteúdo do modal — é isso que deixa o cabeçalho e
    o rodapé parados enquanto o corpo rola. Com uma camada a mais entre os dois,
    o Bootstrap não limita a altura do corpo e o botão de salvar sai da tela.
    """

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.url = reverse("governanca:contratos")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)

    def test_o_formulario_e_o_proprio_conteudo_do_modal(self):
        achatado = _achatado(self.client.get(self.url))

        self.assertIn(
            'modal-dialog-scrollable"> <form class="modal-content"', achatado
        )
        self.assertNotIn('<div class="modal-content"> <form', achatado)

    def test_o_rodape_fica_fora_do_corpo_que_rola(self):
        achatado = _achatado(self.client.get(self.url))
        inicio = achatado.index('<form class="modal-content"')
        fim = achatado.index("</form>", inicio)

        corpo = achatado.index('<div class="modal-body">', inicio)
        rodape = achatado.index('<div class="modal-footer">', inicio)
        self.assertLess(corpo, rodape)
        self.assertLess(rodape, fim)

    def test_o_modal_de_criacao_tem_cancelar_e_a_acao_de_registrar(self):
        achatado = _achatado(self.client.get(self.url))

        self.assertIn('id="novo-contrato"', achatado)
        self.assertLess(achatado.index("Cancelar"), achatado.index(">Registrar</button>"))

    def test_o_modal_de_edicao_traz_salvar_alteracoes(self):
        contrato = contrato_de_teste(self.condominio, self.gestao)
        resposta = self.client.get(
            self.url, {"abrir": "editar", "item": str(contrato.pk)}
        )
        achatado = _achatado(resposta)

        self.assertIn('id="editar-contrato"', achatado)
        self.assertIn("Salvar alterações", achatado)
        self.assertNotIn('<div class="modal-content"> <form', achatado)


class VigenciaDoContratoTests(TestCase):
    """A situação do contrato é lida do prazo: nada é guardado como estado."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)

    def _contrato(self, **campos):
        return contrato_de_teste(self.condominio, self.gestao, **campos)

    def test_contrato_em_curso_esta_vigente(self):
        contrato = self._contrato(antecedencia_alerta_dias=30)
        self.assertEqual(contrato.situacao_em(HOJE), SituacaoVigencia.VIGENTE)

    def test_contrato_perto_do_fim_esta_vencendo(self):
        contrato = self._contrato(
            data_fim=HOJE + timedelta(days=10), antecedencia_alerta_dias=30
        )
        self.assertEqual(contrato.situacao_em(HOJE), SituacaoVigencia.VENCENDO)

    def test_contrato_terminado_esta_vencido(self):
        contrato = self._contrato(data_fim=HOJE - timedelta(days=1))
        self.assertEqual(contrato.situacao_em(HOJE), SituacaoVigencia.VENCIDO)

    def test_contrato_que_ainda_nao_comecou_esta_futuro(self):
        contrato = self._contrato(
            data_inicio=HOJE + timedelta(days=10),
            data_fim=HOJE + timedelta(days=400),
        )
        self.assertEqual(contrato.situacao_em(HOJE), SituacaoVigencia.FUTURO)

    def test_contrato_desativado_esta_inativo(self):
        contrato = self._contrato(ativo=False)
        self.assertEqual(contrato.situacao_em(HOJE), SituacaoVigencia.INATIVO)

    def test_contrato_sem_fim_esta_vigente(self):
        contrato = self._contrato(data_fim=None)
        self.assertEqual(contrato.situacao_em(HOJE), SituacaoVigencia.VIGENTE)


class ServicoDeContratoTests(TestCase):
    """O domínio recusa o que atravessa a fronteira do condomínio."""

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = entrar_como(self.client, Papel.SINDICA, self.condominio)
        self.fornecedor = contrato_de_teste(self.condominio, self.gestao).fornecedor

    def test_local_de_outro_condominio_e_recusado_no_servico(self):
        alheio = criar_local_do(criar_condominio("Beta"), self.gestao)
        with self.assertRaises(ErroDeGovernanca):
            criar_contrato(
                condominio=self.condominio,
                criado_por=self.gestao,
                dados={
                    "fornecedor": self.fornecedor,
                    "objeto": "Portaria",
                    "data_inicio": date(2026, 1, 1),
                },
                locais=[alheio],
            )
        self.assertEqual(ContratoFornecedor.objects.count(), 1)


class ContratoHistoricoImportadoTests(TestCase):
    """O contrato que já existia antes do Eithra entra sem autor, mas com fonte.

    A ausência de autor não é licença para gravar qualquer coisa: ela existe
    porque o contrato é anterior ao sistema. O que substitui o autor é a
    proveniência — e o cadastro comum continua exigindo quem o fez.
    """

    def setUp(self):
        self.condominio = criar_condominio("Alpha")
        self.gestao = criar_pessoa("gestao.carga@exemplo.test", "Gestão da carga")
        self.fornecedor = criar_fornecedor(self.condominio, criado_por=self.gestao)
        self.lote = LoteImportacaoHistorica.objects.create(
            condominio=self.condominio,
            origem=OrigemImportacaoHistorica.DRIVE,
            nome_original="Contratos do condomínio.xlsx",
            criado_por=self.gestao,
        )
        self.item, _ = ItemImportacaoHistorica.objects.registrar_item(
            lote=self.lote,
            identificador_externo="contrato-elevadores-2019",
            file_id="arquivo-001",
            criado_por=self.gestao,
        )

    def _dados(self, **campos):
        base = {
            "fornecedor": self.fornecedor,
            "objeto": "Manutenção de elevadores",
            "data_inicio": date(2019, 3, 1),
        }
        return {**base, **campos}

    def _incorporar(self, **campos):
        return criar_contrato_historico_importado(
            condominio=campos.pop("condominio", self.condominio),
            item=campos.pop("item", self.item),
            dados=campos.pop("dados", None) or self._dados(),
            **campos,
        )

    # -- A: o cadastro comum segue como era --------------------------------

    def test_cadastro_operacional_com_autor_continua_funcionando(self):
        contrato = criar_contrato(
            condominio=self.condominio,
            criado_por=self.gestao,
            dados=self._dados(),
        )
        self.assertEqual(contrato.criado_por, self.gestao)

    # -- B: sem autor, o cadastro comum é recusado -------------------------

    def test_cadastro_operacional_sem_autor_e_recusado(self):
        with self.assertRaises(ErroDeGovernanca):
            criar_contrato(
                condominio=self.condominio,
                criado_por=None,
                dados=self._dados(),
            )
        self.assertEqual(ContratoFornecedor.objects.count(), 0)

    # -- C e D: a incorporação histórica grava sem autor -------------------

    def test_incorporacao_registra_o_contrato_sem_autor(self):
        contrato = self._incorporar()
        self.assertIsNone(contrato.criado_por)
        self.assertTrue(ContratoFornecedor.objects.filter(pk=contrato.pk).exists())

    def test_contrato_incorporado_tem_a_mesma_coerencia_do_cadastro_comum(self):
        contrato = self._incorporar()
        self.assertEqual(contrato.condominio, self.condominio)
        self.assertEqual(contrato.fornecedor, self.fornecedor)
        self.assertTrue(contrato.ativo)

    # -- E: nenhum usuário é criado para preencher a autoria ---------------

    def test_incorporacao_nao_cria_usuario(self):
        antes = get_user_model().objects.count()
        self._incorporar()
        self.assertEqual(get_user_model().objects.count(), antes)

    # -- F: credenciais e permissões ficam intocadas -----------------------

    def test_incorporacao_nao_altera_credenciais_nem_permissoes(self):
        retrato = self._retrato_da_conta()
        self._incorporar()
        self.assertEqual(self._retrato_da_conta(), retrato)

    def _retrato_da_conta(self):
        conta = get_user_model().objects.get(pk=self.gestao.pk)
        return (
            conta.email,
            conta.password,
            conta.is_active,
            conta.is_staff,
            conta.is_superuser,
            tuple(conta.groups.values_list("pk", flat=True)),
            tuple(conta.user_permissions.values_list("pk", flat=True)),
        )

    # -- A fonte sustenta o registro ---------------------------------------

    def test_incorporacao_registra_de_onde_o_contrato_veio(self):
        contrato = self._incorporar()
        vinculo = VinculoImportacaoHistorica.objects.get()
        self.assertEqual(vinculo.item, self.item)
        self.assertEqual(vinculo.item.lote, self.lote)
        self.assertEqual(vinculo.condominio, self.condominio)
        self.assertEqual(
            vinculo.entidade_destino,
            EntidadeImportacaoHistorica.CONTRATO_FORNECEDOR,
        )
        self.assertEqual(vinculo.destino_id, contrato.pk)
        self.assertEqual(vinculo.resultado, ResultadoImportacaoHistorica.CRIADO)

    def test_incorporacao_sem_fonte_e_recusada(self):
        with self.assertRaises(ErroDeGovernanca):
            self._incorporar(item=None)
        self.assertEqual(ContratoFornecedor.objects.count(), 0)

    # -- G: a fronteira do condomínio continua de pé -----------------------

    def test_fonte_de_outro_condominio_e_recusada(self):
        outro = criar_condominio("Beta")
        lote_alheio = LoteImportacaoHistorica.objects.create(
            condominio=outro,
            origem=OrigemImportacaoHistorica.DRIVE,
            criado_por=self.gestao,
        )
        item_alheio, _ = ItemImportacaoHistorica.objects.registrar_item(
            lote=lote_alheio,
            identificador_externo="contrato-alheio",
            file_id="arquivo-alheio",
            criado_por=self.gestao,
        )
        with self.assertRaises(ErroDeGovernanca):
            self._incorporar(item=item_alheio)
        self.assertEqual(ContratoFornecedor.objects.count(), 0)
        self.assertFalse(VinculoImportacaoHistorica.objects.exists())

    # -- H: o fornecedor continua sendo do condomínio ----------------------

    def test_fornecedor_de_outro_condominio_continua_recusado(self):
        alheio = criar_fornecedor(criar_condominio("Gama"), criado_por=self.gestao)
        with self.assertRaises(ErroDeGovernanca):
            self._incorporar(dados=self._dados(fornecedor=alheio))
        self.assertEqual(ContratoFornecedor.objects.count(), 0)

    # -- I: o objeto continua obrigatório ----------------------------------

    def test_objeto_obrigatorio_continua_protegido(self):
        with self.assertRaises(ErroDeGovernanca):
            self._incorporar(dados=self._dados(objeto=""))
        self.assertEqual(ContratoFornecedor.objects.count(), 0)

    # -- J: o prazo continua coerente --------------------------------------

    def test_prazo_invertido_continua_recusado(self):
        with self.assertRaises(ErroDeGovernanca):
            self._incorporar(dados=self._dados(data_fim=date(2018, 1, 1)))
        self.assertEqual(ContratoFornecedor.objects.count(), 0)

    def test_incorporacao_guarda_o_que_o_contrato_cobre(self):
        equipamento = criar_equipamento_do(self.condominio, self.gestao)
        contrato = self._incorporar(equipamentos=[equipamento])
        self.assertEqual(
            list(
                contrato.equipamentos_vinculados.values_list(
                    "equipamento_id", flat=True
                )
            ),
            [equipamento.pk],
        )
