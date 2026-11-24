"""Ponto de recuperação dos registros do RVE, antes e depois da carga.

A carga definitiva é aditiva e rastreável: tudo o que ela cria fica pendurado na
linhagem — lote, item e vínculo —, com a conta de origem declarada. Este comando
usa esse rastro em duas direções:

* **exporta** o estado atual dos registros que a carga pode tocar, num arquivo
  que o ``loaddata`` recarrega — a fotografia anterior à importação;
* **restaura** esse estado: apaga o que a carga criou, pelo rastro da linhagem,
  e recarrega a fotografia, devolvendo o condomínio a como estava.

Nenhuma senha entra no arquivo: ele guarda dados, não conexão. E o export só lê —
quem grava é quem restaura, dentro de uma transação única.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from apps.core.constants import OrigemImportacaoHistorica
from apps.core.management.commands import sincronizar_gmail as leitura

# Onde os pontos de recuperação ficam, quando o caminho não é declarado.
PASTA_PADRAO = Path(r"D:\EITHRA_RVE_IMPORTACAO\backups")


class Command(BaseCommand):
    """Exporta ou restaura os registros do RVE impactados pela importação."""

    help = "Produz o ponto de recuperação dos registros do RVE (export/restore)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--condominio",
            required=True,
            help="Identificador do condomínio cujos registros serão exportados.",
        )
        grupo = parser.add_mutually_exclusive_group(required=True)
        grupo.add_argument(
            "--exportar",
            action="store_true",
            help="Lê o estado atual e grava a fotografia num arquivo JSON.",
        )
        grupo.add_argument(
            "--restaurar",
            metavar="ARQUIVO",
            help="Desfaz a carga e recarrega a fotografia indicada.",
        )
        parser.add_argument(
            "--saida",
            default="",
            help="Caminho do arquivo a gravar. Por omissão, em backups do RVE.",
        )

    def handle(self, *args, **opcoes):
        condominio = leitura.Command._resolver_condominio(opcoes["condominio"])
        if opcoes["restaurar"]:
            self._restaurar(condominio, Path(opcoes["restaurar"]))
            return
        self._exportar(condominio, opcoes["saida"])

    # -- Export ------------------------------------------------------------

    def _exportar(self, condominio, saida: str) -> None:
        from django.core import serializers

        caminho = (
            Path(saida)
            if saida
            else PASTA_PADRAO / f"backup_pre_importacao_rve_{timezone.localdate():%Y%m%d}.json"
        )
        caminho.parent.mkdir(parents=True, exist_ok=True)

        disponiveis = set(connection.introspection.table_names())
        objetos, resumo = [], []
        for rotulo, consulta in self._impactados(condominio):
            if consulta.model._meta.db_table not in disponiveis:
                resumo.append((rotulo, None))
                continue
            linhas = list(consulta)
            objetos.extend(linhas)
            resumo.append((rotulo, len(linhas)))

        caminho.write_text(
            serializers.serialize("json", objetos, indent=1), encoding="utf-8"
        )

        self.stdout.write("Ponto de recuperação do RVE")
        self.stdout.write(f"Condomínio: {condominio.nome} ({condominio.pk})")
        self.stdout.write(f"Arquivo: {caminho}")
        self.stdout.write(f"Registros: {len(objetos)}")
        for rotulo, quantidade in resumo:
            marca = "sem tabela" if quantidade is None else quantidade
            self.stdout.write(f"  - {rotulo}: {marca}")
        self.stdout.write(
            "Para restaurar: "
            f"manage.py backup_rve --condominio {condominio.pk} --restaurar \"{caminho}\""
        )

    def _impactados(self, condominio):
        """As consultas que cobrem o que a carga pode tocar neste condomínio."""
        from apps.cadastros.models import Equipamento
        from apps.core.models import (
            ItemImportacaoHistorica,
            LoteImportacaoHistorica,
            VinculoImportacaoHistorica,
        )
        from apps.demandas.models import Demanda, EvidenciaDemanda, HistoricoDemanda
        from apps.governanca.models import Projeto, ProjetoDemanda, ProjetoItem

        return [
            (
                "core.LoteImportacaoHistorica",
                LoteImportacaoHistorica.objects.filter(condominio=condominio),
            ),
            (
                "core.ItemImportacaoHistorica",
                ItemImportacaoHistorica.objects.filter(condominio=condominio),
            ),
            (
                "core.VinculoImportacaoHistorica",
                VinculoImportacaoHistorica.objects.filter(item__condominio=condominio),
            ),
            ("demandas.Demanda", Demanda.objects.filter(condominio=condominio)),
            (
                "demandas.HistoricoDemanda",
                HistoricoDemanda.objects.filter(demanda__condominio=condominio),
            ),
            (
                "demandas.EvidenciaDemanda",
                EvidenciaDemanda.objects.filter(demanda__condominio=condominio),
            ),
            (
                "cadastros.Equipamento",
                Equipamento.objects.filter(condominio=condominio),
            ),
            ("governanca.Projeto", Projeto.objects.filter(condominio=condominio)),
            (
                "governanca.ProjetoItem",
                ProjetoItem.objects.filter(projeto__condominio=condominio),
            ),
            (
                "governanca.ProjetoDemanda",
                ProjetoDemanda.objects.filter(projeto__condominio=condominio),
            ),
        ]

    # -- Restore -----------------------------------------------------------

    def _restaurar(self, condominio, caminho: Path) -> None:
        if not caminho.is_file():
            raise CommandError(f"A fotografia informada não foi encontrada: {caminho}")

        with transaction.atomic():
            apagados = self._apagar_carga(condominio)
            call_command("loaddata", str(caminho), verbosity=0)

        self.stdout.write("Restauração do RVE")
        self.stdout.write(f"Condomínio: {condominio.nome} ({condominio.pk})")
        self.stdout.write(f"Fotografia: {caminho}")
        for rotulo, quantidade in apagados:
            self.stdout.write(f"  - {rotulo} apagados: {quantidade}")
        self.stdout.write("Estado anterior restaurado.")

    def _apagar_carga(self, condominio):
        """Apaga o que a carga criou, seguindo o rastro da linhagem."""
        from apps.cadastros.models import Equipamento
        from apps.core.constants import EntidadeImportacaoHistorica
        from apps.core.models import (
            ItemImportacaoHistorica,
            LoteImportacaoHistorica,
            VinculoImportacaoHistorica,
        )
        from apps.core.rve_importacao import CONTA_ORIGEM
        from apps.demandas.models import Demanda
        from apps.governanca.models import Projeto, ProjetoItem

        modelos = {
            EntidadeImportacaoHistorica.DEMANDA: Demanda,
            EntidadeImportacaoHistorica.PROJETO: Projeto,
            EntidadeImportacaoHistorica.PROJETO_ITEM: ProjetoItem,
            EntidadeImportacaoHistorica.EQUIPAMENTO: Equipamento,
        }

        itens = ItemImportacaoHistorica.objects.filter(
            condominio=condominio,
            origem=OrigemImportacaoHistorica.IMPORTACAO,
            conta_origem=CONTA_ORIGEM,
        )
        vinculos = VinculoImportacaoHistorica.objects.filter(item__in=itens)

        por_entidade = defaultdict(set)
        for vinculo in vinculos:
            if vinculo.destino_id and vinculo.entidade_destino in modelos:
                por_entidade[vinculo.entidade_destino].add(vinculo.destino_id)

        apagados = []
        # Os itens de projeto saem antes do projeto, e o projeto antes da
        # demanda: a ligação projeto-demanda é protegida, e apagar o projeto a
        # leva junto por cascata, liberando a demanda.
        for entidade in (
            EntidadeImportacaoHistorica.PROJETO_ITEM,
            EntidadeImportacaoHistorica.PROJETO,
            EntidadeImportacaoHistorica.DEMANDA,
            EntidadeImportacaoHistorica.EQUIPAMENTO,
        ):
            identificadores = por_entidade.get(entidade)
            if not identificadores:
                continue
            quantidade = modelos[entidade].objects.filter(pk__in=identificadores).delete()[0]
            apagados.append((entidade.label, quantidade))

        apagados.append(("vinculos", vinculos.delete()[0]))
        apagados.append(("itens", itens.delete()[0]))
        apagados.append(
            (
                "lotes",
                LoteImportacaoHistorica.objects.filter(
                    condominio=condominio,
                    origem=OrigemImportacaoHistorica.IMPORTACAO,
                    conta_origem=CONTA_ORIGEM,
                ).delete()[0],
            )
        )
        return apagados
