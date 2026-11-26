"""Sincroniza a caixa do Gmail para dentro da carga histórica.

Esta é a porta de entrada da informação que chega por e-mail. Ela responde a uma
pergunta só:

    Esta mensagem do Gmail já foi registrada no Eithra?
    Se não, registrar a identidade dela e os metadados mínimos.

O que ela **não** responde — de propósito, porque ainda não é hora: o que o
e-mail significa, se é demanda, se é manutenção, qual fornecedor está envolvido,
se precisa de ação, de documento, ou se fecha algo. Isso pertence às fases
seguintes.

O que entra aqui:

* a identidade da mensagem (``message_id``) e o fio a que ela pertence;
* quem enviou, o assunto e quando chegou;
* a conta do Gmail de onde ela veio e o condomínio a que a execução se refere;
* um lote, que é o registro da execução.

O que **não** entra: o corpo da mensagem e os anexos. O texto é obtido do
provider apenas para confirmar que a mensagem existe e é legível — ele não é
gravado no item, no lote, no log nem na saída do comando, porque nesta fase
ainda não é usado para nada. Nenhum anexo é baixado.

A execução é manual, controlada e auditável: um lote por rodada, mensagem nova
nasce ``PENDENTE`` e mensagem que já existe apenas atualiza metadados legítimos,
preservando o estado em que estiver. Reingestão não é reprocessamento.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.condominios.models import Condominio
from apps.core import gmail
from apps.core.constants import OrigemImportacaoHistorica
from apps.core.gmail import (
    GmailCredencialInvalida,
    GmailIndisponivel,
    GmailMensagemInexistente,
    GmailNaoConfigurado,
    GmailRespostaInvalida,
    obter_provedor,
)
from apps.core.models import ItemImportacaoHistorica, LoteImportacaoHistorica

logger = logging.getLogger(__name__)

# Quantas mensagens uma execução sem filtro declarado pode trazer. Existe para
# que uma rodada nunca varra a caixa inteira por descuido.
LIMITE_PADRAO = 100

# O aviso de que a integração está desligada. É a mesma frase no log e na tela:
# quem roda o comando por engano precisa entender que nada foi feito, e não
# confundir o silêncio com uma caixa vazia.
AVISO_GMAIL_DESLIGADO = (
    "A leitura do Gmail está desligada (EITHRA_GMAIL_ATIVO=False). "
    "Nada foi sincronizado; registre as mensagens manualmente."
)


@dataclass
class ResumoIngestao:
    """O que aconteceu na execução, para ser relatado no fim."""

    condominio: Condominio
    conta: str = ""
    consulta: str = ""
    limite: int = LIMITE_PADRAO
    lote: LoteImportacaoHistorica | None = None
    dry_run: bool = False
    encontradas: int = 0
    novas: int = 0
    existentes: int = 0
    falhas: list[tuple[str, str]] = field(default_factory=list)
    truncado: bool = False

    @property
    def processadas(self) -> int:
        """Quantas mensagens foram de fato tratadas nesta rodada."""
        return self.novas + self.existentes


class Command(BaseCommand):
    """Lê o Gmail e registra cada mensagem como item da carga histórica."""

    help = "Lê o Gmail e registra as mensagens como itens da carga histórica."

    def add_arguments(self, parser):
        parser.add_argument(
            "--condominio",
            required=True,
            help="Identificador do condomínio ao qual a execução pertence.",
        )
        parser.add_argument(
            "--consulta",
            default="",
            help="Busca do Gmail (ex.: 'in:inbox newer_than:7d'). Opcional.",
        )
        parser.add_argument(
            "--desde",
            default="",
            help="Só mensagens a partir desta data, no formato AAAA-MM-DD. Opcional.",
        )
        parser.add_argument(
            "--limite",
            type=int,
            default=None,
            help=(
                "Quantas mensagens trazer nesta execução. "
                f"Sem consulta e sem --desde, o limite passa a ser obrigatório. "
                f"Quando houver filtro, o padrão é {LIMITE_PADRAO}."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Só consulta e conta: não cria lote, não registra item, não altera nada.",
        )

    def handle(self, *args, **opcoes):
        # O interruptor vem antes de tudo: com a integração desligada a execução
        # não faz nada, avisa e termina — sem consultar credencial, sem tocar a
        # rede e sem criar lote. É um no-op deliberado, não uma falha.
        if not gmail.ativo():
            logger.warning(AVISO_GMAIL_DESLIGADO)
            self.stdout.write(self.style.WARNING(AVISO_GMAIL_DESLIGADO))
            return

        if not gmail.configurado():
            raise CommandError(
                "O acesso ao Gmail não está configurado neste ambiente. "
                "Defina as variáveis EITHRA_GMAIL_* antes de sincronizar."
            )

        condominio = self._resolver_condominio(opcoes["condominio"])
        consulta = _consulta_efetiva(opcoes["consulta"], opcoes["desde"])
        limite = self._resolver_limite(opcoes["limite"], consulta=consulta)

        resumo = self._ingerir(
            condominio=condominio, consulta=consulta, limite=limite, dry_run=opcoes["dry_run"]
        )
        self._relatar(resumo)

    @staticmethod
    def _ingerir(*, condominio, consulta, limite, dry_run) -> ResumoIngestao:
        """Roda a ingestão traduzindo a falha do provider numa falha de comando.

        Não configurado, credencial recusada e indisponibilidade do Gmail são
        falhas do conjunto, não de uma mensagem: elas interrompem a execução com
        uma mensagem curta, em vez de subir um erro cru da camada de rede.
        """
        try:
            return ingerir_do_gmail(
                condominio=condominio,
                provedor=obter_provedor(),
                consulta=consulta,
                limite=limite,
                dry_run=dry_run,
            )
        except GmailNaoConfigurado:
            raise CommandError("O acesso ao Gmail não está configurado neste ambiente.")
        except GmailCredencialInvalida:
            raise CommandError("O Gmail recusou a credencial configurada.")
        except GmailIndisponivel:
            raise CommandError("O Gmail não respondeu; tente de novo mais tarde.")
        except GmailRespostaInvalida:
            raise CommandError("O Gmail respondeu de forma inesperada.")

    # -- Resolução dos argumentos -------------------------------------------

    @staticmethod
    def _resolver_condominio(valor) -> Condominio:
        """Resolve o condomínio informado, sem inferir nada pela conta do Gmail.

        A conta do Gmail não serve para deduzir de quem é a caixa: a mesma caixa
        pode atender mais de um condomínio, e deduzir errado colocaria informação
        no lugar errado. Por isso o condomínio é exigido explicitamente.
        """
        try:
            chave = UUID(str(valor or "").strip())
        except (ValueError, AttributeError, TypeError):
            raise CommandError(
                "Informe o condomínio pelo identificador dele (--condominio)."
            )

        condominio = Condominio.objects.filter(pk=chave).first()
        if condominio is None:
            raise CommandError("O condomínio informado não foi encontrado.")
        return condominio

    @staticmethod
    def _resolver_limite(valor, *, consulta: str) -> int:
        """Decide quantas mensagens a execução pode trazer.

        Sem filtro declarado, a execução só avança com um limite explícito — é o
        que impede uma primeira rodada de varrer a caixa histórica inteira por
        descuido. Com filtro, o limite existe de todo modo, só para que a rodada
        termine.
        """
        if valor is None:
            if not consulta:
                raise CommandError(
                    "Para não varrer a caixa inteira, informe --consulta, --desde "
                    "ou --limite nesta execução."
                )
            return LIMITE_PADRAO
        if valor < 1:
            raise CommandError("O limite precisa ser pelo menos 1.")
        return valor

    # -- Relato -------------------------------------------------------------

    def _relatar(self, resumo: ResumoIngestao) -> None:
        """Mostra o resultado da execução, só com o que é operacional.

        Nada de corpo, token, segredo ou conteúdo de anexo aparece aqui.
        """
        escrever = self.stdout.write
        titulo = "Sincronização do Gmail"
        if resumo.dry_run:
            titulo += " (simulação — nada foi gravado)"
        escrever(titulo)
        escrever(f"Condomínio: {resumo.condominio.nome} ({resumo.condominio.pk})")
        escrever(f"Conta: {resumo.conta or 'não declarada'}")
        escrever(f"Filtro: {resumo.consulta or 'nenhum'}")
        escrever(f"Limite: {resumo.limite}")
        if resumo.dry_run:
            escrever("Lote: não criado (simulação)")
        else:
            escrever(f"Lote: {resumo.lote.pk if resumo.lote else 'não criado'}")
        escrever(f"Mensagens encontradas: {resumo.encontradas}")
        escrever(f"Novas: {resumo.novas}")
        escrever(f"Já existentes: {resumo.existentes}")
        escrever(f"Falhas: {len(resumo.falhas)}")
        for identificador, motivo in resumo.falhas:
            escrever(f"  - {identificador}: {motivo}")
        escrever(f"Total processado: {resumo.processadas}")
        if resumo.truncado:
            escrever(
                "Havia mais mensagens do que o limite desta execução; "
                "rode de novo com um filtro mais específico ou um limite maior."
            )


# ---------------------------------------------------------------------------
# Ingestão
# ---------------------------------------------------------------------------


def ingerir_do_gmail(
    *,
    condominio: Condominio,
    provedor,
    consulta: str = "",
    limite: int = LIMITE_PADRAO,
    dry_run: bool = False,
) -> ResumoIngestao:
    """Registra as mensagens do Gmail como itens da carga histórica.

    O provedor é recebido de fora para que a ingestão seja testável sem tocar a
    rede: quem chama decide de onde vêm as mensagens.
    """
    conta = _conta_declarada()
    resumo = ResumoIngestao(
        condominio=condominio,
        conta=conta,
        consulta=consulta,
        limite=limite,
        dry_run=dry_run,
    )

    # A listagem vem antes do lote: se a credencial estiver inválida ou o Gmail
    # não responder, a execução termina aqui e nenhum lote é deixado para trás.
    # A leitura é página a página, e o limite corta a paginação.
    referencias, resumo.truncado = _listar(provedor, consulta=consulta, limite=limite)
    resumo.encontradas = len(referencias)

    if not dry_run:
        resumo.lote = LoteImportacaoHistorica.objects.create(
            condominio=condominio,
            origem=OrigemImportacaoHistorica.GMAIL,
            conta_origem=conta,
            observacao=_contexto_da_execucao(consulta=consulta, limite=limite),
        )

    for referencia in referencias:
        try:
            mensagem = provedor.obter_mensagem(referencia.message_id)
        except (GmailMensagemInexistente, GmailRespostaInvalida) as erro:
            # A mensagem específica não pôde ser lida: registra e segue. As
            # demais não têm por que parar por causa dela.
            resumo.falhas.append((referencia.message_id, type(erro).__name__))
            continue

        if dry_run:
            # Em simulação nada é gravado: só se confirma que a mensagem é
            # legível e se conta.
            continue

        try:
            _, criado = ItemImportacaoHistorica.objects.registrar_item(
                lote=resumo.lote,
                identificador_externo=mensagem.message_id,
                message_id=mensagem.message_id,
                thread_id=mensagem.thread_id,
                remetente=mensagem.remetente,
                assunto=mensagem.assunto,
                momento_da_mensagem=mensagem.momento,
                hash_fonte=_identidade_da_mensagem(mensagem),
            )
        except ValidationError:
            # A mensagem chegou, mas não serve como item da fonte. Fica no
            # resumo e não trava o resto.
            resumo.falhas.append((referencia.message_id, "ValidationError"))
            continue

        if criado:
            resumo.novas += 1
        else:
            resumo.existentes += 1

    return resumo


def _listar(provedor, *, consulta: str, limite: int):
    """Percorre a listagem do provider, página a página, até o limite.

    Devolve as referências e se ficou coisa de fora. O limite existe para que a
    rodada termine de forma previsível em vez de seguir indefinidamente.
    """
    referencias: list = []
    pagina_token = ""

    while len(referencias) < limite:
        restante = limite - len(referencias)
        pagina = provedor.listar_mensagens(
            consulta=consulta,
            quantidade=min(restante, gmail.QUANTIDADE_MAXIMA),
            pagina_token=pagina_token,
        )
        referencias.extend(pagina.itens)

        if not pagina.itens:
            # Página vazia: encerra, mesmo que a API diga haver mais adiante.
            return referencias[:limite], bool(pagina.proxima_pagina)
        if not pagina.proxima_pagina:
            return referencias[:limite], False
        pagina_token = pagina.proxima_pagina

    return referencias[:limite], True


def _consulta_efetiva(consulta: str, desde: str) -> str:
    """Junta a busca informada com o recorte de data, quando houver.

    A busca do Gmail combina termos separados por espaço. O recorte de data vira
    o operador ``after:`` — é filtro de tempo, não regra de negócio.
    """
    partes = []
    if (consulta or "").strip():
        partes.append(consulta.strip())
    if (desde or "").strip():
        partes.append(f"after:{_data_para_busca(desde)}")
    return " ".join(partes)


def _data_para_busca(valor: str) -> str:
    try:
        data = datetime.strptime(valor.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        raise CommandError("--desde deve estar no formato AAAA-MM-DD.")
    return data.strftime("%Y/%m/%d")


def _identidade_da_mensagem(mensagem) -> str:
    """O fingerprint estável da mensagem, para integridade e diagnóstico.

    Não é a identidade dela — essa é o ``message_id``. Serve para reconhecer a
    mesma mensagem por características estáveis e para comparar duas leituras.
    """
    base = "\n".join(
        (
            mensagem.message_id,
            mensagem.thread_id,
            mensagem.remetente,
            mensagem.assunto,
            mensagem.momento.isoformat() if mensagem.momento else "",
        )
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _contexto_da_execucao(*, consulta: str, limite: int) -> str:
    """O contexto da rodada, guardado no lote como registro da execução."""
    partes = ["Execução manual de leitura do Gmail"]
    if consulta:
        partes.append(f"filtro: {consulta}")
    partes.append(f"limite: {limite}")
    return "; ".join(partes)


def _conta_declarada() -> str:
    """A conta de origem configurada para esta instalação."""
    return (getattr(settings, "EITHRA_GMAIL_CONTA", "") or "").strip()
