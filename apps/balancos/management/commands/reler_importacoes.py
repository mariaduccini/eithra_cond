"""Relê as importações que ficaram sem leitura.

Um balancete escaneado pode ter chegado antes de o OCR local existir no
ambiente e ficado sem leitura automática. Este comando pega essas importações
— as de layout desconhecido — e repete a detecção e a leitura sobre o arquivo
já guardado, sem novo upload.

Há um segundo caso, mais silencioso: a leitura legada que rodou e devolveu zero
receitas e zero despesas. As causas foram corrigidas (competência, seção colada
e tolerância ao OCR), e ``--legado-sem-linhas`` relê exatamente essas — o
arquivo é o mesmo, o que faltava era a leitura.

O que ele **não** faz, de propósito:

* não toca em balanço já confirmado nem em importação com desfecho — a
  releitura só vale para o que ainda não virou leitura;
* não altera totais oficiais: a releitura alimenta a conferência, e quem
  confirma o mês continua sendo a gestão.

A saída diz, linha a linha, o que aconteceu com cada arquivo:
``arquivo → layout → situação``.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.balancos.constants import LayoutBalanco, StatusImportacao
from apps.balancos.detector import ErroDeDeteccaoDeLayout
from apps.balancos.models import ImportacaoBalanco
from apps.balancos.services import ErroDeReleitura, reler_importacao


class Command(BaseCommand):
    help = (
        "Repete a detecção e a leitura das importações que ficaram sem leitura "
        "(layout desconhecido, ou legado sem lançamento), sem novo upload."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--desconhecidas",
            action="store_true",
            help="Relê todas as importações de layout desconhecido, sem desfecho.",
        )
        parser.add_argument(
            "--legado-sem-linhas",
            action="store_true",
            dest="legado_sem_linhas",
            help=(
                "Relê as importações legadas, sem desfecho, que ficaram sem "
                "nenhuma receita e nenhuma despesa."
            ),
        )

    def handle(self, *args, **opcoes):
        if not opcoes["desconhecidas"] and not opcoes["legado_sem_linhas"]:
            self.stdout.write(
                "Nada a fazer. Use --desconhecidas ou --legado-sem-linhas para "
                "reler as importações sem leitura."
            )
            return

        total = 0
        if opcoes["desconhecidas"]:
            total += self._reler_desconhecidas()
        if opcoes["legado_sem_linhas"]:
            total += self._reler_legado_sem_linhas()

        if not total:
            self.stdout.write("Nenhuma importação para reler.")
            return

        self.stdout.write(f"\n{total} importação(ões) relida(s).")

    def _reler_desconhecidas(self) -> int:
        """As importações de layout desconhecido, sem desfecho."""
        importacoes = (
            ImportacaoBalanco.objects.select_related("condominio")
            .filter(
                layout_detectado=LayoutBalanco.DESCONHECIDO,
                status__in=(StatusImportacao.PRONTO, StatusImportacao.ERRO),
                confirmado_em__isnull=True,
                descartado_em__isnull=True,
            )
            .order_by("condominio__nome", "criado_em")
        )
        return self._reler(importacoes, permitir_relida=False)

    def _reler_legado_sem_linhas(self) -> int:
        """As importações legadas, sem desfecho, que não renderam lançamento.

        A confirmação continua sendo o fim da linha: o filtro de desfecho vale
        aqui igual ao das desconhecidas, e nenhum balanço confirmado é relido.
        """
        importacoes = (
            ImportacaoBalanco.objects.select_related("condominio")
            .filter(
                layout_detectado=LayoutBalanco.LEGADO,
                status__in=(StatusImportacao.PRONTO, StatusImportacao.ERRO),
                confirmado_em__isnull=True,
                descartado_em__isnull=True,
            )
            .order_by("condominio__nome", "criado_em")
        )
        sem_lancamento = [item for item in importacoes if _sem_lancamentos(item)]
        return self._reler(sem_lancamento, permitir_relida=True)

    def _reler(self, importacoes, *, permitir_relida: bool) -> int:
        """Relê cada importação da seleção e escreve o que aconteceu."""
        total = 0
        for importacao in importacoes:
            total += 1
            try:
                reler_importacao(importacao, permitir_relida=permitir_relida)
            except (ErroDeReleitura, ErroDeDeteccaoDeLayout) as erro:
                self.stderr.write(f"{importacao.nome_original} → falhou: {erro}")
                continue
            importacao.refresh_from_db()
            self.stdout.write(
                f"{importacao.nome_original} → "
                f"{importacao.get_layout_detectado_display()} → "
                f"{importacao.situacao_para_a_gestao}"
            )
        return total


def _sem_lancamentos(importacao) -> bool:
    """Se a leitura legada desta importação não rendeu receita nem despesa."""
    dados = importacao.resultado_extraido or {}
    return not dados.get("receitas") and not dados.get("despesas")
