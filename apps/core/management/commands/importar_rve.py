"""Importa os dados reais do RVE a partir da matriz de reconciliação.

O acervo do Condomínio Rio Vermelho Exclusive já foi lido e reconciliado nas
fases anteriores; a matriz de identidades é a saída consolidada delas. Esta é a
rodada que a consome uma vez e devolve ao sistema o núcleo operacional que ela
manda criar — demandas, projeto/etapa/entrega e ativos —, preservando todo o
resto como linhagem auditável.

A execução definitiva exige quem responda pela carga (``--apply`` e
``--responsavel``); a simulação (``--dry-run``) lê e decide sem gravar nada.
Como a rodada mexe no banco de um condomínio real, nada acontece por omissão: a
intenção precisa ser declarada.
"""
from __future__ import annotations

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.core import rve_importacao, rve_relatorio
from apps.core.management.commands import sincronizar_gmail as leitura
from apps.core.rve_importacao import ErroDaImportacaoRVE, ResumoDaImportacao

# Onde a matriz de reconciliação vive, quando o caminho não é declarado.
MATRIZ_PADRAO = Path(
    r"D:\EITHRA_RVE_IMPORTACAO\relatorios\reconciliacao_importacao_rve.csv"
)


class Command(BaseCommand):
    """Lê a matriz reconciliada do RVE e importa o núcleo operacional dela."""

    help = "Importa os dados reais do RVE a partir da matriz de reconciliação."

    def add_arguments(self, parser):
        parser.add_argument(
            "--condominio",
            required=True,
            help="Identificador do condomínio destino da importação.",
        )
        parser.add_argument(
            "--matriz",
            default=str(MATRIZ_PADRAO),
            help="Caminho da matriz reconciliada (CSV). Opcional.",
        )
        parser.add_argument(
            "--reconciliacao",
            default=None,
            help=(
                "Artefato que declara o que já existe no domínio, para a carga "
                "não criar um segundo fato de negócio. Por omissão, o arquivo "
                "'reconciliacao_rve_decisoes.csv' ao lado da matriz; passe "
                "string vazia para rodar sem reconciliação declarada."
            ),
        )
        grupo = parser.add_mutually_exclusive_group()
        grupo.add_argument(
            "--dry-run",
            action="store_true",
            help="Só lê e decide: não cria lote, item, vínculo nem domínio.",
        )
        grupo.add_argument(
            "--apply",
            action="store_true",
            help="Executa a carga de verdade. Exige --responsavel.",
        )
        parser.add_argument(
            "--responsavel",
            default="",
            help=(
                "E-mail de quem responde pela carga, e assina o que for criado. "
                "Obrigatório ao gravar; na simulação não é exigido."
            ),
        )
        parser.add_argument(
            "--ids",
            nargs="*",
            default=None,
            help="Recorta a rodada para estes identificadores externos. Opcional.",
        )
        parser.add_argument(
            "--limite",
            type=int,
            default=None,
            help="Quantas linhas da matriz processar nesta execução. Opcional.",
        )
        parser.add_argument(
            "--relatorio",
            default="",
            help=(
                "Diretório onde gravar o balanço e os CSVs de conferência. "
                "Por omissão, o mesmo diretório da matriz."
            ),
        )

    def handle(self, *args, **opcoes):
        if not opcoes["dry_run"] and not opcoes["apply"]:
            raise CommandError(
                "Diga o que deve acontecer: --dry-run para simular sem gravar, "
                "ou --apply para executar a carga definitiva."
            )

        caminho = Path(opcoes["matriz"])
        if not caminho.is_file():
            raise CommandError(f"A matriz informada não foi encontrada: {caminho}")

        if opcoes["limite"] is not None and opcoes["limite"] < 1:
            raise CommandError("O limite precisa ser pelo menos 1.")

        simulado = not opcoes["apply"]
        condominio = leitura.Command._resolver_condominio(opcoes["condominio"])
        reconciliacao = self._resolver_reconciliacao(opcoes["reconciliacao"], caminho)
        usuario = None
        if not simulado:
            usuario = self._resolver_responsavel(opcoes["responsavel"])

        try:
            resumo = rve_importacao.importar(
                condominio=condominio,
                matriz=caminho,
                usuario=usuario,
                simulado=simulado,
                ids=opcoes["ids"],
                limite=opcoes["limite"],
                reconciliacao=reconciliacao,
            )
        except ErroDaImportacaoRVE as erro:
            raise CommandError(f"{erro} Nada foi gravado.")

        self._relatar(resumo)
        self._conferir(resumo, diretorio=opcoes["relatorio"] or caminho.parent)

    @staticmethod
    def _resolver_reconciliacao(valor: str | None, matriz: Path):
        """Onde está a reconciliação declarada desta rodada.

        Sem valor, ela mora ao lado da matriz. String vazia desliga a
        reconciliação — e desligar é uma escolha explícita, não um esquecimento.
        """
        if valor is None:
            return matriz.parent / rve_importacao.ARQUIVO_RECONCILIACAO
        valor = valor.strip()
        return valor or None

    @staticmethod
    def _resolver_responsavel(valor: str):
        """Resolve quem responde pela carga — por e-mail, como no resto do sistema."""
        endereco = " ".join((valor or "").split())
        if not endereco:
            raise CommandError(
                "Informe quem responde por esta carga: --responsavel <e-mail>. "
                "Sem isso nada é gravado."
            )

        usuario = get_user_model().objects.filter(email__iexact=endereco).first()
        if usuario is None:
            raise CommandError(
                "Não há pessoa cadastrada com este e-mail para responder pela carga."
            )
        return usuario

    def _relatar(self, resumo: ResumoDaImportacao) -> None:
        """Mostra o resultado da rodada, só com o que é operacional."""
        escrever = self.stdout.write
        titulo = "Importação definitiva RVE"
        if resumo.simulado:
            titulo += " (simulação — nada foi gravado)"
        escrever(titulo)
        escrever(f"Condomínio: {resumo.condominio.nome} ({resumo.condominio.pk})")
        escrever(f"Matriz: {resumo.matriz}")
        if resumo.simulado:
            escrever("Lote: não criado (simulação)")
        else:
            escrever(f"Lote: {resumo.lote.pk if resumo.lote else 'não criado'}")
        escrever(f"Linhas na matriz: {resumo.lidas}")
        escrever(f"Linhas selecionadas: {resumo.selecionadas}")
        escrever(f"Itens da fonte: {resumo.itens}")
        escrever(f"Itens novos: {resumo.itens_novos}")
        escrever(f"Itens já existentes: {resumo.itens_existentes}")
        escrever(f"Registros criados: {resumo.total_criado}")
        for tipo in sorted(resumo.criados):
            escrever(f"  - {tipo}: {resumo.criados[tipo]}")
        escrever(
            "Vinculados a registro existente (VINCULAR_EXISTENTE): "
            f"{resumo.contar_desfecho(rve_importacao.DESFECHO_VINCULADO_EXISTENTE)}"
        )
        escrever(
            "Vinculados a jornada relacionada (VINCULAR_JORNADA): "
            f"{resumo.contar_desfecho(rve_importacao.DESFECHO_VINCULADO_JORNADA)}"
        )
        escrever(
            "Em revisão por reconciliação (REVISAR): "
            f"{resumo.contar_desfecho(rve_importacao.DESFECHO_REVISAR)}"
        )
        escrever(f"Vínculos consolidados: {resumo.vinculados}")
        escrever(f"Já existentes (pulados): {resumo.pulados}")
        escrever(f"Sem destino, preservados: {resumo.ignorados}")
        escrever(f"Pendentes de vínculo: {resumo.pendentes}")
        escrever(f"Marcados para revisão: {resumo.revisao}")
        escrever(f"Falhas: {len(resumo.falhas)}")
        for identificador, motivo in resumo.falhas[:20]:
            escrever(f"  - {identificador}: {motivo}")
        if len(resumo.falhas) > 20:
            escrever(f"  e mais {len(resumo.falhas) - 20} falha(s).")
        if resumo.simulado:
            escrever("Nada foi gravado. Rode com --apply para executar a carga.")

    def _conferir(self, resumo: ResumoDaImportacao, *, diretorio) -> None:
        """Confere a rodada e grava o balanço e os CSVs de acompanhamento.

        Na carga definitiva o relatório ainda relê o banco e confere cada destino
        prometido — é a leitura que fecha a reconciliação pós-carga.
        """
        inventario = None
        if not resumo.simulado:
            inventario = rve_relatorio.inventario_do_banco(resumo)
        relatorio = rve_relatorio.montar(resumo, inventario=inventario)
        caminhos = rve_relatorio.escrever(relatorio, diretorio=diretorio)

        self.stdout.write("")
        self.stdout.write("CONFERÊNCIAS OBRIGATÓRIAS")
        for item in relatorio.conferencias:
            marca = "OK " if item.confirmada else "DIVERGE"
            esperado = (
                f" (esperado {item.esperado})" if item.esperado is not None else ""
            )
            self.stdout.write(f"  [{marca}] {item.nome}: {item.encontrado}{esperado}")
            if item.divergentes:
                self.stdout.write(f"    a explicar por ID: {', '.join(item.divergentes)}")
        if inventario is not None:
            self.stdout.write("INVENTÁRIO DO BANCO (destinos prometidos pela rodada)")
            for entidade in sorted(inventario["por_entidade"]):
                contagem = inventario["por_entidade"][entidade]
                self.stdout.write(
                    f"  {entidade}: {contagem['no_banco']} de "
                    f"{contagem['esperados']} no banco"
                )
            self.stdout.write(f"  Destinos ausentes: {len(inventario['ausentes'])}")
            for identificador in inventario["ausentes"]:
                self.stdout.write(f"    - {identificador}")
        self.stdout.write(f"Conflitos: {len(relatorio.conflitos)}")
        self.stdout.write("Artefatos gravados:")
        for nome, caminho in caminhos.items():
            self.stdout.write(f"  - {nome}: {caminho}")
