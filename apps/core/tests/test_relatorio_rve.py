"""Testes do relatório da importação definitiva do RVE.

O relatório não decide nada: ele lê o que a rodada fez e devolve o balanço, as
conferências obrigatórias e os conflitos. Os testes daqui montam resumos
pequenos — sem banco, sem matriz real — e conferem que cada número vem da linha
certa e que uma divergência aparece com o identificador que a explica.
"""
import csv
import tempfile
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.core import rve_relatorio
from apps.core.rve_importacao import DesfechoDaLinha, ResumoDaImportacao


def _linha(
    external_id,
    tipo,
    *,
    familia=None,
    parent="",
    acao="DO_NOT_CREATE",
    desfecho="SKIP",
    revisao=False,
    motivo="",
    destino="",
):
    """Um desfecho da rodada com só o que o teste precisa declarar."""
    return DesfechoDaLinha(
        external_id=external_id,
        familia=familia or tipo,
        tipo_entidade=tipo,
        titulo=f"Item {external_id}",
        parent=parent,
        acao=acao,
        desfecho=desfecho,
        destino=destino,
        motivo=motivo,
        revisao=revisao,
    )


def _resumo(*linhas, falhas=(), simulado=True):
    """Um resumo da rodada com o mínimo que o relatório lê."""
    return ResumoDaImportacao(
        condominio=SimpleNamespace(nome="Condomínio de Teste", pk="00000000"),
        matriz=Path("reconciliacao.csv"),
        simulado=simulado,
        lidas=len(linhas),
        selecionadas=len(linhas),
        itens=len(linhas),
        detalhes=list(linhas),
        falhas=list(falhas),
    )


def _por_nome(conferencias):
    return {item.nome: item for item in conferencias}


class ConferenciaTests(SimpleTestCase):
    """Uma conferência sem número esperado nunca diverge."""

    def test_sem_expectativa_esta_confirmada(self):
        self.assertTrue(rve_relatorio.Conferencia(nome="x", encontrado=5).confirmada)

    def test_com_expectativa_diverge_quando_o_numero_difere(self):
        item = rve_relatorio.Conferencia(nome="x", encontrado=76, esperado=77)
        self.assertFalse(item.confirmada)

    def test_com_expectativa_confirma_quando_o_numero_bate(self):
        item = rve_relatorio.Conferencia(nome="x", encontrado=77, esperado=77)
        self.assertTrue(item.confirmada)


class ConferenciasTests(SimpleTestCase):
    """As conferências contam a matriz, não a expectativa."""

    def test_demandas_historicas_confirmam_com_sete_sete(self):
        linhas = [_linha(f"RVE-{n:03d}", "demanda") for n in range(1, 78)]

        conferencias = _por_nome(rve_relatorio.conferir(_resumo(*linhas)))

        item = conferencias["Demandas históricas sem duplicação"]
        self.assertEqual(item.encontrado, 77)
        self.assertTrue(item.confirmada)

    def test_demanda_faltando_diverge_e_lista_os_identificadores(self):
        linhas = [_linha(f"RVE-{n:03d}", "demanda") for n in range(1, 77)]

        item = _por_nome(rve_relatorio.conferir(_resumo(*linhas)))[
            "Demandas históricas sem duplicação"
        ]

        self.assertEqual(item.encontrado, 76)
        self.assertFalse(item.confirmada)

    def test_projetos_conta_apenas_o_tipo_projeto(self):
        linhas = [
            _linha("PRJ-1", "projeto"),
            _linha("PRJ-AVCB-001", "projeto"),
            _linha("PRJ-1-E1", "etapa_projeto", parent="PRJ-1"),
        ]

        item = _por_nome(rve_relatorio.conferir(_resumo(*linhas)))[
            "Projetos identificados"
        ]

        self.assertEqual(item.encontrado, 2)
        self.assertFalse(item.confirmada)

    def test_ges_conta_a_familia_e_nao_o_tipo(self):
        linhas = [
            _linha("GES-001", "ocorrencia/seguranca", familia="GES"),
            _linha("GES-004", "ocorrencia/seguranca", familia="GES"),
        ]

        item = _por_nome(rve_relatorio.conferir(_resumo(*linhas)))[
            "Ocorrências de segurança (GES) reconciliadas"
        ]

        self.assertEqual(item.encontrado, 2)
        self.assertTrue(item.confirmada)

    def test_ges_com_falha_aponta_o_identificador(self):
        linhas = [
            _linha("GES-001", "ocorrencia/seguranca", familia="GES"),
            _linha(
                "GES-002",
                "ocorrencia/seguranca",
                familia="GES",
                desfecho="ERROR",
            ),
        ]

        item = _por_nome(rve_relatorio.conferir(_resumo(*linhas)))[
            "Ocorrências de segurança (GES) reconciliadas"
        ]

        self.assertIn("GES-002", item.divergentes)

    def test_avcb_preservado_conta_etapas_e_entregas_com_pai_valido(self):
        linhas = [
            _linha("PRJ-AVCB-001", "projeto", familia="PRJ-AVCB"),
            _linha(
                "AVCB-E1",
                "etapa_projeto",
                familia="AVCB-E",
                parent="PRJ-AVCB-001",
            ),
            _linha(
                "AVCB-E1-D1",
                "entrega_projeto",
                familia="AVCB-T",
                parent="AVCB-E1",
            ),
        ]

        item = _por_nome(rve_relatorio.conferir(_resumo(*linhas)))[
            "AVCB preservado hierarquicamente"
        ]

        self.assertEqual(item.encontrado, 2)
        self.assertEqual(item.divergentes, ())

    def test_avcb_com_pai_ausente_aponta_a_linha_quebrada(self):
        linhas = [
            _linha(
                "AVCB-E9",
                "etapa_projeto",
                familia="AVCB-E",
                parent="PRJ-QUE-NAO-EXISTE",
            )
        ]

        item = _por_nome(rve_relatorio.conferir(_resumo(*linhas)))[
            "AVCB preservado hierarquicamente"
        ]

        self.assertEqual(item.divergentes, ("AVCB-E9",))


class ConflitosTests(SimpleTestCase):
    """O conflito carrega o identificador e o motivo que o explicam."""

    def test_falha_da_rodada_vira_conflito_com_familia_e_tipo(self):
        linhas = [
            _linha("PRJ-1-E9-D1", "entrega_projeto", familia="AVCB-T"),
        ]

        conflitos = rve_relatorio.conflitos(
            _resumo(*linhas, falhas=[("PRJ-1-E9-D1", "Sem etapa superior.")])
        )

        self.assertEqual(
            conflitos,
            [("PRJ-1-E9-D1", "AVCB-T", "entrega_projeto", "Sem etapa superior.")],
        )

    def test_etapa_com_pai_ausente_e_conflito(self):
        linhas = [_linha("AVCB-E1", "etapa_projeto", familia="AVCB-E", parent="PRJ-X")]

        conflitos = rve_relatorio.conflitos(_resumo(*linhas))

        self.assertEqual(len(conflitos), 1)
        self.assertEqual(conflitos[0][0], "AVCB-E1")
        self.assertIn("PRJ-X", conflitos[0][3])

    def test_entrega_com_pai_que_nao_e_etapa_e_conflito(self):
        linhas = [
            _linha("PRJ-1", "projeto", familia="PRJ"),
            _linha(
                "PRJ-1-D1",
                "entrega_projeto",
                familia="AVCB-T",
                parent="PRJ-1",
            ),
        ]

        conflitos = rve_relatorio.conflitos(_resumo(*linhas))

        self.assertEqual(len(conflitos), 1)
        self.assertEqual(conflitos[0][0], "PRJ-1-D1")
        self.assertIn("projeto", conflitos[0][3])

    def test_hierarquia_inteira_nao_gera_conflito(self):
        linhas = [
            _linha("PRJ-1", "projeto", familia="PRJ"),
            _linha("PRJ-1-E1", "etapa_projeto", familia="AVCB-E", parent="PRJ-1"),
            _linha(
                "PRJ-1-E1-D1",
                "entrega_projeto",
                familia="AVCB-T",
                parent="PRJ-1-E1",
            ),
        ]

        self.assertEqual(rve_relatorio.conflitos(_resumo(*linhas)), [])


class RelatorioTests(SimpleTestCase):
    """O relatório reúne as duas leituras e grava os três artefatos."""

    def _relatorio(self):
        linhas = [
            _linha("RVE-001", "demanda", desfecho="CREATE"),
            _linha("DOC-1", "documento", desfecho="SKIP", revisao=True),
        ]
        return rve_relatorio.montar(_resumo(*linhas))

    def test_montar_reune_conferencias_e_conflitos(self):
        relatorio = self._relatorio()

        self.assertEqual(len(relatorio.conferencias), 9)
        self.assertEqual(relatorio.conflitos, [])

    def test_texto_traz_o_cabecalho_e_as_secoes(self):
        texto = rve_relatorio.texto(self._relatorio())

        self.assertIn("IMPORTAÇÃO DEFINITIVA RVE — RELATÓRIO DE DRY-RUN", texto)
        self.assertIn("POR TIPO", texto)
        self.assertIn("TOTAIS POR DESFECHO", texto)
        self.assertIn("CONFERÊNCIAS OBRIGATÓRIAS", texto)
        self.assertIn("CONFLITOS", texto)
        self.assertIn("DIVERGE", texto)

    def test_texto_de_carga_executada_muda_o_titulo(self):
        relatorio = rve_relatorio.montar(
            _resumo(_linha("RVE-001", "demanda", desfecho="CREATE"), simulado=False)
        )

        self.assertIn("DA CARGA EXECUTADA", rve_relatorio.texto(relatorio))

    def test_escrever_grava_os_tres_artefatos(self):
        diretorio = Path(tempfile.mkdtemp(prefix="rve-relatorio-"))

        caminhos = rve_relatorio.escrever(self._relatorio(), diretorio=diretorio)

        self.assertEqual(
            set(caminhos), {"resumo", "detalhes", "conflitos"}
        )
        for caminho in caminhos.values():
            self.assertTrue(caminho.is_file())

        with caminhos["detalhes"].open(encoding="utf-8-sig", newline="") as arquivo:
            linhas = list(csv.DictReader(arquivo))
        self.assertEqual(len(linhas), 2)
        self.assertEqual(linhas[0]["external_id"], "RVE-001")
        self.assertEqual(linhas[0]["desfecho"], "CREATE")

        with caminhos["conflitos"].open(encoding="utf-8-sig", newline="") as arquivo:
            self.assertEqual(list(csv.DictReader(arquivo)), [])

    def test_escrever_registra_a_revisao_no_detalhe(self):
        diretorio = Path(tempfile.mkdtemp(prefix="rve-relatorio-"))

        caminhos = rve_relatorio.escrever(self._relatorio(), diretorio=diretorio)

        with caminhos["detalhes"].open(encoding="utf-8-sig", newline="") as arquivo:
            linhas = list(csv.DictReader(arquivo))
        self.assertEqual(linhas[1]["revisao_necessaria"], "sim")


class DesfechoDaLinhaTests(SimpleTestCase):
    """O desfecho guarda o que o relatório precisa ler sem reabrir a fonte."""

    def test_a_linha_guarda_o_motivo_da_matriz(self):
        linha = _linha(
            "DOC-1",
            "documento",
            desfecho="SKIP",
            motivo="Documento preservado como histórico.",
        )

        self.assertEqual(linha.motivo, "Documento preservado como histórico.")
        self.assertEqual(linha.desfecho, "SKIP")
