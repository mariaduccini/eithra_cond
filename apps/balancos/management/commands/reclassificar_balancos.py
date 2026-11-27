"""Reclassifica as categorias dos balanços já confirmados.

A regra de categorização do Eithra mudou e os meses confirmados antes da mudança
continuam com a leitura antiga. Este comando relê a descrição de cada lançamento
com a regra vigente e atualiza apenas o que a máquina classificou.

O que ele **não** faz, de propósito:

* não toca no lançamento que a gestão já corrigiu à mão — a decisão humana vence
  a regra;
* não mexe nos totais oficiais do documento (receitas e despesas declaradas) —
  o que se recategoriza é o detalhamento;
* sem ``--aplicar``, não grava nada: só mostra, mês a mês, de que categoria cada
  lançamento sairia e para qual iria.

A gravação reaproveita o caminho já usado pela correção manual do balanço, para
que o detalhamento e o resumo da listagem sejam recalculados do mesmo jeito.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.balancos import correcoes, services
from apps.balancos.categorias import categoria_para, rotulo
from apps.balancos.constants import StatusBalanco
from apps.balancos.contratos import de_dicionario_validado
from apps.balancos.models import BalancoMensal
from apps.balancos.normalizadores import texto_para_decimal

# O motivo que fica no histórico do balanço. A data entra na hora de gravar.
MOTIVO_DA_REVISAO = "Reclassificação automática de categorias em {data}"


def reclassificar_leitura(leitura: dict, *, condominio=None) -> list[dict]:
    """Relê a categoria de cada lançamento que a gestão não corrigiu à mão.

    Devolve, em ordem, o que mudaria: descrição, categoria antiga, nova e valor.
    O lançamento corrigido à mão fica de fora — aí quem classificou foi a
    gestão, e a regra não passa por cima. A releitura usa as mesmas pistas da
    leitura: a regra que o condomínio já ensinou, o fornecedor com contrato, as
    palavras da descrição e, por último, o grupo do documento. Nada é gravado:
    quem decide é o comando.
    """
    mudancas = []
    for despesa in leitura.get("despesas") or []:
        correcao = despesa.get("correcao") or {}
        if correcao.get("categoria_eithra"):
            continue

        antiga = despesa.get("categoria_eithra")
        nova = categoria_para(
            condominio,
            descricao=despesa.get("descricao_original") or "",
            categoria_original=despesa.get("categoria_original"),
            fornecedor=despesa.get("fornecedor_candidato"),
            documento=despesa.get("documento"),
        )
        if nova != antiga:
            despesa["categoria_eithra"] = nova
            mudancas.append(
                {
                    "descricao": despesa.get("descricao_original") or "",
                    "de": antiga,
                    "para": nova,
                    "valor": despesa.get("valor"),
                }
            )
    return mudancas


def _competencia_por_extenso(competencia) -> str:
    """O mês como a gestão o lê: ``MM/AAAA``."""
    if competencia is None:
        return "sem competência"
    return competencia.strftime("%m/%Y")


def _moeda(valor) -> str:
    """Escreve um valor decimal do jeito que ele aparece na tela."""
    numero = texto_para_decimal(valor)
    if numero is None:
        return "—"
    return f"R$ {numero:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


class Command(BaseCommand):
    help = (
        "Relê as categorias dos balanços já confirmados com a regra vigente. "
        "Sem --aplicar, apenas mostra o que mudaria."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--aplicar",
            action="store_true",
            help="Grava as categorias recalculadas. Sem esta opção, nada muda.",
        )

    def handle(self, *args, **opcoes):
        aplicar = opcoes["aplicar"]

        balancos = (
            BalancoMensal.objects.select_related("condominio")
            .filter(status=StatusBalanco.VALIDADO)
            .order_by("condominio__nome", "competencia")
        )

        total_de_lancamentos = 0
        total_de_balancos = 0

        for balanco in balancos:
            validado = de_dicionario_validado(balanco.dados_validados)
            leitura = validado.leitura or {}
            mudancas = reclassificar_leitura(leitura, condominio=balanco.condominio)
            if not mudancas:
                continue

            total_de_balancos += 1
            total_de_lancamentos += len(mudancas)

            self.stdout.write(
                f"\n{balanco.condominio.nome} — {_competencia_por_extenso(balanco.competencia)}"
            )
            for mudanca in mudancas:
                self.stdout.write(
                    f"  {rotulo(mudanca['de'])} → {rotulo(mudanca['para'])} "
                    f"| {_moeda(mudanca['valor'])} | {mudanca['descricao']}"
                )

            if not aplicar:
                continue

            with transaction.atomic():
                bloqueado = BalancoMensal.objects.select_for_update().get(
                    pk=balanco.pk
                )
                correcoes.registrar_revisao_automatica(
                    leitura,
                    tipo="reclassificacao",
                    justificativa=MOTIVO_DA_REVISAO.format(
                        data=timezone.localtime().strftime("%d/%m/%Y")
                    ),
                )
                correcoes.recalcular_agregados(
                    leitura, inconsistencias=bloqueado.inconsistencias or ()
                )
                services._gravar_leitura_corrigida(bloqueado, validado, leitura)

        if not total_de_lancamentos:
            self.stdout.write("Nenhuma categoria a reclassificar.")
            return

        if not aplicar:
            self.stdout.write(
                f"\n{total_de_lancamentos} lançamento(s) em {total_de_balancos} "
                "balanço(s) mudariam de categoria. Modo de conferência: nada foi "
                "alterado. Repita com --aplicar para gravar."
            )
            return

        self.stdout.write(
            f"\n{total_de_lancamentos} lançamento(s) reclassificado(s) em "
            f"{total_de_balancos} balanço(s)."
        )
