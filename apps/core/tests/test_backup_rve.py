"""Testes do ponto de recuperação do RVE.

O ponto de recuperação é o que autoriza a carga definitiva: antes de gravar, a
fotografia do que existe; depois, o caminho de volta. Os testes daqui provam o
mais importante — que a restauração devolve o condomínio exatamente ao estado
anterior, apagando só o que a carga criou.
"""
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from apps.cadastros.models import Equipamento
from apps.cadastros.tests.apoio import criar_condominio, criar_pessoa
from apps.core import rve_importacao
from apps.core.models import (
    ItemImportacaoHistorica,
    LoteImportacaoHistorica,
    VinculoImportacaoHistorica,
)
from apps.core.tests.test_importacao_rve import _escrever_matriz, _linha
from apps.demandas.models import Demanda
from apps.governanca.models import Projeto


class PontoDeRecuperacaoTests(TestCase):
    """Exportar e restaurar devolvem o condomínio ao estado anterior."""

    def setUp(self):
        self.condominio = criar_condominio("RVE")
        self.gestao = criar_pessoa("gestao.rve@exemplo.test", "Gestão do RVE")
        self.demanda_previa = Demanda.objects.create(
            condominio=self.condominio,
            titulo="Demanda anterior à carga",
            criado_por=self.gestao,
        )
        self.fotografia = (
            Path(tempfile.mkdtemp(prefix="rve-backup-")) / "backup.json"
        )

    def _exportar(self):
        return call_command(
            "backup_rve",
            "--condominio",
            str(self.condominio.pk),
            "--exportar",
            "--saida",
            str(self.fotografia),
            stdout=StringIO(),
        )

    def _restaurar(self):
        return call_command(
            "backup_rve",
            "--condominio",
            str(self.condominio.pk),
            "--restaurar",
            str(self.fotografia),
            stdout=StringIO(),
        )

    def test_exportacao_guarda_os_registros_do_condominio(self):
        self._exportar()

        self.assertTrue(self.fotografia.is_file())
        conteudo = self.fotografia.read_text(encoding="utf-8")
        # É um arquivo de loaddata: um vetor de objetos com o modelo declarado.
        self.assertIn('"model": "demandas.demanda"', conteudo)
        self.assertIn("Demanda anterior à carga", conteudo)

    def test_restauracao_desfaz_a_carga_e_devolve_o_estado_anterior(self):
        self._exportar()
        caminho = _escrever_matriz(
            [
                _linha("RVE-001", "demanda", "Demanda importada"),
                _linha("PRJ-1", "projeto", "Projeto importado"),
                _linha("PRJ-1-E1", "etapa_projeto", "Etapa importada", parent="PRJ-1"),
                _linha("EQ-001", "ativo/equipamento", "Ativo importado"),
            ]
        )
        rve_importacao.importar(
            condominio=self.condominio,
            matriz=caminho,
            usuario=self.gestao,
            simulado=False,
        )
        self.assertEqual(Demanda.objects.filter(condominio=self.condominio).count(), 2)
        self.assertEqual(Projeto.objects.count(), 1)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 4)

        self._restaurar()

        # A demanda anterior permanece; tudo o que a carga criou, não.
        self.assertEqual(Demanda.objects.filter(condominio=self.condominio).count(), 1)
        self.assertTrue(Demanda.objects.filter(pk=self.demanda_previa.pk).exists())
        self.assertEqual(Projeto.objects.count(), 0)
        self.assertEqual(Equipamento.objects.count(), 0)
        self.assertEqual(ItemImportacaoHistorica.objects.count(), 0)
        self.assertEqual(LoteImportacaoHistorica.objects.count(), 0)
        self.assertEqual(VinculoImportacaoHistorica.objects.count(), 0)

    def test_restaurar_exige_arquivo_existente(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command(
                "backup_rve",
                "--condominio",
                str(self.condominio.pk),
                "--restaurar",
                str(self.fotografia),
                stdout=StringIO(),
            )
