"""Testes do importador definitivo do RVE.

A matriz de reconciliação é a única fonte desta carga. Os testes daqui montam
matrizes pequenas e exercitam o que a rodada promete: a simulação não grava
nada, a execução escreve o núcleo operacional, a linhagem guarda o resto, e uma
segunda passagem não cria nada de novo.

Nenhum dado real do condomínio entra nos testes de comportamento — os
identificadores ali são de teste. A única exceção é o ensaio da amostra
representativa, que lê a matriz real quando ela existe no disco e se declara
pulado quando não existe.
"""
import csv
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from django.test import SimpleTestCase, TestCase

from apps.cadastros.tests.apoio import criar_condominio, criar_pessoa
from apps.core import rve_importacao
from apps.core.constants import (
    EntidadeImportacaoHistorica,
    EstadoProcessamentoImportacao,
    OrigemImportacaoHistorica,
    ResultadoImportacaoHistorica,
)
from apps.core.models import (
    ItemImportacaoHistorica,
    LoteImportacaoHistorica,
    VinculoImportacaoHistorica,
)
from apps.demandas.constants import OrigemDemanda, PrioridadeDemanda, StatusDemanda
from apps.demandas.models import Demanda
from apps.governanca.constants import StatusProjeto, TipoItemProjeto
from apps.governanca.models import Projeto, ProjetoDemanda, ProjetoItem
from apps.cadastros.models import Equipamento

# O cabeçalho da matriz real, para que o teste exercite o mesmo contrato.
COLUNAS = [
    "external_id",
    "familia",
    "tipo_entidade",
    "titulo_canonico",
    "parent_external_id",
    "status_fonte",
    "status_canonico_proposto",
    "prioridade_fonte",
    "prioridade_canonica",
    "responsavel_fonte",
    "responsavel_canonico",
    "local",
    "ativo",
    "fornecedor",
    "financeiro",
    "evidencia",
    "fonte_primaria",
    "fontes_complementares",
    "acao_importacao",
    "motivo",
    "conflito",
    "revisao_necessaria",
    "linha_fonte_primaria",
]


def _linha(
    external_id,
    tipo,
    titulo,
    *,
    acao="CREATE",
    parent="",
    status="",
    prioridade="",
    revisao="nao",
    motivo="",
    fonte="matriz.csv",
):
    """Uma linha da matriz com só o que o teste precisa declarar."""
    return {
        "external_id": external_id,
        "familia": tipo,
        "tipo_entidade": tipo,
        "titulo_canonico": titulo,
        "parent_external_id": parent,
        "status_canonico_proposto": status,
        "prioridade_canonica": prioridade,
        "acao_importacao": acao,
        "motivo": motivo,
        "revisao_necessaria": revisao,
        "fonte_primaria": fonte,
        "linha_fonte_primaria": fonte,
    }


def _escrever_matriz(linhas) -> Path:
    """Grava uma matriz de teste em arquivo temporário, no formato da real."""
    diretorio = tempfile.mkdtemp(prefix="rve-matriz-")
    caminho = Path(diretorio) / "reconciliacao.csv"
    with caminho.open("w", encoding="utf-8-sig", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=COLUNAS)
        escritor.writeheader()
        for linha in linhas:
            escritor.writerow({coluna: linha.get(coluna, "") for coluna in COLUNAS})
    return caminho


# O cabeçalho do artefato de reconciliação declarada.
COLUNAS_RECONCILIACAO = [
    "external_id_historico",
    "entidade_destino",
    "destino_id",
    "decisao",
    "observacao",
]


def _decisao(external_id, decisao, *, entidade="demanda", destino_id="", observacao=""):
    """Uma linha do artefato de reconciliação, com só o que o teste declara."""
    return {
        "external_id_historico": external_id,
        "entidade_destino": entidade,
        "destino_id": destino_id,
        "decisao": decisao,
        "observacao": observacao,
    }


def _escrever_reconciliacao(decisoes) -> Path:
    """Grava um artefato de reconciliação de teste, no formato do real."""
    diretorio = tempfile.mkdtemp(prefix="rve-reconciliacao-")
    caminho = Path(diretorio) / rve_importacao.ARQUIVO_RECONCILIACAO
    with caminho.open("w", encoding="utf-8-sig", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=COLUNAS_RECONCILIACAO)
        escritor.writeheader()
        for decisao in decisoes:
            escritor.writerow(
                {coluna: decisao.get(coluna, "") for coluna in COLUNAS_RECONCILIACAO}
            )
    return caminho


class LeituraDaMatrizTests(SimpleTestCase):
    """A matriz é lida como ela é, sem inventar coluna que não existe."""

    def test_le_a_matriz_e_tolera_coluna_ausente(self):
        diretorio = tempfile.mkdtemp(prefix="rve-matriz-")
        caminho = Path(diretorio) / "parcial.csv"
        caminho.write_text(
            "external_id,tipo_entidade,titulo_canonico,acao_importacao\n"
            "RVE-001,demanda,Trocar a bomba,CREATE\n",
            encoding="utf-8-sig",
        )

        linhas = rve_importacao.ler_matriz(caminho)

        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0].external_id, "RVE-001")
        self.assertEqual(linhas[0].titulo, "Trocar a bomba")
        self.assertEqual(linhas[0].parent_external_id, "")
        self.assertFalse(linhas[0].revisao_necessaria)

    def test_tipo_desconhecido_nao_tem_destino_de_dominio(self):
        linha = rve_importacao.LinhaDaMatriz(
            external_id="X-1", tipo_entidade="tipo que não existe"
        )

        self.assertIsNone(linha.entidade)

    def test_linhagem_e_metadado_sem_destino(self):
        linha = rve_importacao.LinhaDaMatriz(
            external_id="L-1", tipo_entidade="linhagem"
        )

        self.assertIsNone(linha.entidade)


class LeituraDaReconciliacaoTests(SimpleTestCase):
    """O artefato de reconciliação é lido como evidência, não como palpite."""

    def test_le_as_decisoes_indexadas_pelo_identificador_da_fonte(self):
        caminho = _escrever_reconciliacao(
            [
                _decisao(
                    "RVE-055",
                    "VINCULAR_EXISTENTE",
                    destino_id="454498f2-1179-459b-8532-4f43eb69b045",
                ),
                _decisao("RVE-009", "REVISAR"),
            ]
        )

        decisoes = rve_importacao.ler_reconciliacao(caminho)

        self.assertEqual(set(decisoes), {"RVE-055", "RVE-009"})
        self.assertEqual(decisoes["RVE-055"].decisao, "VINCULAR_EXISTENTE")
        self.assertTrue(decisoes["RVE-055"].muda_o_destino)
        self.assertEqual(decisoes["RVE-009"].destino_id, "")

    def test_decisao_criar_nao_muda_o_destino_da_linha(self):
        caminho = _escrever_reconciliacao([_decisao("RVE-100", "CREATE")])

        decisao = rve_importacao.ler_reconciliacao(caminho)["RVE-100"]

        self.assertFalse(decisao.muda_o_destino)

    def test_artefato_ausente_e_ausencia_de_decisao(self):
        self.assertEqual(rve_importacao._carregar_reconciliacao(None), {})
        self.assertEqual(rve_importacao._carregar_reconciliacao(""), {})

    def test_artefato_informado_e_inexistente_interrompe_a_rodada(self):
        with self.assertRaises(rve_importacao.ErroDaImportacaoRVE):
            rve_importacao._carregar_reconciliacao("caminho/que/nao/existe.csv")


class ImportacaoRVETests(TestCase):
    """A rodada escreve o núcleo operacional e preserva o resto como linhagem."""

    def setUp(self):
        self.condominio = criar_condominio("RVE")
        self.gestao = criar_pessoa("gestao.rve@exemplo.test", "Gestão do RVE")

    # -- Simulação ---------------------------------------------------------

    def test_simulacao_nao_grava_nada(self):
        caminho = _escrever_matriz(
            [
                _linha("RVE-001", "demanda", "Trocar a bomba do recalque"),
                _linha("PRJ-1", "projeto", "Projeto do AVCB"),
            ]
        )

        resumo = rve_importacao.importar(
            condominio=self.condominio, matriz=caminho, simulado=True
        )

        self.assertEqual(resumo.selecionadas, 2)
        self.assertIsNone(resumo.lote)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 0)
        self.assertEqual(LoteImportacaoHistorica.objects.count(), 0)
        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(Projeto.objects.count(), 0)
        # O que a rodada faria é contado, mesmo sem gravar.
        self.assertEqual(resumo.criados["demanda"], 1)
        self.assertEqual(resumo.criados["projeto"], 1)

    # -- Escrita -----------------------------------------------------------

    def test_aplica_escreve_demandas_projeto_etapa_entrega_e_ativo(self):
        caminho = _escrever_matriz(
            [
                _linha(
                    "RVE-001",
                    "demanda",
                    "Trocar a bomba do recalque",
                    status="Em andamento",
                    prioridade="Alta",
                ),
                _linha("PRJ-1", "projeto", "Projeto do AVCB", status="Em andamento"),
                _linha("PRJ-1-E1", "etapa_projeto", "Levantamento", parent="PRJ-1"),
                _linha(
                    "PRJ-1-E1-D1",
                    "entrega_projeto",
                    "Relatório do levantamento",
                    parent="PRJ-1-E1",
                ),
                _linha("EQ-001", "ativo/equipamento", "Bomba de recalque"),
            ]
        )

        resumo = rve_importacao.importar(
            condominio=self.condominio,
            matriz=caminho,
            usuario=self.gestao,
            simulado=False,
        )

        self.assertEqual(resumo.falhas, [])
        demanda = Demanda.objects.get(condominio=self.condominio)
        self.assertEqual(demanda.titulo, "Trocar a bomba do recalque")
        self.assertEqual(demanda.status, StatusDemanda.EM_ANDAMENTO)
        self.assertEqual(demanda.prioridade, PrioridadeDemanda.ALTA)
        self.assertEqual(demanda.origem, OrigemDemanda.SISTEMA)

        projeto = Projeto.objects.get(condominio=self.condominio)
        self.assertEqual(projeto.identificacao, "PRJ-1")
        self.assertEqual(projeto.status, StatusProjeto.EM_ANDAMENTO)

        etapa = ProjetoItem.objects.get(projeto=projeto, tipo=TipoItemProjeto.ETAPA)
        self.assertIsNone(etapa.parent)
        entrega = ProjetoItem.objects.get(tipo=TipoItemProjeto.ENTREGA)
        self.assertEqual(entrega.parent_id, etapa.pk)
        self.assertEqual(entrega.projeto_id, projeto.pk)

        self.assertTrue(
            Equipamento.objects.filter(
                condominio=self.condominio, nome="Bomba de recalque"
            ).exists()
        )

        # Cada linha virou item da fonte, e toda linha escrita tem vínculo.
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 5)
        self.assertEqual(
            VinculoImportacaoHistorica.objects.filter(
                resultado=ResultadoImportacaoHistorica.CRIADO
            ).count(),
            5,
        )
        for item in ItemImportacaoHistorica.objects.all():
            self.assertEqual(
                item.estado_processamento,
                EstadoProcessamentoImportacao.PROCESSADO,
            )

    def test_segunda_rodada_nao_cria_nada_de_novo(self):
        caminho = _escrever_matriz(
            [
                _linha("RVE-001", "demanda", "Trocar a bomba do recalque"),
                _linha("EQ-001", "ativo/equipamento", "Bomba de recalque"),
            ]
        )

        rve_importacao.importar(
            condominio=self.condominio,
            matriz=caminho,
            usuario=self.gestao,
            simulado=False,
        )
        segundo = rve_importacao.importar(
            condominio=self.condominio,
            matriz=caminho,
            usuario=self.gestao,
            simulado=False,
        )

        self.assertEqual(segundo.itens_novos, 0)
        self.assertEqual(segundo.itens_existentes, 2)
        self.assertEqual(segundo.total_criado, 0)
        self.assertEqual(segundo.pulados, 2)
        self.assertEqual(Demanda.objects.count(), 1)
        self.assertEqual(Equipamento.objects.count(), 1)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 2)

    def test_demanda_abaixo_de_projeto_entra_no_vinculo_do_projeto(self):
        caminho = _escrever_matriz(
            [
                _linha("PRJ-1", "projeto", "Projeto do AVCB"),
                _linha(
                    "RVE-010",
                    "demanda",
                    "Renovar o AVCB",
                    parent="PRJ-1",
                ),
            ]
        )

        rve_importacao.importar(
            condominio=self.condominio,
            matriz=caminho,
            usuario=self.gestao,
            simulado=False,
        )

        demanda = Demanda.objects.get(titulo="Renovar o AVCB")
        self.assertEqual(ProjetoDemanda.objects.filter(demanda=demanda).count(), 1)

    # -- Consolidação por título -------------------------------------------

    def test_link_consolida_na_demanda_existente_pelo_titulo(self):
        existente = Demanda.objects.create(
            condominio=self.condominio,
            titulo="Atividade de manutenção da piscina",
            criado_por=self.gestao,
        )
        caminho = _escrever_matriz(
            [
                _linha(
                    "RVE-ATV-1",
                    "atividade/pendencia",
                    "Atividade de manutenção da piscina",
                    acao="LINK",
                )
            ]
        )

        resumo = rve_importacao.importar(
            condominio=self.condominio,
            matriz=caminho,
            usuario=self.gestao,
            simulado=False,
        )

        self.assertEqual(resumo.vinculados, 1)
        self.assertEqual(resumo.pendentes, 0)
        self.assertEqual(Demanda.objects.count(), 1)

        vinculo = VinculoImportacaoHistorica.objects.get(
            entidade_destino=EntidadeImportacaoHistorica.DEMANDA
        )
        self.assertEqual(vinculo.destino_id, existente.pk)
        self.assertEqual(vinculo.resultado, ResultadoImportacaoHistorica.CRIADO)
        self.assertFalse(vinculo.revisao_manual)

    def test_link_sem_titulo_correspondente_fica_pendente_e_nao_cria_demanda(self):
        caminho = _escrever_matriz(
            [
                _linha(
                    "RVE-ATV-2",
                    "atividade/pendencia",
                    "Atividade sem par na base",
                    acao="LINK",
                )
            ]
        )

        resumo = rve_importacao.importar(
            condominio=self.condominio,
            matriz=caminho,
            usuario=self.gestao,
            simulado=False,
        )

        self.assertEqual(resumo.vinculados, 0)
        self.assertEqual(resumo.pendentes, 1)
        # A matriz não marcou a linha para revisão; o vínculo é que ficou pendente.
        self.assertEqual(resumo.revisao, 0)
        self.assertEqual(Demanda.objects.count(), 0)
        vinculo = VinculoImportacaoHistorica.objects.get()
        self.assertIsNone(vinculo.destino_id)
        self.assertEqual(vinculo.resultado, ResultadoImportacaoHistorica.PENDENTE)
        self.assertTrue(vinculo.revisao_manual)

    # -- Linhagem ----------------------------------------------------------

    def test_tipo_fora_do_escopo_entra_como_linhagem_ignorada(self):
        caminho = _escrever_matriz(
            [
                _linha(
                    "DOC-1",
                    "documento",
                    "Contrato de elevadores",
                    revisao="sim",
                    motivo="Documento preservado como histórico.",
                )
            ]
        )

        resumo = rve_importacao.importar(
            condominio=self.condominio,
            matriz=caminho,
            usuario=self.gestao,
            simulado=False,
        )

        self.assertEqual(resumo.total_criado, 0)
        self.assertEqual(resumo.ignorados, 1)
        self.assertEqual(resumo.revisao, 1)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 1)
        vinculo = VinculoImportacaoHistorica.objects.get()
        self.assertEqual(vinculo.resultado, ResultadoImportacaoHistorica.IGNORADO)
        self.assertTrue(vinculo.revisao_manual)
        # O motivo da matriz é preservado no vínculo, para a decisão ser auditável.
        self.assertEqual(vinculo.observacao, "Documento preservado como histórico.")

    def test_linhagem_nao_gera_vinculo(self):
        caminho = _escrever_matriz(
            [_linha("LIN-1", "linhagem", "Rastreabilidade da fonte")]
        )

        resumo = rve_importacao.importar(
            condominio=self.condominio,
            matriz=caminho,
            usuario=self.gestao,
            simulado=False,
        )

        self.assertEqual(resumo.ignorados, 1)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 1)
        self.assertEqual(VinculoImportacaoHistorica.objects.count(), 0)

    # -- Falhas -----------------------------------------------------------------

    def test_entrega_sem_etapa_registra_falha_e_nao_derruba_a_rodada(self):
        caminho = _escrever_matriz(
            [
                _linha("PRJ-1", "projeto", "Projeto do AVCB"),
                _linha(
                    "PRJ-1-E9-D1",
                    "entrega_projeto",
                    "Entrega órfã",
                    parent="PRJ-1-E9",
                ),
                _linha("RVE-001", "demanda", "Trocar a bomba do recalque"),
            ]
        )

        resumo = rve_importacao.importar(
            condominio=self.condominio,
            matriz=caminho,
            usuario=self.gestao,
            simulado=False,
        )

        self.assertEqual(len(resumo.falhas), 1)
        self.assertEqual(resumo.falhas[0][0], "PRJ-1-E9-D1")
        self.assertFalse(ProjetoItem.objects.filter(nome="Entrega órfã").exists())
        # A linha seguinte foi processada normalmente.
        self.assertTrue(Demanda.objects.filter(titulo="Trocar a bomba do recalque").exists())

    # -- Recortes ----------------------------------------------------------

    def test_ids_recorta_a_rodada_sem_mudar_a_ordem(self):
        caminho = _escrever_matriz(
            [
                _linha("RVE-001", "demanda", "Primeira"),
                _linha("RVE-002", "demanda", "Segunda"),
                _linha("RVE-003", "demanda", "Terceira"),
            ]
        )

        resumo = rve_importacao.importar(
            condominio=self.condominio,
            matriz=caminho,
            usuario=self.gestao,
            simulado=False,
            ids=["RVE-003", "RVE-001"],
        )

        self.assertEqual(resumo.selecionadas, 2)
        self.assertEqual(
            sorted(Demanda.objects.values_list("titulo", flat=True)),
            ["Primeira", "Terceira"],
        )

    def test_limite_recorta_a_rodada(self):
        caminho = _escrever_matriz(
            [
                _linha("RVE-001", "demanda", "Primeira"),
                _linha("RVE-002", "demanda", "Segunda"),
            ]
        )

        resumo = rve_importacao.importar(
            condominio=self.condominio,
            matriz=caminho,
            usuario=self.gestao,
            simulado=False,
            limite=1,
        )

        self.assertEqual(resumo.selecionadas, 1)
        self.assertEqual(Demanda.objects.count(), 1)


class TraducaoDeVocabularioTests(SimpleTestCase):
    """O que a matriz declara vira o vocabulário do domínio, sem palpite."""

    def test_prioridade_e_situacao_da_demanda(self):
        self.assertEqual(
            rve_importacao._prioridade("Crítica"), PrioridadeDemanda.URGENTE
        )
        self.assertEqual(
            rve_importacao._prioridade("Média"), PrioridadeDemanda.NORMAL
        )
        self.assertEqual(
            rve_importacao._prioridade("algo não previsto"), PrioridadeDemanda.NORMAL
        )
        self.assertEqual(
            rve_importacao._status_demanda("Em andamento"), StatusDemanda.EM_ANDAMENTO
        )
        self.assertEqual(rve_importacao._status_demanda(""), "")

    def test_situacao_do_projeto_parte_de_em_estudo(self):
        self.assertEqual(
            rve_importacao._status_projeto("Concluído"), StatusProjeto.CONCLUIDO
        )
        self.assertEqual(
            rve_importacao._status_projeto(""), StatusProjeto.EM_ESTUDO
        )

    def test_escopo_de_escrita_e_o_nucleo_operacional(self):
        self.assertEqual(
            rve_importacao.TIPOS_ESCRITOS,
            {
                "demanda",
                "demanda (verificacao AVCB)",
                "atividade AVCB",
                "ocorrencia/seguranca",
                "projeto",
                "etapa_projeto",
                "entrega_projeto",
                "ativo/equipamento",
            },
        )

    def test_origem_do_lote_e_importacao(self):
        self.assertEqual(
            rve_importacao.CONTA_ORIGEM, "RVE"
        )
        self.assertEqual(
            rve_importacao.ENTIDADE_POR_TIPO["planejamento"],
            EntidadeImportacaoHistorica.DEMANDA,
        )
        self.assertEqual(
            rve_importacao.ENTIDADE_POR_TIPO["subitem_garantia"],
            EntidadeImportacaoHistorica.GARANTIA,
        )
        self.assertNotEqual(OrigemImportacaoHistorica.IMPORTACAO, "")


class NaoDuplicidadeTests(TestCase):
    """As regras de não duplicidade da carga, exercitadas linha a linha.

    Cada teste isola uma das regras que a especificação nomeia — identidade
    única do acervo, projeto AVCB único, planejamento que não vira demanda,
    cobrança que vincula sem duplicar, garantia que permanece subitem — e
    confere o que ficou no banco depois de uma segunda passagem.
    """

    def setUp(self):
        self.condominio = criar_condominio("RVE")
        self.gestao = criar_pessoa("gestao.rve@exemplo.test", "Gestão do RVE")

    def _importar(self, linhas, **extra):
        caminho = _escrever_matriz(linhas)
        return rve_importacao.importar(
            condominio=self.condominio,
            matriz=caminho,
            usuario=self.gestao,
            simulado=False,
            **extra,
        )

    def test_rve_historico_e_rve_n_geram_uma_demanda_cada(self):
        linhas = [
            _linha("RVE-001", "demanda", "Trocar a bomba do recalque"),
            _linha("RVE-N001", "demanda", "Ajustar o jardim da portaria"),
        ]

        self._importar(linhas)
        self._importar(linhas)

        self.assertEqual(Demanda.objects.count(), 2)
        self.assertEqual(
            Demanda.objects.filter(titulo="Trocar a bomba do recalque").count(), 1
        )
        self.assertEqual(
            Demanda.objects.filter(titulo="Ajustar o jardim da portaria").count(), 1
        )

    def test_projeto_avcb_e_unico_e_nao_duplica(self):
        linhas = [
            _linha("PRJ-AVCB-001", "projeto", "Projeto AVCB 2026"),
            _linha("AVCB-E1", "etapa_projeto", "Etapa 1", parent="PRJ-AVCB-001"),
            _linha("AVCB-T1", "entrega_projeto", "Entrega 1", parent="AVCB-E1"),
        ]

        self._importar(linhas)
        self._importar(linhas)

        projeto = Projeto.objects.get(identificacao="PRJ-AVCB-001")
        self.assertEqual(
            Projeto.objects.filter(identificacao="PRJ-AVCB-001").count(), 1
        )
        self.assertEqual(ProjetoItem.objects.filter(projeto=projeto).count(), 2)

        etapa = ProjetoItem.objects.get(projeto=projeto, nome="Etapa 1")
        entrega = ProjetoItem.objects.get(projeto=projeto, nome="Entrega 1")
        self.assertEqual(entrega.parent_id, etapa.pk)

    def test_planejamento_nao_vira_demanda_aberta(self):
        resumo = self._importar(
            [
                _linha(
                    "PL27-001",
                    "planejamento",
                    "Pintura da fachada em 2027",
                    acao="PLANNING_ONLY",
                )
            ]
        )

        self.assertEqual(resumo.total_criado, 0)
        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(
            resumo.detalhes[0].desfecho, rve_importacao.DESFECHO_PLANEJAMENTO
        )
        vinculo = VinculoImportacaoHistorica.objects.get()
        self.assertEqual(vinculo.resultado, ResultadoImportacaoHistorica.IGNORADO)

    def test_cobranca_vinculada_nao_cria_demanda_duplicada(self):
        existente = Demanda.objects.create(
            condominio=self.condominio,
            titulo="Cobrança de taxa condominial",
            criado_por=self.gestao,
        )

        resumo = self._importar(
            [
                _linha(
                    "RET-001",
                    "cobranca/retorno",
                    "Cobrança de taxa condominial",
                    acao="LINK",
                )
            ]
        )

        self.assertEqual(resumo.vinculados, 1)
        self.assertEqual(resumo.total_criado, 0)
        self.assertEqual(Demanda.objects.count(), 1)
        vinculo = VinculoImportacaoHistorica.objects.get()
        self.assertEqual(vinculo.destino_id, existente.pk)

    def test_garantia_da_piscina_permanece_subitem(self):
        resumo = self._importar(
            [
                _linha(
                    "PISC-G01",
                    "subitem_garantia",
                    "Garantia da bomba da piscina",
                    acao="LINK",
                )
            ]
        )

        self.assertEqual(resumo.total_criado, 0)
        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(resumo.detalhes[0].desfecho, rve_importacao.DESFECHO_PENDENTE)
        vinculo = VinculoImportacaoHistorica.objects.get()
        self.assertEqual(vinculo.entidade_destino, EntidadeImportacaoHistorica.GARANTIA)
        self.assertTrue(vinculo.revisao_manual)

    def test_registros_criados_ficam_com_prioridade_e_status_validos(self):
        resumo = self._importar(
            [
                _linha(
                    "RVE-001",
                    "demanda",
                    "Trocar a bomba do recalque",
                    status="Em andamento",
                    prioridade="Crítica",
                ),
                _linha(
                    "RVE-002",
                    "demanda",
                    "Pintar o salão de festas",
                    status="situação não prevista",
                    prioridade="peso não previsto",
                ),
            ]
        )

        self.assertEqual(resumo.falhas, [])
        prioridades = {valor for valor, _ in PrioridadeDemanda.choices}
        status_validos = {valor for valor, _ in StatusDemanda.choices}
        for demanda in Demanda.objects.all():
            self.assertIn(demanda.prioridade, prioridades)
            self.assertIn(demanda.status, status_validos)

        em_andamento = Demanda.objects.get(titulo="Trocar a bomba do recalque")
        self.assertEqual(em_andamento.status, StatusDemanda.EM_ANDAMENTO)
        self.assertEqual(em_andamento.prioridade, PrioridadeDemanda.URGENTE)

    def test_rastreabilidade_de_origem_liga_item_ao_destino(self):
        self._importar([_linha("RVE-001", "demanda", "Trocar a bomba do recalque")])

        item = ItemImportacaoHistorica.objects.get(identificador_externo="RVE-001")
        self.assertEqual(item.condominio, self.condominio)
        self.assertEqual(item.conta_origem, rve_importacao.CONTA_ORIGEM)
        self.assertEqual(item.origem, OrigemImportacaoHistorica.IMPORTACAO)
        self.assertEqual(
            item.estado_processamento, EstadoProcessamentoImportacao.PROCESSADO
        )

        demanda = Demanda.objects.get()
        vinculo = VinculoImportacaoHistorica.objects.get()
        self.assertEqual(vinculo.item_id, item.pk)
        self.assertEqual(vinculo.entidade_destino, EntidadeImportacaoHistorica.DEMANDA)
        self.assertEqual(vinculo.destino_id, demanda.pk)
        self.assertEqual(vinculo.identificador_destino, "RVE-001")


# ---------------------------------------------------------------------------
# Reconciliação declarada
# ---------------------------------------------------------------------------


class ReconciliacaoDeclaradaTests(TestCase):
    """A reconciliação declarada impede o CREATE do que já está materializado.

    A identidade da fonte não é a identidade do fato de negócio: parte do acervo
    já existe no domínio, materializada por outra rodada com outro identificador
    técnico. O que liga as duas coisas é a evidência escrita — nunca o título.
    """

    def setUp(self):
        self.condominio = criar_condominio("RVE")
        self.gestao = criar_pessoa("gestao.rve@exemplo.test", "Gestão do RVE")

    def _rodar(self, linhas, decisoes, *, simulado=False, **extra):
        matriz = _escrever_matriz(linhas)
        reconciliacao = _escrever_reconciliacao(decisoes)
        return rve_importacao.importar(
            condominio=self.condominio,
            matriz=matriz,
            usuario=self.gestao,
            simulado=simulado,
            reconciliacao=reconciliacao,
            **extra,
        )

    def _demanda_existente(self, titulo):
        return Demanda.objects.create(
            condominio=self.condominio, titulo=titulo, criado_por=self.gestao
        )

    # -- Sem equivalência: segue criando -----------------------------------

    def test_registro_sem_equivalencia_continua_criavel(self):
        resumo = self._rodar(
            [_linha("RVE-100", "demanda", "Trocar a bomba do recalque")], []
        )

        self.assertEqual(resumo.detalhes[0].desfecho, rve_importacao.DESFECHO_CRIADO)
        self.assertEqual(resumo.total_criado, 1)
        self.assertEqual(Demanda.objects.count(), 1)

    def test_decisao_create_no_artefato_segue_o_comportamento_da_matriz(self):
        resumo = self._rodar(
            [_linha("RVE-101", "demanda", "Instalar o interfone")],
            [_decisao("RVE-101", "CREATE")],
        )

        self.assertEqual(resumo.detalhes[0].desfecho, rve_importacao.DESFECHO_CRIADO)
        self.assertEqual(Demanda.objects.count(), 1)

    # -- Equivalência comprovada: não cria ---------------------------------

    def test_equivalencia_declarada_vincular_existente_nao_cria_nova_demanda(self):
        existente = self._demanda_existente(
            "Compra Plafon Quadrado Led Bulbo p/ entrada Play"
        )
        resumo = self._rodar(
            [
                _linha(
                    "RVE-055",
                    "demanda",
                    "Luminária plafon e lâmpadas para entrada do playground",
                    revisao="sim",
                )
            ],
            [
                _decisao(
                    "RVE-055",
                    "VINCULAR_EXISTENTE",
                    destino_id=str(existente.pk),
                    observacao="Vínculo de importação anterior comprova a equivalência.",
                )
            ],
        )

        self.assertEqual(resumo.falhas, [])
        self.assertEqual(
            resumo.detalhes[0].desfecho, rve_importacao.DESFECHO_VINCULADO_EXISTENTE
        )
        self.assertEqual(resumo.total_criado, 0)
        self.assertEqual(Demanda.objects.count(), 1)
        self.assertEqual(resumo.detalhes[0].destino, str(existente.pk))

        vinculo = VinculoImportacaoHistorica.objects.get()
        self.assertEqual(vinculo.destino_id, existente.pk)
        self.assertEqual(vinculo.resultado, ResultadoImportacaoHistorica.CRIADO)
        self.assertEqual(
            vinculo.observacao,
            "Vínculo de importação anterior comprova a equivalência.",
        )

    def test_destino_declarado_inexistente_falha_sem_criar(self):
        resumo = self._rodar(
            [_linha("RVE-055", "demanda", "Luminária plafon do playground")],
            [
                _decisao(
                    "RVE-055",
                    "VINCULAR_EXISTENTE",
                    destino_id=str(UUID(int=0)),
                )
            ],
        )

        self.assertEqual(resumo.detalhes[0].desfecho, rve_importacao.DESFECHO_FALHA)
        self.assertEqual(len(resumo.falhas), 1)
        self.assertEqual(Demanda.objects.count(), 0)

    # -- Jornada relacionada: não destrutivo -------------------------------

    def test_jornada_relacionada_nao_cria_nem_funde(self):
        atual = self._demanda_existente("Manutenção do portão interno")
        resumo = self._rodar(
            [_linha("RVE-014", "demanda", "Regular o portão interno")],
            [
                _decisao(
                    "RVE-014",
                    "VINCULAR_JORNADA",
                    observacao="Mesma jornada do portão interno; evento distinto.",
                )
            ],
        )

        self.assertEqual(
            resumo.detalhes[0].desfecho, rve_importacao.DESFECHO_VINCULADO_JORNADA
        )
        self.assertEqual(resumo.total_criado, 0)
        # Nenhuma demanda nova, e a atual continua exatamente como estava.
        self.assertEqual(Demanda.objects.count(), 1)
        self.assertTrue(Demanda.objects.filter(pk=atual.pk).exists())

        vinculo = VinculoImportacaoHistorica.objects.get()
        self.assertIsNone(vinculo.destino_id)
        self.assertEqual(vinculo.resultado, ResultadoImportacaoHistorica.PENDENTE)
        self.assertTrue(vinculo.revisao_manual)

    # -- Revisar: não cria automaticamente ---------------------------------

    def test_registro_em_revisar_nao_e_criado_automaticamente(self):
        resumo = self._rodar(
            [_linha("RVE-009", "demanda", "Cera para o portão externo", revisao="sim")],
            [_decisao("RVE-009", "REVISAR")],
        )

        self.assertEqual(resumo.detalhes[0].desfecho, rve_importacao.DESFECHO_REVISAR)
        self.assertEqual(resumo.total_criado, 0)
        self.assertEqual(Demanda.objects.count(), 0)
        self.assertEqual(resumo.revisao, 1)

        vinculo = VinculoImportacaoHistorica.objects.get()
        self.assertEqual(vinculo.resultado, ResultadoImportacaoHistorica.PENDENTE)
        self.assertTrue(vinculo.revisao_manual)
        self.assertIsNone(vinculo.destino_id)

    # -- Simulação e idempotência ------------------------------------------

    def test_simulacao_registra_a_decisao_sem_gravar_nada(self):
        existente = self._demanda_existente("Compra de Lâmpadas")
        resumo = self._rodar(
            [
                _linha("RVE-055", "demanda", "Luminária plafon do playground"),
                _linha("RVE-009", "demanda", "Cera para o portão externo"),
            ],
            [
                _decisao(
                    "RVE-055", "VINCULAR_EXISTENTE", destino_id=str(existente.pk)
                ),
                _decisao("RVE-009", "REVISAR"),
            ],
            simulado=True,
        )

        self.assertEqual(
            resumo.contar_desfecho(rve_importacao.DESFECHO_VINCULADO_EXISTENTE), 1
        )
        self.assertEqual(resumo.contar_desfecho(rve_importacao.DESFECHO_REVISAR), 1)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 0)
        self.assertEqual(VinculoImportacaoHistorica.objects.count(), 0)
        self.assertEqual(Demanda.objects.count(), 1)

    def test_rerun_reconciliado_nao_duplica_item_vinculo_nem_demanda(self):
        existente = self._demanda_existente("Compra de Lâmpadas")
        linhas = [
            _linha("RVE-055", "demanda", "Luminária plafon do playground"),
            _linha("RVE-009", "demanda", "Cera para o portão externo"),
        ]
        decisoes = [
            _decisao("RVE-055", "VINCULAR_EXISTENTE", destino_id=str(existente.pk)),
            _decisao("RVE-009", "REVISAR"),
        ]

        self._rodar(linhas, decisoes)
        segundo = self._rodar(linhas, decisoes)

        self.assertEqual(segundo.itens_novos, 0)
        self.assertEqual(segundo.itens_existentes, 2)
        self.assertEqual(segundo.total_criado, 0)
        self.assertEqual(Demanda.objects.count(), 1)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 2)
        self.assertEqual(VinculoImportacaoHistorica.objects.count(), 2)

    # -- Similaridade textual não é identidade -----------------------------

    def test_demanda_atual_sem_proveniencia_nao_e_absorvida_por_titulo(self):
        atual = self._demanda_existente(
            "Luminária plafon e lâmpadas para entrada do playground"
        )
        # A matriz traz a mesma descrição, e NÃO há reconciliação declarada: a
        # semelhança de título não autoriza tratar as duas como a mesma coisa.
        resumo = self._rodar(
            [
                _linha(
                    "RVE-055",
                    "demanda",
                    "Luminária plafon e lâmpadas para entrada do playground",
                )
            ],
            [],
        )

        self.assertEqual(resumo.detalhes[0].desfecho, rve_importacao.DESFECHO_CRIADO)
        self.assertEqual(Demanda.objects.count(), 2)
        self.assertTrue(Demanda.objects.filter(pk=atual.pk).exists())


# ---------------------------------------------------------------------------
# Fase 10 — amostra representativa
# ---------------------------------------------------------------------------

MATRIZ_REAL = Path(
    r"D:\EITHRA_RVE_IMPORTACAO\relatorios\reconciliacao_importacao_rve.csv"
)

# A amostra que a especificação nomeia, mais três identidades que fazem o ensaio
# cobrir documento e financeiro — cenários que a lista fixa não alcança sozinha.
AMOSTRA = (
    "RVE-001",
    "RVE-005",
    "RVE-018",
    "RVE-060",
    "EQ-001",
    "PISC-G01",
    "RET-001",
    "GES-001",
    "PL27-001",
    "PRJ-AVCB-001",
    "AVCB-D001",
    "FIN-AVCB-001",
    "PROP-AVCB-001",
)

# O desfecho que a rodada decide para cada identidade da amostra. Documento e
# financeiro entram como linhagem — a decisão de escopo desta rodada, não falha.
DESFECHO_ESPERADO = {
    "RVE-001": rve_importacao.DESFECHO_CRIADO,
    "RVE-005": rve_importacao.DESFECHO_CRIADO,
    "RVE-018": rve_importacao.DESFECHO_CRIADO,
    "RVE-060": rve_importacao.DESFECHO_CRIADO,
    "EQ-001": rve_importacao.DESFECHO_CRIADO,
    "PISC-G01": rve_importacao.DESFECHO_PENDENTE,
    "RET-001": rve_importacao.DESFECHO_REVISAO,
    "GES-001": rve_importacao.DESFECHO_CRIADO,
    "PL27-001": rve_importacao.DESFECHO_PLANEJAMENTO,
    "PRJ-AVCB-001": rve_importacao.DESFECHO_CRIADO,
    "AVCB-D001": rve_importacao.DESFECHO_IGNORADO,
    "FIN-AVCB-001": rve_importacao.DESFECHO_IGNORADO,
    "PROP-AVCB-001": rve_importacao.DESFECHO_HISTORICO,
}


@unittest.skipUnless(MATRIZ_REAL.is_file(), "matriz real do RVE ausente no disco")
class AmostraRepresentativaTests(TestCase):
    """Ensaio da amostra da Fase 10 — a transação do teste é o rollback."""

    def setUp(self):
        self.condominio = criar_condominio("RVE")
        self.gestao = criar_pessoa("gestao.rve@exemplo.test", "Gestão do RVE")

    def _rodar(self):
        return rve_importacao.importar(
            condominio=self.condominio,
            matriz=MATRIZ_REAL,
            usuario=self.gestao,
            simulado=False,
            ids=list(AMOSTRA),
        )

    def test_amostra_cobre_os_cenarios_e_acerta_o_desfecho_de_cada_linha(self):
        resumo = self._rodar()

        self.assertEqual(resumo.falhas, [])
        self.assertEqual(resumo.selecionadas, len(AMOSTRA))
        for linha in resumo.detalhes:
            self.assertEqual(
                linha.desfecho,
                DESFECHO_ESPERADO[linha.external_id],
                f"{linha.external_id} terminou como {linha.desfecho}",
            )

        # Demanda simples e com garantia, ocorrência de segurança: cinco demandas.
        self.assertEqual(
            Demanda.objects.filter(condominio=self.condominio).count(), 5
        )
        projeto = Projeto.objects.get(condominio=self.condominio)
        self.assertEqual(projeto.identificacao, "PRJ-AVCB-001")
        self.assertEqual(Equipamento.objects.filter(condominio=self.condominio).count(), 1)

        # A garantia não vira registro nesta rodada; o vínculo fica pendente.
        vinculo = VinculoImportacaoHistorica.objects.get(
            entidade_destino=EntidadeImportacaoHistorica.GARANTIA
        )
        self.assertEqual(vinculo.resultado, ResultadoImportacaoHistorica.PENDENTE)
        self.assertIsNone(vinculo.destino_id)

    def test_segunda_rodada_da_amostra_nao_cria_nada_de_novo(self):
        self._rodar()
        segundo = self._rodar()

        self.assertEqual(segundo.total_criado, 0)
        self.assertEqual(segundo.itens_novos, 0)
        self.assertEqual(segundo.itens_existentes, len(AMOSTRA))
        # Só as sete linhas que escreveram encontraram destino já criado.
        self.assertEqual(segundo.pulados, 7)
        self.assertEqual(Demanda.objects.filter(condominio=self.condominio).count(), 5)
        self.assertEqual(Projeto.objects.filter(condominio=self.condominio).count(), 1)
